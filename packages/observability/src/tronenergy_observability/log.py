import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone

request_id_context = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": record.name,
            "event": record.getMessage(),
            "request_id": request_id_context.get(),
        }
        # Only explicitly selected attributes are emitted; never serialize request bodies or secrets.
        for key in (
            "request_id",
            "order_id",
            "operation_id",
            "txid",
            "duration_ms",
            "error_type",
            "status_code",
            "state",
        ):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        return json.dumps(data, ensure_ascii=False)


def configure(level="INFO"):
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
