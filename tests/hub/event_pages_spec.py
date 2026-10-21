"""BDD specs for the public event pages: the detail page (public, published-only, states),
the per-event .ics (public add-to-calendar), the editor-gated QR download, and the themed
404 that an anonymous scanner hits on a stale/withdrawn/pending pk. Then #831 part 4: the
guest form and its POST (the honeypot, the rate limit, the repeat email, the ended event, the
date, a signed-in poster, a member's email), a member's party size, and the page folding guests into a count."""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, quote_plus, urlsplit

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.db.models.signals import post_save
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from factory.django import mute_signals

from core.models import SiteConfiguration

from hub import views
from hub.view_as import ROLE_ADMIN, ROLE_MEMBER, ViewAs
from membership.models import CommunityEvent, EventRSVP, Member
from tests.membership.factories import (
    CommunityEventFactory,
    EventRSVPFactory,
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
)

#: The audience badge's own markup on the event page — assert against this, never bare copy.
_BADGE = '<span class="pl-event-page__type">'

pytestmark = pytest.mark.django_db


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def describe_event_detail():
    def it_is_reachable_by_an_anonymous_visitor(client: Client):
        event = CommunityEventFactory(community=True, title="Open House")
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert resp.status_code == 200
        assert b"Open House" in resp.content
        # The add-to-calendar CTA works logged-out (the whole point of a scannable QR).
        assert reverse("hub_event_ics", args=[event.pk]).encode() in resp.content
        # An anon visitor gets the classes link, not the member-only calendar link.
        assert b">View the Calendar</a>" not in resp.content

    def it_shows_the_member_calendar_link_to_a_logged_in_member(client: Client):
        _user_with_role("evt_member_link")
        event = CommunityEventFactory(community=True)
        client.login(username="evt_member_link", password="pass")
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert b">View the Calendar</a>" in resp.content

    def it_404s_a_pending_proposal(client: Client):
        event = CommunityEventFactory(pending=True)
        assert client.get(reverse("hub_event_detail", args=[event.pk])).status_code == 404

    def it_404s_a_declined_proposal(client: Client):
        event = CommunityEventFactory(declined=True)
        assert client.get(reverse("hub_event_detail", args=[event.pk])).status_code == 404

    def it_404s_a_changes_requested_proposal(client: Client):
        event = CommunityEventFactory(pending=True)
        event.moderation_state = CommunityEvent.ModerationState.CHANGES_REQUESTED
        event.save(update_fields=["moderation_state"])
        assert client.get(reverse("hub_event_detail", args=[event.pk])).status_code == 404

    def it_404s_an_unknown_pk(client: Client):
        assert client.get(reverse("hub_event_detail", args=[999999])).status_code == 404

    def it_renders_a_guild_event_with_a_link_to_its_guild(client: Client):
        guild = GuildFactory(name="Metal Guild")
        event = CommunityEventFactory(guild=guild, title="Forge Night")
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert resp.status_code == 200
        assert b"Metal Guild" in resp.content
        assert reverse("hub_guild_detail", args=[guild.slug]).encode() in resp.content

    def it_badges_a_public_event_by_its_audience(client: Client):
        # Anchored on the badge's own markup: the changelog renders into every page, so a
        # bare copy assertion could pass (or fail) on a release note instead (STANDARDS §8).
        event = CommunityEventFactory(
            community=True, title="Potluck", google_calendar_target=CommunityEvent.GoogleCalendarTarget.PUBLIC
        )
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert resp.status_code == 200
        assert _BADGE + "Public event<" in resp.content.decode()

    def it_badges_a_member_event_by_its_audience(client: Client):
        event = CommunityEventFactory(
            community=True,
            title="Members Night",
            google_calendar_target=CommunityEvent.GoogleCalendarTarget.MEMBER,
        )
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert _BADGE + "Member event<" in resp.content.decode()

    def it_badges_a_guild_event_alongside_its_guild_pill(client: Client):
        guild = GuildFactory(name="Metal Guild")
        event = CommunityEventFactory(
            guild_hosted=True, guild=guild, google_calendar_target=CommunityEvent.GoogleCalendarTarget.PUBLIC
        )
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        html = resp.content.decode()
        assert b"Metal Guild" in resp.content
        assert _BADGE + "Public event<" in html

    def it_never_badges_the_stored_type_on_the_event_page(client: Client):
        event = CommunityEventFactory(community=True, title="Potluck")
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert _BADGE + f"{event.get_event_type_display()}<" not in html

    def it_keeps_the_guild_lead_meetings_own_name_on_the_event_page(client: Client):
        # A lead meeting is a recognisable thing and keeps its name rather than badging
        # "Member event" like everything else on the members calendar.
        event = CommunityEventFactory(
            lead_meeting=True,
            title="September Leads",
            google_calendar_target=CommunityEvent.GoogleCalendarTarget.MEMBER,
        )
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert _BADGE + "Guild Lead Meeting<" in html

    def it_shows_the_past_note_for_an_ended_non_recurring_event(client: Client):
        start = timezone.now() - timedelta(days=2)
        event = CommunityEventFactory(community=True, starts_at=start, ends_at=start + timedelta(hours=2))
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert b"already taken place" in resp.content

    def it_hides_the_past_note_for_a_recurring_series(client: Client):
        start = timezone.now() - timedelta(days=2)
        event = CommunityEventFactory(
            community=True,
            recurrence=CommunityEvent.Recurrence.MONTHLY,
            starts_at=start,
            ends_at=start + timedelta(hours=2),
        )
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert b"already taken place" not in resp.content

    def it_omits_the_description_section_when_blank(client: Client):
        event = CommunityEventFactory(community=True, location="", description="")
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert resp.status_code == 200
        assert b"pl-event-page__description" not in resp.content

    def it_shows_location_and_description_when_set(client: Client):
        event = CommunityEventFactory(community=True, location="Main Studio", description="Come by.")
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert b"Main Studio" in resp.content
        assert b"Come by." in resp.content
        assert b"pl-event-page__description" in resp.content

    def it_shows_a_join_online_primary_cta_when_video_url_is_set(client: Client):
        event = CommunityEventFactory(community=True, video_url="https://meet.google.com/abc-defg-hij")
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        content = resp.content.decode()
        assert 'href="https://meet.google.com/abc-defg-hij"' in content
        assert "Join online" in content
        # "Join online" is the primary CTA; "Add to calendar" is demoted to a ghost button.
        assert (
            'class="pl-event-page__btn" href="https://meet.google.com/abc-defg-hij"'
            ' target="_blank" rel="noopener noreferrer" data-join-online>Join online</a>' in content
        )
        assert 'class="pl-event-page__btn pl-event-page__btn--ghost" @click' in content

    def it_omits_join_online_and_keeps_add_to_calendar_primary_when_video_url_is_blank(client: Client):
        event = CommunityEventFactory(community=True, video_url="")
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        content = resp.content.decode()
        # Scope to the exact CTA anchor, not a bare "Join online" substring — the site-wide
        # changelog widget (rendered in every hub page's context) can legitimately mention
        # "Join online" in this feature's own release notes.
        assert "data-join-online>Join online</a>" not in content
        assert 'class="pl-event-page__btn" @click' in content

    def it_shows_the_edit_button_to_a_guild_lead(client: Client):
        user = _user_with_role("evt_editor")
        guild = GuildFactory(guild_lead=user.member)
        event = CommunityEventFactory(guild=guild)
        client.login(username="evt_editor", password="pass")
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert b"Edit event" in resp.content
        assert reverse("hub_guild_event_edit", args=[guild.pk, event.pk]).encode() in resp.content

    def it_links_a_site_wide_edit_to_the_admin_authoring_view(client: Client):
        _user_with_role("evt_admin_edit", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True)
        client.login(username="evt_admin_edit", password="pass")
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert b"Edit event" in resp.content
        assert reverse("hub_event_edit", args=[event.pk]).encode() in resp.content

    def it_links_a_calendar_administrator_to_the_calendars_composer_for_a_guild_event(client: Client):
        # They may edit every event, but the guild's own composer admits only its lead or staff.
        from membership.models import AdminCapability

        user = _user_with_role("evt_cal_admin")
        user.member.admin_capabilities.create(capability=AdminCapability.Capability.EVENTS_APPROVER)
        guild = GuildFactory()
        event = CommunityEventFactory(guild=guild)
        client.login(username="evt_cal_admin", password="pass")
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert b"Edit event" in resp.content
        assert reverse("hub_event_edit", args=[event.pk]).encode() in resp.content
        assert reverse("hub_guild_event_edit", args=[guild.pk, event.pk]).encode() not in resp.content

    def it_hides_the_edit_button_from_a_non_editor(client: Client):
        _user_with_role("evt_viewer")
        event = CommunityEventFactory(community=True)
        client.login(username="evt_viewer", password="pass")
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert b"Edit event" not in resp.content

    def it_hides_the_editor_affordance_on_a_non_member_surface(db):
        # Editor affordances never render off the member surface (matches guild_detail).
        admin = _user_with_role("evt_admin_surface", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True)
        request = RequestFactory().get(f"/events/{event.pk}/")
        request.user = admin
        request.view_as = ViewAs(actual=frozenset({ROLE_ADMIN, ROLE_MEMBER}), picked=None)
        request.surface = "guilds"
        with patch("hub.views.render") as mock_render:
            views.event_detail(request, event.pk)
        assert mock_render.call_args.args[2]["can_edit"] is False

    def it_allows_the_editor_affordance_on_the_member_surface(db):
        admin = _user_with_role("evt_admin_surface2", fog_role=Member.FogRole.ADMIN)
        event = CommunityEventFactory(community=True)
        request = RequestFactory().get(f"/events/{event.pk}/")
        request.user = admin
        request.view_as = ViewAs(actual=frozenset({ROLE_ADMIN, ROLE_MEMBER}), picked=None)
        request.surface = "members"
        with patch("hub.views.render") as mock_render:
            views.event_detail(request, event.pk)
        assert mock_render.call_args.args[2]["can_edit"] is True


