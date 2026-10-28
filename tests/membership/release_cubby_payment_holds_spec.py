"""Specs for the release_cubby_payment_holds command (#978) and its place in the scheduled jobs."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

from core.scheduled_jobs import JOBS_BY_KEY, Cadence

pytestmark = pytest.mark.django_db


def describe_release_cubby_payment_holds():
    def it_sweeps_and_reports_what_it_did():
        out = StringIO()
        with patch("membership.cubby_rentals.release_stale_holds", return_value=(2, 1)) as sweep:
            call_command("release_cubby_payment_holds", stdout=out)
        sweep.assert_called_once_with()
        assert out.getvalue().strip() == "Released 2 cubby hold(s); recovered 1 paid rental(s)."

    def it_runs_every_tick_and_is_not_a_money_job():
        job = JOBS_BY_KEY["release_cubby_payment_holds"]
        assert (job.cadence, job.money_job, job.command) == (Cadence.ALWAYS, False, "release_cubby_payment_holds")
