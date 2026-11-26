from datetime import datetime, timedelta

from fastapi import Depends, Header, Query
from psycopg.errors import UniqueViolation
from tronenergy_domain.client import call
from tronenergy_domain.config import settings
from tronenergy_domain.models import (
    Approval,
    Error,
    OrderInput,
    PoolInput,
    QuoteInput,
    Reason,
    TransferInput,
    ceil_div,
    now,
)
from tronenergy_domain.web import app, internal
from tronenergy_storage.db import audit, one, transaction

from tronenergy_rental.service import apply_result, approve, cancel, create_order, idem, identifier

app = app("rental-worker")
app.router.dependencies.append(Depends(internal))


def identity(x_tenant: str = Header(default=""), x_admin: str = Header(default="0")):
    if not x_tenant:
        raise Error("UNAUTHORIZED", "tenant identity required", 401)
    return x_tenant, x_admin == "1"


def admin(user=Depends(identity)):
    if not user[1]:
        raise Error("FORBIDDEN", "administrator role required", 403)
    return user[0]


def chain(method, path, data=None):
    return call(settings().chain_url, method, "/internal/" + path, data)


@app.get("/v1/accounts/{address}/resources")
def resources(address: str, user=Depends(identity)):
    return chain("GET", "accounts/" + address)


@app.post("/v1/estimates/transfers")
def estimate(data: TransferInput, user=Depends(identity)):
    return chain("POST", "estimate", data.model_dump())


