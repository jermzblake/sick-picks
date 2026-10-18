from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from picks.operations import submit_pick
from tests.builders import make_league, make_season, make_team, make_user

from sports.models import Game, GameLine, Team
from sports.providers.the_odds_api.client import ProviderPayload
from sports.providers.the_odds_api.normalize import (
    NormalizedGame,
    NormalizedSlate,
    normalize,
)
from sports.seed import seed_sports
from sports.sync import SyncError, sync_slate
from sports.test_odds_api import (
    AWAY,
    AWAY_ID,
    GAME_ID,
    HOME,
    HOME_ID,
    WINDOW_END,
    WINDOW_START,
    _book,
    _moneyline,
    _odds_event,
    _payload,
    _score_event,
    _scores,
    _slate,
    _spread,
    _total,
)

pytestmark = pytest.mark.django_db

LATER_KICKOFF = '2026-10-19T17:00:00Z'
LATER_UPDATE = '2026-10-17T15:00:00Z'


def _line_event(spread='-3.5', away_spread='3.5', last_update=None, commence_time=None):
    markets = [_moneyline(), _spread(home_point=spread, away_point=away_spread)]
    if last_update is None:
        book = _book('fanduel', markets)
    else:
        book = _book('fanduel', markets, last_update=last_update)
    if commence_time is None:
        return _odds_event([book])
    return _odds_event([book], commence_time=commence_time)


def _line_event_with_total():
    markets = [
        _moneyline(),
        _spread(),
        _total(point='47.5', under_price='1.870'),
    ]
    return _odds_event([_book('fanduel', markets, last_update=LATER_UPDATE)])


def _sync(odds=(), scores=()):
    return sync_slate(_slate(odds=odds, scores=scores))


def test_sync_requires_a_seeded_league_and_writes_nothing():
    with pytest.raises(SyncError, match='not seeded'):
        _sync(odds=[_line_event()])

    assert Game.objects.count() == 0
    assert Team.objects.count() == 0


def test_repeated_sync_creates_one_game_and_links_seeded_teams():
    seed_sports('NFL', 2026)

    first = _sync(odds=[_line_event()])
    game = Game.objects.get(external_id=GAME_ID)
    stamp = game.updated_at
    second = _sync(odds=[_line_event()])

    game.refresh_from_db()
    chiefs = Team.objects.get(abbreviation='KC')
    bills = Team.objects.get(abbreviation='BUF')
    assert first.games_created == 1
    assert first.lines_created == 1
    assert second.games_unchanged == 1
    assert second.lines_created == 0
    assert Game.objects.count() == 1
    assert GameLine.objects.count() == 1
    assert game.updated_at == stamp
    assert game.week == 6
    assert game.season.year == 2026
    assert chiefs.external_id == HOME_ID
    assert chiefs.abbreviation == 'KC'
    assert chiefs.name == 'Kansas City Chiefs'
    assert bills.external_id == AWAY_ID
    assert bills.abbreviation == 'BUF'


def test_a_newer_capture_with_the_same_market_does_not_add_a_line():
    seed_sports('NFL', 2026)
    _sync(odds=[_line_event()])

    report = _sync(odds=[_line_event(last_update=LATER_UPDATE)])

    line = GameLine.objects.get()
    assert report.lines_created == 0
    assert report.games_unchanged == 1
    assert line.captured_at == datetime(2026, 10, 16, 15, tzinfo=UTC)


def test_a_changed_spread_inserts_a_snapshot_and_leaves_the_pick_line():
    seed_sports('NFL', 2026)
    _sync(odds=[_line_event()])
    game = Game.objects.get(external_id=GAME_ID)
    original = game.lines.get()
    pick = submit_pick(
        user=make_user(),
        game=game,
        game_line=original,
        picked_team=game.home_team,
        pick_type='spread',
        now=game.kickoff_at - timedelta(hours=1),
    )

    report = _sync(odds=[_line_event(spread='-4.5', away_spread='4.5')])

    pick.refresh_from_db()
    assert report.lines_created == 1
    assert report.games_unchanged == 1
    assert game.lines.count() == 2
    assert pick.game_line_id == original.id
    assert pick.line_value == Decimal('-3.5')
    assert original.home_spread == Decimal('-3.5')


def test_a_changed_total_inserts_a_snapshot():
    seed_sports('NFL', 2026)
    _sync(odds=[_line_event()])
    game = Game.objects.get(external_id=GAME_ID)

    report = _sync(odds=[_line_event_with_total()])

    assert report.lines_created == 1
    assert game.lines.count() == 2
    latest = game.lines.order_by('-captured_at', '-id').first()
    assert latest is not None
    assert latest.game_total == Decimal('47.5')
    assert latest.over_price == Decimal('1.909')
    assert latest.under_price == Decimal('1.870')


