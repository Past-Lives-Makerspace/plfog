"""BDD specs for the member-facing "with Bob" surfaces — the guild-page slot list,
booking status copy, the respond page, and the dashboard column/CSV/nudge."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import GuildStaffMembership, Member
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db


def _member_user(username: str, *, name: str = "", fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    if name:
        member.full_legal_name = name
    member.save()
    member.sync_user_permissions()
    return user


def _enabled_guild(lead_name: str = "Lead Person") -> object:
    guild = GuildFactory(guild_lead=MemberFactory(full_legal_name=lead_name))
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    return guild


def _orienter(guild: object, name: str) -> object:
    member = MemberFactory(full_legal_name=name)
    GuildStaffMembershipFactory(guild=guild, member=member, role=GuildStaffMembership.Role.ORIENTER)
    return member


def describe_guild_page_slot_list():
    def it_shows_with_bob_for_a_personal_slot_and_nothing_for_a_guild_slot(client: Client):
        _member_user("ms_list")
        guild = _enabled_guild()
        bob = _orienter(guild, "Bob Placeholder")
        OrientationSlotFactory(guild=guild, orienter=bob)
        OrientationSlotFactory(guild=guild)
        client.login(username="ms_list", password="pass")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert response.status_code == 200
        assert response.content.count(b"with Bob") == 1  # the guild slot stays silent

    def it_disambiguates_duplicate_first_names(client: Client):
        _member_user("ms_dupe")
        guild = _enabled_guild()
        bob_p = _orienter(guild, "Bob Placeholder")
        bob_q = _orienter(guild, "Bob Quartz")
        OrientationSlotFactory(guild=guild, orienter=bob_p)
        OrientationSlotFactory(guild=guild, orienter=bob_q)
        client.login(username="ms_dupe", password="pass")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"with Bob P." in response.content
        assert b"with Bob Q." in response.content

    def it_hides_a_departed_orienters_slot(client: Client):
        _member_user("ms_gone")
        guild = _enabled_guild()
        bob = _orienter(guild, "Bob Placeholder")
        OrientationSlotFactory(guild=guild, orienter=bob)
        GuildStaffMembership.objects.filter(guild=guild, member=bob).delete()
        client.login(username="ms_gone", password="pass")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"with Bob" not in response.content
        assert b"No one has posted orientation times yet" in response.content

    def it_stays_silent_for_a_nameless_orienter(client: Client):
        # Defensive: a staffer with no display name renders no bare "with".
        _member_user("ms_nameless")
        guild = _enabled_guild()
        nameless = MemberFactory(full_legal_name="", preferred_name="")
        GuildStaffMembershipFactory(guild=guild, member=nameless, role=GuildStaffMembership.Role.ORIENTER)
        OrientationSlotFactory(guild=guild, orienter=nameless)
        client.login(username="ms_nameless", password="pass")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert response.status_code == 200
        # The with-chip (avatar + name) only renders when there is a name to show.
        assert b"pl-orient-avatar" not in response.content

    def it_shows_the_new_empty_state_line(client: Client):
        _member_user("ms_empty")
        guild = _enabled_guild()
        client.login(username="ms_empty", password="pass")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"No one has posted orientation times yet" in response.content

    def it_addresses_the_confirm_modal_to_the_orienter(client: Client):
        _member_user("ms_modal")
        guild = _enabled_guild()
        bob = _orienter(guild, "Bob Placeholder")
        OrientationSlotFactory(guild=guild, orienter=bob)
        client.login(username="ms_modal", password="pass")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert "We&#x27;ll send your request to Bob to confirm.".encode() in response.content


def describe_booking_status_copy():
    def it_awaits_confirmation_from_the_orienter_by_first_name(client: Client):
        user = _member_user("ms_wait")
        guild = _enabled_guild()
        bob = _orienter(guild, "Bob Placeholder")
        slot = OrientationSlotFactory(guild=guild, orienter=bob)
        OrientationBookingFactory(slot=slot, member=user.member)
        client.login(username="ms_wait", password="pass")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"awaiting confirmation from Bob." in response.content
        assert b"with Bob" in response.content

    def it_awaits_confirmation_from_the_guild_for_a_guild_slot(client: Client):
        user = _member_user("ms_wait_g")
        guild = _enabled_guild()
        slot = OrientationSlotFactory(guild=guild)
        OrientationBookingFactory(slot=slot, member=user.member)
        client.login(username="ms_wait_g", password="pass")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"awaiting confirmation from the guild." in response.content


def describe_respond_page():
    def it_names_who_the_slot_runs_with(client: Client):
        _member_user("ms_respond", fog_role=Member.FogRole.ADMIN)
        guild = _enabled_guild()
        bob = _orienter(guild, "Bob Placeholder")
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild, orienter=bob))
        client.login(username="ms_respond", password="pass")
        response = client.get(reverse("hub_orientation_respond", args=[booking.pk]))
        assert b"Runs with: Bob Placeholder" in response.content

    def it_says_any_orienter_for_a_guild_slot(client: Client):
        _member_user("ms_respond_g", fog_role=Member.FogRole.ADMIN)
        guild = _enabled_guild()
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))
        client.login(username="ms_respond_g", password="pass")
        response = client.get(reverse("hub_orientation_respond", args=[booking.pk]))
        assert b"Runs with: Any orienter" in response.content


def describe_dashboard_orienter_column():
    def it_shows_the_orienter_in_the_table_and_the_upcoming_cards(client: Client):
        _member_user("ms_dash", fog_role=Member.FogRole.ADMIN)
        guild = _enabled_guild()
        bob = _orienter(guild, "Bob Placeholder")
        OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild, orienter=bob))
        client.login(username="ms_dash", password="pass")
        response = client.get(reverse("hub_orientations_dashboard"))
        assert b"<th>Orienter</th>" in response.content
        assert b"Bob Placeholder" in response.content
        assert b"with Bob" in response.content  # the Upcoming card's muted chip

    def it_exports_the_orienter_column_in_the_csv(client: Client):
        _member_user("ms_csv", fog_role=Member.FogRole.ADMIN)
        guild = _enabled_guild()
        bob = _orienter(guild, "Bob Placeholder")
        OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild, orienter=bob))
        OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))
        client.login(username="ms_csv", password="pass")
        response = client.get(reverse("hub_orientations_export"))
        body = b"".join(response.streaming_content).decode()
        header = body.splitlines()[0]
        assert header.split(",")[2] == "Orienter"
        assert "Bob Placeholder" in body


def describe_post_your_hours_nudge():
    def it_nudges_a_ruleless_lead_with_a_direct_link(client: Client):
        user = _member_user("ms_nudge", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="ms_nudge", password="pass")
        response = client.get(reverse("hub_orientations_dashboard"))
        assert b"You have not posted any orientation hours yet." in response.content
        assert b"Post your orientation hours" in response.content
        assert f"guilds/{guild.pk}/edit/?tab=orientations".encode() in response.content

    def it_lists_each_guild_when_they_staff_several(client: Client):
        user = _member_user("ms_nudge_multi", name="Lead Person")
        GuildFactory(guild_lead=user.member, name="Alpha Guild")
        guild_b = GuildFactory(name="Beta Guild")
        GuildStaffMembershipFactory(guild=guild_b, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        client.login(username="ms_nudge_multi", password="pass")
        response = client.get(reverse("hub_orientations_dashboard"))
        assert b"Alpha Guild" in response.content
        assert b"Beta Guild" in response.content

    def it_disappears_once_they_have_a_personal_rule(client: Client):
        user = _member_user("ms_nudge_done", name="Lead Person")
        guild = GuildFactory(guild_lead=user.member)
        OrientationAvailabilityFactory(guild=guild, orienter=user.member)
        client.login(username="ms_nudge_done", password="pass")
        response = client.get(reverse("hub_orientations_dashboard"))
        assert b"You have not posted any orientation hours yet." not in response.content

    def it_is_absent_for_an_admin_with_no_staffed_guild(client: Client):
        _member_user("ms_nudge_admin", fog_role=Member.FogRole.ADMIN)
        client.login(username="ms_nudge_admin", password="pass")
        response = client.get(reverse("hub_orientations_dashboard"))
        assert b"You have not posted any orientation hours yet." not in response.content
