"""BDD specs for Eventbrite orders coming in as registrations, and refunds going out (#652, part 2).

The Eventbrite client is :class:`FakeEventbrite`; no spec reaches Eventbrite.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client
from django.urls import reverse

from billing.models import PaymentRefund
from classes import eventbrite_orders
from classes.emails import send_eventbrite_oversold_alert, send_eventbrite_shared_email_alert
from classes.eventbrite_orders import EventbriteRefundRefusedError, apply_order, refund_registration
from classes.factories import RegistrationFactory, UserFactory
from classes.models import ClassOffering, CmsActivity, Registration
from core.integrations.eventbrite import EventbriteClient, EventbriteError, EventbriteSync
from tests.classes.eventbrite_fakes import FakeEventbrite, attendee, listed_class, order

pytestmark = pytest.mark.django_db

Status = Registration.Status


@pytest.fixture
def eventbrite() -> Iterator[FakeEventbrite]:
    fake = FakeEventbrite()
    with patch.object(EventbriteClient, "from_settings", return_value=fake):
        yield fake


@pytest.fixture
def oversold() -> Iterator[MagicMock]:
    with patch.object(eventbrite_orders, "send_eventbrite_oversold_alert") as alert:
        yield alert


def describe_an_order_coming_in():
    def it_makes_one_confirmed_eventbrite_registration_per_ticket(eventbrite: FakeEventbrite, oversold: MagicMock):
        offering = listed_class()
        eventbrite.orders["o-1"] = order(
            "o-1",
            attendee("a-1", first="Ada", last="Lovelace", email="ada@example.com"),
            attendee("a-2", first="Grace", last="Hopper", email="grace@example.com"),
        )

        apply_order("o-1")

        rows = list(offering.registrations.order_by("eventbrite_attendee_id"))
        assert [(r.first_name, r.last_name, r.email) for r in rows] == [
            ("Ada", "Lovelace", "ada@example.com"),
            ("Grace", "Hopper", "grace@example.com"),
        ]
        assert {r.status for r in rows} == {Status.CONFIRMED}
        assert {r.source for r in rows} == {Registration.Source.EVENTBRITE}
        assert {r.eventbrite_order_id for r in rows} == {"o-1"}
        assert [r.eventbrite_attendee_id for r in rows] == ["a-1", "a-2"]
        assert all(r.confirmed_at is not None for r in rows)
        assert eventbrite.args("get_order") == ("o-1",)
        oversold.assert_not_called()

    def it_records_what_past_lives_receives_after_eventbrites_fees(eventbrite: FakeEventbrite, oversold: MagicMock):
        listed_class()
        ticket = attendee("a-1", gross=5600, eventbrite_fee=400, payment_fee=160, tax=40)
        eventbrite.orders["o-1"] = order("o-1", ticket)

        apply_order("o-1")

        assert Registration.objects.get(eventbrite_attendee_id="a-1").amount_paid_cents == 5000

    def it_logs_the_booking_like_a_paid_site_booking(eventbrite: FakeEventbrite, oversold: MagicMock):
        listed_class()
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1"))

        apply_order("o-1")

        registration = Registration.objects.get(eventbrite_attendee_id="a-1")
        kinds = set(CmsActivity.objects.filter(registration=registration).values_list("kind", flat=True))
        assert {CmsActivity.Kind.REGISTRATION_CREATED, CmsActivity.Kind.REGISTRATION_CONFIRMED} <= kinds

    def it_creates_nothing_new_when_the_same_order_is_delivered_again(eventbrite: FakeEventbrite, oversold: MagicMock):
        offering = listed_class()
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1"), attendee("a-2", email="b@example.com"))

        apply_order("o-1")
        apply_order("o-1")

        assert offering.registrations.count() == 2

    def it_seats_a_second_ticket_under_the_same_email_on_a_seat_alias(eventbrite: FakeEventbrite, oversold: MagicMock):
        offering = listed_class()
        RegistrationFactory(class_offering=offering, email="ada@example.com", status=Status.CONFIRMED)
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1"), attendee("a-2"))

        with patch.object(eventbrite_orders, "send_eventbrite_shared_email_alert") as shared:
            apply_order("o-1")

        rows = offering.registrations.filter(eventbrite_order_id="o-1").order_by("eventbrite_attendee_id")
        assert [r.email for r in rows] == ["ada+seat2@example.com", "ada+seat3@example.com"]
        assert [c.args for c in shared.call_args_list] == [(row, "ada@example.com") for row in rows]

    def it_sends_no_shared_email_alert_for_a_free_address(eventbrite: FakeEventbrite, oversold: MagicMock):
        listed_class()
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1"))

        with patch.object(eventbrite_orders, "send_eventbrite_shared_email_alert") as shared:
            apply_order("o-1")

        shared.assert_not_called()

    def it_stops_when_a_racing_delivery_seated_the_ticket_first(eventbrite: FakeEventbrite, oversold: MagicMock):
        offering = listed_class()
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1"))
        winner: list[Registration] = []

        def race(_offering: Any, email: str) -> str:
            winner.append(
                RegistrationFactory(class_offering=offering, email="winner@example.com", eventbrite_attendee_id="a-1")
            )
            return email

        with patch.object(eventbrite_orders, "free_seat_email", side_effect=race):
            apply_order("o-1")

        assert list(Registration.objects.filter(eventbrite_attendee_id="a-1")) == winner

    def it_takes_a_fresh_alias_when_the_email_is_taken_in_the_same_instant(
        eventbrite: FakeEventbrite, oversold: MagicMock
    ):
        offering = listed_class()
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1"))
        real = eventbrite_orders.free_seat_email
        calls: list[str] = []

        def race(target: Any, email: str) -> str:
            calls.append(email)
            if len(calls) == 1:
                RegistrationFactory(class_offering=offering, email=email, status=Status.CONFIRMED)
                return email
            return real(target, email)

        with (
            patch.object(eventbrite_orders, "free_seat_email", side_effect=race),
            patch.object(eventbrite_orders, "send_eventbrite_shared_email_alert"),
        ):
            apply_order("o-1")

        assert Registration.objects.get(eventbrite_attendee_id="a-1").email == "ada+seat2@example.com"

    def it_ignores_an_order_for_an_event_no_class_lists(eventbrite: FakeEventbrite, oversold: MagicMock):
        listed_class()
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1"), event_id="someone-elses")

        apply_order("o-1")

        assert not Registration.objects.filter(eventbrite_order_id="o-1").exists()

    def it_never_matches_an_unlisted_class_to_an_order_with_no_event(eventbrite: FakeEventbrite, oversold: MagicMock):
        listed_class(eventbrite_event_id="", eventbrite_sync_state="idle")
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1"), event_id="")

        apply_order("o-1")

        assert not Registration.objects.exists()

    def it_creates_nothing_for_a_ticket_already_refunded_or_cancelled(eventbrite: FakeEventbrite, oversold: MagicMock):
        listed_class()
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1", refunded=True), attendee("a-2", cancelled=True))

        apply_order("o-1")

        assert not Registration.objects.exists()

    def it_raises_while_sync_is_off_so_eventbrite_delivers_it_again(eventbrite: FakeEventbrite):
        eventbrite.enabled = False

        with pytest.raises(EventbriteError, match=EventbriteSync.SYNC_OFF):
            apply_order("o-1")

        assert eventbrite.calls == []


def describe_an_order_on_a_full_class():
    def it_still_registers_the_buyer_and_alerts_the_admins(eventbrite: FakeEventbrite, oversold: MagicMock):
        offering = listed_class(capacity=1)
        RegistrationFactory(class_offering=offering, status=Status.CONFIRMED)
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1"))

        apply_order("o-1")

        registration = Registration.objects.get(eventbrite_attendee_id="a-1")
        assert registration.status == Status.CONFIRMED
        oversold.assert_called_once_with(registration)

    def it_does_not_alert_when_the_ticket_takes_the_last_seat(eventbrite: FakeEventbrite, oversold: MagicMock):
        listed_class(capacity=1)
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1"))

        apply_order("o-1")

        oversold.assert_not_called()


def describe_a_refund_or_cancellation_coming_in():
    def it_refunds_only_the_tickets_eventbrite_marks_refunded(eventbrite: FakeEventbrite, oversold: MagicMock):
        offering = listed_class()
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1"), attendee("a-2", email="b@example.com"))
        apply_order("o-1")
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1", refunded=True), attendee("a-2", email="b@example.com"))

        apply_order("o-1")

        refunded = Registration.objects.get(eventbrite_attendee_id="a-1")
        assert refunded.status == Status.REFUNDED
        assert refunded.cancellation_reason == eventbrite_orders.REFUNDED_IN_EVENTBRITE
        assert Registration.objects.get(eventbrite_attendee_id="a-2").status == Status.CONFIRMED
        assert offering.seats_taken == 1

    def it_cancels_a_ticket_eventbrite_cancelled_without_a_refund(eventbrite: FakeEventbrite, oversold: MagicMock):
        offering = listed_class()
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1"))
        apply_order("o-1")
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1", cancelled=True))

        apply_order("o-1")

        cancelled = Registration.objects.get(eventbrite_attendee_id="a-1")
        assert cancelled.status == Status.CANCELLED
        assert cancelled.cancellation_reason == eventbrite_orders.CANCELLED_IN_EVENTBRITE
        assert offering.seats_taken == 0

    def it_skips_a_seat_the_refund_panel_freed_while_the_delivery_waited(
        eventbrite: FakeEventbrite, oversold: MagicMock
    ):
        listed_class()
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1"))
        apply_order("o-1")
        registration = Registration.objects.get(eventbrite_attendee_id="a-1")
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1", refunded=True))
        lock = Registration.objects.select_for_update

        def panel_refunds_first() -> Any:
            Registration.objects.filter(pk=registration.pk).update(status=Status.REFUNDED)
            return lock()

        with (
            patch.object(Registration.objects, "select_for_update", side_effect=panel_refunds_first),
            patch.object(Registration, "mark_refunded") as mark_refunded,
        ):
            apply_order("o-1")

        mark_refunded.assert_not_called()

    def it_leaves_a_seat_already_freed_alone(eventbrite: FakeEventbrite, oversold: MagicMock):
        listed_class()
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1"))
        apply_order("o-1")
        registration = Registration.objects.get(eventbrite_attendee_id="a-1")
        registration.cancel(reason="Removed by staff")
        eventbrite.orders["o-1"] = order("o-1", attendee("a-1", refunded=True))

        apply_order("o-1")

        registration.refresh_from_db()
        assert registration.status == Status.CANCELLED
        assert registration.cancellation_reason == "Removed by staff"


def _seated(eventbrite: FakeEventbrite, *tickets: dict) -> list[Registration]:
    """Bring an order in and return its registrations in ticket order."""
    listed_class()
    eventbrite.orders["o-1"] = order("o-1", *tickets)
    with patch.object(eventbrite_orders, "send_eventbrite_oversold_alert"):
        apply_order("o-1")
    return list(Registration.objects.filter(eventbrite_order_id="o-1").order_by("eventbrite_attendee_id"))


def describe_refunding_an_eventbrite_ticket_from_plfog():
    def it_refunds_the_whole_order_when_it_holds_one_ticket(eventbrite: FakeEventbrite):
        (registration,) = _seated(eventbrite, attendee("a-1"))
        actor = UserFactory()

        refund_registration(registration, reason="Sick", actor=actor)

        assert eventbrite.args("refund_order") == ("o-1", {"reason": eventbrite_orders.REFUND_REASON})
        registration.refresh_from_db()
        assert registration.status == Status.REFUNDED
        assert registration.cancellation_reason == "Sick"

    def it_refunds_one_ticket_of_several_for_that_tickets_total(eventbrite: FakeEventbrite):
        first, _second = _seated(eventbrite, attendee("a-1", gross=5509), attendee("a-2", email="b@example.com"))

        refund_registration(first, reason="", actor=None)

        body = eventbrite.args("refund_order")[1]
        assert body == {"reason": eventbrite_orders.REFUND_REASON, "total_refund_amount": "55.09"}
        first.refresh_from_db()
        assert first.status == Status.REFUNDED
        assert first.cancellation_reason == eventbrite_orders.REFUNDED_THROUGH_EVENTBRITE

    def it_refunds_the_whole_order_once_every_other_ticket_is_refunded(eventbrite: FakeEventbrite):
        first, second = _seated(eventbrite, attendee("a-1"), attendee("a-2", email="b@example.com"))
        second.mark_refunded(reason="Earlier")

        refund_registration(first, reason="", actor=None)

        assert "total_refund_amount" not in eventbrite.args("refund_order")[1]

    def it_refunds_only_this_ticket_when_another_was_cancelled_without_a_refund(eventbrite: FakeEventbrite):
        first, second = _seated(eventbrite, attendee("a-1"), attendee("a-2", email="b@example.com"))
        second.cancel(reason="Cancelled in Eventbrite.")

        refund_registration(first, reason="", actor=None)

        assert eventbrite.args("refund_order")[1]["total_refund_amount"] == "50.00"

    def it_refuses_a_ticket_that_no_longer_holds_a_seat(eventbrite: FakeEventbrite):
        (registration,) = _seated(eventbrite, attendee("a-1"))
        refund_registration(registration, reason="", actor=None)

        with pytest.raises(EventbriteRefundRefusedError, match=eventbrite_orders.NOTHING_TO_REFUND):
            refund_registration(registration, reason="", actor=None)

        assert eventbrite.names().count("refund_order") == 1

    def it_says_to_refund_in_eventbrite_when_the_partial_is_refused(eventbrite: FakeEventbrite):
        first, _second = _seated(eventbrite, attendee("a-1"), attendee("a-2", email="b@example.com"))
        eventbrite.fail["refund_order"] = EventbriteError("400 PARTIAL_REFUND_NOT_ALLOWED", 400)

        with pytest.raises(EventbriteRefundRefusedError, match="Refund it in Eventbrite"):
            refund_registration(first, reason="", actor=None)

        first.refresh_from_db()
        assert first.status == Status.CONFIRMED

    def it_says_to_refund_in_eventbrite_when_the_order_no_longer_lists_the_ticket(eventbrite: FakeEventbrite):
        first, _second = _seated(eventbrite, attendee("a-1"), attendee("a-2", email="b@example.com"))
        eventbrite.orders["o-1"] = order("o-1", attendee("a-2", email="b@example.com"))

        with pytest.raises(EventbriteRefundRefusedError, match="Refund it in Eventbrite"):
            refund_registration(first, reason="", actor=None)

        assert "refund_order" not in eventbrite.names()

    def it_passes_on_eventbrites_reason_when_a_whole_order_refund_is_refused(eventbrite: FakeEventbrite):
        (registration,) = _seated(eventbrite, attendee("a-1"))
        eventbrite.fail["refund_order"] = EventbriteError("400 ORDER_ALREADY_REFUNDED", 400)

        with pytest.raises(
            EventbriteRefundRefusedError, match="Eventbrite refused the refund: 400 ORDER_ALREADY_REFUNDED"
        ):
            refund_registration(registration, reason="", actor=None)

    def it_refuses_while_sync_is_off(eventbrite: FakeEventbrite):
        (registration,) = _seated(eventbrite, attendee("a-1"))
        eventbrite.enabled = False

        with pytest.raises(EventbriteRefundRefusedError, match=eventbrite_orders.SYNC_OFF_REFUSED):
            refund_registration(registration, reason="", actor=None)

        assert "refund_order" not in eventbrite.names()

    def it_never_offers_a_stripe_refund_for_an_eventbrite_ticket(eventbrite: FakeEventbrite):
        (registration,) = _seated(eventbrite, attendee("a-1"))

        assert registration.refundable_cents == registration.amount_paid_cents > 0
        assert registration.refund_payment_intent_id == ""
        registration.mark_refunded()
        assert registration.refundable_cents == 0


def describe_reading_a_delivery():
    @pytest.mark.parametrize("action", ["order.placed", "order.updated", "order.refunded"])
    def it_takes_the_order_id_from_an_order_action(action: str):
        body = f'{{"config": {{"action": "{action}"}}, "api_url": "https://www.eventbriteapi.com/v3/orders/123/"}}'

        assert eventbrite_orders.order_id_from_delivery(body.encode()) == "123"

    def it_skips_an_action_it_does_not_act_on():
        body = b'{"config": {"action": "test"}, "api_url": "https://www.eventbriteapi.com/v3/users/1/"}'

        assert eventbrite_orders.order_id_from_delivery(body) is None

    @pytest.mark.parametrize(
        "body",
        [
            b"not json",
            b"[]",
            b'{"api_url": "https://www.eventbriteapi.com/v3/orders/1/"}',
            b'{"config": {"action": "order.placed"}}',
            b'{"config": {"action": "order.placed"}, "api_url": "https://evil.example/v3/orders/1/"}',
            b'{"config": {"action": "order.placed"}, "api_url": "https://www.eventbriteapi.com/v3/orders/1/x"}',
            b'{"config": {"action": "order.placed"}, "api_url": "https://www.eventbriteapi.com/v3/events/1/"}',
        ],
    )
    def it_rejects_anything_not_shaped_like_an_eventbrite_order(body: bytes):
        with pytest.raises(eventbrite_orders.UnverifiedDeliveryError):
            eventbrite_orders.order_id_from_delivery(body)


def describe_the_refund_panel_on_an_eventbrite_ticket():
    def it_offers_a_whole_ticket_refund_through_eventbrite(eventbrite: FakeEventbrite, admin_user: Any, client: Client):
        (registration,) = _seated(eventbrite, attendee("a-1"))
        client.force_login(admin_user)

        content = client.get(reverse("classes:admin_registration_refund_form", args=[registration.pk])).content.decode()

        assert "Refund in Eventbrite" in content
        assert 'name="amount"' not in content

    def it_refunds_through_eventbrite_and_never_stripe(eventbrite: FakeEventbrite, admin_user: Any, client: Client):
        (registration,) = _seated(eventbrite, attendee("a-1"))
        client.force_login(admin_user)

        response = client.post(reverse("classes:admin_registration_refund", args=[registration.pk]), {"reason": "Sick"})

        assert response.status_code == 204
        assert "refund-done" in response.headers["HX-Trigger"]
        assert eventbrite.args("refund_order")[0] == "o-1"
        registration.refresh_from_db()
        assert registration.status == Status.REFUNDED
        assert not PaymentRefund.objects.exists()

    def it_refunds_once_when_the_button_is_pressed_twice(eventbrite: FakeEventbrite, admin_user: Any, client: Client):
        first, _second = _seated(eventbrite, attendee("a-1"), attendee("a-2", email="b@example.com"))
        client.force_login(admin_user)
        url = reverse("classes:admin_registration_refund", args=[first.pk])

        assert client.post(url, {"reason": ""}).status_code == 204
        repeat = client.post(url, {"reason": ""})

        assert repeat.status_code == 200
        assert eventbrite_orders.NOTHING_TO_REFUND in repeat.content.decode()
        assert eventbrite.names().count("refund_order") == 1

    def it_keeps_the_refund_when_the_waitlist_notice_fails(eventbrite: FakeEventbrite, admin_user: Any, client: Client):
        (registration,) = _seated(eventbrite, attendee("a-1"))
        client.force_login(admin_user)
        url = reverse("classes:admin_registration_refund", args=[registration.pk])

        with patch.object(ClassOffering, "promote_next_from_waitlist", side_effect=RuntimeError("mail down")):
            assert client.post(url, {"reason": ""}).status_code == 204
        repeat = client.post(url, {"reason": ""})

        registration.refresh_from_db()
        assert registration.status == Status.REFUNDED
        assert repeat.status_code == 200
        assert eventbrite.names().count("refund_order") == 1

    def it_promotes_the_waitlist_once_the_refund_is_recorded(eventbrite: FakeEventbrite):
        (registration,) = _seated(eventbrite, attendee("a-1"))

        with patch.object(ClassOffering, "promote_next_from_waitlist") as promote:
            refund_registration(registration, reason="", actor=None)

        promote.assert_called_once_with()

    def it_disables_the_refund_button_while_it_submits(eventbrite: FakeEventbrite, admin_user: Any, client: Client):
        (registration,) = _seated(eventbrite, attendee("a-1"))
        client.force_login(admin_user)

        content = client.get(reverse("classes:admin_registration_refund_form", args=[registration.pk])).content.decode()

        assert "hx-disabled-elt=\"find button[type='submit']\"" in content

    def it_keeps_the_modal_open_with_eventbrites_refusal(eventbrite: FakeEventbrite, admin_user: Any, client: Client):
        first, _second = _seated(eventbrite, attendee("a-1"), attendee("a-2", email="b@example.com"))
        eventbrite.fail["refund_order"] = EventbriteError("400", 400)
        client.force_login(admin_user)

        response = client.post(reverse("classes:admin_registration_refund", args=[first.pk]), {"reason": ""})

        assert response.status_code == 200
        assert "data-eventbrite-refund-refused" in response.content.decode()
        first.refresh_from_db()
        assert first.status == Status.CONFIRMED

    def it_says_on_the_refunds_card_that_the_refund_goes_through_eventbrite(
        eventbrite: FakeEventbrite, admin_user: Any, client: Client
    ):
        (registration,) = _seated(eventbrite, attendee("a-1"))
        client.force_login(admin_user)

        content = client.get(
            reverse("classes:admin_registration_refunds_card", args=[registration.pk])
        ).content.decode()

        assert "a whole ticket at a time" in content


def describe_the_oversold_alert():
    def it_tells_every_admin_who_bought_which_class_and_the_order(eventbrite: FakeEventbrite):
        (registration,) = _seated(eventbrite, attendee("a-1", first="Ada", last="Lovelace"))

        with patch("core.events.senders.emit_flat_email") as emit:
            send_eventbrite_oversold_alert(registration)

        key = emit.call_args.args[0]
        kwargs = emit.call_args.kwargs
        assert key == "classes.orphaned_payment_alert"
        assert kwargs["subject"] == f"Oversold on Eventbrite: Ada Lovelace, {registration.class_offering.title}"
        assert "ada@example.com" in kwargs["text_body"]
        assert registration.class_offering.title in kwargs["text_body"]
        assert "Eventbrite order: o-1" in kwargs["text_body"]
        assert kwargs["period"] == f"reg:{registration.pk}:eventbrite:a-1"


def describe_the_shared_email_alert():
    def it_tells_every_admin_the_ticket_was_seated_on_an_alias(eventbrite: FakeEventbrite):
        (registration,) = _seated(eventbrite, attendee("a-1", first="Ada", last="Lovelace"))

        with patch("core.events.senders.emit_flat_email") as emit:
            send_eventbrite_shared_email_alert(registration, "shared@example.com")

        kwargs = emit.call_args.kwargs
        assert emit.call_args.args[0] == "classes.orphaned_payment_alert"
        assert (
            kwargs["subject"]
            == f"Eventbrite ticket on a shared email: Ada Lovelace, {registration.class_offering.title}"
        )
        assert "under shared@example.com" in kwargs["text_body"]
        assert f"registered as {registration.email}" in kwargs["text_body"]
        assert "Eventbrite order: o-1" in kwargs["text_body"]
        assert kwargs["period"] == f"reg:{registration.pk}:eventbrite-shared:a-1"
