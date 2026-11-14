"""Omitted witnesses keep exact distances without building edge dictionaries."""

import gc
import tracemalloc
import unittest
from collections import defaultdict

from splitwitness.core import _witness
from splitwitness.memberships import _path


class CappedPathTests(unittest.TestCase):
    def chain(self, size):
        ids = [f"r{i:05d}" for i in range(size)]
        splits = dict.fromkeys(ids, "train")
        splits[ids[-1]] = "test"
        adjacency = defaultdict(list)
        by_record, by_group = defaultdict(set), {}
        for left, right in zip(ids, ids[1:]):
            adjacency[left].append((right, "link"))
            adjacency[right].append((left, "link"))
            group = ("patient", left)
            by_record[left].add(group)
            by_record[right].add(group)
            by_group[group] = {left, right}
        return ids, splits, adjacency, by_record, by_group

    def peak(self, function, args, cap):
        gc.collect()
        tracemalloc.start()
        try:
            report = function(*args, cap)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        return report, peak

    @unittest.skipIf(
        tracemalloc.is_tracing(), "do not alter caller's allocation tracing"
    )
    def test_link_omission_avoids_full_path_allocation(self):
        ids, splits, adjacency, _, _ = self.chain(4000)
        args = (ids, splits, adjacency)
        omitted, small_peak = self.peak(_witness, args, 20)
        full, full_peak = self.peak(_witness, args, len(ids))
        self.assertEqual(omitted["hops"], len(ids) - 1)
        self.assertIsNone(omitted["path"])
        self.assertTrue(omitted["path_omitted"])
        self.assertEqual(len(full["path"]), omitted["hops"])
        # A broad allocation difference, not a process-RSS or timing bound.
        self.assertGreater(full_peak - small_peak, len(ids) * 80)

    @unittest.skipIf(
        tracemalloc.is_tracing(), "do not alter caller's allocation tracing"
    )
    def test_membership_omission_avoids_full_path_allocation(self):
        ids, _, _, by_record, by_group = self.chain(4000)
        args = (ids[0], ids[-1], by_record, by_group)
        omitted, small_peak = self.peak(_path, args, 20)
        full, full_peak = self.peak(_path, args, len(ids))
        self.assertEqual(omitted["hops"], len(ids) - 1)
        self.assertIsNone(omitted["path"])
        self.assertTrue(omitted["path_omitted"])
        self.assertEqual(len(full["path"]), omitted["hops"])
        self.assertGreater(full_peak - small_peak, len(ids) * 80)

    def test_link_boundary_and_lexical_relation_tie(self):
        ids, splits, adjacency, _, _ = self.chain(4)
        adjacency[ids[0]].append((ids[1], "aaa"))
        for cap in (0, 2, 3, 4):
            with self.subTest(cap=cap):
                result = _witness(ids, splits, adjacency, cap)
                self.assertEqual(result["hops"], 3)
                self.assertEqual(result["path_omitted"], cap < 3)
                if cap < 3:
                    self.assertIsNone(result["path"])
                else:
                    self.assertIsNotNone(result["path"])
                    self.assertEqual(result["path"][0]["relation"], "aaa")
                    self.assertEqual(
                        [(e["left_id"], e["right_id"]) for e in result["path"]],
                        list(zip(ids, ids[1:])),
                    )

    def test_membership_boundary_and_namespace_tie(self):
        ids, _, _, by_record, by_group = self.chain(4)
        by_record[ids[0]].add(("aaa", "alternate"))
        by_record[ids[1]].add(("aaa", "alternate"))
        by_group[("aaa", "alternate")] = set(ids[:2])
        for cap in (0, 2, 3, 4):
            with self.subTest(cap=cap):
                result = _path(ids[0], ids[-1], by_record, by_group, cap)
                self.assertEqual(result["hops"], 3)
                self.assertEqual(result["path_omitted"], cap < 3)
                if cap < 3:
                    self.assertIsNone(result["path"])
                else:
                    self.assertIsNotNone(result["path"])
                    self.assertEqual(result["path"][0]["namespace"], "aaa")
                    self.assertEqual(result["path"][0]["group_id"], "alternate")
                    self.assertEqual(
                        [(e["left_id"], e["right_id"]) for e in result["path"]],
                        list(zip(ids, ids[1:])),
                    )
