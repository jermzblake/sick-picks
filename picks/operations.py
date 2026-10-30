import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import OuterRef, QuerySet, Subquery
from django.utils import timezone
from sports.models import Game, GameLine, Season
from sports.providers.the_odds_api.weeks import week_window

from picks.domain import market_terms, settle_pick
from picks.models import Pick

logger = logging.getLogger(__name__)

_OPEN_FOR_PICKS = {
    Game.Status.SCHEDULED,
    Game.Status.POSTPONED,
    Game.Status.DELAYED,
}
_SETTLEABLE = (Game.Status.FINAL, Game.Status.CANCELLED)
_DUPLICATE_PICK = 'pick_user_game_type_uniq'


@dataclass(frozen=True)
class UpcomingGame:
    game: Game
    line: GameLine | None


@dataclass(frozen=True)
class SettleReport:
    settled: int
    problems: tuple[str, ...]


def upcoming_games(
    *,
    now: datetime | None = None,
    season: Season | None = None,
    week: int | None = None,
) -> list[UpcomingGame]:
    if now is None:
        now = timezone.now()
    games = Game.objects.filter(
        kickoff_at__gt=now,
        status__in=_OPEN_FOR_PICKS,
    )
    if season is not None:
        games = games.filter(season=season)
    if week is not None:
        games = games.filter(week=week)
    slate = list(
        games.select_related('home_team', 'away_team').order_by('kickoff_at', 'id')
    )
    lines = _latest_eligible_lines([game.id for game in slate], now)
    return [UpcomingGame(game, lines.get(game.id)) for game in slate]


def user_picks(user, *, games: Sequence[Game] | None = None) -> QuerySet[Pick]:
    picks = Pick.objects.filter(user=user).select_related(
        'game',
        'game__home_team',
        'game__away_team',
        'game_line',
        'picked_team',
    )
    if games is not None:
        picks = picks.filter(game__in=games)
    return picks.order_by('-submitted_at', 'id')


def submit_pick(
    *,
    user,
    game: Game,
    game_line: GameLine,
    pick_type: str,
    picked_team=None,
    total_side: str | None = None,
    now: datetime | None = None,
) -> Pick:
    if now is None:
        now = timezone.now()
    try:
        with transaction.atomic():
            locked_game = Game.objects.select_for_update().get(pk=game.pk)
            line = GameLine.objects.get(pk=game_line.pk)
            _require_open(locked_game, now)
            _require_selection(locked_game, pick_type, picked_team, total_side)
            if line.game_id != locked_game.id:
                raise ValidationError('Line does not belong to this game.')
            current = _latest_eligible_line(locked_game, now)
            if current is None or current.pk != line.pk:
                raise ValidationError('Line is not the current snapshot.')
            line_value, price = market_terms(locked_game, line, picked_team, pick_type)
            if Pick.objects.filter(
                user=user,
                game=locked_game,
                pick_type=pick_type,
            ).exists():
                raise ValidationError(
                    'A pick of this type already exists for this game.'
                )
            return Pick.objects.create(
                user=user,
                game=locked_game,
                game_line=line,
                picked_team=picked_team,
                pick_type=pick_type,
                total_side=total_side,
                line_value=line_value,
                price=price,
                submitted_at=now,
            )
    except IntegrityError as exc:
        if _DUPLICATE_PICK not in str(exc.__cause__ or exc):
            raise
        raise ValidationError(
            'A pick of this type already exists for this game.'
        ) from exc


