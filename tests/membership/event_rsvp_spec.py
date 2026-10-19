"""BDD specs for the event-RSVP model layer (membership.models).

Covers the ``EventRSVP`` row + unique constraint, ``CommunityEvent.toggle_rsvp`` (create /
delete round-trip and the race backstop), ``attendees_field`` at 0 / 1 / 15 / 16+ (cap copy,
count in the field name, 1024-char guard), the rich ``discord_announcement_embed`` field rules
and footer fallback, the humanized duration, ``next_occurrence_start`` for a recurring series,
``rsvps_closed``, and ``can_manage_from_discord`` per authority tier. Then the guest side
(#831, part 4): the three constraints, ``rsvp_guest``'s upsert by email, finding a member's row by email, the people count,
party sizes, a guest row's names and contact, ``guest_list``'s query count, the Attendees
field folding guests, and the Interested sync leaving guest rows alone.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
import respx
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
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
    tiny_png_bytes,
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

    def it_leaves_a_hidden_member_out_of_the_names_and_the_count():
        event = _future_event()
        EventRSVPFactory(event=event, member=MemberFactory(full_legal_name="Sam K"))
        EventRSVPFactory(event=event, member=MemberFactory(full_legal_name="Qwyll Reviewbot", hide_from_directory=True))
        field = event.attendees_field()
        assert field == {"name": "Attendees (1)", "value": "Sam K"}

    def it_reads_as_empty_when_only_a_hidden_member_rsvped():
        event = _future_event()
        EventRSVPFactory(event=event, member=MemberFactory(hide_from_directory=True))
        assert event.attendees_field()["name"] == "Attendees (0)"

    def it_lists_all_fifteen_without_an_overflow_tail():
        event = _future_event()
        for _ in range(15):
            EventRSVPFactory(event=event)
        field = event.attendees_field()
        assert field["name"] == "Attendees (15)"
        # Assert the TAIL, not the substring "more": these are Faker names, and the
        # surname Gilmore contains it, so `"more" not in value` failed about one run in a
        # hundred on names that were never the point (it took main red on 2026-09-08).
        assert not re.search(r", and \d+ more$", field["value"])
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

    def describe_the_photo():
        def it_carries_the_photo_as_the_embed_image(settings):
            # Discord fetches this URL itself, from its servers, so it has to be absolute —
            # a storage-relative path would render as a broken card.
            settings.MEMBER_BASE_URL = "https://members.example"
            event = _future_event()
            event.photo.save("flyer.png", ContentFile(tiny_png_bytes()), save=True)
            assert event.discord_announcement_embed()["image"] == {"url": event.photo_url}
            assert event.photo_url.startswith("https://")

        def it_leaves_the_card_exactly_as_it_was_without_a_photo():
            assert "image" not in _future_event().discord_announcement_embed()

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


# --- Guest RSVPs and party sizes (#831, part 4) ---------------------------------------------


def _guest(event: CommunityEvent, **kwargs) -> EventRSVP:
    """Someone with no account on the list: the factory's ``guest`` trait."""
    return EventRSVPFactory(event=event, guest=True, **kwargs)


def describe_the_guest_constraints():
    def it_still_keeps_one_row_per_member_per_event():
        event = _future_event()
        member = MemberFactory()
        EventRSVPFactory(event=event, member=member)
        with pytest.raises(IntegrityError):
            EventRSVP.objects.create(event=event, member=member)

    def it_keeps_one_row_per_guest_email_per_event():
        event = _future_event()
        _guest(event, email="lee@example.com")
        with pytest.raises(IntegrityError):
            EventRSVP.objects.create(event=event, name="Lee again", email="Lee@Example.com")

    def it_lets_the_same_guest_email_onto_two_events():
        _guest(_future_event(), email="lee@example.com")
        _guest(_future_event(), email="lee@example.com")
        assert EventRSVP.objects.filter(email="lee@example.com").count() == 2

    def it_lets_two_member_rows_on_one_event_share_a_blank_email():
        event = _future_event()
        EventRSVPFactory(event=event)
        EventRSVPFactory(event=event)
        assert event.rsvps.filter(email="").count() == 2

    def it_refuses_a_row_with_neither_a_member_nor_an_email():
        with pytest.raises(IntegrityError):
            EventRSVP.objects.create(event=_future_event(), name="Nobody", email="")

    def it_lowercases_and_trims_the_email_on_save():
        rsvp = _guest(_future_event(), email="  Lee@Example.COM ")
        rsvp.refresh_from_db()
        assert rsvp.email == "lee@example.com"


