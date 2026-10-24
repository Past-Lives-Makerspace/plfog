"""One automation's history page and the Cancel on a queued Discord removal (#898)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.factories import ScheduledTaskRunFactory
from core.models import AutomationAction, ScheduledTaskRun
from membership.models import DISCORD_REMOVAL_TASK_KEY, DiscordRemoval, Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _admin(client: Client) -> User:
    user = User.objects.create_superuser(username="histadmin", email="histadmin@x.com", password="p")
    client.login(username="histadmin", password="p")
    return user


def _history(key: str = DISCORD_REMOVAL_TASK_KEY) -> str:
    return reverse("hub_admin_automation_history", args=[key])


def _queued() -> DiscordRemoval:
    member = MemberFactory(discord_user_id="111", full_legal_name="Quinn Former", preferred_name="")
    member.status = Member.Status.FORMER
    member.save()
    return DiscordRemoval.objects.get()


def describe_history_page():
    def it_forbids_non_admins(client: Client):
        User.objects.create_user(username="plain", email="plain@x.com", password="p")
        client.login(username="plain", password="p")
        assert client.get(_history()).status_code == 403

    def it_404s_for_an_unknown_job(client: Client):
        _admin(client)
        assert client.get(_history("no_such_job")).status_code == 404

    def it_shows_each_run_with_its_actions(client: Client):
        admin = _admin(client)
        run = ScheduledTaskRunFactory(
            task_key=DISCORD_REMOVAL_TASK_KEY, status=ScheduledTaskRun.Status.FAILED, error="1 of 2 failed", actor=admin
        )
        AutomationAction.objects.create(
            task_key=DISCORD_REMOVAL_TASK_KEY,
            run=run,
            kind=AutomationAction.Kind.DISCORD_KICK,
            result=AutomationAction.Result.FAILED,
            subject="Kicked Kim",
            detail="Discord refused (403)",
        )
        html = client.get(_history()).content.decode()
        assert f'data-run="{run.pk}"' in html
        assert "Kicked Kim" in html
        assert "Discord refused (403)" in html
        assert "hub-pill--danger" in html
        assert "1 of 2 failed" in html
        assert "histadmin" in html

    def it_lists_actions_outside_a_run_with_the_admin(client: Client):
        admin = _admin(client)
        removal = _queued()
        removal.cancel(by=admin)
        html = client.get(_history()).content.decode()
        assert "data-loose-actions" not in html
        loose = client.get(f"{_history()}?list=other").content.decode()
        assert "data-run-history" not in loose
        assert "Removal cancelled" in loose
        assert "histadmin" in loose
        assert "Queued for removal" in loose

    def it_pages_the_actions_outside_a_run_so_none_are_lost(client: Client):
        _admin(client)
        AutomationAction.objects.bulk_create(
            AutomationAction(
                task_key=DISCORD_REMOVAL_TASK_KEY,
                kind=AutomationAction.Kind.REMOVAL_QUEUED,
                result=AutomationAction.Result.OK,
                subject=f"Person {n}",
            )
            for n in range(120)
        )
        oldest = AutomationAction.objects.order_by("created_at", "pk").first()
        first = client.get(f"{_history()}?list=other").content.decode()
        assert first.count("data-action=") == 50
        assert "Page 1 of 3" in first
        assert "?page=2&list=other" in first
        last = client.get(f"{_history()}?list=other&page=3").content.decode()
        assert last.count("data-action=") == 20
        assert f'data-action="{oldest.pk}"' in last

    def it_pages_the_runs(client: Client):
        _admin(client)
        for _ in range(30):
            ScheduledTaskRunFactory(task_key=DISCORD_REMOVAL_TASK_KEY)
        html = client.get(f"{_history()}?page=2").content.decode()
        assert html.count("data-run=") == 5
        assert 'aria-current="page" data-runs-tab' in html

    def it_lists_the_open_queue_with_a_cancel(client: Client):
        _admin(client)
        removal = _queued()
        html = client.get(_history()).content.decode()
        assert f'data-removal-row="{removal.pk}"' in html
        assert reverse("hub_admin_discord_removal_cancel", args=[removal.pk]) in html
        assert f"cancel-removal-{removal.pk}" in html

    def it_says_when_nobody_is_queued(client: Client):
        _admin(client)
        html = client.get(_history()).content.decode()
        assert "data-open-removals" in html
        assert "Nobody is queued." in html

    def it_leaves_the_queue_off_other_jobs(client: Client):
        _admin(client)
        html = client.get(_history("send_class_reminders")).content.decode()
        assert "data-open-removals" not in html
        assert "This automation has not run yet." in html

    def it_is_linked_from_every_automation_card(client: Client):
        _admin(client)
        html = client.get(reverse("hub_admin_automations")).content.decode()
        assert f'href="{_history()}"' in html
        assert f'href="{_history("send_class_reminders")}"' in html


def describe_cancel():
    def it_cancels_and_records_the_admin(client: Client):
        admin = _admin(client)
        removal = _queued()
        response = client.post(reverse("hub_admin_discord_removal_cancel", args=[removal.pk]))
        assert response.status_code == 302
        assert response["Location"] == _history()
        removal.refresh_from_db()
        assert (removal.status, removal.closed_by) == (DiscordRemoval.Status.CANCELLED, admin)

    def it_says_so_when_the_row_is_no_longer_open(client: Client):
        _admin(client)
        removal = _queued()
        removal.close("Kept.")
        response = client.post(reverse("hub_admin_discord_removal_cancel", args=[removal.pk]), follow=True)
        assert "already ran (Closed), so nothing was cancelled" in response.content.decode()
        removal.refresh_from_db()
        assert removal.status == DiscordRemoval.Status.CLOSED

    def it_tells_the_admin_when_the_job_already_removed_them(client: Client):
        _admin(client)
        removal = _queued()
        DiscordRemoval.objects.filter(pk=removal.pk).update(status=DiscordRemoval.Status.KICKED)
        response = client.post(reverse("hub_admin_discord_removal_cancel", args=[removal.pk]), follow=True)
        assert "already ran (Removed), so nothing was cancelled" in response.content.decode()
        removal.refresh_from_db()
        assert removal.status == DiscordRemoval.Status.KICKED

    def it_404s_for_an_unknown_removal(client: Client):
        _admin(client)
        assert client.post(reverse("hub_admin_discord_removal_cancel", args=[999999])).status_code == 404

    def it_only_takes_a_post(client: Client):
        _admin(client)
        removal = _queued()
        assert client.get(reverse("hub_admin_discord_removal_cancel", args=[removal.pk])).status_code == 405

    def it_forbids_non_admins(client: Client):
        removal = _queued()
        User.objects.create_user(username="plain", email="plain@x.com", password="p")
        client.login(username="plain", password="p")
        assert client.post(reverse("hub_admin_discord_removal_cancel", args=[removal.pk])).status_code == 403
        removal.refresh_from_db()
        assert removal.status == DiscordRemoval.Status.QUEUED