def describe_event_detail_404_template():
    @override_settings(DEBUG=False)
    def it_renders_the_themed_404_for_an_anonymous_visitor(client: Client):
        resp = client.get(reverse("hub_event_detail", args=[999999]))
        assert resp.status_code == 404
        body = resp.content.decode()
        assert "We couldn't find that page." in body
        assert reverse("classes:public_list") in body  # anon-safe next step

    @override_settings(DEBUG=False)
    def it_renders_the_themed_404_for_a_logged_in_member_without_error(client: Client):
        _user_with_role("evt_404_member")
        client.login(username="evt_404_member", password="pass")
        resp = client.get(reverse("hub_event_detail", args=[999999]))
        assert resp.status_code == 404
        assert "We couldn't find that page." in resp.content.decode()

    @override_settings(DEBUG=False)
    def it_does_not_leak_a_pending_events_title(client: Client):
        event = CommunityEventFactory(pending=True, title="Secret Proposal")
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert resp.status_code == 404
        assert b"Secret Proposal" not in resp.content


def describe_event_ics():
    def it_serves_the_single_event_ics_to_an_anonymous_visitor(client: Client):
        event = CommunityEventFactory(community=True, title="Potluck", location="Common Area")
        resp = client.get(reverse("hub_event_ics", args=[event.pk]))
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/calendar")
        assert resp["Content-Disposition"] == f'attachment; filename="event-{event.pk}.ics"'
        assert resp.content.decode() == event.ics_document(event.starts_at)

    def it_404s_the_ics_for_a_non_published_event(client: Client):
        event = CommunityEventFactory(pending=True)
        assert client.get(reverse("hub_event_ics", args=[event.pk])).status_code == 404


def describe_event_qr():
    def it_serves_svg_and_png_to_an_editor(client: Client):
        user = _user_with_role("evt_qr_lead")
        guild = GuildFactory(guild_lead=user.member)
        event = CommunityEventFactory(guild=guild)
        client.login(username="evt_qr_lead", password="pass")

        svg = client.get(reverse("hub_event_qr", args=[event.pk, "svg"]))
        assert svg.status_code == 200
        assert svg["Content-Type"] == "image/svg+xml"
        assert svg["Content-Disposition"] == f'attachment; filename="event-{event.pk}-qr.svg"'
        assert b"<svg" in svg.content
        assert b"viewBox" in svg.content

        png = client.get(reverse("hub_event_qr", args=[event.pk, "png"]))
        assert png.status_code == 200
        assert png["Content-Type"] == "image/png"
        assert png.content.startswith(b"\x89PNG")

    def it_forbids_a_non_editor_member(client: Client):
        _user_with_role("evt_qr_plain")
        event = CommunityEventFactory(community=True)
        client.login(username="evt_qr_plain", password="pass")
        assert client.get(reverse("hub_event_qr", args=[event.pk, "svg"])).status_code == 403

    def it_forbids_an_anonymous_request(client: Client):
        # The download is an editor convenience; the public artifact is the page itself.
        event = CommunityEventFactory(community=True)
        assert client.get(reverse("hub_event_qr", args=[event.pk, "png"])).status_code == 403

    def it_404s_an_unknown_format(client: Client):
        user = _user_with_role("evt_qr_fmt")
        guild = GuildFactory(guild_lead=user.member)
        event = CommunityEventFactory(guild=guild)
        client.login(username="evt_qr_fmt", password="pass")
        assert client.get(reverse("hub_event_qr", args=[event.pk, "gif"])).status_code == 404

    def it_404s_for_a_non_published_event(client: Client):
        user = _user_with_role("evt_qr_unpub", fog_role=Member.FogRole.ADMIN)  # noqa: F841
        event = CommunityEventFactory(pending=True)
        client.login(username="evt_qr_unpub", password="pass")
        assert client.get(reverse("hub_event_qr", args=[event.pk, "svg"])).status_code == 404


