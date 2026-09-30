import pytest
from fastapi.testclient import TestClient

from agent.api_parser import get_endpoints, load_api
from api.main import app, reset_data


@pytest.fixture(autouse=True)
def fresh_data():
    reset_data()
    yield
    reset_data()


@pytest.fixture(scope="session")
def spec():
    return load_api("agent/api.json")


@pytest.fixture(scope="session")
def endpoints(spec):
    return get_endpoints(spec)


@pytest.fixture
def client():
    return TestClient(app)
