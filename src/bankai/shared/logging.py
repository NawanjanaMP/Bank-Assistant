"""Structured JSON logging with a last-line-of-defence PII scrubber.

Application code should never log raw customer data, but a processor here masks
anything that looks like an account/card number or email in case it slips through.
"""

from __future__ import annotations

import logging
import re
import sys

import structlog

_SCRUB_PATTERNS = [
    (re.compile(r"\b\d{12,19}\b"), "[NUM]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    (re.compile(r"\b\d{9}[VvXx]\b"), "[NIC]"),
]
_SENSITIVE_KEYS = {"authorization", "token", "access_token", "password", "api_key", "secret"}


def _scrub(value: object) -> object:
    if isinstance(value, str):
        for pattern, repl in _SCRUB_PATTERNS:
            value = pattern.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k.lower() in _SENSITIVE_KEYS else _scrub(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(v) for v in value]
    return value


def _scrub_processor(_logger: object, _name: str, event_dict: dict) -> dict:
    return _scrub(event_dict)  # type: ignore[return-value]


def configure_logging(level: str = "INFO", service: str = "bankai") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _scrub_processor,
            structlog.processors.EventRenamer("message"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
