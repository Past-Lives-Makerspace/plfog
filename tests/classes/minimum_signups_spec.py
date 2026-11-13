"""BDD specs for a class's minimum signups (#1182).

An instructor sets a minimum beside Capacity; a day before registration closes the instructors
of a class still under it are warned; when registration closes still under it, the class
cancels itself and every paid signup is refunded in full. Page assertions anchor on markup
(a data attribute, a class name), never on copy the changelog could carry (STANDARDS.md section 8).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import count
from typing import Any
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from billing.exceptions import AlreadyRefundedError, RefundError
from billing.models import PaymentRefund
from classes.composer import step_for_field
from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.forms import (
    MINIMUM_SIGNUPS_LOCKED_NOTE,
    MINIMUM_SIGNUPS_NEEDS_CUTOFF_MESSAGE,
    ClassOfferingForm,
    TeachClassOfferingForm,
    TeachPublishedClassForm,
)
from classes.models import MINIMUM_SIGNUPS_CANCEL_REASON, ClassOffering, Registration
from classes.spec.live_edit import live_facts
from classes.tasks import (
    build_minimum_warning_occurrence,
    cancel_classes_under_minimum,
    enforce_class_minimums,
    minimum_warning_occurrences,
    refund_for_minimum_cancel,
)
from core.events.scheduler import run_due
from core.models import EventDelivery
from core.scheduled_jobs import JOBS_BY_KEY, Cadence
from core.services.email_queue import timed_email_sources
from membership.models import Member

pytestmark = pytest.mark.django_db

_serial = count(1)
WINDOW = timedelta(minutes=15)


def _instructor() -> Member:
    n = next(_serial)
    return InstructorFactory(
        user=UserFactory(username=f"min-teacher-{n}@example.com", email=f"min-teacher-{n}@example.com"),
        instructor_slug=f"min-teacher-{n}",
    )


def _paid(offering: ClassOffering, **overrides: Any) -> Registration:
    n = next(_serial)
    defaults: dict[str, Any] = {
        "class_offering": offering,
        "status": Registration.Status.CONFIRMED,
        "amount_paid_cents": 5000,
        "stripe_payment_id": f"pi_min_{n}",
        "email": f"signup-{n}@example.com",
    }
    defaults.update(overrides)
    return RegistrationFactory(**defaults)


def _live_class(
    *,
    starts_in: timedelta,
    minimum: int | None = 4,
    cutoff: int | None = 48,
    confirmed: int = 1,
    **overrides: Any,
) -> ClassOffering:
    """A live dated class starting ``starts_in`` from now with ``confirmed`` paid signups."""
    offering = ClassOfferingFactory(
        status=ClassOffering.Status.PUBLISHED,
        minimum_signups=minimum,
        registration_cutoff_hours=cutoff,
        **overrides,
    )
    starts = timezone.now() + starts_in
    ClassSessionFactory(class_offering=offering, starts_at=starts, ends_at=starts + timedelta(hours=2))
    for _ in range(confirmed):
        _paid(offering)
    return offering


def _closed_class(**kwargs: Any) -> ClassOffering:
    """Registration closed 8 hours ago (48h cutoff, first session 40h out), so the sweep decides it."""
    return _live_class(starts_in=timedelta(hours=40), **kwargs)


def _composer_data(**overrides: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title": "Forge Basics",
        "category": str(CategoryFactory().pk),
        "instructors": [str(InstructorFactory().pk)],
        "description": "Hands-on intro.",
        "price_cents": "100.00",
        "capacity": "6",
        "scheduling_model": ClassOffering.SchedulingModel.FIXED,
        "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
        "registration_cutoff_enabled": "on",
        "registration_cutoff_hours": "48",
    }
    data.update(overrides)
    return data


def _live_data(offering: ClassOffering, **overrides: str) -> dict[str, str]:
    return {**live_facts(offering), "description": "A hands-on class.", **overrides}


def _stripe_ok(**kwargs: Any) -> dict[str, Any]:
    return {"id": f"re_min_{next(_serial)}", "status": "succeeded", "amount": kwargs["amount_cents"]}


@pytest.fixture(autouse=True)
def mock_create() -> Any:
    """Stripe's refund call, answering each refund succeeded."""
    with patch("billing.stripe_utils.create_refund", side_effect=_stripe_ok) as mocked:
        yield mocked


@pytest.fixture
def sweep(django_capture_on_commit_callbacks: Any) -> Any:
    """Run the cancel sweep and then its queued refund tasks, as the worker would after the commit."""

    def run(now: datetime) -> int:
        with django_capture_on_commit_callbacks(execute=True):
            return cancel_classes_under_minimum(now)

    return run


