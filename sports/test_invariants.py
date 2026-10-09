from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from tests.builders import make_game, make_league, make_line, make_season, make_team

from sports.models import Game, GameLine

pytestmark = pytest.mark.django_db


def _slate():
    league = make_league()
    season = make_season(league)
    home = make_team(league)
    away = make_team(league)
    return season, home, away


def test_game_rejects_home_team_equal_to_away_team():
    season, home, _away = _slate()
    with pytest.raises(IntegrityError), transaction.atomic():
        make_game(season, home, home)


def test_game_rejects_duplicate_external_id():
    season, home, away = _slate()
    other = make_team(season.league)
    make_game(season, home, away, external_id='provider-1')
    with pytest.raises(IntegrityError), transaction.atomic():
        make_game(season, away, other, external_id='provider-1')


def test_team_external_id_is_unique_within_a_league():
    league = make_league()
    make_team(league, external_id='shared', abbreviation='AAA')
    with pytest.raises(IntegrityError), transaction.atomic():
        make_team(league, external_id='shared', abbreviation='BBB')


def test_team_external_id_can_repeat_in_another_league():
    first = make_league()
    second = make_league()
    make_team(first, external_id='shared', abbreviation='AAA')
    other = make_team(second, external_id='shared', abbreviation='AAA')
    assert other.external_id == 'shared'


def test_game_rejects_unknown_status():
    season, home, away = _slate()
    with pytest.raises(IntegrityError), transaction.atomic():
        make_game(season, home, away, status='postseason')


def test_game_rejects_missing_season():
    _season, home, away = _slate()
    with pytest.raises(IntegrityError), transaction.atomic():
        Game.objects.create(
            external_id='no-season',
            week=1,
            home_team=home,
            away_team=away,
            kickoff_at=datetime(2026, 9, 13, 17, 0, tzinfo=UTC),
            status=Game.Status.SCHEDULED,
        )


def test_game_line_rejects_missing_game():
    with pytest.raises(IntegrityError), transaction.atomic():
        GameLine.objects.create(
            source='provider',
            bookmaker='book',
            home_spread=Decimal('-3.0'),
            away_spread=Decimal('3.0'),
            captured_at=datetime(2026, 9, 12, 12, 0, tzinfo=UTC),
        )


def test_game_line_rejects_missing_captured_at():
    season, home, away = _slate()
    game = make_game(season, home, away)
    with pytest.raises(IntegrityError), transaction.atomic():
        GameLine.objects.create(
            game=game,
            source='provider',
            bookmaker='book',
            home_spread=Decimal('-3.0'),
            away_spread=Decimal('3.0'),
        )


def test_game_line_rejects_snapshot_with_no_market():
    season, home, away = _slate()
    game = make_game(season, home, away)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_line(
            game,
            home_spread=None,
            away_spread=None,
            home_spread_price=None,
            away_spread_price=None,
        )


def test_game_line_rejects_one_sided_spread():
    season, home, away = _slate()
    game = make_game(season, home, away)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_line(game, home_spread=Decimal('-3.0'), away_spread=None)


def test_game_line_rejects_spreads_that_are_not_opposites():
    season, home, away = _slate()
    game = make_game(season, home, away)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_line(
            game,
            home_spread=Decimal('-3.0'),
            away_spread=Decimal('2.5'),
        )


def test_game_line_rejects_moneyline_at_or_below_one():
    season, home, away = _slate()
    game = make_game(season, home, away)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_line(
            game,
            home_spread=None,
            away_spread=None,
            home_spread_price=None,
            away_spread_price=None,
            home_moneyline=Decimal('1.000'),
            away_moneyline=Decimal('2.000'),
        )


def test_game_line_rejects_spread_price_at_or_below_one():
    season, home, away = _slate()
    game = make_game(season, home, away)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_line(game, home_spread_price=Decimal('1.000'))


def test_game_line_accepts_spread_only_snapshot():
    season, home, away = _slate()
    game = make_game(season, home, away)
    line = make_line(
        game,
        home_spread=Decimal('-3.5'),
        away_spread=Decimal('3.5'),
        home_moneyline=None,
        away_moneyline=None,
    )
    assert line.home_spread == Decimal('-3.5')
    assert line.home_moneyline is None


def test_game_line_accepts_moneyline_only_snapshot():
    season, home, away = _slate()
    game = make_game(season, home, away)
    line = make_line(
        game,
        home_spread=None,
        away_spread=None,
        home_spread_price=None,
        away_spread_price=None,
        home_moneyline=Decimal('1.500'),
        away_moneyline=Decimal('2.750'),
    )
    assert line.home_moneyline == Decimal('1.500')
    assert line.home_spread is None
