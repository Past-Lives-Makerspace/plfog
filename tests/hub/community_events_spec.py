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

from core.models import EventDelivery, SiteConfiguration
from hub.forms import CommunityEventForm
from membership.models import AdminCapability, CommunityEvent, Guild, GuildStaffMembership, Member
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


def _granted_user(username: str, capability: str) -> User:
    """A plain member's user holding one admin capability."""
    user = _user_with_role(username)
    user.member.admin_capabilities.create(capability=capability)
    return user


def _signed_in_reviewer(username: str) -> User:
    """A Calendar Administrator the resolver can reach: signed in once, with an email to deliver to."""
    user = _granted_user(username, AdminCapability.Capability.EVENTS_APPROVER)
    user.email = f"{username}@example.com"
    user.last_login = timezone.now()
    user.save(update_fields=["email", "last_login"])
    return user


def _submitted_deliveries() -> int:
    return EventDelivery.objects.filter(event_key="event.submitted", channel="in_app").count()


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

    def it_explains_a_missing_guild_without_naming_a_control_the_author_never_saw():
        # A plain member editing a staffer-typed guild meeting is shown no kind question, so
        # "Pick the guild this meeting belongs to" names something they cannot see.
        guild = GuildFactory()
        event = CommunityEventFactory(guild_meeting=True, guild=guild)
        form = CommunityEventForm(instance=event, data=_event_payload(title="Edited"), can_choose_audience=False)
        assert not form.is_valid()
        assert form.errors["guild"] == [
            "This one is a guild meeting, so it needs a guild. Pick one, or ask a lead if that looks wrong."
        ]

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
        # A local 6 PM, so the form's local wall time never lands on a DST fall back hour.
        start = timezone.localtime(timezone.now() + timedelta(days=5)).replace(
            hour=18, minute=0, second=0, microsecond=0
        )
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

    def it_parks_an_event_with_a_future_announce_time_and_says_so(client: Client):
        from django.contrib.messages import get_messages

        _user_with_role("ad_sched", fog_role=Member.FogRole.ADMIN)
        client.login(username="ad_sched", password="pass")
        start = timezone.localtime(timezone.now() + timedelta(days=30))
        announce_at = timezone.localtime(timezone.now() + timedelta(days=2))
        stamp = "%Y-%m-%dT%H:%M"
        with patch.object(CommunityEvent, "announce") as mock_announce:
            resp = client.post(
                reverse("hub_event_add"),
                data=_event_payload(
                    event_type="community",
                    title="Parked Night",
                    starts_at=start.strftime(stamp),
                    ends_at=(start + timedelta(hours=2)).strftime(stamp),
                    publish_at=announce_at.strftime(stamp),
                ),
            )
        assert resp.status_code == 302
        event = CommunityEvent.objects.get(title="Parked Night")
        assert event.moderation_state == CommunityEvent.ModerationState.SCHEDULED
        mock_announce.assert_not_called()
        assert [m.message for m in get_messages(resp.wsgi_request)] == [
            f"Event scheduled for {event.publish_at_display}."
        ]

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
def describe_granted_event_creators():
    """The "Can create events" grant opens the Calendar's composer review-first: a holder's
    event enters the review queue whatever the member-event policy says, and the author of an
    open proposal reopens it here. Admins and Calendar Administrators keep the full composer."""

    def it_opens_the_composer_without_the_announcement_fields(client: Client):
        _granted_user("creator_get", AdminCapability.Capability.EVENT_CREATOR)
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)
        client.login(username="creator_get", password="pass")
        resp = client.get(reverse("hub_event_add"))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert 'name="title"' in html
        assert 'name="publish_at"' not in html
        assert 'name="remind_7d"' not in html
        assert 'name="notify_happening_now"' not in html
        form = resp.context["form"]
        assert form.review_first is True
        assert form.submits_for_review is True
        assert form.saves_announce is False
        assert form.in_review is True
        assert "delete-event" not in html

    def it_submits_a_new_event_for_review_while_the_policy_is_disabled(client: Client):
        user = _granted_user("creator_post", AdminCapability.Capability.EVENT_CREATOR)
        _signed_in_reviewer("site_reviewer")
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)
        client.login(username="creator_post", password="pass")
        with patch.object(CommunityEvent, "announce") as mock_announce:
            resp = client.post(reverse("hub_event_add"), data=_event_payload(title="Granted Night"))
        assert resp.status_code == 302
        assert resp["Location"] == reverse("hub_community_calendar") + "?tab=events"
        event = CommunityEvent.objects.get(title="Granted Night")
        assert event.moderation_state == CommunityEvent.ModerationState.PENDING
        assert event.submitted_by == user
        assert event.created_by == user
        assert event.is_site_wide
        assert event.sync_state == CommunityEvent.SyncState.IDLE
        assert _submitted_deliveries() == 1
        mock_announce.assert_not_called()

    def it_still_reviews_first_under_an_open_policy(client: Client):
        # The grant promises a review; the OPEN policy is for members proposing without it.
        _granted_user("creator_open", AdminCapability.Capability.EVENT_CREATOR)
        _set_policy(SiteConfiguration.MemberEventPolicy.OPEN)
        client.login(username="creator_open", password="pass")
        with patch.object(CommunityEvent, "announce") as mock_announce:
            client.post(reverse("hub_event_add"), data=_event_payload(title="Still Reviewed"))
        assert CommunityEvent.objects.get(title="Still Reviewed").moderation_state == (
            CommunityEvent.ModerationState.PENDING
        )
        mock_announce.assert_not_called()

    def it_re_renders_with_errors_instead_of_submitting(client: Client):
        _granted_user("creator_bad", AdminCapability.Capability.EVENT_CREATOR)
        client.login(username="creator_bad", password="pass")
        resp = client.post(
            reverse("hub_event_add"),
            data=_event_payload(title="Backwards", starts_at="2026-07-11T20:00", ends_at="2026-07-11T18:00"),
        )
        assert resp.status_code == 200
        assert not CommunityEvent.objects.filter(title="Backwards").exists()

    def it_403s_a_plain_member_on_add_while_the_policy_is_disabled(client: Client):
        _user_with_role("plain_add")
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)
        client.login(username="plain_add", password="pass")
        assert client.get(reverse("hub_event_add")).status_code == 403
        assert client.post(reverse("hub_event_add"), data=_event_payload(title="Sneaked")).status_code == 403
        assert not CommunityEvent.objects.filter(title="Sneaked").exists()

    def it_lets_the_author_reopen_and_resubmit_their_own_sent_back_proposal(client: Client):
        user = _granted_user("creator_edit", AdminCapability.Capability.EVENT_CREATOR)
        _signed_in_reviewer("site_reviewer2")
        event = CommunityEventFactory(
            community=True,
            submitted_by=user,
            moderation_state=CommunityEvent.ModerationState.CHANGES_REQUESTED,
            review_notes="Add a room.",
            title="Needs A Room",
        )
        client.login(username="creator_edit", password="pass")
        resp = client.get(reverse("hub_event_edit", args=[event.pk]))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert resp.context["form"].review_first is True
        assert '<p class="pl-nudge__lead"><strong>A reviewer asked for changes:</strong> Add a room.</p>' in html
        assert 'name="publish_at"' not in html

        resp = client.post(reverse("hub_event_edit", args=[event.pk]), data=_event_payload(title="Has A Room"))
        assert resp.status_code == 302
        event.refresh_from_db()
        assert event.title == "Has A Room"
        assert event.moderation_state == CommunityEvent.ModerationState.PENDING
        assert event.review_notes == ""
        assert event.submitted_by == user
        assert _submitted_deliveries() == 1

    def it_lets_a_plain_member_reopen_their_own_pending_proposal_the_same_way(client: Client):
        # No grant needed: the row is theirs, so the composer opens it review-first.
        user = _user_with_role("plain_author")
        event = CommunityEventFactory(community=True, pending=True, submitted_by=user)
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)
        client.login(username="plain_author", password="pass")
        resp = client.get(reverse("hub_event_edit", args=[event.pk]))
        assert resp.status_code == 200
        assert resp.context["form"].submits_for_review is True

    def it_403s_the_author_on_someone_elses_proposal_and_on_their_own_once_published(client: Client):
        user = _granted_user("creator_other", AdminCapability.Capability.EVENT_CREATOR)
        someone_elses = CommunityEventFactory(community=True, pending=True)
        published = CommunityEventFactory(community=True, submitted_by=user, created_by=user)  # PUBLISHED
        client.login(username="creator_other", password="pass")
        assert client.get(reverse("hub_event_edit", args=[someone_elses.pk])).status_code == 403
        assert client.get(reverse("hub_event_edit", args=[published.pk])).status_code == 403
        resp = client.post(reverse("hub_event_edit", args=[published.pk]), data=_event_payload(title="Changed"))
        assert resp.status_code == 403
        published.refresh_from_db()
        assert published.title != "Changed"

    def it_lets_a_calendar_administrator_create_directly(client: Client):
        user = _granted_user("approver_add", AdminCapability.Capability.EVENTS_APPROVER)
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)
        client.login(username="approver_add", password="pass")
        resp = client.get(reverse("hub_event_add"))
        assert resp.status_code == 200
        assert resp.context["form"].review_first is False
        assert 'name="publish_at"' in resp.content.decode()
        with patch.object(CommunityEvent, "announce") as mock_announce:
            client.post(reverse("hub_event_add"), data=_event_payload(event_type="community", title="Direct Night"))
        event = CommunityEvent.objects.get(title="Direct Night")
        assert event.moderation_state == CommunityEvent.ModerationState.PUBLISHED
        assert event.created_by == user
        mock_announce.assert_called_once()

    def it_keeps_the_full_composer_for_an_admin_editing_a_pending_row(client: Client):
        _user_with_role("admin_pending", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True, pending=True)
        client.login(username="admin_pending", password="pass")
        resp = client.get(reverse("hub_event_edit", args=[event.pk]))
        assert resp.status_code == 200
        form = resp.context["form"]
        assert form.review_first is False
        assert form.in_review is True
        assert 'name="publish_at"' in resp.content.decode()
        assert reverse("hub_event_delete", args=[event.pk]) in resp.content.decode()
        with patch.object(CommunityEvent, "announce") as mock_announce:
            resp = client.post(reverse("hub_event_edit", args=[event.pk]), data=_event_payload(title="Admin Touch"))
        assert resp.status_code == 302
        event.refresh_from_db()
        assert event.title == "Admin Touch"
        assert event.moderation_state == CommunityEvent.ModerationState.PENDING
        mock_announce.assert_not_called()

    def it_keeps_a_calendar_administrator_who_proposed_in_the_full_composer(client: Client):
        # Their own pending row is theirs to publish: administering events wins over resubmitting.
        user = _granted_user("approver_author", AdminCapability.Capability.EVENTS_APPROVER)
        event = CommunityEventFactory(community=True, pending=True, submitted_by=user)
        client.login(username="approver_author", password="pass")
        assert client.get(reverse("hub_event_edit", args=[event.pk])).context["form"].review_first is False

    def it_names_the_reviewers_in_the_note_by_whether_the_event_has_a_guild(client: Client):
        user = _granted_user("creator_note", AdminCapability.Capability.EVENT_CREATOR)
        guild = GuildFactory(name="Lantern Makers")
        guild_proposal = CommunityEventFactory(guild_hosted=True, guild=guild, pending=True, submitted_by=user)
        client.login(username="creator_note", password="pass")
        note_tag = '<p class="pl-form-note hub-text-muted" id="event-review-note">'
        html = client.get(reverse("hub_event_edit", args=[guild_proposal.pk])).content.decode()
        assert "Lantern Makers lead or staff" in html.split(note_tag, 1)[1][:200]
        html = client.get(reverse("hub_event_add")).content.decode()
        assert "Calendar Administrator" in html.split(note_tag, 1)[1][:260]


