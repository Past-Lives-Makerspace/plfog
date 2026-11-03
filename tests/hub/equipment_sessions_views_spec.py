"""BDD specs for Sessions (#1096) on the pages: the switch, the buttons, the times, the session page and its QR.

The Sessions switch on Hours & Limits (hourly only, saved only while Hourly); Start Session and Stop
Session on the item page and on the Bookings tab row, only on the member's own booking in
progress; the session times for the member and for managers (the manage page's Reservations tab
and the Bookings tab); the session page the QR opens (sign in first, Start, Stop, stopped, nothing
current, waiting, switched off); the QR on the manage page and the QR sheet with its downloads; and
crafted POSTs (someone else's booking, an item without Sessions, a season row, after the end, a
second Stop). With the switch off nothing about sessions shows and End Early is as before. Names
are factory strings no changelog could contain (STANDARDS.md §8).
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

import pytest
from allauth.account.models import EmailAddress
from django.contrib import messages as django_messages
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Equipment, EquipmentCredit, EquipmentReservation, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def _laser(**kwargs: Any) -> Equipment:
    kwargs.setdefault("sessions_enabled", True)
    kwargs.setdefault("name", "Thornwick Laser")
    kwargs.setdefault("slug", "thornwick-laser")
    return EquipmentFactory(
        pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=2500, max_duration_minutes=480, **kwargs
    )


def _member(username: str) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = f"{username.title()} Ashcombe"
    member.save(update_fields=["status", "full_legal_name"])
    return member


def _login(client: Client, username: str) -> Member:
    member = _member(username)
    client.login(username=username, password="pass")
    return member


def _manager(client: Client, item: Equipment, username: str = "ashkeeper") -> Member:
    manager = _login(client, username)
    EquipmentStaffMembershipFactory(equipment=item, member=manager)
    return manager


def _in_progress(item: Equipment, member: Member, **kwargs: Any) -> EquipmentReservation:
    """A seven hour booking that started 50 minutes ago, paid in full."""
    starts = timezone.now() - timedelta(minutes=50)
    kwargs.setdefault("starts_at", starts)
    kwargs.setdefault("ends_at", starts + timedelta(hours=7))
    return EquipmentReservationFactory(equipment=item, member=member, amount_paid_cents=17500, **kwargs)


def _later(item: Equipment, member: Member, **kwargs: Any) -> EquipmentReservation:
    starts = timezone.now() + timedelta(days=1)
    return EquipmentReservationFactory(
        equipment=item, member=member, starts_at=starts, ends_at=starts + timedelta(hours=2), **kwargs
    )


def _start_url(booking: EquipmentReservation) -> str:
    return reverse("hub_equipment_session_start", args=[booking.equipment.slug, booking.pk])


def _stop_url(booking: EquipmentReservation) -> str:
    return reverse("hub_equipment_session_stop", args=[booking.equipment.slug, booking.pk])


def _session_url(item: Equipment) -> str:
    return reverse("hub_equipment_session", args=[item.slug])


def _schedule(client: Client, item: Equipment) -> str:
    return client.get(reverse("hub_equipment_schedule", args=[item.slug])).content.decode()


def _bookings(client: Client) -> str:
    return client.get("/equipment/", {"view": "bookings"}).content.decode()


def _toast(response: Any) -> dict[str, str]:
    return json.loads(response["HX-Trigger"])["showToast"]


def _messages(response: Any) -> list[str]:
    return [str(m) for m in django_messages.get_messages(response.wsgi_request)]


def _balance(member: Member, item: Equipment) -> int:
    return EquipmentCredit.objects.balance_for(member, item)


def describe_the_sessions_switch():
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

    def it_sits_beside_end_early_in_the_hourly_fields_only(client: Client):
        item = _laser(sessions_enabled=False)
        _manager(client, item)
        content = client.get(f"{reverse('hub_equipment_manage', args=[item.slug])}?tab=hours").content.decode()
        hourly = content.split("data-pricing-hourly", 1)[1].split("data-pricing-donation", 1)[0]
        assert "data-sessions-switch" in hourly
        assert 'data-guidance-target="reservations.sessions-switch"' in hourly
        assert "Sessions (Start and Stop)" in hourly
        assert hourly.index("data-early-end-switch") < hourly.index("data-sessions-switch")
        # The QR follows the saved switch, so it is not there yet.
        assert "data-session-qr" not in content

    def it_saves_while_hourly_and_keeps_its_value_otherwise(client: Client):
        item = _laser(sessions_enabled=False)
        _manager(client, item)
        url = reverse("hub_equipment_hours_save", args=[item.slug])
        client.post(url, _settings(pricing="hourly", hourly_rate="25", sessions_enabled="on"))
        item.refresh_from_db()
        assert item.sessions_enabled is True
        client.post(url, _settings(pricing="donation", donation_minimum="", donation_suggested=""))
        item.refresh_from_db()
        assert (item.pricing, item.sessions_enabled) == (Equipment.Pricing.DONATION, True)
        assert not item.has_sessions
        client.post(url, _settings())
        item.refresh_from_db()
        assert item.sessions_enabled is True
        Equipment.objects.filter(pk=item.pk).update(pricing=Equipment.Pricing.HOURLY)
        client.post(url, _settings(pricing="hourly", hourly_rate="25"))
        item.refresh_from_db()
        assert item.sessions_enabled is False


def describe_the_item_page():
    def it_offers_start_session_on_the_members_booking_in_progress(client: Client):
        item = _laser(early_end_credit=True)
        member = _login(client, "ashstarter")
        _in_progress(item, member)
        content = _schedule(client, item)
        assert "data-session-start" in content
        assert 'data-guidance-target="reservations.start-session"' in content
        # Stop Session replaces End Early while Sessions are on.
        assert "data-end-early" not in content

    def it_offers_nothing_before_the_start_after_the_end_or_on_someone_elses_booking(client: Client):
        item = _laser()
        member = _login(client, "ashwaiter")
        _later(item, member)
        _in_progress(item, _member("ashother"), starts_at=timezone.now() - timedelta(minutes=10))
        content = _schedule(client, item)
        assert "data-session-start" not in content
        assert "data-session-stop" not in content

    def it_starts_and_answers_the_schedule_with_a_toast(client: Client):
        item = _laser()
        member = _login(client, "ashpresser")
        booking = _in_progress(item, member)
        response = client.post(_start_url(booking), {"week": "0", "day": ""}, HTTP_HX_REQUEST="true")
        toast = _toast(response)
        assert toast["type"] == "success"
        assert toast["message"].startswith("Session started at ")
        content = response.content.decode()
        assert 'id="equipment-schedule"' in content
        assert "data-session-stop" in content
        assert f'data-session-times="{booking.pk}"' in content
        assert "Session started " in content

    def it_changes_nothing_on_a_second_start(client: Client):
        item = _laser()
        member = _login(client, "ashdoubletap")
        booking = _in_progress(item, member)
        client.post(_start_url(booking), {}, HTTP_HX_REQUEST="true")
        booking.refresh_from_db()
        first = booking.session_started_at
        response = client.post(_start_url(booking), {}, HTTP_HX_REQUEST="true")
        assert _toast(response)["type"] == "info"
        assert _toast(response)["message"].startswith("Your session already started at ")
        booking.refresh_from_db()
        assert booking.session_started_at == first

    def it_stops_with_a_confirm_naming_the_credit_and_adds_it(client: Client):
        item = _laser()
        member = _login(client, "ashstopper")
        booking = _in_progress(item, member, session_started_at=timezone.now() - timedelta(minutes=45))
        content = _schedule(client, item)
        assert "data-session-stop" in content
        assert f"session-stop-{booking.pk}" in content
        assert "Stop your session now?" in content
        assert "you have not used become credit here." in content
        expected = booking.end_early_minutes(timezone.now())
        response = client.post(_stop_url(booking), {"week": "0", "day": ""}, HTTP_HX_REQUEST="true")
        toast = _toast(response)
        assert toast["type"] == "success"
        assert toast["message"].startswith("Session stopped at ")
        assert toast["message"].endswith(" of credit added.")
        assert _balance(member, item) == expected

    def it_tells_the_member_why_stop_added_no_credit(client: Client):
        item = _laser()
        member = _login(client, "ashlastminute")
        now = timezone.now()
        booking = _in_progress(
            item,
            member,
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(minutes=20),
            session_started_at=now - timedelta(minutes=55),
        )
        assert "Less than half an hour is left, so stopping adds no credit." in _schedule(client, item)
        response = client.post(_stop_url(booking), {}, HTTP_HX_REQUEST="true")
        assert _toast(response)["message"].endswith("Less than half an hour is left, so there is nothing to credit.")
        booking.refresh_from_db()
        assert booking.session_stopped_at is not None
        assert _balance(member, item) == 0

    def it_shows_nothing_about_sessions_with_the_switch_off_and_end_early_as_before(client: Client):
        item = _laser(sessions_enabled=False, early_end_credit=True)
        member = _login(client, "ashswitchoff")
        _in_progress(item, member)
        content = _schedule(client, item)
        assert "data-session-start" not in content
        assert "data-session-stop" not in content
        assert "data-end-early" in content


def describe_crafted_posts():
    @pytest.mark.parametrize(
        ("case", "message"),
        [
            ("not_owner", "Only the member who booked this can start or stop its session."),
            ("switch_off", "Sessions aren't turned on for this equipment."),
            ("free_item", "Sessions aren't turned on for this equipment."),
            ("season", "A season reservation has no sessions."),
            ("pending_approval", "Only a confirmed reservation has a session."),
            ("after_end", "This reservation already ended."),
        ],
    )
    def it_refuses_a_start_and_records_nothing(client: Client, case: str, message: str):
        item = _laser(sessions_enabled=case != "switch_off")
        if case == "free_item":
            Equipment.objects.filter(pk=item.pk).update(pricing=Equipment.Pricing.FREE)
        if case == "not_owner":
            _login(client, "ashintruder")
            booking = _in_progress(item, _member("ashowner"))
        else:
            owner = _login(client, "ashcrafter")
            booking = _in_progress(item, owner)
        rows = EquipmentReservation.objects.filter(pk=booking.pk)
        if case == "season":
            rows.update(is_season=True)
        elif case == "pending_approval":
            rows.update(status=EquipmentReservation.Status.PENDING_APPROVAL)
        elif case == "after_end":
            rows.update(ends_at=timezone.now() - timedelta(minutes=1))
        response = client.post(_start_url(booking), {"next": _session_url(item)})
        assert _messages(response) == [message]
        booking.refresh_from_db()
        assert booking.session_started_at is None

    @pytest.mark.parametrize(
        ("case", "message"),
        [
            ("not_owner", "Only the member who booked this can start or stop its session."),
            ("manager", "Only the member who booked this can start or stop its session."),
            ("switch_off", "Sessions aren't turned on for this equipment."),
            ("season", "A season reservation has no sessions."),
            ("after_end", "This reservation already ended, so its session ended with it."),
        ],
    )
    def it_refuses_a_stop_and_credits_nothing(client: Client, case: str, message: str):
        item = _laser(sessions_enabled=case != "switch_off")
        started = timezone.now() - timedelta(minutes=45)
        if case == "not_owner":
            _login(client, "ashintruder")
            booking = _in_progress(item, _member("ashowner"), session_started_at=started)
        elif case == "manager":
            _manager(client, item, "ashonbehalf")
            booking = _in_progress(item, _member("ashowner"), session_started_at=started)
        else:
            booking = _in_progress(item, _login(client, "ashcrafter"), session_started_at=started)
        rows = EquipmentReservation.objects.filter(pk=booking.pk)
        if case == "season":
            rows.update(is_season=True)
        elif case == "after_end":
            rows.update(ends_at=timezone.now() - timedelta(minutes=1))
        response = client.post(_stop_url(booking), {"next": _session_url(item)})
        assert _messages(response) == [message]
        booking.refresh_from_db()
        assert booking.session_stopped_at is None
        assert not EquipmentCredit.objects.exists()

    def it_never_credits_a_second_stop(client: Client):
        item = _laser()
        member = _login(client, "ashtwostops")
        booking = _in_progress(item, member, session_started_at=timezone.now() - timedelta(minutes=45))
        client.post(_stop_url(booking), {"next": _session_url(item)})
        balance = _balance(member, item)
        response = client.post(_stop_url(booking), {"next": _session_url(item)})
        # The first stop's message is still queued (the redirect was not followed); the second is last.
        assert _messages(response)[-1].startswith("Your session already stopped at ")
        assert _balance(member, item) == balance > 0
        assert EquipmentCredit.objects.filter(reservation=booking).count() == 1

    def it_404s_a_reservation_on_another_item(client: Client):
        item = _laser()
        other = _laser(name="Thornwick Lathe", slug="thornwick-lathe")
        booking = _in_progress(other, _login(client, "ashwrongitem"))
        url = reverse("hub_equipment_session_start", args=[item.slug, booking.pk])
        assert client.post(url).status_code == 404

    def it_refuses_a_memberless_account(client: Client):
        item = _laser()
        booking = _in_progress(item, _member("ashsomeowner"))
        user = User.objects.create_user(username="ashnomember", password="pass")
        Member.objects.filter(user=user).delete()
        client.login(username="ashnomember", password="pass")
        assert client.post(_start_url(booking)).status_code == 403
        assert client.post(_stop_url(booking)).status_code == 403


def describe_the_bookings_tab():
    def it_offers_start_on_the_members_own_row_and_lands_back_with_a_message(client: Client):
        item = _laser()
        member = _login(client, "ashtabber")
        booking = _in_progress(item, member)
        content = _bookings(client)
        assert "data-session-start" in content
        assert 'data-guidance-target="reservations.start-session"' in content
        next_url = "/equipment/?view=bookings"
        response = client.post(_start_url(booking), {"next": next_url})
        assert response.status_code == 302
        assert response.url == next_url
        [message] = _messages(response)
        assert message.startswith("Session started at ")

    def it_offers_stop_with_its_confirm_and_shows_the_times(client: Client):
        item = _laser()
        member = _login(client, "ashtabstop")
        booking = _in_progress(item, member, session_started_at=timezone.now() - timedelta(minutes=45))
        content = _bookings(client)
        assert "data-session-stop" in content
        assert f"reservation-session-stop-{booking.pk}" in content
        assert f'data-session-times="{booking.pk}"' in content
        response = client.post(_stop_url(booking), {"next": "/equipment/?view=bookings"})
        [message] = _messages(response)
        assert message.endswith(" of credit added.")

    def it_shows_a_started_session_to_its_managers_but_never_their_buttons(client: Client):
        item = _laser()
        booking = _in_progress(
            item,
            _member("ashrenter"),
            session_started_at=timezone.now() - timedelta(minutes=45),
            session_stopped_at=timezone.now() - timedelta(minutes=5),
        )
        _manager(client, item)
        content = _bookings(client)
        assert f'data-reservation-row="{booking.pk}"' in content
        assert f'data-session-times="{booking.pk}"' in content
        assert "data-session-start" not in content
        assert "data-session-stop" not in content

    def it_shows_the_booked_end_for_a_session_left_running(client: Client):
        item = _laser()
        member = _login(client, "ashforgot")
        ended = timezone.now() - timedelta(minutes=10)
        booking = EquipmentReservationFactory(
            equipment=item,
            member=member,
            starts_at=ended - timedelta(hours=2),
            ends_at=ended,
            session_started_at=ended - timedelta(hours=2),
        )
        content = client.get("/equipment/", {"view": "bookings", "show": "past"}).content.decode()
        row = content.split(f'data-session-times="{booking.pk}">', 1)[1].split("</span>", 1)[0]
        assert row.startswith("Session ") and " to " in row
        assert row.endswith(timezone.localtime(ended).strftime("%-I:%M %p"))


def describe_the_manage_page():
    def it_lists_recent_sessions_with_their_times_on_the_reservations_tab(client: Client):
        item = _laser()
        booking = _in_progress(item, _member("ashsessioned"), session_started_at=timezone.now() - timedelta(minutes=45))
        _manager(client, item)
        content = client.get(f"{reverse('hub_equipment_manage', args=[item.slug])}?tab=reservations").content.decode()
        assert "data-sessions-card" in content
        assert f'data-session-row="{booking.pk}"' in content
        # On the card and on its Upcoming row.
        assert content.count(f'data-session-times="{booking.pk}"') == 2

    def it_says_there_are_none_yet_and_hides_the_card_with_sessions_off(client: Client):
        item = _laser()
        _manager(client, item)
        url = f"{reverse('hub_equipment_manage', args=[item.slug])}?tab=reservations"
        assert "data-sessions-empty" in client.get(url).content.decode()
        Equipment.objects.filter(pk=item.pk).update(sessions_enabled=False)
        assert "data-sessions-card" not in client.get(url).content.decode()

    def it_shows_the_session_qr_with_its_downloads_once_sessions_are_on(client: Client):
        item = _laser()
        _manager(client, item)
        content = client.get(f"{reverse('hub_equipment_manage', args=[item.slug])}?tab=hours").content.decode()
        assert "data-session-qr" in content
        assert item.session_qr_svg() in content
        assert reverse("hub_equipment_session_qr", args=[item.slug, "svg"]) in content
        assert reverse("hub_equipment_session_qr", args=[item.slug, "png"]) in content


def describe_the_session_qr_downloads():
    def it_serves_svg_and_png_to_a_manager(client: Client):
        item = _laser()
        _manager(client, item)
        svg = client.get(reverse("hub_equipment_session_qr", args=[item.slug, "svg"]))
        png = client.get(reverse("hub_equipment_session_qr", args=[item.slug, "png"]))
        assert svg["Content-Type"] == "image/svg+xml"
        assert svg.content.decode() == item.session_qr_svg()
        assert svg["Content-Disposition"] == 'attachment; filename="thornwick-laser-session-qr.svg"'
        assert png["Content-Type"] == "image/png"
        assert png.content == item.session_qr_png_bytes()

    def it_forbids_a_plain_member(client: Client):
        item = _laser()
        _login(client, "ashplain")
        assert client.get(reverse("hub_equipment_session_qr", args=[item.slug, "svg"])).status_code == 403

    def it_404s_with_sessions_off_and_on_an_unknown_format(client: Client):
        item = _laser()
        _manager(client, item)
        assert client.get(reverse("hub_equipment_session_qr", args=[item.slug, "gif"])).status_code == 404
        Equipment.objects.filter(pk=item.pk).update(sessions_enabled=False)
        assert client.get(reverse("hub_equipment_session_qr", args=[item.slug, "svg"])).status_code == 404


def describe_the_qr_sheet():
    def it_carries_the_session_qr_and_its_downloads_while_sessions_are_on(client: Client):
        item = _laser()
        _manager(client, item)
        content = client.get(reverse("hub_equipment_flyer", args=[item.slug])).content.decode()
        assert 'data-qr-target="session"' in content
        assert "Start Your Session" in content
        assert item.session_qr_svg() in content
        assert item.session_qr_url in content
        assert 'data-session-qr-download="svg"' in content

    def it_is_the_sheet_as_before_with_sessions_off(client: Client):
        item = _laser(sessions_enabled=False)
        _manager(client, item)
        content = client.get(reverse("hub_equipment_flyer", args=[item.slug])).content.decode()
        assert 'data-qr-target="session"' not in content
        assert "data-session-qr-download" not in content


def _scan_and_log_in(client: Client, path: str) -> str:
    """Scan ``path`` signed out, sign in by emailed code, and return where sign in lands."""
    MembershipPlanFactory()
    user = User.objects.create_user(username="ashscanner", email="ash-session-scanner@example.com")
    EmailAddress.objects.filter(user=user).delete()
    EmailAddress.objects.create(user=user, email="ash-session-scanner@example.com", verified=True, primary=True)
    scan = client.get(path)
    assert scan.status_code == 302
    assert scan["Location"] == f"/accounts/login/?next={path}"
    mail.outbox.clear()
    client.post("/accounts/login/code/", {"email": "ash-session-scanner@example.com", "next": path})
    code = mail.outbox[-1].body.split("Past Lives Makerspace is:")[1].split()[0]
    done = client.post("/accounts/login/code/confirm/", {"code": code, "next": path})
    assert done.status_code == 302
    return done["Location"]


def describe_the_session_page():
    def _page(client: Client, item: Equipment) -> str:
        return client.get(_session_url(item)).content.decode()

    def it_signs_a_signed_out_scan_in_and_lands_back_on_it(client: Client):
        item = _laser()
        path = urlparse(item.session_qr_url).path
        assert path == _session_url(item)
        assert _scan_and_log_in(client, path) == path

    def it_offers_start_on_the_current_reservation(client: Client):
        item = _laser()
        booking = _in_progress(item, _login(client, "ashscanstart"))
        content = _page(client, item)
        assert 'data-session-page="start"' in content
        assert "data-session-booking" in content
        assert _start_url(booking) in content
        assert 'data-guidance-target="reservations.start-session"' in content

    def it_starts_then_offers_stop_then_says_what_happened(client: Client):
        item = _laser()
        member = _login(client, "ashscanloop")
        booking = _in_progress(item, member)
        started = client.post(_start_url(booking), {"next": _session_url(item)})
        assert started.url == _session_url(item)
        content = _page(client, item)
        assert 'data-session-page="running"' in content
        assert "data-session-stop" in content
        assert f"session-stop-{booking.pk}" in content
        stopped = client.post(_stop_url(booking), {"next": _session_url(item)})
        [message] = _messages(stopped)
        assert message.endswith(" of credit added.")
        content = _page(client, item)
        assert 'data-session-page="stopped"' in content
        assert f'data-session-times="{booking.pk}"' in content
        assert "data-session-credit" in content

    def it_says_there_is_nothing_current_and_links_to_book(client: Client):
        item = _laser()
        member = _login(client, "ashscannothing")
        _later(item, member)
        content = _page(client, item)
        assert 'data-session-page="none"' in content
        assert "data-session-upcoming" in content
        assert f'href="{reverse("hub_equipment_detail", args=[item.slug])}#equipment-schedule"' in content

    def it_never_shows_someone_elses_or_a_season_reservation(client: Client):
        item = _laser()
        member = _login(client, "ashscanseason")
        _in_progress(item, _member("ashneighbour"))
        _in_progress(_laser(name="Thornwick Bed", slug="thornwick-bed"), member)
        _in_progress(
            item,
            member,
            is_season=True,
            starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=30),
        )
        assert 'data-session-page="none"' in _page(client, item)

    def it_explains_a_reservation_awaiting_approval(client: Client):
        item = _laser()
        _in_progress(item, _login(client, "ashscanwait"), status=EquipmentReservation.Status.PENDING_APPROVAL)
        content = _page(client, item)
        assert 'data-session-page="waiting"' in content
        assert "waiting for a manager's approval" in content
        assert "data-session-start" not in content

    def it_explains_a_reservation_awaiting_payment(client: Client):
        item = _laser()
        _in_progress(item, _login(client, "ashscanpay"), status=EquipmentReservation.Status.PENDING_PAYMENT)
        assert "Your payment hasn't come through" in _page(client, item)

    def it_says_sessions_are_off(client: Client):
        item = _laser(sessions_enabled=False)
        _in_progress(item, _login(client, "ashscanoff"))
        content = _page(client, item)
        assert 'data-session-page="off"' in content
        assert "data-session-start" not in content

    def it_offers_stop_without_credit_in_the_last_half_hour(client: Client):
        item = _laser()
        now = timezone.now()
        _in_progress(
            item,
            _login(client, "ashscanlate"),
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(minutes=20),
            session_started_at=now - timedelta(minutes=55),
        )
        assert "Less than half an hour is left, so stopping adds no credit." in _page(client, item)

    def it_hides_a_retired_item_from_a_member(client: Client):
        item = _laser(is_active=False)
        _login(client, "ashscanretired")
        assert client.get(_session_url(item)).status_code == 404

    def it_404s_a_memberless_account(client: Client):
        item = _laser()
        user = User.objects.create_user(username="ashscannomember", password="pass")
        Member.objects.filter(user=user).delete()
        client.login(username="ashscannomember", password="pass")
        assert client.get(_session_url(item)).status_code == 404
