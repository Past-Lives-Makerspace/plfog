from django.contrib import admin
from django.urls import include, path

from plfog.admin_views import take_snapshot

# Custom admin URLs must be before admin.site.urls
admin_custom_urls = [
    path("admin/take-snapshot/", take_snapshot, name="admin_take_snapshot"),
]

urlpatterns = admin_custom_urls + [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    # Member hub
    path("", include("hub.urls")),
    path("", include("core.urls")),
]
