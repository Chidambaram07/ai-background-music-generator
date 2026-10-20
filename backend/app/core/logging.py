import json
import logging
import sys
from datetime import datetime, timezone
from contextvars import ContextVar

correlation_context = ContextVar("correlation_id", default=None)
job_context = ContextVar("job_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for key in ("job_id", "correlation_id"):
            payload[key] = getattr(record, key, None) or getattr(record, f"context_{key}", None)
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
            payload["error_code"] = getattr(record.exc_info[1], "code", "INTERNAL_SERVER_ERROR")
        for key in ("method","path","status","code","provider"):
            if hasattr(record,key):
                payload[key] = getattr(record,key)
        return json.dumps(payload, separators=(",", ":"))


def configure_logging() -> None:
    original_factory = logging.getLogRecordFactory()
    def contextual_record(*args, **kwargs):
        record = original_factory(*args, **kwargs)
        record.context_job_id = job_context.get()
        record.context_correlation_id = correlation_context.get()
        return record
    logging.setLogRecordFactory(contextual_record)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
