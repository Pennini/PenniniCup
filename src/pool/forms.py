from django import forms

from src.pool.models import PoolBet
from src.pool.services.rules import PHASE_KNOCKOUT, phase_for_match


class PoolBetForm(forms.ModelForm):
    class Meta:
        model = PoolBet
        fields = ["home_score_pred", "away_score_pred", "winner_pred"]

    def __init__(self, *args, **kwargs):
        self.match = kwargs.pop("match")
        super().__init__(*args, **kwargs)

        if self.match.home_team_id and self.match.away_team_id:
            self.fields["winner_pred"].queryset = self.fields["winner_pred"].queryset.filter(
                id__in=[self.match.home_team_id, self.match.away_team_id]
            )

    def clean(self):
        cleaned_data = super().clean()
        phase = phase_for_match(self.match)
        winner_pred = cleaned_data.get("winner_pred")

        if phase == PHASE_KNOCKOUT and winner_pred is None:
            self.add_error("winner_pred", "Informe o classificado no mata-mata.")

        return cleaned_data
