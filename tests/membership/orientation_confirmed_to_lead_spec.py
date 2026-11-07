"""Specs for #1127: orientation requests go to the Orientors; the guild lead hears of a confirmation.

The request routing itself is in ``tests/core/events/resolvers_spec.py`` and
``tests/membership/orientations_service_spec.py``. Here: the "Orientation confirmed" notice to the
guild lead (who gets it, when it is skipped, how it reads, its switches), the email links' primary
responder, the member message audience, and the lead keeping every power over the guild's bookings.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.events import settings_matrix
from core.events.registry import ORIENTATION_CONFIRMED_TO_LEAD, ORIENTATION_MESSAGE_TO_ORIENTORS
from core.models import Notification, NotificationPreference
from membership import orientations
from membership.models import GuildStaffMembership, Member, OrientationBooking
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _member(username: str, name: str = "") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    if name:
        member.preferred_name = name
        member.save(update_fields=["preferred_name"])
    return member


def _guild(prefix: str, *, orientors: int = 1) -> tuple[object, Member, list[Member]]:
    """A guild with a lead, a treasurer and ``orientors`` Orientors."""
    lead = _member(f"{prefix}_lead", "Lena Lead")
    guild = GuildFactory(name=f"Zzconf {prefix} Guild", guild_lead=lead)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    GuildStaffMembershipFactory(
        guild=guild, member=_member(f"{prefix}_treasurer"), role=GuildStaffMembership.Role.TREASURER
    )
    staff = []
    for index in range(orientors):
        orientor = _member(f"{prefix}_orientor{index}", f"Olive Orientor{index}")
        GuildStaffMembershipFactory(guild=guild, member=orientor, role=GuildStaffMembership.Role.ORIENTER)
        staff.append(orientor)
    return guild, lead, staff


def _booking(guild: object, prefix: str, *, orienter: Member | None = None, **kwargs: object) -> OrientationBooking:
    orientation_type = OrientationTypeFactory(guild=guild, name="Wheel Throwing")
    slot = OrientationSlotFactory(guild=guild, orientation_type=orientation_type, orienter=orienter)
    return OrientationBookingFactory(slot=slot, member=_member(f"{prefix}_member", "Maya Member"), **kwargs)


def _lead_notices(lead: Member) -> tuple[list[object], list[Notification]]:
    emails = [m for m in mail.outbox if m.to == [lead.primary_email] and m.subject.startswith("Orientation confirmed:")]
    bells = list(Notification.objects.filter(user=lead.user, trigger=ORIENTATION_CONFIRMED_TO_LEAD))
    return emails, bells


def _token(email: object, action: str) -> str:
    marker = "/orientation/act/"
    tokens = [part.split("/")[0] for part in email.body.split(marker)[1:]]
    return next(token for token in tokens if orientations.read_action_token(token)[1] == action)


def describe_the_lead_hears_of_a_confirmation():
    def it_sends_the_lead_one_email_and_one_bell_when_an_orientor_confirms():
        guild, lead, [orientor] = _guild("c1")
        booking = _booking(guild, "c1", orienter=orientor)
        mail.outbox.clear()

        orientations.confirm_orientation(booking, oriented_by=orientor)
        # A second notice for the same booking is deduped by its period.
        orientations._tell_lead_of_confirmation(booking, confirmer=orientor)

        emails, bells = _lead_notices(lead)
        assert len(emails) == 1
        assert len(bells) == 1
        when = orientations._when(booking.slot.starts_at)
        body = emails[0].body
        assert emails[0].subject == "Orientation confirmed: Maya Member, Wheel Throwing"
        for text in ("Maya Member", "Wheel Throwing", when, "Confirmed by: Olive Orientor0"):
            assert text in body
        assert f"/orientation/bookings/{booking.pk}/respond/" in body
        assert bells[0].title == "Orientation confirmed"
        assert bells[0].body == f"Olive Orientor0 confirmed Maya Member's Wheel Throwing orientation for {when}."
        assert bells[0].url == f"/orientation/bookings/{booking.pk}/respond/"

    def it_sends_the_lead_nothing_when_the_lead_confirms():
        guild, lead, _orientors = _guild("c2")
        booking = _booking(guild, "c2")
        mail.outbox.clear()

        orientations.confirm_orientation(booking, oriented_by=lead)

        assert _lead_notices(lead) == ([], [])
        # The member still hears.
        assert [m.to for m in mail.outbox] == [[booking.member.primary_email]]

    def it_tells_nobody_else_on_the_team():
        guild, lead, [orientor] = _guild("c3")
        booking = _booking(guild, "c3")

        orientations.confirm_orientation(booking, oriented_by=orientor)

        bells = Notification.objects.filter(trigger=ORIENTATION_CONFIRMED_TO_LEAD)
        assert set(bells.values_list("user_id", flat=True)) == {lead.user_id}

    def it_sends_nothing_for_a_guild_with_no_lead():
        guild = GuildFactory(guild_lead=None)
        orientor = _member("c4_orientor")
        GuildStaffMembershipFactory(guild=guild, member=orientor, role=GuildStaffMembership.Role.ORIENTER)
        booking = _booking(guild, "c4")

        orientations.confirm_orientation(booking, oriented_by=orientor)

        assert not Notification.objects.filter(trigger=ORIENTATION_CONFIRMED_TO_LEAD).exists()

    def it_sends_nothing_for_an_equipment_orientation():
        lead = _member("c5_lead")
        guild = GuildFactory(guild_lead=lead)
        equipment = EquipmentFactory(guild=guild)
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment)
        manager = _member("c5_manager")
        booking = OrientationBookingFactory(
            slot=OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type),
            member=_member("c5_member"),
        )

        orientations.confirm_orientation(booking, oriented_by=manager)

        assert not Notification.objects.filter(trigger=ORIENTATION_CONFIRMED_TO_LEAD).exists()

    def it_respects_the_leads_email_switch_and_keeps_the_bell():
        guild, lead, [orientor] = _guild("c6")
        NotificationPreference.objects.create(
            user=lead.user, event_key=ORIENTATION_CONFIRMED_TO_LEAD, channel="email", enabled=False
        )
        booking = _booking(guild, "c6")
        mail.outbox.clear()

        orientations.confirm_orientation(booking, oriented_by=orientor)

        emails, bells = _lead_notices(lead)
        assert (len(emails), len(bells)) == (0, 1)

    def it_rings_the_bell_even_with_the_email_off():
        # The bell is always on wherever an event declares it (core.events.preferences);
        # the Email switch is the one a lead can turn off.
        guild, lead, [orientor] = _guild("c7")
        NotificationPreference.objects.create(
            user=lead.user, event_key=ORIENTATION_CONFIRMED_TO_LEAD, channel="in_app", enabled=False
        )
        booking = _booking(guild, "c7")
        mail.outbox.clear()

        orientations.confirm_orientation(booking, oriented_by=orientor)

        emails, bells = _lead_notices(lead)
        assert (len(emails), len(bells)) == (1, 1)


def describe_email_link_confirmations():
    def it_names_the_personal_slot_orientor_who_clicked_and_credits_them():
        guild, lead, [orientor] = _guild("t1")
        booking = _booking(guild, "t1", orienter=orientor)
        orientations._emit_lead_request(booking)
        token = _token(next(m for m in mail.outbox if m.to == [orientor.primary_email]), "confirm")
        decoded, action, recipient = orientations.read_action_token(token)

        assert orientations.apply_token_action(decoded, action, recipient=recipient) == "confirmed"

        decoded.refresh_from_db()
        assert decoded.oriented_by == orientor
        emails, _bells = _lead_notices(lead)
        assert "Confirmed by: Olive Orientor0" in emails[0].body

    def it_leaves_a_shared_slot_link_unattributed_when_several_orientors_get_it():
        guild, lead, orientors = _guild("t2", orientors=2)
        booking = _booking(guild, "t2")
        orientations._emit_lead_request(booking)
        token = _token(next(m for m in mail.outbox if m.to == [orientors[0].primary_email]), "confirm")
        decoded, action, recipient = orientations.read_action_token(token)
        assert recipient is None

        orientations.apply_token_action(decoded, action, recipient=recipient)

        decoded.refresh_from_db()
        # Credit is unchanged from before #1127: the default runner, the guild lead.
        assert decoded.oriented_by == lead
        emails, bells = _lead_notices(lead)
        assert "Confirmed by: An Orientor" in emails[0].body
        assert len(bells) == 1

    def it_sends_the_lead_nothing_when_the_lead_alone_got_the_request_and_clicked():
        guild, lead, _none = _guild("t3", orientors=0)
        booking = _booking(guild, "t3")
        orientations._emit_lead_request(booking)
        token = _token(next(m for m in mail.outbox if m.to == [lead.primary_email]), "confirm")
        decoded, action, recipient = orientations.read_action_token(token)
        assert recipient == lead
        mail.outbox.clear()

        orientations.apply_token_action(decoded, action, recipient=recipient)

        assert _lead_notices(lead) == ([], [])


def describe_confirming_by_a_move():
    def _soon() -> object:
        local = timezone.localtime(timezone.now() + timedelta(days=5))
        return local.replace(hour=14, minute=0, second=0, microsecond=0)

    def it_tells_the_lead_when_an_orientor_moves_a_request_and_so_confirms_it():
        guild, lead, [orientor] = _guild("m1")
        booking = _booking(guild, "m1")

        orientations.move_orientation(booking, _soon(), actor=orientor)

        _emails, bells = _lead_notices(lead)
        assert len(bells) == 1
        assert bells[0].body.startswith("Olive Orientor0 confirmed Maya Member's")

    def it_says_nothing_when_a_confirmed_booking_moves():
        guild, lead, [orientor] = _guild("m2")
        booking = _booking(guild, "m2", status=OrientationBooking.Status.CONFIRMED, oriented_by=orientor)

        orientations.move_orientation(booking, _soon(), actor=orientor)

        assert _lead_notices(lead) == ([], [])

    def it_says_nothing_when_the_lead_moves_a_request():
        guild, lead, _orientors = _guild("m3")
        booking = _booking(guild, "m3")

        orientations.move_orientation(booking, _soon(), actor=lead)

        assert _lead_notices(lead) == ([], [])

    def it_tells_the_lead_when_the_member_accepts_an_orientors_proposed_time():
        guild, lead, [orientor] = _guild("m4")
        booking = _booking(guild, "m4")
        orientations.propose_orientation_time(booking, _soon(), actor=orientor)
        booking.refresh_from_db()

        assert orientations.answer_proposal(booking, accept=True, proposal=booking.proposal_key) == "accepted"

        _emails, bells = _lead_notices(lead)
        assert len(bells) == 1
        assert bells[0].body.startswith("Olive Orientor0 confirmed")

    def it_says_nothing_when_the_member_keeps_their_time():
        guild, lead, [orientor] = _guild("m5")
        booking = _booking(guild, "m5")
        orientations.propose_orientation_time(booking, _soon(), actor=orientor)
        booking.refresh_from_db()

        assert orientations.answer_proposal(booking, accept=False, proposal=booking.proposal_key) == "kept"

        assert _lead_notices(lead) == ([], [])


def describe_member_messages():
    def it_reaches_the_shared_slot_orientors_and_not_the_lead():
        guild, lead, orientors = _guild("msg1", orientors=2)
        booking = _booking(guild, "msg1")

        message = booking.post_message(author=booking.member.user, body="Zzcan we do 6?")

        assert message.orientor_user_ids() == {o.user_id for o in orientors}
        notified = set(
            Notification.objects.filter(trigger=ORIENTATION_MESSAGE_TO_ORIENTORS).values_list("user_id", flat=True)
        )
        assert notified == {o.user_id for o in orientors}
        assert lead.user_id not in notified

    def it_reaches_a_personal_slot_orientor_alone():
        guild, lead, orientors = _guild("msg2", orientors=2)
        booking = _booking(guild, "msg2", orienter=orientors[0])

        message = booking.post_message(author=booking.member.user, body="Zzhello")

        assert message.orientor_user_ids() == {orientors[0].user_id}

    def it_still_reaches_the_lead_after_the_lead_wrote_on_the_booking():
        guild, lead, [orientor] = _guild("msg3")
        booking = _booking(guild, "msg3")
        booking.post_message(author=lead.user, body="Zzsee you then")

        message = booking.post_message(author=booking.member.user, body="Zzthanks")

        assert message.orientor_user_ids() == {orientor.user_id, lead.user_id}


def describe_the_settings_row():
    def _keys(user: User) -> set[str]:
        return {
            row.event_key
            for section in settings_matrix.build_matrix(user)
            for block in section.blocks
            for row in block.rows
        }

    def it_shows_the_row_to_a_guild_lead():
        _guild_obj, lead, [orientor] = _guild("s1")
        assert ORIENTATION_CONFIRMED_TO_LEAD in _keys(lead.user)
        assert ORIENTATION_CONFIRMED_TO_LEAD not in _keys(orientor.user)

    def it_offers_the_lead_an_email_switch_that_starts_on():
        from core.events.registry import Channel

        _guild_obj, lead, _orientors = _guild("s3")
        [row] = [
            row
            for section in settings_matrix.build_matrix(lead.user)
            for block in section.blocks
            for row in block.rows
            if row.event_key == ORIENTATION_CONFIRMED_TO_LEAD
        ]
        cells = {cell.channel: cell for cell in row.cells}
        assert cells[Channel.EMAIL].is_editable
        assert cells[Channel.EMAIL].enabled
        assert cells[Channel.IN_APP].present
        assert cells[Channel.IN_APP].enabled

    def it_hides_the_row_from_a_plain_member():
        assert ORIENTATION_CONFIRMED_TO_LEAD not in _keys(_member("s2_plain").user)


def describe_the_lead_keeps_every_action():
    def it_lets_the_lead_open_confirm_and_decline_an_orientors_bookings(client: Client):
        guild, lead, [orientor] = _guild("p1")
        personal = _booking(guild, "p1", orienter=orientor)
        shared = OrientationBookingFactory(
            slot=OrientationSlotFactory(guild=guild, orientation_type=personal.orientation_type),
            member=_member("p1_other"),
        )
        client.login(username="p1_lead", password="pass")
        mail.outbox.clear()

        assert client.get(reverse("hub_orientation_respond", args=[personal.pk])).status_code == 200
        confirm = client.post(reverse("hub_orientation_respond", args=[personal.pk]), {"action": "confirm"})
        decline = client.post(reverse("hub_orientation_respond", args=[shared.pk]), {"action": "decline"})

        assert (confirm.status_code, decline.status_code) == (302, 302)
        personal.refresh_from_db()
        shared.refresh_from_db()
        assert personal.status == OrientationBooking.Status.CONFIRMED
        assert personal.oriented_by == lead
        assert shared.status == OrientationBooking.Status.DECLINED
        # The lead confirmed it, so the lead is not told of their own confirmation.
        assert _lead_notices(lead) == ([], [])

    def it_tells_the_lead_when_an_orientor_confirms_from_the_request_page(client: Client):
        guild, lead, [orientor] = _guild("p2")
        booking = _booking(guild, "p2")
        client.login(username="p2_orientor0", password="pass")

        client.post(reverse("hub_orientation_respond", args=[booking.pk]), {"action": "confirm"})

        emails, bells = _lead_notices(lead)
        assert (len(emails), len(bells)) == (1, 1)
        assert "Confirmed by: Olive Orientor0" in emails[0].body


def describe_the_request_email_is_always_on():
    def it_reaches_an_orientor_who_switched_it_off():
        guild, _lead, [orientor] = _guild("f1")
        NotificationPreference.objects.create(
            user=orientor.user, event_key="orientation_requested", channel="email", enabled=False
        )
        booking = _booking(guild, "f1")
        mail.outbox.clear()

        orientations._emit_lead_request(booking)

        assert [m.to for m in mail.outbox if m.subject.startswith("New orientation request")] == [
            [orientor.primary_email]
        ]

    def it_still_sends_no_email_for_a_cancel_ping():
        # The cancel bell rides the same event with the email suppressed; forcing it changes nothing there.
        guild, _lead, [orientor] = _guild("f2")
        booking = _booking(guild, "f2")
        mail.outbox.clear()

        orientations.cancel_orientation(booking, actor_label="Maya Member")

        assert [m for m in mail.outbox if m.to == [orientor.primary_email]] == []
        assert Notification.objects.filter(user=orientor.user, trigger="orientation_requested").exists()

    def it_shows_the_email_locked_on_in_settings_and_keeps_messages_switchable():
        from core.events.registry import Channel

        _guild_obj, _lead, [orientor] = _guild("f3")
        rows = {
            row.event_key: {cell.channel: cell for cell in row.cells}
            for section in settings_matrix.build_matrix(orientor.user)
            for block in section.blocks
            for row in block.rows
        }
        request = rows["orientation_requested"]
        assert request[Channel.EMAIL].forced
        assert request[Channel.EMAIL].enabled
        assert not request[Channel.EMAIL].is_editable
        assert request[Channel.PUSH].is_editable
        assert request[Channel.DISCORD_DM].present
        assert not request[Channel.DISCORD_DM].forced
        assert rows[ORIENTATION_MESSAGE_TO_ORIENTORS][Channel.EMAIL].is_editable
