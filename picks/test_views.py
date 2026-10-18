import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from sports.providers.the_odds_api.weeks import week_for
from tests.builders import (
    make_game,
    make_league,
    make_line,
    make_season,
    make_team,
    make_user,
)

from picks.models import Pick
from picks.operations import settle_game, submit_pick

pytestmark = pytest.mark.django_db

_NOW = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)
_KICKOFF = datetime(2026, 10, 18, 17, 0, tzinfo=UTC)
_WEEK = week_for(_NOW)


def _freeze(monkeypatch):
    monkeypatch.setattr('picks.views.timezone.now', lambda: _NOW)


def _season():
    league = make_league(name='National Football League', abbreviation='NFL')
    return make_season(league, year=2026)


def _game(season, *, week=_WEEK, kickoff_at=_KICKOFF, status='scheduled', abbr=None):
    home_kwargs = {'abbreviation': abbr} if abbr else {}
    home = make_team(season.league, **home_kwargs)
    away = make_team(season.league)
    game = make_game(
        season,
        home,
        away,
        kickoff_at=kickoff_at,
        status=status,
        week=week,
    )
    return game, home, away


def _post(client, game, team, line, pick_type, **extra):
    prefix = pick_type
    return client.post(
        reverse('submit-pick', args=[game.id]),
        {
            f'{prefix}-picked_team': team.pk,
            f'{prefix}-pick_type': pick_type,
            f'{prefix}-game_line': line.pk,
        },
        **extra,
    )


def _total_line(game, **overrides):
    values = {
        'captured_at': _NOW - timedelta(hours=1),
        'game_total': Decimal('44.5'),
        'over_price': Decimal('1.910'),
        'under_price': Decimal('1.909'),
    }
    values.update(overrides)
    return make_line(game, **values)


def _post_total(client, game, line, side, **extra):
    return client.post(
        reverse('submit-pick', args=[game.id]),
        {
            'total-total_side': side,
            'total-pick_type': Pick.PickType.TOTAL,
            'total-game_line': line.pk,
        },
        **extra,
    )


def test_anonymous_requests_redirect_to_login(client):
    season = _season()
    game, _home, _away = _game(season)
    targets = [
        reverse('game-slate'),
        reverse('pick-history'),
    ]
    for url in targets:
        response = client.get(url)
        assert response.status_code == 302
        assert reverse('login') in response.url
    response = client.post(reverse('submit-pick', args=[game.id]))
    assert response.status_code == 302
    assert reverse('login') in response.url


def test_slate_shows_the_current_line_and_omits_started_games(client, monkeypatch):
    _freeze(monkeypatch)
    season = _season()
    game, home, away = _game(season, abbr='KC')
    make_line(
        game,
        home_spread=Decimal('-3.5'),
        away_spread=Decimal('3.5'),
        captured_at=_NOW - timedelta(hours=2),
    )
    make_line(
        game,
        home_spread=Decimal('-9.0'),
        away_spread=Decimal('9.0'),
        captured_at=_NOW + timedelta(seconds=1),
    )
    started, _started_home, started_away = _game(
        season,
        kickoff_at=_NOW - timedelta(minutes=1),
        abbr='OLD',
    )
    started_away.abbreviation = 'GONE'
    started_away.save(update_fields=['abbreviation'])
    make_line(started, captured_at=_NOW - timedelta(hours=1))
    later, _later_home, later_away = _game(
        season,
        week=_WEEK + 1,
        kickoff_at=_KICKOFF + timedelta(days=7),
        abbr='NXT',
    )
    later_away.abbreviation = 'LATER'
    later_away.save(update_fields=['abbreviation'])
    make_line(later)

    client.force_login(make_user())
    response = client.get(reverse('game-slate'))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'KC' in body
    assert '-3.5' in body
    assert '-9.0' not in body
    assert 'GONE' not in body
    assert 'LATER' not in body
    assert home.abbreviation in body
    assert away.abbreviation in body


def test_week_query_overrides_the_current_week(client, monkeypatch):
    _freeze(monkeypatch)
    season = _season()
    game, _home, away = _game(
        season,
        week=_WEEK + 1,
        kickoff_at=_KICKOFF + timedelta(days=7),
        abbr='WK8',
    )
    away.abbreviation = 'NEXT'
    away.save(update_fields=['abbreviation'])
    make_line(game)

    client.force_login(make_user())
    response = client.get(reverse('game-slate'), {'week': _WEEK + 1})

    assert response.status_code == 200
    assert 'NEXT' in response.content.decode()


