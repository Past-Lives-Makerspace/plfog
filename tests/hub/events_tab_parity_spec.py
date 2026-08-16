"""BDD specs for the Community Calendar Events tab — parity with the grid, pagination,
and the event-card link markup ("More Info" + clickable title).

The bug these lock down: the Events tab used to list only FOG-native CommunityEvents, so
the guild-meeting / class / subscribed-feed events that *are* on the calendar grid were
missing from the tab. The tab now sources the same union the grid shows.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import CalendarEvent, CommunityEvent, Member
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _member(username: str = "tabuser") -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    return user


def _admin(username: str = "tabadmin") -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _feed_event(title: str, *, days: int = 3, source: str = "guild", guild=None, url: str = "") -> CalendarEvent:
    now = timezone.now()
    return CalendarEvent.objects.create(
        guild=guild,
        source=source,
        uid=f"uid-{title}",
        title=title,
        url=url,
        start_dt=now + timedelta(days=days),
        end_dt=now + timedelta(days=days, hours=2),
        fetched_at=now,
    )


def describe_upcoming_calendar_events():
    def it_includes_feed_class_events_not_just_community_events():
        from hub.calendar_entries import upcoming_calendar_events

        guild = GuildFactory(name="Woodshop")
        _feed_event("Woodshop Open Hours", guild=guild)
        CommunityEventFactory(community=True, title="Spring Mixer")

        titles = [e.title for e in upcoming_calendar_events()]
        assert "Woodshop Open Hours" in titles  # the previously-missing feed event
        assert "Spring Mixer" in titles  # the community event was already listed

    def it_carries_the_backing_community_event_for_management_and_sync():
        from hub.calendar_entries import upcoming_calendar_events

        event = CommunityEventFactory(community=True, title="Backed Event")
        entry = next(e for e in upcoming_calendar_events() if getattr(e, "community_event", None))
        assert entry.community_event == event

    def it_collapses_a_recurring_series_to_a_single_upcoming_row():
        from hub.calendar_entries import upcoming_calendar_events

        # A weekly studio-hours event has many occurrences, but the tab lists it once.
        CommunityEventFactory(
            community=True,
            title="Weekly Studio Hours",
            recurrence=CommunityEvent.Recurrence.WEEKLY,
            starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() - timedelta(days=1) + timedelta(hours=2),
        )
        rows = [e for e in upcoming_calendar_events() if e.title == "Weekly Studio Hours"]
        assert len(rows) == 1

    def it_excludes_a_past_non_recurring_event():
        from hub.calendar_entries import upcoming_calendar_events

        now = timezone.now()
        CommunityEventFactory(
            community=True,
            title="Last Month's Talk",
            starts_at=now - timedelta(days=30),
            ends_at=now - timedelta(days=30) + timedelta(hours=1),
        )
        assert "Last Month's Talk" not in [e.title for e in upcoming_calendar_events()]

    def it_populates_video_url_from_the_backing_community_event():
        from hub.calendar_entries import upcoming_calendar_events

        CommunityEventFactory(community=True, title="Streamed Talk", video_url="https://meet.google.com/abc-defg-hij")
        entry = next(e for e in upcoming_calendar_events() if e.title == "Streamed Talk")
        assert entry.video_url == "https://meet.google.com/abc-defg-hij"

    def it_leaves_video_url_blank_for_a_feed_event():
        from hub.calendar_entries import upcoming_calendar_events

        # A feed CalendarEvent has no video_url field at all (only the CalendarEntry
        # dataclass does) — duck-typed template reads resolve it to "", so getattr
        # mirrors that rather than asserting a real model attribute exists.
        guild = GuildFactory(name="Ceramics")
        _feed_event("Ceramics Open Studio", guild=guild)
        entry = next(e for e in upcoming_calendar_events() if e.title == "Ceramics Open Studio")
        assert getattr(entry, "video_url", "") == ""


def describe_events_tab_view():
    def it_lists_a_feed_event_that_shows_on_the_grid(client: Client):
        _member("parity1")
        client.login(username="parity1", password="pass")
        guild = GuildFactory(name="Metal Guild")
        _feed_event("Forge Night", guild=guild)

        resp = client.get(reverse("hub_community_calendar"))
        assert resp.status_code == 200
        titles = [e.title for e in resp.context["events_page_obj"]]
        assert "Forge Night" in titles
        assert b"Forge Night" in resp.content

    def it_paginates_when_there_are_more_than_a_page_of_events(client: Client):
        _member("parity_page")
        client.login(username="parity_page", password="pass")
        guild = GuildFactory(name="Busy Guild")
        for i in range(14):
            _feed_event(f"Event {i:02d}", days=i + 1, guild=guild)

        resp = client.get(reverse("hub_community_calendar"))
        page = resp.context["events_page_obj"]
        assert page.paginator.num_pages == 2
        assert len(page.object_list) == 10

        resp2 = client.get(reverse("hub_community_calendar") + "?events_page=2")
        page2 = resp2.context["events_page_obj"]
        assert page2.number == 2
        assert len(page2.object_list) == 4


def describe_event_card_links():
    def it_links_a_community_event_title_to_its_detail_page():
        event = CommunityEventFactory(community=True, title="Gallery Opening")
        from hub.calendar_entries import upcoming_calendar_events

        entry = next(e for e in upcoming_calendar_events() if getattr(e, "community_event", None) == event)
        html = render_to_string(
            "hub/partials/calendar_event_item.html",
            {"event": entry, "source_colors": {"community": "#3d8bd4"}},
        )
        assert f'href="{event.absolute_url}"' in html
        assert "pl-calendar-list__title--link" in html

    def it_renders_more_info_not_register_for_a_class_event():
        event = _feed_event("Intro to Welding", source="classes", url="https://book.pastlives.space/classes/welding/")
        html = render_to_string(
            "hub/partials/calendar_event_item.html",
            {"event": event, "source_colors": {"classes": "#AA33BB"}},
        )
        assert "More Info" in html
        assert "Register" not in html
        # The class title is now itself a link, not plain text.
        assert "pl-calendar-list__title--link" in html

    def it_shows_a_join_online_link_when_video_url_is_set():
        event = CommunityEventFactory(community=True, title="Streamed Meetup", video_url="https://meet.google.com/x")
        from hub.calendar_entries import upcoming_calendar_events

        entry = next(e for e in upcoming_calendar_events() if getattr(e, "community_event", None) == event)
        html = render_to_string(
            "hub/partials/calendar_event_item.html",
            {"event": entry, "source_colors": {"community": "#3d8bd4"}},
        )
        assert "Join online" in html
        assert 'href="https://meet.google.com/x"' in html

    def it_omits_the_join_online_link_when_video_url_is_blank():
        event = CommunityEventFactory(community=True, title="In Person Meetup")
        from hub.calendar_entries import upcoming_calendar_events

        entry = next(e for e in upcoming_calendar_events() if getattr(e, "community_event", None) == event)
        html = render_to_string(
            "hub/partials/calendar_event_item.html",
            {"event": entry, "source_colors": {"community": "#3d8bd4"}},
        )
        assert "Join online" not in html


def describe_sync_flag():
    def it_shows_a_compact_synced_flag_on_a_synced_community_event():
        from hub.calendar_entries import upcoming_calendar_events

        event = CommunityEventFactory(community=True, pushed=True, title="Synced Party")
        assert event.sync_state == CommunityEvent.SyncState.SYNCED
        entry = next(e for e in upcoming_calendar_events() if getattr(e, "community_event", None) == event)
        html = render_to_string(
            "hub/partials/calendar_event_item.html",
            {"event": entry, "source_colors": {"community": "#3d8bd4"}, "sync_visible": True},
        )
        assert "pl-sync-flag" in html
        assert "Synced to Google" in html  # words preserved in the flag's title/aria

    def it_shows_admin_edit_and_delete_on_the_events_tab(client: Client):
        _admin("flagadmin")
        client.login(username="flagadmin", password="pass")
        CommunityEventFactory(community=True, title="Managed Event")
        resp = client.get(reverse("hub_community_calendar"))
        assert b"pl-calendar-list__manage" in resp.content
        assert b"Managed Event" in resp.content

    def it_shows_the_flag_on_the_calendar_grid_list_too(client: Client, settings):
        from core.models import SiteConfiguration

        _admin("gridadmin")
        settings.GOOGLE_CALENDAR_SYNC_ENABLED = True
        config = SiteConfiguration.load()
        config.google_calendar_sync_enabled = True
        config.save(update_fields=["google_calendar_sync_enabled"])
        client.login(username="gridadmin", password="pass")
        CommunityEventFactory(
            community=True,
            pushed=True,
            title="Grid Synced Event",
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        # The list below the calendar grid (the HTMX partial), not just the Events tab.
        resp = client.get(reverse("hub_community_calendar_events"))
        assert b"pl-sync-flag" in resp.content

    def it_hides_the_flag_from_a_plain_member_on_the_grid_list(client: Client, settings):
        from core.models import SiteConfiguration

        _member("gridmember")
        settings.GOOGLE_CALENDAR_SYNC_ENABLED = True
        config = SiteConfiguration.load()
        config.google_calendar_sync_enabled = True
        config.save(update_fields=["google_calendar_sync_enabled"])
        client.login(username="gridmember", password="pass")
        CommunityEventFactory(
            community=True,
            pushed=True,
            title="Grid Synced Event",
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        resp = client.get(reverse("hub_community_calendar_events"))
        assert b"pl-sync-flag" not in resp.content
