"""BDD specs for the event-RSVP model layer (membership.models).

Covers the ``EventRSVP`` row + unique constraint, ``CommunityEvent.toggle_rsvp`` (create /
delete round-trip and the race backstop), ``attendees_field`` at 0 / 1 / 15 / 16+ (cap copy,
count in the field name, 1024-char guard), the rich ``discord_announcement_embed`` field rules
and footer fallback, the humanized duration, ``next_occurrence_start`` for a recurring series,
``rsvps_closed``, and ``can_manage_from_discord`` per authority tier.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from membership.models import CommunityEvent, EventRSVP, Member
from tests.membership.factories import (
    CommunityEventFactory,
    EventRSVPFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db


def _linked_member(**kwargs) -> Member:
    """A Member with a linked, email-bearing User (signals muted so no second Member spawns)."""
    member = MemberFactory(**kwargs)
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"evt_u_{member.pk}", email=f"evt_u_{member.pk}@example.com")
    member.user = user
    member.save(update_fields=["user"])
    return member


def _future_event(**kwargs) -> CommunityEvent:
    starts = timezone.now() + timedelta(days=3)
    defaults = {"community": True, "starts_at": starts, "ends_at": starts + timedelta(hours=2)}
    defaults.update(kwargs)
    return CommunityEventFactory(**defaults)


def describe_EventRSVP():
    def describe_the_unique_constraint():
        def it_blocks_two_rsvps_from_the_same_member():
            event = _future_event()
            member = MemberFactory()
            EventRSVPFactory(event=event, member=member)
            with pytest.raises(IntegrityError):
                EventRSVP.objects.create(event=event, member=member)

        def it_allows_the_same_member_on_two_events():
            member = MemberFactory()
            EventRSVPFactory(event=_future_event(), member=member)
            EventRSVPFactory(event=_future_event(), member=member)
            assert EventRSVP.objects.filter(member=member).count() == 2

    def describe_str():
        def it_names_the_member_and_the_event():
            member = MemberFactory(full_legal_name="Jo Plaza")
            event = _future_event(title="Monthly Potluck")
            rsvp = EventRSVPFactory(event=event, member=member)
            assert str(rsvp) == "Jo Plaza → Monthly Potluck"


def describe_toggle_rsvp():
    def it_adds_the_rsvp_and_returns_true_when_not_yet_going():
        event = _future_event()
        member = MemberFactory()
        assert event.toggle_rsvp(member) is True
        assert EventRSVP.objects.filter(event=event, member=member).exists()

    def it_removes_the_rsvp_and_returns_false_when_already_going():
        event = _future_event()
        member = MemberFactory()
        event.toggle_rsvp(member)
        assert event.toggle_rsvp(member) is False
        assert not EventRSVP.objects.filter(event=event, member=member).exists()

    def it_round_trips_cleanly():
        event = _future_event()
        member = MemberFactory()
        assert [event.toggle_rsvp(member) for _ in range(4)] == [True, False, True, False]

    def it_treats_a_concurrent_duplicate_insert_as_already_existing():
        event = _future_event()
        member = MemberFactory()
        with patch.object(EventRSVP.objects, "get_or_create", side_effect=IntegrityError):
            # The unique-constraint race path: the caught IntegrityError is treated as
            # "already existed", so the toggle reports un-RSVP without a 500.
            assert event.toggle_rsvp(member) is False


def describe_attendees_field():
    def it_invites_the_first_rsvp_when_empty():
        field = _future_event().attendees_field()
        assert field == {"name": "Attendees (0)", "value": "No RSVPs yet. Click RSVP below to be the first."}

    def it_lists_a_single_attendee_by_display_name():
        event = _future_event()
        EventRSVPFactory(event=event, member=MemberFactory(full_legal_name="Sam K"))
        field = event.attendees_field()
        assert field["name"] == "Attendees (1)"
        assert field["value"] == "Sam K"

    def it_lists_all_fifteen_without_an_overflow_tail():
        event = _future_event()
        for _ in range(15):
            EventRSVPFactory(event=event)
        field = event.attendees_field()
        assert field["name"] == "Attendees (15)"
        assert "more" not in field["value"]
        assert field["value"].count(",") == 14

    def it_caps_at_fifteen_names_then_and_n_more():
        event = _future_event()
        for _ in range(18):
            EventRSVPFactory(event=event)
        field = event.attendees_field()
        assert field["name"] == "Attendees (18)"
        assert field["value"].endswith("and 3 more")

    def it_stays_under_the_1024_char_field_cap():
        event = _future_event()
        for _ in range(15):
            EventRSVPFactory(event=event, member=MemberFactory(full_legal_name="N" * 100))
        field = event.attendees_field()
        assert len(field["value"]) <= 1024
        assert field["value"].endswith("…")


def describe_discord_announcement_embed():
    def it_links_the_title_to_the_public_page_in_calendar_blue(settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        event = _future_event(title="Monthly Potluck")
        embed = event.discord_announcement_embed()
        assert embed["title"] == "Monthly Potluck"
        assert embed["url"] == event.public_url
        assert embed["color"] == 0x3D8BD4

    def it_always_carries_time_duration_and_attendees_fields():
        event = _future_event()
        names = [f["name"] for f in event.discord_announcement_embed()["fields"]]
        assert names == ["Time", "Duration", "Attendees (0)"]

    def it_adds_location_only_when_set():
        with_loc = [f["name"] for f in _future_event(location="Common Area").discord_announcement_embed()["fields"]]
        assert "Location" in with_loc
        without = [f["name"] for f in _future_event().discord_announcement_embed()["fields"]]
        assert "Location" not in without

    def it_adds_repeats_only_for_a_recurring_series():
        recurring = _future_event(recurrence=CommunityEvent.Recurrence.WEEKLY)
        fields = {f["name"]: f["value"] for f in recurring.discord_announcement_embed()["fields"]}
        assert fields["Repeats"] == "Every week"
        oneoff = [f["name"] for f in _future_event().discord_announcement_embed()["fields"]]
        assert "Repeats" not in oneoff

    def it_footers_the_creators_display_name():
        creator = _linked_member(full_legal_name="Tricia M")
        event = _future_event(created_by=creator.user)
        assert event.discord_announcement_embed()["footer"]["text"] == "RSVP below · Created by Tricia M"

    def it_credits_the_submitter_when_there_is_no_creator():
        submitter = _linked_member(full_legal_name="Lee P")
        event = _future_event(created_by=None, submitted_by=submitter.user)
        assert "Created by Lee P" in event.discord_announcement_embed()["footer"]["text"]

    def it_falls_back_to_past_lives_for_a_creatorless_row():
        event = _future_event(created_by=None, submitted_by=None)
        assert event.discord_announcement_embed()["footer"]["text"] == "RSVP below · Created by Past Lives"

    def it_omits_the_description_when_blank():
        assert "description" not in _future_event(description="").discord_announcement_embed()

    def it_keeps_a_short_description_verbatim():
        assert _future_event(description="Bring a dish.").discord_announcement_embed()["description"] == "Bring a dish."

    def it_trims_a_long_description_with_a_page_pointer():
        event = _future_event(description="x" * 900)
        embed = event.discord_announcement_embed()
        assert embed["description"].endswith("more on the event page")
        assert len(embed["description"]) <= 600 + len("… more on the event page")

    def it_shows_an_attendable_time_for_a_recurring_series():
        now = timezone.now()
        event = _future_event(
            recurrence=CommunityEvent.Recurrence.WEEKLY,
            starts_at=now - timedelta(days=30),
            ends_at=now - timedelta(days=30) + timedelta(hours=2),
        )
        time_value = {f["name"]: f["value"] for f in event.discord_announcement_embed()["fields"]}["Time"]
        anchor_day = timezone.localtime(now - timedelta(days=30)).strftime("%b %-d")
        assert anchor_day not in time_value


def describe_discord_announcement_components():
    def it_carries_an_rsvp_toggle_and_a_manage_button():
        event = _future_event()
        buttons = event.discord_announcement_components()[0]["components"]
        assert [b["custom_id"] for b in buttons] == [f"event:rsvp:{event.pk}", f"event:manage:{event.pk}"]
        assert buttons[0]["style"] == 3  # success
        assert buttons[1]["style"] == 2  # secondary


def describe_the_duration_field():
    @pytest.mark.parametrize(
        "minutes,expected",
        [(120, "2 hours"), (90, "1 hour 30 minutes"), (45, "45 minutes"), (60, "1 hour"), (1, "1 minute")],
    )
    def it_humanizes_the_length(minutes, expected):
        starts = timezone.now() + timedelta(days=1)
        event = _future_event(starts_at=starts, ends_at=starts + timedelta(minutes=minutes))
        value = {f["name"]: f["value"] for f in event.discord_announcement_embed()["fields"]}["Duration"]
        assert value == expected


def describe_next_occurrence_start():
    def it_returns_the_anchor_for_a_one_off():
        event = _future_event()
        assert event.next_occurrence_start() == event.starts_at

    def it_falls_back_to_the_anchor_when_no_occurrence_lands_in_the_horizon():
        now = timezone.now()
        far = _future_event(starts_at=now + timedelta(days=400), ends_at=now + timedelta(days=400, hours=2))
        assert far.next_occurrence_start() == far.starts_at

    def it_finds_a_future_occurrence_for_a_recurring_series():
        now = timezone.now()
        event = _future_event(
            recurrence=CommunityEvent.Recurrence.WEEKLY,
            starts_at=now - timedelta(days=30),
            ends_at=now - timedelta(days=30) + timedelta(hours=2),
        )
        assert event.next_occurrence_start() + (event.ends_at - event.starts_at) >= now

    def it_skips_this_weeks_occurrence_once_it_has_finished():
        # Anchor two hours ago (this week's occurrence, already over) so the loop passes it
        # and returns next week's — the attendable one. (Away from local midnight so the
        # occurrence lands on today's date.)
        now = timezone.now()
        anchor = now - timedelta(days=7) - timedelta(hours=2)
        event = _future_event(
            recurrence=CommunityEvent.Recurrence.WEEKLY, starts_at=anchor, ends_at=anchor + timedelta(minutes=1)
        )
        assert event.next_occurrence_start() > now


def describe_rsvps_closed():
    def it_is_true_for_a_finished_one_off():
        past = timezone.now() - timedelta(days=2)
        assert _future_event(starts_at=past, ends_at=past + timedelta(hours=1)).rsvps_closed is True

    def it_is_false_for_an_upcoming_one_off():
        assert _future_event().rsvps_closed is False

    def it_stays_open_for_a_recurring_series_with_a_past_anchor():
        past = timezone.now() - timedelta(days=30)
        event = _future_event(
            recurrence=CommunityEvent.Recurrence.WEEKLY, starts_at=past, ends_at=past + timedelta(hours=1)
        )
        assert event.rsvps_closed is False


_PATCH_URL = "https://discord.com/api/v10/channels/chan/messages/msg"


def _announced_event(**overrides) -> CommunityEvent:
    return _future_event(discord_announce_channel_id="chan", discord_announce_message_id="msg", **overrides)


def describe_refresh_discord_announcement():
    @respx.mock
    def it_patches_only_the_embeds_leaving_the_buttons(settings):
        settings.DISCORD_BOT_TOKEN = "bot"
        settings.MEMBER_BASE_URL = "https://members.example"
        route = respx.patch(_PATCH_URL).mock(return_value=httpx.Response(200, json={"id": "msg"}))
        _announced_event().refresh_discord_announcement()
        payload = json.loads(route.calls.last.request.content)
        assert "embeds" in payload
        assert "components" not in payload  # omitted → Discord keeps the existing buttons

    def it_noops_when_the_message_ids_are_unset():
        with respx.mock:  # no routes — any HTTP call fails the spec
            _future_event().refresh_discord_announcement()

    @respx.mock
    def it_swallows_a_discord_failure(settings):
        settings.DISCORD_BOT_TOKEN = "bot"
        settings.MEMBER_BASE_URL = "https://members.example"
        respx.patch(_PATCH_URL).mock(return_value=httpx.Response(500, text="boom"))
        _announced_event().refresh_discord_announcement()  # must not raise


def describe_strip_discord_announcement_buttons():
    @respx.mock
    def it_clears_the_button_row(settings):
        settings.DISCORD_BOT_TOKEN = "bot"
        settings.MEMBER_BASE_URL = "https://members.example"
        route = respx.patch(_PATCH_URL).mock(return_value=httpx.Response(200, json={"id": "msg"}))
        _announced_event().strip_discord_announcement_buttons()
        assert json.loads(route.calls.last.request.content)["components"] == []

    def it_noops_when_the_message_ids_are_unset():
        with respx.mock:
            _future_event().strip_discord_announcement_buttons()

    @respx.mock
    def it_swallows_a_discord_failure(settings):
        settings.DISCORD_BOT_TOKEN = "bot"
        settings.MEMBER_BASE_URL = "https://members.example"
        respx.patch(_PATCH_URL).mock(return_value=httpx.Response(500, text="boom"))
        _announced_event().strip_discord_announcement_buttons()  # must not raise


def describe_can_manage_from_discord():
    def it_lets_a_fog_admin_manage_any_event():
        admin = _linked_member(fog_role=Member.FogRole.ADMIN)
        assert _future_event().can_manage_from_discord(admin) is True

    def it_lets_the_guild_lead_manage_their_guilds_event():
        lead = _linked_member()
        guild = GuildFactory(name="Clay")
        guild.guild_lead = lead
        guild.save(update_fields=["guild_lead"])
        event = CommunityEventFactory(guild=guild)
        assert event.can_manage_from_discord(lead) is True

    def it_lets_guild_staff_manage_their_guilds_event():
        staffer = _linked_member()
        guild = GuildFactory(name="Metal")
        GuildStaffMembershipFactory(guild=guild, member=staffer)
        event = CommunityEventFactory(guild=guild)
        assert event.can_manage_from_discord(staffer) is True

    def it_lets_the_creator_manage_via_created_by():
        creator = _linked_member()
        event = _future_event(created_by=creator.user)
        assert event.can_manage_from_discord(creator) is True

    def it_lets_the_creator_manage_via_submitted_by():
        proposer = _linked_member()
        event = _future_event(created_by=None, submitted_by=proposer.user)
        assert event.can_manage_from_discord(proposer) is True

    def it_refuses_an_unrelated_plain_member():
        stranger = _linked_member()
        event = CommunityEventFactory(guild=GuildFactory(name="Not Theirs"))
        assert event.can_manage_from_discord(stranger) is False

    def it_refuses_a_userless_member_who_is_not_a_lead():
        member = MemberFactory()  # no linked user
        assert _future_event().can_manage_from_discord(member) is False
