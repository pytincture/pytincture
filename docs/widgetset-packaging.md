# Widgetset browser packaging

Pytincture widgetsets remain pluggable Python wheels. A wheel that needs
browser JavaScript or CSS must own one `pytincture-assets.json` file and list
every executable/style asset in load order. Files outside that list—including
files from transitive packages—are never evaluated.

## Manifest

Place the manifest inside the widgetset's import package and include it as
package data:

```json
{
  "schema": 1,
  "package": "mywidgets",
  "version": "1.2.3",
  "assets": [
    {
      "path": "mywidgets/assets/widgets.css",
      "type": "css",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    },
    {
      "path": "mywidgets/assets/widgets.js",
      "type": "javascript",
      "sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
    }
  ]
}
```

Paths are wheel-relative POSIX paths and must belong to the widget
distribution. Only `javascript`/`.js` and `css`/`.css` pairs are accepted.
Every SHA-256 value is checked against the installed bytes before the asset is
used. CSS font references may inline only font files owned by the same wheel.

The manifest package and version must exactly match installed distribution
metadata. Keep asset order explicit when one script depends on another.

The wheel owns this manifest, so it is integrity metadata for the files selected
from that wheel—not an independent authorization root. The exact package pin or
hash-locked wheel source remains the default deployment trust decision.

## Deployment-owned trust policy

High-trust service deployments may set `widget_trust_policy` or
`PYTINCTURE_WIDGET_TRUST_POLICY` to an inline JSON document or a JSON file path:

```json
{
  "schema": 1,
  "widgetsets": [
    {
      "distribution": "mywidgets",
      "version": "1.2.3",
      "assets": [
        {
          "path": "mywidgets/assets/widgets.css",
          "type": "css",
          "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        },
        {
          "path": "mywidgets/assets/widgets.js",
          "type": "javascript",
          "sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
        }
      ]
    }
  ]
}
```

The service validates and canonicalizes the complete policy at startup. Every
application widget must match one exact distribution/version entry. The
administrator-owned asset list is passed to the browser and takes precedence
over `pytincture-assets.json`; the browser still verifies ownership and hashes
against installed bytes before evaluation. An unlisted distribution, version,
asset, or changed byte fails closed. Backend development-wheel fallback is
disabled in this mode unless the application explicitly declares that version.
The policy is static and requires no shared runtime state.

## Installation locks

- Pytincture's built-in compatibility widget release uses an immutable PyPI
  file URL plus a deployment-owned complete-wheel SHA-256 lock recorded in
  `security/widget-wheel-locks.json`. Service mode still checks the backend
  first; this safe compatibility lock is used only when no backend wheel is
  available. PyPI installs never receive the backend instance UUID.
- Hosted custom widgetsets use the backend wheel matching the discovered
  installed distribution/version first, followed by the explicitly configured
  development version. Retain or download the pure-Python wheel into
  `MODULES_PATH`; a normal pip installation does not guarantee that the
  original wheel archive remains available.
- A hosted custom widget does not silently fall through to PyPI. A deployment
  that intentionally trusts that source must list the exact `name==version` in
  `PYTINCTURE_WIDGET_PUBLIC_INDEX_ALLOWLIST`. Standalone owners can make the
  same explicit choice through `allowPublicWidgetIndex`, or preferably provide
  an administrator-owned `widgetSource` URL with its own
  `#sha256=<64 hex>` lock.
- Explicit wheel sources include `#sha256=<64 hex>`.
- A Pytincture backend computes the complete wheel SHA-256 and exposes it in
  `X-Pytincture-SHA256`; the runtime adds that lock before giving the backend
  URL to micropip. The backend serves only the application's declared version
  and `PYTINCTURE_DEV_WHEEL_VERSION`; the normal real-version-first,
  `99.99.99`-last default order is unchanged.
- Backend wheel metadata is checked with `HEAD` first. A cache miss performs
  one bounded `GET` to compute the digest; subsequent requests reuse that
  digest only while the verified device/inode/size/mtime/ctime identity is
  unchanged. ETag conditional requests, per-worker response admission, a
  peer/application rate limit, and a 64 MiB default maximum bound public wheel
  work. The `PYTINCTURE_WIDGET_WHEEL_*` settings can tune these deployment
  limits without adding shared state.
- Automatic dependency installation is disabled. Applications list every
  additional browser dependency as its own exact or hash-locked entry in
  `#micropip-libs`.

For a controlled legacy wheel, `widgetAssetManifest` accepts the same object in
runtime configuration. In standalone mode the HTML owner can use this as its
deployment-owned lock. Shipping the manifest in the wheel is convenient because
the widgetset and its asset contract then version together, but is not an
independent authorization signal.

Cross-origin companion wheel servers must allow the page origin and expose the
`X-Pytincture-SHA256` response header to browser JavaScript.
