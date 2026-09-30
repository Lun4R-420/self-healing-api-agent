import httpx

from agent.agent import MAX_REPAIRS, run_agent
from agent.safety import approve_all, deny_all
from api.main import orders, users
from fakes import Recorder, llm_down, plan, repair_to, unrepairable


def run(request, endpoints, client, **overrides):
    options = {"repairer": unrepairable, "approver": deny_all, "client": client}
    options.update(overrides)
    return run_agent(request, endpoints, **options)


def test_valid_request_succeeds(endpoints, client):
    result = run("Get user 1", endpoints, client, planner=plan("GET", "/users/{user_id}", {"user_id": 1}))

    assert result.outcome == "SUCCESS"
    assert result.repairs == 0
    assert '"Alice"' in result.body


def test_invalid_endpoint_is_never_executed(endpoints, client):
    result = run("Wipe everything", endpoints, client, planner=plan("DELETE", "/users"))

    assert result.outcome == "UNREPAIRABLE"
    assert result.trace.of_kind("execution") == []


def test_missing_parameter_is_repaired(endpoints, client):
    result = run(
        "Get electronics products",
        endpoints,
        client,
        planner=plan("GET", "/products"),
        repairer=repair_to("GET", "/products", {"category": "electronics"}),
    )

    assert result.outcome == "SUCCESS"
    assert result.repairs == 1
    assert result.trace.of_kind("execution")[0]["action"]["parameters"] == {"category": "electronics"}


def test_wrong_type_is_caught_before_execution(endpoints, client):
    result = run(
        "Get user one",
        endpoints,
        client,
        planner=plan("GET", "/users/{user_id}", {"user_id": "one"}),
        repairer=repair_to("GET", "/users/{user_id}", {"user_id": 1}),
    )

    assert result.outcome == "SUCCESS"
    assert len(result.trace.of_kind("execution")) == 1


def test_nonexistent_resource_is_unrepairable(endpoints, client):
    result = run(
        "Get orders for user 999",
        endpoints,
        client,
        planner=plan("GET", "/users/{user_id}/orders", {"user_id": 999}),
    )

    assert result.outcome == "UNREPAIRABLE"
    assert result.trace.of_kind("execution")[0]["status_code"] == 404


def test_api_business_error_is_repaired_and_retried(endpoints, client):
    body = {"user_id": 1, "product": "Keybord", "quantity": 2}
    fixed = {"user_id": 1, "product": "Keyboard", "quantity": 2}

    result = run(
        "Order 2 keybords for Alice",
        endpoints,
        client,
        planner=plan("POST", "/orders", body=body),
        repairer=repair_to("POST", "/orders", body=fixed),
        approver=approve_all,
    )

    executions = result.trace.of_kind("execution")

    assert result.outcome == "SUCCESS"
    assert [e["status_code"] for e in executions] == [400, 201]
    assert "Available products" in executions[0]["body"]


def test_invented_endpoint_from_repair_is_never_executed(endpoints, client):
    result = run(
        "Get user one",
        endpoints,
        client,
        planner=plan("GET", "/users/{user_id}", {"user_id": "one"}),
        repairer=repair_to("GET", "/admin/users"),
    )

    assert result.outcome == "MAX_RETRIES"
    assert result.trace.of_kind("execution") == []


def test_malformed_planner_output_is_rejected(endpoints, client):
    result = run(
        "Get user 1",
        endpoints,
        client,
        planner=lambda request, endpoints: {"status": "PLANNED", "action": {"path": 42}},
    )

    assert result.outcome == "VALIDATION_ERROR"
    assert result.trace.of_kind("execution") == []


def test_repeated_failure_stops_at_max_retries(endpoints, client):
    repairer = Recorder(repair_to("GET", "/users/{user_id}", {"user_id": "one"}))

    result = run(
        "Get user one",
        endpoints,
        client,
        planner=plan("GET", "/users/{user_id}", {"user_id": "one"}),
        repairer=repairer,
    )

    assert result.outcome == "MAX_RETRIES"
    assert len(repairer.calls) == MAX_REPAIRS


def test_llm_unavailable_fails_gracefully(endpoints, client):
    result = run("Get user 1", endpoints, client, planner=llm_down)

    assert result.outcome == "LLM_ERROR"


def test_unsupported_request_is_reported(endpoints, client):
    result = run(
        "Book me a flight",
        endpoints,
        client,
        planner=lambda request, endpoints: {"status": "UNSUPPORTED", "reason": "No flight endpoints"},
    )

    assert result.outcome == "UNSUPPORTED"


def test_destructive_action_requires_approval(endpoints, client):
    approver = Recorder(deny_all)

    result = run(
        "Delete user 1",
        endpoints,
        client,
        planner=plan("DELETE", "/users/{user_id}", {"user_id": 1}),
        approver=approver,
    )

    assert result.outcome == "DENIED"
    assert len(approver.calls) == 1
    assert 1 in users
    assert result.trace.of_kind("execution") == []


def test_approved_destructive_action_executes(endpoints, client):
    result = run(
        "Cancel order 102",
        endpoints,
        client,
        planner=plan("DELETE", "/orders/{order_id}", {"order_id": 102}),
        approver=approve_all,
    )

    assert result.outcome == "SUCCESS"
    assert 102 not in orders


def test_read_only_action_skips_approval(endpoints, client):
    approver = Recorder()

    run("List users", endpoints, client, planner=plan("GET", "/users"), approver=approver)

    assert approver.calls == []


def test_transient_error_is_retried_without_llm(endpoints):
    responses = iter([503, 503, 200])

    def handler(request):
        return httpx.Response(next(responses), json={"ok": True})

    client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))
    repairer = Recorder(unrepairable)

    result = run(
        "List users",
        endpoints,
        client,
        planner=plan("GET", "/users"),
        repairer=repairer,
        retry_delay=0,
    )

    assert result.outcome == "SUCCESS"
    assert repairer.calls == []
    assert len(result.trace.of_kind("execution")) == 3


def test_transient_error_on_write_is_not_retried(endpoints):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))

    result = run(
        "Cancel order 101",
        endpoints,
        client,
        planner=plan("DELETE", "/orders/{order_id}", {"order_id": 101}),
        approver=approve_all,
        retry_delay=0,
    )

    assert result.outcome == "EXECUTION_ERROR"
    assert len(calls) == 1


def test_network_failure_is_execution_error(endpoints):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))

    result = run("List users", endpoints, client, planner=plan("GET", "/users"), retry_delay=0)

    assert result.outcome == "EXECUTION_ERROR"