def describe_whos_coming():
    def it_shows_the_count_and_names_to_a_signed_in_member(client: Client):
        _user_with_role("evt_rsvp_viewer")
        event = CommunityEventFactory(community=True)
        EventRSVPFactory(event=event, member=MemberFactory(full_legal_name="Zzytrix Quon"))
        client.login(username="evt_rsvp_viewer", password="pass")
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert b'data-people-count="1">1<' in resp.content
        assert b"Zzytrix Quon" in resp.content

    def it_shows_the_count_but_not_names_to_an_anonymous_visitor(client: Client):
        event = CommunityEventFactory(community=True)
        EventRSVPFactory(event=event, member=MemberFactory(full_legal_name="Zzytrix Quon"))
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert b'data-people-count="1">1<' in resp.content  # the count is public
        assert b"Zzytrix Quon" not in resp.content  # names are not

    def it_leaves_a_hidden_member_out_of_the_names_and_the_count(client: Client):
        _user_with_role("evt_rsvp_hidden_viewer")
        event = CommunityEventFactory(community=True)
        EventRSVPFactory(event=event, member=MemberFactory(full_legal_name="Zzytrix Quon"))
        EventRSVPFactory(event=event, member=MemberFactory(full_legal_name="Qwyll Reviewbot", hide_from_directory=True))
        client.login(username="evt_rsvp_hidden_viewer", password="pass")
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert b'data-people-count="1">1<' in resp.content
        assert b"Zzytrix Quon" in resp.content
        assert b"Qwyll Reviewbot" not in resp.content

    def it_still_shows_a_hidden_member_their_own_rsvp(client: Client):
        user = _user_with_role("evt_rsvp_hidden_self")
        Member.objects.filter(user=user).update(full_legal_name="Qwyll Reviewbot", hide_from_directory=True)
        event = CommunityEventFactory(community=True)
        EventRSVPFactory(event=event, member=user.member)
        client.login(username="evt_rsvp_hidden_self", password="pass")
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert b'data-people-count="1">1<' in resp.content
        assert b"Qwyll Reviewbot" in resp.content
        assert resp.context["viewer_rsvped"] is True

    def it_leaves_a_hidden_member_out_of_the_anonymous_count(client: Client):
        event = CommunityEventFactory(community=True)
        EventRSVPFactory(event=event, member=MemberFactory(hide_from_directory=True))
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert b'data-people-count="0">0<' in resp.content

    def it_offers_the_rsvp_button_to_a_signed_in_member(client: Client):
        _user_with_role("evt_rsvp_btn")
        event = CommunityEventFactory(community=True)
        client.login(username="evt_rsvp_btn", password="pass")
        resp = client.get(reverse("hub_event_detail", args=[event.pk]))
        assert reverse("hub_event_rsvp", args=[event.pk]).encode() in resp.content


def describe_event_rsvp_post():
    def it_toggles_the_rsvp_and_refreshes_discord(client: Client):
        user = _user_with_role("evt_rsvp_post")
        event = CommunityEventFactory(community=True)
        client.login(username="evt_rsvp_post", password="pass")
        with patch.object(CommunityEvent, "refresh_discord_announcement") as refresh:
            resp = client.post(reverse("hub_event_rsvp", args=[event.pk]))
        assert resp.status_code == 302
        assert EventRSVP.objects.filter(event=event, member=user.member).exists()
        assert refresh.called
        # A second POST takes it back.
        with patch.object(CommunityEvent, "refresh_discord_announcement"):
            client.post(reverse("hub_event_rsvp", args=[event.pk]))
        assert not EventRSVP.objects.filter(event=event, member=user.member).exists()

    @respx.mock
    def it_never_surfaces_a_discord_error_to_the_member(client: Client, settings):
        settings.DISCORD_BOT_TOKEN = "bot"
        settings.MEMBER_BASE_URL = "https://members.example"
        respx.patch("https://discord.com/api/v10/channels/chan/messages/msg").mock(
            return_value=httpx.Response(500, text="boom")
        )
        user = _user_with_role("evt_rsvp_err")
        event = CommunityEventFactory(
            community=True, discord_announce_channel_id="chan", discord_announce_message_id="msg"
        )
        client.login(username="evt_rsvp_err", password="pass")
        resp = client.post(reverse("hub_event_rsvp", args=[event.pk]))
        assert resp.status_code == 302  # a Discord hiccup never 500s the RSVP
        assert EventRSVP.objects.filter(event=event, member=user.member).exists()

    def it_refuses_an_ended_one_off(client: Client):
        _user_with_role("evt_rsvp_ended")
        past = timezone.now() - timedelta(days=2)
        event = CommunityEventFactory(community=True, starts_at=past, ends_at=past + timedelta(hours=1))
        client.login(username="evt_rsvp_ended", password="pass")
        resp = client.post(reverse("hub_event_rsvp", args=[event.pk]))
        assert resp.status_code == 302
        assert not EventRSVP.objects.filter(event=event).exists()

    def it_turns_away_a_userless_account_without_a_crash(client: Client):
        MembershipPlanFactory()
        with mute_signals(post_save):
            User.objects.create_user(username="evt_rsvp_nomember", password="pass")
        event = CommunityEventFactory(community=True)
        client.login(username="evt_rsvp_nomember", password="pass")
        resp = client.post(reverse("hub_event_rsvp", args=[event.pk]))
        assert resp.status_code == 302
        assert not EventRSVP.objects.filter(event=event).exists()

    def it_404s_an_unpublished_event(client: Client):
        _user_with_role("evt_rsvp_unpub")
        event = CommunityEventFactory(pending=True)
        client.login(username="evt_rsvp_unpub", password="pass")
        assert client.post(reverse("hub_event_rsvp", args=[event.pk])).status_code == 404

    def it_requires_login(client: Client):
        event = CommunityEventFactory(community=True)
        resp = client.post(reverse("hub_event_rsvp", args=[event.pk]))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.headers["Location"] or "login" in resp.headers["Location"]


