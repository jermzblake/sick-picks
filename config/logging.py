"""Logging helpers for stdout output without leaking secrets."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

_SECRET_ENV_KEYS = ('SECRET_KEY', 'DATABASE_URL', 'ODDS_API_KEY')
_SENSITIVE_KEY_PATTERN = re.compile(
    r'(?i)\b(password|secret|apikey|api_key|authorization|csrfmiddlewaretoken)\b'
    r'(\s*[=:]\s*)(\S+)'
)


class RedactSecretsFilter(logging.Filter):
    """Redact known secret values from log message templates and args."""

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = _secret_values()
        record.msg = _redact_secrets(str(record.msg), secrets)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: _redact_arg(value, secrets)
                    for key, value in record.args.items()
                }
            else:
                record.args = tuple(_redact_arg(arg, secrets) for arg in record.args)
        return True


class RedactSecretsFormatter(logging.Formatter):
    """Format a record, then redact secrets and sensitive key=value pairs."""

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return _redact_text(formatted, _secret_values())


def _secret_values() -> list[str]:
    return [value for key in _SECRET_ENV_KEYS if (value := os.environ.get(key))]


def _redact_arg(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        return _redact_secrets(value, secrets)
    return value


def _redact_secrets(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, '[REDACTED]')
    return redacted


def _redact_text(text: str, secrets: list[str]) -> str:
    return _SENSITIVE_KEY_PATTERN.sub(
        r'\1\2[REDACTED]',
        _redact_secrets(text, secrets),
    )
