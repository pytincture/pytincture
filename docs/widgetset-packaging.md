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

## Installation locks

- PyPI widgetsets use an exact `name==version` requirement. Micropip verifies
  the package-index wheel hash.
- Explicit wheel sources include `#sha256=<64 hex>`.
- A Pytincture backend computes the wheel SHA-256 and exposes it in
  `X-Pytincture-SHA256`; the runtime adds that lock before giving the backend
  URL to micropip. The normal real-version-first, `99.99.99`-last fallback is
  unchanged.
- Automatic dependency installation is disabled. Applications list every
  additional browser dependency as its own exact or hash-locked entry in
  `#micropip-libs`.

For a controlled legacy wheel, `widgetAssetManifest` accepts the same object in
runtime configuration. Shipping the manifest in the wheel is preferred because
the widgetset and its asset contract then version together.

Cross-origin companion wheel servers must allow the page origin and expose the
`X-Pytincture-SHA256` response header to browser JavaScript.
