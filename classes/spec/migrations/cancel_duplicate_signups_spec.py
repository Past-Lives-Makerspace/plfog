"""Data-migration spec for classes 0065: duplicate signups cancelled, and restored on reverse.

Uses Django's ``MigrationExecutor`` (the pattern of the other migration specs in this
package) so rows are built against the 0064 state, after the seat constraint has been
dropped and before the duplicates are cancelled. Each test restores the schema to head
in a ``finally`` so the rest of the suite sees the current DB.

The class itself is built with the ordinary factory while the DB is still at head:
only the duplicate registrations need the historical model, because at head the
constraint makes writing a second one impossible, which is the whole point.
"""

from __future__ import annotations

from datetime import timedelta
from importlib import import_module
from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from classes.factories import ClassOfferingFactory

_APP = "classes"
_BEFORE = "0064_video_provider_help_text"
_AFTER = "0065_cancel_duplicate_signups"
_HEAD = "0066_registration_uq_registration_seat_email"

_migration = import_module(f"classes.migrations.{_AFTER}")
MARKER = _migration.MARKER
reason_for = _migration.reason_for

EMAIL = "sam@example.com"


def _migrate(target: str):
    """Migrate the classes app to ``target`` and return that state's historical apps."""
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _make_registration(
    apps: Any,
    *,
    offering_id: int,
    status: str,
    minutes_ago: int,
    email: str = EMAIL,
    amount_paid_cents: int = 0,
    stripe_payment_id: str = "",
) -> int:
    """Write one registration against the historical model and return its pk.

    ``registered_at`` is ``auto_now_add``, so the age that decides which row keeps
    the seat has to be stamped with a follow-up ``update()``.
    """
    Registration = apps.get_model(_APP, "Registration")
    row = Registration.objects.create(
        class_offering_id=offering_id,
        first_name="Sam",
        last_name="Smith",
        email=email,
        status=status,
        amount_paid_cents=amount_paid_cents,
        stripe_payment_id=stripe_payment_id,
        self_serve_token=f"token-{status}-{minutes_ago}-{email}",
        order_number=f"PL-{minutes_ago:04d}-{status[:2].upper()}",
    )
    Registration.objects.filter(pk=row.pk).update(registered_at=timezone.now() - timedelta(minutes=minutes_ago))
    return row.pk


def _row(apps: Any, pk: int):
    return apps.get_model(_APP, "Registration").objects.get(pk=pk)


