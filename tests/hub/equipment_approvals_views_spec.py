"""BDD specs for the screens of equipment that needs approval (#748).

The Hours & Limits switch, the member's schedule (Book a Time copy, the timeline's held
request, Your Reservations Here), the manage page's Needs Approval card, the approve and
decline endpoints (with crafted POSTs from non managers), and the Reservations page's
Bookings tab (the Needs Approval chip, the pills, the staff menu and the decline modal).
With the switch off every surface is checked to read as it did. Names are factory strings
no changelog could contain (STANDARDS.md §8).
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import SiteConfiguration
from membership.models import Equipment, EquipmentReservation, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

WAITING = EquipmentReservation.Status.PENDING_APPROVAL


def _login(client: Client, username: str, name: str = "") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name or f"{username.title()} Thistlewick"
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return member


def _day():
    return timezone.localdate() + timedelta(days=2)


def _at(day, hour: int, minute: int = 0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _tool(*, approval: bool = True, **kwargs) -> Equipment:
    equipment = EquipmentFactory(requires_approval=approval, **kwargs)
    EquipmentHoursFactory(equipment=equipment, weekday=_day().weekday(), start_time=time(9, 0), end_time=time(17, 0))
    return equipment


def _manage_login(client: Client, equipment: Equipment, username: str = "orvald", name: str = "") -> Member:
    member = _login(client, username, name)
    EquipmentStaffMembershipFactory(equipment=equipment, member=member)
    return member


def _request(equipment: Equipment, *, hour: int = 10, status: str = WAITING, **kwargs) -> EquipmentReservation:
    member = kwargs.pop("member", None) or MemberFactory(full_legal_name="Juniper Wrenhallow")
    return EquipmentReservationFactory(
        equipment=equipment,
        member=member,
        starts_at=_at(_day(), hour),
        ends_at=_at(_day(), hour + 1),
        status=status,
        **kwargs,
    )


def _messages(response) -> list[str]:
    return [str(message) for message in response.wsgi_request._messages]


def _toast(response) -> str:
    return json.loads(response["HX-Trigger"])["showToast"]["message"]


def _manage(client: Client, equipment: Equipment, tab: str = "reservations") -> str:
    response = client.get(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab={tab}")
    assert response.status_code == 200
    return response.content.decode()


def _schedule(client: Client, equipment: Equipment) -> str:
    return client.get(reverse("hub_equipment_schedule", args=[equipment.slug])).content.decode()


def describe_the_needs_approval_switch():
    def _settings(**overrides: str) -> dict[str, str]:
        return {
            "hours-TOTAL_FORMS": "0",
            "hours-INITIAL_FORMS": "0",
            "hours-MIN_NUM_FORMS": "0",
            "hours-MAX_NUM_FORMS": "1000",
            "reservations_open_shown": "1",
            "reservations_open": "on",
            "closed_message": "",
            "min_duration_minutes": "30",
            "max_duration_minutes": "240",
            "max_advance_days": "30",
            "max_active_reservations_per_member": "2",
            **overrides,
        }

    def it_sits_in_the_availability_card_under_active_with_the_save_still_last(client: Client):
        equipment = _tool(approval=False)
        _manage_login(client, equipment)
        content = _manage(client, equipment, "hours")
        card = content[content.index("data-availability-card") :]
        assert (
            card.index("data-reservations-open") < card.index("data-closed-message") < card.index("data-needs-approval")
        )
        assert "Every reservation waits for a manager to approve it before it is booked." in card
        form = content[
            content.index('id="equip-hours-form"') : content.index("</form>", content.index('id="equip-hours-form"'))
        ]
        assert form.index("data-needs-approval") < form.rindex('type="submit"')
        assert form.rstrip().endswith("</div>")
        assert 'name="requires_approval"' in form

    def it_turns_approval_on_and_shows_it_on_and_turns_it_off_again(client: Client):
        equipment = _tool(approval=False)
        _manage_login(client, equipment)
        url = reverse("hub_equipment_hours_save", args=[equipment.slug])
        client.post(url, _settings(requires_approval="on"))
        equipment.refresh_from_db()
        assert equipment.requires_approval is True
        content = _manage(client, equipment, "hours")
        switch = content[content.index("data-needs-approval") :]
        assert 'name="requires_approval"' in switch[: switch.index("</label>") + 200]
        assert "checked" in switch[: switch.index('name="requires_approval"') + 120]
        client.post(url, _settings())
        equipment.refresh_from_db()
        assert equipment.requires_approval is False

    def it_leaves_requests_already_waiting_when_turned_off(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        waiting = _request(equipment)
        client.post(reverse("hub_equipment_hours_save", args=[equipment.slug]), _settings())
        waiting.refresh_from_db()
        assert waiting.status == WAITING


def describe_the_member_schedule():
    def it_asks_for_a_request_on_equipment_that_needs_approval(client: Client):
        equipment = _tool(name="Glimmerforge Lathe")
        _login(client, "sched_req")
        content = _schedule(client, equipment)
        assert "data-approval-terms" in content
        assert "A manager approves each reservation before it is booked." in content
        assert "Request this time?" in content
        assert (
            "A manager approves each reservation on the Glimmerforge Lathe. We hold the time for you and "
            "email you when they decide. You can cancel any time."
        ) in content
        assert "Send request" in content
        assert "Reserve this time?" not in content

    def it_reads_as_it_always_did_with_the_switch_off(client: Client):
        equipment = _tool(approval=False)
        _login(client, "sched_instant")
        content = _schedule(client, equipment)
        assert "data-approval-terms" not in content
        assert "Request this time?" not in content
        assert "Reserve this time?" in content
        assert "Your reservation confirms right away." in content

    def it_sends_a_request_and_answers_with_a_toast(client: Client):
        equipment = _tool()
        member = _login(client, "sched_send")
        response = client.post(
            reverse("hub_equipment_reserve", args=[equipment.slug]),
            {"starts_at": _at(_day(), 10).isoformat(), "duration_minutes": "60", "purpose": "Sign blanks"},
        )
        assert _toast(response) == "Request sent. We'll email you when a manager decides."
        assert EquipmentReservation.objects.get(member=member).status == WAITING

    def it_confirms_a_managers_own_booking_with_the_instant_toast(client: Client):
        equipment = _tool()
        manager = _manage_login(client, equipment)
        response = client.post(
            reverse("hub_equipment_reserve", args=[equipment.slug]),
            {"starts_at": _at(_day(), 10).isoformat(), "duration_minutes": "60", "purpose": ""},
        )
        assert _toast(response).startswith("Reserved. See you ")
        assert EquipmentReservation.objects.get(member=manager).status == EquipmentReservation.Status.CONFIRMED

    def it_shows_a_manager_the_instant_copy_on_their_own_approval_equipment(client: Client):
        # reserve() books a manager instantly, so the page must not promise a wait (#748 review).
        equipment = _tool()
        _manage_login(client, equipment)
        content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        assert "data-approval-terms" not in content
        assert "Reserve this time?" in content
        assert "Request this time?" not in content

    def it_never_shows_an_undecided_request_as_in_use_on_the_index_card(client: Client):
        # The card's prefetch reads confirmed rows only, like Equipment.availability_line.
        _login(client, "card_viewer")
        now = timezone.now()
        span = {"starts_at": now - timedelta(minutes=30), "ends_at": now + timedelta(minutes=30)}
        waiting_tool = _tool(name="Waitwhistle Router")
        EquipmentReservationFactory(equipment=waiting_tool, status=WAITING, **span)
        confirmed_tool = _tool(name="Busybrass Press")
        EquipmentReservationFactory(equipment=confirmed_tool, **span)
        content = client.get(reverse("hub_equipment_index")).content.decode()
        assert "Waitwhistle Router" in content
        assert "Busybrass Press" in content
        # Only the confirmed tool's card reads in use.
        assert content.count("pl-equip-avail--busy") == 1
        assert content.count("Reserved until") == 1

    def it_shows_a_held_request_on_the_timeline_dashed_with_the_name(client: Client):
        equipment = _tool()
        _login(client, "sched_view")
        _request(equipment, purpose="Secret project")
        response = client.get(reverse("hub_equipment_schedule", args=[equipment.slug]), {"day": _day().isoformat()})
        content = response.content.decode()
        slot = content[content.index("data-pending-slot") - 120 : content.index("data-pending-slot") + 300]
        assert "pl-equip-slot--pending" in slot
        assert "Juniper Wrenhallow · Awaiting approval" in slot
        assert "Secret project" not in slot
        assert "· Awaiting approval</span>" in content[content.index("Upcoming Reservations") :]

    def it_lists_the_members_own_request_with_a_warn_badge_and_a_free_cancel(client: Client):
        equipment = _tool()
        config = SiteConfiguration.load()
        config.late_cancel_fees_enabled = True
        config.save()
        equipment.late_cancel_fee_cents = 1500
        equipment.save(update_fields=["late_cancel_fee_cents"])
        member = _login(client, "sched_mine")
        starts = timezone.now() + timedelta(hours=2)
        waiting = EquipmentReservationFactory(
            equipment=equipment, member=member, starts_at=starts, ends_at=starts + timedelta(hours=1), status=WAITING
        )
        content = _schedule(client, equipment)
        row = content[content.index('data-my-reservation-state="pending_approval"') :]
        row = row[: row.index("</li>")]
        assert "pl-equip-badge--warn" in row
        assert "Awaiting approval</span> A manager will approve or decline it." in row
        assert f"cancel-my-res-{waiting.pk}" in row
        assert "late cancellation fee" not in row
        response = client.post(
            reverse("hub_equipment_reservation_cancel", args=[equipment.slug, waiting.pk]), {"week": "0", "day": ""}
        )
        assert _toast(response) == "Reservation cancelled."
        waiting.refresh_from_db()
        assert waiting.status == EquipmentReservation.Status.CANCELLED

    def it_shows_a_declined_request_with_the_managers_reason_and_no_actions(client: Client):
        equipment = _tool()
        member = _login(client, "sched_declined")
        manager = MemberFactory(full_legal_name="Sami Brindlewood")
        _request(
            equipment,
            member=member,
            status=EquipmentReservation.Status.DECLINED,
            cancelled_by=manager,
            cancelled_reason="The spindle is out for repair.",
        )
        content = _schedule(client, equipment)
        row = content[content.index('data-my-reservation-state="declined"') :]
        row = row[: row.index("</li>")]
        assert "pl-equip-badge--declined" in row
        assert "Sami Brindlewood: The spindle is out for repair." in row
        assert "open-confirm" not in row


def describe_the_needs_approval_card():
    def it_comes_first_with_the_waiting_rows_soonest_first(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        later = _request(equipment, hour=14, purpose="Sign blanks")
        sooner = _request(equipment, hour=10)
        content = _manage(client, equipment)
        pane = content[content.index("section === 'reservations'") :]
        assert pane.index("data-needs-approval-card") < pane.index("data-block-time-card")
        assert "These members are waiting on you. The time is held for them until you decide." in pane
        assert pane.index(f'data-approval-row="{sooner.pk}"') < pane.index(f'data-approval-row="{later.pk}"')
        assert "Juniper Wrenhallow · Sign blanks" in pane
        assert reverse("hub_equipment_reservation_approve", args=[equipment.slug, later.pk]) in pane
        assert f"decline-res-{later.pk}" in content
        assert "Decline This Reservation?" in content
        assert "The member will see this. Please tell the member why." in content
        # A waiting row lists here, never under Upcoming Reservations.
        upcoming = pane[pane.index("data-upcoming-reservations-card") :]
        assert f"mgr-cancel-{later.pk}" not in upcoming

    def it_says_nothing_is_waiting_while_the_switch_is_on(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        content = _manage(client, equipment)
        assert "data-needs-approval-card" in content
        assert "Nothing is waiting for approval. New requests show here, and every manager gets an email." in content

    def it_stays_while_a_request_still_waits_after_the_switch_goes_off(client: Client):
        equipment = _tool(approval=False)
        _manage_login(client, equipment)
        _request(equipment)
        assert "data-needs-approval-card" in _manage(client, equipment)

    def it_is_absent_on_instant_equipment(client: Client):
        equipment = _tool(approval=False)
        _manage_login(client, equipment)
        content = _manage(client, equipment)
        assert "data-needs-approval-card" not in content
        assert "decline-res-" not in content


def describe_approve_and_decline_endpoints():
    def _approve(client: Client, reservation: EquipmentReservation, **data: str):
        return client.post(
            reverse("hub_equipment_reservation_approve", args=[reservation.equipment.slug, reservation.pk]), data
        )

    def _decline(client: Client, reservation: EquipmentReservation, **data: str):
        return client.post(
            reverse("hub_equipment_reservation_decline", args=[reservation.equipment.slug, reservation.pk]), data
        )

    def it_approves_and_names_the_member_in_the_toast(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        reservation = _request(equipment)
        response = _approve(client, reservation)
        assert response.status_code == 302
        assert response["Location"].endswith(f"/equipment/{equipment.slug}/manage/?tab=reservations")
        assert _messages(response) == ["Approved. Juniper has been emailed."]
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CONFIRMED

    def it_lands_back_on_a_posted_next(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        reservation = _request(equipment)
        response = _approve(client, reservation, next="/equipment/?view=bookings&show=needs_approval")
        assert response["Location"] == "/equipment/?view=bookings&show=needs_approval"

    def it_refuses_a_row_that_is_not_waiting_with_a_friendly_message(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        confirmed = _request(equipment, status=EquipmentReservation.Status.CONFIRMED)
        response = _approve(client, confirmed)
        assert _messages(response) == ["This reservation isn't waiting for approval any more."]
        response = _decline(client, confirmed, reason="No.")
        assert _messages(response)[-1] == "This reservation isn't waiting for approval any more."

    def it_refuses_a_crafted_post_from_a_member_who_does_not_manage_it(client: Client):
        equipment = _tool()
        _login(client, "crafty")
        reservation = _request(equipment)
        assert _approve(client, reservation).status_code == 403
        assert _decline(client, reservation, reason="Mine now.").status_code == 403
        reservation.refresh_from_db()
        assert reservation.status == WAITING

    def it_refuses_an_account_with_no_member(client: Client):
        equipment = _tool()
        user = User.objects.create_user(username="nomember", password="pass")
        Member.objects.filter(user=user).delete()
        client.login(username="nomember", password="pass")
        reservation = _request(equipment)
        assert _approve(client, reservation).status_code == 403

    def it_never_decides_a_managers_block(client: Client):
        equipment = _tool()
        manager = _manage_login(client, equipment)
        block = _request(equipment, member=manager, kind=EquipmentReservation.Kind.BLOCK)
        assert _approve(client, block).status_code == 404

    def it_refuses_a_decline_without_a_reason(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        reservation = _request(equipment)
        response = _decline(client, reservation, reason="   ")
        assert _messages(response) == ["Please tell the member why."]
        reservation.refresh_from_db()
        assert reservation.status == WAITING

    def it_declines_with_a_reason_and_names_the_member_in_the_toast(client: Client):
        equipment = _tool()
        manager = _manage_login(client, equipment)
        member = _login(Client(), "declinee", "Juniper Wrenhallow")
        reservation = _request(equipment, member=member)
        mail.outbox.clear()
        response = _decline(client, reservation, reason="The spindle is out for repair.")
        assert _messages(response) == ["Declined. Juniper has been emailed."]
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.DECLINED
        assert reservation.cancelled_by == manager
        assert [m.to for m in mail.outbox] == [[member.primary_email]]

    def it_lets_a_manager_cancel_a_waiting_request_through_the_reason_required_cancel(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        reservation = _request(equipment)
        response = client.post(
            reverse("hub_equipment_reservation_cancel", args=[equipment.slug, reservation.pk]),
            {"reason": "The shop is closed that day."},
        )
        assert _messages(response) == ["Reservation cancelled. The member has been told."]
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CANCELLED


def describe_the_bookings_tab():
    def _tab(client: Client, **params: str) -> str:
        response = client.get("/equipment/", {"view": "bookings", **params})
        assert response.status_code == 200
        return response.content.decode()

    def _row(content: str, reservation: EquipmentReservation) -> str:
        start = content.index(f'data-reservation-row="{reservation.pk}"')
        return content[start : content.index("</tr>", start)]

    def it_shows_the_needs_approval_chip_after_upcoming_with_its_count(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        _request(equipment, hour=10)
        _request(equipment, hour=12)
        content = _tab(client)
        chips = content[content.index('aria-label="Which reservations"') :]
        chips = chips[: chips.index("</div>")]
        assert chips.index('data-bookings-chip="upcoming"') < chips.index('data-bookings-chip="needs_approval"')
        assert chips.index('data-bookings-chip="needs_approval"') < chips.index('data-bookings-chip="past"')
        assert "data-bookings-chip-count>2</span>" in chips

    def it_counts_only_the_waiting_rows_the_filters_and_search_leave(client: Client):
        lathe = _tool(name="Glimmerforge Lathe")
        press = _tool(name="Busybrass Press")
        manager = _manage_login(client, lathe)
        EquipmentStaffMembershipFactory(equipment=press, member=manager)
        _request(lathe, hour=10)
        _request(press, hour=12, member=MemberFactory(full_legal_name="Ottoline Quarrystone"))
        assert "data-bookings-chip-count>2</span>" in _tab(client)
        content = _tab(client, equipment=str(lathe.pk))
        assert "data-bookings-chip-count>1</span>" in content
        content = _tab(client, show="needs_approval", search="Quarrystone")
        assert "data-bookings-chip-count>1</span>" in content
        assert content.count("data-reservation-row=") == 1

    def it_hides_the_chip_while_nothing_waits(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        _request(equipment, status=EquipmentReservation.Status.CONFIRMED)
        assert 'data-bookings-chip="needs_approval"' not in _tab(client)

    def it_lists_only_the_waiting_rows_under_the_chip(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        waiting = _request(equipment, hour=10)
        confirmed = _request(equipment, hour=12, status=EquipmentReservation.Status.CONFIRMED)
        content = _tab(client, show="needs_approval")
        assert f'data-reservation-row="{waiting.pk}"' in content
        assert f'data-reservation-row="{confirmed.pk}"' not in content

    def it_says_nothing_waits_on_a_stale_chip_link(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        assert "Nothing is waiting for approval." in _tab(client, show="needs_approval")

    def it_reads_awaiting_approval_as_warn_and_declined_as_neutral(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        waiting = _request(equipment, hour=10)
        declined = _request(equipment, hour=12, status=EquipmentReservation.Status.DECLINED)
        assert 'hub-pill--warn" data-reservation-status="pending_approval">Awaiting approval' in _row(
            _tab(client), waiting
        )
        assert 'hub-pill--neutral" data-reservation-status="declined">Declined' in _row(
            _tab(client, show="all"), declined
        )

    def it_offers_approve_and_decline_in_place_of_cancel_on_a_waiting_row(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        waiting = _request(equipment)
        content = _tab(client)
        row = _row(content, waiting)
        assert reverse("hub_equipment_reservation_approve", args=[equipment.slug, waiting.pk]) in row
        assert "data-approve-menu-item" in row
        assert f"open-confirm', 'decline-res-{waiting.pk}'" in row
        assert "Decline Reservation" in row
        assert f"reservation-cancel-{waiting.pk}" not in row
        modals = content[content.index("</table>") :]
        assert f"decline-res-{waiting.pk}" in modals
        assert "Decline This Reservation?" in modals
        assert reverse("hub_equipment_reservation_decline", args=[equipment.slug, waiting.pk]) in modals

    def it_shows_a_request_nobody_decided_as_not_decided_once_its_time_passed(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        starts = timezone.now() - timedelta(hours=3)
        undecided = EquipmentReservationFactory(
            equipment=equipment, starts_at=starts, ends_at=starts + timedelta(hours=1), status=WAITING
        )
        for show in ("past", "all"):
            row = _row(_tab(client, show=show), undecided)
            assert 'hub-pill--neutral" data-reservation-status="not-decided">Not decided' in row
            assert "Awaiting approval" not in row
            assert "data-approve-menu-item" not in row
        undecided.refresh_from_db()
        assert undecided.status == WAITING

    def it_never_puts_a_fee_line_on_a_members_own_waiting_cancel(client: Client):
        config = SiteConfiguration.load()
        config.late_cancel_fees_enabled = True
        config.late_cancel_grace_hours = 0
        config.save()
        member = _login(client, "feebooker")
        equipment = _tool(late_cancel_fee_cents=1500)
        starts = timezone.now() + timedelta(hours=2)
        span = {"starts_at": starts, "ends_at": starts + timedelta(hours=1)}
        waiting = EquipmentReservationFactory(equipment=equipment, member=member, status=WAITING, **span)
        confirmed = EquipmentReservationFactory(
            equipment=equipment, member=member, **{k: v + timedelta(hours=1) for k, v in span.items()}
        )
        content = _tab(client)

        def modal(reservation: EquipmentReservation) -> str:
            start = content.index(f"reservation-cancel-mine-{reservation.pk}", content.index("</table>"))
            return content[start : start + 3000]

        assert "late cancellation fee applies" not in modal(waiting)[: modal(waiting).index("Cancel Reservation")]
        # The control: the confirmed row inside the window does carry it.
        assert "late cancellation fee applies" in modal(confirmed)

    def it_keeps_needs_approval_a_staff_chip(client: Client):
        member = _login(client, "plainbooker")
        equipment = _tool()
        waiting = _request(equipment, member=member)
        content = _tab(client, show="needs_approval")
        assert 'data-bookings-chip="needs_approval"' not in content
        # A member's own waiting row is Upcoming, with a Cancel that carries no fee line.
        row = _row(content, waiting)
        assert f"reservation-cancel-mine-{waiting.pk}" in row
        assert "Approve" not in row

    def it_approves_from_the_menu_and_lands_back_on_the_tab(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        waiting = _request(equipment)
        response = client.post(
            reverse("hub_equipment_reservation_approve", args=[equipment.slug, waiting.pk]),
            {"next": "/equipment/?view=bookings"},
        )
        assert response["Location"] == "/equipment/?view=bookings"
        waiting.refresh_from_db()
        assert waiting.status == EquipmentReservation.Status.CONFIRMED
        assert 'data-reservation-status="confirmed"' in _row(_tab(client), waiting)
