# splitwitness

Audit an existing train/validation/test assignment against explicit links that
must stay in one partition. Get exact component counts and optional short,
reproducible evidence paths. Runs offline, with Python's standard library.

A duplicate export can connect two patient groups even when the patient IDs do
not overlap. Checking each grouping column separately misses the full affected
family. splitwitness follows all supplied links transitively, reports the whole
connected component and counts each affected record once.

This is a constraint checker, not an identity detector or an independence test.
The caller decides which relationships should prohibit crossing a split. A clean
report only says those **declared** links stayed within partitions. It says
nothing about missing links, false identity claims, model quality, statistical
independence, temporal leakage, feature leakage or contamination from pretraining.

## Install and run

Python 3.9 or newer. From a copy of this source tree:

```sh
python -m pip install .
splitwitness examples/records.csv examples/links.csv --examples 2
# exits 1: a complete report found a violated constraint
splitwitness examples/records.csv examples/clean-links.csv
# exits 0: all supplied constraints satisfied
```

The first example has five records in two connected components. Four records
belong to a component spanning train and test. Only one input link crosses the
boundary, but all four records are affected. There are four unordered cross-split
record pairs in that component. The fifth record is isolated and not affected.
The example witness goes from photo-001 to photo-003 in two hops, through
same-patient and duplicate-export links.

## Group inventories

Opt-in `--memberships` accepts complete namespaced group inventories instead of
explicit link pairs, without expanding each group into a quadratic clique.
See [MEMBERSHIPS.md](MEMBERSHIPS.md) for its separate counts, shortest group-hop
witnesses and additional disclosure of group IDs in opt-in examples. Link mode
and its report stay unchanged.

## Manifests

`records.csv`, with this exact ordered header:

```csv
id,split
photo-001,train
photo-002,train
photo-003,test
```

`links.csv`, with this exact ordered header:

```csv
left_id,right_id,relation
photo-001,photo-002,same-patient
photo-002,photo-003,duplicate-export
```

- Every record has exactly one assignment. Empty record manifests and duplicate
  IDs are errors, including repeated IDs with the same split.
- Every endpoint must exist in records. Unknown endpoints fail the entire audit.
- Links are undirected co-location constraints. Every relation participates.
  Labels are descriptive, not trusted provenance, confidence scores or filters.
  A low-confidence match should be reviewed before including it.
- Reverse duplicates with the same relation are errors. Different relations on
  the same pair are allowed and counted as separate input links, not extra
  record pairs. Self-links are allowed, redundant constraints.
- Header-only links are valid: all records are isolated. This does not verify
  that the caller supplied a complete relationship inventory.
- IDs, splits and relations are exact nonempty strings. No trimming, numeric
  conversion, case folding, whitespace collapse or Unicode normalization occurs.
  `01`, `1`, ` a` and `a` are distinct. Even whitespace-only fields are strings.
- UTF-8, comma-separated CSV with Python `csv.reader(strict=True)` semantics.
  Quoted commas, quotes and newlines work. NUL, BOM-prefixed headers, blank data
  records, wrong headers, ragged records and invalid UTF-8 fail. This is not a
  promise to implement every RFC interpretation; Python accepts bare quotes in
  some unquoted fields. The process's native CSV field cap is an additional
  parser bound and is not changed by this library.

## Report and exit codes

The CLI writes one complete JSON report to stdout after both manifests pass
validation. It never edits inputs, repairs splits, trains models, downloads data
or executes relation labels.

- `0`: complete audit, constraints satisfied.
- `1`: complete audit, at least one connected component spans splits.
- `2`: audit incomplete, invalid invocation/input/resource cap, or broken output
  pipe. Validation failures emit a diagnostic on stderr and **no JSON**.

`counts` includes records, accepted links, connected components, isolated records
(no incident link, so a self-linked record is not isolated), violating components,
affected records, direct crossing links, and unordered cross-split record pairs
within components. All counts cover the full validated inputs, not a sample.
`cross_split_record_pairs` counts connected pairs in different splits, whether or
not they share a direct link. It is a constraint exposure count, not a measure of
leakage probability or model-score inflation. `splits` gives total and affected
records for each exact split label. No train/test semantics are assumed.

