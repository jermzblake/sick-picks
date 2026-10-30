import logging
import os
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from sports.providers.the_odds_api.client import OddsApiError
from sports.providers.the_odds_api.normalize import MappingError
from sports.sync import SyncError, run_nfl_sync

logger = logging.getLogger(__name__)


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
            report = run_nfl_sync(
                api_key=os.environ.get('ODDS_API_KEY', ''),
                commence_from=_timezone_aware(options['commence_from']),
                commence_to=_timezone_aware(options['commence_to']),
            )
        except (ValueError, OddsApiError, MappingError, SyncError) as exc:
            logger.error('sync_nfl_data failed: %s', type(exc).__name__)
            raise CommandError(str(exc)) from exc

        logger.info(
            'sync_nfl_data complete games_created=%s games_updated=%s '
            'games_unchanged=%s games_skipped=%s lines_created=%s problems=%s',
            report.games_created,
            report.games_updated,
            report.games_unchanged,
            report.games_skipped,
            report.lines_created,
            len(report.problems),
        )
        self.stdout.write(f'Games created: {report.games_created}')
        self.stdout.write(f'Games updated: {report.games_updated}')
        self.stdout.write(f'Games unchanged: {report.games_unchanged}')
        self.stdout.write(f'Games skipped: {report.games_skipped}')
        self.stdout.write(f'Lines created: {report.lines_created}')
        if report.problems:
            self.stdout.write('Problems:')
            for problem in report.problems:
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
