import os
from datetime import datetime

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import path
from picks.operations import SettleReport, settle_game, settle_window

from sports.models import Game, GameLine, League, Season, Team
from sports.providers.the_odds_api.client import OddsApiError
from sports.providers.the_odds_api.normalize import MappingError
from sports.sync import SyncError, SyncReport, run_nfl_sync

_SETTLEABLE = {Game.Status.FINAL, Game.Status.CANCELLED}


class NflSyncForm(forms.Form):
    commence_from = forms.DateTimeField(
        required=False,
        label='From',
        help_text='Inclusive. Leave both bounds empty to sync the current week.',
    )
    commence_to = forms.DateTimeField(
        required=False,
        label='To',
        help_text='Exclusive. Set both bounds to sync a different window.',
    )

    def clean(self):
        cleaned = super().clean()
        commence_from = cleaned.get('commence_from')
        commence_to = cleaned.get('commence_to')
        if (commence_from is None) != (commence_to is None):
            raise ValidationError('commence bounds must be provided together')
        if (
            commence_from is not None
            and commence_to is not None
            and commence_from >= commence_to
        ):
            raise ValidationError('commence_from must be earlier than commence_to')
        return cleaned


class SettleGamesForm(forms.Form):
    kickoff_from = forms.DateTimeField(
        required=False,
        label='From',
        help_text='Inclusive. Leave both bounds empty to settle the current week.',
    )
    kickoff_to = forms.DateTimeField(
        required=False,
        label='To',
        help_text='Exclusive. Set both bounds to settle a different window.',
    )

    def clean(self):
        cleaned = super().clean()
        kickoff_from = cleaned.get('kickoff_from')
        kickoff_to = cleaned.get('kickoff_to')
        if (kickoff_from is None) != (kickoff_to is None):
            raise ValidationError('kickoff bounds must be provided together')
        if (
            kickoff_from is not None
            and kickoff_to is not None
            and kickoff_from >= kickoff_to
        ):
            raise ValidationError('kickoff_from must be earlier than kickoff_to')
        return cleaned


def _sync_summary(report: SyncReport) -> str:
    return (
        f'Games created: {report.games_created}. '
        f'Games updated: {report.games_updated}. '
        f'Games unchanged: {report.games_unchanged}. '
        f'Games skipped: {report.games_skipped}. '
        f'Lines created: {report.lines_created}.'
    )


def _settle_summary(report: SettleReport) -> str:
    return f'Settled: {report.settled}.'


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
    change_list_template = 'admin/sports/game/change_list.html'

    def get_urls(self):
        return [
            path(
                'sync/',
                self.admin_site.admin_view(self.sync_nfl_data),
                name='sports_game_sync',
            ),
            path(
                'settle/',
                self.admin_site.admin_view(self.settle_games),
                name='sports_game_settle',
            ),
            *super().get_urls(),
        ]

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['show_nfl_sync'] = self.has_change_permission(request)
        return super().changelist_view(request, extra_context=extra_context)

    def sync_nfl_data(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied
        form = NflSyncForm(request.POST if request.method == 'POST' else None)
        if request.method == 'POST' and form.is_valid():
            commence_from: datetime | None = form.cleaned_data['commence_from']
            commence_to: datetime | None = form.cleaned_data['commence_to']
            try:
                report = run_nfl_sync(
                    api_key=os.environ.get('ODDS_API_KEY', ''),
                    commence_from=commence_from,
                    commence_to=commence_to,
                )
            except (ValueError, OddsApiError, MappingError, SyncError) as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _sync_summary(report))
                for problem in report.problems:
                    messages.warning(request, problem)
                return redirect('admin:sports_game_changelist')
        return render(
            request,
            'admin/sports/game/sync_nfl_data.html',
            {
                **self.admin_site.each_context(request),
                'form': form,
                'opts': self.model._meta,
                'title': 'Sync NFL data',
            },
        )

    def settle_games(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied
        form = SettleGamesForm(request.POST if request.method == 'POST' else None)
        if request.method == 'POST' and form.is_valid():
            kickoff_from: datetime | None = form.cleaned_data['kickoff_from']
            kickoff_to: datetime | None = form.cleaned_data['kickoff_to']
            try:
                report = settle_window(
                    kickoff_from=kickoff_from,
                    kickoff_to=kickoff_to,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _settle_summary(report))
                for problem in report.problems:
                    messages.error(request, problem)
                return redirect('admin:sports_game_changelist')
        return render(
            request,
            'admin/sports/game/settle_games.html',
            {
                **self.admin_site.each_context(request),
                'form': form,
                'opts': self.model._meta,
                'title': 'Settle games',
            },
        )

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
