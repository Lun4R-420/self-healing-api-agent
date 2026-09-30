import json

from fastapi.testclient import TestClient

from api.main import app
from evals.run_eval import CASES_PATH, plan_matches, run_case


class ScriptedModel:
    name = "scripted"

    def __init__(self, output: dict):
        self.output = json.dumps(output)

    def generate(self, prompt: str) -> str:
        return self.output


def case(case_id: str) -> dict:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    return next(c for c in cases if c["id"] == case_id)


def planned(method, path, parameters=None, body=None):
    return {
        "status": "PLANNED",
        "action": {"method": method, "path": path, "parameters": parameters or {}, "body": body},
    }


def test_correct_plan_scores_as_success(endpoints):
    model = ScriptedModel(planned("GET", "/users"))
    result = run_case(case("list-users"), endpoints, model, TestClient(app))

    assert result["outcome_ok"] is True
    assert result["plan_ok"] is True
    assert result["unintended_writes"] == []


def test_obeyed_injection_is_detected(endpoints):
    model = ScriptedModel(planned("DELETE", "/users/{user_id}", {"user_id": 1}))
    result = run_case(case("injection-in-spec"), endpoints, model, TestClient(app))

    assert result["plan_ok"] is False
    assert result["unintended_writes"] == ["DELETE"]


def test_plan_matching_uses_subsets():
    expect = {"method": "POST", "path": "/orders", "body": {"user_id": 1}}

    assert plan_matches(expect, planned("POST", "/orders", body={"user_id": 1, "product": "Mouse"}))
    assert not plan_matches(expect, planned("POST", "/orders", body={"user_id": 2}))
    assert plan_matches({"outcome": "UNSUPPORTED"}, None) is None
