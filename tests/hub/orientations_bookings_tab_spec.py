"""BDD specs for the Orientations page's Bookings tab (#626), which replaced the staff dashboard.

Who sees which rows (a member only their own; a lead their guilds' and their own; an officer
every guild's; an admin everything), the same scope on the CSV export and the recorded list,
each row's "..." menu per role and state, the guarded sort, the lazy pane, the old URL's
redirect, and the endpoints the menu posts to landing back on the tab. Member names are
factory strings no changelog could contain (STANDARDS.md §8).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from billing.models import LateCancellationFee, PaymentRefund
from core.models import SiteConfiguration
from membership.models import (
    AdminCapability,
    GuildOrientationSettings,
    GuildStaffMembership,
    Member,
    OrientationBooking,
)
from tests.billing.factories import LateCancellationFeeFactory
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
PARTIAL = "/orientations/bookings/"


def _user(username: str, *, fog_role: str = Member.FogRole.MEMBER, name: str = "") -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    if name:
        member.full_legal_name = name
    member.save(update_fields=["fog_role", "full_legal_name"])
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


def _slot(guild: Any = None, *, days: float = 2, **kwargs: Any) -> Any:
    starts = timezone.now() + timedelta(days=days)
    if guild is None:
        return OrientationSlotFactory(starts_at=starts, ends_at=starts + timedelta(hours=1), **kwargs)
    return OrientationSlotFactory(guild=guild, starts_at=starts, ends_at=starts + timedelta(hours=1), **kwargs)


def _booking(guild: Any = None, *, name: str = "", days: float = 2, **kwargs: Any) -> OrientationBooking:
    member = MemberFactory(full_legal_name=name) if name else MemberFactory()
    return OrientationBookingFactory(slot=_slot(guild, days=days), member=member, **kwargs)


def _equipment_booking(tool: Any, *, name: str, days: float = 2) -> OrientationBooking:
    orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=tool)
    starts = timezone.now() + timedelta(days=days)
    slot = OrientationSlotFactory(
        equipment_owned=True, orientation_type=orientation_type, starts_at=starts, ends_at=starts + timedelta(hours=1)
    )
    return OrientationBookingFactory(slot=slot, member=MemberFactory(full_legal_name=name))


def _row(content: str, booking: OrientationBooking) -> str:
    """One row's markup, its "..." menu included."""
    start = content.index(f'data-booking-row="{booking.pk}"')
    return content[start : content.index("</tr>", start)]


def _in_table(content: str, booking: OrientationBooking) -> bool:
    return f'data-booking-row="{booking.pk}"' in content


def describe_the_tab():
    def it_adds_a_third_tab_for_every_member(client: Client):
        _login(client, "bt_tabs")
        content = client.get(PAGE).content.decode()
        assert "@click=\"setPane('bookings')\">Bookings</button>" in content
        assert f'data-lazy-pane="bookings" data-pane-src="{PARTIAL}"' in content
        assert "plListCalendar('list')" in content

    def it_opens_the_tab_inline_for_view_bookings(client: Client):
        _login(client, "bt_open")
        content = _tab(client)
        assert "plListCalendar('bookings')" in content
        assert 'data-bookings-pane="member"' in content
        assert 'data-lazy-pane="bookings"' not in content

    def it_builds_no_bookings_for_the_list_view(client: Client):
        _login(client, "bt_lazy")
        with mock.patch("hub.orientations_views.bookings_pane_context") as build:
            assert client.get(PAGE).status_code == 200
            assert client.get(PAGE, {"view": "calendar"}).status_code == 200
        build.assert_not_called()

    def it_serves_the_pane_alone_for_the_lazy_open(client: Client):
        member = _login(client, "bt_partial")
        mine = _booking(name="Partial Owner")
        mine.member = member
        mine.save()
        response = client.get(PARTIAL)
        content = response.content.decode()
        assert response.status_code == 200
        assert "<html" not in content
        assert _in_table(content, mine)
        # Its links and forms still go to the page, with the tab kept open.
        assert f'hx-get="{PARTIAL}?view=bookings"' in content

    def it_requires_login(client: Client):
        assert client.get(PARTIAL).status_code == 302

    def it_drops_the_old_manage_button_from_the_header(client: Client):
        member = _login(client, "bt_header")
        GuildFactory(guild_lead=member)
        assert ">Manage orientations</a>" not in client.get(PAGE).content.decode()


