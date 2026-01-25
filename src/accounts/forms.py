from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class CustomUserCreationForm(UserCreationForm):
    invite_token = forms.CharField(
        max_length=36,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Cole seu token de convite aqui"}),
        label="Token de Convite",
    )

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2", "invite_token")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user
