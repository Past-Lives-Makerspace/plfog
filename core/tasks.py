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
* **A task must be safe to run twice.** The worker records a task as running before it calls
  it, and a worker killed mid run leaves it so; a person may then run it again, and a later
  follow up may retry it on its own.
* **A failure alerts the Webmasters** through the same ``automation.failed`` alert as a failed
  scheduled job, at most once per task per Pacific day (:func:`_alert_on_failure`). Failed
  and pending tasks are listed in the admin under Background Tasks.

Under pytest the immediate backend runs a task at enqueue time, in the test's own thread.
pytest wraps a spec in a transaction that never commits, so a spec that wants the task to
run uses ``django_capture_on_commit_callbacks(execute=True)``.

``core.deferred.defer`` stays only for its existing callers until each moves here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.dispatch import receiver
from django.tasks import TaskResultStatus
from django.tasks.signals import task_finished
from django.utils.json import normalize_json

from core.scheduled_jobs import alert_task_failed

if TYPE_CHECKING:
    from django.tasks import Task, TaskResult
    from django.tasks.base import TaskError

logger = logging.getLogger(__name__)


def enqueue_on_commit(task: Task[..., Any], *args: Any, **kwargs: Any) -> None:
    """Enqueue ``task`` with these arguments once the current transaction commits.

    Outside a transaction it enqueues at once. Nothing is enqueued when the transaction
    rolls back. A failure to enqueue after the commit is logged, never raised: the save it
    follows has already committed and must not turn into an error page.

    Args:
        task: A function decorated with ``@task`` from ``django.tasks``. It must be safe
            to run twice.
        *args: JSON values only (ids, strings, numbers), never model instances.
        **kwargs: The same.

    Raises:
        TypeError: An argument is not JSON, such as a model instance or a date.
    """
    normalize_json({"args": args, "kwargs": kwargs})
    transaction.on_commit(lambda: task.enqueue(*args, **kwargs), robust=True)


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
