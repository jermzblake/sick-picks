import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from sports.models import Game
from sports.providers.the_odds_api.client import (
    BOOKMAKERS,
    OddsApiClient,
    OddsApiError,
    ProviderPayload,
)
from sports.providers.the_odds_api.normalize import (
    FINAL,
    IN_PROGRESS,
    SCHEDULED,
    SOURCE,
    MappingError,
    normalize,
)
from sports.providers.the_odds_api.weeks import PACIFIC, week_for, week_window

FIXTURE = (
    Path(__file__).parent / 'providers' / 'the_odds_api' / 'fixtures' / 'nfl_week.json'
)
HOME = 'Kansas City Chiefs'
AWAY = 'Buffalo Bills'
HOME_ID = 'par_chiefs'
AWAY_ID = 'par_bills'
GAME_ID = 'game-1'
KICKOFF = '2026-10-18T17:00:00Z'
UPDATED = '2026-10-16T15:00:00Z'
WINDOW_START = datetime(2026, 10, 13, tzinfo=PACIFIC)
WINDOW_END = datetime(2026, 10, 20, tzinfo=PACIFIC)


def _participant(name, external_id):
    return {'full_name': name, 'id': external_id}


def _moneyline(home_price='1.500', away_price='2.750'):
    return {
        'key': 'h2h',
        'outcomes': [
            {'name': HOME, 'price': home_price},
            {'name': AWAY, 'price': away_price},
        ],
    }


def _spread(
    home_point='-3.5',
    away_point='3.5',
    home_price='1.909',
    away_price='1.909',
):
    return {
        'key': 'spreads',
        'outcomes': [
            {'name': HOME, 'price': home_price, 'point': home_point},
            {'name': AWAY, 'price': away_price, 'point': away_point},
        ],
    }


def _book(key, markets, last_update=UPDATED):
    book = {'key': key, 'title': key, 'markets': markets}
    if last_update is not None:
        book['last_update'] = last_update
    return book


def _odds_event(bookmakers, event_id=GAME_ID, commence_time=KICKOFF):
    return {
        'id': event_id,
        'commence_time': commence_time,
        'home_team': HOME,
        'away_team': AWAY,
        'bookmakers': bookmakers,
    }


def _score_event(completed, scores, event_id=GAME_ID, commence_time=KICKOFF):
    return {
        'id': event_id,
        'commence_time': commence_time,
        'completed': completed,
        'home_team': HOME,
        'away_team': AWAY,
        'scores': scores,
    }


def _scores():
    return [
        {'name': HOME, 'score': '24'},
        {'name': AWAY, 'score': '20'},
    ]


def _payload(odds=(), scores=(), participants=None):
    if participants is None:
        participants = (
            _participant(HOME, HOME_ID),
            _participant(AWAY, AWAY_ID),
        )
    return ProviderPayload(
        participants=tuple(participants),
        odds=tuple(odds),
        scores=tuple(scores),
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )


def _slate(odds=(), scores=()):
    return normalize(_payload(odds, scores))


def test_week_seven_starts_tuesday_october_13_2026_pacific():
    kickoff = datetime(2026, 10, 13, tzinfo=PACIFIC)

    assert week_for(kickoff) == 7
    assert week_window(kickoff) == (
        datetime(2026, 10, 13, tzinfo=PACIFIC),
        datetime(2026, 10, 20, tzinfo=PACIFIC),
    )


def test_instant_before_week_seven_is_week_six():
    kickoff = datetime(2026, 10, 13, tzinfo=PACIFIC) - timedelta(microseconds=1)

    assert week_for(kickoff) == 6


def test_week_eight_starts_the_following_tuesday():
    kickoff = datetime(2026, 10, 20, tzinfo=PACIFIC)

    assert week_for(kickoff) == 8


def test_monday_night_before_the_next_tuesday_stays_in_week_seven():
    kickoff = datetime(2026, 10, 19, 23, 59, tzinfo=PACIFIC)

    assert week_for(kickoff) == 7


def test_utc_kickoff_uses_pacific_week_bounds():
    still_monday = datetime(2026, 10, 13, 6, 59, tzinfo=UTC)
    tuesday = datetime(2026, 10, 13, 7, 0, tzinfo=UTC)

    assert week_for(still_monday) == 6
    assert week_for(tuesday) == 7


def test_week_bounds_stay_on_tuesday_midnight_across_daylight_saving():
    kickoff = datetime(2026, 11, 3, tzinfo=PACIFIC)

    assert kickoff.date().isoformat() == '2026-11-03'
    assert week_for(kickoff) == 10
    assert week_for(kickoff - timedelta(microseconds=1)) == 9


