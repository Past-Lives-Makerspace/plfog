"""The demo scrub: lists every demo row, removes them, then finds nothing (#983)."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from billing.models import PaymentRefund, Payout
from classes.demo_scrub import DemoScrubRefusedError, plan_demo_scrub, scrub_demo_data
from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    RegistrationFactory,
)
from classes.models import Category, ClassOffering, ClassSession, Registration
from tests.billing.factories import PaymentRefundFactory
from membership.models import Member
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db


def _demo_class(slug: str = "demo-cart-ceramics", category=None):
    category = category or CategoryFactory(name="[DEMO] Ceramics", slug="demo-ceramics")
    offering = ClassOfferingFactory(slug=slug, category=category, instructor=MemberFactory())
    ClassSessionFactory(class_offering=offering, starts_at=timezone.now())
    return offering


def _paid(offering, **extra):
    return RegistrationFactory(class_offering=offering, amount_paid_cents=4500, confirmed_at=timezone.now(), **extra)


def _payout(reg, **extra):
    fields = {"status": Payout.Status.OWED_MANUALLY, **extra}
    return Payout.objects.create(
        registration=reg, payee=reg.class_offering.instructor, amount_cents=3000, due_at=timezone.now(), **fields
    )


def describe_plan_demo_scrub():
    def it_lists_the_demo_class_and_everything_below_it_by_model():
        offering = _demo_class()
        reg = _paid(offering, email="past1@pastlives.demo")
        payout = _payout(reg)
        real = ClassOfferingFactory(slug="real-wheel")

        plan = plan_demo_scrub()

        assert plan.deletes["classes.ClassOffering"] == [offering.pk]
        assert plan.deletes["classes.Registration"] == [reg.pk]
        assert plan.deletes["billing.Payout"] == [payout.pk]
        assert plan.deletes["classes.ClassSession"] == list(
            ClassSession.objects.filter(class_offering=offering).values_list("pk", flat=True)
        )
        assert plan.deletes["classes.Category"] == [offering.category_id]
        assert real.pk not in plan.deletes["classes.ClassOffering"]
        assert plan.blockers == []

    def it_writes_nothing():
        _paid(_demo_class())

        plan_demo_scrub()

        assert ClassOffering.objects.filter(slug__startswith="demo-").count() == 1

    def it_names_a_demo_registration_with_a_real_stripe_payment_on_its_own():
        reg = _paid(_demo_class(), stripe_payment_id="pi_live_test")
        PaymentRefundFactory(registration=reg, amount_cents=4500, status=PaymentRefund.Status.SUCCEEDED)
        _paid(reg.class_offering)

        plan = plan_demo_scrub()

        assert [(s.pk, s.paid_cents, s.refunded_cents) for s in plan.stripe_backed] == [(reg.pk, 4500, 4500)]

    def it_takes_a_refund_on_a_demo_registration_with_it():
        reg = _paid(_demo_class(), stripe_payment_id="pi_live_test")
        refund = PaymentRefundFactory(registration=reg, amount_cents=4500, status=PaymentRefund.Status.SUCCEEDED)

        plan = plan_demo_scrub()

        assert plan.deletes["billing.PaymentRefund"] == [refund.pk]
        assert plan.blockers == []

    def it_refuses_a_demo_payout_stripe_may_have_sent():
        _payout(_paid(_demo_class()), stripe_transfer_id="tr_1", status=Payout.Status.SENT)

        assert "has a Stripe transfer" in plan_demo_scrub().blockers[0]

    def it_refuses_a_demo_payout_with_an_attempted_transfer():
        _payout(_paid(_demo_class()), status=Payout.Status.PENDING, attempted_at=timezone.now())

        assert "has a Stripe transfer" in plan_demo_scrub().blockers[0]

    def it_refuses_a_demo_registrant_on_a_real_class():
        stray = _paid(ClassOfferingFactory(slug="real-glass"), email="current1@pastlives.demo")

        assert plan_demo_scrub().blockers == [
            f"classes.Registration {stray.pk} is a demo registrant on the real class 'real-glass'."
        ]

    def it_refuses_a_real_class_filed_under_a_demo_category():
        category = CategoryFactory(name="[DEMO] Glass", slug="demo-glass")
        real = ClassOfferingFactory(slug="real-in-demo-type", category=category)

        assert plan_demo_scrub().blockers == [
            f"classes.ClassOffering {real.pk} is not demo data but holds on to a demo row."
        ]

    def it_warns_about_a_demo_class_still_listed_on_eventbrite():
        offering = _demo_class()
        ClassOffering.objects.filter(pk=offering.pk).update(
            eventbrite_event_id="123", eventbrite_sync_state=ClassOffering.EventbriteSyncState.LISTED
        )

        assert plan_demo_scrub().warnings == [
            f"'{offering.slug}' is still listed on Eventbrite; end that listing there."
        ]

    def it_counts_links_cleared_on_rows_that_stay():
        GuildFactory(featured_class=_demo_class())

        assert plan_demo_scrub().cleared_links["membership.Guild.featured_class"] == 1


def describe_scrub_demo_data():
    def it_removes_every_listed_row_and_leaves_real_classes_and_members():
        offering = _demo_class()
        reg = _paid(offering, stripe_payment_id="pi_live_test")
        _payout(reg)
        PaymentRefundFactory(registration=reg, amount_cents=4500, status=PaymentRefund.Status.SUCCEEDED)
        real = _paid(ClassOfferingFactory(slug="real-wheel"))
        instructor_pk = offering.instructor_id

        plan = scrub_demo_data()

        assert plan.total_rows >= 5
        assert not ClassOffering.objects.filter(slug__startswith="demo-").exists()
        assert not Category.objects.filter(slug__startswith="demo-").exists()
        assert not Registration.objects.filter(pk=reg.pk).exists()
        assert not Payout.objects.exists()
        assert not PaymentRefund.objects.exists()
        assert Registration.objects.filter(pk=real.pk).exists()
        assert Member.objects.filter(pk=instructor_pk).exists()

    def it_removes_nothing_on_a_second_run():
        _paid(_demo_class())
        scrub_demo_data()

        assert scrub_demo_data().is_empty is True

    def it_deletes_nothing_when_it_refuses():
        offering = _demo_class()
        _payout(_paid(offering), stripe_transfer_id="tr_1", status=Payout.Status.SENT)

        with pytest.raises(DemoScrubRefusedError, match="has a Stripe transfer"):
            scrub_demo_data()

        assert ClassOffering.objects.filter(pk=offering.pk).exists()


def describe_scrub_demo_data_command():
    def it_lists_rows_and_stripe_payments_on_a_dry_run_without_deleting():
        offering = _demo_class()
        reg = _paid(offering, stripe_payment_id="pi_live_test")
        PaymentRefundFactory(registration=reg, amount_cents=4500, status=PaymentRefund.Status.SUCCEEDED)
        out = StringIO()

        call_command("scrub_demo_data", "--dry-run", stdout=out)

        text = out.getvalue()
        assert f"classes.ClassOffering (1): {offering.pk}" in text
        assert f"registration {reg.pk} on {offering.slug}: paid $45.00, refunded $45.00" in text
        assert "[dry-run] nothing deleted." in text
        assert ClassOffering.objects.filter(pk=offering.pk).exists()

    def it_removes_then_reports_nothing_left():
        _paid(_demo_class())
        first, second = StringIO(), StringIO()

        call_command("scrub_demo_data", stdout=first)
        call_command("scrub_demo_data", stdout=second)

        assert "Removed" in first.getvalue()
        assert "Nothing to remove" in second.getvalue()

    def it_prints_warnings_and_blockers_on_a_dry_run():
        offering = _demo_class()
        ClassOffering.objects.filter(pk=offering.pk).update(
            eventbrite_event_id="123", eventbrite_sync_state=ClassOffering.EventbriteSyncState.LISTED
        )
        _payout(_paid(offering), stripe_transfer_id="tr_1", status=Payout.Status.SENT)
        out = StringIO()

        call_command("scrub_demo_data", "--dry-run", stdout=out)

        assert "Warning: " in out.getvalue()
        assert "Refuses: " in out.getvalue()

    def it_fails_loudly_when_it_refuses():
        _payout(_paid(_demo_class()), stripe_transfer_id="tr_1", status=Payout.Status.SENT)

        with pytest.raises(CommandError, match="Refused, nothing deleted"):
            call_command("scrub_demo_data", stdout=StringIO())
