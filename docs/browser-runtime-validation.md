# Full application runtime validation — 2026-09-18

The complete Book Library example and Wawesome Chat were exercised automatically
in Playwright Chromium under **MicroPython and Pyodide**. Both runtime runs passed
without browser errors or failed resource requests. MicroPython requested no
Pyodide assets, including when previewing Python artifacts.

## Versions and local branches

| Repository | Updated source | Validation checkout |
|---|---|---|
| pytincture | `feat/browser-runtime-choice` | Original framework checkout; branch remains checked out |
| pytincture_example | latest main `0971ee3` | `pytincture-example-latest-runtime`, `test/latest-browser-runtimes`, commit `f9b0fde` |
| dhxpyt | latest main `349d6dc` | Updated original checkout; existing local edits preserved |
| wAwesomeChat | latest main `23085c7` | Original app checkout, `feat/browser-runtime-validation`, commit `8139590` |
| wapyt | latest voice/widget integration branch `e614507` | `wapyt-runtime-validation`, `test/browser-runtimes`, commit `ebf61a5` |

The wapyt main checkout had uncommitted changes; those were preserved. Its newer
voice branch was needed by the current app, so validation used a separate
checkout. Updated widget wheels were built and installed locally; no manual
widget pull or browser interaction was required. App/widget validation commits
are local, separate from the framework branch publication.

## Features exercised

| Application | Passed in both runtimes |
|---|---|
| Book Library | Password login; full widget layout; grid data, row selection and column filters; ratings chart and fitted axis; sidebar collapse/expand; form population and controls; report modal; double-click and context-menu edit; save/reload persistence and date preservation; restoration of edited records; calendar month navigation/date selection; light/dark preference and reload behavior |
| Wawesome Chat | Password login; full Chat/Providers/Users UI; new/delete/clear chat; model selection and remembered choice; theme toggle and remembered choice; sidebar controls; send/copy messages; incremental replies through real LiteLLM and BFF streaming; persisted transcript; provider create/edit/search/reload/delete; model addition/deletion; user create/edit/select; provider/model access toggles; HTML artifact preview/code/copy/download/close; Python artifact execution using the selected interpreter |
| Wawesome voice | MediaRecorder capture; real Whisper transcription; composer insertion; continuous listening and voice activity detection; automatic submission; streamed response; stop-listening control; denied-microphone recovery |

Voice input used committed synthetic speech and a browser-generated MediaStream.
The test asserted two successful transcription requests, one for push-to-talk and
one for continuous listening. It required no physical microphone or human input.

## Fixes found through the applications

- Generic widget-package/asset-manifest support; stale asset hashes fail the build.
- Synchronous and streaming BFF compatibility, alongside async JSON methods.
- Portable type aliases, title case, optional pinned copy/datetime modules,
  and clear rejection of unsupported async generators.
- Wawesome server configuration/database imports moved behind authenticated BFF
  methods; transcript rows are scoped to the authenticated user.
- Widget task scheduling compatible with browser MicroPython; stream cleanup.
- Clipboard target lifetime, persisted theme restoration, and Python artifact
  output capture without starting a second interpreter.
- CI dependency installation fixed: dhxpyt 0.9.19 is selected from its pinned
  Git source because that version is not published to PyPI.

## Regression checks

- Framework: **915 Python tests**, **46 JavaScript tests**, **10 browser lifecycle tests**.
- Wawesome backend: **7 tests**, including user-scoped transcript storage.
- Wapyt stream handling: **31 tests**.
- Independent DOM and widget fixture applications passed in both interpreters,
  including sync/async/JSON/raw BFF streams, nested modules/layouts, dataclasses,
  UTF-8 output capture and recovery after Python errors.
- The same fixture applications also passed using the **installed framework wheel**.
- Wheel, source distribution, and npm artifact inventories validated.

## Boundaries

The chat provider was a local OpenAI-compatible streaming fixture reached through
the real LiteLLM/BFF stack. Live OpenAI/Anthropic/Bedrock/etc. accounts, live SAML
identity providers, and OS/hardware microphone permission prompts were not tested.
No production database or provider credentials were used.

The app's user-access cards remain in-memory demonstration controls, not backend
authorization enforcement. Deleting widget conversations does not purge the
server transcript archive. These existing product boundaries are not evidence of
runtime incompatibility. MicroPython still supports a Python/stdlib subset;
CPython-only packages and replay-token BFF mode require the original Pyodide path.

## Local results and repeatable checks

Screenshots, JSON results and logs are saved under the framework checkout's
ignored `validation-results/` directory. The app repositories contain the
Playwright scripts and persistent local test launchers:

- Example: `tests/ui_smoke.py`, `tests/browser_interactions.py`, `tests/runtime_server.py`.
- Wawesome: `tests/browser_runtime_smoke.py`, `tests/browser_voice_smoke.py`, `tests/runtime_server.py`.

Both servers remain available locally:

- [Book Library, MicroPython](http://127.0.0.1:8095/py_ui?runtime=micropython)
- [Wawesome Chat, MicroPython](http://127.0.0.1:8094/chat?runtime=micropython)

Use `runtime=pyodide` for the comparison. The isolated launchers use the public
demo login `demo@example.com` / `demo-password`.

## Default upgrade verification — 2026-09-19

After removing the experimental Transcrypt adapter, an unchanged snapshot of
the latest example (`0971ee3`) passed the full UI smoke test with this framework.
It used its original `run.py`, app sources and widget wheel, no runtime settings,
no manifest declaration, and no browser build. Only the isolated database and
local listening port differed. Network requests confirmed the original Pyodide,
widget-wheel and `appcode.pyt` package-loading path.

The default remains `pyodide`, and query-based runtime selection remains
disabled. Regression tests cover these defaults in configuration and browser
startup, including BFF replay tokens and unused/malformed conventional bundles.
MicroPython still requires an explicit opt-in and compatible browser bundle.

Validation passed: 917 Python tests, 47 JavaScript tests, 10 browser lifecycle
tests, nine Chromium end-to-end tests, the untouched full example, and the independent DOM/widget
fixture applications under both supported interpreters. Logs and the example
screenshot are in `validation-results/runtime-default-*`.
