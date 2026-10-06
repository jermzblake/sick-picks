"""Development conveniences for a local Mac."""

import os

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

from .base import *

load_dotenv(BASE_DIR / '.env')

DEBUG = True

SECRET_KEY = 'django-insecure-$xh@yfg)fni2lb0gb58sumk=7am#n$si=sy(e%l4(v9!d@!$3e'

ALLOWED_HOSTS = ['localhost', '127.0.0.1']

_database_url = os.environ.get('DATABASE_URL')
if not _database_url:
    raise ImproperlyConfigured(
        'DATABASE_URL is not set. Copy .env.example to .env and point it at your local Postgres.'
    )

DATABASES = {
    'default': database_from_url(_database_url, conn_max_age=60),
}
