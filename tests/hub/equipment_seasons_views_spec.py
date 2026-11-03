"""BDD specs for the screens of equipment reserved by season (#1095).

The Hours & Limits tab's Season card and its rules (both dates or neither, the end after the start,
never hourly, nothing booked inside new dates); the item page's season card in place of the week
strip and Book a Time; Reserve This Season through the reserve endpoint; the second member's view
and crafted POST; approval; the blocked member; the directory card line; cancelling; clearing the
dates; and the season rows on Your Reservations Here, the Bookings tab and the decline confirm.
Names are factory strings no changelog could contain (STANDARDS.md section 8).
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from membership.models import Equipment, EquipmentReservation, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    MembershipPlanFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

WAITING = EquipmentReservation.Status.PENDING_APPROVAL


def _login(client: Client, username: str) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = f"{username.title()} Loamhart"
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return member


def _bed(start_offset: int = 5, end_offset: int = 200, **kwargs) -> Equipment:
    today = timezone.localdate()
    return EquipmentFactory(
        name=kwargs.pop("name", "Brambledown Bed 4Q"),
        kind=Equipment.Kind.SPACE,
        season_starts_on=today + timedelta(days=start_offset),
        season_ends_on=today + timedelta(days=end_offset),
        **kwargs,
    )


def _detail(client: Client, equipment: Equipment) -> str:
    response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
    assert response.status_code == 200
    return response.content.decode()


def _manage(client: Client, equipment: Equipment) -> str:
    response = client.get(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=hours")
    assert response.status_code == 200
    return response.content.decode()


def _press(client: Client, equipment: Equipment, **extra: str):
    """What Reserve This Season posts: the season's exact start and length."""
    starts_at, _ = equipment.season_span()
    data = {"starts_at": starts_at.isoformat(), "duration_minutes": str(equipment.season_minutes), **extra}
    return client.post(reverse("hub_equipment_reserve", args=[equipment.slug]), data, HTTP_HX_REQUEST="true")


def _toast(response) -> tuple[str, str]:
    toast = json.loads(response["HX-Trigger"])["showToast"]
    return toast["message"], toast["type"]


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
        "season_shown": "1",
        **overrides,
    }


def _save(client: Client, equipment: Equipment, **overrides: str):
    return client.post(reverse("hub_equipment_hours_save", args=[equipment.slug]), _settings(**overrides))


def _field_errors(content: str, marker: str) -> str:
    """The rendered field wrapper that carries ``marker``, up to the next field wrapper."""
    start = content.index(marker)
    return content[start : start + 2500]


