"""BDD specs for CommunityEvent CRUD: the form, the lead views (gated, guild-scoped,
cross-guild isolation), and the admin authoring endpoints + the Events tab."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import SiteConfiguration
from hub.forms import CommunityEventForm
from membership.models import CommunityEvent, Guild, GuildStaffMembership, Member
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory

ADD_HREF = b'href="/events/add/"'
PROPOSE_HREF = b'href="/events/propose/"'
TAB_SENTINEL = b"tab === 'events'"
SYNC_COPY = b"synced to the shared Past Lives Google Calendar"
_MEMBER = CommunityEvent.GoogleCalendarTarget.MEMBER
_PUBLIC = CommunityEvent.GoogleCalendarTarget.PUBLIC


def _set_policy(value: str) -> None:
    config = SiteConfiguration.load()
    config.member_event_policy = value
    config.save()


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _event_payload(**overrides: str) -> dict:
    data = {
        "title": "Forge Night",
        "starts_at": "2026-07-11T18:00",
        "ends_at": "2026-07-11T20:00",
        "location": "Main Studio",
        "description": "Come forge.",
        "recurrence": "none",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
def describe_audience_and_kind_questions():
    def it_asks_who_the_audience_is_without_naming_google_or_a_calendar():
        field = CommunityEventForm(can_choose_audience=True).fields["google_calendar_target"]
        assert field.label == "Who is the audience?"
        assert "calendar" not in field.label.lower()
        assert "google" not in field.label.lower()
        assert [label for _value, label in field.choices] == ["Members", "The public"]

    def it_moves_the_google_wording_into_the_hint():
        field = CommunityEventForm(can_choose_audience=True).fields["google_calendar_target"]
        assert "Google calendar" in field.help_text

    def it_defaults_the_audience_to_the_public():
        assert CommunityEventForm(can_choose_audience=True)["google_calendar_target"].value() == _PUBLIC

    def it_offers_exactly_guild_meeting_and_something_else_as_the_kind():
        field = CommunityEventForm(can_choose_audience=True).fields["event_type"]
        assert [(str(value), label) for value, label in field.choices] == [
            ("guild_meeting", "Guild meeting"),
            ("community", "Something else"),
        ]

    def it_never_offers_studio_hours_or_a_guild_lead_meeting_as_a_kind():
        offered = dict(CommunityEventForm(can_choose_audience=True).fields["event_type"].choices)
        assert CommunityEvent.EventType.STUDIO_HOURS not in offered
        assert CommunityEvent.EventType.LEAD_MEETING not in offered

    def it_asks_a_member_with_no_lead_or_staff_authority_neither_question():
        # Choosing the members calendar is a guild-staff and admin permission; everyone else
        # posts to the public calendar exactly as they always have.
        form = CommunityEventForm(can_choose_audience=False)
        assert "google_calendar_target" not in form.fields
        assert "event_type" not in form.fields
        assert "guild" in form.fields

    def it_omits_the_guild_picker_where_the_guild_is_already_the_page():
        form = CommunityEventForm(guild=GuildFactory(), can_choose_audience=True)
        assert "guild" not in form.fields
        assert "event_type" in form.fields

    def it_offers_a_visible_no_guild_choice_rather_than_a_blank_row():
        field = CommunityEventForm(can_choose_audience=True).fields["guild"]
        assert field.empty_label == "No guild"
        assert not field.required

    def it_lists_only_the_guilds_it_was_handed():
        mine = GuildFactory(name="Metals")
        GuildFactory(name="Textiles")
        form = CommunityEventForm(can_choose_audience=True, guild_choices=Guild.objects.filter(pk=mine.pk))
        assert list(form.fields["guild"].queryset) == [mine]

    def it_opens_on_guild_meeting_where_a_guild_is_the_page():
        assert (
            CommunityEventForm(guild=GuildFactory(), can_choose_audience=True)["event_type"].value() == "guild_meeting"
        )

    def it_opens_on_something_else_where_no_guild_is_settled():
        assert CommunityEventForm(can_choose_audience=True)["event_type"].value() == "community"


@pytest.mark.django_db
def describe_the_four_shapes():
    def it_stores_a_member_guild_meeting_as_a_guild_meeting():
        guild = GuildFactory()
        form = CommunityEventForm(
            data=_event_payload(google_calendar_target="member", event_type="guild_meeting", guild=str(guild.pk)),
            can_choose_audience=True,
        )
        assert form.is_valid(), form.errors
        event = form.save()
        assert event.event_type == CommunityEvent.EventType.GUILD_MEETING
        assert event.guild == guild

    def it_stores_a_member_something_else_as_a_plain_event_with_an_optional_guild():
        guild = GuildFactory()
        form = CommunityEventForm(
            data=_event_payload(google_calendar_target="member", event_type="community", guild=str(guild.pk)),
            can_choose_audience=True,
        )
        assert form.is_valid(), form.errors
        event = form.save()
        assert event.event_type == CommunityEvent.EventType.COMMUNITY
        assert event.guild == guild

    def it_keeps_an_unasked_author_on_the_public_calendar_even_if_they_post_otherwise():
        # The field is not on their form, so a hand-crafted POST cannot move the event onto
        # the members calendar.
        form = CommunityEventForm(data=_event_payload(google_calendar_target="member"), can_choose_audience=False)
        assert form.is_valid(), form.errors
        event = form.save()
        assert event.event_type == CommunityEvent.EventType.COMMUNITY
        assert event.google_calendar_target == _PUBLIC

    def it_stores_a_public_event_with_a_guild_as_a_plain_event():
        guild = GuildFactory()
        form = CommunityEventForm(
            data=_event_payload(google_calendar_target="public", guild=str(guild.pk)), can_choose_audience=True
        )
        assert form.is_valid(), form.errors
        event = form.save()
        assert event.event_type == CommunityEvent.EventType.COMMUNITY
        assert event.guild == guild
        assert event.google_calendar_target == _PUBLIC

    def it_requires_a_guild_for_a_guild_meeting():
        form = CommunityEventForm(
            data=_event_payload(google_calendar_target="member", event_type="guild_meeting"), can_choose_audience=True
        )
        assert not form.is_valid()
        assert "guild" in form.errors

    def it_shows_one_error_when_the_guild_choice_is_not_in_the_list():
        # "Select a valid choice" and "Pick the guild" together read as contradictory advice.
        form = CommunityEventForm(
            data=_event_payload(google_calendar_target="member", event_type="guild_meeting", guild="999999"),
            can_choose_audience=True,
        )
        assert not form.is_valid()
        assert len(form.errors["guild"]) == 1


@pytest.mark.django_db
def describe_kind_and_audience_are_independent():
    """A guild meeting is usually public — ``Meeting.add_to_calendar`` has always written
    ``guild_meeting`` + PUBLIC. Tying the kind to the members answer discarded a submitted
    kind on the public branch, which silently re-typed a guild meeting on every edit and
    dropped it off the guild's Next Meeting card."""

    def it_saves_a_public_guild_meeting():
        guild = GuildFactory()
        form = CommunityEventForm(
            data=_event_payload(google_calendar_target="public", event_type="guild_meeting", guild=str(guild.pk)),
            can_choose_audience=True,
        )
        assert form.is_valid(), form.errors
        event = form.save()
        assert event.event_type == CommunityEvent.EventType.GUILD_MEETING
        assert event.google_calendar_target == _PUBLIC

    def it_asks_the_kind_whichever_audience_is_chosen():
        # No conditional reveal: the field is simply on the form for anyone who may answer it.
        form = CommunityEventForm(can_choose_audience=True)
        assert "event_type" in form.fields
        assert form.fields["event_type"].label == "What kind of event is this?"

    def it_keeps_a_guild_meeting_a_guild_meeting_when_only_the_title_changes():
        guild = GuildFactory()
        event = CommunityEventFactory(guild_meeting=True, guild=guild, google_calendar_target=_PUBLIC)
        form = CommunityEventForm(
            instance=event,
            data=_event_payload(title="Renamed", event_type="guild_meeting", guild=str(guild.pk)),
            can_choose_audience=True,
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.title == "Renamed"
        assert saved.event_type == CommunityEvent.EventType.GUILD_MEETING

    def it_keeps_an_edited_guild_meeting_on_the_guilds_next_meeting_card():
        # The consequence the re-typing bug actually had: next_meeting_occurrence filters on
        # the type, so a silently re-typed row blanked the guild page's Next Meeting card.
        guild = GuildFactory()
        start = timezone.now() + timedelta(days=5)
        event = CommunityEventFactory(
            guild_meeting=True, guild=guild, starts_at=start, ends_at=start + timedelta(hours=2)
        )
        form = CommunityEventForm(
            instance=event,
            data=_event_payload(
                title="Renamed",
                event_type="guild_meeting",
                guild=str(guild.pk),
                starts_at=start.strftime("%Y-%m-%dT%H:%M"),
                ends_at=(start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
            ),
            can_choose_audience=True,
        )
        assert form.is_valid(), form.errors
        form.save()
        assert guild.next_meeting_occurrence() is not None

    def it_leaves_an_owned_proposals_kind_alone_when_the_author_is_never_asked():
        # A plain member editing their proposal must not undo a kind a staffer set.
        guild = GuildFactory()
        event = CommunityEventFactory(guild_meeting=True, guild=guild)
        form = CommunityEventForm(
            instance=event, data=_event_payload(title="Edited", guild=str(guild.pk)), can_choose_audience=False
        )
        assert form.is_valid(), form.errors
        assert form.save().event_type == CommunityEvent.EventType.GUILD_MEETING

    def it_still_makes_a_brand_new_unasked_event_a_plain_event():
        # The model default for a new row is GUILD_MEETING, so this cannot read the instance.
        form = CommunityEventForm(data=_event_payload(), can_choose_audience=False)
        assert form.is_valid(), form.errors
        assert form.save().event_type == CommunityEvent.EventType.COMMUNITY


@pytest.mark.django_db
def describe_rows_the_composer_never_writes():
    def it_keeps_the_stored_kind_of_a_guild_lead_meeting_and_asks_no_kind_question():
        event = CommunityEventFactory(lead_meeting=True)
        form = CommunityEventForm(
            instance=event, data=_event_payload(google_calendar_target="member"), can_choose_audience=True
        )
        assert "event_type" not in form.fields
        assert form.is_valid(), form.errors
        assert form.save().event_type == CommunityEvent.EventType.LEAD_MEETING

    def it_refuses_to_put_a_guild_on_a_guild_lead_meeting():
        event = CommunityEventFactory(lead_meeting=True)
        form = CommunityEventForm(
            instance=event, data=_event_payload(guild=str(GuildFactory().pk)), can_choose_audience=True
        )
        assert not form.is_valid()
        assert "guild" in form.errors

    def it_keeps_the_stored_kind_of_a_studio_hours_row():
        event = CommunityEventFactory(studio_hours=True)
        form = CommunityEventForm(
            instance=event, data=_event_payload(guild=str(event.guild.pk)), can_choose_audience=True
        )
        assert "event_type" not in form.fields
        assert form.is_valid(), form.errors
        assert form.save().event_type == CommunityEvent.EventType.STUDIO_HOURS

    def it_still_demands_a_guild_for_a_studio_hours_row():
        event = CommunityEventFactory(studio_hours=True)
        form = CommunityEventForm(instance=event, data=_event_payload(), can_choose_audience=True)
        assert not form.is_valid()
        assert "guild" in form.errors


@pytest.mark.django_db
def describe_form():
    def it_errors_when_end_is_not_after_start():
        form = CommunityEventForm(
            data=_event_payload(event_type="community", ends_at="2026-07-11T18:00", starts_at="2026-07-11T20:00"),
            can_choose_audience=True,
        )
        assert not form.is_valid()
        assert "End time must be after the start." in form.errors["ends_at"]

    def it_defaults_the_calendar_target_to_public_when_omitted():
        # The payload has no google_calendar_target — the forgiving clean coerces it to PUBLIC.
        form = CommunityEventForm(data=_event_payload(event_type="community"), can_choose_audience=True)
        assert form.is_valid(), form.errors
        assert form.cleaned_data["google_calendar_target"] == CommunityEvent.GoogleCalendarTarget.PUBLIC

    def it_saves_the_chosen_member_calendar_target():
        form = CommunityEventForm(
            data=_event_payload(event_type="community", google_calendar_target="member"), can_choose_audience=True
        )
        assert form.is_valid(), form.errors
        assert form.save().google_calendar_target == CommunityEvent.GoogleCalendarTarget.MEMBER

    def it_saves_the_chosen_public_calendar_target():
        form = CommunityEventForm(
            data=_event_payload(event_type="community", google_calendar_target="public"), can_choose_audience=True
        )
        assert form.is_valid(), form.errors
        assert form.save().google_calendar_target == CommunityEvent.GoogleCalendarTarget.PUBLIC

    def it_labels_the_video_url_field_video_link():
        form = CommunityEventForm(can_choose_audience=True)
        assert form.fields["video_url"].label == "Video link"

    def it_saves_a_valid_video_url():
        form = CommunityEventForm(
            data=_event_payload(event_type="community", video_url="https://meet.google.com/abc-defg-hij"),
            can_choose_audience=True,
        )
        assert form.is_valid(), form.errors
        assert form.save().video_url == "https://meet.google.com/abc-defg-hij"

    def it_errors_on_a_non_url_video_link():
        form = CommunityEventForm(
            data=_event_payload(event_type="community", video_url="not a url"), can_choose_audience=True
        )
        assert not form.is_valid()
        assert "video_url" in form.errors

    def it_rejects_a_javascript_scheme_video_url():
        # Security: the URLField alone accepts any scheme Django's URL regex matches, which
        # includes javascript:/data: — those would render straight into an href (event
        # detail page, calendar item, home upcoming widget) as an XSS vector. The model
        # field restricts to http/https via URLValidator(schemes=...), so a scheme-smuggled
        # value fails form validation just like a malformed URL would.
        form = CommunityEventForm(
            data=_event_payload(event_type="community", video_url="javascript:alert(1)"), can_choose_audience=True
        )
        assert not form.is_valid()
        assert "video_url" in form.errors

    def it_still_accepts_a_plain_http_video_url():
        form = CommunityEventForm(
            data=_event_payload(event_type="community", video_url="http://meet.example.com/x"), can_choose_audience=True
        )
        assert form.is_valid(), form.errors

    def it_allows_a_blank_video_url():
        form = CommunityEventForm(data=_event_payload(event_type="community"), can_choose_audience=True)
        assert form.is_valid(), form.errors
        assert form.save().video_url == ""

    def it_omits_video_url_from_the_studio_hours_form():
        from hub.forms import StudioHoursForm

        guild = GuildFactory()
        form = StudioHoursForm(guild=guild)
        assert "video_url" not in form.fields


@pytest.mark.django_db
def describe_lead_gating():
    def it_403s_a_non_staff_member_on_the_list(client: Client):
        _user_with_role("m1")
        guild = GuildFactory()
        client.login(username="m1", password="pass")
        assert client.get(reverse("hub_guild_events", args=[guild.pk])).status_code == 403

    def it_403s_a_non_staff_member_on_add(client: Client):
        _user_with_role("m2")
        guild = GuildFactory()
        client.login(username="m2", password="pass")
        assert client.get(reverse("hub_guild_event_add", args=[guild.pk])).status_code == 403

    def it_403s_a_non_staff_member_on_delete(client: Client):
        _user_with_role("m3")
        guild = GuildFactory()
        event = CommunityEventFactory(guild=guild)
        client.login(username="m3", password="pass")
        assert client.post(reverse("hub_guild_event_delete", args=[guild.pk, event.pk])).status_code == 403

    def it_redirects_the_guild_lead_to_the_events_tab(client: Client):
        # The list is now an in-page tab on the guild editor; the old list URL redirects there.
        user = _user_with_role("lead1")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="lead1", password="pass")
        resp = client.get(reverse("hub_guild_events", args=[guild.pk]))
        assert resp.status_code == 302
        assert resp["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=events"

    def it_redirects_a_staff_member_to_the_events_tab(client: Client):
        user = _user_with_role("staff1")
        guild = GuildFactory()
        GuildStaffMembership.objects.create(guild=guild, member=user.member, role=GuildStaffMembership.Role.SECRETARY)
        client.login(username="staff1", password="pass")
        resp = client.get(reverse("hub_guild_events", args=[guild.pk]))
        assert resp.status_code == 302
        assert resp["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=events"


@pytest.mark.django_db
def describe_lead_create_and_edit():
    def it_creates_a_guild_meeting_and_announces_once(client: Client):
        user = _user_with_role("c1")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="c1", password="pass")
        with patch.object(CommunityEvent, "announce") as mock_announce:
            resp = client.post(
                reverse("hub_guild_event_add", args=[guild.pk]),
                data=_event_payload(google_calendar_target="member", event_type="guild_meeting"),
            )
        assert resp.status_code == 302
        event = CommunityEvent.objects.get(guild=guild)
        assert event.event_type == CommunityEvent.EventType.GUILD_MEETING
        assert event.created_by == user
        mock_announce.assert_called_once()

    def it_creates_a_public_event_the_guild_hosts(client: Client):
        # The audience defaults to the public, and a public event is never a guild meeting —
        # it stays attached to the guild whose page authored it.
        user = _user_with_role("c1b")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="c1b", password="pass")
        with patch.object(CommunityEvent, "announce"):
            resp = client.post(reverse("hub_guild_event_add", args=[guild.pk]), data=_event_payload(title="Open House"))
        assert resp.status_code == 302
        event = CommunityEvent.objects.get(title="Open House")
        assert event.event_type == CommunityEvent.EventType.COMMUNITY
        assert event.guild == guild
        assert event.google_calendar_target == _PUBLIC

    def it_asks_a_lead_the_kind_question(client: Client):
        user = _user_with_role("c1c")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="c1c", password="pass")
        html = client.get(reverse("hub_guild_event_add", args=[guild.pk])).content.decode()
        assert "What kind of event is this?" in html
        assert "Who is the audience?" in html

    def it_creates_a_guild_meeting_from_the_guild_tab_with_the_defaults(client: Client):
        # A lead adding the monthly meeting and changing nothing gets a guild meeting, which
        # is what main did. The kind opens on Guild meeting because the guild is the page.
        user = _user_with_role("c1d")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="c1d", password="pass")
        add_url = reverse("hub_guild_event_add", args=[guild.pk])
        assert client.get(add_url).context["form"]["event_type"].value() == "guild_meeting"
        with patch.object(CommunityEvent, "announce"):
            client.post(add_url, data=_event_payload(title="Monthly Meeting", event_type="guild_meeting"))
        event = CommunityEvent.objects.get(title="Monthly Meeting")
        assert event.event_type == CommunityEvent.EventType.GUILD_MEETING
        assert event.guild == guild
        assert event.google_calendar_target == _PUBLIC  # public by default, still a meeting

    def it_does_not_re_announce_on_edit(client: Client):
        user = _user_with_role("c2")
        guild = GuildFactory(guild_lead=user.member)
        event = CommunityEventFactory(guild=guild, title="Old")
        client.login(username="c2", password="pass")
        with patch.object(CommunityEvent, "announce") as mock_announce:
            resp = client.post(
                reverse("hub_guild_event_edit", args=[guild.pk, event.pk]), data=_event_payload(title="New")
            )
        assert resp.status_code == 302
        event.refresh_from_db()
        assert event.title == "New"
        mock_announce.assert_not_called()


