# 1.0 release-candidate qualification

The machine-readable evidence record is
[`release/qualification.json`](../release/qualification.json). CI validates it
with `scripts/check_release_gates.py`; a final `v1.0.0` tag cannot publish until
every gate below has durable evidence.

## Current decision: NO-GO; observation active

As of 2026-08-29, the signed `v1.0.0rc1` GitHub prerelease and PyPI
`1.0.0rc1` wheel and source distribution are published. The matching retained
npm tarball is validated and integrity-checked; npm registry publication is
deferred and does not block the approved observation track.

The release manager approved the successful `pytincture_example` run, so the
formal 30-day observation period began at `2026-08-29T02:08:06Z` and cannot
complete before `2026-09-28T02:08:06Z`. The release remains NO-GO: no
`1.0.0rc2`, complete latest-RC evidence, final security/defect and repository
policy audits, or final approval exists. The Starlette security blocker is
resolved by requiring the patched 1.6 release line and allowing no
dependency-audit exceptions.

Published and retained rc1 evidence:

- signed tag and GitHub prerelease:
  <https://github.com/pytincture/pytincture/releases/tag/v1.0.0rc1>;
- green tag qualification run:
  <https://github.com/pytincture/pytincture/actions/runs/33206734049>;
- release run, including successful PyPI publication and failed npm
  publication:
  <https://github.com/pytincture/pytincture/actions/runs/33206884950>;
- wheel SHA-256:
  `aa5e11139f14bf8317bdb77d57c49cd55311533370414506fed79b1a6fc9e64b`;
- source-distribution SHA-256:
  `d3546a1109efdee1d61fac15d3a8e7252d3c111d8158c060fd5cac6343187bbb`;
- retained npm tarball SHA-256:
  `085f5fb67a4b6e4878321579159e66e03892d6a7f60740c8421cb61cb3a7b587`;
- approved representative-application observation:
  <https://github.com/pytincture/pytincture_example/actions/runs/33228136035>;
  and
- durable observation-start approval:
  <https://github.com/pytincture/pytincture/issues/143#issuecomment-5459957268>.

The npm registry publication remains a distribution task before the final
release; the retained tarball hash is sufficient to keep its tested bytes tied
to this candidate while observation proceeds.

## Evidence required after each RC

For every release candidate, record:

- the PEP 440 version, publication timestamp, full commit SHA, and green CI URL;
- SHA-256 digests from the retained wheel, sdist, and npm tarball;
- standalone browser, authenticated BFF, and production-style SAML or OAuth
  acceptance results for the latest RC;
- upgrade from 0.10.7 and package/deployment rollback results;
- browser and service performance evidence satisfying the versioned budgets;
- an administrator-run release branch-protection audit;
- a security review reporting zero open critical/high findings; and
- a defect audit reporting zero open P0/P1 issues.

Every exercise needs an ISO-8601 UTC timestamp, `passed` status, tested RC
version, and durable evidence URL. Do not use local-only logs as final evidence.

## Automated exercise evidence

Qualification jobs emit documents governed by
[`contracts/qualification-evidence-v1.schema.json`](../contracts/qualification-evidence-v1.schema.json).
Each document records the exercise type, pass/fail status, tested Python and npm
versions, UTC timestamp, full tested commit, durable Actions run URL and run
identity, and SHA-256 hashes for the wheel, source distribution, npm tarball,
and every embedded raw result.

The shared `scripts/build_qualification_evidence.py` producer is used for the
standalone, authenticated BFF/browser-performance, federated SAML,
upgrade/rollback, and service-performance tracks. These generated files are
candidate evidence: a release reviewer still links the retained artifact and
records the approved exercise in `release/qualification.json`. A passing CI
run alone does not start an observation period or change the NO-GO decision.
The clock starts only when `observation_started_at` and a durable
`observation_approval_url` are recorded after release-manager approval.

The repository labels `priority:P0`, `priority:P1`, `security:critical`,
`security:high`, and `release-blocker` are release-blocking. Release-event CI
audits open issues for those labels before publishing.

The versioned `contracts/repository-policy-v1.json` defines branch protection.
The bootstrap profile protects the current stack with its available checks.
After the final CI workflow reaches `main`, an administrator applies and audits
the release profile, which requires every Python, JavaScript, browser,
artifact, optional-extra, security, production, and upgrade/rollback check.
The built-in Actions token cannot read administration policy, so the resulting
audit URL is recorded under `repository_policy_reviews` rather than relying on
an under-privileged CI API call.

Every pull request audits all Python runtime extras and npm dependencies. The
`security/pip-audit-allowlist.json` advisory list is empty; any new finding fails
CI. Release-tag auditing also treats any future exception as a blocker even if
its tracking issue is accidentally closed.

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
6. After at least 30 days from the approved observation start, complete
   security/defect audits, execute rollback, and record an explicit `go` or
   `no-go` decision with approvers.
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
