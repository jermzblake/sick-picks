import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from sports.providers.the_odds_api.weeks import week_window
from tests.builders import (
    make_game,
    make_league,
    make_line,
    make_season,
    make_team,
    make_user,
)

from picks.models import Pick
from picks.operations import (
    SettleReport,
    settle_game,
    settle_window,
    submit_pick,
    upcoming_games,
    user_picks,
)

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


def test_settle_game_grades_a_total():
    game, _home, _away = _matchup()
    line = make_line(
        game,
        captured_at=_EARLIER,
        game_total=Decimal('41.5'),
        over_price=Decimal('1.909'),
        under_price=Decimal('1.909'),
    )
    total = submit_pick(
        user=make_user(),
        game=game,
        game_line=line,
        pick_type=Pick.PickType.TOTAL,
        total_side=Pick.TotalSide.OVER,
        now=_NOW,
    )
    _finish(game, status='final', home_score=24, away_score=20)

    settle_game(game)

    total.refresh_from_db()
    assert total.result == Pick.Result.WIN
    assert total.points == Decimal('1.00')
    assert total.picked_team_id is None


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


def _spread(game, team):
    submitted_at = game.kickoff_at - timedelta(minutes=1)
    line = make_line(
        game,
        home_spread=Decimal('-3.0'),
        away_spread=Decimal('3.0'),
        captured_at=submitted_at - timedelta(minutes=1),
    )
    return submit_pick(
        user=make_user(),
        game=game,
        game_line=line,
        picked_team=team,
        pick_type=Pick.PickType.SPREAD,
        now=submitted_at,
    )


def test_settle_window_grades_final_games_in_the_current_week(caplog):
    start, end = week_window(_NOW)
    opening, opening_home, _opening_away = _matchup(kickoff_at=start)
    closing, closing_home, _closing_away = _matchup(
        kickoff_at=end - timedelta(seconds=1)
    )
    outside, outside_home, _outside_away = _matchup(kickoff_at=end)
    scheduled, scheduled_home, _scheduled_away = _matchup()
    opening_pick = _spread(opening, opening_home)
    closing_pick = _spread(closing, closing_home)
    outside_pick = _spread(outside, outside_home)
    scheduled_pick = _spread(scheduled, scheduled_home)
    _finish(opening, status='final', home_score=30, away_score=10)
    _finish(closing, status='final', home_score=30, away_score=10)
    _finish(outside, status='final', home_score=30, away_score=10)

    with caplog.at_level(logging.INFO, logger='picks.operations'):
        report = settle_window(now=_NOW)

    assert report == SettleReport(2, ())
    for pick in (opening_pick, closing_pick):
        pick.refresh_from_db()
        assert pick.result == Pick.Result.WIN
    outside_pick.refresh_from_db()
    scheduled_pick.refresh_from_db()
    assert outside_pick.result is None
    assert scheduled_pick.result is None
    assert any('settle_window starting' in record.message for record in caplog.records)
    assert any(
        'settle_window finished settled=2' in record.message
        for record in caplog.records
    )


def test_settle_window_uses_an_explicit_kickoff_window():
    inside, inside_home, _inside_away = _matchup(
        kickoff_at=datetime(2026, 10, 13, 7, tzinfo=UTC)
    )
    outside, outside_home, _outside_away = _matchup(
        kickoff_at=datetime(2026, 10, 20, 7, tzinfo=UTC)
    )
    inside_pick = _spread(inside, inside_home)
    outside_pick = _spread(outside, outside_home)
    _finish(inside, status='final', home_score=30, away_score=10)
    _finish(outside, status='final', home_score=30, away_score=10)

    report = settle_window(
        kickoff_from=datetime(2026, 10, 13, 7, tzinfo=UTC),
        kickoff_to=datetime(2026, 10, 20, 7, tzinfo=UTC),
        now=_NOW,
    )

    assert report.settled == 1
    inside_pick.refresh_from_db()
    outside_pick.refresh_from_db()
    assert inside_pick.result == Pick.Result.WIN
    assert outside_pick.result is None


