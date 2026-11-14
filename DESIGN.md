# Design boundaries

1. Read each file up to max_bytes + 1, fail if oversized, decode strict UTF-8,
   reject NUL, parse exact CSV headers and field/row acceptance limits. Hash the
   same raw bytes used for parsing. No second read and no trusting a file stat.
2. Validate all unique assignments and every link before reporting anything.
   Reject exact/reversed duplicate (endpoint pair, relation) constraints.
3. Use union-by-size and iterative path compression to form connected components.
   Store both adjacency directions for witness extraction. Relation strings do
   not choose behavior. Self-links add no union but remain input links.
4. Iterate record IDs in sorted order. Each component's member list is therefore
   sorted independently of union-find roots or input order. Count each member
   once. A component violates the policy iff its set of split labels has size >1.
5. Count cross-split member pairs by (n*n - sum(split_size*split_size)) // 2.
   Never enumerate the quadratic number of pairs. Counts are Python integers.
6. Sort violating components by minimum ID. For each requested example, BFS from
   minimum ID to smallest differently assigned ID, sorting neighbors by
   (neighbor ID, relation). A predecessor is assigned only on first discovery.
   This selects a deterministic shortest endpoint-to-endpoint path. If it exceeds
   the report cap, omit the whole path while retaining hop count and endpoints.
7. Construct the full report and serialize it before writing stdout. No invalid
   input yields a partial success report. An output write itself can still fail
   partially. No report is a certificate of underlying relationship truth.

Graph structure is O(V+E) memory in addition to input parsing and component lists.
Union/find has near-linear amortized work. Sorting records costs O(V log V);
neighbor sorting across sampled components is bounded by O(sum deg(v) log deg(v))
there; BFS work is O(V+E) over the sampled disjoint components. Witness arrays
may be as long as a component before the output cap is applied. These statements
are algorithm descriptions, not measured memory/time guarantees. Duplicate
relation labels on different pairs are fine. Multiple relations on one pair count
as multiple edges. The report distinguishes direct edges from connected member
pairs so redundant evidence cannot inflate the affected-record count.

All split labels are symmetric. There are no implicit allowed crossings,
relation exclusions, confidence thresholds, group inference, split generation,
automatic data movement, fuzzy matching or payload scans. Real evaluation design
and false/omitted edges remain the caller's responsibility. No claims about
statistical independence or score impact follow from this graph calculation.

## Opt-in membership mode

See MEMBERSHIPS.md. It uses an incidence representation keyed by exact namespace/
group tuples; each membership participates in union-find and witness traversal.
BFS expands groups once, so a shared group is a single hop between any members
without materializing all its pairs. Every record needs coverage. It returns its
own explicit mode and count fields; the original link-mode implementation and
report remain unchanged. CLI chooses the auditor only by the explicit flag.
