"""Small canonical encoder for Stake 2.0 resource contracts.

Field numbers follow tronprotocol/protocol/core/contract/balance_contract.proto.
The signer compares actual serialized bytes, not just an untrusted JSON description.
"""

import hashlib

from tronpy.keys import to_raw_address
from tronpy.proto import tron_pb2


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def blob(field, value):
    return varint(field * 8 + 2) + varint(len(value)) + value


def number(field, value):
    return varint(field * 8) + varint(value) if value else b""


def contract_bytes(owner, receiver, resource, stake_sun):
    return (
        blob(1, to_raw_address(owner))
        + number(2, 1 if resource == "ENERGY" else 0)
        + number(3, stake_sun)
        + blob(4, to_raw_address(receiver))
    )


def validate_transaction(tx, order, action, permission_id=0):
    raw_bytes = bytes.fromhex(tx["raw_data_hex"])
    raw = tron_pb2.Transaction.raw()
    raw.ParseFromString(raw_bytes)
    expected_type = 57 if action == "DELEGATE" else 58
    expected_name = "DelegateResourceContract" if action == "DELEGATE" else "UnDelegateResourceContract"
    if len(raw.contract) != 1:
        raise ValueError("exactly one contract required")
    c = raw.contract[0]
    if c.type != expected_type or c.Permission_id != permission_id:
        raise ValueError("contract type or permission mismatch")
    expected = contract_bytes(order["address"], order["receiver"], order["resource_type"], order["stake_sun"])
    if (
        c.parameter.type_url != "type.googleapis.com/protocol." + expected_name
        or c.parameter.value != expected
    ):
        raise ValueError("contract parameters mismatch")
    # Reject hidden fields, lock flags, extra contracts, memo, fee_limit and unknown protobuf fields.
    canonical = tron_pb2.Transaction.raw(
        ref_block_bytes=raw.ref_block_bytes,
        ref_block_hash=raw.ref_block_hash,
        expiration=raw.expiration,
        timestamp=raw.timestamp,
    )
    canonical.contract.add(type=expected_type, parameter=c.parameter, Permission_id=permission_id)
    if canonical.SerializeToString() != raw_bytes:
        raise ValueError("unexpected transaction fields")
    if hashlib.sha256(raw_bytes).hexdigest() != tx["txID"]:
        raise ValueError("txid mismatch")
    value = tx["raw_data"]["contract"][0]["parameter"]["value"]
    if (
        to_raw_address(value["owner_address"]) != to_raw_address(order["address"])
        or to_raw_address(value["receiver_address"]) != to_raw_address(order["receiver"])
        or value["balance"] != order["stake_sun"]
        or value.get("resource", "BANDWIDTH") != order["resource_type"]
    ):
        raise ValueError("JSON contract does not match authorized intent")
    return raw


def signed_bandwidth(tx, signatures=1):
    raw = tron_pb2.Transaction.raw()
    raw.ParseFromString(bytes.fromhex(tx["raw_data_hex"]))
    wire = tron_pb2.Transaction(raw_data=raw, signature=[bytes(65)] * signatures)
    # java-tron reserves MAX_RESULT_SIZE_IN_TX (64 bytes) for the transaction result.
    return len(wire.SerializeToString()) + 64
