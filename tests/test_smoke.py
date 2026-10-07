import pytest
from django.conf import settings
from django.db import connection


def test_django_settings_are_configured() -> None:
    assert settings.configured


@pytest.mark.django_db
def test_database_accepts_connections() -> None:
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
        row = cursor.fetchone()
    assert row == (1,)
