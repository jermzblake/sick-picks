"""Settings shared by every environment."""

from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent.parent

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True

STATIC_URL = 'static/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


def database_from_url(url, *, conn_max_age):
    """Parse a postgres:// or postgresql:// URL into a Django database config.

    Query parameters such as sslmode are copied into OPTIONS only when the URL
    includes them.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {'postgres', 'postgresql'}:
        raise ValueError('DATABASE_URL must start with postgres:// or postgresql://.')

    name = unquote(parsed.path.lstrip('/'))
    if not name:
        raise ValueError('DATABASE_URL is missing a database name.')

    config = {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': name,
        'USER': unquote(parsed.username) if parsed.username else '',
        'PASSWORD': unquote(parsed.password) if parsed.password else '',
        'HOST': parsed.hostname or '',
        'PORT': str(parsed.port) if parsed.port else '',
        'CONN_MAX_AGE': conn_max_age,
    }
    options = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if options:
        config['OPTIONS'] = options
    return config
