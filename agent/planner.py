from agent.api_parser import describe_endpoints
from agent.llm import LLMError, Model, get_default_model, parse_json_response
from agent.safety import UNTRUSTED_RULES, untrusted

SPEC_LIMIT = 100_000


def build_plan_prompt(user_request: str, endpoints: list[dict]) -> str:
    return f"""
You are an API planning system.

Select the single API endpoint that best fulfils the user's request
and fill in its parameters and request body.
{UNTRUSTED_RULES}
Available endpoints (from the API's OpenAPI specification):

{untrusted("openapi_spec", describe_endpoints(endpoints), SPEC_LIMIT)}

User request:

{user_request}

If an endpoint fits, return:

{{
    "status": "PLANNED",
    "action": {{
        "method": "...",
        "path": "/path/with/{{placeholders}}",
        "parameters": {{"parameter_name": "value"}},
        "body": null
    }}
}}

"path" must be copied exactly from the endpoint list, placeholders included.
"parameters" holds path, query and header parameters.
"body" holds the JSON request body, or null if the endpoint has none.

If no endpoint can fulfil the request, return:

{{
    "status": "UNSUPPORTED",
    "reason": "..."
}}

Rules:
- Do not invent endpoints, parameters or resource ids.
- Use the exact JSON types from the schemas.
- Do not execute the request.
- Return valid JSON only, with no explanations outside the JSON.
"""


def choose_endpoint(
    user_request: str,
    endpoints: list[dict],
    model: Model | None = None
) -> dict:
    prompt = build_plan_prompt(user_request, endpoints)

    try:
        model = model or get_default_model()
        output = model.generate(prompt)
    except LLMError as error:
        return {"status": "LLM_ERROR", "reason": str(error)}

    try:
        return parse_json_response(output)
    except ValueError as error:
        return {"status": "INVALID_OUTPUT", "reason": f"{error}: {output[:200]}"}