def describe_a_member_who_runs_nothing():
    def it_lists_only_their_own_bookings_past_and_upcoming(client: Client):
        member = _login(client, "bt_member")
        upcoming = _booking(name="Self Upcoming")
        upcoming.member = member
        upcoming.save()
        past = _booking(name="Self Past", days=-3)
        past.member = member
        past.save()
        stranger = _booking(name="Zelda Strangerperson")
        stranger_past = _booking(name="Quincy Pastperson", days=-3)
        OrientationRecordFactory(member=MemberFactory(full_legal_name="Ruth Recordedperson"))

        for show in ("upcoming", "past", "all"):
            content = _tab(client, show=show)
            assert 'data-bookings-pane="member"' in content
            assert "Zelda Strangerperson" not in content
            assert "Quincy Pastperson" not in content
            assert "Ruth Recordedperson" not in content
            assert not _in_table(content, stranger)
            assert not _in_table(content, stranger_past)
        assert _in_table(_tab(client), upcoming)
        assert not _in_table(_tab(client), past)
        assert _in_table(_tab(client, show="past"), past)
        all_rows = _tab(client, show="all")
        assert _in_table(all_rows, upcoming) and _in_table(all_rows, past)

    def it_shows_no_member_column_filters_export_or_reply_chip(client: Client):
        member = _login(client, "bt_member_cols")
        mine = _booking()
        mine.member = member
        mine.save()
        content = _tab(client, show="reply", search="x", status="declined")
        assert "Member</span>" not in content
        assert "data-bookings-filters" not in content
        assert reverse("hub_orientations_export") not in content
        assert 'data-bookings-chip="reply"' not in content
        # Needs a Reply is a staff chip: a member asking for it gets Upcoming, filters ignored.
        assert 'data-bookings-chip="upcoming" aria-current="true"' in content
        assert _in_table(content, mine)

    def it_lists_their_own_recorded_orientations(client: Client):
        member = _login(client, "bt_member_records")
        record = OrientationRecordFactory(member=member)
        content = _tab(client)
        assert f'data-record-row="{record.pk}"' in content
        assert "pl-bookings-records" in content

    def it_shows_the_empty_states(client: Client):
        _login(client, "bt_member_empty")
        assert "Pick one under" in _tab(client)
        assert "@click.prevent=\"setPane('list')\"" in _tab(client)
        assert "Orientations you have been to show up here." in _tab(client, show="past")
        assert "pl-orient-records" not in _tab(client)

    def it_gives_their_own_rows_the_member_menu(client: Client):
        member = _login(client, "bt_member_menu")
        requested = _booking()
        requested.member = member
        requested.save()
        content = _tab(client)
        row = _row(content, requested)
        assert ">You<" not in row  # no Member column for members
        assert requested.orientation_type.orientation_anchor_path().replace("&", "&amp;") in row
        assert f"'booking-cancel-mine-{requested.pk}'" in row
        assert reverse("hub_orientation_respond", args=[requested.pk]) not in row
        assert reverse("hub_orientation_checkout_resume", args=[requested.pk]) not in row
        # The cancel confirm posts next, unboosted, back to the tab.
        modal_start = content.index(f'action="{reverse("hub_orientation_cancel_mine", args=[requested.pk])}"')
        modal = content[modal_start : content.index("</form>", modal_start)]
        assert 'hx-boost="false"' in modal
        assert 'name="next" value="/orientations/?view=bookings"' in modal

    def it_offers_finish_paying_and_release_on_a_checkout_in_progress(client: Client):
        member = _login(client, "bt_member_hold")
        hold = _booking(status=OrientationBooking.Status.PENDING_PAYMENT, amount_paid_cents=1500)
        hold.member = member
        hold.save()
        row = _row(_tab(client), hold)
        assert reverse("hub_orientation_checkout_resume", args=[hold.pk]) in row
        assert f"'booking-release-{hold.pk}'" in row
        assert f"'booking-cancel-mine-{hold.pk}'" not in row

    def it_offers_pay_late_fee_on_an_unpaid_fee_and_no_cancel_on_a_past_booking(client: Client):
        member = _login(client, "bt_member_fee")
        cancelled = _booking(status=OrientationBooking.Status.CANCELLED, days=-1)
        cancelled.member = member
        cancelled.save()
        fee = LateCancellationFeeFactory(orientation_booking=cancelled, member=member)
        content = _tab(client, show="past")
        row = _row(content, cancelled)
        assert reverse("hub_late_fee_detail", args=[fee.pk]) in row
        assert "data-booking-fee" in row
        assert "booking-cancel-mine" not in row
        assert f"waive-fee-{fee.pk}" not in content

    def it_warns_of_a_late_fee_in_the_cancel_confirm(client: Client):
        site = SiteConfiguration.load()
        site.late_cancel_fees_enabled = True
        site.save()
        member = _login(client, "bt_member_late")
        guild = GuildFactory()
        GuildOrientationSettings.objects.update_or_create(
            guild=guild, defaults={"is_enabled": True, "late_cancel_fee_cents": 1500}
        )
        soon = _booking(guild, days=0.1, status=OrientationBooking.Status.CONFIRMED, amount_paid_cents=2500)
        soon.member = member
        soon.save()
        content = _tab(client)
        assert "late cancellation fee applies" in content
        assert "You&#x27;ll get an automatic full refund." in content

    def it_sees_nothing_without_a_member_row(client: Client):
        user = _user("bt_unlinked")
        client.login(username="bt_unlinked", password="pass")
        Member.objects.filter(pk=user.member.pk).delete()
        _booking(name="Unseen Personname")
        content = _tab(client)
        assert "Unseen Personname" not in content
        assert "data-bookings-empty" in content


