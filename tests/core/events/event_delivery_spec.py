"""EventDelivery — the idempotency ledger keyed (event, target, channel, period)."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from core.models import EventDelivery

pytestmark = pytest.mark.django_db


def describe_EventDelivery():
    def describe_uniqueness():
        def it_enforces_the_four_part_unique_key():
            EventDelivery.objects.create(event_key="class_published", target_ref="user:1", channel="in_app", period="")
            with pytest.raises(IntegrityError):
                with transaction.atomic():
                    EventDelivery.objects.create(
                        event_key="class_published", target_ref="user:1", channel="in_app", period=""
                    )

        def it_allows_the_same_target_on_a_different_channel():
            EventDelivery.objects.create(event_key="class_published", target_ref="user:1", channel="in_app", period="")
            EventDelivery.objects.create(event_key="class_published", target_ref="user:1", channel="email", period="")
            assert EventDelivery.objects.count() == 2

        def it_allows_the_same_target_in_a_different_period():
            EventDelivery.objects.create(
                event_key="voting_closing_soon", target_ref="user:1", channel="email", period="2026-06"
            )
            EventDelivery.objects.create(
                event_key="voting_closing_soon", target_ref="user:1", channel="email", period="2026-07"
            )
            assert EventDelivery.objects.count() == 2

    def describe_get_or_create():
        def it_creates_then_finds_the_same_row():
            kwargs = dict(event_key="tab_charged", target_ref="user:5", channel="email", period="")
            _row, created_first = EventDelivery.objects.get_or_create(**kwargs)
            _row2, created_second = EventDelivery.objects.get_or_create(**kwargs)
            assert created_first is True
            assert created_second is False
            assert EventDelivery.objects.count() == 1

    def describe_str():
        def it_renders_a_readable_label_for_one_shot():
            row = EventDelivery(event_key="class_published", target_ref="user:7", channel="in_app", period="")
            assert str(row) == "class_published→user:7[in_app]"

        def it_includes_the_period_when_present():
            row = EventDelivery(event_key="voting_closing_soon", target_ref="user:7", channel="email", period="2026-06")
            assert str(row) == "voting_closing_soon→user:7[email]@2026-06"