def test_missing_season_or_team_skips_the_game():
    league = make_league(
        name='National Football League',
        abbreviation='NFL',
        sport='football',
    )
    make_team(league, name=HOME, abbreviation='KC', external_id='seed:KC')
    make_team(league, name=AWAY, abbreviation='BUF', external_id='seed:BUF')

    missing_season = _sync(odds=[_line_event()])
    make_season(league, 2026)
    Team.objects.filter(abbreviation='BUF').delete()
    missing_team = _sync(odds=[_line_event()])

    assert missing_season.games_skipped == 1
    assert missing_season.problems[0].endswith('NFL season 2026 is not seeded')
    assert missing_team.games_skipped == 1
    assert 'Buffalo Bills is not seeded' in missing_team.problems[0]
    assert Game.objects.count() == 0
    assert Team.objects.filter(name=AWAY).count() == 0


def test_live_scores_do_not_replace_a_final_result():
    seed_sports('NFL', 2026)
    _sync(scores=[_score_event(True, _scores())])
    live = [
        {'name': HOME, 'score': '10'},
        {'name': AWAY, 'score': '7'},
    ]

    _sync(scores=[_score_event(False, live)])
    game = Game.objects.get()
    assert game.status == Game.Status.FINAL
    assert game.home_score == 24
    assert game.away_score == 20

    corrected = [
        {'name': HOME, 'score': '27'},
        {'name': AWAY, 'score': '21'},
    ]
    _sync(scores=[_score_event(True, corrected)])
    game.refresh_from_db()
    assert game.status == Game.Status.FINAL
    assert game.home_score == 27
    assert game.away_score == 21


def test_duplicate_team_names_skip_the_game_without_linking_a_provider_id():
    league = make_league(
        name='National Football League',
        abbreviation='NFL',
        sport='football',
    )
    make_season(league, 2026)
    make_team(league, name=HOME, abbreviation='KC', external_id='seed:KC')
    make_team(league, name=HOME, abbreviation='KC2', external_id='seed:KC2')
    make_team(league, name=AWAY, abbreviation='BUF', external_id='seed:BUF')

    report = _sync(odds=[_line_event()])

    assert report.games_skipped == 1
    assert 'more than one seeded team' in report.problems[0]
    assert Game.objects.count() == 0
    assert not Team.objects.filter(external_id=HOME_ID).exists()


def test_sync_locks_the_game_row_before_writing_a_line():
    seed_sports('NFL', 2026)

    with CaptureQueriesContext(connection) as captured:
        _sync(odds=[_line_event()])

    locked = any(
        'FOR UPDATE' in query['sql'].upper() for query in captured.captured_queries
    )
    assert locked


def test_final_and_in_progress_do_not_regress_when_scores_are_omitted():
    seed_sports('NFL', 2026)
    _sync(scores=[_score_event(True, _scores())])
    final = Game.objects.get()
    assert final.status == Game.Status.FINAL

    _sync(odds=[_line_event()])
    final.refresh_from_db()
    assert final.status == Game.Status.FINAL
    assert final.home_score == 24
    assert final.away_score == 20

    live = Game.objects.get()
    live.status = Game.Status.IN_PROGRESS
    live.save(update_fields=['status'])
    _sync(odds=[_line_event()])
    live.refresh_from_db()
    assert live.status == Game.Status.IN_PROGRESS
    assert live.home_score == 24
    assert live.away_score == 20


@pytest.mark.parametrize('status', ['postponed', 'delayed', 'cancelled'])
def test_admin_status_and_scores_survive_a_provider_final(status):
    seed_sports('NFL', 2026)
    _sync(odds=[_line_event()])
    game = Game.objects.get()
    game.status = status
    game.home_score = 3
    game.away_score = 1
    game.save(update_fields=['status', 'home_score', 'away_score'])

    report = _sync(
        odds=[_line_event(commence_time=LATER_KICKOFF)],
        scores=[_score_event(True, _scores(), commence_time=LATER_KICKOFF)],
    )

    game.refresh_from_db()
    assert report.games_updated == 1
    assert game.status == status
    assert game.home_score == 3
    assert game.away_score == 1
    assert game.kickoff_at == datetime(2026, 10, 19, 17, tzinfo=UTC)


def test_home_and_away_can_change_until_a_pick_exists():
    seed_sports('NFL', 2026)
    _sync(odds=[_line_event()])
    swapped = _line_event()
    swapped['home_team'] = AWAY
    swapped['away_team'] = HOME

    report = sync_slate(_slate(odds=[swapped]))

    game = Game.objects.get()
    assert report.games_updated == 1
    assert game.home_team.name == AWAY
    assert game.away_team.name == HOME

    submit_pick(
        user=make_user(),
        game=game,
        game_line=game.lines.order_by('-captured_at', '-id').first(),
        picked_team=game.home_team,
        pick_type='spread',
        now=game.kickoff_at - timedelta(hours=1),
    )
    restored = _line_event(spread='-7.0', away_spread='7.0')
    before = game.lines.count()
    blocked = sync_slate(_slate(odds=[restored]))

    game.refresh_from_db()
    assert blocked.games_skipped == 1
    assert 'picks were submitted' in blocked.problems[0]
    assert game.home_team.name == AWAY
    assert game.lines.count() == before


