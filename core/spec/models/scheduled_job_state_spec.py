"""Specs for the ScheduledJobState model + manager (per-job ON/OFF; absence == enabled)."""

from __future__ import annotations

import pytest

from core.factories import ScheduledJobStateFactory
from core.models import ScheduledJobState
from core.scheduled_jobs import SCHEDULED_JOBS

pytestmark = pytest.mark.django_db


def describe_ScheduledJobStateManager():
    def describe_is_enabled():
        def it_defaults_to_true_when_no_row_exists():
            assert ScheduledJobState.objects.is_enabled("send_class_reminders") is True

        def it_returns_the_stored_value_when_a_row_exists():
            ScheduledJobStateFactory(task_key="send_class_reminders", enabled=False)
            assert ScheduledJobState.objects.is_enabled("send_class_reminders") is False

    def describe_set_enabled():
        def it_creates_a_row_and_records_who_flipped_it(django_user_model):
            user = django_user_model.objects.create(username="admin")
            ScheduledJobState.objects.set_enabled("bill_tabs", False, user=user)
            row = ScheduledJobState.objects.get(task_key="bill_tabs")
            assert row.enabled is False
            assert row.updated_by == user

        def it_updates_an_existing_row():
            ScheduledJobStateFactory(task_key="bill_tabs", enabled=True)
            ScheduledJobState.objects.set_enabled("bill_tabs", False)
            assert ScheduledJobState.objects.get(task_key="bill_tabs").enabled is False

        def it_drops_a_user_without_a_pk():
            from django.contrib.auth.models import AnonymousUser

            ScheduledJobState.objects.set_enabled("bill_tabs", True, user=AnonymousUser())
            assert ScheduledJobState.objects.get(task_key="bill_tabs").updated_by is None

    def describe_sync_registry():
        def it_creates_a_row_for_every_registry_job():
            ScheduledJobState.objects.sync_registry()
            keys = set(ScheduledJobState.objects.values_list("task_key", flat=True))
            assert keys == {job.key for job in SCHEDULED_JOBS}

        def it_is_idempotent():
            ScheduledJobState.objects.sync_registry()
            ScheduledJobState.objects.sync_registry()
            assert ScheduledJobState.objects.count() == len(SCHEDULED_JOBS)

        def it_preserves_an_existing_disabled_state():
            ScheduledJobStateFactory(task_key="send_class_reminders", enabled=False)
            ScheduledJobState.objects.sync_registry()
            assert ScheduledJobState.objects.get(task_key="send_class_reminders").enabled is False

        def it_keeps_rows_for_unknown_keys():
            ScheduledJobStateFactory(task_key="retired_job", enabled=False)
            ScheduledJobState.objects.sync_registry()
            assert ScheduledJobState.objects.filter(task_key="retired_job").exists()


def describe_ScheduledJobState():
    def describe_str():
        def it_shows_enabled_state():
            row = ScheduledJobStateFactory(task_key="bill_tabs", enabled=True)
            assert str(row) == "bill_tabs (enabled)"

        def it_shows_disabled_state():
            row = ScheduledJobStateFactory(task_key="bill_tabs", enabled=False)
            assert str(row) == "bill_tabs (disabled)"
