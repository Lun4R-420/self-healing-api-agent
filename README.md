# Self-Healing API Agent

An LLM agent that uses HTTP APIs it has never seen before. You give it an OpenAPI spec and a request in plain English, it figures out which call to make, and if the call fails it reads the error and fixes its own mistake.

The part I cared about most is that the LLM never gets to act on its own. It only proposes a call as JSON. Plain code checks that proposal against the spec, anything that changes data needs a human to approve it, and the model can't talk its way past either of those.

```
$ python -m agent "Order 3 keybords for user 2"

[plan]        POST /orders  {"user_id": 2, "product": "Keybords", "quantity": 3}
[validation]  Ok
[approval]    approved
[execution]   400  Unknown product 'Keybords'. Available products: Keyboard, Mouse, Monitor, Notebook
[repair]      REPAIRED  product -> "Keyboard"
[validation]  Ok
[approval]    approved
[execution]   201
Outcome: SUCCESS
```

## How it works

Request goes in, the planner (Gemini, or a local model through Ollama) picks an endpoint and fills in the parameters. The validator checks that against the spec: does the endpoint exist, are the parameters real, are the types right. If the call changes data, I get asked before it's sent. The executor sends it, the observer looks at the response, and on a 4xx the repair step gets the error message and tries again.

The API it talks to by default is a small fake "company" API in `api/main.py` (users, products, orders), so I could test failures without touching anything real. It also works on public specs, like the Petstore one below.

## Design decisions

**Different failures get different fixes.** A 429, a 5xx or a network error on a GET is just retried with backoff, no LLM needed. A 4xx means the request itself was wrong, so that goes to the repair step. Writes are never retried automatically, since repeating a POST could create the same order twice.

**Repairs can't change the method.** A repair can fix a value or pick another path, but a failed GET can never turn into a DELETE, no matter what the error message says.

**Text from outside is data, not instructions.** API responses and spec descriptions come from systems the agent doesn't control, so they're wrapped in `<untrusted>` tags before going into the prompt. That alone isn't enough (models can still be fooled), which is why the method lock and the approval step exist regardless.

**Every run ends in one clear outcome:** `SUCCESS`, `UNREPAIRABLE`, `UNSUPPORTED`, `DENIED`, `VALIDATION_ERROR`, `MAX_RETRIES`, `EXECUTION_ERROR` or `LLM_ERROR`. That's what the tests check.

## Tests

The planner, repairer, approver and HTTP client are all passed into `run_agent`, so the tests swap in scripted fakes and run the fake API in-process. 66 tests, under a second, no API key needed. They cover things like invented endpoints, wrong types, a typo fixed from the error message, a nonexistent id that shouldn't be "fixed", a DELETE the user denies, and a `DELETE /users/1` injected into an error message.

```bash
python -m pytest
```

There's also an evaluation in `evals/` that runs the real model on 21 cases (reads, writes, typos, unsupported requests, injection attempts) and measures how often it gets the right outcome. LLM responses are cached, so a run that hits the free-tier limit can pick up the next day without redoing calls.

```bash
python -m evals.run_eval
```

## Running it

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Put `GEMINI_API_KEY=...` in a `.env` file, start the fake API, then ask it something:

```bash
python -m uvicorn api.main:app --reload
python -m agent "Show me every order placed by user 1"
```

Or point it at an API it has never seen:

```bash
python -m agent --spec https://petstore3.swagger.io/api/v3/openapi.json "Find pets that are sold"
```

`--model ollama` uses a local model instead, `--model gemini+ollama` falls back to the local one when Gemini is down, and `--quiet` hides the step-by-step output. Every run is logged to `traces/<run_id>.jsonl`.

## What's not done

- It makes one API call per request. Something like "cancel all of Alice's orders" would need several calls chained together, and it can't plan that yet.
- JSON request bodies only, no auth flows or file uploads.
- The validator covers the common JSON Schema rules, not the whole spec.
