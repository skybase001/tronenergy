import hashlib
import json
import logging
from datetime import timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb
from tronenergy_domain.models import Error, now
from tronenergy_storage.db import audit, one, transaction


def identifier():
    return str(uuid4())


def idem(conn, tenant, route, key, body, action):
    if not key or len(key) > 128:
        raise Error("INVALID_IDEMPOTENCY_KEY", "provide an Idempotency-Key up to 128 characters", 422)
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    # Serialize the first insert too; a unique constraint alone cannot return the winning response.
    lock = int.from_bytes(hashlib.sha256(f"{tenant}:{route}:{key}".encode()).digest()[:8], "big", signed=True)
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
    existing = one(
        conn,
        "SELECT * FROM idempotency_keys WHERE tenant_id=%s AND route=%s AND key=%s",
        (tenant, route, key),
    )
    if existing:
        if existing["request_hash"] != digest:
            raise Error("IDEMPOTENCY_CONFLICT", "key already used with another request")
        return existing["response"]
    result = action()
    # Store a JSON-safe response so retries return exactly the original payload.
    result = json.loads(json.dumps(result, default=str))
    conn.execute(
        "INSERT INTO idempotency_keys VALUES (%s,%s,%s,%s,%s,now())",
        (tenant, route, key, digest, Jsonb(result)),
    )
    return result


def transition(conn, order, status, actor, details=None):
    version = order["version"] + 1
    conn.execute("UPDATE orders SET status=%s,version=%s WHERE id=%s", (status, version, order["id"]))
    conn.execute(
        "INSERT INTO order_events(order_id,version,type,payload) VALUES (%s,%s,%s,%s)",
        (order["id"], version, status, Jsonb(details or {})),
    )
    audit(conn, actor, status, order["id"], details)
    order.update(status=status, version=version)
    # Intent log; the audit row commits atomically with the order.
    logging.getLogger("rental-worker").info(
        "order_transition", extra={"order_id": order["id"], "state": status}
    )


def order_for(conn, order_id, tenant=None):
    row = one(conn, "SELECT * FROM orders WHERE id=%s FOR UPDATE", (order_id,))
    if not row or (tenant is not None and row["tenant_id"] != tenant):
        raise Error("NOT_FOUND", "order not found", 404)
    return row


def create_order(conn, tenant, data):
    quote = one(conn, "SELECT * FROM quotes WHERE id=%s AND tenant_id=%s", (data.quote_id, tenant))
    if not quote:
        raise Error("NOT_FOUND", "quote not found", 404)
    if quote["expires_at"] <= now():
        raise Error("QUOTE_EXPIRED", "request a new quote")
    oid = identifier()
    conn.execute(
        """INSERT INTO orders(id,tenant_id,quote_id,receiver,resource_type,stake_sun,
                    duration_seconds,price_sun,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'PENDING_APPROVAL')""",
        (
            oid,
            tenant,
            quote["id"],
            data.receiver_address,
            quote["resource_type"],
            quote["stake_sun"],
            quote["duration_seconds"],
            quote["price_sun"],
        ),
    )
    audit(conn, tenant, "CREATE_ORDER", oid)
    return {"order_id": oid, "status": "PENDING_APPROVAL"}


