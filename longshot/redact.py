"""Keeps secrets out of logs and error messages.

The Odds API takes its key as a URL parameter, so any logged URL or HTTP
error would otherwise carry it.
"""

from __future__ import annotations

import logging
import re

_API_KEY_PARAM = re.compile(r"(apiKey=)[^&\s'\"]+", re.IGNORECASE)
_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Remember a secret value so redact() removes it wherever it appears."""
    if value and len(value) >= 8:
        _secrets.add(value)


def redact(text: object) -> str:
    out = _API_KEY_PARAM.sub(r"\1[redacted]", str(text))
    for secret in _secrets:
        out = out.replace(secret, "[redacted]")
    return out


class RedactingFilter(logging.Filter):
    """Scrubs every log record, including ones from third-party libraries."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info and record.exc_info[1] is not None and not record.exc_text:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        return True


def install() -> None:
    """Attach the filter to every handler on the root logger."""
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactingFilter())
