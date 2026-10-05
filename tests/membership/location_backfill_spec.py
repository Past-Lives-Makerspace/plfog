"""Specs for the Location backfill planner and writer (#616, part 2): ``membership.services.location_backfill``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.db import connection

from classes.factories import CategoryFactory, ClassOfferingFactory
from classes.models import ClassOffering
from membership.models import EXAMPLE_GUILD_SLUG, CommunityEvent, Equipment, Guild, Location, OrientationType
from membership.services.location_backfill import (
    GUILD_PRIMARY_LOCATION,
    LOCATIONS,
    Assignment,
    BackfillPlan,
    apply_backfill,
    plan_backfill,
)
from tests.membership.factories import (
    CommunityEventFactory,
    EquipmentFactory,
    GuildFactory,
    LocationFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def guilds() -> dict[str, Guild]:
    """Every guild the seeded locations and primary locations name, plus Writers (which has no primary)."""
    names = {spec.guild_name for spec in LOCATIONS if spec.guild_name} | set(GUILD_PRIMARY_LOCATION)
    names.add("Writers Guild")
    return {name: GuildFactory(name=name) for name in sorted(names)}


@pytest.fixture
def example_guild() -> Guild:
    return GuildFactory(name="Cartographers Guild", slug=EXAMPLE_GUILD_SLUG)


def _reject_writes(execute: Callable[..., Any], sql: str, params: Any, many: bool, context: Any) -> Any:
    """Make any statement other than a SELECT raise, as a read only production connection would."""
    if not sql.lstrip().upper().startswith("SELECT"):
        raise AssertionError(f"write attempted: {sql}")
    return execute(sql, params, many, context)


def _class(title: str, guild: Guild | None, **kwargs: Any) -> ClassOffering:
    return ClassOfferingFactory(title=title, category=CategoryFactory(guild=guild), gallery=0, image="", **kwargs)


def _assignment(plan: BackfillPlan, kind: str, pk: int) -> Assignment:
    [found] = [a for a in plan.assignments if a.kind == kind and a.pk == pk]
    return found


def _proposed(plan: BackfillPlan, kind: str, pk: int) -> str | None:
    return _assignment(plan, kind, pk).location


def describe_plan_backfill():
    def it_issues_only_selects_so_it_runs_on_a_read_only_connection(guilds, example_guild):
        LocationFactory(name="Front Studio")
        _class("Glassblowing", guilds["Glass Guild"])
        _class("Mapmaking", example_guild)
        OrientationTypeFactory(equipment_owned=True, equipment__name="CNC Machine")
        EquipmentFactory(name="Lathe", guild=guilds["Woodworking Guild"])
        CommunityEventFactory(community=True, location="Woodshop")
        with connection.execute_wrapper(_reject_writes):
            plan = plan_backfill()
        assert len(plan.proposed) == 5
        assert Location.objects.count() == 1

    def it_proves_the_read_only_guard_rejects_a_write():
        with connection.execute_wrapper(_reject_writes), pytest.raises(AssertionError, match="write attempted"):
            Location.objects.create(name="Nope")

    def describe_locations():
        def it_creates_each_seeded_location_with_its_guild_and_note(guilds):
            plan = plan_backfill()
            by_name = {c.name: c for c in plan.locations}
            assert by_name["Hot Glass Room"].guild_name == "Glass Guild"
            assert by_name["Common Area"].guild_name is None
            assert by_name["Common Area"].note == "Upstairs next to the Kitchen"
            assert plan.links == [("Front Studio", "Events Stage")]
            assert plan.warnings == []

        def it_warns_and_leaves_the_guild_blank_when_no_guild_has_that_name():
            GuildFactory(name="Glass Guild")
            plan = plan_backfill()
            by_name = {c.name: c for c in plan.locations}
            assert by_name["Hot Glass Room"].guild_name == "Glass Guild"
            assert by_name["Print Studio"].guild_name is None
            assert 'No guild named "Printmaking Guild"; Print Studio is left without a guild.' in plan.warnings

        def it_fills_a_blank_guild_and_note_on_an_existing_location(guilds):
            LocationFactory(name="Kitchen", guild=None)
            LocationFactory(name="Common Area", note="")
            by_name = {c.name: c for c in plan_backfill().locations}
            assert (by_name["Kitchen"].action, by_name["Kitchen"].fills) == ("update", ("guild",))
            assert (by_name["Common Area"].action, by_name["Common Area"].fills) == ("update", ("note",))

        def it_keeps_a_location_that_already_matches(guilds):
            LocationFactory(name="Kitchen", guild=guilds["Food Independence Guild"])
            LocationFactory(name="Common Area", note="By the fridge")
            by_name = {c.name: c for c in plan_backfill().locations}
            assert by_name["Kitchen"].action == "keep"
            assert by_name["Common Area"].action == "keep"

        def it_keeps_and_reports_a_location_that_belongs_to_another_guild(guilds):
            LocationFactory(name="Kitchen", guild=guilds["Gardeners Guild"])
            plan = plan_backfill()
            assert {c.name: c for c in plan.locations}["Kitchen"].action == "keep"
            assert (
                "Kitchen already belongs to Gardeners Guild, not Food Independence Guild; it is kept." in plan.warnings
            )

        def it_plans_the_link_when_front_studio_exists_unlinked():
            LocationFactory(name="Front Studio")
            LocationFactory(name="Events Stage")
            assert plan_backfill().links == [("Front Studio", "Events Stage")]

        def it_plans_no_link_when_the_two_already_share_space():
            front = LocationFactory(name="Front Studio")
            front.shares_space_with.add(LocationFactory(name="Events Stage"))
            assert plan_backfill().links == []

    def describe_classes():
        def it_matches_title_keywords_first_in_order(guilds):
            stained = _class("Stained Glass Lampwork Sampler", guilds["Jewelry Guild"])
            lampwork = _class("Intro to LAMPWORK beads", guilds["Jewelry Guild"])
            forging = _class("Forging a Knife", guilds["Woodworking Guild"])
            figure = _class("Open Figure Drawing", None)
            plan = plan_backfill()
            assert _proposed(plan, "class", stained.pk) == "Cold Glass Room"
            assert _assignment(plan, "class", lampwork.pk).rule == 'title keyword "lampwork"'
            assert _proposed(plan, "class", lampwork.pk) == "Hot Glass Room"
            assert _proposed(plan, "class", forging.pk) == "Metal Shop"
            assert _proposed(plan, "class", figure.pk) == "Front Studio"

        def it_matches_a_keyword_only_at_the_start_of_a_word(guilds):
            vestige = _class("Vestige Collage", guilds["Visual Arts/Gallery Guild"])
            assert _proposed(plan_backfill(), "class", vestige.pk) == "Front Studio"

        def it_falls_back_to_the_category_guilds_primary_location(guilds):
            offering = _class("Wheel Throwing", guilds["Ceramics Guild"])
            plan = plan_backfill()
            assert _proposed(plan, "class", offering.pk) == "Ceramics Studio"
            assert _assignment(plan, "class", offering.pk).rule == "category guild Ceramics Guild"

        def it_sends_a_class_in_a_guildless_category_to_the_common_area():
            offering = _class("Zine Night", None)
            assert _proposed(plan_backfill(), "class", offering.pk) == "Common Area"

        def it_leaves_a_class_blank_when_its_guild_has_no_primary_location(guilds):
            offering = _class("Poetry Workshop", guilds["Writers Guild"])
            found = _assignment(plan_backfill(), "class", offering.pk)
            assert (found.location, found.rule) == (None, "Writers Guild has no primary location")

        def it_sends_a_coworking_class_to_the_common_area_ahead_of_its_category(guilds):
            offering = _class("Small Business Coworking Session", guilds["Metalworkers Guild"])
            found = _assignment(plan_backfill(), "class", offering.pk)
            assert (found.location, found.rule) == ("Common Area", 'title keyword "coworking"')

        def it_leaves_a_class_listed_by_id_blank_with_its_reason(guilds):
            offering = _class("Understanding the Basics of Intellectual Property", guilds["Tech Guild"], pk=672)
            found = _assignment(plan_backfill(), "class", offering.pk)
            assert (found.location, found.rule) == (None, "online class")

        def it_skips_demo_classes(guilds):
            by_slug = _class("Glassblowing", guilds["Glass Guild"], slug="demo-glassblowing")
            by_title = _class("[DEMO] Glassblowing", guilds["Glass Guild"])
            plan = plan_backfill()
            assert _assignment(plan, "class", by_slug.pk).rule == "demo class, skipped"
            assert _proposed(plan, "class", by_title.pk) is None

        def it_skips_classes_of_the_example_guild(example_guild):
            offering = _class("Mapmaking", example_guild)
            assert _assignment(plan_backfill(), "class", offering.pk).rule == "example guild, skipped"

        def it_leaves_out_a_class_whose_location_is_set():
            offering = _class("Glassblowing", None, area=LocationFactory(name="Somewhere"))
            assert not [a for a in plan_backfill().assignments if a.kind == "class" and a.pk == offering.pk]

    def describe_equipment():
        def it_sends_named_equipment_to_its_location(guilds):
            laser = EquipmentFactory(name="Laser Engraver Mira 9", guild=guilds["Woodworking Guild"])
            cnc = EquipmentFactory(name="cnc machine ")
            plan = plan_backfill()
            assert _proposed(plan, "equipment", laser.pk) == "Tech Area"
            assert _proposed(plan, "equipment", cnc.pk) == "CNC Area"

        def it_falls_back_to_its_guilds_primary_location(guilds):
            lathe = EquipmentFactory(name="Lathe", guild=guilds["Woodworking Guild"])
            assert _proposed(plan_backfill(), "equipment", lathe.pk) == "Woodshop"

        def it_leaves_standalone_equipment_blank():
            drill = EquipmentFactory(name="Drill")
            assert _assignment(plan_backfill(), "equipment", drill.pk).rule == "equipment has no guild"

        def it_skips_example_guild_equipment(example_guild):
            plotter = EquipmentFactory(name="CNC Machine", guild=example_guild)
            assert _assignment(plan_backfill(), "equipment", plotter.pk).rule == "example guild, skipped"

    def describe_orientation_types():
        def it_takes_the_owning_equipments_set_location():
            area = LocationFactory(name="Back Room")
            orientation = OrientationTypeFactory(equipment_owned=True, equipment__area=area)
            found = _assignment(plan_backfill(), "orientation type", orientation.pk)
            assert found.location == "Back Room"

        def it_takes_the_owning_equipments_planned_location():
            orientation = OrientationTypeFactory(equipment_owned=True, equipment__name="CNC Machine")
            found = _assignment(plan_backfill(), "orientation type", orientation.pk)
            assert (found.location, found.rule) == ("CNC Area", 'equipment CNC Machine: equipment name "CNC Machine"')

        def it_falls_through_to_the_default_location_when_the_equipment_has_none():
            orientation = OrientationTypeFactory(
                equipment_owned=True, equipment__name="Kiln", default_location="Hot Glass Room"
            )
            assert _proposed(plan_backfill(), "orientation type", orientation.pk) == "Hot Glass Room"

        def it_matches_default_location_keywords(guilds):
            cases = {
                "Art Framing studio": "Art Framing Studio",
                "Gallery/Stage": "Events Stage",
                "Cold Glass Room": "Cold Glass Room",
                "Woodshop": "Woodshop",
            }
            built = {
                text: OrientationTypeFactory(guild=guilds["Writers Guild"], name=text, default_location=text)
                for text in cases
            }
            plan = plan_backfill()
            for text, location in cases.items():
                assert _proposed(plan, "orientation type", built[text].pk) == location

        def it_falls_back_to_the_guilds_primary_location(guilds):
            orientation = OrientationTypeFactory(guild=guilds["Printmaking Guild"], default_location="Print Studio")
            found = _assignment(plan_backfill(), "orientation type", orientation.pk)
            assert (found.location, found.rule) == ("Print Studio", "orientation guild Printmaking Guild")

        def it_skips_example_guild_types_whether_guild_or_equipment_owned(example_guild):
            by_guild = OrientationTypeFactory(guild=example_guild, default_location="Woodshop")
            by_equipment = OrientationTypeFactory(equipment_owned=True, equipment__guild=example_guild)
            plan = plan_backfill()
            assert _assignment(plan, "orientation type", by_guild.pk).rule == "example guild, skipped"
            assert _assignment(plan, "orientation type", by_equipment.pk).rule == "example guild, skipped"

    def describe_events():
        @pytest.mark.parametrize(
            ("text", "location"),
            [
                ("Tech Guild", "Tech Area"),
                ("In Visual Arts during Parallel Play", "Front Studio"),
                ("Past Lives Gallery", "Front Studio"),
                ("Ceramics Studio Room at Past Lives", "Ceramics Studio"),
                ("Woodshop", "Woodshop"),
                ("leather guild area", "Leather Area"),
                ("Printmaking Guild Space", "Print Studio"),
            ],
        )
        def it_matches_location_text(text, location):
            event = CommunityEventFactory(community=True, location=text)
            assert _proposed(plan_backfill(), "event", event.pk) == location

        def it_falls_back_to_the_events_guild(guilds):
            event = CommunityEventFactory(guild=guilds["Gardeners Guild"], location="Out back")
            assert _proposed(plan_backfill(), "event", event.pk) == "Garden"

        def it_leaves_a_guildless_event_without_matching_text_blank():
            event = CommunityEventFactory(community=True, location="")
            assert _assignment(plan_backfill(), "event", event.pk).rule == "event has no guild"

        def it_skips_example_guild_events(example_guild):
            event = CommunityEventFactory(guild=example_guild, location="Woodshop")
            assert _assignment(plan_backfill(), "event", event.pk).rule == "example guild, skipped"

    def it_counts_records_per_location_most_first():
        _class("Glassblowing", None)
        _class("Fused Glass", None)
        _class("Welding", None)
        plan = plan_backfill()
        assert plan.counts_by_location() == [("Hot Glass Room", 2), ("Metal Shop", 1)]
        assert all(a.location is None for a in plan.blank)
        assert all(a.location is not None for a in plan.proposed)


def describe_apply_backfill():
    def it_creates_locations_links_them_and_assigns_blank_records(guilds):
        offering = _class("Glassblowing", guilds["Glass Guild"])
        event = CommunityEventFactory(community=True, location="Woodshop")
        lathe = EquipmentFactory(name="Lathe", guild=guilds["Woodworking Guild"])
        orientation = OrientationTypeFactory(guild=guilds["Ceramics Guild"])

        result = apply_backfill(plan_backfill())

        assert (result.created, result.updated, result.linked, result.assigned) == (len(LOCATIONS), 0, 1, 4)
        front = Location.objects.get(name="Front Studio")
        assert list(front.shares_space_with.values_list("name", flat=True)) == ["Events Stage"]
        assert list(Location.objects.get(name="Events Stage").shares_space_with.all()) == [front]
        assert Location.objects.get(name="Hot Glass Room").guild == guilds["Glass Guild"]
        assert Location.objects.get(name="Common Area").note == "Upstairs next to the Kitchen"
        assert ClassOffering.objects.get(pk=offering.pk).area.name == "Hot Glass Room"
        assert CommunityEvent.objects.get(pk=event.pk).area.name == "Woodshop"
        assert Equipment.objects.get(pk=lathe.pk).area.name == "Woodshop"
        assert OrientationType.objects.get(pk=orientation.pk).area.name == "Ceramics Studio"

    def it_fills_blanks_on_existing_locations_and_keeps_set_values(guilds):
        LocationFactory(name="Kitchen", guild=None)
        LocationFactory(name="Common Area", note="")
        LocationFactory(name="Garden", guild=guilds["Writers Guild"])
        result = apply_backfill(plan_backfill())
        assert (result.created, result.updated) == (len(LOCATIONS) - 3, 2)
        assert Location.objects.get(name="Kitchen").guild == guilds["Food Independence Guild"]
        assert Location.objects.get(name="Common Area").note == "Upstairs next to the Kitchen"
        assert Location.objects.get(name="Garden").guild == guilds["Writers Guild"]

    def it_is_idempotent_even_with_a_stale_plan(guilds):
        _class("Glassblowing", guilds["Glass Guild"])
        plan = plan_backfill()
        apply_backfill(plan)
        again = apply_backfill(plan)
        assert (again.created, again.updated, again.linked, again.assigned) == (0, 0, 0, 0)
        fresh = plan_backfill()
        assert {c.action for c in fresh.locations} == {"keep"}
        assert fresh.links == []
        assert [a for a in fresh.assignments if a.kind == "class"] == []
        assert Location.objects.count() == len(LOCATIONS)

    def it_never_overwrites_a_location_set_after_the_plan(guilds):
        offering = _class("Glassblowing", guilds["Glass Guild"])
        plan = plan_backfill()
        chosen = LocationFactory(name="Chosen By Hand")
        ClassOffering.objects.filter(pk=offering.pk).update(area=chosen)
        result = apply_backfill(plan)
        assert result.assigned == len(plan.proposed) - 1
        assert ClassOffering.objects.get(pk=offering.pk).area == chosen

    def it_writes_nothing_for_a_record_left_blank(guilds):
        offering = _class("Poetry", guilds["Writers Guild"])
        apply_backfill(plan_backfill())
        assert ClassOffering.objects.get(pk=offering.pk).area is None
