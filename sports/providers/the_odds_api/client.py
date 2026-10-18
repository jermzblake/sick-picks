import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sports.providers.the_odds_api.weeks import week_window

BASE_URL = 'https://api.the-odds-api.com'
SPORT_KEY = 'americanfootball_nfl'
BOOKMAKERS = ('fanduel', 'draftkings', 'betmgm')
MARKETS = 'h2h,spreads,totals'
ODDS_FORMAT = 'decimal'
SCORES_DAYS_FROM = '3'

Transport = Callable[[str, dict[str, str]], tuple[int, bytes]]
JsonValue = dict[str, Any] | list[Any]


class OddsApiError(Exception):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f'The Odds API request failed with status {status}')


@dataclass(frozen=True)
class ProviderPayload:
    participants: tuple[dict[str, Any], ...]
    odds: tuple[dict[str, Any], ...]
    scores: tuple[dict[str, Any], ...]
    window_start: datetime
    window_end: datetime


def urllib_transport(url: str, params: dict[str, str]) -> tuple[int, bytes]:
    request = Request(f'{url}?{urlencode(params)}')
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except HTTPError as exc:
        try:
            exc.read()
        finally:
            exc.close()
        return exc.code, b''


class OddsApiClient:
    def __init__(
        self,
        api_key: str,
        *,
        transport: Transport | None = None,
        base_url: str = BASE_URL,
    ):
        if not api_key:
            raise ValueError('ODDS_API_KEY is required')
        self._api_key = api_key
        self._transport = transport or urllib_transport
        self._base_url = base_url.rstrip('/')

    def fetch(
        self,
        *,
        now: datetime | None = None,
        commence_time_from: datetime | None = None,
        commence_time_to: datetime | None = None,
    ) -> ProviderPayload:
        window_start, window_end = _resolve_window(
            now=now,
            commence_time_from=commence_time_from,
            commence_time_to=commence_time_to,
        )
        participants = self._get(f'/v4/sports/{SPORT_KEY}/participants')
        odds = self._get(
            f'/v4/sports/{SPORT_KEY}/odds/',
            {
                'oddsFormat': ODDS_FORMAT,
                'markets': MARKETS,
                'bookmakers': ','.join(BOOKMAKERS),
                'commenceTimeFrom': _api_timestamp(window_start),
                'commenceTimeTo': _api_timestamp(window_end - timedelta(seconds=1)),
            },
        )
        scores = self._get(
            f'/v4/sports/{SPORT_KEY}/scores/',
            {'daysFrom': SCORES_DAYS_FROM},
        )
        return ProviderPayload(
            participants=_as_records(participants),
            odds=_as_records(odds),
            scores=_as_records(scores),
            window_start=window_start,
            window_end=window_end,
        )

    def _get(self, path: str, params: dict[str, str] | None = None) -> JsonValue:
        query = {'apiKey': self._api_key}
        if params:
            query.update(params)
        status, body = self._transport(f'{self._base_url}{path}', query)
        if status != 200:
            raise OddsApiError(status)
        try:
            payload = json.loads(body, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            raise OddsApiError(status) from exc
        if not isinstance(payload, list):
            raise OddsApiError(status)
        return payload


def _resolve_window(
    *,
    now: datetime | None,
    commence_time_from: datetime | None,
    commence_time_to: datetime | None,
) -> tuple[datetime, datetime]:
    if (commence_time_from is None) != (commence_time_to is None):
        raise ValueError('commence bounds must be provided together')
    if commence_time_from is None or commence_time_to is None:
        moment = now if now is not None else datetime.now(UTC)
        return week_window(moment)
    if commence_time_from.tzinfo is None or commence_time_to.tzinfo is None:
        raise ValueError('commence bounds must be timezone-aware')
    if commence_time_from >= commence_time_to:
        raise ValueError('commence_time_from must be earlier than commence_time_to')
    return commence_time_from, commence_time_to


def _api_timestamp(moment: datetime) -> str:
    utc = moment.astimezone(UTC).replace(microsecond=0)
    return utc.strftime('%Y-%m-%dT%H:%M:%SZ')


def _as_records(payload: JsonValue) -> tuple[dict[str, Any], ...]:
    records = []
    for item in payload:
        if not isinstance(item, dict):
            raise OddsApiError(200)
        records.append(item)
    return tuple(records)
