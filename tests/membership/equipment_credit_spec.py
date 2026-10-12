"""BDD specs for hourly equipment credit (#787, part 1): the ledger, granting, spending and giving it back.

The append only ledger and its balance; a manager's grant and take back with every gate; Credit
Unused Time on a started booking; the spend under the equipment lock on both booking roads, with
the race spending it once; the released hold, cancel and decline giving it back while the paid
part refunds as before; and the member's email. Stripe is mocked the way the pricing specs mock
it. Names are factory strings no changelog could contain (STANDARDS.md §8).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.events.copy import COPY_CHANNELS, default_copy_for, placeholders_for
from core.events.registry import Channel, ChannelDefault, Recipients, get_event
from core.events.rendering import render_text
from core.models import Notification
from membership import equipment as equipment_service
from membership.models import (
    Equipment,
    EquipmentCredit,
    EquipmentError,
    EquipmentReservation,
    Member,
    credit_hours_short,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

HOLD = EquipmentReservation.Status.PENDING_PAYMENT
_SESSION = {"id": "cs_credit_1", "url": "https://checkout.stripe.example/cs_credit_1"}


def _day(offset: int = 2):
    return timezone.localdate() + timedelta(days=offset)


def _at(day, hour: int, minute: int = 0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _hourly(**kwargs) -> Equipment:
    """A $25 an hour item open 8 to 5 two days out, booked up to 8 hours at a time."""
    kwargs.setdefault("name", "Quarrelwood Router")
    equipment = EquipmentFactory(
        pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=2500, max_duration_minutes=480, **kwargs
    )
    EquipmentHoursFactory(equipment=equipment, weekday=_day().weekday(), start_time=time(8, 0), end_time=time(17, 0))
    return equipment


def _member(username: str = "thistlewick") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = f"{username.title()} Fernmallow"
    member.save(update_fields=["status", "full_legal_name"])
    return member


def _manager(equipment: Equipment, username: str = "sprocketta") -> Member:
    manager = _member(username)
    EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
    return manager


def _give(equipment: Equipment, member: Member, minutes: int, **kwargs) -> EquipmentCredit:
    return EquipmentCredit.objects.create(
        equipment=equipment, member=member, minutes=minutes, kind=EquipmentCredit.Kind.GRANTED, **kwargs
    )


def _balance(member: Member, equipment: Equipment) -> int:
    return EquipmentCredit.objects.balance_for(member, equipment)


def _started(equipment: Equipment, member: Member, *, hours: int = 8, **kwargs) -> EquipmentReservation:
    """A confirmed booking that began an hour ago and runs ``hours`` in all."""
    starts = timezone.now() - timedelta(hours=1)
    return EquipmentReservationFactory(
        equipment=equipment, member=member, starts_at=starts, ends_at=starts + timedelta(hours=hours), **kwargs
    )


def describe_the_ledger():
    def it_sums_a_members_rows_on_one_item_into_the_balance():
        router = _hourly()
        lathe = _hourly(name="Bramblefold Lathe")
        member = _member()
        assert _balance(member, router) == 0
        _give(router, member, 120)
        _give(router, member, 90)
        EquipmentCredit.objects.create(equipment=router, member=member, minutes=-60, kind=EquipmentCredit.Kind.SPENT)
        _give(lathe, member, 600)
        _give(router, _member("othermoss"), 300)
        assert _balance(member, router) == 150

    def it_refuses_a_zero_line():
        with pytest.raises(IntegrityError), transaction.atomic():
            _give(_hourly(), _member(), 0)

    def it_lists_who_has_credit_by_name_with_the_latest_grants_note_and_date():
        router = _hourly()
        zed = _member("zedwhistle")
        abel = _member("abelquorn")
        spent_out = _member("spentout")
        _give(router, zed, 60, note="First")
        _give(router, zed, 60, note="Cabinet project")
        EquipmentCredit.objects.create(equipment=router, member=zed, minutes=30, kind=EquipmentCredit.Kind.RESTORED)
        _give(router, abel, 30)
        _give(router, spent_out, 60)
        EquipmentCredit.objects.create(
            equipment=router, member=spent_out, minutes=-60, kind=EquipmentCredit.Kind.TAKEN_BACK
        )

        holders = EquipmentCredit.objects.holders(router)

        assert [(h.member, h.minutes, h.note, h.hours_label) for h in holders] == [
            (abel, 30, "", "0.5h"),
            (zed, 150, "Cabinet project", "2.5h"),
        ]
        assert holders[1].granted_at is not None

    def it_lists_holders_in_two_queries(django_assert_num_queries):
        router = _hourly()
        for index in range(3):
            _give(router, _member(f"holder{index}"), 60)
        with django_assert_num_queries(2):
            EquipmentCredit.objects.holders(router)

    def it_writes_short_hours_and_a_readable_line():
        assert credit_hours_short(120) == "2h"
        assert credit_hours_short(90) == "1.5h"
        assert credit_hours_short(30) == "0.5h"
        row = _give(_hourly(), _member(), 90)
        assert str(row) == "Quarrelwood Router: Thistlewick Fernmallow +90 min (Given by a manager)"

    def it_names_the_credit_a_booking_used():
        booking = EquipmentReservationFactory(equipment=_hourly(), credit_minutes=120)
        assert booking.credit_used_label == "2h credit used"
        assert EquipmentReservationFactory(equipment=_hourly(name="Plain Saw")).credit_used_label == ""


def describe_pricing_with_credit():
    def it_charges_the_rate_on_what_the_credit_does_not_cover():
        router = _hourly()
        assert router.credit_applied_minutes(480, 120) == 120
        assert router.checkout_amount_cents(480, None, credit_minutes=120) == 15000
        assert router.checkout_amount_cents(240, None, credit_minutes=300) == 0
        assert router.credit_applied_minutes(240, 300) == 240
        assert router.checkout_amount_cents(240, None) == 10000

    def it_applies_no_credit_on_a_free_or_donation_item():
        free = EquipmentFactory()
        donation = EquipmentFactory(pricing=Equipment.Pricing.DONATION)
        assert free.credit_applied_minutes(60, 120) == 0
        assert donation.credit_applied_minutes(60, 120) == 0
        assert donation.checkout_amount_cents(60, 1000, credit_minutes=120) == 1000


def describe_grant_credit():
    def it_gives_credit_and_emails_the_member_once():
        router = _hourly()
        manager = _manager(router)
        member = _member()
        mail.outbox.clear()

        credit = equipment_service.grant_credit(router, member, manager, 120, note="  Storage cabinet  ")

        assert credit.kind == EquipmentCredit.Kind.GRANTED
        assert credit.granted_by == manager
        assert credit.note == "Storage cabinet"
        assert credit.reservation is None
        assert _balance(member, router) == 120
        [email] = mail.outbox
        assert email.to == ["thistlewick@example.com"]
        assert email.subject == "You have 2 hours of credit on the Quarrelwood Router"
        assert (
            "Sprocketta Fernmallow credited you 2 hours on the Quarrelwood Router. "
            "It applies the next time you book there." in email.body
        )
        assert "Their note: Storage cabinet." in email.body
        assert f"/equipment/{router.slug}/" in email.body
        html = email.alternatives[0][0]
        assert "Book a Time</a>" in html
        bell = Notification.objects.get(user=member.user, trigger="equipment.credit_granted")
        assert bell.title == "2 hours of credit on the Quarrelwood Router"

    def it_leaves_the_note_line_out_when_there_is_no_note():
        router = _hourly()
        mail.outbox.clear()
        equipment_service.grant_credit(router, _member(), _manager(router), 30)
        assert "Their note" not in mail.outbox[0].body
        assert "credited you 30 minutes" in mail.outbox[0].body

    def it_lets_only_a_manager_of_an_hourly_item_give_credit():
        router = _hourly()
        member = _member()
        with pytest.raises(EquipmentError, match=r"^Only a manager of this equipment can give or take back credit\.$"):
            equipment_service.grant_credit(router, member, _member("bystander"), 60)
        free = EquipmentFactory(name="Free Saw")
        with pytest.raises(EquipmentError, match=r"^Credit is only for items that charge by the hour\.$"):
            equipment_service.grant_credit(free, member, _manager(free, "freekeeper"), 60)
        assert not EquipmentCredit.objects.exists()

    @pytest.mark.parametrize("minutes", [0, 45, -60, 6030])
    def it_refuses_minutes_off_the_half_hour_grid_or_out_of_range(minutes):
        router = _hourly()
        with pytest.raises(EquipmentError, match=r"^Give credit in half hour steps, from half an hour to 100 hours\.$"):
            equipment_service.grant_credit(router, _member(), _manager(router), minutes)

    def it_takes_up_to_one_hundred_hours():
        router = _hourly()
        member = _member()
        equipment_service.grant_credit(router, member, _manager(router), 6000)
        assert _balance(member, router) == 6000

    def it_refuses_a_note_over_140_characters():
        router = _hourly()
        with pytest.raises(EquipmentError, match=r"^Keep the note to 140 characters\.$"):
            equipment_service.grant_credit(router, _member(), _manager(router), 60, note="x" * 141)


def describe_credit_unused_time():
    def it_links_the_credit_to_a_started_booking_and_gives_it_to_its_member():
        router = _hourly()
        manager = _manager(router)
        member = _member()
        booking = _started(router, member)

        credit = equipment_service.grant_credit(router, _member("ignored"), manager, 120, reservation=booking)

        assert credit.kind == EquipmentCredit.Kind.UNUSED
        assert credit.reservation == booking
        assert credit.member == member
        assert EquipmentCredit.objects.credited_on(booking) == 120

    def it_never_credits_past_the_bookings_length():
        router = _hourly()
        manager = _manager(router)
        booking = _started(router, _member(), hours=2)
        equipment_service.grant_credit(router, booking.member, manager, 90, reservation=booking)
        with pytest.raises(EquipmentError, match=r"^Only 30 minutes of this reservation is left to credit\.$"):
            equipment_service.grant_credit(router, booking.member, manager, 60, reservation=booking)
        equipment_service.grant_credit(router, booking.member, manager, 30, reservation=booking)
        with pytest.raises(EquipmentError, match=r"^All of this reservation's time is already credited\.$"):
            equipment_service.grant_credit(router, booking.member, manager, 30, reservation=booking)

    def it_refuses_a_booking_that_has_not_started():
        router = _hourly()
        booking = EquipmentReservationFactory(equipment=router)
        with pytest.raises(EquipmentError, match=r"^This reservation hasn't started yet\.$"):
            equipment_service.grant_credit(router, booking.member, _manager(router), 30, reservation=booking)

    @pytest.mark.parametrize(
        "status", [EquipmentReservation.Status.CANCELLED, EquipmentReservation.Status.PENDING_APPROVAL, HOLD]
    )
    def it_refuses_a_booking_that_is_not_confirmed(status):
        router = _hourly()
        booking = _started(router, _member(), status=status)
        with pytest.raises(EquipmentError, match="Only a confirmed reservation"):
            equipment_service.grant_credit(router, booking.member, _manager(router), 30, reservation=booking)

    def it_refuses_a_booking_on_another_item_or_a_block():
        router = _hourly()
        manager = _manager(router)
        elsewhere = _started(_hourly(name="Elsewhere Mill"), _member())
        with pytest.raises(EquipmentError, match="Only a confirmed reservation"):
            equipment_service.grant_credit(router, elsewhere.member, manager, 30, reservation=elsewhere)
        block = _started(router, manager, kind=EquipmentReservation.Kind.BLOCK)
        with pytest.raises(EquipmentError, match="Only a confirmed reservation"):
            equipment_service.grant_credit(router, manager, manager, 30, reservation=block)


def describe_take_back_credit():
    def it_zeroes_the_balance_with_one_line_and_emails_nobody():
        router = _hourly()
        manager = _manager(router)
        member = _member()
        _give(router, member, 150)
        mail.outbox.clear()

        assert equipment_service.take_back_credit(router, member, manager) == 150

        assert _balance(member, router) == 0
        line = EquipmentCredit.objects.filter(kind=EquipmentCredit.Kind.TAKEN_BACK).get()
        assert (line.minutes, line.granted_by) == (-150, manager)
        assert mail.outbox == []

    def it_refuses_when_there_is_nothing_to_take_back_or_the_actor_does_not_manage():
        router = _hourly()
        member = _member()
        with pytest.raises(EquipmentError, match=r"^Thistlewick Fernmallow has no credit here\.$"):
            equipment_service.take_back_credit(router, member, _manager(router))
        _give(router, member, 60)
        with pytest.raises(EquipmentError, match="Only a manager"):
            equipment_service.take_back_credit(router, member, _member("nosy"))
        assert _balance(member, router) == 60


def describe_spending_on_the_free_road():
    def it_confirms_a_booking_the_credit_covers_and_spends_it():
        router = _hourly()
        member = _member()
        _give(router, member, 240)
        mail.outbox.clear()

        booking = equipment_service.reserve(router, member, _at(_day(), 9), 240)

        assert booking.status == EquipmentReservation.Status.CONFIRMED
        assert booking.credit_minutes == 240
        assert booking.amount_paid_cents == 0
        assert _balance(member, router) == 0
        spent = EquipmentCredit.objects.get(kind=EquipmentCredit.Kind.SPENT)
        assert (spent.minutes, spent.reservation) == (-240, booking)
        assert any("Reserved" in m.subject or "reservation" in m.subject.lower() for m in mail.outbox)

    def it_waits_for_approval_when_the_item_needs_it():
        router = _hourly(requires_approval=True)
        member = _member()
        _give(router, member, 240)
        booking = equipment_service.reserve(router, member, _at(_day(), 9), 240)
        assert booking.status == EquipmentReservation.Status.PENDING_APPROVAL
        assert _balance(member, router) == 0

    def it_spends_the_credit_once_when_two_tabs_book_with_it():
        router = _hourly()
        member = _member()
        _give(router, member, 240)
        equipment_service.reserve(router, member, _at(_day(), 9), 240, credit_expected=True)
        # The second tab read the same 4 hour balance before the first one's lock.
        with pytest.raises(EquipmentError, match=r"^Your credit here just changed\. Please pick your time again\.$"):
            equipment_service.reserve(router, member, _at(_day(), 13), 240, credit_expected=True)
        assert EquipmentReservation.objects.count() == 1
        assert _balance(member, router) == 0

    def it_still_refuses_an_hourly_booking_the_credit_only_partly_covers():
        router = _hourly()
        member = _member()
        _give(router, member, 60)
        with pytest.raises(EquipmentError, match="Your credit here just changed"):
            equipment_service.reserve(router, member, _at(_day(), 9), 240, credit_expected=True)
        with pytest.raises(EquipmentError, match="price for this time just changed"):
            equipment_service.reserve(router, member, _at(_day(), 9), 240)
        assert _balance(member, router) == 60


def describe_spending_on_the_checkout_road():
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_charges_the_rate_on_the_rest_and_spends_what_it_covers(mock_create):
        router = _hourly()
        member = _member()
        _give(router, member, 120)

        equipment_service.start_reservation_checkout(router, member, _at(_day(), 8), 480)

        assert mock_create.call_args.kwargs["amount_cents"] == 15000
        hold = EquipmentReservation.objects.get(member=member)
        assert (hold.status, hold.credit_minutes) == (HOLD, 120)
        assert _balance(member, router) == 0

    @patch("billing.stripe_utils.create_checkout_session")
    def it_never_opens_a_checkout_the_credit_covers(mock_create):
        router = _hourly()
        member = _member()
        _give(router, member, 240)
        with pytest.raises(EquipmentError, match="doesn't charge"):
            equipment_service.start_reservation_checkout(router, member, _at(_day(), 9), 240)
        mock_create.assert_not_called()
        assert _balance(member, router) == 240

    @patch("billing.stripe_utils.create_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_gives_the_credit_back_when_stripe_fails(mock_create):
        router = _hourly()
        member = _member()
        _give(router, member, 120)
        with pytest.raises(RuntimeError):
            equipment_service.start_reservation_checkout(router, member, _at(_day(), 8), 480)
        assert not EquipmentReservation.objects.exists()
        assert _balance(member, router) == 120

    @patch("billing.stripe_utils.expire_checkout_session")
    @patch(
        "billing.stripe_utils.retrieve_checkout_session",
        return_value={"id": "cs_credit_1", "status": "expired", "payment_status": "unpaid"},
    )
    def it_gives_the_credit_back_when_the_unpaid_hold_is_released(mock_retrieve, mock_expire):
        router = _hourly()
        member = _member()
        _give(router, member, 120)
        with patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION):
            equipment_service.start_reservation_checkout(router, member, _at(_day(), 8), 480)
        hold = EquipmentReservation.objects.get(member=member)
        assert _balance(member, router) == 0

        assert equipment_service.release_hold_if_unpaid(hold) == "released"

        assert not EquipmentReservation.objects.exists()
        assert not EquipmentCredit.objects.filter(kind=EquipmentCredit.Kind.SPENT).exists()
        assert _balance(member, router) == 120

    def it_keeps_the_spend_when_the_hold_is_paid():
        router = _hourly()
        member = _member()
        _give(router, member, 120)
        with patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION):
            equipment_service.start_reservation_checkout(router, member, _at(_day(), 8), 480)
        hold = EquipmentReservation.objects.get(member=member)
        equipment_service.finalize_paid_reservation(hold, payment_intent="pi_credit_1", amount_total=15000)
        hold.refresh_from_db()
        assert (hold.status, hold.amount_paid_cents, hold.credit_minutes) == (
            EquipmentReservation.Status.CONFIRMED,
            15000,
            120,
        )
        assert _balance(member, router) == 0


def describe_giving_it_back():
    def _paid_with_credit(router: Equipment, member: Member, **kwargs) -> EquipmentReservation:
        booking = EquipmentReservationFactory(
            equipment=router,
            member=member,
            starts_at=timezone.now() + timedelta(days=3),
            ends_at=timezone.now() + timedelta(days=3, hours=8),
            amount_paid_cents=15000,
            stripe_payment_id="pi_credit_back",
            credit_minutes=120,
            **kwargs,
        )
        _give(router, member, 120)
        EquipmentCredit.objects.create(
            equipment=router, member=member, minutes=-120, kind=EquipmentCredit.Kind.SPENT, reservation=booking
        )
        return booking

    @patch("billing.stripe_utils.create_refund", return_value={"id": "re_credit_1", "status": "succeeded"})
    def it_restores_the_credit_and_refunds_the_paid_part_on_a_member_cancel(mock_refund):
        router = _hourly()
        member = _member()
        booking = _paid_with_credit(router, member)

        booking.cancel(member)

        assert _balance(member, router) == 120
        restored = EquipmentCredit.objects.get(kind=EquipmentCredit.Kind.RESTORED)
        assert (restored.minutes, restored.reservation) == (120, booking)
        assert mock_refund.call_args.kwargs["amount_cents"] == 15000
        assert booking.refund_outcome == "refunded"

    @patch("billing.stripe_utils.create_refund", return_value={"id": "re_credit_2", "status": "succeeded"})
    def it_restores_on_a_manager_cancel(mock_refund):
        router = _hourly()
        member = _member()
        booking = _paid_with_credit(router, member)
        booking.cancel(_manager(router), reason="Spindle out", as_manager=True)
        assert _balance(member, router) == 120

    @patch("billing.stripe_utils.create_refund", return_value={"id": "re_credit_3", "status": "succeeded"})
    def it_restores_on_a_decline(mock_refund):
        router = _hourly(requires_approval=True)
        member = _member()
        booking = _paid_with_credit(router, member, status=EquipmentReservation.Status.PENDING_APPROVAL)
        booking.decline(_manager(router), reason="Bed warped")
        assert _balance(member, router) == 120
        assert mock_refund.call_args.kwargs["amount_cents"] == 15000

    def it_restores_a_fully_covered_booking_without_any_refund():
        router = _hourly()
        member = _member()
        _give(router, member, 240)
        booking = equipment_service.reserve(router, member, _at(_day(), 9), 240)
        booking.cancel(member)
        assert _balance(member, router) == 240
        assert booking.refund_outcome == ""

    def it_restores_once_when_a_second_cancel_loses_the_race():
        router = _hourly()
        member = _member()
        _give(router, member, 240)
        booking = equipment_service.reserve(router, member, _at(_day(), 9), 240)
        stale = EquipmentReservation.objects.get(pk=booking.pk)
        booking.cancel(member)
        with pytest.raises(EquipmentError):
            stale.cancel(member)
        assert _balance(member, router) == 240
        assert EquipmentCredit.objects.filter(kind=EquipmentCredit.Kind.RESTORED).count() == 1

    def it_restores_nothing_for_a_booking_that_used_no_credit():
        router = _hourly()
        booking = EquipmentReservationFactory(equipment=router, starts_at=timezone.now() + timedelta(days=3))
        booking.cancel(booking.member)
        assert not EquipmentCredit.objects.exists()


def describe_the_credit_email_event():
    def it_is_registered_with_the_bell_and_email_on():
        event = get_event("equipment.credit_granted")
        assert event.recipient is Recipients.SINGLE_USER
        assert event.channel(Channel.IN_APP).default is ChannelDefault.ON
        assert event.channel(Channel.EMAIL).default is ChannelDefault.ON
        assert event.category == "Spaces & Equipment"
        assert not event.has_channel(Channel.DISCORD)

    def it_supplies_every_documented_placeholder():
        router = _hourly()
        manager = _manager(router)
        with patch("core.events.emit.emit") as emit:
            equipment_service.grant_credit(router, _member(), manager, 60, note="x")
        context = emit.call_args.kwargs["context"]
        for name in placeholders_for("equipment.credit_granted"):
            assert name in context, f"the copy documents {{{{ {name} }}}} but the emit omits it"
        for channel in COPY_CHANNELS:
            copy = default_copy_for("equipment.credit_granted", channel)
            for fragment in (copy.subject, copy.body_text, copy.body_html):
                assert "[missing:" not in render_text(fragment, context), channel.value


def describe_review_round_one():
    @patch("billing.stripe_utils.create_checkout_session")
    def it_refuses_a_checkout_whose_credit_another_tab_spent(mock_create):
        router = _hourly()
        member = _member()
        _give(router, member, 240)
        equipment_service.reserve(router, member, _at(_day(), 9), 240, credit_expected=True)
        # The second tab read the same 4 hours and picked 8, so it expected $100 and would now be charged $200.
        with pytest.raises(EquipmentError, match=r"^Your credit here just changed\. Please pick your time again\.$"):
            equipment_service.start_reservation_checkout(router, member, _at(_day(), 13), 240, credit_expected=240)
        mock_create.assert_not_called()
        assert not EquipmentReservation.objects.filter(status=HOLD).exists()
        assert _balance(member, router) == 0

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_opens_the_checkout_when_the_credit_is_still_there(mock_create):
        router = _hourly()
        member = _member()
        _give(router, member, 120)
        equipment_service.start_reservation_checkout(router, member, _at(_day(), 8), 480, credit_expected=120)
        assert mock_create.call_args.kwargs["amount_cents"] == 15000

    def it_never_cancels_a_booking_whose_unused_time_was_credited():
        router = _hourly()
        manager = _manager(router)
        booking = _started(router, _member(), amount_paid_cents=20000, stripe_payment_id="pi_credited")
        equipment_service.grant_credit(router, booking.member, manager, 480, reservation=booking)
        with pytest.raises(
            EquipmentError, match=r"^Unused time on this reservation was already credited, so it can't be cancelled\.$"
        ):
            booking.cancel(manager, reason="Spindle out", as_manager=True)
        booking.refresh_from_db()
        assert booking.status == EquipmentReservation.Status.CONFIRMED
        assert booking.refunds.count() == 0
        assert _balance(booking.member, router) == 480

    def it_never_declines_a_booking_with_unused_time_credited():
        router = _hourly(requires_approval=True)
        manager = _manager(router)
        booking = EquipmentReservationFactory(equipment=router, status=EquipmentReservation.Status.PENDING_APPROVAL)
        EquipmentCredit.objects.create(
            equipment=router, member=booking.member, minutes=60, kind=EquipmentCredit.Kind.UNUSED, reservation=booking
        )
        with pytest.raises(EquipmentError, match="already credited"):
            booking.decline(manager, reason="No")
        booking.refresh_from_db()
        assert booking.status == EquipmentReservation.Status.PENDING_APPROVAL

    def it_caps_unused_credit_across_grants_at_the_booked_length():
        router = _hourly()
        manager = _manager(router)
        booking = _started(router, _member(), hours=8)
        equipment_service.grant_credit(router, booking.member, manager, 360, reservation=booking)
        equipment_service.grant_credit(router, booking.member, manager, 120, reservation=booking)
        with pytest.raises(EquipmentError, match="already credited"):
            equipment_service.grant_credit(router, booking.member, manager, 30, reservation=booking)
        assert EquipmentCredit.objects.credited_on(booking) == 480

    def it_only_ever_deletes_an_unpaid_hold_so_other_ledger_rows_survive():
        router = _hourly()
        member = _member()
        _give(router, member, 240)
        booking = equipment_service.reserve(router, member, _at(_day(), 9), 240, credit_expected=True)
        equipment_service._delete_hold(booking, expire_session=False)
        assert EquipmentReservation.objects.filter(pk=booking.pk).exists()
        assert EquipmentCredit.objects.filter(reservation=booking, kind=EquipmentCredit.Kind.SPENT).exists()
        assert _balance(member, router) == 0
