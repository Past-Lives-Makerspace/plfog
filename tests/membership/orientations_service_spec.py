"""BDD specs for the orientation orchestration service (emails, iCal, activity, notifications)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core import mail, signing
from django.utils import timezone

from core.models import Notification, SiteActivity
from membership import orientations
from membership.models import GuildStaffMembership, OrientationBooking, OrientationSlot
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

    def it_also_emails_and_notifies_guild_staff():
        guild, lead = _enabled_guild_with_lead("svc_lead_staff")
        staff = _member_with_user("svc_staff")
        GuildStaffMembershipFactory(guild=guild, member=staff, role=GuildStaffMembership.Role.CO_LEAD)
        member = _member_with_user("svc_member_staff")
        slot = OrientationSlotFactory(guild=guild, enabled_settings=False)

        orientations.request_orientation(slot, member, note="hi")

        recipients = {addr for m in mail.outbox for addr in m.to}
        assert {member.primary_email, lead.primary_email, staff.primary_email} <= recipients

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


def describe_action_tokens():
    def it_round_trips_a_token():
        booking = OrientationBookingFactory()
        token = orientations.make_action_token(booking, "confirm")
        decoded_booking, action = orientations.read_action_token(token)
        assert decoded_booking.pk == booking.pk
        assert action == "confirm"

    def it_rejects_a_tampered_token():
        with pytest.raises(signing.BadSignature):
            orientations.read_action_token("not-a-real-token")

    def it_rejects_an_unknown_action():
        booking = OrientationBookingFactory()
        token = signing.dumps({"booking": booking.pk, "action": "destroy"}, salt="orientation-action")
        with pytest.raises(signing.BadSignature):
            orientations.read_action_token(token)


def describe_apply_token_action():
    def it_confirms_a_requested_booking():
        booking = OrientationBookingFactory()
        assert orientations.apply_token_action(booking, "confirm") == "confirmed"
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CONFIRMED

    def it_declines_a_requested_booking():
        booking = OrientationBookingFactory()
        assert orientations.apply_token_action(booking, "decline") == "declined"
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.DECLINED

    def it_cancels_a_confirmed_booking():
        booking = OrientationBookingFactory()
        booking.confirm()
        assert orientations.apply_token_action(booking, "cancel") == "cancelled"
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CANCELLED

    def it_reports_already_for_a_resolved_request():
        booking = OrientationBookingFactory()
        booking.decline()
        assert orientations.apply_token_action(booking, "confirm") == "already"

    def it_reports_already_when_cancelling_a_resolved_booking():
        booking = OrientationBookingFactory()
        booking.cancel()
        assert orientations.apply_token_action(booking, "cancel") == "already"


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


def _past_confirmed_booking(guild: object) -> OrientationBooking:
    slot = OrientationSlotFactory(
        guild=guild,
        starts_at=timezone.now() - timedelta(hours=3),
        ends_at=timezone.now() - timedelta(hours=2),
    )
    booking = OrientationBookingFactory(slot=slot)
    booking.confirm()
    return booking


def describe_complete_orientation():
    def it_marks_complete_and_sends_the_thankyou_email():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(
            guild=guild,
            is_enabled=True,
            thankyou_email_enabled=True,
            thankyou_email_subject="Thanks!",
            thankyou_email_body="Here are your next steps.",
        )
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))

        orientations.complete_orientation(booking)

        booking.refresh_from_db()
        assert booking.is_completed is True
        assert mail.outbox[0].subject == "Thanks!"
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_COMPLETED).exists()

    def it_skips_the_email_when_not_configured():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))

        orientations.complete_orientation(booking)

        booking.refresh_from_db()
        assert booking.is_completed is True
        assert mail.outbox == []


def describe_auto_complete():
    def it_completes_only_past_confirmed_bookings():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        past_confirmed = _past_confirmed_booking(guild)
        future_confirmed = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))
        future_confirmed.confirm()
        past_requested = OrientationBookingFactory(
            slot=OrientationSlotFactory(
                guild=guild,
                starts_at=timezone.now() - timedelta(hours=3),
                ends_at=timezone.now() - timedelta(hours=2),
            )
        )

        completed = orientations.auto_complete()

        assert completed == 1
        past_confirmed.refresh_from_db()
        future_confirmed.refresh_from_db()
        past_requested.refresh_from_db()
        assert past_confirmed.is_completed is True
        assert future_confirmed.is_completed is False
        assert past_requested.is_completed is False


def describe_member_joined_guild():
    def it_emails_the_member_and_notifies_the_lead():
        lead = _member_with_user("join_lead")
        guild = GuildFactory(guild_lead=lead)
        GuildOrientationSettingsFactory(
            guild=guild,
            is_enabled=True,
            join_email_enabled=True,
            join_email_subject="Welcome!",
            join_email_body="Glad you joined.",
        )
        member = _member_with_user("join_member")

        orientations.member_joined_guild(guild, member)

        assert mail.outbox[0].to == [member.primary_email]
        assert mail.outbox[0].subject == "Welcome!"
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.GUILD_JOINED).exists()
        assert Notification.objects.filter(user=lead.user, trigger="guild_joined").exists()

    def it_skips_the_email_when_not_configured():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        member = _member_with_user("join_noemail")

        orientations.member_joined_guild(guild, member)

        assert mail.outbox == []
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.GUILD_JOINED).exists()


def describe_generate_slots():
    def it_creates_future_slots_from_an_active_rule():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        OrientationAvailabilityFactory(guild=guild)

        created = orientations.generate_slots()

        assert created >= 1
        assert OrientationSlot.objects.filter(guild=guild, source=OrientationSlot.Source.GENERATED).exists()

    def it_is_idempotent():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        OrientationAvailabilityFactory(guild=guild)

        first = orientations.generate_slots()
        second = orientations.generate_slots()

        assert first >= 1
        assert second == 0

    def it_skips_closed_guilds():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True, is_closed=True)
        OrientationAvailabilityFactory(guild=guild)

        assert orientations.generate_slots() == 0

    def it_skips_guilds_without_settings():
        guild = GuildFactory()
        OrientationAvailabilityFactory(guild=guild)

        assert orientations.generate_slots() == 0

    def it_can_target_a_single_guild():
        target = GuildFactory()
        other = GuildFactory()
        for g in (target, other):
            GuildOrientationSettingsFactory(guild=g, is_enabled=True)
            OrientationAvailabilityFactory(guild=g)

        created = orientations.generate_slots(guild=target)

        assert created >= 1
        assert OrientationSlot.objects.filter(guild=target).exists()
        assert not OrientationSlot.objects.filter(guild=other).exists()
