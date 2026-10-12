"""Exercise the actual test-server config carried by the pinned app patches."""
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pytincture import PytinctureConfig, create_app


@pytest.mark.parametrize("application", ["example", "chat"])
def test_conformance_server_applies_diagnostic_budget(tmp_path, monkeypatch, application):
    patch = (Path(__file__).parent / "conformance" / f"{application}.patch").read_text()
    section = patch.split("diff --git a/tests/runtime_server.py b/tests/runtime_server.py\n", 1)[1]
    section = section.split("\ndiff --git ", 1)[0]
    source = "\n".join(line[1:] for line in section.splitlines()
                       if line.startswith("+") and not line.startswith("+++")) + "\n"
    server = tmp_path / "tests" / "runtime_server.py"
    server.parent.mkdir()
    server.write_text(source)
    modules = tmp_path / "src" / "wawesomechat"
    modules.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BROWSER_LOG_RATE_LIMIT_ATTEMPTS", "137")
    # The example server copies the app's explicit config. Ambient variables
    # must be applied deliberately, without changing the application defaults.
    baseline = PytinctureConfig(modules_path=str(modules))
    monkeypatch.setitem(sys.modules, "run", SimpleNamespace(app=create_app(baseline)))
    monkeypatch.setattr(sys, "path", list(sys.path))
    # The fixture sets environment variables itself; restore them after execution.
    import os
    monkeypatch.setattr(os, "environ", os.environ.copy())
    namespace = runpy.run_path(str(server))
    configured = namespace["app"].state.pytincture_config
    assert configured.browser_log_rate_limit_attempts == 137
    assert baseline.browser_log_rate_limit_attempts == 60
    assert PytinctureConfig(modules_path=str(modules)).browser_log_rate_limit_attempts == 60
