import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from sports.models import League, Season, Team
from sports.seed import seed_sports
from sports.seed.nfl import TEAMS

pytestmark = pytest.mark.django_db


def test_nfl_catalog_names_and_abbreviations_are_unique():
    names = [name for name, _abbreviation in TEAMS]
    abbreviations = [abbreviation for _name, abbreviation in TEAMS]

    assert len(names) == len(set(names)) == 32
    assert len(abbreviations) == len(set(abbreviations)) == 32


def test_seed_creates_league_season_and_teams():
    report = seed_sports('NFL', 2026)

    league = League.objects.get(abbreviation='NFL')
    assert report.league == 'NFL'
    assert report.season_year == 2026
    assert report.teams == 32
    assert league.name == 'National Football League'
    assert league.sport == 'football'
    assert Season.objects.filter(league=league, year=2026).count() == 1
    chiefs = Team.objects.get(league=league, abbreviation='KC')
    assert chiefs.name == 'Kansas City Chiefs'
    assert chiefs.external_id == 'seed:KC'
    assert Team.objects.filter(league=league).count() == 32


def test_seed_is_idempotent_and_keeps_a_provider_id():
    league = League.objects.create(
        name='Old League',
        abbreviation='NFL',
        sport='other',
    )
    Team.objects.create(
        league=league,
        external_id='par_chiefs',
        name='Old Chiefs',
        abbreviation='KC',
    )

    seed_sports('NFL', 2026)
    seed_sports('NFL', 2027)

    league.refresh_from_db()
    chiefs = Team.objects.get(abbreviation='KC')
    assert league.name == 'National Football League'
    assert league.sport == 'football'
    assert chiefs.name == 'Kansas City Chiefs'
    assert chiefs.external_id == 'par_chiefs'
    assert Team.objects.filter(league=league).count() == 32
    assert set(Season.objects.filter(league=league).values_list('year', flat=True)) == {
        2026,
        2027,
    }


def test_seed_command_rejects_an_unknown_league_and_a_bad_year():
    with pytest.raises(CommandError, match='Unknown league'):
        call_command('seed_sports', league='MLS', season_year=2026)
    with pytest.raises(CommandError, match='positive'):
        call_command('seed_sports', league='NFL', season_year=0)

    assert League.objects.count() == 0
    assert Season.objects.count() == 0
    assert Team.objects.count() == 0
