import logging
import time
from uuid import uuid4

from tronenergy_domain.client import call
from tronenergy_domain.config import settings
from tronenergy_observability.log import configure
from tronenergy_storage.db import one, transaction

from tronenergy_rental.service import apply_result, schedule_due

log = logging.getLogger("rental-worker")


def claim(owner):
    with transaction() as conn:
        return one(
            conn,
            """WITH task AS (
            SELECT id FROM outbox_tasks WHERE
            (status='PENDING' AND available_at<=now()) OR (status='RUNNING' AND lease_until<now())
            ORDER BY available_at FOR UPDATE SKIP LOCKED LIMIT 1)
            UPDATE outbox_tasks SET status='RUNNING',owner=%s,lease_until=now()+interval '120 seconds',
            claim_version=claim_version+1,attempts=attempts+1
            WHERE id=(SELECT id FROM task) RETURNING *""",
            (owner,),
        )


def finish(task, status, error=None):
    with transaction() as conn:
        conn.execute(
            """UPDATE outbox_tasks SET status=%s,last_error=%s,
                        available_at=now()+make_interval(secs=>%s),owner=NULL,lease_until=NULL
                        WHERE id=%s AND owner=%s AND claim_version=%s""",
            (
                status,
                error,
                min(300, 2 ** min(task["attempts"], 8)),
                task["id"],
                task["owner"],
                task["claim_version"],
            ),
        )


def tick(owner):
    schedule_due()
    task = claim(owner)
    if not task:
        return False
    cfg = settings()
    try:
        opid = task["order_id"] + ":" + task["action"]
        try:
            result = call(cfg.chain_url, "GET", "/internal/observe/" + opid)
        except Exception as exc:
            from tronenergy_domain.models import Error

            if not isinstance(exc, Error) or exc.status != 404:
                raise
            result = {"status": "NOT_CREATED"}
        if result["status"] not in ("CONFIRMED", "FAILED", "RECONCILING"):
            result = call(
                cfg.chain_url, "POST", "/internal/execute/" + task["order_id"] + "/" + task["action"]
            )
        apply_result(task["order_id"], task["action"], result)
        if result["status"] in ("CONFIRMED", "FAILED"):
            finish(task, "DONE")
        elif result["status"] == "RECONCILING":
            finish(task, "BLOCKED", "reconciliation_required")
        else:
            finish(task, "PENDING")
        return True
    except Exception as exc:
        log.warning("task_retry", extra={"order_id": task["order_id"], "error_type": type(exc).__name__})
        finish(task, "BLOCKED" if task["attempts"] >= 10 else "PENDING", type(exc).__name__)
        return True


def main():
    configure(settings().log_level)
    owner = str(uuid4())
    while True:
        try:
            tick(owner)
        except Exception as exc:
            log.error("worker_iteration_failed", extra={"error_type": type(exc).__name__})
        time.sleep(settings().poll_seconds)


if __name__ == "__main__":
    main()
