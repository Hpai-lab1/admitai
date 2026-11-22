"""Errors as RFC 9457 problem details (PRD 12.3): `application/problem+json` with a user-safe
message and a trace id, on every error this service returns.

- Every request gets a trace id (the caller's `X-Trace-Id` if it sent a sensible one, so one id
  can follow a request across modules), echoed in the `X-Trace-Id` response header.
- `detail` is always one readable sentence. Validation errors add `errors` (field and message,
  never the submitted value, which may be personal data); structured problems such as a Tenant
  Pack's are passed through as extension members (`problems`).
- An unexpected error is logged with its trace id and answered with a generic message: no stack
  trace, SQL or internal name reaches the caller.

Each module keeps its own copy of this file (modules share contracts, not code).
"""

from __future__ import annotations

import logging
import re
import uuid
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("admitai.errors")
MEDIA_TYPE = "application/problem+json"
_TRACE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
TITLES = {
    400: "Bad request",
    401: "Not signed in",
    403: "Not allowed",
    404: "Not found",
    409: "Conflict",
    413: "Too large",
    422: "Invalid request",
    429: "Too many requests",
    500: "Something went wrong",
    502: "A connected service failed",
    503: "Temporarily unavailable",
}
CRASH = (
    "Something went wrong on our side. Please try again; if it keeps happening, "
    "quote the trace id to the team."
)


def _title(status: int) -> str:
    try:
        return TITLES.get(status) or HTTPStatus(status).phrase
    except ValueError:
        return "Error"


def trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", None) or uuid.uuid4().hex


def problem(
    request: Request,
    status: int,
    detail: str,
    headers: dict | None = None,
    **extra,
) -> JSONResponse:
    tid = trace_id(request)
    body = {
        "type": "about:blank",
        "title": _title(status),
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "trace_id": tid,
        **extra,
    }
    return JSONResponse(
        body,
        status_code=status,
        media_type=MEDIA_TYPE,
        headers={**(headers or {}), "X-Trace-Id": tid},
    )


def _field_errors(errors: list) -> list[dict]:
    out = []
    for e in errors:
        loc = [str(x) for x in e.get("loc", ()) if x not in ("body", "query", "path")]
        msg = str(e.get("msg", "invalid")).removeprefix("Value error, ")
        out.append({"field": ".".join(loc) or None, "message": msg})
    return out


class TraceIds:
    """Plain ASGI middleware (it must not buffer Module B's streamed answers)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        sent = (
            dict(scope.get("headers") or []).get(b"x-trace-id", b"").decode("latin-1")
        )
        tid = sent if _TRACE.match(sent) else uuid.uuid4().hex
        scope.setdefault("state", {})["trace_id"] = tid

        async def with_header(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                if not any(k.lower() == b"x-trace-id" for k, _ in headers):
                    headers.append((b"x-trace-id", tid.encode()))
                message["headers"] = headers
            await send(message)

        started = False

        async def tracked(message):
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await with_header(message)

        try:
            await self.app(scope, receive, tracked)
        except Exception:
            # Answered here, inside CORS, so a browser sees the message and not a CORS failure.
            log.exception("unhandled error trace_id=%s path=%s", tid, scope.get("path"))
            if started:
                raise
            body = {
                "type": "about:blank",
                "title": TITLES[500],
                "status": 500,
                "detail": CRASH,
                "instance": scope.get("path"),
                "trace_id": tid,
            }
            await JSONResponse(body, status_code=500, media_type=MEDIA_TYPE)(
                scope, receive, with_header
            )


def install(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        d = exc.detail
        extra: dict = {}
        if isinstance(d, dict):
            extra = {
                k: v for k, v in d.items() if k not in ("type", "status", "trace_id")
            }
            items = d.get("problems") or []
            d = "; ".join(str(p) for p in items[:3]) or _title(exc.status_code)
        elif isinstance(d, list):
            extra = {"errors": d}
            d = _title(exc.status_code)
        return problem(
            request,
            exc.status_code,
            str(d or _title(exc.status_code)),
            exc.headers,
            **extra,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError):
        errors = _field_errors(exc.errors())
        first = errors[0] if errors else {"field": None, "message": "invalid"}
        detail = (
            f"{first['field']}: {first['message']}"
            if first["field"]
            else first["message"]
        )
        return problem(request, 422, detail, errors=errors)

    # Call this after the service's own middleware and before CORS, so the order (outside in) is
    # CORS, trace ids, the rest: errors then carry CORS headers too.
    app.add_middleware(TraceIds)
