"""Audit long-form namespaced group memberships without expanding cliques."""

from collections import Counter, defaultdict, deque

from .core import AuditError, Limits, _Groups, _integer, _rows


def _path(start, end, by_record, by_group, cap):
    # A bipartite BFS: each group is expanded at most once. Sorting both sides
    # chooses a reproducible shortest path in number of group-sharing hops.
    queue = deque([start])
    previous = {start: None}
    expanded = set()
    while queue:
        current = queue.popleft()
        if current == end:
            break
        for group in sorted(by_record[current]):
            if group in expanded:
                continue
            expanded.add(group)
            for neighbor in sorted(by_group[group]):
                if neighbor not in previous:
                    previous[neighbor] = (current, group)
                    queue.append(neighbor)
    hops = 0
    current = end
    while current != start:
        current = previous[current][0]
        hops += 1
    path = None
    if hops <= cap:
        current = end
        path = []
        while current != start:
            before, (namespace, group_id) = previous[current]
            path.append(
                {
                    "left_id": before,
                    "right_id": current,
                    "namespace": namespace,
                    "group_id": group_id,
                }
            )
            current = before
        path.reverse()
    return {
        "start_id": start,
        "end_id": end,
        "hops": hops,
        "path_omitted": hops > cap,
        "path": path,
    }


def audit_memberships(
    records_path, memberships_path, *, examples=0, max_witness_edges=20, limits=None
):
    """Check every assigned record's declared groups stay within one split.

    All records must have at least one membership, including otherwise isolated
    singleton records. A group is the exact tuple (namespace, group_id).
    max_links caps membership rows in this mode, not generated graph edges.
    """
    _integer("examples", examples, 0)
    _integer("max_witness_edges", max_witness_edges, 0)
    if limits is None:
        limits = Limits()
    if type(limits) is not Limits:
        raise AuditError("limits must be a Limits instance")
    records, receipt = _rows(
        records_path, ["id", "split"], "records", limits, limits.max_records
    )
    if not records:
        raise AuditError("records: at least one record is required")
    splits = {}
    for number, (key, split) in enumerate(records, 2):
        if key in splits:
            raise AuditError(f"records: duplicate id at record {number}")
        splits[key] = split
    del records
    totals = Counter(splits.values())
    if len(totals) > limits.max_splits:
        raise AuditError("records: split limit exceeded")
    rows, membership_receipt = _rows(
        memberships_path,
        ["record_id", "namespace", "group_id"],
        "memberships",
        limits,
        limits.max_links,
    )
    by_record = defaultdict(set)
    by_group = defaultdict(set)
    for number, (key, namespace, group_id) in enumerate(rows, 2):
        if key not in splits:
            raise AuditError(f"memberships: unknown record at record {number}")
        group = (namespace, group_id)
        if group in by_record[key]:
            raise AuditError(f"memberships: duplicate membership at record {number}")
        by_record[key].add(group)
        by_group[group].add(key)
    count = len(rows)
    del rows
    if len(by_record) != len(splits):
        raise AuditError("memberships: every assigned record needs a membership")
    groups = _Groups(splits)
    violating_groups = 0
    for members in by_group.values():
        representative = next(iter(members))
        for key in members:
            groups.join(representative, key)
        violating_groups += len({splits[key] for key in members}) > 1
    components = defaultdict(list)
    for key in sorted(splits):
        components[groups.find(key)].append(key)
    affected = Counter()
    violations = []
    pairs = 0
    for members in components.values():
        counts = Counter(splits[key] for key in members)
        if len(counts) > 1:
            affected.update(counts)
            violations.append((members, counts))
            pairs += (len(members) ** 2 - sum(n * n for n in counts.values())) // 2
    violations.sort(key=lambda item: item[0][0])
    samples = []
    for members, counts in violations[:examples]:
        start = members[0]
        end = next(key for key in members if splits[key] != splits[start])
        witness = _path(start, end, by_record, by_group, max_witness_edges)
        witness.update(start_split=splits[start], end_split=splits[end])
        samples.append(
            {
                "component_id": start,
                "records": len(members),
                "split_counts": dict(sorted(counts.items())),
                "witness": witness,
            }
        )
    return {
        "schema_version": 1,
        "mode": "memberships",
        "policy": "all_declared_groups_must_stay_within_one_split",
        "passed": not violations,
        "counts": {
            "records": len(splits),
            "memberships": count,
            "groups": len(by_group),
            "components": len(components),
            "singleton_components": sum(len(m) == 1 for m in components.values()),
            "cross_split_groups": violating_groups,
            "violating_components": len(violations),
            "affected_records": sum(affected.values()),
            "cross_split_record_pairs": pairs,
        },
        "splits": {
            s: {"records": totals[s], "affected_records": affected[s]}
            for s in sorted(totals)
        },
        "examples": samples,
        "examples_omitted": len(violations) - len(samples),
        "settings": {
            "examples": examples,
            "max_witness_edges": max_witness_edges,
            "limits": dict(vars(limits)),
        },
        "inputs": {"records": receipt, "memberships": membership_receipt},
        }
