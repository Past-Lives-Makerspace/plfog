from django.conf import settings as django_settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from membership import views as membership_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("guilds/", include("membership.urls")),
    # Stripe callbacks
    path("checkout/success/", membership_views.checkout_success, name="checkout_success"),
    path("checkout/cancel/", membership_views.checkout_cancel, name="checkout_cancel"),
    # Member pages
    path("members/", membership_views.member_directory, name="member_directory"),
    # User account area
    path("account/orders/", membership_views.user_orders, name="user_orders"),
    path("account/profile/", membership_views.profile_edit, name="profile_edit"),
    path("", include("core.urls")),
]

if django_settings.DEBUG:  # pragma: no cover
    urlpatterns += static(django_settings.MEDIA_URL, document_root=django_settings.MEDIA_ROOT)  # type: ignore[arg-type]
