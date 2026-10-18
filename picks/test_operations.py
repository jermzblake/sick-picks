from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from tests.builders import (
    make_game,
    make_league,
    make_line,
    make_season,
    make_team,
    make_user,
)

from picks.models import Pick
from picks.operations import settle_game, submit_pick, upcoming_games, user_picks

pytestmark = pytest.mark.django_db

_NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
_KICKOFF = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
_EARLIER = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
_LATER = datetime(2026, 9, 13, 15, 0, tzinfo=UTC)


def _matchup(*, status='scheduled', kickoff_at=_KICKOFF, week=1, season=None):
    league = season.league if season is not None else make_league()
    season = season or make_season(league)
    home = make_team(league)
    away = make_team(league)
    game = make_game(
        season,
        home,
        away,
        kickoff_at=kickoff_at,
        status=status,
        week=week,
    )
    return game, home, away


def _finish(game, *, status, home_score=None, away_score=None):
    game.status = status
    game.home_score = home_score
    game.away_score = away_score
    game.save(update_fields=['status', 'home_score', 'away_score'])


def test_upcoming_games_keep_only_the_newest_line_captured_by_now():
    game, _home, _away = _matchup(status='postponed')
    make_line(
        game,
        home_spread=Decimal('-3.0'),
        away_spread=Decimal('3.0'),
        captured_at=_EARLIER,
    )
    current = make_line(game, captured_at=_NOW)
    make_line(
        game,
        home_spread=Decimal('-7.0'),
        away_spread=Decimal('7.0'),
        captured_at=_NOW + timedelta(seconds=1),
    )
    started, _started_home, _started_away = _matchup(kickoff_at=_NOW)
    make_line(started)
    delayed, _delayed_home, _delayed_away = _matchup(
        status='delayed',
        kickoff_at=_NOW - timedelta(minutes=1),
    )
    make_line(delayed)
    unpriced, _unpriced_home, _unpriced_away = _matchup(
        kickoff_at=_KICKOFF + timedelta(hours=1),
    )

    slate = upcoming_games(now=_NOW)

    by_game = {item.game.id: item for item in slate}
    assert by_game[game.id].line == current
    assert by_game[unpriced.id].line is None
    assert started.id not in by_game
    assert delayed.id not in by_game


def test_upcoming_games_exclude_a_kickoff_that_is_exactly_now():
    game, _home, _away = _matchup(kickoff_at=_NOW)
    make_line(game, captured_at=_EARLIER)

    assert upcoming_games(now=_NOW) == []


@pytest.mark.parametrize('status', ['cancelled', 'final', 'in_progress'])
def test_upcoming_games_exclude_a_closed_status_even_with_a_future_kickoff(status):
    game, _home, _away = _matchup(status=status)
    make_line(game)

    assert upcoming_games(now=_NOW) == []


def test_upcoming_games_filter_by_season_and_week():
    game, _home, _away = _matchup(week=2)
    other_week, _other_home, _other_away = _matchup(season=game.season, week=3)
    other_season, _season_home, _season_away = _matchup(week=2)
    make_line(game)
    make_line(other_week)
    make_line(other_season)

    slate = upcoming_games(now=_NOW, season=game.season, week=2)

    assert [item.game.id for item in slate] == [game.id]


def test_upcoming_games_load_lines_without_a_query_per_game():
    first, _first_home, _first_away = _matchup()
    second, _second_home, _second_away = _matchup(
        season=first.season,
        kickoff_at=_KICKOFF + timedelta(hours=3),
    )
    make_line(first, captured_at=_EARLIER)
    make_line(first, captured_at=_NOW)
    make_line(second, captured_at=_EARLIER)

    with CaptureQueriesContext(connection) as queries:
        slate = upcoming_games(now=_NOW)

    assert len(slate) == 2
    assert all(item.line is not None for item in slate)
    assert len(queries) == 2


def test_submit_stores_the_selected_side_and_the_cutoff_clock():
    game, home, away = _matchup()
    line = make_line(
        game,
        home_spread=Decimal('-3.5'),
        away_spread=Decimal('3.5'),
        captured_at=_EARLIER,
    )

    pick = submit_pick(
        user=make_user(),
        game=game,
        game_line=line,
        picked_team=away,
        pick_type=Pick.PickType.SPREAD,
        now=_NOW,
    )

    pick.refresh_from_db()
    assert pick.line_value == Decimal('3.5')
    assert pick.price is None
    assert pick.game_line_id == line.id
    assert pick.submitted_at == _NOW


def test_submit_accepts_the_latest_line_when_a_later_capture_is_still_in_the_future():
    game, home, _away = _matchup()
    current = make_line(game, captured_at=_NOW)
    make_line(
        game,
        home_spread=Decimal('-7.0'),
        away_spread=Decimal('7.0'),
        captured_at=_NOW + timedelta(seconds=1),
    )

    pick = submit_pick(
        user=make_user(),
        game=game,
        game_line=current,
        picked_team=home,
        pick_type=Pick.PickType.SPREAD,
        now=_NOW,
    )

    assert pick.line_value == Decimal('-3.5')


