# Vendored Pyodide tarfile fix

The bundled Pyodide 0.29.3 runtime uses CPython 3.13.2. Its `tarfile`
module receives a narrow backport for
[CVE-2026-82049](https://www.cve.org/CVERecord?id=CVE-2026-82049):
hard links resolve their source before linking, following
[CPython's fix](https://github.com/python/cpython/commit/b8f23e307097552eaea2604383a12ab280520d0d).
Pyodide has no `os.link`, so its fallback also follows archive link chains to
regular file data instead of relocating a relative symlink. Cycles and
non-file hard-link targets are rejected.

Only `tarfile.py` inside `python_stdlib.zip` changes. The WebAssembly binary,
interpreter version, package ABI, package catalog, and application API stay
the same. This is a downstream backport, not an upgrade of CPython to 3.13.16;
a version-only scanner may still flag the runtime. The patch and its exact
source/output hashes are recorded in `security/pyodide-upstream.json` and
the vendored SBOM identifies the backport. This does not claim to resolve
other CPython vulnerabilities.

Rebuild and verify the vendored archive with:

```sh
python scripts/patch_vendored_pyodide.py
python scripts/generate_vendored_pyodide_sbom.py
node pytincture/frontend/generate-integrity-manifest.mjs
python scripts/validate_vendored_pyodide.py --verify-upstream
```

Upstream verification requires every other stdlib member to match the pinned
official Pyodide archive. Regression tests check both extraction filters,
compressed archives, selected members, valid links, and ordinary streaming
extraction, including the actual browser runtime.

For the server interpreter, use CPython 3.13.16 or a supported stable 3.14
release to address this CVE. Updating the vendored browser module does not
patch the server's Python installation. The PyJWT and urllib3 CVEs reported
alongside this issue are already fixed by RC14's dependency locks; existing
application environments must also install patched dependency versions.
