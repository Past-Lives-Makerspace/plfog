"""The durable task queue: work that runs after a save, and survives a redeploy.

New after save side effects (a Discord post, an email fan out, a call to an external API)
are Django tasks (``django.tasks``). Outside pytest each one is a row in Postgres
(``django_tasks_db``) that the separate ``python manage.py db_worker`` process picks up, so
a redeploy or a crash of the web service never loses it: a task that had not started waits
in the table and runs once a worker is back (docs/adr/0003-durable-task-queue.md).

How to write one::

    from django.tasks import task

    from core.tasks import enqueue_on_commit


    @task
    def announce_event(event_id: int) -> None:
        event = CommunityEvent.objects.get(pk=event_id)
        ...  # safe to run twice: check what was already sent before sending


    def publish(self) -> None:  # a model method, inside the save's transaction
        self.save()
        enqueue_on_commit(announce_event, self.pk)

The rules:

* **Enqueue on commit.** :func:`enqueue_on_commit` enqueues once the transaction commits, so
  the task reads committed rows and a rolled back save enqueues nothing.
* **Arguments are ids, never model instances.** They are stored as JSON;
  :func:`enqueue_on_commit` rejects anything else at the call, before the save commits.
  The task loads fresh rows when it runs, which may be minutes later.
* **A task must be safe to run twice.** Nothing runs a task again today, but a caller may
  enqueue the same work twice, and a retry added later must be able to run it again safely.
* **A failure alerts the Webmasters** through the same ``automation.failed`` alert as a failed
  scheduled job, at most once per task per Pacific day: at once from :func:`_alert_on_failure`,
  and from the 15 minute ``check_task_queue`` job for a failure the signal never reported (a
  task whose code no longer imports, or one the worker abandoned). A task the worker never
  finishes (it was killed mid run) is marked failed after :data:`ABANDONED_AFTER`. A failed
  enqueue alerts too. Failed and pending tasks are listed in the admin under Background Tasks,
  where a superuser may delete rows.

Under pytest the immediate backend runs a task at enqueue time, in the test's own thread.
pytest wraps a spec in a transaction that never commits, so a spec that wants the task to
run uses ``django_capture_on_commit_callbacks(execute=True)``.

``core.deferred.defer`` stays only for its existing callers until each moves here.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.dispatch import receiver
from django.tasks import TaskResultStatus
from django.tasks.base import TaskError
from django.tasks.signals import task_finished
from django.utils import timezone
from django.utils.json import normalize_json

from core.scheduled_jobs import alert_task_failed

if TYPE_CHECKING:
    from django.tasks import Task, TaskResult

logger = logging.getLogger(__name__)

#: How long a task may stay RUNNING before :func:`abandon_stuck_tasks` marks it failed. A
#: task runs well under a minute; one still running this long lost its worker.
ABANDONED_AFTER = timedelta(hours=6)


class TaskAbandonedError(Exception):
    """Recorded on a task the worker never finished, so the admin and the alert can name it."""


def enqueue_on_commit(task: Task[..., Any], *args: Any, **kwargs: Any) -> None:
    """Enqueue ``task`` with these arguments once the current transaction commits.

    Outside a transaction it enqueues at once. Nothing is enqueued when the transaction
    rolls back. The arguments go through JSON first, so the immediate backend under test
    hands the task exactly what the database backend would (a tuple arrives as a list).
    A failure to enqueue after the commit is logged and alerts the Webmasters, never raised:
    the save it follows has already committed and must not turn into an error page.

    Args:
        task: A function decorated with ``@task`` from ``django.tasks``. It must be safe
            to run twice.
        *args: JSON values only (ids, strings, numbers), never model instances.
        **kwargs: The same.

    Raises:
        TypeError: An argument is not JSON, such as a model instance, a date or bytes
            that are not UTF-8.
    """
    try:
        stored = json.loads(json.dumps(normalize_json({"args": args, "kwargs": kwargs})))
    except ValueError as exc:  # bytes that are not UTF-8
        raise TypeError(f"Task arguments must be JSON: {exc}") from exc
    transaction.on_commit(lambda: _enqueue(task, stored["args"], stored["kwargs"]), robust=True)


def enqueue_in_transaction(task: Task[..., Any], *args: Any) -> None:
    """Enqueue ``task`` now, inside the caller's transaction, so its row commits or rolls back with the save.

    For work that must never be lost between a commit and its callback, such as a refund owed
    for a status change (#1182): with the database backend the task row is written in the same
    transaction as the change, so a process killed after the commit still leaves the task
    queued, and a rollback leaves neither. The worker cannot see the row until the commit. A
    failure to enqueue raises, rolling the change back with it. Under pytest the immediate
    backend runs the task at once, inside the transaction.

    Args:
        task: A function decorated with ``@task`` from ``django.tasks``. It must be safe to run twice.
        *args: JSON values only (ids, strings, numbers), never model instances.

    Raises:
        TypeError: An argument is not JSON.
    """
    try:
        stored = json.loads(json.dumps(normalize_json({"args": args})))
    except ValueError as exc:  # bytes that are not UTF-8
        raise TypeError(f"Task arguments must be JSON: {exc}") from exc
    task.enqueue(*stored["args"])


def enqueue_at_on_commit(task: Task[..., Any], run_after: datetime, *args: Any, **kwargs: Any) -> None:
    """Like :func:`enqueue_on_commit`, but the task waits until ``run_after`` (an aware datetime).

    The database backend keeps the row until then and the worker claims it only once it is due,
    so a deadline needs no polling job. Under pytest the immediate backend cannot wait, so nothing
    is queued there: a spec runs the task itself, or swaps in the dummy backend to see the queued
    row and its ``run_after``.

    Raises:
        ValueError: ``run_after`` is naive.
        TypeError: An argument is not JSON.
    """
    if not timezone.is_aware(run_after):
        raise ValueError("run_after must be an aware datetime.")
    if not task.get_backend().supports_defer:
        logger.info("Not queueing %s for %s: this task backend cannot wait.", task.module_path, run_after)
        return
    enqueue_on_commit(task.using(run_after=run_after), *args, **kwargs)


def _enqueue(task: Task[..., Any], args: list[Any], kwargs: dict[str, Any]) -> None:
    """Enqueue now; a failure is logged and alerts the Webmasters instead of raising."""
    try:
        task.enqueue(*args, **kwargs)
    except Exception as exc:
        logger.exception("Could not enqueue task %s", task.module_path)
        lines = str(exc).splitlines() or [type(exc).__name__]
        alert_task_failed(
            task_path=task.module_path,
            task_name=task.name,
            finished_at=timezone.now(),
            error_lines=[f"It could not be queued: {lines[0]}", *lines[1:]],
        )


@receiver(task_finished)
def _alert_on_failure(sender: type, task_result: TaskResult[..., Any], **kwargs: Any) -> None:
    """Alert the Webmasters when a task finishes FAILED, the way a failed scheduled job does.

    The worker sends ``task_finished`` from inside its own error handling, so this never
    raises: a raise here would stop the worker.
    """
    if task_result.status != TaskResultStatus.FAILED:
        return
    try:
        alert_task_failed(
            task_path=task_result.task.module_path,
            task_name=task_result.task.name,
            finished_at=task_result.finished_at,
            error_lines=error_message_lines(task_result.errors[-1]),
        )
    except Exception:
        logger.exception("Could not alert the Webmasters that task %s failed", task_result.id)


def error_message_lines(error: TaskError) -> list[str]:
    """The failed exception's message, as ``str(exc)`` would give it, cut from its traceback.

    A task result keeps the traceback, not the exception, and the traceback ends with the
    exception's own line, ``"ValueError: no send"`` (``"psycopg.errors.UniqueViolation: ..."``
    for one outside the builtins), followed by any further lines of a multi line message.
    The alert shows only the first, because later lines can carry member data. A message
    that cannot be found falls back to the exception's class name.
    """
    path = error.exception_class_path
    module, _, _name = path.partition(".")
    prefix = path.removeprefix(f"{module}.") if module in ("builtins", "__main__") else path
    lines = error.traceback.splitlines()
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if line.startswith(f"{prefix}: "):
            return [line.removeprefix(f"{prefix}: "), *lines[index + 1 :]]
    return [prefix.rpartition(".")[2]]


def abandon_stuck_tasks(now: datetime) -> int:
    """Mark every task RUNNING longer than :data:`ABANDONED_AFTER` as FAILED; return how many.

    The worker records a task as running before it calls it, and a worker killed mid run
    (a deploy past its grace period, an out of memory kill) leaves it so for ever: the
    worker only picks up READY rows. Failing it clears the queue's stuck check and puts it
    in the failed list, where :func:`alert_unreported_failures` alerts about it.
    """
    from django_tasks_db.models import DBTaskResult

    error = TaskAbandonedError(
        f"The worker stopped while this task was running; it was still running after {ABANDONED_AFTER.total_seconds() // 3600:.0f} hours."
    )
    class_path = f"{TaskAbandonedError.__module__}.{TaskAbandonedError.__qualname__}"
    return (
        DBTaskResult.objects.running()
        .filter(started_at__lt=now - ABANDONED_AFTER)
        .update(
            status=TaskResultStatus.FAILED,
            finished_at=now,
            exception_class_path=class_path,
            traceback=f"{class_path}: {error}\n",
        )
    )


def alert_unreported_failures(since: datetime) -> list[str]:
    """Alert the Webmasters for each task with a FAILED row finished since ``since``; return the paths.

    The signal alert misses a failure the worker records without sending ``task_finished``
    (a task whose code no longer imports) and one :func:`abandon_stuck_tasks` wrote. This
    sweeps every failure instead; the alert's ledger (task plus the Pacific date it failed)
    lets nothing through twice, so a failure the signal already reported stays silent.
    The newest failure of each task speaks for it.
    """
    from django_tasks_db.models import DBTaskResult

    latest: dict[str, Any] = {}
    for row in DBTaskResult.objects.failed().filter(finished_at__gte=since).order_by("task_path", "-finished_at"):
        latest.setdefault(row.task_path, row)
    for row in latest.values():
        alert_task_failed(
            task_path=row.task_path,
            task_name=row.task_name,
            finished_at=row.finished_at,
            error_lines=error_message_lines(
                TaskError(exception_class_path=row.exception_class_path, traceback=row.traceback)
            ),
        )
    return list(latest)
