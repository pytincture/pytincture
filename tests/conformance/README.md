# Book Library conformance inputs

`applications.lock.json` pins the public Book Library example and dhxpyt widget
commits plus a hash-checked example patch. The patch adds an isolated launcher,
browser tests and client/server import separation. Preparation creates fresh
checkouts and never changes a developer's existing app repositories.

Full wAwesomeChat/provider/voice application tests belong in the wapyt/app
repositories. Pytincture no longer carries their patches, LiteLLM dependency,
cloud SDK or speech-model setup. The smaller independent framework fixtures
continue to exercise widget compatibility and BFF streaming.

`requirements.txt` is a hash-locked **test environment**, not a framework runtime
dependency list. It includes framework development dependencies, Playwright and
SQLAlchemy for Book Library. CI audits this environment before running the app.
Authentication uses public demo credentials and isolated SQLite data; there are
no production database connections or paid provider calls.

From the framework root, using Python 3.13 and uv:

```sh
uv venv .venv-conformance
uv pip sync --python .venv-conformance/bin/python --require-hashes tests/conformance/requirements.txt
uv pip install --python .venv-conformance/bin/python --no-deps .
.venv-conformance/bin/python scripts/audit_python_dependencies.py
.venv-conformance/bin/python -m playwright install --with-deps chromium
.venv-conformance/bin/python tests/prepare_application_conformance.py --destination /tmp/pytincture-apps
.venv-conformance/bin/python tests/run_application_conformance.py --prepared /tmp/pytincture-apps/prepared.json
```

Use a new destination each time. The harness owns port 8095, refuses to reuse an
existing server, and stops only processes it starts. Screenshots, logs, per-route
identity, CSP/network checks and cold/warm runtime information are written to
`validation-results/full-apps/`. Use `--profile portable-micropython` to narrow a
debugging run. Release conformance runs legacy Pyodide, portable Pyodide and
portable MicroPython and gates release artifact attestation in `ci.yml`.

The example suite covers editing/persistence, filters, charts, forms and reports.
Fresh-page probes cover each implemented tab/sidebar view. The independent
`tests/browser_build_smoke.py` suite also covers CodeMirror, modal persistence,
BFF argument/stream variants and widget apps.

To update inputs, review upstream changes, generate diffs against the pinned
commits, update patch hashes, and rerun every profile. Regenerate the dependency
lock with the command in its header. Wheel timestamps use the lock's fixed
`source_date_epoch`; no build clock or local absolute path is included in portable
bundle identities. Historical RC8 chat/voice evidence remains in
`docs/browser-runtime-validation.md`; it does not describe the current suite.