def test_submit_uses_the_higher_id_when_captures_share_a_timestamp():
    game, home, _away = _matchup()
    older = make_line(
        game,
        home_spread=Decimal('-3.0'),
        away_spread=Decimal('3.0'),
        captured_at=_EARLIER,
    )
    newer = make_line(
        game,
        home_spread=Decimal('-6.0'),
        away_spread=Decimal('6.0'),
        captured_at=_EARLIER,
    )

    with pytest.raises(ValidationError, match='current snapshot'):
        submit_pick(
            user=make_user(),
            game=game,
            game_line=older,
            picked_team=home,
            pick_type=Pick.PickType.SPREAD,
            now=_NOW,
        )
    pick = submit_pick(
        user=make_user(),
        game=game,
        game_line=newer,
        picked_team=home,
        pick_type=Pick.PickType.SPREAD,
        now=_NOW,
    )

    assert pick.line_value == Decimal('-6.0')


def test_submit_rejects_a_stale_snapshot():
    game, home, _away = _matchup()
    stale = make_line(
        game,
        home_spread=Decimal('-3.0'),
        away_spread=Decimal('3.0'),
        captured_at=_EARLIER,
    )
    make_line(game, captured_at=_LATER)

    with pytest.raises(ValidationError, match='current snapshot'):
        submit_pick(
            user=make_user(),
            game=game,
            game_line=stale,
            picked_team=home,
            pick_type=Pick.PickType.SPREAD,
            now=_NOW + timedelta(hours=4),
        )


def test_submit_rejects_a_line_captured_after_now():
    game, home, _away = _matchup()
    future = make_line(game, captured_at=_LATER)

    with pytest.raises(ValidationError, match='current snapshot'):
        submit_pick(
            user=make_user(),
            game=game,
            game_line=future,
            picked_team=home,
            pick_type=Pick.PickType.SPREAD,
            now=_NOW,
        )


def test_submit_rejects_a_duplicate_type_and_keeps_the_first_pick():
    game, home, _away = _matchup()
    line = make_line(game, captured_at=_EARLIER)
    user = make_user()
    first = submit_pick(
        user=user,
        game=game,
        game_line=line,
        picked_team=home,
        pick_type=Pick.PickType.SPREAD,
        now=_NOW,
    )

    with pytest.raises(ValidationError, match='already exists'):
        submit_pick(
            user=user,
            game=game,
            game_line=line,
            picked_team=home,
            pick_type=Pick.PickType.SPREAD,
            now=_NOW,
        )

    assert list(Pick.objects.values_list('id', flat=True)) == [first.id]


def test_settle_game_grades_every_pick_on_a_final_game():
    game, home, _away = _matchup()
    line = make_line(
        game,
        home_spread=Decimal('-3.0'),
        away_spread=Decimal('3.0'),
        home_moneyline=Decimal('1.500'),
        away_moneyline=Decimal('2.750'),
        captured_at=_EARLIER,
    )
    spread = submit_pick(
        user=make_user(),
        game=game,
        game_line=line,
        picked_team=home,
        pick_type=Pick.PickType.SPREAD,
        now=_NOW,
    )
    moneyline = submit_pick(
        user=make_user(),
        game=game,
        game_line=line,
        picked_team=home,
        pick_type=Pick.PickType.MONEYLINE,
        now=_NOW,
    )
    _finish(game, status='final', home_score=24, away_score=20)

    settled = settle_game(game)

    assert [pick.id for pick in settled] == [spread.id, moneyline.id]
    spread.refresh_from_db()
    moneyline.refresh_from_db()
    assert spread.result == Pick.Result.WIN
    assert spread.points == Decimal('1.00')
    assert moneyline.result == Pick.Result.WIN
    assert moneyline.points == Decimal('50.00')


def test_settle_game_loads_the_game_once_for_every_pick():
    game, home, _away = _matchup()
    line = make_line(
        game,
        home_spread=Decimal('-3.0'),
        away_spread=Decimal('3.0'),
        home_moneyline=Decimal('1.500'),
        away_moneyline=Decimal('2.750'),
        captured_at=_EARLIER,
    )
    submit_pick(
        user=make_user(),
        game=game,
        game_line=line,
        picked_team=home,
        pick_type=Pick.PickType.SPREAD,
        now=_NOW,
    )
    submit_pick(
        user=make_user(),
        game=game,
        game_line=line,
        picked_team=home,
        pick_type=Pick.PickType.MONEYLINE,
        now=_NOW,
    )
    _finish(game, status='final', home_score=24, away_score=20)

    with CaptureQueriesContext(connection) as queries:
        settle_game(game)

    game_reads = [
        query['sql'] for query in queries if 'FROM "sports_game"' in query['sql']
    ]
    assert len(game_reads) == 1


