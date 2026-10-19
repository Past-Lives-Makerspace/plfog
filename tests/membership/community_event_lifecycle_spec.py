"""BDD specs for the CommunityEvent review lifecycle: submit / approve / request-changes
/ decline / withdraw / publish, their guards, and the workflow notifications they emit; then
the Manage Events additions (#831, part 2): the DRAFT state and where it is invisible,
``duplicate``, ``unpublish`` and re-publishing a draft, the Past and Declined querysets, the
Status pill and ``creator_name``."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from core.integrations import discord_events as de
from core.integrations.discord_events import DiscordEventsError
from core.integrations.google_calendar import GoogleCalendarClient, GoogleCalendarError
from core.models import EventDelivery
from membership.models import CommunityEvent, EventUnpublishError, InvalidEventTransition
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory, tiny_png_bytes

pytestmark = pytest.mark.django_db


def _user(username: str, email: str = "") -> User:
    MembershipPlanFactory()
    return User.objects.create_user(
        username=username, email=email or f"{username}@example.com", password="x", last_login=timezone.now()
    )


def _fresh_event(guild) -> CommunityEvent:
    """An unsaved guild event, ready to be proposed."""
    return CommunityEvent(
        title="Forge Night",
        event_type=CommunityEvent.EventType.GUILD_MEETING,
        guild=guild,
        starts_at=timezone.now() + timedelta(days=10),
        ends_at=timezone.now() + timedelta(days=10, hours=2),
    )


def _delivery_count(event_key: str) -> int:
    return EventDelivery.objects.filter(event_key=event_key, channel="in_app").count()


@pytest.mark.django_db
def describe_submit_for_review():
    def it_sets_pending_records_the_proposer_and_notifies_reviewers():
        lead = _user("lead", "lead@example.com")
        guild = GuildFactory(guild_lead=lead.member)
        proposer = _user("prop", "prop@example.com")
        event = _fresh_event(guild)
        event.submit_for_review(submitted_by=proposer)
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.PENDING
        assert event.submitted_by == proposer
        assert _delivery_count("event.submitted") == 1

    def it_clears_a_prior_review_verdict_on_resubmit():
        lead = _user("lead2", "lead2@example.com")
        guild = GuildFactory(guild_lead=lead.member)
        proposer = _user("prop2", "prop2@example.com")
        event = _fresh_event(guild)
        event.submit_for_review(submitted_by=proposer)
        event.request_changes(reviewer=lead, notes="Please move it later.")
        event.submit_for_review(submitted_by=proposer)
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.PENDING
        assert event.review_notes == ""
        assert event.reviewed_by is None

    def it_re_notifies_reviewers_on_resubmit_via_a_distinct_period():
        lead = _user("lead3", "lead3@example.com")
        guild = GuildFactory(guild_lead=lead.member)
        proposer = _user("prop3", "prop3@example.com")
        event = _fresh_event(guild)
        event.submit_for_review(submitted_by=proposer)
        event.request_changes(reviewer=lead, notes="Tweak the time.")
        event.submit_for_review(submitted_by=proposer)
        # Two distinct submit rounds → two reviewer notifications (not deduped away).
        assert _delivery_count("event.submitted") == 2

    def it_raises_from_a_published_event():
        event = CommunityEventFactory()  # PUBLISHED
        with pytest.raises(InvalidEventTransition):
            event.submit_for_review(submitted_by=_user("p"))


@pytest.mark.django_db
def describe_approve():
    def it_publishes_records_the_reviewer_and_notifies_the_proposer():
        reviewer = _user("rev", "rev@example.com")
        proposer = _user("subm", "subm@example.com")
        event = CommunityEventFactory(pending=True, submitted_by=proposer)
        event.approve(reviewer=reviewer)
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.PUBLISHED
        assert event.reviewed_by == reviewer
        assert event.reviewed_at is not None
        # publish() marked it as needing a Google push (Wave 2 wiring), without pushing.
        assert event.sync_state == CommunityEvent.SyncState.PENDING
        assert _delivery_count("event.approved") == 1

    def it_raises_from_a_declined_event():
        event = CommunityEventFactory(declined=True)
        with pytest.raises(InvalidEventTransition):
            event.approve(reviewer=_user("r"))


@pytest.mark.django_db
def describe_request_changes():
    def it_sets_changes_requested_with_the_note_and_notifies_the_proposer():
        reviewer = _user("rc", "rc@example.com")
        proposer = _user("submrc", "submrc@example.com")
        event = CommunityEventFactory(pending=True, submitted_by=proposer)
        event.request_changes(reviewer=reviewer, notes="Add a location.")
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.CHANGES_REQUESTED
        assert event.review_notes == "Add a location."
        assert _delivery_count("event.changes_requested") == 1

    def it_requires_a_note():
        event = CommunityEventFactory(pending=True)
        with pytest.raises(ValueError):
            event.request_changes(reviewer=_user("r"), notes="   ")

    def it_raises_from_a_non_pending_event():
        event = CommunityEventFactory(moderation_state=CommunityEvent.ModerationState.CHANGES_REQUESTED)
        with pytest.raises(InvalidEventTransition):
            event.request_changes(reviewer=_user("r"), notes="x")


@pytest.mark.django_db
def describe_decline():
    def it_sets_declined_with_the_note_and_notifies_the_proposer():
        reviewer = _user("dc", "dc@example.com")
        proposer = _user("submdc", "submdc@example.com")
        event = CommunityEventFactory(pending=True, submitted_by=proposer)
        event.decline(reviewer=reviewer, notes="Not a fit right now.")
        event.refresh_from_db()
        assert event.moderation_state == CommunityEvent.ModerationState.DECLINED
        assert _delivery_count("event.declined") == 1
        assert event not in CommunityEvent.objects.published()

    def it_requires_a_note():
        event = CommunityEventFactory(pending=True)
        with pytest.raises(ValueError):
            event.decline(reviewer=_user("r"), notes="")

    def it_raises_from_a_published_event():
        event = CommunityEventFactory()  # PUBLISHED
        with pytest.raises(InvalidEventTransition):
            event.decline(reviewer=_user("r"), notes="nope")


@pytest.mark.django_db
def describe_withdraw():
    def it_deletes_a_pending_proposal():
        event = CommunityEventFactory(pending=True)
        pk = event.pk
        event.withdraw(by=event.submitted_by)
        assert not CommunityEvent.objects.filter(pk=pk).exists()

    def it_raises_from_a_published_event():
        event = CommunityEventFactory()  # PUBLISHED
        with pytest.raises(InvalidEventTransition):
            event.withdraw(by=_user("w"))


@pytest.mark.django_db
def describe_publish():
    def it_flips_idle_to_pending_and_announces_once():
        member = _user("gm", "gm@example.com")
        guild = GuildFactory()
        from tests.membership.factories import GuildMembershipFactory

        GuildMembershipFactory(guild=guild, member=member.member)
        event = CommunityEventFactory(guild=guild)
        event.publish(actor=None)
        event.refresh_from_db()
        assert event.sync_state == CommunityEvent.SyncState.PENDING
        # A second publish neither double-announces (period dedup) nor re-flips.
        event.publish(actor=None)
        assert _delivery_count("event.guild_published") == 1


# --- Manage Events (#831, part 2) -------------------------------------------

States = CommunityEvent.ModerationState
Sync = CommunityEvent.SyncState


def _member_user(username: str, *, preferred_name: str = "") -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    if preferred_name:
        user.member.preferred_name = preferred_name
        user.member.save(update_fields=["preferred_name"])
    return user


def _draft(**overrides: object) -> CommunityEvent:
    return CommunityEventFactory(community=True, moderation_state=States.DRAFT, **overrides)


def _live_and_synced(**overrides: object) -> CommunityEvent:
    """A published, pushed and announced site-wide event: everything unpublish has to unwind."""
    return CommunityEventFactory(
        community=True,
        pushed=True,
        discord_event_id="disc-1",
        discord_sync_state=Sync.SYNCED,
        discord_synced_at=timezone.now(),
        discord_pushed_occurrence=timezone.now(),
        discord_pushed_start=timezone.now(),
        channel_announced_at=timezone.now() - timedelta(days=1),
        discord_announce_channel_id="chan-1",
        discord_announce_message_id="msg-1",
        synced_at=timezone.now(),
        **overrides,
    )


def describe_the_draft_state():
    def it_is_a_choice_of_its_own():
        assert States.DRAFT.value == "draft"
        assert States.DRAFT.label == "Draft"

    def it_is_left_out_of_every_live_queryset():
        now = timezone.now()
        draft = _draft(publish_at=now - timedelta(hours=1), sync_state=Sync.FAILED, discord_sync_state=Sync.FAILED)
        ended = _draft(starts_at=now - timedelta(days=2))
        assert draft in CommunityEvent.objects.all()
        assert draft not in CommunityEvent.objects.published()
        assert draft not in CommunityEvent.objects.scheduled()
        assert draft not in CommunityEvent.objects.awaiting_review()
        assert draft not in CommunityEvent.objects.due_to_publish(now)
        assert draft not in CommunityEvent.objects.needs_push()
        assert draft not in CommunityEvent.objects.needs_discord_push()
        assert draft not in CommunityEvent.objects.declined()
        assert ended not in CommunityEvent.objects.past()

    def it_is_left_out_of_the_interested_sync_and_the_reminders():
        from membership.events import _sending_events
        from membership.interested_sync import sync_interested_rsvps

        soon = timezone.now() + timedelta(hours=6)
        _draft(starts_at=soon, discord_event_id="disc-draft", remind_1d=True, notify_happening_now=True)
        client = MagicMock(enabled=True, server_id="srv")
        client.list_interested_user_ids = MagicMock(return_value=[])
        with patch("core.integrations.discord_events.DiscordScheduledEventsClient.from_settings", return_value=client):
            assert sync_interested_rsvps() == 0
        client.list_interested_user_ids.assert_not_called()
        assert not _sending_events(timezone.now(), days_before=7).exists()


def describe_schedule_or_go_live_from_a_draft():
    def it_publishes_now_without_an_announce_time():
        draft = _draft()
        with patch.object(CommunityEvent, "publish") as publish:
            draft.schedule_or_go_live(actor=None)
        draft.refresh_from_db()
        assert draft.moderation_state == States.PUBLISHED
        publish.assert_called_once()

    def it_parks_a_draft_with_a_future_announce_time():
        draft = _draft(publish_at=timezone.now() + timedelta(days=2))
        with patch.object(CommunityEvent, "publish") as publish:
            draft.schedule_or_go_live(actor=None)
        draft.refresh_from_db()
        assert draft.moderation_state == States.SCHEDULED
        publish.assert_not_called()

    def it_re_syncs_a_once_live_event_under_the_same_announcement_period():
        """The announcement's idempotency key is the event's, so the ledger refuses a second one."""
        user = _member_user("republisher")
        event = _live_and_synced()
        # Sync is off in tests, so there is nothing to remove and the unpublish goes through.
        with patch.object(CommunityEvent, "strip_discord_announcement_buttons"):
            event.unpublish(actor=user)
        with (
            patch("core.events.emit.emit") as emit,
            patch("core.integrations.discord_channel.edit_channel_message") as edit_message,
        ):
            event.schedule_or_go_live(actor=user)
        event.refresh_from_db()
        assert event.moderation_state == States.PUBLISHED
        assert emit.call_args.kwargs["period"] == f"event:{event.pk}:published"
        assert event.channel_announced_at is not None
        # IDLE after the unpublish, so push_live marks it for a fresh push.
        assert event.sync_state == Sync.PENDING
        assert event.google_event_id == ""
        # The announcement message gets its RSVP and Manage buttons back (unpublish stripped them).
        assert edit_message.call_args.args[:2] == ("chan-1", "msg-1")
        assert edit_message.call_args.kwargs["components"] == event.discord_announcement_components()

    def it_restores_no_buttons_on_a_first_publish():
        draft = _draft()
        with (
            patch("core.events.emit.emit"),
            patch.object(CommunityEvent, "refresh_discord_announcement") as refresh,
        ):
            draft.schedule_or_go_live(actor=None)
        refresh.assert_not_called()
        assert draft.moderation_state == States.PUBLISHED


def describe_duplicate():
    def it_copies_the_columns_as_a_fresh_draft_of_the_clicker():
        source = CommunityEventFactory(
            guild_hosted=True,
            title="Open Studio",
            location="Main floor",
            description="Bring a friend.",
            recurrence=CommunityEvent.Recurrence.MONTHLY,
            remind_3d=True,
            video_url="https://meet.example/open",
            google_calendar_target=CommunityEvent.GoogleCalendarTarget.MEMBER,
        )
        user = _member_user("copier")
        copy = source.duplicate(by=user)
        assert copy.pk != source.pk
        assert copy.title == "Open Studio (copy)"
        assert copy.moderation_state == States.DRAFT
        assert copy.created_by == user
        assert (copy.guild, copy.event_type, copy.starts_at, copy.ends_at) == (
            source.guild,
            source.event_type,
            source.starts_at,
            source.ends_at,
        )
        assert (copy.location, copy.description, copy.recurrence, copy.remind_3d, copy.video_url) == (
            "Main floor",
            "Bring a friend.",
            CommunityEvent.Recurrence.MONTHLY,
            True,
            "https://meet.example/open",
        )
        assert copy.google_calendar_target == CommunityEvent.GoogleCalendarTarget.MEMBER
        source.refresh_from_db()
        assert source.title == "Open Studio"
        assert source.moderation_state == States.PUBLISHED

    def it_leaves_the_sources_life_behind():
        source = _live_and_synced(
            publish_at=timezone.now() - timedelta(days=3),
            import_source_uid="seed-uid@google.com",
            sync_error="",
        )
        source.submitted_by = _member_user("proposer")
        source.reviewed_by = _member_user("reviewer")
        source.reviewed_at = timezone.now()
        source.review_notes = "Looks good."
        source.save()
        copy = source.duplicate(by=_member_user("copier2"))
        assert copy.publish_at is None
        assert (copy.submitted_by, copy.reviewed_by, copy.reviewed_at, copy.review_notes) == (None, None, None, "")
        assert (copy.google_event_id, copy.google_calendar_id, copy.google_ical_uid) == ("", "", "")
        assert (copy.sync_state, copy.sync_error, copy.synced_at) == (Sync.IDLE, "", None)
        assert (copy.discord_event_id, copy.discord_sync_state, copy.discord_sync_error) == ("", Sync.IDLE, "")
        assert (copy.discord_synced_at, copy.discord_pushed_occurrence, copy.discord_pushed_start) == (
            None,
            None,
            None,
        )
        assert copy.channel_announced_at is None
        assert (copy.discord_announce_channel_id, copy.discord_announce_message_id) == ("", "")
        assert copy.import_source_uid == ""
        source.refresh_from_db()
        assert source.google_event_id != ""

    def it_writes_the_photo_to_a_new_file_so_the_source_keeps_its_own():
        source = CommunityEventFactory(community=True)
        source.photo.save("flyer.png", ContentFile(tiny_png_bytes()), save=True)
        copy = source.duplicate(by=_member_user("copier3"))
        assert copy.photo
        assert copy.photo.name != source.photo.name
        assert default_storage.exists(copy.photo.name)
        with source.photo.open("rb") as original, copy.photo.open("rb") as duplicate:
            assert duplicate.read() == original.read()
        # Clearing the copy's picture sweeps the copy's file only (delete_orphan_on_replace).
        copy.photo = ""
        copy.save()
        assert default_storage.exists(source.photo.name)

    def it_copies_without_a_photo_when_the_source_has_none():
        copy = CommunityEventFactory(community=True).duplicate(by=_member_user("copier4"))
        assert not copy.photo

    def it_keeps_the_title_inside_the_column():
        source = CommunityEventFactory(community=True, title="x" * CommunityEvent.TITLE_MAX_LENGTH)
        copy = source.duplicate(by=_member_user("copier5"))
        assert len(copy.title) == CommunityEvent.TITLE_MAX_LENGTH
        assert copy.title.endswith(" (copy)")


def describe_unpublish():
    def _clients(
        *, google_error: Exception | None = None, discord_error: Exception | None = None
    ) -> tuple[MagicMock, MagicMock]:
        """Enabled Google and Discord clients whose delete succeeds, or raises the given error."""
        google = MagicMock(spec=GoogleCalendarClient)
        google.enabled = True
        google.delete_event = MagicMock(side_effect=google_error)
        discord = MagicMock(enabled=True, server_id="srv")
        discord.delete_event = MagicMock(side_effect=discord_error)
        return google, discord

    def _gone_on_google() -> GoogleCalendarError:
        """The error the client raises for a copy someone deleted by hand: a 410 on the wrapped cause."""
        cause = RuntimeError("Resource has been deleted")
        cause.resp = SimpleNamespace(status=410)  # type: ignore[attr-defined]
        error = GoogleCalendarError("<HttpError 410>")
        error.__cause__ = cause
        return error

    def _assert_draft_with_nothing_remote(event: CommunityEvent) -> None:
        assert event.moderation_state == States.DRAFT
        assert event.publish_at is None
        assert (event.google_event_id, event.google_calendar_id, event.google_ical_uid) == ("", "", "")
        assert (event.sync_state, event.sync_error, event.synced_at) == (Sync.IDLE, "", None)
        assert (event.discord_event_id, event.discord_sync_state, event.discord_sync_error) == ("", Sync.IDLE, "")
        assert (event.discord_synced_at, event.discord_pushed_occurrence, event.discord_pushed_start) == (
            None,
            None,
            None,
        )
        assert event.channel_announced_at is not None
        assert (event.discord_announce_channel_id, event.discord_announce_message_id) == ("chan-1", "msg-1")

    def it_takes_a_live_event_down_to_a_draft_once_both_copies_are_gone():
        event = _live_and_synced()
        google_event_id = event.google_event_id  # blanked on the instance once the copy is gone
        google, discord = _clients()
        with (
            patch.object(GoogleCalendarClient, "from_settings", return_value=google),
            patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=discord),
            patch.object(CommunityEvent, "strip_discord_announcement_buttons") as strip,
        ):
            event.unpublish(actor=_member_user("unpublisher"))
        google.delete_event.assert_called_once_with("cal@group.calendar.google.com", google_event_id)
        discord.delete_event.assert_called_once_with("srv", "disc-1")
        strip.assert_called_once()
        event.refresh_from_db()
        _assert_draft_with_nothing_remote(event)

    def it_has_nothing_to_remove_while_sync_is_off():
        # from_settings answers a disabled client when nothing is configured (the test default).
        # The schedule it once went out on is cleared too: the composer would refuse a past one.
        event = _live_and_synced(publish_at=timezone.now() - timedelta(days=3))
        with patch.object(CommunityEvent, "strip_discord_announcement_buttons"):
            event.unpublish(actor=_member_user("unpublisher_off"))
        event.refresh_from_db()
        _assert_draft_with_nothing_remote(event)

    def it_treats_a_copy_already_deleted_by_hand_as_removed():
        event = _live_and_synced()
        google, discord = _clients(
            google_error=_gone_on_google(),
            discord_error=DiscordEventsError('Discord API 404: {"message": "Unknown Guild Scheduled Event"}'),
        )
        with (
            patch.object(GoogleCalendarClient, "from_settings", return_value=google),
            patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=discord),
            patch.object(CommunityEvent, "strip_discord_announcement_buttons"),
        ):
            event.unpublish(actor=_member_user("unpublisher_gone"))
        event.refresh_from_db()
        _assert_draft_with_nothing_remote(event)

    def it_stays_published_when_google_refuses():
        event = _live_and_synced()
        google, discord = _clients(google_error=GoogleCalendarError("403 forbidden"))
        with (
            patch.object(GoogleCalendarClient, "from_settings", return_value=google),
            patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=discord),
            patch.object(CommunityEvent, "strip_discord_announcement_buttons") as strip,
            pytest.raises(EventUnpublishError, match="could not be removed from Google Calendar\\."),
        ):
            event.unpublish(actor=_member_user("unpublisher_g"))
        strip.assert_not_called()
        event.refresh_from_db()
        assert event.moderation_state == States.PUBLISHED
        assert event.google_event_id != ""
        assert event.sync_state == Sync.FAILED
        assert event.sync_error == "Could not remove it from Google Calendar: 403 forbidden"
        # Discord's copy did go: blank, and PENDING so the retry cron puts it back on the live event.
        assert (event.discord_event_id, event.discord_sync_state, event.discord_sync_error) == ("", Sync.PENDING, "")

    def it_stays_published_when_discord_refuses():
        event = _live_and_synced()
        google, discord = _clients(discord_error=DiscordEventsError("Discord API 500: down"))
        with (
            patch.object(GoogleCalendarClient, "from_settings", return_value=google),
            patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=discord),
            patch.object(CommunityEvent, "strip_discord_announcement_buttons"),
            pytest.raises(EventUnpublishError, match="could not be removed from Discord\\."),
        ):
            event.unpublish(actor=_member_user("unpublisher_d"))
        event.refresh_from_db()
        assert event.moderation_state == States.PUBLISHED
        assert (event.google_event_id, event.sync_state, event.sync_error) == ("", Sync.PENDING, "")
        assert event.discord_event_id == "disc-1"
        assert event.discord_sync_state == Sync.FAILED
        assert event.discord_sync_error == "Could not remove it from Discord: Discord API 500: down"

    def it_names_both_when_both_refuse():
        event = _live_and_synced()
        google, discord = _clients(
            google_error=GoogleCalendarError("boom"), discord_error=DiscordEventsError("Discord API 502: bad")
        )
        with (
            patch.object(GoogleCalendarClient, "from_settings", return_value=google),
            patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=discord),
            pytest.raises(EventUnpublishError, match="from Google Calendar and Discord\\."),
        ):
            event.unpublish(actor=_member_user("unpublisher_both"))
        event.refresh_from_db()
        assert event.moderation_state == States.PUBLISHED
        assert (event.sync_state, event.discord_sync_state) == (Sync.FAILED, Sync.FAILED)
        assert (event.google_event_id != "", event.discord_event_id) == (True, "disc-1")

    @pytest.mark.parametrize(
        "state", [States.SCHEDULED, States.PENDING, States.CHANGES_REQUESTED, States.DECLINED, States.DRAFT]
    )
    def it_refuses_anything_but_a_published_event(state: str):
        event = CommunityEventFactory(community=True, moderation_state=state, pushed=True)
        with (
            patch.object(GoogleCalendarClient, "from_settings") as google,
            pytest.raises(InvalidEventTransition, match=f"'{state}'"),
        ):
            event.unpublish(actor=_member_user(f"refused_{state}"))
        google.assert_not_called()
        event.refresh_from_db()
        assert event.moderation_state == state
        assert event.google_event_id != ""


def describe_past_and_declined():
    def it_lists_the_published_one_offs_that_have_ended():
        now = timezone.now()
        ended = CommunityEventFactory(community=True, starts_at=now - timedelta(days=2))
        series = CommunityEventFactory(
            community=True, starts_at=now - timedelta(days=40), recurrence=CommunityEvent.Recurrence.MONTHLY
        )
        upcoming = CommunityEventFactory(community=True)
        declined = CommunityEventFactory(community=True, declined=True, starts_at=now - timedelta(days=2))
        assert list(CommunityEvent.objects.past()) == [ended]
        assert series not in CommunityEvent.objects.past()
        assert upcoming not in CommunityEvent.objects.past()
        assert list(CommunityEvent.objects.declined()) == [declined]


def describe_the_status_pill():
    @pytest.mark.parametrize(
        ("state", "label", "tone"),
        [
            (States.PUBLISHED, "Published", "ok"),
            (States.SCHEDULED, "Scheduled", "primary"),
            (States.PENDING, "Pending review", "warn"),
            (States.CHANGES_REQUESTED, "Changes requested", "warn"),
            (States.DECLINED, "Declined", "danger"),
            (States.DRAFT, "Draft", "neutral"),
        ],
    )
    def it_names_the_state_with_a_tone(state: str, label: str, tone: str):
        event = CommunityEventFactory(community=True, moderation_state=state)
        assert (event.status_label, event.status_tone) == (label, tone)

    def it_reads_past_once_a_live_one_off_is_over():
        event = CommunityEventFactory(community=True, starts_at=timezone.now() - timedelta(days=1))
        assert event.has_ended
        assert (event.status_label, event.status_tone) == ("Past", "neutral")

    def it_never_calls_a_series_or_a_draft_past():
        series = CommunityEventFactory(
            community=True, starts_at=timezone.now() - timedelta(days=30), recurrence=CommunityEvent.Recurrence.WEEKLY
        )
        draft = _draft(starts_at=timezone.now() - timedelta(days=1))
        assert not series.has_ended
        assert series.status_label == "Published"
        assert draft.status_label == "Draft"
        assert draft.rsvps_closed


def describe_creator_name():
    def it_is_the_creators_display_name():
        user = _member_user("maker", preferred_name="Lee")
        assert CommunityEventFactory(community=True, created_by=user).creator_name == "Lee"

    def it_falls_back_to_the_proposer_then_to_the_makerspace():
        proposer = _member_user("proposer2", preferred_name="Mira")
        assert CommunityEventFactory(community=True, submitted_by=proposer).creator_name == "Mira"
        assert CommunityEventFactory(community=True).creator_name == "Past Lives"

    def it_names_the_makerspace_for_a_user_with_no_member():
        user = _member_user("memberless")
        user.member.delete()
        event = CommunityEventFactory(community=True, created_by=User.objects.get(pk=user.pk))
        assert event.creator_name == "Past Lives"

    def it_is_what_the_discord_footer_reads():
        user = _member_user("footer", preferred_name="Jay")
        embed = CommunityEventFactory(community=True, created_by=user).discord_announcement_embed()
        assert embed["footer"]["text"].endswith("Created by Jay")
