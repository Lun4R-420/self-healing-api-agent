from agent.executor import APIResult

STATUS_NAMES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "UNPROCESSABLE",
    429: "RATE_LIMITED",
}

TRANSIENT = {"RATE_LIMITED", "SERVER_ERROR", "NETWORK_ERROR"}


def observe(result: APIResult) -> str:
    if result.status_code is None:
        return "NETWORK_ERROR"

    if 200 <= result.status_code < 300:
        return "SUCCESS"

    if result.status_code in STATUS_NAMES:
        return STATUS_NAMES[result.status_code]

    if 500 <= result.status_code < 600:
        return "SERVER_ERROR"

    return "UNKNOWN_ERROR"


def is_transient(observation: str) -> bool:
    return observation in TRANSIENT
