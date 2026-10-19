"""Run slow work after the current transaction commits, on a background thread.

A web request that has to tell many people something (an event's member announcement:
one email, bell and push per member) must not hold the response while it does, because
gunicorn kills a request at 30 seconds. :func:`defer` hands the work to a thread that
starts once the transaction commits, so the thread reads committed rows and nothing runs
when the save rolls back.

Why a thread, not a response close hook: in a gunicorn sync worker the close hook still
runs on the worker's main loop, which stops the worker's heartbeat, so WORKER TIMEOUT
kills it exactly as before. A thread leaves the main loop free. The thread is non-daemon,
so a worker or a management command that exits normally waits for it to finish.

What a thread cannot promise is survival: a redeploy or an out of memory kill ends it
mid send. Every caller therefore needs a backstop that finds the unfinished work from the
database and completes it (for events, ``CommunityEvent.announcement_owed_since`` and the
``finish_event_announcements`` job), and the work itself must be safe to run twice.

Under test, the root ``conftest.py`` swaps :data:`_schedule` for :func:`run_inline`, so
deferred work runs at the call, deterministically; a spec that exercises the real thread
puts :func:`after_commit_thread` back.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from django.db import connections, transaction

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

#: The prefix on every deferred thread's name, so a spec (or a person reading a stack dump)
#: can find them.
THREAD_NAME_PREFIX = "deferred:"


def defer(task: Callable[[], None], *, name: str) -> None:
    """Run ``task`` after the current transaction commits, without holding the caller.

    Args:
        task: The work. It must be safe to run twice, because its backstop may also run it.
        name: A short label for logs and the thread name (``"event:31:announce"``).
    """
    _schedule(task, name)


def after_commit_thread(task: Callable[[], None], name: str) -> None:
    """The production schedule: start a non-daemon thread once the transaction commits."""
    transaction.on_commit(lambda: _start_thread(task, name))


def run_inline(task: Callable[[], None], name: str) -> None:
    """The test schedule: run the task now, in this thread, with the same failure logging."""
    _guarded(task, name)


def _start_thread(task: Callable[[], None], name: str) -> None:
    thread = threading.Thread(
        target=_run_in_thread,
        args=(task, name),
        name=f"{THREAD_NAME_PREFIX}{name}",
        daemon=False,
    )
    thread.start()


def _run_in_thread(task: Callable[[], None], name: str) -> None:
    """Run the task, then close the database connections Django opened for this thread.

    Django keeps one connection per thread and only closes a request thread's at the end
    of the request; a thread of our own has no such end, so without this each deferred
    task would leave a Postgres connection open until the worker exits.
    """
    try:
        _guarded(task, name)
    finally:
        connections.close_all()


def _guarded(task: Callable[[], None], name: str) -> None:
    """Run the task and log a failure instead of raising: nobody is waiting to catch it."""
    try:
        task()
    except Exception:
        logger.exception("Deferred task %s failed; its backstop will finish it", name)


#: How :func:`defer` schedules work. A module attribute so the test suite can swap it.
_schedule: Callable[[Callable[[], None], str], None] = after_commit_thread
