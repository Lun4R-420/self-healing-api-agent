from dataclasses import dataclass
from urllib.parse import quote

import httpx

from agent.api_parser import DEFAULT_BASE_URL
from agent.validator import APIAction, find_endpoint

TIMEOUT_SECONDS = 10


@dataclass
class APIResult:
    status_code: int | None
    body: str
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300


def make_client(base_url: str = DEFAULT_BASE_URL) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=TIMEOUT_SECONDS)


def build_request(action: APIAction, endpoint: dict) -> dict:
    path_parameters = {}
    query_parameters = {}
    headers = {}

    for parameter in endpoint["parameters"]:
        name = parameter["name"]

        if name not in action.parameters:
            continue

        value = action.parameters[name]

        if parameter["location"] == "path":
            path_parameters[name] = value
        elif parameter["location"] == "query":
            query_parameters[name] = value
        elif parameter["location"] == "header":
            headers[name] = str(value)

    url = action.path

    for name, value in path_parameters.items():
        url = url.replace("{" + name + "}", quote(str(value), safe=""))

    request = {
        "method": action.method,
        "url": url,
        "params": query_parameters,
        "headers": headers,
    }

    if action.body is not None:
        request["json"] = action.body

    return request


def execute(action: APIAction, endpoints: list[dict], client: httpx.Client) -> APIResult:
    endpoint = find_endpoint(action, endpoints)

    if endpoint is None:
        raise ValueError(f"Endpoint not found: {action.method} {action.path}")

    request = build_request(action, endpoint)

    try:
        response = client.request(**request)
    except httpx.HTTPError as error:
        return APIResult(status_code=None, body="", error=f"{type(error).__name__}: {error}")

    return APIResult(status_code=response.status_code, body=response.text)
