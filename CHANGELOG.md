# Changelog

This project follows semantic versioning from 1.0. Dates and final entries are
set when a release is published.

## Unreleased — 1.0 development

- Add opt-in minimal and operator-only health/readiness detail modes while
  preserving public probe status, existing detailed development responses,
  and the existing public/authenticated/disabled API-documentation controls.
  Operator details use a strong stateless Bearer token; no Redis or sticky
  routing is required.

## 1.0.0rc4 — 2026-09-01

- Pin the browser compatibility runtime to `dhxpyt==0.9.18`, which renders
  assistant and ordinary widget data as inert content, isolates artifact
  previews, authenticates preview messages, and bounds chat persistence.
  Existing applications that explicitly configure a chat `storageKey` retain
  persistence; implicit plaintext history persistence is removed.
- Replace pickle across the optional isolated-process BFF boundary with a
  bounded canonical JSON protocol and reject encrypted SAML assertions before
  toolkit processing. Isolated-process calls must use JSON-compatible values,
  and IdPs must send signed, unencrypted assertions.
- Retain BFF admission until abandoned synchronous streams actually finish,
  bound browser fetch/body/stream waits, make replay-token refill single-flight,
  and preserve existing synchronous generated methods while adding async
  companions. These controls add no Redis, sticky routing, or server session.
- Bound, rate-limit, cache, and conditionally serve exact widget wheels; verify
  the complete built-in dhxpyt wheel before import while keeping PyPI first and
  backend real-version/development-version fallback behavior.
- Qualify the exact production proxy virtual host and overwritten client IP,
  reject special or ambiguous release-archive members, require explicit opt-in
  for unverified external Pyodide, and byte-compare independently built release
  artifacts from frozen dependency inputs.
- Record machine-readable dispositions and compatibility effects for all 12
  findings from the 2026-09-01 review. Class-level `@backend_for_frontend`,
  pluggable widgetsets, stateless signed browser sessions, and load-balanced
  Redis-free deployments remain supported.

## 1.0.0rc3 — 2026-09-01

- Discover installed widget metadata from static source/distribution records
  without importing browser packages on the server. Add an optional
  deployment-owned widget distribution/version/asset-hash trust policy that
  overrides wheel self-attestation and fails closed, and serialize all service
  widget/page metadata safely for HTML script context. Pluggable pinned
  widgetsets remain the default and the policy adds no runtime state or Redis.
- Freeze Python CI and release-build inputs through the hash-bearing `uv.lock`,
  enforce exact wheel/sdist/npm file inventories, generate a complete SPDX
  inventory for every embedded Pyodide catalog component, verify vendored core
  bytes against the official release archive, and add deterministic plus
  history-wide secret scanning. These are release controls only and add no
  runtime service, session state, or Redis requirement.
- Protect dynamic responses with private/no-store and cookie/authorization
  variance; make raw Uvicorn access logging opt-in and path-only; strictly
  bound, rate-limit, and schema-check browser diagnostics while disabling them
  for no-auth services unless explicitly enabled. API documentation can now be
  public, authenticated, or disabled, and final qualification requires durable
  production-edge HTTPS/HSTS/canonical-origin/proxy evidence. These controls
  add no session store and do not require Redis.
- Offload SAML XML/signature validation and optional shared-store work from
  async request paths under bounded admission and deadlines. Readiness checks
  are briefly coalesced, and the legacy Redis read cache is disabled by
  default; its opt-in mode is positive-only, TTL-bound, and size-bound. Redis
  remains optional and normal browser-carried sessions remain stateless.
- Stream direct public assets from verified no-follow descriptors in bounded
  chunks, perform file I/O off the event loop, and make HEAD metadata-only.
  Warm appcode archives now validate cached source digests through file and
  directory identity metadata before any source reread; the per-worker LRU is
  bounded by aggregate bytes as well as entry count.