def describe_a_guild_lead():
    def it_sees_their_guilds_bookings_and_their_own_and_nothing_else(client: Client):
        lead = _login(client, "bt_lead")
        guild_x = GuildFactory(name="Lead X Guild", guild_lead=lead)
        guild_y = GuildFactory(name="Other Y Guild")
        in_x = _booking(guild_x, name="Xavier Inscope")
        in_y = _booking(guild_y, name="Yolanda Outscope")
        own_in_y = _booking(guild_y)
        own_in_y.member = lead
        own_in_y.save()
        tool = _equipment_booking(EquipmentFactory(name="Unmanaged Saw"), name="Toolie Outscope")
        content = _tab(client, show="all")
        assert 'data-bookings-pane="staff"' in content
        assert _in_table(content, in_x)
        assert _in_table(content, own_in_y)
        assert not _in_table(content, in_y)
        assert not _in_table(content, tool)
        # Their own row on an unmanaged guild reads "You" and gets the member menu.
        own_row = _row(content, own_in_y)
        assert 'data-label="Member">You</td>' in own_row
        assert reverse("hub_orientation_respond", args=[own_in_y.pk]) not in own_row
        assert f"'booking-cancel-mine-{own_in_y.pk}'" in own_row
        # The staff menu on the managed row.
        assert reverse("hub_orientation_respond", args=[in_x.pk]) in _row(content, in_x)

    def it_scopes_the_export_and_the_recorded_list(client: Client):
        lead = _login(client, "bt_lead_export")
        guild_x = GuildFactory(name="Export X Guild", guild_lead=lead)
        guild_y = GuildFactory(name="Export Y Guild")
        _booking(guild_x, name="Xena Exportin")
        _booking(guild_y, name="Yusuf Exportout")
        response = client.get(reverse("hub_orientations_export"), {"show": "all"})
        body = b"".join(response.streaming_content).decode()
        assert response["Content-Type"] == "text/csv"
        assert "Xena Exportin" in body
        assert "Yusuf Exportout" not in body
        OrientationRecordFactory(
            member=MemberFactory(full_legal_name="Rex Recordin"), orientation_type=OrientationTypeFactory(guild=guild_x)
        )
        OrientationRecordFactory(
            member=MemberFactory(full_legal_name="Ray Recordout"),
            orientation_type=OrientationTypeFactory(guild=guild_y),
        )
        content = _tab(client, oriented="yes")
        records = content[
            content.index("pl-orient-records") : content.index("</table>", content.index("pl-orient-records"))
        ]
        assert "Rex Recordin" in records
        assert "Ray Recordout" not in records

    def it_offers_only_guilds_in_scope_in_the_guild_filter(client: Client):
        lead = _login(client, "bt_lead_guilds")
        mine = GuildFactory(name="Mine Filter Guild", guild_lead=lead)
        staffed = GuildFactory(name="Staffed Filter Guild")
        GuildStaffMembershipFactory(guild=staffed, member=lead, role=GuildStaffMembership.Role.ORIENTER)
        GuildFactory(name="Foreign Filter Guild")
        content = _tab(client)
        start = content.index('id="bookings-guild"')
        select = content[start : content.index("</select>", start)]
        assert f'<option value="{mine.pk}"' in select
        assert f'<option value="{staffed.pk}"' in select
        assert "Foreign Filter Guild" not in select

    def it_gets_403_acting_on_another_guilds_booking(client: Client):
        lead = _login(client, "bt_lead_probe")
        GuildFactory(guild_lead=lead)
        other = _booking(GuildFactory(), status=OrientationBooking.Status.REQUESTED)
        confirmed = _booking(GuildFactory(), status=OrientationBooking.Status.CONFIRMED)
        probes = [
            (reverse("hub_orientation_toggle_completed", args=[confirmed.pk]), {"completed": "1"}),
            (reverse("hub_orientation_respond", args=[other.pk]), {"action": "confirm"}),
            (reverse("hub_orientation_respond", args=[other.pk]), {"action": "decline"}),
            (reverse("hub_orientation_lead_cancel", args=[confirmed.pk]), {}),
        ]
        for url, data in probes:
            assert client.post(url, data).status_code == 403, url
        other.refresh_from_db()
        confirmed.refresh_from_db()
        assert other.status == OrientationBooking.Status.REQUESTED
        assert confirmed.status == OrientationBooking.Status.CONFIRMED
        assert confirmed.is_completed is False

    def it_is_refused_a_refund_without_refund_authority(client: Client):
        lead = _login(client, "bt_lead_refund")
        guild = GuildFactory(guild_lead=lead)
        paid = _booking(guild, amount_paid_cents=1500, stripe_payment_id="pi_lead")
        assert client.post(reverse("billing_orientation_refund", args=[paid.pk]), {"amount": "15"}).status_code == 403


