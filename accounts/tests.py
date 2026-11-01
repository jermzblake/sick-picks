import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from tests.builders import make_user

pytestmark = pytest.mark.django_db

User = get_user_model()

_VALID_PASSWORD = 'Correct-Horse-Battery1'


def test_signup_get_returns_200(client):
    response = client.get(reverse('signup'))

    assert response.status_code == 200


def test_signup_valid_post_creates_user_logs_in_and_redirects(client):
    response = client.post(
        reverse('signup'),
        {
            'username': 'newpicker',
            'password1': _VALID_PASSWORD,
            'password2': _VALID_PASSWORD,
        },
    )

    assert response.status_code == 302
    assert response['Location'] == reverse('game-slate')
    user = User.objects.get(username='newpicker')
    assert int(client.session['_auth_user_id']) == user.pk


def test_signup_honors_safe_next_redirect(client):
    next_url = reverse('pick-history')
    response = client.post(
        reverse('signup'),
        {
            'username': 'nextpicker',
            'password1': _VALID_PASSWORD,
            'password2': _VALID_PASSWORD,
            'next': next_url,
        },
    )

    assert response.status_code == 302
    assert response['Location'] == next_url


def test_login_page_signup_link_forwards_next(client):
    next_url = reverse('pick-history')
    response = client.get(reverse('login'), {'next': next_url})

    assert response.status_code == 200
    assert f'{reverse("signup")}?next={next_url}' in response.content.decode()


def test_signup_duplicate_username_shows_errors(client):
    make_user(username='taken')

    response = client.post(
        reverse('signup'),
        {
            'username': 'taken',
            'password1': _VALID_PASSWORD,
            'password2': _VALID_PASSWORD,
        },
    )

    assert response.status_code == 200
    assert response.context['form'].errors
    assert User.objects.filter(username='taken').count() == 1
    assert '_auth_user_id' not in client.session


def test_signup_password_mismatch_shows_errors(client):
    response = client.post(
        reverse('signup'),
        {
            'username': 'newpicker',
            'password1': _VALID_PASSWORD,
            'password2': 'Different-Password-Xyz1',
        },
    )

    assert response.status_code == 200
    assert response.context['form'].errors
    assert not User.objects.filter(username='newpicker').exists()
    assert '_auth_user_id' not in client.session


def test_signup_rejects_password_missing_complexity(client):
    response = client.post(
        reverse('signup'),
        {
            'username': 'newpicker',
            'password1': 'alllowercase1',
            'password2': 'alllowercase1',
        },
    )

    assert response.status_code == 200
    assert response.context['form'].errors
    assert not User.objects.filter(username='newpicker').exists()
    assert '_auth_user_id' not in client.session


def test_signup_rejects_short_password(client):
    response = client.post(
        reverse('signup'),
        {
            'username': 'newpicker',
            'password1': 'Ab1',
            'password2': 'Ab1',
        },
    )

    assert response.status_code == 200
    assert response.context['form'].errors
    assert not User.objects.filter(username='newpicker').exists()


def test_signup_redirects_authenticated_user(client):
    user = make_user()
    client.force_login(user)

    response = client.get(reverse('signup'))

    assert response.status_code == 302
    assert response['Location'] == reverse('game-slate')


def test_login_page_links_to_signup(client):
    response = client.get(reverse('login'))

    assert response.status_code == 200
    assert reverse('signup') in response.content.decode()
