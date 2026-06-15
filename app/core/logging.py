import logging
import sys
import structlog
from app.core.config import settings


def configure_logging() -> None:
    """
    Configure structlog to emit newline-delimited JSON to stdout.
    Call once at startup before any log statements.
    CloudWatch Logs Insights can query these JSON lines directly.
    """
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level)

    # Silence noisy third-party loggers
    for noisy in ("uvicorn.access", "httpx", "boto3", "botocore",
                  "urllib3", "s3transfer", "paddle"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """Return a structlog logger bound with the given module name."""
    return structlog.get_logger(name)
