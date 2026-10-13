"""BDD specs for End Early (#787, part 2): a member ends their own booking and keeps the unused time as credit.

The half hour rounding; every gate on the model and on crafted POSTs; the interplay with Credit
Unused Time (one cap, either one blocks the other and a later cancel); the freed time; the Pricing
card switch saved only while Hourly; the button and the Bookings tab menu item with their confirms.
Names are factory strings no changelog could contain (STANDARDS.md §8).
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from typing import Any

import pytest
from django.contrib import messages as django_messages
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership import equipment as equipment_service
from membership.models import Equipment, EquipmentCredit, EquipmentError, EquipmentReservation, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _at(hour: int, minute: int = 0, day=TODAY):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _router(**kwargs: Any) -> Equipment:
    kwargs.setdefault("early_end_credit", True)
    kwargs.setdefault("name", "Mossgate Router")
    return EquipmentFactory(
        pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=2500, max_duration_minutes=480, **kwargs
    )


def _member(username: str = "lindenquill") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = f"{username.title()} Harrowell"
    member.save(update_fields=["status", "full_legal_name"])
    return member


def _booking(router: Equipment, member: Member, **kwargs: Any) -> EquipmentReservation:
    """An 8 to 4 booking today, paid in full."""
    kwargs.setdefault("starts_at", _at(8))
    kwargs.setdefault("ends_at", _at(16))
    return EquipmentReservationFactory(equipment=router, member=member, amount_paid_cents=20000, **kwargs)


def _balance(member: Member, router: Equipment) -> int:
    return EquipmentCredit.objects.balance_for(member, router)


def describe_the_rounding():
    def it_ends_at_the_next_half_hour_and_credits_the_rest():
        router = _router()
        member = _member()
        booking = _booking(router, member)
        mail.outbox.clear()

        minutes = booking.end_early(member, now=_at(10, 20))

        assert minutes == 330
        booking.refresh_from_db()
        assert booking.ends_at == _at(10, 30)
        assert (booking.status, booking.amount_paid_cents) == (EquipmentReservation.Status.CONFIRMED, 20000)
        line = EquipmentCredit.objects.get()
        assert (line.kind, line.minutes, line.reservation, line.member) == (
            EquipmentCredit.Kind.ENDED_EARLY,
            330,
            booking,
            member,
        )
        assert _balance(member, router) == 330
        assert booking.refunds.count() == 0
        assert mail.outbox == []

    def it_keeps_an_exact_half_hour_mark():
        router = _router()
        member = _member()
        booking = _booking(router, member)
        assert booking.end_early(member, now=_at(10, 30)) == 330
        booking.refresh_from_db()
        assert booking.ends_at == _at(10, 30)

    def it_rounds_a_second_past_the_mark_up_to_the_next_one():
        assert EquipmentReservation.end_early_mark(_at(10, 30) + timedelta(seconds=1)) == _at(11)

    def it_frees_the_rest_of_the_time_for_someone_else():
        router = _router()
        member = _member()
        booking = _booking(router, member)
        assert EquipmentReservation.objects.overlapping(router, _at(11), _at(12)).exists()
        booking.end_early(member, now=_at(10, 20))
        assert not EquipmentReservation.objects.overlapping(router, _at(11), _at(12)).exists()
        assert EquipmentReservation.objects.overlapping(router, _at(10), _at(10, 30)).exists()


def describe_the_gates():
    @pytest.mark.parametrize(
        ("setup", "message"),
        [
            ("other_member", "Only the member who booked this can end it early."),
            ("switch_off", "Ending early isn't turned on for this equipment."),
            ("free_item", "Ending early isn't turned on for this equipment."),
            ("not_started", "This reservation hasn't started yet."),
            ("cancelled", "Only a confirmed reservation can be ended early."),
            ("under_half_hour", "Less than half an hour is left, so there is nothing to credit."),
            ("block", "Only the member who booked this can end it early."),
        ],
    )
    def it_refuses_and_changes_nothing(setup: str, message: str):
        router = _router()
        member = _member()
        actor = member
        now = _at(10, 20)
        booking = _booking(router, member)
        if setup == "other_member":
            actor = _member("crowbarrow")
        elif setup == "switch_off":
            Equipment.objects.filter(pk=router.pk).update(early_end_credit=False)
        elif setup == "free_item":
            Equipment.objects.filter(pk=router.pk).update(pricing=Equipment.Pricing.FREE)
        elif setup == "not_started":
            now = _at(7)
        elif setup == "cancelled":
            EquipmentReservation.objects.filter(pk=booking.pk).update(status=EquipmentReservation.Status.CANCELLED)
        elif setup == "under_half_hour":
            now = _at(15, 40)
        elif setup == "block":
            EquipmentReservation.objects.filter(pk=booking.pk).update(kind=EquipmentReservation.Kind.BLOCK)
        with pytest.raises(EquipmentError, match=f"^{message}$".replace(".", r"\.")):
            booking.end_early(actor, now=now)
        booking.refresh_from_db()
        assert booking.ends_at == _at(16)
        assert not EquipmentCredit.objects.exists()

    def it_offers_end_early_only_on_a_row_that_would_take_it():
        router = _router()
        member = _member()
        booking = _booking(router, member)
        now = _at(10, 20)
        assert booking.can_end_early(member, now=now, credited_minutes=0)
        assert not booking.can_end_early(None, now=now, credited_minutes=0)
        assert not booking.can_end_early(_member("bystander"), now=now, credited_minutes=0)
        assert not booking.can_end_early(member, now=now, credited_minutes=30)
        assert not booking.can_end_early(member, now=_at(15, 40), credited_minutes=0)
        assert not booking.can_end_early(member, now=_at(7), credited_minutes=0)
        assert not booking.can_end_early(member, now=_at(16), credited_minutes=0)
        router.early_end_credit = False
        assert not booking.can_end_early(member, now=now, credited_minutes=0)


def describe_with_credit_unused_time():
    def _manager(router: Equipment) -> Member:
        manager = _member("sorrelgate")
        EquipmentStaffMembershipFactory(equipment=router, member=manager)
        return manager

    def it_refuses_end_early_after_a_manager_credited_unused_time():
        router = _router()
        member = _member()
        starts = timezone.now() - timedelta(hours=1)
        booking = _booking(router, member, starts_at=starts, ends_at=starts + timedelta(hours=8))
        equipment_service.grant_credit(router, member, _manager(router), 120, reservation=booking)
        with pytest.raises(EquipmentError, match=r"^Unused time on this reservation was already credited\.$"):
            booking.end_early(member)
        assert _balance(member, router) == 120

    def it_counts_toward_the_cap_and_blocks_a_later_credit_or_cancel():
        router = _router()
        member = _member()
        manager = _manager(router)
        starts = timezone.now() - timedelta(hours=1)
        booking = _booking(router, member, starts_at=starts, ends_at=starts + timedelta(hours=8))
        minutes = booking.end_early(member)
        assert minutes >= 360
        booking.refresh_from_db()
        assert EquipmentCredit.objects.credited_on(booking) == minutes
        with pytest.raises(EquipmentError, match="already credited"):
            equipment_service.grant_credit(router, member, manager, 30, reservation=booking)
        with pytest.raises(EquipmentError, match="already credited, so it can't be cancelled"):
            booking.cancel(manager, reason="Late", as_manager=True)
        assert _balance(member, router) == minutes


# --- The screens --------------------------------------------------------------------------------


def _login(client: Client, username: str) -> Member:
    member = _member(username)
    client.login(username=username, password="pass")
    return member


def _messages(response: Any) -> list[str]:
    return [str(m) for m in django_messages.get_messages(response.wsgi_request)]


def _in_progress(router: Equipment, member: Member, hours: int = 8) -> EquipmentReservation:
    starts = timezone.now() - timedelta(minutes=50)
    return _booking(router, member, starts_at=starts, ends_at=starts + timedelta(hours=hours))


def _end_url(booking: EquipmentReservation) -> str:
    return reverse("hub_equipment_reservation_end_early", args=[booking.equipment.slug, booking.pk])


def describe_the_pricing_card_switch():
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
            "max_duration_minutes": "480",
            "max_advance_days": "30",
            "max_active_reservations_per_member": "2",
            **overrides,
        }

    def _manage_login(client: Client, router: Equipment) -> None:
        manager = _login(client, "switchkeeper")
        EquipmentStaffMembershipFactory(equipment=router, member=manager)

    def it_sits_under_the_hourly_fields(client: Client):
        router = _router(early_end_credit=False)
        _manage_login(client, router)
        content = client.get(f"{reverse('hub_equipment_manage', args=[router.slug])}?tab=hours").content.decode()
        hourly = content.split("data-pricing-hourly", 1)[1].split("data-pricing-donation", 1)[0]
        assert "data-early-end-switch" in hourly
        assert "Members can end a reservation early and keep the unused time as credit" in hourly

    def it_saves_while_hourly_and_keeps_its_value_otherwise(client: Client):
        router = _router(early_end_credit=False)
        _manage_login(client, router)
        url = reverse("hub_equipment_hours_save", args=[router.slug])
        client.post(url, _settings(pricing="hourly", hourly_rate="25", early_end_credit="on"))
        router.refresh_from_db()
        assert router.early_end_credit is True
        client.post(url, _settings(pricing="donation", donation_minimum="", donation_suggested=""))
        router.refresh_from_db()
        assert (router.pricing, router.early_end_credit) == (Equipment.Pricing.DONATION, True)
        client.post(url, _settings())
        router.refresh_from_db()
        assert router.early_end_credit is True
        Equipment.objects.filter(pk=router.pk).update(pricing=Equipment.Pricing.HOURLY)
        client.post(url, _settings(pricing="hourly", hourly_rate="25"))
        router.refresh_from_db()
        assert router.early_end_credit is False


def describe_the_schedule_button():
    def _schedule(client: Client, router: Equipment) -> str:
        return client.get(reverse("hub_equipment_schedule", args=[router.slug])).content.decode()

    def it_shows_end_early_with_its_confirm_on_the_members_in_progress_row(client: Client):
        router = _router()
        member = _login(client, "endbutton")
        booking = _in_progress(router, member)
        content = _schedule(client, router)
        assert "data-end-early" in content
        assert f"end-early-{booking.pk}" in content
        assert "End your reservation now?" in content
        assert "you have not used become credit here." in content

    def it_hides_end_early_with_the_switch_off_or_under_half_an_hour_left(client: Client):
        router = _router(early_end_credit=False)
        member = _login(client, "nobutton")
        _in_progress(router, member)
        assert "data-end-early" not in _schedule(client, router)
        other = _router(name="Mossgate Lathe")
        now = timezone.now()
        # Ends 20 minutes after the next half hour mark, so no whole half hour is left to credit.
        _booking(
            other,
            member,
            starts_at=now - timedelta(hours=1),
            ends_at=EquipmentReservation.end_early_mark(now) + timedelta(minutes=20),
        )
        assert "data-end-early" not in _schedule(client, other)

    def it_ends_the_booking_and_answers_the_schedule_with_a_toast(client: Client):
        router = _router()
        member = _login(client, "enderone")
        booking = _in_progress(router, member)
        expected = booking.end_early_minutes(timezone.now())
        response = client.post(_end_url(booking), {"week": "0", "day": ""}, HTTP_HX_REQUEST="true")
        toast = json.loads(response["HX-Trigger"])["showToast"]
        assert toast["type"] == "success"
        assert toast["message"].startswith("Ended. ") and toast["message"].endswith(" of credit added.")
        assert 'id="equipment-schedule"' in response.content.decode()
        assert _balance(member, router) == expected

    @pytest.mark.parametrize("case", ["not_owner", "switch_off", "not_started"])
    def it_refuses_a_crafted_post(client: Client, case: str):
        router = _router(early_end_credit=case != "switch_off")
        owner = _member("realowner")
        if case == "not_owner":
            _login(client, "intruder")
            booking = _in_progress(router, owner)
        else:
            owner = _login(client, "crafter")
            booking = (
                _booking(
                    router,
                    owner,
                    starts_at=timezone.now() + timedelta(days=1),
                    ends_at=timezone.now() + timedelta(days=1, hours=4),
                )
                if case == "not_started"
                else _in_progress(router, owner)
            )
        response = client.post(_end_url(booking), {}, HTTP_HX_REQUEST="true")
        assert json.loads(response["HX-Trigger"])["showToast"]["type"] == "error"
        assert not EquipmentCredit.objects.exists()

    def it_refuses_a_memberless_account(client: Client):
        router = _router()
        booking = _in_progress(router, _member("someowner"))
        user = User.objects.create_user(username="nomember", password="pass")
        Member.objects.filter(user=user).delete()
        client.login(username="nomember", password="pass")
        assert client.post(_end_url(booking)).status_code == 403


def describe_the_bookings_tab_menu():
    def it_offers_end_early_in_the_members_menu_and_lands_back_with_a_message(client: Client):
        router = _router()
        member = _login(client, "tabender")
        booking = _in_progress(router, member)
        content = client.get("/equipment/", {"view": "bookings"}).content.decode()
        assert "data-end-early-menu-item" in content
        assert f"reservation-end-early-{booking.pk}" in content
        next_url = "/equipment/?view=bookings"
        response = client.post(_end_url(booking), {"next": next_url})
        assert response.status_code == 302
        assert response.url == next_url
        [message] = _messages(response)
        assert message.startswith("Ended. ")
        assert _balance(member, router) > 0

    def it_answers_a_refusal_with_a_message_too(client: Client):
        router = _router(early_end_credit=False)
        member = _login(client, "tabrefused")
        booking = _in_progress(router, member)
        response = client.post(_end_url(booking), {"next": "/equipment/?view=bookings"})
        assert _messages(response) == ["Ending early isn't turned on for this equipment."]