@pytest.fixture(params=[TeachClassOfferingForm, ClassOfferingForm], ids=["teach", "admin"])
def composer_form(request: pytest.FixtureRequest) -> type[TeachClassOfferingForm] | type[ClassOfferingForm]:
    return request.param


def describe_setting_the_minimum():
    """AC1: Minimum signups beside Capacity when building or editing a class, live ones too."""

    def it_sits_on_step_3_with_capacity():
        assert step_for_field("minimum_signups") == step_for_field("capacity") == 3

    def it_saves_from_the_composer(composer_form):
        form = composer_form(data=_composer_data(minimum_signups="3"))
        assert form.is_valid(), form.errors
        assert form.save().minimum_signups == 3

    def it_stores_blank_as_no_minimum(composer_form):
        form = composer_form(data=_composer_data(minimum_signups=""))
        assert form.is_valid(), form.errors
        assert form.save().minimum_signups is None

    def it_stores_zero_as_no_minimum(composer_form):
        form = composer_form(data=_composer_data(minimum_signups="0"))
        assert form.is_valid(), form.errors
        assert form.save().minimum_signups is None

    def it_stores_none_for_a_flexible_class(composer_form):
        data = _composer_data(minimum_signups="3", scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        form = composer_form(data=data)
        assert form.is_valid(), form.errors
        assert form.save().minimum_signups is None

    def it_saves_on_a_live_class():
        offering = _live_class(starts_in=timedelta(days=10), minimum=None)
        form = TeachPublishedClassForm(data=_live_data(offering, minimum_signups="2"), instance=offering)
        assert form.is_valid(), form.errors
        form.save()
        offering.refresh_from_db()
        assert offering.minimum_signups == 2

    def it_has_no_box_on_a_live_flexible_class():
        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE
        )
        assert "minimum_signups" not in TeachPublishedClassForm(instance=offering).fields

    def it_renders_beside_capacity_in_the_composer(client: Client):
        instructor = _instructor()
        client.force_login(instructor.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        pair = html[html.index('<div class="pl-seats-pair">') : html.index('data-seats-block="flexible"')]
        assert pair.index('name="capacity"') < pair.index('name="minimum_signups"')

    def it_renders_beside_capacity_on_the_live_edit_page(client: Client):
        instructor = _instructor()
        offering = _live_class(starts_in=timedelta(days=10), instructor=instructor)
        client.force_login(instructor.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        pair = html[html.index('<div class="pl-seats-pair">') :]
        assert pair.index('name="capacity"') < pair.index('name="minimum_signups"')
        assert html.count('name="minimum_signups"') == 1


def describe_refusing_a_minimum():
    """AC2: never over Capacity, needs the cutoff on, and locked once registration has closed."""

    def it_refuses_more_than_the_capacity_on_the_composer(composer_form):
        form = composer_form(data=_composer_data(capacity="5", minimum_signups="6"))
        assert not form.is_valid()
        assert form.errors["minimum_signups"] == ["The minimum cannot be more than the class's 5 seats."]

    def it_accepts_a_minimum_equal_to_the_capacity(composer_form):
        form = composer_form(data=_composer_data(capacity="5", minimum_signups="5"))
        assert form.is_valid(), form.errors

    def it_refuses_a_minimum_with_the_cutoff_off_on_the_composer(composer_form):
        data = _composer_data(minimum_signups="3")
        del data["registration_cutoff_enabled"]
        form = composer_form(data=data)
        assert not form.is_valid()
        assert form.errors["minimum_signups"] == [MINIMUM_SIGNUPS_NEEDS_CUTOFF_MESSAGE]

    def it_leaves_a_bad_hours_box_to_its_own_error(composer_form):
        form = composer_form(data=_composer_data(minimum_signups="3", registration_cutoff_hours="0"))
        assert not form.is_valid()
        assert "registration_cutoff_hours" in form.errors
        assert "minimum_signups" not in form.errors

    def it_refuses_more_than_the_capacity_on_a_live_class():
        offering = _live_class(starts_in=timedelta(days=10), minimum=None)
        form = TeachPublishedClassForm(data=_live_data(offering, capacity="3", minimum_signups="4"), instance=offering)
        assert not form.is_valid()
        assert form.errors["minimum_signups"] == ["The minimum cannot be more than the class's 3 seats."]

    def it_refuses_a_minimum_on_a_live_class_with_the_cutoff_off():
        offering = _live_class(starts_in=timedelta(days=10), minimum=None, cutoff=None)
        form = TeachPublishedClassForm(data=_live_data(offering, minimum_signups="2"), instance=offering)
        assert not form.is_valid()
        assert form.errors["minimum_signups"] == [MINIMUM_SIGNUPS_NEEDS_CUTOFF_MESSAGE]

    def describe_once_registration_has_closed():
        def it_disables_the_box_with_the_locked_note():
            field = TeachPublishedClassForm(instance=_closed_class(minimum=2)).fields["minimum_signups"]
            assert field.disabled is True
            assert field.help_text == MINIMUM_SIGNUPS_LOCKED_NOTE

        def it_keeps_the_saved_minimum_whatever_is_posted():
            offering = _closed_class(minimum=2, confirmed=3)
            form = TeachPublishedClassForm(data=_live_data(offering, minimum_signups="5"), instance=offering)
            assert form.is_valid(), form.errors
            form.save()
            offering.refresh_from_db()
            assert offering.minimum_signups == 2

        def it_shows_the_note_on_the_live_edit_page(client: Client):
            instructor = _instructor()
            offering = _closed_class(minimum=2, instructor=instructor)
            client.force_login(instructor.user)
            html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
            box = html[html.index('name="minimum_signups"') - 300 : html.index('name="minimum_signups"') + 300]
            assert "disabled" in box
            assert MINIMUM_SIGNUPS_LOCKED_NOTE in html

        def it_leaves_the_box_open_before_the_close():
            offering = _live_class(starts_in=timedelta(days=10), minimum=2)
            assert TeachPublishedClassForm(instance=offering).fields["minimum_signups"].disabled is False

        def it_never_locks_a_draft():
            offering = _closed_class(minimum=2)
            offering.status = ClassOffering.Status.DRAFT
            assert offering.minimum_signups_locked is False


def describe_the_warning():
    """AC3: a day before registration closes, each instructor of a class under its minimum gets one email."""

    def _warned_class(**kwargs: Any) -> ClassOffering:
        # Registration closes 24h05m from now (48h cutoff), so the warning fires 5 minutes in.
        return _live_class(starts_in=timedelta(hours=72, minutes=5), **kwargs)

    def it_emails_each_instructor_once_with_the_count_minimum_and_time():
        first, second = _instructor(), _instructor()
        offering = _warned_class(minimum=4, confirmed=1, instructors=[first, second])
        mail.outbox.clear()
        now = timezone.now()
        assert run_due(minimum_warning_occurrences(now), now=now, window=WINDOW) == 1
        warnings = [m for m in mail.outbox if offering.title in m.subject and "more signups" in m.subject]
        assert sorted(address for m in warnings for address in m.to) == sorted([first.user.email, second.user.email])
        body = warnings[0].body
        assert "has 1 confirmed signups" in body
        assert "It needs 4 to run, so 3 more" in body
        decided = offering.registration_closes_at
        assert decided is not None
        assert timezone.localtime(decided).strftime("%B %-d at %-I:%M %p") in body

    def it_sends_nothing_more_on_a_later_tick():
        _warned_class(minimum=4, confirmed=1)
        now = timezone.now()
        run_due(minimum_warning_occurrences(now), now=now, window=WINDOW)
        assert run_due(minimum_warning_occurrences(now), now=now, window=WINDOW) == 0

    def it_skips_a_class_at_its_minimum():
        _warned_class(minimum=2, confirmed=2)
        assert list(minimum_warning_occurrences(timezone.now())) == []

    def it_skips_a_class_closing_later_than_a_day_out():
        _live_class(starts_in=timedelta(hours=80), minimum=4, confirmed=1)
        assert list(minimum_warning_occurrences(timezone.now())) == []

    def it_refuses_to_build_a_warning_without_a_minimum():
        offering = ClassOfferingFactory(minimum_signups=None)
        offering.minimum_decided_at = timezone.now()  # type: ignore[attr-defined]
        offering.confirmed_signups = 0  # type: ignore[attr-defined]
        with pytest.raises(ValueError, match="no minimum"):
            build_minimum_warning_occurrence(offering)

    def it_is_listed_with_the_timed_emails():
        from classes.tasks import minimum_warning_occurrences as source

        assert source in [entry for entry, _ in timed_email_sources()]


def describe_cancelling_at_the_close():
    """AC4 and AC5: still under at the close, the class cancels itself and every paid signup is refunded."""

    def it_cancels_through_cancel_with_the_reason_and_tells_registrants(mock_create, sweep):
        offering = _closed_class(minimum=4, confirmed=2)
        emails = sorted(offering.registrations.values_list("email", flat=True))
        mail.outbox.clear()
        assert sweep(timezone.now()) == 1
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.CANCELLED
        assert offering.cancellation_reason == MINIMUM_SIGNUPS_CANCEL_REASON
        assert offering.cancelled_by is None
        assert EventDelivery.objects.filter(
            event_key="class_cancelled", period=f"offering:{offering.pk}:cancelled"
        ).exists()
        told = sorted({a for m in mail.outbox if MINIMUM_SIGNUPS_CANCEL_REASON in m.body for a in m.to})
        assert told == emails

    def it_cancels_a_class_once_however_often_the_sweep_runs(mock_create, sweep):
        offering = _closed_class(minimum=4, confirmed=1)
        now = timezone.now()
        assert sweep(now) == 1
        assert sweep(now) == 0
        assert offering.cancel_for_minimum(now) is False
        assert mock_create.call_count == 1

    def it_does_not_count_a_class_another_run_took_first(mock_create, sweep):
        _closed_class(minimum=4, confirmed=1)
        with patch.object(ClassOffering, "cancel_for_minimum", return_value=False):
            assert sweep(timezone.now()) == 0

    def it_refunds_every_paid_site_signup_in_full(mock_create, sweep):
        offering = _closed_class(minimum=5, confirmed=2)
        eventbrite = _paid(offering, source=Registration.Source.EVENTBRITE, stripe_payment_id="")
        free = _paid(offering, amount_paid_cents=0, stripe_payment_id="")
        sweep(timezone.now())
        site = offering.registrations.exclude(pk__in=[eventbrite.pk, free.pk])
        for registration in site:
            refund = registration.refunds.get()
            assert (refund.status, refund.amount_cents) == (PaymentRefund.Status.SUCCEEDED, 5000)
            assert refund.reason == MINIMUM_SIGNUPS_CANCEL_REASON
            assert registration.status == Registration.Status.REFUNDED
        assert not PaymentRefund.objects.filter(registration__in=[eventbrite, free]).exists()

    def it_does_not_send_the_manual_refund_notice(mock_create, sweep):
        offering = _closed_class(minimum=4, confirmed=1)
        sweep(timezone.now())
        assert not EventDelivery.objects.filter(
            event_key="class_cancelled_admin_notice", target_ref__contains=str(offering.pk)
        ).exists()
        assert not EventDelivery.objects.filter(event_key="class_cancelled_admin_notice").exists()

    def it_alerts_billing_when_stripe_refuses_and_still_refunds_the_rest(mock_create, sweep):
        import stripe

        offering = _closed_class(minimum=5, confirmed=2)
        refused, ok = offering.registrations.order_by("pk")
        mock_create.side_effect = lambda **kw: (
            (_ for _ in ()).throw(stripe.InvalidRequestError("card closed", param=None))
            if kw["payment_intent_id"] == refused.stripe_payment_id
            else _stripe_ok(**kw)
        )
        with patch("billing.refunds._emit_refund_failed_alert") as alert:
            sweep(timezone.now())
        failed = refused.refunds.get()
        assert failed.status == PaymentRefund.Status.FAILED
        alert.assert_called_once_with(failed)
        assert ok.refunds.get().status == PaymentRefund.Status.SUCCEEDED

    def describe_the_refund_task():
        def it_is_safe_to_run_twice(mock_create, sweep):
            offering = _closed_class(minimum=4, confirmed=1)
            sweep(timezone.now())
            registration = offering.registrations.get()
            refund_for_minimum_cancel.call(registration.pk)
            assert registration.refunds.count() == 1

        def it_stops_quietly_when_the_refund_was_already_taken(mock_create):
            registration = _paid(_closed_class(minimum=4, confirmed=0))
            with patch.object(Registration, "issue_refund", side_effect=AlreadyRefundedError("done")):
                refund_for_minimum_cancel.call(registration.pk)

        def it_raises_a_refusal_that_left_no_ledger_row(mock_create):
            registration = _paid(_closed_class(minimum=4, confirmed=0))
            with patch.object(Registration, "issue_refund", side_effect=RefundError("odd")):
                with pytest.raises(RefundError):
                    refund_for_minimum_cancel.call(registration.pk)

    def it_leaves_a_class_whose_registration_is_still_open(mock_create, sweep):
        offering = _live_class(starts_in=timedelta(days=10), minimum=4, confirmed=1)
        assert sweep(timezone.now()) == 0
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED

    def it_leaves_a_class_that_has_started(mock_create, sweep):
        offering = _live_class(starts_in=-timedelta(hours=1), minimum=4, confirmed=1)
        assert sweep(timezone.now()) == 0
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED

    def it_still_cancels_the_others_when_one_fails_then_reports_it(mock_create, sweep):
        broken = _closed_class(minimum=4, confirmed=1)
        fine = _closed_class(minimum=4, confirmed=1)
        real = ClassOffering.cancel_for_minimum

        def cancel(self: ClassOffering, now: datetime) -> bool:
            if self.pk == broken.pk:
                raise RuntimeError("boom")
            return real(self, now)

        with patch.object(ClassOffering, "cancel_for_minimum", cancel):
            with pytest.raises(RuntimeError, match=str(broken.pk)):
                sweep(timezone.now())
        fine.refresh_from_db()
        assert fine.status == ClassOffering.Status.CANCELLED


def describe_classes_left_alone():
    """AC6: at or over the minimum, or with no minimum, the close changes nothing."""

    def it_leaves_a_class_at_its_minimum(sweep):
        offering = _closed_class(minimum=2, confirmed=2)
        assert sweep(timezone.now()) == 0
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED

    def it_leaves_a_class_with_no_minimum(sweep):
        offering = _closed_class(minimum=None, confirmed=0)
        assert sweep(timezone.now()) == 0
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED

    def it_counts_only_confirmed_signups():
        offering = _closed_class(minimum=2, confirmed=1)
        _paid(offering, status=Registration.Status.PENDING)
        _paid(offering, status=Registration.Status.WAITLISTED)
        assert list(ClassOffering.objects.due_minimum_cancel(timezone.now())) == [offering]


def describe_the_waitlist_on_a_cancelled_class():
    """A refund on a cancelled class frees a seat in a class that will not run: nobody is offered it."""

    def it_offers_no_seat_after_the_minimum_cancels_it(mock_create, sweep):
        offering = _closed_class(minimum=4, confirmed=1, capacity=1)
        waiting = _paid(offering, status=Registration.Status.WAITLISTED, amount_paid_cents=0, stripe_payment_id="")
        sweep(timezone.now())
        waiting.refresh_from_db()
        assert waiting.waitlist_notified_at is None

    def it_offers_no_seat_when_an_admin_cancels_then_refunds(mock_create):
        offering = _live_class(starts_in=timedelta(days=10), minimum=None, confirmed=1, capacity=1)
        waiting = _paid(offering, status=Registration.Status.WAITLISTED, amount_paid_cents=0, stripe_payment_id="")
        offering.cancel(actor=None, reason="Instructor is ill.")
        offering.registrations.get(status=Registration.Status.CONFIRMED).issue_refund()
        waiting.refresh_from_db()
        assert waiting.waitlist_notified_at is None


def describe_the_class_page():
    """AC7: the public page says how many signups the class needs and when that is decided."""

    def it_says_the_minimum_and_when_it_is_decided(client: Client):
        offering = _live_class(starts_in=timedelta(days=10), minimum=3)
        html = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()
        line = html[html.index('data-minimum-signups="3"') :]
        line = line[: line.index("</div>")]
        assert "needs 3 signups to run" in line
        decided = offering.registration_closes_at
        assert decided is not None
        assert timezone.localtime(decided).strftime("%b %-d") in line

    def it_says_nothing_without_a_minimum(client: Client):
        offering = _live_class(starts_in=timedelta(days=10), minimum=None)
        html = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()
        assert "data-minimum-signups" not in html


def describe_the_job():
    def it_runs_every_15_minutes_from_the_registry(mock_create):
        job = JOBS_BY_KEY["enforce_class_minimums"]
        assert (job.command, job.cadence) == ("enforce_class_minimums", Cadence.ALWAYS)

    def it_warns_then_cancels(mock_create):
        _live_class(starts_in=timedelta(hours=72, minutes=5), minimum=4, confirmed=1, instructor=_instructor())
        _closed_class(minimum=4, confirmed=1)
        assert enforce_class_minimums() == (1, 1)

    def it_reports_what_it_did(mock_create, capsys: pytest.CaptureFixture[str]):
        _closed_class(minimum=4, confirmed=1)
        call_command("enforce_class_minimums")
        assert "Sent 0 warning(s); cancelled 1 class(es)." in capsys.readouterr().out
