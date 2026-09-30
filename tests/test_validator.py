import pytest

from agent.validator import OK, APIAction, action_from_dict, validate_action


def check(endpoints, method, path, parameters=None, body=None):
    return validate_action(APIAction(method, path, parameters or {}, body), endpoints)


def test_valid_get(endpoints):
    assert check(endpoints, "GET", "/users/{user_id}", {"user_id": 1}) == OK


def test_unknown_endpoint(endpoints):
    assert check(endpoints, "GET", "/admin").startswith("Invalid endpoint")


def test_wrong_method(endpoints):
    assert check(endpoints, "PUT", "/users/{user_id}", {"user_id": 1}).startswith("Invalid endpoint")


def test_missing_required_parameter(endpoints):
    assert check(endpoints, "GET", "/products") == "Missing required parameter: category"


def test_unknown_parameter(endpoints):
    result = check(endpoints, "GET", "/users/{user_id}", {"user_id": 1, "admin": True})
    assert result == "Unknown parameter: admin"


def test_wrong_parameter_type(endpoints):
    result = check(endpoints, "GET", "/users/{user_id}", {"user_id": "one"})
    assert "expected integer" in result


def test_boolean_is_not_an_integer(endpoints):
    assert "expected integer" in check(endpoints, "GET", "/users/{user_id}", {"user_id": True})


def test_enum_through_ref(endpoints):
    assert check(endpoints, "GET", "/products", {"category": "electronics"}) == OK
    assert "is not one of" in check(endpoints, "GET", "/products", {"category": "Electronics"})


def test_valid_body(endpoints):
    body = {"user_id": 1, "product": "Mouse", "quantity": 3}
    assert check(endpoints, "POST", "/orders", body=body) == OK


def test_optional_body_field_can_be_omitted(endpoints):
    assert check(endpoints, "POST", "/orders", body={"user_id": 1, "product": "Mouse"}) == OK


def test_missing_body(endpoints):
    assert check(endpoints, "POST", "/orders") == "Missing required request body"


def test_missing_body_field(endpoints):
    assert "missing required field 'product'" in check(endpoints, "POST", "/orders", body={"user_id": 1})


def test_unknown_body_field(endpoints):
    body = {"user_id": 1, "product": "Mouse", "is_admin": True}
    assert "unknown field 'is_admin'" in check(endpoints, "POST", "/orders", body=body)


def test_exclusive_minimum(endpoints):
    body = {"user_id": 1, "product": "Mouse", "quantity": 0}
    assert "must be > 0" in check(endpoints, "POST", "/orders", body=body)


def test_nullable_any_of(endpoints):
    assert check(endpoints, "PATCH", "/users/{user_id}", {"user_id": 1}, {"email": None}) == OK
    assert "does not match" in check(endpoints, "PATCH", "/users/{user_id}", {"user_id": 1}, {"email": 5})


def test_body_on_endpoint_without_body(endpoints):
    result = check(endpoints, "GET", "/users", body={"x": 1})
    assert result == "This endpoint does not accept a request body"


@pytest.mark.parametrize("data", [
    None,
    "GET /users",
    {"path": "/users"},
    {"method": "GET", "path": "users"},
    {"method": "GET", "path": "/users", "parameters": [1]},
    {"method": "GET", "path": "/users", "body": "text"},
])
def test_malformed_actions(data):
    with pytest.raises(ValueError):
        action_from_dict(data)


def test_action_method_is_normalised():
    assert action_from_dict({"method": "get", "path": "/users"}).method == "GET"