@pytest.mark.django_db
def describe_cross_guild_isolation():
    def it_404s_a_lead_editing_another_guilds_event(client: Client):
        user = _user_with_role("iso1")
        guild_a = GuildFactory(guild_lead=user.member)
        guild_b = GuildFactory()
        b_event = CommunityEventFactory(guild=guild_b, title="B's event")
        client.login(username="iso1", password="pass")
        resp = client.post(
            reverse("hub_guild_event_edit", args=[guild_a.pk, b_event.pk]), data=_event_payload(title="Hijacked")
        )
        assert resp.status_code == 404
        b_event.refresh_from_db()
        assert b_event.title == "B's event"

    def it_404s_a_lead_deleting_another_guilds_event(client: Client):
        user = _user_with_role("iso2")
        guild_a = GuildFactory(guild_lead=user.member)
        guild_b = GuildFactory()
        b_event = CommunityEventFactory(guild=guild_b)
        client.login(username="iso2", password="pass")
        resp = client.post(reverse("hub_guild_event_delete", args=[guild_a.pk, b_event.pk]))
        assert resp.status_code == 404
        assert CommunityEvent.objects.filter(pk=b_event.pk).exists()


@pytest.mark.django_db
def describe_admin_authoring():
    def it_403s_a_non_admin_on_add(client: Client):
        _user_with_role("na1")
        client.login(username="na1", password="pass")
        assert client.get(reverse("hub_event_add")).status_code == 403

    def it_403s_a_non_admin_on_delete(client: Client):
        _user_with_role("na2")
        event = CommunityEventFactory(community=True)
        client.login(username="na2", password="pass")
        assert client.post(reverse("hub_event_delete", args=[event.pk])).status_code == 403

    def it_lets_an_admin_open_the_add_page(client: Client):
        _user_with_role("ad1", fog_role=Member.FogRole.ADMIN)
        client.login(username="ad1", password="pass")
        assert client.get(reverse("hub_event_add")).status_code == 200

    def it_asks_an_admin_both_questions(client: Client):
        _user_with_role("adq", fog_role=Member.FogRole.ADMIN)
        client.login(username="adq", password="pass")
        html = client.get(reverse("hub_event_add")).content.decode()
        assert "Who is the audience?" in html
        assert "What kind of event is this?" in html

    def it_lists_every_active_guild_for_an_admin(client: Client):
        _user_with_role("adg", fog_role=Member.FogRole.ADMIN)
        live = GuildFactory(name="Live Guild")
        GuildFactory(name="Retired Guild", is_active=False)
        client.login(username="adg", password="pass")
        resp = client.get(reverse("hub_event_add"))
        assert list(resp.context["form"].fields["guild"].queryset) == [live]

    def it_keeps_a_deactivated_guild_in_the_picker_for_its_own_event(client: Client):
        # Deactivating a guild keeps its rows, so these events exist. Without its own guild in
        # the queryset every save fails on "Select a valid choice" and a guild meeting cannot
        # be blanked either, so the event becomes uneditable.
        _user_with_role("addg", fog_role=Member.FogRole.ADMIN)
        retired = GuildFactory(name="Retired Guild", is_active=False)
        event = CommunityEventFactory(guild_meeting=True, guild=retired)
        client.login(username="addg", password="pass")
        resp = client.get(reverse("hub_event_edit", args=[event.pk]))
        assert retired in list(resp.context["form"].fields["guild"].queryset)

    def it_saves_an_event_on_a_deactivated_guild(client: Client):
        _user_with_role("addg2", fog_role=Member.FogRole.ADMIN)
        retired = GuildFactory(name="Retired Guild", is_active=False)
        event = CommunityEventFactory(guild_meeting=True, guild=retired, title="Old Meeting")
        client.login(username="addg2", password="pass")
        resp = client.post(
            reverse("hub_event_edit", args=[event.pk]),
            data=_event_payload(title="Renamed", event_type="guild_meeting", guild=str(retired.pk)),
        )
        assert resp.status_code == 302
        event.refresh_from_db()
        assert event.title == "Renamed"
        assert event.guild == retired

    def it_lets_an_admin_attach_a_guild_to_a_public_event(client: Client):
        _user_with_role("adh", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="adh", password="pass")
        with patch.object(CommunityEvent, "announce"):
            client.post(reverse("hub_event_add"), data=_event_payload(title="Hosted Night", guild=str(guild.pk)))
        event = CommunityEvent.objects.get(title="Hosted Night")
        assert event.guild == guild
        assert event.event_type == CommunityEvent.EventType.COMMUNITY

    def it_badges_each_event_in_the_events_tab_list(client: Client):
        # The acceptance criterion asks the Events tab itself to label every event, not just
        # the admin-only "Scheduled, not yet announced" block.
        _user_with_role("adbadge", fog_role=Member.FogRole.ADMIN)
        CommunityEventFactory(community=True, title="Listed Potluck", google_calendar_target=_MEMBER)
        client.login(username="adbadge", password="pass")
        html = client.get(reverse("hub_community_calendar")).content.decode()
        assert '<div class="pl-calendar-list__audience"><span class="hub-badge">Member event</span></div>' in html

    def it_badges_a_parked_event_on_the_events_tab_by_its_audience(client: Client):
        # Scoped to the text right after the event's own title, which no changelog entry can
        # contain — a bare copy assertion would read the site-wide changelog too (STANDARDS §8).
        _user_with_role("adsch", fog_role=Member.FogRole.ADMIN)
        CommunityEventFactory(
            community=True,
            title="Parked Potluck",
            moderation_state=CommunityEvent.ModerationState.SCHEDULED,
            publish_at=timezone.now() + timedelta(days=1),
            google_calendar_target=_MEMBER,
        )
        client.login(username="adsch", password="pass")
        html = client.get(reverse("hub_community_calendar")).content.decode()
        assert "Member event" in html.split("Parked Potluck", 1)[1][:400]

    def it_renders_the_video_link_field_on_the_add_page(client: Client):
        _user_with_role("ad1v", fog_role=Member.FogRole.ADMIN)
        client.login(username="ad1v", password="pass")
        resp = client.get(reverse("hub_event_add"))
        assert b"Video link" in resp.content

    def it_creates_a_community_event_with_a_video_url(client: Client):
        _user_with_role("ad5", fog_role=Member.FogRole.ADMIN)
        client.login(username="ad5", password="pass")
        with patch.object(CommunityEvent, "announce"):
            client.post(
                reverse("hub_event_add"),
                data=_event_payload(
                    event_type="community", guild="", title="Streamed Potluck", video_url="https://meet.google.com/x"
                ),
            )
        event = CommunityEvent.objects.get(title="Streamed Potluck")
        assert event.video_url == "https://meet.google.com/x"

    def it_lets_an_admin_create_a_community_event_and_announces(client: Client):
        _user_with_role("ad2", fog_role=Member.FogRole.ADMIN)
        client.login(username="ad2", password="pass")
        with patch.object(CommunityEvent, "announce") as mock_announce:
            resp = client.post(
                reverse("hub_event_add"),
                data=_event_payload(event_type="community", guild="", title="Potluck"),
            )
        assert resp.status_code == 302
        event = CommunityEvent.objects.get(title="Potluck")
        assert event.is_site_wide
        mock_announce.assert_called_once()

    def it_deletes_an_event_as_admin(client: Client):
        _user_with_role("ad3", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True)
        client.login(username="ad3", password="pass")
        resp = client.post(reverse("hub_event_delete", args=[event.pk]))
        assert resp.status_code == 302
        assert not CommunityEvent.objects.filter(pk=event.pk).exists()

    def it_rejects_a_get_on_delete(client: Client):
        _user_with_role("ad4", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True)
        client.login(username="ad4", password="pass")
        assert client.get(reverse("hub_event_delete", args=[event.pk])).status_code == 405


