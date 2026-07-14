"""BDD specs for CommunityEvent on the calendar: inclusion in both calendar branches,
monthly occurrence expansion, source-filter visibility wiring, and the .ics export."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from hub.calendar_entries import EVENT_PK_OFFSET, community_event_entries
from hub.views import _get_calendar_context
from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _aware(y: int, m: int, d: int, hour: int = 12) -> datetime:
    return timezone.make_aware(datetime(y, m, d, hour, 0))


def _login(client: Client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    client.login(username=username, password="pass")
    return user


def _community_entries(ctx: dict) -> list:
    return [e for e in ctx["month_events"] if getattr(e, "source", "") == "community"]


def describe_calendar_inclusion():
    def it_includes_an_in_window_event_on_the_community_calendar():
        now = timezone.now()
        CommunityEventFactory(
            community=True,
            title="In-window Potluck",
            starts_at=now + timedelta(days=5),
            ends_at=now + timedelta(days=5, hours=2),
        )
        ctx = _get_calendar_context(RequestFactory().get("/"))
        titles = [e.title for e in _community_entries(ctx)]
        assert "In-window Potluck" in titles

    def it_includes_a_guild_event_on_that_guilds_calendar_only():
        now = timezone.now()
        guild_a = GuildFactory()
        guild_b = GuildFactory()
        CommunityEventFactory(
            guild=guild_a, title="A event", starts_at=now + timedelta(days=4), ends_at=now + timedelta(days=4, hours=2)
        )
        CommunityEventFactory(
            guild=guild_b, title="B event", starts_at=now + timedelta(days=4), ends_at=now + timedelta(days=4, hours=2)
        )
        ctx_a = _get_calendar_context(RequestFactory().get("/"), guild=guild_a)
        titles = [e.title for e in _community_entries(ctx_a)]
        assert "A event" in titles
        assert "B event" not in titles


def describe_monthly_expansion():
    def it_expands_a_past_anchored_series_across_a_multi_month_window():
        # Anchored years before the window — a plain BETWEEN filter would drop it.
        CommunityEventFactory(
            recurrence=CommunityEvent.Recurrence.MONTHLY,
            starts_at=_aware(2020, 1, 11, 18),  # 2nd Saturday
            ends_at=_aware(2020, 1, 11, 20),
        )
        entries = community_event_entries(date(2026, 8, 1), date(2026, 10, 31))
        assert len(entries) == 3  # one per month
        pks = [e.pk for e in entries]
        assert len(set(pks)) == 3  # distinct synthetic pks (no collision)
        assert all(pk >= EVENT_PK_OFFSET for pk in pks)

    def it_uses_the_event_pk_offset_so_it_never_collides():
        now = timezone.now()
        event = CommunityEventFactory(
            community=True, starts_at=now + timedelta(days=3), ends_at=now + timedelta(days=3, hours=1)
        )
        entries = community_event_entries((now - timedelta(days=1)).date(), (now + timedelta(days=10)).date())
        assert entries[0].pk == EVENT_PK_OFFSET + event.pk * 100


def describe_visibility_wiring():
    def it_wires_the_community_source_on_the_community_calendar(client: Client):
        _login(client, "vis1")
        now = timezone.now()
        CommunityEventFactory(
            community=True,
            title="Open Mic",
            starts_at=now + timedelta(days=3),
            ends_at=now + timedelta(days=3, hours=2),
        )
        resp = client.get(reverse("hub_community_calendar"))
        assert b"Community events" in resp.content  # the filter button (proves color wiring)
        assert b"community" in resp.content  # present in default_filters_json

    def it_wires_the_community_source_on_a_guild_calendar(client: Client):
        _login(client, "vis2")
        guild = GuildFactory()
        resp = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"Community events" in resp.content


def describe_ics_export():
    def it_emits_a_timed_vevent_for_a_community_event(client: Client):
        _login(client, "ics1")
        now = timezone.now()
        CommunityEventFactory(
            community=True,
            title="Potluck",
            location="Common Area",
            description="Bring a dish.",
            starts_at=now + timedelta(days=3),
            ends_at=now + timedelta(days=3, hours=2),
        )
        event = CommunityEvent.objects.get(title="Potluck")
        resp = client.get(reverse("hub_calendar_export_ics"))
        body = resp.content.decode()
        assert f"UID:community-{event.pk}@pastlives" in body
        assert "SUMMARY:Potluck" in body
        assert "DTSTART:" in body
        assert "DTEND:" in body
        assert "LOCATION:Common Area" in body
        assert "RRULE" not in body  # non-recurring → no RRULE

    def it_emits_one_rrule_vevent_for_a_monthly_event(client: Client):
        _login(client, "ics2")
        CommunityEventFactory(
            community=True,
            title="Monthly Potluck",
            recurrence=CommunityEvent.Recurrence.MONTHLY,
            starts_at=_aware(2026, 7, 11, 18),  # 2nd Saturday
            ends_at=_aware(2026, 7, 11, 20),
        )
        resp = client.get(reverse("hub_calendar_export_ics"))
        body = resp.content.decode()
        assert body.count("RRULE:FREQ=MONTHLY;BYDAY=2SA") == 1
        # One VEVENT for the whole series, not per-occurrence.
        assert body.count("UID:community-") == 1


def _class_event(guild, *, days: int = 5):
    """A class-source CalendarEvent for ``guild`` whose start falls in-window."""
    from membership.models import CalendarEvent

    now = timezone.now()
    start = now + timedelta(days=days)
    return CalendarEvent.objects.create(
        guild=guild,
        source=CalendarEvent.Source.CLASSES,
        uid=f"local-class-{guild.pk}-{days}",
        title="Intro to Welding",
        start_dt=start,
        end_dt=start + timedelta(hours=2),
        fetched_at=now,
    )


def describe_guild_colored_classes():
    """§5-6: a class inherits its guild's calendar color + legend toggle; orientation/
    community keep their own color, and a community-only guild earns no dead toggle."""

    def it_adds_the_color_of_a_guild_that_only_has_class_events():
        guild = GuildFactory(calendar_url="", calendar_color="#C41E3A")
        _class_event(guild)
        ctx = _get_calendar_context(RequestFactory().get("/"))
        assert ctx["source_colors"][str(guild.pk)] == "#C41E3A"

    def it_lists_a_class_only_guild_in_legend_guilds():
        guild = GuildFactory(calendar_url="")
        _class_event(guild)
        ctx = _get_calendar_context(RequestFactory().get("/"))
        assert guild in ctx["legend_guilds"]

    def it_omits_a_guild_that_only_has_a_community_event():
        now = timezone.now()
        guild = GuildFactory(calendar_url="")
        CommunityEventFactory(
            guild=guild,
            title="Guild Social",
            starts_at=now + timedelta(days=5),
            ends_at=now + timedelta(days=5, hours=2),
        )
        ctx = _get_calendar_context(RequestFactory().get("/"))
        # The community entry rides the grid, but a community source never earns a guild
        # legend toggle (that would be a dead toggle controlling nothing).
        assert "Guild Social" in [getattr(e, "title", "") for e in ctx["month_events"]]
        assert guild not in ctx["legend_guilds"]
        assert str(guild.pk) not in ctx["source_colors"]

    def it_flags_has_ungrouped_classes_for_a_class_with_no_guild():
        from membership.models import CalendarEvent

        now = timezone.now()
        start = now + timedelta(days=5)
        CalendarEvent.objects.create(
            guild=None,
            source=CalendarEvent.Source.CLASSES,
            uid="ungrouped-class",
            title="Open Class",
            start_dt=start,
            end_dt=start + timedelta(hours=2),
            fetched_at=now,
        )
        ctx = _get_calendar_context(RequestFactory().get("/"))
        assert ctx["has_ungrouped_classes"] is True

    def it_is_false_when_every_class_has_a_guild():
        guild = GuildFactory(calendar_url="")
        _class_event(guild)
        ctx = _get_calendar_context(RequestFactory().get("/"))
        assert ctx["has_ungrouped_classes"] is False
