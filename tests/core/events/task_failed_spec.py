"""A durable queue task that fails alerts the Webmasters like a failed automation (#1150).

``core.tasks`` listens for ``task_finished`` and, on a FAILED result, sends the same
``automation.failed`` alert as ``core.scheduled_jobs.alert_webmasters``: plain text, bell and
push then email, at most once per task per Pacific day, linking the failed tasks in the admin.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core import mail
from django.tasks import task
from django.urls import reverse
from django_tasks_db.management.commands.db_worker import Worker
from django_tasks_db.models import DBTaskResult

from core.models import Notification, PushSubscription
from core.scheduled_jobs import failed_tasks_path
from core.tasks import enqueue_on_commit
from membership.models import AdminCapability

pytestmark = pytest.mark.django_db

EVENT = "automation.failed"
DB_BACKEND = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}


@task
def send_to_discord(event_id: int) -> None:
    raise ValueError(f"Discord said no to event {event_id}\nFor more information check: https://discord.com")


@task
def other_send() -> None:
    raise RuntimeError("provider down")


@task
def works() -> None:
    return None


def _webmaster(linked_member):
    member = linked_member()
    member.admin_capabilities.create(capability=AdminCapability.Capability.WEBMASTER)
    PushSubscription.objects.create(user=member.user, endpoint=f"https://push/{member.pk}", p256dh="k", auth="a")
    return member


def _bells(member) -> list[Notification]:
    return list(Notification.objects.filter(user=member.user, trigger=EVENT))


def _run(django_capture_on_commit_callbacks, the_task, *args: object) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        enqueue_on_commit(the_task, *args)


@pytest.fixture
def pushed():
    with patch("core.events.channels.send_web_push") as send_web_push:
        yield send_web_push


@pytest.mark.usefixtures("midday_now")
def describe_a_failed_task():
    def it_alerts_the_webmasters_with_the_task_name_and_first_error_line(
        linked_member, pushed, django_capture_on_commit_callbacks
    ):
        holder = _webmaster(linked_member)

        _run(django_capture_on_commit_callbacks, send_to_discord, 31)

        [bell] = _bells(holder)
        assert bell.title == "Background task send_to_discord failed"
        assert "Error: Discord said no to event 31\n" in bell.body
        assert "For more information" not in bell.body
        assert bell.url == failed_tasks_path()
        pushed.assert_called_once()
        [message] = mail.outbox
        assert message.to == [holder.user.email]
        assert message.body.endswith(
            f"See the failed background tasks in the admin: {settings.MEMBER_BASE_URL}{failed_tasks_path()}"
        )

    def it_sends_no_second_alert_for_the_same_task_the_same_day(
        linked_member, pushed, django_capture_on_commit_callbacks
    ):
        holder = _webmaster(linked_member)

        _run(django_capture_on_commit_callbacks, send_to_discord, 31)
        _run(django_capture_on_commit_callbacks, send_to_discord, 32)

        assert len(_bells(holder)) == 1
        assert len(mail.outbox) == 1
        pushed.assert_called_once()

    def it_alerts_again_for_a_different_task_the_same_day(linked_member, pushed, django_capture_on_commit_callbacks):
        holder = _webmaster(linked_member)

        _run(django_capture_on_commit_callbacks, send_to_discord, 31)
        _run(django_capture_on_commit_callbacks, other_send)

        assert [bell.title for bell in _bells(holder)] == [
            "Background task send_to_discord failed",
            "Background task other_send failed",
        ]

    def it_logs_instead_of_raising_when_the_alert_cannot_be_built(
        linked_member, pushed, django_capture_on_commit_callbacks, caplog
    ):
        _webmaster(linked_member)

        with (
            patch("core.tasks.alert_task_failed", side_effect=RuntimeError("no alert")),
            caplog.at_level(logging.ERROR, logger="core.tasks"),
        ):
            _run(django_capture_on_commit_callbacks, send_to_discord, 31)

        assert "Could not alert the Webmasters that task" in caplog.text

    def it_points_the_alert_at_the_failed_tasks_in_the_admin():
        changelist = reverse("admin:django_tasks_database_dbtaskresult_changelist")
        assert failed_tasks_path() == f"{changelist}?status__exact=FAILED"


def describe_a_task_that_succeeds():
    def it_sends_no_alert(linked_member, pushed, django_capture_on_commit_callbacks):
        holder = _webmaster(linked_member)

        _run(django_capture_on_commit_callbacks, works)

        assert _bells(holder) == []
        assert mail.outbox == []


def describe_the_worker():
    """The database backend and the package's own worker loop, as production runs them."""

    @pytest.fixture(autouse=True)
    def _database_backend(settings):
        settings.TASKS = DB_BACKEND

    def _drain() -> None:
        # The worker closes stale connections on every loop, which on PostgreSQL closes the
        # spec's own open transaction; that hygiene is for a long running process, not this.
        with patch("django_tasks_db.management.commands.db_worker.close_old_connections"):
            _worker().run()

    def _worker() -> Worker:
        return Worker(
            queue_names=["*"],
            interval=0,
            batch=True,
            backend_name="default",
            startup_delay=False,
            max_tasks=None,
            worker_id="spec-worker",
            excluded_queue_names=[],
        )

    def it_stores_the_task_until_a_worker_runs_it(linked_member, pushed, django_capture_on_commit_callbacks):
        holder = _webmaster(linked_member)

        _run(django_capture_on_commit_callbacks, send_to_discord, 31)
        [row] = DBTaskResult.objects.all()
        assert row.status == "READY"
        assert _bells(holder) == []

        _drain()

        row.refresh_from_db()
        assert row.status == "FAILED"
        [bell] = _bells(holder)
        assert bell.title == "Background task send_to_discord failed"
        assert "Error: Discord said no to event 31\n" in bell.body
