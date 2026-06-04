import json
import logging
import time
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

_SENSITIVE_KEYS = frozenset(
    {
        "signature",
        "api_key",
        "password",
        "secret",
        "token",
        "openrouter_api_key",
        "openrouter_management_key",
    }
)

_COMPACT_BODY_KEYS_BY_PATH = {
    "/validator/update-evaluation-run": (
        "evaluation_run_id",
        "updated_status",
        "verifier_reward",
        "error_code",
    ),
}


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: "[REDACTED]" if k.lower() in _SENSITIVE_KEYS else _redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(item) for item in obj]
    return obj


def _body_for_log(path: str, parsed: Any) -> Any:
    keys = _COMPACT_BODY_KEYS_BY_PATH.get(path)
    if keys and isinstance(parsed, dict):
        return {key: _redact(parsed.get(key)) for key in keys}
    return _redact(parsed)


class RequestInterceptorMiddleware(BaseHTTPMiddleware):
    """This Middleware intercepts incoming requests.

    When the request arrives it logs the request metadata
    and once the request is processed it logs the response
    metadata before returning the response to the caller.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()

        # Read and cache body so downstream handlers can still consume it
        body_bytes = await request.body()

        body_log: str | None = None
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type and body_bytes:
            try:
                parsed = json.loads(body_bytes)
                body_log = json.dumps(_body_for_log(request.url.path, parsed), default=str)
            except json.JSONDecodeError:
                body_log = body_bytes.decode("utf-8", errors="replace")[:500]
        elif "multipart/form-data" in content_type:
            body_log = f"<multipart {len(body_bytes)} bytes>"

        query = dict(request.query_params)

        logger.info(
            f"→ {request.method} {request.url.path}",
            extra={
                "method": request.method,
                "path": request.url.path,
                "query_params": query or None,
                "body": body_log,
            },
        )

        response = await call_next(request)

        duration_ms = round((time.perf_counter() - start) * 1000)
        status = response.status_code

        log_fn = logger.info
        if 400 <= status < 500:
            log_fn = logger.warning
        elif status >= 500:
            log_fn = logger.error

        log_fn(
            f"← {request.method} {request.url.path} {status}",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": status,
                "duration_ms": duration_ms,
            },
        )

        return response