@pytest.mark.parametrize('week', ['0', '-1', 'no', '1.5', '²', '32768'])
def test_invalid_week_is_rejected(client, monkeypatch, week):
    _freeze(monkeypatch)
    client.force_login(make_user())

    response = client.get(reverse('game-slate'), {'week': week})

    assert response.status_code == 400
    assert b'positive integer' in response.content


def test_missing_nfl_season_renders_an_empty_slate(client, monkeypatch):
    _freeze(monkeypatch)
    league = make_league(abbreviation='NFL')
    make_season(league, year=2025)
    client.force_login(make_user())

    response = client.get(reverse('game-slate'))

    assert response.status_code == 200
    assert b'NFL season is not available.' in response.content


def test_get_on_the_pick_endpoint_is_not_allowed(client):
    season = _season()
    game, _home, _away = _game(season)
    client.force_login(make_user())

    response = client.get(reverse('submit-pick', args=[game.id]))

    assert response.status_code == 405


def test_unknown_game_returns_404(client):
    client.force_login(make_user())

    response = client.post(reverse('submit-pick', args=[99999]))

    assert response.status_code == 404


@pytest.mark.parametrize(
    'pick_type',
    [Pick.PickType.SPREAD, Pick.PickType.MONEYLINE],
)
def test_submitting_a_pick_creates_it_and_redirects(client, monkeypatch, pick_type):
    _freeze(monkeypatch)
    season = _season()
    game, home, _away = _game(season)
    line = make_line(
        game,
        captured_at=_NOW - timedelta(hours=1),
        home_moneyline=Decimal('1.500'),
        away_moneyline=Decimal('2.500'),
    )
    user = make_user()
    client.force_login(user)

    response = _post(client, game, home, line, pick_type)

    assert response.status_code == 302
    assert response.url == reverse('game-slate')
    pick = Pick.objects.get(user=user, game=game, pick_type=pick_type)
    assert pick.picked_team == home
    assert pick.game_line == line


