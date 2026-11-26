from functools import lru_cache

import httpx
from tronenergy_observability.log import request_id_context

from tronenergy_domain.config import settings
from tronenergy_domain.models import Error


@lru_cache
def http():
    return httpx.Client(timeout=30, limits=httpx.Limits(max_connections=30, max_keepalive_connections=10))


def call(base, method, path, data=None, headers=None):
    response = http().request(
        method,
        base + path,
        json=data,
        headers={
            "X-Internal-Token": settings().internal_token.get_secret_value(),
            "X-Request-ID": request_id_context.get() or "",
            **(headers or {}),
        },
    )
    if response.is_error:
        try:
            body = response.json()
        except ValueError:
            body = {}
        raise Error(
            body.get("code", "UPSTREAM_FAILED"),
            body.get("message", "internal service unavailable"),
            response.status_code,
            body.get("retryable", True),
        )
    return response.json()
