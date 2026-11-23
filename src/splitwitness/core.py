"""Audit fixed partition assignments against explicit undirected constraints."""

import csv
import hashlib
import io
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path


class AuditError(ValueError):
    """The supplied manifests cannot be completely audited."""


def _integer(name, value, minimum):
    if type(value) is not int or value < minimum:
        raise AuditError(f"{name} must be an integer >= {minimum}")


@dataclass(frozen=True)
class Limits:
    """Acceptance caps, not a process memory or runtime guarantee."""

    max_bytes: int = 16 * 1024 * 1024
    max_records: int = 100_000
    max_links: int = 500_000
    max_field_chars: int = 1024
    max_splits: int = 100

    def __post_init__(self):
        for name, value in vars(self).items():
            _integer(name, value, 1)


def _rows(path, header, label, limits, row_cap):
    try:
        with Path(path).open("rb") as source:
            raw = source.read(limits.max_bytes + 1)
        if len(raw) > limits.max_bytes:
            raise AuditError(f"{label}: byte limit exceeded")
        text = raw.decode("utf-8")
        if "\x00" in text:
            raise AuditError(f"{label}: NUL is not allowed")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        if next(reader, None) != header:
            raise AuditError(f"{label}: expected exact header {','.join(header)}")
        rows = []
        for number, row in enumerate(reader, 2):
            if len(rows) >= row_cap:
                raise AuditError(f"{label}: row limit exceeded")
            if len(row) != len(header):
                raise AuditError(f"{label}: wrong field count at record {number}")
            if any(not cell or len(cell) > limits.max_field_chars for cell in row):
                raise AuditError(
                    f"{label}: empty or oversized field at record {number}"
                )
            rows.append(row)
    except (OSError, UnicodeError, csv.Error, OverflowError) as exc:
        raise AuditError(f"{label}: unreadable or invalid UTF-8 CSV") from exc
    return rows, {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


class _Groups:
    def __init__(self, keys):
        self.parent = {key: key for key in keys}
        self.size = dict.fromkeys(keys, 1)

    def find(self, key):
        while key != self.parent[key]:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def join(self, left, right):
        left, right = self.find(left), self.find(right)
        if left == right:
            return
        if self.size[left] < self.size[right]:
            left, right = right, left
        self.parent[right] = left
        self.size[left] += self.size[right]


def _witness(members, splits, adjacency, edge_cap):
    """Shortest path to smallest differently assigned ID, lexical BFS ties."""
    start = members[0]
    end = next(key for key in members if splits[key] != splits[start])
    queue = deque([start])
    previous = {start: None}
    while queue:
        key = queue.popleft()
        if key == end:
            break
        for neighbor, relation in sorted(adjacency[key]):
            if neighbor not in previous:
                previous[neighbor] = (key, relation)
                queue.append(neighbor)
    hops = 0
    key = end
    while key != start:
        key = previous[key][0]
        hops += 1
    path = None
    if hops <= edge_cap:
        path = []
        key = end
        while key != start:
            before, relation = previous[key]
            path.append({"left_id": before, "right_id": key, "relation": relation})
            key = before
        path.reverse()
    return {
        "start_id": start,
        "start_split": splits[start],
        "end_id": end,
        "end_split": splits[end],
        "hops": hops,
        "path_omitted": hops > edge_cap,
        "path": path,
    }


def audit(records_path, links_path, *, examples=0, max_witness_edges=20, limits=None):
    """Return a complete JSON-ready report or raise AuditError.

    Links assert that endpoints must share a partition. All links participate;
    relation strings label evidence and do not select or weaken constraints.
    """
    _integer("examples", examples, 0)
    _integer("max_witness_edges", max_witness_edges, 0)
    if limits is None:
        limits = Limits()
    if type(limits) is not Limits:
        raise AuditError("limits must be a Limits instance")
    rows, records_receipt = _rows(
        records_path, ["id", "split"], "records", limits, limits.max_records
    )
    if not rows:
        raise AuditError("records: at least one record is required")
    splits = {}
    for number, (key, split) in enumerate(rows, 2):
        if key in splits:
            raise AuditError(f"records: duplicate id at record {number}")
        splits[key] = split
    del rows
    split_totals = Counter(splits.values())
    if len(split_totals) > limits.max_splits:
        raise AuditError("records: split limit exceeded")
    rows, links_receipt = _rows(
        links_path,
        ["left_id", "right_id", "relation"],
        "links",
        limits,
        limits.max_links,
    )
    groups = _Groups(splits)
    seen = set()
    adjacency = defaultdict(list) if examples else None
    incident = set()
    crossing_links = 0
    for number, (left, right, relation) in enumerate(rows, 2):
        if left not in splits or right not in splits:
            raise AuditError(f"links: unknown endpoint at record {number}")
        edge = (left, right, relation) if left <= right else (right, left, relation)
        if edge in seen:
            raise AuditError(f"links: duplicate undirected link at record {number}")
        seen.add(edge)
        groups.join(left, right)
        incident.update((left, right))
        if adjacency is not None:
            adjacency[left].append((right, relation))
            adjacency[right].append((left, relation))
        crossing_links += splits[left] != splits[right]
    link_count = len(rows)
    del rows, seen
    components = defaultdict(list)
    for key in sorted(splits):
        components[groups.find(key)].append(key)
    affected = Counter()
    bad = []
    cross_pairs = 0
    for members in components.values():
        counts = Counter(splits[key] for key in members)
        if len(counts) > 1:
            affected.update(counts)
            bad.append((members, counts))
            cross_pairs += (
                len(members) ** 2 - sum(n * n for n in counts.values())
            ) // 2
    bad.sort(key=lambda item: item[0][0])
    samples = []
    for members, counts in bad[:examples]:
        samples.append(
            {
                "component_id": members[0],
                "records": len(members),
                "split_counts": dict(sorted(counts.items())),
                "witness": _witness(members, splits, adjacency, max_witness_edges),
            }
        )
    return {
        "schema_version": 1,
        "passed": not bad,
        "policy": "all_declared_links_must_stay_within_one_split",
        "counts": {
            "records": len(splits),
            "links": link_count,
            "components": len(components),
            "isolated_records": len(splits) - len(incident),
            "violating_components": len(bad),
            "affected_records": sum(affected.values()),
            "crossing_links": crossing_links,
            "cross_split_record_pairs": cross_pairs,
        },
        "splits": {
            split: {"records": split_totals[split], "affected_records": affected[split]}
            for split in sorted(split_totals)
        },
        "examples": samples,
        "examples_omitted": len(bad) - len(samples),
        "settings": {
            "examples": examples,
            "max_witness_edges": max_witness_edges,
            "limits": dict(vars(limits)),
        },
        "inputs": {"records": records_receipt, "links": links_receipt},
    }
