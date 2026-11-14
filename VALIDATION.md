# 0.2.2: capped witness reconstruction

Local validation on 2026-11-14, Linux. This is source-install evidence, not a
hosted CI result, registry release, adoption or general performance claim.

Both link and membership mode first count the exact predecessor-chain distance.
Only a path that fits the inclusive cap is materialized as edge dictionaries.
BFS, endpoint selection, tie breaking, graph storage, counts, schemas, report
settings, complete-input validation and input receipts are unchanged. A retained
path is walked a second time. The cap is not a traversal or hard RAM bound.

## Checks

- The original 96 test methods are byte-unchanged. Four new methods give 100
  passing tests on actual local CPython 3.9, 3.10, 3.11, 3.13, 3.14 and 3.15.
- The new tests cover zero/short/exact/overlong caps, lexical relation and
  namespace ties, exact omitted distances, and allocation of long omitted paths
  in each mode. The allocation checks compare traced helper peaks with a full
  retained path, using a broad difference rather than a process-memory cap.
  They skip if an enclosing caller is already tracing allocations, so as not to
  stop or reset that caller's tracing.
- Against the actual 0.2.1 source, the same 100 methods produce two assertion
  failures and zero errors: omitted and retained helper peaks are equal, instead
  of the expected reduction. The other 98 methods pass.
- 1,000 seeded generated graph/inventory input pairs, five combinations of
  example and path caps, and two modes give 10,000 complete-report equalities
  against 0.2.1. Receipts, witness paths, hop counts and all counts are included.
- Six targeted production mutations fail by assertions with zero test errors:
  exclusive cap in either mode, doubled distance in either mode, and missing
  path reversal in either mode. Failure counts are 1, 1, 5, 5, 2 and 2,
  respectively, including failing subtests. Intact and restored four-test
  focused suites pass.
- Ruff 0.17.5 check/format passes for `src` and `tests` (eight formatted files).
  Wheel and sdist build. Neutral installed-wheel runs on actual Python 3.9 and
  3.14 each pass all 100 tests.

## Scoped local measurement

These are helper-only measurements with an already constructed graph, not
whole-audit peak RAM or speed measurements. Three fresh Python 3.14.9 processes
per source/scenario; time is measured without allocation tracing, then the helper
is run again to measure peak traced Python allocations. Values below are medians.
The link and membership helper runs share a process in that order. Do not infer
independent whole-process RSS reductions from them.

For a 20,000-record chain, distance 19,999 and cap 20 (path omitted):

| Helper | 0.2.1 traced peak bytes | 0.2.2 traced peak bytes | 0.2.1 seconds | 0.2.2 seconds |
| --- | ---: | ---: | ---: | ---: |
| Link witness | 5,549,472 | 1,696,816 | 0.009677 | 0.007188 |
| Membership witness | 7,646,664 | 4,296,416 | 0.016614 | 0.013625 |

When the same path is retained (cap 20,000), traced peaks are unchanged. Link
runtime rises from 0.009435 to 0.010497 seconds, and membership runtime from
0.017568 to 0.019106 seconds. A 40-record retained path also has unchanged traced
peaks and is slightly slower locally. These results support avoiding discarded
edge dictionaries for overlong witnesses, not a universal speed/RAM promise.
