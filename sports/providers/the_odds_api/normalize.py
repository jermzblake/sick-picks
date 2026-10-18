from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sports.providers.the_odds_api.client import (
    BOOKMAKERS,
    ProviderPayload,
)
from sports.providers.the_odds_api.weeks import week_for

SOURCE = 'the_odds_api'
SCHEDULED = 'scheduled'
IN_PROGRESS = 'in_progress'
FINAL = 'final'

_MONEY = Decimal('0.001')
_SPREAD = Decimal('0.1')
_ONE = Decimal('1')


class MappingError(Exception):
    pass


@dataclass(frozen=True)
class NormalizedTeam:
    external_id: str
    name: str


@dataclass(frozen=True)
class NormalizedGame:
    external_id: str
    home_team_external_id: str
    away_team_external_id: str
    kickoff_at: datetime
    week: int
    status: str
    home_score: int | None
    away_score: int | None


@dataclass(frozen=True)
class NormalizedGameLine:
    game_external_id: str
    source: str
    bookmaker: str
    home_spread: Decimal | None
    away_spread: Decimal | None
    home_spread_price: Decimal | None
    away_spread_price: Decimal | None
    home_moneyline: Decimal | None
    away_moneyline: Decimal | None
    game_total: Decimal | None
    over_price: Decimal | None
    under_price: Decimal | None
    captured_at: datetime


@dataclass(frozen=True)
class NormalizedSlate:
    teams: tuple[NormalizedTeam, ...]
    games: tuple[NormalizedGame, ...]
    lines: tuple[NormalizedGameLine, ...]


def normalize(
    payload: ProviderPayload,
    *,
    on_event_error: Callable[[str, MappingError], None] | None = None,
) -> NormalizedSlate:
    participants = _participants_by_name(payload.participants)
    odds_by_id = _events_by_id(payload.odds, 'odds')
    scores_by_id = _events_by_id(payload.scores, 'scores')
    event_ids = sorted(set(odds_by_id) | set(scores_by_id))

    games: list[NormalizedGame] = []
    lines: list[NormalizedGameLine] = []
    for event_id in event_ids:
        odds_event = odds_by_id.get(event_id)
        score_event = scores_by_id.get(event_id)
        try:
            game = _game(
                event_id,
                odds_event,
                score_event,
                participants,
                payload.window_start,
                payload.window_end,
            )
        except MappingError as exc:
            if on_event_error is None:
                raise
            on_event_error(event_id, exc)
            continue
        if game is None:
            continue
        games.append(game)
        if odds_event is None:
            continue
        try:
            line = _line(odds_event)
        except MappingError as exc:
            if on_event_error is None:
                raise
            on_event_error(event_id, exc)
            continue
        if line is not None:
            lines.append(line)

    team_ids = {game.home_team_external_id for game in games}
    team_ids.update(game.away_team_external_id for game in games)
    teams_by_id = {team.external_id: team for team in participants.values()}
    teams = tuple(teams_by_id[team_id] for team_id in sorted(team_ids))
    return NormalizedSlate(teams=teams, games=tuple(games), lines=tuple(lines))


def _participants_by_name(
    participants: tuple[dict[str, Any], ...],
) -> dict[str, NormalizedTeam]:
    by_name: dict[str, NormalizedTeam] = {}
    for participant in participants:
        name = participant.get('full_name')
        external_id = participant.get('id')
        if not isinstance(name, str) or not name:
            raise MappingError('participant is missing full_name')
        if not isinstance(external_id, str) or not external_id:
            raise MappingError(f'participant {name} is missing id')
        if name in by_name:
            raise MappingError(f'duplicate participant name {name}')
        by_name[name] = NormalizedTeam(external_id=external_id, name=name)
    return by_name


def _events_by_id(
    events: tuple[dict[str, Any], ...],
    label: str,
) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for event in events:
        event_id = event.get('id')
        if not isinstance(event_id, str) or not event_id:
            raise MappingError(f'{label} event is missing id')
        if event_id in by_id:
            raise MappingError(f'duplicate {label} event id {event_id}')
        by_id[event_id] = event
    return by_id


