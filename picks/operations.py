from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import OuterRef, QuerySet, Subquery
from django.utils import timezone
from sports.models import Game, GameLine, Season

from picks.domain import market_terms, settle_pick
from picks.models import Pick

_OPEN_FOR_PICKS = {
    Game.Status.SCHEDULED,
    Game.Status.POSTPONED,
    Game.Status.DELAYED,
}
_DUPLICATE_PICK = 'pick_user_game_type_uniq'


@dataclass(frozen=True)
class UpcomingGame:
    game: Game
    line: GameLine | None


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
    picked_team,
    pick_type: str,
    now: datetime | None = None,
) -> Pick:
    if now is None:
        now = timezone.now()
    try:
        with transaction.atomic():
            locked_game = Game.objects.select_for_update().get(pk=game.pk)
            line = GameLine.objects.get(pk=game_line.pk)
            _require_open(locked_game, now)
            if picked_team.pk not in (
                locked_game.home_team_id,
                locked_game.away_team_id,
            ):
                raise ValidationError('Picked team is not playing in this game.')
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


def settle_game(game: Game) -> list[Pick]:
    with transaction.atomic():
        locked_game = Game.objects.select_for_update().get(pk=game.pk)
        picks = list(locked_game.picks.select_for_update().order_by('id'))
        if locked_game.status not in (Game.Status.FINAL, Game.Status.CANCELLED):
            return picks
        if locked_game.status == Game.Status.FINAL and (
            locked_game.home_score is None or locked_game.away_score is None
        ):
            raise ValidationError('Final game is missing a score.')
        for pick in picks:
            settle_pick(pick, game=locked_game)
        return picks


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
