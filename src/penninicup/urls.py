from django.urls import path

from . import views

app_name = "penninicup"

urlpatterns = [
    path("", views.index, name="index"),
    path("regras/", views.rules, name="rules"),
    path("perfil/", views.profile, name="profile"),
    path("perfil/<str:username>/", views.profile_user, name="profile-user"),
]
