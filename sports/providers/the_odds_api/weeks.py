from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo('America/Los_Angeles')
WEEK_LENGTH = timedelta(days=7)
ANCHOR_WEEK = 7
ANCHOR_START = datetime(2026, 10, 13, tzinfo=PACIFIC)


def week_for(kickoff_at: datetime) -> int:
    """Return the NFL week that contains this kickoff.

    Weeks start Tuesday at midnight Pacific. Week 7 starts on
    2026-10-13. The interval is half-open: the next Tuesday belongs
    to the following week.
    """
    start = week_window(kickoff_at)[0]
    days = (start.date() - ANCHOR_START.date()).days
    return ANCHOR_WEEK + days // WEEK_LENGTH.days


def week_window(moment: datetime) -> tuple[datetime, datetime]:
    """Return the half-open Pacific week that contains moment."""
    if moment.tzinfo is None:
        raise ValueError('moment must be timezone-aware')
    local = moment.astimezone(PACIFIC)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    days_since_tuesday = (midnight.weekday() - 1) % 7
    start = midnight - timedelta(days=days_since_tuesday)
    end = datetime.combine(
        start.date() + WEEK_LENGTH,
        datetime.min.time(),
        tzinfo=PACIFIC,
    )
    return start, end
