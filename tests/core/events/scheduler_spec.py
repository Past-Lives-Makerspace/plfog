"""Generalized scheduled-event walker (§2.5/§2.6) — anchor ± offset → emit, deduped."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from core.events.registry import Channel
from core.events.scheduler import ScheduledOccurrence, flush_digests, run_due, run_sources
from core.models import EventDelivery


def _occurrence(*, anchor, offset, period, member=None, title="Hi", body="There"):
    """A minimal single-user occurrence for the ``new_login`` event (in_app + email).

    ``new_login`` resolves via SINGLE_USER (the explicit ``user`` in context) and
    has an in-app channel, so it exercises the per-recipient fan-out + dedupe.
    """
    return ScheduledOccurrence(
        event_key="new_login",
        anchor=anchor,
        offset=offset,
        context={"user": member.user if member is not None else None},
        period=period,
        title=title,
        body=body,
    )


def describe_run_due():
    def it_fires_a_due_occurrence_and_dedupes_on_event_delivery(db, linked_member):
        member = linked_member()
        now = timezone.now()
        anchor = now + timedelta(hours=48)  # 48h-before fires exactly now
        occ = _occurrence(anchor=anchor, offset=timedelta(hours=-48), period="2026-06", member=member)

        fired = run_due([occ], now=now)

        assert fired == 1
        assert EventDelivery.objects.filter(
            event_key="new_login",
            target_ref=f"user:{member.user.pk}",
            channel=Channel.IN_APP.value,
            period="2026-06",
        ).exists()

    def it_does_not_fire_an_occurrence_outside_the_window(db, linked_member):
        member = linked_member()
        now = timezone.now()
        anchor = now + timedelta(hours=72)  # 48h-before fires in 24h, not this tick
        occ = _occurrence(anchor=anchor, offset=timedelta(hours=-48), period="2026-06", member=member)

        assert run_due([occ], now=now) == 0
        assert EventDelivery.objects.count() == 0

    def it_is_idempotent_across_reruns_in_the_same_window(db, linked_member):
        member = linked_member()
        now = timezone.now()
        anchor = now + timedelta(hours=48)
        occ = _occurrence(anchor=anchor, offset=timedelta(hours=-48), period="2026-06", member=member)

        assert run_due([occ], now=now) == 1
        # A re-run within the same tick window must not re-deliver (EventDelivery dedupe).
        again = _occurrence(anchor=anchor, offset=timedelta(hours=-48), period="2026-06", member=member)
        assert run_due([again], now=now) == 0

    def it_isolates_one_occurrence_per_period_bucket(db, linked_member):
        member = linked_member()
        now = timezone.now()
        anchor = now + timedelta(hours=48)
        run_due([_occurrence(anchor=anchor, offset=timedelta(hours=-48), period="2026-06", member=member)], now=now)
        # A different period (next month) for the same user re-fires.
        fired = run_due(
            [_occurrence(anchor=anchor, offset=timedelta(hours=-48), period="2026-07", member=member)], now=now
        )
        assert fired == 1


def describe_run_sources():
    def it_walks_each_source_and_fires_due_occurrences(db, linked_member):
        m1 = linked_member()
        m2 = linked_member()
        now = timezone.now()
        anchor = now + timedelta(hours=48)

        def source_a(current):
            yield _occurrence(anchor=anchor, offset=timedelta(hours=-48), period="a", member=m1)

        def source_b(current):
            # one due, one not
            yield _occurrence(anchor=anchor, offset=timedelta(hours=-48), period="b", member=m2)
            yield _occurrence(anchor=now + timedelta(days=10), offset=timedelta(hours=-48), period="future", member=m2)

        fired = run_sources([source_a, source_b], now=now)
        assert fired == 2

    def it_defaults_now_to_current_time(db):
        # An empty source list fires nothing and does not raise.
        assert run_sources([]) == 0


def describe_ScheduledOccurrence():
    def it_reports_due_via_the_window_math():
        now = timezone.now()
        occ = ScheduledOccurrence(event_key="new_login", anchor=now + timedelta(hours=48), offset=timedelta(hours=-48))
        assert occ.is_due(now=now) is True
        not_yet = ScheduledOccurrence(
            event_key="new_login", anchor=now + timedelta(hours=72), offset=timedelta(hours=-48)
        )
        assert not_yet.is_due(now=now) is False


def describe_flush_digests():
    def it_drains_buffered_digest_rows(db):
        # A PENDING digest row should be grouped + flipped to SENT by the flush.
        EventDelivery.objects.create(
            event_key="class_published",
            target_ref="user:1",
            channel=Channel.DIGEST.value,
            period="digest",
            status=EventDelivery.Status.PENDING,
            title="Buffered",
            body="Body",
        )
        grouped = flush_digests()
        assert "user:1" in grouped
        assert (
            EventDelivery.objects.get(target_ref="user:1", channel=Channel.DIGEST.value).status
            == EventDelivery.Status.SENT
        )