def describe_event_page_add_to_calendar_menu():
    """Add to calendar puts this one event in the viewer's calendar, never the whole calendar."""

    # Assert on markup, never bare copy: the site-wide changelog widget on every hub page can
    # legitimately say "Subscribe" or "Google Calendar" in a release note.
    GOOGLE_ITEM = 'class="pl-calendar-export__item" target="_blank" rel="noopener">Google Calendar</a>'
    ICS_ITEM = 'hx-boost="false" data-pl-download>Apple Calendar or Outlook (.ics)</a>'
    SUBSCRIBE_NOTE = "Subscribe once and your calendar app stays up to date."

    def _configure_calendars() -> None:
        config = SiteConfiguration.load()
        config.member_google_calendar_id = "memid@group.calendar.google.com"
        config.public_google_calendar_id = "pubid@group.calendar.google.com"
        config.save()

    def _body(client: Client, event: CommunityEvent) -> str:
        return client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()

    def it_offers_a_signed_in_member_this_one_event_and_no_subscription(client: Client):
        _configure_calendars()
        _user_with_role("add_member")
        client.login(username="add_member", password="pass")
        event = CommunityEventFactory(community=True, title="Potluck")
        body = _body(client, event)
        assert GOOGLE_ITEM in body and ICS_ITEM in body
        assert "https://calendar.google.com/calendar/render?action=TEMPLATE&amp;text=Potluck" in body
        assert f'href="{reverse("hub_event_ics", args=[event.pk])}"' in body
        assert "webcal://" not in body and "calendar/r?cid=" not in body
        assert SUBSCRIBE_NOTE not in body
        assert reverse("hub_calendar_export_ics") not in body

    def it_offers_an_anonymous_scanner_the_same_menu(client: Client):
        _configure_calendars()
        event = CommunityEventFactory(community=True, title="Potluck")
        body = _body(client, event)
        assert GOOGLE_ITEM in body and ICS_ITEM in body
        assert "webcal://" not in body

    def it_links_the_google_entry_back_to_the_event_page(client: Client):
        event = CommunityEventFactory(community=True)
        page = "http://testserver" + reverse("hub_event_detail", args=[event.pk])
        assert f"details={quote_plus(page)}" in _body(client, event)


def describe_event_page_on_one_date():
    """A series' page and its Add to calendar speak about one date: the one the link names
    (the calendar links each date with ``?date=``), or else the next one."""

    def _weekly() -> CommunityEvent:
        # Every week at 6 PM, since five weeks ago.
        anchor = timezone.localtime().replace(hour=18, minute=0, second=0, microsecond=0) - timedelta(weeks=5)
        return CommunityEventFactory(
            community=True,
            recurrence=CommunityEvent.Recurrence.WEEKLY,
            starts_at=anchor,
            ends_at=anchor + timedelta(hours=2),
        )

    def _later_date(event: CommunityEvent) -> datetime:
        """The series' date two to three weeks out: not its first, and not its next."""
        today = timezone.localdate()
        return event.occurrences_in(today + timedelta(days=14), today + timedelta(days=20))[0]

    def _page(client: Client, event: CommunityEvent, query: str = "") -> str:
        return client.get(reverse("hub_event_detail", args=[event.pk]) + query).content.decode()

    def _google_params(body: str) -> dict[str, str]:
        match = re.search(r'href="(https://calendar\.google\.com/calendar/render\?[^"]+)"', body)
        assert match is not None
        query = urlsplit(html.unescape(match.group(1))).query
        return {key: values[0] for key, values in parse_qs(query).items()}

    def it_shows_the_date_the_link_names(client: Client):
        event = _weekly()
        later = _later_date(event)
        body = _page(client, event, f"?date={timezone.localdate(later).isoformat()}")
        assert f"<span>{event.when_display_for(later)}</span>" in body
        assert event.when_display_for(event.starts_at) not in body

    def it_shows_the_next_date_when_the_link_names_none(client: Client):
        event = _weekly()
        assert f"<span>{event.when_display_for(event.next_occurrence_start())}</span>" in _page(client, event)

    def it_shows_the_next_date_for_a_date_the_series_does_not_meet(client: Client):
        event = _weekly()
        off_day = timezone.localdate(_later_date(event)) + timedelta(days=1)
        body = _page(client, event, f"?date={off_day.isoformat()}")
        assert f"<span>{event.when_display_for(event.next_occurrence_start())}</span>" in body

    def it_shows_the_next_date_for_a_date_that_is_not_one(client: Client):
        event = _weekly()
        body = _page(client, event, "?date=next-tuesday")
        assert f"<span>{event.when_display_for(event.next_occurrence_start())}</span>" in body

    def it_shows_the_next_date_for_a_date_too_far_away_to_look_up(client: Client):
        event = _weekly()
        resp = client.get(reverse("hub_event_detail", args=[event.pk]) + "?date=9999-12-31")
        assert resp.status_code == 200
        assert f"<span>{event.when_display_for(event.next_occurrence_start())}</span>" in resp.content.decode()
        assert client.get(reverse("hub_event_ics", args=[event.pk]) + "?date=9999-12-31").status_code == 200

    def it_sends_an_rsvp_back_to_the_date_shown(client: Client):
        _user_with_role("rsvp_on_date")
        client.login(username="rsvp_on_date", password="pass")
        event = _weekly()
        query = f"?date={timezone.localdate(_later_date(event)).isoformat()}"
        rsvp_url = reverse("hub_event_rsvp", args=[event.pk]) + query
        assert f'action="{rsvp_url}"' in _page(client, event, query)
        with patch.object(CommunityEvent, "refresh_discord_announcement"):
            resp = client.post(rsvp_url)
        assert resp["Location"] == reverse("hub_event_detail", args=[event.pk]) + query

    def it_adds_the_series_to_google_from_the_date_shown(client: Client):
        event = _weekly()
        later = _later_date(event)
        day = timezone.localdate(later).isoformat()
        params = _google_params(_page(client, event, f"?date={day}"))
        assert params["dates"].startswith(f"{day.replace('-', '')}T180000/")
        assert params["recur"] == f"RRULE:{event.ical_rrule()}"
        # Every date of the series in Google carries this link, so it opens on the next date.
        assert params["details"].endswith("http://testserver" + reverse("hub_event_detail", args=[event.pk]))

    def it_downloads_the_ics_from_the_date_shown(client: Client):
        event = _weekly()
        later = _later_date(event)
        query = f"?date={timezone.localdate(later).isoformat()}"
        ics_url = reverse("hub_event_ics", args=[event.pk]) + query
        assert f'href="{ics_url}"' in _page(client, event, query)
        assert client.get(ics_url).content.decode() == event.ics_document(later)


# --- Formatted text, the gallery and the promo video (#831, part 3) ---------------------------


def describe_the_description():
    def it_renders_editor_html_sanitised(client: Client):
        event = CommunityEventFactory(
            community=True, description="<p>Bring <strong>paper</strong>.</p><script>alert(1)</script><p>Second</p>"
        )
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert (
            '<div class="pl-event-page__description pl-rich-body"><p>Bring <strong>paper</strong>.</p><p>Second</p></div>'
            in html
        )
        assert "<script>alert(1)</script>" not in html

    def it_renders_legacy_plain_text_as_paragraphs(client: Client):
        event = CommunityEventFactory(community=True, description="Line one\nLine two\n\nPara two <b>not bold</b>")
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert "<p>Line one<br>Line two</p><p>Para two &lt;b&gt;not bold&lt;/b&gt;</p>" in html


