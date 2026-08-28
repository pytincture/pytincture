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
| Pyodide | 0.29.3 browser distribution | Supported, browser CI pending | Bundled service/standalone runtime; Python 3.13. |
| `pyodide-py` | 0.29.4 | CI | Backend/build dependency; not the browser asset version. |
| dhxpyt | application-declared version | Provisional | PyPI first, matching backend wheel second, `99.99.99` development wheel last. A qualified version range will be recorded by browser tests. |
| Chromium | current stable | Planned gate | Service and standalone modes under issue #136. |
| Firefox | current stable | Planned gate | Service and standalone modes under issue #136. |
| WebKit | current Playwright build | Planned gate | Service and standalone modes under issue #136. |

Pytincture does not claim support for Python versions outside the range in
`pyproject.toml`. A browser becomes release-qualified only when its automated
matrix is green; "current stable" will be recorded by exact CI image/tool
versions for each release candidate.

## Deployment matrix

| Mode | Supported scope | Qualification |
| --- | --- | --- |
| Service | FastAPI delivery, packaged browser app, BFF, auth, public assets, optional MCP | Python CI now; browser, proxy, and multi-worker gates pending. |
| Standalone | `pytincture.js`, inline Python, Pyodide, micropip, configurable widgetset | Runtime build CI now; cross-browser gate pending. |
| Multiple workers | Shared signing key; Redis required for shared revocation/replay state | Planned production integration gate. |
| Reverse proxy | Forwarded HTTPS scheme and stable host configuration | Supported configuration; integration gate pending. |

## Version compatibility rules

- Python and npm artifacts from one release must have the same Pytincture
  version.
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
