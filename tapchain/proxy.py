from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import urljoin
import json
import uuid

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse

from .logging import JsonlLogger, body_preview, normalize_headers, sanitize_hop_headers, utc_now


def _split_host_port(value: str) -> tuple[str, int]:
    host, port_text = value.rsplit(":", 1)
    return host, int(port_text)


def _join_upstream(base: str, request_path: str, query_string: str) -> str:
    base = base.rstrip("/") + "/"
    path = request_path.lstrip("/")
    url = urljoin(base, path)
    if query_string:
        return f"{url}?{query_string}"
    return url


def _decode_header_items(raw_headers: list[tuple[bytes, bytes]]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for key, value in raw_headers:
        items.append(
            (
                key.decode("latin-1", errors="replace"),
                value.decode("latin-1", errors="replace"),
            )
        )
    return items


def _maybe_rewrite_request_body(stage_name: str, body: bytes) -> tuple[bytes, dict[str, str] | None]:
    if stage_name != "client-newapi" or not body:
        return body, None

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return body, None

    if not isinstance(payload, dict):
        return body, None

    model = payload.get("model")
    if model != "claude-haiku-4-5-20251001":
        return body, None

    tools = payload.get("tools")
    if tools != []:
        return body, None

    system = payload.get("system")
    if not isinstance(system, list):
        return body, None

    system_text = "\n".join(
        item.get("text", "")
        for item in system
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    )
    if "Generate a concise, sentence-case title" not in system_text:
        return body, None

    payload["model"] = "claude-sonnet-4-6"
    rewritten = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return rewritten, {
        "reason": "claude_code_title_request",
        "original_model": "claude-haiku-4-5-20251001",
        "rewritten_model": "claude-sonnet-4-6",
    }


@dataclass
class ProxyStage:
    name: str
    listen: str
    upstream: str


class CaptureProxy:
    def __init__(self, stage: ProxyStage, logger: JsonlLogger):
        self.stage = stage
        self.logger = logger
        self.app = Starlette(debug=False)
        self.app.add_route("/__tap/health", self.health, methods=["GET"])
        self.app.add_route("/{path:path}", self.handle, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])

    async def health(self, request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "stage": self.stage.name,
                "listen": self.stage.listen,
                "upstream": self.stage.upstream,
                "ts": utc_now(),
            }
        )

    async def handle(self, request: Request) -> Response:
        trace_id = request.headers.get("x-trace-id") or uuid.uuid4().hex
        request_body = await request.body()
        request_headers = normalize_headers(dict(request.headers))
        request_headers["x-trace-id"] = trace_id

        capture = self.logger.capture_paths(trace_id, self.stage.name)
        request_body_path = capture.body_path("request")
        response_body_path = capture.body_path("response")
        request_http_path = capture.http_path("request")
        response_http_path = capture.http_path("response")
        request_preview = body_preview(request_body)
        request_http_version = request.scope.get("http_version", "1.1")
        request_raw_headers = _decode_header_items(list(request.scope.get("headers", [])))
        request_raw_headers.append(("x-trace-id", trace_id))
        request_start_line = f"{request.method} {request.url.path}{'?' + request.url.query if request.url.query else ''} HTTP/{request_http_version}"

        self.logger.write(
            {
                "event": "request_in",
                "trace_id": trace_id,
                "stage": self.stage.name,
                "listen": self.stage.listen,
                "upstream": self.stage.upstream,
                "method": request.method,
                "path": str(request.url.path),
                "query": request.url.query,
                "headers": request_headers,
                "body": request_preview,
                "body_path": self.logger.write_body(request_body_path, request_body),
                "http_path": self.logger.write_http_message(
                    request_http_path,
                    request_start_line,
                    request_raw_headers,
                    request_body,
                ),
                "source": {"client": request.client.host if request.client else None, "port": request.client.port if request.client else None},
            }
        )

        upstream_url = _join_upstream(self.stage.upstream, request.url.path, request.url.query)
        upstream_headers = sanitize_hop_headers(dict(request.headers))
        upstream_headers["x-trace-id"] = trace_id
        upstream_body, rewrite_info = _maybe_rewrite_request_body(self.stage.name, request_body)
        if rewrite_info is not None:
            upstream_headers.pop("content-length", None)
            self.logger.write(
                {
                    "event": "request_rewrite",
                    "trace_id": trace_id,
                    "stage": self.stage.name,
                    **rewrite_info,
                }
            )
        else:
            upstream_body = request_body

        timeout = httpx.Timeout(None)
        client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        try:
            upstream_request = client.build_request(
                method=request.method,
                url=upstream_url,
                headers=upstream_headers,
                content=upstream_body,
            )
            upstream_response = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            error_body = f"upstream error in stage {self.stage.name}: {exc}".encode("utf-8", errors="replace")
            response_headers = {"content-type": "text/plain; charset=utf-8", "x-trace-id": trace_id}
            response_raw_headers = [
                ("content-type", "text/plain; charset=utf-8"),
                ("x-trace-id", trace_id),
            ]
            response_start_line = "HTTP/1.1 502 Bad Gateway"
            self.logger.write_body(response_body_path, error_body)
            self.logger.write_http_message(
                response_http_path,
                response_start_line,
                response_raw_headers,
                error_body,
            )
            await client.aclose()
            self.logger.write(
                {
                    "event": "request_error",
                    "trace_id": trace_id,
                    "stage": self.stage.name,
                    "upstream": self.stage.upstream,
                    "error": repr(exc),
                    "status_code": 502,
                    "body_path": str(response_body_path),
                    "http_path": str(response_http_path),
                }
            )
            self.logger.write_pretty_export(
                trace_id=trace_id,
                stage=self.stage.name,
                upstream=self.stage.upstream,
                method=request.method,
                path=str(request.url.path),
                query=request.url.query,
                request_headers=request_headers,
                request_body_path=request_body_path,
                response_headers=response_headers,
                response_body_path=response_body_path,
                status_code=502,
                completed=True,
            )
            return PlainTextResponse(
                error_body.decode("utf-8", errors="replace"),
                status_code=502,
                headers=response_headers,
            )

        response_headers = normalize_headers(dict(upstream_response.headers))
        response_headers = {
            key: value
            for key, value in response_headers.items()
            if key.lower()
            not in {
                "connection",
                "keep-alive",
                "proxy-authenticate",
                "proxy-authorization",
                "te",
                "trailer",
                "transfer-encoding",
                "upgrade",
                "content-length",
            }
        }
        response_headers["x-trace-id"] = trace_id
        response_raw_headers = _decode_header_items(list(getattr(upstream_response.headers, "raw", [])))
        response_raw_headers = [
            (key, value)
            for key, value in response_raw_headers
            if key.lower()
            not in {
                "connection",
                "keep-alive",
                "proxy-authenticate",
                "proxy-authorization",
                "te",
                "trailer",
                "transfer-encoding",
                "upgrade",
                "content-length",
            }
        ]
        response_raw_headers.append(("x-trace-id", trace_id))
        try:
            reason = HTTPStatus(upstream_response.status_code).phrase
        except ValueError:
            reason = ""
        response_start_line = f"HTTP/1.1 {upstream_response.status_code} {reason}".rstrip()

        response_meta = {
            "event": "response_out",
            "trace_id": trace_id,
            "stage": self.stage.name,
            "upstream": self.stage.upstream,
            "status_code": upstream_response.status_code,
            "headers": response_headers,
            "body_path": str(response_body_path),
        }

        response_file = response_body_path.open("wb")
        bytes_written = 0
        completed = False
        is_event_stream = upstream_response.headers.get("content-type", "").lower().startswith("text/event-stream")

        async def iterator():
            nonlocal bytes_written, completed
            try:
                async for chunk in upstream_response.aiter_raw():
                    bytes_written += len(chunk)
                    response_file.write(chunk)
                    yield chunk
                completed = True
            except httpx.ReadError as exc:
                if is_event_stream and bytes_written > 0:
                    completed = True
                self.logger.write(
                    {
                        "event": "response_stream_error",
                        "trace_id": trace_id,
                        "stage": self.stage.name,
                        "upstream": self.stage.upstream,
                        "error": repr(exc),
                        "bytes_written": bytes_written,
                        "ignored": True,
                    }
                )
            except Exception as exc:
                self.logger.write(
                    {
                        "event": "response_stream_error",
                        "trace_id": trace_id,
                        "stage": self.stage.name,
                        "upstream": self.stage.upstream,
                        "error": repr(exc),
                        "bytes_written": bytes_written,
                    }
                )
                raise
            finally:
                response_file.close()
                self.logger.write(
                    {
                        **response_meta,
                        "bytes_written": bytes_written,
                        "completed": completed,
                        "http_path": self.logger.write_http_message(
                            response_http_path,
                            response_start_line,
                            response_raw_headers,
                            response_body_path.read_bytes(),
                        ),
                    }
                )
                self.logger.write_pretty_export(
                    trace_id=trace_id,
                    stage=self.stage.name,
                    upstream=self.stage.upstream,
                    method=request.method,
                    path=str(request.url.path),
                    query=request.url.query,
                    request_headers=request_headers,
                    request_body_path=request_body_path,
                    response_headers=response_headers,
                    response_body_path=response_body_path,
                    status_code=upstream_response.status_code,
                    completed=completed,
                )
                await upstream_response.aclose()
                await client.aclose()

        return StreamingResponse(
            iterator(),
            status_code=upstream_response.status_code,
            headers=response_headers,
        )


def parse_listen(value: str) -> tuple[str, int]:
    return _split_host_port(value)