def test_a_bad_event_does_not_roll_back_a_good_game():
    seed_sports('NFL', 2026)
    seen: list[str] = []
    slate = normalize(
        _payload(
            odds=[_line_event()],
            scores=[_score_event(True, None, event_id='game-2')],
        ),
        on_event_error=lambda event_id, _exc: seen.append(event_id),
    )

    report = sync_slate(slate)

    assert seen == ['game-2']
    assert report.games_created == 1
    assert Game.objects.filter(external_id=GAME_ID).count() == 1
    assert Game.objects.filter(external_id='game-2').count() == 0


def test_a_database_failure_on_one_game_keeps_the_other():
    seed_sports('NFL', 2026)
    good = _slate(odds=[_line_event()])
    bad = NormalizedGame(
        external_id='bad-game',
        home_team_external_id=HOME_ID,
        away_team_external_id=HOME_ID,
        kickoff_at=good.games[0].kickoff_at,
        week=good.games[0].week,
        status=Game.Status.SCHEDULED,
        home_score=None,
        away_score=None,
    )
    slate = NormalizedSlate(
        teams=good.teams,
        games=good.games + (bad,),
        lines=good.lines,
    )

    report = sync_slate(slate)

    assert report.games_created == 1
    assert report.games_skipped == 1
    assert Game.objects.filter(external_id=GAME_ID).exists()
    assert not Game.objects.filter(external_id='bad-game').exists()
    assert any(problem.startswith('bad-game:') for problem in report.problems)


def test_a_game_without_a_line_keeps_the_line_it_already_has():
    seed_sports('NFL', 2026)
    _sync(odds=[_line_event()])

    report = _sync(odds=[_odds_event([])])

    assert report.lines_created == 0
    assert report.games_unchanged == 1
    assert GameLine.objects.count() == 1


def test_sync_command_reports_skips_and_rejects_a_bad_payload(monkeypatch):
    monkeypatch.setenv('ODDS_API_KEY', 'test-key')
    payload = _payload(odds=[_line_event()])

    class FakeClient:
        def __init__(self, api_key):
            assert api_key == 'test-key'

        def fetch(self, **kwargs):
            return payload

    monkeypatch.setattr(
        'sports.sync.OddsApiClient',
        FakeClient,
    )
    stdout = StringIO()
    with pytest.raises(CommandError, match='not seeded'):
        call_command('sync_nfl_data', stdout=stdout)
    assert Game.objects.count() == 0

    seed_sports('NFL', 2026)
    call_command('sync_nfl_data', stdout=stdout)
    assert 'Games created: 1' in stdout.getvalue()
    assert 'Lines created: 1' in stdout.getvalue()

    payload = _payload(
        participants=(
            {'full_name': HOME, 'id': HOME_ID},
            {'full_name': HOME, 'id': 'other'},
        )
    )
    with pytest.raises(CommandError, match='duplicate participant'):
        call_command('sync_nfl_data')
    assert Game.objects.count() == 1


def test_sync_command_passes_a_commence_window_and_requires_both_bounds(monkeypatch):
    monkeypatch.setenv('ODDS_API_KEY', 'test-key')
    seen = {}

    class FakeClient:
        def __init__(self, api_key):
            pass

        def fetch(self, **kwargs):
            seen.update(kwargs)
            return ProviderPayload(
                participants=(),
                odds=(),
                scores=(),
                window_start=WINDOW_START,
                window_end=WINDOW_END,
            )

    monkeypatch.setattr(
        'sports.sync.OddsApiClient',
        FakeClient,
    )
    seed_sports('NFL', 2026)
    call_command(
        'sync_nfl_data',
        commence_from='2026-10-13T07:00:00+00:00',
        commence_to='2026-10-20T07:00:00+00:00',
    )

    assert seen['commence_time_from'] == datetime(2026, 10, 13, 7, tzinfo=UTC)
    assert seen['commence_time_to'] == datetime(2026, 10, 20, 7, tzinfo=UTC)

    with pytest.raises(CommandError, match='timezone-aware'):
        call_command(
            'sync_nfl_data',
            commence_from='2026-10-13T07:00:00',
            commence_to='2026-10-20T07:00:00',
        )


def test_sync_command_requires_an_api_key_and_both_bounds(monkeypatch):
    monkeypatch.delenv('ODDS_API_KEY', raising=False)
    with pytest.raises(CommandError, match='ODDS_API_KEY'):
        call_command('sync_nfl_data')

    monkeypatch.setenv('ODDS_API_KEY', 'test-key')
    with pytest.raises(CommandError, match='together'):
        call_command('sync_nfl_data', commence_from='2026-10-13T07:00:00+00:00')
