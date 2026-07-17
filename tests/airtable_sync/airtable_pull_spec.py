"""BDD specs for the airtable_pull management command — _upsert_member logic."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.management import call_command

from airtable_sync.management.commands.airtable_pull import Command
from core.factories import ScheduledJobStateFactory
from core.models import ScheduledTaskRun
from membership.models import Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory


@pytest.mark.django_db
def describe_upsert_member():
    def it_skips_records_with_no_email():
        plan = MembershipPlanFactory()
        cmd = Command()
        results = {"created": 0, "updated": 0, "skipped": 0}

        cmd._upsert_member(
            model=Member,
            record_id="recNOEMAIL",
            django_kwargs={"full_legal_name": "No Email Person", "_pre_signup_email": ""},
            default_plan=plan,
            dry_run=False,
            results=results,
        )

        assert results["skipped"] == 1
        assert results["created"] == 0
        assert not Member.objects.filter(full_legal_name="No Email Person").exists()

    def it_creates_records_with_email():
        plan = MembershipPlanFactory()
        cmd = Command()
        results = {"created": 0, "updated": 0, "skipped": 0}

        cmd._upsert_member(
            model=Member,
            record_id="recHASEMAIL",
            django_kwargs={"full_legal_name": "Has Email", "_pre_signup_email": "has@example.com", "status": "active"},
            default_plan=plan,
            dry_run=False,
            results=results,
        )

        assert results["created"] == 1
        assert Member.objects.filter(_pre_signup_email="has@example.com").exists()

    def it_updates_existing_member_by_email():
        plan = MembershipPlanFactory()
        existing = MemberFactory(_pre_signup_email="match@example.com", full_legal_name="Old Name")
        cmd = Command()
        results = {"created": 0, "updated": 0, "skipped": 0}

        cmd._upsert_member(
            model=Member,
            record_id="recMATCH",
            django_kwargs={"full_legal_name": "New Name", "_pre_signup_email": "match@example.com"},
            default_plan=plan,
            dry_run=False,
            results=results,
        )

        assert results["updated"] == 1
        existing.refresh_from_db()
        assert existing.airtable_record_id == "recMATCH"


@pytest.mark.django_db
def describe_handle():
    def it_records_a_run_and_pulls_when_enabled():
        with patch.object(Command, "_pull") as mock_pull:
            call_command("airtable_pull")
        mock_pull.assert_called_once()
        run = ScheduledTaskRun.objects.get(task_key="airtable_pull")
        assert run.succeeded

    def it_skips_and_does_not_record_when_paused():
        ScheduledJobStateFactory(task_key="airtable_pull", enabled=False)
        with patch.object(Command, "_pull") as mock_pull:
            call_command("airtable_pull")
        mock_pull.assert_not_called()
        assert not ScheduledTaskRun.objects.filter(task_key="airtable_pull").exists()
