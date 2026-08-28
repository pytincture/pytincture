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

The job retains `performance.json` as a CI artifact even on success. RC
qualification should link the retained browser evidence for the exact RC.

## Service measurements

Production-smoke CI runs a wheel-installed Uvicorn service and records p95
latency for:

- 500 health requests at concurrency 20;
- 40 generated `appcode.pyt` archives at concurrency 5; and
- 200 representative BFF calls at concurrency 10.

The resulting `performance-health.json`, `performance-appcode.json`, and
`performance-bff.json` files are retained as the service performance artifact.

Budgets may only be relaxed with a documented reason and review of the
corresponding CI evidence. Application owners must establish tighter budgets
for their deployment, network, widgetset, and workload.
