from django import forms

from src.accounts.models import UserProfile


class ProfilePreferencesForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["profile_image", "favorite_team", "world_cup_team"]
