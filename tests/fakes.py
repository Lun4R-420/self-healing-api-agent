from agent.safety import approve_all


def action(method: str, path: str, parameters: dict | None = None, body: dict | None = None) -> dict:
    return {
        "method": method,
        "path": path,
        "parameters": parameters or {},
        "body": body,
    }


def plan(method: str, path: str, parameters: dict | None = None, body: dict | None = None):
    def planner(user_request, endpoints):
        return {"status": "PLANNED", "action": action(method, path, parameters, body)}

    return planner


def repair_to(method: str, path: str, parameters: dict | None = None, body: dict | None = None):
    def repairer(user_request, current, error, endpoints):
        return {"status": "REPAIRED", "action": action(method, path, parameters, body)}

    return repairer


def unrepairable(user_request, current, error, endpoints):
    return {"status": "UNREPAIRABLE", "reason": "Cannot repair without changing intent."}


def llm_down(user_request, *args):
    return {"status": "LLM_ERROR", "reason": "429 Rate limit exceeded"}


class Recorder:
    def __init__(self, inner=approve_all):
        self.inner = inner
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.inner(*args)
