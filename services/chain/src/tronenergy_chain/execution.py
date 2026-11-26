import hashlib
import logging
import time

import httpx
from psycopg.types.json import Jsonb
from tronenergy_domain.config import settings
from tronenergy_domain.models import Error
from tronenergy_storage.db import audit, one, pool, transaction

from tronenergy_chain.encoding import validate_transaction
from tronenergy_chain.rpc import rpc


def context(oid):
    with transaction() as conn:
        row = one(
            conn,
            """SELECT o.*,p.address,p.paused FROM orders o JOIN pools p ON p.id=o.pool_id
                           WHERE o.id=%s""",
            (oid,),
        )
    if not row:
        raise Error("NOT_FOUND", "allocated order not found", 404)
    return row


def public_operation(row):
    return {
        key: row[key]
        for key in (
            "id",
            "order_id",
            "action",
            "status",
            "txid",
            "block_number",
            "block_timestamp",
            "last_error",
        )
    }


def execute(oid, action):
    if action not in ("DELEGATE", "RECLAIM"):
        raise Error("INVALID_ACTION", "unsupported action", 422)
    cfg = settings()
    order = context(oid)
    lock = int.from_bytes(hashlib.sha256(order["pool_id"].encode()).digest()[:8], "big", signed=True)
    # Session lock serializes a pool while short DB transactions commit before network calls.
    # Failover of this write path still requires fencing the old process (see operations guide).
    with pool().connection() as guard:
        guard.autocommit = True
        acquired = one(guard, "SELECT pg_try_advisory_lock(%s) AS acquired", (lock,))["acquired"]
        if not acquired:
            guard.autocommit = False
            raise Error("POOL_BUSY", "pool has an operation in flight", 503, True)
        try:
            return _execute(order, action, cfg)
        finally:
            guard.execute("SELECT pg_advisory_unlock(%s)", (lock,))
            guard.autocommit = False


def _execute(order, action, cfg):
    opid = order["id"] + ":" + action
    with transaction() as conn:
        op = one(conn, "SELECT * FROM chain_operations WHERE id=%s", (opid,))
    if op and op["status"] in ("CONFIRMED", "FAILED", "RECONCILING"):
        return public_operation(op)
    if not cfg.broadcasting_enabled:
        raise Error("EXECUTION_DISABLED", "broadcasting is disabled", 503)
    rpc().network_check()
    allowed = ("RESERVED", "DELEGATING") if action == "DELEGATE" else ("RECLAIMING", "RECONCILING")
    if order["status"] not in allowed:
        raise Error("INVALID_STATE", "order does not authorize this operation")
    if action == "DELEGATE" and order["paused"]:
        raise Error("POOL_PAUSED", "pool is paused", 503)
    if not op:
        if action == "DELEGATE":
            rpc().receiver(order["receiver"])
            if rpc().capacity(order["address"], order["resource_type"]) < order["stake_sun"]:
                raise Error("INSUFFICIENT_CAPACITY", "pool capacity changed", 503, True)
            # Do not merge platform orders with pre-existing, untracked delegation.
            if rpc().delegated(order["address"], order["receiver"], order["resource_type"]):
                raise Error("UNTRACKED_DELEGATION", "existing delegation requires reconciliation")
        method = "delegateresource" if action == "DELEGATE" else "undelegateresource"
        payload = {
            "owner_address": order["address"],
            "receiver_address": order["receiver"],
            "balance": order["stake_sun"],
            "resource": order["resource_type"],
            "visible": True,
        }
        if action == "DELEGATE":
            payload["lock"] = False
        if cfg.permission_id:
            payload["Permission_id"] = cfg.permission_id
        tx = rpc().call(method, payload)
        validate_transaction(tx, order, action, cfg.permission_id)
        with transaction() as conn:
            conn.execute(
                """INSERT INTO chain_operations(id,order_id,action,txid,raw_tx)
                            VALUES (%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING""",
                (opid, order["id"], action, tx["txID"], Jsonb(tx)),
            )
            audit(conn, "chain-service", "TRANSACTION_PREPARED", opid, {"txid": tx["txID"]})
            op = one(conn, "SELECT * FROM chain_operations WHERE id=%s", (opid,))
    # Never replace an expired transaction automatically; absence from one RPC is insufficient proof.
    if op["raw_tx"]["raw_data"]["expiration"] <= int(time.time() * 1000):
        with transaction() as conn:
            conn.execute(
                "UPDATE chain_operations SET status='RECONCILING',last_error='expired' WHERE id=%s AND status NOT IN ('CONFIRMED','FAILED')",
                (opid,),
            )
        return {**public_operation(op), "status": "RECONCILING"}
    if not op["signed_tx"]:
        with httpx.Client(timeout=cfg.rpc_timeout) as client:
            response = client.post(
                cfg.signer_url + "/internal/sign/" + opid,
                headers={"X-Signer-Token": cfg.signer_token.get_secret_value()},
            )
            if response.status_code != 200:
                raise Error("SIGNER_UNAVAILABLE", "signer refused operation", 503, True)
            signed = response.json()
        if signed.get("txID") != op["txid"] or signed.get("raw_data_hex") != op["raw_tx"]["raw_data_hex"]:
            raise Error("INVALID_SIGNATURE_RESPONSE", "signer returned a different transaction", 503)
        with transaction() as conn:
            conn.execute(
                "UPDATE chain_operations SET signed_tx=%s,status='SIGNED' WHERE id=%s", (Jsonb(signed), opid)
            )
            audit(conn, "chain-service", "SIGNED_TRANSACTION_STORED", opid, {"txid": op["txid"]})
        op["signed_tx"] = signed
    # Persist intent before attempting the irreversible network side effect.
    with transaction() as conn:
        current = one(conn, "SELECT status FROM chain_operations WHERE id=%s FOR UPDATE", (opid,))
        if current["status"] in ("CONFIRMED", "FAILED", "RECONCILING"):
            return public_operation(one(conn, "SELECT * FROM chain_operations WHERE id=%s", (opid,)))
        conn.execute("UPDATE chain_operations SET status='BROADCASTING' WHERE id=%s", (opid,))
        audit(conn, "chain-service", "BROADCAST_INTENT", opid, {"txid": op["txid"]})
    result = rpc().call("broadcasttransaction", op["signed_tx"])
    # Even a rejection can mean the transaction was previously broadcast. Observer decides finality.
    with transaction() as conn:
        conn.execute(
            "UPDATE chain_operations SET status='PENDING',last_error=%s WHERE id=%s AND status NOT IN ('CONFIRMED','FAILED','RECONCILING')",
            (None if result.get("result") else "broadcast_not_accepted", opid),
        )
        audit(conn, "chain-service", "BROADCAST_RESULT", opid, {"accepted": bool(result.get("result"))})
        op = one(conn, "SELECT * FROM chain_operations WHERE id=%s", (opid,))
    logging.getLogger("chain-service").info(
        "broadcast_attempted", extra={"operation_id": opid, "txid": op["txid"]}
    )
    return public_operation(op)