@app.post("/v1/quotes")
def quote(data: QuoteInput, user=Depends(identity), idempotency_key: str = Header(default="")):
    cfg = settings()
    if data.duration_seconds not in cfg.duration_options:
        raise Error("INVALID_DURATION", "unsupported rental duration", 422)
    estimate = chain("POST", "quote", data.model_dump())
    if estimate["stake_sun"] > cfg.max_stake_sun:
        raise Error("ORDER_LIMIT", "resource amount exceeds pool policy", 422)
    with transaction() as conn:

        def write():
            qid = identifier()
            price = ceil_div(
                data.target_units * data.duration_seconds * cfg.price_per_million_unit_hour_sun,
                1_000_000 * 3600,
            )
            expires = now() + timedelta(seconds=cfg.quote_ttl_seconds)
            conn.execute(
                """INSERT INTO quotes(id,tenant_id,resource_type,target_units,stake_sun,
                            duration_seconds,price_sun,price_version,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    qid,
                    user[0],
                    data.resource_type,
                    data.target_units,
                    estimate["stake_sun"],
                    data.duration_seconds,
                    price,
                    str(cfg.price_per_million_unit_hour_sun),
                    expires,
                ),
            )
            audit(conn, user[0], "QUOTE", qid)
            return {
                "quote_id": qid,
                "price_sun": price,
                "stake_sun": estimate["stake_sun"],
                "expires_at": expires.isoformat(),
            }

        return idem(conn, user[0], "quotes", idempotency_key, data.model_dump(), write)


@app.post("/v1/orders", status_code=202)
def create(data: OrderInput, user=Depends(identity), idempotency_key: str = Header(default="")):
    account = chain("GET", "accounts/" + data.receiver_address)["account"]
    if not account.get("address") or account.get("type") == "Contract":
        raise Error("INVALID_RECEIVER", "receiver must be an activated external account", 422)
    with transaction() as conn:
        return idem(
            conn,
            user[0],
            "orders",
            idempotency_key,
            data.model_dump(),
            lambda: create_order(conn, user[0], data),
        )


@app.get("/v1/orders/{oid}")
def get_order(oid: str, user=Depends(identity)):
    with transaction() as conn:
        order = one(conn, "SELECT * FROM orders WHERE id=%s AND tenant_id=%s", (oid, user[0]))
        if not order:
            raise Error("NOT_FOUND", "order not found", 404)
        order["transactions"] = conn.execute(
            "SELECT action,status,txid FROM chain_operations WHERE order_id=%s", (oid,)
        ).fetchall()
        return order


@app.get("/v1/orders")
def list_orders(
    user=Depends(identity),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
):
    with transaction() as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE tenant_id=%s ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (user[0], limit, offset),
        ).fetchall()


@app.get("/v1/admin/audit")
def audit_history(
    actor=Depends(admin),
    target: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
):
    with transaction() as conn:
        return conn.execute(
            "SELECT * FROM audit_events WHERE target=%s ORDER BY event_id DESC LIMIT %s", (target, limit)
        ).fetchall()


@app.get("/v1/admin/metrics")
def metrics(actor=Depends(admin)):
    with transaction() as conn:
        return {
            "orders": conn.execute("SELECT status,count(*) AS count FROM orders GROUP BY status").fetchall(),
            "tasks": conn.execute(
                "SELECT status,count(*) AS count FROM outbox_tasks GROUP BY status"
            ).fetchall(),
            "overdue_orders": one(
                conn,
                "SELECT count(*) AS count FROM orders WHERE expires_at<now() AND status NOT IN ('COMPLETED','FAILED','CANCELLED')",
            )["count"],
            "oldest_task_seconds": one(
                conn,
                "SELECT COALESCE(max(extract(epoch FROM now()-available_at)),0) AS age FROM outbox_tasks WHERE status IN ('PENDING','BLOCKED')",
            )["age"],
        }


@app.post("/v1/orders/{oid}/cancel")
def cancel_order(oid: str, data: Reason, user=Depends(identity), idempotency_key: str = Header(default="")):
    with transaction() as conn:
        return idem(
            conn,
            user[0],
            "cancel/" + oid,
            idempotency_key,
            data.model_dump(),
            lambda: cancel(conn, oid, user[0], data.reason),
        )


@app.post("/v1/admin/orders/{oid}/approve")
def approve_order(oid: str, data: Approval, actor=Depends(admin), idempotency_key: str = Header(default="")):
    with transaction() as conn:
        order = one(conn, "SELECT resource_type FROM orders WHERE id=%s", (oid,))
    if not order:
        raise Error("NOT_FOUND", "order not found", 404)
    snapshots = chain("GET", "capacity/" + order["resource_type"])
    for snap in snapshots:
        snap["observed_at"] = datetime.fromisoformat(snap["observed_at"])
    try:
        with transaction() as conn:
            return idem(
                conn,
                actor,
                "approve/" + oid,
                idempotency_key,
                data.model_dump(),
                lambda: approve(conn, oid, actor, data, snapshots),
            )
    except UniqueViolation as exc:
        raise Error("RESOURCE_CONFLICT", "another order owns this delegation") from exc


@app.post("/v1/admin/pools")
def add_pool(data: PoolInput, actor=Depends(admin), idempotency_key: str = Header(default="")):
    account = chain("GET", "accounts/" + data.address)["account"]
    if not account.get("address") or account.get("type") == "Contract":
        raise Error("INVALID_POOL", "pool must be an activated external account", 422)
    with transaction() as conn:

        def write():
            existing = one(conn, "SELECT id FROM pools WHERE address=%s", (data.address,))
            pid = existing["id"] if existing else identifier()
            conn.execute(
                "INSERT INTO pools(id,address) VALUES (%s,%s) ON CONFLICT(address) DO NOTHING",
                (pid, data.address),
            )
            audit(conn, actor, "ADD_POOL", pid)
            return {"pool_id": pid}

        return idem(conn, actor, "pools", idempotency_key, data.model_dump(), write)


@app.get("/v1/admin/pools")
def pools(actor=Depends(admin)):
    with transaction() as conn:
        return conn.execute("""SELECT p.*,s.resource_type,s.max_delegatable_sun,s.observed_at
                               FROM pools p LEFT JOIN resource_snapshots s ON s.pool_id=p.id ORDER BY p.id""").fetchall()


@app.get("/v1/admin/orders/{oid}")
def admin_order(oid: str, actor=Depends(admin)):
    with transaction() as conn:
        row = one(conn, "SELECT * FROM orders WHERE id=%s", (oid,))
        if not row:
            raise Error("NOT_FOUND", "order not found", 404)
        return row


@app.post("/v1/admin/pools/{pid}/{action}")
def pause(
    pid: str, action: str, data: Reason, actor=Depends(admin), idempotency_key: str = Header(default="")
):
    if action not in ("pause", "resume"):
        raise Error("NOT_FOUND", "unknown action", 404)
    with transaction() as conn:

        def write():
            row = one(
                conn,
                "UPDATE pools SET paused=%s,version=version+1 WHERE id=%s RETURNING id",
                (action == "pause", pid),
            )
            if not row:
                raise Error("NOT_FOUND", "pool not found", 404)
            audit(conn, actor, action.upper(), pid, data.model_dump())
            return {"pool_id": pid, "paused": action == "pause"}

        return idem(conn, actor, action + "/" + pid, idempotency_key, data.model_dump(), write)


@app.post("/v1/admin/orders/{oid}/retry")
def retry(oid: str, data: Reason, actor=Depends(admin), idempotency_key: str = Header(default="")):
    with transaction() as conn:

        def write():
            # Retry only existing work. Never erase a txid or mint a new transaction attempt.
            row = one(
                conn,
                """UPDATE outbox_tasks SET status='PENDING',available_at=now(),owner=NULL,
                               lease_until=NULL,claim_version=claim_version+1,attempts=0
                               WHERE order_id=%s AND status='BLOCKED' RETURNING id""",
                (oid,),
            )
            if not row:
                raise Error("NOT_RETRYABLE", "no blocked task; reconcile unknown transactions first")
            audit(conn, actor, "RETRY", oid, data.model_dump())
            return {"order_id": oid, "status": "RETRY_QUEUED"}

        return idem(conn, actor, "retry/" + oid, idempotency_key, data.model_dump(), write)


@app.post("/v1/admin/orders/{oid}/refund")
def refund(oid: str, data: Approval, actor=Depends(admin), idempotency_key: str = Header(default="")):
    with transaction() as conn:

        def write():
            row = one(
                conn,
                """UPDATE orders SET refund_status='MANUALLY_REFUNDED'
                               WHERE id=%s AND refund_status='PENDING_MANUAL' RETURNING id""",
                (oid,),
            )
            if not row:
                raise Error("INVALID_STATE", "order is not awaiting manual refund")
            audit(conn, actor, "MANUAL_REFUND_RECORDED", oid, data.model_dump())
            return {"order_id": oid, "refund_status": "MANUALLY_REFUNDED"}

        return idem(conn, actor, "refund/" + oid, idempotency_key, data.model_dump(), write)


@app.post("/internal/result/{oid}/{action}")
def result(oid: str, action: str):
    # Do not trust a supplied result body: query the chain service's persisted observation.
    observation = chain("GET", "observe/" + oid + ":" + action)
    apply_result(oid, action, observation)
    return {"status": "accepted"}