def describe_rsvp_guest():
    def it_writes_a_site_row_with_the_email_normalised():
        event = _future_event()
        rsvp = event.rsvp_guest(
            name=" Lee ", email=" Lee@Example.com ", phone=" 503 ", party_size=3, wants_updates=True
        )
        assert rsvp.is_guest and rsvp.member is None
        assert (rsvp.name, rsvp.email, rsvp.phone, rsvp.party_size, rsvp.wants_updates) == (
            "Lee",
            "lee@example.com",
            "503",
            3,
            True,
        )
        assert rsvp.source == EventRSVP.Source.SITE
        assert rsvp.note == ""

    def it_updates_the_row_on_a_repeat_email_and_keeps_its_door_and_note():
        event = _future_event()
        first = event.rsvp_guest(name="Lee", email="lee@example.com", source=EventRSVP.Source.STAFF, note="Plus one")
        again = event.rsvp_guest(name="Lee P", email="LEE@example.com", phone="503", party_size=4, wants_updates=True)
        assert again.pk == first.pk
        assert event.rsvps.count() == 1
        again.refresh_from_db()
        assert (again.name, again.phone, again.party_size, again.wants_updates) == ("Lee P", "503", 4, True)
        assert again.source == EventRSVP.Source.STAFF
        assert again.note == "Plus one"

    def it_replaces_the_note_when_staff_write_one_blank_included():
        event = _future_event()
        event.rsvp_guest(name="Lee", email="lee@example.com", note="Plus one")
        rsvp = event.rsvp_guest(name="Lee", email="lee@example.com", source=EventRSVP.Source.STAFF, note=" ")
        rsvp.refresh_from_db()
        assert rsvp.note == ""


def describe_member_rsvp_for_email():
    def it_finds_an_unlinked_members_row_by_their_email_in_any_case():
        event = _future_event()
        rsvp = EventRSVPFactory(event=event, member=MemberFactory(_pre_signup_email="Sam@Example.com"))
        assert event.member_rsvp_for_email(" SAM@example.COM ") == rsvp

    def it_finds_a_linked_members_row_by_their_primary_email():
        event = _future_event()
        member = _linked_member()
        EmailAddress.objects.create(user=member.user, email="Primary@Example.com", primary=True, verified=True)
        rsvp = EventRSVPFactory(event=event, member=member)
        assert event.member_rsvp_for_email("primary@example.com") == rsvp

    def it_ignores_guest_rows_other_events_and_a_blank_email():
        event = _future_event()
        _guest(event, email="lee@example.com")
        EventRSVPFactory(event=_future_event(), member=MemberFactory(_pre_signup_email="sam@example.com"))
        assert event.member_rsvp_for_email("lee@example.com") is None
        assert event.member_rsvp_for_email("sam@example.com") is None
        assert event.member_rsvp_for_email("  ") is None


def describe_rsvp_people_count():
    def it_sums_party_sizes_across_members_and_guests():
        event = _future_event()
        EventRSVPFactory(event=event, party_size=2)
        _guest(event, party_size=3)
        assert event.rsvp_people_count() == 5

    def it_is_zero_with_no_rsvps():
        assert _future_event().rsvp_people_count() == 0

    def it_leaves_a_hidden_member_out_and_keeps_the_guests():
        event = _future_event()
        EventRSVPFactory(event=event, member=MemberFactory(hide_from_directory=True), party_size=4)
        _guest(event, party_size=2)
        assert event.rsvp_people_count() == 2


def describe_party_sizes():
    def it_sizes_a_new_rsvp_from_the_toggle():
        event = _future_event()
        member = MemberFactory()
        assert event.toggle_rsvp(member, party_size=4) is True
        assert event.rsvps.get(member=member).party_size == 4

    def it_brings_one_person_from_the_discord_button():
        event = _future_event()
        member = MemberFactory()
        event.toggle_rsvp(member)
        assert event.rsvps.get(member=member).party_size == 1

    def it_resizes_a_members_rsvp():
        event = _future_event()
        member = MemberFactory()
        event.toggle_rsvp(member)
        assert event.set_member_party_size(member, 3) is True
        assert event.rsvps.get(member=member).party_size == 3

    def it_reports_no_rsvp_to_resize_and_creates_none():
        event = _future_event()
        assert event.set_member_party_size(MemberFactory(), 3) is False
        assert not event.rsvps.exists()