def test_naive_moment_is_rejected():
    with pytest.raises(ValueError, match='timezone-aware'):
        week_for(datetime(2026, 10, 13))


def test_client_requests_the_current_week_and_priority_books():
    calls = []

    def transport(url, params):
        calls.append((url, params))
        return 200, b'[]'

    now = datetime(2026, 10, 15, 18, tzinfo=PACIFIC)
    payload = OddsApiClient('test-key', transport=transport).fetch(now=now)

    assert payload.window_start == datetime(2026, 10, 13, tzinfo=PACIFIC)
    assert payload.window_end == datetime(2026, 10, 20, tzinfo=PACIFIC)
    odds_url, odds_params = calls[1]
    assert odds_url.endswith('/v4/sports/americanfootball_nfl/odds/')
    assert odds_params['apiKey'] == 'test-key'
    assert odds_params['oddsFormat'] == 'decimal'
    assert odds_params['markets'] == 'h2h,spreads'
    assert odds_params['bookmakers'] == 'fanduel,draftkings,betmgm'
    assert odds_params['commenceTimeFrom'] == '2026-10-13T07:00:00Z'
    assert odds_params['commenceTimeTo'] == '2026-10-20T06:59:59Z'
    scores_url, scores_params = calls[2]
    assert scores_url.endswith('/v4/sports/americanfootball_nfl/scores/')
    assert scores_params['daysFrom'] == '3'
    assert 'commenceTimeFrom' not in scores_params


def test_client_sends_an_explicit_commence_window():
    seen = {}

    def transport(url, params):
        if url.endswith('/odds/'):
            seen.update(params)
        return 200, b'[]'

    start = datetime(2026, 10, 20, tzinfo=PACIFIC)
    end = datetime(2026, 10, 27, tzinfo=PACIFIC)
    payload = OddsApiClient('test-key', transport=transport).fetch(
        commence_time_from=start,
        commence_time_to=end,
    )

    assert payload.window_start == start
    assert payload.window_end == end
    assert seen['commenceTimeFrom'] == '2026-10-20T07:00:00Z'
    assert seen['commenceTimeTo'] == '2026-10-27T06:59:59Z'


def test_client_rejects_a_missing_key_and_a_partial_window():
    with pytest.raises(ValueError, match='ODDS_API_KEY'):
        OddsApiClient('')
    client = OddsApiClient('test-key', transport=lambda url, params: (200, b'[]'))
    with pytest.raises(ValueError, match='together'):
        client.fetch(commence_time_from=WINDOW_START)
    with pytest.raises(ValueError, match='timezone-aware'):
        client.fetch(
            commence_time_from=datetime(2026, 10, 13),
            commence_time_to=WINDOW_END,
        )
    with pytest.raises(ValueError, match='earlier'):
        client.fetch(
            commence_time_from=WINDOW_END,
            commence_time_to=WINDOW_START,
        )


def test_urllib_transport_closes_http_errors(monkeypatch):
    from io import BytesIO
    from urllib.error import HTTPError

    from sports.providers.the_odds_api import client as client_module
    from sports.providers.the_odds_api.client import urllib_transport

    error = HTTPError(
        'https://api.the-odds-api.com/v4/sports',
        429,
        'Too Many Requests',
        hdrs=None,
        fp=BytesIO(b'limit'),
    )

    def raise_error(request, timeout):
        raise error

    monkeypatch.setattr(client_module, 'urlopen', raise_error)

    status, body = urllib_transport(
        'https://api.the-odds-api.com',
        {'apiKey': 'secret'},
    )

    assert status == 429
    assert body == b''
    assert error.fp.closed
    client = OddsApiClient('test-key', transport=lambda url, params: (401, b''))
    with pytest.raises(OddsApiError, match='401') as error:
        client.fetch(now=WINDOW_START)
    assert error.value.status == 401

    client = OddsApiClient('test-key', transport=lambda url, params: (200, b'{'))
    with pytest.raises(OddsApiError):
        client.fetch(now=WINDOW_START)

    client = OddsApiClient(
        'test-key',
        transport=lambda url, params: (200, b'{"message": "nope"}'),
    )
    with pytest.raises(OddsApiError):
        client.fetch(now=WINDOW_START)

    client = OddsApiClient('test-key', transport=lambda url, params: (200, b'[1]'))
    with pytest.raises(OddsApiError):
        client.fetch(now=WINDOW_START)


