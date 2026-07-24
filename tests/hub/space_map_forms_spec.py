"""BDD specs for the space map's forms — marker structure validation, the coordinate
payload's bounds, the request form's guards, and the reviewer decision's note rule."""

from __future__ import annotations

from decimal import Decimal

import pytest

from hub.forms import (
    MapHotspotEditForm,
    MapHotspotForm,
    MapHotspotPositionForm,
    SpaceRequestDecisionForm,
    SpaceRequestForm,
)
from membership.models import MapHotspot, Member, Space
from tests.membership.factories import (
    GuildFactory,
    MapHotspotFactory,
    MemberFactory,
    SpaceFactory,
    SpaceRequestFactory,
)


def _marker_data(**overrides):
    data = {
        "kind": MapHotspot.Kind.STUDIO,
        "shape": MapHotspot.Shape.REGION,
        "space": "",
        "label": "",
        "description": "",
        "sort_order": "0",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
def describe_MapHotspotForm():
    def it_requires_a_space_for_a_studio():
        form = MapHotspotForm(_marker_data())
        assert not form.is_valid()
        assert "Pick the space this marker stands for." in form.errors["space"]

    def it_requires_a_space_for_a_cubby():
        form = MapHotspotForm(_marker_data(kind=MapHotspot.Kind.CUBBY))
        assert not form.is_valid()
        assert "space" in form.errors

    def it_requires_a_label_for_a_facility():
        form = MapHotspotForm(_marker_data(kind=MapHotspot.Kind.FACILITY))
        assert not form.is_valid()
        assert "Give this marker a label so members know what it is." in form.errors["label"]

    def it_accepts_a_labelled_facility():
        form = MapHotspotForm(_marker_data(kind=MapHotspot.Kind.FACILITY, label="Wood Shop"))
        assert form.is_valid()

    def it_gives_a_new_region_a_starter_box_rather_than_erroring():
        # x/y/w/h are not on this form (the drag endpoint owns them), so a region with no
        # box has to be reconciled here or the model's CheckConstraint rejects the save.
        form = MapHotspotForm(_marker_data(space=str(SpaceFactory().pk)))
        assert form.is_valid()
        assert form.instance.w == MapHotspotForm.DEFAULT_REGION_W
        assert form.instance.h == MapHotspotForm.DEFAULT_REGION_H

    def it_keeps_an_existing_regions_box():
        hotspot = MapHotspotFactory(w=Decimal("42.00"), h=Decimal("8.00"))
        form = MapHotspotForm(_marker_data(space=str(hotspot.space_id)), instance=hotspot)
        assert form.is_valid()
        assert form.instance.w == Decimal("42.00")

    def it_leaves_dimensions_alone_when_the_shape_itself_is_invalid():
        # An unrecognised shape already fails the field; don't compound it by silently
        # rewriting the box on the way out of clean().
        hotspot = MapHotspotFactory(w=Decimal("42.00"))
        form = MapHotspotForm(_marker_data(space=str(hotspot.space_id), shape="blob"), instance=hotspot)
        assert not form.is_valid()
        assert form.instance.w == Decimal("42.00")

    def it_clears_the_box_when_a_region_becomes_a_pin():
        hotspot = MapHotspotFactory()
        form = MapHotspotForm(_marker_data(space=str(hotspot.space_id), shape=MapHotspot.Shape.PIN), instance=hotspot)
        assert form.is_valid()
        assert form.instance.w is None
        assert form.instance.h is None

    def it_links_a_facility_marker_to_a_guild():
        guild = GuildFactory(name="Wood Guild")
        form = MapHotspotForm(_marker_data(kind=MapHotspot.Kind.FACILITY, label="Wood Shop", guild=str(guild.pk)))
        assert form.is_valid()
        assert form.save(commit=False).guild == guild


@pytest.mark.django_db
def describe_MapHotspotPositionForm():
    def it_accepts_a_region_inside_the_plan():
        form = MapHotspotPositionForm({"x": "10", "y": "10", "w": "20", "h": "20"}, hotspot=MapHotspotFactory())
        assert form.is_valid()

    def it_rejects_a_region_that_overflows_the_right_edge():
        form = MapHotspotPositionForm({"x": "90", "y": "10", "w": "20", "h": "20"}, hotspot=MapHotspotFactory())
        assert not form.is_valid()
        assert "runs off the edge" in form.error_message()

    def it_rejects_a_region_that_overflows_the_bottom_edge():
        form = MapHotspotPositionForm({"x": "10", "y": "90", "w": "20", "h": "20"}, hotspot=MapHotspotFactory())
        assert not form.is_valid()

    def it_rejects_a_coordinate_beyond_the_image():
        form = MapHotspotPositionForm({"x": "101", "y": "10", "w": "5", "h": "5"}, hotspot=MapHotspotFactory())
        assert not form.is_valid()

    def it_rejects_a_negative_coordinate():
        form = MapHotspotPositionForm({"x": "-1", "y": "10", "w": "5", "h": "5"}, hotspot=MapHotspotFactory())
        assert not form.is_valid()

    def it_rejects_a_region_with_no_box():
        form = MapHotspotPositionForm({"x": "10", "y": "10"}, hotspot=MapHotspotFactory())
        assert not form.is_valid()
        assert "needs a width and height" in form.error_message()

    def it_drops_a_box_posted_for_a_pin():
        pin = MapHotspotFactory(shape=MapHotspot.Shape.PIN, w=None, h=None)
        form = MapHotspotPositionForm({"x": "10", "y": "10", "w": "5", "h": "5"}, hotspot=pin)
        assert form.is_valid()
        form.apply()
        pin.refresh_from_db()
        assert (pin.w, pin.h) == (None, None)

    def it_writes_only_the_coordinate_columns():
        hotspot = MapHotspotFactory(label="Untouched")
        hotspot.label = "Not saved by apply()"
        form = MapHotspotPositionForm({"x": "1", "y": "2", "w": "3", "h": "4"}, hotspot=hotspot)
        assert form.is_valid()
        form.apply()
        hotspot.refresh_from_db()
        assert hotspot.x == Decimal("1.00")
        assert hotspot.label == "Untouched"


@pytest.mark.django_db
def describe_SpaceRequestForm():
    def it_accepts_an_active_members_ask():
        member = MemberFactory(status=Member.Status.ACTIVE)
        form = SpaceRequestForm({"message": "For pottery."}, hotspot=MapHotspotFactory(), member=member)
        assert form.is_valid()

    def it_names_the_admins_in_the_hint_for_a_guild_shelf():
        # The request flow was lightened: every ask now routes to the makerspace admins,
        # a guild-owned shelf included, so the hint says the same for all of them.
        guild = GuildFactory(name="Clay Guild")
        hotspot = MapHotspotFactory(kind=MapHotspot.Kind.CUBBY, space=SpaceFactory(sublet_guild=guild))
        form = SpaceRequestForm(hotspot=hotspot, member=MemberFactory())
        assert form.fields["message"].help_text == "This goes to the makerspace admins."

    def it_names_the_admins_in_the_hint_for_a_studio():
        form = SpaceRequestForm(hotspot=MapHotspotFactory(), member=MemberFactory())
        assert form.fields["message"].help_text == "This goes to the makerspace admins."

    def it_rejects_a_duplicate_pending_ask():
        member = MemberFactory(status=Member.Status.ACTIVE)
        hotspot = MapHotspotFactory()
        SpaceRequestFactory(requester=member, space=hotspot.space)
        form = SpaceRequestForm({"message": ""}, hotspot=hotspot, member=member)
        assert not form.is_valid()
        assert "You already have a pending request for this space." in form.non_field_errors()

    def it_rejects_a_member_without_an_active_membership():
        member = MemberFactory(status=Member.Status.FORMER)
        form = SpaceRequestForm({"message": ""}, hotspot=MapHotspotFactory(), member=member)
        assert not form.is_valid()
        assert "Requesting a space needs an active membership." in form.non_field_errors()

    def it_rejects_a_space_that_is_not_available():
        hotspot = MapHotspotFactory(space=SpaceFactory(status=Space.Status.MAINTENANCE))
        form = SpaceRequestForm({"message": ""}, hotspot=hotspot, member=MemberFactory())
        assert not form.is_valid()

    def it_saves_a_cubby_ask_with_its_provenance():
        guild = GuildFactory()
        hotspot = MapHotspotFactory(kind=MapHotspot.Kind.CUBBY, space=SpaceFactory(sublet_guild=guild))
        member = MemberFactory(status=Member.Status.ACTIVE)
        form = SpaceRequestForm({"message": "Glaze storage."}, hotspot=hotspot, member=member)
        assert form.is_valid()
        request = form.save()
        assert request.hotspot == hotspot
        assert request.message == "Glaze storage."


def describe_SpaceRequestDecisionForm():
    def it_approves_without_a_note():
        assert SpaceRequestDecisionForm({"decision": "approve", "notes": ""}).is_valid()

    def it_requires_a_note_to_decline():
        form = SpaceRequestDecisionForm({"decision": "decline", "notes": "   "})
        assert not form.is_valid()
        assert "Add a note so the member knows why." in form.errors["notes"]

    def it_accepts_a_decline_with_a_note():
        assert SpaceRequestDecisionForm({"decision": "decline", "notes": "Taken."}).is_valid()

    def it_has_no_changes_requested_outcome():
        # A space request has no editable body — "not this one" is a decline with a note.
        assert not SpaceRequestDecisionForm({"decision": "changes", "notes": "Fix it"}).is_valid()


@pytest.mark.django_db
def describe_MapHotspotEditForm():
    """The click-a-tile editor form: the same structural validation plus a status field that
    prefills from the linked space and is ignored for a marker with no space."""

    def _edit_data(**overrides):
        data = {
            "kind": MapHotspot.Kind.STUDIO,
            "shape": MapHotspot.Shape.REGION,
            "space": "",
            "label": "",
            "description": "",
            "guild": "",
            "status": "",
        }
        data.update(overrides)
        return data

    def it_prefills_status_from_the_linked_space():
        hotspot = MapHotspotFactory(space=SpaceFactory(status=Space.Status.OCCUPIED))
        form = MapHotspotEditForm(instance=hotspot)
        assert form.fields["status"].initial == Space.Status.OCCUPIED

    def it_has_no_status_for_a_marker_with_no_space():
        hotspot = MapHotspotFactory(kind=MapHotspot.Kind.FACILITY, space=None, label="Wood Shop")
        form = MapHotspotEditForm(instance=hotspot)
        assert form.fields["status"].initial is None

    def it_saves_the_info_fields():
        guild = GuildFactory()
        hotspot = MapHotspotFactory()
        form = MapHotspotEditForm(
            _edit_data(space=str(hotspot.space_id), description="Kiln room.", guild=str(guild.pk)),
            instance=hotspot,
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.description == "Kiln room."
        assert saved.guild_id == guild.pk

    def it_still_requires_a_space_for_a_studio():
        hotspot = MapHotspotFactory()
        form = MapHotspotEditForm(_edit_data(kind=MapHotspot.Kind.STUDIO, space=""), instance=hotspot)
        assert not form.is_valid()
        assert "space" in form.errors

    def it_accepts_a_blank_status():
        hotspot = MapHotspotFactory()
        form = MapHotspotEditForm(_edit_data(space=str(hotspot.space_id), status=""), instance=hotspot)
        assert form.is_valid(), form.errors
