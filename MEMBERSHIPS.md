# Audit declared group memberships

Sometimes the input is a patient/session/export inventory rather than a list of
pairs. Use the opt-in membership mode to avoid creating a clique of every pair
in a group. This is part of splitwitness, not a separate detection algorithm.

```sh
splitwitness examples/records.csv examples/memberships.csv --memberships --examples 1
```

Records keep the exact `id,split` format. The second CSV has the exact header:

```csv
record_id,namespace,group_id
photo-001,patient,patient-A
photo-002,patient,patient-A
photo-002,export,export-B
photo-003,export,export-B
photo-003,session,session-C
photo-004,session,session-C
unrelated-005,singleton,unrelated-005
```

A record may have multiple memberships. A group is the exact tuple
`(namespace, group_id)`: patient `123` does not connect to session `123` unless
some record explicitly belongs to both. A record belonging to two groups connects
their families transitively. Group values are neither payloads nor verified real
identities. False group values and false joins are the caller's responsibility.

Every assigned record **must** have at least one membership, including records
that otherwise have no related examples. Use a deliberate singleton group for
those records; don't invent a common "unknown" group. Header-only membership
input, missing coverage, unknown record IDs, duplicate membership triples and
empty fields all fail with exit 2 and no JSON. All fields use the same exact
string/CSV validation as link mode. Empty strings do not mean "missing". There
is no implicit per-record singleton, null inference or silently dropped row.
A complete singleton inventory can still be a false assertion by its caller.

## Different report, same narrow promise

Membership mode has `mode: "memberships"`, schema_version1 and policy
`all_declared_groups_must_stay_within_one_split`. Do not treat its counts as link
mode counts. `memberships` counts accepted input rows; `groups` counts namespaced
identities; `cross_split_groups` counts groups whose members themselves span
splits. `components`, `violating_components`, `affected_records`, per-split counts
and unordered `cross_split_record_pairs` describe the full connected families.
A family can cross partitions even when some of its individual groups do not.
`singleton_components` counts one-record components, not one-member groups.
There is no generated-edge count or direct-link count because no edges are input.
Multiple shared groups cannot count an affected record or member pair twice.

Examples retain the same component/endpoint selection: smallest component record
ID to smallest differently assigned record ID. In this mode a hop means two
records share a group. A witness is shortest in number of these group-sharing
hops, as if every group were a clique, **not** a shortest path through an arbitrary
star expansion. A breadth-first traversal expands a group's memberships once;
it never builds the quadratic set of all pairwise links. Groups at a visited
record are considered in `(namespace, group_id)` Unicode lexical order; members
of each group in record-ID order. First discovery sets the predecessor. This is
a deterministic tie-break, not a promise to choose the lexicographically smallest
record-only path. It may differ from link mode's neighbor/relation ordering.

Each witness edge has left_id, right_id, namespace and group_id. This explicitly
shows why the two endpoints were connected. The values are caller assertions,
not an authenticated identity or data-provenance proof. If the witness exceeds
`--max-witness-edges`, its entire path is omitted, with exact hops/endpoints and
`path_omitted: true`. The cap is inclusive. The exact predecessor-chain distance is counted first;
edge dictionaries are built only if the complete path fits the cap. The BFS
queue, predecessor map and inventory graph still take memory. Retained paths
are walked a second time. This is not a traversal, hard RAM or speed guarantee.

## Limits, cost and disclosure

The existing `--max-links` option caps **membership rows** when `--memberships`
is selected. Other byte/record/field/split caps and 0/1/2 exit meanings are unchanged.
The existing positional `links` argument is the membership CSV only when this
flag is present. There is no format auto-detection. Normal `audit`/CLI link mode
and its report schema/default settings are unchanged.

All inventory strings and associations are in memory, without temporary files or
network calls. Storage is O(records + groups + memberships + input bytes), not
O(sum group_size squared). Group/member sorting and selected-component witness
searches add work. A group's members are expanded once per witness. A large
connected family may still take substantial memory/time, and acceptance caps are
not a hard bound on either. Witness arrays exist before the output cap applies.
No general runtime or RAM promise is made.

Default examples remain zero: group values/namespace labels do not appear in
report counts or per-split summaries. Opting into examples exposes record IDs,
namespace labels **and group IDs**. Even without examples, split labels, counts
and exact input SHA-256 digests can be sensitive. The receipts bind the exact CSV
bytes parsed; they do not establish relationship truth, inventory completeness,
or a simultaneous immutable snapshot of both input paths. No secure memory
clearing. No identity inference, data movement, automatic repair or independence
certificate. OS kills and output write failures have the same limitations as
link mode.

## Library

```python
from splitwitness import audit_memberships
report = audit_memberships("records.csv", "memberships.csv", examples=1)
```

The same `Limits`, `AuditError`, examples and max_witness_edges options apply.
The caller chooses suitable namespaces/groups and must not confuse syntactically
complete coverage with a truthful or exhaustive relationship inventory.
