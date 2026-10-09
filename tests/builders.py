from datetime import UTC, datetime
from decimal import Decimal
from itertools import count

from django.contrib.auth import get_user_model
from sports.models import Game, GameLine, League, Season, Team

_ids = count(1)


def make_league(*, name=None, abbreviation=None, sport='football'):
    number = next(_ids)
    return League.objects.create(
        name=name or f'League {number}',
        abbreviation=abbreviation or f'L{number}',
        sport=sport,
    )


def make_season(league, year=2026):
    return Season.objects.create(league=league, year=year)


def make_team(league, *, external_id=None, name=None, abbreviation=None):
    number = next(_ids)
    return Team.objects.create(
        league=league,
        external_id=external_id or f'team-{number}',
        name=name or f'Team {number}',
        abbreviation=abbreviation or f'T{number}',
    )


def make_game(
    season,
    home_team,
    away_team,
    *,
    external_id=None,
    kickoff_at=None,
    status=Game.Status.SCHEDULED,
    week=1,
    home_score=None,
    away_score=None,
):
    number = next(_ids)
    return Game.objects.create(
        external_id=external_id or f'game-{number}',
        season=season,
        week=week,
        home_team=home_team,
        away_team=away_team,
        kickoff_at=kickoff_at or datetime(2026, 9, 13, 17, 0, tzinfo=UTC),
        status=status,
        home_score=home_score,
        away_score=away_score,
    )


def make_line(
    game,
    *,
    captured_at=None,
    home_spread=Decimal('-3.5'),
    away_spread=Decimal('3.5'),
    home_spread_price=Decimal('1.909'),
    away_spread_price=Decimal('1.909'),
    home_moneyline=None,
    away_moneyline=None,
    source='provider',
    bookmaker='book',
):
    return GameLine.objects.create(
        game=game,
        source=source,
        bookmaker=bookmaker,
        home_spread=home_spread,
        away_spread=away_spread,
        home_spread_price=home_spread_price,
        away_spread_price=away_spread_price,
        home_moneyline=home_moneyline,
        away_moneyline=away_moneyline,
        captured_at=captured_at or datetime(2026, 9, 12, 12, 0, tzinfo=UTC),
    )


def make_user(username=None):
    number = next(_ids)
    return get_user_model().objects.create_user(
        username=username or f'user-{number}',
        password='secret',
    )
