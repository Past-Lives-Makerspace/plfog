"""Guild pulse — the Recent Activity feed on the Overview tab."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory
from classes.models import ClassOffering
from membership.models import GuildAnnouncement, GuildMembership
from tests.membership.factories import GuildFactory, MemberFactory, MembershipPlanFactory


def _member(username: str) -> User:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, password="pw")


@pytest.mark.django_db
def describe_guild_pulse():
    def it_shows_recent_joins_announcements_and_classes(client: Client):
        _member("p1")
        client.login(username="p1", password="pw")
        guild = GuildFactory()
        GuildMembership.objects.create(guild=guild, member=MemberFactory(full_legal_name="Penina Stitch"))
        GuildAnnouncement.objects.create(guild=guild, title="Forge night", body="Friday")
        offering = ClassOfferingFactory(
            category=CategoryFactory(guild=guild),
            status=ClassOffering.Status.PUBLISHED,
            title="Anvil Basics",
            slug="anvil-basics-pulse",
        )
        offering.published_at = timezone.now()
        offering.save(update_fields=["published_at"])

        resp = client.get(reverse("hub_guild_detail", args=[guild.pk]))
        assert b"Recent Activity" in resp.content
        assert b"joined the guild" in resp.content
        assert b"Forge night" in resp.content
        assert b"New class: Anvil Basics" in resp.content