@pytest.mark.django_db
def describe_a_guilds_own_events_tab():
    def _open_tab(client: Client, guild: Guild) -> Client:
        return client.get(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=events")

    def it_lists_a_public_event_the_guild_hosts(client: Client):
        # Keyed on "not studio hours" rather than "is a meeting", so the guild's own public
        # event cannot vanish from its own page (#505).
        user = _user_with_role("ge1")
        guild = GuildFactory(guild_lead=user.member)
        event = CommunityEventFactory(guild_hosted=True, guild=guild, title="Open House")
        client.login(username="ge1", password="pass")
        assert list(_open_tab(client, guild).context["events"]) == [event]

    def it_still_lists_the_guilds_meetings(client: Client):
        user = _user_with_role("ge2")
        guild = GuildFactory(guild_lead=user.member)
        event = CommunityEventFactory(guild_meeting=True, guild=guild)
        client.login(username="ge2", password="pass")
        assert list(_open_tab(client, guild).context["events"]) == [event]

    def it_leaves_studio_hours_to_their_own_editor(client: Client):
        user = _user_with_role("ge3")
        guild = GuildFactory(guild_lead=user.member)
        CommunityEventFactory(studio_hours=True, guild=guild)
        client.login(username="ge3", password="pass")
        assert list(_open_tab(client, guild).context["events"]) == []


def describe_member_facing_surfaces():
    """Asserted against the template sources rather than a rendered page: the changelog
    renders into every page, so a copy assertion could pass or fail on a release note
    instead (STANDARDS §8)."""

    _TEMPLATES = Path(__file__).resolve().parents[2] / "templates"
    _BADGE_SURFACES = [
        "hub/event_detail.html",
        "hub/community_calendar.html",
        "hub/partials/calendar_event_item.html",
    ]

    def it_routes_every_badge_through_the_one_property():
        # The Guild Lead Meeting special case lives in CommunityEvent.badge_label. A template
        # that branched on the stored type itself would drift from it.
        missing = [
            name for name in _BADGE_SURFACES if "badge_label" not in (_TEMPLATES / name).read_text(encoding="utf-8")
        ]
        assert missing == []

    def it_reads_the_stored_type_in_no_template():
        # Not "members never see the stored type" — a lead meeting badges its own type label.
        # The rule is that no template asks the model for it, so the one property decides.
        offenders = [
            str(path.relative_to(_TEMPLATES))
            for path in sorted(_TEMPLATES.rglob("*.html"))
            if "get_event_type_display" in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def it_never_says_community_event_in_member_facing_help():
        # The retired phrase, swept out of the Help Center copy members actually read.
        from membership import help_content

        source = Path(help_content.__file__).read_text(encoding="utf-8")
        assert "community event" not in source.lower()


@pytest.mark.django_db
def describe_events_tab_visibility():
    def it_shows_the_list_read_only_to_a_plain_member(client: Client):
        _user_with_role("v1")
        CommunityEventFactory(community=True, title="Open Mic Night")
        client.login(username="v1", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert resp.status_code == 200
        assert b"Open Mic Night" in resp.content
        assert b"+ Add event" not in resp.content
        assert reverse("hub_event_add").encode() not in resp.content

    def it_shows_management_controls_to_an_admin(client: Client):
        _user_with_role("v2", fog_role=Member.FogRole.ADMIN)
        CommunityEventFactory(community=True, title="Open Mic Night")
        client.login(username="v2", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert b"+ Add event" in resp.content
        assert reverse("hub_event_add").encode() in resp.content


@pytest.mark.django_db
def describe_header_cta():
    def it_shows_add_event_for_admin(client: Client):
        _user_with_role("hc_admin", fog_role=Member.FogRole.ADMIN)
        client.login(username="hc_admin", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert resp.status_code == 200
        assert ADD_HREF in resp.content
        assert b"+ Add event" in resp.content

    def it_shows_propose_for_member_when_policy_open(client: Client):
        _user_with_role("hc_member")
        _set_policy(SiteConfiguration.MemberEventPolicy.APPROVAL)
        client.login(username="hc_member", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert PROPOSE_HREF in resp.content
        assert b"+ Propose an event" in resp.content
        assert ADD_HREF not in resp.content

    def it_shows_propose_to_a_logged_out_visitor(client: Client):
        # /calendar/ is public and member_can_propose doesn't check auth; default policy is APPROVAL.
        resp = client.get(reverse("hub_community_calendar"))
        assert resp.status_code == 200
        assert PROPOSE_HREF in resp.content
        assert b"+ Propose an event" in resp.content

    def it_hides_cta_when_policy_disabled_and_not_admin(client: Client):
        _user_with_role("hc_disabled")
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)  # default is APPROVAL — must set explicitly
        client.login(username="hc_disabled", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert ADD_HREF not in resp.content
        assert PROPOSE_HREF not in resp.content

    def it_renders_the_cta_exactly_once(client: Client):
        # A member with an in-flight proposal also renders an edit link at
        # /events/propose/<pk>/edit/ — which *starts with* the propose-new path — so the
        # count must match the exact quoted href, never a bare substring, or it double-counts.
        user = _user_with_role("hc_once")
        _set_policy(SiteConfiguration.MemberEventPolicy.APPROVAL)
        CommunityEventFactory(
            community=True,
            submitted_by=user,
            moderation_state=CommunityEvent.ModerationState.PENDING,
            title="Pending Proposal",
        )
        client.login(username="hc_once", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert resp.content.count(PROPOSE_HREF) == 1
        # The one CTA lives in the header, above the tab bar — so it shows on the default Calendar tab.
        assert resp.content.index(PROPOSE_HREF) < resp.content.index(TAB_SENTINEL)


@pytest.mark.django_db
def describe_google_sync_note():
    def it_hides_the_sync_note_by_default(client: Client):
        # Both gates default false, so the note must not claim sync that isn't live.
        _user_with_role("sync_off")
        client.login(username="sync_off", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert SYNC_COPY not in resp.content

    @override_settings(GOOGLE_CALENDAR_SYNC_ENABLED=True)
    def it_shows_the_sync_note_when_sync_is_live(client: Client):
        # _google_sync_enabled() ANDs env + SiteConfiguration — both must be on.
        _user_with_role("sync_on")
        config = SiteConfiguration.load()
        config.google_calendar_sync_enabled = True
        config.save(update_fields=["google_calendar_sync_enabled"])
        client.login(username="sync_on", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert SYNC_COPY in resp.content


@pytest.mark.django_db
def describe_edit_page_delete_button():
    def it_shows_delete_on_the_site_wide_edit_page(client: Client):
        _user_with_role("dele1", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True, moderation_state=CommunityEvent.ModerationState.PUBLISHED)
        client.login(username="dele1", password="pass")
        resp = client.get(reverse("hub_event_edit", args=[event.pk]))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert reverse("hub_event_delete", args=[event.pk]) in html
        assert "open-confirm" in html
        assert "removed from Google Calendar and Discord" in html

    def it_shows_delete_on_the_guild_edit_page(client: Client):
        user = _user_with_role("dele2")
        guild = GuildFactory(guild_lead=user.member)
        event = CommunityEventFactory(guild=guild)
        client.login(username="dele2", password="pass")
        resp = client.get(reverse("hub_guild_event_edit", args=[guild.pk, event.pk]))
        assert resp.status_code == 200
        assert reverse("hub_guild_event_delete", args=[guild.pk, event.pk]) in resp.content.decode()

    def it_hides_delete_when_creating(client: Client):
        _user_with_role("dele3", fog_role=Member.FogRole.ADMIN)
        client.login(username="dele3", password="pass")
        resp = client.get(reverse("hub_event_add"))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "delete-event" not in html
        assert "Delete event" not in html

    def it_uses_unannounced_copy_for_scheduled_events(client: Client):
        _user_with_role("dele4", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True, moderation_state=CommunityEvent.ModerationState.SCHEDULED)
        client.login(username="dele4", password="pass")
        html = client.get(reverse("hub_event_edit", args=[event.pk])).content.decode()
        assert "ever announced" in html
        assert "removed from Google Calendar and Discord" not in html

    def it_uses_series_copy_for_recurring_events(client: Client):
        _user_with_role("dele5", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(
            community=True,
            moderation_state=CommunityEvent.ModerationState.PUBLISHED,
            recurrence=CommunityEvent.Recurrence.WEEKLY,
        )
        client.login(username="dele5", password="pass")
        html = client.get(reverse("hub_event_edit", args=[event.pk])).content.decode()
        assert "This removes the whole series." in html


@pytest.mark.django_db
def describe_calendar_subscribe_links():
    def it_offers_member_and_public_subscribe_links_when_configured(client: Client):
        _user_with_role("sub1")
        config = SiteConfiguration.load()
        config.member_google_calendar_id = "memid@group.calendar.google.com"
        config.public_google_calendar_id = "pubid@group.calendar.google.com"
        config.save()
        client.login(username="sub1", password="pass")

        body = client.get(reverse("hub_community_calendar")).content.decode()

        assert "webcal://calendar.google.com/calendar/ical/memid%40group.calendar.google.com/public/basic.ics" in body
        assert "webcal://calendar.google.com/calendar/ical/pubid%40group.calendar.google.com/public/basic.ics" in body
        assert "Subscribe to the Member calendar" in body
        assert "Subscribe to the Public calendar" in body

    def it_hides_a_subscribe_link_when_its_calendar_is_unset(client: Client):
        _user_with_role("sub2")
        config = SiteConfiguration.load()
        config.member_google_calendar_id = "memid@group.calendar.google.com"
        config.public_google_calendar_id = ""
        config.save()
        client.login(username="sub2", password="pass")

        body = client.get(reverse("hub_community_calendar")).content.decode()

        assert "Subscribe to the Member calendar" in body
        assert "Subscribe to the Public calendar" not in body
