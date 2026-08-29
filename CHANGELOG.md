# Changelog

This project follows semantic versioning from 1.0. Dates and final entries are
set when a release is published.

## Unreleased — 1.0 development

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
