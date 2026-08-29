# Authentication and sessions

Install the feature used by the deployment: `pytincture[password]`,
`pytincture[oauth]`, or `pytincture[saml]`. All production authentication
modes require a stable `SAML_SECRET_KEY` with at least 32 random characters
and sufficient character variety. Despite its historical name, this key signs
all Pytincture sessions.

## Local password login

Set `ENABLE_USER_LOGIN=true` and provide either `AUTH_PASSWORD_HASHES` or an
`AUTH_USER_AUTHENTICATOR` dotted callable. `ALLOWED_EMAILS` is an authorization
allowlist, not a password database. Hash values must be Argon2id or bcrypt:

```bash
python -c 'from argon2 import PasswordHasher; print(PasswordHasher().hash("change-me"))'
```

`ENABLE_DEV_EMAIL_LOGIN=true` bypasses password verification only on loopback
hosts and must never be enabled in production. `LOGIN_HELP_TEXT` is escaped
plain text suitable for disposable demo credentials.

## OAuth/OIDC

Set the Google or Microsoft enable flag and client credentials. Register the
exact callback URL shown by the route:

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
Use `AUTH_SESSION_CLAIM_KEYS` for small additional trusted claims. Rotate keys
by placing old values in `AUTH_SESSION_PREVIOUS_SECRET_KEYS` for at least one
session lifetime.

One worker may use in-memory revocations. Multiple workers require
`pytincture[redis]` and `USE_REDIS_INSTANCE=true` so logout and one-time BFF
tokens are immediately shared. See the [production runbook](production-deployment.md).
