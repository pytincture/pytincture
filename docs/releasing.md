# Release and rollback procedure

Only a maintained release commit with green required CI is publishable.

1. Choose the canonical PEP 440 version and run
   `python scripts/set_release_version.py <version>`. It updates Python/project
   metadata, maps npm SemVer, rebuilds runtime bundles, updates examples, and
   refreshes the lockfile.
2. Update `CHANGELOG.md`, compatibility versions, migration notes, and the
   release qualification record.
3. Run Python 3.13/3.14, JavaScript, browser lifecycle, Chromium/Firefox/WebKit
   Pyodide E2E, clean artifact/extras, and production topology gates.
4. Create and push a signed `v<version>` tag at the green commit. Wait for the
   tag-triggered CI, version/qualification gate, and blocker audit to pass.
5. Publish a GitHub release from that qualified tag. The release event rebuilds
   artifacts with the commit timestamp, verifies
   byte reproducibility/content/hashes, then publishes those exact wheel,
   sdist, and npm files after every dependency job succeeds.
6. Verify PyPI/npm metadata and install each artifact from the public index in
   a new environment. Attach the generated `SHA256SUMS.json` to the release.

Prerelease npm artifacts are published under the `next` dist-tag; only stable
releases update `latest`.

There is intentionally no independent GitHub publish workflow. Never upload a
locally rebuilt file under an existing version.

## Failed publication

Do not overwrite an immutable PyPI/npm version. If one registry succeeds and
the other fails, diagnose credentials/registry state, retry the exact retained
artifact when safe, and record the partial release. If artifact bytes or code
must change, bump the version and issue a new release.

## Runtime rollback

Follow the [production rollback runbook](production-deployment.md#rollback).
Registry artifacts are immutable and are not deleted as a normal rollback;
deploy the last known-good pinned version, preserve current/previous signing
keys, verify readiness/login/BFF/streaming, and document the incident.

For release candidates, Python uses PEP 440 (`1.0.0rc1`) while npm uses the
SemVer-equivalent (`1.0.0-rc.1`). The browser runtime continues to report the
canonical Python/framework version. `npm run build` and artifact inspection
enforce this mapping. The [qualification procedure](release-qualification.md)
defines evidence and the current go/no-go state.
