import argparse
import time
from dataclasses import dataclass, field
from functools import partial

import httpx

from agent.api_parser import get_base_url, get_endpoints, load_api
from agent.executor import execute, make_client
from agent.llm import build_model
from agent.observer import is_transient, observe
from agent.planner import choose_endpoint
from agent.repair import repair_action
from agent.safety import SAFE_METHODS, check_repair_scope, cli_approver, requires_approval, untrusted
from agent.tracing import Trace
from agent.validator import OK, APIAction, action_from_dict, validate_action

MAX_REPAIRS = 3
MAX_TRANSIENT_RETRIES = 2
RETRY_DELAY_SECONDS = 0.5


@dataclass
class AgentResult:
    outcome: str
    action: APIAction | None = None
    status_code: int | None = None
    body: str | None = None
    repairs: int = 0
    reason: str = ""
    trace: Trace = field(default_factory=Trace)


def describe_api_failure(status_code, observation: str, body: str, error: str | None) -> str:
    if error:
        return f"Observation: {observation}\nNetwork error: {error}"

    return (
        f"HTTP status: {status_code}\n"
        f"Observation: {observation}\n"
        f"API response:\n{untrusted('api_response', body)}"
    )


def run_agent(
    user_request: str,
    endpoints: list[dict],
    planner=choose_endpoint,
    repairer=repair_action,
    approver=cli_approver,
    client: httpx.Client | None = None,
    trace: Trace | None = None,
    max_repairs: int = MAX_REPAIRS,
    max_transient_retries: int = MAX_TRANSIENT_RETRIES,
    retry_delay: float = RETRY_DELAY_SECONDS,
) -> AgentResult:
    client = client or make_client()
    trace = trace or Trace()
    repairs = 0
    transient_retries = 0

    def finish(outcome: str, **details) -> AgentResult:
        trace.record("outcome", outcome=outcome, repairs=repairs, reason=details.get("reason", ""))
        return AgentResult(outcome=outcome, repairs=repairs, trace=trace, **details)

    trace.record("request", user_request=user_request)

    plan = planner(user_request, endpoints)
    trace.record("plan", result=plan)

    status = plan.get("status") if isinstance(plan, dict) else None

    if status == "LLM_ERROR":
        return finish("LLM_ERROR", reason=plan.get("reason", ""))

    if status == "UNSUPPORTED":
        return finish("UNSUPPORTED", reason=plan.get("reason", ""))

    if status != "PLANNED":
        return finish("VALIDATION_ERROR", reason=f"Planner returned malformed output: {plan}")

    try:
        action = action_from_dict(plan.get("action"))
    except ValueError as error:
        return finish("VALIDATION_ERROR", reason=f"Malformed planned action: {error}")

    while True:
        validation = validate_action(action, endpoints)
        trace.record("validation", action=action.to_dict(), result=validation)

        if validation == OK:
            if requires_approval(action):
                approved = approver(action)
                trace.record("approval", action=action.to_dict(), approved=approved)

                if not approved:
                    return finish("DENIED", action=action, reason="Human rejected the action")

            api_result = execute(action, endpoints, client)
            observation = observe(api_result)
            trace.record(
                "execution",
                action=action.to_dict(),
                status_code=api_result.status_code,
                observation=observation,
                body=api_result.body[:500],
                error=api_result.error,
            )

            if observation == "SUCCESS":
                return finish(
                    "SUCCESS",
                    action=action,
                    status_code=api_result.status_code,
                    body=api_result.body,
                )

            if is_transient(observation):
                if action.method in SAFE_METHODS and transient_retries < max_transient_retries:
                    transient_retries += 1
                    time.sleep(retry_delay * 2 ** (transient_retries - 1))
                    continue

                return finish(
                    "EXECUTION_ERROR",
                    action=action,
                    status_code=api_result.status_code,
                    body=api_result.body,
                    reason=observation,
                )

            failure = describe_api_failure(
                api_result.status_code, observation, api_result.body, api_result.error
            )
        else:
            failure = f"Validation error: {validation}"

        if repairs >= max_repairs:
            return finish("MAX_RETRIES", action=action, reason=failure)

        repair = repairer(user_request, action, failure, endpoints)
        repairs += 1
        trace.record("repair", attempt=repairs, result=repair)

        repair_status = repair.get("status") if isinstance(repair, dict) else None

        if repair_status == "LLM_ERROR":
            return finish("LLM_ERROR", action=action, reason=repair.get("reason", ""))

        if repair_status == "UNREPAIRABLE":
            return finish("UNREPAIRABLE", action=action, reason=repair.get("reason", ""))

        if repair_status != "REPAIRED":
            return finish("VALIDATION_ERROR", action=action, reason=f"Repair returned malformed output: {repair}")

        try:
            repaired = action_from_dict(repair.get("action"))
        except ValueError as error:
            return finish("VALIDATION_ERROR", action=action, reason=f"Malformed repaired action: {error}")

        scope = check_repair_scope(action, repaired)

        if scope != OK:
            trace.record("repair_rejected", reason=scope)
            return finish("VALIDATION_ERROR", action=action, reason=scope)

        action = repaired


def main():
    parser = argparse.ArgumentParser(description="Self-healing API agent")
    parser.add_argument("request", nargs="?", help="What you want to do, in plain English")
    parser.add_argument("--spec", default="agent/api.json", help="OpenAPI spec file path or URL")
    parser.add_argument("--base-url", help="Override the API base URL")
    parser.add_argument("--model", default="gemini", choices=["gemini", "ollama", "gemini+ollama"])
    parser.add_argument("--quiet", action="store_true", help="Hide step-by-step trace output")
    args = parser.parse_args()

    request = args.request or input("What do you want to do? ")

    spec = load_api(args.spec)
    endpoints = get_endpoints(spec)
    base_url = args.base_url or get_base_url(spec, args.spec)
    model = build_model(args.model)

    trace = Trace(verbose=not args.quiet)

    result = run_agent(
        request,
        endpoints,
        planner=partial(choose_endpoint, model=model),
        repairer=partial(repair_action, model=model),
        client=make_client(base_url),
        trace=trace,
    )

    print(f"\nOutcome: {result.outcome}")
    if result.reason:
        print(f"Reason: {result.reason}")
    if result.body:
        print(f"Response: {result.body}")
    print(f"Trace saved to {trace.save()}")


if __name__ == "__main__":
    main()