def settle_window(
    *,
    kickoff_from: datetime | None = None,
    kickoff_to: datetime | None = None,
    now: datetime | None = None,
) -> SettleReport:
    """Grade final and cancelled games in a kickoff window.

    Omit both bounds to use the NFL week that contains now. Pass both
    for another half-open window. A game that cannot be graded is
    reported and the rest of the window still settles.
    """
    start, end = _kickoff_window(kickoff_from, kickoff_to, now)
    logger.info(
        'settle_window starting kickoff_from=%s kickoff_to=%s',
        start,
        end,
    )
    games = (
        Game.objects.filter(
            status__in=_SETTLEABLE,
            kickoff_at__gte=start,
            kickoff_at__lt=end,
        )
        .select_related('home_team', 'away_team')
        .order_by('kickoff_at', 'id')
    )
    settled = 0
    problems: list[str] = []
    for game in games:
        try:
            settle_game(game)
        except ValidationError as exc:
            logger.warning(
                'settle_window game failed game_id=%s reason=%s',
                game.pk,
                ' '.join(exc.messages),
            )
            problems.append(f'{game}: {" ".join(exc.messages)}')
        else:
            settled += 1
    report = SettleReport(settled, tuple(problems))
    logger.info(
        'settle_window finished settled=%s problems=%s',
        report.settled,
        len(report.problems),
    )
    return report


def settle_game(game: Game) -> list[Pick]:
    with transaction.atomic():
        locked_game = Game.objects.select_for_update().get(pk=game.pk)
        picks = list(locked_game.picks.select_for_update().order_by('id'))
        if locked_game.status not in (Game.Status.FINAL, Game.Status.CANCELLED):
            logger.info(
                'settle_game skipped game_id=%s status=%s pick_count=%s',
                locked_game.pk,
                locked_game.status,
                len(picks),
            )
            return picks
        if locked_game.status == Game.Status.FINAL and (
            locked_game.home_score is None or locked_game.away_score is None
        ):
            raise ValidationError('Final game is missing a score.')
        for pick in picks:
            settle_pick(pick, game=locked_game)
        logger.info(
            'settle_game finished game_id=%s status=%s pick_count=%s',
            locked_game.pk,
            locked_game.status,
            len(picks),
        )
        return picks


def _kickoff_window(
    kickoff_from: datetime | None,
    kickoff_to: datetime | None,
    now: datetime | None,
) -> tuple[datetime, datetime]:
    if (kickoff_from is None) != (kickoff_to is None):
        raise ValueError('kickoff bounds must be provided together')
    if kickoff_from is None or kickoff_to is None:
        moment = now if now is not None else timezone.now()
        if moment.tzinfo is None:
            raise ValueError('now must be timezone-aware')
        return week_window(moment)
    if kickoff_from.tzinfo is None or kickoff_to.tzinfo is None:
        raise ValueError('kickoff bounds must be timezone-aware')
    if kickoff_from >= kickoff_to:
        raise ValueError('kickoff_from must be earlier than kickoff_to')
    return kickoff_from, kickoff_to


def _require_selection(game: Game, pick_type: str, picked_team, total_side) -> None:
    if pick_type == Pick.PickType.TOTAL:
        if picked_team is not None:
            raise ValidationError('A total pick does not select a team.')
        if total_side not in (Pick.TotalSide.OVER, Pick.TotalSide.UNDER):
            raise ValidationError('Choose over or under.')
        return
    if total_side is not None:
        raise ValidationError('This market does not take an over or under.')
    if picked_team is None or picked_team.pk not in (
        game.home_team_id,
        game.away_team_id,
    ):
        raise ValidationError('Picked team is not playing in this game.')


def _require_open(game: Game, now: datetime) -> None:
    if game.status not in _OPEN_FOR_PICKS:
        raise ValidationError('Game is not open for picks.')
    if now >= game.kickoff_at:
        raise ValidationError('Pick cannot be submitted at or after kickoff.')


def _latest_eligible_line(game: Game, now: datetime) -> GameLine | None:
    return (
        GameLine.objects.filter(game=game, captured_at__lte=now)
        .order_by('-captured_at', '-id')
        .first()
    )


def _latest_eligible_lines(game_ids: list[int], now: datetime) -> dict[int, GameLine]:
    if not game_ids:
        return {}
    latest_pk = (
        GameLine.objects.filter(
            game_id=OuterRef('game_id'),
            captured_at__lte=now,
        )
        .order_by('-captured_at', '-id')
        .values('pk')[:1]
    )
    lines = GameLine.objects.filter(
        game_id__in=game_ids,
        captured_at__lte=now,
        pk=Subquery(latest_pk),
    )
    return {line.game_id: line for line in lines}