- Bound ordinary BFF JSON responses by configurable serialized bytes, depth,
  and item count, and serialize stream items under the remaining byte budget
  before retaining them. Add an opt-in process executor with killable wall
  time, CPU/memory/output limits, and worker/per-user admission; trusted thread
  execution remains the default and process isolation is not mandatory.
- Bound the optional, default-off BFF replay-proof feature with per-session,
  per-peer, and per-worker refill quotas plus expiration-indexed worker/session
  storage caps. Add a vendor-neutral atomic-store contract and fail startup
  when strict fleet-wide single use is requested without a shared provider.
  Ordinary sessions and load balancing remain stateless and Redis-free.
- Make generated sync, async, and streaming BFF clients require a 2xx response
  before decoding. Non-2xx responses raise `PytinctureBFFError` with only the
  status, operation, and request correlation id; 401 still redirects to login.
  Policy hooks now treat `False` as denial and reject invalid return types.
- Canonicalize BFF calls to one `{args, kwargs}` JSON object encoded once;
  reject duplicate keys, non-finite values, excessive structure, malformed
  arguments, and static signature/type mismatches before application import or
  construction. GET exports are now a parameterless/read-only contract with
  browser Origin/Fetch Metadata enforcement. No server-side state is added.
- Require SAML request correlation to be authenticated by either the validated
  Response signature or an exact `InResponseTo` inside the validated assertion
  signature, reject SHA-1 and unknown XML signature/digest algorithms, and cap
  the browser session to the earliest assertion/session expiry. Assertion-only
  IdPs remain supported, and the handshake remains stateless and Redis-free.
- Make service icons and the standalone runtime fully self-hostable: vendor the
  Material Design stylesheet/font, publish a versioned runtime/Pyodide/WASM/
  standard-library integrity manifest, verify installed bytes during static
  asset export, and require SRI plus anonymous CORS for explicitly external
  Pyodide scripts or icon CSS. The icon feature and CDN convenience mode remain.
- Remove the direct local npm publication script. The protected
  `npm-publish.yml` retained-artifact/attestation/OIDC workflow is now the sole
  registry publication path; local build and `npm pack --dry-run` inspection
  remain credential-free.
- Add stateless, fail-closed per-application identity admission for shared
  multi-app services. Optional rules constrain provider, issuer, tenant,
  subject, email/domain, and role before session issuance and whenever the
  signed application audience is enforced. Empty configuration preserves the
  simple single-trust service model; Redis and sticky sessions are not needed.
- Remove the unscoped `/classcall/...` compatibility routes. Every BFF call,
  including no-auth and MCP-backed calls, now names a real application and
  passes exact application graph membership before dispatch. Generated stubs,
  OpenAPI metadata, and the public contract emit scoped routes only; the class
  decorator remains the no-auth export decision without a second allowlist.
- Bind static BFF operations to exact class and member source identities,
  reject duplicate definitions and post-export rebinding before module import,
  track assignment expressions inside comprehensions, require the Pytincture
  export decorator to be outermost, and verify the runtime wrapper/member
  before class construction. The class-level export API remains unchanged.
- Replace long-lived PyPI credentials and in-CI publication with a protected
  OIDC trusted-publisher workflow that accepts only attested wheel/sdist bytes
  from successful release CI for a published, default-branch-reachable tag.
  The npm publisher now enforces the same tag ancestry boundary.
- Reject credentialed wildcard CORS in the compatibility application, and
  require passwordless development login requests to have a literal-loopback
  peer, direct Host, and browser origin while rejecting proxy/public/production
  provider combinations.
- Redact and structurally bound browser console diagnostics before transmission;
  expand Dependabot to npm and GitHub Actions, fail npm audit from low severity,
  and inventory Pyodide's CPython, micropip, and Emscripten components in the
  verified vendored SBOM.
- Isolate static BFF registry failures to their canonical source files: unsafe,
  unreadable, malformed, or invalidly encoded files now export nothing without
  denying startup to unrelated applications, and can rejoin only after a full
  successful rescan. Valid policy and explicit MCP targets remain fail-closed.
