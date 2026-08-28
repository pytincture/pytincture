# Changelog

This project follows semantic versioning from 1.0. Dates and final entries are
set when a release is published.

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
