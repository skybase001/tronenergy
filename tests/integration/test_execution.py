import hashlib
import time
from copy import deepcopy

import pytest
from google.protobuf.any_pb2 import Any
from tronenergy_chain import execution
from tronenergy_chain.encoding import contract_bytes
from tronenergy_domain.config import settings
from tronenergy_domain.models import Error
from tronenergy_storage.db import one, transaction
from tronpy.keys import PrivateKey
from tronpy.proto import tron_pb2

pytestmark = pytest.mark.integration
OWNER = PrivateKey(bytes.fromhex("01" * 32)).public_key.to_base58check_address()
RECEIVER = PrivateKey(bytes.fromhex("02" * 32)).public_key.to_base58check_address()


def seed():
    with transaction() as conn:
        conn.execute("INSERT INTO pools(id,address) VALUES ('p',%s)", (OWNER,))
        conn.execute("""INSERT INTO quotes(id,tenant_id,resource_type,target_units,stake_sun,duration_seconds,
                        price_sun,price_version,expires_at) VALUES ('q','t','ENERGY',1,2000000,3600,1,'1',now())""")
        conn.execute(
            """INSERT INTO orders(id,tenant_id,quote_id,receiver,resource_type,stake_sun,pool_id,
                        duration_seconds,price_sun,status) VALUES ('o','t','q',%s,'ENERGY',2000000,'p',
                        3600,1,'RESERVED')""",
            (RECEIVER,),
        )


class Node:
    def __init__(self):
        self.builds = 0
        self.broadcasts = []
        self.confirmed = False
        self.tx = None

    def network_check(self):
        pass

    def receiver(self, address):
        return {"address": address}

    def capacity(self, *args):
        return 2000000

    def delegated(self, *args):
        return 2000000 if self.confirmed else 0

    def call(self, method, payload=None, solid=False):
        if method == "delegateresource":
            self.builds += 1
            clock = int(time.time() * 1000)
            raw = tron_pb2.Transaction.raw(
                ref_block_bytes=b"ab", ref_block_hash=b"12345678", expiration=clock + 60000, timestamp=clock
            )
            raw.contract.add(
                type=57,
                parameter=Any(
                    type_url="type.googleapis.com/protocol.DelegateResourceContract",
                    value=contract_bytes(OWNER, RECEIVER, "ENERGY", 2000000),
                ),
            )
            wire = raw.SerializeToString()
            self.tx = {
                "raw_data_hex": wire.hex(),
                "txID": hashlib.sha256(wire).hexdigest(),
                "raw_data": {
                    "expiration": clock + 60000,
                    "contract": [
                        {
                            "parameter": {
                                "value": {
                                    "owner_address": OWNER,
                                    "receiver_address": RECEIVER,
                                    "resource": "ENERGY",
                                    "balance": 2000000,
                                }
                            }
                        }
                    ],
                },
            }
            return self.tx
        if method == "broadcasttransaction":
            self.broadcasts.append(deepcopy(payload))
            if len(self.broadcasts) == 1:
                raise Error("RPC_UNAVAILABLE", "response lost", 503, True)
            return {"result": True}
        if method == "gettransactioninfobyid":
            return (
                {"id": self.tx["txID"], "blockNumber": 100, "blockTimeStamp": int(time.time() * 1000)}
                if self.confirmed
                else {}
            )
        if method == "gettransactionbyid":
            return {"txID": self.tx["txID"], "ret": [{"contractRet": "SUCCESS"}]} if self.confirmed else {}
        raise AssertionError(method)


def test_broadcast_timeout_reuses_persisted_signature(database, monkeypatch):
    seed()
    monkeypatch.setenv("TE_BROADCASTING_ENABLED", "true")
    settings.cache_clear()
    node = Node()
    monkeypatch.setattr(execution, "rpc", lambda: node)

    class SignClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *args, **kw):
            class Response:
                status_code = 200

                def json(self):
                    return {**node.tx, "signature": ["ab" * 65]}

            return Response()

    monkeypatch.setattr(execution.httpx, "Client", SignClient)
    with pytest.raises(Error):
        execution.execute("o", "DELEGATE")
    with transaction() as conn:
        assert one(conn, "SELECT signed_tx FROM chain_operations WHERE order_id='o'")["signed_tx"]
    execution.execute("o", "DELEGATE")
    assert node.builds == 1
    assert node.broadcasts[0] == node.broadcasts[1]
    node.confirmed = True
    assert execution.observe("o:DELEGATE")["status"] == "CONFIRMED"
    assert execution.execute("o", "DELEGATE")["status"] == "CONFIRMED"
    assert len(node.broadcasts) == 2


def test_expired_transaction_is_not_rebuilt(database, monkeypatch):
    seed()
    monkeypatch.setenv("TE_BROADCASTING_ENABLED", "true")
    settings.cache_clear()
    node = Node()
    monkeypatch.setattr(execution, "rpc", lambda: node)
    tx = node.call("delegateresource")
    tx["raw_data"]["expiration"] = 0
    from psycopg.types.json import Jsonb

    with transaction() as conn:
        conn.execute(
            """INSERT INTO chain_operations(id,order_id,action,txid,raw_tx)
                        VALUES ('o:DELEGATE','o','DELEGATE',%s,%s)""",
            (tx["txID"], Jsonb(tx)),
        )
    assert execution.execute("o", "DELEGATE")["status"] == "RECONCILING"
    assert node.builds == 1
    assert not node.broadcasts