def test_htmx_submit_returns_the_locked_card_without_the_page(client, monkeypatch):
    _freeze(monkeypatch)
    season = _season()
    game, home, _away = _game(season, abbr='KC')
    line = make_line(game, captured_at=_NOW - timedelta(hours=1))
    client.force_login(make_user())

    response = _post(
        client,
        game,
        home,
        line,
        Pick.PickType.SPREAD,
        HTTP_HX_REQUEST='true',
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert 'Log out' not in body
    assert 'Submitted Spread' in body
    assert f'id="game-{game.id}"' in body
    assert '-3.5' in body


def test_htmx_duplicate_pick_returns_422_and_keeps_one_pick(client, monkeypatch):
    _freeze(monkeypatch)
    season = _season()
    game, home, _away = _game(season)
    line = make_line(game, captured_at=_NOW - timedelta(hours=1))
    user = make_user()
    client.force_login(user)
    _post(client, game, home, line, Pick.PickType.SPREAD, HTTP_HX_REQUEST='true')

    response = _post(
        client,
        game,
        home,
        line,
        Pick.PickType.SPREAD,
        HTTP_HX_REQUEST='true',
    )

    assert response.status_code == 422
    assert b'already exists' in response.content
    assert b'Log out' not in response.content
    assert Pick.objects.filter(user=user, game=game).count() == 1


def test_non_htmx_validation_error_rerenders_the_slate(client, monkeypatch):
    _freeze(monkeypatch)
    season = _season()
    game, home, _away = _game(season)
    line = make_line(game, captured_at=_NOW - timedelta(hours=1))
    user = make_user()
    client.force_login(user)
    _post(client, game, home, line, Pick.PickType.SPREAD)

    response = _post(client, game, home, line, Pick.PickType.SPREAD)

    assert response.status_code == 422
    body = response.content.decode()
    assert 'already exists' in body
    assert 'Log out' in body
    assert Pick.objects.filter(user=user, game=game).count() == 1


def test_stale_line_returns_422_and_shows_the_current_line(client, monkeypatch):
    _freeze(monkeypatch)
    season = _season()
    game, home, _away = _game(season)
    stale = make_line(
        game,
        home_spread=Decimal('-3.5'),
        away_spread=Decimal('3.5'),
        captured_at=_NOW - timedelta(hours=2),
    )
    make_line(
        game,
        home_spread=Decimal('-7.5'),
        away_spread=Decimal('7.5'),
        captured_at=_NOW - timedelta(minutes=1),
    )
    client.force_login(make_user())

    response = _post(
        client,
        game,
        home,
        stale,
        Pick.PickType.SPREAD,
        HTTP_HX_REQUEST='true',
    )

    body = response.content.decode()
    assert response.status_code == 422
    assert 'current snapshot' in body
    assert '-7.5' in body
    assert '-3.5' not in body
    assert Pick.objects.count() == 0


def test_pick_after_kickoff_returns_422(client, monkeypatch):
    season = _season()
    game, home, _away = _game(season)
    line = make_line(game, captured_at=_NOW - timedelta(hours=1))
    monkeypatch.setattr(
        'picks.views.timezone.now',
        lambda: _KICKOFF + timedelta(minutes=1),
    )
    client.force_login(make_user())

    response = _post(
        client,
        game,
        home,
        line,
        Pick.PickType.SPREAD,
        HTTP_HX_REQUEST='true',
    )

    assert response.status_code == 422
    assert b'kickoff' in response.content
    assert Pick.objects.count() == 0


def test_missing_team_and_foreign_line_are_rejected(client, monkeypatch):
    _freeze(monkeypatch)
    season = _season()
    game, _home, _away = _game(season)
    line = make_line(game, captured_at=_NOW - timedelta(hours=1))
    other, _other_home, _other_away = _game(season, abbr='ZZZ')
    other_line = make_line(other, captured_at=_NOW - timedelta(hours=1))
    outsider = make_team(season.league, abbreviation='NOPE')
    client.force_login(make_user())

    missing_team = client.post(
        reverse('submit-pick', args=[game.id]),
        {
            'spread-pick_type': Pick.PickType.SPREAD,
            'spread-game_line': line.pk,
        },
        HTTP_HX_REQUEST='true',
    )
    foreign_line = _post(
        client,
        game,
        game.home_team,
        other_line,
        Pick.PickType.SPREAD,
        HTTP_HX_REQUEST='true',
    )
    foreign_team = _post(
        client,
        game,
        outsider,
        line,
        Pick.PickType.SPREAD,
        HTTP_HX_REQUEST='true',
    )
    no_market = client.post(
        reverse('submit-pick', args=[game.id]),
        HTTP_HX_REQUEST='true',
    )

    assert missing_team.status_code == 422
    assert foreign_line.status_code == 422
    assert b'does not belong' in foreign_line.content
    assert foreign_team.status_code == 422
    assert b'valid choice' in foreign_team.content
    assert no_market.status_code == 422
    assert b'Choose a market.' in no_market.content
    assert Pick.objects.count() == 0


def test_unavailable_market_returns_422(client, monkeypatch):
    _freeze(monkeypatch)
    season = _season()
    game, home, _away = _game(season)
    line = make_line(game, captured_at=_NOW - timedelta(hours=1))
    client.force_login(make_user())

    response = _post(
        client,
        game,
        home,
        line,
        Pick.PickType.MONEYLINE,
        HTTP_HX_REQUEST='true',
    )

    assert response.status_code == 422
    assert b'Moneyline is not available' in response.content
    assert Pick.objects.count() == 0


def test_history_shows_only_the_current_users_picks_and_status(client):
    season = _season()
    game, home, away = _game(season, abbr='KC')
    away.abbreviation = 'BUF'
    away.save(update_fields=['abbreviation'])
    line = make_line(
        game,
        captured_at=_NOW - timedelta(hours=1),
        home_spread=Decimal('-3.0'),
        away_spread=Decimal('3.0'),
    )
    owner = make_user()
    other = make_user(username='other-person')
    submit_pick(
        user=owner,
        game=game,
        game_line=line,
        picked_team=home,
        pick_type=Pick.PickType.SPREAD,
        now=_NOW,
    )
    other_game, other_home, _other_away = _game(season, abbr='HID')
    other_home.abbreviation = 'SECRET'
    other_home.save(update_fields=['abbreviation'])
    other_line = make_line(other_game, captured_at=_NOW - timedelta(hours=1))
    submit_pick(
        user=other,
        game=other_game,
        game_line=other_line,
        picked_team=other_home,
        pick_type=Pick.PickType.SPREAD,
        now=_NOW,
    )
    client.force_login(owner)

    pending_page = client.get(reverse('pick-history'))

    pending_body = pending_page.content.decode()
    assert pending_page.status_code == 200
    assert 'Pending' in pending_body
    assert 'KC' in pending_body
    assert 'other-person' not in pending_body
    assert 'SECRET' not in pending_body

    game.status = 'final'
    game.home_score = 30
    game.away_score = 10
    game.save(update_fields=['status', 'home_score', 'away_score'])
    settle_game(game)
    scored_body = client.get(reverse('pick-history')).content.decode()
    assert 'Win' in scored_body
    assert '1.00' in scored_body


def test_a_total_pick_renders_its_side_on_the_slate_and_in_history(client, monkeypatch):
    _freeze(monkeypatch)
    season = _season()
    game, home, _away = _game(season, abbr='KC')
    line = make_line(
        game,
        captured_at=_NOW - timedelta(hours=1),
        game_total=Decimal('44.5'),
        over_price=Decimal('1.909'),
        under_price=Decimal('1.909'),
    )
    user = make_user()
    submit_pick(
        user=user,
        game=game,
        game_line=line,
        pick_type=Pick.PickType.SPREAD,
        picked_team=home,
        now=_NOW - timedelta(minutes=2),
    )
    submit_pick(
        user=user,
        game=game,
        game_line=line,
        pick_type=Pick.PickType.TOTAL,
        total_side=Pick.TotalSide.OVER,
        now=_NOW - timedelta(minutes=1),
    )
    client.force_login(user)

    slate_response = client.get(reverse('game-slate'))
    history_response = client.get(reverse('pick-history'))
    slate = slate_response.content.decode()
    history = history_response.content.decode()

    assert slate_response.status_code == 200
    assert history_response.status_code == 200

    assert 'KC' in slate
    assert 'Over' in slate
    assert '44.5' in slate
    assert 'KC' in history
    assert 'Over' in history
    assert '44.5' in history

    game.status = 'final'
    game.home_score = 30
    game.away_score = 20
    game.save(update_fields=['status', 'home_score', 'away_score'])
    settle_game(game)
    scored = client.get(reverse('pick-history')).content.decode()
    assert 'Over' in scored
    assert '44.5' in scored
    assert 'Win' in scored
    assert '1.00' in scored


@pytest.mark.parametrize(
    ('side', 'total', 'home_score', 'away_score', 'result', 'points'),
    [
        (Pick.TotalSide.OVER, Decimal('44.5'), 30, 20, 'Win', '1.00'),
        (Pick.TotalSide.UNDER, Decimal('44.5'), 30, 20, 'Loss', '0.00'),
        (Pick.TotalSide.OVER, Decimal('44.0'), 24, 20, 'Push', '0.50'),
    ],
)
def test_history_shows_a_graded_total(
    client,
    side,
    total,
    home_score,
    away_score,
    result,
    points,
):
    season = _season()
    game, _home, _away = _game(season, abbr='KC')
    line = _total_line(game, game_total=total)
    user = make_user()
    submit_pick(
        user=user,
        game=game,
        game_line=line,
        pick_type=Pick.PickType.TOTAL,
        total_side=side,
        now=_NOW,
    )
    game.status = 'final'
    game.home_score = home_score
    game.away_score = away_score
    game.save(update_fields=['status', 'home_score', 'away_score'])
    settle_game(game)
    client.force_login(user)

    body = client.get(reverse('pick-history')).content.decode()

    assert side.label in body
    assert str(total) in body
    assert result in body
    assert points in body


def test_slate_offers_over_under_only_when_the_line_has_a_total(client, monkeypatch):
    _freeze(monkeypatch)
    season = _season()
    priced, _priced_home, _priced_away = _game(season, abbr='KC')
    _total_line(priced)
    bare, _bare_home, bare_away = _game(season, abbr='BUF')
    bare_away.abbreviation = 'MIA'
    bare_away.save(update_fields=['abbreviation'])
    make_line(bare, captured_at=_NOW - timedelta(hours=1))
    client.force_login(make_user())

    body = client.get(reverse('game-slate')).content.decode()

    assert 'Total: 44.5' in body
    assert 'Over 1.910' in body
    assert 'Under 1.909' in body
    assert body.count('name="total-total_side"') == 2
    assert 'value="over"' in body
    assert 'value="under"' in body
    assert 'MIA' in body


@pytest.mark.parametrize('side', [Pick.TotalSide.OVER, Pick.TotalSide.UNDER])
def test_submitting_a_total_stores_the_side_and_number(client, monkeypatch, side):
    _freeze(monkeypatch)
    season = _season()
    game, _home, _away = _game(season)
    line = _total_line(game)
    user = make_user()
    client.force_login(user)

    response = _post_total(client, game, line, side)

    assert response.status_code == 302
    assert response.url == reverse('game-slate')
    pick = Pick.objects.get(user=user, game=game, pick_type=Pick.PickType.TOTAL)
    assert pick.total_side == side
    assert pick.picked_team is None
    assert pick.line_value == Decimal('44.5')
    assert pick.price is None


def test_htmx_total_submit_returns_the_locked_card(client, monkeypatch):
    _freeze(monkeypatch)
    season = _season()
    game, home, _away = _game(season, abbr='KC')
    line = _total_line(game)
    client.force_login(make_user())

    response = _post_total(
        client,
        game,
        line,
        Pick.TotalSide.OVER,
        HTTP_HX_REQUEST='true',
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert 'Log out' not in body
    assert 'Submitted Total' in body
    assert 'Over' in body
    assert '44.5' in body
    assert 'name="total-total_side"' not in body
    assert 'name="spread-picked_team"' in body
    assert home.abbreviation in body


def test_spread_and_total_stay_open_until_each_is_submitted(client, monkeypatch):
    _freeze(monkeypatch)
    season = _season()
    game, home, _away = _game(season)
    line = _total_line(game)
    client.force_login(make_user())

    spread = _post(
        client,
        game,
        home,
        line,
        Pick.PickType.SPREAD,
        HTTP_HX_REQUEST='true',
    )
    after_spread = spread.content.decode()
    assert spread.status_code == 200
    assert 'name="spread-picked_team"' not in after_spread
    assert 'name="total-total_side"' in after_spread

    total = _post_total(
        client,
        game,
        line,
        Pick.TotalSide.UNDER,
        HTTP_HX_REQUEST='true',
    )
    after_total = total.content.decode()
    assert total.status_code == 200
    assert 'Submitted Total' in after_total
    assert 'Under' in after_total
    assert 'name="total-total_side"' not in after_total
    assert Pick.objects.filter(game=game).count() == 2


def test_missing_total_side_is_rejected(client, monkeypatch):
    _freeze(monkeypatch)
    season = _season()
    game, _home, _away = _game(season)
    line = _total_line(game)
    client.force_login(make_user())

    response = client.post(
        reverse('submit-pick', args=[game.id]),
        {
            'total-pick_type': Pick.PickType.TOTAL,
            'total-game_line': line.pk,
        },
        HTTP_HX_REQUEST='true',
    )

    assert response.status_code == 422
    assert b'required' in response.content
    assert Pick.objects.count() == 0


def test_a_second_total_is_rejected(client, monkeypatch):
    _freeze(monkeypatch)
    season = _season()
    game, _home, _away = _game(season)
    line = _total_line(game)
    user = make_user()
    client.force_login(user)
    _post_total(client, game, line, Pick.TotalSide.OVER, HTTP_HX_REQUEST='true')

    response = _post_total(
        client,
        game,
        line,
        Pick.TotalSide.UNDER,
        HTTP_HX_REQUEST='true',
    )

    assert response.status_code == 422
    assert b'already exists' in response.content
    assert Pick.objects.filter(user=user, game=game).count() == 1


def test_total_without_a_posted_number_is_rejected(client, monkeypatch):
    _freeze(monkeypatch)
    season = _season()
    game, _home, _away = _game(season)
    line = make_line(game, captured_at=_NOW - timedelta(hours=1))
    client.force_login(make_user())

    response = _post_total(
        client,
        game,
        line,
        Pick.TotalSide.OVER,
        HTTP_HX_REQUEST='true',
    )

    assert response.status_code == 422
    assert b'Total is not available' in response.content
    assert Pick.objects.count() == 0


def test_stale_total_keeps_the_selected_side(client, monkeypatch):
    _freeze(monkeypatch)
    season = _season()
    game, _home, _away = _game(season)
    stale = _total_line(
        game,
        game_total=Decimal('41.0'),
        captured_at=_NOW - timedelta(hours=2),
    )
    _total_line(game, captured_at=_NOW - timedelta(minutes=1))
    client.force_login(make_user())

    response = _post_total(
        client,
        game,
        stale,
        Pick.TotalSide.UNDER,
        HTTP_HX_REQUEST='true',
    )

    body = response.content.decode()
    assert response.status_code == 422
    assert 'current snapshot' in body
    assert '44.5' in body
    assert '41.0' not in body
    assert re.search(r'value="under"[^>]*checked', body)
    assert Pick.objects.count() == 0


def test_history_is_empty_until_the_user_submits(client):
    client.force_login(make_user())

    response = client.get(reverse('pick-history'))

    assert response.status_code == 200
    assert b'No picks yet.' in response.content
