from dataclasses import dataclass

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from sports.models import Game, GameLine, Season
from sports.providers.the_odds_api.weeks import season_year_for, week_for
from sports.seed import NFL_ABBREVIATION

from picks.forms import PickForm
from picks.models import Pick
from picks.operations import UpcomingGame, submit_pick, upcoming_games, user_picks

_MAX_WEEK = 32_767


@dataclass
class GameCard:
    game: Game
    line: GameLine | None
    picks: list[Pick]
    forms: list[PickForm]
    error: str | None = None


@login_required
def game_slate(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    week = _week_from_request(request, now)
    if isinstance(week, HttpResponse):
        return week
    season = _nfl_season(now)
    return render(
        request,
        'picks/slate.html',
        _slate_context(request, now, week, season),
    )


@login_required
@require_POST
def submit_game_pick(request: HttpRequest, game_id: int) -> HttpResponse:
    game = get_object_or_404(
        Game.objects.select_related('home_team', 'away_team', 'season'),
        pk=game_id,
    )
    now = timezone.now()
    market = _posted_market(request)
    if market is None:
        return _reject(request, game, now, message='Choose a market.')

    form = PickForm(
        request.POST,
        game=game,
        pick_type=market,
        line=None,
        prefix=market,
    )
    if not form.is_valid():
        return _reject(
            request,
            game,
            now,
            message=_form_error_text(form),
            market=market,
        )

    try:
        submit_pick(
            user=request.user,
            game=game,
            game_line=form.cleaned_data['game_line'],
            picked_team=form.cleaned_data['picked_team'],
            pick_type=form.cleaned_data['pick_type'],
            now=now,
        )
    except ValidationError as exc:
        return _reject(
            request,
            game,
            now,
            message=' '.join(exc.messages),
            market=market,
            picked_team_id=form.cleaned_data['picked_team'].pk,
        )

    if _is_htmx(request):
        return render(
            request,
            'picks/_game_card.html',
            {'card': _card_for(request, game, now)},
        )
    return redirect('game-slate')


@login_required
def pick_history(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        'picks/history.html',
        {'picks': user_picks(request.user)},
    )


def _slate_context(
    request,
    now,
    week: int,
    season: Season | None,
    *,
    focus: GameCard | None = None,
):
    slate = (
        upcoming_games(now=now, season=season, week=week) if season is not None else []
    )
    picks_by_game = _picks_by_game(request.user, [item.game for item in slate])
    cards = [
        _game_card(item.game, item.line, picks_by_game.get(item.game.id, []))
        for item in slate
    ]
    if focus is not None:
        cards = [focus if card.game.id == focus.game.id else card for card in cards]
        if all(card.game.id != focus.game.id for card in cards):
            cards.insert(0, focus)
    return {
        'cards': cards,
        'week': week,
        'season': season,
    }


def _reject(
    request: HttpRequest,
    game: Game,
    now,
    *,
    message: str,
    market: str | None = None,
    picked_team_id: int | None = None,
) -> HttpResponse:
    card = _card_for(
        request,
        game,
        now,
        error=message,
        market=market,
        picked_team_id=picked_team_id,
    )
    if _is_htmx(request):
        return render(request, 'picks/_game_card.html', {'card': card}, status=422)
    week = game.week if game.week is not None else week_for(now)
    return render(
        request,
        'picks/slate.html',
        _slate_context(request, now, week, game.season, focus=card),
        status=422,
    )


def _card_for(
    request: HttpRequest,
    game: Game,
    now,
    *,
    error: str | None = None,
    market: str | None = None,
    picked_team_id: int | None = None,
) -> GameCard:
    item = _open_item(game, now)
    line = item.line if item is not None else None
    picks = list(user_picks(request.user, games=[game]))
    initial = {}
    if picked_team_id is not None:
        initial['picked_team'] = picked_team_id
    replacements = {}
    if market is not None and line is not None and initial:
        replacements[market] = PickForm(
            game=game,
            pick_type=market,
            line=line,
            prefix=market,
            initial=initial,
        )
    return _game_card(game, line, picks, error=error, replacements=replacements)


def _game_card(
    game: Game,
    line: GameLine | None,
    picks: list[Pick],
    *,
    error: str | None = None,
    replacements: dict[str, PickForm] | None = None,
) -> GameCard:
    return GameCard(
        game=game,
        line=line,
        picks=picks,
        forms=_forms_for(game, line, picks, replacements or {}),
        error=error,
    )


def _forms_for(
    game: Game,
    line: GameLine | None,
    picks: list[Pick],
    replacements: dict[str, PickForm],
) -> list[PickForm]:
    if line is None:
        return []
    taken = {pick.pick_type for pick in picks}
    forms = []
    for market in _markets(line):
        if market in taken:
            continue
        forms.append(
            replacements.get(market)
            or PickForm(game=game, pick_type=market, line=line, prefix=market)
        )
    return forms


def _markets(line: GameLine) -> list[str]:
    markets: list[str] = []
    if line.home_spread is not None:
        markets.append(Pick.PickType.SPREAD)
    if line.home_moneyline is not None:
        markets.append(Pick.PickType.MONEYLINE)
    return markets


def _open_item(game: Game, now) -> UpcomingGame | None:
    week = game.week if game.week is not None else None
    for item in upcoming_games(now=now, season=game.season, week=week):
        if item.game.pk == game.pk:
            return item
    return None


def _picks_by_game(user, games: list[Game]) -> dict[int, list[Pick]]:
    grouped: dict[int, list[Pick]] = {}
    for pick in user_picks(user, games=games):
        grouped.setdefault(pick.game_id, []).append(pick)
    return grouped


def _nfl_season(now) -> Season | None:
    return (
        Season.objects.filter(
            league__abbreviation=NFL_ABBREVIATION,
            year=season_year_for(now),
        )
        .select_related('league')
        .first()
    )


def _week_from_request(request: HttpRequest, now):
    raw = request.GET.get('week')
    if raw is None or raw == '':
        return week_for(now)
    try:
        week = int(raw)
    except ValueError:
        week = 0
    if week < 1 or week > _MAX_WEEK:
        return HttpResponseBadRequest('Week must be a positive integer.')
    return week


def _posted_market(request: HttpRequest) -> str | None:
    for market in Pick.PickType.values:
        if f'{market}-pick_type' in request.POST:
            return market
    return None


def _form_error_text(form: forms.Form) -> str:
    messages: list[str] = []
    for field_errors in form.errors.values():
        messages.extend(str(error) for error in field_errors)
    return ' '.join(messages)


def _is_htmx(request: HttpRequest) -> bool:
    return bool(getattr(request, 'htmx', False))
