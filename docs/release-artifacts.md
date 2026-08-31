# Release artifacts and dependency extras

Pytincture publishes three semantically synchronized artifacts:

- a platform-independent Python wheel;
- a Python source distribution; and
- the `@pytincture/runtime` npm tarball.

The Python wheel embeds the backend, browser runtime bundles, service worker,
and the pinned Pyodide 0.29.3 runtime. The npm package intentionally contains
only its README, package metadata, and built JavaScript bundles; it does not
duplicate Pyodide.

## Dependency model

`pip install pytincture` installs the FastAPI/Starlette service core. Optional
features have explicit extras:

| Extra | Required for |
| --- | --- |
| `password` | Argon2id or bcrypt local-password verification |
| `oauth` | Google or Microsoft OAuth/OIDC |
| `saml` | OneLogin SAML 2.0 and xmlsec |
| `redis` | Optional Upstash-backed legacy shared state, immediate cross-worker revocation, and replay tokens |
| `mcp` | FastMCP endpoint generation |
| `dev` | All feature stacks plus Python test/build/release tools |

Missing feature dependencies produce an installation hint such as `install
pytincture[saml]` when that feature is enabled. Dependency ranges accept
compatible non-breaking updates; the repository lockfiles pin the exact
versions used by development and CI.

## Local artifact verification

Build the runtime first, then all three release files:

```bash
cd pytincture/frontend
npm ci
npm run build
mkdir -p ../../dist
npm pack --pack-destination ../../dist
cd ../..
python -m build
export SOURCE_DATE_EPOCH="$(git log -1 --pretty=%ct)"
python scripts/normalize_sdist.py dist/pytincture-*.tar.gz "$SOURCE_DATE_EPOCH"
python scripts/inspect_release_artifacts.py \
  --wheel dist/pytincture-*.whl \
  --sdist dist/pytincture-*.tar.gz \
  --npm dist/pytincture-runtime-*.tgz
```

The validator applies
[`contracts/release-artifacts-v1.json`](../contracts/release-artifacts-v1.json),
checks source and artifact versions, verifies base dependencies and declared
extras, rejects development files, confirms required browser/Pyodide assets,
and prints SHA-256 hashes.

Stable Python and npm versions are identical. For prereleases, the canonical
PEP 440 version `1.0.0rc1` maps to npm SemVer `1.0.0-rc.1`; the embedded browser
runtime reports the canonical framework version.

CI derives `SOURCE_DATE_EPOCH` from the release commit. Wheels already honor
that standard timestamp; the normalization step sorts sdist entries and fixes
archive ownership and timestamps. Rebuilding the same commit therefore yields
byte-identical Python artifacts.

## CI and publication

Every pull request builds and inspects the artifacts, installs both wheel and
sdist into clean base environments, and installs each feature extra in its own
clean environment. It also runs Python 3.13/3.14, JavaScript, Chromium,
Firefox, WebKit, and production topology gates.

Publishing a GitHub release runs the same gates and attests the exact validated
artifact bytes after they pass. PyPI publishes from that release run. The npm
publisher additionally verifies the published tag and commit, release-triggered
CI identity, artifact attestation, package identity, filename, and mapped
SemVer. A manual npm retry accepts only a published tag and resolves the same
successful attested release run; it cannot select an arbitrary CI artifact.
