from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def custom_bad_request(request: HttpRequest, exception: Exception) -> HttpResponse:
    return render(request, "400.html", status=400)


def custom_permission_denied(request: HttpRequest, exception: Exception) -> HttpResponse:
    return render(request, "403.html", {"exception": exception}, status=403)


def custom_page_not_found(request: HttpRequest, exception: Exception) -> HttpResponse:
    return render(request, "404.html", status=404)


def custom_server_error(request: HttpRequest) -> HttpResponse:
    return render(request, "500.html", status=500)
