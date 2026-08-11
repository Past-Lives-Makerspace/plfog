"""BDD specs for the admin Teaching access override (Spec D §6, Screen 3).

The ``hub_admin_member_teaching`` endpoint grants/revokes the teaching unlock
with a full-page POST + message; the member edit page renders the state card
(member mode only — the non-member user-edit mode hides it).
"""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import SiteActivity
from membership.models import Member

pytestmark = pytest.mark.django_db

PASSWORD = "pw12345!"


def _login_admin(client: Client, username: str = "teach-admin") -> Member:
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password=PASSWORD)
    member = Member.objects.get(user=user)
    member.fog_role = Member.FogRole.ADMIN
    member.full_legal_name = "Teach Admin"
    member.save()
    client.login(username=username, password=PASSWORD)
    return member


def _target_member(username: str = "teach-target", **fields) -> Member:
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password=PASSWORD)
    member = Member.objects.get(user=user)
    member.full_legal_name = "Target Member"
    for field, value in fields.items():
        setattr(member, field, value)
    member.save()
    return member


def _teaching_url(member: Member) -> str:
    return reverse("hub_admin_member_teaching", kwargs={"pk": member.pk})


def describe_admin_member_teaching_set():
    def it_denies_a_non_admin(client):
        user = User.objects.create_user(username="plain-poster", email="pp@x.com", password=PASSWORD)
        target = _target_member()
        client.login(username="plain-poster", password=PASSWORD)
        assert user.member.fog_role == Member.FogRole.MEMBER
        response = client.post(_teaching_url(target), {"action": "grant"})
        assert response.status_code == 403

    def it_grants_teaching_with_message_and_activity(client):
        admin = _login_admin(client)
        target = _target_member()
        response = client.post(_teaching_url(target), {"action": "grant"}, follow=True)
        target.refresh_from_db()
        assert target.instructor_oriented_at is not None
        assert "Granted teaching access for Target Member." in response.content.decode()
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.TEACHING_GRANTED)
        assert row.actor == admin.user

    def it_revokes_teaching_with_message_and_activity(client):
        admin = _login_admin(client)
        target = _target_member(instructor_oriented_at=timezone.now())
        response = client.post(_teaching_url(target), {"action": "revoke"}, follow=True)
        target.refresh_from_db()
        assert target.instructor_oriented_at is None
        assert "Revoked teaching access for Target Member." in response.content.decode()
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.TEACHING_REVOKED)
        assert row.actor == admin.user

    def it_400s_an_unknown_action(client):
        _login_admin(client)
        target = _target_member()
        response = client.post(_teaching_url(target), {"action": "explode"})
        assert response.status_code == 400
        target.refresh_from_db()
        assert target.instructor_oriented_at is None

    def it_rejects_get(client):
        _login_admin(client)
        target = _target_member()
        assert client.get(_teaching_url(target)).status_code == 405


def describe_member_edit_teaching_card():
    def it_renders_the_locked_state_with_the_grant_button(client):
        _login_admin(client)
        target = _target_member()
        content = client.get(reverse("hub_admin_member_edit", kwargs={"pk": target.pk})).content.decode()
        assert "Teaching access" in content
        assert "Locked — hasn't completed the instructor orientation." in content
        assert "Grant teaching access" in content
        assert "Revoke teaching access" not in content

    def it_renders_the_unlocked_state_with_the_revoke_confirm(client):
        _login_admin(client)
        target = _target_member(instructor_oriented_at=timezone.now())
        content = client.get(reverse("hub_admin_member_edit", kwargs={"pk": target.pk})).content.decode()
        assert "Can create classes — unlocked" in content
        assert "Revoke teaching access" in content
        assert "Grant teaching access" not in content
        assert "Revoke teaching access?" in content  # the confirm modal names the action

    def it_hides_the_card_in_the_non_member_user_edit_mode(client):
        _login_admin(client)
        user = User.objects.create_user(username="no-member-user", email="nmu@x.com")
        Member.objects.filter(user=user).delete()
        EmailAddress.objects.filter(user=user).delete()
        content = client.get(reverse("hub_admin_user_edit", kwargs={"user_pk": user.pk})).content.decode()
        assert "Teaching access" not in content
