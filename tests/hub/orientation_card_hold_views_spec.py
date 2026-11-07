"""BDD specs for #1128 on the hub: what members and Orientors see and click around a card hold.

The service rules live in ``tests/membership/orientation_card_hold_spec.py``; here Stripe is faked
at ``billing.stripe_utils`` and the pages are rendered for real.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership import orientations
from membership.models import Member, OrientationBooking, OrientationSlot
from tests.membership.factories import (
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)
from tests.membership.stripe_intents import CANCEL, CAPTURE, READ, intent

pytestmark = pytest.mark.django_db

Status = OrientationBooking.Status


def _login(client: Client, username: str, *, admin: bool = False) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    if admin:
        member.fog_role = Member.FogRole.ADMIN
        member.save(update_fields=["fog_role"])
        member.sync_user_permissions()
    client.login(username=username, password="pass")
    return member


def _slot(price_cents: int = 1500) -> OrientationSlot:
    settings_obj = GuildOrientationSettingsFactory()
    OrientationTypeFactory(guild=settings_obj.guild, price_cents=price_cents)
    starts = timezone.now() + timedelta(days=3)
    return OrientationSlotFactory(guild=settings_obj.guild, starts_at=starts, ends_at=starts + timedelta(hours=1))


def _held(status: str = Status.REQUESTED, **overrides) -> OrientationBooking:
    defaults = {
        "slot": _slot(),
        "status": status,
        "authorized_cents": 1500,
        "authorized_at": timezone.now(),
        "stripe_payment_id": "pi_hold",
        "stripe_session_id": "cs_hold",
    }
    defaults.update(overrides)
    return OrientationBookingFactory(**defaults)


def _messages(response) -> list[str]:
    return [str(m) for m in response.context["messages"]]


def _held_session() -> dict:
    return {
        "id": "cs_hold",
        "url": "https://checkout.example/cs_hold",
        "status": "complete",
        "payment_status": "unpaid",
        "payment_intent": "pi_hold",
        "amount_total": 1500,
    }


def describe_before_booking():
    def it_says_the_card_is_held_not_charged_on_the_guild_page(client: Client):
        _login(client, "hv_guild")
        slot = _slot()
        content = client.get(reverse("hub_guild_detail", args=[slot.guild.slug])).content.decode()
        assert "Your card is held when you book and charged only when an Orientor confirms." in content
        assert "Our secure checkout holds $15 on your card." in content
        # The changelog renders on every page and quotes the old line, so read the section alone.
        section = content.split('id="guild-orientation"')[1].split("</section>")[0]
        assert "You pay when you book" not in section

    def it_says_so_on_the_orientation_info_page(client: Client):
        _login(client, "hv_info")
        slot = _slot()
        content = client.get(reverse("hub_orientation_info", args=[slot.guild.pk])).content.decode()
        assert "your card is held when you book and charged only when an Orientor confirms" in content


def describe_back_from_checkout():
    def it_tells_the_member_their_card_is_held_while_the_request_waits(client: Client):
        member = _login(client, "hv_return")
        booking = _held(member=member)
        token = orientations.make_checkout_token(booking)
        content = client.get(reverse("hub_orientation_checkout_return", args=[token])).content.decode()
        assert "Your card is held for $15, not charged." in content
        assert "charged only when an Orientor confirms" in content

    def it_says_the_hold_is_released_once_declined(client: Client):
        member = _login(client, "hv_return_dec")
        booking = _held(status=Status.DECLINED, member=member, hold_released_at=timezone.now())
        token = orientations.make_checkout_token(booking)
        content = client.get(reverse("hub_orientation_checkout_return", args=[token])).content.decode()
        assert "hold on your card is released, and you were not charged." in content

    def it_says_the_hold_is_being_released_while_stripe_still_holds_it(client: Client):
        member = _login(client, "hv_return_pend")
        booking = _held(status=Status.CANCELLED, member=member)
        token = orientations.make_checkout_token(booking)
        content = client.get(reverse("hub_orientation_checkout_return", args=[token])).content.decode()
        assert "hold on your card is being released, and you were not charged." in content

    def it_resumes_a_checkout_that_already_held_the_card(client: Client):
        member = _login(client, "hv_resume")
        hold = OrientationBookingFactory(
            slot=_slot(), member=member, status=Status.PENDING_PAYMENT, stripe_session_id="cs_hold"
        )
        with (
            patch("billing.stripe_utils.retrieve_checkout_session", return_value=_held_session()),
            patch(READ, return_value=intent("requires_capture")),
        ):
            response = client.post(reverse("hub_orientation_checkout_resume", args=[hold.pk]))
        assert "/orientation/checkout/return/" in response["Location"]
        hold.refresh_from_db()
        assert (hold.status, hold.authorized_cents, hold.amount_paid_cents) == (Status.REQUESTED, 1500, 0)

    def it_keeps_a_held_checkout_from_the_stripe_cancel_page(client: Client):
        member = _login(client, "hv_cancelled")
        hold = OrientationBookingFactory(
            slot=_slot(), member=member, status=Status.PENDING_PAYMENT, stripe_session_id="cs_hold"
        )
        token = orientations.make_checkout_token(hold)
        with (
            patch("billing.stripe_utils.retrieve_checkout_session", return_value=_held_session()),
            patch(READ, return_value=intent("requires_capture")),
        ):
            response = client.post(reverse("hub_orientation_checkout_cancelled", args=[token]), follow=True)
        assert any("your card is held, not charged" in m for m in _messages(response))
        hold.refresh_from_db()
        assert hold.status == Status.REQUESTED

    def it_points_the_pending_cancel_at_the_request_once_the_card_is_held(client: Client):
        member = _login(client, "hv_cancel_hold")
        hold = OrientationBookingFactory(
            slot=_slot(), member=member, status=Status.PENDING_PAYMENT, stripe_session_id="cs_hold"
        )
        with (
            patch("billing.stripe_utils.retrieve_checkout_session", return_value=_held_session()),
            patch(READ, return_value=intent("requires_capture")),
        ):
            response = client.post(reverse("hub_orientation_checkout_cancel_hold", args=[hold.pk]), follow=True)
        assert any("the hold on your card is released" in m for m in _messages(response))


def describe_the_members_booking():
    def it_shows_the_hold_on_the_guild_page_card(client: Client):
        member = _login(client, "hv_card")
        booking = _held(member=member)
        content = client.get(reverse("hub_guild_detail", args=[booking.slot.guild.slug])).content.decode()
        assert "$15 held on your card, not charged." in content
        assert "The hold on your card is released, so you" in content  # the cancel confirm
        assert "Automatic refund if this is declined" not in content

    def it_shows_the_hold_in_the_bookings_tab_and_its_cancel_confirm(client: Client):
        member = _login(client, "hv_tab")
        booking = _held(member=member)
        content = client.get(reverse("hub_orientations"), {"view": "bookings"}).content.decode()
        row = content[content.index(f'data-booking-row="{booking.pk}"') :]
        assert "Card held $15" in row[: row.index("</tr>")]
        assert "The hold on your card is released, so you" in content


def describe_the_orientor_side():
    def it_shows_the_hold_and_what_confirm_and_decline_do(client: Client):
        _login(client, "hv_respond", admin=True)
        booking = _held()
        content = client.get(reverse("hub_orientation_respond", args=[booking.pk])).content.decode()
        assert 'data-hold-chip="held"' in content
        assert "Card held $15" in content
        assert "Confirming charges the $15 held on their card." in content
        assert "Declining releases the $15 hold on their card. Nothing is charged." in content
        assert "Declining refunds their" not in content

    def it_shows_a_released_hold_on_a_declined_request(client: Client):
        _login(client, "hv_respond_rel", admin=True)
        booking = _held(status=Status.DECLINED, hold_released_at=timezone.now())
        content = client.get(reverse("hub_orientation_respond", args=[booking.pk])).content.decode()
        assert 'data-hold-chip="released"' in content

    def it_charges_on_confirm_and_says_so(client: Client):
        _login(client, "hv_confirm", admin=True)
        booking = _held()
        with patch(CAPTURE, return_value=intent("succeeded")):
            response = client.post(
                reverse("hub_orientation_respond", args=[booking.pk]), {"action": "confirm"}, follow=True
            )
        assert "Orientation confirmed. We emailed the member." in _messages(response)
        booking.refresh_from_db()
        assert (booking.status, booking.amount_paid_cents) == (Status.CONFIRMED, 1500)

    def it_tells_the_orientor_plainly_when_the_card_will_not_charge(client: Client):
        _login(client, "hv_confirm_fail", admin=True)
        booking = _held()
        with patch(CAPTURE, return_value=intent("canceled")), patch(CANCEL, return_value=intent("canceled")):
            response = client.post(
                reverse("hub_orientation_respond", args=[booking.pk]), {"action": "confirm"}, follow=True
            )
        assert any(
            "couldn't charge the member's card" in m and "declined the request" in m for m in _messages(response)
        )
        booking.refresh_from_db()
        assert booking.status == Status.DECLINED

    def it_asks_the_orientor_to_try_again_when_stripe_is_down(client: Client):
        _login(client, "hv_confirm_down", admin=True)
        booking = _held()
        with patch(CAPTURE, side_effect=RuntimeError("down")), patch(READ, side_effect=RuntimeError("down")):
            response = client.post(
                reverse("hub_orientation_respond", args=[booking.pk]), {"action": "confirm"}, follow=True
            )
        assert any("Try again in a minute" in m for m in _messages(response))
        booking.refresh_from_db()
        assert booking.status == Status.REQUESTED

    def it_words_the_bookings_tab_confirm_and_decline_for_a_hold(client: Client):
        _login(client, "hv_modals", admin=True)
        _held()
        content = client.get(reverse("hub_orientations"), {"view": "bookings"}).content.decode()
        assert "Confirming charges the $15 held on their card." in content
        assert "hold on their card is released, so nothing is charged." in content
        assert "data-booking-hold" in content

    def it_says_a_move_charges_the_hold(client: Client):
        _login(client, "hv_move", admin=True)
        booking = _held()
        content = client.get(reverse("hub_orientation_respond", args=[booking.pk])).content.decode()
        assert "Moving it now confirms the request and charges the $15 held on their card." in content


def describe_the_emailed_links():
    def _page(client: Client, booking: OrientationBooking, action: str, **kwargs) -> str:
        token = orientations.make_action_token(booking, action, **kwargs)
        return client.get(reverse("hub_orientation_action", args=[token])).content.decode()

    @pytest.mark.parametrize(
        ("action", "line"),
        [
            ("confirm", "Confirming charges the $15 held on their card."),
            ("decline", "Declining releases the $15 hold on their card. Nothing is charged."),
            ("cancel", "Cancelling releases the $15 hold on the card. Nothing is charged."),
        ],
    )
    def it_says_what_each_link_does_to_the_hold(client: Client, action: str, line: str):
        assert line in _page(client, _held(), action)

    def it_tells_the_member_accepting_a_new_time_charges_the_hold(client: Client):
        booking = orientations.propose_orientation_time(_held(), timezone.now() + timedelta(days=9), actor=None)
        accept = _page(client, booking, "accept_time", proposal=booking.proposal_key)
        keep = _page(client, booking, "keep_time", proposal=booking.proposal_key)
        assert "Accepting confirms your orientation and charges the $15 held on your card." in accept
        assert "Your card stays held, not charged, until your orientation is confirmed." in keep

    def it_reports_a_card_that_would_not_charge(client: Client):
        booking = _held()
        token = orientations.make_action_token(booking, "confirm")
        with patch(CAPTURE, return_value=intent("canceled")), patch(CANCEL, return_value=intent("canceled")):
            content = client.post(reverse("hub_orientation_action", args=[token])).content.decode()
        assert 'data-capture-result="card_failed"' in content
        assert booking.member.display_name in content

    def it_tells_the_member_their_own_accept_could_not_charge(client: Client):
        booking = orientations.propose_orientation_time(_held(), timezone.now() + timedelta(days=9), actor=None)
        token = orientations.make_action_token(booking, "accept_time", proposal=booking.proposal_key)
        with patch(CAPTURE, return_value=intent("canceled")), patch(CANCEL, return_value=intent("canceled")):
            content = client.post(reverse("hub_orientation_action", args=[token])).content.decode()
        assert 'data-capture-result="card_failed"' in content
        assert "we emailed you to book again" in content

    def it_asks_to_try_again_when_stripe_is_down(client: Client):
        booking = _held()
        token = orientations.make_action_token(booking, "confirm")
        with patch(CAPTURE, side_effect=RuntimeError("down")), patch(READ, side_effect=RuntimeError("down")):
            content = client.post(reverse("hub_orientation_action", args=[token])).content.decode()
        assert 'data-capture-result="try_again"' in content


def describe_the_member_answering_a_new_time():
    def _proposed_for(member: Member) -> OrientationBooking:
        return orientations.propose_orientation_time(
            _held(member=member), timezone.now() + timedelta(days=9), actor=None
        )

    def it_says_the_request_was_declined_when_the_card_will_not_charge(client: Client):
        booking = _proposed_for(_login(client, "hv_answer_fail"))
        with patch(CAPTURE, return_value=intent("canceled")), patch(CANCEL, return_value=intent("canceled")):
            response = client.post(
                reverse("hub_orientation_proposal_answer", args=[booking.pk]),
                {"answer": "accept", "proposal": booking.proposal_key},
                follow=True,
            )
        assert any("your request was declined" in m and "You were not charged" in m for m in _messages(response))

    def it_asks_to_try_again_when_stripe_is_down(client: Client):
        booking = _proposed_for(_login(client, "hv_answer_down"))
        with patch(CAPTURE, side_effect=RuntimeError("down")), patch(READ, side_effect=RuntimeError("down")):
            response = client.post(
                reverse("hub_orientation_proposal_answer", args=[booking.pk]),
                {"answer": "accept", "proposal": booking.proposal_key},
                follow=True,
            )
        assert any("nothing changed" in m for m in _messages(response))
