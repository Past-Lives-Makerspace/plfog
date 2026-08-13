"""BDD specs for the admin Instructor permission (Permissions tab).

The ``hub_admin_member_teaching`` endpoint now grants/revokes the unified *Instructor*
permission: the public instructor page (``instructor_slug``) AND teaching access
(``instructor_oriented_at``) together. The member edit Permissions tab renders it as a
toggle (member mode only — the non-member user-edit mode hides it).
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


def describe_grant_and_revoke_instructor_models():
    def it_grants_a_bio_page_and_teaching(db):
        target = _target_member()
        target.grant_instructor(granted_by=None)
        target.refresh_from_db()
        assert target.is_instructor  # a public instructor slug was minted
        assert target.can_create_classes  # teaching unlocked

    def it_gives_an_already_oriented_member_the_bio_page(db):
        # A member who completed the instructor orientation (teaching, no bio) gains the page.
        target = _target_member(instructor_oriented_at=timezone.now())
        assert not target.is_instructor
        target.grant_instructor(granted_by=None)
        target.refresh_from_db()
        assert target.is_instructor

    def it_revoke_clears_both_the_bio_and_teaching(db):
        target = _target_member(instructor_oriented_at=timezone.now(), instructor_slug="target-member")
        target.revoke_instructor(revoked_by=None)
        target.refresh_from_db()
        assert not target.is_instructor
        assert not target.can_create_classes

    def it_logs_exactly_once_on_a_fresh_grant(db):
        target = _target_member()
        target.grant_instructor(granted_by=None)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.TEACHING_GRANTED, target_id=target.pk).count() == 1

    def it_logs_the_page_grant_for_an_already_oriented_member(db):
        # The teaching half no-ops (already oriented), but the public page going live is audited.
        target = _target_member(instructor_oriented_at=timezone.now())
        target.grant_instructor(granted_by=None)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.TEACHING_GRANTED, target_id=target.pk).count() == 1

    def it_logs_the_page_removal_when_teaching_was_already_off(db):
        target = _target_member(instructor_slug="target-member")  # bio page, but never had teaching
        target.revoke_instructor(revoked_by=None)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.TEACHING_REVOKED, target_id=target.pk).count() == 1


def describe_admin_member_teaching_set():
    def it_denies_a_non_admin(client):
        user = User.objects.create_user(username="plain-poster", email="pp@x.com", password=PASSWORD)
        target = _target_member()
        client.login(username="plain-poster", password=PASSWORD)
        assert user.member.fog_role == Member.FogRole.MEMBER
        response = client.post(_teaching_url(target), {"action": "grant"})
        assert response.status_code == 403

    def it_grants_instructor_with_message_and_activity(client):
        admin = _login_admin(client)
        target = _target_member()
        response = client.post(_teaching_url(target), {"action": "grant"}, follow=True)
        target.refresh_from_db()
        assert target.is_instructor
        assert target.can_create_classes
        assert "Made Target Member an instructor" in response.content.decode()
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.TEACHING_GRANTED)
        assert row.actor == admin.user

    def it_revokes_instructor_with_message_and_activity(client):
        admin = _login_admin(client)
        target = _target_member(instructor_oriented_at=timezone.now(), instructor_slug="target-member")
        response = client.post(_teaching_url(target), {"action": "revoke"}, follow=True)
        target.refresh_from_db()
        assert not target.is_instructor
        assert not target.can_create_classes
        assert "Removed instructor access for Target Member." in response.content.decode()
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


def describe_member_edit_instructor_toggle():
    def it_renders_the_instructor_toggle_and_revoke_confirm(client):
        _login_admin(client)
        target = _target_member()
        content = client.get(reverse("hub_admin_member_edit", kwargs={"pk": target.pk})).content.decode()
        assert 'aria-label="Instructor permission"' in content
        assert "Remove instructor access?" in content  # the confirm modal names the action

    def it_hides_the_toggle_in_the_non_member_user_edit_mode(client):
        _login_admin(client)
        user = User.objects.create_user(username="no-member-user", email="nmu@x.com")
        Member.objects.filter(user=user).delete()
        EmailAddress.objects.filter(user=user).delete()
        content = client.get(reverse("hub_admin_user_edit", kwargs={"user_pk": user.pk})).content.decode()
        assert 'aria-label="Instructor permission"' not in content
