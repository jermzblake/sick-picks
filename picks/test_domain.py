from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from tests.builders import (
    make_game,
    make_league,
    make_line,
    make_season,
    make_team,
    make_user,
)

from picks.domain import settle_pick, submit_pick
from picks.models import Pick

pytestmark = pytest.mark.django_db

_KICKOFF = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
_BEFORE = _KICKOFF - timedelta(seconds=1)


def _matchup(*, status=None, kickoff_at=_KICKOFF, home_score=None, away_score=None):
    league = make_league()
    season = make_season(league)
    home = make_team(league)
    away = make_team(league)
    game = make_game(
        season,
        home,
        away,
        kickoff_at=kickoff_at,
        status=status or 'scheduled',
        home_score=home_score,
        away_score=away_score,
    )
    return game, home, away


def _submit(game, home, *, pick_type, team=None, line=None, now=_BEFORE):
    return submit_pick(
        user=make_user(),
        game=game,
        game_line=line or make_line(game),
        picked_team=team or home,
        pick_type=pick_type,
        now=now,
    )


def test_second_spread_for_the_same_user_and_game_is_rejected():
    game, home, _away = _matchup()
    user = make_user()
    line = make_line(game)
    submit_pick(
        user=user,
        game=game,
        game_line=line,
        picked_team=home,
        pick_type=Pick.PickType.SPREAD,
        now=_BEFORE,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        submit_pick(
            user=user,
            game=game,
            game_line=line,
            picked_team=home,
            pick_type=Pick.PickType.SPREAD,
            now=_BEFORE,
        )


def test_user_can_submit_spread_and_moneyline_on_the_same_game():
    game, home, _away = _matchup()
    user = make_user()
    line = make_line(
        game,
        home_moneyline=Decimal('1.500'),
        away_moneyline=Decimal('2.750'),
    )
    spread = submit_pick(
        user=user,
        game=game,
        game_line=line,
        picked_team=home,
        pick_type=Pick.PickType.SPREAD,
        now=_BEFORE,
    )
    moneyline = submit_pick(
        user=user,
        game=game,
        game_line=line,
        picked_team=home,
        pick_type=Pick.PickType.MONEYLINE,
        now=_BEFORE,
    )
    assert spread.pick_type == Pick.PickType.SPREAD
    assert moneyline.pick_type == Pick.PickType.MONEYLINE


def test_submit_rejects_a_team_that_is_not_in_the_game():
    game, _home, _away = _matchup()
    outsider = make_team(game.season.league)
    with pytest.raises(ValidationError, match='not playing'):
        _submit(game, outsider, pick_type=Pick.PickType.SPREAD, team=outsider)


def test_submit_rejects_a_line_from_another_game():
    game, home, _away = _matchup()
    other, _other_home, _other_away = _matchup()
    other_line = make_line(other)
    with pytest.raises(ValidationError, match='does not belong'):
        submit_pick(
            user=make_user(),
            game=game,
            game_line=other_line,
            picked_team=home,
            pick_type=Pick.PickType.SPREAD,
            now=_BEFORE,
        )


def test_submit_rejects_at_and_after_kickoff():
    game, home, _away = _matchup()
    with pytest.raises(ValidationError, match='kickoff'):
        _submit(game, home, pick_type=Pick.PickType.SPREAD, now=_KICKOFF)
    later = _KICKOFF + timedelta(minutes=1)
    with pytest.raises(ValidationError, match='kickoff'):
        _submit(game, home, pick_type=Pick.PickType.SPREAD, now=later)


def test_postponed_game_with_a_future_kickoff_still_accepts_a_pick():
    game, home, _away = _matchup(status='postponed', kickoff_at=_KICKOFF)
    pick = _submit(game, home, pick_type=Pick.PickType.SPREAD, now=_BEFORE)
    assert pick.line_value == Decimal('-3.5')


def test_delayed_game_rejects_submission_after_the_stored_kickoff():
    game, home, _away = _matchup(status='delayed', kickoff_at=_KICKOFF)
    with pytest.raises(ValidationError, match='kickoff'):
        _submit(
            game,
            home,
            pick_type=Pick.PickType.SPREAD,
            now=_KICKOFF + timedelta(hours=2),
        )


def test_spread_pick_keeps_the_selected_side_of_that_snapshot():
    game, home, away = _matchup()
    line = make_line(
        game,
        home_spread=Decimal('-3.5'),
        away_spread=Decimal('3.5'),
    )
    home_pick = _submit(
        game,
        home,
        pick_type=Pick.PickType.SPREAD,
        team=home,
        line=line,
    )
    away_pick = _submit(
        game,
        away,
        pick_type=Pick.PickType.SPREAD,
        team=away,
        line=line,
    )
    assert home_pick.line_value == Decimal('-3.5')
    assert home_pick.price is None
    assert away_pick.line_value == Decimal('3.5')


def test_moneyline_pick_keeps_the_selected_side_of_that_snapshot():
    game, home, _away = _matchup()
    line = make_line(
        game,
        home_moneyline=Decimal('1.500'),
        away_moneyline=Decimal('2.750'),
    )
    pick = _submit(
        game,
        home,
        pick_type=Pick.PickType.MONEYLINE,
        line=line,
    )
    assert pick.price == Decimal('1.500')
    assert pick.line_value is None


def test_a_later_line_does_not_change_a_submitted_pick():
    game, home, _away = _matchup()
    original = make_line(game, home_spread=Decimal('-3.0'), away_spread=Decimal('3.0'))
    pick = _submit(
        game,
        home,
        pick_type=Pick.PickType.SPREAD,
        line=original,
    )
    make_line(
        game,
        home_spread=Decimal('-7.0'),
        away_spread=Decimal('7.0'),
        captured_at=datetime(2026, 9, 13, 12, 0, tzinfo=UTC),
    )
    pick.refresh_from_db()
    assert pick.line_value == Decimal('-3.0')
    assert pick.game_line_id == original.id


def test_unknown_pick_type_is_rejected():
    game, home, _away = _matchup()
    with pytest.raises(ValidationError, match='Unknown pick type'):
        _submit(game, home, pick_type='total')


def test_spread_submit_rejects_a_line_without_a_spread():
    game, home, _away = _matchup()
    line = make_line(
        game,
        home_spread=None,
        away_spread=None,
        home_spread_price=None,
        away_spread_price=None,
        home_moneyline=Decimal('1.800'),
        away_moneyline=Decimal('2.100'),
    )
    with pytest.raises(ValidationError, match='Spread is not available'):
        _submit(game, home, pick_type=Pick.PickType.SPREAD, line=line)


def test_final_spread_cover_scores_one_point():
    game, _home, away = _matchup(
        status='final',
        home_score=20,
        away_score=24,
    )
    pick = _submit(game, away, pick_type=Pick.PickType.SPREAD, team=away)
    settle_pick(pick)
    assert pick.result == Pick.Result.WIN
    assert pick.points == Decimal('1.00')


def test_final_spread_push_scores_half_a_point():
    game, home, _away = _matchup(status='final', home_score=23, away_score=20)
    line = make_line(game, home_spread=Decimal('-3.0'), away_spread=Decimal('3.0'))
    pick = _submit(game, home, pick_type=Pick.PickType.SPREAD, line=line)
    settle_pick(pick)
    assert pick.result == Pick.Result.PUSH
    assert pick.points == Decimal('0.50')


def test_final_spread_loss_scores_zero():
    game, home, _away = _matchup(status='final', home_score=20, away_score=20)
    line = make_line(game, home_spread=Decimal('-3.0'), away_spread=Decimal('3.0'))
    pick = _submit(game, home, pick_type=Pick.PickType.SPREAD, line=line)
    settle_pick(pick)
    assert pick.result == Pick.Result.LOSS
    assert pick.points == Decimal('0.00')


@pytest.mark.parametrize(
    ('price', 'points'),
    [
        (Decimal('1.500'), Decimal('50.00')),
        (Decimal('3.000'), Decimal('200.00')),
    ],
)
def test_winning_moneyline_scores_profit_on_a_hundred_point_stake(price, points):
    game, home, away = _matchup(status='final', home_score=27, away_score=17)
    other_price = Decimal('3.000') if price == Decimal('1.500') else Decimal('1.500')
    line = make_line(
        game,
        home_moneyline=price,
        away_moneyline=other_price,
    )
    pick = _submit(game, home, pick_type=Pick.PickType.MONEYLINE, line=line)
    settle_pick(pick)
    assert pick.result == Pick.Result.WIN
    assert pick.points == points


def test_losing_moneyline_scores_zero():
    game, home, _away = _matchup(status='final', home_score=10, away_score=17)
    line = make_line(
        game,
        home_moneyline=Decimal('1.500'),
        away_moneyline=Decimal('2.750'),
    )
    pick = _submit(game, home, pick_type=Pick.PickType.MONEYLINE, line=line)
    settle_pick(pick)
    assert pick.result == Pick.Result.LOSS
    assert pick.points == Decimal('0.00')


def test_tied_moneyline_is_a_push_with_no_profit():
    game, home, _away = _matchup(status='final', home_score=20, away_score=20)
    line = make_line(
        game,
        home_moneyline=Decimal('1.500'),
        away_moneyline=Decimal('2.750'),
    )
    pick = _submit(game, home, pick_type=Pick.PickType.MONEYLINE, line=line)
    settle_pick(pick)
    assert pick.result == Pick.Result.PUSH
    assert pick.points == Decimal('0.00')


def test_cancelled_game_voids_the_pick_and_clears_points():
    game, home, _away = _matchup(status='cancelled', home_score=21, away_score=14)
    pick = _submit(game, home, pick_type=Pick.PickType.SPREAD)
    settle_pick(pick)
    pick.refresh_from_db()
    assert pick.result == Pick.Result.VOID
    assert pick.points is None


def test_postponed_game_does_not_settle_the_pick():
    game, home, _away = _matchup(
        status='postponed',
        home_score=21,
        away_score=14,
    )
    pick = _submit(game, home, pick_type=Pick.PickType.SPREAD)
    settle_pick(pick)
    pick.refresh_from_db()
    assert pick.result is None
    assert pick.points is None


def test_settling_a_final_pick_twice_keeps_the_same_result():
    game, home, _away = _matchup(status='final', home_score=30, away_score=10)
    line = make_line(game, home_spread=Decimal('-3.0'), away_spread=Decimal('3.0'))
    pick = _submit(game, home, pick_type=Pick.PickType.SPREAD, line=line)
    settle_pick(pick)
    pick.points = Decimal('9.99')
    pick.result = Pick.Result.LOSS
    pick.save(update_fields=['result', 'points'])
    settle_pick(pick)
    pick.refresh_from_db()
    assert pick.result == Pick.Result.WIN
    assert pick.points == Decimal('1.00')


def test_final_game_without_scores_is_not_settled():
    game, home, _away = _matchup(status='final')
    pick = _submit(game, home, pick_type=Pick.PickType.SPREAD)
    with pytest.raises(ValidationError, match='missing a score'):
        settle_pick(pick)
    pick.refresh_from_db()
    assert pick.result is None
    assert pick.points is None


def test_pick_rejects_an_unknown_result():
    game, home, _away = _matchup()
    pick = _submit(game, home, pick_type=Pick.PickType.SPREAD)
    pick.result = 'bogus'
    with pytest.raises(IntegrityError), transaction.atomic():
        pick.save(update_fields=['result'])
