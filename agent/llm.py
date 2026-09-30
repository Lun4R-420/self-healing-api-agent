import hashlib
import json
import os
import time
from functools import cache
from pathlib import Path
from typing import Protocol

import httpx
from dotenv import load_dotenv

GEMINI_MODEL = "gemini-3.6-flash"
OLLAMA_MODEL = "qwen2.5:7b"
OLLAMA_URL = "http://127.0.0.1:11434"


class LLMError(Exception):
    pass


class Model(Protocol):
    name: str

    def generate(self, prompt: str) -> str:
        ...


class GeminiModel:
    def __init__(self, model_name: str = GEMINI_MODEL):
        load_dotenv()
        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise LLMError("GEMINI_API_KEY is not set")

        from google import genai

        self.client = genai.Client(api_key=api_key)
        self.name = f"gemini:{model_name}"
        self.model_name = model_name

    def generate(self, prompt: str) -> str:
        try:
            interaction = self.client.interactions.create(
                model=self.model_name,
                input=prompt
            )
        except Exception as error:
            raise LLMError(str(error)) from error

        return interaction.output_text


class OllamaModel:
    def __init__(self, model_name: str = OLLAMA_MODEL, url: str = OLLAMA_URL):
        self.name = f"ollama:{model_name}"
        self.model_name = model_name
        self.url = url

    def generate(self, prompt: str) -> str:
        try:
            response = httpx.post(
                f"{self.url}/api/generate",
                json={
                    "model": self.model_name,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                },
                timeout=120,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise LLMError(f"Ollama request failed: {error}") from error

        return response.json()["response"]


class FallbackModel:
    def __init__(self, primary: Model, fallback: Model):
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}->{fallback.name}"

    def generate(self, prompt: str) -> str:
        try:
            return self.primary.generate(prompt)
        except LLMError:
            return self.fallback.generate(prompt)


class RateLimitedModel:
    def __init__(self, model: Model, per_minute: int):
        self.model = model
        self.name = model.name
        self.interval = 60 / per_minute
        self.last_call = None

    def generate(self, prompt: str) -> str:
        if self.last_call is not None:
            wait = self.interval - (time.monotonic() - self.last_call)
            if wait > 0:
                time.sleep(wait)

        self.last_call = time.monotonic()
        return self.model.generate(prompt)


class CachedModel:
    def __init__(self, model: Model | None, path: str | Path):
        self.model = model
        self.path = Path(path)
        self.name = f"cached:{model.name if model else 'offline'}"
        self.calls = 0
        self.hits = 0
        self.cache = {}

        if self.path.exists():
            self.cache = json.loads(self.path.read_text(encoding="utf-8"))

    def generate(self, prompt: str) -> str:
        key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

        if key in self.cache:
            self.hits += 1
            return self.cache[key]

        if self.model is None:
            raise LLMError("Prompt not in cache and no live model configured")

        self.calls += 1
        output = self.model.generate(prompt)
        self.cache[key] = output
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.cache, indent=2), encoding="utf-8")

        return output


@cache
def get_default_model() -> Model:
    return GeminiModel()


def build_model(name: str) -> Model:
    if name == "gemini":
        return GeminiModel()
    if name == "ollama":
        return OllamaModel()
    if name == "gemini+ollama":
        return FallbackModel(GeminiModel(), OllamaModel())
    raise ValueError(f"Unknown model: {name}")


def parse_json_response(text: str) -> dict:
    text = text.strip()

    if text.startswith("```"):
        lines = text.splitlines()[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines)

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("Model output contains no JSON object")

    return json.loads(text[start:end + 1])
