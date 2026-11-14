"""Counts-only audits must retain validation without retaining witness edges."""

import csv
import json
import random
import tempfile
import unittest
from collections import Counter, defaultdict, deque
from pathlib import Path
from unittest.mock import patch

from splitwitness import AuditError, Limits, audit


class CountsOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.records = Path(self.temp.name) / "records.csv"
        self.links = Path(self.temp.name) / "links.csv"

    def write(self, records, links):
        for path, header, values in (
            (self.records, ["id", "split"], records),
            (self.links, ["left_id", "right_id", "relation"], links),
        ):
            with path.open("w", newline="", encoding="utf8") as stream:
                w = csv.writer(stream)
                w.writerow(header)
                w.writerows(values)

    def test_no_adjacency_retained_when_examples_zero(self):
        self.write(
            [("a", "train"), ("b", "test"), ("self", "train"), ("alone", "test")],
            [("a", "b", "one"), ("a", "b", "two"), ("self", "self", "self")],
        )
        for examples, expected_edges in ((0, 0), (1, 6)):
            mappings = []

            def capture(factory):
                value = defaultdict(factory)
                mappings.append(value)
                return value

            with patch("splitwitness.core.defaultdict", side_effect=capture):
                row = audit(self.records, self.links, examples=examples)
            retained = sum(
                isinstance(value, tuple)
                for mapping in mappings
                for values in mapping.values()
                for value in values
            )
            self.assertEqual(retained, expected_edges)
            self.assertEqual(row["counts"]["isolated_records"], 1)
            self.assertEqual(row["counts"]["crossing_links"], 2)

    def test_no_witness_search_even_when_omission_cap_zero(self):
        self.write([("a", "x"), ("b", "y")], [("a", "b", "r")])
        with patch(
            "splitwitness.core._witness",
            side_effect=AssertionError("counts-only cannot search"),
        ):
            row = audit(self.records, self.links, examples=0, max_witness_edges=0)
        self.assertFalse(row["passed"])
        self.assertEqual(row["examples_omitted"], 1)

    def test_self_edges_parallel_relations_and_empty_links(self):
        self.write(
            [("a", "x"), ("b", "y"), ("c", "x")],
            [("a", "a", "r"), ("a", "b", "r"), ("b", "a", "s")],
        )
        row = audit(self.records, self.links)
        self.assertEqual(row["counts"]["isolated_records"], 1)
        self.assertEqual(row["counts"]["cross_split_record_pairs"], 1)
        self.assertEqual(row["counts"]["links"], 3)
        self.write([("a", "x"), ("b", "y")], [])
        row = audit(self.records, self.links)
        self.assertEqual(row["counts"]["isolated_records"], 2)
        self.assertEqual(row["counts"]["components"], 2)

    def test_counts_only_still_rejects_invalid_links(self):
        invalid = [
            [("a", "z", "x")],
            [("a", "b", "x"), ("b", "a", "x")],
            [("a", "a", "x"), ("a", "a", "x")],
            [("a", "b", "")],
        ]
        for links in invalid:
            self.write([("a", "x"), ("b", "y")], links)
            for examples in (0, 1):
                with self.subTest(links=links, examples=examples):
                    with self.assertRaises(AuditError):
                        audit(self.records, self.links, examples=examples)
        self.write([("a", "x"), ("b", "y")], [("a", "b", "x"), ("a", "a", "x")])
        with self.assertRaisesRegex(AuditError, "row limit"):
            audit(self.records, self.links, limits=Limits(max_links=1))

    def test_independent_reachability_counts_and_example_mode_consistency(self):
        rng = random.Random(31415)
        for case in range(240):
            splits = {
                str(i): rng.choice(["train", "test", "val"])
                for i in range(rng.randrange(1, 30))
            }
            keys = list(splits)
            links = {
                (*sorted(rng.choices(keys, k=2)), rng.choice(["entity", "session"]))
                for _ in range(rng.randrange(70))
            }
            self.write(list(splits.items()), sorted(links))
            neighbors = {key: set() for key in splits}
            incident = set()
            crossing = 0
            for a, b, _ in links:
                neighbors[a].add(b)
                neighbors[b].add(a)
                incident.update((a, b))
                crossing += splits[a] != splits[b]
            components = []
            remaining = set(keys)
            while remaining:
                root = min(remaining)
                seen = {root}
                queue = deque([root])
                while queue:
                    for n in neighbors[queue.popleft()]:
                        if n not in seen:
                            seen.add(n)
                            queue.append(n)
                remaining -= seen
                components.append(seen)
            bad = [c for c in components if len({splits[k] for k in c}) > 1]
            affected = Counter(splits[k] for c in bad for k in c)
            pairs = sum(
                sum(splits[a] != splits[b] for b in c if a < b)
                for c in components
                for a in c
            )
            expected = dict(
                records=len(keys),
                links=len(links),
                components=len(components),
                isolated_records=len(keys) - len(incident),
                violating_components=len(bad),
                affected_records=sum(map(len, bad)),
                crossing_links=crossing,
                cross_split_record_pairs=pairs,
            )
            with self.subTest(case=case):
                counts = audit(self.records, self.links)
                full = audit(self.records, self.links, examples=1000)
                self.assertEqual(counts["counts"], expected)
                self.assertEqual(counts["passed"], not bad)
                self.assertEqual(counts["examples_omitted"], len(bad))
                self.assertEqual(
                    counts["splits"],
                    {
                        s: dict(
                            records=sum(v == s for v in splits.values()),
                            affected_records=affected[s],
                        )
                        for s in sorted(set(splits.values()))
                    },
                )
                full["examples"] = []
                full["examples_omitted"] = len(bad)
                full["settings"]["examples"] = 0
                self.assertEqual(
                    json.dumps(counts, sort_keys=True), json.dumps(full, sort_keys=True)
                )
