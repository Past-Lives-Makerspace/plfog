"""Guild page tabs: Overview / Calendar / Classes."""

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
    def it_shows_the_three_tabs(client: Client):
        _member("t1")
        client.login(username="t1", password="pw")
        guild = GuildFactory()
        body = client.get(reverse("hub_guild_detail", args=[guild.pk])).content
        assert b">Overview<" in body
        assert b">Calendar<" in body
        assert b">Classes<" in body

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
