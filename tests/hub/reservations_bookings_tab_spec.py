"""BDD specs for the Reservations page's Bookings tab (#627).

Who sees which rows (a member only their own, cancelled ones included; a manager the equipment
they manage plus their own; an admin and the EQUIPMENT holder everything; an officer with no
equipment role only their own), each row's "..." menu per role and state, the guarded sort and
dates, the lazy pane, the preview rule, and the cancel endpoint landing back on the tab. Member
names are factory strings no changelog could contain (STANDARDS.md §8).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest import mock
from unittest.mock import patch

import pytest
from django.contrib import messages as django_messages
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from billing.models import LateCancellationFee
from core.models import SiteConfiguration
from membership.models import AdminCapability, EquipmentReservation, GuildStaffMembership, Member
from tests.billing.factories import LateCancellationFeeFactory
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

PAGE = "/equipment/"
PARTIAL = "/equipment/bookings/"
TAB = "/equipment/?view=bookings"
_SESSION = {"id": "cs_res_tab_1", "url": "https://checkout.stripe.example/cs_res_tab_1"}


def _user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _login(client: Client, username: str, **kwargs: Any) -> Member:
    user = _user(username, **kwargs)
    client.login(username=username, password="pass")
    return user.member


def _tab(client: Client, **params: str) -> str:
    response = client.get(PAGE, {"view": "bookings", **params})
    assert response.status_code == 200
    return response.content.decode()


def _reservation(equipment: Any = None, *, name: str = "", hours: float = 48, **kwargs: Any) -> EquipmentReservation:
    starts = timezone.now() + timedelta(hours=hours)
    member = kwargs.pop("member", None) or (MemberFactory(full_legal_name=name) if name else MemberFactory())
    return EquipmentReservationFactory(
        equipment=equipment or EquipmentFactory(),
        member=member,
        starts_at=starts,
        ends_at=starts + timedelta(hours=1),
        **kwargs,
    )


def _row(content: str, reservation: EquipmentReservation) -> str:
    start = content.index(f'data-reservation-row="{reservation.pk}"')
    return content[start : content.index("</tr>", start)]


def _in_table(content: str, reservation: EquipmentReservation) -> bool:
    return f'data-reservation-row="{reservation.pk}"' in content


def _cancel_url(reservation: EquipmentReservation) -> str:
    return reverse("hub_equipment_reservation_cancel", args=[reservation.equipment.slug, reservation.pk])


def _messages(response: Any) -> list[str]:
    return [str(m) for m in django_messages.get_messages(response.wsgi_request)]


def _manager(client: Client, username: str = "rt_manager") -> tuple[Member, Any]:
    manager = _login(client, username)
    tool = EquipmentFactory(name="Managed Laser")
    EquipmentStaffMembershipFactory(equipment=tool, member=manager)
    return manager, tool


def describe_the_tab():
    def it_adds_a_third_tab_and_loads_it_lazily(client: Client):
        _login(client, "rt_tabs")
        with mock.patch("hub.equipment_views.reservation_bookings_context") as build:
            content = client.get(PAGE).content.decode()
            assert client.get(PAGE, {"view": "calendar"}).status_code == 200
        build.assert_not_called()
        assert "@click=\"setPane('bookings')\">Bookings</button>" in content
        assert f'data-lazy-pane="bookings" data-pane-src="{PARTIAL}"' in content
        assert "css/bookings-tab" in content

    def it_opens_the_tab_inline_for_view_bookings(client: Client):
        _login(client, "rt_open")
        content = _tab(client)
        assert "plListCalendar('bookings')" in content
        assert 'data-bookings-pane="member"' in content

    def it_serves_the_pane_alone(client: Client):
        member = _login(client, "rt_partial")
        mine = _reservation(member=member)
        response = client.get(PARTIAL)
        content = response.content.decode()
        assert response.status_code == 200
        assert "<html" not in content
        assert _in_table(content, mine)
        assert f'hx-get="{PARTIAL}?view=bookings"' in content

    def it_requires_login(client: Client):
        assert client.get(PARTIAL).status_code == 302


def describe_a_member_who_manages_nothing():
    def it_lists_only_their_own_reservations_cancelled_included(client: Client):
        member = _login(client, "rt_member")
        upcoming = _reservation(member=member)
        past = _reservation(member=member, hours=-30)
        cancelled = _reservation(member=member, status=EquipmentReservation.Status.CANCELLED, cancelled_by=member)
        stranger = _reservation(name="Zora Strangerperson")
        stranger_past = _reservation(name="Quill Pastperson", hours=-30)
        for show in ("upcoming", "past", "all"):
            content = _tab(client, show=show)
            assert "Zora Strangerperson" not in content
            assert "Quill Pastperson" not in content
            assert not _in_table(content, stranger) and not _in_table(content, stranger_past)
        assert _in_table(_tab(client), upcoming)
        assert not _in_table(_tab(client), cancelled)
        past_rows = _tab(client, show="past")
        assert _in_table(past_rows, past) and _in_table(past_rows, cancelled)
        all_rows = _tab(client, show="all")
        assert all(_in_table(all_rows, r) for r in (upcoming, past, cancelled))

    def it_shows_no_member_column_or_filters_and_ignores_staff_params(client: Client):
        member = _login(client, "rt_member_cols")
        mine = _reservation(member=member, purpose="Cutting signs")
        content = _tab(client, status="cancelled", search="nobody", equipment="1")
        assert "Member</span>" not in content
        assert "data-bookings-filters" not in content
        assert _in_table(content, mine)
        assert "Cutting signs" in _row(content, mine)

    def it_gives_their_own_row_the_member_menu(client: Client):
        member = _login(client, "rt_member_menu")
        mine = _reservation(member=member)
        content = _tab(client)
        row = _row(content, mine)
        assert reverse("hub_equipment_detail", args=[mine.equipment.slug]) in row
        assert f"'reservation-cancel-mine-{mine.pk}'" in row
        assert reverse("hub_equipment_manage", args=[mine.equipment.slug]) not in row
        when = timezone.localtime(mine.starts_at).strftime("%a %b %-d, %-I:%M %p")
        assert f'aria-label="Actions for {mine.equipment.name} on {when}"' in row
        modal_start = content.index(f'action="{_cancel_url(mine)}"')
        modal = content[modal_start : content.index("</form>", modal_start)]
        assert 'hx-boost="false"' in modal
        assert 'name="next" value="/equipment/?view=bookings"' in modal
        assert 'name="reason"' not in modal

    def it_offers_pay_late_fee_and_no_cancel_once_started(client: Client):
        member = _login(client, "rt_member_fee")
        started = _reservation(member=member, hours=-0.5)
        cancelled = _reservation(member=member, status=EquipmentReservation.Status.CANCELLED, cancelled_by=member)
        fee = LateCancellationFeeFactory(for_reservation=True, reservation=cancelled, member=member)
        content = _tab(client, show="all")
        assert "reservation-cancel-mine" not in _row(content, started)
        row = _row(content, cancelled)
        assert reverse("hub_late_fee_detail", args=[fee.pk]) in row
        assert "data-booking-fee" in row
        assert f"waive-fee-{fee.pk}" not in content

    def it_warns_of_a_late_fee_in_the_cancel_confirm(client: Client):
        site = SiteConfiguration.load()
        site.late_cancel_fees_enabled = True
        site.save()
        member = _login(client, "rt_member_late")
        _reservation(EquipmentFactory(late_cancel_fee_cents=1500), member=member, hours=2)
        assert "late cancellation fee applies" in _tab(client)

    def it_shows_the_empty_states(client: Client):
        _login(client, "rt_member_empty")
        assert "You have nothing booked." in _tab(client)
        assert "@click.prevent=\"setPane('list')\"" in _tab(client)
        assert "Times you have booked show up here." in _tab(client, show="past")

    def it_sees_nothing_without_a_member_row(client: Client):
        user = _user("rt_unlinked")
        client.login(username="rt_unlinked", password="pass")
        Member.objects.filter(pk=user.member.pk).delete()
        _reservation(name="Unseen Reserver")
        content = _tab(client)
        assert "Unseen Reserver" not in content
        assert "data-bookings-empty" in content


def describe_managers_officers_and_admins():
    def it_shows_a_manager_their_equipment_and_their_own_and_nothing_else(client: Client):
        manager, tool = _manager(client)
        managed = _reservation(tool, name="Mira Managedrow")
        other = _reservation(EquipmentFactory(name="Other Saw"), name="Otto Outsider")
        own_elsewhere = _reservation(EquipmentFactory(name="Elsewhere Room"), member=manager)
        content = _tab(client)
        assert 'data-bookings-pane="staff"' in content
        assert _in_table(content, managed) and _in_table(content, own_elsewhere)
        assert not _in_table(content, other)
        assert "Otto Outsider" not in content
        own_row = _row(content, own_elsewhere)
        assert 'data-label="Member">You</td>' in own_row
        assert f"'reservation-cancel-mine-{own_elsewhere.pk}'" in own_row
        assert reverse("hub_equipment_manage", args=[tool.slug]) in _row(content, managed)

    def it_counts_a_guild_lead_and_guild_staff_on_the_owning_guild(client: Client):
        lead = _login(client, "rt_lead")
        guild = GuildFactory(guild_lead=lead)
        owned = _reservation(EquipmentFactory(guild=guild))
        content = _tab(client)
        assert _in_table(content, owned)
        client.logout()
        staff = _login(client, "rt_guild_staff")
        GuildStaffMembershipFactory(guild=guild, member=staff, role=GuildStaffMembership.Role.TREASURER)
        assert _in_table(_tab(client), owned)

    def it_gives_an_officer_without_equipment_roles_only_their_own(client: Client):
        officer = _login(client, "rt_officer", fog_role=Member.FogRole.GUILD_OFFICER)
        mine = _reservation(member=officer)
        other = _reservation(EquipmentFactory(guild=GuildFactory()))
        content = _tab(client)
        assert 'data-bookings-pane="member"' in content
        assert _in_table(content, mine) and not _in_table(content, other)

    def it_shows_an_admin_and_the_capability_holder_everything(client: Client):
        _login(client, "rt_admin", fog_role=Member.FogRole.ADMIN)
        anywhere = _reservation(name="Anywhere Reserver")
        assert _in_table(_tab(client), anywhere)
        client.logout()
        holder = _login(client, "rt_holder")
        holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        assert _in_table(_tab(client), anywhere)

    def it_keeps_a_previewing_admin_to_their_own_rows_with_member_menus(client: Client):
        admin = _login(client, "rt_preview", fog_role=Member.FogRole.ADMIN)
        admin.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        session = client.session
        session["view_as_role"] = "member"
        session.save()
        stranger = _reservation(name="Preview Strangerperson")
        own = _reservation(member=admin)
        content = _tab(client, show="all")
        assert 'data-bookings-pane="member"' in content
        assert _in_table(content, own) and not _in_table(content, stranger)
        assert reverse("hub_equipment_manage", args=[own.equipment.slug]) not in content
        # No action gate moved: the manager route still answers the capability holder.
        response = client.post(_cancel_url(stranger), {"reason": "Shop closed", "next": TAB})
        assert response["Location"] == TAB
        stranger.refresh_from_db()
        assert stranger.status == EquipmentReservation.Status.CANCELLED

    def it_reads_the_menu_s_managed_set_the_way_the_list_does(client: Client):
        # An admin previewing as an officer: no capability in the list, so their own row on
        # equipment they do not otherwise run gets the member menu, not the staff one.
        admin = _login(client, "rt_preview_officer", fog_role=Member.FogRole.ADMIN)
        admin.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        session = client.session
        session["view_as_role"] = "guild_officer"
        session.save()
        lead_tool = EquipmentFactory(guild=GuildFactory(guild_lead=admin))
        own = _reservation(EquipmentFactory(name="Unrun Kiln"), member=admin)
        managed = _reservation(lead_tool, name="Lead Toolperson")
        content = _tab(client)
        assert 'data-bookings-pane="staff"' in content
        assert f"'reservation-cancel-mine-{own.pk}'" in _row(content, own)
        assert f"'reservation-cancel-{managed.pk}'" in _row(content, managed)


def describe_the_staff_row_menu():
    def it_offers_cancel_with_a_required_reason_naming_the_member(client: Client):
        _manager_member, tool = _manager(client)
        upcoming = _reservation(tool, name="Rena Reasonrow")
        content = _tab(client)
        assert f"'reservation-cancel-{upcoming.pk}'" in _row(content, upcoming)
        assert "Rena Reasonrow has this time booked. Cancelling frees it and tells them why." in content
        assert "Rena Reasonrow will see this." in content
        assert ":disabled=\"note.trim() === ''\"" in content

    def it_offers_cancel_while_in_progress_but_not_once_ended_or_cancelled(client: Client):
        _manager_member, tool = _manager(client)
        running = _reservation(tool, hours=-0.5)
        ended = _reservation(tool, hours=-5)
        cancelled = _reservation(tool, status=EquipmentReservation.Status.CANCELLED)
        content = _tab(client, show="all")
        assert f"'reservation-cancel-{running.pk}'" in _row(content, running)
        assert "reservation-cancel-" not in _row(content, ended)
        assert "reservation-cancel-" not in _row(content, cancelled)

    def it_gives_a_manager_the_staff_menu_on_their_own_managed_row(client: Client):
        manager, tool = _manager(client)
        own = _reservation(tool, member=manager)
        content = _tab(client)
        row = _row(content, own)
        assert f"'reservation-cancel-{own.pk}'" in row
        assert "mailto:" not in row  # never email yourself
        assert "Kept with the reservation for the record." in content

    def it_marks_a_manager_cancel_in_the_status(client: Client):
        manager, tool = _manager(client)
        by_manager = _reservation(tool, status=EquipmentReservation.Status.CANCELLED, cancelled_by=manager)
        content = _tab(client, show="past")
        assert 'data-reservation-status="cancelled-by-manager"' in _row(content, by_manager)

    def it_gates_waive_on_manage_and_refund_on_refund_authority(client: Client):
        manager, tool = _manager(client)
        unpaid_on = _reservation(tool, status=EquipmentReservation.Status.CANCELLED)
        unpaid = LateCancellationFeeFactory(for_reservation=True, reservation=unpaid_on, member=unpaid_on.member)
        paid_on = _reservation(tool, status=EquipmentReservation.Status.CANCELLED)
        paid = LateCancellationFeeFactory(
            for_reservation=True,
            reservation=paid_on,
            member=paid_on.member,
            status=LateCancellationFee.Status.PAID,
            stripe_payment_id="pi_res_fee",
        )
        content = _tab(client, show="all")
        assert f"'waive-fee-{unpaid.pk}'" in _row(content, unpaid_on)
        assert 'name="next" value="/equipment/?view=bookings&amp;show=all"' in content
        assert reverse("billing_late_fee_refund_form", args=[paid.pk]) not in content
        assert 'id="refund-modal-body"' not in content
        manager.admin_capabilities.create(capability=AdminCapability.Capability.REFUNDS)
        content = _tab(client, show="all")
        assert reverse("billing_late_fee_refund_form", args=[paid.pk]) in _row(content, paid_on)
        assert reverse("billing_late_fee_refund_form", args=[unpaid.pk]) not in content
        assert 'id="refund-modal-body"' in content

    def it_shows_email_member_only_where_the_directory_allows_it(client: Client):
        _manager_member, tool = _manager(client)
        public = _reservation(tool)
        hidden = _reservation(tool)
        hidden.member.directory_visibility = {"email": False}
        hidden.member.save(update_fields=["directory_visibility"])
        content = _tab(client)
        assert "mailto:" in _row(content, public)
        assert "mailto:" not in _row(content, hidden)
        assert reverse("hub_admin_member_edit", args=[public.member.pk]) not in content

    def it_gives_an_admin_email_and_view_member(client: Client):
        _login(client, "rt_admin_menu", fog_role=Member.FogRole.ADMIN)
        hidden = _reservation()
        hidden.member.directory_visibility = {"email": False}
        hidden.member.save(update_fields=["directory_visibility"])
        row = _row(_tab(client), hidden)
        assert "mailto:" in row
        assert reverse("hub_admin_member_edit", args=[hidden.member.pk]) in row


def describe_chips_filters_and_sort():
    def it_sorts_past_newest_first(client: Client):
        _manager_member, tool = _manager(client)
        older = _reservation(tool, hours=-90)
        newer = _reservation(tool, hours=-10)
        content = _tab(client, show="past")
        assert content.index(f'data-reservation-row="{newer.pk}"') < content.index(f'data-reservation-row="{older.pk}"')

    def it_falls_back_to_the_default_sort_for_an_unknown_key(client: Client):
        _manager_member, tool = _manager(client)
        row = _reservation(tool)
        assert _in_table(_tab(client, sort="member__user__password", dir="desc"), row)

    def it_sorts_by_member_name_for_staff(client: Client):
        _manager_member, tool = _manager(client)
        zed = _reservation(tool, name="Zed Sortrow", hours=10)
        abe = _reservation(tool, name="Abe Sortrow", hours=30)
        content = _tab(client, sort="member__full_legal_name")
        assert content.index(f'data-reservation-row="{abe.pk}"') < content.index(f'data-reservation-row="{zed.pk}"')

    def it_applies_the_filters_and_ignores_bad_values(client: Client):
        manager, tool = _manager(client)
        second = EquipmentFactory(name="Second Drill")
        EquipmentStaffMembershipFactory(equipment=second, member=manager)
        target = _reservation(tool, hours=-20, status=EquipmentReservation.Status.CANCELLED)
        LateCancellationFeeFactory(for_reservation=True, reservation=target, member=target.member)
        elsewhere = _reservation(second, hours=-20)
        day = timezone.localtime(target.starts_at).date().isoformat()
        content = _tab(client, show="all", equipment=str(tool.pk), status="cancelled", fee="unpaid", start=day, end=day)
        assert _in_table(content, target) and not _in_table(content, elsewhere)
        start = content.index('id="reservations-equipment"')
        select = content[start : content.index("</select>", start)]
        assert "Managed Laser" in select and "Second Drill" in select
        assert not _in_table(_tab(client, show="all", fee="waived"), target)
        assert not _in_table(_tab(client, show="all", status="confirmed"), target)
        assert not _in_table(_tab(client, show="all", start="2999-01-01"), target)
        assert not _in_table(_tab(client, show="all", end="2001-01-01"), target)
        loose = _tab(client, show="all", equipment="abc", status="bogus", fee="maybe", start="2026-02-30", end="nope")
        assert _in_table(loose, target) and _in_table(loose, elsewhere)

    def it_searches_member_and_equipment_names(client: Client):
        _manager_member, tool = _manager(client)
        found = _reservation(tool, name="Searchable Reserver")
        missed = _reservation(tool, name="Hidden Otherrow")
        content = _tab(client, search="Searchable")
        assert _in_table(content, found) and not _in_table(content, missed)
        assert _in_table(_tab(client, search="Managed Laser"), found)
        empty = _tab(client, search="nobody-matches")
        assert "Nothing matches those filters." in empty
        assert 'href="?view=bookings">Clear filters</a>' in empty

    def it_says_nothing_is_booked_without_filters(client: Client):
        _manager(client)
        assert "Nothing is booked yet." in _tab(client)

    def it_keeps_the_list_panes_params_out_of_the_tab(client: Client):
        _manager_member, tool = _manager(client)
        row = _reservation(tool)
        content = _tab(client, q="lathe", guild="woodshop", kind="tool")
        assert _in_table(content, row)
        pane = content.split("data-bookings-pane", 1)[1]
        assert "q=lathe" not in pane and "kind=tool" not in pane


def describe_query_counts():
    def it_costs_the_same_for_one_row_as_for_many(client: Client):
        _login(client, "rt_queries", fog_role=Member.FogRole.ADMIN)
        tool = EquipmentFactory(guild=GuildFactory())

        def _fee_row() -> None:
            reservation = _reservation(tool, status=EquipmentReservation.Status.CANCELLED)
            LateCancellationFeeFactory(for_reservation=True, reservation=reservation, member=reservation.member)

        _fee_row()
        _tab(client, show="all")  # warm the per process caches first
        with CaptureQueriesContext(connection) as one:
            _tab(client, show="all")
        for _ in range(9):
            _fee_row()
        with CaptureQueriesContext(connection) as ten:
            _tab(client, show="all")
        assert len(ten.captured_queries) == len(one.captured_queries)


def describe_cancel_from_the_tab():
    def it_lands_a_manager_cancel_on_next_and_tells_the_member(client: Client):
        _manager_member, tool = _manager(client)
        reservation = _reservation(tool)
        response = client.post(_cancel_url(reservation), {"reason": "Laser is down", "next": f"{TAB}&show=all"})
        assert response["Location"] == f"{TAB}&show=all"
        assert "Reservation cancelled. The member has been told." in _messages(response)
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CANCELLED
        assert reservation.cancelled_reason == "Laser is down"

    def it_refuses_a_manager_cancel_without_a_reason_and_keeps_the_row(client: Client):
        _manager_member, tool = _manager(client)
        reservation = _reservation(tool)
        response = client.post(_cancel_url(reservation), {"reason": "  ", "next": TAB})
        assert response["Location"] == TAB
        assert "Please tell the member why." in _messages(response)
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CONFIRMED

    def it_falls_back_to_the_manage_tab_for_an_off_site_next(client: Client):
        _manager_member, tool = _manager(client)
        reservation = _reservation(tool)
        response = client.post(_cancel_url(reservation), {"reason": "Closed", "next": "https://evil.example/"})
        assert response["Location"] == f"{reverse('hub_equipment_manage', args=[tool.slug])}?tab=reservations"

    def it_forbids_a_manager_of_x_cancelling_y_s_reservation(client: Client):
        _manager(client)
        other = _reservation(EquipmentFactory(name="Unmanaged Y"))
        response = client.post(_cancel_url(other), {"reason": "Mine now", "next": TAB})
        assert response.status_code == 403
        other.refresh_from_db()
        assert other.status == EquipmentReservation.Status.CONFIRMED

    def it_forbids_a_member_cancelling_someone_else_s_reservation(client: Client):
        _login(client, "rt_probe")
        other = _reservation()
        assert client.post(_cancel_url(other), {"next": TAB}).status_code == 403
        other.refresh_from_db()
        assert other.status == EquipmentReservation.Status.CONFIRMED

    def it_lands_a_self_cancel_on_next_as_a_full_page(client: Client):
        member = _login(client, "rt_self")
        mine = _reservation(member=member)
        response = client.post(_cancel_url(mine), {"next": f"{TAB}&show=past"})
        assert response["Location"] == f"{TAB}&show=past"
        assert "Reservation cancelled." in _messages(response)
        mine.refresh_from_db()
        assert mine.status == EquipmentReservation.Status.CANCELLED

    def it_sends_a_self_cancel_with_an_off_site_next_to_the_tab(client: Client):
        member = _login(client, "rt_self_evil")
        mine = _reservation(member=member)
        response = client.post(_cancel_url(mine), {"next": "https://evil.example/"})
        assert response["Location"] == TAB
        mine.refresh_from_db()
        assert mine.status == EquipmentReservation.Status.CANCELLED

    def it_says_why_a_self_cancel_was_refused(client: Client):
        member = _login(client, "rt_self_started")
        started = _reservation(member=member, hours=-0.5)
        response = client.post(_cancel_url(started), {"next": TAB})
        assert response["Location"] == TAB
        assert any("already started" in m for m in _messages(response))

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_sends_a_late_self_cancel_to_checkout_with_the_fee_created_once(mock_create, client: Client):
        site = SiteConfiguration.load()
        site.late_cancel_fees_enabled = True
        site.save()
        member = _login(client, "rt_self_late")
        late = _reservation(EquipmentFactory(late_cancel_fee_cents=1500), member=member, hours=2)
        response = client.post(_cancel_url(late), {"next": TAB})
        assert response.status_code == 302
        assert response["Location"] == _SESSION["url"]
        assert LateCancellationFee.objects.filter(reservation=late).count() == 1
        again = client.post(_cancel_url(late), {"next": TAB})
        assert again["Location"] == TAB
        assert LateCancellationFee.objects.filter(reservation=late).count() == 1

    @patch("billing.stripe_utils.create_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_lands_on_next_with_the_fee_when_checkout_cannot_open(mock_create, client: Client):
        site = SiteConfiguration.load()
        site.late_cancel_fees_enabled = True
        site.save()
        member = _login(client, "rt_self_down")
        late = _reservation(EquipmentFactory(late_cancel_fee_cents=1500), member=member, hours=2)
        response = client.post(_cancel_url(late), {"next": TAB})
        assert response["Location"] == TAB
        assert any("use the Pay button" in m for m in _messages(response))
        assert LateCancellationFee.objects.filter(reservation=late, status="unpaid").exists()

    def it_keeps_the_schedule_swap_without_next(client: Client):
        member = _login(client, "rt_self_schedule")
        mine = _reservation(member=member)
        response = client.post(_cancel_url(mine))
        assert response.status_code == 200
        assert "showToast" in response["HX-Trigger"]
