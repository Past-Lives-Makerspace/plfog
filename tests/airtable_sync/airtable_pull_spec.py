"""BDD specs for the airtable_pull management command — _upsert_member logic."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
import respx
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


@pytest.mark.django_db
def describe_pull_members_with_an_unmapped_status():
    def it_warns_naming_the_record_and_leaves_the_status_alone(caplog):
        plan = MembershipPlanFactory()
        existing = MemberFactory(
            _pre_signup_email="lapsed@example.com", status=Member.Status.ACTIVE, membership_plan=plan
        )
        record = {
            "id": "recLAPSED1",
            "fields": {"Member Name": "Lee Lapsed", "Email": "lapsed@example.com", "Status": "Lapsed"},
        }

        with (
            patch("airtable_sync.management.commands.airtable_pull.get_table") as get_table,
            caplog.at_level(logging.WARNING, logger="airtable_sync.config"),
        ):
            get_table.return_value.all.return_value = [record]
            Command()._pull_members(dry_run=False)

        existing.refresh_from_db()
        assert existing.status == Member.Status.ACTIVE
        messages = [r.getMessage() for r in caplog.records if r.name == "airtable_sync.config"]
        assert len(messages) == 1
        assert "recLAPSED1" in messages[0]
        assert "Lee Lapsed" in messages[0]
        assert "'Lapsed'" in messages[0]


@pytest.mark.django_db
def describe_pull_turning_a_member_former():
    def it_queues_their_discord_removal():
        from datetime import date

        from membership.models import DiscordRemoval

        existing = MemberFactory(
            _pre_signup_email="leaving@example.com", status=Member.Status.ACTIVE, discord_user_id="555"
        )
        existing.airtable_record_id = "recLEAVING"
        existing.save()
        results = {"created": 0, "updated": 0, "skipped": 0}

        Command()._upsert_member(
            model=Member,
            record_id="recLEAVING",
            django_kwargs={"status": "former", "cancellation_date": date(2026, 10, 20)},
            default_plan=None,
            dry_run=False,
            results=results,
        )

        removal = DiscordRemoval.objects.get()
        assert (removal.member, removal.warn_on) == (existing, date(2026, 10, 21))


@pytest.mark.django_db
def describe_healthchecks_reporting():
    """Each run reports to its Healthchecks check: counts on success, the traceback on failure."""

    ping_url = "https://hc.example.test/ping/00000000-0000-0000-0000-000000000000"

    @pytest.fixture()
    def hc(settings):
        settings.HEALTHCHECKS_PING_URL = ping_url
        with respx.mock(assert_all_called=False) as router:
            router.post(ping_url).respond(200)
            router.post(f"{ping_url}/start").respond(200)
            router.post(f"{ping_url}/fail").respond(200)
            yield router

    def _last_ping(router: respx.MockRouter) -> tuple[str, str]:
        request = router.calls.last.request
        return str(request.url), request.content.decode()

    def it_pings_success_with_each_tables_counts(hc):
        with patch("airtable_sync.management.commands.airtable_pull.get_table") as get_table:
            get_table.return_value.all.return_value = []
            call_command("airtable_pull")
        assert _last_ping(hc) == (
            ping_url,
            "members: created=0, updated=0, skipped=0\nspaces: created=0, updated=0, skipped=0",
        )
        assert str(hc.calls[0].request.url) == f"{ping_url}/start"

    def it_marks_a_dry_run_in_the_log(hc):
        with patch("airtable_sync.management.commands.airtable_pull.get_table") as get_table:
            get_table.return_value.all.return_value = []
            call_command("airtable_pull", "--model=space", "--dry-run")
        assert _last_ping(hc) == (ping_url, "spaces (dry run): created=0, updated=0, skipped=0")

    def it_pings_fail_with_the_traceback_when_a_table_errors(hc):
        with (
            patch("airtable_sync.management.commands.airtable_pull.get_table") as get_table,
            pytest.raises(RuntimeError, match="airtable down"),
        ):
            get_table.return_value.all.side_effect = RuntimeError("airtable down")
            call_command("airtable_pull")
        url, body = _last_ping(hc)
        assert url == f"{ping_url}/fail"
        assert body.rstrip().endswith("RuntimeError: airtable down")
        assert ScheduledTaskRun.objects.get(task_key="airtable_pull").failed

    def it_pings_success_saying_it_was_paused(hc):
        ScheduledJobStateFactory(task_key="airtable_pull", enabled=False)
        call_command("airtable_pull")
        assert _last_ping(hc) == (ping_url, "airtable_pull is paused; skipped.")