- Restore the interactive BFF API documentation under the enforced CSP by
  packaging an exact, SHA-256-locked Swagger UI release in the Python wheel.
  Documentation assets are same-origin, explicit framework-manifest entries
  with the service instance UUID; no floating CDN scripts are executed.
- Remove first-party SHA-1 usage by deleting unreachable certificate/XML
  helpers and using SHA-256 for internal module labels, and stop requesting
  Microsoft's unused `offline_access` permission. Active bounded SAML
  validation and normal Microsoft login remain unchanged.
- Record accepted security architecture contracts for class-level BFF exports,
  same-origin browser code, and stateless cross-worker SAML, with a
  machine-readable disposition file mapped to enforced regression tests.
- Require HTTPS for explicitly enabled remote Redis/Upstash endpoints while
  retaining literal loopback HTTP for local emulators. Redis remains optional;
  signed-cookie sessions and load balancing do not depend on it.
- Restrict backend widget-wheel routes to the application's exact declared
  distribution/version plus `PYTINCTURE_DEV_WHEEL_VERSION`, preventing stale
  or arbitrary same-name wheels from becoming public while preserving the
  real-version-first, development-version-last fallback.
- Require exact allowed hosts and one canonical HTTPS origin whenever
  production authentication is enabled, and reject incomplete trusted-proxy
  configurations. Local HTTP authentication remains available through an
  explicit loopback-only development switch, with signed-cookie sessions and
  no Redis requirement.
- Bound direct app assets to real application entrypoints and explicit
  browser/public/favicon/widget declarations, added per-application public
  asset globs, permanently denied direct Python-file exposure, and sandboxed
  SVG/favicon responses against same-origin script execution. Public assets
  remain intentionally unauthenticated and Redis-free.
- Require statically discovered BFF export, policy, method, and stream
  decorators to have direct Pytincture import provenance, with source-order
  shadowing detection. The class-level export API and normal aliases remain
  unchanged, while same-named local or unrelated decorators can no longer
  create remotely callable operations.
- Run both checked-in quickstarts under real Pyodide in Chromium, Firefox, and
  WebKit during CI, exporting the standalone quickstart's verified,
  version-matched local runtime and Pyodide tree before serving it.

## 1.0.0rc2 — 2026-08-29

- Added absolute authenticated-session expiry, browser-bound login protection,
  CSRF-protected POST logout, strict Google identity verification, and explicit
  Microsoft tenant/issuer validation while retaining stateless signed-cookie
  operation without Redis or sticky sessions.
- Added baseline CSP/browser security headers, an explicit framework static
  manifest, safe generated-script literal handling, per-application redacted
  BFF documentation, and correct relative-import discovery.
- Unified the launcher on typed configuration, hid secrets from configuration
  representations, enforced strong previous signing keys, pinned CI actions,
  added CODEOWNERS, tracked generated browser bundles, and verified vendored
  Pyodide files with an SPDX SBOM and hashes.
- Scoped service workers and caches per application/release, limited interception
  to an explicit immutable framework-asset manifest, preserved unrelated caches
  and signed/cross-origin requests, and removed the runtime-wide `fetch` patch.
  Private, authenticated, cookie-setting, and credential-varying responses are
  never cached; application URLs remain clean while backend assets retain their
  instance UUID.
- Restricted browser installs to exact package pins or SHA-256-locked wheels,
  disabled unpinned/transitive bootstrap installs, and replaced recursive
  site-packages JavaScript evaluation with widget-owned hashed asset manifests.
- Discover service-mode browser entrypoints with AST and literal package
  metadata, so page and appcode requests never execute browser module top-level
  code with backend privileges.
- Enforced canonical no-symlink module access with no-follow reads, bound BFF
  registry entries to SHA-256 source digests, rejected cross-platform path
  traversal, and standardized every application route on non-reserved ASCII
  Python identifiers. Application names containing dots or hyphens must be
  renamed before 1.0.
