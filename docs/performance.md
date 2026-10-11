# Performance budgets

Pytincture 1.0 uses versioned regression budgets from
[`contracts/performance-budgets-v1.json`](../contracts/performance-budgets-v1.json).
They protect framework startup and request paths from major regressions; they
are not production capacity or service-level guarantees.

## Browser measurements

Each Chromium, Firefox, and WebKit E2E job records:

- cold authenticated startup from the first navigation through visible
  packaged UI readiness;
- warm authenticated startup after the browser has loaded and cached the
  runtime once; and
- p95 latency for 20 authenticated BFF calls from the running Pyodide app.

The job retains the raw browser result and a standardized
`qualification-evidence-authenticated-bff-{browser}.json` document even on
success. The standardized document includes the exact release-artifact hashes,
tested version, UTC timestamp, commit, run URL, and the measured values.

## Service measurements

Production-smoke CI runs a wheel-installed Uvicorn service and records p95
latency for:

- 500 health requests at concurrency 20;
- 40 generated `appcode.pyt` archives at concurrency 5; and
- 20 metadata-only HEAD requests for a 5.7 MiB public wheel at concurrency 5;
- 4 streamed GET requests for that wheel at concurrency 2; and
- 200 representative BFF calls at concurrency 10.

The resulting `performance-health.json`, `performance-appcode.json`,
`performance-public_asset_head.json`, `performance-public_asset.json`, and
`performance-bff.json` files are retained with
`qualification-evidence-performance-service.json`, which hashes each raw
result and the validated wheel, source distribution, and npm tarball.

The same job deliberately saturates a narrowly configured BFF admission gate.
`performance-saturation.json` must show both completed work and prompt `503`
overload responses, followed by successful BFF and health probes that prove
the worker recovered.

Budgets may only be relaxed with a documented reason and review of the
corresponding CI evidence. Application owners must establish tighter budgets
for their deployment, network, widgetset, and workload.

## BFF request overhead

Ordinary BFF calls include authentication, operation/graph validation, current
source verification, method execution, and bounded result preparation. A small
method's measured HTTP latency is not a universal fixed framework cost, and a
serialization microbenchmark is not an end-to-end capacity measurement.

The optimization for [#385](https://github.com/pytincture/pytincture/issues/385)
keeps the following boundaries:

- Plain exact-builtin JSON values are copied under the existing item/depth
  budgets. That private snapshot cannot be changed by the caller before
  encoding. SQLAlchemy-private keys, non-string keys, subclasses, models,
  dataclasses, and iterators keep the compatibility conversion path.
- A conservative UTF-8 JSON size bound admits the C encoder **before** allocating
  the complete encoded response. If the estimate exceeds the byte budget, the
  incremental encoder checks actual bytes instead. Conservative estimates do
  not reject otherwise-valid responses. NaN, infinity, cycles, and invalid
  Unicode remain rejected; Pydantic serializers and aliases remain supported.
- Compiled source and static definition fingerprints use separate 128-entry
  LRU caches. Compilation entries admit at most 64 KiB of input source; analysis
  entries admit source strings whose Python object size is at most 64 KiB.
  Larger sources remain supported without entering those caches. Cache entries
  retain immutable code/analysis, not modules, instances, users, policy results,
  or live attributes. Source files are reopened and verified on every load,
  and module-level code still executes on every request.
- Authentication, replay validation, policy checks, worker dispatch, admission,
  and deadlines retain their existing order and behavior. The shared encoder
  also benefits isolated execution and individual streaming items without
  changing their dispatch or response lifecycle.

Run the diagnostic microbenchmark against both versions with the same Python
and dependency versions, using separate environments or an explicit source
path. For a source checkout:

```bash
PYTHONPATH=. python scripts/benchmark_bff_hot_path.py --output bff-hot-path.json
```

The report records dependency versions, source hashes, response sizes, and
unprofiled timings. It measures ping/page result preparation and warm source
loading. It deliberately has no machine-dependent pass/fail time threshold.
Run baseline and candidate load tests sequentially without another test suite
competing for the same CPU, and retain both raw reports.

### Local comparison with RC13

[Recorded measurements](benchmarks/bff-overhead-385.json) compare published
RC13 with this change on an Apple M1 Pro using Python 3.13.5 and identical
dependencies. The 32,946-byte synthetic page's median result preparation fell
from 2.69 ms to 0.62 ms; warm source loading fell from 0.33 ms to 0.22 ms.
These component timings do not describe total HTTP latency.

The real example at commit `d135b4f`, with one Uvicorn process, 100 rows per
response, and the example's existing load-profile limits, produced:

| Active callers | RC13 requests/s | Candidate requests/s | RC13 p95 | Candidate p95 |
| --- | ---: | ---: | ---: | ---: |
| 50 | 55.4 | 73.9 | 1,005 ms | 800 ms |
| 100 | 62.2 | 69.9 | 1,843 ms | 1,575 ms |
| 250 | 56.8 | 66.3 | 4,665 ms | 3,998 ms |

Both runs returned zero request errors. This is a local, single-run comparison,
not the two-core GitHub runner; timings vary. The 100/250-caller stages still
exceed the unchanged one-second p95 budget, so this improvement does not fully
resolve #385's concurrency target.

The example load profile uses 50/100/250 simultaneous callers, each making four
requests without user think time. The one-second p95 budget at 250 callers
requires substantially more capacity than a service with 250 signed-in but
mostly idle users. More queued calls do not increase CPU capacity. Benchmark
one and multiple Uvicorn worker processes for the intended hardware, including
an I/O-bound workload; increasing worker threads is not a substitute for
measuring process capacity. Keep the example's latency budget unchanged when
assessing this optimization.
