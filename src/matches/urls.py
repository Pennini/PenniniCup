from django.urls import path

from . import views

app_name = "matches"

urlpatterns = [
    path("", views.match_list, name="list"),
]
