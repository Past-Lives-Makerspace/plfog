"""BDD specs for the abandoned class-hold sweep, its age threshold, and the release/confirm moves.

The sweep is about time AND state, so the fixtures here vary both on purpose rather than
sharing one age and one session shape: a row seconds old, one just under the threshold, one
just over, and — at the same age — sessions Stripe reports as open, complete-but-unpaid,
expired, paid, and missing entirely. Seat math is asserted on every path, because "the seat
came back" is the only thing any of this is for.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core import mail
from django.utils import timezone

from classes.exceptions import RegistrationStateError
from classes.factories import ClassOfferingFactory, RegistrationFactory, UserFactory
from classes.models import (
    ABANDONED_HOLD_SWEEP_AGE,
    EXPIRED_HOLD_CANCEL_REASON,
    ClassOffering,
    CmsActivity,
    Registration,
    RegistrationAnswer,
    RegistrationQuestion,
    Waiver,
)

pytestmark = pytest.mark.django_db

RETRIEVE = "billing.stripe_utils.retrieve_checkout_session"


def _offering(capacity: int, **overrides) -> ClassOffering:
    return ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, capacity=capacity, **overrides)


def _hold(offering, *, age: timedelta, **overrides) -> Registration:
    """A PENDING signup whose ``registered_at`` is backdated by ``age``.

    ``registered_at`` is ``auto_now_add``, so the age has to be written after the insert —
    which is also the honest simulation: a resumed signup keeps its original timestamp.
    """
    overrides.setdefault("status", Registration.Status.PENDING)
    overrides.setdefault("stripe_session_id", "cs_hold_1")
    overrides.setdefault("amount_paid_cents", 9000)
    registration = RegistrationFactory(class_offering=offering, **overrides)
    Registration.objects.filter(pk=registration.pk).update(registered_at=timezone.now() - age)
    registration.refresh_from_db()
    return registration


def _session(
    *,
    session_id: str = "cs_hold_1",
    status: str = "expired",
    payment_status: str = "unpaid",
    payment_intent: str = "",
    amount_total: int = 9000,
) -> dict:
    return {
        "id": session_id,
        "url": "https://checkout.stripe.test/c/pay/cs_hold_1",
        "status": status,
        "payment_status": payment_status,
        "payment_intent": payment_intent,
        "amount_total": amount_total,
    }


def describe_abandoned_holds_queryset():
    def it_ignores_a_signup_that_started_seconds_ago():
        offering = _offering(6)
        _hold(offering, age=timedelta(seconds=20))
        assert list(Registration.objects.abandoned_holds()) == []

    def it_ignores_a_signup_just_under_the_sweep_age():
        offering = _offering(6)
        _hold(offering, age=ABANDONED_HOLD_SWEEP_AGE - timedelta(minutes=1))
        assert list(Registration.objects.abandoned_holds()) == []

    def it_picks_up_a_signup_just_over_the_sweep_age():
        offering = _offering(6)
        stale = _hold(offering, age=ABANDONED_HOLD_SWEEP_AGE + timedelta(minutes=1))
        assert [row.pk for row in Registration.objects.abandoned_holds()] == [stale.pk]

    def it_takes_its_clock_from_the_caller():
        offering = _offering(6)
        young = _hold(offering, age=timedelta(minutes=10))
        later = timezone.now() + timedelta(hours=3)
        assert [row.pk for row in Registration.objects.abandoned_holds(now=later)] == [young.pk]

    def it_never_looks_at_a_row_that_is_not_pending():
        offering = _offering(6)
        old = ABANDONED_HOLD_SWEEP_AGE + timedelta(days=4)
        for status in (
            Registration.Status.CONFIRMED,
            Registration.Status.WAITLISTED,
            Registration.Status.CANCELLED,
            Registration.Status.REFUNDED,
        ):
            _hold(offering, age=old, status=status, stripe_session_id=f"cs_{status}")
        assert list(Registration.objects.abandoned_holds()) == []

    def it_stays_strictly_longer_than_the_checkout_session_lifetime():
        # The reason the sweep can never race the resume path #419 built: a session Stripe
        # still calls live has not yet reached the age at which this sweep looks at it.
        from billing.stripe_utils import CLASS_CHECKOUT_SESSION_LIFETIME

        assert ABANDONED_HOLD_SWEEP_AGE > CLASS_CHECKOUT_SESSION_LIFETIME


def describe_release_abandoned_holds():
    def describe_when_the_session_has_expired():
        def it_cancels_the_row_and_gives_the_seat_back():
            offering = _offering(3)
            stale = _hold(offering, age=timedelta(hours=9))
            assert offering.spots_remaining == 2

            with patch(RETRIEVE, return_value=_session(status="expired")) as retrieve:
                assert Registration.objects.release_abandoned_holds() == (1, 0)

            retrieve.assert_called_once_with(session_id="cs_hold_1")
            stale.refresh_from_db()
            assert stale.status == Registration.Status.CANCELLED
            assert stale.cancellation_reason == EXPIRED_HOLD_CANCEL_REASON
            assert stale.cancelled_at is not None
            assert offering.spots_remaining == 3

        def it_keeps_the_waiver_the_answers_and_the_audit_trail():
            # The reason release is a cancel and not the orientation flow's delete.
            offering = _offering(4)
            stale = _hold(offering, age=timedelta(days=115))
            Waiver.objects.create(
                registration=stale,
                kind=Waiver.Kind.LIABILITY,
                waiver_text="I assume the risk.",
                signature_text="Dana Okonkwo",
            )
            question = RegistrationQuestion.objects.create(prompt="Have you used a lathe before?")
            RegistrationAnswer.objects.create(registration=stale, question=question, answer_text="Twice.")

            with patch(RETRIEVE, return_value=_session()):
                Registration.objects.release_abandoned_holds()

            stale.refresh_from_db()
            assert stale.status == Registration.Status.CANCELLED
            assert stale.waivers.get().signature_text == "Dana Okonkwo"
            assert stale.custom_answers.get().answer_text == "Twice."
            assert CmsActivity.objects.filter(registration=stale, kind=CmsActivity.Kind.REGISTRATION_CANCELLED).exists()

        def it_frees_the_seat_for_the_next_person_on_the_waitlist():
            offering = _offering(1)
            stale = _hold(offering, age=timedelta(hours=5), email="abandoner@example.com")
            waiting = RegistrationFactory(
                class_offering=offering,
                status=Registration.Status.WAITLISTED,
                email="waiting@example.com",
            )
            assert offering.spots_remaining == 0
            mail.outbox.clear()

            with patch(RETRIEVE, return_value=_session()):
                Registration.objects.release_abandoned_holds()

            waiting.refresh_from_db()
            stale.refresh_from_db()
            assert stale.status == Registration.Status.CANCELLED
            assert waiting.waitlist_notified_at is not None
            assert offering.spots_remaining == 1
            assert any("waiting@example.com" in message.to for message in mail.outbox)

        def it_lets_the_released_registrant_sign_up_again_for_the_same_class():
            offering = _offering(2)
            stale = _hold(offering, age=timedelta(hours=4), email="second.try@example.com")

            with patch(RETRIEVE, return_value=_session()):
                Registration.objects.release_abandoned_holds()

            # uq_registration_seat_email stops counting a CANCELLED row, so the same
            # address can hold a seat again.
            fresh = RegistrationFactory(
                class_offering=offering,
                status=Registration.Status.PENDING,
                email="second.try@example.com",
            )
            stale.refresh_from_db()
            assert fresh.pk != stale.pk
            assert offering.spots_remaining == 1

    def describe_when_the_session_is_still_live():
        def it_leaves_an_open_session_alone_however_old_the_row_is():
            # The #419 resume case: the row is three days old, the session on it is minutes old.
            offering = _offering(5)
            resumed = _hold(offering, age=timedelta(days=3), stripe_session_id="cs_reminted")

            with patch(RETRIEVE, return_value=_session(session_id="cs_reminted", status="open")):
                assert Registration.objects.release_abandoned_holds() == (0, 0)

            resumed.refresh_from_db()
            assert resumed.status == Registration.Status.PENDING
            assert offering.spots_remaining == 4

        def it_leaves_a_delayed_payment_still_settling_alone():
            # A bank debit completes the session immediately and settles days later.
            offering = _offering(5)
            settling = _hold(offering, age=timedelta(days=2))

            with patch(RETRIEVE, return_value=_session(status="complete", payment_status="unpaid")):
                assert Registration.objects.release_abandoned_holds() == (0, 0)

            settling.refresh_from_db()
            assert settling.status == Registration.Status.PENDING
            assert offering.spots_remaining == 4

    def describe_when_stripe_says_the_session_was_paid():
        def it_confirms_the_row_instead_of_cancelling_it():
            offering = _offering(4)
            lost = _hold(offering, age=timedelta(hours=8), email="paid.but.quiet@example.com")
            mail.outbox.clear()

            with patch(
                RETRIEVE,
                return_value=_session(status="complete", payment_status="paid", payment_intent="pi_lost_1"),
            ):
                assert Registration.objects.release_abandoned_holds() == (0, 1)

            lost.refresh_from_db()
            assert lost.status == Registration.Status.CONFIRMED
            assert lost.stripe_payment_id == "pi_lost_1"
            assert lost.amount_paid_cents == 9000
            assert offering.spots_remaining == 3
            assert any("paid.but.quiet@example.com" in message.to for message in mail.outbox)

        def it_counts_nothing_when_the_webhook_already_won_the_race():
            offering = _offering(4)
            already = _hold(
                offering,
                age=timedelta(hours=8),
                status=Registration.Status.CONFIRMED,
                stripe_payment_id="pi_already",
            )
            # CONFIRMED rows are not candidates at all, so the sweep never asks Stripe.
            with patch(RETRIEVE) as retrieve:
                assert Registration.objects.release_abandoned_holds() == (0, 0)
            retrieve.assert_not_called()
            already.refresh_from_db()
            assert already.status == Registration.Status.CONFIRMED

        def it_counts_no_recovery_when_the_seat_went_to_someone_else_mid_sweep():
            # The expiry webhook lands while the sweep is asking Stripe, and the registrant
            # re-books on the freed seat. The payment can no longer be applied, so it is
            # orphaned for a human rather than counted as a rescue.
            offering = _offering(1)
            abandoned = _hold(offering, age=timedelta(hours=8), email="same.person@example.com")

            def _lose_the_seat_mid_flight(*, session_id):
                Registration.objects.filter(pk=abandoned.pk).update(status=Registration.Status.CANCELLED)
                RegistrationFactory(
                    class_offering=offering,
                    status=Registration.Status.CONFIRMED,
                    email="same.person@example.com",
                )
                return _session(status="complete", payment_status="paid", payment_intent="pi_taken")

            with patch(RETRIEVE, side_effect=_lose_the_seat_mid_flight):
                assert Registration.objects.release_abandoned_holds() == (0, 0)

            abandoned.refresh_from_db()
            assert abandoned.status == Registration.Status.CANCELLED
            assert abandoned.stripe_payment_id == "pi_taken"
            assert CmsActivity.objects.filter(registration=abandoned, kind=CmsActivity.Kind.DUPLICATE_PAYMENT).exists()
            assert offering.spots_remaining == 0

    def describe_when_the_row_moves_under_the_sweeps_feet():
        def it_counts_no_release_it_did_not_make():
            # A webhook confirming the seat between the queryset read and the release.
            offering = _offering(3)
            racing = _hold(offering, age=timedelta(hours=8))

            def _confirm_mid_flight(*, session_id):
                Registration.objects.filter(pk=racing.pk).update(status=Registration.Status.CONFIRMED)
                return _session(status="expired")

            with patch(RETRIEVE, side_effect=_confirm_mid_flight):
                assert Registration.objects.release_abandoned_holds() == (0, 0)

            racing.refresh_from_db()
            assert racing.status == Registration.Status.CONFIRMED
            assert offering.spots_remaining == 2

    def describe_when_no_session_was_ever_stored():
        def it_releases_the_seat_without_asking_stripe():
            offering = _offering(2)
            stranded = _hold(offering, age=timedelta(hours=30), stripe_session_id="")

            with patch(RETRIEVE) as retrieve:
                assert Registration.objects.release_abandoned_holds() == (1, 0)

            retrieve.assert_not_called()
            stranded.refresh_from_db()
            assert stranded.status == Registration.Status.CANCELLED
            assert offering.spots_remaining == 2

    def describe_when_stripe_cannot_be_reached():
        def it_keeps_the_hold_and_retries_next_tick():
            offering = _offering(3)
            unknown = _hold(offering, age=timedelta(hours=6))

            with patch(RETRIEVE, side_effect=RuntimeError("stripe down")):
                assert Registration.objects.release_abandoned_holds() == (0, 0)

            unknown.refresh_from_db()
            assert unknown.status == Registration.Status.PENDING
            assert offering.spots_remaining == 2

            with patch(RETRIEVE, return_value=_session()):
                assert Registration.objects.release_abandoned_holds() == (1, 0)
            unknown.refresh_from_db()
            assert unknown.status == Registration.Status.CANCELLED
            assert offering.spots_remaining == 3

    def describe_with_the_whole_matrix_in_one_class():
        def it_releases_only_the_dead_ones_and_counts_them():
            offering = _offering(8)
            expired = _hold(offering, age=timedelta(hours=7), stripe_session_id="cs_expired")
            sessionless = _hold(offering, age=timedelta(hours=7), stripe_session_id="")
            open_session = _hold(offering, age=timedelta(hours=7), stripe_session_id="cs_open")
            settling = _hold(offering, age=timedelta(hours=7), stripe_session_id="cs_settling")
            paid = _hold(offering, age=timedelta(hours=7), stripe_session_id="cs_paid")
            young = _hold(offering, age=timedelta(minutes=5), stripe_session_id="cs_young")
            waitlisted = RegistrationFactory(
                class_offering=offering, status=Registration.Status.WAITLISTED, email="queued@example.com"
            )
            # 6 PENDING seats taken, the waitlist row takes none.
            assert offering.spots_remaining == 2

            answers = {
                "cs_expired": _session(session_id="cs_expired", status="expired"),
                "cs_open": _session(session_id="cs_open", status="open"),
                "cs_settling": _session(session_id="cs_settling", status="complete", payment_status="unpaid"),
                "cs_paid": _session(
                    session_id="cs_paid", status="complete", payment_status="paid", payment_intent="pi_matrix"
                ),
            }
            with patch(RETRIEVE, side_effect=lambda *, session_id: answers[session_id]):
                assert Registration.objects.release_abandoned_holds() == (2, 1)

            for row in (expired, sessionless, open_session, settling, paid, young, waitlisted):
                row.refresh_from_db()
            assert expired.status == Registration.Status.CANCELLED
            assert sessionless.status == Registration.Status.CANCELLED
            assert open_session.status == Registration.Status.PENDING
            assert settling.status == Registration.Status.PENDING
            assert paid.status == Registration.Status.CONFIRMED
            assert young.status == Registration.Status.PENDING
            assert waitlisted.status == Registration.Status.WAITLISTED
            # 2 seats came back; 3 holds and 1 confirmed seat still consume: 8 - 4.
            assert offering.spots_remaining == 4


def describe_release_hold():
    def it_reports_false_and_changes_nothing_when_the_row_already_moved():
        offering = _offering(3)
        confirmed = _hold(offering, age=timedelta(hours=4), status=Registration.Status.CONFIRMED)

        assert confirmed.release_hold(reason=EXPIRED_HOLD_CANCEL_REASON) is False

        confirmed.refresh_from_db()
        assert confirmed.status == Registration.Status.CONFIRMED
        assert offering.spots_remaining == 2

    def it_is_idempotent_on_a_second_call():
        offering = _offering(3)
        stale = _hold(offering, age=timedelta(hours=4))

        assert stale.release_hold(reason=EXPIRED_HOLD_CANCEL_REASON) is True
        assert stale.release_hold(reason=EXPIRED_HOLD_CANCEL_REASON) is False
        assert offering.spots_remaining == 3


def describe_confirm_pending_payment():
    def it_confirms_with_the_balance_still_owed():
        offering = _offering(4)
        stuck = _hold(offering, age=timedelta(hours=3), amount_paid_cents=7500)
        actor = UserFactory(username="desk-staff")

        stuck.confirm_pending_payment(actor=actor)

        stuck.refresh_from_db()
        assert stuck.status == Registration.Status.CONFIRMED
        assert stuck.confirmed_at is not None
        assert stuck.payment_due_cents == 7500
        # The provisional stamp was the price, never money: it must not read as paid.
        assert stuck.amount_paid_cents == 0
        assert stuck.balance_due_cents == 7500
        assert stuck.is_unpaid is True
        # The seat was already consumed as PENDING, so confirming takes no new seat.
        assert offering.spots_remaining == 3

    def it_records_the_staff_member_who_did_it():
        offering = _offering(4)
        stuck = _hold(offering, age=timedelta(hours=3))
        actor = UserFactory(username="named-staff")

        stuck.confirm_pending_payment(actor=actor)

        row = CmsActivity.objects.filter(registration=stuck, kind=CmsActivity.Kind.REGISTRATION_CONFIRMED).first()
        assert row is not None
        assert row.actor == actor

    def it_keeps_a_price_that_was_already_stamped():
        offering = _offering(4)
        stuck = _hold(offering, age=timedelta(hours=3), payment_due_cents=5000, amount_paid_cents=9000)

        stuck.confirm_pending_payment(actor=None)

        stuck.refresh_from_db()
        assert stuck.payment_due_cents == 5000
        assert stuck.amount_paid_cents == 0

    def it_leaves_a_free_signup_with_nothing_owed():
        offering = _offering(4)
        stuck = _hold(offering, age=timedelta(hours=3), amount_paid_cents=0)

        stuck.confirm_pending_payment(actor=None)

        stuck.refresh_from_db()
        assert stuck.status == Registration.Status.CONFIRMED
        assert stuck.balance_due_cents == 0
        assert stuck.is_unpaid is False

    def describe_when_the_row_is_not_pending():
        def it_refuses_a_confirmed_row():
            offering = _offering(4)
            done = _hold(offering, age=timedelta(hours=3), status=Registration.Status.CONFIRMED)
            with pytest.raises(RegistrationStateError, match="still waiting on payment"):
                done.confirm_pending_payment(actor=None)

        def it_refuses_a_row_the_sweep_already_cancelled():
            offering = _offering(4)
            gone = _hold(offering, age=timedelta(hours=3), status=Registration.Status.CANCELLED)
            with pytest.raises(RegistrationStateError, match="still waiting on payment"):
                gone.confirm_pending_payment(actor=None)
            assert offering.spots_remaining == 4

        def it_refuses_a_waitlisted_row():
            offering = _offering(4)
            queued = _hold(offering, age=timedelta(hours=3), status=Registration.Status.WAITLISTED)
            with pytest.raises(RegistrationStateError, match="still waiting on payment"):
                queued.confirm_pending_payment(actor=None)
