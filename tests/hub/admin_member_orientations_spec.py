"""BDD specs for the member edit Orientations tab (issue #465).

An admin lists a member's completed orientations (booked or recorded), records one through
the datalist picker, is refused a duplicate or an unknown label, and removes a recorded one.
Every surface that lists completed orientations renders a record: the dashboard's Completed
filter, the guild page, the equipment page and index. Recording is silent.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import SiteActivity
from hub.forms import OrientationRecordForm
from membership.models import Member, OrientationRecord
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationRecordFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

PASSWORD = "pw12345!"


def _login_admin(client: Client, username: str = "orient-admin") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password=PASSWORD)
    member = Member.objects.get(user=user)
    member.fog_role = Member.FogRole.ADMIN
    member.full_legal_name = "Orient Admin"
    member.status = Member.Status.ACTIVE
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password=PASSWORD)
    return member


def _login_member(client: Client, username: str = "orient-member") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password=PASSWORD)
    member = Member.objects.get(user=user)
    member.full_legal_name = "Plain Member"
    member.status = Member.Status.ACTIVE
    member.save()
    client.login(username=username, password=PASSWORD)
    return member


def _target_member(username: str = "orient-target") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password=PASSWORD)
    member = Member.objects.get(user=user)
    member.full_legal_name = "Target Member"
    member.status = Member.Status.ACTIVE
    member.save()
    return member


def _woodshop_basics():
    guild = GuildFactory(name="Woodshop")
    return guild, OrientationTypeFactory(guild=guild, name="Shop Basics")


def _completed_booking(member: Member, orientation_type, *, days_ago: int = 3):
    slot = OrientationSlotFactory(
        guild=orientation_type.guild,
        orientation_type=orientation_type,
        starts_at=timezone.now() - timedelta(days=days_ago),
        ends_at=timezone.now() - timedelta(days=days_ago) + timedelta(hours=1),
    )
    booking = OrientationBookingFactory(slot=slot, member=member)
    booking.mark_completed()
    return booking


def _edit_url(member: Member) -> str:
    return reverse("hub_admin_member_edit", kwargs={"pk": member.pk})


def _record_url(member: Member) -> str:
    return reverse("hub_admin_member_orientation_record", kwargs={"pk": member.pk})


def _remove_url(record: OrientationRecord) -> str:
    return reverse(
        "hub_admin_member_orientation_record_remove", kwargs={"pk": record.member_id, "record_pk": record.pk}
    )


def _post_data(label: str, **extra: str) -> dict[str, str]:
    return {"orientation": label, "completed_on": "2026-09-01", "oriented_by": "", "note": "", **extra}


def describe_record_orientation():
    def it_records_and_returns_to_the_orientations_tab(client: Client):
        admin = _login_admin(client)
        target = _target_member()
        guild, basics = _woodshop_basics()
        orienter = MemberFactory(full_legal_name="Dana Orienter")
        mail.outbox.clear()
        with mock.patch("core.events.emit.emit") as emit:
            response = client.post(
                _record_url(target), _post_data(str(basics), oriented_by=str(orienter.pk), note="In person")
            )
        assert response.status_code == 302
        assert response["Location"] == f"{_edit_url(target)}?tab=orientations"
        record = OrientationRecord.objects.get(member=target)
        assert record.orientation_type == basics
        assert record.completed_on.isoformat() == "2026-09-01"
        assert (record.oriented_by, record.note, record.recorded_by) == (orienter, "In person", admin.user)
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.ORIENTATION_RECORDED)
        assert row.actor == admin.user
        assert row.target == target
        assert row.payload == {"orientation_type": "Shop Basics", "owner": "Woodshop"}
        emit.assert_not_called()
        assert mail.outbox == []
        assert target.is_oriented_for(guild) is True
        assert target.is_oriented_for_type(basics) is True

    def it_shows_the_message_and_the_new_row_after_the_redirect(client: Client):
        _login_admin(client)
        target = _target_member()
        _guild, basics = _woodshop_basics()
        response = client.post(_record_url(target), _post_data(str(basics)), follow=True)
        content = response.content.decode()
        assert "Recorded the Shop Basics orientation for Target Member." in content
        record = OrientationRecord.objects.get(member=target)
        assert f"remove-orientation-{record.pk}" in content
        assert 'class="hub-pill hub-pill--neutral">Recorded' in content

    def it_records_a_retired_type_from_the_end_of_the_list(client: Client):
        _login_admin(client)
        target = _target_member()
        guild = GuildFactory(name="Woodshop")
        retired = OrientationTypeFactory(guild=guild, name="Old Basics", is_active=False)
        response = client.post(_record_url(target), _post_data("Woodshop — Old Basics (retired)"))
        assert response.status_code == 302
        assert OrientationRecord.objects.get(member=target).orientation_type == retired

    def it_resolves_the_label_case_insensitively(client: Client):
        _login_admin(client)
        target = _target_member()
        _guild, basics = _woodshop_basics()
        response = client.post(_record_url(target), _post_data("  woodshop — SHOP basics "))
        assert response.status_code == 302
        assert OrientationRecord.objects.get(member=target).orientation_type == basics

    def it_refuses_an_unknown_label_on_the_orientations_tab(client: Client):
        _login_admin(client)
        target = _target_member()
        _woodshop_basics()
        response = client.post(_record_url(target), _post_data("Woodshop — Nope"))
        assert response.status_code == 200
        assert response.context["open_tab"] == "orientations"
        assert "Pick an orientation from the list." in response.content.decode()
        assert not OrientationRecord.objects.exists()

    def it_refuses_a_type_completed_by_booking(client: Client):
        _login_admin(client)
        target = _target_member()
        _guild, basics = _woodshop_basics()
        _completed_booking(target, basics)
        response = client.post(_record_url(target), _post_data(str(basics)))
        assert response.status_code == 200
        assert "Target Member already completed this orientation." in response.content.decode()
        assert not OrientationRecord.objects.exists()

    def it_refuses_a_type_already_recorded(client: Client):
        _login_admin(client)
        target = _target_member()
        _guild, basics = _woodshop_basics()
        OrientationRecordFactory(member=target, orientation_type=basics)
        response = client.post(_record_url(target), _post_data(str(basics)))
        assert response.status_code == 200
        assert "Target Member already completed this orientation." in response.content.decode()
        assert OrientationRecord.objects.filter(member=target).count() == 1
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_RECORDED).exists()

    def it_denies_a_non_admin(client: Client):
        _login_member(client)
        target = _target_member()
        _guild, basics = _woodshop_basics()
        assert client.post(_record_url(target), _post_data(str(basics))).status_code == 403
        assert not OrientationRecord.objects.exists()

    def it_rejects_get(client: Client):
        _login_admin(client)
        assert client.get(_record_url(_target_member())).status_code == 405


def describe_remove_orientation_record():
    def it_removes_the_record_logs_it_and_closes_the_gates(client: Client):
        admin = _login_admin(client)
        target = _target_member()
        guild, basics = _woodshop_basics()
        record = OrientationRecordFactory(member=target, orientation_type=basics)
        mail.outbox.clear()
        with mock.patch("core.events.emit.emit") as emit:
            response = client.post(_remove_url(record))
        assert response.status_code == 302
        assert response["Location"] == f"{_edit_url(target)}?tab=orientations"
        assert not OrientationRecord.objects.filter(pk=record.pk).exists()
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.ORIENTATION_RECORD_REMOVED)
        assert row.actor == admin.user
        assert row.target == target
        assert row.payload == {"orientation_type": "Shop Basics", "owner": "Woodshop"}
        emit.assert_not_called()
        assert mail.outbox == []
        assert target.is_oriented_for(guild) is False
        assert target.is_oriented_for_type(basics) is False

    def it_shows_the_message_after_the_redirect(client: Client):
        _login_admin(client)
        target = _target_member()
        _guild, basics = _woodshop_basics()
        record = OrientationRecordFactory(member=target, orientation_type=basics)
        response = client.post(_remove_url(record), follow=True)
        assert "Removed the Shop Basics orientation record for Target Member." in response.content.decode()

    def it_never_touches_a_booking(client: Client):
        _login_admin(client)
        target = _target_member()
        guild, basics = _woodshop_basics()
        lathe = OrientationTypeFactory(guild=guild, name="Lathe")
        booking = _completed_booking(target, basics)
        record = OrientationRecordFactory(member=target, orientation_type=lathe)
        client.post(_remove_url(record))
        booking.refresh_from_db()
        assert booking.is_completed is True
        assert target.is_oriented_for_type(basics) is True

    def it_404s_a_record_that_belongs_to_another_member(client: Client):
        _login_admin(client)
        target = _target_member()
        record = OrientationRecordFactory()  # someone else's
        url = reverse("hub_admin_member_orientation_record_remove", kwargs={"pk": target.pk, "record_pk": record.pk})
        assert client.post(url).status_code == 404
        assert OrientationRecord.objects.filter(pk=record.pk).exists()

    def it_denies_a_non_admin(client: Client):
        _login_member(client)
        record = OrientationRecordFactory(member=_target_member())
        assert client.post(_remove_url(record)).status_code == 403
        assert OrientationRecord.objects.filter(pk=record.pk).exists()

    def it_rejects_get(client: Client):
        _login_admin(client)
        record = OrientationRecordFactory(member=_target_member())
        assert client.get(_remove_url(record)).status_code == 405


def describe_orientations_tab():
    def it_lists_booked_and_recorded_rows_newest_first(client: Client):
        _login_admin(client)
        target = _target_member()
        guild, basics = _woodshop_basics()
        lathe = OrientationTypeFactory(guild=guild, name="Lathe")
        orienter = MemberFactory(full_legal_name="Dana Orienter")
        _completed_booking(target, basics, days_ago=10)
        record = OrientationRecordFactory(
            member=target, orientation_type=lathe, oriented_by=orienter, completed_on=timezone.localdate()
        )
        response = client.get(_edit_url(target))
        content = response.content.decode()
        rows = response.context["orientation_rows"]
        assert [row["orientation_type"] for row in rows] == [lathe, basics]
        assert rows[0]["record"] == record
        assert rows[1]["record"] is None
        assert 'class="hub-pill hub-pill--ok">Booked' in content
        assert 'class="hub-pill hub-pill--neutral">Recorded' in content
        assert "oriented by Dana Orienter" in content
        assert f"remove-orientation-{record.pk}" in content
        assert 'id="orientation-type-options"' in content
        assert response.context["open_tab"] == "details"

    def it_gives_a_booking_row_no_remove_control(client: Client):
        _login_admin(client)
        target = _target_member()
        _guild, basics = _woodshop_basics()
        _completed_booking(target, basics)
        content = client.get(_edit_url(target)).content.decode()
        assert "pl-member-orient-row" in content
        assert "pl-member-orient-row__actions" not in content
        assert "remove-orientation-" not in content

    def it_shows_the_empty_state(client: Client):
        _login_admin(client)
        content = client.get(_edit_url(_target_member())).content.decode()
        assert "pl-member-orient-empty" in content
        assert "pl-member-orient-row" not in content

    def it_hides_the_tab_in_the_non_member_user_edit_mode(client: Client):
        _login_admin(client)
        user = User.objects.create_user(username="no-member-user", email="nmu@x.com")
        Member.objects.filter(user=user).delete()
        content = client.get(reverse("hub_admin_user_edit", kwargs={"user_pk": user.pk})).content.decode()
        assert "section === 'orientations'" not in content

    def it_keeps_the_other_tabs_saves_for_an_unlinked_member(client: Client):
        # admin_member_edit was refactored onto a shared renderer; its Notifications save
        # for a member with no account still redirects without touching anything.
        _login_admin(client)
        target = MemberFactory(full_legal_name="Unlinked Target")
        response = client.post(_edit_url(target), {"form_id": "notifications"})
        assert response.status_code == 302
        assert response["Location"] == f"{_edit_url(target)}?tab=permissions"
        assert client.get(_edit_url(target)).status_code == 200


def describe_orientation_record_form():
    def it_lists_active_types_before_retired_ones_with_a_suffix():
        woodshop = GuildFactory(name="Woodshop")
        metalshop = GuildFactory(name="Metalshop")
        OrientationTypeFactory(guild=woodshop, name="Shop Basics", sort_order=2)
        OrientationTypeFactory(guild=metalshop, name="Shop Basics", sort_order=1)
        OrientationTypeFactory(guild=woodshop, name="Old Basics", sort_order=0, is_active=False)
        form = OrientationRecordForm(MemberFactory())
        assert form.type_options == [
            "Metalshop — Shop Basics",
            "Woodshop — Shop Basics",
            "Woodshop — Old Basics (retired)",
        ]

    def it_backs_the_input_with_the_datalist():
        form = OrientationRecordForm(MemberFactory())
        assert form.fields["orientation"].widget.attrs["list"] == "orientation-type-options"

    def it_defaults_the_date_to_today():
        form = OrientationRecordForm(MemberFactory())
        assert form.fields["completed_on"].initial == timezone.localdate()
        assert form.fields["completed_on"].widget.attrs["class"] == "pl-slot-date"

    def it_offers_only_active_members_as_orienters():
        active = MemberFactory(full_legal_name="Active Orienter")
        gone = MemberFactory(full_legal_name="Gone Orienter", status=Member.Status.FORMER)
        queryset = OrientationRecordForm(MemberFactory()).fields["oriented_by"].queryset
        assert active in queryset
        assert gone not in queryset


def describe_orientations_dashboard_records():
    def _dashboard(client: Client, **params: str) -> str:
        return client.get(reverse("hub_orientations_dashboard"), params).content.decode()

    def it_lists_records_under_the_completed_filter(client: Client):
        _login_admin(client)
        target = _target_member()
        _guild, basics = _woodshop_basics()
        OrientationRecordFactory(member=target, orientation_type=basics, oriented_by=MemberFactory())
        content = _dashboard(client, completed="yes")
        assert "pl-orient-records" in content
        assert "Target Member" in content
        assert f'href="{_edit_url(target)}?tab=orientations"' in content
        assert 'class="hub-pill hub-pill--neutral">Recorded' in content

    def it_hides_the_table_without_the_completed_filter(client: Client):
        _login_admin(client)
        OrientationRecordFactory(member=_target_member())
        assert "pl-orient-records" not in _dashboard(client)
        assert "pl-orient-records" not in _dashboard(client, completed="no")

    def it_applies_the_guild_filter(client: Client):
        _login_admin(client)
        alpha = GuildFactory(name="Alpha")
        beta = GuildFactory(name="Beta")
        OrientationRecordFactory(
            member=MemberFactory(full_legal_name="In Alpha"), orientation_type=OrientationTypeFactory(guild=alpha)
        )
        OrientationRecordFactory(
            member=MemberFactory(full_legal_name="In Beta"), orientation_type=OrientationTypeFactory(guild=beta)
        )
        content = _dashboard(client, completed="yes", guild=str(alpha.pk))
        # The add-member select lists every active member, so anchor on the table's link text.
        assert ">In Alpha</a>" in content
        assert ">In Beta</a>" not in content

    def it_shows_the_empty_row(client: Client):
        _login_admin(client)
        assert "pl-orient-records__empty" in _dashboard(client, completed="yes")

    def it_shows_a_lead_the_row_without_the_admin_link(client: Client):
        lead = _login_member(client, "orient-lead")
        guild = GuildFactory(name="Led", guild_lead=lead)
        target = _target_member()
        OrientationRecordFactory(member=target, orientation_type=OrientationTypeFactory(guild=guild))
        content = _dashboard(client, completed="yes")
        assert "Target Member" in content
        assert f'href="{_edit_url(target)}?tab=orientations"' not in content


def describe_member_facing_surfaces():
    def it_renders_the_guild_page_with_a_recorded_orientation(client: Client):
        member = _login_member(client)
        guild, basics = _woodshop_basics()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        OrientationRecordFactory(member=member, orientation_type=basics, completed_on=timezone.localdate())
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "pl-orient-recorded" in content
        assert response.context["orientation_all_done"] is True
        assert response.context["orientation_sections"][0]["is_oriented"] is True

    def it_says_recorded_on_only_when_no_booking_completed_the_type(client: Client):
        member = _login_member(client)
        guild, basics = _woodshop_basics()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        _completed_booking(member, basics)
        OrientationRecordFactory(member=member, orientation_type=basics)
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert response.status_code == 200
        assert "pl-orient-recorded" not in response.content.decode()
        assert response.context["orientation_sections"][0]["record"] is None

    def it_renders_the_equipment_page_all_set_with_a_recorded_orientation(client: Client):
        member = _login_member(client)
        orientation_type = OrientationTypeFactory(equipment_owned=True, name="Lathe Basics")
        equipment = orientation_type.equipment
        equipment.required_orientation = orientation_type
        equipment.save(update_fields=["required_orientation"])
        url = reverse("hub_equipment_detail", args=[equipment.slug])
        assert b"You're all set." not in client.get(url).content
        OrientationRecordFactory(member=member, orientation_type=orientation_type)
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode()
        assert "You're all set." in content
        assert "pl-orient-recorded" in content

    def it_marks_the_equipment_index_card_all_set(client: Client):
        member = _login_member(client)
        orientation_type = OrientationTypeFactory(name="Lathe")
        EquipmentFactory(name="Gated Lathe", required_orientation=orientation_type)
        assert b"Orientation needed" in client.get(reverse("hub_equipment_index")).content
        OrientationRecordFactory(member=member, orientation_type=orientation_type)
        content = client.get(reverse("hub_equipment_index")).content
        assert b"You're all set" in content
        assert b"Orientation needed" not in content
