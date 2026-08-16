"""BDD specs for CommunityEvent CRUD: the form, the lead views (gated, guild-scoped,
cross-guild isolation), and the admin authoring endpoints + the Events tab."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client, override_settings
from django.urls import reverse

from core.models import SiteConfiguration
from hub.forms import CommunityEventForm
from membership.models import CommunityEvent, GuildStaffMembership, Member
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory

ADD_HREF = b'href="/events/add/"'
PROPOSE_HREF = b'href="/events/propose/"'
TAB_SENTINEL = b"tab === 'events'"
SYNC_COPY = b"synced to the shared Past Lives Google Calendar"


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
def describe_form():
    def it_omits_type_and_guild_for_the_lead_variant():
        form = CommunityEventForm(as_admin=False)
        assert "event_type" not in form.fields
        assert "guild" not in form.fields
        assert "recurrence" in form.fields

    def it_errors_when_end_is_not_after_start():
        form = CommunityEventForm(
            data=_event_payload(event_type="community", ends_at="2026-07-11T18:00", starts_at="2026-07-11T20:00"),
            as_admin=True,
        )
        assert not form.is_valid()
        assert "End time must be after the start." in form.errors["ends_at"]

    def it_errors_for_a_guild_meeting_without_a_guild():
        form = CommunityEventForm(data=_event_payload(event_type="guild_meeting"), as_admin=True)
        assert not form.is_valid()
        assert "guild" in form.errors

    def it_errors_for_a_site_wide_type_with_a_guild():
        guild = GuildFactory()
        form = CommunityEventForm(data=_event_payload(event_type="community", guild=str(guild.pk)), as_admin=True)
        assert not form.is_valid()
        assert "guild" in form.errors

    def it_saves_a_valid_admin_guild_meeting():
        guild = GuildFactory()
        form = CommunityEventForm(data=_event_payload(event_type="guild_meeting", guild=str(guild.pk)), as_admin=True)
        assert form.is_valid(), form.errors

    def it_defaults_the_calendar_target_to_member_when_omitted():
        # The payload has no google_calendar_target — the forgiving clean coerces it to MEMBER.
        form = CommunityEventForm(data=_event_payload(event_type="community"), as_admin=True)
        assert form.is_valid(), form.errors
        assert form.cleaned_data["google_calendar_target"] == CommunityEvent.GoogleCalendarTarget.MEMBER

    def it_saves_the_chosen_public_calendar_target():
        form = CommunityEventForm(
            data=_event_payload(event_type="community", google_calendar_target="public"), as_admin=True
        )
        assert form.is_valid(), form.errors
        assert form.save().google_calendar_target == CommunityEvent.GoogleCalendarTarget.PUBLIC

    def it_labels_the_video_url_field_video_link():
        form = CommunityEventForm(as_admin=True)
        assert form.fields["video_url"].label == "Video link"

    def it_saves_a_valid_video_url():
        form = CommunityEventForm(
            data=_event_payload(event_type="community", video_url="https://meet.google.com/abc-defg-hij"),
            as_admin=True,
        )
        assert form.is_valid(), form.errors
        assert form.save().video_url == "https://meet.google.com/abc-defg-hij"

    def it_errors_on_a_non_url_video_link():
        form = CommunityEventForm(data=_event_payload(event_type="community", video_url="not a url"), as_admin=True)
        assert not form.is_valid()
        assert "video_url" in form.errors

    def it_rejects_a_javascript_scheme_video_url():
        # Security: the URLField alone accepts any scheme Django's URL regex matches, which
        # includes javascript:/data: — those would render straight into an href (event
        # detail page, calendar item, home upcoming widget) as an XSS vector. The model
        # field restricts to http/https via URLValidator(schemes=...), so a scheme-smuggled
        # value fails form validation just like a malformed URL would.
        form = CommunityEventForm(
            data=_event_payload(event_type="community", video_url="javascript:alert(1)"), as_admin=True
        )
        assert not form.is_valid()
        assert "video_url" in form.errors

    def it_still_accepts_a_plain_http_video_url():
        form = CommunityEventForm(
            data=_event_payload(event_type="community", video_url="http://meet.example.com/x"), as_admin=True
        )
        assert form.is_valid(), form.errors

    def it_allows_a_blank_video_url():
        form = CommunityEventForm(data=_event_payload(event_type="community"), as_admin=True)
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
            resp = client.post(reverse("hub_guild_event_add", args=[guild.pk]), data=_event_payload())
        assert resp.status_code == 302
        event = CommunityEvent.objects.get(guild=guild)
        assert event.event_type == CommunityEvent.EventType.GUILD_MEETING
        assert event.created_by == user
        mock_announce.assert_called_once()

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
