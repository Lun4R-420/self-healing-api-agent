import pytest

from agent.llm import CachedModel, FallbackModel, LLMError, parse_json_response
from agent.planner import choose_endpoint


class FakeModel:
    def __init__(self, output=None, fail=False, name="fake"):
        self.output = output
        self.fail = fail
        self.name = name
        self.calls = 0

    def generate(self, prompt):
        self.calls += 1
        if self.fail:
            raise LLMError("quota exceeded")
        return self.output


@pytest.mark.parametrize("text", [
    '{"status": "PLANNED"}',
    '```json\n{"status": "PLANNED"}\n```',
    'Sure! Here it is: {"status": "PLANNED"} Hope that helps.',
])
def test_parse_json_response(text):
    assert parse_json_response(text) == {"status": "PLANNED"}


def test_parse_json_response_rejects_prose():
    with pytest.raises(ValueError):
        parse_json_response("I cannot help with that.")


def test_fallback_is_used_when_primary_fails():
    primary = FakeModel(fail=True)
    fallback = FakeModel(output="local answer")

    assert FallbackModel(primary, fallback).generate("hi") == "local answer"
    assert primary.calls == 1


def test_cache_avoids_repeat_calls(tmp_path):
    inner = FakeModel(output="answer")
    model = CachedModel(inner, tmp_path / "cache.json")

    assert model.generate("prompt") == "answer"
    assert model.generate("prompt") == "answer"
    assert inner.calls == 1

    replay = CachedModel(None, tmp_path / "cache.json")
    assert replay.generate("prompt") == "answer"

    with pytest.raises(LLMError):
        replay.generate("new prompt")


def test_planner_reports_llm_failure(endpoints):
    result = choose_endpoint("Get user 1", endpoints, model=FakeModel(fail=True))
    assert result["status"] == "LLM_ERROR"


def test_planner_reports_invalid_output(endpoints):
    result = choose_endpoint("Get user 1", endpoints, model=FakeModel(output="no json here"))
    assert result["status"] == "INVALID_OUTPUT"
