"""Static BFF documentation schemas; never import or evaluate application code."""

import ast
from copy import deepcopy


class AnnotationSchemas:
    """Resolve common JSON annotations and module-local TypedDict declarations."""

    def __init__(self, module: ast.Module):
        self.classes = {node.name: node for node in module.body if isinstance(node, ast.ClassDef)}
        self.aliases = {}
        for node in module.body:
            if isinstance(node, ast.ImportFrom) and node.module in {"typing", "typing_extensions"}:
                self.aliases.update({alias.asname or alias.name: alias.name for alias in node.names})

    def name(self, node):
        name = ast.unparse(node).removeprefix("typing.").removeprefix("typing_extensions.")
        return self.aliases.get(name, name)

    def schema(self, annotation, depth=0):
        if depth == 0:
            self.remaining = 512
        self.remaining -= 1
        if self.remaining < 0:
            return {}
        if annotation is None or depth > 8:
            return {}
        if isinstance(annotation, str):
            try:
                annotation = ast.parse(annotation, mode="eval").body
            except (SyntaxError, ValueError, RecursionError):
                return {}
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            return self.schema(annotation.value, depth + 1)
        if isinstance(annotation, ast.Call):
            return {}
        if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            return {"anyOf": [self.schema(annotation.left, depth + 1), self.schema(annotation.right, depth + 1)]}
        name = self.name(annotation)
        primitives = {"str": "string", "int": "integer", "float": "number", "bool": "boolean", "None": "null", "NoneType": "null"}
        if name in primitives:
            return {"type": primitives[name]}
        if name in {"Any", "object"}:
            return {}
        if name in {"dict", "Dict", "Mapping"}:
            return {"type": "object"}
        if name in {"list", "List", "Sequence", "tuple", "Tuple", "set", "Set"}:
            return {"type": "array", "items": {}}
        if isinstance(annotation, ast.Subscript):
            base = self.name(annotation.value)
            arguments = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
            if base in {"Annotated", "Required", "NotRequired"}:
                return self.schema(arguments[0], depth + 1)
            if base in {"Union", "Optional"}:
                schemas = [self.schema(arg, depth + 1) for arg in arguments]
                if base == "Optional":
                    schemas.append({"type": "null"})
                return {"anyOf": schemas}
            if base == "Literal":
                values = [arg.value for arg in arguments if isinstance(arg, ast.Constant) and type(arg.value) in {str, int, float, bool, type(None)}]
                return {"enum": values} if len(values) == len(arguments) else {}
            if base in {"list", "List", "Sequence", "set", "Set"}:
                return {"type": "array", "items": self.schema(arguments[0], depth + 1)}
            if base in {"dict", "Dict", "Mapping"} and len(arguments) == 2:
                return {"type": "object", "additionalProperties": self.schema(arguments[1], depth + 1)}
            if base in {"tuple", "Tuple"}:
                if len(arguments) == 2 and isinstance(arguments[1], ast.Constant) and arguments[1].value is Ellipsis:
                    return {"type": "array", "items": self.schema(arguments[0], depth + 1)}
                return {"type": "array", "prefixItems": [self.schema(arg, depth + 1) for arg in arguments], "minItems": len(arguments), "maxItems": len(arguments)}
        node = self.classes.get(name)
        if node is not None:
            bases = [self.name(base) for base in node.bases]
            parents = [self.schema(base, depth + 1) for base in node.bases if self.name(base) in self.classes]
            if "TypedDict" in bases or any(parent.get("type") == "object" for parent in parents):
                properties = {}
                required = []
                for parent in parents:
                    properties.update(parent.get("properties", {}))
                    required.extend(parent.get("required", []))
                total = not any(keyword.arg == "total" and isinstance(keyword.value, ast.Constant) and keyword.value.value is False for keyword in node.keywords)
                for field in node.body:
                    if not isinstance(field, ast.AnnAssign) or not isinstance(field.target, ast.Name):
                        continue
                    field_name = field.target.id
                    properties[field_name] = self.schema(field.annotation, depth + 1)
                    wrapper = self.name(field.annotation.value) if isinstance(field.annotation, ast.Subscript) else ""
                    if wrapper == "Required" or (total and wrapper != "NotRequired"):
                        required.append(field_name)
                schema = {"title": name, "type": "object", "properties": properties}
                if required:
                    schema["required"] = sorted(set(required))
                return schema
        return {"description": f"Python type: {name}. Its JSON shape is not declared here."}


