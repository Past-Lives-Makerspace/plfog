"""BDD-style tests for core.abuse_limits — global login-code circuit breaker."""

from __future__ import annotations

import pytest

from core import abuse_limits


@pytest.fixture(autouse=True)
def _reset_counters():
    abuse_limits.reset()
    yield
    abuse_limits.reset()


def describe_record_send_attempt():
    def it_allows_sends_under_both_caps():
        allowed, reason = abuse_limits.record_send_attempt(hourly_limit=10, daily_limit=100)
        assert allowed is True
        assert reason is None

    def it_increments_both_counters_on_each_call():
        for _ in range(3):
            abuse_limits.record_send_attempt(hourly_limit=10, daily_limit=100)
        counts = abuse_limits.current_counts()
        assert counts["hourly"] == 3
        assert counts["daily"] == 3

    def it_blocks_when_hourly_cap_exceeded():
        for _ in range(5):
            abuse_limits.record_send_attempt(hourly_limit=5, daily_limit=100)
        allowed, reason = abuse_limits.record_send_attempt(hourly_limit=5, daily_limit=100)
        assert allowed is False
        assert reason == "hourly"

    def it_blocks_when_daily_cap_exceeded_even_if_hourly_is_loose():
        for _ in range(3):
            abuse_limits.record_send_attempt(hourly_limit=1000, daily_limit=3)
        allowed, reason = abuse_limits.record_send_attempt(hourly_limit=1000, daily_limit=3)
        assert allowed is False
        assert reason == "daily"

    def it_reports_hourly_first_when_both_caps_exceeded():
        for _ in range(2):
            abuse_limits.record_send_attempt(hourly_limit=2, daily_limit=2)
        allowed, reason = abuse_limits.record_send_attempt(hourly_limit=2, daily_limit=2)
        assert allowed is False
        assert reason == "hourly"

    def it_keeps_incrementing_counters_after_blocking_so_logs_show_real_volume():
        for _ in range(10):
            abuse_limits.record_send_attempt(hourly_limit=2, daily_limit=100)
        counts = abuse_limits.current_counts()
        assert counts["hourly"] == 10


def describe_reset():
    def it_clears_counters():
        for _ in range(5):
            abuse_limits.record_send_attempt(hourly_limit=100, daily_limit=100)
        abuse_limits.reset()
        assert abuse_limits.current_counts() == {"hourly": 0, "daily": 0}


def describe_bump_race_condition_fallback():
    """When cache.add reports the key exists but cache.incr raises ValueError
    (the key expired in between), the counter is reseeded at 1 instead of
    crashing the request flow.
    """

    def it_reseeds_when_incr_raises_value_error(monkeypatch):
        from django.core.cache import cache

        original_add = cache.add
        original_set = cache.set

        def fake_add(*args, **kwargs):
            return False

        def fake_incr(*args, **kwargs):
            raise ValueError("key expired between add and incr")

        recorded: list[tuple] = []

        def recording_set(key, value, ttl):
            recorded.append((key, value, ttl))
            return original_set(key, value, ttl)

        monkeypatch.setattr(cache, "add", fake_add)
        monkeypatch.setattr(cache, "incr", fake_incr)
        monkeypatch.setattr(cache, "set", recording_set)

        allowed, reason = abuse_limits.record_send_attempt(hourly_limit=10, daily_limit=10)

        assert allowed is True
        assert reason is None
        assert len(recorded) == 2

        monkeypatch.setattr(cache, "add", original_add)
