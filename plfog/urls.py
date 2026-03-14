from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    # Member hub
    path("", include("hub.urls")),
    path("", include("core.urls")),
]
