"""Generalized scheduled-event walker (design §2.5 / §2.6).

The proven ``send_due_class_reminders`` shape — *walk a set of candidates, fire the
ones whose anchor ± offset just crossed the tick window, dedupe so a re-run is
safe* — is generalized here so EVERY scheduled event rides one path:

1. a **source** yields :class:`ScheduledOccurrence` candidates (each carries the
   ``event_key``, the timing ``anchor``/``offset``, the resolver ``context``, the
   dedupe ``period``, and any per-channel message/attachment overrides);
2. :func:`run_due` keeps the occurrences whose fire time lands in this tick's
   ``[now, now + window)`` window (:func:`core.events.scheduling.is_due`);
3. each due occurrence is delivered via :func:`core.events.emit.emit`, which dedupes
   every ``(event, target, channel, period)`` on :class:`core.models.EventDelivery`
   (§2.5) — so the SAME ``EventDelivery`` ledger that backs the per-recipient
   fan-out also backs the scheduled-send idempotency. There is now **one** dedupe
   model, not three.

This replaces the per-feature dedupe rows: the class reminder's
``RegistrationReminder`` and the orientation ``is_completed``-as-dedupe collapse
onto ``EventDelivery``'s ``period`` bucket (the class reminder uses
``reg:<pk>:reminder:<session_pk>``; a future orientation 48h reminder uses
``booking:<pk>:reminder_48h`` — both one-shot via a stable ``period``).

The cron (``run_scheduled_tasks``) calls :func:`run_sources` with the registered
sources; Phase 6 (voting 48h, the digest flush) adds its source(s) here with zero
changes to the walker. :func:`flush_digests` is the documented entry point the
digest flush cron will call — it drains the buffered ``PENDING`` digest rows via
:meth:`core.events.channels.DigestAdapter.flush_due`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.events import scheduling
from core.events.emit import emit
from core.events.scheduling import DEFAULT_WINDOW

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from datetime import datetime, timedelta

    from django.db.models import Model

    from core.email import Attachment
    from core.events.channels import Message
    from core.events.emit import EmitResult
    from core.events.registry import Channel


@dataclass(frozen=True)
class ScheduledOccurrence:
    """One due-check for the scheduler: an event to fire at ``anchor + offset``.

    A source yields these; :func:`run_due` fires the ones due this tick. The
    ``period`` is the :class:`core.models.EventDelivery` dedupe bucket — make it
    stable per logical occurrence (e.g. ``reg:42:reminder:7``) so a one-shot
    scheduled send fires exactly once no matter how many ticks overlap its window.

    ``messages`` / ``attachments`` carry the same per-channel overrides
    :func:`core.events.emit.emit` accepts — a scheduled email whose body is a rich
    structural shell renders that shell and passes it here, identical to the
    immediate-send path (so a scheduled reminder email is byte-for-byte the same as
    today's).
    """

    event_key: str
    anchor: datetime
    offset: timedelta
    context: dict[str, Any] = field(default_factory=dict)
    period: str = ""
    actor: Model | None = None
    target: Model | None = None
    title: str = ""
    body: str = ""
    url: str = ""
    html_body: str | None = None
    messages: dict[Channel, Message] | None = None
    attachments: dict[Channel, list[Attachment]] | None = None
    email_to: str | list[str] | None = None

    def is_due(self, *, now: datetime | None = None, window: timedelta = DEFAULT_WINDOW) -> bool:
        """Whether this occurrence's fire time lands in the current tick window."""
        return scheduling.is_due(self.anchor, self.offset, now=now, window=window)

    def fire(self) -> EmitResult:
        """Emit this occurrence's event (dedupe + fan-out handled by ``emit``)."""
        return emit(
            self.event_key,
            actor=self.actor,
            target=self.target,
            context=self.context,
            title=self.title,
            body=self.body,
            url=self.url,
            html_body=self.html_body,
            period=self.period,
            messages=self.messages,
            attachments=self.attachments,
            email_to=self.email_to,
        )


# A source is a callable that yields the occurrences to consider this tick. It
# receives ``now`` so it can window its DB query (the proven shape: select only the
# rows whose anchor falls near the fire time, rather than scanning everything).
Source = "Callable[[datetime], Iterable[ScheduledOccurrence]]"


def run_due(
    occurrences: Iterable[ScheduledOccurrence],
    *,
    now: datetime | None = None,
    window: timedelta = DEFAULT_WINDOW,
) -> int:
    """Fire every occurrence due this tick; return the count actually delivered.

    "Delivered" counts an occurrence whose ``emit`` produced at least one fresh
    delivery (not a deduped no-op) — so a re-run within the same window reports 0,
    matching the old ``send_due_class_reminders`` return contract. Each occurrence
    fires in isolation: one failing emit must not abort the rest of the tick.
    """
    fired = 0
    for occurrence in occurrences:
        if not occurrence.is_due(now=now, window=window):
            continue
        result = occurrence.fire()
        if result.delivery_count:
            fired += 1
    return fired


def run_sources(
    sources: Iterable[Callable[[datetime], Iterable[ScheduledOccurrence]]],
    *,
    now: datetime | None = None,
    window: timedelta = DEFAULT_WINDOW,
) -> int:
    """Walk every registered source and fire its due occurrences this tick.

    The cron entry point: each source windows its own DB query against ``now`` and
    yields candidate occurrences; :func:`run_due` fires the due ones. Sources are
    independent — a source that raises is the caller's concern (the cron wraps each
    task in try/except), but within a source the per-occurrence isolation in
    :func:`run_due` still holds.
    """
    from django.utils import timezone

    current = now or timezone.now()
    fired = 0
    for source in sources:
        fired += run_due(source(current), now=current, window=window)
    return fired


def flush_digests() -> dict[str, list[int]]:
    """Drain buffered digest rows — the entry point the digest flush cron calls.

    Thin, documented re-export of
    :meth:`core.events.channels.DigestAdapter.flush_due` so the scheduler is the one
    place the cron reaches for both timed sends (:func:`run_sources`) and the digest
    batch (this). Phase 6 wires the actual digest-email assembly on top of the
    grouped result; here it simply groups + marks the pending rows sent so they do
    not accumulate.
    """
    from core.events.channels import DigestAdapter

    return DigestAdapter.flush_due()
