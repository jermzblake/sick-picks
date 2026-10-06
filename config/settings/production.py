"""Render deployment settings. Environment variables come from the platform."""

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *

DEBUG = False

SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    raise ImproperlyConfigured('SECRET_KEY is not set.')

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get('ALLOWED_HOSTS', '').split(',')
    if host.strip()
]
_render_hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
if _render_hostname and _render_hostname not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_render_hostname)
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        'ALLOWED_HOSTS is not set. Provide a comma-separated list or RENDER_EXTERNAL_HOSTNAME.'
    )

_database_url = os.environ.get('DATABASE_URL')
if not _database_url:
    raise ImproperlyConfigured('DATABASE_URL is not set.')

DATABASES = {
    'default': database_from_url(_database_url, conn_max_age=0),
}

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_TRUSTED_ORIGINS = [
    f'https://{host}' for host in ALLOWED_HOSTS if not host.startswith(('.', '*'))
]

STATIC_ROOT = BASE_DIR / 'staticfiles'
