"""Migration spec for classes 0094: every active guild without a Class Type gets one.

Guilds are made with ``bulk_create`` so ``Guild.save`` does not create their Class Types first,
which is the state production was in. Uses Django's ``MigrationExecutor`` (the pattern of
backfill_class_instructors_spec); each test restores the schema to head in a ``finally``.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from classes.models import Category
from membership.models import Guild

_APP = "classes"
_BEFORE = "0093_split_multi_date_single_classes"
_AFTER = "0094_guild_class_types"
_HEAD = MigrationLoader(None).graph.leaf_nodes(_APP)[0][1]


def _migrate(target: str) -> None:
    MigrationExecutor(connection).migrate([(_APP, target)])


def _guild(name: str, slug: str, **fields: object) -> Guild:
    return Guild.all_objects.bulk_create([Guild(name=name, slug=slug, **fields)])[0]


@pytest.mark.django_db(transaction=True)
def describe_migration_0094_guild_class_types():
    def it_gives_each_active_guild_without_one_a_class_type():
        try:
            _migrate(_BEFORE)
            printmaking = _guild("Printmaking Guild", "printmaking-guild")
            outreach = _guild("Prison Outreach Guild", "prison-outreach-guild")
            wood = _guild("Wood Guild", "wood-guild")
            woodworking = Category.objects.create(name="Woodworking", slug="woodworking", guild=wood)
            _guild("Cartographers Guild", "cartographers-guild", is_active=False)
            _guild("Gone Guild", "gone-guild", deleted_at=timezone.now())

            _migrate(_AFTER)

            assert Category.objects.get(guild=printmaking).name == "Printmaking"
            assert Category.objects.get(guild=outreach).slug == "prison-outreach"
            assert list(Category.objects.filter(guild=wood)) == [woodworking]
            assert not Category.objects.filter(name__in=["Cartographers", "Gone"]).exists()
        finally:
            _migrate(_HEAD)

    def it_links_an_unlinked_match_and_skips_one_another_guild_owns():
        try:
            _migrate(_BEFORE)
            glass = _guild("Glass Guild", "glass-guild")
            unlinked = Category.objects.create(name="Glass", slug="glass")
            owner = _guild("Metal", "metal")
            owned = Category.objects.create(name="Metal", slug="metal", guild=owner)
            other = _guild("Metal Guild", "metal-guild")

            _migrate(_AFTER)

            unlinked.refresh_from_db()
            owned.refresh_from_db()
            assert unlinked.guild == glass
            assert owned.guild == owner
            assert not Category.objects.filter(guild=other).exists()
        finally:
            _migrate(_HEAD)

    def it_reverses_without_deleting_anything():
        try:
            _migrate(_BEFORE)
            guild = _guild("Printmaking Guild", "printmaking-guild")
            _migrate(_AFTER)
            _migrate(_BEFORE)

            assert Category.objects.filter(guild=guild).exists()
        finally:
            _migrate(_HEAD)