def describe_the_gallery():
    def it_renders_the_class_gallery_component_over_the_events_rows(client: Client):
        from tests.membership.factories import EventImageFactory

        event = CommunityEventFactory(community=True, title="Zine Night")
        cover = EventImageFactory(event=event, sort_order=0, alt_text="Loom")
        EventImageFactory(event=event, sort_order=1, focus_x=20, focus_y=80)
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert "data-event-gallery" in html
        assert 'x-data="clsGallery(2)"' in html
        assert f'data-slide-src="{cover.image.url}"' in html
        assert 'aria-label="Event photos"' in html
        assert 'alt="Zine Night" style="object-position: 20% 80%"' in html

    def it_renders_nothing_without_a_gallery(client: Client):
        event = CommunityEventFactory(community=True)
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert "data-event-gallery" not in html
        assert "cls-gallery" not in html


def describe_the_promo_video():
    def it_embeds_a_youtube_link(client: Client):
        event = CommunityEventFactory(community=True, promo_video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert "data-event-video" in html
        assert 'src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"' in html
        assert 'class="pl-guild-video"' in html

    def it_links_an_instagram_post_as_a_card(client: Client):
        event = CommunityEventFactory(community=True, promo_video_url="https://www.instagram.com/reel/C1a2b3c4d5e/")
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert "data-event-video" in html
        assert 'class="pl-video-card" href="https://www.instagram.com/reel/C1a2b3c4d5e/"' in html

    def it_renders_nothing_without_one(client: Client):
        event = CommunityEventFactory(community=True, promo_video_url="")
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert "data-event-video" not in html

    def it_keeps_join_online_for_the_video_link(client: Client):
        event = CommunityEventFactory(community=True, video_url="https://meet.google.com/abc-defg-hij")
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert 'href="https://meet.google.com/abc-defg-hij"' in html
        assert "data-event-video" not in html


def describe_the_composer_content_fields():
    def _admin(client: Client, username: str) -> None:
        _user_with_role(username, fog_role=Member.FogRole.ADMIN)
        client.login(username=username, password="pass")

    def it_renders_the_rich_editor_the_hero_field_and_the_promo_video(client: Client):
        _admin(client, "composer_admin1")
        html = client.get(reverse("hub_event_add")).content.decode()
        assert "rich-editor-init.js" in html
        assert 'data-rte-for="id_description"' in html
        assert 'class="pl-rte-source"' in html
        assert "data-hero-image-field" in html
        assert "data-hero-crop-shape" in html
        assert 'name="hero_crop"' in html
        assert "hero_cropper.js" in html
        assert 'name="promo_video_url"' in html
        assert 'name="photo"' in html

    def it_opens_a_saved_events_photo_in_the_cropper_with_delete(client: Client):
        from django.core.files.base import ContentFile

        from tests.membership.factories import tiny_png_bytes

        _admin(client, "composer_admin2")
        event = CommunityEventFactory(community=True)
        event.photo.save("stored.png", ContentFile(tiny_png_bytes()), save=True)
        html = client.get(reverse("hub_event_edit", args=[event.pk])).content.decode()
        assert f'src="{event.photo.url}"' in html
        assert "data-hero-cropper-preview" in html
        assert f'data-upload-url="{reverse("hub_event_hero_upload", args=[event.pk])}"' in html
        assert "data-hero-delete" in html
        assert f'action="{reverse("hub_event_photo_delete", args=[event.pk])}"' in html
        # A saved event uploads straight to the route, so the form carries no photo input.
        assert 'name="photo"' not in html

    def it_hides_the_shape_picker_until_there_is_a_photo(client: Client):
        _admin(client, "composer_admin3")
        event = CommunityEventFactory(community=True)
        html = client.get(reverse("hub_event_edit", args=[event.pk])).content.decode()
        assert "data-hero-crop-shape" in html
        assert 'data-hero-crop-shape aria-describedby="hero-crop-shape-hint" hidden' in html
        assert "data-hero-delete" not in html

    def it_renders_the_editor_on_the_propose_page(client: Client):
        config = SiteConfiguration.load()
        config.member_event_policy = SiteConfiguration.MemberEventPolicy.APPROVAL
        config.save()
        _user_with_role("composer_member")
        client.login(username="composer_member", password="pass")
        html = client.get(reverse("hub_propose_event")).content.decode()
        assert 'data-rte-for="id_description"' in html
        assert "data-hero-image-field" in html
        assert 'name="promo_video_url"' in html


# --- Guest RSVPs and party sizes (#831, part 4) ---------------------------------------------


_GUEST_FORM = "data-guest-rsvp-form"
_MEMBER_FORM = "data-member-rsvp-form"
_GOING = "data-rsvp-going"
_LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "guest-rsvp-limit-spec",
    }
}


def _guest_post(client: Client, event: CommunityEvent, query: str = "", **overrides: str):
    """One Count me in POST with a good form, the Discord refresh stubbed; ``overrides`` edit the fields."""
    data = {"name": "Lee Organiser", "email": "Lee@Example.com", "phone": "", "party_size": "2", "website": ""}
    data.update(overrides)
    with patch.object(CommunityEvent, "refresh_discord_announcement"):
        return client.post(reverse("hub_event_rsvp_guest", args=[event.pk]) + query, data)


def _page_url(event: CommunityEvent, query: str = "") -> str:
    return reverse("hub_event_detail", args=[event.pk]) + query


