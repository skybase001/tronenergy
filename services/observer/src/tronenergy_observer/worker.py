import logging
import time

from tronenergy_domain.client import call
from tronenergy_domain.config import settings
from tronenergy_observability.log import configure


def tick():
    cfg = settings()
    operations = call(cfg.chain_url, "GET", "/internal/pending")
    for operation in operations:
        try:
            call(
                cfg.rental_url,
                "POST",
                "/internal/result/" + operation["order_id"] + "/" + operation["action"],
            )
        except Exception as exc:
            logging.getLogger("observer-worker").warning(
                "operation_observation_failed",
                extra={"operation_id": operation["id"], "error_type": type(exc).__name__},
            )


def main():
    configure(settings().log_level)
    log = logging.getLogger("observer-worker")
    while True:
        try:
            tick()
        except Exception as exc:
            log.warning("observation_retry", extra={"error_type": type(exc).__name__})
        time.sleep(settings().poll_seconds)


if __name__ == "__main__":
    main()