def example_value(schema):
    """Illustrative, type-based values, never application defaults or live data."""
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        return example_value(schema["anyOf"][0])
    kind = schema.get("type")
    if kind == "object":
        return {name: example_value(value) for name, value in schema.get("properties", {}).items()}
    if kind == "array":
        if "prefixItems" in schema:
            return [example_value(item) for item in schema["prefixItems"]]
        return [example_value(schema["items"])] if schema.get("items") else []
    return {"string": "string", "integer": 1, "number": 1.0, "boolean": True, "null": None}.get(kind)


def operation_spec(module_path, class_name, method_name, operation):
    parameters = operation.get("parameters", ())
    var_positional = next((p for p in parameters if p["kind"] == "var_positional"), None)
    var_keyword = next((p for p in parameters if p["kind"] == "var_keyword"), None)
    types = AnnotationSchemas(ast.Module(body=[], type_ignores=[]))

    def parameter_schema(parameter):
        schema = deepcopy(parameter.get("schema", types.schema(parameter.get("annotation", "Any"))))
        schema["title"] = parameter["name"]
        status = "Required" if parameter.get("required") else "Optional; the server supplies its default when omitted"
        schema["description"] = f"{status}. Python type: {parameter.get('annotation', 'Any')}."
        return schema

    properties = {p["name"]: parameter_schema(p) for p in parameters if p["kind"] not in {"var_positional", "var_keyword"}}
    if var_positional:
        properties[var_positional["name"]] = {"type": "array", "items": parameter_schema(var_positional)}
    body_schema = {
        "type": "object", "properties": properties,
        "additionalProperties": parameter_schema(var_keyword) if var_keyword else False,
    }
    required = [p["name"] for p in parameters if p.get("required")]
    if required:
        body_schema["required"] = required
    example = {name: example_value(schema) for name, schema in properties.items()}
    signature_parts = []
    keyword_marker = var_positional is not None
    for index, parameter in enumerate(parameters):
        if parameter["kind"] == "keyword_only" and not keyword_marker:
            signature_parts.append("*")
            keyword_marker = True
        prefix = {"var_positional": "*", "var_keyword": "**"}.get(parameter["kind"], "")
        part = f"{prefix}{parameter['name']}: {parameter.get('annotation', 'Any')}"
        if not parameter.get("required") and not prefix:
            part += " = <default>"
        signature_parts.append(part)
        if parameter["kind"] == "positional_only" and (
            index + 1 == len(parameters) or parameters[index + 1]["kind"] != "positional_only"
        ):
            signature_parts.append("/")
    signature = f"{class_name}.{method_name}({', '.join(signature_parts)}) -> {operation.get('return_annotation', 'Any')}"
    argument_table = "| Argument | Python type | Required | Passed as |\n| --- | --- | --- | --- |\n"
    for parameter in parameters:
        annotation = parameter.get("annotation", "Any").replace("|", "\\|")
        argument_table += f"| `{parameter['name']}` | `{annotation}` | {'Yes' if parameter.get('required') else 'No'} | {parameter['kind'].replace('_', ' ')} |\n"
    if not parameters:
        argument_table = "This method takes no arguments."
    docstring = operation.get("docstring", "")
    response_schema = deepcopy(operation.get("return_schema", {}))
    stream = operation.get("stream", {})
    media_type = stream.get("media_type", "text/event-stream") if stream.get("enabled") else "application/json"
    if stream.get("enabled"):
        response_schema = {"type": "string", "description": "Streamed response; Execute shows the bytes returned by the server."}
    media = {"schema": response_schema}
    if response_schema and not stream.get("enabled"):
        media["examples"] = {"shape": {"summary": "Illustrative response from declared types (not live data)", "value": example_value(response_schema)}}
    result = {
        "summary": docstring.splitlines()[0] if docstring else f"Call {method_name} on {class_name}",
        "description": f"{docstring}\n\n```python\n{signature}\n```\n\n{argument_table}\n\nSend the function arguments directly as JSON fields. Examples are illustrative; Execute sends a real request using your current session and displays the actual response. Omit optional fields to use server defaults; default values are not published.",
        "operationId": f"call_{class_name}_{method_name}"[:50], "tags": [module_path],
        "parameters": [{"name": "application", "in": "path", "required": True, "schema": {"type": "string"}}],
        "requestBody": {"required": True, "content": {"application/json": {"schema": body_schema, "examples": {"named": {"summary": "Named arguments (illustrative values)", "value": example}}}}},
        "responses": {"200": {"description": "Successful response" if response_schema else "Return type is not annotated. Execute displays the actual response.", "content": {media_type: media}}},
        "x-bff-http-methods": list(operation.get("http_methods", ("POST",))),
        "x-bff-external": bool(operation.get("external")),
        "x-bff-include-session-methods-in-docs": operation.get("include_session_methods_in_docs"),
    }
    return result
