from dataclasses import dataclass, field, asdict

OK = "Ok"

JSON_TYPES = {
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "string": lambda value: isinstance(value, str),
    "boolean": lambda value: isinstance(value, bool),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
    "null": lambda value: value is None,
}


@dataclass
class APIAction:
    method: str
    path: str
    parameters: dict = field(default_factory=dict)
    body: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def action_from_dict(data) -> APIAction:
    if not isinstance(data, dict):
        raise ValueError("Action must be a JSON object")

    method = data.get("method")
    path = data.get("path")
    parameters = data.get("parameters") or {}
    body = data.get("body")

    if not isinstance(method, str) or not method:
        raise ValueError("Action is missing a string 'method'")

    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("Action is missing a 'path' starting with '/'")

    if not isinstance(parameters, dict):
        raise ValueError("Action 'parameters' must be an object")

    if body is not None and not isinstance(body, dict):
        raise ValueError("Action 'body' must be an object or null")

    return APIAction(
        method=method.upper(),
        path=path,
        parameters=parameters,
        body=body,
    )


def find_endpoint(action: APIAction, endpoints: list[dict]) -> dict | None:
    for endpoint in endpoints:
        if endpoint["method"] == action.method and endpoint["path"] == action.path:
            return endpoint

    return None


def check_value(value, schema: dict, name: str) -> str | None:
    if not schema:
        return None

    for key in ("anyOf", "oneOf"):
        if key in schema:
            for option in schema[key]:
                if check_value(value, option, name) is None:
                    return None
            return f"{name}: value {value!r} does not match any allowed schema"

    if "enum" in schema and value not in schema["enum"]:
        return f"{name}: {value!r} is not one of {schema['enum']}"

    expected = schema.get("type")

    if expected is not None:
        allowed = expected if isinstance(expected, list) else [expected]

        if not any(JSON_TYPES.get(kind, lambda _: True)(value) for kind in allowed):
            return f"{name}: expected {'/'.join(allowed)}, got {type(value).__name__} {value!r}"

    if JSON_TYPES["number"](value):
        if "minimum" in schema and value < schema["minimum"]:
            return f"{name}: must be >= {schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{name}: must be <= {schema['maximum']}"
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            return f"{name}: must be > {schema['exclusiveMinimum']}"
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            return f"{name}: must be < {schema['exclusiveMaximum']}"

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            error = check_value(item, schema["items"], f"{name}[{index}]")
            if error:
                return error

    if isinstance(value, dict) and "properties" in schema:
        return check_object(value, schema, name)

    return None


def check_object(value: dict, schema: dict, name: str) -> str | None:
    properties = schema.get("properties", {})

    for required in schema.get("required", []):
        if required not in value:
            return f"{name}: missing required field '{required}'"

    for key, item in value.items():
        if key not in properties:
            if schema.get("additionalProperties"):
                continue
            return f"{name}: unknown field '{key}'"

        error = check_value(item, properties[key], f"{name}.{key}")
        if error:
            return error

    return None


def validate_parameters(action: APIAction, endpoint: dict) -> str:
    known = {parameter["name"]: parameter for parameter in endpoint["parameters"]}

    for name in action.parameters:
        if name not in known:
            return f"Unknown parameter: {name}"

    for parameter in endpoint["parameters"]:
        name = parameter["name"]

        if name not in action.parameters:
            if parameter["required"]:
                return f"Missing required parameter: {name}"
            continue

        error = check_value(action.parameters[name], parameter["schema"], name)
        if error:
            return f"Invalid parameter {error}"

    return OK


def validate_body(action: APIAction, endpoint: dict) -> str:
    body_spec = endpoint["body"]

    if body_spec is None:
        if action.body:
            return "This endpoint does not accept a request body"
        return OK

    if action.body is None:
        if body_spec["required"]:
            return "Missing required request body"
        return OK

    error = check_value(action.body, body_spec["schema"], "body")
    if error:
        return f"Invalid body {error}"

    return OK


def validate_action(action: APIAction, endpoints: list[dict]) -> str:
    endpoint = find_endpoint(action, endpoints)

    if endpoint is None:
        return f"Invalid endpoint: {action.method} {action.path}"

    parameter_result = validate_parameters(action, endpoint)
    if parameter_result != OK:
        return parameter_result

    return validate_body(action, endpoint)