def describe_the_season_card_on_hours_and_limits():
    def it_sits_before_limits_and_saves_both_dates(client: Client):
        bed = EquipmentFactory(name="Thistlewick Bed 2A")
        manager = _login(client, "seasonkeeper")
        EquipmentStaffMembershipFactory(equipment=bed, member=manager)
        content = _manage(client, bed)
        assert content.index("data-season-card") < content.index(">Limits<")
        assert 'name="season_starts_on"' in content and 'name="season_ends_on"' in content
        assert "members reserve the Thistlewick Bed 2A for the whole season with one press" in content
        response = _save(client, bed, season_starts_on="2027-03-01", season_ends_on="2027-12-15")
        assert response.status_code == 302
        bed.refresh_from_db()
        assert (bed.season_starts_on.isoformat(), bed.season_ends_on.isoformat()) == ("2027-03-01", "2027-12-15")

    @pytest.mark.parametrize(
        ("start", "end", "marker", "message"),
        [
            ("2027-03-01", "", "data-season-end", "Set the season end too, or clear the start."),
            ("", "2027-12-15", "data-season-start", "Set the season start too, or clear the end."),
            ("2027-12-15", "2027-03-01", "data-season-end", "The season must end after it starts."),
            ("2027-03-01", "2027-03-01", "data-season-end", "The season must end after it starts."),
        ],
    )
    def it_refuses_half_a_season_or_a_backwards_one_beside_the_field(
        client: Client, start: str, end: str, marker: str, message: str
    ):
        bed = EquipmentFactory(name="Gorsemoor Bed 7C")
        EquipmentStaffMembershipFactory(equipment=bed, member=_login(client, "halfseason"))
        response = _save(client, bed, season_starts_on=start, season_ends_on=end)
        assert response.status_code == 200
        assert message in _field_errors(response.content.decode(), marker)
        bed.refresh_from_db()
        assert not bed.has_season

    def it_shows_only_the_date_error_for_a_date_that_does_not_parse(client: Client):
        bed = EquipmentFactory(name="Mossbury Bed 9P")
        EquipmentStaffMembershipFactory(equipment=bed, member=_login(client, "baddate"))
        response = _save(client, bed, season_starts_on="the first thaw", season_ends_on="")
        assert response.status_code == 200
        errors = _field_errors(response.content.decode(), "data-season-start")
        assert "Enter a valid date." in errors
        assert "Set the season start too" not in response.content.decode()
        assert "Set the season end too" not in response.content.decode()

    def it_refuses_season_dates_with_hourly_pricing_beside_pricing(client: Client):
        bed = EquipmentFactory(name="Pipwort Bed 1D")
        EquipmentStaffMembershipFactory(equipment=bed, member=_login(client, "hourlyseason"))
        response = _save(
            client, bed, season_starts_on="2027-03-01", season_ends_on="2027-12-15", pricing="hourly", hourly_rate="20"
        )
        assert response.status_code == 200
        card = _field_errors(response.content.decode(), "data-pricing-card")
        assert "A season can be free or donation based, not hourly." in card
        bed.refresh_from_db()
        assert not bed.has_season

    def it_refuses_season_dates_while_stored_pricing_is_hourly(client: Client):
        bed = EquipmentFactory(name="Sorrelgate Bed 6F", pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=1500)
        EquipmentStaffMembershipFactory(equipment=bed, member=_login(client, "storedhourly"))
        # A page drawn before the Pricing card posts no pricing; the stored Hourly stands and refuses.
        response = _save(client, bed, season_starts_on="2027-03-01", season_ends_on="2027-12-15")
        assert response.status_code == 200
        assert "A season can be free or donation based, not hourly." in response.content.decode()

    def it_refuses_hourly_from_a_page_without_the_card_while_season_dates_are_stored(client: Client):
        bed = _bed(name="Oldpage Bed 0H")
        EquipmentStaffMembershipFactory(equipment=bed, member=_login(client, "oldhourly"))
        data = _settings(pricing="hourly", hourly_rate="20")
        data.pop("season_shown")
        response = client.post(reverse("hub_equipment_hours_save", args=[bed.slug]), data)
        assert response.status_code == 200
        card = _field_errors(response.content.decode(), "data-pricing-card")
        assert "A season can be free or donation based, not hourly." in card
        bed.refresh_from_db()
        assert bed.pricing == Equipment.Pricing.FREE

    def it_saves_a_donation_season(client: Client):
        bed = EquipmentFactory(name="Wrenfield Bed 3B")
        EquipmentStaffMembershipFactory(equipment=bed, member=_login(client, "donationseason"))
        response = _save(
            client,
            bed,
            season_starts_on="2027-03-01",
            season_ends_on="2027-12-15",
            pricing="donation",
            donation_minimum="0",
        )
        assert response.status_code == 302
        bed.refresh_from_db()
        assert (bed.pricing, bed.has_season) == (Equipment.Pricing.DONATION, True)

    def it_refuses_new_dates_over_upcoming_bookings_and_says_how_many(client: Client):
        bed = EquipmentFactory(name="Hollyreach Bed 5E")
        EquipmentStaffMembershipFactory(equipment=bed, member=_login(client, "busyseason"))
        today = timezone.localdate()
        for days in (3, 4):
            start = timezone.make_aware(datetime.combine(today + timedelta(days=days), time(10, 0)))
            EquipmentReservationFactory(equipment=bed, starts_at=start, ends_at=start + timedelta(hours=1))
        response = _save(
            client,
            bed,
            season_starts_on=(today + timedelta(days=1)).isoformat(),
            season_ends_on=(today + timedelta(days=60)).isoformat(),
        )
        assert response.status_code == 200
        errors = _field_errors(response.content.decode(), "data-season-start")
        assert "2 upcoming reservations sit inside these dates. Cancel them first, or pick other dates." in errors
        bed.refresh_from_db()
        assert not bed.has_season

    def it_names_one_booking_in_the_singular(client: Client):
        bed = EquipmentFactory(name="Hollyreach Bed 5F")
        EquipmentStaffMembershipFactory(equipment=bed, member=_login(client, "onebooking"))
        today = timezone.localdate()
        start = timezone.make_aware(datetime.combine(today + timedelta(days=3), time(10, 0)))
        EquipmentReservationFactory(equipment=bed, starts_at=start, ends_at=start + timedelta(hours=1))
        response = _save(
            client,
            bed,
            season_starts_on=(today + timedelta(days=1)).isoformat(),
            season_ends_on=(today + timedelta(days=60)).isoformat(),
        )
        assert "1 upcoming reservation sits inside these dates. Cancel it first, or pick other dates." in (
            response.content.decode()
        )

    def it_saves_unchanged_dates_again_whatever_is_held(client: Client):
        bed = _bed(name="Ferncastle Bed 8G")
        EquipmentStaffMembershipFactory(equipment=bed, member=_login(client, "resaver"))
        start = bed.season_span()[0] + timedelta(days=2, hours=10)
        EquipmentReservationFactory(
            equipment=bed, kind=EquipmentReservation.Kind.BLOCK, starts_at=start, ends_at=start + timedelta(hours=1)
        )
        response = _save(
            client,
            bed,
            season_starts_on=bed.season_starts_on.isoformat(),
            season_ends_on=bed.season_ends_on.isoformat(),
            closed_message="Resting soil.",
        )
        assert response.status_code == 302

    def it_keeps_the_dates_when_a_page_without_the_card_saves(client: Client):
        bed = _bed(name="Oldpage Bed 0K")
        EquipmentStaffMembershipFactory(equipment=bed, member=_login(client, "oldpager"))
        data = _settings()
        data.pop("season_shown")
        response = client.post(reverse("hub_equipment_hours_save", args=[bed.slug]), data)
        assert response.status_code == 302
        bed.refresh_from_db()
        assert bed.has_season

    def it_goes_back_to_book_a_time_when_both_dates_are_cleared(client: Client):
        bed = _bed(name="Clearwell Bed 2R")
        EquipmentStaffMembershipFactory(equipment=bed, member=_login(client, "clearer"))
        assert _save(client, bed, season_starts_on="", season_ends_on="").status_code == 302
        bed.refresh_from_db()
        assert not bed.has_season
        # The save posted no hours rows, so give the item its hours back before looking.
        EquipmentHoursFactory(equipment=bed, weekday=(timezone.localdate() + timedelta(days=2)).weekday())
        content = _detail(client, bed)
        assert "data-reserve-season" not in content
        assert ">Book a Time<" in content


