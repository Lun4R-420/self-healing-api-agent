import json
from urllib.parse import urljoin

import httpx

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

SUPPORTED_LOCATIONS = {"path", "query", "header"}

DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def load_api(source: str) -> dict:
    if is_url(source):
        response = httpx.get(source, timeout=10, follow_redirects=True)
        response.raise_for_status()
        return response.json()

    with open(source, "r", encoding="utf-8") as file:
        return json.load(file)


def get_base_url(spec: dict, source: str) -> str:
    servers = spec.get("servers") or []

    if not servers:
        if is_url(source):
            return urljoin(source, "/")
        return DEFAULT_BASE_URL

    server_url = servers[0]["url"]

    if is_url(server_url):
        return server_url

    if is_url(source):
        return urljoin(source, server_url)

    return DEFAULT_BASE_URL + server_url


def resolve_refs(schema, spec: dict, seen: frozenset = frozenset()):
    if isinstance(schema, list):
        return [resolve_refs(item, spec, seen) for item in schema]

    if not isinstance(schema, dict):
        return schema

    if "$ref" in schema:
        ref = schema["$ref"]

        if ref in seen or not ref.startswith("#/"):
            return {}

        target = spec
        for part in ref[2:].split("/"):
            target = target[part]

        return resolve_refs(target, spec, seen | {ref})

    return {
        key: resolve_refs(value, spec, seen)
        for key, value in schema.items()
    }


def schema_type(schema: dict) -> str:
    if "type" in schema:
        return schema["type"] if isinstance(schema["type"], str) else "/".join(schema["type"])

    if "enum" in schema:
        return "enum"

    for key in ("anyOf", "oneOf"):
        if key in schema:
            return "/".join(schema_type(option) for option in schema[key])

    return "unknown"


def parse_parameter(parameter: dict, spec: dict) -> dict:
    parameter = resolve_refs(parameter, spec)
    schema = parameter.get("schema", {})

    return {
        "name": parameter["name"],
        "location": parameter["in"],
        "required": parameter.get("required", parameter["in"] == "path"),
        "type": schema_type(schema),
        "schema": schema,
        "description": parameter.get("description", ""),
    }


def parse_body(operation: dict, spec: dict) -> dict | None:
    request_body = operation.get("requestBody")

    if request_body is None:
        return None

    request_body = resolve_refs(request_body, spec)
    json_content = request_body.get("content", {}).get("application/json")

    if json_content is None:
        return None

    return {
        "required": request_body.get("required", False),
        "schema": json_content.get("schema", {}),
    }


def get_endpoints(spec: dict) -> list[dict]:
    endpoints = []

    for path, path_item in spec["paths"].items():
        shared_parameters = path_item.get("parameters", [])

        for method, operation in path_item.items():
            if method not in HTTP_METHODS:
                continue

            parameters_by_key = {}

            for raw in shared_parameters + operation.get("parameters", []):
                parameter = parse_parameter(raw, spec)

                if parameter["location"] in SUPPORTED_LOCATIONS:
                    key = (parameter["name"], parameter["location"])
                    parameters_by_key[key] = parameter

            endpoints.append({
                "method": method.upper(),
                "path": path,
                "summary": operation.get("summary", ""),
                "description": operation.get("description", ""),
                "parameters": list(parameters_by_key.values()),
                "body": parse_body(operation, spec),
            })

    return endpoints


def describe_endpoints(endpoints: list[dict]) -> str:
    compact = []

    for endpoint in endpoints:
        entry = {
            "method": endpoint["method"],
            "path": endpoint["path"],
            "summary": endpoint["summary"],
            "description": endpoint["description"],
            "parameters": [
                {
                    "name": parameter["name"],
                    "in": parameter["location"],
                    "required": parameter["required"],
                    "schema": parameter["schema"],
                }
                for parameter in endpoint["parameters"]
            ],
        }

        if endpoint["body"] is not None:
            entry["body"] = endpoint["body"]

        compact.append(entry)

    return json.dumps(compact, indent=2)
