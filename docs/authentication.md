# Authentication and sessions

Install the feature used by the deployment: `pytincture[password]`,
`pytincture[oauth]`, or `pytincture[saml]`. All production authentication
modes require a stable `SAML_SECRET_KEY` with at least 32 random characters
and sufficient character variety. Despite its historical name, this key signs
all Pytincture sessions.

Production authentication also requires exact `PYTINCTURE_ALLOWED_HOSTS` and
one HTTPS `PYTINCTURE_CANONICAL_ORIGIN`. Pytincture uses that fixed origin for
OAuth and SAML URLs instead of trusting the request `Host`. Proxy-header trust
is accepted only when both controls are configured. Production authentication
always uses Secure host-only cookies: `__Host-pytincture-session` for the
HttpOnly session, `__Host-pytincture-csrf` for the readable CSRF token, and an
application-specific `__Host-pytincture-saml-handshake-*` cookie during SAML
login. Each uses `Path=/` with no `Domain` attribute so a sibling hostname
cannot inject it. An explicit `AUTH_SESSION_HTTPS_ONLY=false` is rejected.

Local HTTP auth testing can explicitly set
`PYTINCTURE_ALLOW_DEVELOPMENT_AUTH_ORIGIN=true` and
`AUTH_SESSION_HTTPS_ONLY=false`. The supported launcher confines this mode to a
literal loopback listener, and the application rejects non-loopback peers even
under another ASGI launcher. It cannot be combined with trusted proxy headers
or production host/origin controls. HTTP development uses separate
`pytincture-dev-*` cookie names rather than weakening the production cookie
contract.

## Local password login

Set `ENABLE_USER_LOGIN=true` and provide either `AUTH_PASSWORD_HASHES` or an
`AUTH_USER_AUTHENTICATOR` dotted callable. `ALLOWED_EMAILS` is an authorization
allowlist, not a password database. Hash values must be Argon2id or bcrypt:

```bash
python -c 'from argon2 import PasswordHasher; print(PasswordHasher().hash("change-me"))'
```

Argon2id is preferred for new hashes. Bcrypt remains compatible, including its
72-byte input boundary: an oversized bcrypt attempt is rejected only after the
same Argon2id dummy work used for an unknown account, preventing the fast-error
account probe introduced by bcrypt 5. Credential stores implemented through
`AUTH_USER_AUTHENTICATOR` may call
`pytincture.backend.auth.verify_password_hash()` and, after successful
authentication, atomically persist its optional `replacement_hash`. That value
upgrades bcrypt (and outdated Argon2id parameters) to the current Argon2id
policy. `AUTH_PASSWORD_HASHES` is static deployment configuration, so
Pytincture never mutates it or keeps a process-local replacement.

`ENABLE_DEV_EMAIL_LOGIN=true` bypasses password verification only when the
actual network peer and direct browser-facing `Host` are literal loopback IP
addresses. Any supplied `Origin` or `Referer` must also use a literal loopback
IP; forwarded headers cannot enable it. Typed configuration rejects this mode
with proxy trust, public host/origin settings, or a production identity
provider. With `launch_service()`, the development mode automatically binds to
`127.0.0.1` unless another literal loopback `host` is supplied, and a routable
bind is rejected. It must never be enabled in production.
`LOGIN_HELP_TEXT` is escaped plain text suitable for disposable demo
credentials.

Password forms and the JSON `/auth/mcp` login require a one-time CSRF
transaction bound to the application and signed browser session. Browser forms
receive it from `/{application}/login`; JSON clients first call
`GET /{application}/auth/mcp` and return its `login_csrf_token` in the login
body. The transaction is consumed once and expires after
`AUTH_LOGIN_CSRF_TTL_SECONDS`.

The signed browser session retains only approved identity keys. Individual
values, total key count, canonical identity JSON, and the final signed cookie
are bounded by `AUTH_SESSION_MAX_CLAIM_COUNT`,
`AUTH_SESSION_MAX_IDENTITY_BYTES`, and `AUTH_SESSION_MAX_COOKIE_BYTES`.
Oversized provider identities are rejected before a session is issued. These
checks remain stateless and require neither Redis nor sticky routing.

## OAuth/OIDC

Set the Google or Microsoft enable flag and client credentials. Microsoft also
requires `MICROSOFT_TENANT_ID`; the multi-tenant `common` issuer is not
accepted. Google callbacks require a verified email and both providers retain
the immutable issuer/subject identity in the signed session. Microsoft also
retains the tenant-scoped immutable Entra object id as `oid`. For sensitive
authorization, use tenant plus `oid` (the `object_ids` application-admission
field), or issuer plus subject. Microsoft email is mutable display data;
email/domain admission remains supported for simple applications and emits a
non-blocking production warning when no stable identity/role constraint is
present. Register the exact callback URL shown by the route:

Microsoft requests only `openid email profile`. Pytincture does not retain or
refresh provider tokens, so it does not request `offline_access`.

- `https://host/{application}/auth/google/callback`
- `https://host/{application}/auth/microsoft/callback`

