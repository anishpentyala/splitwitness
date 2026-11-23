import contextlib
import csv
import hashlib
import io
import json
import random
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from splitwitness import AuditError, Limits, audit
from splitwitness.__main__ import main


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.records = Path(self.temp.name) / "records.csv"
        self.links = Path(self.temp.name) / "links.csv"
        self.write([("a", "train"), ("b", "test")], [("a", "b", "entity")])

    def write(self, records, links):
        for path, header, rows in (
            (self.records, ["id", "split"], records),
            (self.links, ["left_id", "right_id", "relation"], links),
        ):
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(header)
                writer.writerows(rows)

    def audit(self, **kwargs):
        return audit(self.records, self.links, **kwargs)

    def cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = main([str(self.records), str(self.links), *args])
        return result, stdout.getvalue(), stderr.getvalue()

    def test_singleton_components_do_not_build_split_counters(self):
        self.write(
            [("a", "train"), ("b", "test"), ("c", "train"), ("d", "val")],
            [("b", "c", "entity")],
        )
        from collections import Counter

        sizes = []

        def tracked(values=()):
            values = list(values)
            sizes.append(len(values))
            return Counter(values)

        expected = self.audit(examples=2)
        with patch("splitwitness.core.Counter", side_effect=tracked):
            actual = self.audit(examples=2)
        self.assertEqual(actual, expected)
        self.assertEqual(sizes, [4, 0, 2])
        self.assertEqual(actual["counts"]["violating_components"], 1)
        self.assertEqual(actual["examples"][0]["component_id"], "b")

    def test_direct_crossing(self):
        report = self.audit(examples=1)
        self.assertFalse(report["passed"])
        self.assertEqual(
            report["counts"],
            {
                "records": 2,
                "links": 1,
                "components": 1,
                "isolated_records": 0,
                "violating_components": 1,
                "affected_records": 2,
                "crossing_links": 1,
                "cross_split_record_pairs": 1,
            },
        )
        self.assertEqual(
            report["examples"][0]["witness"]["path"],
            [{"left_id": "a", "right_id": "b", "relation": "entity"}],
        )

    def test_transitive_affected_records_and_pairs(self):
        self.write(
            [("a", "train"), ("b", "train"), ("c", "test"), ("d", "val")],
            [("a", "b", "patient"), ("b", "c", "duplicate"), ("c", "d", "session")],
        )
        report = self.audit(examples=1)
        self.assertEqual(report["counts"]["affected_records"], 4)
        self.assertEqual(report["counts"]["cross_split_record_pairs"], 5)
        self.assertEqual(report["counts"]["crossing_links"], 2)
        self.assertEqual(report["examples"][0]["witness"]["hops"], 2)
        self.assertEqual(report["splits"]["train"]["affected_records"], 2)

    def test_same_split_is_clean(self):
        self.write([("a", "train"), ("b", "train")], [("a", "b", "same")])
        report = self.audit(examples=2)
        self.assertTrue(report["passed"])
        self.assertEqual(report["counts"]["affected_records"], 0)
        self.assertEqual(report["examples"], [])

    def test_header_only_links_are_allowed(self):
        self.write([("a", "train"), ("b", "test")], [])
        report = self.audit()
        self.assertTrue(report["passed"])
        self.assertEqual(report["counts"]["isolated_records"], 2)
        self.assertEqual(report["counts"]["components"], 2)

    def test_empty_records_rejected(self):
        self.write([], [])
        with self.assertRaises(AuditError):
            self.audit()

    def test_disconnected_components_and_example_order(self):
        self.write(
            [
                (x, s)
                for x, s in [
                    ("z", "a"),
                    ("y", "b"),
                    ("a", "a"),
                    ("b", "b"),
                    ("i", "alone"),
                ]
            ],
            [("z", "y", "x"), ("a", "b", "x")],
        )
        report = self.audit(examples=1)
        self.assertEqual(report["counts"]["violating_components"], 2)
        self.assertEqual(report["counts"]["components"], 3)
        self.assertEqual(report["examples"][0]["component_id"], "a")
        self.assertEqual(report["examples_omitted"], 1)

    def test_no_examples_by_default(self):
        report = self.audit()
        self.assertEqual(report["examples"], [])
        self.assertEqual(report["examples_omitted"], 1)

    def test_self_link_redundant_not_isolated(self):
        self.write([("a", "x")], [("a", "a", "x")])
        report = self.audit()
        self.assertTrue(report["passed"])
        self.assertEqual(report["counts"]["isolated_records"], 0)
        self.assertEqual(report["counts"]["links"], 1)

    def test_multiple_relations_not_multiple_member_pairs(self):
        self.write([("a", "x"), ("b", "y")], [("a", "b", "r1"), ("b", "a", "r2")])
        report = self.audit(examples=1)
        self.assertEqual(report["counts"]["links"], 2)
        self.assertEqual(report["counts"]["crossing_links"], 2)
        self.assertEqual(report["counts"]["cross_split_record_pairs"], 1)
        self.assertEqual(report["examples"][0]["witness"]["path"][0]["relation"], "r1")

    def test_cycle_no_double_count(self):
        self.write(
            [("a", "x"), ("b", "y"), ("c", "x")],
            [("a", "b", "r"), ("b", "c", "r"), ("a", "c", "r")],
        )
        self.assertEqual(self.audit()["counts"]["cross_split_record_pairs"], 2)

    def test_shortest_path_not_first_dfs_path(self):
        self.write(
            [(x, "x" if x != "z" else "y") for x in "abcdz"],
            [
                ("a", "b", "r"),
                ("b", "z", "r"),
                ("a", "d", "r"),
                ("d", "c", "r"),
                ("c", "z", "r"),
            ],
        )
        witness = self.audit(examples=1)["examples"][0]["witness"]
        self.assertEqual(witness["hops"], 2)
        self.assertEqual(witness["path"][0]["right_id"], "b")

    def test_lexical_shortest_path_ties(self):
        self.write(
            [(x, "y" if x == "z" else "x") for x in "abcdz"],
            [
                ("z", "c", "q"),
                ("c", "a", "q"),
                ("z", "b", "z"),
                ("b", "a", "z"),
                ("b", "a", "a"),
            ],
        )
        witness = self.audit(examples=1)["examples"][0]["witness"]
        self.assertEqual(
            [(e["right_id"], e["relation"]) for e in witness["path"]],
            [("b", "a"), ("z", "z")],
        )

    def test_target_smallest_different_id_not_nearest(self):
        self.write(
            [("a", "x"), ("b", "y"), ("c", "x"), ("z", "y")],
            [("a", "z", "r"), ("a", "c", "r"), ("c", "b", "r")],
        )
        witness = self.audit(examples=1)["examples"][0]["witness"]
        self.assertEqual(witness["end_id"], "b")
        self.assertEqual(witness["hops"], 2)

    def test_path_cap_omits_whole_path(self):
        report = self.audit(examples=1, max_witness_edges=0)
        witness = report["examples"][0]["witness"]
        self.assertTrue(witness["path_omitted"])
        self.assertIsNone(witness["path"])
        self.assertEqual(witness["hops"], 1)
        self.assertFalse(report["passed"])

    def test_path_cap_inclusive(self):
        witness = self.audit(examples=1, max_witness_edges=1)["examples"][0]["witness"]
        self.assertFalse(witness["path_omitted"])
        self.assertEqual(len(witness["path"]), 1)

    def test_exact_unicode_whitespace_and_numeric_ids(self):
        rows = [
            ("01", "x"),
            ("1", "y"),
            (" a", "x"),
            ("a", "y"),
            ("é", "x"),
            ("e\u0301", "y"),
            ("\n", "x"),
        ]
        self.write(rows, [])
        report = self.audit()
        self.assertTrue(report["passed"])
        self.assertEqual(report["counts"]["records"], 7)

    def test_quoted_newline_comma_and_quotes(self):
        self.write([('a,"\n', "s,\n"), ("b", "t")], [('a,"\n', "b", 'r,"\n')])
        witness = self.audit(examples=1)["examples"][0]["witness"]
        self.assertEqual(witness["start_id"], 'a,"\n')
        self.assertEqual(witness["path"][0]["relation"], 'r,"\n')

    def test_duplicate_ids_even_same_split_rejected(self):
        for second in ("x", "y"):
            with self.subTest(second=second):
                self.write([("a", "x"), ("a", second)], [])
                with self.assertRaises(AuditError):
                    self.audit()

    def test_duplicate_undirected_link_rejected(self):
        for edge in [("a", "b", "r"), ("b", "a", "r")]:
            with self.subTest(edge=edge):
                self.write([("a", "x"), ("b", "y")], [("a", "b", "r"), edge])
                with self.assertRaises(AuditError):
                    self.audit()

    def test_unknown_endpoint_rejected(self):
        for edge in [("missing", "b", "r"), ("a", "missing", "r")]:
            with self.subTest(edge=edge):
                self.write([("a", "x"), ("b", "y")], [edge])
                with self.assertRaises(AuditError):
                    self.audit()

    def test_empty_fields_rejected(self):
        for target, text in [
            (self.records, "id,split\n,x\n"),
            (self.records, "id,split\na,\n"),
            (self.links, "left_id,right_id,relation\na,b,\n"),
            (self.links, "left_id,right_id,relation\n,b,r\n"),
        ]:
            with self.subTest(text=text):
                self.write([("a", "x"), ("b", "y")], [])
                target.write_text(text)
                with self.assertRaises(AuditError):
                    self.audit()

    def test_bad_headers(self):
        for text in [
            "",
            "id,id\na,x\n",
            "split,id\nx,a\n",
            "\ufeffid,split\na,x\n",
            "id,split,extra\na,x,z\n",
            " id,split\na,x\n",
        ]:
            with self.subTest(text=text):
                self.records.write_text(text, encoding="utf-8")
                with self.assertRaises(AuditError):
                    self.audit()

    def test_bad_links_header(self):
        self.links.write_text("right_id,left_id,relation\nb,a,r\n")
        with self.assertRaises(AuditError):
            self.audit()

    def test_ragged_blank_and_malformed_quotes(self):
        for text in [
            "id,split\na,x,z\n",
            "id,split\na\n",
            "id,split\n\n",
            'id,split\n"a,x\n',
            'id,split\n"a"bad,x\n',
        ]:
            with self.subTest(text=text):
                self.records.write_text(text)
                with self.assertRaises(AuditError):
                    self.audit()

    def test_invalid_utf8(self):
        self.records.write_bytes(b"id,split\na,\xff\n")
        with self.assertRaises(AuditError):
            self.audit()

    def test_nul_rejected_in_both_inputs(self):
        for target in (self.records, self.links):
            with self.subTest(target=target.name):
                self.write([("a", "x"), ("b", "y")], [("a", "b", "r")])
                target.write_bytes(target.read_bytes().replace(b"a", b"a\0"))
                with self.assertRaises(AuditError):
                    self.audit()

    def test_input_receipts_are_exact_bytes(self):
        report = self.audit()
        for key, path in [("records", self.records), ("links", self.links)]:
            raw = path.read_bytes()
            self.assertEqual(
                report["inputs"][key],
                {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
            )

    def test_lf_and_crlf_same_semantics_different_receipt(self):
        old = self.audit()
        for path in (self.records, self.links):
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
        new = self.audit()
        self.assertNotEqual(old.pop("inputs"), new.pop("inputs"))
        self.assertEqual(old, new)

    def test_byte_cap_exact_boundary(self):
        self.write([("a", "x")], [])
        size = max(self.records.stat().st_size, self.links.stat().st_size)
        self.assertTrue(self.audit(limits=replace(Limits(), max_bytes=size))["passed"])
        with self.assertRaises(AuditError):
            self.audit(limits=replace(Limits(), max_bytes=size - 1))

    def test_record_cap_boundary(self):
        self.audit(limits=replace(Limits(), max_records=2))
        with self.assertRaises(AuditError):
            self.audit(limits=replace(Limits(), max_records=1))

    def test_link_cap_boundary(self):
        self.write([("a", "x"), ("b", "y")], [("a", "b", "r1"), ("a", "b", "r2")])
        self.audit(limits=replace(Limits(), max_links=2))
        with self.assertRaises(AuditError):
            self.audit(limits=replace(Limits(), max_links=1))

    def test_split_cap_boundary(self):
        self.audit(limits=replace(Limits(), max_splits=2))
        with self.assertRaises(AuditError):
            self.audit(limits=replace(Limits(), max_splits=1))

    def test_field_cap_inclusive(self):
        self.write([("aa", "xx"), ("bb", "yy")], [("aa", "bb", "rr")])
        self.audit(limits=replace(Limits(), max_field_chars=2))
        with self.assertRaises(AuditError):
            self.audit(limits=replace(Limits(), max_field_chars=1))

    def test_invalid_limits(self):
        for field in vars(Limits()):
            for value in (0, -1, True, 1.5, "2", None):
                with (
                    self.subTest(field=field, value=value),
                    self.assertRaises(AuditError),
                ):
                    replace(Limits(), **{field: value})

    def test_invalid_options(self):
        for name in ("examples", "max_witness_edges"):
            for value in (-1, True, 1.5, "2", None):
                with (
                    self.subTest(name=name, value=value),
                    self.assertRaises(AuditError),
                ):
                    self.audit(**{name: value})
        with self.assertRaises(AuditError):
            self.audit(limits={})

    def test_unreadable_inputs(self):
        for path in (self.records, self.links):
            with self.subTest(path=path.name):
                self.write([("a", "x"), ("b", "y")], [])
                path.unlink()
                with self.assertRaises(AuditError):
                    self.audit()

    def test_open_error_redacts_source(self):
        with (
            patch("pathlib.Path.open", side_effect=OSError("private-secret")),
            self.assertRaises(AuditError) as caught,
        ):
            self.audit()
        self.assertNotIn("private-secret", str(caught.exception))

    def test_cli_violation_complete_report(self):
        code, output, errors = self.cli()
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output)["passed"])
        self.assertEqual(errors, "")

    def test_cli_clean(self):
        self.write([("a", "x")], [])
        code, output, errors = self.cli()
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["passed"])
        self.assertEqual(errors, "")

    def test_cli_invalid_no_partial_json_or_input_echo(self):
        self.links.write_text("left_id,right_id,relation\na,private-secret,r\n")
        code, output, errors = self.cli("--examples", "4")
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertNotIn("private-secret", errors)
        self.assertNotIn(str(self.temp.name), errors)

    def test_cli_resource_failure_no_json(self):
        code, output, errors = self.cli("--max-records", "1")
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("row limit exceeded", errors)

    def test_cli_memory_failure_no_json(self):
        with patch("splitwitness.__main__.audit", side_effect=MemoryError):
            code, output, errors = self.cli()
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("incomplete", errors)

    def test_cli_negative_argument_no_json(self):
        code, output, _errors = self.cli("--examples", "-1")
        self.assertEqual(code, 2)
        self.assertEqual(output, "")

    def test_actual_read_failure(self):
        with patch("pathlib.Path.open") as opened:
            opened.return_value.__enter__.return_value.read.side_effect = OSError(
                "private"
            )
            with self.assertRaises(AuditError) as caught:
                self.audit()
        self.assertNotIn("private", str(caught.exception))

    def test_native_parser_limit_not_changed(self):
        before = csv.field_size_limit()
        self.audit()
        self.assertEqual(csv.field_size_limit(), before)
        self.records.write_text("id,split\na," + "x" * (before + 1) + "\n")
        with self.assertRaises(AuditError):
            self.audit(limits=replace(Limits(), max_field_chars=before + 1))
        self.assertEqual(csv.field_size_limit(), before)

    def test_iterative_long_chain_witness_omitted(self):
        keys = [f"n{i:04}" for i in range(2000)]
        self.write(
            [(x, "test" if x == keys[-1] else "train") for x in keys],
            [(a, b, "family") for a, b in zip(keys, keys[1:])],
        )
        report = self.audit(examples=1)
        witness = report["examples"][0]["witness"]
        self.assertEqual(witness["hops"], 1999)
        self.assertTrue(witness["path_omitted"])
        self.assertIsNone(witness["path"])
        self.assertEqual(report["counts"]["affected_records"], 2000)
        self.assertEqual(report["counts"]["cross_split_record_pairs"], 1999)

    def test_cli_serialization_memory_failure(self):
        with patch("splitwitness.__main__.json.dumps", side_effect=MemoryError):
            code, output, _errors = self.cli()
        self.assertEqual(code, 2)
        self.assertEqual(output, "")

    def test_byte_count_read_overflow_fails_closed(self):
        with self.assertRaises(AuditError):
            self.audit(limits=replace(Limits(), max_bytes=10**100))

    def test_cli_broken_pipe_is_not_success(self):
        with patch("sys.stdout") as output:
            output.write.side_effect = BrokenPipeError
            self.assertEqual(main([str(self.records), str(self.links)]), 2)

    def test_module_entry_point(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "splitwitness",
                str(self.records),
                str(self.links),
                "--examples",
                "1",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["examples"][0]["component_id"], "a")
        self.assertEqual(result.stderr, "")

    def test_seeded_graph_oracle_and_permutation(self):
        rng = random.Random(7451)
        for case in range(160):
            n = rng.randrange(1, 16)
            records = [
                (f"n{i:02}", rng.choice(["train", "test", "val"])) for i in range(n)
            ]
            links = [
                (a, b, rng.choice(["patient", "duplicate"]))
                for i, (a, _) in enumerate(records)
                for b, _ in records[i:]
                if rng.random() < 0.15
            ]
            self.write(records, links)
            report = self.audit(examples=100, max_witness_edges=100)
            # Independent set-expansion oracle, no production union-find.
            remaining = dict(records)
            components = []
            while remaining:
                members = {min(remaining)}
                while True:
                    expanded = members | {
                        y
                        for a, b, _ in links
                        for x, y in [(a, b), (b, a)]
                        if x in members
                    }
                    if expanded == members:
                        break
                    members = expanded
                components.append(members)
                for key in members:
                    del remaining[key]
            assignments = dict(records)
            bad = [c for c in components if len({assignments[k] for k in c}) > 1]
            pairs = sum(
                assignments[a] != assignments[b]
                for c in components
                for a in c
                for b in c
                if a < b
            )
            # Independent all-pairs distances, not the production BFS.
            ids = sorted(assignments)
            distance = {(a, b): (0 if a == b else n + 1) for a in ids for b in ids}
            for a, b, _relation in links:
                if a != b:
                    distance[a, b] = distance[b, a] = 1
            for middle in ids:
                for a in ids:
                    for b in ids:
                        distance[a, b] = min(
                            distance[a, b], distance[a, middle] + distance[middle, b]
                        )
            with self.subTest(case=case):
                self.assertEqual(report["counts"]["components"], len(components))
                self.assertEqual(report["counts"]["violating_components"], len(bad))
                self.assertEqual(
                    report["counts"]["affected_records"], sum(map(len, bad))
                )
                self.assertEqual(report["counts"]["cross_split_record_pairs"], pairs)
                self.assertEqual(report["passed"], not bad)
                expected_bad = sorted(bad, key=min)
                self.assertEqual(
                    [example["component_id"] for example in report["examples"]],
                    [min(component) for component in expected_bad],
                )
                for sample, component in zip(report["examples"], expected_bad):
                    witness = sample["witness"]
                    root = min(component)
                    target = min(
                        k for k in component if assignments[k] != assignments[root]
                    )
                    self.assertEqual(witness["start_id"], root)
                    self.assertEqual(witness["end_id"], target)
                    self.assertEqual(witness["hops"], distance[root, target])
                    self.assertEqual(sample["records"], len(component))
                    current = witness["start_id"]
                    for edge in witness["path"]:
                        self.assertEqual(current, edge["left_id"])
                        self.assertTrue(
                            (edge["left_id"], edge["right_id"], edge["relation"])
                            in links
                            or (edge["right_id"], edge["left_id"], edge["relation"])
                            in links
                        )
                        current = edge["right_id"]
                    self.assertEqual(current, witness["end_id"])
                    self.assertNotEqual(witness["start_split"], witness["end_split"])
                rng.shuffle(records)
                rng.shuffle(links)
                links = [
                    (b, a, r) if rng.random() < 0.5 else (a, b, r) for a, b, r in links
                ]
                self.write(records, links)
                permuted = self.audit(examples=100, max_witness_edges=100)
                del report["inputs"], permuted["inputs"]
                self.assertEqual(report, permuted)


if __name__ == "__main__":
    unittest.main()
