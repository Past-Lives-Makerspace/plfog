"""A guest account (#654) on Manage Members, on its edit page, and in member counts."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from core.events import resolvers
from membership.models import Member
from membership.services.provisioning import provision_user_for_member
from plfog.dashboard import dashboard_callback
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

_ROLES_MANAGEMENT = {
    "roles-TOTAL_FORMS": "0",
    "roles-INITIAL_FORMS": "0",
    "roles-MIN_NUM_FORMS": "0",
    "roles-MAX_NUM_FORMS": "1000",
}


def _sign_in_admin(client: Client) -> None:
    User.objects.create_superuser(username="guestadmin", email="guestadmin@x.com", password="p")
    client.login(username="guestadmin", password="p")


def _guest(name: str = "Gail Guestbooker") -> Member:
    """A guest with an account, as a class booking makes one."""
    member = MemberFactory(full_legal_name=name, _pre_signup_email=f"{name.split()[0].lower()}@example.com")
    provision_user_for_member(member)
    member.status = Member.Status.GUEST
    member.save(update_fields=["status"])
    return member


def _row(response, name: str):  # noqa: ANN202 - hub.views.PersonRow
    return next(row for row in response.context["page"] if row.name == name)


def _edit_data(member: Member, *, status: str, role: str) -> dict[str, str]:
    return {
        "full_legal_name": member.full_legal_name,
        "preferred_name": "",
        "pronouns": "",
        "discord_handle": "",
        "about_me": "",
        "status": status,
        "member_type": Member.MemberType.STANDARD,
        "role": role,
        "show_in_directory": "on",
        **_ROLES_MANAGEMENT,
    }


def describe_manage_members():
    def it_labels_a_guest_as_guest_in_both_status_and_role(client):
        _sign_in_admin(client)
        _guest()

        row = _row(client.get(reverse("hub_admin_members") + "?status=all"), "Gail Guestbooker")

        assert row.status_display == "Guest"
        assert row.role_display == "Guest"

    def it_offers_a_guest_filter_that_lists_only_guests(client):
        _sign_in_admin(client)
        _guest()
        MemberFactory(full_legal_name="Avery Activemember")

        response = client.get(reverse("hub_admin_members") + "?status=guest")

        assert b'<option value="guest" selected>Guest</option>' in response.content
        assert [row.name for row in response.context["page"]] == ["Gail Guestbooker"]

    def it_leaves_guests_out_of_the_default_active_list(client):
        _sign_in_admin(client)
        _guest()

        response = client.get(reverse("hub_admin_members"))

        assert "Gail Guestbooker" not in [row.name for row in response.context["page"]]


def describe_the_member_edit_page():
    def it_keeps_a_guest_a_guest_when_saved_unchanged(client):
        _sign_in_admin(client)
        guest = _guest()
        page = client.get(reverse("hub_admin_member_edit", args=[guest.pk]))
        assert page.context["form"]["role"].initial == Member.ADMIN_ROLE_GUEST

        client.post(
            reverse("hub_admin_member_edit", args=[guest.pk]),
            data=_edit_data(guest, status=Member.Status.GUEST, role=Member.ADMIN_ROLE_GUEST),
        )

        guest.refresh_from_db()
        assert guest.status == Member.Status.GUEST

    def it_turns_a_guest_into_an_active_member(client):
        _sign_in_admin(client)
        guest = _guest()

        response = client.post(
            reverse("hub_admin_member_edit", args=[guest.pk]),
            data=_edit_data(guest, status=Member.Status.ACTIVE, role=Member.FogRole.MEMBER),
        )

        assert response.status_code == 302
        guest.refresh_from_db()
        assert guest.status == Member.Status.ACTIVE

    def it_still_makes_an_active_member_former_with_the_guest_role(client):
        _sign_in_admin(client)
        member = MemberFactory(full_legal_name="Avery Activemember")

        client.post(
            reverse("hub_admin_member_edit", args=[member.pk]),
            data=_edit_data(member, status=Member.Status.ACTIVE, role=Member.ADMIN_ROLE_GUEST),
        )

        member.refresh_from_db()
        assert member.status == Member.Status.FORMER


def describe_member_counts():
    def it_leaves_guests_out_of_the_active_member_queryset():
        guest = _guest()
        active = MemberFactory()

        assert list(Member.objects.active()) == [active]
        assert guest not in Member.objects.active()

    def it_leaves_guests_out_of_the_dashboard_active_count():
        _guest()
        MemberFactory()

        context = dashboard_callback(RequestFactory().get("/admin/"), {})

        assert context["stats"]["active_members"] == 1

    def it_leaves_signed_in_guests_out_of_member_wide_announcements():
        guest = _guest()
        User.objects.filter(pk=guest.user_id).update(last_login=timezone.now())

        recipients = resolvers.all_active_members({})

        assert guest.user_id not in {user.pk for user, _reason in recipients}