def test_settle_window_voids_a_cancelled_game_and_keeps_going_after_a_missing_score(
    caplog,
):
    cancelled, cancelled_home, _cancelled_away = _matchup()
    unscored, unscored_home, _unscored_away = _matchup(
        kickoff_at=_KICKOFF + timedelta(hours=1)
    )
    scored, scored_home, _scored_away = _matchup(
        kickoff_at=_KICKOFF + timedelta(hours=2)
    )
    cancelled_pick = _spread(cancelled, cancelled_home)
    unscored_pick = _spread(unscored, unscored_home)
    scored_pick = _spread(scored, scored_home)
    _finish(cancelled, status='cancelled')
    _finish(unscored, status='final')
    _finish(scored, status='final', home_score=30, away_score=10)

    with caplog.at_level(logging.WARNING, logger='picks.operations'):
        report = settle_window(now=_NOW)

    assert report.settled == 2
    assert len(report.problems) == 1
    assert 'missing a score' in report.problems[0]
    cancelled_pick.refresh_from_db()
    unscored_pick.refresh_from_db()
    scored_pick.refresh_from_db()
    assert cancelled_pick.result == Pick.Result.VOID
    assert unscored_pick.result is None
    assert scored_pick.result == Pick.Result.WIN
    assert any(
        'settle_window game failed' in record.message
        and f'game_id={unscored.pk}' in record.message
        for record in caplog.records
    )


def test_settle_window_is_stable_when_run_twice():
    game, home, _away = _matchup()
    pick = _spread(game, home)
    _finish(game, status='final', home_score=30, away_score=10)

    first = settle_window(now=_NOW)
    pick.refresh_from_db()
    graded = (pick.result, pick.points)
    second = settle_window(now=_NOW)
    pick.refresh_from_db()

    assert first.settled == 1
    assert second.settled == 1
    assert (pick.result, pick.points) == graded


def test_settle_window_with_no_final_games_settles_nothing():
    report = settle_window(now=_NOW)

    assert report.settled == 0
    assert report.problems == ()


@pytest.mark.parametrize(
    ('kickoff_from', 'kickoff_to', 'message'),
    [
        (
            datetime(2026, 10, 13, 7, tzinfo=UTC),
            None,
            'together',
        ),
        (
            datetime(2026, 10, 20, 7, tzinfo=UTC),
            datetime(2026, 10, 13, 7, tzinfo=UTC),
            'earlier',
        ),
        (
            datetime(2026, 10, 13, 7),
            datetime(2026, 10, 20, 7, tzinfo=UTC),
            'timezone-aware',
        ),
        (
            datetime(2026, 10, 13, 7, tzinfo=UTC),
            datetime(2026, 10, 20, 7),
            'timezone-aware',
        ),
    ],
)
def test_settle_window_rejects_bad_bounds_before_grading(
    kickoff_from, kickoff_to, message
):
    game, home, _away = _matchup()
    pick = _spread(game, home)
    _finish(game, status='final', home_score=30, away_score=10)

    with pytest.raises(ValueError, match=message):
        settle_window(kickoff_from=kickoff_from, kickoff_to=kickoff_to, now=_NOW)

    pick.refresh_from_db()
    assert pick.result is None


def test_settle_window_rejects_a_naive_now():
    with pytest.raises(ValueError, match='timezone-aware'):
        settle_window(now=datetime(2026, 9, 13, 12))


def test_settle_games_command_prints_the_settled_count_and_problems():
    scored, scored_home, _scored_away = _matchup()
    unscored, _unscored_home, _unscored_away = _matchup(
        kickoff_at=_KICKOFF + timedelta(hours=1)
    )
    _spread(scored, scored_home)
    _finish(scored, status='final', home_score=30, away_score=10)
    _finish(unscored, status='final')
    stdout = StringIO()

    call_command(
        'settle_games',
        '--from',
        '2026-09-08T07:00:00Z',
        '--to',
        '2026-09-15T07:00:00Z',
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert 'Settled: 1' in output
    assert 'missing a score' in output


def test_settle_games_command_rejects_bad_bounds():
    with pytest.raises(CommandError, match='timezone-aware'):
        call_command(
            'settle_games',
            kickoff_from='2026-10-13T07:00:00',
            kickoff_to='2026-10-20T07:00:00',
        )

    with pytest.raises(CommandError, match='together'):
        call_command('settle_games', kickoff_from='2026-10-13T07:00:00+00:00')

    with pytest.raises(CommandError, match='earlier'):
        call_command(
            'settle_games',
            kickoff_from='2026-10-20T07:00:00+00:00',
            kickoff_to='2026-10-13T07:00:00+00:00',
        )
