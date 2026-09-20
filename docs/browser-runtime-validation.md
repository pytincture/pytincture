# Full application runtime conformance — 2026-09-20

The complete Book Library example and Wawesome Chat passed automated Playwright
Chromium tests in all three supported configurations. The final matrix used new
checkouts from pinned public commits, hash-checked integration patches, rebuilt
widget wheels, a new hash-locked Python environment and a pinned Whisper model.
The framework branch remains `feat/browser-runtime-choice`; rc8 is unreleased.

| Configuration | Book Library | Wawesome Chat, including voice |
|---|---|---|
| Default legacy Pyodide | Passed | Passed |
| Portable Pyodide | Passed | Passed |
| Portable MicroPython | Passed | Passed |

Portable Pyodide used native CPython sources. MicroPython requested no Pyodide
assets, including when executing Python artifacts. Each route probe checked the
public runtime identity, delivery mode, profile/bundle identity where applicable,
CSP, fonts, unexpected errors and failed HTTP responses, then recorded cold/warm
startup stages and screenshots.

## Features exercised

| Application | Automated coverage in each configuration |
|---|---|
| Book Library | Password login; full layout; grid/BFF data; row selection; column filters; ratings chart/axis; sidebar collapse/expand; form population; reports modal; double-click and context-menu edit; save/reload/date preservation; restoration of edited records; calendar navigation/date selection; light/dark preference and persistence |
| Wawesome | Password login; Chat/Providers/Users on fresh pages; new/delete/clear conversations; model and theme persistence; sidebar controls; send/copy; incremental replies through real LiteLLM/BFF streaming; provider create/edit/search/reload/delete; model add/delete; user create/edit/select and access-card controls; HTML artifact preview/code/copy/download/close; Python artifacts using the existing interpreter |
| Voice | Synthetic MediaStream captured by real MediaRecorder; real Whisper transcription; push-to-talk composer insertion; continuous listening/VAD; automatic submission and streamed reply; stop control; denied-microphone recovery |

The independent fixture suite also passed in both portable engines: direct DOM
callbacks, nested imports/layouts, dataclasses, BFF arguments/defaults/GET/variadic
calls, JSON and raw streams, package resource bytes, CardPanel/Kanban/Chat, real
CodeMirror editing, modal persistence, reload, fonts and screenshots.

## Asset ownership regression

The portable loader owns the verified manifest's script/style path. The wapyt
integration hook adopts that completed registry before app startup. Fresh-page
probes observed each widget constructor registered once, verified that forcing
its private loader did not execute assets again, and compared inline style hashes
against owned styles to reject duplicate CSS injection. Application-specific inline
styles remain valid. Material Icons and Material Design Icons CSS/fonts now belong
to the same verified bundle path rather than a second host-loader path.

Unexpected resource failures are errors. Explicit BFF stream-reader cancellation
and the legacy wheel metadata probe can appear as `ERR_ABORTED` after successful
consumption; fixture tests distinguish these from failed asset downloads and
assert the complete stream values. No broader network-error ignore is used.

## Regression checks

- Full framework suite: 955 Python tests passed; the subsequent extensionless CSS
  origin regression also passed with its resource/profile test file.
- JavaScript: 49 tests passed. Browser lifecycle: 10 tests passed.
- Legacy Chromium end-to-end: 9 tests passed, covering packaged/inline apps,
  authentication, Swagger/API access, widget integrity and SVG isolation.
- Firefox/WebKit were not executed locally because their Playwright binaries were
  absent. The existing CI browser job installs and exercises those engines.
- Wapyt: 33 local stream/asset-ownership tests passed.
- Wheel, source distribution and npm inventories validated.
- The new framework/conformance changes passed the local secret scan.

## Reproducible inputs

The complete source, patch and dependency pins are in
[`tests/conformance/`](../tests/conformance/README.md). Upstream bases are example
`0971ee3`, dhxpyt `349d6dc`, Wawesome `23085c7` and wapyt `e614507`. The companion
local validation commits are example `840c72c`, Wawesome `97cf912` and wapyt
`0fb5f99`; these repositories were not pushed as part of the framework task.
Original dirty widget checkouts were preserved.

The full application CI job prepares these inputs and runs all three modes. It
also gates release artifact attestation. The smaller fixture job covers the
editor/resource regressions and uploads its screenshots/results separately.

Local final matrix evidence is under `validation-results/pinned-full-apps/`, with
its transcript in `validation-results/pinned-full-conformance.log`. The resource
fixture evidence is under `validation-results/conformance-fixtures/` and
`validation-results/conformance-resources-final.log`. These are ignored local
artifacts; CI uploads equivalent evidence. The harness stops its own test servers;
use the committed launchers to start a persistent local preview.

## Boundaries

The provider is a local OpenAI-compatible streaming fixture reached through the
real LiteLLM/BFF stack. Tests use isolated SQLite data and public demo credentials.
Live model-provider accounts, SAML identity providers and physical microphone/OS
permission prompts are outside this conformance run. The synthetic voice fixture
exercises recording, encoding and real transcription without human interaction.

Wawesome's user-access cards are demonstration state, not backend authorization
enforcement. Deleting widget conversations does not purge its server archive.
Those existing app boundaries are separate from runtime compatibility.

MicroPython remains a documented Python subset. Static compatibility checks do
not prove arbitrary dynamic Python/JavaScript behavior or every widget option.
The startup reports distinguish parent/child phases; they are diagnostic evidence,
not a general speed guarantee. Compare equivalent inputs and cache conditions
before drawing performance conclusions.
