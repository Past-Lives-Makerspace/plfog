from django.conf import settings as django_settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from membership import views as membership_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("guilds/", include("membership.urls")),
    # Member pages
    path("members/", membership_views.member_directory, name="member_directory"),
    # User account area
    path("account/profile/", membership_views.profile_edit, name="profile_edit"),
    # Guild voting
    path("voting/", include("membership.vote_urls")),
    path("", include("core.urls")),
]

if django_settings.DEBUG:  # pragma: no cover
    urlpatterns += static(django_settings.MEDIA_URL, document_root=django_settings.MEDIA_ROOT)  # type: ignore[arg-type]
