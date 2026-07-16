"""Specs for the ``/whats-on`` slash command handler (membership.discord_commands)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from membership.discord_commands import WHATS_ON, _event_occurrences, _whats_on
from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory

pytestmark = pytest.mark.django_db


def _content() -> str:
    return _whats_on({}, None)["data"]["content"]


def _published_offering(**kwargs) -> ClassOffering:
    return ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, is_private=False, **kwargs)


def describe_whats_on_command_definition():
    def it_is_public_content_ungated_and_immediate():
        assert WHATS_ON.name == "whats-on"
        assert (WHATS_ON.requires_link, WHATS_ON.ephemeral, WHATS_ON.defer) == (False, True, False)


def describe_whats_on():
    def it_lists_an_upcoming_event_as_a_linked_dated_line():
        starts = timezone.now() + timedelta(days=2)
        event = CommunityEventFactory(title="Monthly Potluck", starts_at=starts, ends_at=starts + timedelta(hours=2))
        content = _content()
        assert "Monthly Potluck" in content
        assert event.public_url in content
        assert "**Events**" in content

    def it_lists_an_upcoming_class_session():
        offering = _published_offering(title="Intro to Lampworking")
        ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=3))
        content = _content()
        assert "Intro to Lampworking" in content
        assert offering.public_url in content
        assert "**Classes**" in content

    def describe_the_seven_day_window():
        def it_includes_an_event_on_the_final_day_and_excludes_the_day_after():
            frm = timezone.localdate()
            in_window = timezone.now() + timedelta(days=7)
            out_window = timezone.now() + timedelta(days=8)
            CommunityEventFactory(title="LastDayEvent", starts_at=in_window, ends_at=in_window + timedelta(hours=1))
            CommunityEventFactory(title="TooLateEvent", starts_at=out_window, ends_at=out_window + timedelta(hours=1))
            # Sanity: window upper bound is today + 7.
            assert frm + timedelta(days=7) == (timezone.localtime(in_window).date())
            content = _content()
            assert "LastDayEvent" in content
            assert "TooLateEvent" not in content

    def describe_a_monthly_event_with_a_past_anchor():
        def it_still_surfaces_via_occurrence_expansion():
            # Anchor two months back — a plain starts_at filter would drop it, but the series
            # keeps recurring, so its projected occurrence must appear (the occurrences_in gotcha).
            anchor = timezone.now() - timedelta(days=60)
            event = CommunityEventFactory(
                title="Recurring Guild Night",
                recurrence=CommunityEvent.Recurrence.MONTHLY,
                starts_at=anchor,
                ends_at=anchor + timedelta(hours=2),
            )
            today = timezone.localdate()
            occurrences = event.occurrences_in(today, today + timedelta(days=90))
            assert occurrences  # a monthly series projects forward off a past anchor
            target = timezone.localtime(occurrences[0]).date()

            items = _event_occurrences(target, target)

            assert "Recurring Guild Night" in [title for _dt, title, _url in items]

    def describe_with_only_events():
        def it_omits_the_empty_classes_heading():
            starts = timezone.now() + timedelta(days=1)
            CommunityEventFactory(title="Solo Event", starts_at=starts, ends_at=starts + timedelta(hours=1))
            content = _content()
            assert "**Events**" in content
            assert "**Classes**" not in content

    def describe_with_more_than_the_cap():
        def it_appends_an_and_more_calendar_tail():
            for i in range(9):
                starts = timezone.now() + timedelta(days=1, hours=i)
                CommunityEventFactory(title=f"Ev{i}", starts_at=starts, ends_at=starts + timedelta(hours=1))
            content = _content()
            assert "…and more — full calendar:" in content

    def describe_when_nothing_is_scheduled():
        def it_returns_the_exact_empty_state_with_a_calendar_link(settings):
            settings.MEMBER_BASE_URL = "https://members.example"
            content = _content()
            assert "Nothing scheduled in the next 7 days." in content
            assert "https://members.example/calendar/" in content
