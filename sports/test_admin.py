from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
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
from sports.sync import SyncError, SyncReport

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


def _staff_with_game_view():
    user = make_user()
    user.is_staff = True
    user.save(update_fields=['is_staff'])
    user.user_permissions.add(
        Permission.objects.get(codename='view_game', content_type__app_label='sports')
    )
    return user


def test_game_changelist_links_to_nfl_sync_for_editors(admin_client, client):
    editor = admin_client.get(reverse('admin:sports_game_changelist'))
    assert b'Sync NFL data' in editor.content
    assert b'Settle games' in editor.content

    client.force_login(_staff_with_game_view())
    viewer = client.get(reverse('admin:sports_game_changelist'))
    assert viewer.status_code == 200
    assert b'Sync NFL data' not in viewer.content
    assert b'Settle games' not in viewer.content


def test_view_only_staff_cannot_open_nfl_sync(client):
    client.force_login(_staff_with_game_view())

    response = client.get(reverse('admin:sports_game_sync'))

    assert response.status_code == 403


def test_nfl_sync_page_runs_the_current_week_and_reports_problems(
    admin_client, monkeypatch
):
    seen = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return SyncReport(1, 2, 3, 4, 5, ('bad-game: skipped',))

    monkeypatch.setattr('sports.admin.run_nfl_sync', fake_run)
    monkeypatch.setenv('ODDS_API_KEY', 'test-key')

    page = admin_client.get(reverse('admin:sports_game_sync'))
    assert page.status_code == 200
    assert b'Sync NFL data' in page.content

    response = admin_client.post(reverse('admin:sports_game_sync'), follow=True)

    body = response.content.decode()
    assert seen['api_key'] == 'test-key'
    assert seen['commence_from'] is None
    assert seen['commence_to'] is None
    assert 'Games created: 1' in body
    assert 'Lines created: 5' in body
    assert 'Bad-game: skipped' in body


def test_nfl_sync_passes_a_commence_window(admin_client, monkeypatch):
    seen = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return SyncReport(0, 0, 0, 0, 0, ())

    monkeypatch.setattr('sports.admin.run_nfl_sync', fake_run)

    admin_client.post(
        reverse('admin:sports_game_sync'),
        {
            'commence_from': '2026-10-13 07:00:00',
            'commence_to': '2026-10-20 07:00:00',
        },
    )

    assert seen['commence_from'] == datetime(2026, 10, 13, 7, tzinfo=UTC)
    assert seen['commence_to'] == datetime(2026, 10, 20, 7, tzinfo=UTC)


@pytest.mark.parametrize(
    'data',
    [
        {'commence_from': '2026-10-13 07:00:00'},
        {
            'commence_from': '2026-10-20 07:00:00',
            'commence_to': '2026-10-13 07:00:00',
        },
    ],
)
def test_nfl_sync_rejects_a_bad_window_without_calling_the_api(
    admin_client, monkeypatch, data
):
    def fail(**kwargs):
        raise AssertionError(kwargs)

    monkeypatch.setattr('sports.admin.run_nfl_sync', fail)

    response = admin_client.post(reverse('admin:sports_game_sync'), data)

    assert response.status_code == 200
    body = response.content
    assert b'Sync NFL data' in body
    if 'commence_to' in data:
        assert b'commence_from must be earlier than commence_to' in body
    else:
        assert b'commence bounds must be provided together' in body


def test_nfl_sync_reports_a_failure_and_stays_on_the_form(admin_client, monkeypatch):
    def fail(**kwargs):
        raise SyncError('NFL league is not seeded. Run seed_sports first.')

    monkeypatch.setattr('sports.admin.run_nfl_sync', fail)

    response = admin_client.post(reverse('admin:sports_game_sync'))

    assert response.status_code == 200
    assert b'not seeded' in response.content


def test_view_only_staff_cannot_open_settle_games(client):
    client.force_login(_staff_with_game_view())

    response = client.get(reverse('admin:sports_game_settle'))

    assert response.status_code == 403


def test_settle_page_grades_the_current_week(admin_client, monkeypatch):
    monkeypatch.setattr('picks.operations.timezone.now', lambda: _NOW)
    game, home, _away = _matchup()
    pick = _spread_pick(game, home)
    game.status = 'final'
    game.home_score = 30
    game.away_score = 10
    game.save(update_fields=['status', 'home_score', 'away_score'])

    page = admin_client.get(reverse('admin:sports_game_settle'))
    assert page.status_code == 200
    assert b'Settle games' in page.content

    response = admin_client.post(reverse('admin:sports_game_settle'), follow=True)

    assert 'Settled: 1' in response.content.decode()
    pick.refresh_from_db()
    assert pick.result == Pick.Result.WIN


def test_settle_page_uses_an_explicit_window_and_reports_a_missing_score(admin_client):
    inside, inside_home, _inside_away = _matchup()
    outside, outside_home, _outside_away = _matchup()
    inside_pick = _spread_pick(inside, inside_home)
    outside_pick = _spread_pick(outside, outside_home)
    inside.status = 'final'
    inside.save(update_fields=['status'])
    outside.kickoff_at = datetime(2026, 10, 20, 7, tzinfo=UTC)
    outside.status = 'final'
    outside.home_score = 30
    outside.away_score = 10
    outside.save(update_fields=['kickoff_at', 'status', 'home_score', 'away_score'])

    response = admin_client.post(
        reverse('admin:sports_game_settle'),
        {
            'kickoff_from': '2026-09-08 07:00:00',
            'kickoff_to': '2026-09-15 07:00:00',
        },
        follow=True,
    )

    body = response.content.decode()
    assert 'Settled: 0' in body
    assert 'missing a score' in body
    inside_pick.refresh_from_db()
    outside_pick.refresh_from_db()
    assert inside_pick.result is None
    assert outside_pick.result is None


@pytest.mark.parametrize(
    'data',
    [
        {'kickoff_from': '2026-10-13 07:00:00'},
        {
            'kickoff_from': '2026-10-20 07:00:00',
            'kickoff_to': '2026-10-13 07:00:00',
        },
    ],
)
def test_settle_page_rejects_a_bad_window(admin_client, data):
    game, home, _away = _matchup()
    pick = _spread_pick(game, home)
    game.status = 'final'
    game.home_score = 30
    game.away_score = 10
    game.save(update_fields=['status', 'home_score', 'away_score'])

    response = admin_client.post(reverse('admin:sports_game_settle'), data)

    assert response.status_code == 200
    body = response.content
    assert b'Settle games' in body
    if 'kickoff_to' in data:
        assert b'kickoff_from must be earlier than kickoff_to' in body
    else:
        assert b'kickoff bounds must be provided together' in body
    pick.refresh_from_db()
    assert pick.result is None


def test_nfl_sync_requires_an_api_key(admin_client, monkeypatch):
    monkeypatch.delenv('ODDS_API_KEY', raising=False)

    response = admin_client.post(reverse('admin:sports_game_sync'))

    assert response.status_code == 200
    assert b'ODDS_API_KEY' in response.content
