"""BDD specs for keeping the Eventbrite ticket quantity in step with plfog's seats (#652, part 2).

Eventbrite's ``quantity_total`` counts the tickets it already sold, so plfog pushes
``spots_remaining + quantity_sold`` and Eventbrite's own remaining equals ``spots_remaining``.
Pushes run after commit, so each spec runs the on-commit callbacks. The client is
:class:`FakeEventbrite`; no spec reaches Eventbrite.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any
from unittest.mock import patch

import pytest

from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import ClassOffering, Registration
from core.integrations.eventbrite import EventbriteClient, EventbriteError, EventbriteSync
from tests.classes.eventbrite_fakes import FakeEventbrite, listed_class

pytestmark = pytest.mark.django_db

Status = Registration.Status
State = ClassOffering.EventbriteSyncState
OnCommit = Callable[..., AbstractContextManager[Any]]


@pytest.fixture
def eventbrite() -> Iterator[FakeEventbrite]:
    fake = FakeEventbrite()
    with patch.object(EventbriteClient, "from_settings", return_value=fake):
        yield fake


def _pushed_quantities(eventbrite: FakeEventbrite) -> list[int]:
    return [
        args[2]["ticket_class"]["quantity_total"] for name, args in eventbrite.calls if name == "update_ticket_class"
    ]


def describe_a_booking_on_this_site():
    def it_sets_eventbrites_quantity_to_the_seats_left_plus_what_it_sold(
        eventbrite: FakeEventbrite, django_capture_on_commit_callbacks: OnCommit
    ):
        offering = listed_class(capacity=6)
        eventbrite.quantity_sold = 2

        with django_capture_on_commit_callbacks(execute=True):
            RegistrationFactory(class_offering=offering, status=Status.CONFIRMED)

        assert eventbrite.args("get_ticket_class") == ("ev-9", "tc-9")
        assert _pushed_quantities(eventbrite) == [5 + 2]

    def it_leaves_eventbrite_sold_out_when_the_last_seat_goes(
        eventbrite: FakeEventbrite, django_capture_on_commit_callbacks: OnCommit
    ):
        offering = listed_class(capacity=1)
        eventbrite.quantity_sold = 3

        with django_capture_on_commit_callbacks(execute=True):
            RegistrationFactory(class_offering=offering, status=Status.PENDING)

        assert _pushed_quantities(eventbrite) == [3]

    def it_pushes_nothing_for_a_waitlist_signup(
        eventbrite: FakeEventbrite, django_capture_on_commit_callbacks: OnCommit
    ):
        offering = listed_class()

        with django_capture_on_commit_callbacks(execute=True):
            RegistrationFactory(class_offering=offering, status=Status.WAITLISTED)

        assert eventbrite.calls == []

    def it_pushes_nothing_when_a_held_seat_is_confirmed(
        eventbrite: FakeEventbrite, django_capture_on_commit_callbacks: OnCommit
    ):
        registration = RegistrationFactory(class_offering=listed_class(), status=Status.PENDING)

        with django_capture_on_commit_callbacks(execute=True):
            registration.status = Status.CONFIRMED
            registration.save(update_fields=["status"])

        assert eventbrite.calls == []

    @pytest.mark.parametrize(
        "unlisted",
        [
            {"eventbrite_enabled": False, "eventbrite_event_id": "", "eventbrite_ticket_class_id": ""},
            {"eventbrite_sync_state": State.FAILED},
            {"eventbrite_ticket_class_id": ""},
        ],
    )
    def it_pushes_nothing_for_a_class_not_listed(
        unlisted: dict[str, Any], eventbrite: FakeEventbrite, django_capture_on_commit_callbacks: OnCommit
    ):
        offering = listed_class(**unlisted)

        with django_capture_on_commit_callbacks(execute=True):
            RegistrationFactory(class_offering=offering, status=Status.CONFIRMED)

        assert eventbrite.calls == []


def describe_a_cancellation_on_this_site():
    def it_hands_the_freed_seat_back_to_eventbrite(
        eventbrite: FakeEventbrite, django_capture_on_commit_callbacks: OnCommit
    ):
        offering = listed_class(capacity=2)
        registration = RegistrationFactory(class_offering=offering, status=Status.CONFIRMED)

        with django_capture_on_commit_callbacks(execute=True):
            registration.cancel(reason="Cannot make it")

        assert _pushed_quantities(eventbrite) == [2]

    def it_pushes_both_classes_when_a_seat_moves(
        eventbrite: FakeEventbrite, django_capture_on_commit_callbacks: OnCommit
    ):
        source = listed_class(capacity=4)
        target = listed_class(capacity=4, eventbrite_event_id="ev-10", eventbrite_ticket_class_id="tc-10")
        registration = RegistrationFactory(class_offering=source, status=Status.CONFIRMED)

        with django_capture_on_commit_callbacks(execute=True), patch("classes.emails.send_registration_moved"):
            registration.move_to(target)

        pushed = {
            args[0]: args[2]["ticket_class"]["quantity_total"]
            for name, args in eventbrite.calls
            if name == "update_ticket_class"
        }
        assert pushed == {"ev-9": 4, "ev-10": 3}

    def it_pushes_nothing_when_a_freed_row_moves(
        eventbrite: FakeEventbrite, django_capture_on_commit_callbacks: OnCommit
    ):
        registration = RegistrationFactory(class_offering=listed_class(), status=Status.CANCELLED)
        target = ClassOfferingFactory()

        with django_capture_on_commit_callbacks(execute=True):
            registration.move_to(target)

        assert eventbrite.calls == []


def describe_when_the_quantity_push_cannot_go_through():
    def it_records_the_failure_for_the_retry_command(
        eventbrite: FakeEventbrite, django_capture_on_commit_callbacks: OnCommit
    ):
        offering = listed_class()
        eventbrite.fail["update_ticket_class"] = EventbriteError("POST ticket: 500", 500)

        with django_capture_on_commit_callbacks(execute=True):
            RegistrationFactory(class_offering=offering, status=Status.CONFIRMED)

        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.FAILED
        assert offering.eventbrite_sync_error == "POST ticket: 500"
        assert offering in ClassOffering.objects.needs_eventbrite_push()

    def it_waits_for_sync_to_come_back_on(eventbrite: FakeEventbrite, django_capture_on_commit_callbacks: OnCommit):
        offering = listed_class()
        eventbrite.enabled = False

        with django_capture_on_commit_callbacks(execute=True):
            RegistrationFactory(class_offering=offering, status=Status.CONFIRMED)

        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.PENDING
        assert offering.eventbrite_sync_error == EventbriteSync.SYNC_OFF
        assert eventbrite.calls == []


def describe_a_listing_update():
    def it_sets_the_same_quantity_as_a_booking_does(eventbrite: FakeEventbrite):
        offering = listed_class(capacity=6)
        RegistrationFactory(class_offering=offering, status=Status.CONFIRMED)
        eventbrite.quantity_sold = 4

        offering.sync_eventbrite_listing()

        assert _pushed_quantities(eventbrite) == [5 + 4]
