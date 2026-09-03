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
- The service discovers the callable without executing this file. A callable
  named for the application, a direct `MainWindow` subclass, or literal
  `APP_ENTRYPOINT`/`APP_CONFIG["entrypoint"]` metadata identifies it.
- Statically reachable local Python imports are included recursively.
- Import traversal stops at a proven BFF module: the module is emitted as a
  proxy, while imports used only by its server implementation are excluded.
- Package `__init__.py` files implicitly executed by ordinary browser modules
  are included and traversed. A package containing only BFF proxy modules is
  emitted as a namespace package so its server initializer is not exposed.
- Extra files selected by `PYTINCTURE_BROWSER_FILES` are included with their
  module-root-relative paths.
- Hidden directories, virtual environments, `node_modules`, `build`, `dist`,
  and `__pycache__` are excluded from automatic discovery.
- Hidden files and high-confidence credential, private-key, database, and
  backup filenames are rejected from explicit browser-file selection.
- Symlinked files/directories are never packaged. Every member is opened
  relative to the canonical modules root with no-follow semantics where the
  operating system supports them.

Pytincture transforms Python source as it packages it. Exported BFF classes
become browser proxies while ordinary browser code and required imports remain
available. Archive member ordering and compression level are not contractual.

Construction is subject to configured file-count, individual-file,
aggregate-source, build-concurrency, and admission-wait limits. Public archives
without session-specific replay material may be served from a bounded
per-worker cache. A warm lookup validates the digest-bearing source fingerprint
through no-follow file identity metadata and relevant directory metadata before
returning immutable cached bytes, without rereading or rehashing unchanged
sources. The LRU has independent entry-count and aggregate-byte limits. Limit
failures return `413`; temporary build saturation returns `503` with
`Retry-After`.

The response lifetime is separately bounded by configurable per-worker and
per-peer download admission, an absolute response duration, and a blocked-write
deadline. Admission is released only after completion, disconnect, or cleanup;
these limits do not alter archive contents.

Production deployments may build immutable archives ahead of time:

```text
pytincture-build-appcode demo --modules-path ./modules --output-directory ./appcode
```

Set `PYTINCTURE_APPCODE_PREBUILT_DIRECTORY=./appcode` to prefer
`./appcode/demo.pyt`, and optionally set
`PYTINCTURE_REQUIRE_PREBUILT_APPCODE=true` to fail closed when it is absent.
The command also writes `demo.pyt.json`, binding the complete archive digest,
the Pytincture transformer version, the browser-file declaration, and the exact
source file set and hashes. The service verifies this manifest and rejects a
missing, modified, or stale required archive before sending it; warm checks use
secure file and relevant-directory identities rather than rebuilding the ZIP.
The normal backend entrypoint at `MODULES_PATH/demo.py` remains required and is
not served from the archive. Dynamic packaging remains the default for
development. Session-specific BFF replay clients require dynamic archives and
cannot be combined with required prebuilt appcode.

## Security boundary

The archive is delivered under the application's authentication policy. Its
contents are visible to the browser and must never contain server secrets.
Python source is included only when it is the entrypoint, reachable through
static local imports, or explicitly selected. Dynamic imports must be declared
through `PYTINCTURE_BROWSER_FILES`.

Public assets served separately by `/{application}/appcode/{asset_path}` are
unauthenticated and are not implicitly part of this archive contract. The
application must have a real entrypoint, and each asset must belong to an
explicit browser/public file declaration, the application's favicon metadata,
or its authorized widget wheel. Service-wide declarations are intentionally
shared; application-keyed public declarations provide isolation. Python files
remain non-public, and SVG responses use a restrictive sandbox CSP.

## Evolution

Version 1 permits additive files required by an application, new optional
metadata members, and compatible source transformations. Removing required
files, changing the payload away from ZIP, changing relative-path semantics,
or making version-1 runtimes unable to locate the entrypoint requires a new
contract version and migration instructions.
