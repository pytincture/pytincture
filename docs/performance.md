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

### Optional cache revalidation

`BFF_CACHE_REVALIDATION=true` is the default. It preserves registry source-digest
checks and application-graph source/directory freshness checks on warm calls.
An immutable-code benchmark can explicitly opt out.

For the pinned example at `d135b4f`, first forward the setting in its explicit
`PytinctureConfig(...)` constructor in `example/run.py`:

```python
bff_cache_revalidation={"true": True, "false": False}[
    os.getenv("BFF_CACHE_REVALIDATION", "true").strip().lower()
],
```

Applications using `PytinctureConfig.from_env()` already read the environment
setting. An explicit `PytinctureConfig(...)` keeps its typed defaults unless
the field is passed; an environment variable alone does not override it.

```bash
# Run in the example checkout, using the candidate Pytincture installation.
BFF_CACHE_REVALIDATION=true python tests/load_test.py --output validation-on.json
BFF_CACHE_REVALIDATION=false python tests/load_test.py --output validation-off.json
```

The typed equivalent is `PytinctureConfig(bff_cache_revalidation=False, ...)`.
The setting is per application/worker, not a client-controlled request flag.
Both runs should use the same code, dependencies, worker count, workload, and
limits. The existing default CI/security tests keep revalidation enabled;
targeted regression tests also exercise the disabled mode.

With `false`, warm registry and application-graph snapshots are reused without
checking whether files changed. Cold discovery and graph consistency checks
still run, and graph-cache eviction can cause another validated discovery.
Before each execution, the BFF source is still opened through the contained-file
loader, its digest must match the registry, and its current runtime export is
verified. Modules/instances remain fresh. Authentication, application/method
permissions, CSRF/replay proofs, policy decisions, request/result limits,
timeouts, and admission controls are unchanged.

This setting requires immutable application files and a restart of **every
worker after any application-code change**. It is unsuitable for development
with live edits: a removed application import can leave old BFF membership
permissions cached, newly added exports may be unavailable, and an edited BFF
file will fail execution verification rather than automatically loading its
new version. Explicit registry reload alone is not a substitute for refreshing
the whole worker. Disabling this option never permits sending a result that
fails response validation.

The [recorded on/off comparison](benchmarks/bff-cache-revalidation-385.json)
uses the same candidate code, Python 3.13.5, and local one-process example as
above, including the explicit example configuration change:

| Active callers | Revalidation on requests/s | Off requests/s | On p95 | Off p95 |
| --- | ---: | ---: | ---: | ---: |
| 50 | 68.6 | 69.4 | 840 ms | 962 ms |
| 100 | 68.4 | 72.6 | 1,608 ms | 1,473 ms |
| 250 | 65.4 | 65.2 | 3,986 ms | 4,155 ms |

Both runs had zero request errors. This single comparison shows no consistent
speedup from opting out, and neither mode meets the one-second p95 target at
100/250 callers. Keep the default enabled unless measurements on the intended
immutable workload justify the tradeoff; this switch is not a demonstrated fix
for the remaining concurrency bottleneck.
