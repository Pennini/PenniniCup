from django.urls import path

from src.rankings.views import pool_ranking_dashboard

from . import views

app_name = "pool"

urlpatterns = [
    path("", views.pool_list, name="list"),
    path("join-by-token/", views.join_pool_by_token, name="join-by-token"),
    path("open/", views.open_pool, name="open"),
    path("palpites/", views.bets_tab, name="bets-tab"),
    path("ranking/", views.ranking_tab, name="ranking-tab"),
    path("<slug:slug>/ranking/", pool_ranking_dashboard, name="ranking"),
    path("<slug:slug>/", views.pool_detail, name="detail"),
    path("<slug:slug>/join/", views.join_pool, name="join"),
    path("<slug:slug>/bet/<int:match_id>/", views.save_bet, name="save-bet"),
    path("<slug:slug>/bets/save/", views.save_bets_bulk, name="save-bets-bulk"),
    path("<slug:slug>/projection-status/", views.projection_status, name="projection-status"),
    path("<slug:slug>/knockout-cards/", views.knockout_cards_partial, name="knockout-cards"),
]
