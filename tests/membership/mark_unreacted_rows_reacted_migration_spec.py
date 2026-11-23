"""Data-migration spec for membership 0291: rows marked un-reacted become reacted (#1320).

The migration only touches fields that exist at head, so its functions run here against the
current app registry instead of rewinding the schema with ``MigrationExecutor``.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from django.apps import apps

from membership.models import GuildMembership
from tests.membership.factories import GuildFactory, MemberFactory

_migration = import_module("membership.migrations.0291_mark_unreacted_rows_reacted")


def _reacted() -> dict[int, bool]:
    return dict(GuildMembership.objects.values_list("guild_id", "reacted"))


@pytest.mark.django_db
def describe_migration_0291_mark_unreacted_rows_reacted():
    def it_marks_only_the_rows_marked_no_discord_role_and_reverses():
        glass, wood = GuildFactory(name="Glass"), GuildFactory(name="Wood")
        member = MemberFactory()
        GuildMembership.objects.record_app_join(glass, member)
        GuildMembership.objects.record_app_join(wood, member)
        GuildMembership.objects.filter(guild=glass).update(no_discord_role=True)

        _migration.mark_reacted(apps, None)
        assert _reacted() == {glass.pk: True, wood.pk: False}

        _migration.unmark_reacted(apps, None)
        assert _reacted() == {glass.pk: False, wood.pk: False}
