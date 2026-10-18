from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from picks.models import Pick
from picks.operations import submit_pick
from tests.builders import (
    make_game,
    make_league,
    make_line,
    make_season,
    make_team,
    make_user,
)

from sports.models import Game, GameLine

pytestmark = pytest.mark.django_db

_NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
_KICKOFF = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)


def _matchup():
    league = make_league()
    season = make_season(league)
    home = make_team(league)
    away = make_team(league)
    game = make_game(season, home, away, kickoff_at=_KICKOFF)
    return game, home, away


def _spread_pick(game, home):
    line = make_line(
        game,
        home_spread=Decimal('-3.0'),
        away_spread=Decimal('3.0'),
        captured_at=_NOW - datetime.resolution,
    )
    return submit_pick(
        user=make_user(),
        game=game,
        game_line=line,
        picked_team=home,
        pick_type=Pick.PickType.SPREAD,
        now=_NOW,
    )


def _settle(admin_client, *games):
    return admin_client.post(
        reverse('admin:sports_game_changelist'),
        {
            'action': 'settle_selected',
            '_selected_action': [str(game.pk) for game in games],
        },
        follow=True,
    )


def test_settle_action_grades_a_final_game(admin_client):
    game, home, _away = _matchup()
    pick = _spread_pick(game, home)
    game.status = 'final'
    game.home_score = 30
    game.away_score = 10
    game.save(update_fields=['status', 'home_score', 'away_score'])

    response = _settle(admin_client, game)

    assert response.status_code == 200
    assert 'settled' in response.content.decode()
    pick.refresh_from_db()
    assert pick.result == Pick.Result.WIN
    assert pick.points == Decimal('1.00')


def test_settle_action_reports_a_missing_score_and_still_grades_another(admin_client):
    unscored, unscored_home, _unscored_away = _matchup()
    scored, scored_home, _scored_away = _matchup()
    left = _spread_pick(unscored, unscored_home)
    right = _spread_pick(scored, scored_home)
    unscored.status = 'final'
    unscored.save(update_fields=['status'])
    scored.status = 'final'
    scored.home_score = 30
    scored.away_score = 10
    scored.save(update_fields=['status', 'home_score', 'away_score'])

    response = _settle(admin_client, unscored, scored)

    body = response.content.decode()
    assert 'missing a score' in body
    left.refresh_from_db()
    right.refresh_from_db()
    assert left.result is None
    assert right.result == Pick.Result.WIN


def test_settle_action_leaves_a_scheduled_game_unscored(admin_client):
    game, home, _away = _matchup()
    pick = _spread_pick(game, home)

    response = _settle(admin_client, game)

    body = response.content.decode()
    assert 'left unchanged' in body
    assert 'settled' not in body
    pick.refresh_from_db()
    assert pick.result is None
    assert pick.points is None


def _game_change(admin_client, game, **overrides):
    kickoff = timezone_label(game.kickoff_at)
    data = {
        'week': game.week if game.week is not None else '',
        'kickoff_at_0': kickoff[0],
        'kickoff_at_1': kickoff[1],
        'status': game.status,
        'home_score': '' if game.home_score is None else game.home_score,
        'away_score': '' if game.away_score is None else game.away_score,
        '_save': 'Save',
    }
    data.update(overrides)
    return admin_client.post(
        reverse('admin:sports_game_change', args=[game.pk]),
        data,
        follow=True,
    )


def timezone_label(kickoff):
    return kickoff.date().isoformat(), kickoff.time().isoformat()


def test_saving_a_final_score_settles_the_pick(admin_client):
    game, home, _away = _matchup()
    pick = _spread_pick(game, home)

    response = _game_change(
        admin_client,
        game,
        status='final',
        home_score='30',
        away_score='10',
    )

    assert response.status_code == 200
    assert b'settled' in response.content
    pick.refresh_from_db()
    assert pick.result == Pick.Result.WIN
    assert pick.points == Decimal('1.00')


def test_saving_a_final_game_without_scores_does_not_grade(admin_client):
    game, home, _away = _matchup()
    pick = _spread_pick(game, home)

    response = _game_change(admin_client, game, status='final')

    assert b'missing a score' in response.content
    pick.refresh_from_db()
    game.refresh_from_db()
    assert game.status == 'final'
    assert pick.result is None
    assert pick.points is None


def _delete_game(admin_client, game):
    return admin_client.post(
        reverse('admin:sports_game_delete', args=[game.pk]),
        {'post': 'yes'},
        follow=True,
    )


def test_game_admin_deletes_a_game_and_its_lines(admin_client):
    game, _home, _away = _matchup()
    line = make_line(game, captured_at=_NOW)

    response = _delete_game(admin_client, game)

    assert response.status_code == 200
    assert not Game.objects.filter(pk=game.pk).exists()
    assert not GameLine.objects.filter(pk=line.pk).exists()


def test_game_admin_keeps_a_game_that_still_has_a_pick(admin_client):
    game, home, _away = _matchup()
    pick = _spread_pick(game, home)

    _delete_game(admin_client, game)

    game.refresh_from_db()
    pick.refresh_from_db()


def test_line_and_pick_admins_are_view_only(admin_client):
    line_add = admin_client.get(reverse('admin:sports_gameline_add'))
    pick_add = admin_client.get(reverse('admin:picks_pick_add'))

    assert line_add.status_code == 403
    assert pick_add.status_code == 403


def _changelist_queries(admin_client, url):
    with CaptureQueriesContext(connection) as captured:
        response = admin_client.get(url)
    assert response.status_code == 200
    return len(captured)


def test_pick_and_line_changelists_do_not_query_teams_per_row(admin_client):
    first, first_home, _first_away = _matchup()
    second, second_home, _second_away = _matchup()
    _spread_pick(first, first_home)
    make_line(first, captured_at=_NOW)
    pick_url = reverse('admin:picks_pick_changelist')
    line_url = reverse('admin:sports_gameline_changelist')
    one_pick = _changelist_queries(admin_client, pick_url)
    one_line = _changelist_queries(admin_client, line_url)
    _spread_pick(second, second_home)
    make_line(second, captured_at=_NOW)

    assert _changelist_queries(admin_client, pick_url) == one_pick
    assert _changelist_queries(admin_client, line_url) == one_line