def test_client_keeps_decimal_prices_exact():
    def transport(url, params):
        if url.endswith('/odds/'):
            body = [{'id': 'g', 'price': 1.1}]
        else:
            body = []
        return 200, json.dumps(body).encode()

    payload = OddsApiClient('test-key', transport=transport).fetch(now=WINDOW_START)

    assert payload.odds[0]['price'] == Decimal('1.1')


def test_normalize_maps_a_complete_fanduel_line():
    slate = _slate(odds=[_odds_event([_book('fanduel', [_moneyline(), _spread()])])])

    assert [team.name for team in slate.teams] == [AWAY, HOME]
    game = slate.games[0]
    assert game.external_id == GAME_ID
    assert game.home_team_external_id == HOME_ID
    assert game.away_team_external_id == AWAY_ID
    assert game.kickoff_at == datetime(2026, 10, 18, 17, tzinfo=UTC)
    assert game.week == 7
    assert game.status == SCHEDULED
    assert game.status == Game.Status.SCHEDULED
    assert game.home_score is None
    line = slate.lines[0]
    assert line.source == SOURCE
    assert line.bookmaker == 'fanduel'
    assert line.home_spread == Decimal('-3.5')
    assert line.away_spread == Decimal('3.5')
    assert line.home_moneyline == Decimal('1.500')
    assert line.away_moneyline == Decimal('2.750')
    assert line.captured_at == datetime(2026, 10, 16, 15, tzinfo=UTC)


def test_bookmaker_priority_skips_an_incomplete_book():
    both = [_moneyline(), _spread()]
    slate = _slate(
        odds=[
            _odds_event(
                [
                    _book('betmgm', both),
                    _book('draftkings', both),
                    _book('fanduel', [_moneyline()]),
                ]
            )
        ]
    )

    assert slate.lines[0].bookmaker == 'draftkings'


def test_betmgm_is_used_when_earlier_books_lack_a_market():
    slate = _slate(
        odds=[
            _odds_event(
                [
                    _book('fanduel', [_spread()]),
                    _book('draftkings', [_moneyline()]),
                    _book('betmgm', [_moneyline(), _spread()]),
                ]
            )
        ]
    )

    assert slate.lines[0].bookmaker == 'betmgm'


def test_no_line_when_no_priority_book_has_both_markets():
    slate = _slate(
        odds=[
            _odds_event(
                [
                    _book('fanduel', [_moneyline()]),
                    _book('draftkings', [_spread()]),
                    _book('betmgm', []),
                    _book('caesars', [_moneyline(), _spread()]),
                ]
            )
        ]
    )

    assert slate.games[0].external_id == GAME_ID
    assert slate.lines == ()


@pytest.mark.parametrize(
    'markets',
    [
        [_moneyline(home_price='1.000'), _spread()],
        [_moneyline(), _spread(home_point='-3.0', away_point='3.5')],
        [_moneyline(), _spread(home_price='1.000')],
        [_moneyline(home_price='nope'), _spread()],
        [
            {
                'key': 'h2h',
                'outcomes': [
                    {'name': HOME, 'price': '1.500'},
                    {'name': HOME, 'price': '2.000'},
                    {'name': AWAY, 'price': '2.750'},
                ],
            },
            _spread(),
        ],
    ],
)
def test_unusable_priority_book_falls_through(markets):
    slate = _slate(
        odds=[
            _odds_event(
                [
                    _book('fanduel', markets),
                    _book('draftkings', [_moneyline(), _spread()]),
                ]
            )
        ]
    )

    assert slate.lines[0].bookmaker == 'draftkings'


def test_missing_last_update_tries_the_next_book():
    slate = _slate(
        odds=[
            _odds_event(
                [
                    _book('fanduel', [_moneyline(), _spread()], last_update=None),
                    _book('draftkings', [_moneyline(), _spread()]),
                ]
            )
        ]
    )

    assert slate.lines[0].bookmaker == 'draftkings'


def test_malformed_last_update_fails_closed():
    with pytest.raises(MappingError, match='last_update'):
        _slate(
            odds=[
                _odds_event(
                    [
                        _book(
                            'fanduel',
                            [_moneyline(), _spread()],
                            last_update='not-a-time',
                        )
                    ]
                )
            ]
        )


