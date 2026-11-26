import hashlib
import time
from copy import deepcopy

import pytest
from google.protobuf.any_pb2 import Any
from tronenergy_chain.encoding import contract_bytes, signed_bandwidth, validate_transaction
from tronpy.keys import PrivateKey
from tronpy.proto import tron_pb2

OWNER = PrivateKey(bytes.fromhex("01" * 32)).public_key.to_base58check_address()
RECEIVER = PrivateKey(bytes.fromhex("02" * 32)).public_key.to_base58check_address()


def fixture_tx(resource="ENERGY", action="DELEGATE"):
    order = {"address": OWNER, "receiver": RECEIVER, "resource_type": resource, "stake_sun": 2_000_000}
    name = "DelegateResourceContract" if action == "DELEGATE" else "UnDelegateResourceContract"
    clock = int(time.time() * 1000)
    raw = tron_pb2.Transaction.raw(
        ref_block_bytes=b"ab", ref_block_hash=b"12345678", expiration=clock + 60_000, timestamp=clock
    )
    raw.contract.add(
        type=57 if action == "DELEGATE" else 58,
        parameter=Any(
            type_url="type.googleapis.com/protocol." + name,
            value=contract_bytes(OWNER, RECEIVER, resource, 2_000_000),
        ),
    )
    wire = raw.SerializeToString()
    tx = {
        "raw_data_hex": wire.hex(),
        "txID": hashlib.sha256(wire).hexdigest(),
        "raw_data": {
            "expiration": clock + 60_000,
            "contract": [
                {
                    "parameter": {
                        "value": {
                            "owner_address": OWNER,
                            "receiver_address": RECEIVER,
                            "resource": resource,
                            "balance": 2_000_000,
                        }
                    }
                }
            ],
        },
    }
    return tx, order


@pytest.mark.parametrize("resource", ["ENERGY", "BANDWIDTH"])
@pytest.mark.parametrize("action", ["DELEGATE", "RECLAIM"])
def test_resource_transaction_bytes(resource, action):
    tx, order = fixture_tx(resource, action)
    validate_transaction(tx, order, action)
    assert signed_bandwidth(tx, 2) > signed_bandwidth(tx, 1)


def test_signer_rejects_json_covering_different_bytes():
    tx, order = fixture_tx()
    forged = deepcopy(order)
    forged["stake_sun"] = 3_000_000
    with pytest.raises(ValueError):
        validate_transaction(tx, forged, "DELEGATE")


def test_signer_rejects_hidden_fields_and_wrong_hash():
    tx, order = fixture_tx()
    tx["txID"] = "00" * 32
    with pytest.raises(ValueError, match="txid"):
        validate_transaction(tx, order, "DELEGATE")
    tx, order = fixture_tx()
    raw = tron_pb2.Transaction.raw()
    raw.ParseFromString(bytes.fromhex(tx["raw_data_hex"]))
    raw.fee_limit = 1000
    tx["raw_data_hex"] = raw.SerializeToString().hex()
    tx["txID"] = hashlib.sha256(raw.SerializeToString()).hexdigest()
    with pytest.raises(ValueError, match="unexpected"):
        validate_transaction(tx, order, "DELEGATE")
