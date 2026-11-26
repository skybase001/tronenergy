from fastapi.testclient import TestClient
from tronenergy_api.main import app
from tronenergy_domain.config import settings


def test_public_api_rejects_missing_credentials():
    response = TestClient(app).get("/v1/admin/pools")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
    assert response.headers["X-Request-ID"]


def test_customer_cannot_access_admin(monkeypatch):
    monkeypatch.setenv("TE_API_KEYS", '{"test-key":"tenant-a"}')
    settings.cache_clear()
    try:
        response = TestClient(app).get("/v1/admin/pools", headers={"X-API-Key": "test-key"})
        assert response.status_code == 403
    finally:
        settings.cache_clear()


def test_internal_api_requires_service_token():
    from tronenergy_rental.main import app as rental

    response = TestClient(rental).get("/v1/admin/pools", headers={"X-Tenant": "admin", "X-Admin": "1"})
    assert response.status_code == 401