def test_prices_and_spreads_are_quantized_to_model_precision():
    slate = _slate(
        odds=[
            _odds_event(
                [
                    _book(
                        'fanduel',
                        [
                            _moneyline(home_price='1.5004', away_price='2.7505'),
                            _spread(home_point='-3.25', away_point='3.25'),
                        ],
                    )
                ]
            )
        ]
    )

    line = slate.lines[0]
    assert line.home_moneyline == Decimal('1.500')
    assert line.away_moneyline == Decimal('2.751')
    assert line.home_spread == Decimal('-3.3')
    assert line.away_spread == Decimal('3.3')


def test_final_in_progress_and_scheduled_scores():
    final = _slate(scores=[_score_event(True, _scores())])
    live = _slate(scores=[_score_event(False, _scores())])
    upcoming = _slate(scores=[_score_event(False, None)])

    assert final.games[0].status == FINAL
    assert final.games[0].status == Game.Status.FINAL
    assert final.games[0].home_score == 24
    assert final.games[0].away_score == 20
    assert final.lines == ()
    assert live.games[0].status == IN_PROGRESS
    assert live.games[0].status == Game.Status.IN_PROGRESS
    assert upcoming.games[0].status == SCHEDULED
    assert upcoming.games[0].home_score is None


def test_completed_game_without_scores_fails_closed():
    with pytest.raises(MappingError, match='missing scores'):
        _slate(scores=[_score_event(True, None)])
    with pytest.raises(MappingError, match='team score'):
        _slate(
            scores=[_score_event(True, [{'name': HOME, 'score': '10'}])],
        )
    with pytest.raises(MappingError, match='non-negative integer'):
        _slate(
            scores=[
                _score_event(
                    True,
                    [
                        {'name': HOME, 'score': '-1'},
                        {'name': AWAY, 'score': '3'},
                    ],
                )
            ]
        )


def test_unknown_team_and_team_mismatch_fail_closed():
    with pytest.raises(MappingError, match='unknown team'):
        normalize(
            _payload(
                odds=[_odds_event([_book('fanduel', [_moneyline(), _spread()])])],
                participants=(_participant(HOME, HOME_ID),),
            )
        )
    odds = _odds_event([_book('fanduel', [_moneyline(), _spread()])])
    scores = _score_event(False, None)
    scores['home_team'] = 'Other Team'
    with pytest.raises(MappingError, match='home team mismatch'):
        _slate(odds=[odds], scores=[scores])


def test_events_outside_the_window_are_dropped():
    slate = _slate(
        odds=[_odds_event([], event_id='later', commence_time='2026-10-20T07:00:00Z')],
        scores=[
            _score_event(
                True,
                _scores(),
                event_id='earlier',
                commence_time='2026-10-06T07:00:00Z',
            )
        ],
    )

    assert slate.games == ()
    assert slate.teams == ()
    assert slate.lines == ()


def test_duplicate_event_and_participant_names_fail_closed():
    with pytest.raises(MappingError, match='duplicate odds'):
        _slate(odds=[_odds_event([]), _odds_event([])])
    with pytest.raises(MappingError, match='duplicate participant'):
        normalize(
            _payload(
                participants=(
                    _participant(HOME, HOME_ID),
                    _participant(HOME, 'other'),
                )
            )
        )


@pytest.mark.skipif(
    not FIXTURE.is_file(),
    reason='local Odds API fixture is absent',
)
def test_saved_nfl_fixture_normalizes_without_provider_field_names():
    document = json.loads(FIXTURE.read_text(), parse_float=Decimal)
    payload = ProviderPayload(
        participants=tuple(document['participants']),
        odds=tuple(document['odds']),
        scores=tuple(document['scores']),
        window_start=datetime.fromisoformat(document['window_start']),
        window_end=datetime.fromisoformat(document['window_end']),
    )

    slate = normalize(payload)

    assert slate.games
    assert {line.bookmaker for line in slate.lines} <= set(BOOKMAKERS)
    team_ids = {team.external_id for team in slate.teams}
    for game in slate.games:
        assert game.week == week_for(game.kickoff_at)
        assert payload.window_start <= game.kickoff_at < payload.window_end
        assert game.status in {SCHEDULED, IN_PROGRESS, FINAL}
        assert game.home_team_external_id in team_ids
        assert game.away_team_external_id in team_ids
        assert game.home_team_external_id != game.away_team_external_id
    for line in slate.lines:
        assert line.source == SOURCE
        assert line.home_moneyline > 1
        assert line.away_moneyline > 1
        assert line.away_spread == -line.home_spread
        assert line.home_spread_price > 1
        assert line.away_spread_price > 1
    for event in document['odds']:
        assert {book['key'] for book in event['bookmakers']} <= set(BOOKMAKERS)
