"""Specs for the backfill_locations management command (#616, part 2)."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from classes.factories import CategoryFactory, ClassOfferingFactory
from classes.models import ClassOffering
from membership.models import Location
from membership.services.location_backfill import LOCATIONS
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

LONG_TITLE = "Glassblowing for Absolute Beginners Who Have Never Touched a Pipe"


def _run(*args: str) -> str:
    out = StringIO()
    call_command("backfill_locations", *args, stdout=out)
    return out.getvalue()


@pytest.fixture
def offering() -> ClassOffering:
    return ClassOfferingFactory(title=LONG_TITLE, category=CategoryFactory(guild=None), gallery=0, image="")


def describe_backfill_locations():
    def it_prints_the_plan_and_writes_nothing_by_default(offering):
        poetry = ClassOfferingFactory(
            title="Poetry", category=CategoryFactory(guild=GuildFactory(name="Writers Guild")), gallery=0, image=""
        )
        out = _run()
        assert '  create Common Area [no guild, note "Upstairs next to the Kitchen"]' in out
        assert "  link   Front Studio shares space with Events Stage" in out
        assert "Proposed assignments (1)" in out
        assert f"{LONG_TITLE[:45]}..." in out
        assert 'Hot Glass Room         title keyword "glassblowing"' in out
        assert "Counts per location\n  Hot Glass Room           1\n" in out
        assert "Left blank (1)" in out
        assert f"{poetry.pk}" in out and "Writers Guild has no primary location" in out
        assert 'Warning: No guild named "Glass Guild"; Hot Glass Room is left without a guild.' in out
        assert out.rstrip().endswith("Dry run: nothing written. Run with --apply to write this plan.")
        assert Location.objects.count() == 0
        assert ClassOffering.objects.get(pk=offering.pk).area is None

    def it_writes_the_plan_with_apply(offering):
        out = _run("--apply")
        assert f"Applied: {len(LOCATIONS)} locations created, 0 filled in, 1 links added, 1 records assigned." in out
        assert "Dry run" not in out
        assert ClassOffering.objects.get(pk=offering.pk).area.name == "Hot Glass Room"

    def it_is_safe_to_run_twice(offering):
        _run("--apply")
        out = _run("--apply")
        assert "  keep   Hot Glass Room [no guild]" in out
        assert "Proposed assignments (0)" in out
        assert "Applied: 0 locations created, 0 filled in, 0 links added, 0 records assigned." in out
        assert Location.objects.count() == len(LOCATIONS)

    def it_names_the_fields_it_fills_on_an_existing_location():
        Location.objects.create(name="Common Area")
        assert '  update Common Area [no guild, note "Upstairs next to the Kitchen"] (fill note)' in _run()
