import hmac
import logging
import time
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from tronenergy_observability.log import configure, request_id_context

from tronenergy_domain.config import settings
from tronenergy_domain.models import Error


def internal(x_internal_token: str = Header(default="")):
    expected = settings().internal_token.get_secret_value()
    if not expected or not hmac.compare_digest(expected, x_internal_token):
        raise Error("UNAUTHORIZED", "invalid service credentials", 401)


def app(name):
    configure(settings().log_level)
    application = FastAPI(title=name, version="0.1.0")
    log = logging.getLogger(name)

    @application.middleware("http")
    async def correlation(request: Request, call_next):
        request.state.request_id = str(uuid4())
        if name != "api-service":
            try:
                request.state.request_id = str(UUID(request.headers.get("X-Request-ID", "")))
            except ValueError:
                pass
        token = request_id_context.set(request.state.request_id)
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception as exc:
            log.error(
                "request_failed",
                extra={"error_type": type(exc).__name__, "request_id": request.state.request_id},
            )
            response = JSONResponse(
                status_code=503,
                content={
                    "code": "DEPENDENCY_UNAVAILABLE",
                    "message": "service temporarily unavailable",
                    "retryable": True,
                    "request_id": request.state.request_id,
                },
            )
        response.headers["X-Request-ID"] = request.state.request_id
        log.info(
            "request_completed",
            extra={
                "request_id": request.state.request_id,
                "duration_ms": int((time.monotonic() - start) * 1000),
                "status_code": response.status_code,
            },
        )
        request_id_context.reset(token)
        return response

    @application.exception_handler(Error)
    async def business_error(request, exc):
        return JSONResponse(
            status_code=exc.status,
            content={
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
                "request_id": request.state.request_id,
            },
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's raw errors may contain secret input; expose only locations and messages.
        return JSONResponse(
            status_code=422,
            content={
                "code": "INVALID_INPUT",
                "message": "invalid request",
                "retryable": False,
                "request_id": request.state.request_id,
                "errors": [{"loc": list(e["loc"]), "message": e["msg"]} for e in exc.errors()],
            },
        )

    @application.get("/health/live")
    def live():
        return {"status": "alive"}

    @application.get("/health/ready")
    def ready():
        from tronenergy_storage.db import transaction

        with transaction() as conn:
            conn.execute("SELECT 1")
        return {"status": "ready"}

    return application