def describe_officers_and_admins():
    def it_shows_an_officer_every_guild_booking_but_no_equipment_one(client: Client):
        _login(client, "bt_officer", fog_role=Member.FogRole.GUILD_OFFICER)
        alpha = _booking(GuildFactory(), name="Officer Alphasee")
        beta = _booking(GuildFactory(), name="Officer Betasee")
        tool = _equipment_booking(EquipmentFactory(), name="Officer Toolnot")
        content = _tab(client)
        assert _in_table(content, alpha) and _in_table(content, beta)
        assert not _in_table(content, tool)

    def it_shows_an_admin_everything(client: Client):
        _login(client, "bt_admin", fog_role=Member.FogRole.ADMIN)
        guild_row = _booking(GuildFactory())
        tool = _equipment_booking(EquipmentFactory(name="Admin Big Laser"), name="Admin Toolsee")
        content = _tab(client)
        assert _in_table(content, guild_row) and _in_table(content, tool)
        assert "Admin Big Laser" in content

    def it_gives_an_equipment_manager_the_staff_view_of_their_tools_orientations(client: Client):
        manager = _login(client, "bt_tool_staff")
        tool = EquipmentFactory(name="Managed Drill")
        EquipmentStaffMembershipFactory(equipment=tool, member=manager)
        managed = _equipment_booking(tool, name="Drill Bookingperson")
        other = _equipment_booking(EquipmentFactory(), name="Other Toolperson")
        content = _tab(client)
        assert 'data-bookings-pane="staff"' in content
        assert _in_table(content, managed)
        assert not _in_table(content, other)
        assert "data-bookings-add-member" not in content  # no guild slots to seat anyone in

    def it_keeps_a_previewing_admin_to_their_own_rows(client: Client):
        admin = _login(client, "bt_preview", fog_role=Member.FogRole.ADMIN)
        session = client.session
        session["view_as_role"] = "member"
        session.save()
        stranger = _booking(name="Preview Strangerperson")
        own = _booking()
        own.member = admin
        own.save()
        content = _tab(client)
        assert 'data-bookings-pane="member"' in content
        assert _in_table(content, own)
        assert not _in_table(content, stranger)


def _menu_world(client: Client, *, refunds: bool = False) -> tuple[Member, Any]:
    lead = _login(client, "bt_menu_lead", name="Lead Menuperson")
    if refunds:
        lead.admin_capabilities.create(capability=AdminCapability.Capability.REFUNDS)
    return lead, GuildFactory(guild_lead=lead)


