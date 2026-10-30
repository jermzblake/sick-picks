import logging

from config.logging import RedactSecretsFilter, RedactSecretsFormatter


def test_redact_secrets_filter_masks_env_secret_values(monkeypatch):
    monkeypatch.setenv('ODDS_API_KEY', 'super-secret-api-key')
    monkeypatch.setenv('SECRET_KEY', 'django-secret-value')
    monkeypatch.setenv('DATABASE_URL', 'postgres://user:pass@host/db')
    record = logging.LogRecord(
        name='test',
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='key=%s url=%s secret=%s',
        args=(
            'super-secret-api-key',
            'postgres://user:pass@host/db',
            'django-secret-value',
        ),
        exc_info=None,
    )

    assert RedactSecretsFilter().filter(record) is True
    assert record.getMessage() == 'key=[REDACTED] url=[REDACTED] secret=[REDACTED]'


def test_redact_secrets_formatter_masks_sensitive_key_value_pairs():
    record = logging.LogRecord(
        name='test',
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg='login failed password=hunter2 api_key=abc123',
        args=(),
        exc_info=None,
    )
    formatter = RedactSecretsFormatter('%(message)s')

    assert formatter.format(record) == (
        'login failed password=[REDACTED] api_key=[REDACTED]'
    )
