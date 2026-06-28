"""Scheduled-event due-window math (§2.6) — anchor ± offset against a tick window."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from core.events.scheduling import DueWindow, due_anchor_window, fire_time, is_due


def describe_fire_time():
    def it_adds_a_negative_offset_for_before_the_anchor():
        anchor = timezone.now()
        assert fire_time(anchor, timedelta(hours=-48)) == anchor - timedelta(hours=48)

    def it_adds_a_positive_offset_for_after_the_anchor():
        anchor = timezone.now()
        assert fire_time(anchor, timedelta(hours=24)) == anchor + timedelta(hours=24)


def describe_due_anchor_window():
    def it_is_due_when_fire_time_lands_in_the_window():
        now = timezone.now()
        anchor = now + timedelta(hours=48)  # 48h-before fires exactly now
        window = due_anchor_window(anchor, timedelta(hours=-48), now=now)
        assert window.is_due is True

    def it_is_not_due_before_the_fire_time():
        now = timezone.now()
        anchor = now + timedelta(hours=72)  # 48h-before fires in 24h, not yet
        assert due_anchor_window(anchor, timedelta(hours=-48), now=now).is_due is False

    def it_is_not_due_after_the_window_passed():
        now = timezone.now()
        anchor = now + timedelta(hours=47)  # 48h-before fired an hour ago
        assert due_anchor_window(anchor, timedelta(hours=-48), now=now).is_due is False

    def it_is_due_at_the_start_edge_but_not_the_end_edge():
        now = timezone.now()
        # fire exactly at window_start (now): due (half-open includes start).
        at_start = due_anchor_window(now, timedelta(0), now=now)
        assert at_start.is_due is True
        # fire exactly at window_end (now + window): NOT due (half-open excludes end).
        at_end_anchor = now + timedelta(minutes=15)
        at_end = due_anchor_window(at_end_anchor, timedelta(0), now=now, window=timedelta(minutes=15))
        assert at_end.is_due is False

    def it_uses_a_custom_window_width():
        now = timezone.now()
        anchor = now + timedelta(minutes=30)
        # Default 15-min window: fire at +30 is outside.
        assert due_anchor_window(anchor, timedelta(0), now=now).is_due is False
        # 60-min window: fire at +30 is inside.
        assert due_anchor_window(anchor, timedelta(0), now=now, window=timedelta(minutes=60)).is_due is True


def describe_is_due():
    def it_is_the_due_flag_of_the_window():
        now = timezone.now()
        anchor = now + timedelta(hours=48)
        assert is_due(anchor, timedelta(hours=-48), now=now) is True

    def it_defaults_now_to_current_time():
        # anchor in the far future never fires this tick.
        anchor = timezone.now() + timedelta(days=30)
        assert is_due(anchor, timedelta(hours=-48)) is False


def describe_DueWindow():
    def it_exposes_its_fields():
        now = timezone.now()
        w = DueWindow(fire_at=now, window_start=now, window_end=now + timedelta(minutes=15))
        assert w.fire_at == now
        assert w.is_due is True
