# `appcode.pyt` browser-package contract — version 1

Service mode downloads the browser application from:

```text
GET /{application}/appcode/appcode.pyt
```

The response uses `application/zip` and downloads as `appcode.pyt`. The `.pyt`
suffix is a transport convention; the payload is a standard ZIP archive.

## Archive layout

- All member names are relative, POSIX-style paths with no leading slash or
  parent traversal.
- `{application}.py` is the required entrypoint at the archive root.
- Statically reachable local Python imports are included recursively.
- Package `__init__.py` files required by included modules are included.
- Extra files selected by `PYTINCTURE_BROWSER_FILES` are included with their
  module-root-relative paths.
- Hidden directories, virtual environments, `node_modules`, `build`, `dist`,
  and `__pycache__` are excluded from automatic discovery.
- Symlinked files/directories are never packaged. Every member is opened
  relative to the canonical modules root with no-follow semantics where the
  operating system supports them.

Pytincture transforms Python source as it packages it. Exported BFF classes
become browser proxies while ordinary browser code and required imports remain
available. Archive member ordering and compression level are not contractual.

Construction is subject to configured file-count, individual-file,
aggregate-source, build-concurrency, and admission-wait limits. Public archives
without session-specific replay material may be served from a bounded
per-worker cache keyed by selected-file metadata. Limit failures return `413`;
temporary build saturation returns `503` with `Retry-After`.

## Security boundary

The archive is delivered under the application's authentication policy. Its
contents are visible to the browser and must never contain server secrets.
Python source is included only when it is the entrypoint, reachable through
static local imports, or explicitly selected. Dynamic imports must be declared
through `PYTINCTURE_BROWSER_FILES`.

Public assets served separately by `/{application}/appcode/{asset_path}` use a
separate allowlist and are not implicitly part of this archive contract.

## Evolution

Version 1 permits additive files required by an application, new optional
metadata members, and compatible source transformations. Removing required
files, changing the payload away from ZIP, changing relative-path semantics,
or making version-1 runtimes unable to locate the entrypoint requires a new
contract version and migration instructions.