def test_settle_game_voids_every_pick_when_the_game_is_cancelled():
    game, home, _away = _matchup()
    line = make_line(game, captured_at=_EARLIER)
    pick = submit_pick(
        user=make_user(),
        game=game,
        game_line=line,
        picked_team=home,
        pick_type=Pick.PickType.SPREAD,
        now=_NOW,
    )
    _finish(game, status='cancelled', home_score=21, away_score=14)

    settle_game(game)

    pick.refresh_from_db()
    assert pick.result == Pick.Result.VOID
    assert pick.points is None


def test_settle_game_leaves_a_postponed_game_unscored():
    game, home, _away = _matchup(status='postponed')
    line = make_line(game, captured_at=_EARLIER)
    pick = submit_pick(
        user=make_user(),
        game=game,
        game_line=line,
        picked_team=home,
        pick_type=Pick.PickType.SPREAD,
        now=_NOW,
    )
    game.home_score = 21
    game.away_score = 14
    game.save(update_fields=['home_score', 'away_score'])

    settled = settle_game(game)

    assert settled[0].result is None
    pick.refresh_from_db()
    assert pick.result is None
    assert pick.points is None


def test_settle_game_is_stable_when_run_twice():
    game, home, _away = _matchup()
    line = make_line(
        game,
        home_spread=Decimal('-3.0'),
        away_spread=Decimal('3.0'),
        captured_at=_EARLIER,
    )
    pick = submit_pick(
        user=make_user(),
        game=game,
        game_line=line,
        picked_team=home,
        pick_type=Pick.PickType.SPREAD,
        now=_NOW,
    )
    _finish(game, status='final', home_score=30, away_score=10)
    settle_game(game)
    pick.result = Pick.Result.LOSS
    pick.points = Decimal('9.99')
    pick.save(update_fields=['result', 'points'])

    settle_game(game)

    pick.refresh_from_db()
    assert pick.result == Pick.Result.WIN
    assert pick.points == Decimal('1.00')


def test_settle_game_does_not_save_a_partial_grade():
    game, home, away = _matchup()
    line = make_line(
        game,
        home_spread=Decimal('-3.0'),
        away_spread=Decimal('3.0'),
        captured_at=_EARLIER,
    )
    first = submit_pick(
        user=make_user(),
        game=game,
        game_line=line,
        picked_team=home,
        pick_type=Pick.PickType.SPREAD,
        now=_NOW,
    )
    second = submit_pick(
        user=make_user(),
        game=game,
        game_line=line,
        picked_team=away,
        pick_type=Pick.PickType.SPREAD,
        now=_NOW,
    )
    _finish(game, status='final', home_score=30, away_score=10)
    second.picked_team = make_team(game.season.league)
    second.save(update_fields=['picked_team'])

    with pytest.raises(ValidationError, match='not playing'):
        settle_game(game)

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.result is None
    assert first.points is None
    assert second.result is None
    assert second.points is None


def test_settle_game_rejects_a_final_game_without_scores():
    game, home, _away = _matchup()
    line = make_line(game, captured_at=_EARLIER)
    pick = submit_pick(
        user=make_user(),
        game=game,
        game_line=line,
        picked_team=home,
        pick_type=Pick.PickType.SPREAD,
        now=_NOW,
    )
    _finish(game, status='final')

    with pytest.raises(ValidationError, match='missing a score'):
        settle_game(game)

    pick.refresh_from_db()
    assert pick.result is None
    assert pick.points is None


def test_user_picks_are_newest_first_and_exclude_other_users():
    game, home, away = _matchup()
    line = make_line(
        game,
        captured_at=_EARLIER,
        home_moneyline=Decimal('1.500'),
        away_moneyline=Decimal('2.500'),
    )
    owner = make_user()
    other = make_user()
    older = submit_pick(
        user=owner,
        game=game,
        game_line=line,
        picked_team=home,
        pick_type=Pick.PickType.SPREAD,
        now=_EARLIER,
    )
    newer = submit_pick(
        user=owner,
        game=game,
        game_line=line,
        picked_team=away,
        pick_type=Pick.PickType.MONEYLINE,
        now=_NOW,
    )
    submit_pick(
        user=other,
        game=game,
        game_line=line,
        picked_team=home,
        pick_type=Pick.PickType.SPREAD,
        now=_NOW,
    )

    assert list(user_picks(owner)) == [newer, older]
    assert list(user_picks(owner, games=[])) == []
    assert list(user_picks(owner, games=[game])) == [newer, older]


def test_settle_game_with_no_picks_returns_an_empty_list():
    game, _home, _away = _matchup()
    _finish(game, status='final', home_score=17, away_score=14)

    assert settle_game(game) == []
