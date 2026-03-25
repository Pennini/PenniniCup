from django.urls import path

from . import views

app_name = "football"

urlpatterns = [
    path("partidas/", views.match_list, name="matches"),
]
