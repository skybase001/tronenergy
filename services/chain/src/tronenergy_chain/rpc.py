import logging
from functools import lru_cache

import httpx
from tronenergy_domain.config import settings
from tronenergy_domain.models import Error


class RPC:
    def __init__(self):
        cfg = settings()
        self.client = httpx.Client(
            base_url=cfg.rpc_url,
            timeout=cfg.rpc_timeout,
            headers={"TRON-PRO-API-KEY": cfg.rpc_api_key.get_secret_value()},
        )

    def call(self, method, payload=None, solid=False):
        path = ("walletsolidity/" if solid else "wallet/") + method
        try:
            response = self.client.post(path, json=payload or {})
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise Error("RPC_UNAVAILABLE", "TRON node unavailable", 503, True) from exc
        if not isinstance(data, dict) or data.get("Error"):
            raise Error("RPC_REJECTED", "TRON node rejected the request", 502, True)
        logging.getLogger("chain-service").debug("rpc_completed")
        return data

    def network_check(self):
        expected = settings().genesis_block_id
        if not expected:
            raise Error("NETWORK_NOT_VERIFIED", "configure an independently verified genesis block ID", 503)
        block = self.call("getblockbynum", {"num": 0})
        if block.get("blockID") != expected:
            raise Error("NETWORK_MISMATCH", "RPC network does not match configuration", 503)

    def account(self, address):
        return self.call("getaccount", {"address": address, "visible": True})

    def resources(self, address):
        return self.call("getaccountresource", {"address": address, "visible": True})

    def receiver(self, address):
        account = self.account(address)
        if not account.get("address") or account.get("type") == "Contract":
            raise Error("INVALID_RECEIVER", "receiver must be an activated external account", 422)
        return account

    def capacity(self, address, resource):
        data = self.call(
            "getcandelegatedmaxsize",
            {"owner_address": address, "type": 1 if resource == "ENERGY" else 0, "visible": True},
        )
        return int(data.get("max_size", 0))

    def delegated(self, owner, receiver, resource):
        data = self.call(
            "getdelegatedresourcev2",
            {"fromAddress": owner, "toAddress": receiver, "visible": True},
            solid=True,
        )
        field = "frozen_balance_for_energy" if resource == "ENERGY" else "frozen_balance_for_bandwidth"
        return sum(int(row.get(field, 0)) for row in data.get("delegatedResource", []))


@lru_cache
def rpc():
    return RPC()