When a trusted proxy terminates TLS, enable forwarded headers only if that
proxy replaces client values. A callback generated with `http://` or an
internal host indicates proxy configuration, not an identity-provider issue.

OAuth initiation and callback traffic is rate-limited per network peer,
application, and provider. Provider token exchanges also have a small bounded
per-worker admission gate plus explicit connection, read, write, pool, and
overall deadlines. The defaults are deliberately generous for interactive
login and can be adjusted with the `OAUTH_*` settings documented in
[configuration](configuration.md). Unknown applications are rejected before
provider discovery or token exchange work begins.

These controls are disposable and process-local: normal OAuth remains
stateless across load-balanced workers and requires neither Redis nor sticky
routing. A caller that restores an old valid browser state cookie can retry a
callback until that state expires, but the request and provider work are now
bounded. Deployments that require globally single-use OAuth transactions may
add an atomic shared transaction provider as a separate strict mode; it is not
part of the default framework contract.

## Per-application identity admission

The global provider checks establish who the user is. A shared multi-app
service can additionally configure `AUTH_APPLICATION_ADMISSION` so each
application decides which verified identities may receive its signed session.
Rules support provider, issuer, tenant, immutable subject, exact email,
email-domain, and role constraints. All configured dimensions must match and
unlisted applications fail closed.

This check runs before session issuance for local login, OAuth/OIDC, SAML, and
the JSON login endpoint. It is also repeated when enforcing the session's
application audience. The decision depends only on verified claims carried in
the signed browser session, so replicas need neither Redis nor sticky routing.
See [configuration](configuration.md) for the rule format. Leave the mapping
empty for an intentionally single-trust service. Use separate origins and
processes when applications represent materially different trust domains.

## SAML

Single-provider deployments use the `SAML_IDP_*` values. Multi-provider
deployments use `SAML_PROVIDERS`; providers share the standard entity/ACS URLs
unless explicitly overridden. `SAML_REQUESTED_AUTHN_CONTEXT` defaults to
`false`. If an identity provider offers “password” as a manual fallback but
rejects the initial request, first confirm this setting is false and that the
new process actually received the environment value.

Never disable signature/certificate validation to work around IdP errors.
Use metadata at `/{application}/auth/saml/metadata`, confirm entity ID, ACS,
certificate formatting, clock synchronization, and forwarded HTTPS origin.
Pytincture accepts response-signed IdPs and assertion-only IdPs. For an
assertion-only IdP, the signed assertion's `SubjectConfirmationData` must carry
the exact AuthnRequest `InResponseTo`; an unsigned outer Response cannot supply
that trusted correlation. SAML signature and digest algorithms must use
SHA-256 or stronger. SHA-1 and unknown algorithms are rejected before toolkit
signature processing.

Encrypted assertions are currently rejected before the SAML toolkit runs. The
supported toolkit decrypts assertions before exposing their signature
transforms, so accepting them would bypass Pytincture's pre-signature transform
allowlist. Configure the IdP to send signed plaintext assertions. This
restriction can be removed only when the decrypted document can be checked
before xmlsec signature processing.

`SAML_REQUESTED_AUTHN_CONTEXT` is a boolean setting: use `true` to request a
context or `false` (the default) to omit `RequestedAuthnContext` entirely. It
does not accept a numeric authentication-context identifier.

## Session behavior

The signed cookie stores compact stable identity claims, an opaque session ID,
and CSRF state—not passwords, tokens, assertions, or full SAML attributes.
Use `AUTH_SESSION_CLAIM_KEYS` for small additional trusted claims. Idle expiry
is controlled by `AUTH_SESSION_MAX_AGE_SECONDS`; the non-sliding upper bound is
`AUTH_SESSION_ABSOLUTE_MAX_AGE_SECONDS`. Rotate keys by placing equally strong
old values in `AUTH_SESSION_PREVIOUS_SECRET_KEYS` for at least the absolute
session lifetime. SAML sessions are additionally capped to the earliest
`Conditions.NotOnOrAfter`, `SubjectConfirmationData.NotOnOrAfter`, or
`AuthnStatement.SessionNotOnOrAfter` bound supplied by the validated response.

Login forms and logout use pre-authentication/CSRF checks, and logout is a
state-changing `POST`. By default, logout clears the browser cookie and the
service stores no session or revocation state in Redis or process memory, so
replicas require neither Redis nor sticky sessions. If immediate revocation of
a copied cookie is required, install `pytincture[redis]` and explicitly set
`USE_REDIS_INSTANCE=true`; authentication then fails closed when that shared
store is unavailable. See the [production runbook](production-deployment.md).

The signed SAML handshake rejects cross-browser login CSRF and normal
sequential response replay while remaining portable across workers. As a
documented stateless boundary, it cannot guarantee which of two simultaneous
raw requests with the same valid pre-response cookie and assertion is consumed
first. Deployments requiring strict single-consumption must add shared atomic
transaction control; this is optional and does not make Redis a framework
requirement. See the machine-readable
[`saml-replay-mitigation.json`](../security/saml-replay-mitigation.json).
