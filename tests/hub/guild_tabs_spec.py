"""Guild page tabs: Overview / Calendar / Classes / Buyables."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory
from classes.models import ClassOffering
from membership.models import CalendarEvent
from tests.membership.factories import GuildFactory, MembershipPlanFactory


def _member(username: str) -> User:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, password="pw")


@pytest.mark.django_db
def describe_guild_tabs():
    def it_shows_the_four_tabs(client: Client):
        _member("t1")
        client.login(username="t1", password="pw")
        guild = GuildFactory()
        body = client.get(reverse("hub_guild_detail", args=[guild.pk])).content
        assert b">Overview<" in body
        assert b">Calendar<" in body
        assert b">Classes<" in body
        assert b">Buyables<" in body

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

    def it_lists_published_classes_in_the_classes_tab(client: Client):
        _member("t2")
        client.login(username="t2", password="pw")
        guild = GuildFactory()
        ClassOfferingFactory(
            category=CategoryFactory(guild=guild),
            status=ClassOffering.Status.PUBLISHED,
            title="Anvil Basics",
            slug="anvil-basics",
        )
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
