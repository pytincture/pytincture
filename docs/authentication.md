# Authentication and sessions

Install the feature used by the deployment: `pytincture[password]`,
`pytincture[oauth]`, or `pytincture[saml]`. All production authentication
modes require a stable `SAML_SECRET_KEY` with at least 32 random characters
and sufficient character variety. Despite its historical name, this key signs
all Pytincture sessions.

Production authentication also requires exact `PYTINCTURE_ALLOWED_HOSTS` and
one HTTPS `PYTINCTURE_CANONICAL_ORIGIN`. Pytincture uses that fixed origin for
OAuth and SAML URLs instead of trusting the request `Host`. Proxy-header trust
is accepted only when both controls are configured.

Local HTTP auth testing can explicitly set
`PYTINCTURE_ALLOW_DEVELOPMENT_AUTH_ORIGIN=true` and
`AUTH_SESSION_HTTPS_ONLY=false`. The supported launcher confines this mode to a
literal loopback listener, and the application rejects non-loopback peers even
under another ASGI launcher. It cannot be combined with trusted proxy headers
or production host/origin controls.

## Local password login

Set `ENABLE_USER_LOGIN=true` and provide either `AUTH_PASSWORD_HASHES` or an
`AUTH_USER_AUTHENTICATOR` dotted callable. `ALLOWED_EMAILS` is an authorization
allowlist, not a password database. Hash values must be Argon2id or bcrypt:

```bash
python -c 'from argon2 import PasswordHasher; print(PasswordHasher().hash("change-me"))'
```

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

## OAuth/OIDC

Set the Google or Microsoft enable flag and client credentials. Microsoft also
requires `MICROSOFT_TENANT_ID`; the multi-tenant `common` issuer is not
accepted. Google callbacks require a verified email and both providers retain
the immutable issuer/subject identity in the signed session. Register the exact
callback URL shown by the route:

Microsoft requests only `openid email profile`. Pytincture does not retain or
refresh provider tokens, so it does not request `offline_access`.

- `https://host/{application}/auth/google/callback`
- `https://host/{application}/auth/microsoft/callback`

When a trusted proxy terminates TLS, enable forwarded headers only if that
proxy replaces client values. A callback generated with `http://` or an
internal host indicates proxy configuration, not an identity-provider issue.

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
session lifetime.

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
