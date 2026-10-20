"""Specs for Move to Another Time on the hub (#853).

The leader's move endpoint and its refusals, the respond page panel, the Bookings tab row menu
and modal, the member's booking card with Accept and Keep, the member's answer endpoint, and
the no-login Accept and Keep links. Member names are factory strings no changelog could
contain (STANDARDS.md section 8).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import SiteConfiguration
from hub.forms import OrientationMoveForm
from membership import orientations
from membership.models import Member, OrientationBooking, OrientationSlot
from tests.membership.factories import (
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _user(username: str, name: str = "") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    member = user.member
    if name:
        member.full_legal_name = name
        member.save(update_fields=["full_legal_name"])
    return member


def _login(client: Client, member: Member) -> Member:
    client.force_login(member.user)
    return member


def _world(
    client: Client, *, status: str = OrientationBooking.Status.REQUESTED, **booking_kwargs: Any
) -> tuple[Member, OrientationBooking]:
    """A logged in guild lead and a booking on their guild by a member with an account."""
    lead = _login(client, _user("mvv_lead", "Lead Quillfeather"))
    guild = GuildFactory(guild_lead=lead, name="Mossbank Guild")
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    orientation_type = OrientationTypeFactory(guild=guild, name="Lathe Basics")
    slot = OrientationSlotFactory(guild=guild, orientation_type=orientation_type)
    booking = OrientationBookingFactory(
        slot=slot, member=_user("mvv_member", "Orla Thistlewood"), status=status, **booking_kwargs
    )
    return lead, booking


def _future_day(days: int = 6) -> str:
    return timezone.localdate(timezone.now() + timedelta(days=days)).isoformat()


def _post_move(client: Client, booking: OrientationBooking, **data: str) -> Any:
    payload = {"date": _future_day(), "start_time": "14:00", "mode": "now", **data}
    return client.post(reverse("hub_orientation_move", args=[booking.pk]), payload)


def _messages(response: Any) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def _expected_start(days: int = 6) -> datetime:
    day = timezone.localdate(timezone.now() + timedelta(days=days))
    return timezone.make_aware(datetime.combine(day, datetime.min.time().replace(hour=14)))


def describe_the_move_endpoint():
    def it_moves_now_and_lands_on_the_request_page(client: Client):
        _lead, booking = _world(client)

        response = _post_move(client, booking)

        assert response.status_code == 302
        assert response["Location"] == reverse("hub_orientation_respond", args=[booking.pk])
        booking.refresh_from_db()
        assert booking.slot.starts_at == _expected_start()
        assert booking.status == OrientationBooking.Status.CONFIRMED
        assert _messages(response) == ["Orientation moved and confirmed. We emailed Orla Thistlewood."]

    def it_asks_the_member_first_and_keeps_their_time(client: Client):
        _lead, booking = _world(client)
        slot_pk = booking.slot_id

        response = _post_move(client, booking, mode="ask")

        booking.refresh_from_db()
        assert booking.slot_id == slot_pk
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert booking.proposed_starts_at == _expected_start()
        assert "We asked Orla Thistlewood about the new time." in _messages(response)[0]

    def it_lands_on_a_local_next(client: Client):
        _lead, booking = _world(client)

        response = _post_move(client, booking, next="/manage/bookings/")

        assert response["Location"] == "/manage/bookings/"

    @pytest.mark.parametrize(
        ("data", "error"),
        [
            ({"mode": ""}, "Pick Move it now or Ask the member first."),
            ({"mode": "later"}, "Pick Move it now or Ask the member first."),
            ({"date": ""}, "Pick a date."),
            ({"start_time": "03:15"}, "Pick a start time from the list."),
        ],
    )
    def it_refuses_a_bad_form_with_a_friendly_error_and_changes_nothing(
        client: Client, data: dict[str, str], error: str
    ):
        _lead, booking = _world(client)
        slots_before = OrientationSlot.objects.count()

        response = _post_move(client, booking, **data)

        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert not booking.has_open_proposal
        assert OrientationSlot.objects.count() == slots_before
        assert error in _messages(response)[0]
        assert mail.outbox == []

    def it_refuses_a_past_time(client: Client):
        _lead, booking = _world(client)
        yesterday = timezone.localdate(timezone.now() - timedelta(days=1)).isoformat()

        response = _post_move(client, booking, date=yesterday)

        booking.refresh_from_db()
        assert booking.moved_by_guild_at is None
        assert "Pick a time in the future." in _messages(response)[0]

    def it_answers_403_to_a_member_who_cannot_manage_the_booking(client: Client):
        _lead, booking = _world(client)
        _login(client, _user("mvv_outsider"))

        response = _post_move(client, booking)

        assert response.status_code == 403
        booking.refresh_from_db()
        assert booking.moved_by_guild_at is None

    def it_answers_403_to_the_lead_of_another_guild(client: Client):
        _lead, booking = _world(client)
        other_lead = _user("mvv_other_lead")
        GuildFactory(guild_lead=other_lead)
        _login(client, other_lead)

        assert _post_move(client, booking, mode="ask").status_code == 403
        booking.refresh_from_db()
        assert not booking.has_open_proposal

    def it_lets_an_equipment_manager_move_an_equipment_owned_booking(client: Client):
        manager = _login(client, _user("mvv_manager"))
        booking = OrientationBookingFactory(equipment_owned=True, member=_user("mvv_eq_member"))
        EquipmentStaffMembershipFactory(equipment=booking.orientation_type.equipment, member=manager)

        _post_move(client, booking)

        booking.refresh_from_db()
        assert booking.moved_by_guild_at is not None

    @pytest.mark.parametrize("status", [OrientationBooking.Status.PENDING_PAYMENT, OrientationBooking.Status.CANCELLED])
    def it_refuses_a_crafted_post_for_a_booking_that_cannot_move(client: Client, status: str):
        _lead, booking = _world(client, status=status)

        response = _post_move(client, booking)

        booking.refresh_from_db()
        assert booking.status == status
        assert booking.moved_by_guild_at is None
        assert response.status_code == 302
        assert _messages(response)

    def it_takes_post_only(client: Client):
        _lead, booking = _world(client)
        assert client.get(reverse("hub_orientation_move", args=[booking.pk])).status_code == 405


def describe_the_respond_page():
    @pytest.mark.parametrize("status", [OrientationBooking.Status.REQUESTED, OrientationBooking.Status.CONFIRMED])
    def it_offers_move_on_a_live_booking(client: Client, status: str):
        _lead, booking = _world(client, status=status)

        content = client.get(reverse("hub_orientation_respond", args=[booking.pk])).content.decode()

        assert "data-orientation-move-open" in content
        assert reverse("hub_orientation_move", args=[booking.pk]) in content
        assert 'id="id_move_mode_0"' in content
        assert "Nothing is refunded or charged." in content

    @pytest.mark.parametrize(
        ("status", "completed"),
        [
            (OrientationBooking.Status.DECLINED, False),
            (OrientationBooking.Status.CANCELLED, False),
            (OrientationBooking.Status.PENDING_PAYMENT, False),
            (OrientationBooking.Status.CONFIRMED, True),
        ],
    )
    def it_hides_move_on_a_booking_that_cannot_move(client: Client, status: str, completed: bool):
        _lead, booking = _world(client, status=status, is_completed=completed)

        content = client.get(reverse("hub_orientation_respond", args=[booking.pk])).content.decode()

        assert "data-orientation-move-open" not in content
        assert reverse("hub_orientation_move", args=[booking.pk]) not in content

    def it_names_the_paid_amount_that_stays(client: Client):
        _lead, booking = _world(client, amount_paid_cents=3500)

        content = client.get(reverse("hub_orientation_respond", args=[booking.pk])).content.decode()

        assert "Their $35 payment stays with the booking. Nothing is refunded or charged." in content

    def it_shows_an_open_proposal(client: Client):
        lead, booking = _world(client)
        orientations.propose_orientation_time(booking, _expected_start(), actor=lead)

        content = client.get(reverse("hub_orientation_respond", args=[booking.pk])).content.decode()

        assert "data-booking-proposal" in content
        assert "You asked Orla Thistlewood to move to" in content


def _tab(client: Client) -> str:
    response = client.get("/orientations/", {"view": "bookings", "show": "all"})
    assert response.status_code == 200
    return response.content.decode()


def _row(content: str, booking: OrientationBooking) -> str:
    start = content.index(f'data-booking-row="{booking.pk}"')
    return content[start : content.index("</tr>", start)]


def describe_the_bookings_tab():
    def it_offers_move_in_a_live_rows_menu_with_its_own_modal(client: Client):
        lead, requested = _world(client)
        confirmed = OrientationBookingFactory(
            slot=OrientationSlotFactory(guild=requested.guild, orientation_type=requested.orientation_type),
            member=MemberFactory(full_legal_name="Bram Hollowell"),
            status=OrientationBooking.Status.CONFIRMED,
        )

        content = _tab(client)

        for booking in (requested, confirmed):
            assert f'data-booking-move="{booking.pk}"' in _row(content, booking)
            assert f"'booking-move-{booking.pk}'" in content
            assert f'id="id_move_{booking.pk}_mode_0"' in content
        assert content.count('name="next" value="/orientations/?view=bookings') >= 2

    @pytest.mark.parametrize(
        ("status", "completed"),
        [
            (OrientationBooking.Status.CANCELLED, False),
            (OrientationBooking.Status.DECLINED, False),
            (OrientationBooking.Status.PENDING_PAYMENT, False),
            (OrientationBooking.Status.CONFIRMED, True),
        ],
    )
    def it_offers_no_move_on_a_row_that_cannot_move(client: Client, status: str, completed: bool):
        _lead, booking = _world(client, status=status, is_completed=completed)

        content = _tab(client)

        assert f'data-booking-move="{booking.pk}"' not in content
        assert f"booking-move-{booking.pk}" not in content

    def it_pills_a_row_with_an_open_proposal(client: Client):
        lead, booking = _world(client)
        orientations.propose_orientation_time(booking, _expected_start(), actor=lead)

        assert "data-booking-proposal" in _row(_tab(client), booking)

    def it_offers_no_move_to_a_member_on_their_own_row(client: Client):
        _lead, booking = _world(client)
        _login(client, booking.member)

        content = _tab(client)

        assert f'data-booking-row="{booking.pk}"' in content
        assert f'data-booking-move="{booking.pk}"' not in content


def describe_the_member_booking_card():
    def it_shows_accept_and_keep_for_an_open_proposal(client: Client):
        lead, booking = _world(client)
        orientations.propose_orientation_time(booking, _expected_start(), actor=lead)
        booking.refresh_from_db()
        _login(client, booking.member)

        content = client.get("/orientations/").content.decode()

        assert f'data-orientation-proposal="{booking.pk}"' in content
        assert "data-proposal-accept" in content
        assert "data-proposal-keep" in content
        assert f'name="proposal" value="{booking.proposal_key}"' in content
        assert reverse("hub_orientation_proposal_answer", args=[booking.pk]) in content

    def it_shows_it_on_the_guild_page_too(client: Client):
        lead, booking = _world(client)
        orientations.propose_orientation_time(booking, _expected_start(), actor=lead)
        _login(client, booking.member)

        content = client.get(reverse("hub_guild_detail", args=[booking.guild.slug])).content.decode()

        assert f'data-orientation-proposal="{booking.pk}"' in content
        assert "The guild asked to move this to" in content

    def it_says_a_manager_asked_on_an_equipment_booking(client: Client):
        member = _login(client, _user("mvv_card_eq"))
        booking = OrientationBookingFactory(equipment_owned=True, member=member)
        orientations.propose_orientation_time(booking, _expected_start(), actor=None)

        content = client.get("/orientations/").content.decode()

        assert "A manager asked to move this to" in content

    def it_hides_a_proposal_whose_time_has_passed(client: Client):
        lead, booking = _world(client)
        orientations.propose_orientation_time(booking, _expected_start(), actor=lead)
        OrientationBooking.objects.filter(pk=booking.pk).update(proposed_starts_at=timezone.now() - timedelta(hours=1))
        _login(client, booking.member)

        assert "data-orientation-proposal" not in client.get("/orientations/").content.decode()
        _login(client, lead)
        assert (
            "data-booking-proposal"
            not in client.get(reverse("hub_orientation_respond", args=[booking.pk])).content.decode()
        )

    def it_shows_nothing_without_a_proposal(client: Client):
        _lead, booking = _world(client)
        _login(client, booking.member)

        assert "data-orientation-proposal" not in client.get("/orientations/").content.decode()


def _answer(client: Client, booking: OrientationBooking, answer: str, **extra: str) -> Any:
    data = {"answer": answer, "proposal": booking.proposal_key, **extra}
    return client.post(reverse("hub_orientation_proposal_answer", args=[booking.pk]), data)


def _proposed_world(client: Client, **kwargs: Any) -> OrientationBooking:
    lead, booking = _world(client, **kwargs)
    orientations.propose_orientation_time(booking, _expected_start(), actor=lead)
    booking.refresh_from_db()
    _login(client, booking.member)
    return booking


def describe_the_member_answer_endpoint():
    def it_accepts_the_new_time(client: Client):
        booking = _proposed_world(client)

        response = _answer(client, booking, "accept", next="/orientations/")

        assert response["Location"] == "/orientations/"
        booking.refresh_from_db()
        assert booking.slot.starts_at == _expected_start()
        assert booking.status == OrientationBooking.Status.CONFIRMED
        assert _messages(response) == ["Your orientation moved to the new time. It's confirmed."]

    def it_keeps_the_current_time(client: Client):
        booking = _proposed_world(client)
        slot_pk = booking.slot_id

        response = _answer(client, booking, "keep")

        booking.refresh_from_db()
        assert booking.slot_id == slot_pk
        assert not booking.has_open_proposal
        assert _messages(response) == ["You kept your current time. We let the guild know."]
        assert response["Location"].startswith(reverse("hub_guild_detail", args=[booking.guild.slug]))

    def it_changes_nothing_for_a_replaced_proposal(client: Client):
        booking = _proposed_world(client)

        response = _answer(client, booking, "accept", proposal="12345")

        booking.refresh_from_db()
        assert booking.has_open_proposal
        assert booking.moved_by_guild_at is None
        assert _messages(response) == ["That new time is no longer open. Your booking shows where things stand."]

    def it_says_so_when_the_new_time_has_passed(client: Client):
        booking = _proposed_world(client)
        OrientationBooking.objects.filter(pk=booking.pk).update(proposed_starts_at=timezone.now() - timedelta(hours=1))

        response = _answer(client, booking, "accept")

        assert _messages(response) == [
            "That new time has already passed, so your orientation stays at its current time."
        ]

    def it_says_so_when_the_new_time_cannot_be_had(client: Client):
        member = _login(client, _user("mvv_unavail"))
        booking = OrientationBookingFactory(equipment_owned=True, member=member)
        orientations.propose_orientation_time(booking, _expected_start(), actor=None)
        booking.refresh_from_db()
        EquipmentReservationFactory(
            equipment=booking.orientation_type.equipment,
            starts_at=_expected_start(),
            ends_at=_expected_start() + timedelta(hours=1),
        )

        response = _answer(client, booking, "accept")

        assert _messages(response) == [
            "That new time can't be booked anymore, so your orientation stays at its current time."
        ]

    def it_answers_403_to_anyone_but_the_member(client: Client):
        booking = _proposed_world(client)
        _login(client, _user("mvv_stranger"))

        assert _answer(client, booking, "accept").status_code == 403
        booking.refresh_from_db()
        assert booking.has_open_proposal

    def it_answers_400_to_an_unknown_answer(client: Client):
        booking = _proposed_world(client)
        assert _answer(client, booking, "maybe").status_code == 400


def _link(booking: OrientationBooking, action: str, proposal: str | None = None) -> str:
    key = booking.proposal_key if proposal is None else proposal
    token = orientations.make_action_token(booking, action, recipient=booking.member, proposal=key)
    return reverse("hub_orientation_action", args=[token])


def describe_the_no_login_links():
    def it_asks_before_accepting_and_shows_both_times(client: Client):
        booking = _proposed_world(client)
        client.logout()

        content = client.get(_link(booking, "accept_time")).content.decode()

        assert "Move to the new time?" in content
        assert "data-proposal-times" in content
        assert "Accept new time" in content
        booking.refresh_from_db()
        assert booking.has_open_proposal

    def it_accepts_on_post(client: Client):
        booking = _proposed_world(client)
        client.logout()

        content = client.post(_link(booking, "accept_time")).content.decode()

        assert "Your Orientation Moved" in content
        booking.refresh_from_db()
        assert booking.moved_by_guild_at is not None

    def it_keeps_on_post(client: Client):
        booking = _proposed_world(client)
        client.logout()

        get_content = client.get(_link(booking, "keep_time")).content.decode()
        content = client.post(_link(booking, "keep_time")).content.decode()

        assert "Keep your current time?" in get_content
        assert "You Kept Your Time" in content
        booking.refresh_from_db()
        assert not booking.has_open_proposal

    def it_lands_a_replaced_proposals_link_on_a_friendly_page(client: Client):
        booking = _proposed_world(client)
        old_link = _link(booking, "accept_time")
        orientations.propose_orientation_time(booking, _expected_start(8), actor=None)
        client.logout()

        before = client.get(old_link)
        after = client.post(old_link)

        assert before.status_code == 200
        assert "This New Time Is No Longer Open" in before.content.decode()
        assert "This New Time Is No Longer Open" in after.content.decode()
        booking.refresh_from_db()
        assert booking.moved_by_guild_at is None

    def it_lands_an_already_answered_link_on_the_same_page(client: Client):
        booking = _proposed_world(client)
        link = _link(booking, "keep_time")
        client.post(link)

        assert "This New Time Is No Longer Open" in client.post(link).content.decode()

    def it_says_a_passed_new_time_has_passed_before_and_after_the_click(client: Client):
        booking = _proposed_world(client)
        link = _link(booking, "accept_time")
        OrientationBooking.objects.filter(pk=booking.pk).update(proposed_starts_at=timezone.now() - timedelta(hours=1))
        client.logout()

        assert "That New Time Has Passed" in client.get(link).content.decode()
        assert "That New Time Has Passed" in client.post(link).content.decode()

    def it_says_an_unbookable_new_time_cannot_be_booked(client: Client):
        member = _login(client, _user("mvv_link_unavail"))
        booking = OrientationBookingFactory(equipment_owned=True, member=member)
        orientations.propose_orientation_time(booking, _expected_start(), actor=None)
        booking.refresh_from_db()
        EquipmentReservationFactory(
            equipment=booking.orientation_type.equipment,
            starts_at=_expected_start(),
            ends_at=_expected_start() + timedelta(hours=1),
        )
        client.logout()

        assert "That New Time Can't Be Booked" in client.post(_link(booking, "accept_time")).content.decode()

    def it_lands_a_link_on_a_cancelled_booking_on_already_handled(client: Client):
        booking = _proposed_world(client)
        link = _link(booking, "accept_time")
        OrientationBooking.objects.filter(pk=booking.pk).update(status=OrientationBooking.Status.CANCELLED)

        assert "Already Handled" in client.post(link).content.decode()


def describe_the_cancel_link_after_a_move():
    def it_shows_no_late_fee_warning(client: Client):
        config = SiteConfiguration.load()
        config.late_cancel_fees_enabled = True
        config.save()
        lead, booking = _world(client, status=OrientationBooking.Status.CONFIRMED)
        booking.guild.orientation_settings.late_cancel_fee_cents = 1500
        booking.guild.orientation_settings.save(update_fields=["late_cancel_fee_cents"])
        orientations.move_orientation(booking, timezone.now() + timedelta(hours=2), actor=lead)
        cancel_link = reverse("hub_orientation_action", args=[orientations.make_action_token(booking, "cancel")])
        client.logout()

        assert "data-late-cancel-warning" not in client.get(cancel_link).content.decode()


def describe_the_move_form():
    def it_joins_the_date_and_start_in_the_site_timezone():
        form = OrientationMoveForm({"date": _future_day(), "start_time": "14:00", "mode": "ask"})
        assert form.is_valid()
        assert form.cleaned_data["starts_at"] == _expected_start()
        assert form.asks_member_first

    def it_seeds_the_current_time_when_it_is_on_the_grid():
        start = _expected_start(3)
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(starts_at=start))
        form = OrientationMoveForm(booking=booking)
        assert form.fields["date"].initial == timezone.localdate(start)
        assert form.fields["start_time"].initial == "14:00"

    def it_leaves_the_start_blank_for_an_off_grid_time():
        booking = OrientationBookingFactory(
            slot=OrientationSlotFactory(starts_at=_expected_start(3) + timedelta(minutes=10))
        )
        assert OrientationMoveForm(booking=booking).fields["start_time"].initial is None

    def it_runs_every_error_into_one_sentence():
        form = OrientationMoveForm({"date": "", "start_time": "14:00", "mode": ""})
        assert not form.is_valid()
        assert form.error_summary() == "Pick a date. Pick Move it now or Ask the member first."
