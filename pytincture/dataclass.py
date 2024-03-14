import re
import ast

def backend_for_frontend(cls):
    class BackendForFrontendWrapper:
        def __init__(self, *args, **kwargs):
            self._real_instance = cls(*args, **kwargs)

        def __getattr__(self, item):
            return getattr(self._real_instance, item)

    return BackendForFrontendWrapper

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
                    imports_used.add(f"import {subnode.id}")
                elif isinstance(subnode, ast.Attribute) and isinstance(subnode.value, ast.Name):
                    full_name = f"import {subnode.attr}"
                    if full_name in imports:
                        imports_used.add(full_name)

    return import_lines, imports_used

def generate_stub_classes(file_path, return_url, return_protocol):
    with open(file_path, 'r') as file:
        code = file.read()
    
    file_name = file_path.split("/")[-1]

    if not "@backend_for_frontend" in code:
        return code

    module = ast.parse(code)
    class_nodes = [node for node in module.body if isinstance(node, ast.ClassDef)]

    stub_class_code = ""
    class_imports = set()
    all_imports = set()
    used_imports = set()
    for class_node in class_nodes:
        class_name = class_node.name
        all_imports, _ = get_imports_used_in_class(file_path, class_name)

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
            stub_class_code += f"        return StringIO(req.response).getvalue()\n"            
            for node in class_node.body:
                if isinstance(node, ast.FunctionDef) and not node.name.startswith('_'):
                    stub_class_code += f"    def {node.name}(self, *args, **kwargs):\n"
                    stub_class_code += f"        url = '{return_protocol}://{return_url}/classcall/{file_name}/{class_name}/{node.name}'\n"
                    stub_class_code +=  "        payload = {'args': args, 'kwargs': kwargs}\n"
                    stub_class_code +=  "        response = self.fetch(url, payload, 'POST')\n"
                    stub_class_code +=  "        return json.loads(response)\n"
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            property_name = target.id
                            stub_class_code +=  "    @property\n"
                            stub_class_code += f"    def {property_name}(self):\n"
                            stub_class_code += f"        url = '{return_protocol}://{return_url}/classcall/{file_name}/{class_name}/{property_name}'\n"
                            stub_class_code +=  "        response = self.fetch(url)\n"
                            stub_class_code +=  "        return json.loads(response)\n"
        else:
            # If the class is not decorated with 'backend_for_frontend', simply write the original class code
            stub_class_code += "\n"+ast.unparse(class_node)

    all_imports.add("import json")
    all_imports.add("from js import XMLHttpRequest, JSON")
    all_imports.add("from io import StringIO")
    # Write imports
    for imp in all_imports:
        if not imp in class_imports and not "backend_for_frontend" in imp:
            stub_class_code = f"{imp}\n" + stub_class_code

    return stub_class_code

def get_parsed_output(file_path, return_url, return_protocol="http"):
    stub_code = generate_stub_classes(file_path, return_url, return_protocol)
    if stub_code:
        return stub_code