def _game(
    event_id: str,
    odds_event: dict[str, Any] | None,
    score_event: dict[str, Any] | None,
    participants: dict[str, NormalizedTeam],
    window_start: datetime,
    window_end: datetime,
) -> NormalizedGame | None:
    source = odds_event if odds_event is not None else score_event
    if source is None:
        return None
    home_name = _team_name(source, 'home_team')
    away_name = _team_name(source, 'away_team')
    kickoff_at = _timestamp(source.get('commence_time'), 'commence_time')
    if score_event is not None and odds_event is not None:
        if _team_name(score_event, 'home_team') != home_name:
            raise MappingError(f'home team mismatch for {event_id}')
        if _team_name(score_event, 'away_team') != away_name:
            raise MappingError(f'away team mismatch for {event_id}')
        score_kickoff = _timestamp(score_event.get('commence_time'), 'commence_time')
        if score_kickoff != kickoff_at:
            raise MappingError(f'commence_time mismatch for {event_id}')
    if kickoff_at < window_start or kickoff_at >= window_end:
        return None
    home = _participant(participants, home_name)
    away = _participant(participants, away_name)
    if home.external_id == away.external_id:
        raise MappingError(f'home and away teams match for {event_id}')
    status, home_score, away_score = _status(score_event, home_name, away_name)
    return NormalizedGame(
        external_id=event_id,
        home_team_external_id=home.external_id,
        away_team_external_id=away.external_id,
        kickoff_at=kickoff_at,
        week=week_for(kickoff_at),
        status=status,
        home_score=home_score,
        away_score=away_score,
    )


def _line(odds_event: dict[str, Any]) -> NormalizedGameLine | None:
    event_id = odds_event['id']
    home_name = _team_name(odds_event, 'home_team')
    away_name = _team_name(odds_event, 'away_team')
    bookmakers = odds_event.get('bookmakers') or []
    if not isinstance(bookmakers, list):
        raise MappingError(f'bookmakers for {event_id} is not a list')
    by_key: dict[str, dict[str, Any]] = {}
    for bookmaker in bookmakers:
        if not isinstance(bookmaker, dict):
            raise MappingError(f'bookmaker for {event_id} is not an object')
        key = bookmaker.get('key')
        if not isinstance(key, str) or not key:
            raise MappingError(f'bookmaker for {event_id} is missing key')
        if key in by_key:
            raise MappingError(f'duplicate bookmaker {key} for {event_id}')
        by_key[key] = bookmaker
    for key in BOOKMAKERS:
        bookmaker = by_key.get(key)
        if bookmaker is None:
            continue
        parsed = _complete_line(bookmaker, home_name, away_name)
        if parsed is None or not bookmaker.get('last_update'):
            continue
        captured_at = _timestamp(bookmaker.get('last_update'), 'last_update')
        home_spread, away_spread, home_spread_price, away_spread_price = parsed[0]
        home_moneyline, away_moneyline = parsed[1]
        game_total, over_price, under_price = _present_total(parsed[2])
        return NormalizedGameLine(
            game_external_id=event_id,
            source=SOURCE,
            bookmaker=key,
            home_spread=home_spread,
            away_spread=away_spread,
            home_spread_price=home_spread_price,
            away_spread_price=away_spread_price,
            home_moneyline=home_moneyline,
            away_moneyline=away_moneyline,
            game_total=game_total,
            over_price=over_price,
            under_price=under_price,
            captured_at=captured_at,
        )
    return None


def _complete_line(
    bookmaker: dict[str, Any],
    home_name: str,
    away_name: str,
) -> (
    tuple[
        tuple[Decimal, Decimal, Decimal, Decimal],
        tuple[Decimal, Decimal],
        tuple[Decimal, Decimal, Decimal] | None,
    ]
    | None
):
    markets = bookmaker.get('markets') or []
    if not isinstance(markets, list):
        return None
    by_key = {}
    for market in markets:
        if not isinstance(market, dict):
            return None
        key = market.get('key')
        if key in ('h2h', 'spreads', 'totals'):
            by_key[key] = market
    moneyline = _moneyline(by_key.get('h2h'), home_name, away_name)
    spread = _spread(by_key.get('spreads'), home_name, away_name)
    if moneyline is None or spread is None:
        return None
    return spread, moneyline, _total(by_key.get('totals'))


def _moneyline(
    market: dict[str, Any] | None,
    home_name: str,
    away_name: str,
) -> tuple[Decimal, Decimal] | None:
    prices = _prices(market, home_name, away_name, with_point=False)
    if prices is None:
        return None
    home_price, away_price, _home_point, _away_point = prices
    if home_price <= _ONE or away_price <= _ONE:
        return None
    return home_price, away_price


def _spread(
    market: dict[str, Any] | None,
    home_name: str,
    away_name: str,
) -> tuple[Decimal, Decimal, Decimal, Decimal] | None:
    prices = _prices(market, home_name, away_name, with_point=True)
    if prices is None:
        return None
    home_price, away_price, home_point, away_point = prices
    if home_point is None or away_point is None:
        return None
    if home_price <= _ONE or away_price <= _ONE:
        return None
    if away_point != -home_point:
        return None
    return home_point, away_point, home_price, away_price


