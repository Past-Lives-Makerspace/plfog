from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    # Guild voting
    path("voting/", include("membership.vote_urls")),
    path("", include("core.urls")),
]
