"""Per-incident JSONL trace + JSON logging."""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("incidentops")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "data", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    log.handlers[:] = [h]
    log.setLevel(level)


def clip(obj: Any, n: int = 800) -> Any:
    s = json.dumps(obj, default=str)
    return obj if len(s) <= n else s[:n] + "...<truncated>"


class TraceWriter:
    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def path(self, incident_id: str) -> Path:
        return self.dir / f"{incident_id}.jsonl"

    def emit(self, incident_id: str, node: str, kind: str, **data: Any) -> None:
        rec = {
            "ts": round(time.time(), 3),
            "incident_id": incident_id,
            "node": node,
            "kind": kind,
            **data,
        }
        with self._lock, self.path(incident_id).open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        log.debug("%s/%s", node, kind, extra={"data": rec})

    def read(self, incident_id: str) -> list[dict[str, Any]]:
        p = self.path(incident_id)
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
