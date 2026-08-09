from allauth.account.views import EmailView, RequestLoginCodeView
from django.contrib import admin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import include, path

from plfog.admin_login import install_admin_login_redirect
from plfog.admin_views import (
    invite_member,
    member_aliases,
    member_aliases_add,
    member_aliases_remove,
    member_aliases_set_primary,
    member_aliases_toggle_verified,
    site_announcement,
)

# Swap the stock admin password login for a redirect/deny. MUST run before
# admin.site.urls is evaluated below (AdminSite.get_urls binds self.login then).
install_admin_login_redirect()

# Custom admin URLs must be before admin.site.urls
admin_custom_urls = [
    path("admin/membership/member/invite/", invite_member, name="admin_invite_member"),
    path("admin/announcement/", site_announcement, name="admin_site_announcement"),
    path(
        "admin/members/<int:pk>/aliases/",
        member_aliases,
        name="admin_member_aliases",
    ),
    path(
        "admin/members/<int:pk>/aliases/add/",
        member_aliases_add,
        name="admin_member_aliases_add",
    ),
    path(
        "admin/members/<int:pk>/aliases/<int:email_pk>/remove/",
        member_aliases_remove,
        name="admin_member_aliases_remove",
    ),
    path(
        "admin/members/<int:pk>/aliases/<int:email_pk>/set-primary/",
        member_aliases_set_primary,
        name="admin_member_aliases_set_primary",
    ),
    path(
        "admin/members/<int:pk>/aliases/<int:email_pk>/toggle-verified/",
        member_aliases_toggle_verified,
        name="admin_member_aliases_toggle_verified",
    ),
]


class HubEmailView(EmailView):
    """Override allauth's email management view to redirect into the hub's User Settings page.

    POSTs (add, make primary, re-send, remove) still run through allauth's
    EmailView logic; only the success_url and GET rendering change so the user
    always lands on /settings/?tab=emails instead of the legacy themed page.
    """

    # Always land back on the Emails tab after add/primary/resend/remove so the
    # user's context is preserved instead of bouncing them to Profile.
    success_url = "/settings/?tab=emails"

    def get(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        return redirect("/settings/?tab=emails")


class SeededRequestLoginCodeView(RequestLoginCodeView):
    """Override allauth's request-login-code view to honor ``?email=`` prefill.

    allauth's :class:`~allauth.account.views.RequestLoginCodeView` has no
    ``get_initial``, so the ``?email=`` query string the "Send login invite" email
    points at is otherwise ignored. Seeding the initial here makes the first-time
    sign-in seamless: the member lands on the page with their address already filled
    and just requests a code.
    """

    def get_initial(self) -> dict[str, object]:
        initial = super().get_initial()
        email = self.request.GET.get("email", "")
        if email:
            initial["email"] = email
        return initial


urlpatterns = admin_custom_urls + [
    path("admin/", admin.site.urls),
    # Must precede the allauth include so our overrides win URL resolution.
    path("accounts/email/", HubEmailView.as_view(), name="account_email"),
    path("accounts/login/code/", SeededRequestLoginCodeView.as_view(), name="account_request_login_code"),
    path("accounts/", include("allauth.urls")),
    path("billing/", include("billing.urls")),
    path("classes/", include("classes.urls")),
    path("account/", include("classes.account.urls", namespace="account")),
    path("api/v1/", include("plfog.api_urls")),
    # Member hub
    path("", include("hub.urls")),
    path("", include("core.urls")),
]
