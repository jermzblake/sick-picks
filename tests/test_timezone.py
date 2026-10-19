from datetime import UTC, datetime, timedelta

import pytest
from django.urls import reverse
from picks.models import Pick
from picks.operations import submit_pick
from sports.providers.the_odds_api.weeks import week_for

from tests.builders import (
    make_game,
    make_league,
    make_line,
    make_season,
    make_team,
    make_user,
)

pytestmark = pytest.mark.django_db

_NOW = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)
_KICKOFF = datetime(2026, 10, 18, 17, 0, tzinfo=UTC)
_EASTERN = 'Sun, Oct 18, 13:00 EDT'
_MOUNTAIN = 'Sun, Oct 18, 11:00 MDT'


def _freeze(monkeypatch):
    monkeypatch.setattr('picks.views.timezone.now', lambda: _NOW)


def _open_game():
    league = make_league(name='National Football League', abbreviation='NFL')
    season = make_season(league, year=2026)
    home = make_team(league)
    away = make_team(league)
    game = make_game(
        season,
        home,
        away,
        kickoff_at=_KICKOFF,
        week=week_for(_NOW),
    )
    make_line(game, captured_at=_NOW - timedelta(hours=1))
    return game, home


def test_slate_defaults_kickoff_to_eastern(client, monkeypatch):
    _freeze(monkeypatch)
    _open_game()
    client.force_login(make_user())

    body = client.get(reverse('game-slate')).content.decode()

    assert _EASTERN in body
    assert '17:00' not in body


def test_slate_uses_a_timezone_cookie(client, monkeypatch):
    _freeze(monkeypatch)
    _open_game()
    client.force_login(make_user())
    client.cookies['timezone'] = 'America/Denver'

    body = client.get(reverse('game-slate')).content.decode()

    assert _MOUNTAIN in body


def test_slate_accepts_a_percent_encoded_timezone_cookie(client, monkeypatch):
    _freeze(monkeypatch)
    _open_game()
    client.force_login(make_user())
    client.cookies['timezone'] = 'America%2FDenver'

    body = client.get(reverse('game-slate')).content.decode()

    assert _MOUNTAIN in body


def test_slate_ignores_an_unknown_timezone_cookie(client, monkeypatch):
    _freeze(monkeypatch)
    _open_game()
    client.force_login(make_user())
    client.cookies['timezone'] = 'Not/AZone'

    response = client.get(reverse('game-slate'))

    assert response.status_code == 200
    assert _EASTERN in response.content.decode()


def test_htmx_card_uses_the_timezone_cookie(client, monkeypatch):
    _freeze(monkeypatch)
    game, home = _open_game()
    line = game.lines.get()
    client.force_login(make_user())
    client.cookies['timezone'] = 'America/Denver'

    response = client.post(
        reverse('submit-pick', args=[game.id]),
        {
            'spread-picked_team': home.pk,
            'spread-pick_type': Pick.PickType.SPREAD,
            'spread-game_line': line.pk,
        },
        HTTP_HX_REQUEST='true',
    )

    assert response.status_code == 200
    assert _MOUNTAIN in response.content.decode()


def test_history_uses_the_same_clock(client, monkeypatch):
    _freeze(monkeypatch)
    game, home = _open_game()
    user = make_user()
    pick = submit_pick(
        user=user,
        game=game,
        game_line=game.lines.get(),
        picked_team=home,
        pick_type=Pick.PickType.SPREAD,
        now=_NOW,
    )
    pick.submitted_at = _KICKOFF
    pick.save(update_fields=['submitted_at'])
    client.force_login(user)
    client.cookies['timezone'] = 'America/Denver'

    body = client.get(reverse('pick-history')).content.decode()

    assert _MOUNTAIN in body


def test_public_timezone_does_not_leak_into_admin(admin_client, monkeypatch):
    _freeze(monkeypatch)
    game, _home = _open_game()
    admin_client.cookies['timezone'] = 'America/Denver'
    admin_client.get(reverse('game-slate'))
    del admin_client.cookies['timezone']

    page = admin_client.get(reverse('admin:sports_game_change', args=[game.pk]))

    assert b'value="17:00:00"' in page.content
    assert b'value="11:00:00"' not in page.content
