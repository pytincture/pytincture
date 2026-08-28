# Contributing

Use Python 3.13, Node 24, and a branch from current `main` (or the documented
stack base for roadmap PRs).

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cd pytincture/frontend
npm ci
cd ../..
```

Before opening a pull request:

```bash
python -m pytest -q
cd pytincture/frontend
npm test
npm run test:browser
npm run build
cd ../..
git diff --exit-code -- pytincture/frontend/package.json pytincture/frontend/package-lock.json pytincture/frontend/dist
```

Changes to browser delivery, Pyodide, widget installation, authentication, or
service workers should also run the [real E2E matrix](docs/e2e-testing.md).
Update public contracts and migration docs when behavior changes. New supported
configuration belongs in `PytinctureConfig`; its metadata row is automatically
checked against `docs/configuration.md`.

Do not commit secrets, local wheels unless they are intentional test fixtures,
Playwright reports, virtual environments, or caches. Report vulnerabilities
through [`SECURITY.md`](SECURITY.md), not a normal issue.
