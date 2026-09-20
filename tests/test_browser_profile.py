"""Profile checks must preserve CPython semantics and expose MicroPython changes."""
from dataclasses import FrozenInstanceError

import pytest

from pytincture.browser_profile import import_source, inspect_source
from pytincture.browser_compatibility import adapt


def test_cpython_source_retains_enforced_frozen_dataclass_semantics():
    source = 'from dataclasses import dataclass\n@dataclass(frozen=True)\nclass Row:\n value: int\nif __name__ == "__main__":\n import desktop_only\nelse:\n row = Row(3)\n'
    converted = import_source(source)
    namespace = {'__name__':'browser_client'}
    exec(converted, namespace)
    with pytest.raises(FrozenInstanceError):
        namespace['row'].value = 4
    assert 'desktop_only' not in converted


def test_profile_checks_resolve_aliases_and_report_runtime_specific_limits():
    source = 'import time as clock\nimport importlib as imports\nclock.perf_counter()\nimports.import_module("plugin")\n'
    pyodide = inspect_source('app.py',source,'pyodide',explicit_dynamic=['plugin'])
    micro = inspect_source('app.py',source,'micropython',explicit_dynamic=['plugin'])
    assert not pyodide
    assert any(f['rule']=='runtime-api' and 'perf_counter' in f['message'] for f in micro)
    assert any(f['rule']=='dynamic-import' for f in inspect_source('app.py',source,'pyodide'))


def test_alias_scheduler_adaptation_is_visible_in_the_build_report():
    findings = []
    result = adapt('import asyncio as aio\nfrom asyncio import get_running_loop as loop\na = aio.get_running_loop()\nb = loop()\n',report=findings,filename='app.py')
    assert 'a = browser_event_loop()' in result
    assert 'browser_event_loop as loop' in result
    assert any(f['rule']=='adapt-Call' and f['behavior_changing'] for f in findings)
    assert all(f['file']=='app.py' for f in findings)


def test_micropython_rejects_iterable_display_unpacking_but_allows_assignment():
    source = 'values=[0,*items]\nfirst,*rest=items\n'
    assert not inspect_source('app.py',source,'pyodide')
    findings = inspect_source('app.py',source,'micropython')
    assert len(findings)==1 and findings[0]['rule']=='runtime-syntax'