def describe_the_item_page():
    def it_offers_reserve_this_season_with_the_dates_and_no_pickers(client: Client):
        bed = _bed()
        _login(client, "seedling")
        content = _detail(client, bed)
        assert f"Reserve This Season, {bed.season_label}" in content
        assert f"Open for the season, {bed.season_label}" in content
        assert 'name="duration_minutes" value="' in content
        assert 'select name="starts_at"' not in content
        assert "pl-equip-strip" not in content
        assert "Reserve this season?" in content
        assert "You're all set." in content
        assert "Pick a time below." not in content

    def it_reserves_the_season_and_lists_it_under_your_reservations(client: Client):
        bed = _bed()
        member = _login(client, "planter")
        response = _press(client, bed)
        assert _toast(response) == (f"Reserved for the season, {bed.season_label}.", "success")
        reservation = EquipmentReservation.objects.get(equipment=bed)
        assert (reservation.member, reservation.is_season) == (member, True)
        assert (reservation.starts_at, reservation.ends_at) == bed.season_span()
        content = response.content.decode()
        assert f"Season, {bed.season_label}" in content
        assert "data-season-yours" in content
        assert "data-reserve-season" not in content
        bookings = client.get(reverse("hub_equipment_bookings")).content.decode()
        assert f"Season, {bed.season_label}" in bookings

    def it_shows_a_second_member_no_button_and_refuses_their_crafted_post(client: Client):
        bed = _bed()
        EquipmentReservationFactory(
            equipment=bed, is_season=True, starts_at=bed.season_span()[0], ends_at=bed.season_span()[1]
        )
        _login(client, "runnerup")
        content = _detail(client, bed)
        assert "data-reserve-season" not in content
        assert f"Reserved for the season, {bed.season_label}" in content
        assert "Someone already reserved this season." in content
        response = _press(client, bed)
        assert _toast(response) == (Equipment.SEASON_TAKEN_MESSAGE, "error")
        assert EquipmentReservation.objects.filter(equipment=bed).count() == 1

    def it_sends_a_request_when_approval_is_on_and_a_decline_frees_the_season(client: Client):
        bed = _bed(requires_approval=True)
        _login(client, "requester")
        content = _detail(client, bed)
        assert "Request this season?" in content
        assert "A manager approves each reservation. We hold the season for you" in content
        response = _press(client, bed)
        assert _toast(response) == ("Request sent. We'll email you when a manager decides.", "success")
        request = EquipmentReservation.objects.get(equipment=bed)
        assert request.status == WAITING

        manager_client = Client()
        manager = _login(manager_client, "bedsteward")
        EquipmentStaffMembershipFactory(equipment=bed, member=manager)
        manage = _manage(manager_client, bed)
        assert f"asked for the season, {bed.season_label}." in manage
        manager_client.post(
            reverse("hub_equipment_reservation_decline", args=[bed.slug, request.pk]), {"reason": "Bed 4Q rests."}
        )
        request.refresh_from_db()
        assert request.status == EquipmentReservation.Status.DECLINED

        other = Client()
        _login(other, "afterdecline")
        assert "data-reserve-season" in _detail(other, bed)

    def it_hides_the_button_from_a_member_without_the_orientation(client: Client):
        bed = _bed(unlocking_orientations=[OrientationTypeFactory(name="Compostry Intro")])
        _login(client, "notyet")
        content = _detail(client, bed)
        assert "data-reserve-season" not in content
        assert f"Open for the season, {bed.season_label}" in content
        response = _press(client, bed)
        assert "Compostry Intro" in _toast(response)[0]

    def it_names_the_agreement_card_this_season(client: Client):
        bed = _bed(usage_agreement_text="Brambledown beds are watered on Sundays.")
        _login(client, "agreefirst")
        content = _detail(client, bed)
        assert "data-equip-agree-prompt" in content
        prompt = content[content.index("data-equip-agree-prompt") :]
        assert prompt.index(">This Season<") < prompt.index("Agree to the usage agreement")
        assert "data-reserve-season" not in content

    def it_offers_a_donation_season_with_the_amount_box(client: Client):
        bed = _bed(pricing=Equipment.Pricing.DONATION)
        _login(client, "giver")
        content = _detail(client, bed)
        assert "data-reserve-amount" in content
        assert "Your amount ($)" in content
        assert "You choose what you pay." in content

    def it_promises_a_refund_only_before_the_season_starts(client: Client):
        bed = _bed(start_offset=10, pricing=Equipment.Pricing.DONATION)
        _login(client, "refundwords")
        content = _detail(client, bed).replace("&#x27;", "'")
        promise = "If you cancel before the season starts, you get an automatic full refund. Once it has started, a cancel isn't refunded."
        assert content.count(promise) == 2  # the terms line and the confirm
        assert "You pay when you reserve. If you cancel before the season starts" in content

    def it_promises_no_cancel_refund_once_the_season_has_started(client: Client):
        bed = _bed(start_offset=-10, pricing=Equipment.Pricing.DONATION, requires_approval=True)
        _login(client, "startedwords")
        content = _detail(client, bed).replace("&#x27;", "'")
        assert content.count("If a manager declines, you get an automatic full refund.") == 2
        assert content.count("The season has started, so if you cancel, your payment isn't refunded.") == 2

    def it_offers_a_donation_request_when_approval_is_on(client: Client):
        bed = _bed(pricing=Equipment.Pricing.DONATION, requires_approval=True)
        _login(client, "giverasks")
        content = _detail(client, bed)
        assert "Request this season?" in content
        assert "until they do, the season is held for you" in content

    def it_tells_everyone_an_ended_season_is_over_and_points_a_manager_to_new_dates(client: Client):
        bed = _bed(start_offset=-100, end_offset=-1)
        manager = _login(client, "lateseason")
        content = _detail(client, bed)
        assert f"Season ended, {bed.season_label}" in content
        assert "This season is over. A manager sets the next season's dates." in content
        assert "Set the next season" not in content
        assert "data-reserve-season" not in content
        EquipmentStaffMembershipFactory(equipment=bed, member=manager)
        assert "Set the next season" in _detail(client, bed)

    def it_offers_the_new_season_to_last_seasons_holder_once_new_dates_are_set(client: Client):
        bed = _bed(start_offset=-200, end_offset=10)
        member = _login(client, "lastholder")
        starts_at, ends_at = bed.season_span()
        EquipmentReservationFactory(equipment=bed, member=member, is_season=True, starts_at=starts_at, ends_at=ends_at)
        assert "data-season-yours" in _detail(client, bed)
        bed.season_starts_on = timezone.localdate() + timedelta(days=120)
        bed.season_ends_on = timezone.localdate() + timedelta(days=400)
        bed.save(update_fields=["season_starts_on", "season_ends_on"])
        content = _detail(client, bed)
        assert "data-season-yours" not in content
        assert f"Reserve This Season, {bed.season_label}" in content

    def it_tells_the_holder_their_own_row_holds_the_bed_after_the_dates_move(client: Client):
        bed = _bed(start_offset=-10, end_offset=100)
        member = _login(client, "movedholder")
        starts_at, ends_at = bed.season_span()
        reservation = EquipmentReservationFactory(
            equipment=bed, member=member, is_season=True, starts_at=starts_at, ends_at=ends_at
        )
        bed.season_ends_on = bed.season_ends_on + timedelta(days=30)
        bed.save(update_fields=["season_ends_on"])
        content = _detail(client, bed).replace("&#x27;", "'")
        assert f"Your reservation holds this bed for {reservation.season_label}." in content
        assert "Someone already reserved this season." not in content
        other = Client()
        _login(other, "lookingon")
        assert "Someone already reserved this season." in _detail(other, bed)

    def it_shows_the_button_to_others_again_after_the_holder_cancels(client: Client):
        bed = _bed(start_offset=-10, end_offset=150)
        holder = _login(client, "giveitback")
        _press(client, bed)
        reservation = EquipmentReservation.objects.get(equipment=bed, member=holder)
        response = client.post(reverse("hub_equipment_reservation_cancel", args=[bed.slug, reservation.pk]))
        assert response.status_code == 200
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CANCELLED
        other = Client()
        _login(other, "nextup")
        assert "data-reserve-season" in _detail(other, bed)


