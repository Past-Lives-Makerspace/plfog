"""BDD specs for the cubby waitlist's pages (#980): hub/cubby_waitlist_views.py and its templates.

The Cubbies tab's full card with Notify Me When One Opens and its joined card with the place and
Leave the List; the buttons' toasts and refusals; the staff Rentals tab's Waitlist chip with its
list, phone cards, Remove confirm and empty state; Remove as a 404 to another guild's staff and
open to admins, telling the member nothing; and the Cubbies tab switch coming on telling the line.
Assertions anchor on markup and factory strings (STANDARDS.md, section 8).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import Notification
from membership.models import CubbyRental, CubbyWaitlistEntry, Guild, Member
from tests.membership.factories import (
    CubbyFactory,
    CubbyRentalFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
    ShelfUnitFactory,
)

pytestmark = pytest.mark.django_db

Status = CubbyRental.Status


def _account(username: str, name: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    member = user.member
    member.fog_role = fog_role
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.save(update_fields=["fog_role", "status", "full_legal_name"])
    member.sync_user_permissions()
    return member


def _login(client: Client, username: str, name: str = "", **kwargs: str) -> Member:
    member = _account(username, name or f"{username.title()} Larkspurrow", **kwargs)
    client.login(username=username, password="pass")
    return member


def _guild(*, cubbies: int = 2, rented: int = 2, **kwargs: object) -> Guild:
    """A guild whose shelf has ``cubbies`` cubbies, the first ``rented`` of them rented."""
    guild = GuildFactory(name=kwargs.pop("name", "Brindlemoor Guild"), show_cubbies_tab=True, **kwargs)
    unit = ShelfUnitFactory(guild=guild, name="Clay Wall")
    for index in range(cubbies):
        cubby = CubbyFactory(unit=unit, label=f"W{index + 1}", row=1, column=index + 1)
        if index < rented:
            CubbyRentalFactory(cubby=cubby)
    return guild


def _wait(guild: Guild, *names: str) -> list[CubbyWaitlistEntry]:
    start = timezone.now() - timedelta(days=2)
    return [
        CubbyWaitlistEntry.objects.create(
            guild=guild, member=_account(name.lower(), f"{name} Larkspurrow"), joined_at=start + timedelta(hours=n)
        )
        for n, name in enumerate(names)
    ]


def _tab(client: Client, guild: Guild) -> str:
    response = client.get(f"{reverse('hub_guild_detail', args=[guild.slug])}?tab=cubbies")
    assert response.status_code == 200
    return response.content.decode()


def _messages(response: object) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]  # type: ignore[attr-defined]


# ── The member's side ────────────────────────────────────────────────────────


def describe_the_cubbies_tab():
    def it_offers_notify_me_only_while_every_cubby_is_taken(client: Client):
        _login(client, "full_viewer")
        full = _guild()
        content = _tab(client, full)
        assert "data-cubbies-full" in content and "data-waitlist-join" in content
        assert "All 2 cubbies are rented right now." in content
        assert reverse("hub_cubby_waitlist_join", args=[full.pk]) in content
        open_guild = _guild(rented=1, name="Openfield Guild")
        content = _tab(client, open_guild)
        assert "data-cubbies-full" not in content and "data-waitlist-join" not in content

    def it_says_taken_when_a_cubby_is_out_of_service_and_words_a_lone_cubby(client: Client):
        _login(client, "taken_viewer")
        guild = _guild(cubbies=2, rented=1)
        guild.shelf_units.get().cubbies.filter(label="W2").update(out_of_service=True)
        assert "All 2 cubbies are taken right now." in _tab(client, guild)
        lone = _guild(cubbies=1, rented=1, name="Lonestone Guild")
        assert "The only cubby is rented right now." in _tab(client, lone)

    def it_shows_the_place_and_leave_the_list_once_joined(client: Client):
        guild = _guild()
        _wait(guild, "Ahead")
        member = _login(client, "joined_viewer")
        CubbyWaitlistEntry.objects.create(guild=guild, member=member)
        content = _tab(client, guild)
        assert "data-cubbies-waiting" in content and "data-cubbies-full" not in content
        assert '<span class="pl-cubby-place" data-waitlist-place>number 2</span>' in content
        assert 'class="pl-btn pl-btn--danger pl-btn--sm"' in content and "data-waitlist-leave" in content
        assert reverse("hub_cubby_waitlist_leave", args=[guild.pk]) in content

    def it_keeps_the_joined_card_once_a_cubby_opens_so_they_can_leave(client: Client):
        guild = _guild(rented=1)
        member = _login(client, "still_waiting")
        CubbyWaitlistEntry.objects.create(guild=guild, member=member)
        content = _tab(client, guild)
        assert "data-cubbies-waiting" in content and "data-waitlist-join" not in content


def describe_joining_and_leaving():
    def it_joins_with_a_toast_and_a_second_tap_keeps_one_place(client: Client):
        guild = _guild()
        member = _login(client, "joiner")
        url = reverse("hub_cubby_waitlist_join", args=[guild.pk])
        response = client.post(url)
        assert response.status_code == 302
        assert response["Location"] == f"/guilds/{guild.slug}/?tab=cubbies"
        assert _messages(response) == ["You're on the list."]
        client.post(url)
        assert CubbyWaitlistEntry.objects.filter(member=member).count() == 1

    def it_refuses_while_a_cubby_is_open(client: Client):
        guild = _guild(rented=1)
        _login(client, "too_soon")
        response = client.post(reverse("hub_cubby_waitlist_join", args=[guild.pk]))
        assert _messages(response) == ["A cubby is open right now, so you can rent it instead."]
        assert not CubbyWaitlistEntry.objects.exists()

    def it_leaves_with_a_toast_and_says_so_when_already_off(client: Client):
        guild = _guild()
        member = _login(client, "leaver")
        CubbyWaitlistEntry.objects.create(guild=guild, member=member)
        url = reverse("hub_cubby_waitlist_leave", args=[guild.pk])
        assert _messages(client.post(url)) == ["You've left the list."]
        assert _messages(client.post(url))[-1] == "You weren't on the list any more."

    def it_takes_posts_only_and_a_signed_in_member(client: Client):
        guild = _guild()
        url = reverse("hub_cubby_waitlist_join", args=[guild.pk])
        assert client.post(url).status_code == 302  # to the login page
        assert not CubbyWaitlistEntry.objects.exists()
        _login(client, "getter")
        assert client.get(url).status_code == 405
        assert client.post(reverse("hub_cubby_waitlist_join", args=[987654])).status_code == 404

    def it_is_a_404_for_an_account_with_no_member(client: Client):
        guild = _guild()
        user = User.objects.create_user(username="memberless", password="pass")
        Member.objects.filter(user=user).delete()
        client.login(username="memberless", password="pass")
        assert client.post(reverse("hub_cubby_waitlist_join", args=[guild.pk])).status_code == 404
        assert client.post(reverse("hub_cubby_waitlist_leave", args=[guild.pk])).status_code == 404


# ── The staff side ───────────────────────────────────────────────────────────


def _rentals(client: Client, guild: Guild, show: str = "waitlist") -> str:
    response = client.get(f"{reverse('hub_guild_cubbies_rentals', args=[guild.pk])}?show={show}")
    assert response.status_code == 200
    return response.content.decode()


def describe_the_waitlist_chip():
    def it_lists_the_line_with_place_joined_date_and_remove(client: Client):
        lead = _login(client, "chip_lead")
        guild = _guild(guild_lead=lead)
        first, second = _wait(guild, "Primrose", "Quillan")
        content = _rentals(client, guild)
        assert (
            '<a href="?show=waitlist" class="pl-wp-chip pl-wp-chip--active" aria-current="true" data-rental-filter="waitlist">Waitlist (2)</a>'
            in content
        )
        assert "data-cubby-waitlist-table" in content and "data-cubby-rentals-table" not in content
        assert content.index(f'data-waitlist-row="{first.pk}"') < content.index(f'data-waitlist-row="{second.pk}"')
        assert f'data-waitlist-card="{second.pk}"' in content and "Number 2" in content
        assert "Remove Primrose Larkspurrow from the Waitlist?" in content
        assert "We won&#x27;t tell Quillan." in content
        assert first.remove_url in content

    def it_counts_the_line_on_every_chip_and_shows_the_rentals_otherwise(client: Client):
        lead = _login(client, "count_lead")
        guild = _guild(guild_lead=lead)
        _wait(guild, "Rowanne")
        content = _rentals(client, guild, show="current")
        assert 'data-rental-filter="waitlist">Waitlist (1)</a>' in content
        assert "data-cubby-rentals-table" in content and "data-cubby-waitlist-table" not in content

    def it_says_no_one_is_waiting(client: Client):
        lead = _login(client, "empty_lead")
        guild = _guild(guild_lead=lead)
        content = _rentals(client, guild)
        assert "No one is waiting." in content and "data-cubby-waitlist-empty" in content


def describe_remove():
    def it_takes_the_member_off_without_telling_them(client: Client):
        lead = _login(client, "remove_lead")
        guild = _guild(guild_lead=lead)
        (entry,) = _wait(guild, "Sorrel")
        response = client.post(entry.remove_url)
        assert response["Location"] == f"{reverse('hub_guild_cubbies_rentals', args=[guild.pk])}?show=waitlist"
        assert _messages(response) == ["Sorrel Larkspurrow is off the waitlist."]
        entry.refresh_from_db()
        assert entry.left_at is not None and entry.notified_at is None
        assert not Notification.objects.filter(user=entry.member.user).exists()
        assert _messages(client.post(entry.remove_url))[-1] == "Sorrel Larkspurrow wasn't waiting any more."

    def it_lets_guild_staff_remove(client: Client):
        guild = _guild()
        staff = _login(client, "remove_staff")
        GuildStaffMembershipFactory(guild=guild, member=staff)
        (entry,) = _wait(guild, "Tamsin")
        assert client.post(entry.remove_url).status_code == 302
        entry.refresh_from_db()
        assert entry.left_at is not None

    def it_is_a_404_for_another_guilds_staff_and_open_to_admins(client: Client):
        other_lead = _login(client, "other_lead")
        GuildFactory(guild_lead=other_lead)
        guild = _guild()
        (entry,) = _wait(guild, "Umber")
        assert client.post(entry.remove_url).status_code == 404
        assert client.get(f"{reverse('hub_guild_cubbies_rentals', args=[guild.pk])}?show=waitlist").status_code == 404
        entry.refresh_from_db()
        assert entry.is_waiting
        client.logout()
        _login(client, "waitlist_admin", fog_role=Member.FogRole.ADMIN)
        assert client.post(entry.remove_url).status_code == 302
        entry.refresh_from_db()
        assert not entry.is_waiting


def describe_the_cubbies_tab_switch():
    def it_tells_the_line_about_each_open_cubby_when_it_comes_on(client: Client, django_capture_on_commit_callbacks):
        lead = _login(client, "switch_lead")
        guild = _guild(cubbies=3, rented=1, guild_lead=lead)
        _wait(guild, "Vesper", "Wynn", "Yarrow")
        Guild.objects.filter(pk=guild.pk).update(show_cubbies_tab=False)
        url = reverse("hub_guild_cubbies_settings_save", args=[guild.pk])
        with django_capture_on_commit_callbacks(execute=True):
            client.post(url, {"show_cubbies_tab": "on"})
        told = Notification.objects.filter(title="A cubby just opened at Brindlemoor Guild")
        assert sorted(told.values_list("user__username", flat=True)) == ["vesper", "wynn"]
        with django_capture_on_commit_callbacks(execute=True):
            client.post(url, {"show_cubbies_tab": "on"})  # already on: no new opening
        assert told.count() == 2
