from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from membership.models import Member
from tests.core.factories import UserFactory
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def describe_member_directory():
    def it_redirects_anonymous_users(client: Client):
        resp = client.get(reverse("member_directory"))
        assert resp.status_code == 302

    def it_returns_403_for_user_without_member_record(client: Client):
        user = UserFactory()
        client.force_login(user)
        resp = client.get(reverse("member_directory"))
        assert resp.status_code == 403

    def it_returns_200_for_active_member(client: Client):
        user = UserFactory()
        MemberFactory(user=user, status=Member.Status.ACTIVE)
        client.force_login(user)
        resp = client.get(reverse("member_directory"))
        assert resp.status_code == 200

    def it_shows_only_active_members(client: Client):
        user = UserFactory()
        MemberFactory(user=user, status=Member.Status.ACTIVE)
        active = MemberFactory(full_legal_name="Active Person", status=Member.Status.ACTIVE)
        MemberFactory(full_legal_name="Former Person", status=Member.Status.FORMER)
        client.force_login(user)
        resp = client.get(reverse("member_directory"))
        members = list(resp.context["members"])
        assert active in members
        assert all(m.status == Member.Status.ACTIVE for m in members)

    def it_returns_403_for_former_member(client: Client):
        user = UserFactory()
        MemberFactory(user=user, status=Member.Status.FORMER)
        client.force_login(user)
        resp = client.get(reverse("member_directory"))
        assert resp.status_code == 403


def describe_profile_edit():
    def it_redirects_anonymous_users(client: Client):
        resp = client.get(reverse("profile_edit"))
        assert resp.status_code == 302

    def it_returns_403_for_user_without_member_record(client: Client):
        user = UserFactory()
        client.force_login(user)
        resp = client.get(reverse("profile_edit"))
        assert resp.status_code == 403

    def it_returns_200_for_active_member(client: Client):
        user = UserFactory()
        MemberFactory(user=user, status=Member.Status.ACTIVE)
        client.force_login(user)
        resp = client.get(reverse("profile_edit"))
        assert resp.status_code == 200

    def it_updates_preferred_name_on_post(client: Client):
        user = UserFactory()
        member = MemberFactory(user=user, status=Member.Status.ACTIVE, preferred_name="Old")
        client.force_login(user)
        resp = client.post(
            reverse("profile_edit"),
            {
                "preferred_name": "New Name",
                "phone": "",
                "emergency_contact_name": "",
                "emergency_contact_phone": "",
                "emergency_contact_relationship": "",
            },
        )
        assert resp.status_code == 302
        member.refresh_from_db()
        assert member.preferred_name == "New Name"

    def it_rerenders_form_on_invalid_post(client: Client):
        user = UserFactory()
        MemberFactory(user=user, status=Member.Status.ACTIVE)
        client.force_login(user)
        # phone field has max_length=20, so a very long value should fail
        resp = client.post(
            reverse("profile_edit"),
            {
                "preferred_name": "",
                "phone": "x" * 21,
                "emergency_contact_name": "",
                "emergency_contact_phone": "",
                "emergency_contact_relationship": "",
            },
        )
        assert resp.status_code == 200
        assert resp.context["form"].errors

    def it_redirects_after_save(client: Client):
        user = UserFactory()
        MemberFactory(user=user, status=Member.Status.ACTIVE)
        client.force_login(user)
        resp = client.post(
            reverse("profile_edit"),
            {
                "preferred_name": "X",
                "phone": "",
                "emergency_contact_name": "",
                "emergency_contact_phone": "",
                "emergency_contact_relationship": "",
            },
        )
        assert resp.status_code == 302
        assert resp.url == reverse("profile_edit")
