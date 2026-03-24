#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path("/Users/leo/code/alltoken-proxy-test")
LOGS = ROOT / "logs"
EVENTS = LOGS / "events.jsonl"
RAW = LOGS / "raw"
OUT = LOGS / "grouped"
CPA_LOGS = Path("/Users/leo/code/CLIProxyAPIPlus/logs")


def safe_name(value: str, limit: int = 48) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    if not compact:
        compact = "NoPrompt"
    compact = compact[:limit]
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in compact)


def load_events() -> list[dict]:
    rows: list[dict] = []
    if not EVENTS.exists():
        return rows
    for line in EVENTS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def read_json(path: str | Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def extract_prompt(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                direct_parts: list[str] = []
                fallback_parts: list[str] = []
                for item in content:
                    if not (isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)):
                        continue
                    text = item["text"].strip()
                    if not text:
                        continue
                    fallback_parts.append(text)
                    if not text.startswith("<system-reminder>"):
                        direct_parts.append(text)
                if direct_parts:
                    return direct_parts[-1]
                if fallback_parts:
                    return fallback_parts[-1]
    return ""


def extract_model(payload: dict | None) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("model"), str):
        return payload["model"]
    return "unknown-model"


def stage_label(stage: str) -> tuple[str, str]:
    if stage == "client-newapi":
        return "客户端到NewAPI", "client_to_newapi"
    if stage == "newapi-cliproxy":
        return "NewAPI到CPA", "newapi_to_cpa"
    if stage == "cpa-official":
        return "CPA到Claude官方", "cpa_to_official"
    return stage, stage


def copy_if_exists(src: str | Path | None, dest: Path) -> None:
    if not src:
        return
    src_path = Path(src)
    if src_path.exists():
        shutil.copy2(src_path, dest)


def read_text(path: str | Path | None) -> str:
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def extract_trace_id_from_http(http_text: str) -> str:
    match = re.search(r"(?im)^x-trace-id:\s*([a-f0-9]+)\s*$", http_text)
    return match.group(1).strip() if match else ""


def iter_cpa_logs() -> list[Path]:
    if not CPA_LOGS.exists():
        return []
    return sorted(CPA_LOGS.glob("*v1-messages-*.log"))


def parse_headers_block(block: str) -> list[tuple[str, str]]:
    headers: list[tuple[str, str]] = []
    for line in block.splitlines():
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers.append((name.strip(), value.strip()))
    return headers


def parse_cpa_log(path: Path) -> dict | None:
    text = read_text(path)
    if not text:
        return None

    def section(name: str, next_names: list[str]) -> str:
        pattern = rf"(?s)^=== {re.escape(name)} ===\n(.*?)(?=^=== (?:{'|'.join(re.escape(n) for n in next_names)}) ===\n|\Z)"
        match = re.search(pattern, text, flags=re.MULTILINE)
        return match.group(1).strip("\n") if match else ""

    request_info = section("REQUEST INFO", ["HEADERS", "API REQUEST 1", "API RESPONSE", "RESPONSE"])
    request_headers = section("HEADERS", ["REQUEST BODY", "API REQUEST 1", "API RESPONSE", "RESPONSE"])
    request_body = section("REQUEST BODY", ["API REQUEST 1", "API RESPONSE", "RESPONSE"])
    api_request = section("API REQUEST 1", ["API RESPONSE 1", "RESPONSE"])
    api_response = section("API RESPONSE 1", ["RESPONSE"])

    trace_match = re.search(r"(?im)^X-Trace-Id:\s*([a-f0-9]+)\s*$", request_headers)
    ts_match = re.search(r"(?m)^Timestamp:\s*(.+)$", request_info)
    if not trace_match:
        return None

    upstream_url_match = re.search(r"(?m)^Upstream URL:\s*(.+)$", api_request)
    method_match = re.search(r"(?m)^HTTP Method:\s*(.+)$", api_request)
    api_request_headers_block = ""
    api_request_body = ""
    api_response_headers_block = ""
    api_response_body = ""
    api_response_status = ""
    api_response_ts = ""

    if "\nHeaders:\n" in api_request:
        _, rest = api_request.split("\nHeaders:\n", 1)
        if "\n\nBody:\n" in rest:
            api_request_headers_block, api_request_body = rest.split("\n\nBody:\n", 1)
        else:
            api_request_headers_block = rest

    if "\nStatus:" in api_response or api_response.startswith("Timestamp:"):
        api_response_ts_match = re.search(r"(?m)^Timestamp:\s*(.+)$", api_response)
        if api_response_ts_match:
            api_response_ts = api_response_ts_match.group(1).strip()
        status_match = re.search(r"(?m)^Status:\s*(.+)$", api_response)
        if status_match:
            api_response_status = status_match.group(1).strip()
        if "\nHeaders:\n" in api_response:
            _, rest = api_response.split("\nHeaders:\n", 1)
            if "\n\nBody:\n" in rest:
                api_response_headers_block, api_response_body = rest.split("\n\nBody:\n", 1)
            else:
                api_response_headers_block = rest

    upstream_url = upstream_url_match.group(1).strip() if upstream_url_match else ""
    method = method_match.group(1).strip() if method_match else "POST"
    request_target = "/"
    if upstream_url:
        parts = urlsplit(upstream_url)
        request_target = parts.path or "/"
        if parts.query:
            request_target += f"?{parts.query}"

    response_status_code = api_response_status.split()[0] if api_response_status else "200"
    response_reason = " ".join(api_response_status.split()[1:]) if len(api_response_status.split()) > 1 else "OK"

    request_http_lines = [f"{method} {request_target} HTTP/1.1"]
    for name, value in parse_headers_block(api_request_headers_block):
        request_http_lines.append(f"{name}: {value}")
    request_http = "\n".join(request_http_lines) + "\n\n" + api_request_body.strip() + "\n"

    response_http_lines = [f"HTTP/1.1 {response_status_code} {response_reason}".rstrip()]
    for name, value in parse_headers_block(api_response_headers_block):
        response_http_lines.append(f"{name}: {value}")
    response_http = "\n".join(response_http_lines) + "\n\n" + api_response_body.strip() + "\n"

    return {
        "path": path,
        "trace_id": trace_match.group(1).strip(),
        "request_ts": ts_match.group(1).strip() if ts_match else "",
        "response_ts": api_response_ts,
        "request_http": request_http,
        "response_http": response_http,
        "request_body": api_request_body.strip() or request_body.strip(),
        "response_body": api_response_body.strip(),
        "upstream_url": upstream_url,
    }


