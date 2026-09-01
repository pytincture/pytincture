# Release and rollback procedure

Only a maintained release commit with green required CI is publishable.

1. Choose the canonical PEP 440 version and run
   `python scripts/set_release_version.py <version>`. It updates Python/project
   metadata, maps npm SemVer, rebuilds runtime bundles, updates examples, and
   refreshes `uv.lock`. `uv lock --check` must pass and release builds install
   the exact hash-bearing graph.
2. Update `CHANGELOG.md`, compatibility versions, migration notes, and the
   release qualification record.
3. After the complete 1.0 CI workflow is on `main`, apply and verify the full
   branch-protection contract with an administration token:
   `GITHUB_TOKEN=... python scripts/repository_policy.py --profile release --apply`.
   The policy requires one CODEOWNER approval, dismisses stale approvals, and
   requires approval from someone other than the last pusher. Keep at least two
   active maintainers in `CODEOWNERS` so author review cannot deadlock a release.
4. Run Python 3.13/3.14, JavaScript, browser lifecycle, Chromium/Firefox/WebKit
   Pyodide E2E, clean artifact/extras, and production topology gates.
5. Create and push a signed `v<version>` tag at the green commit. Wait for the
   tag-triggered CI, version/qualification gate, and blocker audit to pass.
6. Publish a GitHub release from that qualified tag. The release event rebuilds
   artifacts with the commit timestamp and verifies byte reproducibility,
   content, and hashes. Release CI signs GitHub artifact attestations after
   every qualification job succeeds. After that run completes,
   `pypi-publish.yml` and `npm-publish.yml` independently verify the published
   tag, exact commit, protected-default-branch ancestry, CI workflow identity,
   attestation, package identity, filename, and version. They publish only the
   exact retained wheel/sdist and npm tarball through registry trusted-publisher
   OIDC. Both workflows support idempotent retries by published release tag and
   resolve the trusted release run themselves.
7. Verify PyPI/npm metadata and install each artifact from the public index in
   a new environment. Attach the generated `SHA256SUMS.json` to the release.

Prerelease npm artifacts are published under the `next` dist-tag; only stable
releases update `latest`.

Never configure either publisher to accept a caller-selected CI run or locally
rebuilt file, and never upload a replacement under an existing version.

Pytincture does not currently publish an official container image. Do not add
container pull or run guidance until a protected tagged-release workflow
publishes immutable version and digest references, generates an SBOM, and
signs or attests the image. Mutable tags are not production pins.

The publishers use protected `pypi` and `npm` GitHub environments. Keep
non-self required reviewers enabled and restrict deployments to the protected
default branch. Protect creation, update, and deletion of `v*` release tags.
Publication actions are pinned to full commit SHAs, and values derived from
release or artifact metadata are passed to shell steps through environment
variables. Run manual tag-based retries from the protected default branch; the
tag is an input, not the workflow execution ref.

Configure the PyPI trusted publisher for project `pytincture` with owner and
repository `pytincture/pytincture`, workflow `pypi-publish.yml`, and environment
`pypi`. Do not configure `PYPI_PASSWORD`, `TWINE_PASSWORD`, or another fallback
credential. Until that PyPI-side publisher exists, publication fails closed
after repository verification rather than falling back to a long-lived token.

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

## Local npm artifact inspection

The only supported npm registry publication path is
`.github/workflows/npm-publish.yml`. Repository scripts never run `npm publish`
and local builds never receive registry credentials. To inspect the exact
package contents without publishing, run:

```bash
cd pytincture/frontend
npm ci --ignore-scripts
npm run build
npm pack --dry-run
```
