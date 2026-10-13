from django.core.management.base import BaseCommand, CommandError

from sports.seed import UnknownLeague, seed_sports


class Command(BaseCommand):
    help = 'Create or update a league, season, and teams from the checked-in catalog.'

    def add_arguments(self, parser):
        parser.add_argument('--league', required=True)
        parser.add_argument(
            '--season-year',
            dest='season_year',
            type=int,
            required=True,
        )

    def handle(self, *args, **options):
        try:
            report = seed_sports(options['league'], options['season_year'])
        except UnknownLeague as exc:
            raise CommandError(str(exc)) from exc
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f'Seeded {report.league} {report.season_year} with {report.teams} teams.'
        )