def describe_cancelling_a_paid_season():
    def _paid(member: Member, start_offset: int) -> EquipmentReservation:
        bed = _bed(start_offset=start_offset, pricing=Equipment.Pricing.DONATION)
        starts_at, ends_at = bed.season_span()
        return EquipmentReservationFactory(
            equipment=bed,
            member=member,
            is_season=True,
            starts_at=starts_at,
            ends_at=ends_at,
            amount_paid_cents=2500,
            stripe_payment_id="pi_view_season",
        )

    def it_tells_the_member_before_and_after_cancelling_that_a_started_season_is_not_refunded(client: Client):
        reservation = _paid(_login(client, "paidmid"), start_offset=-10)
        content = _detail(client, reservation.equipment)
        assert (
            "The season opens for the next person. Your $25.00 isn&#x27;t refunded once the season has started."
            in content
        )
        response = client.post(
            reverse("hub_equipment_reservation_cancel", args=[reservation.equipment.slug, reservation.pk]),
            HTTP_HX_REQUEST="true",
        )
        assert _toast(response) == (
            "Reservation cancelled. Your $25.00 isn't refunded once the season has started.",
            "success",
        )

    def it_promises_the_full_refund_before_the_season_starts(client: Client):
        reservation = _paid(_login(client, "paidearly"), start_offset=10)
        content = _detail(client, reservation.equipment)
        assert "You&#x27;ll get an automatic full refund." in content

    def it_tells_a_manager_the_payment_is_kept(client: Client):
        reservation = _paid(Member.objects.get(pk=_login(Client(), "paidholder").pk), start_offset=-10)
        manager = _login(client, "paidwarden")
        EquipmentStaffMembershipFactory(equipment=reservation.equipment, member=manager)
        content = _manage(client, reservation.equipment).replace("&#x27;", "'")
        assert (
            "The season has started, so their $25.00 isn't refunded. An admin can refund it from Payments." in content
        )
        response = client.post(
            reverse("hub_equipment_reservation_cancel", args=[reservation.equipment.slug, reservation.pk]),
            {"reason": "Bed closed for the winter."},
        )
        assert response.status_code == 302
        messages = [str(m) for m in response.wsgi_request._messages]
        assert any("The season had started, so the payment isn't refunded." in m for m in messages)


