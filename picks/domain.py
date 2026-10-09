from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone
from sports.models import Game, GameLine, Team

from picks.models import Pick

_STAKE = Decimal('100')
_POINTS = Decimal('0.01')
_SPREAD_WIN = Decimal('1.00')
_SPREAD_PUSH = Decimal('0.50')
_SPREAD_LOSS = Decimal('0.00')
_NO_PROFIT = Decimal('0.00')
_OPEN_FOR_PICKS = {
    Game.Status.SCHEDULED,
    Game.Status.POSTPONED,
    Game.Status.DELAYED,
}


def submit_pick(
    *,
    user,
    game: Game,
    game_line: GameLine,
    picked_team: Team,
    pick_type: str,
    now: datetime | None = None,
) -> Pick:
    if now is None:
        now = timezone.now()
    if game.status not in _OPEN_FOR_PICKS:
        raise ValidationError('Game is not open for picks.')
    if now >= game.kickoff_at:
        raise ValidationError('Pick cannot be submitted at or after kickoff.')
    if picked_team.pk not in (game.home_team_id, game.away_team_id):
        raise ValidationError('Picked team is not playing in this game.')
    if game_line.game_id != game.id:
        raise ValidationError('Line does not belong to this game.')

    line_value = None
    price = None
    if pick_type == Pick.PickType.SPREAD:
        line_value = _side_value(
            game,
            picked_team,
            game_line.home_spread,
            game_line.away_spread,
        )
        if line_value is None:
            raise ValidationError('Spread is not available on this line.')
    elif pick_type == Pick.PickType.MONEYLINE:
        price = _side_value(
            game,
            picked_team,
            game_line.home_moneyline,
            game_line.away_moneyline,
        )
        if price is None:
            raise ValidationError('Moneyline is not available on this line.')
    else:
        raise ValidationError('Unknown pick type.')

    return Pick.objects.create(
        user=user,
        game=game,
        game_line=game_line,
        picked_team=picked_team,
        pick_type=pick_type,
        line_value=line_value,
        price=price,
    )


def settle_pick(pick: Pick) -> Pick:
    game = pick.game
    if game.status == Game.Status.CANCELLED:
        pick.result = Pick.Result.VOID
        pick.points = None
        pick.save(update_fields=['result', 'points'])
        return pick
    if game.status != Game.Status.FINAL:
        return pick
    home_score = game.home_score
    away_score = game.away_score
    if home_score is None or away_score is None:
        raise ValidationError('Final game is missing a score.')
    if pick.picked_team_id not in (game.home_team_id, game.away_team_id):
        raise ValidationError('Picked team is not playing in this game.')

    if pick.pick_type == Pick.PickType.SPREAD:
        _settle_spread(pick, game, home_score, away_score)
    elif pick.pick_type == Pick.PickType.MONEYLINE:
        _settle_moneyline(pick, home_score, away_score)
    else:
        raise ValidationError('Unknown pick type.')
    pick.save(update_fields=['result', 'points'])
    return pick


def _side_value(game: Game, picked_team: Team, home_value, away_value):
    if picked_team.pk == game.home_team_id:
        return home_value
    return away_value


def _settle_spread(
    pick: Pick,
    game: Game,
    home_score: int,
    away_score: int,
) -> None:
    if pick.line_value is None:
        raise ValidationError('Spread pick is missing a line.')
    if pick.picked_team_id == game.home_team_id:
        picked_score = home_score
        opponent_score = away_score
    else:
        picked_score = away_score
        opponent_score = home_score
    margin = Decimal(picked_score) + pick.line_value - Decimal(opponent_score)
    if margin > 0:
        pick.result = Pick.Result.WIN
        pick.points = _SPREAD_WIN
    elif margin == 0:
        pick.result = Pick.Result.PUSH
        pick.points = _SPREAD_PUSH
    else:
        pick.result = Pick.Result.LOSS
        pick.points = _SPREAD_LOSS


def _settle_moneyline(pick: Pick, home_score: int, away_score: int) -> None:
    if pick.price is None:
        raise ValidationError('Moneyline pick is missing a price.')
    if home_score == away_score:
        pick.result = Pick.Result.PUSH
        pick.points = _NO_PROFIT
        return
    home_won = home_score > away_score
    picked_home = pick.picked_team_id == pick.game.home_team_id
    if home_won == picked_home:
        pick.result = Pick.Result.WIN
        pick.points = ((pick.price - 1) * _STAKE).quantize(
            _POINTS,
            rounding=ROUND_HALF_UP,
        )
        return
    pick.result = Pick.Result.LOSS
    pick.points = _NO_PROFIT
