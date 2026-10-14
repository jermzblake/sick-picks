from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo('America/Los_Angeles')
WEEK_LENGTH = timedelta(days=7)
ANCHOR_WEEK = 6
ANCHOR_START = datetime(2026, 10, 13, tzinfo=PACIFIC)
SEASON_OPENING = datetime(2026, 9, 8, tzinfo=PACIFIC)
SEASON_LENGTH = WEEK_LENGTH * 52


def season_year_for(kickoff_at: datetime) -> int:
    """Return the NFL season year that contains this kickoff.

    The 2026 season opens Tuesday, September 8, 2026 Pacific. Each later
    season opens 52 weeks after the previous opening. A January kickoff
    stays with the season that opened the previous September. This is
    separate from week numbers: those are anchored so week 6 starts
    October 13, 2026, which is not the season boundary.
    """
    if kickoff_at.tzinfo is None:
        raise ValueError('kickoff_at must be timezone-aware')
    elapsed = kickoff_at.astimezone(PACIFIC) - SEASON_OPENING
    return SEASON_OPENING.year + elapsed // SEASON_LENGTH


def week_for(kickoff_at: datetime) -> int:
    """Return the NFL week that contains this kickoff.

    Weeks start Tuesday at midnight Pacific. Week 6 starts on
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
