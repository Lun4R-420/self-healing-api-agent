import json

import httpx

from agent.api_parser import get_base_url, get_endpoints, resolve_refs
from agent.executor import build_request, execute
from agent.validator import APIAction, find_endpoint
from api.main import app


def test_committed_spec_matches_api(spec):
    assert spec == json.loads(json.dumps(app.openapi()))


def test_every_route_is_discovered(endpoints):
    found = {(e["method"], e["path"]) for e in endpoints}

    assert ("POST", "/orders") in found
    assert ("DELETE", "/users/{user_id}") in found
    assert ("PATCH", "/users/{user_id}") in found
    assert len(found) == 9


def test_parameter_ref_is_resolved(endpoints):
    products = find_endpoint(APIAction("GET", "/products"), endpoints)
    category = products["parameters"][0]

    assert category["schema"]["enum"] == ["electronics", "stationery"]
    assert category["type"] == "string"


def test_request_body_is_parsed(endpoints):
    create = find_endpoint(APIAction("POST", "/orders"), endpoints)

    assert create["body"]["required"] is True
    assert create["body"]["schema"]["required"] == ["user_id", "product"]


def test_recursive_refs_terminate():
    spec = {"components": {"schemas": {"Node": {
        "type": "object",
        "properties": {"child": {"$ref": "#/components/schemas/Node"}},
    }}}}

    resolved = resolve_refs({"$ref": "#/components/schemas/Node"}, spec)

    assert resolved["type"] == "object"
    assert resolved["properties"]["child"] == {}


def test_path_level_parameters_are_inherited():
    spec = {"paths": {"/items/{item_id}": {
        "parameters": [{"name": "item_id", "in": "path", "schema": {"type": "integer"}}],
        "get": {"summary": "Get item"},
        "delete": {"summary": "Delete item"},
    }}}

    for endpoint in get_endpoints(spec):
        assert endpoint["parameters"][0]["name"] == "item_id"
        assert endpoint["parameters"][0]["required"] is True


def test_base_url_resolution():
    assert get_base_url({}, "agent/api.json") == "http://127.0.0.1:8000"
    assert get_base_url(
        {"servers": [{"url": "/api/v3"}]},
        "https://petstore3.swagger.io/api/v3/openapi.json",
    ) == "https://petstore3.swagger.io/api/v3"
    assert get_base_url({"servers": [{"url": "https://api.example.com"}]}, "x.json") == "https://api.example.com"


def test_path_values_cannot_escape_the_path(endpoints):
    action = APIAction("GET", "/users/{user_id}", {"user_id": "1/../../admin?x=1"})
    request = build_request(action, find_endpoint(action, endpoints))

    assert request["url"] == "/users/1%2F..%2F..%2Fadmin%3Fx%3D1"


def test_query_and_body_are_sent(endpoints):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(201, json={})

    client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))

    execute(APIAction("GET", "/products", {"category": "stationery"}), endpoints, client)
    assert seen["url"] == "http://test/products?category=stationery"

    execute(APIAction("POST", "/orders", body={"user_id": 1, "product": "Mouse"}), endpoints, client)
    assert json.loads(seen["body"]) == {"user_id": 1, "product": "Mouse"}
