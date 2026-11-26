import pytest
from fastapi.testclient import TestClient
from tronenergy_api import main as api
from tronenergy_domain.config import settings
from tronenergy_domain.models import now
from tronenergy_rental import main as rental
from tronenergy_storage.db import transaction

pytestmark = pytest.mark.integration
A = "TJRabPrwbZy45sbavfcjinPJC18kjpRTv8"
B = "TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7"


def test_http_quote_order_approval_and_tenant_isolation(database, monkeypatch):
    monkeypatch.setenv("TE_API_KEYS", '{"customer-key":"tenant-a","other-key":"tenant-b"}')
    monkeypatch.setenv("TE_ADMIN_KEYS", '["admin-key"]')
    settings.cache_clear()

    class Cache:
        def eval(self, *args):
            return 1

    monkeypatch.setattr(api, "cache", lambda: Cache())
    internal_client = TestClient(rental.app)

    def proxy(base, method, path, data=None, headers=None):
        response = internal_client.request(
            method, path, json=data, headers={"X-Internal-Token": "unit-test-internal", **(headers or {})}
        )
        if response.is_error:
            from tronenergy_domain.models import Error

            body = response.json()
            raise Error(body["code"], body["message"], response.status_code)
        return response.json()

    monkeypatch.setattr(api, "call", proxy)

    def chain(method, path, data=None):
        if path.startswith("accounts/"):
            return {"account": {"address": path.split("/")[-1]}, "resources": {}}
        if path == "quote":
            return {"stake_sun": 2000000}
        if path == "capacity/ENERGY":
            with transaction() as conn:
                row = conn.execute("SELECT * FROM pools LIMIT 1").fetchone()
            return [
                {
                    "pool_id": row["id"],
                    "version": row["version"],
                    "max_delegatable_sun": 4000000,
                    "observed_at": now().isoformat(),
                }
            ]
        raise AssertionError(path)

    monkeypatch.setattr(rental, "chain", chain)
    client = TestClient(api.app)

    def post(path, body, key="customer-key", idem="request"):
        return client.post("/v1/" + path, json=body, headers={"X-API-Key": key, "Idempotency-Key": idem})

    assert post("admin/pools", {"address": A}, "admin-key").status_code == 200
    q = post("quotes", {"resource_type": "ENERGY", "target_units": 100, "duration_seconds": 3600}).json()
    created = post("orders", {"quote_id": q["quote_id"], "receiver_address": B})
    assert created.status_code == 202
    oid = created.json()["order_id"]
    assert post("orders", {"quote_id": q["quote_id"], "receiver_address": B}).json() == created.json()
    denied = client.get("/v1/orders/" + oid, headers={"X-API-Key": "other-key"})
    assert denied.status_code == 404
    approved = post(
        "admin/orders/" + oid + "/approve",
        {"reason": "payment checked", "settlement_reference": "external-reference"},
        "admin-key",
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "RESERVED"
    audit = client.get("/v1/admin/audit?target=" + oid, headers={"X-API-Key": "admin-key"})
    assert audit.status_code == 200
    assert any(row["action"] == "RESERVED" for row in audit.json())