def describe_a_guest_row():
    def it_displays_the_guests_own_name_and_contact():
        rsvp = _guest(_future_event(title="Potluck"), name="Lee P", email="lee@example.com", phone="503")
        assert rsvp.display_name == "Lee P"
        assert str(rsvp) == "Lee P → Potluck"
        assert rsvp.contact_email == "lee@example.com"
        assert rsvp.contact_phone == "503"

    def it_reads_a_members_contact_off_the_member():
        member = _linked_member(full_legal_name="Sam K", phone="971")
        EmailAddress.objects.create(user=member.user, email="sam@example.com", primary=True, verified=True)
        rsvp = EventRSVPFactory(event=_future_event(), member=member)
        assert rsvp.display_name == "Sam K"
        assert rsvp.contact_email == "sam@example.com"
        assert rsvp.contact_phone == "971"

    def it_is_read_only_only_as_an_eventbrite_row():
        eventbrite = _guest(_future_event(), source=EventRSVP.Source.EVENTBRITE, eventbrite_order_id="order-1")
        assert eventbrite.is_read_only is True
        assert _guest(_future_event()).is_read_only is False

    def it_checks_in_and_undoes():
        rsvp = _guest(_future_event())
        assert rsvp.is_checked_in is False
        assert rsvp.toggle_check_in() is True
        rsvp.refresh_from_db()
        assert rsvp.checked_in_at is not None
        assert rsvp.toggle_check_in() is False
        rsvp.refresh_from_db()
        assert rsvp.checked_in_at is None


def describe_guest_list():
    def it_reads_every_contact_email_in_two_queries(django_assert_num_queries):
        event = _future_event()
        for i in range(3):
            member = _linked_member()
            EmailAddress.objects.create(user=member.user, email=f"m{i}@example.com", primary=True, verified=True)
            EventRSVPFactory(event=event, member=member)
        EventRSVPFactory(event=event, member=MemberFactory(hide_from_directory=True))
        _guest(event, email="lee@example.com")
        # The rows with their members and users, then the prefetched primary addresses.
        with django_assert_num_queries(2):
            emails = [rsvp.contact_email for rsvp in event.guest_list()]
        assert len(emails) == 5
        assert {"m0@example.com", "m1@example.com", "m2@example.com", "lee@example.com"} <= set(emails)


def describe_attendees_field_with_guests():
    def it_counts_people_and_folds_guests_without_naming_them():
        event = _future_event()
        EventRSVPFactory(event=event, member=MemberFactory(full_legal_name="Sam K"), party_size=3)
        _guest(event, name="Zzytrix Guest", party_size=2)
        field = event.attendees_field()
        assert field == {"name": "Attendees (5)", "value": "Sam K, and 4 guests"}

    def it_says_one_guest_in_the_singular():
        event = _future_event()
        EventRSVPFactory(event=event, member=MemberFactory(full_legal_name="Sam K"))
        _guest(event)
        assert event.attendees_field()["value"] == "Sam K, and 1 guest"

    def it_names_only_the_guests_when_no_member_is_coming():
        event = _future_event()
        _guest(event, party_size=2)
        assert event.attendees_field() == {"name": "Attendees (2)", "value": "2 guests"}

    def it_puts_the_overflow_tail_before_the_guests():
        event = _future_event()
        for _ in range(16):
            EventRSVPFactory(event=event)
        _guest(event, party_size=2)
        field = event.attendees_field()
        assert field["name"] == "Attendees (18)"
        assert field["value"].endswith(", and 1 more, and 2 guests")


def describe_reconcile_interested_with_guests():
    def it_leaves_guest_rows_alone():
        event = _future_event()
        MemberFactory(discord_user_id="111")
        event.reconcile_interested({"111"})
        guest = _guest(event)
        assert event.reconcile_interested(set()) is True
        assert list(event.rsvps.values_list("pk", flat=True)) == [guest.pk]

    def it_adds_a_member_beside_an_existing_guest():
        event = _future_event()
        _guest(event)
        member = MemberFactory(discord_user_id="111")
        assert event.reconcile_interested({"111"}) is True
        assert event.rsvps.filter(member=member, source=EventRSVP.Source.INTERESTED).exists()
