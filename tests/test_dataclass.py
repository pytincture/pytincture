import ast
import os
import textwrap
import pytest
from os import sep
from pathlib import Path

# Import functions to test from dataclass.py
from pytincture.dataclass import (
    backend_for_frontend,
    get_imports_used_in_class,
    generate_stub_classes,
    get_parsed_output
)

# --------------------------------------------
# Tests for backend_for_frontend decorator
# --------------------------------------------

def test_backend_for_frontend_decorator():
    """Test that the decorator wraps a class and captures _user."""
    
    @backend_for_frontend
    class Dummy:
        def __init__(self, x):
            self.x = x
        def get_x(self):
            return self.x

    # Create an instance with a _user parameter.
    instance = Dummy(10, _user="tester")
    # The wrapper should have stored _user in the wrapper instance
    # and proxied attribute access to the real instance.
    assert hasattr(instance, "_user")
    assert instance._user == "tester"
    # Access the underlying method via proxy.
    assert instance.get_x() == 10
    # Also, the real instance should have _user set.
    assert hasattr(instance._real_instance, "_user")
    assert instance._real_instance._user == "tester"

# --------------------------------------------
# Tests for get_imports_used_in_class
# --------------------------------------------

def test_get_imports_used_in_class(tmp_path):
    """
    Create a temporary Python file with some import statements and a dummy class.
    Verify that get_imports_used_in_class returns the correct import lines and
    the set of imports used in the class.
    """
    code = textwrap.dedent("""
        import os
        import sys
        from math import sqrt, pi
        from collections import defaultdict
        
        class MyClass:
            def method(self):
                print(os.getcwd())
                x = sqrt(4)
    """)
    file_path = tmp_path / "dummy.py"
    file_path.write_text(code)
    
    import_lines, imports_used = get_imports_used_in_class(str(file_path), "MyClass")
    # Expect import_lines to include these four lines (order might differ)
    expected_import_lines = {
        "import os",
        "import sys",
        "from math import sqrt",
        "from math import pi",
        "from collections import defaultdict"
    }
    # We allow extra spaces or order variations.
    for line in expected_import_lines:
        assert any(line in imp for imp in import_lines), f"Missing import line: {line}"
    
    # In MyClass, "os" and "sqrt" are used.
    assert "os" in imports_used
    assert "sqrt" in imports_used
    # "sys", "pi", and "defaultdict" are not used in the class body.
    assert "sys" not in imports_used
    assert "pi" not in imports_used
    assert "defaultdict" not in imports_used

# --------------------------------------------
# Tests for generate_stub_classes and get_parsed_output
# --------------------------------------------

def test_generate_stub_classes_returns_stub(tmp_path):
    """
    Create a dummy file that contains a class decorated with @backend_for_frontend.
    Verify that generate_stub_classes returns a stub with the fetch method and generated URL.
    """
    # Note: The marker '@backend_for_frontend' must appear in the code.
    dummy_code = textwrap.dedent("""
        @backend_for_frontend
        class MyService:
            def foo(self, a):
                return a * 2
    """)
    file_path = tmp_path / "service.py"
    file_path.write_text(dummy_code)
    
    # Call generate_stub_classes with dummy return values.
    stub = generate_stub_classes(str(file_path), "example.com", "https")
    # We expect the stub to contain a class definition for MyService, a fetch method, and a generated URL.
    assert "class MyService:" in stub
    assert "def fetch(self, url, payload=None, method='GET'):" in stub
    expected_url = f"https://example.com/classcall/service.py/MyService/foo"
    assert expected_url in stub
    # Also check that required imports are added.
    assert "import json" in stub
    assert "from js import XMLHttpRequest, JSON" in stub
    assert "from io import StringIO" in stub

def test_get_parsed_output_returns_stub(tmp_path):
    """
    When the file contains '@backend_for_frontend', get_parsed_output should return stub code.
    Otherwise, it should return None (or the original code if no marker is found).
    """
    # Case 1: File contains the marker.
    dummy_code_with_marker = textwrap.dedent("""
        @backend_for_frontend
        class MyService:
            def foo(self, a):
                return a * 2
    """)
    file_path = tmp_path / "service_with_marker.py"
    file_path.write_text(dummy_code_with_marker)
    
    parsed_output = get_parsed_output(str(file_path), "example.com", "https")
    assert parsed_output is not None
    assert "class MyService:" in parsed_output
    assert "def fetch(" in parsed_output
    
    # Case 2: File does NOT contain the marker.
    dummy_code_without_marker = textwrap.dedent("""
        class PlainService:
            def foo(self, a):
                return a * 3
    """)
    file_path2 = tmp_path / "service_without_marker.py"
    file_path2.write_text(dummy_code_without_marker)
    parsed_output2 = get_parsed_output(str(file_path2), "example.com", "https")
    # In this case, our function returns the original code.
    # (Your code returns stub_code only if stub_code is truthy.)
    assert parsed_output2 is not None
    assert "class PlainService:" in parsed_output2
    # It should not contain any fetch method.
    assert "def fetch(" not in parsed_output2
