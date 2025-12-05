import ast
from decimal import Subnormal
import os
import sys
from typing import Set
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.openapi.docs import get_swagger_ui_html
import inspect
from typing import Dict, Any
from pytincture import get_modules_path

# Global set to track BFF endpoints
bff_routes: Dict[str, Dict] = {}


def _module_relative_identifier(file_path: str) -> str:
    """
    Return a stable, POSIX-style path (including `.py`) for a module relative to MODULES_PATH.
    Falls back to the file basename when the module is outside the configured folder.
    """
    modules_root = os.path.abspath(get_modules_path() or os.getcwd())
    absolute_file = os.path.abspath(file_path)

    try:
        rel_path = os.path.relpath(absolute_file, modules_root)
    except ValueError:
        rel_path = os.path.basename(absolute_file)

    if rel_path.startswith(".."):
        rel_path = os.path.basename(absolute_file)

    rel_path = rel_path.replace("\\", "/").lstrip("./")
    if not rel_path:
        rel_path = os.path.basename(absolute_file)

    if not rel_path.lower().endswith(".py"):
        rel_path += ".py"

    return rel_path


def bff_stream(func=None, *, raw: bool = False, media_type: str = "text/event-stream"):
    """
    Mark a backend_for_frontend method as streaming.

    Args:
        raw: When False (default), streamed Python values will be JSON-encoded and newline-delimited.
             When True, values are forwarded as-is (strings/bytes recommended).
        media_type: Content type to advertise for the stream response.
    """

    def _apply(target):
        setattr(target, "_bff_streaming", True)
        setattr(target, "_bff_streaming_raw", raw)
        setattr(target, "_bff_streaming_media_type", media_type)
        return target

    if func is None:
        return _apply
    return _apply(func)


def bff_policy(**metadata):
    """
    Attach arbitrary policy metadata to a backend_for_frontend method.
    The metadata is later surfaced to the server-side policy hook so applications
    can run custom authorization/validation logic per call.
    """

    def _apply(target):
        existing = getattr(target, "_bff_policy", {})
        combined = {**existing, **metadata}
        setattr(target, "_bff_policy", combined)
        return target

    return _apply

def get_method_info_from_node(class_node: ast.ClassDef) -> Dict[str, Any]:
    """Extract method information from a class AST node"""
    methods_info = {}
    
    for node in class_node.body:
        if isinstance(node, ast.FunctionDef):
            if not node.name.startswith('_'):  # Skip private methods
                params = []
                for arg in node.args.args:
                    if arg.arg != 'self':  # Skip 'self' parameter
                        param_info = {
                            'name': arg.arg,
                            'type': 'any'  # Default type
                        }
                        # Try to get type annotation if it exists
                        if arg.annotation:
                            if isinstance(arg.annotation, ast.Name):
                                param_info['type'] = arg.annotation.id
                            elif isinstance(arg.annotation, ast.Constant):
                                param_info['type'] = arg.annotation.value
                            
                        params.append(param_info)
                
                methods_info[node.name] = {
                    'parameters': params,
                    'docstring': ast.get_docstring(node)[0:30] or f"Call {node.name[0:30]}",
                    'return_type': 'any'  # Default return type
                }
                
                # Try to get return type annotation if it exists
                if node.returns:
                    if isinstance(node.returns, ast.Name):
                        methods_info[node.name]['return_type'] = node.returns.id
                    elif isinstance(node.returns, ast.Constant):
                        methods_info[node.name]['return_type'] = node.returns.value
    
    return methods_info

