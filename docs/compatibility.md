# Compatibility and support policy

This matrix records the 1.0 support scope and its qualification coverage. "CI" means the repository exercises the combination on every pull
request. "Planned gate" means the combination is in the 1.0 support target but
does not yet have automated qualification.

## Runtime matrix

| Component | Version or range | Status | Notes |
| --- | --- | --- | --- |
| CPython | 3.13 | CI | Unit tests, package build, and wheel smoke test. |
| CPython | 3.14 | CI | Unit tests and editable installation. |
| Pyodide | 0.29.3 browser distribution | CI | Bundled service/standalone runtime; Python 3.13. `pyodide-py` is not a backend dependency. |
| MicroPython | 1.29.0-6 browser WASM | Experimental CI | Opt-in portable delivery; defined portable Python profile, not arbitrary CPython compatibility. |
| dhxpyt | 0.9.18 browser fixture | CI | Legacy service mode prefers the matching backend wheel, then its permitted development fallback, then the built-in complete-wheel lock. Explicit `widgetSource` overrides this order. Other Widgetsets follow the [widget source policy](widgetset-packaging.md). |
| wapyt | 0.2.0 quickstart fixture | CI | Service and standalone examples; exact service allowlist or a local wheel is required. |
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
| Multiple workers | Shared signing key; no shared store for normal sessions. Optional atomic provider only for immediate revocation or strict replay-proof single use. | Simulated stateless and shared-state multi-worker CI. |
| Lambda Web Adapter | Normal ASGI lifespan and streaming; optional prebuilt BFF manifest, checked bytecode and initialized alias | Framework CI plus KingdomGive AWS application evidence; qualify application integrations separately. |
| Reverse proxy | Forwarded HTTPS scheme and stable host configuration | Header/topology integration CI and deployment runbook. |

## Version compatibility rules

1.0.1 is a dependency and bundled Swagger UI maintenance update. Public APIs,
configuration defaults and FastAPI/ASGI, browser, desktop and Lambda deployment
paths are unchanged from 1.0.0.

1.0.0 preserves RC15 public APIs, regular FastAPI/ASGI and desktop launch paths,
and existing authentication defaults. The Lambda manifest/bytecode build steps
and provisioned concurrency are opt-in. Unchanged BFF manifests are reused only
after source verification; optional MCP/OAuth/SAML imports load when enabled.
See the [Lambda guide](aws-lambda.md) for deployment, login/BFF composition and
public CDN caching. MicroPython remains an opt-in portable profile.

RC15 preserves RC14's public APIs and defaults. The bundled Pyodide standard
library includes a narrow tarfile security backport; its upstream runtime
version remains unchanged. Removing the separate chat test application does
not remove a framework feature or change widgetset support.
See [RC15 migration notes](migrations/0.10-to-1.0.md#rc14-to-rc15).

RC14 preserves the version-1 BFF contract and existing defaults. BFF result and
source-analysis optimizations retain authorization, source checks and response
limits. `BFF_CACHE_REVALIDATION` defaults to `true`; immutable-code deployments
that disable it must restart workers after code changes. Frame sources remain
unchanged unless the service opts into the new HTTPS-origin or blob-frame
controls. WebKit's inherited frame-ancestor restriction remains in force.
The maintained quickstarts now pin `wapyt==0.2.0`; dhxpyt remains supported.
The Lambda Web Adapter recipe preserves streaming, with deployment qualification
limits described in [the guide](aws-lambda.md).
See [RC14 migration notes](migrations/0.10-to-1.0.md#rc13-to-rc14).

RC13 keeps the version-1 BFF contract, RC11 resource limits, and RC12 page and
streaming behavior. It adds `cookie_namespace` / `AUTH_COOKIE_NAMESPACE`; the
default, `pytincture`, keeps every existing cookie name, so nothing changes until
a deployment sets it. Rebuild precompiled appcode and portable bundles before
setting a namespace: the regenerated clients read the namespaced CSRF cookie.
See [RC13 migration notes](migrations/0.10-to-1.0.md#rc12-to-rc13).

RC12 keeps the version-1 BFF contract and RC11 resource limits. Application
page links at `/app` redirect to `/app/` so the scoped service worker controls
the document. BFF, authentication, and appcode endpoint paths stay the same. Rebuild
precompiled appcode and portable bundles to receive the generated-client fixes.
Default streamed strings now arrive as JSON string values; producers of
hand-encoded JSON/SSE text must use `bff_stream(raw=True)` and supply their own
delimiters. Existing raw streams and pre-encoded byte framing are preserved.
See [RC12 migration notes](migrations/0.10-to-1.0.md#rc11-to-rc12).

RC11 keeps the version-1 BFF contract and adds limit metadata and response
headers. Rebuild precompiled appcode and portable bundles to get the updated
client exception details. Existing explicit payload limits retain precedence;
see [RC11 defaults](configuration.md#rc11-resource-limits-and-diagnostics).

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
