from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import base64
import json
import re
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


def _short_text(value: str, limit: int = 32) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    if not compact:
        return "NoPrompt"
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "..."


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except Exception:
        return b""


def _read_json_bytes(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _extract_first_user_prompt(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                        parts.append(item["text"])
                text = "".join(parts).strip()
                if text:
                    return text
    prompt = payload.get("prompt")
    if isinstance(prompt, str):
        return prompt
    return ""


def _infer_provider(payload: Any, stage: str, upstream: str) -> str:
    model = ""
    if isinstance(payload, dict):
        raw_model = payload.get("model")
        if isinstance(raw_model, str):
            model = raw_model.lower()
    upstream_lower = upstream.lower()
    if "anthropic" in upstream_lower or "claude" in model:
        return "Claude"
    if "gemini" in model or "generativelanguage" in upstream_lower:
        return "Gemini"
    if "gpt" in model or "openai" in upstream_lower or "codex" in model:
        return "OpenAI"
    if "newapi-sub2" in stage:
        return "Sub2API"
    return "Unknown"


def _infer_route(stage: str, upstream: str) -> str:
    stage_lower = stage.lower()
    upstream_lower = upstream.lower()
    if "newapi-cliproxy" in stage_lower:
        return "NewAPI_CPA"
    if "newapi-sub2" in stage_lower:
        return "NewAPI_Sub2API"
    if "client-newapi" in stage_lower:
        return "Client_NewAPI"
    if "api.anthropic.com" in upstream_lower or "generativelanguage.googleapis.com" in upstream_lower or "api.openai.com" in upstream_lower:
        return "官方直连"
    return safe_filename(stage)


def _infer_client(headers: dict[str, Any], records: list[dict[str, Any]]) -> str:
    candidates: list[str] = []
    user_agent = str(headers.get("user-agent", ""))
    if user_agent:
        candidates.append(user_agent)
    for record in records:
        if record.get("event") != "request_in":
            continue
        record_headers = record.get("headers")
        if isinstance(record_headers, dict):
            ua = record_headers.get("user-agent")
            if isinstance(ua, str) and ua:
                candidates.append(ua)
    joined = " ".join(candidates).lower()
    if "claude" in joined:
        return "ClaudeCode"
    if "opencode" in joined:
        return "OpenCode"
    if "gemini" in joined:
        return "GeminiCLI"
    if "go-http-client" in joined:
        return "NewAPI"
    return "UnknownClient"


def _infer_model(payload: Any) -> str:
    if isinstance(payload, dict):
        model = payload.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
    return "unknown-model"


def _raw_header_names(headers: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in headers:
        parts = key.split("-")
        out[key] = "-".join(part[:1].upper() + part[1:] for part in parts if part)
    return out


def _load_trace_records(events_path: Path, trace_id: str) -> list[dict[str, Any]]:
    if not events_path.exists():
        return []
    matches: list[dict[str, Any]] = []
    try:
        with events_path.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line or trace_id not in line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if record.get("trace_id") == trace_id:
                    matches.append(record)
    except Exception:
        return []
    return matches


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

    def http_path(self, direction: str) -> Path:
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{self.stage}.{direction}.http"
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

    def write_http_message(
        self,
        path: Path,
        start_line: str,
        header_items: list[tuple[str, str]],
        body: bytes,
    ) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fp:
            fp.write(start_line.encode("utf-8", errors="replace") + b"\r\n")
            for key, value in header_items:
                fp.write(key.encode("utf-8", errors="replace"))
                fp.write(b": ")
                fp.write(value.encode("utf-8", errors="replace"))
                fp.write(b"\r\n")
            fp.write(b"\r\n")
            fp.write(body)
        return str(path)

    def write_pretty_export(
        self,
        *,
        trace_id: str,
        stage: str,
        upstream: str,
        method: str,
        path: str,
        query: str,
        request_headers: dict[str, str],
        request_body_path: Path,
        response_headers: dict[str, str],
        response_body_path: Path,
        status_code: int,
        completed: bool,
    ) -> str | None:
        request_payload = _read_json_bytes(request_body_path)
        prompt = _extract_first_user_prompt(request_payload)
        provider = _infer_provider(request_payload, stage, upstream)
        route = _infer_route(stage, upstream)
        records = _load_trace_records(self.events_path, trace_id)
        client = _infer_client(request_headers, records)
        model = _infer_model(request_payload)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        prompt_part = safe_filename(f"Prompt-{_short_text(prompt, 24)}")
        filename = safe_filename(f"{provider}_{route}_{client}_{model}_{prompt_part}_{ts}.txt")

        export_dir = self.root / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        export_path = export_dir / filename

        request_bytes = _read_bytes(request_body_path)
        response_bytes = _read_bytes(response_body_path)
        query_suffix = f"?{query}" if query else ""
        url = f"{upstream.rstrip('/')}{path}{query_suffix}"
        host = re.sub(r"^https?://", "", upstream).split("/", 1)[0]
        hostname = host.split(":", 1)[0]
        protocol = "HTTPS" if upstream.lower().startswith("https://") else "HTTP"

        payload = [
            {
                "useH2": False,
                "startTime": int(datetime.now().timestamp() * 1000),
                "id": trace_id,
                "url": url,
                "req": {
                    "method": method,
                    "httpVersion": "1.1",
                    "ip": request_headers.get("host", ""),
                    "port": "",
                    "size": len(request_bytes),
                    "body": "",
                    "headers": request_headers,
                    "rawHeaderNames": _raw_header_names(request_headers),
                    "base64": base64.b64encode(request_bytes).decode("ascii"),
                    "rawHeaders": _raw_header_names(request_headers) | request_headers,
                },
                "res": {
                    "ip": hostname,
                    "port": 443 if protocol == "HTTPS" else 80,
                    "rawHeaderNames": _raw_header_names(response_headers),
                    "statusCode": status_code,
                    "statusMessage": "OK" if 200 <= status_code < 400 else "",
                    "headers": response_headers,
                    "size": len(response_bytes),
                    "body": "",
                    "unzipSize": len(response_bytes),
                    "base64": base64.b64encode(response_bytes).decode("ascii"),
                    "rawHeaders": _raw_header_names(response_headers) | response_headers,
                },
                "rules": {},
                "rulesHeaders": {},
                "version": "tapchain",
                "nodeVersion": "",
                "method": method,
                "appName": client,
                "hostIp": hostname,
                "clientIp": "",
                "date": datetime.now().strftime("%Y/%m/%d %H:%M:%S.%f")[:-3],
                "serverPort": 443 if protocol == "HTTPS" else 80,
                "contentEncoding": response_headers.get("content-encoding", ""),
                "body": f"{len(request_bytes)} / {len(response_bytes)}",
                "bodySize": len(request_bytes) + len(response_bytes),
                "result": status_code,
                "type": response_headers.get("content-type", ""),
                "protocol": protocol,
                "hostname": hostname,
                "path": f"{path}{query_suffix}",
                "traceId": trace_id,
                "provider": provider,
                "route": route,
                "model": model,
                "prompt": prompt,
                "completed": completed,
                "filename": path.rsplit("/", 1)[-1] or "request",
                "rawFiles": {
                    "request": str(request_body_path),
                    "response": str(response_body_path),
                },
            }
        ]
        export_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return str(export_path)


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
