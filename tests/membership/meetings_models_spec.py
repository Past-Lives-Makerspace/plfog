"""BDD specs for the Meeting aggregate (scheduling only since #1113): display/scope
properties, the window querysets, and calendar-event ownership (create/link/sync/unlink/remove)."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone
from unittest.mock import patch

from core.models import EventDelivery
from membership.models import (
    CommunityEvent,
    Meeting,
)
from tests.membership.factories import (
    CommunityEventFactory,
    GuildFactory,
    MeetingFactory,
    MeetingItemProposalFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def _user(username: str) -> User:
    """A signed-in user with an auto-provisioned linked Member (activation-gated resolvers
    require ``last_login``)."""
    MembershipPlanFactory()
    return User.objects.create_user(
        username=username, email=f"{username}@example.com", password="x", last_login=timezone.now()
    )


def _deliveries(event_key: str) -> int:
    return EventDelivery.objects.filter(event_key=event_key, channel="in_app").count()


def describe_Meeting():
    def describe_display_title():
        def it_names_a_guild_monthly_meeting():
            meeting = MeetingFactory(guild=GuildFactory(name="Woodshop"))
            assert meeting.display_title == "Woodshop — Monthly Meeting"

        def it_names_a_named_special_meeting():
            meeting = MeetingFactory(guild=GuildFactory(name="Woodshop"), is_special=True, special_title="Emergency")
            assert meeting.display_title == "Woodshop — Emergency Meeting"

        def it_falls_back_to_special_when_the_special_has_no_name():
            meeting = MeetingFactory(guild=GuildFactory(name="Woodshop"), is_special=True)
            assert meeting.display_title == "Woodshop — Special Meeting"

        def it_names_the_council_scope_when_guild_is_blank():
            meeting = MeetingFactory(guild=None)
            assert meeting.display_title == "Council — Monthly Meeting"
            assert meeting.scope_label == "Council"

    def describe_str():
        def it_appends_the_date_when_scheduled():
            meeting = MeetingFactory(guild=GuildFactory(name="Forge"), scheduled_date=timezone.localdate())
            assert str(meeting) == f"Forge — Monthly Meeting ({timezone.localdate():%Y-%m-%d})"

        def it_says_no_date_for_an_undated_draft():
            meeting = MeetingFactory(scheduled_date=None)
            assert str(meeting).endswith("(no date)")

    def describe_starts_at():
        def it_combines_date_and_time_into_an_aware_datetime():
            meeting = MeetingFactory(scheduled_date=timezone.localdate(), scheduled_time=time(18, 30))
            expected = timezone.make_aware(datetime.combine(timezone.localdate(), time(18, 30)))
            assert meeting.starts_at == expected

        def it_uses_midnight_when_no_time_is_set():
            meeting = MeetingFactory(scheduled_date=timezone.localdate(), scheduled_time=None)
            assert meeting.starts_at == timezone.make_aware(datetime.combine(timezone.localdate(), time()))

        def it_is_none_while_undated():
            assert MeetingFactory(scheduled_date=None).starts_at is None

    def describe_ends_at():
        def it_combines_the_date_and_end_time():
            meeting = MeetingFactory(scheduled_date=timezone.localdate(), scheduled_end_time=time(19, 30))
            expected = timezone.make_aware(datetime.combine(timezone.localdate(), time(19, 30)))
            assert meeting.ends_at == expected

        def it_is_none_without_an_end_time():
            assert MeetingFactory(scheduled_date=timezone.localdate(), scheduled_end_time=None).ends_at is None

        def it_is_none_without_a_date():
            assert MeetingFactory(scheduled_date=None, scheduled_end_time=time(19, 30)).ends_at is None

    def describe_constraints():
        def it_rejects_a_special_title_on_a_non_special_meeting():
            with pytest.raises(IntegrityError), transaction.atomic():
                MeetingFactory(is_special=False, special_title="Sneaky")

        def it_allows_a_special_title_on_a_special_meeting():
            meeting = MeetingFactory(is_special=True, special_title="Planning")
            assert meeting.pk is not None

    def describe_queryset():
        def describe_upcoming():
            def it_orders_ascending_so_first_is_the_soonest():
                today = timezone.localdate()
                later = MeetingFactory(scheduled_date=today + timedelta(days=14))
                soonest = MeetingFactory(scheduled_date=today + timedelta(days=2))
                MeetingFactory(scheduled_date=today - timedelta(days=1))  # past — excluded
                assert list(Meeting.objects.upcoming()) == [soonest, later]
                assert Meeting.objects.upcoming().first() == soonest

            def it_breaks_a_date_tie_by_time_with_blank_times_last():
                day = timezone.localdate() + timedelta(days=3)
                tbd = MeetingFactory(scheduled_date=day, scheduled_time=None)
                evening = MeetingFactory(scheduled_date=day, scheduled_time=time(18, 0))
                morning = MeetingFactory(scheduled_date=day, scheduled_time=time(9, 0))
                assert list(Meeting.objects.upcoming()) == [morning, evening, tbd]

            def it_excludes_undated_drafts():
                MeetingFactory(scheduled_date=None)
                assert Meeting.objects.upcoming().count() == 0

        def describe_past():
            def it_returns_only_meetings_dated_before_today():
                past = MeetingFactory(scheduled_date=timezone.localdate() - timedelta(days=1))
                MeetingFactory(scheduled_date=timezone.localdate())  # today is upcoming
                MeetingFactory(scheduled_date=None)
                assert list(Meeting.objects.past()) == [past]

        def describe_archive():
            def it_includes_past_meetings_and_undated_drafts_but_not_upcoming():
                today = timezone.localdate()
                old = MeetingFactory(scheduled_date=today - timedelta(days=30))
                recent = MeetingFactory(scheduled_date=today - timedelta(days=1))
                undated = MeetingFactory(scheduled_date=None)
                upcoming = MeetingFactory(scheduled_date=today + timedelta(days=1))
                rows = list(Meeting.objects.archive())
                assert rows == [recent, old, undated]  # dated newest first, undated last
                assert upcoming not in rows

            def it_is_disjoint_from_upcoming():
                today = timezone.localdate()
                MeetingFactory(scheduled_date=today - timedelta(days=1))
                MeetingFactory(scheduled_date=today)
                MeetingFactory(scheduled_date=None)
                archive_pks = set(Meeting.objects.archive().values_list("pk", flat=True))
                upcoming_pks = set(Meeting.objects.upcoming().values_list("pk", flat=True))
                assert archive_pks & upcoming_pks == set()
                assert archive_pks | upcoming_pks == set(Meeting.objects.values_list("pk", flat=True))

        def describe_undated():
            def it_returns_meetings_with_no_date_oldest_first():
                first = MeetingFactory(scheduled_date=None)
                second = MeetingFactory(scheduled_date=None)
                MeetingFactory(scheduled_date=timezone.localdate() - timedelta(days=1))
                assert list(Meeting.objects.undated()) == [first, second]

        def describe_for_scope():
            def it_isolates_a_guild_from_the_council_scope():
                guild = GuildFactory()
                guild_meeting = MeetingFactory(guild=guild)
                council_meeting = MeetingFactory(guild=None)
                MeetingFactory(guild=GuildFactory())  # another guild
                assert list(Meeting.objects.for_scope(guild)) == [guild_meeting]
                assert list(Meeting.objects.for_scope(None)) == [council_meeting]

    def describe_create_calendar_event():
        def it_builds_a_guild_meeting_event_and_pushes_it_live():
            by = _user("creator")
            guild = GuildFactory(name="Forge", meeting_location="Studio B")
            meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0))
            with patch.object(CommunityEvent, "push_live") as go_live:
                event = meeting.create_calendar_event(by=by)
            go_live.assert_called_once_with(actor=by)
            meeting.refresh_from_db()
            assert event.event_type == CommunityEvent.EventType.GUILD_MEETING
            assert event.guild == guild
            assert event.title == meeting.display_title
            assert event.starts_at == meeting.starts_at
            assert event.ends_at == event.starts_at + timedelta(minutes=90)
            assert event.location == "Studio B"
            assert event.recurrence == CommunityEvent.Recurrence.NONE
            assert meeting.event == event
            assert meeting.event_occurrence == meeting.scheduled_date
            assert meeting.owns_event is True

        def it_uses_the_scheduled_end_time_for_the_event_end():
            meeting = MeetingFactory(scheduled_time=time(18, 0), scheduled_end_time=time(19, 30))
            with patch.object(CommunityEvent, "push_live"):
                event = meeting.create_calendar_event(by=_user("cend"))
            assert event.ends_at == meeting.ends_at

        def it_falls_back_to_a_default_length_without_an_end_time():
            meeting = MeetingFactory(scheduled_time=time(18, 0), scheduled_end_time=None)
            with patch.object(CommunityEvent, "push_live"):
                event = meeting.create_calendar_event(by=_user("cdef"))
            assert event.ends_at == event.starts_at + timedelta(minutes=90)

        def it_prefers_the_video_call_url_as_the_location():
            guild = GuildFactory(meeting_location="Studio B")
            meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0), video_call_url="https://meet.example/x")
            with patch.object(CommunityEvent, "push_live"):
                event = meeting.create_calendar_event(by=_user("c2"))
            assert event.location == "https://meet.example/x"

        def it_targets_the_public_calendar_for_a_guild_meeting():
            meeting = MeetingFactory(guild=GuildFactory(), scheduled_time=time(18, 0))
            with patch.object(CommunityEvent, "push_live"):
                event = meeting.create_calendar_event(by=_user("cpub"))
            assert event.google_calendar_target == CommunityEvent.GoogleCalendarTarget.PUBLIC

        def it_targets_the_members_only_calendar_for_a_council_meeting():
            # Internal leadership meeting — its location can carry the leads' video link,
            # so it must never ride the public default.
            meeting = MeetingFactory(guild=None, scheduled_time=time(19, 0))
            with patch.object(CommunityEvent, "push_live"):
                event = meeting.create_calendar_event(by=_user("cmem"))
            assert event.google_calendar_target == CommunityEvent.GoogleCalendarTarget.MEMBER

        def it_builds_a_lead_meeting_for_the_council_without_touching_a_guild():
            meeting = MeetingFactory(guild=None, scheduled_time=time(19, 0))
            with patch.object(CommunityEvent, "push_live"):
                event = meeting.create_calendar_event(by=_user("c3"))  # guild=None must not raise
            assert event.event_type == CommunityEvent.EventType.LEAD_MEETING
            assert event.guild is None
            assert event.location == ""

        def it_requires_both_date_and_time():
            with pytest.raises(ValueError, match="date and time"):
                MeetingFactory(scheduled_time=None).create_calendar_event(by=_user("c4"))
            with pytest.raises(ValueError, match="date and time"):
                MeetingFactory(scheduled_date=None, scheduled_time=time(18, 0)).create_calendar_event(by=_user("c5"))

        def it_refuses_a_double_link():
            meeting = MeetingFactory(scheduled_time=time(18, 0))
            with patch.object(CommunityEvent, "push_live"):
                meeting.create_calendar_event(by=_user("c6"))
            with pytest.raises(ValueError, match="already linked"):
                meeting.create_calendar_event(by=_user("c7"))

    def describe_link_event():
        def it_links_without_taking_ownership():
            meeting = MeetingFactory()
            event = CommunityEventFactory(guild=meeting.guild)
            occurrence = timezone.localdate(event.starts_at)
            meeting.link_event(event, occurrence, by=_user("l1"))
            meeting.refresh_from_db()
            assert meeting.event == event
            assert meeting.event_occurrence == occurrence
            assert meeting.owns_event is False

        def it_validates_the_occurrence_date():
            meeting = MeetingFactory()
            event = CommunityEventFactory(guild=meeting.guild)
            with pytest.raises(ValueError, match="not an occurrence"):
                meeting.link_event(event, timezone.localdate(event.starts_at) + timedelta(days=1), by=_user("l2"))

        def it_accepts_a_projected_occurrence_of_a_recurring_event():
            meeting = MeetingFactory()
            event = CommunityEventFactory(guild=meeting.guild, recurrence=CommunityEvent.Recurrence.MONTHLY)
            horizon_start = timezone.localdate(event.starts_at) + timedelta(days=20)
            occurrences = event.occurrences_in(horizon_start, horizon_start + timedelta(days=40))
            meeting.link_event(event, occurrences[0].date(), by=_user("l3"))
            assert meeting.event_occurrence == occurrences[0].date()

        def it_refuses_when_already_linked():
            meeting = MeetingFactory()
            event = CommunityEventFactory(guild=meeting.guild)
            meeting.link_event(event, timezone.localdate(event.starts_at), by=_user("l4"))
            with pytest.raises(ValueError, match="already linked"):
                meeting.link_event(event, timezone.localdate(event.starts_at), by=_user("l5"))

    def describe_sync_event():
        @pytest.fixture
        def owned(db):
            """A meeting owning its calendar event (created through the workspace path)."""
            guild = GuildFactory(meeting_location="Studio B")
            meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0))
            with patch.object(CommunityEvent, "push_live"):
                meeting.create_calendar_event(by=_user("owner"))
            meeting.refresh_from_db()
            return meeting

        def it_repositions_the_owned_event_preserving_the_duration(owned):
            owned.scheduled_date = timezone.localdate() + timedelta(days=21)
            owned.scheduled_time = time(19, 30)
            owned.save(update_fields=["scheduled_date", "scheduled_time"])
            with (
                patch.object(CommunityEvent, "push_to_google") as google,
                patch.object(CommunityEvent, "push_to_discord") as discord,
                patch.object(CommunityEvent, "announce") as announce,
            ):
                owned.sync_event()
            event = CommunityEvent.objects.get(pk=owned.event_id)
            assert event.starts_at == owned.starts_at
            assert event.ends_at == event.starts_at + timedelta(minutes=90)
            owned.refresh_from_db()
            assert owned.event_occurrence == owned.scheduled_date
            google.assert_called_once()
            discord.assert_called_once()
            announce.assert_not_called()  # a reschedule is a sync, never a re-announcement

        def it_pushes_a_scheduled_end_time_to_the_owned_event(owned):
            owned.scheduled_end_time = time(20, 0)
            owned.save(update_fields=["scheduled_end_time"])
            with (
                patch.object(CommunityEvent, "push_to_google"),
                patch.object(CommunityEvent, "push_to_discord"),
            ):
                owned.sync_event()
            event = CommunityEvent.objects.get(pk=owned.event_id)
            assert event.ends_at == owned.ends_at

        def it_propagates_a_changed_video_call_url_into_the_location(owned):
            owned.video_call_url = "https://meet.example/new"
            owned.save(update_fields=["video_call_url"])
            with patch.object(CommunityEvent, "push_to_google"), patch.object(CommunityEvent, "push_to_discord"):
                owned.sync_event()
            assert CommunityEvent.objects.get(pk=owned.event_id).location == "https://meet.example/new"

        def it_falls_back_to_the_guild_location_when_the_video_url_is_cleared(owned):
            owned.video_call_url = "https://meet.example/old"
            owned.save(update_fields=["video_call_url"])
            owned.video_call_url = ""
            owned.save(update_fields=["video_call_url"])
            with patch.object(CommunityEvent, "push_to_google"), patch.object(CommunityEvent, "push_to_discord"):
                owned.sync_event()
            assert CommunityEvent.objects.get(pk=owned.event_id).location == "Studio B"

        def it_leaves_the_event_in_place_when_the_date_is_cleared(owned):
            original_starts = CommunityEvent.objects.get(pk=owned.event_id).starts_at
            owned.scheduled_date = None
            owned.save(update_fields=["scheduled_date"])
            with patch.object(CommunityEvent, "push_to_google"), patch.object(CommunityEvent, "push_to_discord"):
                owned.sync_event()
            assert CommunityEvent.objects.get(pk=owned.event_id).starts_at == original_starts

        def it_never_mutates_a_merely_linked_event():
            meeting = MeetingFactory()
            event = CommunityEventFactory(guild=meeting.guild, title="Their event")
            meeting.link_event(event, timezone.localdate(event.starts_at), by=_user("s1"))
            with patch.object(CommunityEvent, "push_to_google") as google:
                meeting.sync_event()
            google.assert_not_called()
            event.refresh_from_db()
            assert event.title == "Their event"

        def it_is_a_no_op_without_a_linked_event():
            MeetingFactory().sync_event()  # must not raise

    def describe_unlink_event():
        def it_clears_the_link_fields_and_leaves_the_event_row_alone():
            editor = _user("u1")
            meeting = MeetingFactory()
            event = CommunityEventFactory(guild=meeting.guild)
            meeting.link_event(event, timezone.localdate(event.starts_at), by=editor)
            meeting.unlink_event(by=editor)
            meeting.refresh_from_db()
            assert meeting.event is None
            assert meeting.event_occurrence is None
            assert meeting.owns_event is False
            assert CommunityEvent.objects.filter(pk=event.pk).exists()

        def it_leaves_even_a_formerly_owned_event_on_the_calendar():
            editor = _user("u3")
            meeting = MeetingFactory(scheduled_time=time(18, 0))
            with patch.object(CommunityEvent, "push_live"):
                event = meeting.create_calendar_event(by=editor)
            meeting.refresh_from_db()
            meeting.unlink_event(by=editor)
            meeting.refresh_from_db()
            assert meeting.owns_event is False
            assert CommunityEvent.objects.filter(pk=event.pk).exists()  # becomes a normal event

        def it_requires_a_linked_event():
            with pytest.raises(ValueError, match="No calendar event"):
                MeetingFactory().unlink_event(by=_user("u2"))

    def describe_remove():
        def it_unwinds_and_deletes_an_owned_event_then_the_meeting():
            editor = _user("r1")
            meeting = MeetingFactory(scheduled_time=time(18, 0))
            with patch.object(CommunityEvent, "push_live"):
                meeting.create_calendar_event(by=editor)
            meeting.refresh_from_db()
            event_pk = meeting.event_id
            with (
                patch.object(CommunityEvent, "remove_from_google") as google,
                patch.object(CommunityEvent, "remove_from_discord") as discord,
            ):
                meeting.remove(by=editor)
            google.assert_called_once()
            discord.assert_called_once()
            assert not CommunityEvent.objects.filter(pk=event_pk).exists()
            assert not Meeting.objects.filter(pk=meeting.pk).exists()

        def it_leaves_a_merely_linked_event_completely_alone():
            editor = _user("r2")
            meeting = MeetingFactory()
            event = CommunityEventFactory(guild=meeting.guild)
            meeting.link_event(event, timezone.localdate(event.starts_at), by=editor)
            with patch.object(CommunityEvent, "remove_from_google") as google:
                meeting.remove(by=editor)
            google.assert_not_called()
            assert CommunityEvent.objects.filter(pk=event.pk).exists()
            assert not Meeting.objects.filter(pk=meeting.pk).exists()


def describe_calendar_mismatch():
    """The §6.3 amber-warning predicate + the linked-occurrence helper (phase 4)."""

    def _linked(*, owns: bool, meeting_time: time | None, event_time: time = time(18, 0)) -> Meeting:
        meeting_date = timezone.localdate() + timedelta(days=7)
        starts = timezone.make_aware(datetime.combine(meeting_date, event_time))
        event = CommunityEventFactory(guild=None, community=True, starts_at=starts)
        return MeetingFactory(
            guild=None,
            scheduled_date=meeting_date,
            scheduled_time=meeting_time,
            event=event,
            event_occurrence=meeting_date,
            owns_event=owns,
        )

    def it_is_false_while_unlinked():
        assert MeetingFactory(event=None).calendar_mismatch is False

    def it_is_false_when_a_merely_linked_occurrence_matches():
        assert _linked(owns=False, meeting_time=time(18, 0)).calendar_mismatch is False

    def it_is_true_when_a_merely_linked_occurrence_drifted():
        assert _linked(owns=False, meeting_time=time(19, 0)).calendar_mismatch is True

    def it_is_true_for_a_merely_linked_meeting_with_no_time_set():
        # Blank time means midnight in starts_at — that never matches a 6 PM occurrence.
        assert _linked(owns=False, meeting_time=None).calendar_mismatch is True

    def it_ignores_drift_on_an_owned_link():
        # Owned events auto-sync via sync_event — drift is impossible, so no warning.
        assert _linked(owns=True, meeting_time=time(19, 0)).calendar_mismatch is False

    def it_is_true_for_a_cleared_date_under_an_owned_link():
        meeting = _linked(owns=True, meeting_time=time(18, 0))
        meeting.scheduled_date = None
        assert meeting.calendar_mismatch is True

    def it_derives_the_linked_occurrence_from_the_pinned_date_and_event_time():
        meeting = _linked(owns=False, meeting_time=time(18, 0))
        occurrence = meeting.linked_occurrence_starts_at
        assert occurrence is not None
        assert occurrence.date() == meeting.event_occurrence
        assert timezone.localtime(occurrence).time() == time(18, 0)

    def it_has_no_linked_occurrence_while_unlinked():
        assert MeetingFactory(event=None).linked_occurrence_starts_at is None


def describe_MeetingItemProposal():
    def describe_request_bell_path():
        def it_is_the_link_its_bell_rows_were_written_with():
            """The meeting page is gone (#1171); the path stays literal so old bell rows still clear."""
            proposal = MeetingItemProposalFactory()
            assert proposal.request_bell_path == f"/meetings/{proposal.meeting_id}/#proposal-{proposal.pk}"
