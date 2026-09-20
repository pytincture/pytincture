# Full application conformance inputs

`applications.lock.json` pins public application/widget commits, reviewed local
patches and the speech-model revision. The patches preserve the full applications;
they add isolated launchers, browser tests, client/server import separation and
the widget asset-ownership hook. They are temporary integration fixtures until
corresponding app/widget changes are available upstream. Preparing them creates
new checkouts and never changes a developer's existing app repositories.

`requirements.txt` is a hash-locked **test environment**, not a framework runtime
dependency list. It includes the app's real SQLAlchemy, LiteLLM and Whisper stack.
Authentication uses public demo credentials, SQLite files in the test cache, an
absent environment file and a local OpenAI-compatible provider fixture. There are
no paid provider calls or production database connections. Voice uses real Whisper
with synthetic recorded speech, not a physical microphone.

From the framework root, using Python 3.13 and uv:

```sh
uv venv .venv-conformance
uv pip sync --python .venv-conformance/bin/python --require-hashes tests/conformance/requirements.txt
uv pip install --python .venv-conformance/bin/python --no-deps .
.venv-conformance/bin/python -m playwright install --with-deps chromium
.venv-conformance/bin/python tests/prepare_application_conformance.py --destination /tmp/pytincture-apps
.venv-conformance/bin/python tests/run_application_conformance.py --prepared /tmp/pytincture-apps/prepared.json
```

Use a new destination each time. The harness owns ports 8094/8095 and refuses to
reuse an existing server. It stops only processes it starts. Screenshots, full
logs, per-route identity, CSP/network checks and cold/warm timing are written to
`validation-results/full-apps/`. `--profile portable-micropython` and `--app chat`
can narrow a debugging run; release conformance runs all three supported profiles
and both apps. The full job gates release artifact attestation in `ci.yml`.

The example suite covers editing/persistence, filters, charts, forms and reports.
Fresh-page probes cover each implemented tab/sidebar view. Wawesome covers chat,
providers, users, artifacts, streaming and voice. Its user-access cards are demo
state, not an authorization product test. Live identity providers, paid model
accounts and hardware permissions remain outside this fixture.

The smaller `tests/browser_build_smoke.py` suite adds CodeMirror, modal persistence,
BFF argument/stream variants and independent widget apps. It distinguishes explicit
stream-reader cancellation from unexpected failed resource downloads; complete
stream content is asserted before accepting those cancellations.

To update inputs, review upstream changes, generate binary diffs against the pinned
commits, update patch hashes in the lock, and rerun every profile. Do not replace
pins with floating branches. Regenerate the dependency lock with the command in
its header. Wheel timestamps use the lock's fixed `source_date_epoch`; no build
clock or local absolute path is included in portable bundle identities.
