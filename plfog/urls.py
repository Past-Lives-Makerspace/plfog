from django.conf import settings as django_settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from membership import views as membership_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("guilds/", include("membership.urls")),
    path("member-hub/", include("membership.hub_urls")),
    # Stripe callbacks
    path("checkout/success/", membership_views.checkout_success, name="checkout_success"),
    path("checkout/cancel/", membership_views.checkout_cancel, name="checkout_cancel"),
    # Member pages
    path("members/", membership_views.member_directory, name="member_directory"),
    # Redirects from old account URLs
    path("account/orders/", RedirectView.as_view(url="/member-hub/orders/", permanent=True)),
    path("account/profile/", RedirectView.as_view(url="/member-hub/profile/", permanent=True)),
    path("", include("core.urls")),
]

if django_settings.DEBUG:  # pragma: no cover
    urlpatterns += static(django_settings.MEDIA_URL, document_root=django_settings.MEDIA_ROOT)  # type: ignore[arg-type]
