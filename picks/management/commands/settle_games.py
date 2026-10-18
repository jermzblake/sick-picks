from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from picks.operations import settle_window


class Command(BaseCommand):
    help = 'Settle final and cancelled games for the current week or a kickoff window.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--from',
            dest='kickoff_from',
            default=None,
            help='Inclusive kickoff time, timezone-aware ISO-8601.',
        )
        parser.add_argument(
            '--to',
            dest='kickoff_to',
            default=None,
            help='Exclusive kickoff time, timezone-aware ISO-8601.',
        )

    def handle(self, *args, **options):
        try:
            report = settle_window(
                kickoff_from=_timezone_aware(options['kickoff_from']),
                kickoff_to=_timezone_aware(options['kickoff_to']),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f'Settled: {report.settled}')
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
        raise ValueError('kickoff bounds must be timezone-aware')
    return parsed
