"""BDD-style tests for guild page views."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from membership.models import Guild, GuildVote, Member, MembershipPlan
from tests.membership.factories import LeaseFactory, SpaceFactory

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture()
def guild() -> Guild:
    return Guild.objects.create(name="Woodworkers", slug="woodworkers")


@pytest.fixture()
def member_user():
    user = User.objects.create_user(username="member1", password="test")
    plan = MembershipPlan.objects.create(name="Full-Time", monthly_price=300)
    Member.objects.create(
        user=user,
        full_legal_name="Test Member",
        preferred_name="Test",
        email="test@example.com",
        phone="503-555-0001",
        billing_name="Test Member",
        emergency_contact_name="EC",
        emergency_contact_phone="503-555-9999",
        emergency_contact_relationship="Partner",
        membership_plan=plan,
    )
    return user


def describe_guild_list():
    def it_returns_200(client, guild):
        response = client.get(reverse("guild_list"))
        assert response.status_code == 200

    def it_contains_guild_name(client, guild):
        response = client.get(reverse("guild_list"))
        assert guild.name in response.content.decode()


def describe_guild_detail():
    def it_returns_200(client, guild):
        response = client.get(reverse("guild_detail", args=[guild.slug]))
        assert response.status_code == 200

    def it_returns_404_for_missing_guild(client):
        response = client.get(reverse("guild_detail", args=["nonexistent"]))
        assert response.status_code == 404

    def it_returns_200_for_authenticated_user(client, guild, member_user):
        """Authenticated path sets voters and has_voted in context."""
        client.login(username="member1", password="test")
        response = client.get(reverse("guild_detail", args=[guild.slug]))
        assert response.status_code == 200
        assert "voters" in response.context
        assert "has_voted" in response.context

    def it_shows_voter_names_to_authenticated_user(client, guild, member_user):
        """has_voted is True when the logged-in member voted for this guild."""
        member = Member.objects.get(user=member_user)
        GuildVote.objects.create(member=member, guild=guild, priority=1)
        client.login(username="member1", password="test")
        response = client.get(reverse("guild_detail", args=[guild.slug]))
        assert response.context["has_voted"] is True

    def it_includes_spaces_when_guild_has_active_lease(client, guild):
        """spaces context key is populated when an active lease exists for the guild."""
        space = SpaceFactory()
        today = timezone.now().date()
        LeaseFactory(
            tenant_obj=guild,
            space=space,
            start_date=today - timedelta(days=10),
        )
        response = client.get(reverse("guild_detail", args=[guild.slug]))
        assert response.status_code == 200
        assert space in response.context["spaces"]


def describe_dashboard():
    def it_redirects_anonymous_users(client):
        response = client.get(reverse("dashboard"))
        assert response.status_code == 302

    def it_returns_200_for_authenticated_users(client, member_user):
        client.login(username="member1", password="test")
        response = client.get(reverse("dashboard"))
        assert response.status_code == 200
