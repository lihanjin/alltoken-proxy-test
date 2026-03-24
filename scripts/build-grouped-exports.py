#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from pathlib import Path


ROOT = Path("/Users/leo/code/alltoken-proxy-test")
LOGS = ROOT / "logs"
EVENTS = LOGS / "events.jsonl"
RAW = LOGS / "raw"
OUT = LOGS / "grouped"


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
        for message in messages:
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
                text = "".join(direct_parts).strip() or "".join(fallback_parts).strip()
                if text:
                    return text
    return ""


def extract_model(payload: dict | None) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("model"), str):
        return payload["model"]
    return "unknown-model"


def stage_label(stage: str) -> tuple[str, str]:
    if stage == "client-newapi":
        return "01_客户端到NewAPI", "client_to_newapi"
    if stage == "newapi-cliproxy":
        return "02_NewAPI到CPA", "newapi_to_cpa"
    if stage == "cpa-official":
        return "03_CPA到Claude官方", "cpa_to_official"
    return f"99_{stage}", stage


def copy_if_exists(src: str | Path | None, dest: Path) -> None:
    if not src:
        return
    src_path = Path(src)
    if src_path.exists():
        shutil.copy2(src_path, dest)


def main() -> int:
    events = load_events()
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
        ]

        found_labels: set[str] = set()
        for req, res in sorted(pairs, key=lambda item: item[0].get("ts", "")):
            stage = str(req.get("stage", "unknown"))
            label_cn, short = stage_label(stage)
            found_labels.add(short)
            payload = read_json(req.get("body_path", ""))
            model = extract_model(payload)
            req_http = req.get("http_path")
            res_http = res.get("http_path")

            prefix = f"{label_cn}_{safe_name(model, 64)}"
            copy_if_exists(req_http, folder / f"{prefix}.request.http")
            copy_if_exists(res_http, folder / f"{prefix}.response.http")
            copy_if_exists(req.get("body_path"), folder / f"{prefix}.request.bin")
            copy_if_exists(res.get("body_path"), folder / f"{prefix}.response.bin")

            summary_lines.extend(
                [
                    f"[{label_cn}]",
                    f"trace_id: {req.get('trace_id', '')}",
                    f"model: {model}",
                    f"status: {res.get('status_code', '')}",
                    f"request: {prefix}.request.http",
                    f"response: {prefix}.response.http",
                    "",
                ]
            )

        if "cpa_to_official" not in found_labels:
            summary_lines.extend(
                [
                    "[03_CPA到Claude官方]",
                    "当前未捕获到这一段原始 HTTP 报文。",
                    "现有拓扑只稳定抓到了 客户端=>NewAPI 和 NewAPI=>CPA。",
                    "",
                ]
            )

        (folder / "README.txt").write_text("\n".join(summary_lines), encoding="utf-8")

    print(f"grouped exports written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