Examples default to zero. `--examples N` selects the first N violating components
by their smallest record ID (Python Unicode code-point ordering). An example has
that ID, component size, split counts, and one witness. The witness starts at the
smallest component ID and ends at the smallest ID in a different split. It is a
shortest path **between those chosen endpoints**, not the closest cross-split
pair. Breadth-first search visits neighbor-ID/relation pairs in sorted order to
break ties. Input order, edge orientation and union-find root choice do not change
semantic results.

`--max-witness-edges N` defaults to 20. A longer path is omitted entirely, with
`path: null`, `path_omitted: true` and its exact hop count. It is not presented as
a clipped proof. This bounds report evidence size, not traversal cost. Increasing
examples or the path limit can expose more identifiers and make reports large.

`inputs` contains SHA-256 and byte counts of the exact bytes parsed from each
file, including headers/newlines. Permuting equivalent CSV changes the receipts,
not the semantic counts or witness selection. Digests are not signatures and do
not authenticate provenance, completeness or an immutable simultaneous snapshot
of both files. Each input is read once, sequentially.

## Limits and privacy

Acceptance caps are configurable with `--max-bytes` (per file, default 16 MiB),
`--max-records` (100,000), `--max-links` (500,000), `--max-field-chars` (1,024 per
field) and `--max-splits` (100). Positive integers only; examples and witness-edge
caps also allow zero. Caps fail closed; nothing is silently clipped or skipped.
An empty links file still needs the header. A single populated split is accepted.

The graph, CSV bytes and parsed strings live in process memory. This is not a
streaming/disk-backed implementation and caps are **not** hard RAM or runtime
bounds. Memory use is O(input bytes + records + links); retained graph adjacency
includes both orientations. Sorting and witness searches add work. Each sampled
component is searched separately. Membership mode has its own incidence-graph
costs described in MEMBERSHIPS.md. Avoid untrusted device/FIFO paths or files that
may block indefinitely. There is no timeout or sandbox for filesystem reads.

No network calls, telemetry, persistent cache or scratch database. No payload
columns are accepted. Still, identifiers, split labels and relationships can be
sensitive. Default reports include split labels, counts and input digests;
opt-in examples include record IDs and relation labels. Library exceptions and
CLI diagnostics identify input roles and record numbers, not field values or
paths. Python tracebacks from misuse are not a private reporting channel. Do not
share reports without checking them. There is no secure memory erasure guarantee.

One erroneous edge can connect large unrelated families. The report proves only
reachability under the declared undirected constraints, not real-world identity.
Do not automatically repair data or move evaluation rows based on this output.

## Python API

```python
from splitwitness import AuditError, Limits, audit

try:
    report = audit("records.csv", "links.csv", examples=2,
                   max_witness_edges=10,
                   limits=Limits(max_records=50_000))
except AuditError as error:
    print(error)  # incomplete, not a clean audit
else:
    assert report["passed"], report["counts"]
```

`audit` returns a JSON-ready dictionary or raises `AuditError` for invalid
manifests/options or ordinary filesystem/parser failures. It expects filesystem
paths. Programming errors, interruptions and process memory exhaustion may still
raise their normal exceptions. The CLI catches memory exhaustion before JSON
serialization completes; an OS kill cannot be caught. Output I/O failures are not
an atomic-output guarantee: a consumer can receive a partial write if the pipe or
filesystem fails while stdout is being written.

## Development

```sh
python -m unittest discover -s tests -v  # after install, or PYTHONPATH=src
ruff check src tests
ruff format --check src tests
python -m build
```

Tests include a separate set-expansion graph oracle and shuffled/orientation-
reversed inputs. See [DESIGN.md](DESIGN.md) for algorithms and [EVIDENCE.md](EVIDENCE.md)
for motivation and existing alternatives. This project does not claim a new graph
algorithm or unique leakage detection. Source install only; no registry release
or outside adoption is implied.
