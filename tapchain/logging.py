from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
from typing import Any


SENSITIVE_HEADERS = {
    "authorization",
    "proxy-authorization",
    "api-key",
    "x-api-key",
    "anthropic-api-key",
    "openai-api-key",
    "google-api-key",
    "cookie",
    "set-cookie",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def normalize_headers(headers: dict[str, str]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in SENSITIVE_HEADERS:
            sanitized[key] = "[REDACTED]"
        else:
            sanitized[key] = value
    return sanitized


def body_preview(data: bytes, limit: int = 2048) -> dict[str, Any]:
    if not data:
        return {"kind": "empty", "preview": "", "size": 0}

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")

    kind = "text"
    preview = text[:limit]
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    else:
        kind = "json"
        preview = json.dumps(parsed, ensure_ascii=False, indent=2)[:limit]

    return {
        "kind": kind,
        "preview": preview,
        "size": len(data),
        "sha256": sha256(data).hexdigest(),
    }


def safe_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)


@dataclass
class CapturePaths:
    root: Path
    trace_id: str
    stage: str

    @property
    def trace_dir(self) -> Path:
        return self.root / safe_filename(self.trace_id)

    def body_path(self, direction: str, suffix: str = "bin") -> Path:
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{self.stage}.{direction}.{suffix}"
        return self.trace_dir / filename


class JsonlLogger:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "events.jsonl"

    def write(self, record: dict[str, Any]) -> None:
        record = dict(record)
        record.setdefault("ts", utc_now())
        with self.events_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")

    def capture_paths(self, trace_id: str, stage: str) -> CapturePaths:
        return CapturePaths(root=self.root / "raw", trace_id=trace_id, stage=stage)

    def write_body(self, path: Path, data: bytes) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def write_json_body(self, path: Path, payload: Any) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return str(path)


def sanitize_hop_headers(headers: dict[str, str]) -> dict[str, str]:
    hop_by_hop = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
    }
    cleaned: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in hop_by_hop:
            continue
        cleaned[key] = value
    return cleaned


def ensure_dir(path: str | Path) -> Path:
    out = Path(path)
    out.mkdir(parents=True, exist_ok=True)
    return out
