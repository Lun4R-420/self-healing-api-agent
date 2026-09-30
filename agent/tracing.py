import json
import time
import uuid
from pathlib import Path


class Trace:
    def __init__(self, verbose: bool = False):
        self.run_id = uuid.uuid4().hex[:12]
        self.verbose = verbose
        self.events = []
        self.started = time.perf_counter()

    def record(self, kind: str, **data):
        event = {
            "kind": kind,
            "elapsed_ms": round((time.perf_counter() - self.started) * 1000, 1),
            **data,
        }
        self.events.append(event)

        if self.verbose:
            print(f"\n[{kind}]")
            if data:
                print(json.dumps(data, indent=2, default=str))

    def of_kind(self, kind: str) -> list[dict]:
        return [event for event in self.events if event["kind"] == kind]

    def save(self, directory: str | Path = "traces") -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.run_id}.jsonl"

        with open(path, "w", encoding="utf-8") as file:
            for event in self.events:
                file.write(json.dumps(event, default=str) + "\n")

        return path
