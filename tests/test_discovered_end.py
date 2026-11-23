"""A discovered destination fixes the BFS path before it is dequeued."""

import unittest

from splitwitness.core import _witness
from splitwitness.memberships import _path


class ReadTracking(dict):
    def __init__(self, *args):
        super().__init__(*args)
        self.reads = []

    def __getitem__(self, key):
        self.reads.append(key)
        return super().__getitem__(key)


class DiscoveredEndTests(unittest.TestCase):
    def test_link_witness_does_not_expand_queued_siblings(self):
        adjacency = ReadTracking(
            {
                "a": [("b", "z"), ("c", "label"), ("b", "a")],
                "b": [("a", "a"), ("c", "other")],
                "c": [("a", "label"), ("b", "other")],
            }
        )
        splits = {"a": "train", "b": "train", "c": "test"}
        for cap in (0, 1, 2):
            adjacency.reads.clear()
            result = _witness(["a", "b", "c"], splits, adjacency, cap)
            self.assertEqual(adjacency.reads, ["a"])
            self.assertEqual(result["hops"], 1)
            self.assertEqual(result["path_omitted"], cap == 0)
            if cap:
                self.assertEqual(
                    result["path"],
                    [{"left_id": "a", "right_id": "c", "relation": "label"}],
                )

    def test_membership_witness_does_not_expand_queued_siblings(self):
        group = ("n", "shared")
        by_record = ReadTracking({key: {group} for key in "abc"})
        by_group = {group: set("abc")}
        for cap in (0, 1, 2):
            by_record.reads.clear()
            result = _path("a", "c", by_record, by_group, cap)
            self.assertEqual(by_record.reads, ["a"])
            self.assertEqual(result["hops"], 1)
            self.assertEqual(result["path_omitted"], cap == 0)
            if cap:
                self.assertEqual(result["path"][0]["group_id"], "shared")

    def test_link_shortest_path_relation_and_lexical_ties(self):
        adjacency = ReadTracking(
            {
                "a": [("c", "r"), ("b", "z"), ("b", "a")],
                "b": [("d", "first")],
                "c": [("d", "second")],
                "d": [],
            }
        )
        result = _witness(
            list("abcd"), dict(zip("abcd", ["x", "x", "x", "y"])), adjacency, 2
        )
        self.assertEqual(adjacency.reads, ["a", "b"])
        self.assertEqual(result["hops"], 2)
        self.assertEqual([hop["relation"] for hop in result["path"]], ["a", "first"])

    def test_membership_ties_and_multi_hop_discovery(self):
        start, left, right = [("n", key) for key in ("start", "left", "right")]
        by_record = ReadTracking(
            {"a": {start}, "b": {start, left}, "c": {start, right}, "d": {left, right}}
        )
        by_group = {start: set("abc"), left: set("bd"), right: set("cd")}
        result = _path("a", "d", by_record, by_group, 2)
        self.assertEqual(by_record.reads, ["a", "b"])
        self.assertEqual(result["hops"], 2)
        self.assertEqual([hop["group_id"] for hop in result["path"]], ["start", "left"])
