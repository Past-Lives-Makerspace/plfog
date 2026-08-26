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


def describe_record_keyed_attempt():
    def it_allows_attempts_under_both_caps():
        allowed, reason = abuse_limits.record_keyed_attempt("spec_a", "1", hourly_limit=2, daily_limit=5)
        assert (allowed, reason) == (True, None)

    def it_isolates_counters_per_key():
        for _ in range(2):
            abuse_limits.record_keyed_attempt("spec_b", "1", hourly_limit=2, daily_limit=5)
        # Member 1 is at the cap; member 2 is untouched.
        allowed, reason = abuse_limits.record_keyed_attempt("spec_b", "1", hourly_limit=2, daily_limit=5)
        assert (allowed, reason) == (False, "hourly")
        allowed, reason = abuse_limits.record_keyed_attempt("spec_b", "2", hourly_limit=2, daily_limit=5)
        assert (allowed, reason) == (True, None)

    def it_names_the_daily_cap_when_the_hourly_one_is_loose():
        for _ in range(3):
            abuse_limits.record_keyed_attempt("spec_c", "1", hourly_limit=100, daily_limit=3)
        allowed, reason = abuse_limits.record_keyed_attempt("spec_c", "1", hourly_limit=100, daily_limit=3)
        assert (allowed, reason) == (False, "daily")


def describe_keyed_within_limits():
    def it_is_true_before_any_attempt():
        assert abuse_limits.keyed_within_limits("spec_d", "1", hourly_limit=1, daily_limit=1) is True

    def it_does_not_record_an_attempt():
        for _ in range(5):
            abuse_limits.keyed_within_limits("spec_e", "1", hourly_limit=2, daily_limit=2)
        allowed, _reason = abuse_limits.record_keyed_attempt("spec_e", "1", hourly_limit=2, daily_limit=2)
        assert allowed is True  # the peeks above consumed nothing

    def it_is_false_once_the_hourly_cap_is_reached():
        for _ in range(2):
            abuse_limits.record_keyed_attempt("spec_f", "1", hourly_limit=2, daily_limit=5)
        assert abuse_limits.keyed_within_limits("spec_f", "1", hourly_limit=2, daily_limit=5) is False

    def it_is_false_once_the_daily_cap_is_reached():
        for _ in range(2):
            abuse_limits.record_keyed_attempt("spec_g", "1", hourly_limit=100, daily_limit=2)
        assert abuse_limits.keyed_within_limits("spec_g", "1", hourly_limit=100, daily_limit=2) is False
