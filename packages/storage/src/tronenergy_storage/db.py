from contextlib import contextmanager
from functools import lru_cache

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from tronenergy_domain.config import settings
from tronenergy_observability.log import request_id_context


@lru_cache
def pool():
    return ConnectionPool(
        settings().database_url.get_secret_value(),
        min_size=1,
        max_size=10,
        timeout=5,
        open=True,
        kwargs={"row_factory": dict_row},
    )


@contextmanager
def transaction():
    with pool().connection() as conn:
        with conn.transaction():
            yield conn


def one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def audit(conn, actor, action, target, details=None, request_id=None):
    conn.execute(
        "INSERT INTO audit_events(actor,action,target,details,request_id) VALUES (%s,%s,%s,%s,%s)",
        (actor, action, target, Jsonb(details or {}), request_id or request_id_context.get()),
    )
