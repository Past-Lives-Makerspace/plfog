"""BDD specs for member orientation booking, the guild-page section, and lead respond views."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Member, OrientationBooking
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def describe_orientation_book():
    def it_lets_a_member_request_an_orientation(client: Client):
        user = _user_with_role("bk1")
        slot = OrientationSlotFactory()
        client.login(username="bk1", password="pass")
        response = client.post(reverse("hub_orientation_book", args=[slot.pk]))
        assert response.status_code == 302
        assert OrientationBooking.objects.filter(
            member=user.member, slot=slot, status=OrientationBooking.Status.REQUESTED
        ).exists()

    def it_errors_when_the_slot_cannot_be_booked(client: Client):
        user = _user_with_role("bk2")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=user.member).mark_completed()
        slot = OrientationSlotFactory(guild=guild, enabled_settings=False)
        client.login(username="bk2", password="pass")
        response = client.post(reverse("hub_orientation_book", args=[slot.pk]), follow=True)
        assert response.status_code == 200
        # Only the pre-existing completed booking exists — no new request was created.
        assert OrientationBooking.objects.filter(member=user.member).count() == 1
        assert any("already completed this orientation" in str(m).lower() for m in response.context["messages"])

    def it_errors_when_the_user_has_no_member(client: Client):
        user = _user_with_role("bk3")
        slot = OrientationSlotFactory()
        client.force_login(user)
        Member.objects.filter(pk=user.member.pk).delete()
        response = client.post(reverse("hub_orientation_book", args=[slot.pk]))
        assert response.status_code == 302
        assert OrientationBooking.objects.filter(slot=slot).count() == 0

    def it_rejects_get_requests(client: Client):
        _user_with_role("bk4")
        slot = OrientationSlotFactory()
        client.login(username="bk4", password="pass")
        assert client.get(reverse("hub_orientation_book", args=[slot.pk])).status_code == 405

    def it_requires_login(client: Client):
        slot = OrientationSlotFactory()
        response = client.post(reverse("hub_orientation_book", args=[slot.pk]))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]


def describe_orientation_info():
    def it_renders_the_info_page(client: Client):
        _user_with_role("info1")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True, info="Bring closed-toe shoes")
        client.login(username="info1", password="pass")
        response = client.get(reverse("hub_orientation_info", args=[guild.pk]))
        assert response.status_code == 200
        assert b"Bring closed-toe shoes" in response.content

    def it_renders_when_no_settings_exist(client: Client):
        _user_with_role("info2")
        guild = GuildFactory()
        client.login(username="info2", password="pass")
        response = client.get(reverse("hub_orientation_info", args=[guild.pk]))
        assert response.status_code == 200
        assert b"No orientation details" in response.content


def describe_guild_orientation_section():
    def _setup(client: Client, username: str) -> tuple[User, object]:
        user = _user_with_role(username)
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        client.login(username=username, password="pass")
        return user, guild

    def it_shows_a_booking_prompt_when_slots_are_open(client: Client):
        _user, guild = _setup(client, "sec1")
        OrientationSlotFactory(guild=guild, enabled_settings=False)
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"Get oriented for" in response.content

    def it_gates_booking_behind_a_confirmation_and_paginates(client: Client):
        _user, guild = _setup(client, "sec_confirm")
        OrientationSlotFactory(guild=guild, enabled_settings=False)
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        # Request opens a confirm modal instead of posting straight to the book endpoint.
        assert b"open-confirm" in response.content
        assert b"Send request" in response.content
        # Slots are shown 5 at a time with arrows.
        assert b"size: 5" in response.content

    def it_shows_oriented_when_the_member_is_oriented(client: Client):
        user, guild = _setup(client, "sec2")
        OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=user.member).mark_completed()
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"completed this orientation" in response.content

    def it_shows_status_when_the_member_has_a_live_booking(client: Client):
        user, guild = _setup(client, "sec3")
        OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=user.member)
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"awaiting confirmation" in response.content

    def it_hides_the_section_when_orientation_is_disabled(client: Client):
        _user_with_role("sec4")
        guild = GuildFactory()
        client.login(username="sec4", password="pass")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b'aria-label="Orientation"' not in response.content

    def it_shows_the_section_to_a_logged_in_non_member(client: Client):
        # Being a linked member is no longer required to see the orientation section.
        user = User.objects.create_user(username="sec_unlinked", password="pass")
        Member.objects.filter(user=user).delete()
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        OrientationSlotFactory(guild=guild, enabled_settings=False)
        client.login(username="sec_unlinked", password="pass")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"Get oriented for" in response.content

    def it_shows_a_join_an_orientation_button_when_not_oriented(client: Client):
        _user, guild = _setup(client, "join1")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"Join an Orientation" in response.content

    def it_hides_join_an_orientation_once_oriented(client: Client):
        user, guild = _setup(client, "join2")
        OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=user.member).mark_completed()
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"Join an Orientation" not in response.content


def describe_orientation_request_custom():
    def it_creates_a_requested_booking_at_the_chosen_time(client: Client):
        user = _user_with_role("cust1")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True, allow_custom_requests=True)
        orientation_type = OrientationTypeFactory(guild=guild, duration_minutes=45)
        client.login(username="cust1", password="pass")
        when = (timezone.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")

        resp = client.post(
            reverse("hub_guild_orientation_request_custom", args=[guild.pk]),
            {"orientation_type": orientation_type.pk, "starts_at": when, "note": "evenings work best"},
        )

        assert resp.status_code == 302
        booking = OrientationBooking.objects.get(guild=guild, member=user.member)
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert booking.orientation_type == orientation_type
        assert booking.slot.source == "manual"
        assert booking.slot.ends_at - booking.slot.starts_at == timedelta(minutes=45)

    def it_refuses_when_custom_requests_are_disabled(client: Client):
        _user_with_role("cust2")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True, allow_custom_requests=False)
        client.login(username="cust2", password="pass")
        when = (timezone.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")

        resp = client.post(reverse("hub_guild_orientation_request_custom", args=[guild.pk]), {"starts_at": when})

        assert resp.status_code == 302
        assert not OrientationBooking.objects.filter(guild=guild).exists()

    def it_rejects_a_time_in_the_past(client: Client):
        _user_with_role("cust3")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True, allow_custom_requests=True)
        client.login(username="cust3", password="pass")
        past = (timezone.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")

        resp = client.post(reverse("hub_guild_orientation_request_custom", args=[guild.pk]), {"starts_at": past})

        assert resp.status_code == 302
        assert not OrientationBooking.objects.filter(guild=guild).exists()

    def it_refuses_when_the_guild_has_no_orientation_settings(client: Client):
        _user_with_role("cust4")
        guild = GuildFactory()
        client.login(username="cust4", password="pass")
        when = (timezone.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")

        resp = client.post(reverse("hub_guild_orientation_request_custom", args=[guild.pk]), {"starts_at": when})

        assert resp.status_code == 302
        assert not OrientationBooking.objects.filter(guild=guild).exists()

    def it_requires_a_member_profile(client: Client):
        MembershipPlanFactory()
        user = User.objects.create_user(username="cust_unlinked", password="pass")
        Member.objects.filter(user=user).delete()
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True, allow_custom_requests=True)
        client.login(username="cust_unlinked", password="pass")
        when = (timezone.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")

        resp = client.post(reverse("hub_guild_orientation_request_custom", args=[guild.pk]), {"starts_at": when})

        assert resp.status_code == 302
        assert not OrientationBooking.objects.filter(guild=guild).exists()

    def it_does_not_orphan_a_slot_when_the_booking_is_rejected(client: Client):
        from membership.models import OrientationSlot

        user = _user_with_role("cust5")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True, allow_custom_requests=True)
        # An existing live booking blocks a second request (one active booking per guild+member).
        OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=user.member)
        client.login(username="cust5", password="pass")
        when = (timezone.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")

        resp = client.post(reverse("hub_guild_orientation_request_custom", args=[guild.pk]), {"starts_at": when})

        assert resp.status_code == 302
        assert OrientationBooking.objects.filter(guild=guild, member=user.member).count() == 1
        # The extra slot created for the rejected request was rolled back — only the
        # original booking's slot remains.
        assert OrientationSlot.objects.filter(guild=guild).count() == 1


def describe_orientation_respond():
    def it_renders_for_an_editor(client: Client):
        _user_with_role("resp_admin", fog_role=Member.FogRole.ADMIN)
        booking = OrientationBookingFactory()
        client.login(username="resp_admin", password="pass")
        response = client.get(reverse("hub_orientation_respond", args=[booking.pk]))
        assert response.status_code == 200
        assert booking.member.display_name.encode() in response.content

    def it_forbids_a_non_editor(client: Client):
        _user_with_role("resp_reg", fog_role=Member.FogRole.MEMBER)
        booking = OrientationBookingFactory()
        client.login(username="resp_reg", password="pass")
        assert client.get(reverse("hub_orientation_respond", args=[booking.pk])).status_code == 403

    def it_confirms_on_post(client: Client):
        _user_with_role("resp_conf", fog_role=Member.FogRole.ADMIN)
        booking = OrientationBookingFactory()
        client.login(username="resp_conf", password="pass")
        response = client.post(reverse("hub_orientation_respond", args=[booking.pk]), {"action": "confirm"})
        assert response.status_code == 302
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CONFIRMED

    def it_credits_the_confirming_staffer_as_the_runner(client: Client):
        # Decision 7: the staffer who confirms is recorded as oriented_by, not the
        # guild lead. Here the confirmer is an admin distinct from the guild lead.
        lead_user = _user_with_role("resp_lead", fog_role=Member.FogRole.MEMBER)
        guild = GuildFactory(guild_lead=lead_user.member)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        confirmer = _user_with_role("resp_runner", fog_role=Member.FogRole.ADMIN)
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))
        client.login(username="resp_runner", password="pass")

        client.post(reverse("hub_orientation_respond", args=[booking.pk]), {"action": "confirm"})

        booking.refresh_from_db()
        assert booking.oriented_by_id == confirmer.member.pk
        assert booking.oriented_by_id != lead_user.member.pk

    def it_declines_on_post(client: Client):
        _user_with_role("resp_dec", fog_role=Member.FogRole.ADMIN)
        booking = OrientationBookingFactory()
        client.login(username="resp_dec", password="pass")
        response = client.post(
            reverse("hub_orientation_respond", args=[booking.pk]), {"action": "decline", "note": "later"}
        )
        assert response.status_code == 302
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.DECLINED
        assert booking.lead_note == "later"

    def it_ignores_an_unknown_action(client: Client):
        _user_with_role("resp_bogus", fog_role=Member.FogRole.ADMIN)
        booking = OrientationBookingFactory()
        client.login(username="resp_bogus", password="pass")
        response = client.post(reverse("hub_orientation_respond", args=[booking.pk]), {"action": "frobnicate"})
        assert response.status_code == 302
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.REQUESTED


def describe_orientation_lead_cancel():
    def it_cancels_for_an_editor(client: Client):
        _user_with_role("lc_admin", fog_role=Member.FogRole.ADMIN)
        booking = OrientationBookingFactory()
        booking.confirm()
        client.login(username="lc_admin", password="pass")
        response = client.post(reverse("hub_orientation_lead_cancel", args=[booking.pk]))
        assert response.status_code == 302
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CANCELLED

    def it_forbids_a_non_editor(client: Client):
        _user_with_role("lc_reg", fog_role=Member.FogRole.MEMBER)
        booking = OrientationBookingFactory()
        client.login(username="lc_reg", password="pass")
        assert client.post(reverse("hub_orientation_lead_cancel", args=[booking.pk])).status_code == 403

    def it_rejects_get_requests(client: Client):
        _user_with_role("lc_get", fog_role=Member.FogRole.ADMIN)
        booking = OrientationBookingFactory()
        client.login(username="lc_get", password="pass")
        assert client.get(reverse("hub_orientation_lead_cancel", args=[booking.pk])).status_code == 405


def describe_orientation_cancel_mine():
    def it_lets_the_member_cancel_their_own(client: Client):
        user = _user_with_role("cm1")
        booking = OrientationBookingFactory(member=user.member)
        client.login(username="cm1", password="pass")
        response = client.post(reverse("hub_orientation_cancel_mine", args=[booking.pk]))
        assert response.status_code == 302
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CANCELLED

    def it_forbids_cancelling_someone_elses_booking(client: Client):
        _user_with_role("cm2")
        booking = OrientationBookingFactory(member=MemberFactory())
        client.login(username="cm2", password="pass")
        response = client.post(reverse("hub_orientation_cancel_mine", args=[booking.pk]))
        assert response.status_code == 403
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.REQUESTED

    def it_rejects_get_requests(client: Client):
        user = _user_with_role("cm3")
        booking = OrientationBookingFactory(member=user.member)
        client.login(username="cm3", password="pass")
        assert client.get(reverse("hub_orientation_cancel_mine", args=[booking.pk])).status_code == 405
