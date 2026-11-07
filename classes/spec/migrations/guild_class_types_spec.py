"""Migration spec for classes 0094: every active guild without a Class Type gets one.

Guilds are made with ``bulk_create`` so ``Guild.save`` does not create their Class Types first,
which is the state production was in. Uses Django's ``MigrationExecutor`` (the pattern of
backfill_class_instructors_spec); each test restores the schema to head in a ``finally``. Rows are
read and written through the models of the applied migrations, never today's: rolling classes back
to 0093 also unapplies membership 0259 onward, so today's ``Guild`` names columns not there yet.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from classes.factories import ClassOfferingFactory
from classes.spec.migrations._head import migrate_all_to_head

_APP = "classes"
_BEFORE = "0093_split_multi_date_single_classes"
_AFTER = "0094_guild_class_types"


def _migrate(target: str) -> None:
    MigrationExecutor(connection).migrate([(_APP, target)])


def _model(app: str, name: str) -> Any:
    """``app.name`` as the migrations applied right now shape it."""
    executor = MigrationExecutor(connection)
    return executor.loader.project_state(list(executor.loader.applied_migrations)).apps.get_model(app, name)


def _category() -> Any:
    return _model("classes", "Category")._base_manager


def _guild(name: str, slug: str, **fields: object) -> int:
    guild = _model("membership", "Guild")
    return guild._base_manager.bulk_create([guild(name=name, slug=slug, **fields)])[0].pk


@pytest.mark.django_db(transaction=True)
def describe_migration_0094_guild_class_types():
    def it_gives_each_active_guild_without_one_a_class_type():
        try:
            _migrate(_BEFORE)
            printmaking = _guild("Printmaking Guild", "printmaking-guild")
            outreach = _guild("Prison Outreach Guild", "prison-outreach-guild")
            wood = _guild("Wood Guild", "wood-guild")
            woodworking = _category().create(name="Woodworking", slug="woodworking", guild_id=wood).pk
            _guild("Cartographers Guild", "cartographers-guild", is_active=False)
            _guild("Gone Guild", "gone-guild", deleted_at=timezone.now())

            _migrate(_AFTER)

            assert _category().get(guild_id=printmaking).name == "Printmaking"
            assert _category().get(guild_id=outreach).slug == "prison-outreach"
            assert list(_category().filter(guild_id=wood).values_list("pk", flat=True)) == [woodworking]
            assert not _category().filter(name__in=["Cartographers", "Gone"]).exists()
        finally:
            migrate_all_to_head()

    def it_links_an_unlinked_match_and_skips_one_another_guild_owns():
        try:
            _migrate(_BEFORE)
            glass = _guild("Glass Guild", "glass-guild")
            unlinked = _category().create(name="Glass", slug="glass").pk
            owner = _guild("Metal", "metal")
            owned = _category().create(name="Metal", slug="metal", guild_id=owner).pk
            other = _guild("Metal Guild", "metal-guild")

            _migrate(_AFTER)

            assert _category().get(pk=unlinked).guild_id == glass
            assert _category().get(pk=owned).guild_id == owner
            assert not _category().filter(guild_id=other).exists()
        finally:
            migrate_all_to_head()

    def it_reverses_by_deleting_only_the_empty_class_types_it_made():
        try:
            # Built at head (it makes an instructor, a Member); it takes the migration's class type
            # below by a plain update, which writes no Member.
            offering = ClassOfferingFactory()
            _migrate(_BEFORE)
            weaving = _guild("Weaving Guild", "weaving-guild")
            kept = _category().create(name="Weaving", slug="weaving", guild_id=weaving, icon_svg="<svg></svg>").pk
            empty = _guild("Printmaking Guild", "printmaking-guild")
            used = _guild("Prison Outreach Guild", "prison-outreach-guild")
            _migrate(_AFTER)
            _model("classes", "ClassOffering")._base_manager.filter(pk=offering.pk).update(
                category=_category().get(guild_id=used)
            )

            _migrate(_BEFORE)

            assert not _category().filter(guild_id=empty).exists()
            assert _category().filter(guild_id=used).exists()
            assert _category().filter(pk=kept).exists()
        finally:
            migrate_all_to_head()
