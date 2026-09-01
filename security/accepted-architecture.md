# Accepted security architecture and non-goals

This document records security-related Pytincture 1.x behavior that is
intentional and will not be removed merely because a scanner can describe the
associated trust boundary as risk. It exists so future reviews can distinguish
accepted architecture from implementation defects that still require fixes.

These decisions do not waive defects that cross the stated boundary. A
decorator with false provenance, an archive that includes server-only source,
an unscoped route that bypasses an application graph, or an asset that was not
explicitly selected remains a bug.

## Class-level BFF export is the developer opt-in

`@backend_for_frontend` intentionally exports the decorated class's public
methods and public read-only attributes. Pytincture will not require a second
method-level export decorator for every member.

Why:

- The class decorator is an explicit network-export decision and is central to
  Pytincture's simple developer contract.
- Requiring duplicate declarations on every method would add ceremony without
  fixing provenance, application scoping, or runtime-identity defects.

Required controls that remain in scope:

- Prove that security decorators came directly from Pytincture and were not
  rebound or shadowed.
- Require the Pytincture export decorator to be the single outermost export
  decorator so another decorator cannot replace its wrapper afterward.
- Bind static manifest entries to the exact runtime class definition.
- Exclude private names beginning with `_`.
- Enforce the application graph, session audience, declared HTTP methods, and
  any configured BFF policy.
- Produce a reviewable export inventory in tests and deployment evidence.

## No-auth services deliberately expose decorated BFF operations

When every authentication provider is disabled, a correctly scoped BFF class
decorated with `@backend_for_frontend` is intentionally callable without a
login. Pytincture will not require a redundant no-auth allowlist for every
decorated operation.

Why:

- Selecting no-auth mode and decorating a BFF class are both explicit
  deployment/developer decisions.
- Public examples and private-network applications need a low-ceremony mode.

Required controls that remain in scope:

- Every call must name a real application and belong to that application's
  browser/BFF graph.
- Unscoped BFF routes do not exist; every call names and validates a real
  application graph.
- Browser-origin, Fetch Metadata, content-type, resource-limit, and optional
  policy controls still apply.

## Selected browser code is trusted same-origin application code

Application-selected browser Python and approved widget JavaScript execute
with the application's browser origin and authority. Pytincture does not claim
to sandbox those assets from the DOM, session-backed requests, or one another.

Why:

- Browser Python and pluggable widgetsets are the framework's execution model,
  not untrusted user content.
- A meaningful in-page sandbox would break widget interoperability and the
  normal application programming model.

Required controls that remain in scope:

- Only explicit application files and approved widget assets may enter the
  browser package.
- Server-only imports, secrets, unrelated package JavaScript, and undeclared
  public assets must remain outside the archive.
- Production deployments must pin or verify browser dependencies and keep
  untrusted uploads on a separate origin.

## Explicit direct assets are public

Files deliberately selected as an application's direct public assets are
served without application authentication. The application path is an
ownership boundary, not a promise that the bytes require a session.

Why:

- Login pages, icons, fonts, and boot assets must be fetchable before a session
  exists.

Required controls that remain in scope:

- A real application entrypoint and an explicit asset declaration are
  required.
- Python, sensitive filenames, unsafe paths, symlinks, and untrusted active
  content must not become public through broad patterns.
- Active content must be sandboxed, blocked, or hosted on an appropriate
  separate origin.

## Widgetsets and micropip remain pluggable

Pytincture will continue to support pinned widgetset packages and explicitly
configured micropip dependencies. It will not restrict applications to dhxpyt
or to packages bundled with Pytincture.

Why:

- Widgetsets are an extension point, and pure-Python browser dependencies are
  a core Pyodide capability.

Required controls that remain in scope:

- Package requirements must be exact pins or hash-qualified wheel URLs.
- Executable widget assets must come from an explicit manifest and match their
  declared hashes.
- Production guidance should prefer vendored or independently verified assets
  and may support an administrator-owned allowlist.

## Normal sessions remain stateless and browser-carried

The default authenticated session is a signed browser cookie that can be
validated by any worker sharing the signing key. Pytincture will not require a
database, process-memory session table, sticky routing, or Redis for ordinary
login sessions.

Why:

- Stateless sessions keep deployment and load balancing simple.
- Application audience, absolute expiration, claim allowlisting, key rotation,
  and secure cookie controls provide the normal security boundary.

Required controls that remain in scope:

- Production cookies must be Secure and use bounded idle and absolute
  lifetimes.
- Session identity claims must have a positive schema and total size limit.
- Sensitive deployments may opt into a shared revocation provider, but it is
  not a framework requirement.

## Application admission remains stateless

An intentionally single-trust service may use one global identity policy for
all of its applications. A shared multi-app service can opt into a fail-closed
application admission mapping for provider, issuer, tenant, subject,
email/domain, and role constraints. Pytincture will not require server-side
session state to make this authorization decision.

Why:

- The verified identity and admitted application fit in the signed browser
  session and can be checked independently by every worker.
