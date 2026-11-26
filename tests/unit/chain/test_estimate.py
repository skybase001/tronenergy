import pytest
from tronenergy_chain.estimate import estimate
from tronenergy_domain.config import settings
from tronenergy_domain.models import Error, TransferInput
from tronpy.proto import tron_pb2

A = "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8"
B = "TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7"


class Node:
    def __init__(self, active=True, simulate=True, precise=True):
        self.active, self.simulate, self.precise = active, simulate, precise

    def receiver(self, address):
        return {"address": address, "balance": 1000000}

    def account(self, address):
        return {"address": address} if self.active else {}

    def resources(self, address):
        return {"NetLimit": 10, "freeNetLimit": 10, "EnergyLimit": 50}

    def call(self, method, payload=None):
        raw = tron_pb2.Transaction.raw(timestamp=1000, expiration=60000)
        tx = {"raw_data_hex": raw.SerializeToString().hex()}
        if method == "getchainparameters":
            return {
                "chainParameter": [
                    {"key": k, "value": v}
                    for k, v in {
                        "getEnergyFee": 100,
                        "getTransactionFee": 1000,
                        "getCreateAccountFee": 100000,
                        "getCreateNewAccountFeeInSystemContract": 1000000,
                        "getCreateNewAccountBandwidthRate": 1,
                        "getMaxFeeLimit": 15000000000,
                    }.items()
                ]
            }
        if method == "createtransaction":
            return tx
        if method == "triggerconstantcontract":
            return {"result": {"result": self.simulate}, "energy_used": 100}
        if method == "estimateenergy":
            if not self.precise:
                raise Error("UNAVAILABLE", "disabled", 503)
            return {"result": {"result": True}, "energy_required": 120}
        if method == "triggersmartcontract":
            return {"transaction": tx}
        raise AssertionError(method)


def test_trx_does_not_consume_energy_and_charges_full_bandwidth():
    result = estimate(Node(), TransferInput(sender=A, receiver=B, amount=1))
    assert result["energy"] == 0
    assert result["bandwidth_burn_sun"] == result["bandwidth"] * 1000


def test_unactivated_trx_recipient_has_activation_fee():
    result = estimate(Node(active=False), TransferInput(sender=A, receiver=B, amount=1))
    assert result["extra_fee_sun"] == 1000000
    assert result["bandwidth_burn_sun"] == 100000


def test_contract_simulation_and_fallback(monkeypatch):
    monkeypatch.setenv("TE_TOKEN_CONTRACTS", '["' + B + '"]')
    settings.cache_clear()
    request = TransferInput(sender=A, receiver=B, amount=1, kind="TRC20", contract_address=B)
    try:
        result = estimate(Node(), request)
        assert result["energy_burn_sun"] == 7000
        assert result["method"] == "estimateenergy"
        assert estimate(Node(precise=False), request)["method"] == "triggerconstantcontract"
        with pytest.raises(Error) as error:
            estimate(Node(simulate=False), request)
        assert error.value.code == "SIMULATION_FAILED"
    finally:
        settings.cache_clear()