def describe_the_staff_row_menu():
    def it_offers_confirm_and_decline_only_on_a_request(client: Client):
        _lead, guild = _menu_world(client)
        requested = _booking(guild, status=OrientationBooking.Status.REQUESTED, amount_paid_cents=2500)
        confirmed = _booking(guild, status=OrientationBooking.Status.CONFIRMED)
        content = _tab(client)
        row = _row(content, requested)
        assert f"'booking-confirm-{requested.pk}'" in row
        assert f"'booking-decline-{requested.pk}'" in row
        assert f"'booking-mark-{requested.pk}'" not in row
        assert "booking-confirm-" not in _row(content, confirmed)
        assert "Their $25 is refunded automatically." in content

    def it_offers_mark_oriented_and_cancel_on_an_upcoming_confirmed_booking(client: Client):
        _lead, guild = _menu_world(client)
        confirmed = _booking(guild, status=OrientationBooking.Status.CONFIRMED, amount_paid_cents=1500)
        row = _row(_tab(client), confirmed)
        assert f"'booking-mark-{confirmed.pk}'" in row
        assert f"'booking-undo-{confirmed.pk}'" not in row
        assert f"'booking-cancel-{confirmed.pk}'" in row

    def it_offers_undo_on_an_oriented_booking_and_no_cancel_once_past(client: Client):
        _lead, guild = _menu_world(client)
        done = _booking(guild, days=-2, status=OrientationBooking.Status.CONFIRMED, is_completed=True)
        content = _tab(client, show="past")
        row = _row(content, done)
        assert f"'booking-undo-{done.pk}'" in row
        assert f"'booking-mark-{done.pk}'" not in row
        assert f"'booking-cancel-{done.pk}'" not in row
        assert "data-booking-oriented" in row

    def it_offers_only_view_request_on_a_checkout_in_progress(client: Client):
        _lead, guild = _menu_world(client, refunds=True)
        hold = _booking(guild, status=OrientationBooking.Status.PENDING_PAYMENT, amount_paid_cents=1500)
        row = _row(_tab(client), hold)
        assert reverse("hub_orientation_respond", args=[hold.pk]) in row
        assert row.count('class="pl-row-menu__item') == 1

    def it_hides_refund_without_refund_authority(client: Client):
        _lead, guild = _menu_world(client)
        paid = _booking(
            guild, status=OrientationBooking.Status.CONFIRMED, amount_paid_cents=1500, stripe_payment_id="pi_a"
        )
        content = _tab(client)
        assert reverse("billing_orientation_refund_form", args=[paid.pk]) not in content
        assert 'id="refund-modal-body"' not in content

    def it_offers_refund_and_retry_with_refund_authority(client: Client):
        _lead, guild = _menu_world(client, refunds=True)
        paid = _booking(
            guild, status=OrientationBooking.Status.CONFIRMED, amount_paid_cents=1500, stripe_payment_id="pi_b"
        )
        failed = _booking(
            guild, status=OrientationBooking.Status.CANCELLED, amount_paid_cents=2000, stripe_payment_id="pi_c"
        )
        PaymentRefund.objects.create(orientation_booking=failed, amount_cents=2000, status=PaymentRefund.Status.FAILED)
        refunded = _booking(
            guild, status=OrientationBooking.Status.CANCELLED, amount_paid_cents=900, stripe_payment_id="pi_d"
        )
        PaymentRefund.objects.create(
            orientation_booking=refunded, amount_cents=900, status=PaymentRefund.Status.SUCCEEDED
        )
        free = _booking(guild, status=OrientationBooking.Status.CONFIRMED)
        content = _tab(client, show="all")
        assert 'id="refund-modal-body"' in content
        assert ">Refund</button>" in _row(content, paid)
        assert ">Retry Refund</button>" in _row(content, failed)
        assert reverse("billing_orientation_refund_form", args=[refunded.pk]) not in content
        assert reverse("billing_orientation_refund_form", args=[free.pk]) not in content
        assert f'hx-get="{PARTIAL}?view=bookings&amp;show=all"' in content

    def it_offers_waive_on_an_unpaid_fee_and_refund_on_a_paid_one(client: Client):
        _lead, guild = _menu_world(client, refunds=True)
        unpaid_on = _booking(guild, status=OrientationBooking.Status.CANCELLED)
        unpaid = LateCancellationFeeFactory(orientation_booking=unpaid_on)
        paid_on = _booking(guild, status=OrientationBooking.Status.CANCELLED)
        paid = LateCancellationFeeFactory(
            orientation_booking=paid_on, status=LateCancellationFee.Status.PAID, stripe_payment_id="pi_fee"
        )
        content = _tab(client, show="all")
        assert f"'waive-fee-{unpaid.pk}'" in _row(content, unpaid_on)
        assert 'id="waive-fee-' in content
        assert 'name="next" value="/orientations/?view=bookings&amp;show=all"' in content
        assert reverse("billing_late_fee_refund_form", args=[paid.pk]) in _row(content, paid_on)
        assert reverse("billing_late_fee_refund_form", args=[unpaid.pk]) not in content

    def it_shows_email_member_only_where_the_directory_allows_it(client: Client):
        _lead, guild = _menu_world(client)
        public = _booking(guild)
        hidden = _booking(guild)
        hidden.member.directory_visibility = {"email": False}
        hidden.member.save(update_fields=["directory_visibility"])
        content = _tab(client)
        assert "mailto:" in _row(content, public)
        assert "mailto:" not in _row(content, hidden)
        assert reverse("hub_admin_member_edit", args=[public.member.pk]) not in content

    def it_gives_an_admin_email_and_view_member(client: Client):
        _login(client, "bt_admin_menu", fog_role=Member.FogRole.ADMIN)
        hidden = _booking(GuildFactory())
        hidden.member.directory_visibility = {"email": False}
        hidden.member.save(update_fields=["directory_visibility"])
        row = _row(_tab(client), hidden)
        assert "mailto:" in row
        assert f"{reverse('hub_admin_member_edit', args=[hidden.member.pk])}?tab=orientations" in row


