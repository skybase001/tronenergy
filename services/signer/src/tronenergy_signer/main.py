import hmac
import time
from pathlib import Path

from fastapi import Header
from tronenergy_chain.encoding import validate_transaction
from tronenergy_chain.rpc import rpc
from tronenergy_domain.config import settings
from tronenergy_domain.models import Error
from tronenergy_domain.web import app
from tronenergy_storage.db import audit, one, transaction
from tronpy.keys import PrivateKey

app = app("signer-service")


@app.post("/internal/sign/{opid}")
def sign(opid: str, x_signer_token: str = Header(default="")):
    cfg = settings()
    if not cfg.signer_token.get_secret_value() or not hmac.compare_digest(
        x_signer_token, cfg.signer_token.get_secret_value()
    ):
        raise Error("UNAUTHORIZED", "invalid signer credentials", 401)
    if not cfg.signing_enabled or not cfg.private_key_file:
        raise Error("SIGNING_DISABLED", "signing is disabled", 503)
    rpc().network_check()
    with transaction() as conn:
        op = one(
            conn,
            """SELECT c.*,o.receiver,o.resource_type,o.stake_sun,o.status AS order_status,
                         p.address,p.paused FROM chain_operations c JOIN orders o ON o.id=c.order_id
                         JOIN pools p ON p.id=o.pool_id WHERE c.id=%s FOR UPDATE OF c""",
            (opid,),
        )
        if not op:
            raise Error("NOT_FOUND", "operation not found", 404)
        allowed = ("RESERVED", "DELEGATING") if op["action"] == "DELEGATE" else ("RECLAIMING", "RECONCILING")
        if op["order_status"] not in allowed or (op["action"] == "DELEGATE" and op["paused"]):
            raise Error("INVALID_STATE", "order does not authorize signing")
        if not 1_000_000 <= op["stake_sun"] <= cfg.max_stake_sun:
            raise Error("SIGNING_LIMIT", "stake exceeds signing policy")
        tx = op["raw_tx"]
        try:
            raw = validate_transaction(tx, op, op["action"], cfg.permission_id)
        except (ValueError, KeyError) as exc:
            raise Error("INVALID_TRANSACTION", "serialized transaction violates policy", 422) from exc
        clock = int(time.time() * 1000)
        if raw.expiration <= clock or raw.expiration > clock + 600_000 or raw.timestamp > clock + 30_000:
            raise Error("INVALID_EXPIRATION", "transaction time is outside signing policy")
        key = PrivateKey(bytes.fromhex(Path(cfg.private_key_file).read_text().strip()))
        # V1 supports one key with owner permissions only. Custom active permissions need separate acceptance.
        if cfg.permission_id != 0 or key.public_key.to_base58check_address() != op["address"]:
            raise Error("OWNER_MISMATCH", "signing key does not own this pool")
        if op["signed_tx"]:
            return op["signed_tx"]
        audit(conn, "signer-service", "SIGN_AUTHORIZED", opid, {"txid": op["txid"]})
        signed = {**tx, "signature": [key.sign_msg_hash(bytes.fromhex(op["txid"])).hex()]}
        # Canonical JSON comes from the validated builder; private key never leaves this process.
        from psycopg.types.json import Jsonb

        conn.execute(
            "UPDATE chain_operations SET signed_tx=%s,status='SIGNED' WHERE id=%s", (Jsonb(signed), opid)
        )
        return signed
