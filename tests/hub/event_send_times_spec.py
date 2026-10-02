"""BDD specs for the event editor saying what a save and each reminder will send.

The reminder times come from ``CommunityEvent.reminder_sends`` (the scheduler's own method),
the Save button names what the save does from the same form flags the edit views act on, and
the toggles re-render with fresh times when the start, repeat or announce time changes.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from hub.forms import CommunityEventForm, EventSendTogglesForm, event_send_hints
from membership.models import CommunityEvent, Member
from tests.hub.community_event_scheduling_spec import _payload, _user_with_role
from tests.membership.factories import CommunityEventFactory, GuildFactory

State = CommunityEvent.ModerationState
Weekly = CommunityEvent.Recurrence.WEEKLY
TOGGLES = ["remind_7d", "remind_3d", "remind_1d", "notify_happening_now"]


def _evening(days: int) -> datetime:
    """6 PM local, ``days`` from today."""
    return (timezone.localtime() + timedelta(days=days)).replace(hour=18, minute=0, second=0, microsecond=0)


def _time(at: datetime) -> str:
    return f"{at.strftime('%a, %b %-d')} at {at.strftime('%-I:%M %p')}"


def _field(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M")


def _save_label(content: bytes) -> str:
    match = re.search(rb'id="event-save"[^>]*>([^<]*)</button>', content)
    assert match, "no Save button"
    return match.group(1).decode().strip()


def describe_event_send_hints():
    def it_says_only_what_each_toggle_is_for_without_a_start():
        hints = event_send_hints(starts_at=None, recurrence=None, publish_at=None)
        assert list(hints) == TOGGLES
        assert hints["remind_7d"] == "Send members a reminder 7 days before it starts."
        assert hints["remind_1d"] == "Send members a reminder 1 day before it starts."
        assert hints["notify_happening_now"] == "A single ping to members when it begins."

    def it_names_when_a_one_off_reminder_goes_out():
        start = _evening(10)
        hints = event_send_hints(starts_at=start, recurrence="none", publish_at=None)
        assert (
            hints["remind_7d"]
            == f"Send members a reminder 7 days before it starts: {_time(start - timedelta(days=7))}."
        )
        assert hints["notify_happening_now"] == f"A single ping to members when it begins: {_time(start)}."

    def it_says_a_reminder_too_close_to_the_start_will_not_send():
        hints = event_send_hints(starts_at=_evening(2), recurrence="none", publish_at=None)
        assert (
            hints["remind_7d"]
            == "Send members a reminder 7 days before it starts. Too late for this event, so it won't send."
        )
        assert hints["remind_1d"].endswith(f"{_time(_evening(1))}.")

    def it_names_the_next_send_and_its_date_for_a_series():
        first = _evening(2)
        hints = event_send_hints(starts_at=first, recurrence=Weekly, publish_at=None)
        # The first date is too close for a week's notice, so the next one goes before the second.
        second = first + timedelta(days=7)
        assert hints["remind_7d"] == (
            f"Send members a reminder 7 days before each date. Next: {_time(first)}, for {second.strftime('%a, %b %-d')}."
        )
        assert hints["notify_happening_now"] == f"A ping to members as each date begins. Next: {_time(first)}."

    def it_counts_from_a_future_announce_time_because_nothing_sends_before_it():
        start = _evening(10)
        hints = event_send_hints(starts_at=start, recurrence="none", publish_at=_evening(5))
        assert hints["remind_7d"].endswith("Too late for this event, so it won't send.")
        assert hints["remind_3d"].endswith(f"{_time(start - timedelta(days=3))}.")

    def it_calls_a_send_due_within_a_tick_too_late():
        # The cron already checked anything due in the next 15 minutes, before this save.
        soon = timezone.now() + timedelta(minutes=10)
        hints = event_send_hints(starts_at=soon, recurrence="none", publish_at=None)
        assert hints["notify_happening_now"].endswith("Too late for this event, so it won't send.")

    def it_calls_a_send_due_at_the_announce_time_too_late():
        # That send is checked in the tick before, while the event is still scheduled.
        start = _evening(10)
        at_announce = event_send_hints(starts_at=start, recurrence="none", publish_at=start - timedelta(days=7))
        before_it = event_send_hints(
            starts_at=start, recurrence="none", publish_at=start - timedelta(days=7, minutes=30)
        )
        assert at_announce["remind_7d"].endswith("Too late for this event, so it won't send.")
        assert before_it["remind_7d"].endswith(f"{_time(start - timedelta(days=7))}.")

    def it_says_a_proposals_times_hold_only_if_it_is_approved_by_then():
        start = _evening(10)
        hints = event_send_hints(starts_at=start, recurrence="none", publish_at=None, in_review=True)
        assert hints["remind_7d"].endswith(f"{_time(start - timedelta(days=7))}, if it's approved by then.")
        series = event_send_hints(starts_at=start, recurrence=Weekly, publish_at=None, in_review=True)
        assert series["notify_happening_now"].endswith("if it's approved by then.")
        assert series["remind_7d"].endswith("if it's approved by then.")

    def it_ignores_an_announce_time_that_has_passed():
        start = _evening(10)
        hints = event_send_hints(starts_at=start, recurrence="none", publish_at=timezone.now() - timedelta(days=1))
        assert hints["remind_7d"].endswith(f"{_time(start - timedelta(days=7))}.")


@pytest.mark.django_db
def describe_what_a_save_does():
    def it_announces_a_new_event():
        form = CommunityEventForm(instance=CommunityEvent())
        assert (form.saves_announce, form.announced, form.in_review) == (True, False, False)

    def it_announces_a_scheduled_event():
        event = CommunityEventFactory(moderation_state=State.SCHEDULED, publish_at=timezone.now() + timedelta(days=2))
        form = CommunityEventForm(instance=event)
        assert (form.saves_announce, form.announced, form.in_review) == (True, False, False)

    def it_sends_nothing_for_a_live_event():
        form = CommunityEventForm(instance=CommunityEventFactory())
        assert (form.saves_announce, form.announced, form.in_review) == (False, True, False)

    def it_leaves_a_proposal_to_its_reviewer():
        form = CommunityEventForm(instance=CommunityEventFactory(pending=True))
        assert (form.saves_announce, form.announced, form.in_review) == (False, False, True)

    def it_does_not_announce_a_proposal_an_admin_edits(client: Client):
        _user_with_role("send_admin_review", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        event = CommunityEventFactory(pending=True, guild=guild)
        client.login(username="send_admin_review", password="pass")
        with (
            patch.object(CommunityEvent, "schedule_or_go_live") as mock_live,
            patch.object(CommunityEvent, "push_to_google") as mock_push,
        ):
            resp = client.post(
                reverse("hub_event_edit", args=[event.pk]),
                data=_payload(event_type="guild_meeting", guild=str(guild.pk), google_calendar_target="public"),
            )
        assert resp.status_code == 302
        mock_live.assert_not_called()
        mock_push.assert_not_called()
        event.refresh_from_db()
        assert event.moderation_state == State.PENDING

    def it_does_not_announce_a_proposal_a_lead_edits_on_the_guild_tab(client: Client):
        user = _user_with_role("send_lead_review")
        guild = GuildFactory(guild_lead=user.member)
        event = CommunityEventFactory(pending=True, guild=guild)
        client.login(username="send_lead_review", password="pass")
        with (
            patch.object(CommunityEvent, "schedule_or_go_live") as mock_live,
            patch.object(CommunityEvent, "push_to_google") as mock_push,
        ):
            resp = client.post(reverse("hub_guild_event_edit", args=[guild.pk, event.pk]), data=_payload())
        assert resp.status_code == 302
        mock_live.assert_not_called()
        mock_push.assert_not_called()


@pytest.mark.django_db
def describe_the_save_button():
    def _page(client: Client, event: CommunityEvent | None = None) -> bytes:
        user = _user_with_role("send_lead")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="send_lead", password="pass")
        if event is None:
            return client.get(reverse("hub_guild_event_add", args=[guild.pk])).content
        event.guild = guild
        event.save()
        return client.get(reverse("hub_guild_event_edit", args=[guild.pk, event.pk])).content

    def it_says_save_and_announce_on_a_new_event(client: Client):
        content = _page(client)
        assert _save_label(content) == "Save and announce"
        # The label it switches to once a time is set. Anchored on the attribute: the changelog
        # renders on every page and says "Save and schedule" too.
        assert re.search(rb'id="event-save"[^>]*x-text="[^"]*\'Save and schedule\'', content)

    def it_says_save_and_schedule_on_a_scheduled_event(client: Client):
        event = CommunityEventFactory(moderation_state=State.SCHEDULED, publish_at=timezone.now() + timedelta(days=2))
        assert _save_label(_page(client, event)) == "Save and schedule"

    def it_says_save_on_a_live_event(client: Client):
        content = _page(client, CommunityEventFactory())
        assert _save_label(content) == "Save"
        assert b'x-text="scheduleLater' not in content

    def it_says_save_on_a_proposal_and_that_approving_announces_it(client: Client):
        content = _page(client, CommunityEventFactory(pending=True))
        assert _save_label(content) == "Save"
        assert re.search(rb'id="event-save-note"[^>]*>\s*This event hasn\'t been approved', content)


@pytest.mark.django_db
def describe_the_send_toggles():
    def it_shows_each_reminders_time_on_the_edit_page(client: Client):
        user = _user_with_role("send_times_lead")
        guild = GuildFactory(guild_lead=user.member)
        start = _evening(10)
        event = CommunityEventFactory(guild=guild, starts_at=start, ends_at=start + timedelta(hours=2))
        client.login(username="send_times_lead", password="pass")
        content = client.get(reverse("hub_guild_event_edit", args=[guild.pk, event.pk])).content
        assert b'id="event-send-toggles"' in content
        assert _time(start - timedelta(days=7)).encode() in content

    def it_times_the_submitted_start_after_a_refused_save(client: Client):
        user = _user_with_role("send_refused_lead")
        guild = GuildFactory(guild_lead=user.member)
        start = _evening(12)
        client.login(username="send_refused_lead", password="pass")
        resp = client.post(
            reverse("hub_guild_event_add", args=[guild.pk]),
            data=_payload(starts_at=_field(start), ends_at=_field(start - timedelta(hours=1))),
        )
        assert resp.status_code == 200  # ends before it starts
        assert _time(start - timedelta(days=3)).encode() in resp.content

    def it_re_renders_with_times_for_the_start_being_typed(client: Client):
        _user_with_role("send_partial")
        client.login(username="send_partial", password="pass")
        start = _evening(9)
        resp = client.get(
            reverse("hub_event_send_toggles"),
            {"starts_at": _field(start), "recurrence": "none", "remind_1d": "on"},
        )
        assert resp.status_code == 200
        content = resp.content
        assert content.lstrip().startswith(b'<div id="event-send-toggles"')
        assert _time(start - timedelta(days=1)).encode() in content
        # The flip made before the start changed survives the swap.
        assert re.search(rb'<input[^>]*name="remind_1d"[^>]*checked', content)
        assert not re.search(rb'<input[^>]*name="remind_7d"[^>]*checked', content)

    def it_carries_a_proposals_qualifier_through_the_swap(client: Client):
        _user_with_role("send_partial_review")
        client.login(username="send_partial_review", password="pass")
        resp = client.get(
            reverse("hub_event_send_toggles"),
            {"starts_at": _field(_evening(9)), "recurrence": "none", "in_review": "on"},
        )
        assert escape("if it's approved by then.").encode() in resp.content
        assert b'hx-vals=\'{"in_review": "on"}\'' in resp.content

    def it_marks_a_proposals_toggles_on_the_edit_page(client: Client):
        user = _user_with_role("send_review_page")
        guild = GuildFactory(guild_lead=user.member)
        start = _evening(10)
        event = CommunityEventFactory(pending=True, guild=guild, starts_at=start, ends_at=start + timedelta(hours=2))
        client.login(username="send_review_page", password="pass")
        content = client.get(reverse("hub_guild_event_edit", args=[guild.pk, event.pk])).content
        assert b'hx-vals=\'{"in_review": "on"}\'' in content
        assert escape(f"{_time(start - timedelta(days=7))}, if it's approved by then.").encode() in content

    def it_says_only_what_each_is_for_when_the_start_does_not_parse(client: Client):
        _user_with_role("send_partial_bad")
        client.login(username="send_partial_bad", password="pass")
        resp = client.get(reverse("hub_event_send_toggles"), {"starts_at": "soon", "recurrence": "none"})
        assert b"Send members a reminder 7 days before it starts.</" in resp.content

    def it_needs_a_signed_in_member(client: Client):
        resp = client.get(reverse("hub_event_send_toggles"), {"starts_at": _field(_evening(9))})
        assert resp.status_code == 302


def describe_event_send_toggles_form():
    def it_carries_the_same_toggles_as_the_editor():
        assert [name for name in EventSendTogglesForm().fields if name in TOGGLES] == TOGGLES