def approve(conn, oid, actor, data, snapshots):
    order = order_for(conn, oid)
    if order["status"] != "PENDING_APPROVAL":
        raise Error("INVALID_STATE", "order cannot be approved")
    quote = one(conn, "SELECT * FROM quotes WHERE id=%s", (order["quote_id"],))
    if quote["expires_at"] <= now():
        raise Error("QUOTE_EXPIRED", "cancel and create an order with a new accepted quote")
    # Lock candidates in a stable order. RPC snapshots were fetched before entering the transaction.
    for snap in sorted(snapshots, key=lambda x: x["pool_id"]):
        pool = one(conn, "SELECT * FROM pools WHERE id=%s FOR UPDATE", (snap["pool_id"],))
        if not pool or pool["paused"] or pool["version"] != snap["version"]:
            continue
        if pool["address"] == order["receiver"]:
            continue
        from tronenergy_domain.config import settings

        if (now() - snap["observed_at"]).total_seconds() > settings().snapshot_ttl_seconds:
            continue
        # Conservative: subtract HELD only. COMMITTED delegations are already reflected at head.
        # Pending broadcasts may already affect head: temporary double subtraction is safe.
        held = one(
            conn,
            """SELECT COALESCE(sum(stake_sun),0) AS value FROM reservations
                            WHERE pool_id=%s AND resource_type=%s AND status='HELD'""",
            (pool["id"], order["resource_type"]),
        )["value"]
        duplicate = one(
            conn,
            """SELECT id FROM orders WHERE pool_id=%s AND receiver=%s AND resource_type=%s
                                AND status IN ('RESERVED','DELEGATING','ACTIVE','RECLAIMING','RECONCILING')""",
            (pool["id"], order["receiver"], order["resource_type"]),
        )
        if duplicate or snap["max_delegatable_sun"] - held < order["stake_sun"]:
            continue
        conn.execute("UPDATE pools SET version=version+1 WHERE id=%s", (pool["id"],))
        conn.execute(
            "UPDATE orders SET pool_id=%s,settlement_reference=%s WHERE id=%s",
            (pool["id"], data.settlement_reference, oid),
        )
        conn.execute(
            "INSERT INTO reservations VALUES (%s,%s,%s,%s,'HELD')",
            (oid, pool["id"], order["resource_type"], order["stake_sun"]),
        )
        conn.execute(
            "INSERT INTO outbox_tasks(id,order_id,action) VALUES (%s,%s,'DELEGATE')", (identifier(), oid)
        )
        transition(conn, order, "RESERVED", actor, data.model_dump())
        return {"order_id": oid, "status": "RESERVED"}
    raise Error("INSUFFICIENT_CAPACITY", "no pool has sufficient fresh capacity")


def cancel(conn, oid, tenant, reason):
    order = order_for(conn, oid, tenant)
    # RESERVED cannot be cancelled: it may already have a transaction under construction.
    if order["status"] != "PENDING_APPROVAL":
        raise Error("INVALID_STATE", "only unapproved orders can be cancelled")
    transition(conn, order, "CANCELLED", tenant, {"reason": reason})
    return {"order_id": oid, "status": "CANCELLED"}


def schedule_due():
    with transaction() as conn:
        rows = conn.execute("""SELECT * FROM orders WHERE status='ACTIVE' AND expires_at<=now()
                               ORDER BY expires_at FOR UPDATE SKIP LOCKED LIMIT 50""").fetchall()
        for order in rows:
            conn.execute(
                """INSERT INTO outbox_tasks(id,order_id,action) VALUES (%s,%s,'RECLAIM')
                            ON CONFLICT(order_id,action) DO NOTHING""",
                (identifier(), order["id"]),
            )
            transition(conn, order, "RECLAIMING", "scheduler")


def apply_result(oid, action, result):
    with transaction() as conn:
        order = order_for(conn, oid)
        if order["status"] in ("COMPLETED", "FAILED"):
            return
        if result["status"] == "CONFIRMED":
            if action == "DELEGATE" and order["delivered_at"] is None:
                delivered = result["block_timestamp"] / 1000
                from datetime import datetime, timezone

                start = datetime.fromtimestamp(delivered, timezone.utc)
                conn.execute("SELECT id FROM pools WHERE id=%s FOR UPDATE", (order["pool_id"],))
                conn.execute("UPDATE pools SET version=version+1 WHERE id=%s", (order["pool_id"],))
                conn.execute("UPDATE reservations SET status='COMMITTED' WHERE order_id=%s", (oid,))
                conn.execute(
                    "UPDATE orders SET delivered_at=%s,expires_at=%s WHERE id=%s",
                    (start, start + timedelta(seconds=order["duration_seconds"]), oid),
                )
                transition(conn, order, "ACTIVE", "observer", {"txid": result["txid"]})
            elif action == "RECLAIM":
                conn.execute("UPDATE reservations SET status='RELEASED' WHERE order_id=%s", (oid,))
                transition(conn, order, "COMPLETED", "observer", {"txid": result["txid"]})
        elif result["status"] == "FAILED":
            if action == "DELEGATE":
                conn.execute("UPDATE reservations SET status='RELEASED' WHERE order_id=%s", (oid,))
                conn.execute("UPDATE orders SET refund_status='PENDING_MANUAL' WHERE id=%s", (oid,))
                transition(conn, order, "FAILED", "observer", {"txid": result.get("txid")})
            else:
                transition(conn, order, "RECONCILING", "observer", {"reason": "reclaim_failed"})
        elif result["status"] == "RECONCILING" and order["status"] not in ("ACTIVE", "COMPLETED"):
            transition(conn, order, "RECONCILING", "observer")
