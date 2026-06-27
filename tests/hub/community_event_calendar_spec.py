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