def _present_total(
    total: tuple[Decimal, Decimal, Decimal] | None,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    if total is None:
        return None, None, None
    return total


def _total(
    market: dict[str, Any] | None,
) -> tuple[Decimal, Decimal, Decimal] | None:
    if market is None:
        return None
    outcomes = market.get('outcomes')
    if not isinstance(outcomes, list):
        return None
    found: dict[str, dict[str, Any]] = {}
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            return None
        name = outcome.get('name')
        if name not in ('Over', 'Under'):
            continue
        if name in found:
            return None
        found[name] = outcome
    if set(found) != {'Over', 'Under'}:
        return None
    try:
        over_price = _quantize(_decimal(found['Over'].get('price')), _MONEY)
        under_price = _quantize(_decimal(found['Under'].get('price')), _MONEY)
        over_point = _quantize(_decimal(found['Over'].get('point')), _SPREAD)
        under_point = _quantize(_decimal(found['Under'].get('point')), _SPREAD)
    except MappingError:
        return None
    except TypeError:
        return None
    if over_price <= _ONE or under_price <= _ONE:
        return None
    if over_point != under_point or over_point <= 0:
        return None
    return over_point, over_price, under_price


def _prices(
    market: dict[str, Any] | None,
    home_name: str,
    away_name: str,
    *,
    with_point: bool,
) -> tuple[Decimal, Decimal, Decimal | None, Decimal | None] | None:
    if market is None:
        return None
    outcomes = market.get('outcomes')
    if not isinstance(outcomes, list):
        return None
    found: dict[str, dict[str, Any]] = {}
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            return None
        name = outcome.get('name')
        if name not in (home_name, away_name):
            continue
        if name in found:
            return None
        found[name] = outcome
    if set(found) != {home_name, away_name}:
        return None
    try:
        home_price = _quantize(_decimal(found[home_name].get('price')), _MONEY)
        away_price = _quantize(_decimal(found[away_name].get('price')), _MONEY)
        home_point = None
        away_point = None
        if with_point:
            home_point = _quantize(_decimal(found[home_name].get('point')), _SPREAD)
            away_point = _quantize(_decimal(found[away_name].get('point')), _SPREAD)
    except MappingError:
        return None
    except TypeError:
        return None
    return home_price, away_price, home_point, away_point


def _status(
    score_event: dict[str, Any] | None,
    home_name: str,
    away_name: str,
) -> tuple[str, int | None, int | None]:
    if score_event is None:
        return SCHEDULED, None, None
    completed = score_event.get('completed')
    if not isinstance(completed, bool):
        raise MappingError('score event completed flag is missing')
    raw_scores = score_event.get('scores')
    if not raw_scores:
        if completed:
            raise MappingError('completed game is missing scores')
        return SCHEDULED, None, None
    if not isinstance(raw_scores, list):
        raise MappingError('scores is not a list')
    by_name: dict[str, int] = {}
    for entry in raw_scores:
        if not isinstance(entry, dict):
            raise MappingError('score entry is not an object')
        name = entry.get('name')
        if name not in (home_name, away_name):
            continue
        if name in by_name:
            raise MappingError(f'duplicate score for {name}')
        by_name[name] = _score_value(entry.get('score'))
    if set(by_name) != {home_name, away_name}:
        raise MappingError('score event is missing a team score')
    status = FINAL if completed else IN_PROGRESS
    return status, by_name[home_name], by_name[away_name]


def _participant(
    participants: dict[str, NormalizedTeam],
    name: str,
) -> NormalizedTeam:
    team = participants.get(name)
    if team is None:
        raise MappingError(f'unknown team {name}')
    return team


def _team_name(event: dict[str, Any], field: str) -> str:
    name = event.get(field)
    if not isinstance(name, str) or not name:
        raise MappingError(f'event is missing {field}')
    return name


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise MappingError(f'{field} is missing')
    text = value[:-1] + '+00:00' if value.endswith('Z') else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MappingError(f'{field} is not a timestamp') from exc
    if parsed.tzinfo is None:
        raise MappingError(f'{field} must include a timezone')
    return parsed


def _score_value(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise MappingError('score is missing')
    try:
        if isinstance(value, str):
            number = Decimal(value)
        elif isinstance(value, Decimal):
            number = value
        elif isinstance(value, int):
            number = Decimal(value)
        else:
            raise MappingError('score is not an integer')
    except (MappingError, ArithmeticError) as exc:
        if isinstance(exc, MappingError):
            raise
        raise MappingError('score is not an integer') from exc
    if number != number.to_integral_value() or number < 0:
        raise MappingError('score is not a non-negative integer')
    return int(number)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        raise MappingError('price is missing')
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, int | str):
        try:
            return Decimal(value)
        except ArithmeticError as exc:
            raise MappingError('price is not a number') from exc
    raise MappingError('price is not a number')


def _quantize(value: Decimal, places: Decimal) -> Decimal:
    return value.quantize(places, rounding=ROUND_HALF_UP)
