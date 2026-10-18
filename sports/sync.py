from dataclasses import dataclass
from datetime import datetime

from django.db import DatabaseError, transaction

from sports.models import Game, GameLine, League, Season, Team
from sports.providers.the_odds_api.client import OddsApiClient
from sports.providers.the_odds_api.normalize import (
    MappingError,
    NormalizedGame,
    NormalizedGameLine,
    NormalizedSlate,
    NormalizedTeam,
    normalize,
)
from sports.providers.the_odds_api.weeks import season_year_for
from sports.seed import NFL_ABBREVIATION, PLACEHOLDER_PREFIX

_ADMIN_STATUSES = frozenset(
    {
        Game.Status.POSTPONED,
        Game.Status.DELAYED,
        Game.Status.CANCELLED,
    }
)
_PROGRESS: dict[str, int] = {
    Game.Status.SCHEDULED: 0,
    Game.Status.IN_PROGRESS: 1,
    Game.Status.FINAL: 2,
}
_LINE_FIELDS = (
    'bookmaker',
    'home_spread',
    'away_spread',
    'home_spread_price',
    'away_spread_price',
    'home_moneyline',
    'away_moneyline',
)


class SyncError(Exception):
    pass


@dataclass(frozen=True)
class SyncReport:
    games_created: int
    games_updated: int
    games_unchanged: int
    games_skipped: int
    lines_created: int
    problems: tuple[str, ...]


def run_nfl_sync(
    *,
    api_key: str,
    commence_from: datetime | None = None,
    commence_to: datetime | None = None,
) -> SyncReport:
    """Fetch a slate from The Odds API and upsert it.

    Omit both bounds to sync the current NFL week. Pass both to sync
    another half-open window.
    """
    payload = OddsApiClient(api_key).fetch(
        commence_time_from=commence_from,
        commence_time_to=commence_to,
    )
    event_problems: list[str] = []

    def remember(event_id: str, exc: MappingError) -> None:
        event_problems.append(f'{event_id}: {exc}')

    slate = normalize(payload, on_event_error=remember)
    report = sync_slate(slate)
    if not event_problems:
        return report
    return SyncReport(
        games_created=report.games_created,
        games_updated=report.games_updated,
        games_unchanged=report.games_unchanged,
        games_skipped=report.games_skipped,
        lines_created=report.lines_created,
        problems=tuple(event_problems) + report.problems,
    )


@dataclass(frozen=True)
class _Outcome:
    created: bool = False
    updated: bool = False
    skipped: bool = False
    lines_created: int = 0
    problem: str = ''


def sync_slate(slate: NormalizedSlate) -> SyncReport:
    """Upsert games and append line snapshots for an already normalized slate.

    The NFL league, the season, and both teams must already exist. This
    function does not create them.
    """
    league = League.objects.filter(abbreviation=NFL_ABBREVIATION).first()
    if league is None:
        raise SyncError('NFL league is not seeded. Run seed_sports first.')

    teams_by_id = {team.external_id: team for team in slate.teams}
    lines_by_game = {line.game_external_id: line for line in slate.lines}
    created = 0
    updated = 0
    unchanged = 0
    skipped = 0
    lines_created = 0
    problems: list[str] = []

    for game in slate.games:
        try:
            outcome = _sync_game(
                league,
                game,
                teams_by_id.get(game.home_team_external_id),
                teams_by_id.get(game.away_team_external_id),
                lines_by_game.get(game.external_id),
            )
        except DatabaseError as exc:
            skipped += 1
            problems.append(f'{game.external_id}: {exc}')
            continue
        if outcome.skipped:
            skipped += 1
            if outcome.problem:
                problems.append(outcome.problem)
            continue
        if outcome.created:
            created += 1
        elif outcome.updated:
            updated += 1
        else:
            unchanged += 1
        lines_created += outcome.lines_created

    return SyncReport(
        games_created=created,
        games_updated=updated,
        games_unchanged=unchanged,
        games_skipped=skipped,
        lines_created=lines_created,
        problems=tuple(problems),
    )