def describe_the_directory_card():
    def it_reads_open_or_reserved_for_the_season_with_the_dates(client: Client):
        open_bed = _bed(name="Larkspur Bed 1Y")
        held_bed = _bed(name="Larkspur Bed 2Y")
        EquipmentReservationFactory(
            equipment=held_bed, is_season=True, starts_at=held_bed.season_span()[0], ends_at=held_bed.season_span()[1]
        )
        _login(client, "browser")
        content = client.get(reverse("hub_equipment_index")).content.decode()
        target = ' data-guidance-target="reservations.season-line"'
        assert f'pl-equip-avail--free"{target}>Open for the season, {open_bed.season_label}<' in content
        assert f'pl-equip-avail--busy"{target}>Reserved for the season, {held_bed.season_label}<' in content

    def it_costs_one_query_for_any_number_of_season_cards(client: Client):
        _login(client, "counter")
        _bed(name="Countwell Bed 1")
        client.get(reverse("hub_equipment_index"))  # The first request warms the session and site caches.
        with CaptureQueriesContext(connection) as one:
            client.get(reverse("hub_equipment_index"))
        for number in (2, 3, 4):
            bed = _bed(name=f"Countwell Bed {number}")
            EquipmentReservationFactory(
                equipment=bed, is_season=True, starts_at=bed.season_span()[0], ends_at=bed.season_span()[1]
            )
        with CaptureQueriesContext(connection) as four:
            client.get(reverse("hub_equipment_index"))
        assert len(four.captured_queries) == len(one.captured_queries)