def describe_the_guest_form():
    @pytest.fixture(autouse=True)
    def _fresh_counters():
        # The guest POST counts every attempt from the test client's one address against the
        # shared test cache, so each scenario starts with an empty allowance.
        cache.clear()

    def it_renders_for_an_anonymous_visitor_with_the_honeypot(client: Client):
        event = CommunityEventFactory(community=True)
        body = client.get(_page_url(event)).content.decode()
        assert _GUEST_FORM in body
        assert f'action="{reverse("hub_event_rsvp_guest", args=[event.pk])}"' in body
        assert 'name="name"' in body and 'name="email"' in body and 'name="phone"' in body
        assert 'name="party_size"' in body and 'name="wants_updates"' in body
        assert '<div class="pl-honeypot" aria-hidden="true">' in body
        assert 'name="website"' in body and 'tabindex="-1"' in body
        assert "data-rsvp-count-me-in" in body
        assert _MEMBER_FORM not in body and _GOING not in body

    def it_is_not_offered_to_a_signed_in_member(client: Client):
        _user_with_role("guest_form_member")
        client.login(username="guest_form_member", password="pass")
        body = client.get(_page_url(CommunityEventFactory(community=True))).content.decode()
        assert _GUEST_FORM not in body and _MEMBER_FORM in body

    def it_hides_once_the_event_has_ended(client: Client):
        past = timezone.now() - timedelta(days=2)
        event = CommunityEventFactory(community=True, starts_at=past, ends_at=past + timedelta(hours=1))
        body = client.get(_page_url(event)).content.decode()
        assert _GUEST_FORM not in body and _GOING not in body

    def it_says_one_rsvp_covers_a_series(client: Client):
        event = CommunityEventFactory(community=True, recurrence=CommunityEvent.Recurrence.WEEKLY)
        body = client.get(_page_url(event)).content.decode()
        assert '<p class="pl-event-page__note" data-rsvp-series-note>One RSVP is for the whole series.</p>' in body
        assert _GUEST_FORM in body
        oneoff = client.get(_page_url(CommunityEventFactory(community=True))).content.decode()
        assert "data-rsvp-series-note" not in oneoff

    def it_creates_a_site_row_and_lands_on_the_going_state(client: Client):
        event = CommunityEventFactory(community=True)
        with patch.object(CommunityEvent, "refresh_discord_announcement") as refresh:
            resp = client.post(
                reverse("hub_event_rsvp_guest", args=[event.pk]),
                {"name": "Lee Organiser", "email": "Lee@Example.com", "phone": "503", "party_size": "2", "website": ""},
            )
        assert resp.status_code == 302
        assert resp["Location"] == _page_url(event, "?going=1")
        assert refresh.called
        rsvp = event.rsvps.get()
        assert rsvp.is_guest and rsvp.member is None
        assert (rsvp.name, rsvp.email, rsvp.phone, rsvp.party_size) == ("Lee Organiser", "lee@example.com", "503", 2)
        assert rsvp.source == EventRSVP.Source.SITE
        body = client.get(resp["Location"]).content.decode()
        assert _GOING in body and _GUEST_FORM not in body
        assert 'data-people-count="2"' in body
        assert f'href="{_page_url(event)}"' in body  # fill the form again

    def it_answers_a_filled_honeypot_like_a_success_and_writes_nothing(client: Client):
        event = CommunityEventFactory(community=True)
        resp = _guest_post(client, event, website="http://spam.example")
        assert resp.status_code == 302
        assert resp["Location"] == _page_url(event, "?going=1")
        assert not event.rsvps.exists()

    def it_re_renders_the_form_with_errors_and_writes_nothing(client: Client):
        event = CommunityEventFactory(community=True)
        resp = _guest_post(client, event, email="not-an-email")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert _GUEST_FORM in body and "pl-field-error" in body
        assert 'value="Lee Organiser"' in body
        assert not event.rsvps.exists()

    def it_updates_the_row_on_a_repeat_email(client: Client):
        event = CommunityEventFactory(community=True)
        _guest_post(client, event, party_size="2")
        _guest_post(client, event, email="LEE@example.com", name="Lee P", party_size="5", wants_updates="on")
        rsvp = event.rsvps.get()
        assert (rsvp.name, rsvp.party_size, rsvp.wants_updates) == ("Lee P", 5, True)
        assert 'data-people-count="5"' in client.get(_page_url(event)).content.decode()

    def it_sends_a_signed_in_user_back_to_the_page_and_writes_no_guest_row(client: Client):
        _user_with_role("guest_post_member")
        client.login(username="guest_post_member", password="pass")
        anchor = timezone.localtime().replace(hour=18, minute=0, second=0, microsecond=0) - timedelta(weeks=5)
        event = CommunityEventFactory(
            community=True,
            recurrence=CommunityEvent.Recurrence.WEEKLY,
            starts_at=anchor,
            ends_at=anchor + timedelta(hours=2),
        )
        today = timezone.localdate()
        later = event.occurrences_in(today + timedelta(days=14), today + timedelta(days=20))[0]
        query = f"?date={timezone.localdate(later).isoformat()}"
        resp = _guest_post(client, event, query)
        assert resp.status_code == 302
        assert resp["Location"] == _page_url(event, query)
        assert not event.rsvps.exists()

    def it_resizes_a_members_rsvp_when_the_email_is_theirs(client: Client):
        event = CommunityEventFactory(community=True)
        member = MemberFactory(_pre_signup_email="sam@example.com")
        EventRSVPFactory(event=event, member=member, party_size=1)
        resp = _guest_post(client, event, email="SAM@Example.com", party_size="4")
        assert resp["Location"] == _page_url(event, "?going=1")
        rsvp = event.rsvps.get()
        assert rsvp.member == member and rsvp.party_size == 4 and rsvp.email == ""

    def it_adds_a_guest_row_when_the_member_with_that_email_is_not_going(client: Client):
        event = CommunityEventFactory(community=True)
        MemberFactory(_pre_signup_email="sam@example.com")
        _guest_post(client, event, email="sam@example.com", party_size="3")
        rsvp = event.rsvps.get()
        assert rsvp.is_guest and rsvp.email == "sam@example.com" and rsvp.party_size == 3

    def it_shows_no_going_state_on_an_ended_event(client: Client):
        past = timezone.now() - timedelta(days=2)
        event = CommunityEventFactory(community=True, starts_at=past, ends_at=past + timedelta(hours=1))
        assert _GOING not in _page_url_body(client, event, "?going=1")

    def it_refuses_an_ended_event(client: Client):
        past = timezone.now() - timedelta(days=2)
        event = CommunityEventFactory(community=True, starts_at=past, ends_at=past + timedelta(hours=1))
        resp = _guest_post(client, event)
        assert resp.status_code == 302
        assert resp["Location"] == _page_url(event)
        assert not event.rsvps.exists()

    def it_404s_an_unpublished_event(client: Client):
        assert _guest_post(client, CommunityEventFactory(pending=True)).status_code == 404

    def it_keeps_the_date_on_the_action_and_the_redirect(client: Client):
        anchor = timezone.localtime().replace(hour=18, minute=0, second=0, microsecond=0) - timedelta(weeks=5)
        event = CommunityEventFactory(
            community=True,
            recurrence=CommunityEvent.Recurrence.WEEKLY,
            starts_at=anchor,
            ends_at=anchor + timedelta(hours=2),
        )
        today = timezone.localdate()
        later = event.occurrences_in(today + timedelta(days=14), today + timedelta(days=20))[0]
        query = f"?date={timezone.localdate(later).isoformat()}"
        assert f'action="{reverse("hub_event_rsvp_guest", args=[event.pk])}{query}"' in _page_url_body(
            client, event, query
        )
        resp = _guest_post(client, event, query)
        assert resp["Location"] == _page_url(event, query) + "&going=1"
        assert _GOING in client.get(resp["Location"]).content.decode()

    @override_settings(CACHES=_LOCMEM)
    def it_refuses_the_eleventh_attempt_from_one_address_in_an_hour(client: Client):
        event = CommunityEventFactory(community=True)
        for i in range(10):
            assert _guest_post(client, event, email=f"g{i}@example.com")["Location"].endswith("?going=1")
        resp = _guest_post(client, event, email="g10@example.com")
        assert resp.status_code == 302
        assert resp["Location"] == _page_url(event)
        assert not event.rsvps.filter(email="g10@example.com").exists()
        assert event.rsvps.count() == 10
        assert any("Too many" in m.message for m in get_messages(resp.wsgi_request))


def _page_url_body(client: Client, event: CommunityEvent, query: str = "") -> str:
    return client.get(_page_url(event, query)).content.decode()


