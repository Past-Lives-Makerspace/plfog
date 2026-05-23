from allauth.account.views import EmailView
from django.contrib import admin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import include, path

from plfog.admin_views import (
    invite_member,
    member_aliases,
    member_aliases_add,
    member_aliases_remove,
    member_aliases_set_primary,
    member_aliases_toggle_verified,
    snapshot_delete,
    snapshot_detail,
    snapshot_draft,
    snapshot_take,
)

# Custom admin URLs must be before admin.site.urls
admin_custom_urls = [
    path("admin/membership/member/invite/", invite_member, name="admin_invite_member"),
    path("admin/snapshots/draft/", snapshot_draft, name="admin_snapshot_draft"),
    path("admin/snapshots/take/", snapshot_take, name="admin_snapshot_take"),
    path("admin/snapshots/<int:pk>/", snapshot_detail, name="admin_snapshot_detail"),
    path("admin/snapshots/<int:pk>/delete/", snapshot_delete, name="admin_snapshot_delete"),
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


urlpatterns = admin_custom_urls + [
    path("admin/", admin.site.urls),
    # Must precede the allauth include so our override wins URL resolution.
    path("accounts/email/", HubEmailView.as_view(), name="account_email"),
    path("accounts/", include("allauth.urls")),
    path("billing/", include("billing.urls")),
    path("classes/", include("classes.urls")),
    path("account/", include("classes.account.urls", namespace="account")),
    # Member hub
    path("", include("hub.urls")),
    path("", include("core.urls")),
]
