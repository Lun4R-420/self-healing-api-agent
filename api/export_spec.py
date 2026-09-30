import json
from pathlib import Path

from api.main import app

OUTPUT = Path("agent/api.json")


if __name__ == "__main__":
    OUTPUT.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT}")
