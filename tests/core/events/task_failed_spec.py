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
from django.core.management import call_command
from django.tasks import task
from django.urls import reverse
from django_tasks_db.management.commands.db_worker import Worker
from django_tasks_db.models import DBTaskResult

from core.models import EventDelivery, Notification, PushSubscription
from core.scheduled_jobs import ALERT_PERIOD_MAX, alert_task_failed, failed_tasks_path, task_period_key
from core.tasks import ABANDONED_AFTER, enqueue_on_commit
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
            queue_names=["default"],
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

    def it_sends_no_second_alert_from_the_watch_for_a_failure_the_signal_reported(
        linked_member, pushed, django_capture_on_commit_callbacks
    ):
        holder = _webmaster(linked_member)
        _run(django_capture_on_commit_callbacks, send_to_discord, 31)
        _drain()

        call_command("check_task_queue")

        assert len(_bells(holder)) == 1
        assert len(mail.outbox) == 1


def _failed_row(task_path: str, *, traceback: str, class_path: str = "builtins.ValueError") -> DBTaskResult:
    from django.utils import timezone

    return DBTaskResult.objects.create(
        task_path=task_path,
        args_kwargs={"args": [], "kwargs": {}},
        backend_name="default",
        status="FAILED",
        finished_at=timezone.now(),
        exception_class_path=class_path,
        traceback=traceback,
    )


@pytest.mark.usefixtures("midday_now")
def describe_the_watch():
    """``check_task_queue`` alerts on failures the ``task_finished`` signal never reported."""

    def it_alerts_once_a_day_for_a_task_whose_code_no_longer_imports(linked_member, pushed):
        # The worker marks such a task FAILED in run_task without sending task_finished.
        holder = _webmaster(linked_member)
        _failed_row(
            "events.tasks.retired_send",
            class_path="builtins.ModuleNotFoundError",
            traceback="Traceback (most recent call last):\nModuleNotFoundError: No module named 'events.tasks'\n",
        )

        call_command("check_task_queue")
        call_command("check_task_queue")

        [bell] = _bells(holder)
        assert bell.title == "Background task retired_send failed"
        assert "Error: No module named 'events.tasks'\n" in bell.body
        assert len(mail.outbox) == 1

    def it_alerts_for_a_task_it_marked_abandoned(linked_member, pushed):
        from datetime import timedelta

        from django.utils import timezone

        holder = _webmaster(linked_member)
        row = DBTaskResult.objects.create(
            task_path="tests.core.events.task_failed_spec.works",
            args_kwargs={"args": [], "kwargs": {}},
            backend_name="default",
            status="RUNNING",
        )
        DBTaskResult.objects.filter(pk=row.pk).update(started_at=timezone.now() - ABANDONED_AFTER - timedelta(hours=1))

        call_command("check_task_queue")

        [bell] = _bells(holder)
        assert bell.title == "Background task works failed"
        assert "Error: The worker stopped while this task was running" in bell.body


def describe_a_failed_enqueue():
    @pytest.mark.usefixtures("midday_now")
    def it_alerts_the_webmasters_and_never_raises_into_the_save(
        linked_member, pushed, django_capture_on_commit_callbacks
    ):
        from django.tasks.backends.immediate import ImmediateBackend

        holder = _webmaster(linked_member)

        with patch.object(ImmediateBackend, "enqueue", side_effect=RuntimeError("queue table missing")):
            _run(django_capture_on_commit_callbacks, works)

        [bell] = _bells(holder)
        assert bell.title == "Background task works failed"
        assert "Error: It could not be queued: queue table missing\n" in bell.body


def describe_the_alert_period():
    def it_keeps_a_short_path_readable():
        assert task_period_key("events.tasks.announce") == "task:events.tasks.announce"

    def it_fits_a_long_path_in_the_bell_rows_period_column():
        long_path = "membership." + "very_long_module_name." * 6 + "announce_everything"
        other = "membership." + "very_long_module_name." * 6 + "announce_something_else"

        key = task_period_key(long_path)

        assert len(f"automation.failed:{key}:2026-11-09") == ALERT_PERIOD_MAX
        assert key == task_period_key(long_path)
        assert key != task_period_key(other)

    @pytest.mark.usefixtures("midday_now")
    def it_writes_every_period_within_its_column(linked_member, pushed):
        from django.utils import timezone

        holder = _webmaster(linked_member)
        long_path = "membership." + "very_long_module_name." * 6 + "announce_everything"

        alert_task_failed(
            task_path=long_path, task_name="announce_everything", finished_at=timezone.now(), error_lines=["x"]
        )

        [bell] = _bells(holder)
        assert 0 < len(bell.period) <= Notification._meta.get_field("period").max_length
        periods = list(EventDelivery.objects.values_list("period", flat=True))
        assert periods
        assert all(len(period) <= EventDelivery._meta.get_field("period").max_length for period in periods)