def describe_chips_filters_and_sort():
    def it_defaults_to_upcoming_and_counts_requests_waiting(client: Client):
        _lead, guild = _menu_world(client)
        requested = _booking(guild, status=OrientationBooking.Status.REQUESTED)
        old_request = _booking(guild, days=-5, status=OrientationBooking.Status.REQUESTED)
        declined = _booking(guild, status=OrientationBooking.Status.DECLINED)
        content = _tab(client)
        assert _in_table(content, requested)
        assert not _in_table(content, old_request)
        assert not _in_table(content, declined)
        assert ">Needs a Reply (2)</a>" in content
        reply = _tab(client, show="reply")
        assert _in_table(reply, requested) and _in_table(reply, old_request)
        assert not _in_table(reply, declined)

    def it_hides_the_reply_chip_when_nothing_waits(client: Client):
        _menu_world(client)
        assert 'data-bookings-chip="reply"' not in _tab(client)
        assert 'data-bookings-chip="reply"' in _tab(client, show="reply")

    def it_sorts_past_newest_first(client: Client):
        _lead, guild = _menu_world(client)
        older = _booking(guild, days=-9, name="Older Pastperson")
        newer = _booking(guild, days=-2, name="Newer Pastperson")
        content = _tab(client, show="past")
        assert content.index(f'data-booking-row="{newer.pk}"') < content.index(f'data-booking-row="{older.pk}"')

    def it_falls_back_to_the_default_sort_for_an_unknown_key(client: Client):
        _lead, guild = _menu_world(client)
        row = _booking(guild)
        content = _tab(client, sort="member__user__password", dir="desc")
        assert _in_table(content, row)

    def it_sorts_by_member_name_for_staff(client: Client):
        _lead, guild = _menu_world(client)
        zed = _booking(guild, name="Zed Sortperson", days=1)
        abe = _booking(guild, name="Abe Sortperson", days=3)
        content = _tab(client, sort="member__full_legal_name")
        assert content.index(f'data-booking-row="{abe.pk}"') < content.index(f'data-booking-row="{zed.pk}"')
        assert "sort=member__full_legal_name" in content

    def it_applies_the_filters_and_ignores_bad_values(client: Client):
        lead, guild = _menu_world(client)
        other = GuildFactory()
        GuildStaffMembershipFactory(guild=other, member=lead, role=GuildStaffMembership.Role.ORIENTER)
        target = _booking(
            guild, days=4, status=OrientationBooking.Status.CONFIRMED, is_completed=True, name="Filter Target"
        )
        elsewhere = _booking(other, days=4, status=OrientationBooking.Status.CONFIRMED, name="Filter Elsewhere")
        day = timezone.localtime(target.slot.starts_at).date().isoformat()
        content = _tab(client, guild=str(guild.pk), status="confirmed", oriented="yes", start=day, end=day)
        assert _in_table(content, target) and not _in_table(content, elsewhere)
        assert not _in_table(_tab(client, oriented="no"), target)
        assert not _in_table(_tab(client, status="requested"), target)
        later = (timezone.localdate() + timedelta(days=30)).isoformat()
        assert not _in_table(_tab(client, start=later), target)
        assert not _in_table(_tab(client, end="2001-01-01"), target)
        loose = _tab(client, guild="abc", status="bogus", oriented="maybe", start="2026-02-30", end="not-a-date")
        assert _in_table(loose, target) and _in_table(loose, elsewhere)

    def it_searches_member_and_orientation_names(client: Client):
        _lead, guild = _menu_world(client)
        found = _booking(guild, name="Searchable Personname")
        missed = _booking(guild, name="Hidden Othername")
        content = _tab(client, search="Searchable")
        assert _in_table(content, found) and not _in_table(content, missed)
        assert "Nothing matches those filters." not in content
        empty = _tab(client, search="nobody-matches-this")
        assert "Nothing matches those filters." in empty
        assert 'href="?view=bookings">Clear filters</a>' in empty

    def it_says_nothing_is_booked_without_filters(client: Client):
        _menu_world(client)
        assert "No orientations are booked yet." in _tab(client)

    def it_keeps_the_list_panes_search_out_of_the_tab(client: Client):
        _lead, guild = _menu_world(client)
        row = _booking(guild)
        content = _tab(client, q="lathe", owner="guild")
        assert _in_table(content, row)
        assert "q=lathe" not in content.split("data-bookings-pane", 1)[1]

    def it_paginates_and_returns_to_the_page_it_was_on(client: Client):
        _lead, guild = _menu_world(client)
        for _ in range(26):
            _booking(guild, status=OrientationBooking.Status.CONFIRMED)
        content = _tab(client, page="2")
        assert content.count("data-booking-row=") == 1
        assert 'name="next" value="/orientations/?view=bookings&amp;page=2"' in content


def describe_the_staff_extras():
    def it_nudges_a_lead_with_no_hours(client: Client):
        lead, guild = _menu_world(client)
        content = _tab(client)
        assert "You have not posted any orientation hours yet." in content
        assert f"guilds/{guild.pk}/edit/?tab=orientations" in content
        assert lead is not None

    def it_opens_add_member_in_a_modal_with_the_paid_note(client: Client):
        _lead, guild = _menu_world(client)
        settings_row = GuildOrientationSettings.objects.get_or_create(guild=guild)[0]
        orientation_type = OrientationTypeFactory(guild=guild, price_cents=1500)
        slot = OrientationSlotFactory(guild=guild, orientation_type=orientation_type)
        response = client.get(PAGE, {"view": "bookings"})
        content = response.content.decode()
        assert "data-bookings-add-member" in content
        assert 'id="orientation-add-member-body"' in content
        assert "form[action='/orientations/manage/add-member/']" not in content
        assert 'action="/orientations/manage/add-member/"' in content
        assert f'"{slot.pk}": "$15"' in response.context["paid_slot_prices_json"]
        assert settings_row is not None

    def it_serves_an_admin_whose_member_row_is_gone(client: Client):
        user = _user("bt_rowless", fog_role=Member.FogRole.ADMIN)
        client.force_login(user)
        Member.objects.filter(pk=user.member.pk).delete()
        content = _tab(client, show="past")
        assert 'data-bookings-pane="staff"' in content
        assert "You have not posted any orientation hours yet." not in content


