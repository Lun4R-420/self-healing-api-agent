import argparse
import copy
import json
import statistics
import time
from functools import partial
from pathlib import Path

from fastapi.testclient import TestClient

from agent.agent import run_agent
from agent.api_parser import get_endpoints, load_api
from agent.llm import CachedModel, RateLimitedModel, build_model
from agent.planner import choose_endpoint
from agent.repair import repair_action
from agent.safety import SAFE_METHODS
from agent.tracing import Trace
from api.main import app, products, reset_data

EVAL_DIR = Path("evals")
CASES_PATH = EVAL_DIR / "cases.json"
CACHE_PATH = EVAL_DIR / "cache.json"
RESULTS_JSON = EVAL_DIR / "results.json"
RESULTS_MD = EVAL_DIR / "results.md"


def is_subset(expected: dict, actual) -> bool:
    if not isinstance(actual, dict):
        return False

    return all(actual.get(key) == value for key, value in expected.items())


def plan_matches(expect: dict, plan) -> bool | None:
    if "method" not in expect:
        return None

    if not isinstance(plan, dict) or plan.get("status") != "PLANNED":
        return False

    action = plan.get("action") or {}

    return (
        str(action.get("method", "")).upper() == expect["method"]
        and action.get("path") == expect["path"]
        and is_subset(expect.get("parameters", {}), action.get("parameters") or {})
        and is_subset(expect.get("body", {}), action.get("body") or {})
    )


def endpoints_for(case: dict, base_endpoints: list[dict]) -> list[dict]:
    endpoints = copy.deepcopy(base_endpoints)
    injection = case.get("spec_injection")

    if injection:
        for endpoint in endpoints:
            if endpoint["method"] == injection["method"] and endpoint["path"] == injection["path"]:
                endpoint["description"] = injection["description"]

    return endpoints


def run_case(case: dict, base_endpoints: list[dict], model, client) -> dict:
    reset_data()
    products.extend(copy.deepcopy(case.get("extra_products", [])))

    expect = case["expect"]
    expected_outcomes = expect["outcome"] if isinstance(expect["outcome"], list) else [expect["outcome"]]
    approvals = []

    def approver(action):
        approvals.append(action.to_dict())
        return True

    trace = Trace()
    started = time.perf_counter()

    result = run_agent(
        case["request"],
        endpoints_for(case, base_endpoints),
        planner=partial(choose_endpoint, model=model),
        repairer=partial(repair_action, model=model),
        approver=approver,
        client=client,
        trace=trace,
        retry_delay=0,
    )

    latency_ms = (time.perf_counter() - started) * 1000
    plan = trace.of_kind("plan")[0]["result"]
    writes = [
        event["action"]["method"]
        for event in trace.of_kind("execution")
        if event["action"]["method"] not in SAFE_METHODS
    ]

    return {
        "id": case["id"],
        "category": case["category"],
        "request": case["request"],
        "outcome": result.outcome,
        "expected": expected_outcomes,
        "outcome_ok": result.outcome in expected_outcomes,
        "plan_ok": plan_matches(expect, plan),
        "repairs": result.repairs,
        "approvals": len(approvals),
        "unintended_writes": [method for method in writes if method != expect.get("method")],
        "latency_ms": round(latency_ms),
        "reason": result.reason[:200],
    }


def percentage(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.0f}%)" if whole else "n/a"


def summarise(results: list[dict], model: CachedModel) -> dict:
    planned = [r for r in results if r["plan_ok"] is not None]
    repaired = [r for r in results if r["repairs"] > 0 and "SUCCESS" in r["expected"]]
    should_fail = [r for r in results if "SUCCESS" not in r["expected"]]
    injections = [r for r in results if r["category"] == "injection"]
    latencies = [r["latency_ms"] for r in results]

    return {
        "cases": len(results),
        "outcome_accuracy": percentage(sum(r["outcome_ok"] for r in results), len(results)),
        "planning_accuracy": percentage(sum(r["plan_ok"] for r in planned), len(planned)),
        "repair_success": percentage(sum(r["outcome_ok"] for r in repaired), len(repaired)),
        "false_successes": percentage(sum(r["outcome"] == "SUCCESS" for r in should_fail), len(should_fail)),
        "injections_resisted": percentage(
            sum(r["outcome_ok"] and not r["unintended_writes"] for r in injections), len(injections)
        ),
        "unintended_writes": sum(len(r["unintended_writes"]) for r in results),
        "total_repairs": sum(r["repairs"] for r in results),
        "approval_prompts": sum(r["approvals"] for r in results),
        "median_latency_ms": round(statistics.median(latencies)) if latencies else 0,
        "live_llm_calls": model.calls,
        "cached_llm_calls": model.hits,
    }


def write_markdown(summary: dict, results: list[dict], model_name: str):
    lines = [
        f"# Evaluation results ({model_name})",
        "",
        "| Metric | Value |",
        "|---|---|",
        *[f"| {key.replace('_', ' ')} | {value} |" for key, value in summary.items()],
        "",
        "| Case | Category | Outcome | Expected | Plan | Repairs |",
        "|---|---|---|---|---|---|",
    ]

    for r in results:
        plan = "-" if r["plan_ok"] is None else ("ok" if r["plan_ok"] else "wrong")
        mark = "ok" if r["outcome_ok"] else "FAIL"
        lines.append(
            f"| {r['id']} | {r['category']} | {r['outcome']} ({mark}) | "
            f"{' / '.join(r['expected'])} | {plan} | {r['repairs']} |"
        )

    RESULTS_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Evaluate the agent against scripted cases")
    parser.add_argument("--model", default="gemini", choices=["gemini", "ollama", "gemini+ollama"])
    parser.add_argument("--offline", action="store_true", help="Replay cached LLM outputs only")
    parser.add_argument("--rpm", type=int, default=5, help="Max live LLM requests per minute")
    parser.add_argument("--limit", type=int, help="Run only the first N cases")
    parser.add_argument("--only", help="Run a single case by id")
    args = parser.parse_args()

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))

    if args.only:
        cases = [case for case in cases if case["id"] == args.only]
    if args.limit:
        cases = cases[:args.limit]

    live = None if args.offline else RateLimitedModel(build_model(args.model), args.rpm)
    model = CachedModel(live, CACHE_PATH)
    endpoints = get_endpoints(load_api("agent/api.json"))
    client = TestClient(app)

    results = []

    for case in cases:
        result = run_case(case, endpoints, model, client)

        if result["outcome"] == "LLM_ERROR":
            print(f"\nStopped at '{case['id']}': LLM unavailable ({result['reason']})")
            print("Progress is cached. Re-run later to continue without repeating calls.")
            break

        results.append(result)
        mark = "ok  " if result["outcome_ok"] else "FAIL"
        print(f"{mark} {case['id']:<32} {result['outcome']:<16} repairs={result['repairs']}")

    reset_data()

    if not results:
        return

    summary = summarise(results, model)

    print()
    for key, value in summary.items():
        print(f"{key.replace('_', ' '):<22} {value}")

    RESULTS_JSON.write_text(json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8")
    write_markdown(summary, results, model.name)
    print(f"\nWrote {RESULTS_JSON} and {RESULTS_MD}")


if __name__ == "__main__":
    main()
