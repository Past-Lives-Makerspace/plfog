"""BDD specs for the Meeting aggregate: display/scope properties, the window querysets,
approve/unlock locking, calendar-event ownership (create/link/sync/unlink/remove), the
workspace add_* methods, and action-item carryover."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone
from unittest.mock import patch

from core.models import EventDelivery, SiteActivity
from membership.models import (
    CommunityEvent,
    Meeting,
    MeetingActionItem,
    MeetingAttachment,
    MeetingItemProposal,
    MeetingLockedError,
)
from tests.membership.factories import (
    CommunityEventFactory,
    GuildFactory,
    GuildMembershipFactory,
    MeetingActionItemFactory,
    MeetingAgendaItemFactory,
    MeetingAttendeeFactory,
    MeetingFactory,
    MeetingItemProposalFactory,
    MemberFactory,
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

    def describe_absolute_url():
        def it_points_at_the_workspace_route():
            meeting = MeetingFactory()
            assert meeting.absolute_url.endswith(f"/meetings/{meeting.pk}/")

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

            def it_includes_both_drafts_and_approved_meetings():
                draft = MeetingFactory(scheduled_date=timezone.localdate() + timedelta(days=1))
                approved = MeetingFactory(approved=True, scheduled_date=timezone.localdate() + timedelta(days=2))
                assert list(Meeting.objects.upcoming()) == [draft, approved]

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

        def describe_needs_attention():
            def it_returns_undated_and_past_dated_drafts_only():
                undated_draft = MeetingFactory(scheduled_date=None)
                stale_draft = MeetingFactory(scheduled_date=timezone.localdate() - timedelta(days=3))
                MeetingFactory(approved=True, scheduled_date=timezone.localdate() - timedelta(days=3))
                MeetingFactory(scheduled_date=timezone.localdate() + timedelta(days=3))  # future draft is fine
                assert set(Meeting.objects.needs_attention()) == {undated_draft, stale_draft}

        def describe_for_scope():
            def it_isolates_a_guild_from_the_council_scope():
                guild = GuildFactory()
                guild_meeting = MeetingFactory(guild=guild)
                council_meeting = MeetingFactory(guild=None)
                MeetingFactory(guild=GuildFactory())  # another guild
                assert list(Meeting.objects.for_scope(guild)) == [guild_meeting]
                assert list(Meeting.objects.for_scope(None)) == [council_meeting]

        def describe_status_filters():
            def it_splits_approved_from_drafts():
                draft = MeetingFactory()
                approved = MeetingFactory(approved=True)
                assert list(Meeting.objects.drafts()) == [draft]
                assert list(Meeting.objects.approved()) == [approved]

        def describe_visible_to():
            def it_returns_every_meeting_for_any_member():
                MeetingFactory()
                MeetingFactory(guild=None, approved=True)
                assert Meeting.objects.visible_to(_user("reader")).count() == 2

    def describe_assert_editable():
        def it_passes_for_a_draft():
            MeetingFactory().assert_editable()

        def it_raises_MeetingLockedError_when_approved():
            with pytest.raises(MeetingLockedError):
                MeetingFactory(approved=True).assert_editable()

    def describe_approve():
        def it_stamps_and_locks_the_minutes():
            approver = _user("approver")
            meeting = MeetingFactory()
            meeting.approve(by=approver)
            meeting.refresh_from_db()
            assert meeting.status == Meeting.Status.APPROVED
            assert meeting.is_locked is True
            assert meeting.approved_by == approver
            assert meeting.approved_at is not None

        def it_requires_a_scheduled_date():
            with pytest.raises(ValueError, match="Set the meeting date"):
                MeetingFactory(scheduled_date=None).approve(by=_user("a1"))

        def it_refuses_when_already_locked():
            with pytest.raises(MeetingLockedError):
                MeetingFactory(approved=True).approve(by=_user("a2"))

        def it_writes_the_meeting_approved_activity_row():
            meeting = MeetingFactory()
            meeting.approve(by=_user("a3"))
            assert SiteActivity.objects.filter(kind=SiteActivity.Kind.MEETING_APPROVED).count() == 1

        def it_emits_minutes_approved_to_the_guild_members():
            member_user = _user("guildmember")
            guild = GuildFactory()
            GuildMembershipFactory(guild=guild, member=member_user.member)
            meeting = MeetingFactory(guild=guild)
            meeting.approve(by=_user("a4"))
            assert _deliveries("meeting.minutes_approved") == 1
            assert _deliveries("meeting.council_minutes_approved") == 0

        def it_emits_council_minutes_approved_for_the_council_scope():
            lead = _user("counc-lead")
            GuildFactory(guild_lead=lead.member)
            meeting = MeetingFactory(guild=None)
            meeting.approve(by=_user("a5"))
            assert _deliveries("meeting.council_minutes_approved") == 1
            assert _deliveries("meeting.minutes_approved") == 0

        def it_auto_declines_pending_proposals_and_notifies_each_proposer():
            meeting = MeetingFactory()
            pending = MeetingItemProposalFactory(meeting=meeting, proposed_by=_user("prop1"))
            withdrawn = MeetingItemProposalFactory(meeting=meeting, proposed_by=_user("prop2"))
            withdrawn.withdraw(by=withdrawn.proposed_by)
            meeting.approve(by=_user("a6"))
            pending.refresh_from_db()
            withdrawn.refresh_from_db()
            assert pending.state == MeetingItemProposal.State.DECLINED
            assert pending.review_note == "The meeting was closed before this was reviewed."
            assert withdrawn.state == MeetingItemProposal.State.WITHDRAWN  # skipped, not declined
            assert _deliveries("meeting.item_decided") == 1  # only the pending proposer heard

        def it_re_stamps_and_re_announces_after_an_unlock():
            member_user = _user("guildmember2")
            guild = GuildFactory()
            GuildMembershipFactory(guild=guild, member=member_user.member)
            meeting = MeetingFactory(guild=guild)
            first_approver = _user("first")
            meeting.approve(by=first_approver)
            first_stamp = meeting.approved_at
            meeting.unlock(by=_user("admin1"))
            second_approver = _user("second")
            meeting.approve(by=second_approver)
            meeting.refresh_from_db()
            assert meeting.approved_by == second_approver
            assert meeting.approved_at is not None and meeting.approved_at > first_stamp
            # Timestamped period → the corrected minutes announce again instead of deduping.
            assert _deliveries("meeting.minutes_approved") == 2

    def describe_publish():
        def it_transitions_draft_to_published(db):
            meeting = MeetingFactory()
            meeting.publish()
            meeting.refresh_from_db()
            assert meeting.status == Meeting.Status.PUBLISHED
            assert meeting.is_locked is False

        def it_raises_when_already_published(db):
            meeting = MeetingFactory(published=True)
            with pytest.raises(ValueError):
                meeting.publish()

        def it_raises_MeetingLockedError_when_approved(db):
            meeting = MeetingFactory(approved=True)
            with pytest.raises(MeetingLockedError):
                meeting.publish()

    def describe_unlock():
        def it_reopens_the_draft_and_keeps_the_stamps_as_history():
            approver = _user("appr")
            meeting = MeetingFactory()
            meeting.approve(by=approver)
            meeting.unlock(by=_user("admin2"))
            meeting.refresh_from_db()
            assert meeting.status == Meeting.Status.DRAFT
            assert meeting.approved_by == approver
            assert meeting.approved_at is not None
            assert SiteActivity.objects.filter(kind=SiteActivity.Kind.MEETING_UNLOCKED).count() == 1

        def it_does_not_broadcast():
            meeting = MeetingFactory()
            meeting.approve(by=_user("appr2"))
            before = EventDelivery.objects.count()
            meeting.unlock(by=_user("admin3"))
            assert EventDelivery.objects.count() == before

        def it_refuses_to_unlock_a_draft():
            with pytest.raises(ValueError, match="Only approved minutes"):
                MeetingFactory().unlock(by=_user("admin4"))

    def describe_add_item():
        def it_creates_an_empty_item_at_the_bottom():
            meeting = MeetingFactory()
            first = meeting.add_item(by=_user("sec1"))
            second = meeting.add_item(by=_user("sec2"))
            assert (first.name, first.description, first.minutes) == ("", "", "")
            assert first.sort_order == 10
            assert second.sort_order == 20

        def it_respects_an_explicit_sort_order():
            meeting = MeetingFactory()
            item = MeetingAgendaItemFactory(meeting=meeting, sort_order=5)
            assert item.sort_order == 5

        def it_refuses_on_a_locked_meeting():
            with pytest.raises(MeetingLockedError):
                MeetingFactory(approved=True).add_item(by=_user("sec3"))

    def describe_add_attendee():
        def it_adds_a_roster_member():
            meeting = MeetingFactory()
            member = MemberFactory()
            row = meeting.add_attendee(member=member)
            assert row.member == member
            assert row.present is True

        def it_adds_a_free_text_guest():
            row = MeetingFactory().add_attendee(guest_name="Visiting Vera")
            assert row.member is None
            assert row.display_name == "Visiting Vera"

        def it_rejects_a_duplicate_member():
            meeting = MeetingFactory()
            member = MemberFactory()
            meeting.add_attendee(member=member)
            with pytest.raises(ValueError, match="Already on the list"):
                meeting.add_attendee(member=member)

        def it_refuses_on_a_locked_meeting():
            with pytest.raises(MeetingLockedError):
                MeetingFactory(approved=True).add_attendee(guest_name="Late Larry")

    def describe_propose_item():
        def it_creates_a_pending_proposal_and_notifies_reviewers():
            lead = _user("lead-rev")
            guild = GuildFactory(guild_lead=lead.member)
            meeting = MeetingFactory(guild=guild)
            proposer = _user("proposer")
            proposal = meeting.propose_item(by=proposer, title="New sander", why="Ours died.")
            assert proposal.state == MeetingItemProposal.State.PENDING
            assert proposal.proposed_by == proposer
            assert _deliveries("meeting.item_proposed") == 1

        def it_refuses_a_past_meeting():
            meeting = MeetingFactory(scheduled_date=timezone.localdate() - timedelta(days=1))
            with pytest.raises(ValueError, match="already happened"):
                meeting.propose_item(by=_user("p2"), title="Too late", why="")

        def it_refuses_a_locked_meeting():
            with pytest.raises(MeetingLockedError):
                MeetingFactory(approved=True).propose_item(by=_user("p3"), title="Nope", why="")

    def describe_create_calendar_event():
        def it_builds_a_guild_meeting_event_and_rides_schedule_or_go_live():
            by = _user("creator")
            guild = GuildFactory(name="Forge", meeting_location="Studio B")
            meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0))
            with patch.object(CommunityEvent, "schedule_or_go_live") as go_live:
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
            with patch.object(CommunityEvent, "schedule_or_go_live"):
                event = meeting.create_calendar_event(by=_user("cend"))
            assert event.ends_at == meeting.ends_at

        def it_falls_back_to_a_default_length_without_an_end_time():
            meeting = MeetingFactory(scheduled_time=time(18, 0), scheduled_end_time=None)
            with patch.object(CommunityEvent, "schedule_or_go_live"):
                event = meeting.create_calendar_event(by=_user("cdef"))
            assert event.ends_at == event.starts_at + timedelta(minutes=90)

        def it_prefers_the_video_call_url_as_the_location():
            guild = GuildFactory(meeting_location="Studio B")
            meeting = MeetingFactory(guild=guild, scheduled_time=time(18, 0), video_call_url="https://meet.example/x")
            with patch.object(CommunityEvent, "schedule_or_go_live"):
                event = meeting.create_calendar_event(by=_user("c2"))
            assert event.location == "https://meet.example/x"

        def it_builds_a_lead_meeting_for_the_council_without_touching_a_guild():
            meeting = MeetingFactory(guild=None, scheduled_time=time(19, 0))
            with patch.object(CommunityEvent, "schedule_or_go_live"):
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
            with patch.object(CommunityEvent, "schedule_or_go_live"):
                meeting.create_calendar_event(by=_user("c6"))
            with pytest.raises(ValueError, match="already linked"):
                meeting.create_calendar_event(by=_user("c7"))

        def it_refuses_on_a_locked_meeting():
            with pytest.raises(MeetingLockedError):
                MeetingFactory(approved=True, scheduled_time=time(18, 0)).create_calendar_event(by=_user("c8"))

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
            with patch.object(CommunityEvent, "schedule_or_go_live"):
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
            with patch.object(CommunityEvent, "schedule_or_go_live"):
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
            with patch.object(CommunityEvent, "schedule_or_go_live"):
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

        def it_refuses_to_delete_locked_minutes():
            with pytest.raises(MeetingLockedError):
                MeetingFactory(approved=True).remove(by=_user("r3"))

    def describe_carryover_actions():
        def it_delegates_to_the_carryover_queryset():
            guild = GuildFactory()
            source = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() - timedelta(days=30))
            action = MeetingActionItemFactory(item=MeetingAgendaItemFactory(meeting=source))
            target = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=7))
            assert list(target.carryover_actions()) == [action]


def describe_MeetingAttendee():
    def it_shows_the_members_name_or_the_guest_name():
        member = MemberFactory(full_legal_name="Ada Marsh")
        assert MeetingAttendeeFactory(member=member).display_name == "Ada Marsh"
        guest = MeetingAttendeeFactory(guest=True, guest_name="Guest Gwen")
        assert guest.display_name == "Guest Gwen"
        assert str(guest).startswith("Guest Gwen @ ")

    def it_rejects_a_row_that_is_both_member_and_guest():
        with pytest.raises(IntegrityError), transaction.atomic():
            MeetingAttendeeFactory(guest_name="Both Betty")

    def it_rejects_a_row_that_is_neither_member_nor_guest():
        with pytest.raises(IntegrityError), transaction.atomic():
            MeetingAttendeeFactory(member=None, guest_name="")

    def it_rejects_the_same_member_twice_per_meeting():
        row = MeetingAttendeeFactory()
        with pytest.raises(IntegrityError), transaction.atomic():
            MeetingAttendeeFactory(meeting=row.meeting, member=row.member)


def describe_MeetingAgendaItem():
    def it_labels_an_untitled_item_in_str():
        assert str(MeetingAgendaItemFactory(name="")).startswith("Untitled item (")
        assert str(MeetingAgendaItemFactory(name="Budget")).startswith("Budget (")

    def describe_open_action_count():
        def it_counts_only_open_actions():
            item = MeetingAgendaItemFactory()
            MeetingActionItemFactory(item=item)
            MeetingActionItemFactory(item=item)
            MeetingActionItemFactory(item=item, status=MeetingActionItem.Status.DONE)
            MeetingActionItemFactory(item=item, status=MeetingActionItem.Status.DISMISSED)
            assert item.open_action_count == 2


def describe_MeetingActionItem():
    def it_labels_an_untitled_action_in_str():
        assert str(MeetingActionItemFactory(name="")) == "Untitled action [Open]"
        assert str(MeetingActionItemFactory(name="Buy sandpaper")) == "Buy sandpaper [Open]"

    def describe_transitions():
        def it_completes_an_open_action_in_its_own_meeting():
            action = MeetingActionItemFactory()
            action.complete(by=_user("t1"))
            action.refresh_from_db()
            assert action.status == MeetingActionItem.Status.DONE
            assert action.closed_at is not None
            assert action.closed_in is None

        def it_refuses_to_complete_a_closed_action():
            action = MeetingActionItemFactory(status=MeetingActionItem.Status.DONE)
            with pytest.raises(ValueError, match="open action"):
                action.complete(by=_user("t2"))

        def it_dismisses_an_open_action_from_a_carryover_panel():
            target = MeetingFactory()
            action = MeetingActionItemFactory()
            action.dismiss(by=_user("t3"), in_meeting=target)
            action.refresh_from_db()
            assert action.status == MeetingActionItem.Status.DISMISSED
            assert action.closed_in == target

        def it_refuses_to_dismiss_a_closed_action():
            action = MeetingActionItemFactory(status=MeetingActionItem.Status.DISMISSED)
            with pytest.raises(ValueError, match="open action"):
                action.dismiss(by=_user("t4"), in_meeting=MeetingFactory())

        def it_reopens_a_done_action_and_clears_the_stamps():
            action = MeetingActionItemFactory()
            action.complete(by=_user("t5"), in_meeting=MeetingFactory())
            action.reopen()
            action.refresh_from_db()
            assert action.status == MeetingActionItem.Status.OPEN
            assert action.closed_at is None
            assert action.closed_in is None

        def it_refuses_to_reopen_a_dismissal():
            action = MeetingActionItemFactory(status=MeetingActionItem.Status.DISMISSED)
            with pytest.raises(ValueError, match="completed action"):
                action.reopen()

    def describe_carryover_for():
        @pytest.fixture
        def guild(db):
            return GuildFactory()

        def _source(guild, days_ago: int, **meeting_kwargs):
            meeting = MeetingFactory(
                guild=guild, scheduled_date=timezone.localdate() - timedelta(days=days_ago), **meeting_kwargs
            )
            return MeetingActionItemFactory(item=MeetingAgendaItemFactory(meeting=meeting))

        def it_returns_open_actions_from_earlier_meetings_in_the_same_scope(guild):
            old_action = _source(guild, days_ago=60)
            recent_action = _source(guild, days_ago=10)
            target = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=7))
            assert list(MeetingActionItem.objects.carryover_for(target)) == [old_action, recent_action]

        def it_excludes_closed_actions(guild):
            action = _source(guild, days_ago=10)
            action.complete(by=_user("c1"))
            target = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=7))
            assert list(MeetingActionItem.objects.carryover_for(target)) == []

        def it_excludes_other_guilds_and_the_council_scope(guild):
            _source(GuildFactory(), days_ago=10)  # another guild
            _source(None, days_ago=10)  # council
            target = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=7))
            assert list(MeetingActionItem.objects.carryover_for(target)) == []

        def it_keeps_the_council_scope_isolated_the_other_way(guild):
            council_action = _source(None, days_ago=10)
            _source(guild, days_ago=10)  # a guild's action never bleeds into council
            target = MeetingFactory(guild=None, scheduled_date=timezone.localdate() + timedelta(days=7))
            assert list(MeetingActionItem.objects.carryover_for(target)) == [council_action]

        def it_excludes_later_and_same_day_meetings(guild):
            day = timezone.localdate() + timedelta(days=7)
            same_day = MeetingFactory(guild=guild, scheduled_date=day)
            MeetingActionItemFactory(item=MeetingAgendaItemFactory(meeting=same_day))
            later = MeetingFactory(guild=guild, scheduled_date=day + timedelta(days=7))
            MeetingActionItemFactory(item=MeetingAgendaItemFactory(meeting=later))
            target = MeetingFactory(guild=guild, scheduled_date=day)
            assert list(MeetingActionItem.objects.carryover_for(target)) == []

        def it_excludes_undated_source_drafts(guild):
            undated = MeetingFactory(guild=guild, scheduled_date=None)
            MeetingActionItemFactory(item=MeetingAgendaItemFactory(meeting=undated))
            target = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=7))
            assert list(MeetingActionItem.objects.carryover_for(target)) == []

        def it_is_empty_for_an_undated_target(guild):
            _source(guild, days_ago=10)
            target = MeetingFactory(guild=guild, scheduled_date=None)
            assert list(MeetingActionItem.objects.carryover_for(target)) == []

        def it_includes_locked_sources_and_allows_closing_from_the_carryover_panel(guild):
            source = MeetingFactory(
                guild=guild, approved=True, scheduled_date=timezone.localdate() - timedelta(days=30)
            )
            action = MeetingActionItemFactory(item=MeetingAgendaItemFactory(meeting=source))
            target = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=7))
            assert list(MeetingActionItem.objects.carryover_for(target)) == [action]
            action.complete(by=_user("carry"), in_meeting=target)  # the one deliberate write into locked data
            action.refresh_from_db()
            assert action.status == MeetingActionItem.Status.DONE
            assert action.closed_in == target


def describe_MeetingAttachment():
    def it_stores_a_link_or_a_file_never_both():
        meeting = MeetingFactory()
        link = MeetingAttachment.objects.create(meeting=meeting, url="https://docs.example.com/agenda")
        assert link.is_link is True
        assert link.is_file is False
        with pytest.raises(IntegrityError), transaction.atomic():
            MeetingAttachment.objects.create(meeting=meeting, url="", file="")

    def it_derives_a_display_name_from_label_file_or_url():
        meeting = MeetingFactory()
        labeled = MeetingAttachment.objects.create(meeting=meeting, url="https://x.example", label="Budget sheet")
        assert labeled.display_name == "Budget sheet"
        bare = MeetingAttachment.objects.create(meeting=meeting, url="https://y.example")
        assert bare.display_name == "https://y.example"
        assert str(bare) == f"Attachment #{bare.pk} for {meeting}"

    def it_uses_the_file_base_name_when_a_file_is_attached():
        from django.core.files.uploadedfile import SimpleUploadedFile

        meeting = MeetingFactory()
        doc = MeetingAttachment.objects.create(
            meeting=meeting, file=SimpleUploadedFile("agenda.pdf", b"%PDF-1.4 test", "application/pdf")
        )
        assert doc.is_file is True
        assert doc.display_name.startswith("agenda")
        assert doc.display_name.endswith(".pdf")


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
