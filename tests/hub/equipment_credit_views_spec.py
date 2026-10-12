"""BDD specs for the screens of hourly equipment credit (#787, part 1).

The Manage > Reservations tab's Give Credit card with Who Has Credit and Take back; the Bookings
tab's Credit Unused Time menu item, its modal and the "Credited 2h" pill; Book a Time's credit line,
total and the two confirms; the reserve endpoint's free and checkout roads with credit; the
member's "2h credit used" pills; and the Bookings tab's query count with credit rows. Stripe is
mocked. Names are factory strings no changelog could contain (STANDARDS.md §8).
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib import messages as django_messages
from django.contrib.auth.models import User
from django.core import mail
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from membership.models import Equipment, EquipmentCredit, EquipmentError, EquipmentReservation, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

_SESSION = {"id": "cs_credit_view", "url": "https://checkout.stripe.example/cs_credit_view"}


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = f"{username.title()} Larkspindle"
    member.fog_role = fog_role
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return member


def _member(username: str) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = f"{username.title()} Copperfen"
    member.save(update_fields=["status", "full_legal_name"])
    return member


def _day():
    return timezone.localdate() + timedelta(days=2)


def _at(day, hour: int, minute: int = 0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _hourly(**kwargs: Any) -> Equipment:
    kwargs.setdefault("name", "Gloamshire Router")
    equipment = EquipmentFactory(
        pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=2500, max_duration_minutes=480, **kwargs
    )
    EquipmentHoursFactory(equipment=equipment, weekday=_day().weekday(), start_time=time(8, 0), end_time=time(17, 0))
    return equipment


def _manager_of(client: Client, equipment: Equipment, username: str = "credkeeper") -> Member:
    manager = _login(client, username)
    EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
    return manager


def _give(equipment: Equipment, member: Member, minutes: int, note: str = "") -> EquipmentCredit:
    return EquipmentCredit.objects.create(
        equipment=equipment, member=member, minutes=minutes, kind=EquipmentCredit.Kind.GRANTED, note=note
    )


def _manage(client: Client, equipment: Equipment) -> str:
    response = client.get(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=reservations")
    assert response.status_code == 200
    return response.content.decode()


def _messages(response: Any) -> list[str]:
    return [str(m) for m in django_messages.get_messages(response.wsgi_request)]


def _credit_url(equipment: Equipment) -> str:
    return reverse("hub_equipment_credit_add", args=[equipment.slug])


def _started(equipment: Equipment, member: Member, *, hours: int = 8, **kwargs: Any) -> EquipmentReservation:
    starts = timezone.now() - timedelta(hours=1)
    return EquipmentReservationFactory(
        equipment=equipment, member=member, starts_at=starts, ends_at=starts + timedelta(hours=hours), **kwargs
    )


def _tab(client: Client, **params: str) -> str:
    response = client.get("/equipment/", {"view": "bookings", **params})
    assert response.status_code == 200
    return response.content.decode()


def _row(content: str, reservation: EquipmentReservation) -> str:
    start = content.index(f'data-reservation-row="{reservation.pk}"')
    return content[start : content.index("</tr>", start)]


def _schedule(client: Client, equipment: Equipment) -> str:
    return client.get(reverse("hub_equipment_schedule", args=[equipment.slug])).content.decode()


def _durations(content: str) -> dict[str, list[dict[str, Any]]]:
    return json.loads(content.split('id="equip-durations-data" type="application/json">')[1].split("</script>")[0])


def _reserve(client: Client, equipment: Equipment, *, hour: int = 8, minutes: int = 480):
    payload = {"starts_at": _at(_day(), hour).isoformat(), "duration_minutes": str(minutes), "purpose": ""}
    return client.post(reverse("hub_equipment_reserve", args=[equipment.slug]), payload, HTTP_HX_REQUEST="true")


def _toast(response: Any) -> tuple[str, str]:
    toast = json.loads(response["HX-Trigger"])["showToast"]
    return toast["message"], toast["type"]


def describe_the_give_credit_card():
    def it_shows_on_an_hourly_item_with_the_form_collapsed_and_the_empty_state(client: Client):
        router = _hourly()
        _manager_of(client, router)
        content = _manage(client, router)
        assert "data-give-credit-card" in content
        assert "creditOpen: false" in content
        assert "data-credit-empty>No one has credit here yet.</p>" in content
        assert 'list="equip-credit-member-options"' in content

    def it_is_absent_on_a_free_item(client: Client):
        saw = EquipmentFactory(name="Freely Saw")
        _manager_of(client, saw)
        assert "data-give-credit-card" not in _manage(client, saw)

    def it_lists_who_has_credit_with_hours_note_and_date(client: Client):
        router = _hourly()
        _manager_of(client, router)
        holder = _member("nettlebright")
        _give(router, holder, 120, note="Shop cabinet")
        content = _manage(client, router)
        assert f'data-credit-holder="{holder.pk}"' in content
        assert "data-credit-hours>2h</span>" in content
        assert "Shop cabinet ·" in content
        assert f"credit-take-back-{holder.pk}" in content
        assert "This takes back all 2h of Nettlebright Copperfen&#x27;s credit on the Gloamshire Router." in content


def describe_giving_credit():
    def it_gives_credit_emails_the_member_and_lists_them(client: Client):
        router = _hourly()
        _manager_of(client, router)
        holder = _member("wrenmarrow")
        mail.outbox.clear()

        response = client.post(
            _credit_url(router), {"member": "Wrenmarrow Copperfen", "hours": "2", "note": "Approved project"}
        )

        assert response.status_code == 302
        assert response.url.endswith("?tab=reservations")
        assert _messages(response) == ["Gave Wrenmarrow Copperfen 2 hours of credit. They have been emailed."]
        assert EquipmentCredit.objects.balance_for(holder, router) == 120
        assert [m.to for m in mail.outbox] == [["wrenmarrow@example.com"]]
        assert "data-credit-hours>2h</span>" in _manage(client, router)

    def it_takes_half_hours(client: Client):
        router = _hourly()
        _manager_of(client, router)
        holder = _member("halfmoss")
        client.post(_credit_url(router), {"member": "Halfmoss Copperfen", "hours": "1.5"})
        assert EquipmentCredit.objects.balance_for(holder, router) == 90

    @pytest.mark.parametrize(
        ("data", "error"),
        [
            ({"member": "Nobody Atall", "hours": "2"}, "Pick a member from the list."),
            ({"member": "Fernrue Copperfen", "hours": "1.25"}, "Use half hour steps, like 1.5 or 2."),
            ({"member": "Fernrue Copperfen", "hours": "101"}, "Ensure this value is less than or equal to 100."),
        ],
    )
    def it_shows_errors_inline_with_the_form_open(client: Client, data: dict[str, str], error: str):
        router = _hourly()
        _manager_of(client, router)
        _member("fernrue")
        response = client.post(_credit_url(router), data)
        assert response.status_code == 200
        content = response.content.decode()
        assert error in content
        assert "creditOpen: true" in content
        assert not EquipmentCredit.objects.exists()

    def it_shows_a_refused_grant_inline(client: Client):
        router = _hourly()
        _manager_of(client, router)
        _member("notelong")
        with patch(
            "membership.equipment.grant_credit",
            side_effect=EquipmentError("Nope here."),
        ):
            response = client.post(_credit_url(router), {"member": "Notelong Copperfen", "hours": "1"})
        assert response.status_code == 200
        assert '<p class="pl-field-error">Nope here.</p>' in response.content.decode()

    def it_refuses_a_member_who_does_not_manage_the_item(client: Client):
        router = _hourly()
        _login(client, "outsider")
        _member("target")
        response = client.post(_credit_url(router), {"member": "Target Copperfen", "hours": "2"})
        assert response.status_code == 403
        assert not EquipmentCredit.objects.exists()


def describe_taking_credit_back():
    def it_zeroes_the_balance_after_the_confirm(client: Client):
        router = _hourly()
        _manager_of(client, router)
        holder = _member("burrowfinch")
        _give(router, holder, 120)
        url = reverse("hub_equipment_credit_take_back", args=[router.slug, holder.pk])

        response = client.post(url)

        assert _messages(response) == ["Took back Burrowfinch Copperfen's 2 hours of credit."]
        assert EquipmentCredit.objects.balance_for(holder, router) == 0
        assert "No one has credit here yet." in _manage(client, router)

    def it_says_so_when_there_is_nothing_to_take_back(client: Client):
        router = _hourly()
        _manager_of(client, router)
        holder = _member("emptypocket")
        response = client.post(reverse("hub_equipment_credit_take_back", args=[router.slug, holder.pk]))
        assert _messages(response) == ["Emptypocket Copperfen has no credit here."]

    def it_refuses_a_non_manager(client: Client):
        router = _hourly()
        _login(client, "grabber")
        holder = _member("keeper")
        _give(router, holder, 60)
        response = client.post(reverse("hub_equipment_credit_take_back", args=[router.slug, holder.pk]))
        assert response.status_code == 403
        assert EquipmentCredit.objects.balance_for(holder, router) == 60


def describe_credit_unused_time():
    def it_offers_the_item_only_on_a_started_confirmed_hourly_booking_the_viewer_manages(client: Client):
        router = _hourly()
        _manager_of(client, router)
        started = _started(router, _member("startedone"))
        upcoming = EquipmentReservationFactory(equipment=router, member=_member("laterone"))
        free_started = _started(EquipmentFactory(name="Free Jig"), _member("freeone"))
        unmanaged = _started(_hourly(name="Other Mill"), _member("otherone"))
        content = _tab(client, show="all")
        assert "data-credit-unused-menu-item" in _row(content, started)
        assert f'id="credit-unused-{started.pk}-title"' in content
        assert "data-credit-unused-menu-item" not in _row(content, upcoming)
        assert f'data-reservation-row="{free_started.pk}"' not in content
        assert f'data-reservation-row="{unmanaged.pk}"' not in content

    def it_offers_nothing_on_a_free_item_or_a_cancelled_booking_even_to_a_manager(client: Client):
        jig = EquipmentFactory(name="Free Jig")
        _manager_of(client, jig)
        free_started = _started(jig, _member("freetwo"))
        router = _hourly()
        EquipmentStaffMembershipFactory(equipment=router, member=Member.objects.get(user__username="credkeeper"))
        cancelled = _started(router, _member("goneone"), status=EquipmentReservation.Status.CANCELLED)
        content = _tab(client, show="all")
        assert "data-credit-unused-menu-item" not in _row(content, free_started)
        assert "data-credit-unused-menu-item" not in _row(content, cancelled)

    def it_offers_half_hour_steps_up_to_what_is_left(client: Client):
        router = _hourly()
        _manager_of(client, router)
        booking = _started(router, _member("stepsone"), hours=2)
        EquipmentCredit.objects.create(
            equipment=router, member=booking.member, minutes=60, kind=EquipmentCredit.Kind.UNUSED, reservation=booking
        )
        content = _tab(client, show="all")
        form = content.split(f'data-credit-unused-form="{booking.pk}"', 1)[1].split("</form>", 1)[0]
        assert '<option value="30">30 minutes</option>' in form
        assert '<option value="60">1 hour</option>' in form
        assert 'value="90"' not in form
        assert "Credited 1h" in _row(content, booking)

    def it_credits_the_booking_and_shows_the_pill(client: Client):
        router = _hourly()
        _manager_of(client, router)
        booking = _started(router, _member("eighthour"))
        mail.outbox.clear()
        next_url = "/equipment/?view=bookings&show=all"

        response = client.post(
            _credit_url(router),
            {"reservation": str(booking.pk), "minutes": "120", "note": "Done early", "next": next_url},
        )

        assert response.status_code == 302
        assert response.url == next_url
        assert _messages(response) == ["Credited Eighthour 2 hours. They have been emailed."]
        credit = EquipmentCredit.objects.get()
        assert (credit.reservation, credit.member, credit.minutes, credit.kind) == (
            booking,
            booking.member,
            120,
            EquipmentCredit.Kind.UNUSED,
        )
        assert len(mail.outbox) == 1
        assert "data-booking-credited>Credited 2h</span>" in _row(_tab(client, show="all"), booking)

    def it_says_why_when_the_pick_is_too_much_or_the_booking_refuses(client: Client):
        router = _hourly()
        _manager_of(client, router)
        booking = _started(router, _member("shortone"), hours=1)
        response = client.post(_credit_url(router), {"reservation": str(booking.pk), "minutes": "120"})
        assert _messages(response) == ["Pick how many hours to credit, up to what is left on the reservation."]
        upcoming = EquipmentReservationFactory(equipment=router, member=booking.member)
        response = client.post(_credit_url(router), {"reservation": str(upcoming.pk), "minutes": "30"})
        assert _messages(response)[-1] == "This reservation hasn't started yet."
        assert not EquipmentCredit.objects.exists()

    def it_404s_a_booking_on_another_item_or_a_bad_id(client: Client):
        router = _hourly()
        _manager_of(client, router)
        elsewhere = _started(_hourly(name="Elsewhere Lathe"), _member("elsewhere"))
        assert client.post(_credit_url(router), {"reservation": str(elsewhere.pk), "minutes": "30"}).status_code == 404
        assert client.post(_credit_url(router), {"reservation": "x1", "minutes": "30"}).status_code == 404


def describe_book_a_time_with_credit():
    def it_names_the_credit_and_what_is_left_to_pay(client: Client):
        router = _hourly()
        member = _login(client, "twohours")
        _give(router, member, 120)
        content = _schedule(client, router)
        assert "data-credit-line>You have 2 hours of credit here.</p>" in content
        assert "data-reserve-credit" in content
        option = next(o for o in _durations(content)[_at(_day(), 8).isoformat()] if o["v"] == 480)
        assert option == {
            "v": 480,
            "label": "8 hours",
            "span": "8:00 AM to 4:00 PM",
            "total": "$200.00",
            "credit": "2 hours of credit",
            "pay": "$150.00",
            "covered": False,
        }
        assert "goes toward this time." in content
        assert "Continue to Payment" in content

    def it_says_covered_and_offers_the_credit_confirm(client: Client):
        router = _hourly()
        member = _login(client, "fourhours")
        _give(router, member, 240)
        content = _schedule(client, router)
        option = next(o for o in _durations(content)[_at(_day(), 8).isoformat()] if o["v"] == 240)
        assert (option["credit"], option["pay"], option["covered"]) == ("4 hours of credit", "", True)
        assert "equip-reserve-confirm-covered" in content
        assert "Covered by your credit" in content
        assert (
            "Your credit covers this time, so nothing is charged and your reservation confirms right away." in content
        )

    def it_says_nothing_about_credit_without_any(client: Client):
        router = _hourly()
        _login(client, "nocredit")
        content = _schedule(client, router)
        assert "data-credit-line" not in content
        assert "equip-reserve-confirm-covered" not in content
        assert "credit" not in _durations(content)[_at(_day(), 8).isoformat()][0]

    @patch("billing.stripe_utils.create_checkout_session")
    def it_reserves_a_covered_time_with_no_checkout(mock_create, client: Client):
        router = _hourly()
        member = _login(client, "coveredres")
        _give(router, member, 240)
        response = _reserve(client, router, minutes=240)
        assert _toast(response)[1] == "success"
        mock_create.assert_not_called()
        booking = EquipmentReservation.objects.get(member=member)
        assert (booking.status, booking.credit_minutes) == (EquipmentReservation.Status.CONFIRMED, 240)
        assert EquipmentCredit.objects.balance_for(member, router) == 0
        assert "data-credit-used>4h credit used</span>" in _schedule(client, router)

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_charges_the_rest_at_checkout(mock_create, client: Client):
        router = _hourly()
        member = _login(client, "partpayer")
        _give(router, member, 120)
        response = _reserve(client, router)
        assert response["HX-Redirect"] == _SESSION["url"]
        assert mock_create.call_args.kwargs["amount_cents"] == 15000
        assert EquipmentCredit.objects.balance_for(member, router) == 0

    @patch("billing.stripe_utils.create_checkout_session")
    def it_says_the_credit_changed_when_another_tab_spent_it(mock_create, client: Client):
        router = _hourly()
        member = _login(client, "racetab")
        _give(router, member, 240)
        real = EquipmentCredit.objects.balance_for
        reads: list[int] = []

        def stale_then_spent(target: Member, equipment: Equipment) -> int:
            reads.append(1)
            if len(reads) == 1:
                return real(target, equipment)
            # Another tab spends the credit between this read and the lock.
            EquipmentCredit.objects.create(
                equipment=router, member=member, minutes=-240, kind=EquipmentCredit.Kind.SPENT
            )
            return real(target, equipment)

        with patch.object(EquipmentCredit.objects, "balance_for", side_effect=stale_then_spent):
            response = _reserve(client, router, minutes=240)
        assert _toast(response) == ("Your credit here just changed. Please pick your time again.", "error")
        mock_create.assert_not_called()
        assert not EquipmentReservation.objects.filter(member=member).exists()


def describe_member_pills():
    def it_shows_credit_used_beside_paid_on_the_bookings_tab_and_the_schedule(client: Client):
        router = _hourly()
        member = _login(client, "pillowed")
        booking = EquipmentReservationFactory(
            equipment=router,
            member=member,
            amount_paid_cents=15000,
            credit_minutes=120,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=8),
        )
        row = _row(_tab(client), booking)
        assert "data-booking-paid>Paid $150.00</span>" in row
        assert "data-booking-credit-used>2h credit used</span>" in row
        schedule = _schedule(client, router)
        assert "Paid $150.00" in schedule
        assert "data-credit-used>2h credit used</span>" in schedule


def describe_query_counts():
    def it_costs_the_same_for_one_credited_row_as_for_many(client: Client):
        _login(client, "credqueries", fog_role=Member.FogRole.ADMIN)
        router = _hourly()

        def _credited_row() -> None:
            booking = _started(router, MemberFactory(), credit_minutes=60)
            EquipmentCredit.objects.create(
                equipment=router,
                member=booking.member,
                minutes=60,
                kind=EquipmentCredit.Kind.UNUSED,
                reservation=booking,
            )

        _credited_row()
        _tab(client, show="all")
        with CaptureQueriesContext(connection) as one:
            _tab(client, show="all")
        for _ in range(9):
            _credited_row()
        with CaptureQueriesContext(connection) as ten:
            _tab(client, show="all")
        assert len(ten.captured_queries) == len(one.captured_queries)


def describe_an_account_with_no_member():
    def it_is_refused_by_both_credit_endpoints(client: Client):
        router = _hourly()
        user = User.objects.create_user(username="memberless", password="pass")
        Member.objects.filter(user=user).delete()
        client.login(username="memberless", password="pass")
        assert client.post(_credit_url(router), {"member": "x", "hours": "1"}).status_code == 403
        holder = _member("unreached")
        url = reverse("hub_equipment_credit_take_back", args=[router.slug, holder.pk])
        assert client.post(url).status_code == 403


def describe_review_round_one():
    def _credited(router: Equipment, member: Member) -> EquipmentReservation:
        booking = _started(router, member, amount_paid_cents=20000)
        EquipmentCredit.objects.create(
            equipment=router, member=member, minutes=120, kind=EquipmentCredit.Kind.UNUSED, reservation=booking
        )
        return booking

    def it_hides_cancel_on_a_credited_row_in_the_bookings_menu_and_on_the_manage_page(client: Client):
        router = _hourly()
        _manager_of(client, router)
        credited = _credited(router, _member("creditedrow"))
        plain = _started(router, _member("plainrow"))
        tab = _tab(client, show="all")
        assert f"reservation-cancel-{credited.pk}'" not in _row(tab, credited)
        assert f"reservation-cancel-{plain.pk}'" in _row(tab, plain)
        manage = _manage(client, router)
        assert "data-credited-chip>Credited 2h</span>" in manage
        assert f"mgr-cancel-{credited.pk}" not in manage
        assert f"mgr-cancel-{plain.pk}" in manage

    def it_hides_the_members_own_cancel_on_a_credited_row(client: Client):
        router = _hourly()
        member = _login(client, "owncredited")
        credited = _credited(router, member)
        assert f"cancel-my-res-{credited.pk}" not in _schedule(client, router)

    def it_refuses_a_posted_cancel_of_a_credited_row(client: Client):
        router = _hourly()
        _manager_of(client, router)
        credited = _credited(router, _member("postcancel"))
        url = reverse("hub_equipment_reservation_cancel", args=[router.slug, credited.pk])
        client.post(url, {"reason": "Broken", "next": "/equipment/?view=bookings"})
        credited.refresh_from_db()
        assert credited.status == EquipmentReservation.Status.CONFIRMED

    @patch("billing.stripe_utils.create_checkout_session")
    def it_refuses_the_checkout_road_when_another_tab_spent_the_credit(mock_create, client: Client):
        router = _hourly()
        member = _login(client, "checkoutrace")
        _give(router, member, 120)
        real = EquipmentCredit.objects.balance_for
        reads: list[int] = []

        def stale_then_spent(target: Member, equipment: Equipment) -> int:
            reads.append(1)
            if len(reads) == 2:
                EquipmentCredit.objects.create(
                    equipment=router, member=member, minutes=-120, kind=EquipmentCredit.Kind.SPENT
                )
            return real(target, equipment)

        with patch.object(EquipmentCredit.objects, "balance_for", side_effect=stale_then_spent):
            response = _reserve(client, router)
        assert _toast(response) == ("Your credit here just changed. Please pick your time again.", "error")
        mock_create.assert_not_called()
        assert not EquipmentReservation.objects.filter(member=member).exists()

    def it_builds_no_credit_form_on_a_free_item(client: Client):
        saw = EquipmentFactory(name="Plain Bandsaw")
        _manager_of(client, saw)
        response = client.get(f"{reverse('hub_equipment_manage', args=[saw.slug])}?tab=reservations")
        assert response.context["credit_form"] is None

    def it_says_covered_in_the_confirm_summary(client: Client):
        router = _hourly()
        member = _login(client, "coveredsummary")
        _give(router, member, 240)
        assert "option.covered ? ' · Covered by your credit'" in _schedule(client, router)