def backend_for_frontend(cls):
    """
    A decorator that wraps `cls` in a proxy/wrapper class and generates OpenAPI specs.
    """
    print(f"Registering BFF class: {cls.__name__}")

    # Get module/file name consistently
    module_name = cls.__module__.split('.')[-1]

    # Compute the relative module identifier for routing (includes folders + .py)
    module_file = inspect.getfile(cls)
    module_identifier = _module_relative_identifier(module_file)

    # Register all methods
    for method_name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
        if not method_name.startswith('_'):
            route_path = f"/classcall/{module_identifier}/{cls.__name__}/{method_name}"

            # Get method signature
            sig = inspect.signature(method)
            streaming_enabled = getattr(method, "_bff_streaming", False)
            streaming_raw = getattr(method, "_bff_streaming_raw", False)
            streaming_media_type = getattr(method, "_bff_streaming_media_type", "text/event-stream")

            # Create list of parameters in order (excluding self)
            param_list = [
                {
                    'name': name,
                    'type': str(param.annotation) if param.annotation != inspect.Parameter.empty else 'str',
                    'required': param.default == inspect.Parameter.empty
                }
                for name, param in sig.parameters.items()
                if name != 'self'
            ]

            # Create OpenAPI operation spec
            operation_id_full = f"call_{cls.__name__}_{method_name}"
            operation_id = operation_id_full[:50] if len(operation_id_full) > 50 else operation_id_full  # Truncate to ensure <64 chars
            responses_spec = {
                '200': {
                    'description': 'Streaming response' if streaming_enabled else 'Successful response',
                    'content': {
                        (streaming_media_type if streaming_enabled else 'application/json'): {
                            'schema': {
                                'type': 'string' if streaming_enabled else 'object'
                            }
                        }
                    }
                }
            }

            operation_spec = {
                'summary': method.__doc__ or f"Call {method_name} on {cls.__name__}",
                'operationId': operation_id,  # Useful, unique, short, and now truncated if needed
                'tags': [module_name],
                'parameters': [],
                'requestBody': {
                    'content': {
                        'application/json': {
                            'schema': {
                                'type': 'object',
                                'properties': {
                                    'args': {
                                        'type': 'array',
                                        'items': {
                                            'type': 'object',
                                            'properties': {
                                                'name': {'type': 'string'},
                                                'type': {'type': 'string'},
                                                'value': {'type': 'string'}
                                            }
                                        },
                                        'description': 'Positional arguments in order: ' + 
                                            ', '.join([f"{p['name']}: {p['type']}" for p in param_list])
                                    },
                                    'kwargs': {
                                        'type': 'object',
                                        'properties': {
                                            param.name: {
                                                'type': 'string',
                                                'description': f"Type: {str(param.annotation) if param.annotation != inspect.Parameter.empty else 'str'}"
                                                + (f", Default: {param.default}" if param.default != inspect.Parameter.empty else "")
                                            }
                                            for param in sig.parameters.values()
                                            if param.name != 'self'
                                        }
                                    }
                                }
                            }
                        }
                    }
                },
                'responses': responses_spec
            }

            if streaming_enabled:
                operation_spec['x-bff-streaming'] = True
                operation_spec['x-bff-streaming-raw'] = streaming_raw
                operation_spec['x-bff-streaming-media-type'] = streaming_media_type
            
            # Add example if we have parameters
            if param_list:
                operation_spec['requestBody']['content']['application/json']['examples'] = {
                    'args_example': {
                        'value': {
                            'args': [
                                {
                                    'name': p['name'],
                                    'type': p['type'],
                                    'value': 'example_value'
                                }
                                for p in param_list
                            ],
                            'kwargs': {}
                        }
                    },
                    'kwargs_example': {
                        'value': {
                            'args': [],
                            'kwargs': {
                                p['name']: 'example_value'
                                for p in param_list
                            }
                        }
                    }
                }
            
            bff_routes[route_path] = operation_spec

    class BackendForFrontendWrapper:
        def __init__(self, *args, **kwargs):
            self._user = kwargs.pop('_user', None)
            self._real_instance = cls(*args, **kwargs)
            if self._user is not None:
                setattr(self._real_instance, '_user', self._user)

        def __getattr__(self, item):
            return getattr(self._real_instance, item)

    return BackendForFrontendWrapper
    
def add_bff_docs_to_app(app: FastAPI):
    """
    Adds BFF-specific OpenAPI documentation to a FastAPI application
    """
    # Get configuration from environment variables or use defaults
    docs_path = os.getenv("BFF_DOCS_PATH", "bff-docs")
    docs_title = os.getenv("BFF_DOCS_TITLE", "pyTincture BFF API")
    
    # Ensure docs_path starts with /
    docs_path = f"/{docs_path.lstrip('/')}"
    openapi_path = f"{docs_path}/openapi.json"

    def custom_openapi():
        if not app.openapi_schema:
            openapi_schema = get_openapi(
                title=app.title,
                version=app.version or "1.0.0",
                description=app.description or "pyTincture API with Backend for Frontend specification",
                routes=app.routes
            )
            
            # Merge BFF paths into existing paths
            paths = openapi_schema.get("paths", {}).copy()
            # Collect unique tags from BFF
            new_tags = set()
            for route_path, operation_spec in bff_routes.items():
                if route_path not in paths:
                    paths[route_path] = {}
                paths[route_path]['post'] = operation_spec
                if 'tags' in operation_spec:
                    new_tags.update(operation_spec['tags'])
            
            openapi_schema["paths"] = paths
            
            # Add components section if needed
            if 'components' not in openapi_schema:
                openapi_schema['components'] = {}
            
            # Add schemas section if needed
            if 'schemas' not in openapi_schema['components']:
                openapi_schema['components']['schemas'] = {}
            
            # Merge tags: get existing tag names
            existing_tags = openapi_schema.get('tags', [])
            existing_tag_names = set(tag['name'] for tag in existing_tags)
            
            # Add new BFF tags if not already present
            for tag in sorted(new_tags - existing_tag_names):
                existing_tags.append({
                    'name': tag,
                    'description': f'Endpoints from {tag}'
                })
            openapi_schema['tags'] = existing_tags
            
            app.openapi_schema = openapi_schema
        
        return app.openapi_schema

    @app.get(openapi_path, tags=["documentation"])
    async def get_bff_openapi():
        """
        Get the OpenAPI schema for BFF endpoints
        """
        return custom_openapi()

    @app.get(docs_path, tags=["documentation"])
    async def get_bff_docs():
        """
        Get the Swagger UI HTML for BFF endpoints
        """
        return get_swagger_ui_html(
            openapi_url=openapi_path,
            title=docs_title,
            swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
            swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"
        )

    # Set the custom OpenAPI function
    app.openapi = custom_openapi