def observe(opid):
    with transaction() as conn:
        conn.execute("UPDATE chain_operations SET observed_at=now() WHERE id=%s", (opid,))
        op = one(conn, "SELECT * FROM chain_operations WHERE id=%s", (opid,))
    if not op:
        raise Error("NOT_FOUND", "operation not found", 404)
    if op["status"] in ("CONFIRMED", "FAILED"):
        return public_operation(op)
    if not op["txid"]:
        return public_operation(op)
    receipt = rpc().call("gettransactioninfobyid", {"value": op["txid"]}, solid=True)
    tx = rpc().call("gettransactionbyid", {"value": op["txid"]}, solid=True)
    if not receipt.get("id") or not tx.get("txID"):
        return public_operation(op)
    if receipt["id"] != op["txid"] or tx["txID"] != op["txid"]:
        raise Error("RPC_INCONSISTENT", "receipt identity mismatch", 503)
    ret = tx.get("ret", [])
    if not ret or "blockNumber" not in receipt or "blockTimeStamp" not in receipt:
        return public_operation(op)
    success = all(x.get("contractRet") == "SUCCESS" for x in ret) and receipt.get("result") != "FAILED"
    status = "CONFIRMED" if success else "FAILED"
    if success:
        order = context(op["order_id"])
        amount = rpc().delegated(order["address"], order["receiver"], order["resource_type"])
        expected = order["stake_sun"] if op["action"] == "DELEGATE" else 0
        if amount != expected:
            status = "RECONCILING"
    with transaction() as conn:
        conn.execute(
            """UPDATE chain_operations SET status=%s,block_number=%s,block_timestamp=%s
                        WHERE id=%s AND status NOT IN ('CONFIRMED','FAILED')""",
            (status, receipt["blockNumber"], receipt["blockTimeStamp"], opid),
        )
        audit(conn, "observer", "CHAIN_OBSERVED", opid, {"status": status, "txid": op["txid"]})
        return public_operation(one(conn, "SELECT * FROM chain_operations WHERE id=%s", (opid,)))
