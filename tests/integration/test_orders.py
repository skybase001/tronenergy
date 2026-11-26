from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from tronenergy_domain.models import Approval, Error, OrderInput, now
from tronenergy_rental.service import apply_result, approve, create_order, idem, schedule_due
from tronenergy_rental.worker import claim, finish
from tronenergy_storage.db import one, transaction

pytestmark = pytest.mark.integration
RECEIVER = "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8"


def seed():
    with transaction() as conn:
        conn.execute("INSERT INTO pools(id,address) VALUES ('pool','pool-address')")
        conn.execute(
            """INSERT INTO quotes(id,tenant_id,resource_type,target_units,stake_sun,duration_seconds,
                        price_sun,price_version,expires_at) VALUES ('quote','tenant','ENERGY',100,2000000,
                        3600,1000,'1',%s)""",
            (now() + timedelta(hours=1),),
        )


def order(receiver=RECEIVER):
    with transaction() as conn:
        return create_order(conn, "tenant", OrderInput(quote_id="quote", receiver_address=receiver))[
            "order_id"
        ]


def snapshot():
    with transaction() as conn:
        version = one(conn, "SELECT version FROM pools WHERE id='pool'")["version"]
    return [{"pool_id": "pool", "version": version, "max_delegatable_sun": 2_000_000, "observed_at": now()}]


def test_idempotency_conflict_and_rollback(database):
    seed()
    body = {"quote_id": "quote", "receiver_address": RECEIVER}
    with transaction() as conn:
        first = idem(
            conn, "tenant", "orders", "key", body, lambda: create_order(conn, "tenant", OrderInput(**body))
        )
    with transaction() as conn:
        assert idem(conn, "tenant", "orders", "key", body, lambda: 1) == first
    with pytest.raises(Error):
        with transaction() as conn:
            idem(conn, "tenant", "orders", "key", {"changed": True}, lambda: 1)
    with pytest.raises(RuntimeError):
        with transaction() as conn:
            approve(
                conn,
                first["order_id"],
                "admin",
                Approval(reason="paid", settlement_reference="ref"),
                snapshot(),
            )
            raise RuntimeError("injected")
    with transaction() as conn:
        assert (
            one(conn, "SELECT status FROM orders WHERE id=%s", (first["order_id"],))["status"]
            == "PENDING_APPROVAL"
        )
        assert one(conn, "SELECT count(*) AS n FROM reservations")["n"] == 0
        assert one(conn, "SELECT count(*) AS n FROM outbox_tasks")["n"] == 0


def test_concurrent_last_capacity(database):
    seed()
    orders = [order(), order("TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7")]
    snap = snapshot()

    def attempt(oid):
        try:
            with transaction() as conn:
                approve(conn, oid, "admin", Approval(reason="paid", settlement_reference="ref"), snap)
            return True
        except Error:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(attempt, orders)) == 1
    with transaction() as conn:
        assert one(conn, "SELECT sum(stake_sun) AS n FROM reservations")["n"] == 2_000_000


def test_lifecycle_and_stale_claim(database):
    seed()
    oid = order()
    with transaction() as conn:
        approve(conn, oid, "admin", Approval(reason="paid", settlement_reference="ref"), snapshot())
    first = claim("worker-a")
    with transaction() as conn:
        conn.execute("UPDATE outbox_tasks SET lease_until=now()-interval '1 second'")
    second = claim("worker-b")
    finish(first, "DONE")
    with transaction() as conn:
        assert one(conn, "SELECT status FROM outbox_tasks WHERE id=%s", (first["id"],))["status"] == "RUNNING"
    apply_result(
        oid,
        "DELEGATE",
        {"status": "CONFIRMED", "block_timestamp": int(now().timestamp() * 1000), "txid": "delegate"},
    )
    finish(second, "DONE")
    with transaction() as conn:
        conn.execute("UPDATE orders SET expires_at=now()-interval '1 second' WHERE id=%s", (oid,))
    schedule_due()
    reclaim = claim("worker-c")
    assert reclaim["action"] == "RECLAIM"
    apply_result(oid, "RECLAIM", {"status": "FAILED", "txid": "failed-reclaim"})
    # A replayed delegation confirmation must not erase a later reclaim failure.
    apply_result(
        oid,
        "DELEGATE",
        {"status": "CONFIRMED", "block_timestamp": int(now().timestamp() * 1000), "txid": "delegate"},
    )
    with transaction() as conn:
        assert one(conn, "SELECT status FROM orders WHERE id=%s", (oid,))["status"] == "RECONCILING"
    apply_result(oid, "RECLAIM", {"status": "CONFIRMED", "txid": "reclaim"})
    with transaction() as conn:
        assert one(conn, "SELECT status FROM orders WHERE id=%s", (oid,))["status"] == "COMPLETED"
        assert one(conn, "SELECT status FROM reservations WHERE order_id=%s", (oid,))["status"] == "RELEASED"
