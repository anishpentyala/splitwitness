import contextlib
import csv
import hashlib
import io
import json
import random
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from splitwitness import AuditError, Limits, audit, audit_memberships
from splitwitness.__main__ import main


class MembershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.records = Path(self.temp.name) / "records.csv"
        self.memberships = Path(self.temp.name) / "memberships.csv"
        self.write(
            [("a", "train"), ("b", "test")],
            [("a", "patient", "123"), ("b", "patient", "123")],
        )

    def write(self, records, memberships):
        for path, header, rows in [
            (self.records, ["id", "split"], records),
            (self.memberships, ["record_id", "namespace", "group_id"], memberships),
        ]:
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(header)
                writer.writerows(rows)

    def audit(self, **kwargs):
        return audit_memberships(self.records, self.memberships, **kwargs)

    def cli(self, *args):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(
                [str(self.records), str(self.memberships), "--memberships", *args]
            )
        return code, output.getvalue(), error.getvalue()

    def test_direct_shared_group(self):
        r = self.audit(examples=1)
        self.assertFalse(r["passed"])
        self.assertEqual(
            r["counts"],
            {
                "records": 2,
                "memberships": 2,
                "groups": 1,
                "components": 1,
                "singleton_components": 0,
                "cross_split_groups": 1,
                "violating_components": 1,
                "affected_records": 2,
                "cross_split_record_pairs": 1,
            },
        )
        self.assertEqual(
            r["examples"][0]["witness"]["path"],
            [
                {
                    "left_id": "a",
                    "right_id": "b",
                    "namespace": "patient",
                    "group_id": "123",
                }
            ],
        )

    def test_namespace_is_part_of_identity(self):
        self.write(
            [("a", "train"), ("b", "test")],
            [("a", "patient", "123"), ("b", "session", "123")],
        )
        r = self.audit(examples=2)
        self.assertTrue(r["passed"])
        self.assertEqual(r["counts"]["components"], 2)
        self.assertEqual(r["counts"]["groups"], 2)

    def test_tuple_not_delimiter_key(self):
        self.write(
            [("a", "train"), ("b", "test")], [("a", "a|b", "c"), ("b", "a", "b|c")]
        )
        self.assertTrue(self.audit()["passed"])

    def test_transitive_mixed_namespaces(self):
        self.write(
            [("a", "train"), ("b", "train"), ("c", "test"), ("d", "test")],
            [
                ("a", "patient", "p"),
                ("b", "patient", "p"),
                ("b", "export", "e"),
                ("c", "export", "e"),
                ("c", "session", "s"),
                ("d", "session", "s"),
            ],
        )
        r = self.audit(examples=1)
        self.assertEqual(r["counts"]["affected_records"], 4)
        self.assertEqual(r["counts"]["cross_split_record_pairs"], 4)
        self.assertEqual(r["counts"]["cross_split_groups"], 1)
        self.assertEqual(r["examples"][0]["witness"]["hops"], 2)

    def test_shortest_group_path_not_star_expansion_path(self):
        self.write(
            [("a", "x"), ("b", "x"), ("c", "y")],
            [("a", "g", "1"), ("b", "g", "1"), ("c", "g", "1")],
        )
        w = self.audit(examples=1)["examples"][0]["witness"]
        self.assertEqual(w["hops"], 1)
        self.assertEqual(w["path"][0]["right_id"], "c")

    def test_shortest_path_over_multiple_routes(self):
        self.write(
            [(k, "y" if k == "z" else "x") for k in "abcdz"],
            [
                ("a", "n", "1"),
                ("b", "n", "1"),
                ("b", "n", "2"),
                ("z", "n", "2"),
                ("a", "n", "3"),
                ("c", "n", "3"),
                ("c", "n", "4"),
                ("d", "n", "4"),
                ("d", "n", "5"),
                ("z", "n", "5"),
            ],
        )
        w = self.audit(examples=1)["examples"][0]["witness"]
        self.assertEqual(w["hops"], 2)
        self.assertEqual(w["path"][0]["right_id"], "b")

    def test_lexical_group_then_member_tie_break(self):
        self.write(
            [("a", "x"), ("b", "x"), ("c", "x"), ("z", "y")],
            [
                ("a", "n", "g2"),
                ("b", "n", "g2"),
                ("b", "n", "end"),
                ("z", "n", "end"),
                ("a", "n", "g1"),
                ("c", "n", "g1"),
                ("c", "n", "end"),
            ],
        )
        w = self.audit(examples=1)["examples"][0]["witness"]
        self.assertEqual([e["right_id"] for e in w["path"]], ["c", "z"])

    def test_multiple_new_group_members_use_record_id_order(self):
        self.write(
            [("a", "train"), ("b", "train"), ("c", "train"), ("z", "test")],
            [
                ("a", "n", "first"),
                ("c", "n", "first"),
                ("b", "n", "first"),
                ("b", "n", "last"),
                ("c", "n", "last"),
                ("z", "n", "last"),
            ],
        )
        witness = self.audit(examples=1)["examples"][0]["witness"]
        self.assertEqual(witness["hops"], 2)
        self.assertEqual([edge["right_id"] for edge in witness["path"]], ["b", "z"])
        self.assertEqual(
            [edge["group_id"] for edge in witness["path"]], ["first", "last"]
        )

    def test_target_smallest_different_not_nearest(self):
        self.write(
            [("a", "x"), ("b", "y"), ("c", "x"), ("z", "y")],
            [
                ("a", "n", "direct"),
                ("z", "n", "direct"),
                ("a", "n", "step1"),
                ("c", "n", "step1"),
                ("c", "n", "step2"),
                ("b", "n", "step2"),
            ],
        )
        w = self.audit(examples=1)["examples"][0]["witness"]
        self.assertEqual(w["end_id"], "b")
        self.assertEqual(w["hops"], 2)

    def test_default_no_examples(self):
        r = self.audit()
        self.assertEqual(r["examples"], [])
        self.assertEqual(r["examples_omitted"], 1)

    def test_whole_omission_inclusive_cap(self):
        w = self.audit(examples=1, max_witness_edges=0)["examples"][0]["witness"]
        self.assertTrue(w["path_omitted"])
        self.assertIsNone(w["path"])
        self.assertEqual(w["hops"], 1)
        w = self.audit(examples=1, max_witness_edges=1)["examples"][0]["witness"]
        self.assertFalse(w["path_omitted"])
        self.assertEqual(len(w["path"]), 1)

    def test_singletons_and_single_split(self):
        self.write([("a", "x"), ("b", "x")], [("a", "n", "one"), ("b", "n", "two")])
        r = self.audit()
        self.assertTrue(r["passed"])
        self.assertEqual(r["counts"]["singleton_components"], 2)

    def test_redundant_shared_groups_count_once(self):
        self.write(
            [("a", "x"), ("b", "y")],
            [("a", "n", "g1"), ("b", "n", "g1"), ("a", "n", "g2"), ("b", "n", "g2")],
        )
        r = self.audit()
        self.assertEqual(r["counts"]["affected_records"], 2)
        self.assertEqual(r["counts"]["cross_split_groups"], 2)
        self.assertEqual(r["counts"]["cross_split_record_pairs"], 1)

    def test_membership_coverage_required(self):
        self.write([("a", "x"), ("b", "y")], [("a", "n", "g")])
        with self.assertRaises(AuditError):
            self.audit()

    def test_header_only_not_clean(self):
        self.write([("a", "x")], [])
        with self.assertRaises(AuditError):
            self.audit()

    def test_unknown_record(self):
        self.write([("a", "x")], [("a", "n", "g"), ("unknown", "n", "g")])
        with self.assertRaises(AuditError):
            self.audit()

    def test_duplicate_membership_rejected(self):
        self.write([("a", "x")], [("a", "n", "g"), ("a", "n", "g")])
        with self.assertRaises(AuditError):
            self.audit()

    def test_empty_records(self):
        self.write([], [])
        with self.assertRaises(AuditError):
            self.audit()

    def test_duplicate_records(self):
        self.write([("a", "x"), ("a", "x")], [("a", "n", "g")])
        with self.assertRaises(AuditError):
            self.audit()

    def test_split_limit(self):
        with self.assertRaises(AuditError):
            self.audit(limits=replace(Limits(), max_splits=1))

    def test_membership_cap_uses_max_links_inclusive(self):
        self.audit(limits=replace(Limits(), max_links=2))
        with self.assertRaises(AuditError):
            self.audit(limits=replace(Limits(), max_links=1))

    def test_empty_fields_rejected(self):
        for col in range(3):
            row = ["a", "n", "g"]
            row[col] = ""
            self.write([("a", "x")], [row])
            with self.subTest(col=col), self.assertRaises(AuditError):
                self.audit()

    def test_wrong_header_width_nul_utf8(self):
        for data in [
            b"record_id,group_id,namespace\na,n,g\n",
            b"record_id,namespace,group_id\na,n,g,extra\n",
            b"record_id,namespace,group_id\na,n,\0\n",
            b"record_id,namespace,group_id\na,n,\xff\n",
        ]:
            self.memberships.write_bytes(data)
            with self.subTest(data=data), self.assertRaises(AuditError):
                self.audit()

    def test_exact_whitespace_unicode_keys(self):
        self.write(
            [
                ("01", "x"),
                ("1", "y"),
                (" a", "x"),
                ("a", "y"),
                ("é", "x"),
                ("e\u0301", "y"),
            ],
            [
                ("01", "n", "01"),
                ("1", "n", "1"),
                (" a", "n", " a"),
                ("a", "n", "a"),
                ("é", "n", "é"),
                ("e\u0301", "n", "e\u0301"),
            ],
        )
        self.assertTrue(self.audit()["passed"])

    def test_quoted_multiline_identity(self):
        self.write(
            [("a", "x"), ("b", "y")], [("a", "n,\n", 'g"\n'), ("b", "n,\n", 'g"\n')]
        )
        edge = self.audit(examples=1)["examples"][0]["witness"]["path"][0]
        self.assertEqual(edge["namespace"], "n,\n")
        self.assertEqual(edge["group_id"], 'g"\n')

    def test_receipts_exact_and_no_paths(self):
        r = self.audit()
        for key, path in [("records", self.records), ("memberships", self.memberships)]:
            raw = path.read_bytes()
            self.assertEqual(
                r["inputs"][key],
                {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
            )
        self.assertNotIn(self.temp.name, json.dumps(r))

    def test_group_id_not_default_report(self):
        self.assertNotIn("123", json.dumps(self.audit()["counts"]))
        self.assertEqual(self.audit()["examples"], [])

    def test_invalid_options(self):
        for name in ["examples", "max_witness_edges"]:
            for value in [-1, True, "2", 1.5]:
                with (
                    self.subTest(name=name, value=value),
                    self.assertRaises(AuditError),
                ):
                    self.audit(**{name: value})
        with self.assertRaises(AuditError):
            self.audit(limits={})

    def test_record_byte_cap(self):
        with self.assertRaises(AuditError):
            self.audit(limits=replace(Limits(), max_bytes=10))

    def test_record_row_cap(self):
        with self.assertRaises(AuditError):
            self.audit(limits=replace(Limits(), max_records=1))

    def test_membership_field_cap(self):
        with self.assertRaises(AuditError):
            self.audit(limits=replace(Limits(), max_field_chars=2))

    def test_read_failure(self):
        self.memberships.unlink()
        with self.assertRaises(AuditError):
            self.audit()

    def test_cli_violation(self):
        code, out, err = self.cli("--examples", "1")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["mode"], "memberships")
        self.assertEqual(err, "")

    def test_cli_clean(self):
        self.write([("a", "x")], [("a", "n", "g")])
        code, out, err = self.cli()
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["passed"])
        self.assertEqual(err, "")

    def test_cli_missing_coverage_no_json(self):
        self.write([("a", "x")], [])
        code, out, err = self.cli()
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("every assigned", err)

    def test_cli_membership_memory_failure(self):
        with patch("splitwitness.__main__.audit_memberships", side_effect=MemoryError):
            code, out, err = self.cli()
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("memory", err)

    def test_distinct_mode_not_link_format(self):
        with self.assertRaises(AuditError):
            audit(self.records, self.memberships)

    def test_late_invalid_membership_no_json_or_values(self):
        self.memberships.write_text(
            self.memberships.read_text() + "private-id,patient,secret-group\n"
        )
        code, output, errors = self.cli("--examples", "10")
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertNotIn("private-id", errors)
        self.assertNotIn("secret-group", errors)
        self.assertNotIn(self.temp.name, errors)

    def test_dense_group_many_members_one_hop(self):
        records = [(f"id{i:05}", "train" if i < 1000 else "test") for i in range(2000)]
        self.write(records, [(key, "group", "shared") for key, _split in records])
        r = self.audit(examples=1)
        self.assertEqual(r["counts"]["groups"], 1)
        self.assertEqual(r["counts"]["memberships"], 2000)
        self.assertEqual(r["counts"]["cross_split_record_pairs"], 1_000_000)
        self.assertEqual(r["examples"][0]["witness"]["hops"], 1)

    def test_seeded_clique_oracle(self):
        rng = random.Random(1742)
        for case in range(180):
            n = rng.randrange(1, 15)
            ids = [f"r{i:02}" for i in range(n)]
            assignments = {key: rng.choice(["a", "b", "c"]) for key in ids}
            memberships = {(key, "self", key) for key in ids}
            for group in range(rng.randrange(1, 8)):
                namespace = rng.choice(["patient", "export", "session"])
                value = str(group)
                memberships.update(
                    (key, namespace, value) for key in ids if rng.random() < 0.3
                )
            rows = list(memberships)
            records = list(assignments.items())
            self.write(records, rows)
            r = self.audit(examples=100, max_witness_edges=100)
            # Build actual conceptual cliques, never production union-find/BFS.
            groups = {
                g: {key for key, ns, value in memberships if (ns, value) == g}
                for g in {(ns, value) for key, ns, value in memberships}
            }
            distance = {(a, b): 0 if a == b else n + 1 for a in ids for b in ids}
            for members in groups.values():
                for a in members:
                    for b in members:
                        if a != b:
                            distance[a, b] = 1
            for k in ids:
                for a in ids:
                    for b in ids:
                        distance[a, b] = min(
                            distance[a, b], distance[a, k] + distance[k, b]
                        )
            components = []
            remaining = set(ids)
            while remaining:
                root = min(remaining)
                members = {key for key in ids if distance[root, key] <= n}
                remaining -= members
                components.append(members)
            bad = [
                members
                for members in components
                if len({assignments[key] for key in members}) > 1
            ]
            with self.subTest(case=case):
                self.assertEqual(r["counts"]["components"], len(components))
                self.assertEqual(r["counts"]["violating_components"], len(bad))
                self.assertEqual(r["counts"]["affected_records"], sum(map(len, bad)))
                self.assertEqual(
                    r["counts"]["cross_split_groups"],
                    sum(
                        len({assignments[k] for k in group}) > 1
                        for group in groups.values()
                    ),
                )
                self.assertEqual(
                    r["counts"]["cross_split_record_pairs"],
                    sum(
                        assignments[a] != assignments[b]
                        for group in components
                        for a in group
                        for b in group
                        if a < b
                    ),
                )
                self.assertEqual(r["passed"], not bad)
                expected_roots = sorted(min(component) for component in bad)
                self.assertEqual(
                    [example["component_id"] for example in r["examples"]],
                    expected_roots,
                )
                expected_affected = {
                    split: sum(
                        assignments[key] == split for group in bad for key in group
                    )
                    for split in set(assignments.values())
                }
                self.assertEqual(
                    {
                        split: entry["affected_records"]
                        for split, entry in r["splits"].items()
                    },
                    expected_affected,
                )
                for sample in r["examples"]:
                    w = sample["witness"]
                    self.assertEqual(w["hops"], distance[w["start_id"], w["end_id"]])
                    component = next(group for group in bad if w["start_id"] in group)
                    self.assertEqual(w["start_id"], min(component))
                    self.assertEqual(
                        w["end_id"],
                        min(
                            key
                            for key in component
                            if assignments[key] != assignments[w["start_id"]]
                        ),
                    )
                    current = w["start_id"]
                    for edge in w["path"]:
                        self.assertEqual(current, edge["left_id"])
                        self.assertIn(
                            (current, edge["namespace"], edge["group_id"]), memberships
                        )
                        self.assertIn(
                            (edge["right_id"], edge["namespace"], edge["group_id"]),
                            memberships,
                        )
                        current = edge["right_id"]
                    self.assertEqual(current, w["end_id"])
                rng.shuffle(rows)
                rng.shuffle(records)
                self.write(records, rows)
                shuffled = self.audit(examples=100, max_witness_edges=100)
                del r["inputs"], shuffled["inputs"]
                self.assertEqual(r, shuffled)


if __name__ == "__main__":
    unittest.main()