@pytest.mark.django_db(transaction=True)
def describe_migration_0065_cancel_duplicate_signups():
    def it_cancels_every_duplicate_beyond_the_first():
        try:
            offering = ClassOfferingFactory()
            apps = _migrate(_BEFORE)
            first = _make_registration(apps, offering_id=offering.pk, status="pending", minutes_ago=30)
            second = _make_registration(apps, offering_id=offering.pk, status="pending", minutes_ago=20)
            third = _make_registration(apps, offering_id=offering.pk, status="pending", minutes_ago=10)

            apps = _migrate(_AFTER)

            assert _row(apps, first).status == "pending"
            for pk in (second, third):
                row = _row(apps, pk)
                assert row.status == "cancelled"
                assert row.cancellation_reason == reason_for("pending")
                assert row.cancelled_at is not None
        finally:
            _migrate(_HEAD)

    def it_leaves_a_lone_registration_alone():
        try:
            offering = ClassOfferingFactory()
            apps = _migrate(_BEFORE)
            only = _make_registration(apps, offering_id=offering.pk, status="pending", minutes_ago=30)

            apps = _migrate(_AFTER)

            row = _row(apps, only)
            assert row.status == "pending"
            assert row.cancellation_reason == ""
            assert row.cancelled_at is None
        finally:
            _migrate(_HEAD)

    def it_leaves_an_already_cancelled_row_out_of_the_group():
        try:
            offering = ClassOfferingFactory()
            apps = _migrate(_BEFORE)
            gone = _make_registration(apps, offering_id=offering.pk, status="cancelled", minutes_ago=30)
            live = _make_registration(apps, offering_id=offering.pk, status="pending", minutes_ago=20)

            apps = _migrate(_AFTER)

            assert _row(apps, live).status == "pending"
            assert _row(apps, gone).cancellation_reason == ""
        finally:
            _migrate(_HEAD)

    def it_keeps_the_paid_row_when_an_unpaid_one_is_older():
        try:
            offering = ClassOfferingFactory()
            apps = _migrate(_BEFORE)
            unpaid = _make_registration(apps, offering_id=offering.pk, status="pending", minutes_ago=30)
            paid = _make_registration(apps, offering_id=offering.pk, status="confirmed", minutes_ago=10)

            apps = _migrate(_AFTER)

            assert _row(apps, paid).status == "confirmed"
            assert _row(apps, unpaid).status == "cancelled"
        finally:
            _migrate(_HEAD)

    def it_leaves_the_same_email_on_a_different_class_alone():
        try:
            one = ClassOfferingFactory()
            two = ClassOfferingFactory()
            apps = _migrate(_BEFORE)
            here = _make_registration(apps, offering_id=one.pk, status="pending", minutes_ago=30)
            there = _make_registration(apps, offering_id=two.pk, status="pending", minutes_ago=20)

            apps = _migrate(_AFTER)

            assert _row(apps, here).status == "pending"
            assert _row(apps, there).status == "pending"
        finally:
            _migrate(_HEAD)

    def describe_the_reverse():
        def it_restores_every_row_the_migration_cancelled():
            try:
                offering = ClassOfferingFactory()
                apps = _migrate(_BEFORE)
                kept = _make_registration(apps, offering_id=offering.pk, status="pending", minutes_ago=30)
                cancelled = _make_registration(apps, offering_id=offering.pk, status="pending", minutes_ago=20)
                _migrate(_AFTER)

                apps = _migrate(_BEFORE)

                restored = _row(apps, cancelled)
                assert restored.status == "pending"
                assert restored.cancellation_reason == ""
                assert restored.cancelled_at is None
                assert _row(apps, kept).status == "pending"
            finally:
                _migrate(_HEAD)

        def it_restores_a_waitlisted_row_as_waitlisted():
            try:
                offering = ClassOfferingFactory()
                apps = _migrate(_BEFORE)
                _make_registration(apps, offering_id=offering.pk, status="confirmed", minutes_ago=30)
                queued = _make_registration(apps, offering_id=offering.pk, status="waitlisted", minutes_ago=20)
                apps = _migrate(_AFTER)
                assert _row(apps, queued).cancellation_reason == reason_for("waitlisted")

                apps = _migrate(_BEFORE)

                assert _row(apps, queued).status == "waitlisted"
            finally:
                _migrate(_HEAD)

        def it_leaves_a_row_an_admin_cancelled_where_it_is():
            try:
                offering = ClassOfferingFactory()
                apps = _migrate(_BEFORE)
                by_hand = _make_registration(apps, offering_id=offering.pk, status="pending", minutes_ago=30)
                Registration = apps.get_model(_APP, "Registration")
                Registration.objects.filter(pk=by_hand).update(
                    status="cancelled", cancellation_reason="Registrant emailed us to drop."
                )
                _migrate(_AFTER)

                apps = _migrate(_BEFORE)

                row = _row(apps, by_hand)
                assert row.status == "cancelled"
                assert row.cancellation_reason == "Registrant emailed us to drop."
                assert MARKER not in row.cancellation_reason
            finally:
                _migrate(_HEAD)

    def describe_a_duplicate_that_carries_money():
        """Two paid rows is what the worst double-click produced: two sessions, both charged.

        Cancelling one of those silently would strand a real payment with no refund and
        no email, because the raw ``.update()`` is chosen precisely so nothing fires.
        """

        def it_refuses_to_run_and_names_the_rows():
            try:
                offering = ClassOfferingFactory()
                apps = _migrate(_BEFORE)
                kept = _make_registration(
                    apps, offering_id=offering.pk, status="confirmed", minutes_ago=30, amount_paid_cents=10000
                )
                paid = _make_registration(
                    apps,
                    offering_id=offering.pk,
                    status="confirmed",
                    minutes_ago=20,
                    amount_paid_cents=10000,
                    stripe_payment_id="pi_BBB",
                )

                with pytest.raises(RuntimeError, match="carries a recorded payment"):
                    _migrate(_AFTER)

                assert _row(apps, paid).status == "confirmed"  # untouched, still refundable
                assert _row(apps, kept).status == "confirmed"
            finally:
                _row(apps, paid).delete()
                _migrate(_HEAD)

        def it_names_the_offending_row_in_the_message():
            """A Stripe payment id on the losing row is the signal, whatever its status."""
            try:
                offering = ClassOfferingFactory()
                apps = _migrate(_BEFORE)
                _make_registration(apps, offering_id=offering.pk, status="pending", minutes_ago=30)
                paid = _make_registration(
                    apps,
                    offering_id=offering.pk,
                    status="pending",
                    minutes_ago=20,
                    amount_paid_cents=4500,
                    stripe_payment_id="pi_CCC",
                )

                with pytest.raises(RuntimeError) as caught:
                    _migrate(_AFTER)

                assert f"pk={paid}" in str(caught.value)
                assert "payment=pi_CCC" in str(caught.value)
                assert "Decide each one by hand" in str(caught.value)
            finally:
                _row(apps, paid).delete()
                _migrate(_HEAD)

        def it_still_cancels_an_unpaid_duplicate_alongside_a_paid_keeper():
            """Only a row that would LOSE its seat and holds money stops the migration."""
            try:
                offering = ClassOfferingFactory()
                apps = _migrate(_BEFORE)
                keeper = _make_registration(
                    apps, offering_id=offering.pk, status="confirmed", minutes_ago=30, amount_paid_cents=10000
                )
                unpaid = _make_registration(apps, offering_id=offering.pk, status="pending", minutes_ago=20)

                apps = _migrate(_AFTER)

                assert _row(apps, keeper).status == "confirmed"
                assert _row(apps, unpaid).status == "cancelled"
            finally:
                _migrate(_HEAD)

    def describe_the_provisional_amount_on_an_unpaid_row():
        """The exact shape production carries, and the reason this migration nearly failed a deploy.

        ``classes/views.py`` stamps ``amount_paid_cents = final_price`` when it mints the
        Checkout Session, before anybody has paid. So an unpaid double-click on a paid
        class looks like two rows holding money. Reading that as a payment stops the
        migration on every deploy, on rows that owe nothing.
        """

        def it_cancels_an_unpaid_duplicate_that_carries_the_provisional_amount():
            try:
                offering = ClassOfferingFactory()
                apps = _migrate(_BEFORE)
                # pk 16 / 17 and 59 / 60 on production: pending, non-zero amount, no payment.
                kept = _make_registration(
                    apps, offering_id=offering.pk, status="pending", minutes_ago=30, amount_paid_cents=7200
                )
                dupe = _make_registration(
                    apps, offering_id=offering.pk, status="pending", minutes_ago=20, amount_paid_cents=7200
                )

                apps = _migrate(_AFTER)  # must not raise

                assert _row(apps, kept).status == "pending"
                assert _row(apps, dupe).status == "cancelled"
                assert _row(apps, dupe).cancellation_reason == reason_for("pending")
            finally:
                _migrate(_HEAD)

        def it_still_refuses_when_a_confirmed_duplicate_was_settled_in_cash():
            """``mark_paid`` records real money and leaves no Stripe id, only ever on a CONFIRMED row."""
            try:
                offering = ClassOfferingFactory()
                apps = _migrate(_BEFORE)
                _make_registration(
                    apps, offering_id=offering.pk, status="confirmed", minutes_ago=30, amount_paid_cents=7200
                )
                cash = _make_registration(
                    apps, offering_id=offering.pk, status="confirmed", minutes_ago=20, amount_paid_cents=7200
                )

                with pytest.raises(RuntimeError, match="carries a recorded payment"):
                    _migrate(_AFTER)

                assert _row(apps, cash).status == "confirmed"
            finally:
                _row(apps, cash).delete()
                _migrate(_HEAD)
