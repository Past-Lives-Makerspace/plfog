"""BDD specs for the orientation orchestration service (emails, iCal, activity, notifications)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail, signing
from django.urls import reverse
from django.utils import timezone

from core.models import EventDelivery, Notification, SiteActivity
from membership import orientations
from membership.models import GuildStaffMembership, OrientationBooking, OrientationError, OrientationSlot
from tests.membership.factories import (
    EquipmentReservationFactory,
    GuildFactory,
    GuildMembershipFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

_GUILD_HOOK = "https://discord.com/api/webhooks/900/orientation-guild"


def _guild_member(guild: object, username: str) -> object:
    """An active member with a linked User who has joined ``guild`` (a welcome recipient)."""
    member = _member_with_user(username)
    GuildMembershipFactory(guild=guild, member=member)
    return member


def _guild_url(guild: object) -> str:
    return orientations._absolute_url(reverse("hub_guild_detail", args=[guild.slug]))


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

    def it_fans_the_in_app_request_out_to_all_orienters_not_just_the_lead():
        # Decision 7: the in-app "needs a runner" ping reaches every orienter, fixing
        # the old lead-only asymmetry (email→all, in-app→lead only).
        guild, lead = _enabled_guild_with_lead("o7_lead")
        orienter = _member_with_user("o7_orienter")
        GuildStaffMembershipFactory(guild=guild, member=orienter, role=GuildStaffMembership.Role.ORIENTER)
        member = _member_with_user("o7_member")
        slot = OrientationSlotFactory(guild=guild, enabled_settings=False)

        orientations.request_orientation(slot, member)

        notified = set(Notification.objects.filter(trigger="orientation_requested").values_list("user_id", flat=True))
        assert lead.user_id in notified
        assert orienter.user_id in notified  # the orienter now gets the in-app ping too

    def it_credits_the_actual_runner_who_confirms_not_the_lead():
        # Decision 7: confirm passes the acting staffer as oriented_by; the booking is
        # credited to them, not defaulted to the guild lead.
        guild, _lead = _enabled_guild_with_lead("o7c_lead")
        orienter = _member_with_user("o7c_orienter")
        GuildStaffMembershipFactory(guild=guild, member=orienter, role=GuildStaffMembership.Role.ORIENTER)
        member = _member_with_user("o7c_member")
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=member)

        orientations.confirm_orientation(booking, oriented_by=orienter)

        booking.refresh_from_db()
        assert booking.oriented_by_id == orienter.pk

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
        decoded_booking, action, recipient = orientations.read_action_token(token)
        assert decoded_booking.pk == booking.pk
        assert action == "confirm"
        assert recipient is None  # payload without a recipient reads back as None

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
    def it_marks_complete_and_sends_the_guilds_custom_thankyou_email():
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

    def it_sends_the_standard_thankyou_when_enabled_with_no_custom_copy():
        # thankyou_email_enabled defaults True and the guild left subject/body blank,
        # so the standard copy from membership.orientation_copy stands in.
        from membership.orientation_copy import STANDARD_THANKYOU_BODY, standard_thankyou_subject

        guild = GuildFactory(name="Fallback Guild")
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))

        orientations.complete_orientation(booking)

        booking.refresh_from_db()
        assert booking.is_completed is True
        assert mail.outbox[0].subject == standard_thankyou_subject("Fallback Guild")
        assert STANDARD_THANKYOU_BODY in mail.outbox[0].body

    def it_sends_the_standard_thankyou_when_the_guild_has_no_orientation_settings_at_all():
        from membership.orientation_copy import STANDARD_THANKYOU_BODY, standard_thankyou_subject

        guild = GuildFactory(name="No Settings Guild")
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild, enabled_settings=False))

        orientations.complete_orientation(booking)

        booking.refresh_from_db()
        assert booking.is_completed is True
        assert mail.outbox[0].subject == standard_thankyou_subject("No Settings Guild")
        assert STANDARD_THANKYOU_BODY in mail.outbox[0].body

    def it_skips_the_email_when_the_guild_turned_it_off():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True, thankyou_email_enabled=False)
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))

        orientations.complete_orientation(booking)

        booking.refresh_from_db()
        assert booking.is_completed is True
        assert mail.outbox == []

    def it_posts_the_welcome_to_existing_guild_members_on_manual_complete():
        guild = GuildFactory(name="Metal Guild")
        first = _guild_member(guild, "welcome_first")
        second = _guild_member(guild, "welcome_second")
        newcomer = MemberFactory(full_legal_name="Robin Newcomer")
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=newcomer)

        orientations.complete_orientation(booking)

        for existing in (first, second):
            row = Notification.objects.get(user=existing.user, trigger="orientation.completed")
            assert "Robin Newcomer" in row.title
            assert "Metal Guild" in row.title
            assert "Robin Newcomer" in row.body
            assert "Metal Guild" in row.body

    def it_logs_exactly_one_completion_activity():
        # emit's activity_kind is None, so the only ORIENTATION_COMPLETED row is the one
        # complete_orientation logs directly — never a duplicate from the welcome emit.
        guild = GuildFactory(name="Glass Guild")
        _guild_member(guild, "activity_existing")
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))

        orientations.complete_orientation(booking)

        assert (
            SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_COMPLETED, target_id=booking.pk).count() == 1
        )

    def describe_idempotency():
        def it_does_not_double_post_when_completed_twice():
            guild = GuildFactory(name="Wood Guild", discord_webhook_url=_GUILD_HOOK, discord_post_enabled=True)
            existing = _guild_member(guild, "idem_existing")
            booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))

            with patch("core.events.discord.post_embed", return_value=True) as mock_post:
                orientations.complete_orientation(booking)
                orientations.complete_orientation(booking)

            # The period slot dedupes: exactly one in-app row per member, one guild post.
            assert Notification.objects.filter(user=existing.user, trigger="orientation.completed").count() == 1
            guild_calls = [call for call in mock_post.call_args_list if call.args[0] == _GUILD_HOOK]
            assert len(guild_calls) == 1

    def describe_discord_gating():
        def it_posts_to_the_guild_channel_when_discord_is_enabled():
            guild = GuildFactory(name="Fiber Guild", discord_webhook_url=_GUILD_HOOK, discord_post_enabled=True)
            booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))

            with patch("core.events.discord.post_embed", return_value=True) as mock_post:
                orientations.complete_orientation(booking)

            guild_calls = [call for call in mock_post.call_args_list if call.args[0] == _GUILD_HOOK]
            assert len(guild_calls) == 1
            assert _guild_url(guild) in guild_calls[0].args[1].body

        def it_silently_skips_discord_when_no_webhook():
            # Default guild has a blank webhook → the guild-own Discord post never fires, but
            # the in-app welcome still does.
            guild = GuildFactory(name="Clay Guild")
            existing = _guild_member(guild, "nodiscord_existing")
            booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))

            with patch("core.events.discord.post_embed", return_value=True):
                orientations.complete_orientation(booking)

            assert not EventDelivery.objects.filter(target_ref__startswith="broadcast:guild:").exists()
            assert Notification.objects.filter(user=existing.user, trigger="orientation.completed").exists()

    def describe_audience():
        def it_does_not_notify_the_newcomer_who_has_not_joined():
            # The completing member has no GuildMembership → they're the subject, not a recipient.
            guild = GuildFactory(name="Leather Guild")
            newcomer = _member_with_user("newcomer_subject")
            booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=newcomer)

            orientations.complete_orientation(booking)

            assert not Notification.objects.filter(user=newcomer.user, trigger="orientation.completed").exists()

        def it_notifies_a_directory_hidden_member():
            # guild_members ignores directory privacy — a hidden member still hears from their guild.
            guild = GuildFactory(name="Stone Guild")
            hidden = _member_with_user("hidden_member")
            hidden.show_in_directory = False
            hidden.save(update_fields=["show_in_directory"])
            GuildMembershipFactory(guild=guild, member=hidden)
            booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))

            orientations.complete_orientation(booking)

            assert Notification.objects.filter(user=hidden.user, trigger="orientation.completed").exists()

        def it_creates_no_rows_when_the_guild_has_no_other_members():
            guild = GuildFactory(name="Solo Guild")
            booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))

            orientations.complete_orientation(booking)

            assert not Notification.objects.filter(trigger="orientation.completed").exists()


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

    def it_posts_the_welcome_when_auto_complete_closes_the_booking():
        # The cron path loops complete_orientation, so the welcome ships on auto-completion too.
        guild = GuildFactory(name="Bronze Guild")
        existing = _guild_member(guild, "autocomplete_existing")
        _past_confirmed_booking(guild)

        completed = orientations.auto_complete()

        assert completed == 1
        assert Notification.objects.filter(user=existing.user, trigger="orientation.completed").exists()


def describe_member_joined_guild():
    def it_notifies_the_lead_and_logs_activity_without_emailing():
        lead = _member_with_user("join_lead")
        guild = GuildFactory(guild_lead=lead)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        member = _member_with_user("join_member")

        orientations.member_joined_guild(guild, member)

        # The welcome email was removed with its dead trigger; only the lead-only "New
        # follower" notice and the GUILD_JOINED activity remain — no email is sent.
        assert mail.outbox == []
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.GUILD_JOINED).exists()
        notice = Notification.objects.get(user=lead.user, trigger="guild_joined")
        assert notice.title == "New follower"
        assert notice.body == f"{member.display_name} now follows {guild.name}."


def describe_generate_slots():
    def it_creates_future_slots_from_an_active_rule():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        OrientationAvailabilityFactory(guild=guild)

        created = orientations.generate_slots()

        assert created >= 1
        assert OrientationSlot.objects.filter(guild=guild, source=OrientationSlot.Source.GENERATED).exists()

    def it_carries_the_rules_orientation_type_onto_every_generated_slot():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        lathe = OrientationTypeFactory(guild=guild, name="Lathe", default_location="Lathe Corner")
        rule = OrientationAvailabilityFactory(guild=guild, orientation_type=lathe, location="")

        assert orientations.generate_slots() >= 1
        generated = OrientationSlot.objects.filter(guild=guild, source=OrientationSlot.Source.GENERATED)
        assert generated.exists()
        assert all(slot.orientation_type == lathe for slot in generated)
        # A rule with no location falls back to the TYPE's default location.
        assert all(slot.location == "Lathe Corner" for slot in generated)
        assert rule.orientation_type == lathe

    def it_stops_generating_for_a_retired_type():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        retired = OrientationTypeFactory(guild=guild, name="Retired", is_active=False)
        OrientationAvailabilityFactory(guild=guild, orientation_type=retired)

        assert orientations.generate_slots() == 0

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


def describe_equipment_owned_orientations_service():
    """The full pipeline re-plumbed for an equipment owner — request through unlock."""

    def _equipment_slot(name: str = "CNC Router", **kwargs):
        from tests.membership.factories import EquipmentFactory

        equipment = EquipmentFactory(name=name)
        orientation_type = OrientationTypeFactory(
            equipment_owned=True, equipment=equipment, name="Operator Basics", **kwargs
        )
        return OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)

    def it_runs_the_full_free_unlock_loop():
        from membership.models import EquipmentStaffMembership

        slot = _equipment_slot()
        equipment = slot.orientation_type.equipment
        equipment.required_orientation = slot.orientation_type
        equipment.save(update_fields=["required_orientation"])
        manager = _member_with_user("eq_mgr_loop")
        EquipmentStaffMembership.objects.create(equipment=equipment, member=manager)
        member = _member_with_user("eq_loop")
        assert equipment.booking_blockers(member)  # gated before

        booking = orientations.request_orientation(slot, member)
        assert booking.guild is None
        orientations.confirm_orientation(booking, oriented_by=manager)
        orientations.complete_orientation(booking)
        assert member.is_oriented_for_type(slot.orientation_type) is True
        assert equipment.booking_blockers(member) == []  # the unlock

    def it_routes_a_personal_slot_to_its_manager_the_capability_holders_and_the_guild_lead():
        from membership.models import AdminCapability, EquipmentStaffMembership
        from tests.membership.factories import EquipmentFactory

        lead = _member_with_user("eq_p_lead")
        equipment = EquipmentFactory(name="CNC Router", guild=GuildFactory(guild_lead=lead))
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics")
        dana = _member_with_user("eq_p_dana")
        EquipmentStaffMembership.objects.create(equipment=equipment, member=dana)
        other_manager = _member_with_user("eq_p_other")
        EquipmentStaffMembership.objects.create(equipment=equipment, member=other_manager)
        holder = _member_with_user("eq_p_holder")
        holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        slot = OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type, orienter=dana)
        requester = _member_with_user("eq_p_member")
        mail.outbox.clear()
        with patch.object(orientations, "_action_url", wraps=orientations._action_url) as spy:
            orientations.request_orientation(slot, requester)
        # One message per recipient: union the whole request fan-out.
        addressed = {addr for m in mail.outbox if "New orientation request" in m.subject for addr in m.to}
        assert addressed == {dana.primary_email, holder.primary_email, lead.primary_email}
        assert other_manager.primary_email not in addressed
        # The confirm and decline links credit the manager the member booked.
        confirm_call = next(call for call in spy.call_args_list if call.args[1] == "confirm")
        assert confirm_call.kwargs["recipient"] == dana
        assert Notification.objects.filter(user=dana.user, trigger="orientation_requested").exists()
        assert not Notification.objects.filter(user=other_manager.user, trigger="orientation_requested").exists()

    def it_dedupes_a_manager_who_is_also_a_holder_and_lead_and_copes_with_a_standalone_tool():
        from membership.models import AdminCapability, EquipmentStaffMembership
        from tests.membership.factories import EquipmentFactory

        dana = _member_with_user("eq_d_dana")
        dana.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        owned = EquipmentFactory(guild=GuildFactory(guild_lead=dana))
        EquipmentStaffMembership.objects.create(equipment=owned, member=dana)
        assert orientations.equipment_personal_audience(owned, dana) == [dana]
        standalone = EquipmentFactory(guild=None)
        EquipmentStaffMembership.objects.create(equipment=standalone, member=dana)
        assert orientations.equipment_personal_audience(standalone, dana) == [dana]

    def it_routes_the_request_to_the_equipment_managers_only():
        from membership.models import EquipmentStaffMembership

        slot = _equipment_slot()
        equipment = slot.orientation_type.equipment
        manager = _member_with_user("eq_aud_mgr")
        EquipmentStaffMembership.objects.create(equipment=equipment, member=manager)
        bystander_lead = _member_with_user("eq_aud_lead")
        GuildFactory(guild_lead=bystander_lead)
        member = _member_with_user("eq_aud_member")
        mail.outbox.clear()
        orientations.request_orientation(slot, member)
        lead_request = next(m for m in mail.outbox if "New orientation request" in m.subject)
        assert lead_request.to == [manager.primary_email]
        assert equipment.name in lead_request.subject
        # The in-app row lands for the manager via the composed resolver.
        assert Notification.objects.filter(user=manager.user, trigger="orientation_requested").exists()
        assert not Notification.objects.filter(user=bystander_lead.user, trigger="orientation_requested").exists()

    def it_builds_the_ics_and_emails_around_the_equipment_name():
        slot = _equipment_slot(name="Big Laser")
        member = _member_with_user("eq_ics")
        mail.outbox.clear()
        booking = orientations.request_orientation(slot, member)
        payload = orientations.build_ics(booking, method="REQUEST", status="TENTATIVE").decode()
        assert "Big Laser" in payload
        member_email = next(m for m in mail.outbox if "request received" in m.subject)
        assert "Big Laser" in member_email.subject
        assert "[missing:" not in member_email.body
        assert "{{" not in member_email.body
        assert f"/equipment/{slot.orientation_type.equipment.slug}/" in member_email.body

    def it_cancels_an_equipment_slot_with_the_full_fan_out():
        slot = _equipment_slot()
        member = _member_with_user("eq_slotcancel")
        booking = orientations.request_orientation(slot, member)
        mail.outbox.clear()
        orientations.cancel_slot(slot, reason="Machine down.")
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CANCELLED
        cancelled_email = next(m for m in mail.outbox if "cancelled" in m.subject)
        assert slot.orientation_type.owner_name in cancelled_email.subject

    def it_sends_the_standard_thankyou_with_the_equipment_name():
        slot = _equipment_slot(name="Kiln Room")
        member = _member_with_user("eq_thanks")
        booking = orientations.request_orientation(slot, member)
        orientations.confirm_orientation(booking)
        mail.outbox.clear()
        orientations.complete_orientation(booking)
        thankyou = next(m for m in mail.outbox if "Kiln Room" in m.subject)
        assert "[missing:" not in thankyou.body


def describe_equipment_request_lock():
    """An equipment-owned request books under the Equipment row lock ``reserve()`` takes (PR 2)."""

    def it_refuses_a_request_under_a_confirmed_reservation():
        slot = OrientationSlotFactory(equipment_owned=True)
        EquipmentReservationFactory(
            equipment=slot.orientation_type.equipment, starts_at=slot.starts_at, ends_at=slot.ends_at
        )
        with pytest.raises(OrientationError, match="not available to book"):
            orientations.request_orientation(slot, _member_with_user("lock_refused"))
        assert not slot.bookings.exists()

    def it_books_under_the_lock_otherwise():
        slot = OrientationSlotFactory(equipment_owned=True)
        booking = orientations.request_orientation(slot, _member_with_user("lock_booked"))
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert slot.bookings.count() == 1
