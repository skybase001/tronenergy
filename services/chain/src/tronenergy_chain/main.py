from fastapi import Depends
from tronenergy_domain.models import Address, QuoteInput, TransferInput, now, stake_for_units
from tronenergy_domain.web import app, internal
from tronenergy_storage.db import transaction

from tronenergy_chain.estimate import estimate
from tronenergy_chain.execution import execute, observe
from tronenergy_chain.rpc import rpc

app = app("chain-service")


@app.get("/internal/accounts/{address}", dependencies=[Depends(internal)])
def account(address: Address):
    return {
        "account": rpc().account(address),
        "resources": rpc().resources(address),
        "observed_at": now().isoformat(),
    }


@app.post("/internal/estimate", dependencies=[Depends(internal)])
def estimate_transfer(data: TransferInput):
    return estimate(rpc(), data)


@app.post("/internal/quote", dependencies=[Depends(internal)])
def quote(data: QuoteInput):
    with transaction() as conn:
        row = conn.execute("SELECT address FROM pools WHERE NOT paused ORDER BY id LIMIT 1").fetchone()
    from tronenergy_domain.models import Error

    if not row:
        raise Error("NO_POOLS", "no active resource pool", 503)
    resources = rpc().resources(row["address"])
    weight_key, limit_key = (
        ("TotalEnergyWeight", "TotalEnergyLimit")
        if data.resource_type == "ENERGY"
        else ("TotalNetWeight", "TotalNetLimit")
    )
    stake = stake_for_units(data.target_units, resources.get(weight_key, 0), resources.get(limit_key, 0))
    if resources.get(weight_key, 0) <= 0:
        raise Error("INVALID_WEIGHT", "resource weight unavailable", 503)
    return {"stake_sun": stake, "observed_at": now().isoformat()}


@app.get("/internal/capacity/{resource}", dependencies=[Depends(internal)])
def capacity(resource: str):
    from tronenergy_domain.models import Resource

    resource = Resource(resource)
    with transaction() as conn:
        pools = conn.execute("SELECT * FROM pools WHERE NOT paused ORDER BY id").fetchall()
    result = []
    for pool in pools:
        amount = rpc().capacity(pool["address"], resource)
        block = rpc().call("getnowblock")["block_header"]["raw_data"]["number"]
        result.append(
            {
                "pool_id": pool["id"],
                "version": pool["version"],
                "max_delegatable_sun": amount,
                "block_number": block,
                "observed_at": now().isoformat(),
            }
        )
        with transaction() as conn:
            conn.execute(
                """INSERT INTO resource_snapshots(pool_id,resource_type,max_delegatable_sun,block_number)
                            VALUES (%s,%s,%s,%s) ON CONFLICT(pool_id,resource_type) DO UPDATE SET
                            max_delegatable_sun=EXCLUDED.max_delegatable_sun,block_number=EXCLUDED.block_number,
                            observed_at=now() WHERE resource_snapshots.block_number<=EXCLUDED.block_number""",
                (pool["id"], resource, amount, block),
            )
    return result


@app.post("/internal/execute/{oid}/{action}", dependencies=[Depends(internal)])
def operation(oid: str, action: str):
    return execute(oid, action)


@app.get("/internal/observe/{opid}", dependencies=[Depends(internal)])
def observe_operation(opid: str):
    return observe(opid)


@app.get("/internal/pending", dependencies=[Depends(internal)])
def pending():
    with transaction() as conn:
        return conn.execute("""SELECT c.id,c.order_id,c.action FROM chain_operations c
                               JOIN orders o ON o.id=c.order_id WHERE
                               (c.action='DELEGATE' AND o.delivered_at IS NULL AND o.status IN ('RESERVED','DELEGATING','RECONCILING'))
                               OR (c.action='RECLAIM' AND o.status IN ('RECLAIMING','RECONCILING'))
                               ORDER BY COALESCE(c.observed_at,c.created_at) LIMIT 100""").fetchall()
