from django.contrib import admin

from picks.models import Pick


@admin.register(Pick)
class PickAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'game',
        'pick_type',
        'picked_team',
        'total_side',
        'line_value',
        'price',
        'result',
        'points',
        'submitted_at',
    )
    list_filter = ('pick_type', 'result')
    list_select_related = (
        'user',
        'game',
        'game__home_team',
        'game__away_team',
        'picked_team',
    )
    readonly_fields = (
        'user',
        'game',
        'game_line',
        'picked_team',
        'pick_type',
        'total_side',
        'line_value',
        'price',
        'result',
        'points',
        'submitted_at',
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
