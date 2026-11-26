import hashlib
import hmac
import time
from functools import lru_cache

import redis
from fastapi import Header, Request
from fastapi.responses import JSONResponse
from tronenergy_domain.client import call
from tronenergy_domain.config import settings
from tronenergy_domain.models import Error
from tronenergy_domain.web import app

app = app("api-service")


@lru_cache
def cache():
    return redis.Redis.from_url(settings().redis_url, socket_connect_timeout=1, socket_timeout=1)


def authenticate(key):
    cfg = settings()
    for known, tenant in cfg.api_keys.items():
        if hmac.compare_digest(known, key):
            return tenant, False
    for known in cfg.admin_keys:
        if hmac.compare_digest(known, key):
            return "admin:" + hashlib.sha256(key.encode()).hexdigest()[:12], True
    raise Error("UNAUTHORIZED", "invalid API key", 401)


@app.api_route("/v1/{path:path}", methods=["GET", "POST"])
async def gateway(
    path: str,
    request: Request,
    x_api_key: str = Header(default=""),
    idempotency_key: str = Header(default=""),
):
    tenant, admin = authenticate(x_api_key)
    if path.startswith("admin/") and not admin:
        raise Error("FORBIDDEN", "administrator role required", 403)
    # Atomic counter with expiry; Redis failure closes the public ingress, not background workers.
    bucket = hashlib.sha256(x_api_key.encode()).hexdigest() + ":" + str(int(time.time()) // 60)
    from starlette.concurrency import run_in_threadpool

    try:
        count = await run_in_threadpool(
            cache().eval,
            "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],120) end; return n",
            1,
            "rate:" + bucket,
        )
    except redis.RedisError as exc:
        raise Error("RATE_LIMIT_UNAVAILABLE", "public ingress temporarily unavailable", 503, True) from exc
    if count > settings().rate_per_minute:
        raise Error("RATE_LIMITED", "request rate exceeded", 429, True)
    data = None
    if request.method == "POST":
        if len(await request.body()) > 16_384:
            raise Error("BODY_TOO_LARGE", "request exceeds 16 KiB", 413)
        try:
            data = await request.json()
        except ValueError as exc:
            raise Error("INVALID_JSON", "JSON body required", 422) from exc
    result = await run_in_threadpool(
        call,
        settings().rental_url,
        request.method,
        "/v1/" + path + ("?" + request.url.query if request.url.query else ""),
        data,
        {
            "X-Tenant": tenant,
            "X-Admin": "1" if admin else "0",
            "Idempotency-Key": idempotency_key,
            "X-Request-ID": request.state.request_id,
        },
    )
    return JSONResponse(
        result, status_code=202 if request.method == "POST" and path.startswith("orders") else 200
    )
