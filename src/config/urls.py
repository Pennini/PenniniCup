from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

handler400 = "src.config.settings.error_handlers.custom_bad_request"
handler403 = "src.config.settings.error_handlers.custom_permission_denied"
handler404 = "src.config.settings.error_handlers.custom_page_not_found"
handler500 = "src.config.settings.error_handlers.custom_server_error"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("", include("src.penninicup.urls")),
    path("pools/", include("src.pool.urls")),
    path("rankings/", include("src.rankings.urls")),
    path("accounts/", include("src.accounts.urls")),
    path("payments/", include("src.payments.urls")),
    path("football/", include("src.football.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.DEBUG:
    # Include django_browser_reload URLs only in DEBUG mode
    urlpatterns += [
        path("__reload__/", include("django_browser_reload.urls")),
    ]