- Redis, sticky routing, and in-process ACL state do not improve a deterministic
  identity-to-application decision.

Required controls that remain in scope:

- Check admission before every authentication flow issues a session.
- Bind the admitted application as the signed session audience and recheck the
  rule whenever that audience is enforced.
- When any per-application mapping is configured, deny applications missing
  from it and reject malformed rules at startup.
- Recommend separate origins/processes for materially different trust domains.

## Redis is optional, not a Pytincture dependency

Redis or another shared atomic store may be configured for features that
inherently require fleet-wide mutation, such as immediate cross-worker
revocation or strict one-time token consumption. Those optional controls may
not turn Redis into a requirement for normal sessions, load balancing, SAML
browser binding, or BFF operation.

Why:

- The framework must work across workers using signed browser state alone.
- A feature requiring atomic shared consumption should expose a pluggable
  store contract or fail closed when enabled without a suitable topology.

When a shared store is explicitly enabled, synchronous client operations are
kept off async request paths, bounded by deadlines/admission and a circuit
breaker, and readiness probes are briefly coalesced. The legacy read cache is
disabled by default; its explicit mode stores only positive values under entry
and TTL limits. These operational controls do not add shared state to normal
sessions or make Redis part of the default deployment.

## BFF replay proofs are optional and topology-explicit

One-time BFF request proofs remain disabled by default. Enabling the local
mode creates bounded, disposable per-worker proof state, not login/session
state. Refills are limited per signed session, direct peer, and worker; the
local store has fixed session/worker capacities and expiration-indexed cleanup.

Local mode guarantees atomic consumption only inside one worker. A deployment
that requires strict fleet-wide single consumption must explicitly enable that
requirement and install any provider implementing the vendor-neutral
`AtomicReplayStore` contract. Startup fails closed if the provider is absent or
declares itself local-only. Redis is one optional adapter, not a requirement.

Why:

- Fleet-wide single consumption is inherently a shared atomic-state property.
- Ordinary BFF calls, browser-carried sessions, SAML handshakes, and load
  balancing do not depend on replay proofs and remain stateless.
- Explicit topology selection prevents a per-worker store from silently being
  mistaken for a fleet-wide guarantee.

## Stateless logout has a bounded revocation window

Without an optional shared revocation provider, logout deletes the browser's
cookie but cannot invalidate a copy stolen before logout. Pytincture accepts
this standard stateless-session boundary rather than requiring server state.

Required controls are short absolute lifetimes for sensitive deployments,
signing-key rotation, Secure/HttpOnly cookie protection, and an optional shared
revocation provider when immediate fleet-wide logout is required.

## Stateless SAML cannot guarantee global single consumption

Pytincture binds SAML login to a signed, expiring HttpOnly handshake cookie and
requires exact request, response, and assertion correlation. Without shared
mutable state, it cannot guarantee that exactly one of multiple raw requests
reusing the same pre-response cookie and valid assertion wins across all
workers.

Why:

- Cross-worker portability without sticky routing or Redis is intentional.
- Strict global single-consumption is mathematically a shared-state property.

Required controls that remain in scope:

- Reject cross-browser login CSRF and ordinary browser replay.
- Require cryptographically authenticated correlation, modern signature
  algorithms, bounded assertion lifetimes, and short transaction expiry.
- Permit a pluggable atomic consumption provider for deployments that require
  strict one-time semantics; do not require Redis specifically.

## BFF modules are deployment-trusted server code

Pytincture will not require every BFF call to execute in a separate sandboxed
process by default. BFF modules are application code installed by the service
operator, not arbitrary code supplied by a caller.

Why:

- Mandatory process isolation would substantially change latency, streaming,
  object lifecycle, and deployment complexity.

Required controls that remain in scope are bounded admission, time, request,
result, and stream sizes; safe failure behavior; and an optional isolated
executor for deployments that deliberately run less-trusted application code.
Ordinary results are byte/depth/item bounded before a response is retained.
Stream items are serialized under the remaining byte budget before being
retained. The opt-in process executor provides killable wall-time execution,
per-worker/per-user admission, CPU limits on POSIX, address-space limits on
Linux, and the same output boundary. It intentionally rejects streaming BFFs;
trusted mode remains the default and keeps current streaming/object behavior.
The child-to-parent channel is a bounded, versioned byte protocol carrying
canonical JSON or fixed failure statuses. The parent never performs pickle or
another executable deserialization operation on child-controlled bytes.

## HSTS remains an edge responsibility

Pytincture expects production TLS termination at a reverse proxy or managed
edge, which owns HSTS policy. The application will not emit a universal HSTS
header because it cannot know the public domain's preload and subdomain policy.

Required controls are explicit production documentation and qualification
evidence verifying HTTPS redirects, HSTS, canonical origin, and trusted proxy
configuration at the deployed edge.

## Reconsidering a decision

Changing one of these decisions requires an explicit contract/versioning
proposal that explains the security benefit, developer migration, deployment
impact, and compatibility cost. A new scanner report alone is not sufficient;
new evidence that a retained control cannot enforce its stated boundary is.