@pytest.mark.django_db
def describe_a_guild_events_on_the_site_wide_composer():
    """A guild's own leadership edits its event in the guild composer, which fixes the guild:
    this composer's guild picker would let a lead move the event out of their own reach. A
    Calendar Administrator, who manages every event, gets the full composer here."""

    def it_sends_a_lead_to_the_guild_composer(client: Client):
        user = _user_with_role("lead_site")
        guild = GuildFactory(guild_lead=user.member)
        event = CommunityEventFactory(guild=guild, google_calendar_target=_MEMBER)
        client.login(username="lead_site", password="pass")
        resp = client.get(reverse("hub_event_edit", args=[event.pk]))
        assert resp.status_code == 302
        assert resp["Location"] == reverse("hub_guild_event_edit", args=[guild.pk, event.pk])

    def it_never_lets_a_crafted_post_reach_the_site_wide_save(client: Client):
        user = _user_with_role("lead_craft")
        guild = GuildFactory(guild_lead=user.member)
        event = CommunityEventFactory(guild=guild, google_calendar_target=_MEMBER, title="Guild Night")
        client.login(username="lead_craft", password="pass")
        with (
            patch.object(CommunityEvent, "push_to_google") as mock_google,
            patch.object(CommunityEvent, "push_to_discord") as mock_discord,
        ):
            resp = client.post(
                reverse("hub_event_edit", args=[event.pk]),
                data=_event_payload(
                    title="Orphaned", guild="", event_type="community", google_calendar_target="public"
                ),
            )
        assert resp.status_code == 302
        assert resp["Location"] == reverse("hub_guild_event_edit", args=[guild.pk, event.pk])
        event.refresh_from_db()
        assert event.guild == guild
        assert event.google_calendar_target == _MEMBER
        assert event.title == "Guild Night"
        mock_google.assert_not_called()
        mock_discord.assert_not_called()
        # And the event is still within the lead's reach.
        assert client.get(reverse("hub_guild_event_edit", args=[guild.pk, event.pk])).status_code == 200

    def it_lets_a_calendar_administrator_edit_a_guild_event_in_the_full_composer(client: Client):
        _granted_user("approver_guild", AdminCapability.Capability.EVENTS_APPROVER)
        event = CommunityEventFactory(guild=GuildFactory())
        client.login(username="approver_guild", password="pass")
        resp = client.get(reverse("hub_event_edit", args=[event.pk]))
        assert resp.status_code == 200
        assert resp.context["form"].review_first is False
        assert 'name="guild"' in resp.content.decode()

    def it_still_403s_a_plain_member_on_a_guild_event(client: Client):
        _user_with_role("plain_guild")
        event = CommunityEventFactory(guild=GuildFactory())
        client.login(username="plain_guild", password="pass")
        assert client.get(reverse("hub_event_edit", args=[event.pk])).status_code == 403