def build_cpa_trace_index() -> dict[str, list[dict]]:
    indexed: dict[str, list[dict]] = defaultdict(list)
    for path in iter_cpa_logs():
        item = parse_cpa_log(path)
        if not item:
            continue
        indexed[item["trace_id"]].append(item)
    for items in indexed.values():
        items.sort(key=lambda item: (item["request_ts"], str(item["path"])))
    return indexed


def main() -> int:
    events = load_events()
    cpa_by_trace = build_cpa_trace_index()
    request_by_trace: dict[str, dict] = {}
    response_by_trace: dict[str, dict] = {}

    for row in events:
        trace_id = row.get("trace_id")
        if not isinstance(trace_id, str):
            continue
        if row.get("event") == "request_in":
            request_by_trace[trace_id] = row
        elif row.get("event") == "response_out":
            response_by_trace[trace_id] = row

    groups: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for trace_id, req in request_by_trace.items():
        res = response_by_trace.get(trace_id)
        if not res:
            continue
        payload = read_json(req.get("body_path", ""))
        prompt = extract_prompt(payload)
        key = prompt or "_system_reminder"
        groups[key].append((req, res))

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    for prompt, pairs in groups.items():
        folder_name = f"ClaudeCode_Prompt-{safe_name(prompt)}"
        folder = OUT / folder_name
        folder.mkdir(parents=True, exist_ok=True)

        summary_lines = [
            f"Prompt: {prompt or '(empty)'}",
            f"Request count: {len(pairs)}",
            "",
            "Flow:",
            "",
        ]

        flow_index = 1
        found_labels: set[str] = set()
        for req, res in sorted(pairs, key=lambda item: item[0].get("ts", "")):
            stage = str(req.get("stage", "unknown"))
            label_cn, short = stage_label(stage)
            found_labels.add(short)
            payload = read_json(req.get("body_path", ""))
            model = extract_model(payload)
            req_http = req.get("http_path")
            res_http = res.get("http_path")
            trace_hint = extract_trace_id_from_http(read_text(req_http)) if stage == "newapi-cliproxy" else ""

            prefix = f"{flow_index:02d}_{label_cn}_{safe_name(model, 64)}"
            copy_if_exists(req_http, folder / f"{prefix}.request.http")
            copy_if_exists(res_http, folder / f"{prefix}.response.http")
            copy_if_exists(req.get("body_path"), folder / f"{prefix}.request.bin")
            copy_if_exists(res.get("body_path"), folder / f"{prefix}.response.bin")

            summary_lines.extend(
                [
                    f"[{flow_index:02d}] {label_cn}",
                    f"time: {req.get('ts', '')}",
                    f"trace_id: {req.get('trace_id', '')}",
                    f"model: {model}",
                    f"status: {res.get('status_code', '')}",
                    f"request: {prefix}.request.http",
                    f"response: {prefix}.response.http",
                    "",
                ]
            )
            flow_index += 1

            if stage == "newapi-cliproxy" and trace_hint and trace_hint in cpa_by_trace:
                for cpa_item in cpa_by_trace[trace_hint]:
                    cpa_label_cn, _ = stage_label("cpa-official")
                    cpa_prefix = f"{flow_index:02d}_{cpa_label_cn}_{safe_name(model, 64)}"
                    (folder / f"{cpa_prefix}.request.http").write_text(cpa_item["request_http"], encoding="utf-8")
                    (folder / f"{cpa_prefix}.response.http").write_text(cpa_item["response_http"], encoding="utf-8")
                    if cpa_item["request_body"]:
                        (folder / f"{cpa_prefix}.request.bin").write_text(cpa_item["request_body"], encoding="utf-8")
                    if cpa_item["response_body"]:
                        (folder / f"{cpa_prefix}.response.bin").write_text(cpa_item["response_body"], encoding="utf-8")

                    summary_lines.extend(
                        [
                            f"[{flow_index:02d}] {cpa_label_cn}",
                            f"time: {cpa_item.get('request_ts', '')}",
                            f"trace_id: {trace_hint}",
                            f"model: {model}",
                            f"upstream: {cpa_item.get('upstream_url', '')}",
                            f"request: {cpa_prefix}.request.http",
                            f"response: {cpa_prefix}.response.http",
                            "",
                        ]
                    )
                    found_labels.add("cpa_to_official")
                    flow_index += 1

        if "cpa_to_official" not in found_labels:
            summary_lines.extend(
                [
                    "[CPA到Claude官方]",
                    "当前未捕获到这一段原始 HTTP 报文。",
                    "请先确认 CLIProxyAPIPlus 已开启 request-log: true，且本次请求确实经过 CPA。",
                    "",
                ]
            )

        (folder / "README.txt").write_text("\n".join(summary_lines), encoding="utf-8")

    print(f"grouped exports written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
