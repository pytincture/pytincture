# Backend architecture

The backend is assembled by `pytincture.create_app()`. Each call loads an
isolated compatibility facade and owns its FastAPI routes, configuration,
BFF registry, session stores, middleware, and MCP mount. The legacy
`pytincture.backend.app:app` import remains available as a deprecated
compatibility path for existing integrations, but it now applies the same
fail-closed `PytinctureConfig` validation as `create_app()` before assembling
any routes. New launchers and integrations should call `create_app()`.

Focused backend modules keep policy and infrastructure testable without
creating a service or mutating process-global environment state:

| Module | Responsibility |
| --- | --- |
| `auth` | Email allowlists, password verification, safe user claims, and role normalization |
| `saml` | SAML provider parsing, selection, login-button metadata, and role policy |
| `bff` | Per-application BFF manifest discovery and registry ownership |
| `browser_packages` | Static widgetset discovery and browser archive file selection/building |
| `pages` | AST-only application title, entrypoint, and favicon metadata discovery |
| `mcp` | Explicit MCP operation policy, schema filtering, and transport compatibility |
| `middleware` | Rotating signed sessions and request body limits |
| `storage` | Injectable Redis-backed shared session and one-time-token storage |
| `diagnostics` | Correlation IDs and sanitized public error payloads |
| `source_loading` | Collision-safe dynamic source module loading |

`backend.app` is now the route-wiring and backwards-compatibility layer. Its
historical helper names delegate to these modules so applications that import
them continue to behave as before. New backend tests should target the focused
module first and retain route-level tests for observable HTTP behavior.

## Dependency rules

- Focused modules do not import `backend.app`.
- Configuration values are passed into focused functions and objects. They do
  not read another application instance's environment.
- Local browser metadata is parsed statically where possible; discovering a
  widgetset does not execute application modules.
- Serving a page or appcode archive never imports the browser entrypoint on the
  server. Entrypoint discovery accepts documented static aliases and literal
  metadata and rejects ambiguous dynamic patterns.
- Mutable BFF and storage state belongs to an application instance or an
  explicitly constructed store.
- Routes, operation IDs, session schema, and public imports remain governed by
  the versioned public contracts.