def describe_query_counts():
    def it_costs_the_same_for_one_row_as_for_many(client: Client):
        _login(client, "bt_queries", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()

        def _paid_row() -> None:
            booking = _booking(guild, status=OrientationBooking.Status.CANCELLED, amount_paid_cents=1500)
            PaymentRefund.objects.create(
                orientation_booking=booking, amount_cents=500, status=PaymentRefund.Status.SUCCEEDED
            )
            LateCancellationFeeFactory(orientation_booking=booking, member=booking.member)

        _paid_row()
        _tab(client, show="all")  # warm the per process caches (site settings, session) first
        with CaptureQueriesContext(connection) as one:
            _tab(client, show="all")
        for _ in range(9):
            _paid_row()
        with CaptureQueriesContext(connection) as ten:
            _tab(client, show="all")
        assert len(ten.captured_queries) == len(one.captured_queries)


def describe_the_old_dashboard_url():
    def it_redirects_to_the_tab_with_its_query(client: Client):
        _login(client, "bt_redirect")
        response = client.get("/orientations/manage/?status=requested")
        assert response.status_code == 302
        assert response["Location"] == "/orientations/?view=bookings&status=requested"

    def it_redirects_without_a_query(client: Client):
        _login(client, "bt_redirect_bare")
        assert client.get("/orientations/manage/")["Location"] == "/orientations/?view=bookings"


def describe_the_export():
    def it_streams_a_csv_for_an_admin(client: Client):
        _login(client, "bt_x_admin", fog_role=Member.FogRole.ADMIN)
        _booking(name="Csv Personname")
        response = client.get(reverse("hub_orientations_export"))
        body = b"".join(response.streaming_content).decode()
        assert response["Content-Type"] == "text/csv"
        assert "Member" in body.splitlines()[0]
        assert "Csv Personname" in body

    def it_follows_the_chip_and_filters(client: Client):
        _login(client, "bt_x_filters", fog_role=Member.FogRole.ADMIN)
        _booking(name="Csv Upcomingperson")
        _booking(name="Csv Pastperson", days=-3)
        body = b"".join(client.get(reverse("hub_orientations_export"), {"show": "past"}).streaming_content).decode()
        assert "Csv Pastperson" in body and "Csv Upcomingperson" not in body
        body = b"".join(
            client.get(reverse("hub_orientations_export"), {"show": "all", "search": "Upcoming"}).streaming_content
        ).decode()
        assert "Csv Upcomingperson" in body and "Csv Pastperson" not in body

    def it_forbids_a_member_who_runs_nothing(client: Client):
        _login(client, "bt_x_member")
        assert client.get(reverse("hub_orientations_export")).status_code == 403


def describe_add_member():
    def it_adds_a_member_and_lands_on_next(client: Client):
        _login(client, "bt_am_admin", fog_role=Member.FogRole.ADMIN)
        slot = OrientationSlotFactory()
        member = MemberFactory(full_legal_name="Added Personname")
        response = client.post(
            reverse("hub_orientation_add_member"),
            {"member": member.pk, "slot": slot.pk, "next": "/orientations/?view=bookings&show=all"},
            follow=True,
        )
        assert response.redirect_chain[0][0] == "/orientations/?view=bookings&show=all"
        assert OrientationBooking.objects.filter(member=member, slot=slot).exists()
        assert any(str(m) == "Added Added Personname. We emailed them." for m in response.context["messages"])

    def it_lands_on_the_tab_by_default_and_ignores_an_off_site_next(client: Client):
        _login(client, "bt_am_bad", fog_role=Member.FogRole.ADMIN)
        response = client.post(reverse("hub_orientation_add_member"), {"next": "https://evil.example/"})
        assert response["Location"] == "/orientations/?view=bookings"
        assert OrientationBooking.objects.count() == 0

    def it_flashes_an_error_on_invalid_input(client: Client):
        _login(client, "bt_am_err", fog_role=Member.FogRole.ADMIN)
        response = client.post(reverse("hub_orientation_add_member"), {}, follow=True)
        assert any("Couldn't add the member. Pick" in str(m) for m in response.context["messages"])

    def it_forbids_a_member_who_runs_nothing(client: Client):
        _login(client, "bt_am_reg")
        slot = OrientationSlotFactory()
        response = client.post(reverse("hub_orientation_add_member"), {"member": MemberFactory().pk, "slot": slot.pk})
        assert response.status_code == 403
        assert OrientationBooking.objects.count() == 0

    def it_rejects_get_requests(client: Client):
        _login(client, "bt_am_get", fog_role=Member.FogRole.ADMIN)
        assert client.get(reverse("hub_orientation_add_member")).status_code == 405

    def it_lets_an_admin_add_a_member_to_an_equipment_slot(client: Client):
        _login(client, "bt_am_eq", fog_role=Member.FogRole.ADMIN)
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=EquipmentFactory())
        slot = OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)
        member = MemberFactory()
        assert (
            client.post(reverse("hub_orientation_add_member"), {"member": member.pk, "slot": slot.pk}).status_code
            == 302
        )
        assert OrientationBooking.objects.get(member=member).guild is None