@pytest.mark.django_db
def describe_event_actions_for_calendar_administrators():
    """Delete and Retry sync answer to ``can_administer_events`` and land on ``next``."""

    def it_lets_a_calendar_administrator_delete_and_return_to_next(client: Client):
        _granted_user("approver_del", AdminCapability.Capability.EVENTS_APPROVER)
        event = CommunityEventFactory(community=True)
        client.login(username="approver_del", password="pass")
        resp = client.post(reverse("hub_event_delete", args=[event.pk]), {"next": "/calendar/?tab=events&page=2"})
        assert resp.status_code == 302
        assert resp["Location"] == "/calendar/?tab=events&page=2"
        assert not CommunityEvent.objects.filter(pk=event.pk).exists()

    def it_ignores_a_next_on_another_host(client: Client):
        _user_with_role("admin_del_next", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True)
        client.login(username="admin_del_next", password="pass")
        resp = client.post(reverse("hub_event_delete", args=[event.pk]), {"next": "https://evil.example/x"})
        assert resp["Location"] == reverse("hub_community_calendar") + "?tab=events"

    def it_403s_a_can_create_events_holder_on_delete(client: Client):
        _granted_user("creator_del", AdminCapability.Capability.EVENT_CREATOR)
        event = CommunityEventFactory(community=True)
        client.login(username="creator_del", password="pass")
        assert client.post(reverse("hub_event_delete", args=[event.pk])).status_code == 403
        assert CommunityEvent.objects.filter(pk=event.pk).exists()

    def it_lets_a_calendar_administrator_retry_sync_but_not_a_holder(client: Client):
        _granted_user("approver_sync", AdminCapability.Capability.EVENTS_APPROVER)
        _granted_user("creator_sync", AdminCapability.Capability.EVENT_CREATOR)
        event = CommunityEventFactory(community=True, sync_state=CommunityEvent.SyncState.FAILED)
        client.login(username="approver_sync", password="pass")
        with patch.object(CommunityEvent, "push_to_google", autospec=True) as mock_push:
            assert client.post(reverse("hub_event_retry_sync", args=[event.pk])).status_code == 302
        mock_push.assert_called_once()
        client.logout()
        client.login(username="creator_sync", password="pass")
        with patch.object(CommunityEvent, "push_to_google", autospec=True) as mock_push:
            assert client.post(reverse("hub_event_retry_sync", args=[event.pk])).status_code == 403
        mock_push.assert_not_called()


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

    def it_never_says_community_event_in_a_notification_label_or_description():
        # Every member can open the notification preferences matrix, so a retired phrase in
        # an event's label or description is a member-reachable surface like any other. Keys
        # and recipients are untouched; only the words changed.
        from core.events.registry import EVENTS

        offenders = [event.key for event in EVENTS if "community event" in f"{event.label} {event.description}".lower()]
        assert offenders == []

    def it_never_says_community_event_in_a_slash_command_description():
        # A slash command's description is what Discord shows every member in the command
        # picker, so it is as member-facing as any page. Found there after the registry sweep.
        from core.events.discord_commands import all_commands, autodiscover

        autodiscover()
        offenders = [c.name for c in all_commands() if "community event" in c.description.lower()]
        assert offenders == []

    def it_never_says_community_event_in_a_scheduled_job_description():
        # Admin-facing (the Automations tab), but the same retired phrase and the same sweep.
        from core.scheduled_jobs import SCHEDULED_JOBS

        offenders = [j.key for j in SCHEDULED_JOBS if "community event" in j.description.lower()]
        assert offenders == []

    @pytest.mark.django_db
    def it_never_says_community_event_in_the_email_catalogue():
        # The Emails tab of notification preferences renders every row's label and
        # description to the member reading it. Missed on the first sweep because the copy
        # lives in a plain dict, not in the event registry the earlier sweep walked.
        from core.events.email_catalogue import build_email_catalogue

        offenders = [
            row.key
            for _group, rows in build_email_catalogue()
            for row in rows
            if "community event" in f"{row.label} {row.description} {row.schedule_note}".lower()
        ]
        assert offenders == []

    def it_never_says_a_retired_calendar_name_in_member_facing_help():
        # The Help Center is member-reachable copy, and the page is called "Calendar" — not
        # the "community calendar". Checks the live tooltips and the seeded article intro;
        # ``RETIRED_INTRO`` is deliberately excluded because it is a frozen fingerprint used
        # to spot stale seed output, so editing it would break that detection.
        from core.help_registry import HELP_KEYS
        from membership.help_content import PAGE_INTRO

        retired = ("community event", "community calendar")
        offenders = [
            key
            for key, entry in HELP_KEYS.items()
            if any(phrase in f"{entry.get('title', '')} {entry.get('short_text', '')}".lower() for phrase in retired)
        ]
        assert offenders == []
        assert not any(phrase in PAGE_INTRO.lower() for phrase in retired)


