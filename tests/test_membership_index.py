"""Counts-only membership validation needs one incidence orientation, not two."""

import csv
import gc
import json
import random
import tempfile
import tracemalloc
import unittest
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

from splitwitness import AuditError, audit_memberships
from splitwitness.core import _Groups


class MembershipIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.records = Path(self.temp.name) / "records.csv"
        self.memberships = Path(self.temp.name) / "memberships.csv"

    def write(self, records, memberships):
        for path, header, rows in (
            (self.records, ["id", "split"], records),
            (self.memberships, ["record_id", "namespace", "group_id"], memberships),
        ):
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(header)
                writer.writerows(rows)

    def run_audit(self, **kwargs):
        return audit_memberships(self.records, self.memberships, **kwargs)

    def indexes(self, examples, cap=20):
        indexes = []

        def create(factory):
            index = defaultdict(factory)
            indexes.append((factory, index))
            return index

        with patch("splitwitness.memberships.defaultdict", side_effect=create):
            report = self.run_audit(examples=examples, max_witness_edges=cap)
        return report, [index for factory, index in indexes if factory is set]

    def test_counts_only_has_no_record_to_group_index(self):
        self.write(
            [("PRIVATE-a", "train"), ("PRIVATE-b", "test")],
            [("PRIVATE-a", "n", "SECRET"), ("PRIVATE-b", "n", "SECRET")],
        )
        report, indexes = self.indexes(0)
        self.assertEqual(len(indexes), 1)
        self.assertEqual(
            dict(indexes[0]), {("n", "SECRET"): {"PRIVATE-a", "PRIVATE-b"}}
        )
        self.assertEqual(report["counts"]["memberships"], 2)
        self.assertEqual(report["counts"]["cross_split_record_pairs"], 1)
        self.assertEqual(report["examples"], [])
        self.assertNotIn("PRIVATE", json.dumps(report))
        self.assertNotIn("SECRET", json.dumps(report))

    def test_positive_examples_keep_both_indexes_even_if_path_is_omitted(self):
        self.write([("a", "x"), ("b", "y")], [("a", "n", "g"), ("b", "n", "g")])
        for cap in (0, 1):
            with self.subTest(cap=cap):
                report, indexes = self.indexes(1, cap)
                self.assertEqual(len(indexes), 2)
                self.assertEqual(
                    dict(indexes[0]), {"a": {("n", "g")}, "b": {("n", "g")}}
                )
                self.assertEqual(
                    report["examples"][0]["witness"]["path_omitted"], cap == 0
                )

    def test_positive_examples_keep_both_indexes_even_for_clean_inventory(self):
        self.write([("a", "x")], [("a", "n", "g")])
        report, indexes = self.indexes(1)
        self.assertTrue(report["passed"])
        self.assertEqual(len(indexes), 2)

    def test_full_counts_and_receipts_identical_with_witness_index(self):
        rng = random.Random(20261122)
        for case in range(100):
            records = [
                (str(i), rng.choice(["train", "test", "val"])) for i in range(20)
            ]
            memberships = {(key, "singleton", key) for key, _ in records}
            memberships.update(
                (key, "group", str(group))
                for group in range(12)
                for key, _ in records
                if rng.random() < 0.2
            )
            rows = list(memberships)
            rng.shuffle(rows)
            self.write(records, rows)
            plain = self.run_audit()
            witnesses = self.run_audit(examples=100, max_witness_edges=100)
            for result in (plain, witnesses):
                for field in ("examples", "examples_omitted", "settings"):
                    result.pop(field)
            with self.subTest(case=case):
                self.assertEqual(plain, witnesses)

    def test_duplicate_and_coverage_validation_still_fail_in_both_modes(self):
        for examples in (0, 1):
            for rows in (
                [("a", "n", "g"), ("a", "n", "g"), ("b", "n", "g")],
                [("a", "n", "g"), ("a", "other", "g")],
                [("a", "n", "g"), ("unknown", "n", "g")],
                [],
            ):
                self.write([("a", "x"), ("b", "y")], rows)
                with (
                    self.subTest(examples=examples, rows=rows),
                    self.assertRaises(AuditError),
                ):
                    self.run_audit(examples=examples)

    def test_namespaces_and_record_group_names_do_not_alias(self):
        self.write(
            [("a", "x"), ("b", "y"), ("c", "z")],
            [("a", "x|y", "z"), ("b", "x", "y|z"), ("c", "a", "a")],
        )
        self.assertTrue(self.run_audit()["passed"])

    @unittest.skipIf(tracemalloc.is_tracing(), "do not alter caller allocation tracing")
    def test_counts_only_avoids_reverse_index_allocations_on_dense_inventory(self):
        records = [(f"r{i:04}", "one-split") for i in range(600)]
        memberships = [
            (key, "group", str(group)) for key, _ in records for group in range(30)
        ]
        self.write(records, memberships)
        retained = {}

        def groups(keys):
            retained[examples] = tracemalloc.get_traced_memory()[0]
            return _Groups(keys)

        for examples in (0, 1):
            gc.collect()
            tracemalloc.start()
            try:
                with patch("splitwitness.memberships._Groups", side_effect=groups):
                    report = self.run_audit(examples=examples)
            finally:
                tracemalloc.stop()
            self.assertTrue(report["passed"])
        # Live Python allocations at the pre-union checkpoint, after parsing
        # rows are released. Whole-process parsing peak may be unchanged.
        self.assertGreater(retained[1] - retained[0], 200_000)
