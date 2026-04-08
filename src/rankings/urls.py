from django.urls import path

from src.rankings import views

app_name = "rankings"

urlpatterns = [
    path("pool/<slug:slug>/", views.pool_ranking_dashboard, name="pool-dashboard"),
]
