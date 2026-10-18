from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from picks.operations import settle_game

from sports.models import Game, GameLine, League, Season, Team

_SETTLEABLE = {Game.Status.FINAL, Game.Status.CANCELLED}


def _settle_and_report(request, game: Game) -> None:
    if game.status not in _SETTLEABLE:
        messages.warning(request, f'{game}: left unchanged')
        return
    try:
        settle_game(game)
    except ValidationError as exc:
        messages.error(request, f'{game}: {" ".join(exc.messages)}')
    else:
        messages.success(request, f'{game}: settled')


@admin.action(description='Settle selected games')
def settle_selected(modeladmin, request, queryset):
    for game in queryset:
        _settle_and_report(request, game)


@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ('name', 'abbreviation', 'sport')
    search_fields = ('name', 'abbreviation')


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ('league', 'year')
    list_filter = ('league',)
    list_select_related = ('league',)


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ('name', 'abbreviation', 'league')
    list_filter = ('league',)
    search_fields = ('name', 'abbreviation')
    list_select_related = ('league',)


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = (
        'matchup',
        'week',
        'kickoff_at',
        'status',
        'away_score',
        'home_score',
    )
    list_filter = ('status', 'week', 'season')
    list_select_related = ('home_team', 'away_team', 'season')
    readonly_fields = (
        'external_id',
        'season',
        'home_team',
        'away_team',
        'created_at',
        'updated_at',
    )
    actions = [settle_selected]

    def get_deleted_objects(self, objs, request):
        deleted, counts, perms_needed, protected = super().get_deleted_objects(
            objs, request
        )
        perms_needed.discard(GameLine._meta.verbose_name)
        return deleted, counts, perms_needed, protected

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.status in _SETTLEABLE:
            _settle_and_report(request, obj)

    @admin.display(description='Matchup')
    def matchup(self, obj: Game) -> str:
        return str(obj)


@admin.register(GameLine)
class GameLineAdmin(admin.ModelAdmin):
    list_display = (
        'game',
        'captured_at',
        'bookmaker',
        'home_spread',
        'away_spread',
        'home_moneyline',
        'away_moneyline',
    )
    list_filter = ('bookmaker', 'source')
    list_select_related = ('game', 'game__home_team', 'game__away_team')
    readonly_fields = (
        'game',
        'source',
        'bookmaker',
        'home_spread',
        'away_spread',
        'home_spread_price',
        'away_spread_price',
        'home_moneyline',
        'away_moneyline',
        'captured_at',
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
