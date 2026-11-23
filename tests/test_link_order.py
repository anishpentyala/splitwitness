"""Link canonicalization preserves exact string order and duplicate checks."""

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from splitwitness import AuditError, audit, core


class LinkOrderTests(unittest.TestCase):
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
                writer = csv.writer(stream)
                writer.writerow(header)
                writer.writerows(values)

    def test_reversed_unicode_duplicate_retains_record_error(self):
        for left, right in [("é", "e\u0301"), ("a", "a"), ("10", "2"), ("😀", "中")]:
            with self.subTest(left=left, right=right):
                records = list(dict.fromkeys([left, right]))
                self.write(
                    [(key, "train") for key in records],
                    [(left, right, "r"), (right, left, "r")],
                )
                for examples in (0, 2):
                    with self.assertRaisesRegex(
                        AuditError, "duplicate undirected link at record 3"
                    ):
                        audit(self.records, self.links, examples=examples)

    def test_different_relations_are_not_duplicates(self):
        self.write(
            [("é", "train"), ("a", "test")], [("é", "a", "first"), ("a", "é", "second")]
        )
        report = audit(self.records, self.links, examples=1)
        self.assertEqual(report["counts"]["links"], 2)
        self.assertEqual(report["counts"]["crossing_links"], 2)
        self.assertEqual(report["counts"]["cross_split_record_pairs"], 1)
        self.assertEqual(
            report["examples"][0]["witness"]["path"][0]["relation"], "first"
        )

    def test_link_pairs_do_not_call_sorted(self):
        self.write([("z", "train"), ("a", "test")], [("z", "a", "r")])
        original = sorted
        calls = []

        def record(value, *args, **kwargs):
            calls.append(value)
            return original(value, *args, **kwargs)

        with patch.object(core, "sorted", record, create=True):
            report = audit(self.records, self.links)
        self.assertFalse(report["passed"])
        self.assertFalse(
            any(isinstance(value, tuple) and len(value) == 2 for value in calls)
        )
