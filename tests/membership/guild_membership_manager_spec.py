"""BDD specs for GuildMembershipManager — the source-provenance anti-oscillation key."""

from __future__ import annotations

import pytest

from membership.models import GuildMembership
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db


def describe_record_app_join():
    def it_creates_a_source_app_row():
        guild, member = GuildFactory(), MemberFactory()
        membership, created, upgraded = GuildMembership.objects.record_app_join(guild, member)
        assert created is True
        assert upgraded is False
        assert membership.source == GuildMembership.Source.APP

    def it_is_idempotent_for_an_existing_app_row():
        guild, member = GuildFactory(), MemberFactory()
        GuildMembership.objects.record_app_join(guild, member)
        _membership, created, upgraded = GuildMembership.objects.record_app_join(guild, member)
        assert created is False
        assert upgraded is False

    def it_upgrades_an_existing_discord_row_to_app():
        # The data-loss case §4.5 closes: a standing reaction is promoted by an explicit join.
        guild, member = GuildFactory(), MemberFactory()
        GuildMembership.objects.record_discord_join(guild, member)
        membership, created, upgraded = GuildMembership.objects.record_app_join(guild, member)
        assert created is False
        assert upgraded is True
        membership.refresh_from_db()
        assert membership.source == GuildMembership.Source.APP


def describe_record_discord_join():
    def it_creates_a_source_discord_row():
        guild, member = GuildFactory(), MemberFactory()
        membership, created = GuildMembership.objects.record_discord_join(guild, member)
        assert created is True
        assert membership.source == GuildMembership.Source.DISCORD

    def it_never_downgrades_an_existing_app_row():
        guild, member = GuildFactory(), MemberFactory()
        GuildMembership.objects.record_app_join(guild, member)
        membership, created = GuildMembership.objects.record_discord_join(guild, member)
        assert created is False
        membership.refresh_from_db()
        assert membership.source == GuildMembership.Source.APP