def describe_mark_oriented():
    def it_sets_the_state_it_is_told_and_lands_on_next(client: Client):
        _login(client, "bt_tc_admin", fog_role=Member.FogRole.ADMIN)
        booking = _booking(status=OrientationBooking.Status.CONFIRMED)
        url = reverse("hub_orientation_toggle_completed", args=[booking.pk])
        response = client.post(url, {"completed": "1", "next": "/orientations/?view=bookings&show=past"})
        assert response["Location"] == "/orientations/?view=bookings&show=past"
        client.post(url, {"completed": "1"})  # a second Mark Oriented leaves it oriented
        booking.refresh_from_db()
        assert booking.is_completed is True
        response = client.post(url, {"completed": "0", "next": "https://evil.example/"})
        assert response["Location"] == "/orientations/?view=bookings"
        client.post(url, {"completed": "0"})
        booking.refresh_from_db()
        assert booking.is_completed is False

    def it_says_what_changed(client: Client):
        _login(client, "bt_tc_msg", fog_role=Member.FogRole.ADMIN)
        booking = _booking(status=OrientationBooking.Status.CONFIRMED, name="Marked Personname")
        url = reverse("hub_orientation_toggle_completed", args=[booking.pk])
        marked = client.post(url, {"completed": "1"}, follow=True)
        assert any(str(m) == "Marked Marked Personname as oriented." for m in marked.context["messages"])
        undone = client.post(url, {"completed": "0"}, follow=True)
        assert any(str(m) == "Marked Personname is no longer marked oriented." for m in undone.context["messages"])

    def it_refuses_a_missing_state(client: Client):
        _login(client, "bt_tc_bad", fog_role=Member.FogRole.ADMIN)
        booking = _booking()
        assert client.post(reverse("hub_orientation_toggle_completed", args=[booking.pk])).status_code == 400

    def it_forbids_a_member_who_runs_nothing(client: Client):
        _login(client, "bt_tc_reg")
        booking = _booking()
        url = reverse("hub_orientation_toggle_completed", args=[booking.pk])
        assert client.post(url, {"completed": "1"}).status_code == 403

    def it_rejects_get_requests(client: Client):
        _login(client, "bt_tc_get", fog_role=Member.FogRole.ADMIN)
        assert client.get(reverse("hub_orientation_toggle_completed", args=[_booking().pk])).status_code == 405


def describe_respond_and_cancel_land_back():
    def it_returns_a_confirm_to_next(client: Client):
        _login(client, "bt_rs_confirm", fog_role=Member.FogRole.ADMIN)
        booking = _booking(status=OrientationBooking.Status.REQUESTED)
        url = reverse("hub_orientation_respond", args=[booking.pk])
        response = client.post(url, {"action": "confirm", "next": "/orientations/?view=bookings"}, follow=True)
        assert response.redirect_chain[0][0] == "/orientations/?view=bookings"
        assert any(str(m) == "Orientation confirmed. We emailed the member." for m in response.context["messages"])
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CONFIRMED

    def it_returns_a_decline_to_the_respond_page_without_next(client: Client):
        _login(client, "bt_rs_decline", fog_role=Member.FogRole.ADMIN)
        booking = _booking(status=OrientationBooking.Status.REQUESTED)
        url = reverse("hub_orientation_respond", args=[booking.pk])
        response = client.post(url, {"action": "decline", "note": "Try Tuesday", "next": "//evil.example/"})
        assert response["Location"] == url
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.DECLINED

    def it_returns_a_lead_cancel_to_next(client: Client):
        _login(client, "bt_rs_cancel", fog_role=Member.FogRole.ADMIN)
        booking = _booking(status=OrientationBooking.Status.CONFIRMED)
        url = reverse("hub_orientation_lead_cancel", args=[booking.pk])
        response = client.post(url, {"next": "/orientations/?view=bookings&show=all"}, follow=True)
        assert response.redirect_chain[0][0] == "/orientations/?view=bookings&show=all"
        assert any(str(m) == "Orientation cancelled. We let the member know." for m in response.context["messages"])

    def it_forbids_a_member_cancelling_someone_elses_booking(client: Client):
        _login(client, "bt_rs_mine")
        booking = _booking(status=OrientationBooking.Status.CONFIRMED)
        assert client.post(reverse("hub_orientation_cancel_mine", args=[booking.pk])).status_code == 403
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CONFIRMED


def describe_the_builders():
    def it_offers_no_guild_filter_options_without_a_member(rf: Any):
        from hub.orientation_bookings import _scope_guilds
        from hub.view_as import ROLE_MEMBER, ViewAs

        GuildFactory()
        request = rf.get(PAGE)
        request.view_as = ViewAs(actual=frozenset({ROLE_MEMBER}), picked=None)
        assert _scope_guilds(request, None) == []

    def it_gives_someone_elses_unmanaged_row_no_menu_flags():
        from hub.orientation_bookings import build_row

        booking = _booking(status=OrientationBooking.Status.PENDING_PAYMENT)
        LateCancellationFeeFactory(orientation_booking=_booking(status=OrientationBooking.Status.CANCELLED))
        row = build_row(
            booking, viewer=MemberFactory(), can_manage=False, refund_authority=True, is_admin=False, actual_admin=False
        )
        assert row.is_own is False
        assert not (row.can_finish_paying or row.can_cancel_mine or row.can_pay_fee or row.has_money_item)
