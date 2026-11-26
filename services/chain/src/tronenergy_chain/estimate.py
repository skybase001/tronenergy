from tronenergy_domain.config import settings
from tronenergy_domain.models import Error, bandwidth_burn, ceil_div
from tronpy.keys import to_hex_address

from tronenergy_chain.encoding import signed_bandwidth


def estimate(rpc, request):
    sender = rpc.receiver(request.sender)
    receiver = rpc.account(request.receiver)
    resources = rpc.resources(request.sender)
    params = {x["key"]: x.get("value", 0) for x in rpc.call("getchainparameters")["chainParameter"]}
    pe, pb = params["getEnergyFee"], params["getTransactionFee"]
    energy, method = 0, "serialized_transaction"
    warnings = ["Estimate reflects current state; later execution may differ."]
    if request.kind == "TRX":
        tx = rpc.call(
            "createtransaction",
            {
                "owner_address": request.sender,
                "to_address": request.receiver,
                "amount": request.amount,
                "visible": True,
            },
        )
    else:
        if request.contract_address not in settings().token_contracts:
            raise Error("TOKEN_NOT_ALLOWED", "contract is not allowlisted", 422)
        parameter = to_hex_address(request.receiver)[2:].rjust(64, "0") + hex(request.amount)[2:].rjust(
            64, "0"
        )
        payload = {
            "owner_address": request.sender,
            "contract_address": request.contract_address,
            "function_selector": "transfer(address,uint256)",
            "parameter": parameter,
            "visible": True,
        }
        # A failed simulation is never interpreted as a zero-cost transfer.
        simulation = rpc.call("triggerconstantcontract", payload)
        if not simulation.get("result", {}).get("result"):
            raise Error("SIMULATION_FAILED", "contract transfer simulation failed", 422)
        energy = int(simulation.get("energy_used", 0))
        method = "triggerconstantcontract"
        try:
            precise = rpc.call("estimateenergy", payload)
            if precise.get("result", {}).get("result") and precise.get("energy_required"):
                energy, method = int(precise["energy_required"]), "estimateenergy"
        except Error:
            warnings.append("estimateenergy unavailable; using constant-call fallback.")
        if energy <= 0:
            raise Error("ESTIMATE_UNAVAILABLE", "node returned no energy estimate", 503, True)
        built = rpc.call(
            "triggersmartcontract",
            {
                **payload,
                "fee_limit": min(
                    ceil_div(energy * pe * 120, 100), params.get("getMaxFeeLimit", 15_000_000_000)
                ),
            },
        )
        tx = built.get("transaction", {})
        warnings.append("Conservative estimate assumes the caller pays all contract Energy.")
    if not tx.get("raw_data_hex"):
        raise Error("BUILD_FAILED", "unable to construct transfer for estimation", 422)
    size = signed_bandwidth(tx, request.signatures)
    staked = max(resources.get("NetLimit", 0) - resources.get("NetUsed", 0), 0)
    free = max(resources.get("freeNetLimit", 0) - resources.get("freeNetUsed", 0), 0)
    extra = 0
    if request.kind == "TRX" and not receiver.get("address"):
        ratio = params.get("getCreateNewAccountBandwidthRate", 1)
        net_fee = 0 if staked >= size * ratio else params["getCreateAccountFee"]
        extra += params["getCreateNewAccountFeeInSystemContract"]
    else:
        net_fee = bandwidth_burn(size, staked, free, pb)
    if request.signatures > 1:
        extra += params.get("getMultiSignFee", 0)
    available = max(resources.get("EnergyLimit", 0) - resources.get("EnergyUsed", 0), 0)
    burned = max(energy - available, 0) * pe
    fee_limit = ceil_div(energy * pe * 120, 100) if energy else None
    if fee_limit and fee_limit > params.get("getMaxFeeLimit", 15_000_000_000):
        raise Error("FEE_LIMIT_EXCEEDED", "required energy budget exceeds chain limit", 422)
    from tronenergy_domain.models import now

    return {
        "energy": energy,
        "bandwidth": size,
        "energy_burn_sun": burned,
        "bandwidth_burn_sun": net_fee,
        "extra_fee_sun": extra,
        "estimated_network_fee_sun": burned + net_fee + extra,
        "conservative_upper_sun": (fee_limit or 0) + net_fee + extra,
        "suggested_fee_limit_sun": fee_limit,
        "method": method,
        "observed_at": now().isoformat(),
        "warnings": warnings,
        "sender_balance_sun": sender.get("balance", 0),
    }
