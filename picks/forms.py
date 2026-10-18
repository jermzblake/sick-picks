from django import forms
from sports.models import Game, GameLine, Team

from picks.models import Pick


class PickForm(forms.Form):
    picked_team = forms.ModelChoiceField(
        queryset=Team.objects.none(),
        widget=forms.RadioSelect,
        empty_label=None,
    )
    pick_type = forms.ChoiceField(
        choices=Pick.PickType.choices,
        widget=forms.HiddenInput,
    )
    game_line = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(
        self,
        *args,
        game: Game,
        pick_type: str,
        line: GameLine | None,
        **kwargs,
    ):
        self.game = game
        self.market = pick_type
        super().__init__(*args, **kwargs)
        team_field = self.fields['picked_team']
        assert isinstance(team_field, forms.ModelChoiceField)
        team_field.queryset = Team.objects.filter(
            pk__in=[game.home_team_id, game.away_team_id]
        )
        if not self.is_bound:
            self.initial.setdefault('pick_type', pick_type)
            if line is not None:
                self.initial.setdefault('game_line', line.pk)

    @property
    def market_label(self) -> str:
        return str(Pick.PickType(self.market).label)

    def clean_pick_type(self) -> str:
        value = self.cleaned_data['pick_type']
        if value != self.market:
            raise forms.ValidationError('Unknown pick type.')
        return value

    def clean_game_line(self) -> GameLine:
        line_id = self.cleaned_data['game_line']
        line = GameLine.objects.filter(pk=line_id).first()
        if line is None or line.game_id != self.game.id:
            raise forms.ValidationError('Line does not belong to this game.')
        return line
