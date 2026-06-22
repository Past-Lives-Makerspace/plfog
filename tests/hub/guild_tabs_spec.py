"""Guild page tabs: Overview / Guild Calendar (calendar + classes + orientation) / Buyables."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from membership.models import CalendarEvent
from tests.membership.factories import GuildFactory, MembershipPlanFactory


def _member(username: str) -> User:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, password="pw")


@pytest.mark.django_db
def describe_guild_tabs():
    def it_shows_the_tabs(client: Client):
        _member("t1")
        client.login(username="t1", password="pw")
        guild = GuildFactory()
        body = client.get(reverse("hub_guild_detail", args=[guild.pk])).content
        assert b"section === 'overview'" in body
        assert b"section === 'schedule'" in body
        assert b"section === 'buyables'" in body
        # Calendar and Classes were merged into the single Schedule tab.
        assert b"section === 'calendar'" not in body
        assert b"section === 'classes'" not in body

    def it_puts_the_products_card_inside_the_buyables_tab(client: Client):
        _member("t4")
        client.login(username="t4", password="pw")
        guild = GuildFactory()
        body = client.get(reverse("hub_guild_detail", args=[guild.pk])).content
        # The Products heading must live within the Buyables tab panel, i.e. it
        # is rendered after the panel opens — not loose below every tab.
        panel = b"section === 'buyables'"
        assert panel in body
        assert b">Products<" in body
        assert body.index(panel) < body.index(b">Products<")

    def it_loads_the_calendar_stylesheet(client: Client):
        # The pl-calendar-* grid styles live in calendar.css; without the link
        # the Calendar tab renders as an unstyled stack of day names.
        _member("t6")
        client.login(username="t6", password="pw")
        guild = GuildFactory()
        body = client.get(reverse("hub_guild_detail", args=[guild.pk])).content
        assert b"calendar.css" in body

    def it_does_not_emit_the_calendar_comment_as_text(client: Client):
        # Guards the multi-line {# #} bug: Django only matches single-line
        # comments, so a wrapped comment leaks into the rendered page.
        _member("t5")
        client.login(username="t5", password="pw")
        guild = GuildFactory()
        body = client.get(reverse("hub_guild_detail", args=[guild.pk])).content
        assert b"Self-contained, read-only guild calendar" not in body

    def it_shows_published_class_sessions_on_the_guild_calendar(client: Client):
        _member("t2")
        client.login(username="t2", password="pw")
        guild = GuildFactory()
        published = ClassOfferingFactory(
            category=CategoryFactory(guild=guild),
            status=ClassOffering.Status.PUBLISHED,
            title="Anvil Basics",
            slug="anvil-basics",
        )
        ClassSessionFactory(class_offering=published, starts_at=timezone.now() + timedelta(days=2))
        ClassOfferingFactory(
            category=CategoryFactory(guild=guild),
            status=ClassOffering.Status.DRAFT,
            title="Secret Draft",
            slug="secret-draft",
        )
        resp = client.get(reverse("hub_guild_detail", args=[guild.pk]))
        assert b"Anvil Basics" in resp.content
        assert b"Secret Draft" not in resp.content
        assert reverse("classes:register", args=["anvil-basics"]).encode() in resp.content

    def it_scopes_the_calendar_to_this_guild(client: Client):
        _member("t3")
        client.login(username="t3", password="pw")
        now = timezone.now()
        when = now + timedelta(days=2)  # safely inside the rolling window in any local tz
        guild = GuildFactory()
        other = GuildFactory()
        CalendarEvent.objects.create(
            guild=guild,
            uid="g-1",
            title="Forge Open Studio",
            start_dt=when,
            end_dt=when + timedelta(hours=2),
            fetched_at=now,
        )
        CalendarEvent.objects.create(
            guild=other,
            uid="o-1",
            title="Other Guild Event",
            start_dt=when,
            end_dt=when + timedelta(hours=2),
            fetched_at=now,
        )
        resp = client.get(reverse("hub_guild_detail", args=[guild.pk]))
        assert b"Forge Open Studio" in resp.content
        assert b"Other Guild Event" not in resp.content

    def it_synthesizes_class_entries_only_for_the_guild_calendar(client: Client):
        _member("t7")
        client.login(username="t7", password="pw")
        guild = GuildFactory()
        published = ClassOfferingFactory(
            category=CategoryFactory(guild=guild),
            status=ClassOffering.Status.PUBLISHED,
            title="Unique Forge Class",
            slug="unique-forge",
        )
        ClassSessionFactory(class_offering=published, starts_at=timezone.now() + timedelta(days=1))

        guild_resp = client.get(reverse("hub_guild_calendar_events", args=[guild.pk]))
        community_resp = client.get(reverse("hub_community_calendar_events"))

        assert guild_resp.status_code == 200
        assert b"Unique Forge Class" in guild_resp.content
        # Nav buttons point back at the guild-scoped endpoint, not the community one.
        assert reverse("hub_guild_calendar_events", args=[guild.pk]).encode() in guild_resp.content
        # Synthetic entries never leak into the community calendar (it reads CalendarEvent only).
        assert b"Unique Forge Class" not in community_resp.content

    def it_shows_orientation_slots_on_the_guild_calendar(client: Client):
        from tests.membership.factories import GuildOrientationSettingsFactory, OrientationSlotFactory

        _member("t8")
        client.login(username="t8", password="pw")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        OrientationSlotFactory(guild=guild, enabled_settings=False, starts_at=timezone.now() + timedelta(days=1))

        resp = client.get(reverse("hub_guild_calendar_events", args=[guild.pk]))

        assert resp.status_code == 200
        assert b"Orientation" in resp.content
