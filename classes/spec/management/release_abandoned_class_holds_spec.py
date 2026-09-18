"""Specs for classes/management/commands/release_abandoned_class_holds.py and its job registration."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import ClassOffering, Registration
from core.scheduled_jobs import JOBS_BY_KEY, Cadence

pytestmark = pytest.mark.django_db

JOB_KEY = "release_abandoned_class_holds"


def describe_release_abandoned_class_holds_command():
    def it_reports_what_it_released_and_recovered():
        stdout = StringIO()
        with patch.object(Registration.objects, "release_abandoned_holds", return_value=(3, 1)) as sweep:
            call_command(JOB_KEY, stdout=stdout)

        sweep.assert_called_once_with()
        assert "Released 3 seat(s); recovered 1 paid signup(s)." in stdout.getvalue()

    def it_reports_a_quiet_tick_too():
        stdout = StringIO()
        with patch.object(Registration.objects, "release_abandoned_holds", return_value=(0, 0)):
            call_command(JOB_KEY, stdout=stdout)

        assert "Released 0 seat(s); recovered 0 paid signup(s)." in stdout.getvalue()

    def it_actually_frees_a_seat_end_to_end():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, capacity=2)
        stale = RegistrationFactory(
            class_offering=offering,
            status=Registration.Status.PENDING,
            stripe_session_id="cs_cmd_1",
            amount_paid_cents=4000,
        )
        Registration.objects.filter(pk=stale.pk).update(registered_at=timezone.now() - timedelta(hours=6))
        assert offering.spots_remaining == 1

        session = {
            "id": "cs_cmd_1",
            "url": "",
            "status": "expired",
            "payment_status": "unpaid",
            "payment_intent": "",
            "amount_total": 4000,
        }
        with patch("billing.stripe_utils.retrieve_checkout_session", return_value=session):
            call_command(JOB_KEY, stdout=StringIO())

        stale.refresh_from_db()
        assert stale.status == Registration.Status.CANCELLED
        assert offering.spots_remaining == 2


def describe_its_scheduled_job_registration():
    def it_runs_on_every_dispatcher_tick():
        job = JOBS_BY_KEY[JOB_KEY]
        assert job.cadence == Cadence.ALWAYS
        assert job.command == JOB_KEY

    def it_is_neither_a_money_job_nor_pinned_on():
        # It charges nothing and sends no member-facing mail of its own, so an admin may
        # pause it from the Automations dashboard like any ordinary job.
        job = JOBS_BY_KEY[JOB_KEY]
        assert job.money_job is False
        assert job.confirm_before_run is False
        assert job.toggleable is True
        assert job.default_enabled is True
