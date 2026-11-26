import hashlib
import time

import pytest
from fastapi.testclient import TestClient
from google.protobuf.any_pb2 import Any
from psycopg.types.json import Jsonb
from tronenergy_chain.encoding import contract_bytes
from tronenergy_domain.config import settings
from tronenergy_signer import main as signer
from tronenergy_storage.db import transaction
from tronpy.keys import PrivateKey, Signature
from tronpy.proto import tron_pb2

pytestmark = pytest.mark.integration


def test_signer_signs_only_persisted_authorized_operation(database, monkeypatch, tmp_path):
    # Publicly known test scalar; never funded, never used for network transactions.
    key = PrivateKey(bytes.fromhex("03" * 32))
    owner = key.public_key.to_base58check_address()
    receiver = PrivateKey(bytes.fromhex("04" * 32)).public_key.to_base58check_address()
    keyfile = tmp_path / "test.key"
    keyfile.write_text("03" * 32)
    monkeypatch.setenv("TE_PRIVATE_KEY_FILE", str(keyfile))
    monkeypatch.setenv("TE_SIGNING_ENABLED", "true")
    monkeypatch.setenv("TE_SIGNER_TOKEN", "test-signer-token")
    settings.cache_clear()

    class Node:
        def network_check(self):
            pass

    monkeypatch.setattr(signer, "rpc", lambda: Node())
    clock = int(time.time() * 1000)
    raw = tron_pb2.Transaction.raw(
        ref_block_bytes=b"ab", ref_block_hash=b"12345678", timestamp=clock, expiration=clock + 60000
    )
    raw.contract.add(
        type=57,
        parameter=Any(
            type_url="type.googleapis.com/protocol.DelegateResourceContract",
            value=contract_bytes(owner, receiver, "ENERGY", 1000000),
        ),
    )
    rawbytes = raw.SerializeToString()
    txid = hashlib.sha256(rawbytes).hexdigest()
    tx = {
        "txID": txid,
        "raw_data_hex": rawbytes.hex(),
        "raw_data": {
            "contract": [
                {
                    "parameter": {
                        "value": {
                            "owner_address": owner,
                            "receiver_address": receiver,
                            "balance": 1000000,
                            "resource": "ENERGY",
                        }
                    }
                }
            ]
        },
    }
    with transaction() as conn:
        conn.execute("INSERT INTO pools(id,address) VALUES ('pool',%s)", (owner,))
        conn.execute("""INSERT INTO quotes VALUES ('q','t','ENERGY',1,1000000,3600,1,'1',now(),now())""")
        conn.execute(
            """INSERT INTO orders(id,tenant_id,quote_id,receiver,resource_type,stake_sun,pool_id,
                        duration_seconds,price_sun,status) VALUES ('o','t','q',%s,'ENERGY',1000000,'pool',
                        3600,1,'RESERVED')""",
            (receiver,),
        )
        conn.execute(
            """INSERT INTO chain_operations(id,order_id,action,txid,raw_tx)
                        VALUES ('op','o','DELEGATE',%s,%s)""",
            (txid, Jsonb(tx)),
        )
    client = TestClient(signer.app)
    assert client.post("/internal/sign/op").status_code == 401
    response = client.post("/internal/sign/op", headers={"X-Signer-Token": "test-signer-token"})
    assert response.status_code == 200
    signature = Signature(bytes.fromhex(response.json()["signature"][0]))
    assert signature.recover_public_key_from_msg_hash(bytes.fromhex(txid)) == key.public_key
    with transaction() as conn:
        conn.execute("UPDATE pools SET paused=true")
    assert (
        client.post("/internal/sign/op", headers={"X-Signer-Token": "test-signer-token"}).status_code == 409
    )