- Added bounded local-login rate/field/hash-work controls; BFF admission,
  preparation, policy, execution, stream item/byte/idle/wall budgets; bounded
  cached appcode construction; and short optional remote-store deadlines with
  circuit breaking. Saturated workers now reject promptly and recover without
  requiring Redis or sticky sessions.
- Required JSON for state-changing BFF dispatcher requests and applied strict
  Origin and Fetch Metadata validation to authenticated and no-auth calls,
  blocking drive-by requests to loopback/private-network services while
  retaining clients that send no browser security headers.
- Scoped generated BFF calls and signed sessions to an application audience,
  replaced basename authorization aliases with exact packaged-module paths,
  enforced declared provider/issuer/tenant/role/operation predicates, and made
  policy-bearing exports fail startup when no enforcement hook is configured.
- Bound each SAML login to a signed, expiring HttpOnly handshake cookie, with
  exact `InResponseTo` validation and no Redis or process-memory dependency, so
  callbacks remain portable across load-balanced workers.
- Guarded SAML signature processing with a strict transform allowlist, bounded
  secure XML pre-validation, decoded-response limits, per-peer ACS throttling,
  and federated-auth qualification evidence for upstream python3-saml #447.
- Changed generated browser BFF and replay calls to same-origin relative URLs,
  preventing Host and forwarded-header values from entering executable Python,
  marked personalized appcode archives private and non-cacheable, added optional
  canonical-origin/allowed-host enforcement, and defaulted legacy proxy-header
  trust off.
- Restricted npm publication and retries to attested artifacts from successful
  release-triggered CI at the exact published tag and commit, with strict
  package/version/path validation and a protected release environment.
- Bound passwordless development login to the actual loopback network peer,
  ignored spoofable Host headers for that decision, and made the supported
  launcher default this mode to a loopback-only listener while rejecting
  routable binds.
- Recorded the partial rc1 publication state without treating the candidate as
  passed or starting the observation period before the npm artifact is
  available.

### Changed

- npm publication uses one OIDC trusted-publisher workflow for normal releases
  and exact retained-artifact retries, replacing the expired long-lived token.

## 1.0.0rc1 — 2026-08-28

### Added

- Stable Python, JavaScript, BFF v1, and `appcode.pyt` v1 contracts.
- Typed `PytinctureConfig` and isolated `create_app()` factory.
- Deterministic browser lifecycle events/errors and real Pyodide tests in
  Chromium, Firefox, and WebKit.
- Health/readiness probes, structured security logs, bounded BFF execution,
  production load smoke tests, and multi-worker shared-state validation.
- Minimal base installation, optional feature extras, reproducible artifact
  manifests/hashes, and CI-gated publishing.

### Changed

- Backend responsibilities are split into focused modules while legacy public
  imports remain compatible.
- Packaged application errors are reported instead of silently switching to
  inline mode.
- MCP is mounted only when enabled.
- The supported Starlette range starts at 1.6, removing the temporary
  dependency-audit exceptions for the patched 1.0 release line; development
  tests use Starlette's supported `httpx2` client path.

## 0.10.7

- Quieted the expected PyPI widget lookup failure before backend wheel fallback.

## 0.10.6

- Applied the service UUID only to backend-hosted cacheable files and kept
  micropip package-index URLs unchanged.
- Reused one process UUID and kept application navigation URLs clean.

## 0.10.5

- Added the 1.0 roadmap, baseline CI, and frontend runtime hardening.

## 0.10.0–0.10.4

- Hardened authentication, CSRF, BFF exposure, browser packaging, widget-wheel
  delivery, and SAML requested-authn-context compatibility.
- Restored verified local-user profile claims and browser logging compatibility.

See the [0.9-to-0.10 migration guide](docs/migrations/0.9-to-0.10.md) for the
breaking security defaults and the Git history for patch-level detail.
