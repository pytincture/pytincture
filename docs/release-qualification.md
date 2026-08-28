# 1.0 release-candidate qualification

The machine-readable evidence record is
[`release/qualification.json`](../release/qualification.json). CI validates it
with `scripts/check_release_gates.py`; a final `v1.0.0` tag cannot publish until
every gate below has durable evidence.

## Current decision: NO-GO

As of 2026-08-28, engineering implementation for the roadmap workstreams is in
stacked review, but no `1.0.0rc1` or `1.0.0rc2` artifact has been published.
The 30-day observation clock has therefore not started, representative
federated-auth production evidence does not exist, and no final approval has
been recorded. In addition, #162 tracks high-severity Starlette advisories that
cannot yet be resolved inside FastAPI's compatible dependency range.

## Evidence required after each RC

For every release candidate, record:

- the PEP 440 version, publication timestamp, full commit SHA, and green CI URL;
- SHA-256 digests from the retained wheel, sdist, and npm tarball;
- standalone browser, authenticated BFF, and production-style SAML or OAuth
  acceptance results for the latest RC;
- upgrade from 0.10.7 and package/deployment rollback results;
- a security review reporting zero open critical/high findings; and
- a defect audit reporting zero open P0/P1 issues.

Every exercise needs an ISO-8601 UTC timestamp, `passed` status, tested RC
version, and durable evidence URL. Do not use local-only logs as final evidence.

The repository labels `priority:P0`, `priority:P1`, `security:critical`,
`security:high`, and `release-blocker` are release-blocking. Release-event CI
audits open issues for those labels before publishing.

Every pull request audits all Python runtime extras and npm dependencies.
Starlette advisories listed in `security/pip-audit-allowlist.json` are temporary
exceptions tied to #162 so unrelated new findings still fail CI. Release-tag
auditing treats any remaining exception as a blocker even if its issue is
accidentally closed; the allowlist must be empty before a 1.0 tag.

## RC sequence

1. Merge the complete roadmap chain and set the canonical Python version to
   `1.0.0rc1`.
2. Run `npm run build`. Python/browser runtime versions remain `1.0.0rc1`; npm
   metadata uses the SemVer-equivalent `1.0.0-rc.1`.
3. Push the signed tag and wait for tag-triggered version/qualification gates,
   blocker audit, and full CI. Then publish the GitHub prerelease. Copy artifact
   hashes and CI evidence into the qualification record in a follow-up PR.
4. Run representative applications and begin the observation log.
5. Resolve discovered blockers, publish/record `1.0.0rc2`, and repeat every
   latest-RC exercise.
6. After at least 30 days from rc1, complete security/defect audits, execute
   rollback, and record an explicit `go` or `no-go` decision with approvers.
7. Set `1.0.0`, build synchronized artifacts, and create the final tag. CI
   refuses publication if the record is incomplete, stale, or says no-go.

## Automated upgrade and rollback

Every CI run installs published `pytincture==0.10.7`, runs a documented public
service/package probe, force-installs the candidate wheel into the same
environment, repeats the probe, reinstalls 0.10.7, and proves the rollback
result matches the baseline. JSON results are retained as the
`upgrade-rollback-results` artifact. RC qualification links the successful run
from the evidence record.

## Commands

```bash
python scripts/check_release_gates.py
python scripts/check_release_gates.py --release-ref v1.0.0rc1
python scripts/check_release_gates.py --release-ref v1.0.0
GITHUB_TOKEN=... python scripts/audit_release_blockers.py
```

The first command validates static controls and version alignment; it does not
declare the final release ready. The final command succeeds only when the
GitHub issue audit finds no labeled blocker.