def describe_a_members_party_size():
    def _member(client: Client, username: str) -> User:
        user = _user_with_role(username)
        client.login(username=username, password="pass")
        return user

    def _rsvp(client: Client, event: CommunityEvent, data: dict[str, str]):
        with patch.object(CommunityEvent, "refresh_discord_announcement"):
            return client.post(reverse("hub_event_rsvp", args=[event.pk]), data)

    def it_offers_the_select_beside_the_one_tap(client: Client):
        _member(client, "party_select")
        event = CommunityEventFactory(community=True, recurrence=CommunityEvent.Recurrence.WEEKLY)
        body = _page_url_body(client, event)
        assert _MEMBER_FORM in body and 'name="party_size"' in body and "data-rsvp-join" in body
        assert "data-rsvp-series-note" in body
        assert "data-rsvp-update" not in body

    def it_sizes_the_rsvp_from_the_select(client: Client):
        user = _member(client, "party_three")
        event = CommunityEventFactory(community=True)
        assert _rsvp(client, event, {"party_size": "3"}).status_code == 302
        assert event.rsvps.get(member=user.member).party_size == 3
        body = _page_url_body(client, event)
        assert 'data-people-count="3"' in body
        assert 'data-rsvp-state="going"' in body and "data-rsvp-update" in body and "data-rsvp-leave" in body
        assert 'data-guest-count="2"' in body

    def it_updates_the_size_of_an_rsvp_the_member_already_has(client: Client):
        user = _member(client, "party_update")
        event = CommunityEventFactory(community=True)
        _rsvp(client, event, {"party_size": "3"})
        resp = _rsvp(client, event, {"party_size": "2", "update": "1"})
        assert any("Updated: 2 people" in m.message for m in get_messages(resp.wsgi_request))
        assert event.rsvps.get(member=user.member).party_size == 2
        resp = _rsvp(client, event, {"party_size": "1", "update": "1"})
        assert any("Updated: 1 person" in m.message for m in get_messages(resp.wsgi_request))

    def it_rsvps_again_when_update_finds_no_row(client: Client):
        user = _member(client, "party_update_gone")
        event = CommunityEventFactory(community=True)
        _rsvp(client, event, {"party_size": "2", "update": "1"})
        assert event.rsvps.get(member=user.member).party_size == 2

    def it_keeps_the_one_tap_at_one_person(client: Client):
        user = _member(client, "party_one_tap")
        event = CommunityEventFactory(community=True)
        _rsvp(client, event, {})
        assert event.rsvps.get(member=user.member).party_size == 1

    def it_refuses_a_party_size_off_the_select(client: Client):
        _member(client, "party_nine")
        event = CommunityEventFactory(community=True)
        resp = _rsvp(client, event, {"party_size": "9"})
        assert resp.status_code == 302
        assert not event.rsvps.exists()
        assert any("1 to 6" in m.message for m in get_messages(resp.wsgi_request))


def describe_guests_on_the_page():
    def it_folds_guests_and_extra_people_into_a_count_for_a_member(client: Client):
        _user_with_role("sees_guests")
        client.login(username="sees_guests", password="pass")
        event = CommunityEventFactory(community=True)
        EventRSVPFactory(event=event, member=MemberFactory(full_legal_name="Zzytrix Quon"), party_size=2)
        EventRSVPFactory(event=event, guest=True, name="Zzorp Guest", party_size=3)
        body = _page_url_body(client, event)
        assert 'data-people-count="5">5<' in body
        assert "Zzytrix Quon" in body
        assert "Zzorp Guest" not in body
        assert 'data-guest-count="4">and 4 guests<' in body

    def it_says_one_guest_in_the_singular(client: Client):
        _user_with_role("sees_one_guest")
        client.login(username="sees_one_guest", password="pass")
        event = CommunityEventFactory(community=True)
        EventRSVPFactory(event=event, guest=True)
        assert 'data-guest-count="1">1 guest is going<' in _page_url_body(client, event)

    def it_says_guests_are_going_when_no_member_is_named(client: Client):
        _user_with_role("sees_only_guests")
        client.login(username="sees_only_guests", password="pass")
        event = CommunityEventFactory(community=True)
        EventRSVPFactory(event=event, guest=True, party_size=3)
        assert 'data-guest-count="3">3 guests are going<' in _page_url_body(client, event)

    def it_shows_an_anonymous_visitor_the_people_count_only(client: Client):
        event = CommunityEventFactory(community=True)
        EventRSVPFactory(event=event, guest=True, name="Zzorp Guest", party_size=3)
        body = _page_url_body(client, event)
        assert 'data-people-count="3">3<' in body
        assert "Zzorp Guest" not in body

    def it_keeps_the_guests_when_a_hidden_member_is_left_out(client: Client):
        event = CommunityEventFactory(community=True)
        EventRSVPFactory(event=event, member=MemberFactory(hide_from_directory=True), party_size=2)
        EventRSVPFactory(event=event, guest=True)
        assert 'data-people-count="1">1<' in _page_url_body(client, event)


# --- The event page redesign and the RSVP goal (#831, part 5) -------------------------------


def _going(event: CommunityEvent, people: int) -> None:
    """``people`` coming to ``event`` as one guest party, so the count is exactly that."""
    EventRSVPFactory(event=event, guest=True, party_size=people)


def describe_the_going_band():
    def it_shows_the_people_count_to_an_anonymous_visitor(client: Client):
        event = CommunityEventFactory(community=True)
        _going(event, 4)
        body = _page_url_body(client, event)
        assert '<span class="pl-event-page__count-number" data-people-count="4">4</span>' in body
        assert "data-people-label>people are going<" in body

    def it_says_one_person_in_the_singular(client: Client):
        event = CommunityEventFactory(community=True)
        _going(event, 1)
        assert "data-people-label>person is going<" in _page_url_body(client, event)

    def it_draws_no_meter_without_a_goal(client: Client):
        event = CommunityEventFactory(community=True, rsvp_goal=None)
        body = _page_url_body(client, event)
        assert "data-goal-meter" not in body
        assert 'role="progressbar"' not in body

    def it_draws_the_meter_towards_a_goal(client: Client):
        event = CommunityEventFactory(community=True, rsvp_goal=40)
        _going(event, 10)
        body = _page_url_body(client, event)
        assert (
            '<progress class="pl-event-page__meter" max="100" value="25" role="progressbar"'
            ' aria-valuenow="10" aria-valuemin="0" aria-valuemax="40"'
        ) in body
        assert 'data-goal="40">Goal 40<' in body

    def it_shows_the_pitch_only_when_set(client: Client):
        event = CommunityEventFactory(community=True, pitch="Bring a friend and a mug.")
        assert "data-event-pitch>Bring a friend and a mug.<" in _page_url_body(client, event)
        assert "data-event-pitch" not in _page_url_body(client, CommunityEventFactory(community=True))


def describe_rsvp_goal_percent():
    def it_is_zero_without_a_goal():
        assert CommunityEvent(rsvp_goal=None).rsvp_goal_percent(12) == 0

    def it_rounds_the_share_of_the_goal():
        assert CommunityEvent(rsvp_goal=3).rsvp_goal_percent(1) == 33

    def it_stops_at_full_once_the_goal_is_passed():
        assert CommunityEvent(rsvp_goal=10).rsvp_goal_percent(25) == 100


