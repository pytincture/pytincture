# Compatibility and support policy

This matrix records what is supported today and what must be qualified before
1.0. "CI" means the repository exercises the combination on every pull
request. "Planned gate" means the combination is in the 1.0 support target but
does not yet have automated qualification.

## Runtime matrix

| Component | Version or range | Status | Notes |
| --- | --- | --- | --- |
| CPython | 3.13 | CI | Unit tests, package build, and wheel smoke test. |
| CPython | 3.14 | CI | Unit tests and editable installation. |
| Pyodide | 0.29.3 browser distribution | CI | Bundled service/standalone runtime; Python 3.13. `pyodide-py` is not a backend dependency. |
| dhxpyt | 0.9.16 browser fixture | CI | PyPI first, matching backend wheel second, `99.99.99` development wheel last. Applications may declare another compatible widgetset version. |
| Chromium | current Playwright build | CI | Authenticated service and standalone modes. |
| Firefox | current Playwright build | CI | Authenticated service and standalone modes. |
| WebKit | current Playwright build | CI | Authenticated service and standalone modes. |

Pytincture does not claim support for Python versions outside the range in
`pyproject.toml`. A browser becomes release-qualified only when its automated
matrix is green; "current stable" will be recorded by exact CI image/tool
versions for each release candidate.

## Deployment matrix

| Mode | Supported scope | Qualification |
| --- | --- | --- |
| Service | FastAPI delivery, packaged browser app, BFF, auth, public assets, optional MCP | Python and cross-browser CI. |
| Standalone | `pytincture.js`, inline Python, Pyodide, micropip, configurable widgetset | Cross-browser CI. |
| Multiple workers | Shared signing key; Redis required for shared revocation/replay state | Simulated multi-worker shared-state CI. |
| Reverse proxy | Forwarded HTTPS scheme and stable host configuration | Header/topology integration CI and deployment runbook. |

## Version compatibility rules

- Python and npm artifacts from one release must have semantically equivalent
  versions. Stable strings match; PEP 440 `1.0.0rc1` maps to npm SemVer
  `1.0.0-rc.1`.
- The bundled JavaScript runtime and backend are released together. Mixing
  major versions is unsupported.
- A 1.x backend accepts the version-1 BFF and appcode contracts documented in
  this repository. Additive fields and headers may appear in minor releases.
- Widgetset compatibility is based on the package/version declared by the
  application. Pytincture does not silently substitute a different real
  release; `99.99.99` is an explicit development fallback only.
- Security fixes may narrow unsafe behavior without a full deprecation period.

## Deprecation policy

For a public 1.x API:

1. The replacement and migration steps are documented in release notes.
2. Python callers receive `DeprecationWarning`; browser callers receive one
   `console.warn` per deprecated feature where practical.
3. The deprecated behavior remains available for at least one minor release
   and 90 days, whichever is longer.
4. Removal normally occurs only in the next major release.

Exceptions are allowed for active security vulnerabilities, legal
requirements, or behavior that cannot function on a supported dependency. The
release notes must identify the exception, impact, and safest migration.

Internal APIs may change without deprecation. An API is public only when it is
listed in `contracts/public-api-v1.json` or explicitly versioned by a contract
document.
