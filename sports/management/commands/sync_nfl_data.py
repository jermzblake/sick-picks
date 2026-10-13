import os
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from sports.providers.the_odds_api.client import OddsApiClient, OddsApiError
from sports.providers.the_odds_api.normalize import MappingError, normalize
from sports.sync import SyncError, sync_slate


class Command(BaseCommand):
    help = 'Fetch the current NFL slate and upsert games and line snapshots.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--from',
            dest='commence_from',
            default=None,
            help='Inclusive commence time, timezone-aware ISO-8601.',
        )
        parser.add_argument(
            '--to',
            dest='commence_to',
            default=None,
            help='Exclusive commence time, timezone-aware ISO-8601.',
        )

    def handle(self, *args, **options):
        try:
            commence_from = _timezone_aware(options['commence_from'])
            commence_to = _timezone_aware(options['commence_to'])
            client = OddsApiClient(os.environ.get('ODDS_API_KEY', ''))
            payload = client.fetch(
                commence_time_from=commence_from,
                commence_time_to=commence_to,
            )
        except (ValueError, OddsApiError) as exc:
            raise CommandError(str(exc)) from exc

        event_problems: list[str] = []

        def remember(event_id: str, exc: MappingError) -> None:
            event_problems.append(f'{event_id}: {exc}')

        try:
            slate = normalize(payload, on_event_error=remember)
        except MappingError as exc:
            raise CommandError(str(exc)) from exc

        try:
            report = sync_slate(slate)
        except SyncError as exc:
            raise CommandError(str(exc)) from exc

        problems = tuple(event_problems) + report.problems
        self.stdout.write(f'Games created: {report.games_created}')
        self.stdout.write(f'Games updated: {report.games_updated}')
        self.stdout.write(f'Games unchanged: {report.games_unchanged}')
        self.stdout.write(f'Games skipped: {report.games_skipped}')
        self.stdout.write(f'Lines created: {report.lines_created}')
        if problems:
            self.stdout.write('Problems:')
            for problem in problems:
                self.stdout.write(f'- {problem}')
        else:
            self.stdout.write('Problems: 0')


def _timezone_aware(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('commence bounds must be timezone-aware')
    return parsed
