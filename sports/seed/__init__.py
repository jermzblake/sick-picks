from dataclasses import dataclass

from django.db import transaction

from sports.models import League, Season, Team
from sports.seed.nfl import (
    LEAGUE_ABBREVIATION,
    LEAGUE_NAME,
    LEAGUE_SPORT,
    TEAMS,
)

PLACEHOLDER_PREFIX = 'seed:'
NFL_ABBREVIATION = LEAGUE_ABBREVIATION


class UnknownLeague(Exception):
    def __init__(self, league_code: str):
        self.league_code = league_code
        known = ', '.join(sorted(CATALOGS))
        super().__init__(f"Unknown league '{league_code}'. Seed catalogs: {known}.")


@dataclass(frozen=True)
class SeedReport:
    league: str
    season_year: int
    teams: int


@dataclass(frozen=True)
class _Catalog:
    name: str
    abbreviation: str
    sport: str
    teams: tuple[tuple[str, str], ...]


CATALOGS: dict[str, _Catalog] = {
    LEAGUE_ABBREVIATION: _Catalog(
        name=LEAGUE_NAME,
        abbreviation=LEAGUE_ABBREVIATION,
        sport=LEAGUE_SPORT,
        teams=TEAMS,
    ),
}


def placeholder_external_id(abbreviation: str) -> str:
    return f'{PLACEHOLDER_PREFIX}{abbreviation}'


def seed_sports(league_code: str, season_year: int) -> SeedReport:
    """Create or update a league, one season, and that league's teams.

    Re-running updates catalog names and adds the requested season. A
    provider id already stored on a team is left in place.
    """
    catalog = CATALOGS.get(league_code)
    if catalog is None:
        raise UnknownLeague(league_code)
    if season_year < 1:
        raise ValueError('season year must be positive')
    _require_unique_teams(catalog)

    with transaction.atomic():
        league, created = League.objects.get_or_create(
            abbreviation=catalog.abbreviation,
            defaults={'name': catalog.name, 'sport': catalog.sport},
        )
        if not created:
            changed: list[str] = []
            if league.name != catalog.name:
                league.name = catalog.name
                changed.append('name')
            if league.sport != catalog.sport:
                league.sport = catalog.sport
                changed.append('sport')
            if changed:
                league.save(update_fields=changed)

        Season.objects.get_or_create(league=league, year=season_year)

        for name, abbreviation in catalog.teams:
            team, team_created = Team.objects.get_or_create(
                league=league,
                abbreviation=abbreviation,
                defaults={
                    'name': name,
                    'external_id': placeholder_external_id(abbreviation),
                },
            )
            if not team_created and team.name != name:
                team.name = name
                team.save(update_fields=['name'])

    return SeedReport(
        league=catalog.abbreviation,
        season_year=season_year,
        teams=len(catalog.teams),
    )


def _require_unique_teams(catalog: _Catalog) -> None:
    names = [name for name, _abbreviation in catalog.teams]
    abbreviations = [abbreviation for _name, abbreviation in catalog.teams]
    if len(names) != len(set(names)):
        raise ValueError(f'{catalog.abbreviation} catalog has duplicate team names')
    if len(abbreviations) != len(set(abbreviations)):
        raise ValueError(f'{catalog.abbreviation} catalog has duplicate abbreviations')
