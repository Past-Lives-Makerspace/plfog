"""BDD specs for the needs_attention published-but-unapproved fix (MeetingQuerySet).

``needs_attention`` used to only surface unapproved DRAFT meetings; a PUBLISHED
meeting whose date has slipped by with the minutes never approved silently
dropped out of the reminder. The fix excludes only APPROVED, so a past-dated
PUBLISHED meeting still surfaces.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from membership.models import Meeting
from tests.membership.factories import MeetingFactory

pytestmark = pytest.mark.django_db


def describe_needs_attention():
    def it_includes_a_published_meeting_dated_in_the_past():
        published = MeetingFactory(published=True, scheduled_date=timezone.localdate() - timedelta(days=2))
        assert published in Meeting.objects.needs_attention()

    def it_excludes_an_approved_meeting_dated_in_the_past():
        approved = MeetingFactory(approved=True, scheduled_date=timezone.localdate() - timedelta(days=2))
        assert approved not in Meeting.objects.needs_attention()

    def it_still_includes_a_past_dated_draft():
        draft = MeetingFactory(scheduled_date=timezone.localdate() - timedelta(days=2))
        assert draft in Meeting.objects.needs_attention()

    def it_still_includes_an_undated_draft():
        undated = MeetingFactory(scheduled_date=None)
        assert undated in Meeting.objects.needs_attention()

    def it_excludes_a_published_meeting_dated_in_the_future():
        MeetingFactory(published=True, scheduled_date=timezone.localdate() + timedelta(days=2))
        assert Meeting.objects.needs_attention().count() == 0
