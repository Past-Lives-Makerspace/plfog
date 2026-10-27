"""BDD specs for 0248, which carries orientation booking notes into the message thread.

``member_note`` becomes the member's first message and ``lead_note`` a message with no author;
then both fields leave the model while their columns stay, with a database default, for the
release still serving during the deploy. Each spec steps membership back to 0247 with the real
migration executor and forward again, so the functions run against the schema they were written for.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from membership.models import OrientationBooking
from tests.membership.factories import MembershipPlanFactory, OrientationBookingFactory

BEFORE = ("membership", "0247_orientation_booking_messages")
AFTER = ("membership", "0248_orientation_notes_to_messages")

pytestmark = pytest.mark.django_db(transaction=True)

migration = importlib.import_module("membership.migrations.0248_orientation_notes_to_messages")


def _migrate(target: tuple[str, str]) -> Any:
    executor = MigrationExecutor(connection)
    executor.migrate([target])
    executor.loader.build_graph()
    return executor.loader.project_state([target]).apps


@pytest.fixture
def step_back() -> Iterator[Any]:
    """Steps membership back to 0247 (both notes still fields) when called; forward again after.

    Called after a spec makes its bookings, since the current model's inserts leave the note
    columns to their database default, which 0247's schema does not have.
    """
    try:
        yield lambda: _migrate(BEFORE)
    finally:
        _migrate(AFTER)


def _booking(username: str) -> OrientationBooking:
    MembershipPlanFactory()
    member = User.objects.create_user(username=username, email=f"{username}@example.com").member
    return OrientationBookingFactory(member=member)


def describe_notes_to_messages():
    def it_copies_each_note_into_the_thread_signed_and_dated(step_back: Any):
        noted = _booking("mig_noted")
        declined_at = timezone.now()
        blank = _booking("mig_blank")
        old_apps = step_back()
        old_booking = old_apps.get_model("membership", "OrientationBooking")
        old_booking.objects.filter(pk=noted.pk).update(
            member_note="  Zzfirst timer  ", lead_note="Zzfull this month", declined_at=declined_at
        )
        old_booking.objects.filter(pk=blank.pk).update(member_note="   ", lead_note="")

        migration.notes_to_messages(old_apps, None)

        message = old_apps.get_model("membership", "OrientationBookingMessage")
        rows = list(message.objects.order_by("pk").values_list("booking_id", "author_id", "body", "created_at"))
        assert rows == [
            (noted.pk, noted.member.user_id, "Zzfirst timer", noted.requested_at),
            (noted.pk, None, "Zzfull this month", declined_at),
        ]

    def it_dates_a_lead_note_with_no_decline_at_the_request(step_back: Any):
        booking = _booking("mig_undated")
        old_apps = step_back()
        old_apps.get_model("membership", "OrientationBooking").objects.filter(pk=booking.pk).update(lead_note="Zzx")

        migration.notes_to_messages(old_apps, None)

        [created_at] = old_apps.get_model("membership", "OrientationBookingMessage").objects.values_list(
            "created_at", flat=True
        )
        assert created_at == booking.requested_at


def describe_messages_to_notes():
    def it_fills_the_notes_from_the_thread_and_keeps_every_message(step_back: Any):
        booking = _booking("mig_rev")
        other = _booking("mig_rev_none")
        old_apps = step_back()
        message = old_apps.get_model("membership", "OrientationBookingMessage")
        message.objects.create(booking_id=booking.pk, author_id=booking.member.user_id, body="Zzmember first")
        message.objects.create(booking_id=booking.pk, author_id=None, body="Zzstaff older")
        message.objects.create(booking_id=booking.pk, author_id=booking.member.user_id, body="Zzmember later")
        message.objects.create(booking_id=booking.pk, author_id=None, body="Zzstaff newest")
        message.objects.create(booking_id=other.pk, author_id=None, body="Zzonly staff")

        migration.messages_to_notes(old_apps, None)

        old_booking = old_apps.get_model("membership", "OrientationBooking")
        assert old_booking.objects.values_list("member_note", "lead_note").get(pk=booking.pk) == (
            "Zzmember first",
            "Zzstaff newest",
        )
        assert old_booking.objects.values_list("member_note", "lead_note").get(pk=other.pk) == ("", "Zzonly staff")
        assert message.objects.count() == 5


def describe_the_retired_columns():
    def it_keeps_both_columns_with_a_blank_default_for_the_old_release():
        _migrate(AFTER)
        booking = _booking("mig_default")

        assert "member_note" not in {field.name for field in OrientationBooking._meta.get_fields()}
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT member_note, lead_note FROM membership_orientationbooking WHERE id = %s", [booking.pk]
            )
            assert cursor.fetchone() == ("", "")
