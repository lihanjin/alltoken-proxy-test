from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin
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
        request_preview = body_preview(request_body)

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
                "source": {"client": request.client.host if request.client else None, "port": request.client.port if request.client else None},
            }
        )

        upstream_url = _join_upstream(self.stage.upstream, request.url.path, request.url.query)
        upstream_headers = sanitize_hop_headers(dict(request.headers))
        upstream_headers["x-trace-id"] = trace_id

        timeout = httpx.Timeout(None)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            try:
                upstream_request = client.build_request(
                    method=request.method,
                    url=upstream_url,
                    headers=upstream_headers,
                    content=request_body,
                )
                upstream_response = await client.send(upstream_request, stream=True)
            except httpx.HTTPError as exc:
                self.logger.write(
                    {
                        "event": "request_error",
                        "trace_id": trace_id,
                        "stage": self.stage.name,
                        "upstream": self.stage.upstream,
                        "error": repr(exc),
                    }
                )
                return PlainTextResponse(
                    f"upstream error in stage {self.stage.name}: {exc}",
                    status_code=502,
                    headers={"x-trace-id": trace_id},
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

            async def iterator():
                nonlocal bytes_written, completed
                try:
                    async for chunk in upstream_response.aiter_raw():
                        bytes_written += len(chunk)
                        response_file.write(chunk)
                        yield chunk
                    completed = True
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
                        }
                    )
                    await upstream_response.aclose()

            return StreamingResponse(
                iterator(),
                status_code=upstream_response.status_code,
                headers=response_headers,
            )


def parse_listen(value: str) -> tuple[str, int]:
    return _split_host_port(value)
