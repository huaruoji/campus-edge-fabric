"""Export the shared worker wire models; update only with a contract review."""
import json
from pathlib import Path

from cef_protocol import models
from pydantic.json_schema import models_json_schema

NAMES = (
    "RegisterRequest", "RegisterResponse", "HeartbeatRequest", "HeartbeatResponse",
    "PollRequest", "PollResponse", "CompleteMeta", "CompleteResponse",
    "FailRequest", "FailResponse", "DrainResponse", "ErrorResponse",
)


def snapshot():
    _, schema = models_json_schema([(getattr(models, name), "validation") for name in NAMES])
    return {"contract": "cef-worker-v1", "models": list(NAMES), **schema}


if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "docs/worker-schema-v1.json"
    path.write_text(json.dumps(snapshot(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
