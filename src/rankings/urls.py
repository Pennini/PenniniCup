from django.urls import path

from src.rankings import views

app_name = "rankings"

urlpatterns = [
    path("pool/<slug:slug>/", views.pool_ranking_dashboard, name="pool-dashboard"),
    path("pool/<slug:slug>/match-guesses/", views.match_guesses_partial, name="match-guesses-partial"),
    path("pool/<slug:slug>/toggle-stars/", views.toggle_supporter_stars, name="toggle-stars"),
]