def describe_the_eyebrow():
    def it_names_past_lives_for_a_site_wide_event(client: Client):
        body = _page_url_body(client, CommunityEventFactory(community=True))
        assert '<span class="pl-event-page__org">Past Lives Makerspace</span>' in body

    def it_links_the_guild_for_a_guild_event(client: Client):
        guild = GuildFactory(name="Metal Guild")
        body = _page_url_body(client, CommunityEventFactory(guild=guild))
        url = reverse("hub_guild_detail", args=[guild.slug])
        assert f'<a href="{url}" class="pl-event-page__guild">Metal Guild</a>' in body
        assert "pl-event-page__org" not in body

    def it_carries_the_recurrence_pill_for_a_series(client: Client):
        event = CommunityEventFactory(community=True, recurrence=CommunityEvent.Recurrence.WEEKLY)
        assert "data-recurrence-pill>Every week<" in _page_url_body(client, event)
        assert "data-recurrence-pill" not in _page_url_body(client, CommunityEventFactory(community=True))


def describe_the_share_button():
    def it_renders_the_class_page_share_wrapper_and_script(client: Client):
        event = CommunityEventFactory(community=True, title="Zine Night")
        body = _page_url_body(client, event)
        assert "/static/js/class_share.js" in body
        assert '@pl-share-menu="open = !open"' in body
        assert f'data-share-url="{event.public_url}"' in body
        assert 'data-share-title="Zine Night"' in body
        assert 'data-share-text="Zine Night at Past Lives Makerspace"' in body
        assert f'data-share-copy="{event.public_url}"' in body
        assert 'role="menu" aria-label="Share this event"' in body


def describe_the_location_card():
    def it_links_directions_to_a_maps_search(client: Client):
        event = CommunityEventFactory(community=True, location="2100 SE Division St")
        body = _page_url_body(client, event)
        assert f'href="https://www.google.com/maps/search/?api=1&amp;query={quote_plus("2100 SE Division St")}"' in body
        assert "data-directions>Get directions<" in body

    def it_offers_no_directions_without_a_location(client: Client):
        event = CommunityEventFactory(community=True, location="")
        assert event.directions_url == ""
        assert "data-directions" not in _page_url_body(client, event)


def describe_the_manage_link():
    def it_links_an_administrator_to_manage_events(client: Client):
        _user_with_role("page_manage_admin", fog_role=Member.FogRole.ADMIN)
        client.login(username="page_manage_admin", password="pass")
        event = CommunityEventFactory(community=True)
        body = _page_url_body(client, event)
        assert f'href="{reverse("hub_admin_event", args=[event.pk])}" data-event-manage>Manage<' in body

    def it_links_a_calendar_administrator_too(client: Client):
        from membership.models import AdminCapability

        user = _user_with_role("page_manage_cal")
        user.member.admin_capabilities.create(capability=AdminCapability.Capability.EVENTS_APPROVER)
        client.login(username="page_manage_cal", password="pass")
        assert "data-event-manage" in _page_url_body(client, CommunityEventFactory(community=True))

    def it_hides_from_a_guild_lead_who_may_only_edit(client: Client):
        user = _user_with_role("page_manage_lead")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="page_manage_lead", password="pass")
        body = _page_url_body(client, CommunityEventFactory(guild=guild))
        assert "data-event-edit" in body
        assert "data-event-manage" not in body

    def it_hides_from_a_member(client: Client):
        _user_with_role("page_manage_member")
        client.login(username="page_manage_member", password="pass")
        assert "data-event-manage" not in _page_url_body(client, CommunityEventFactory(community=True))


def describe_the_anonymous_page():
    def it_renders_the_whole_page_with_no_editor_or_member_parts(client: Client):
        event = CommunityEventFactory(community=True, rsvp_goal=20, pitch="Come along.", location="Main Studio")
        _going(event, 3)
        resp = client.get(_page_url(event))
        assert resp.status_code == 200
        body = resp.content.decode()
        for present in (
            "data-event-hero",
            "data-event-going",
            "data-goal-meter",
            _GUEST_FORM,
            "data-share",
            "data-directions",
        ):
            assert present in body
        for absent in ("data-event-edit", "data-event-manage", "data-event-qr", "data-event-rsvps", _MEMBER_FORM):
            assert absent not in body

    def it_carries_no_style_attribute_in_the_page(client: Client):
        body = _page_url_body(client, CommunityEventFactory(community=True, rsvp_goal=5))
        page = body[body.index("data-event-page") : body.index('class="pl-event-page__footer"')]
        assert "style=" not in page


def describe_the_event_page_composer_card():
    def it_renders_in_the_direct_composer(client: Client):
        _user_with_role("page_card_admin", fog_role=Member.FogRole.ADMIN)
        client.login(username="page_card_admin", password="pass")
        html = client.get(reverse("hub_event_add")).content.decode()
        assert "data-event-page-card" in html
        assert 'name="rsvp_goal"' in html and 'name="pitch"' in html

    def it_renders_in_the_review_composer(client: Client):
        config = SiteConfiguration.load()
        config.member_event_policy = SiteConfiguration.MemberEventPolicy.APPROVAL
        config.save()
        _user_with_role("page_card_member")
        client.login(username="page_card_member", password="pass")
        html = client.get(reverse("hub_propose_event")).content.decode()
        assert "data-event-page-card" in html
        assert 'name="rsvp_goal"' in html and 'name="pitch"' in html

    def it_saves_the_goal_and_the_pitch(client: Client):
        _user_with_role("page_card_saver", fog_role=Member.FogRole.ADMIN)
        client.login(username="page_card_saver", password="pass")
        data = {
            "title": "Goal Night",
            "starts_at": "2026-07-11T18:00",
            "ends_at": "2026-07-11T20:00",
            "recurrence": "none",
            "rsvp_goal": "60",
            "pitch": "Sixty makers in one room.",
        }
        with patch.object(CommunityEvent, "announce"):
            client.post(reverse("hub_event_add"), data=data)
        event = CommunityEvent.objects.get(title="Goal Night")
        assert (event.rsvp_goal, event.pitch) == (60, "Sixty makers in one room.")

    def it_refuses_a_goal_of_zero(client: Client):
        _user_with_role("page_card_zero", fog_role=Member.FogRole.ADMIN)
        client.login(username="page_card_zero", password="pass")
        data = {
            "title": "Zero Night",
            "starts_at": "2026-07-11T18:00",
            "ends_at": "2026-07-11T20:00",
            "recurrence": "none",
            "rsvp_goal": "0",
        }
        with patch.object(CommunityEvent, "announce"):
            resp = client.post(reverse("hub_event_add"), data=data)
        assert resp.status_code == 200
        assert not CommunityEvent.objects.filter(title="Zero Night").exists()
        assert resp.context["form"].errors["rsvp_goal"]
