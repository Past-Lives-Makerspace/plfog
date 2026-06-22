"""Guild Overview build-out: stat chips, upcoming-classes strip, get-involved CTA."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from tests.membership.factories import GuildFactory, MembershipPlanFactory


def _member(username: str) -> User:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, password="pw")  # signal links an active Member


def _published_class(guild, **kwargs):
    offering = ClassOfferingFactory(
        category=CategoryFactory(guild=guild), status=ClassOffering.Status.PUBLISHED, is_private=False, **kwargs
    )
    ClassSessionFactory(
        class_offering=offering,
        starts_at=timezone.now() + timedelta(days=7),
        ends_at=timezone.now() + timedelta(days=7, hours=2),
    )
    return offering


@pytest.mark.django_db
def describe_guild_overview():
    def it_shows_stat_chips_with_tba_when_no_meeting(client: Client):
        _member("ov1")
        client.login(username="ov1", password="pw")
        guild = GuildFactory()
        resp = client.get(reverse("hub_guild_detail", args=[guild.pk]))
        assert resp.status_code == 200
        assert b"member" in resp.content
        assert b"Meetings TBA" in resp.content

    def it_lists_upcoming_classes_for_this_guild_only(client: Client):
        _member("ov2")
        client.login(username="ov2", password="pw")
        guild = GuildFactory()
        _published_class(guild, title="Forge Intro", slug="forge-intro")
        _published_class(GuildFactory(), title="Other Guild Class", slug="other-gc")
        resp = client.get(reverse("hub_guild_detail", args=[guild.pk]))
        assert b"Forge Intro" in resp.content
        assert b"Other Guild Class" not in resp.content
        assert reverse("classes:register", args=["forge-intro"]).encode() in resp.content

    def it_shows_the_get_involved_cta(client: Client):
        _member("ov3")
        client.login(username="ov3", password="pw")
        guild = GuildFactory()
        resp = client.get(reverse("hub_guild_detail", args=[guild.pk]))
        assert b"Get Involved" in resp.content
        assert reverse("classes:teach_class_create").encode() in resp.content
