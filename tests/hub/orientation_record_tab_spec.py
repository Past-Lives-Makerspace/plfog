"""BDD specs for Record Orientation on the Orientations page's Bookings tab (#630).

Guild leadership and equipment managers record an orientation done outside the app, for any
active member, on the types they run, and remove a record they could have made. The button
and the modal show only in the staff view; the type choices follow the viewer's scope; a
crafted POST for another owner's type, a duplicate, a future date or an unknown member is
refused and writes nothing; recording is silent and counts at once on the equipment pages.
Member names are factory strings no changelog could contain (STANDARDS.md §8).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import SiteActivity
from hub.forms import OrientationRecordForm
from membership.models import AdminCapability, GuildStaffMembership, Member, OrientationRecord, OrientationType
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationRecordFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

PAGE = "/orientations/"
RECORD_URL = "/orientations/manage/records/"
LANDING = "/orientations/?view=bookings&oriented=yes"


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER, name: str = "") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.full_legal_name = name or f"Viewer {username}"
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["fog_role", "full_legal_name", "status"])
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return member


def _tab(client: Client, **params: str) -> str:
    response = client.get(PAGE, {"view": "bookings", **params})
    assert response.status_code == 200
    return response.content.decode()


def _post(type_label: str, member_label: str, **extra: str) -> dict[str, str]:
    return {
        "member": member_label,
        "orientation": type_label,
        "completed_on": timezone.localdate().isoformat(),
        "oriented_by": "",
        "note": "",
        "next": LANDING,
        **extra,
    }


def _remove_url(record: OrientationRecord) -> str:
    return reverse("hub_orientation_record_remove", args=[record.pk])


def _lead_world(client: Client) -> dict[str, Any]:
    """A lead of guild A logged in; guild B's type and a tool's type are out of their reach."""
    lead = _login(client, "rec_lead", name="Lena Recordlead")
    guild_a = GuildFactory(name="Recording Alpha", guild_lead=lead)
    guild_b = GuildFactory(name="Recording Beta")
    return {
        "lead": lead,
        "type_a": OrientationTypeFactory(guild=guild_a, name="Alpha Basics"),
        "type_b": OrientationTypeFactory(guild=guild_b, name="Beta Basics"),
        "tool_type": OrientationTypeFactory(equipment_owned=True, equipment=EquipmentFactory(name="Faraway Lathe")),
        "target": MemberFactory(full_legal_name="Tobias Recordee", status=Member.Status.ACTIVE),
    }


def describe_the_button():
    def it_shows_for_a_lead_beside_add_member(client: Client):
        world = _lead_world(client)
        content = _tab(client)
        assert "data-bookings-record " in content
        assert "+ Record Orientation" in content
        assert "data-bookings-record-form" in content
        assert f'action="{RECORD_URL}"' in content
        assert 'name="next" value="/orientations/?view=bookings&amp;oriented=yes"' in content
        assert str(world["type_a"]) in content

    def it_never_shows_for_a_plain_member(client: Client):
        _login(client, "rec_plain")
        OrientationTypeFactory(guild=GuildFactory(name="Some Guild"))
        content = _tab(client)
        assert "data-bookings-record" not in content
        assert "orientation-record-member-options" not in content

    def it_hides_for_staff_who_run_no_type(client: Client):
        lead = _login(client, "rec_notypes")
        GuildFactory(name="Typeless Guild", guild_lead=lead)
        content = _tab(client)
        assert 'data-bookings-pane="staff"' in content
        assert "data-bookings-record" not in content

    def it_defaults_oriented_by_to_the_viewer_and_the_date_to_today(client: Client):
        world = _lead_world(client)
        response = client.get(PAGE, {"view": "bookings"})
        form = response.context["record_form"]
        assert form["oriented_by"].value() == world["lead"].pk
        assert form["completed_on"].value() == timezone.localdate()


def describe_the_type_choices():
    def it_offers_a_lead_only_their_guilds_types_retired_ones_included(client: Client):
        world = _lead_world(client)
        retired = OrientationTypeFactory(guild=world["type_a"].guild, name="Old Alpha", is_active=False)
        form = client.get(PAGE, {"view": "bookings"}).context["record_form"]
        assert form.type_options == [str(world["type_a"]), f"{retired}{OrientationRecordForm.RETIRED_SUFFIX}"]

    def it_offers_guild_staff_their_guilds_types(client: Client):
        staffer = _login(client, "rec_staff")
        guild = GuildFactory(name="Staffed Guild")
        GuildStaffMembershipFactory(guild=guild, member=staffer, role=GuildStaffMembership.Role.ORIENTER)
        staffed = OrientationTypeFactory(guild=guild, name="Staffed Basics")
        OrientationTypeFactory(guild=GuildFactory(name="Other Guild"), name="Other Basics")
        form = client.get(PAGE, {"view": "bookings"}).context["record_form"]
        assert form.type_options == [str(staffed)]

    def it_offers_an_equipment_staffer_their_tools_types(client: Client):
        staffer = _login(client, "rec_tool")
        tool = EquipmentFactory(name="Staffed Bandsaw")
        EquipmentStaffMembershipFactory(equipment=tool, member=staffer)
        tool_type = OrientationTypeFactory(equipment_owned=True, equipment=tool, name="Bandsaw Basics")
        OrientationTypeFactory(guild=GuildFactory(name="Unrelated Guild"))
        form = client.get(PAGE, {"view": "bookings"}).context["record_form"]
        assert form.type_options == [str(tool_type)]

    def it_offers_an_admin_every_type(client: Client):
        _login(client, "rec_admin", fog_role=Member.FogRole.ADMIN)
        OrientationTypeFactory(guild=GuildFactory(name="Admin Guild"))
        OrientationTypeFactory(equipment_owned=True)
        form = client.get(PAGE, {"view": "bookings"}).context["record_form"]
        assert len(form.type_options) == OrientationType.objects.count()


def describe_the_member_picker():
    def it_lists_active_members_and_leaves_out_former_and_hidden_ones():
        MembershipPlanFactory()
        active = MemberFactory(full_legal_name="Ada Pickable", status=Member.Status.ACTIVE)
        MemberFactory(full_legal_name="Fred Formerly", status=Member.Status.FORMER)
        MemberFactory(full_legal_name="Hal Hiddenaway", status=Member.Status.ACTIVE, hide_from_directory=True)
        form = OrientationRecordForm(None)
        assert "Ada Pickable" in form.member_options
        assert "Fred Formerly" not in form.member_options
        assert "Hal Hiddenaway" not in form.member_options
        assert list(form.fields)[0] == "member"
        assert form.fields["member"].widget.attrs["list"] == "orientation-record-member-options"
        assert form._members_by_label["ada pickable"] == active

    def it_tells_two_members_with_one_name_apart_by_number():
        MembershipPlanFactory()
        first = MemberFactory(full_legal_name="Sam Twin", status=Member.Status.ACTIVE)
        second = MemberFactory(full_legal_name="Sam Twin", status=Member.Status.ACTIVE)
        form = OrientationRecordForm(None)
        assert f"Sam Twin (#{first.pk})" in form.member_options
        assert f"Sam Twin (#{second.pk})" in form.member_options
        assert "Sam Twin" not in form.member_options

    def it_adds_no_picker_for_the_admin_flow():
        MembershipPlanFactory()
        form = OrientationRecordForm(MemberFactory())
        assert "member" not in form.fields
        assert form.member_options == []


def describe_recording():
    def it_records_silently_and_lands_on_the_oriented_list(client: Client):
        world = _lead_world(client)
        orienter = MemberFactory(full_legal_name="Olga Orienter", status=Member.Status.ACTIVE)
        mail.outbox.clear()
        with mock.patch("core.events.emit.emit") as emit:
            response = client.post(
                RECORD_URL,
                _post(str(world["type_a"]), "Tobias Recordee", oriented_by=str(orienter.pk), note="Trained in 2024"),
            )
        assert response.status_code == 302
        assert response["Location"] == LANDING
        record = OrientationRecord.objects.get(member=world["target"])
        assert record.orientation_type == world["type_a"]
        assert record.completed_on == timezone.localdate()
        assert (record.oriented_by, record.note, record.recorded_by) == (
            orienter,
            "Trained in 2024",
            world["lead"].user,
        )
        assert mail.outbox == []
        emit.assert_not_called()
        activity = SiteActivity.objects.get(kind=SiteActivity.Kind.ORIENTATION_RECORDED)
        assert (activity.actor, activity.target) == (world["lead"].user, world["target"])
        landing = client.get(LANDING)
        content = landing.content.decode()
        assert f'data-record-row="{record.pk}"' in content
        assert any(
            "Recorded the Alpha Basics orientation for Tobias Recordee." in str(m) for m in landing.context["messages"]
        )

    def it_records_a_retired_type(client: Client):
        world = _lead_world(client)
        retired = OrientationTypeFactory(guild=world["type_a"].guild, name="Old Alpha", is_active=False)
        client.post(RECORD_URL, _post(f"{retired}{OrientationRecordForm.RETIRED_SUFFIX}", "Tobias Recordee"))
        assert OrientationRecord.objects.filter(member=world["target"], orientation_type=retired).exists()

    def it_counts_at_once_on_the_equipment_page(client: Client):
        member_client = Client()
        target = _login(member_client, "rec_cora", name="Cora Countable")
        staffer = _login(client, "rec_tool_count")
        tool = EquipmentFactory(name="Counted Lathe")
        EquipmentStaffMembershipFactory(equipment=tool, member=staffer)
        tool_type = OrientationTypeFactory(equipment_owned=True, equipment=tool, name="Counted Basics")
        tool.required_orientation = tool_type
        tool.save(update_fields=["required_orientation"])
        detail = reverse("hub_equipment_detail", args=[tool.slug])
        assert "You're all set." not in member_client.get(detail).content.decode()
        response = client.post(RECORD_URL, _post(str(tool_type), "Cora Countable"))
        assert response.status_code == 302
        assert tool_type.pk in target.completed_orientation_type_ids([tool_type])
        assert "You're all set." in member_client.get(detail).content.decode()
        index = member_client.get(reverse("hub_equipment_index")).content.decode()
        assert "pl-equip-badge--ok" in index

    def it_falls_back_to_the_tab_for_an_off_site_next(client: Client):
        world = _lead_world(client)
        response = client.post(RECORD_URL, _post(str(world["type_a"]), "Tobias Recordee", next="https://evil.example/"))
        assert response["Location"] == "/orientations/?view=bookings"


def describe_refusals():
    def it_refuses_a_type_another_guild_owns_and_writes_nothing(client: Client):
        world = _lead_world(client)
        response = client.post(RECORD_URL, _post(str(world["type_b"]), "Tobias Recordee"), follow=True)
        assert not OrientationRecord.objects.exists()
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_RECORDED).exists()
        assert "Pick an orientation from the list." in [str(m) for m in response.context["messages"]]

    def it_refuses_a_tool_type_the_lead_does_not_manage(client: Client):
        world = _lead_world(client)
        client.post(RECORD_URL, _post(str(world["tool_type"]), "Tobias Recordee"))
        assert not OrientationRecord.objects.exists()

    def it_refuses_a_plain_member_outright(client: Client):
        _login(client, "rec_plain_post")
        type_a = OrientationTypeFactory(guild=GuildFactory(name="Plain Guild"))
        MemberFactory(full_legal_name="Tobias Recordee", status=Member.Status.ACTIVE)
        response = client.post(RECORD_URL, _post(str(type_a), "Tobias Recordee"))
        assert response.status_code == 403
        assert not OrientationRecord.objects.exists()

    def it_refuses_a_type_the_member_already_completed(client: Client):
        world = _lead_world(client)
        OrientationRecordFactory(member=world["target"], orientation_type=world["type_a"])
        response = client.post(RECORD_URL, _post(str(world["type_a"]), "Tobias Recordee"), follow=True)
        assert OrientationRecord.objects.count() == 1
        assert "Tobias Recordee already completed this orientation." in [str(m) for m in response.context["messages"]]

    def it_refuses_a_type_completed_by_booking(client: Client):
        world = _lead_world(client)
        slot = OrientationSlotFactory(guild=world["type_a"].guild, orientation_type=world["type_a"])
        OrientationBookingFactory(slot=slot, member=world["target"]).mark_completed()
        client.post(RECORD_URL, _post(str(world["type_a"]), "Tobias Recordee"))
        assert not OrientationRecord.objects.exists()

    def it_refuses_a_future_date(client: Client):
        world = _lead_world(client)
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        response = client.post(
            RECORD_URL, _post(str(world["type_a"]), "Tobias Recordee", completed_on=tomorrow), follow=True
        )
        assert not OrientationRecord.objects.exists()
        assert "Pick today or a day in the past." in [str(m) for m in response.context["messages"]]

    def it_refuses_an_unknown_member(client: Client):
        world = _lead_world(client)
        response = client.post(RECORD_URL, _post(str(world["type_a"]), "Nobody Atall"), follow=True)
        assert not OrientationRecord.objects.exists()
        assert "Pick a member from the list." in [str(m) for m in response.context["messages"]]

    def it_takes_only_a_post(client: Client):
        _lead_world(client)
        assert client.get(RECORD_URL).status_code == 405


def describe_removing():
    def it_offers_remove_only_on_records_the_viewer_runs(client: Client):
        world = _lead_world(client)
        mine = OrientationRecordFactory(member=world["target"], orientation_type=world["type_a"])
        # The lead's own record on another guild's type is listed, but is not theirs to remove.
        own_elsewhere = OrientationRecordFactory(member=world["lead"], orientation_type=world["type_b"])
        content = _tab(client, oriented="yes")
        assert f"'record-remove-{mine.pk}'" in content
        assert f'action="{_remove_url(mine)}"' in content
        assert f'data-record-row="{own_elsewhere.pk}"' in content
        assert f"'record-remove-{own_elsewhere.pk}'" not in content

    def it_removes_a_record_in_scope_and_lands_back(client: Client):
        world = _lead_world(client)
        record = OrientationRecordFactory(member=world["target"], orientation_type=world["type_a"])
        response = client.post(_remove_url(record), {"next": LANDING})
        assert response.status_code == 302
        assert response["Location"] == LANDING
        assert not OrientationRecord.objects.exists()
        activity = SiteActivity.objects.get(kind=SiteActivity.Kind.ORIENTATION_RECORD_REMOVED)
        assert (activity.actor, activity.target) == (world["lead"].user, world["target"])

    def it_refuses_a_record_on_another_guilds_type(client: Client):
        world = _lead_world(client)
        record = OrientationRecordFactory(member=world["target"], orientation_type=world["type_b"])
        assert client.post(_remove_url(record)).status_code == 403
        assert OrientationRecord.objects.filter(pk=record.pk).exists()

    def it_refuses_a_record_on_a_tool_the_lead_does_not_manage(client: Client):
        world = _lead_world(client)
        record = OrientationRecordFactory(member=world["target"], orientation_type=world["tool_type"])
        assert client.post(_remove_url(record)).status_code == 403
        assert OrientationRecord.objects.filter(pk=record.pk).exists()

    def it_lets_an_equipment_capability_holder_remove_a_tool_record(client: Client):
        holder = _login(client, "rec_holder")
        holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        record = OrientationRecordFactory(orientation_type=OrientationTypeFactory(equipment_owned=True))
        assert client.post(_remove_url(record)).status_code == 302
        assert not OrientationRecord.objects.exists()

    def it_404s_for_a_missing_record(client: Client):
        _lead_world(client)
        assert client.post(reverse("hub_orientation_record_remove", args=[999999])).status_code == 404
