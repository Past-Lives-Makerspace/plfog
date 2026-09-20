"""BDD specs for the Show on Leadership Directory control on the admin member edit Details tab (#464).

The toggle and its role lines save with the Details form. A listing row is written only
when the toggle or a role line changed, so a member nobody listed never gains a row and
the page's Updated date stays still on an unrelated Details save.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import LeadershipListing, LeadershipRole, Member
from tests.membership.factories import LeadershipListingFactory, LeadershipRoleFactory

pytestmark = pytest.mark.django_db

PASSWORD = "pw12345!"


def _login(client: Client, username: str, role: str) -> Member:
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password=PASSWORD)
    member = Member.objects.get(user=user)
    member.fog_role = role
    member.save()
    client.login(username=username, password=PASSWORD)
    return member


def _target(username: str = "lead-target") -> Member:
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password=PASSWORD)
    member = Member.objects.get(user=user)
    member.full_legal_name = "Target Member"
    member.save()
    return member


def _edit_url(member: Member) -> str:
    return reverse("hub_admin_member_edit", kwargs={"pk": member.pk})


def _details_post(member: Member, roles: list[dict[str, str]] | None = None, **overrides: str) -> dict[str, str]:
    """The full Details payload: the member fields plus the role formset's rows and management form.

    Rows with an ``id`` (saved roles) must come first, as the formset's INITIAL_FORMS count expects.
    """
    roles = roles or []
    data = {
        "full_legal_name": member.full_legal_name,
        "preferred_name": "",
        "pronouns": "",
        "discord_handle": "",
        "about_me": "",
        "status": Member.Status.ACTIVE,
        "member_type": Member.MemberType.STANDARD,
        "role": Member.FogRole.MEMBER,
        "show_in_directory": "on",
        "roles-TOTAL_FORMS": str(len(roles)),
        "roles-INITIAL_FORMS": str(sum(1 for row in roles if "id" in row)),
        "roles-MIN_NUM_FORMS": "0",
        "roles-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(roles):
        for key, value in row.items():
            data[f"roles-{index}-{key}"] = value
    data.update(overrides)
    return data


def describe_admin_member_edit_leadership():
    def it_denies_a_plain_member(client):
        _login(client, "plain", Member.FogRole.MEMBER)
        assert client.get(_edit_url(_target())).status_code == 403

    def it_renders_the_toggle_off_with_no_role_rows_for_an_unlisted_member(client):
        _login(client, "admin1", Member.FogRole.ADMIN)
        content = client.get(_edit_url(_target())).content.decode()
        assert 'name="leadership-is_listed"' in content
        assert 'x-model="listed"' in content
        assert 'x-data="{ listed: false }"' in content
        assert 'name="roles-TOTAL_FORMS" value="0"' in content
        assert 'id="leadership-role-empty-template"' in content
        assert LeadershipListing.objects.count() == 0  # a GET never writes a listing row

    def it_renders_the_saved_roles_for_a_listed_member(client):
        _login(client, "admin2", Member.FogRole.ADMIN)
        target = _target()
        listing = LeadershipListingFactory(member=target, is_listed=True)
        LeadershipRoleFactory(listing=listing, title="Founder", email="founder@x.com")
        content = client.get(_edit_url(target)).content.decode()
        assert 'x-data="{ listed: true }"' in content
        assert 'value="Founder"' in content
        assert 'name="roles-0-DELETE"' in content

    def it_creates_the_listing_and_its_roles_when_turned_on(client):
        _login(client, "admin3", Member.FogRole.ADMIN)
        target = _target()
        roles = [{"title": "Founder", "email": "founder@x.com", "sort_order": "0"}]
        response = client.post(_edit_url(target), _details_post(target, roles, **{"leadership-is_listed": "on"}))
        assert response.status_code == 302
        listing = LeadershipListing.objects.get(member=target)
        assert listing.is_listed is True
        assert list(listing.roles.values_list("title", "email")) == [("Founder", "founder@x.com")]

    def it_leaves_an_untouched_member_without_a_listing_row(client):
        _login(client, "admin4", Member.FogRole.ADMIN)
        target = _target()
        response = client.post(_edit_url(target), _details_post(target, full_legal_name="Renamed"))
        assert response.status_code == 302
        target.refresh_from_db()
        assert target.full_legal_name == "Renamed"
        assert not LeadershipListing.objects.filter(member=target).exists()

    def it_keeps_the_roles_when_turned_off(client):
        _login(client, "admin5", Member.FogRole.ADMIN)
        target = _target()
        listing = LeadershipListingFactory(member=target, is_listed=True)
        role = LeadershipRoleFactory(listing=listing, title="Founder")
        roles = [{"id": str(role.pk), "title": "Founder", "email": "", "sort_order": "0"}]
        response = client.post(_edit_url(target), _details_post(target, roles))  # no toggle key: off
        assert response.status_code == 302
        listing.refresh_from_db()
        assert listing.is_listed is False
        assert list(listing.roles.values_list("title", flat=True)) == ["Founder"]

    def it_deletes_a_role_and_adds_another_in_one_save(client):
        _login(client, "admin6", Member.FogRole.ADMIN)
        target = _target()
        listing = LeadershipListingFactory(member=target, is_listed=True)
        gone = LeadershipRoleFactory(listing=listing, title="Old Title")
        roles = [
            {"id": str(gone.pk), "title": "Old Title", "email": "", "sort_order": "0", "DELETE": "on"},
            {"title": "New Title", "email": "new@x.com", "sort_order": "1"},
        ]
        response = client.post(_edit_url(target), _details_post(target, roles, **{"leadership-is_listed": "on"}))
        assert response.status_code == 302
        assert list(listing.roles.values_list("title", "sort_order")) == [("New Title", 1)]
        assert not LeadershipRole.objects.filter(pk=gone.pk).exists()

    def it_ignores_an_abandoned_blank_role_row(client):
        _login(client, "admin7", Member.FogRole.ADMIN)
        target = _target()
        listing = LeadershipListingFactory(member=target, is_listed=True)
        roles = [{"title": "", "email": "", "sort_order": "3"}]  # the add button stamped sort_order
        response = client.post(_edit_url(target), _details_post(target, roles, **{"leadership-is_listed": "on"}))
        assert response.status_code == 302
        assert listing.roles.count() == 0

    def it_re_renders_with_the_error_when_a_role_has_no_title(client):
        _login(client, "admin8", Member.FogRole.ADMIN)
        target = _target()
        roles = [{"title": "", "email": "x@x.com", "sort_order": "0"}]
        response = client.post(_edit_url(target), _details_post(target, roles, **{"leadership-is_listed": "on"}))
        assert response.status_code == 200
        assert 'class="pl-field-error"' in response.content.decode()
        assert not LeadershipListing.objects.filter(member=target).exists()

    def it_does_not_move_the_listing_stamp_on_an_unrelated_details_save(client):
        _login(client, "admin9", Member.FogRole.ADMIN)
        target = _target()
        listing = LeadershipListingFactory(member=target, is_listed=True)
        stamp = timezone.now() - timedelta(days=2)
        LeadershipListing.objects.filter(pk=listing.pk).update(updated_at=stamp)
        payload = _details_post(target, full_legal_name="Renamed", **{"leadership-is_listed": "on"})
        response = client.post(_edit_url(target), payload)
        assert response.status_code == 302
        listing.refresh_from_db()
        assert listing.updated_at == stamp
