"""Data-migration spec for membership 0290: memberships marked un-reacted are removed (#1319).

The migration only reads fields that exist at head, so its function runs here against the
current app registry instead of rewinding the schema with ``MigrationExecutor``.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from django.apps import apps

from membership.models import GuildMembership
from tests.membership.factories import GuildFactory, MemberFactory

_migration = import_module("membership.migrations.0290_remove_unreacted_guild_memberships")


@pytest.mark.django_db
def describe_migration_0290_remove_unreacted_guild_memberships():
    def it_removes_every_marked_membership_whatever_its_source_and_keeps_the_rest():
        glass, wood = GuildFactory(name="Glass"), GuildFactory(name="Wood")
        portal, reactor = MemberFactory(), MemberFactory()
        GuildMembership.objects.record_app_join(glass, portal)
        GuildMembership.objects.record_app_join(wood, portal)
        GuildMembership.objects.record_discord_join(glass, reactor)
        GuildMembership.objects.filter(guild=glass).update(no_discord_role=True)

        _migration.remove_marked(apps, None)

        assert list(GuildMembership.objects.values_list("member", "guild")) == [(portal.pk, wood.pk)]
