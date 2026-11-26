import os

import pytest


@pytest.fixture
def database(monkeypatch):
    url = os.environ.get("TE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TE_TEST_DATABASE_URL to an isolated PostgreSQL test database")
    from uuid import uuid4

    # Test schema prevents dropping or truncating tables in an existing application schema.
    import psycopg
    from tronenergy_domain.config import settings
    from tronenergy_storage.db import pool

    schema = "test_" + uuid4().hex
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
    from psycopg.conninfo import make_conninfo

    test_url = make_conninfo(url, options=f"-c search_path={schema}")
    monkeypatch.setenv("TE_DATABASE_URL", test_url)
    monkeypatch.setenv("TE_INTERNAL_TOKEN", "unit-test-internal")
    settings.cache_clear()
    pool.cache_clear()
    from tronenergy_storage.migrate import migrate

    migrate()
    yield
    pool().close()
    pool.cache_clear()
    settings.cache_clear()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
