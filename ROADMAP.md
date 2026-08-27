# Pytincture 1.0 Roadmap

Pytincture 1.0 is a stability release. Its primary goal is to make service-mode
and standalone browser applications predictable to build, deploy, operate, and
upgrade. New widget features are secondary to compatibility, diagnostics,
security, and repeatable releases.

## 1.0 stability contract

Pytincture 1.x will provide:

- semantic versioning, with breaking changes reserved for major releases;
- a documented public Python API and JavaScript runtime API;
- a deprecation period and migration instructions before public APIs are removed;
- a versioned BFF transport and generated-stub contract;
- a documented `appcode.pyt` packaging contract;
- a tested compatibility matrix for Python, Pyodide, dhxpyt, browsers, and
  supported deployment modes;
- secure defaults and startup-time validation for production configuration;
- deterministic browser startup behavior with actionable failure messages; and
- reproducible, tested Python and npm release artifacts with matching versions.

Internal modules and undocumented implementation details are not part of the
compatibility contract.

## Supported product modes

The 1.0 test and documentation matrix will cover:

1. **Service mode**: FastAPI delivery, packaged browser application, BFF calls,
   authentication, public assets, and optional MCP integration.
2. **Standalone mode**: `pytincture.js`, inline Python applications, Pyodide,
   micropip dependencies, and a configurable widgetset without a Pytincture
   backend.

## Milestones

### 0.11 — Public contracts and continuous integration

- Define the supported Python and JavaScript APIs.
- Document configuration precedence, BFF behavior, package contents, and the
  compatibility/deprecation policy.
- Add required pull-request CI for supported Python versions.
- Build and validate Python and JavaScript artifacts in CI.
- Introduce typed configuration and an application factory design while keeping
  `launch_service()` compatible.

**Exit criteria:** public contracts are reviewable, every pull request runs the
core test suite, and clean artifacts can be built from a checkout.

### 0.12 — Browser runtime and packaging reliability

- Add JavaScript unit tests and Playwright browser tests.
- Run real packaged and inline applications in Pyodide.
- Test BFF sync, async, streaming, authentication, static imports, dynamic file
  inclusion, widget wheels, CSS/fonts, and service-worker behavior.
- Replace swallowed startup errors and ambiguous packaged-to-inline fallback
  with structured lifecycle events and actionable errors.
- Validate Pytincture/Pyodide/dhxpyt compatibility before application startup.

**Exit criteria:** packaged and standalone applications pass in Chromium,
Firefox, and WebKit, and startup failures identify the failed stage and cause.

### 0.13 — Production architecture and operations

- Add a `create_app(config)` application factory and isolate mutable state.
- Split authentication, BFF dispatch, packaging, assets, MCP, and UI delivery
  out of the backend application module without changing public behavior.
- Validate configuration before accepting traffic.
- Add health/readiness endpoints and consistent structured logging.
- Test reverse-proxy headers, multiple workers, Redis-backed shared state,
  streaming disconnects, timeouts, and resource limits.

**Exit criteria:** multiple isolated application instances can be tested in one
process, and documented production topologies pass integration and load tests.

### 0.14 — Packaging, documentation, and developer experience

- Move development-only packages out of runtime dependencies.
- Introduce optional dependency groups for SAML, Redis, MCP, and development.
- Test wheels and source distributions after installation into clean
  environments.
- Add configuration diagnostics and browser-package inspection commands.
- Publish task-oriented documentation, configuration references, deployment
  recipes, troubleshooting, and 0.9/0.10-to-1.0 migration guides.

**Exit criteria:** a new user can build and deploy each supported mode from the
documentation, and release artifacts contain only intended dependencies/assets.

### 1.0 release candidates

- Publish at least `1.0.0rc1` and `1.0.0rc2`.
- Exercise the release candidates in a standalone app, an authenticated BFF app,
  and a production-style SAML or OAuth deployment.
- Maintain an RC observation period of at least 30 days.
- Resolve every release-blocking defect or explicitly remove the affected
  behavior from the 1.0 support contract.

## 1.0 release gates

`1.0.0` may be released when all of the following are true:

- Python 3.13 and 3.14 CI is green on the supported operating systems.
- Chromium, Firefox, and WebKit integration tests are green.
- Python wheel, source distribution, and npm runtime smoke tests are green.
- Python and npm package versions and bundled runtime assets agree.
- Upgrade tests from the latest 0.10 release pass with documented migration
  steps.
- There are no open critical/high security findings or P0/P1 defects.
- The public API, compatibility matrix, security policy, changelog, migration
  guide, deployment guide, and rollback procedure are published.
- Release candidates have completed the qualification period in representative
  applications.

## Engineering quality targets

- Security-sensitive BFF, authentication, session, and packaging paths receive
  branch-oriented tests and explicit negative cases.
- Browser startup and BFF calls expose correlation-friendly errors without
  leaking secrets.
- Pull requests cannot merge without required tests and artifact validation.
- Supported behavior is tested through public APIs rather than implementation
  globals wherever practical.
- Performance budgets are recorded for cold browser startup, warm startup,
  package generation, and representative BFF calls.

## Tracking

The GitHub milestone **Pytincture 1.0** tracks the workstreams in this document.
The umbrella roadmap issue links the individual delivery issues and records
cross-cutting release decisions.

- [Umbrella roadmap and release checklist](https://github.com/pytincture/pytincture/issues/144)
- [Public APIs, compatibility matrix, and deprecation policy](https://github.com/pytincture/pytincture/issues/135)
- [Playwright and Pyodide end-to-end browser tests](https://github.com/pytincture/pytincture/issues/136)
- [Deterministic browser startup and structured lifecycle errors](https://github.com/pytincture/pytincture/issues/137)
- [Typed configuration and `create_app` application factory](https://github.com/pytincture/pytincture/issues/138)
- [Focused backend modules](https://github.com/pytincture/pytincture/issues/139)
- [Production deployment, observability, and multi-worker behavior](https://github.com/pytincture/pytincture/issues/140)
- [Dependency groups and release artifact validation](https://github.com/pytincture/pytincture/issues/141)
- [Documentation and migration guides](https://github.com/pytincture/pytincture/issues/142)
- [Release candidate qualification and 1.0 release gates](https://github.com/pytincture/pytincture/issues/143)