@pytest.mark.django_db
def describe_events_tab_visibility():
    def it_shows_the_list_read_only_to_a_plain_member(client: Client):
        _user_with_role("v1")
        CommunityEventFactory(community=True, title="Open Mic Night")
        client.login(username="v1", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert resp.status_code == 200
        assert b"Open Mic Night" in resp.content
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

    def it_points_a_member_at_host_a_class_when_policy_disabled(client: Client):
        _user_with_role("hc_host")
        _set_policy(SiteConfiguration.MemberEventPolicy.DISABLED)
        client.login(username="hc_host", password="pass")
        resp = client.get(reverse("hub_community_calendar"))
        assert (
            f'href="{reverse("classes:teach_overview")}" class="hub-btn hub-btn--primary">Host a Class<'.encode()
            in resp.content
        )

    def it_renders_the_cta_exactly_once(client: Client):
        # A member with an in-flight proposal also renders an Edit link for it, so the count
        # must match the exact quoted href, never a bare substring, or it double-counts.
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
        assert "https://calendar.google.com/calendar/r?cid=memid%40group.calendar.google.com" in body
        assert "https://calendar.google.com/calendar/r?cid=pubid%40group.calendar.google.com" in body
        # Grouped by calendar app, each calendar under both.
        assert 'pl-calendar-export__heading">Apple Calendar</p>' in body
        assert 'pl-calendar-export__heading">Google Calendar</p>' in body
        assert body.count(">Member calendar</a>") == 2 and body.count(">Public calendar</a>") == 2
        assert "Download .ics (one time)" in body

    def it_hides_a_subscribe_link_when_its_calendar_is_unset(client: Client):
        _user_with_role("sub2")
        config = SiteConfiguration.load()
        config.member_google_calendar_id = "memid@group.calendar.google.com"
        config.public_google_calendar_id = ""
        config.save()
        client.login(username="sub2", password="pass")

        body = client.get(reverse("hub_community_calendar")).content.decode()

        assert body.count(">Member calendar</a>") == 2
        assert ">Public calendar</a>" not in body


def describe_the_rich_description_and_the_promo_video():
    def it_stores_editor_html_sanitised(db):
        form = CommunityEventForm(
            data=_event_payload(description="<p>Hi</p><script>alert(1)</script>", hero_crop=""),
            can_choose_audience=False,
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["description"] == "<p>Hi</p>"

    def it_keeps_plain_text_as_typed(db):
        form = CommunityEventForm(data=_event_payload(description="Bring paper <3"), can_choose_audience=False)
        assert form.is_valid(), form.errors
        assert form.cleaned_data["description"] == "Bring paper <3"

    def it_takes_a_recognised_video_link_and_refuses_anything_else(db):
        good = CommunityEventForm(
            data=_event_payload(promo_video_url="https://youtu.be/dQw4w9WgXcQ"), can_choose_audience=False
        )
        assert good.is_valid(), good.errors
        assert good.cleaned_data["promo_video_url"] == "https://youtu.be/dQw4w9WgXcQ"
        bad = CommunityEventForm(
            data=_event_payload(promo_video_url="https://example.com/v"), can_choose_audience=False
        )
        assert not bad.is_valid()
        assert "YouTube" in bad.errors["promo_video_url"][0]
        blank = CommunityEventForm(data=_event_payload(promo_video_url=""), can_choose_audience=False)
        assert blank.is_valid(), blank.errors
        assert blank.cleaned_data["promo_video_url"] == ""

    def it_saves_the_sanitised_html_through_the_composer(client: Client, db):
        _user_with_role("rich_admin", fog_role=Member.FogRole.ADMIN)
        client.login(username="rich_admin", password="pass")
        resp = client.post(
            reverse("hub_event_add"),
            data=_event_payload(
                description="<p>Bring <strong>paper</strong>.</p><script>x</script>",
                promo_video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            ),
        )
        assert resp.status_code == 302
        event = CommunityEvent.objects.get(title=_event_payload()["title"])
        assert event.description == "<p>Bring <strong>paper</strong>.</p>"
        assert event.description_text == "Bring paper."
        assert event.promo_video_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