def describe_the_guidance_target():
    def it_marks_a_season_items_line_on_the_guild_reservations_tab_the_feature_opens(client: Client):
        guild = GuildFactory(name="Loamhart Gardeners", show_reservations_tab=True)
        bed = _bed(name="Loamhart Bed 1", guild=guild)
        EquipmentFactory(name="Loamhart Trowel Rack", guild=guild)
        _login(client, "targetlooker")
        content = client.get(f"{reverse('hub_guild_detail', args=[guild.slug])}?tab=reservations").content.decode()
        marker = 'data-guidance-target="reservations.season-line"'
        assert content.count(marker) == 1
        assert f"{marker}>Open for the season, {bed.season_label}<" in content


def describe_the_bookings_tab():
    def it_never_offers_credit_unused_time_on_a_season_row(client: Client):
        bed = _bed(start_offset=-30, end_offset=260)
        manager = _login(client, "creditlooker")
        EquipmentStaffMembershipFactory(equipment=bed, member=manager)
        starts_at, ends_at = bed.season_span()
        EquipmentReservationFactory(equipment=bed, is_season=True, starts_at=starts_at, ends_at=ends_at)
        Equipment.objects.filter(pk=bed.pk).update(
            season_starts_on=None, season_ends_on=None, pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=1000
        )
        content = client.get(f"{reverse('hub_equipment_bookings')}?show=all").content.decode()
        assert "data-credit-unused-menu-item" not in content

    def it_offers_the_member_cancel_on_a_season_that_already_started(client: Client):
        bed = _bed(start_offset=-10, end_offset=150)
        member = _login(client, "midseason")
        starts_at, ends_at = bed.season_span()
        reservation = EquipmentReservationFactory(
            equipment=bed, member=member, is_season=True, starts_at=starts_at, ends_at=ends_at
        )
        content = client.get(f"{reverse('hub_equipment_bookings')}?show=all").content.decode()
        assert f"reservation-cancel-mine-{reservation.pk}" in content
        assert f"Season, {bed.season_label}" in content