def _sync_game(
    league: League,
    game: NormalizedGame,
    home_team: NormalizedTeam | None,
    away_team: NormalizedTeam | None,
    line: NormalizedGameLine | None,
) -> _Outcome:
    if home_team is None or away_team is None:
        return _Outcome(
            skipped=True,
            problem=f'{game.external_id}: normalized team is missing',
        )
    year = season_year_for(game.kickoff_at)
    season = Season.objects.filter(league=league, year=year).first()
    if season is None:
        return _Outcome(
            skipped=True,
            problem=f'{game.external_id}: NFL season {year} is not seeded',
        )

    with transaction.atomic():
        home, home_problem = _prepare_team(league, home_team)
        away, away_problem = _prepare_team(league, away_team)
        if home is None or away is None:
            transaction.set_rollback(True)
            detail = '; '.join(
                problem for problem in (home_problem, away_problem) if problem
            )
            return _Outcome(skipped=True, problem=f'{game.external_id}: {detail}')

        existing = (
            Game.objects.select_for_update()
            .filter(external_id=game.external_id)
            .first()
        )
        if (
            existing is not None
            and (existing.home_team_id != home.id or existing.away_team_id != away.id)
            and existing.picks.exists()
        ):
            transaction.set_rollback(True)
            return _Outcome(
                skipped=True,
                problem=(
                    f'{game.external_id}: home or away team changed after '
                    'picks were submitted'
                ),
            )

        if existing is None:
            stored = Game.objects.create(
                external_id=game.external_id,
                season=season,
                week=game.week,
                home_team=home,
                away_team=away,
                kickoff_at=game.kickoff_at,
                status=game.status,
                home_score=game.home_score,
                away_score=game.away_score,
            )
            created = True
            updated = False
        else:
            stored = existing
            created = False
            updated = _assign_game(existing, game, season, home, away)
        if _insert_line_if_changed(stored, line):
            return _Outcome(created=created, updated=updated, lines_created=1)
        return _Outcome(created=created, updated=updated)


def _prepare_team(
    league: League,
    normalized: NormalizedTeam,
) -> tuple[Team | None, str]:
    linked = Team.objects.filter(
        league=league,
        external_id=normalized.external_id,
    ).first()
    if linked is not None:
        return linked, ''
    named_matches = list(Team.objects.filter(league=league, name=normalized.name)[:2])
    if not named_matches:
        return None, f'team {normalized.name} is not seeded'
    if len(named_matches) > 1:
        return None, f'team {normalized.name} matches more than one seeded team'
    named = named_matches[0]
    if not named.external_id.startswith(PLACEHOLDER_PREFIX):
        return None, (
            f'team {normalized.name} is already linked to a different provider id'
        )
    named.external_id = normalized.external_id
    named.save(update_fields=['external_id'])
    return named, ''


def _assign_game(
    stored: Game,
    game: NormalizedGame,
    season: Season,
    home: Team,
    away: Team,
) -> bool:
    status = _merged_status(stored.status, game.status)
    home_score, away_score = _merged_scores(
        stored.status,
        game.status,
        game.home_score,
        game.away_score,
        stored.home_score,
        stored.away_score,
    )
    changed = False
    if stored.season_id != season.id:
        stored.season = season
        changed = True
    if stored.week != game.week:
        stored.week = game.week
        changed = True
    if stored.home_team_id != home.id:
        stored.home_team = home
        changed = True
    if stored.away_team_id != away.id:
        stored.away_team = away
        changed = True
    if stored.kickoff_at != game.kickoff_at:
        stored.kickoff_at = game.kickoff_at
        changed = True
    if stored.status != status:
        stored.status = status
        changed = True
    if stored.home_score != home_score:
        stored.home_score = home_score
        changed = True
    if stored.away_score != away_score:
        stored.away_score = away_score
        changed = True
    if changed:
        stored.save()
    return changed


def _merged_status(current: str, incoming: str) -> str:
    if current in _ADMIN_STATUSES:
        return current
    current_rank = _PROGRESS.get(current)
    incoming_rank = _PROGRESS.get(incoming)
    if current_rank is None or incoming_rank is None:
        return current
    if incoming_rank < current_rank:
        return current
    return incoming


def _merged_scores(
    current_status: str,
    incoming_status: str,
    incoming_home: int | None,
    incoming_away: int | None,
    stored_home: int | None,
    stored_away: int | None,
) -> tuple[int | None, int | None]:
    if current_status in _ADMIN_STATUSES:
        return stored_home, stored_away
    if incoming_home is None or incoming_away is None:
        return stored_home, stored_away
    current_rank = _PROGRESS.get(current_status)
    incoming_rank = _PROGRESS.get(incoming_status)
    if current_rank is None or incoming_rank is None or incoming_rank < current_rank:
        return stored_home, stored_away
    return incoming_home, incoming_away


def _insert_line_if_changed(game: Game, line: NormalizedGameLine | None) -> bool:
    if line is None:
        return False
    latest = game.lines.order_by('-captured_at', '-id').first()
    if latest is not None and _same_market(latest, line):
        return False
    GameLine.objects.create(
        game=game,
        source=line.source,
        bookmaker=line.bookmaker,
        home_spread=line.home_spread,
        away_spread=line.away_spread,
        home_spread_price=line.home_spread_price,
        away_spread_price=line.away_spread_price,
        home_moneyline=line.home_moneyline,
        away_moneyline=line.away_moneyline,
        captured_at=line.captured_at,
    )
    return True


def _same_market(latest: GameLine, line: NormalizedGameLine) -> bool:
    return all(getattr(latest, field) == getattr(line, field) for field in _LINE_FIELDS)