def get_imports_used_in_class(file_path, class_name):
    with open(file_path, 'r') as file:
        tree = ast.parse(file.read())

    imports = set()
    imports_used = set()
    import_lines = set()

    # Collect imports in the file
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
                import_lines.add(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module if node.module else ''
            for alias in node.names:
                imported_name = alias.asname if alias.asname else alias.name
                imports.add(imported_name)
                import_lines.add(f"from {module} import {imported_name}")

    # Find imports used in the specified class
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for subnode in ast.walk(node):
                if isinstance(subnode, ast.Name) and subnode.id in imports:
                    imports_used.add(subnode.id)

    return import_lines, imports_used

def generate_stub_classes(file_path, return_url, return_protocol):
    with open(file_path, 'r') as file:
        code = file.read()
    
    file_identifier = _module_relative_identifier(file_path)

    if not "@backend_for_frontend" in code:
        return code

    module = ast.parse(code)
    class_nodes = [node for node in module.body if isinstance(node, ast.ClassDef)]

    stub_class_code = ""
    class_imports = set()
    all_imports = set()
    used_imports = set()
    def _is_bff_stream_decorator(decorator: ast.AST):
        def _matches(node):
            if isinstance(node, ast.Name):
                return node.id == 'bff_stream'
            if isinstance(node, ast.Attribute):
                return node.attr == 'bff_stream'
            return False

        if isinstance(decorator, ast.Call):
            return _matches(decorator.func), decorator
        return _matches(decorator), decorator

    def _extract_stream_config(decorator_call):
        config = {
            "raw": False,
            "media_type": "text/event-stream"
        }
        if not isinstance(decorator_call, ast.Call):
            return config
        for keyword in decorator_call.keywords:
            if keyword.arg == "raw" and isinstance(keyword.value, ast.Constant):
                config["raw"] = bool(keyword.value.value)
            if keyword.arg == "media_type" and isinstance(keyword.value, ast.Constant):
                config["media_type"] = str(keyword.value.value)
        return config

    for class_node in class_nodes:
        class_name = class_node.name

        if any(isinstance(decorator, ast.Name) and decorator.id == 'backend_for_frontend' for decorator in class_node.decorator_list):
            _, used_imports = get_imports_used_in_class(file_path, class_name)
            class_imports.update(used_imports)
            stub_class_code += f"\nclass {class_name}:\n"
            stub_class_code += f"    def fetch(self, url, payload=None, method='GET'):\n"
            stub_class_code += f"        req = XMLHttpRequest.new()\n"
            stub_class_code += f"        req.open(method, url, False)\n"
            stub_class_code += f"        req.setRequestHeader('Content-Type', 'application/json')\n"
            stub_class_code += f"        if payload:\n"
            stub_class_code += f"            req.send(JSON.stringify(json.dumps(payload)))\n"
            stub_class_code += f"        else:\n"
            stub_class_code += f"            req.send()\n"
            stub_class_code += f"        if req.status == 401:\n"
            stub_class_code += f"            from js import window\n"
            stub_class_code += f"            current_url = window.location.href.rstrip('/')\n"
            stub_class_code += f"            redirect_url = current_url + '/login'\n"
            stub_class_code += f"            window.location.href = redirect_url\n"
            stub_class_code += f"            return ''\n"
            stub_class_code += f"        return StringIO(req.response).getvalue()\n"

            streaming_methods = {}
            for node in class_node.body:
                if isinstance(node, ast.FunctionDef):
                    for decorator in node.decorator_list:
                        matches, decorator_node = _is_bff_stream_decorator(decorator)
                        if matches:
                            streaming_methods[node.name] = _extract_stream_config(decorator_node)
                            break

            if streaming_methods:
                stub_class_code += f"    async def fetch_stream(self, url, payload=None, method='GET'):\n"
                stub_class_code += f"        from js import fetch, TextDecoder\n"
                stub_class_code += f"        from pyodide.ffi import to_js\n"
                stub_class_code += f"        options = {{'method': method, 'headers': {{'Content-Type': 'application/json'}}}}\n"
                stub_class_code += f"        body_payload = payload if payload is not None else {{'args': [], 'kwargs': {{}}}}\n"
                stub_class_code += f"        options['body'] = JSON.stringify(json.dumps(body_payload))\n"
                stub_class_code += f"        response = await fetch(url, to_js(options))\n"
                stub_class_code += f"        if response.status == 401:\n"
                stub_class_code += f"            from js import window\n"
                stub_class_code += f"            current_url = window.location.href.rstrip('/')\n"
                stub_class_code += f"            redirect_url = current_url + '/login'\n"
                stub_class_code += f"            window.location.href = redirect_url\n"
                stub_class_code += f"            return\n"
                stub_class_code += f"        reader = response.body.getReader()\n"
                stub_class_code += f"        decoder = TextDecoder.new()\n"
                stub_class_code += f"        while True:\n"
                stub_class_code += f"            chunk = await reader.read()\n"
                stub_class_code += f"            if chunk.done:\n"
                stub_class_code += f"                break\n"
                stub_class_code += f"            text = decoder.decode(chunk.value, to_js({{'stream': True}}))\n"
                stub_class_code += f"            if text:\n"
                stub_class_code += f"                yield text\n"
                stub_class_code += f"        final_text = decoder.decode()\n"
                stub_class_code += f"        if final_text:\n"
                stub_class_code += f"            yield final_text\n"

            for node in class_node.body:
                if isinstance(node, ast.FunctionDef) and not node.name.startswith('_'):
                    is_streaming = node.name in streaming_methods
                    stream_config = streaming_methods.get(node.name, {"raw": False})
                    if is_streaming:
                        stub_class_code += f"    async def {node.name}(self, *args, **kwargs):\n"
                        stub_class_code += f"        url = '{return_protocol}://{return_url}/classcall/{file_identifier}/{class_name}/{node.name}'\n"
                        stub_class_code +=  "        payload = {'args': args, 'kwargs': kwargs}\n"
                        stub_class_code +=  "        stream_iter = self.fetch_stream(url, payload, 'POST')\n"
                        if stream_config.get("raw"):
                            stub_class_code +=  "        async for chunk in stream_iter:\n"
                            stub_class_code +=  "            if chunk:\n"
                            stub_class_code +=  "                yield chunk\n"
                        else:
                            stub_class_code +=  "        buffer = ''\n"
                            stub_class_code +=  "        async for chunk in stream_iter:\n"
                            stub_class_code +=  "            if not chunk:\n"
                            stub_class_code +=  "                continue\n"
                            stub_class_code +=  "            buffer += chunk\n"
                            stub_class_code +=  "            while '\\n' in buffer:\n"
                            stub_class_code +=  "                line, buffer = buffer.split('\\n', 1)\n"
                            stub_class_code +=  "                line = line.strip()\n"
                            stub_class_code +=  "                if not line:\n"
                            stub_class_code +=  "                    continue\n"
                            stub_class_code +=  "                yield json.loads(line)\n"
                            stub_class_code +=  "        if buffer.strip():\n"
                            stub_class_code +=  "            yield json.loads(buffer)\n"
                    else:
                        stub_class_code += f"    def {node.name}(self, *args, **kwargs):\n"
                        stub_class_code += f"        url = '{return_protocol}://{return_url}/classcall/{file_identifier}/{class_name}/{node.name}'\n"
                        stub_class_code +=  "        payload = {'args': args, 'kwargs': kwargs}\n"
                        stub_class_code +=  "        response = self.fetch(url, payload, 'POST')\n"
                        stub_class_code +=  "        return json.loads(response)\n"
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            property_name = target.id
                            stub_class_code +=  "    @property\n"
                            stub_class_code += f"    def {property_name}(self):\n"
                            stub_class_code += f"        url = '{return_protocol}://{return_url}/classcall/{file_identifier}/{class_name}/{property_name}'\n"
                            stub_class_code +=  "        response = self.fetch(url)\n"
                            stub_class_code +=  "        return json.loads(response)\n"
        else:
            stub_class_code += "\n"+ast.unparse(class_node)

    all_imports.add("import json")
    all_imports.add("from js import XMLHttpRequest, JSON")
    all_imports.add("from io import StringIO")
    for imp in all_imports:
        stub_class_code = f"{imp}\n" + stub_class_code

    return stub_class_code

def get_parsed_output(file_path, return_url, return_protocol="http"):
    stub_code = generate_stub_classes(file_path, return_url, return_protocol)
    if stub_code:
        return stub_code

if __name__ == "__main__":
    import sys
    print(generate_stub_classes(sys.argv[1],"test","http"))
