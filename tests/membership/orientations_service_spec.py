"""BDD specs for the orientation orchestration service (emails, iCal, activity, notifications)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core import mail

from core.models import Notification, SiteActivity
from membership import orientations
from membership.models import OrientationBooking
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db


def _member_with_user(username: str) -> object:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, email=f"{username}@example.com").member


def _enabled_guild_with_lead(username: str = "svc_lead") -> tuple[object, object]:
    lead = _member_with_user(username)
    guild = GuildFactory(guild_lead=lead)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    return guild, lead


def _ics_bytes(message: object) -> bytes:
    _filename, content, _mime = message.attachments[0]
    return content if isinstance(content, bytes) else content.encode()


def describe_build_ics():
    def it_builds_a_tentative_request_vevent():
        booking = OrientationBookingFactory()
        ics = orientations.build_ics(booking, method="REQUEST", status="TENTATIVE")
        assert b"BEGIN:VEVENT" in ics
        assert b"STATUS:TENTATIVE" in ics
        assert b"METHOD:REQUEST" in ics
        assert booking.guild.name.encode() in ics

    def it_marks_a_cancellation():
        booking = OrientationBookingFactory()
        ics = orientations.build_ics(booking, method="CANCEL", status="CANCELLED")
        assert b"STATUS:CANCELLED" in ics
        assert b"METHOD:CANCEL" in ics

    def it_includes_the_location_when_set():
        slot = OrientationSlotFactory(location="Front desk")
        booking = OrientationBookingFactory(slot=slot)
        assert b"LOCATION" in orientations.build_ics(booking, method="REQUEST", status="CONFIRMED")


def describe_request_orientation():
    def it_creates_a_requested_booking_and_emails_member_and_lead():
        guild, lead = _enabled_guild_with_lead()
        member = _member_with_user("svc_member")
        slot = OrientationSlotFactory(guild=guild, enabled_settings=False)

        booking = orientations.request_orientation(slot, member, note="hi")

        assert booking.status == OrientationBooking.Status.REQUESTED
        recipients = {m.to[0] for m in mail.outbox}
        assert recipients == {member.primary_email, lead.primary_email}
        member_email = next(m for m in mail.outbox if m.to == [member.primary_email])
        assert b"BEGIN:VCALENDAR" in _ics_bytes(member_email)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_REQUESTED).exists()
        assert Notification.objects.filter(user=lead.user, trigger="orientation_requested").exists()

    def it_skips_the_lead_email_when_the_guild_has_no_lead():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        member = _member_with_user("svc_solo")
        slot = OrientationSlotFactory(guild=guild, enabled_settings=False)

        orientations.request_orientation(slot, member)

        assert [m.to for m in mail.outbox] == [[member.primary_email]]

    def it_emails_a_lead_without_a_user_but_skips_their_in_app_notification():
        lead = MemberFactory()  # no linked user → can't receive an in-app notification
        guild = GuildFactory(guild_lead=lead)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        member = _member_with_user("svc_nuser")
        slot = OrientationSlotFactory(guild=guild, enabled_settings=False)

        orientations.request_orientation(slot, member)

        assert lead.primary_email in {m.to[0] for m in mail.outbox}
        assert Notification.objects.filter(trigger="orientation_requested").count() == 0


def describe_confirm_orientation():
    def it_confirms_emails_the_member_and_logs_activity():
        guild, _lead = _enabled_guild_with_lead("conf_lead")
        member = _member_with_user("conf_member")
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=member)

        orientations.confirm_orientation(booking)

        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CONFIRMED
        assert mail.outbox[0].to == [member.primary_email]
        assert b"BEGIN:VCALENDAR" in _ics_bytes(mail.outbox[0])
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_CONFIRMED).exists()
        assert Notification.objects.filter(user=member.user, trigger="orientation_update").exists()

    def it_confirms_when_the_guild_has_no_lead():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        member = _member_with_user("conf_nolead")
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=member)

        orientations.confirm_orientation(booking)

        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CONFIRMED
        assert booking.oriented_by is None


def describe_decline_orientation():
    def it_declines_with_a_note_and_emails_the_member():
        member = _member_with_user("dec_member")
        booking = OrientationBookingFactory(member=member)

        orientations.decline_orientation(booking, note="try next month")

        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.DECLINED
        assert booking.lead_note == "try next month"
        assert mail.outbox[0].to == [member.primary_email]
        assert mail.outbox[0].attachments == []
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_DECLINED).exists()


def describe_cancel_orientation():
    def it_cancels_emails_the_member_and_notifies_the_lead():
        guild, lead = _enabled_guild_with_lead("can_lead")
        member = _member_with_user("can_member")
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=member)

        orientations.cancel_orientation(booking, actor_label="Sam")

        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CANCELLED
        assert mail.outbox[0].to == [member.primary_email]
        assert b"METHOD:CANCEL" in _ics_bytes(mail.outbox[0])
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_CANCELLED).exists()
        assert Notification.objects.filter(user=lead.user, trigger="orientation_requested").exists()
        assert Notification.objects.filter(user=member.user, trigger="orientation_update").exists()

    def it_cancels_when_the_guild_has_no_lead():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        member = _member_with_user("can_nolead")
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=member)

        orientations.cancel_orientation(booking, actor_label="Pat")

        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CANCELLED
        assert mail.outbox[0].to == [member.primary_email]


def describe_cancel_slot():
    def it_cancels_the_slot_and_every_active_booking():
        guild, _lead = _enabled_guild_with_lead("slot_lead")
        slot = OrientationSlotFactory(guild=guild, seats=3)
        first = OrientationBookingFactory(slot=slot, member=MemberFactory())
        second = OrientationBookingFactory(slot=slot, member=MemberFactory())

        orientations.cancel_slot(slot, reason="closed")

        slot.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        assert slot.is_cancelled is True
        assert first.status == OrientationBooking.Status.CANCELLED
        assert second.status == OrientationBooking.Status.CANCELLED
        assert len(mail.outbox) == 2
