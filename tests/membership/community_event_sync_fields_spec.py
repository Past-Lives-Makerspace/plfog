"""BDD specs for the CommunityEvent moderation + Google-sync fields added in Phase 1:
field defaults, the four new queryset methods, and that the type↔scope constraint
still holds after the additive migration."""

from __future__ import annotations

from datetime import datetime

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory, GuildFactory


def _aware(y: int, m: int, d: int, hour: int = 12) -> datetime:
    return timezone.make_aware(datetime(y, m, d, hour, 0))


def describe_CommunityEvent_sync_fields():
    def describe_defaults():
        def it_defaults_to_published_and_idle(db):
            event = CommunityEventFactory()
            assert event.moderation_state == CommunityEvent.ModerationState.PUBLISHED
            assert event.sync_state == CommunityEvent.SyncState.IDLE

        def it_leaves_google_identifiers_and_review_fields_blank(db):
            event = CommunityEventFactory()
            assert event.google_event_id == ""
            assert event.google_calendar_id == ""
            assert event.google_ical_uid == ""
            assert event.sync_error == ""
            assert event.review_notes == ""
            assert event.submitted_by is None
            assert event.reviewed_by is None
            assert event.reviewed_at is None
            assert event.synced_at is None

    def describe_queryset_published():
        def it_returns_only_published_rows(db):
            live = CommunityEventFactory()
            CommunityEventFactory(pending=True)
            CommunityEventFactory(declined=True)
            assert list(CommunityEvent.objects.published()) == [live]

    def describe_queryset_awaiting_review():
        def it_returns_only_pending_rows(db):
            pending = CommunityEventFactory(pending=True)
            CommunityEventFactory()  # published
            CommunityEventFactory(moderation_state=CommunityEvent.ModerationState.CHANGES_REQUESTED)
            assert list(CommunityEvent.objects.awaiting_review()) == [pending]

    def describe_queryset_pushed():
        def it_returns_only_rows_with_a_google_ical_uid(db):
            pushed = CommunityEventFactory(google_ical_uid="abc123@google.com")
            CommunityEventFactory()  # blank uid — not pushed
            assert list(CommunityEvent.objects.pushed()) == [pushed]

    def describe_queryset_needs_push():
        def it_returns_published_rows_pending_or_failed(db):
            pending = CommunityEventFactory(sync_state=CommunityEvent.SyncState.PENDING)
            failed = CommunityEventFactory(sync_state=CommunityEvent.SyncState.FAILED)
            CommunityEventFactory(sync_state=CommunityEvent.SyncState.SYNCED)  # already synced
            CommunityEventFactory(sync_state=CommunityEvent.SyncState.IDLE)  # not opted in
            result = set(CommunityEvent.objects.needs_push())
            assert result == {pending, failed}

        def it_excludes_an_unpublished_row_even_if_pending(db):
            # A pending-review proposal that somehow carries sync_state=PENDING is still
            # never in the push set — only PUBLISHED rows push.
            CommunityEventFactory(
                pending=True,
                sync_state=CommunityEvent.SyncState.PENDING,
            )
            assert list(CommunityEvent.objects.needs_push()) == []

    def describe_constraint_still_holds():
        def it_still_rejects_end_equal_to_start(db):
            guild = GuildFactory()
            with pytest.raises(IntegrityError), transaction.atomic():
                CommunityEvent.objects.create(
                    title="Bad",
                    event_type=CommunityEvent.EventType.GUILD_MEETING,
                    guild=guild,
                    starts_at=_aware(2026, 7, 11),
                    ends_at=_aware(2026, 7, 11),
                )

        def it_still_rejects_a_guild_meeting_with_no_guild(db):
            with pytest.raises(IntegrityError), transaction.atomic():
                CommunityEvent.objects.create(
                    title="Bad type/scope",
                    event_type=CommunityEvent.EventType.GUILD_MEETING,
                    guild=None,
                    starts_at=_aware(2026, 7, 11, 18),
                    ends_at=_aware(2026, 7, 11, 20),
                )
