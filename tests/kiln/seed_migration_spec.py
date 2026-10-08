"""The seed migration for the clay and glaze lists runs both ways (#691)."""

from __future__ import annotations

from importlib import import_module

import pytest
from django.apps import apps

from kiln.models import ClayOption, GlazeOption
from tests.kiln.factories import KilnTicketFactory

pytestmark = pytest.mark.django_db

seed_migration = import_module("kiln.migrations.0002_seed_clay_and_glaze_lists")


def describe_seed_migration():
    def it_removes_only_unused_seeded_options_on_the_way_back():
        used = ClayOption.objects.get(name="G-Mix 6")
        KilnTicketFactory(clay=used)
        ClayOption.objects.create(name="Added By Crew")

        seed_migration.unseed(apps, None)

        assert set(ClayOption.objects.values_list("name", flat=True)) == {"G-Mix 6", "Added By Crew"}
        assert not GlazeOption.objects.filter(name__in=seed_migration.GLAZES).exists()

    def it_seeds_again_without_duplicates():
        seed_migration.unseed(apps, None)
        seed_migration.seed(apps, None)
        seed_migration.seed(apps, None)

        assert ClayOption.objects.filter(name__in=seed_migration.CLAYS).count() == len(seed_migration.CLAYS)
        assert GlazeOption.objects.filter(name__in=seed_migration.GLAZES).count() == len(seed_migration.GLAZES)
