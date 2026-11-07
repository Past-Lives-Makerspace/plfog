"""BDD specs for Send a bill in Record Orientation, the bill's page and its Bookings tab row (#1111, part 1).

Ticked, Record Orientation makes a completed booking that asks for the amount and sends
exactly one email, the bill; unticked it is still today's silent record. The refusals (no
amount, under $1, over $500, a member with no account, a live booking, a type the viewer
does not run) write nothing. The bill's page is the member's own; a GET never reaches
Stripe; Pay mints a Checkout; the return and cancelled landings; the row's chip and the
member's Pay button. Names are factory strings no changelog could contain (STANDARDS.md §8).
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib import messages as django_messages
from django.contrib.auth.models import User
from django.core import mail
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from billing import orientation_bills
from core.models import Notification, SiteActivity
from hub.forms import OrientationRecordForm
from membership.models import Member, OrientationBooking, OrientationError, OrientationRecord, OrientationSlot
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
    MemberFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

PAGE = "/orientations/"
RECORD_URL = "/orientations/manage/records/"
LANDING = "/orientations/?view=bookings&oriented=yes"
_SESSION = {"id": "cs_bill_view_1", "url": "https://checkout.stripe.example/cs_bill_view_1"}


def _login(client: Client, username: str, *, name: str = "") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.full_legal_name = name or f"Viewer {username}"
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["full_legal_name", "status"])
    client.login(username=username, password="pass")
    return member


def _account(username: str, name: str) -> Member:
    """An active member with a login account: someone a bill can reach."""
    MembershipPlanFactory()
    member = User.objects.create_user(username=username, email=f"{username}@example.com").member
    member.full_legal_name = name
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["full_legal_name", "status"])
    return member


def _runner() -> str:
    """Who ran it, as the Oriented by select posts it: a bill requires one."""
    return str(MemberFactory(full_legal_name="Rory Runner", status=Member.Status.ACTIVE).pk)


def _post(type_label: str, member_label: str, **extra: str) -> dict[str, str]:
    return {
        "member": member_label,
        "orientation": type_label,
        "completed_on": timezone.localdate().isoformat(),
        "oriented_by": extra.pop("oriented_by", None) or _runner(),
        "note": "",
        "send_bill": "on",
        "bill_amount": "40",
        "next": LANDING,
        **extra,
    }


def _messages(response: HttpResponse) -> list[str]:
    return [str(m) for m in django_messages.get_messages(response.wsgi_request)]


def _world(client: Client) -> dict[str, Any]:
    """A lead of one guild logged in, a priced type of theirs, a member with an account."""
    lead = _login(client, "bill_lead", name="Lorna Billlead")
    guild = GuildFactory(name="Billing Alpha", guild_lead=lead)
    return {
        "lead": lead,
        "type": OrientationTypeFactory(guild=guild, name="Alpha Lathe", price_cents=4000),
        "other_type": OrientationTypeFactory(guild=GuildFactory(name="Billing Beta"), name="Beta Lathe"),
        "target": _account("bill_target", "Quincy Billee"),
    }


def _bill(member: Member, *, amount_cents: int = 4000, **fields: Any) -> OrientationBooking:
    """A billed booking for ``member`` on a past one-off slot, unpaid unless ``fields`` say otherwise."""
    starts = timezone.now() - timedelta(days=2)
    slot = OrientationSlotFactory(starts_at=starts, ends_at=starts + timedelta(hours=1), seats=1)
    booking = OrientationBooking.create_bill(slot=slot, member=member, oriented_by=None, amount_cents=amount_cents)
    if fields:
        OrientationBooking.objects.filter(pk=booking.pk).update(**fields)
        booking.refresh_from_db()
    return booking


def describe_recording_with_a_bill():
    def it_makes_a_completed_booking_on_a_one_off_past_slot_and_no_record(client: Client):
        world = _world(client)
        response = client.post(
            RECORD_URL, _post(str(world["type"]), "Quincy Billee", oriented_by=str(world["lead"].pk))
        )
        assert response.status_code == 302
        assert response["Location"] == "/orientations/?view=bookings&show=past&oriented=yes"
        assert not OrientationRecord.objects.exists()
        booking = OrientationBooking.objects.get(member=world["target"])
        assert (booking.status, booking.is_completed, booking.oriented_by) == (
            OrientationBooking.Status.CONFIRMED,
            True,
            world["lead"],
        )
        assert (booking.bill_amount_cents, booking.amount_paid_cents, booking.bill_state) == (4000, 0, "unpaid")
        slot = booking.slot
        assert (slot.source, slot.seats, slot.orienter) == (OrientationSlot.Source.CUSTOM, 1, world["lead"])
        assert slot.ends_at <= timezone.now()
        assert timezone.localtime(slot.starts_at).date() == timezone.localdate()
        assert any("sent a $40 bill" in m for m in _messages(response))

    def it_orients_the_member_at_once(client: Client):
        world = _world(client)
        client.post(RECORD_URL, _post(str(world["type"]), "Quincy Billee"))
        assert world["type"].pk in world["target"].completed_orientation_type_ids([world["type"]])

    def it_sends_only_the_bill_email_and_posts_nothing(client: Client):
        world = _world(client)
        mail.outbox.clear()
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            client.post(RECORD_URL, _post(str(world["type"]), "Quincy Billee", bill_amount="12.50"))
        assert [m.to for m in mail.outbox] == [["bill_target@example.com"]]
        assert mail.outbox[0].subject == "Your bill for the Alpha Lathe orientation"
        body = mail.outbox[0].body
        booking = OrientationBooking.objects.get(member=world["target"])
        for expected in ("Alpha Lathe", "Billing Alpha", "$12.50", orientation_bills.bill_url(booking)):
            assert expected in body
        assert not mock_post.called
        # Nobody but the billed member hears anything: no orientor or lead ping.
        assert set(Notification.objects.values_list("user_id", flat=True)) == {world["target"].user_id}

    def it_logs_one_bill_sent_row_naming_the_orientor(client: Client):
        world = _world(client)
        client.post(RECORD_URL, _post(str(world["type"]), "Quincy Billee", note="Paid later, said Sam"))
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.ORIENTATION_BILL_SENT)
        assert row.actor == world["lead"].user
        assert row.payload["amount_cents"] == 4000
        assert row.payload["note"] == "Paid later, said Sam"
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_RECORDED).exists()

    def it_counts_on_the_equipment_page(client: Client):
        member_client = Client()
        target = _login(member_client, "bill_cora", name="Cora Billable")
        staffer = _login(client, "bill_tool")
        tool = EquipmentFactory(name="Billed Lathe")
        EquipmentStaffMembershipFactory(equipment=tool, member=staffer)
        tool_type = OrientationTypeFactory(equipment_owned=True, equipment=tool, name="Billed Basics")
        tool.unlocking_orientations.set([tool_type])
        detail = reverse("hub_equipment_detail", args=[tool.slug])
        assert "You're all set." not in member_client.get(detail).content.decode()
        client.post(RECORD_URL, _post(str(tool_type), "Cora Billable"))
        assert tool_type.pk in target.completed_orientation_type_ids([tool_type])
        assert "You're all set." in member_client.get(detail).content.decode()

    def it_works_for_guild_staff(client: Client):
        staffer = _login(client, "bill_staffer")
        guild = GuildFactory(name="Billing Gamma")
        GuildStaffMembershipFactory(guild=guild, member=staffer)
        staffed = OrientationTypeFactory(guild=guild, name="Gamma Lathe")
        _account("bill_gamma_target", "Gina Billee")
        client.post(RECORD_URL, _post(str(staffed), "Gina Billee"))
        assert OrientationBooking.objects.filter(orientation_type=staffed, billed_at__isnull=False).exists()


def describe_recording_without_a_bill():
    def it_ignores_a_stray_amount_and_who_ran_it_when_unticked(client: Client):
        world = _world(client)
        data = _post(str(world["type"]), "Quincy Billee", bill_amount="abc")
        del data["send_bill"]
        data["oriented_by"] = ""
        response = client.post(RECORD_URL, data)
        assert response["Location"] == LANDING
        assert OrientationRecord.objects.filter(member=world["target"]).exists()
        assert not OrientationBooking.objects.exists()

    def it_stays_a_silent_record(client: Client):
        world = _world(client)
        mail.outbox.clear()
        data = _post(str(world["type"]), "Quincy Billee")
        del data["send_bill"]
        client.post(RECORD_URL, data)
        assert OrientationRecord.objects.filter(member=world["target"]).exists()
        assert not OrientationBooking.objects.exists()
        assert mail.outbox == []


def describe_the_refusals():
    def _refused(client: Client, data: dict[str, str]) -> list[str]:
        mail.outbox.clear()
        response = client.post(RECORD_URL, data)
        assert response.status_code == 302
        assert not OrientationBooking.objects.filter(billed_at__isnull=False).exists()
        assert not OrientationRecord.objects.exists()
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_BILL_SENT).exists()
        assert mail.outbox == []
        return _messages(response)

    def it_refuses_no_amount(client: Client):
        world = _world(client)
        assert "Enter the amount to bill." in _refused(
            client, _post(str(world["type"]), "Quincy Billee", bill_amount="")
        )

    def it_refuses_under_a_dollar(client: Client):
        world = _world(client)
        assert "A bill is at least $1." in _refused(
            client, _post(str(world["type"]), "Quincy Billee", bill_amount="0.50")
        )

    def it_refuses_over_five_hundred(client: Client):
        world = _world(client)
        assert "Enter an amount up to $500." in _refused(
            client, _post(str(world["type"]), "Quincy Billee", bill_amount="500.01")
        )

    def it_refuses_an_amount_that_is_not_a_number(client: Client):
        world = _world(client)
        assert "Enter the amount in dollars, like 40 or 12.50." in _refused(
            client, _post(str(world["type"]), "Quincy Billee", bill_amount="forty")
        )

    def it_refuses_fractions_of_a_cent_and_takes_trailing_zeros(client: Client):
        world = _world(client)
        assert "Enter the amount in dollars, like 40 or 12.50." in _refused(
            client, _post(str(world["type"]), "Quincy Billee", bill_amount="12.505")
        )
        assert not OrientationBooking.objects.exists()
        client.post(reverse("hub_orientation_record"), _post(str(world["type"]), "Quincy Billee", bill_amount="12.500"))
        assert OrientationBooking.objects.get().bill_amount_cents == 1250

    def it_requires_who_ran_it(client: Client):
        world = _world(client)
        data = _post(str(world["type"]), "Quincy Billee")
        data["oriented_by"] = ""
        assert "Pick who ran the orientation so they get their share." in _refused(client, data)

    def it_refuses_an_amount_that_is_not_finite(client: Client):
        world = _world(client)
        assert "Enter the amount to bill." in _refused(
            client, _post(str(world["type"]), "Quincy Billee", bill_amount="NaN")
        )

    def it_refuses_a_member_with_no_account(client: Client):
        world = _world(client)
        MemberFactory(full_legal_name="Nola Noaccount", status=Member.Status.ACTIVE)
        assert "Nola Noaccount has no account yet, so they can't pay online." in _refused(
            client, _post(str(world["type"]), "Nola Noaccount")
        )

    def it_refuses_a_member_with_a_live_booking_for_the_type(client: Client):
        world = _world(client)
        OrientationBookingFactory(
            member=world["target"],
            slot=OrientationSlotFactory(guild=world["type"].guild, orientation_type=world["type"]),
            status=OrientationBooking.Status.REQUESTED,
        )
        mail.outbox.clear()
        response = client.post(RECORD_URL, _post(str(world["type"]), "Quincy Billee"))
        assert "Quincy Billee already has a booking for this orientation." in _messages(response)
        assert not OrientationBooking.objects.filter(billed_at__isnull=False).exists()
        assert mail.outbox == []

    def it_refuses_a_member_already_oriented(client: Client):
        world = _world(client)
        _bill(world["target"])
        OrientationBooking.objects.update(orientation_type=world["type"])
        response = client.post(RECORD_URL, _post(str(world["type"]), "Quincy Billee"))
        assert "Quincy Billee already completed this orientation." in _messages(response)
        assert OrientationBooking.objects.count() == 1

    def it_refuses_a_crafted_post_for_a_type_the_viewer_does_not_run(client: Client):
        world = _world(client)
        assert "Pick an orientation from the list." in _refused(
            client, _post(str(world["other_type"]), "Quincy Billee")
        )

    def it_answers_a_double_click_as_the_live_booking_refusal(client: Client):
        from django.db import IntegrityError

        world = _world(client)
        with patch("billing.orientation_bills.send_bill", side_effect=IntegrityError("uq_orientationbooking")):
            messages = _refused(client, _post(str(world["type"]), "Quincy Billee"))
        assert "Quincy Billee already has a booking for this orientation." in messages

    def it_shows_a_refusal_the_service_raises(client: Client):
        world = _world(client)
        with patch("billing.orientation_bills.send_bill", side_effect=OrientationError("Raised by the service.")):
            assert "Raised by the service." in _refused(client, _post(str(world["type"]), "Quincy Billee"))


def describe_the_window():
    def it_renders_send_a_bill_and_amount_after_note(client: Client):
        world = _world(client)
        content = client.get(PAGE, {"view": "bookings"}).content.decode()
        note = content.index('name="note"')
        assert note < content.index('name="send_bill"') < content.index('name="bill_amount"')
        assert 'id="orientation-record-bill-prices"' in content
        assert "x-text=\"sendBill ? 'Record and Send Bill' : 'Record Orientation'\"" in content
        assert "data-bookings-record-bill-intro" in content
        # Hidden while unticked, and never posted then, so it cannot block a plain record.
        assert ':disabled="!sendBill"' in content
        assert str(world["type"]).lower() in content.lower()

    def it_prefills_each_price_kind(client: Client):
        world = _world(client)
        guild = world["type"].guild
        donation = OrientationTypeFactory(
            guild=guild,
            name="Alpha Donation",
            is_donation=True,
            donation_suggested_cents=2500,
            donation_minimum_cents=500,
        )
        floor_only = OrientationTypeFactory(
            guild=guild, name="Alpha Floor", is_donation=True, donation_minimum_cents=700
        )
        open_hand = OrientationTypeFactory(guild=guild, name="Alpha Open", is_donation=True)
        free = OrientationTypeFactory(guild=guild, name="Alpha Free", price_cents=0)
        cents = OrientationTypeFactory(guild=guild, name="Alpha Cents", price_cents=1250)
        form = client.get(PAGE, {"view": "bookings"}).context["record_form"]
        assert form.bill_prices == {
            str(world["type"]).lower(): "40",
            str(donation).lower(): "25",
            str(floor_only).lower(): "7",
            str(open_hand).lower(): "",
            str(free).lower(): "",
            str(cents).lower(): "12.50",
        }
        script = client.get(PAGE, {"view": "bookings"}).content.decode()
        start = script.index(">", script.index('id="orientation-record-bill-prices"')) + 1
        assert json.loads(script[start : script.index("</script>", start)]) == form.bill_prices

    def it_is_not_offered_on_the_admin_member_flow():
        member = MemberFactory()
        form = OrientationRecordForm(member)
        assert "send_bill" not in form.fields
        assert "bill_amount" not in form.fields


def describe_the_bill_page():
    def it_shows_the_bill_with_its_pay_button_and_never_calls_stripe(client: Client):
        member = _login(client, "bill_page")
        booking = _bill(member)
        with (
            patch("billing.stripe_utils.create_checkout_session") as mock_create,
            patch("billing.stripe_utils.retrieve_checkout_session") as mock_retrieve,
        ):
            response = client.get(reverse("hub_orientation_bill", args=[booking.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'data-orientation-bill-state="unpaid"' in content
        assert 'data-orientation-bill-status="unpaid"' in content
        assert "data-orientation-bill-pay" in content
        assert "Pay $40" in content
        assert orientation_bills.item_label(booking) in content
        assert not mock_create.called
        assert not mock_retrieve.called

    def it_names_who_ran_it(client: Client):
        member = _login(client, "bill_ranby")
        booking = _bill(member, oriented_by=MemberFactory(full_legal_name="Ulla Runner"))
        assert "Ulla Runner" in client.get(reverse("hub_orientation_bill", args=[booking.pk])).content.decode()

    def it_404s_another_members_bill(client: Client):
        _login(client, "bill_stranger")
        booking = _bill(_account("bill_owner", "Owen Owner"))
        assert client.get(reverse("hub_orientation_bill", args=[booking.pk])).status_code == 404

    def it_404s_a_booking_that_is_not_a_bill(client: Client):
        member = _login(client, "bill_plain")
        booking = OrientationBookingFactory(member=member)
        assert client.get(reverse("hub_orientation_bill", args=[booking.pk])).status_code == 404

    def it_shows_a_paid_bill_without_a_pay_button(client: Client):
        member = _login(client, "bill_paid_page")
        booking = _bill(member, paid_at=timezone.now(), amount_paid_cents=4000)
        content = client.get(reverse("hub_orientation_bill", args=[booking.pk])).content.decode()
        assert 'data-orientation-bill-state="paid"' in content
        assert "data-orientation-bill-pay" not in content

    def it_shows_a_cancelled_bill_without_a_pay_button(client: Client):
        member = _login(client, "bill_cancelled_page")
        booking = _bill(member, bill_cancelled_at=timezone.now())
        content = client.get(reverse("hub_orientation_bill", args=[booking.pk])).content.decode()
        assert 'data-orientation-bill-state="cancelled"' in content
        assert 'data-orientation-bill-status="cancelled"' in content
        assert "data-orientation-bill-pay" not in content

    def it_requires_login(client: Client):
        booking = _bill(_account("bill_anon_owner", "Ann Owner"))
        response = client.get(reverse("hub_orientation_bill", args=[booking.pk]))
        assert reverse("account_login") in response["Location"]


def describe_paying():
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_mints_a_fresh_checkout_for_the_unpaid_amount(mock_create, client: Client):
        member = _login(client, "bill_pay")
        booking = _bill(member, amount_cents=4250)
        response = client.post(reverse("hub_orientation_bill_pay", args=[booking.pk]))
        assert response.status_code == 302
        assert response["Location"] == _SESSION["url"]
        kwargs = mock_create.call_args.kwargs
        assert kwargs["amount_cents"] == 4250
        assert kwargs["metadata"] == {"kind": "orientation_bill", "booking_id": str(booking.pk)}
        booking.refresh_from_db()
        assert (booking.stripe_session_id, booking.bill_checkout_attempts) == ("cs_bill_view_1", 1)

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_refuses_a_paid_bill(mock_create, client: Client):
        member = _login(client, "bill_pay_paid")
        booking = _bill(member, paid_at=timezone.now(), amount_paid_cents=4000)
        response = client.post(reverse("hub_orientation_bill_pay", args=[booking.pk]))
        assert response["Location"] == reverse("hub_orientation_bill", args=[booking.pk])
        assert "This bill has nothing left to pay." in _messages(response)
        assert not mock_create.called

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_refuses_a_cancelled_bill(mock_create, client: Client):
        member = _login(client, "bill_pay_cancelled")
        booking = _bill(member, bill_cancelled_at=timezone.now())
        client.post(reverse("hub_orientation_bill_pay", args=[booking.pk]))
        assert not mock_create.called

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_404s_another_members_bill(mock_create, client: Client):
        _login(client, "bill_pay_stranger")
        booking = _bill(_account("bill_pay_owner", "Pia Owner"))
        assert client.post(reverse("hub_orientation_bill_pay", args=[booking.pk])).status_code == 404
        assert not mock_create.called

    @patch("billing.stripe_utils.create_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_returns_to_the_bill_with_an_error_when_stripe_fails(mock_create, client: Client):
        member = _login(client, "bill_pay_down")
        booking = _bill(member)
        response = client.post(reverse("hub_orientation_bill_pay", args=[booking.pk]))
        assert response["Location"] == reverse("hub_orientation_bill", args=[booking.pk])
        assert any("couldn't open the payment page" in m for m in _messages(response))

    def it_rejects_get(client: Client):
        member = _login(client, "bill_pay_get")
        booking = _bill(member)
        assert client.get(reverse("hub_orientation_bill_pay", args=[booking.pk])).status_code == 405


def describe_the_return_landing():
    def _url(booking: OrientationBooking) -> str:
        return reverse("hub_orientation_bill_return", args=[orientation_bills.make_checkout_token(booking)])

    def it_marks_paid_when_stripe_says_paid(client: Client):
        member = _login(client, "bill_ret_paid")
        booking = _bill(member, stripe_session_id="cs_ret_1")
        session = {"id": "cs_ret_1", "payment_status": "paid", "payment_intent": "pi_ret_1", "amount_total": 4000}
        with patch("billing.stripe_utils.retrieve_checkout_session", return_value=session):
            content = client.get(_url(booking)).content.decode()
        assert 'data-orientation-bill-state="paid"' in content
        booking.refresh_from_db()
        assert (booking.stripe_payment_id, booking.amount_paid_cents) == ("pi_ret_1", 4000)
        assert booking.paid_at is not None

    def it_says_pending_while_stripe_has_not_confirmed(client: Client):
        member = _login(client, "bill_ret_pending")
        booking = _bill(member, stripe_session_id="cs_ret_2")
        session = {"id": "cs_ret_2", "payment_status": "unpaid", "payment_intent": "", "amount_total": None}
        with patch("billing.stripe_utils.retrieve_checkout_session", return_value=session):
            content = client.get(_url(booking)).content.decode()
        assert 'data-orientation-bill-state="pending"' in content
        assert "data-orientation-bill-pay" in content

    def it_shows_a_bill_already_paid_without_asking_stripe(client: Client):
        member = _login(client, "bill_ret_already")
        booking = _bill(member, paid_at=timezone.now(), amount_paid_cents=4000)
        with patch("billing.stripe_utils.retrieve_checkout_session") as mock_retrieve:
            content = client.get(_url(booking)).content.decode()
        assert 'data-orientation-bill-state="paid"' in content
        assert not mock_retrieve.called

    def it_renders_400_for_a_bad_token(client: Client):
        _login(client, "bill_ret_bad")
        response = client.get(reverse("hub_orientation_bill_return", args=["not-a-token"]))
        assert response.status_code == 400
        assert 'data-orientation-bill-state="invalid"' in response.content.decode()

    def it_404s_another_members_bill(client: Client):
        _login(client, "bill_ret_stranger")
        booking = _bill(_account("bill_ret_owner", "Rita Owner"))
        assert client.get(_url(booking)).status_code == 404


def describe_the_cancelled_landing():
    def _url(booking: OrientationBooking) -> str:
        return reverse("hub_orientation_bill_checkout_cancelled", args=[orientation_bills.make_checkout_token(booking)])

    def it_sends_the_member_back_to_the_bill_saying_it_is_still_due(client: Client):
        member = _login(client, "bill_cx")
        booking = _bill(member)
        response = client.get(_url(booking))
        assert response["Location"] == reverse("hub_orientation_bill", args=[booking.pk])
        assert "Your bill is still due. Pay it from the Pay button any time." in _messages(response)

    def it_says_nothing_for_a_bill_no_longer_owed(client: Client):
        member = _login(client, "bill_cx_paid")
        booking = _bill(member, paid_at=timezone.now(), amount_paid_cents=4000)
        response = client.get(_url(booking))
        assert response["Location"] == reverse("hub_orientation_bill", args=[booking.pk])
        assert _messages(response) == []

    def it_goes_home_on_a_bad_token(client: Client):
        _login(client, "bill_cx_bad")
        response = client.get(reverse("hub_orientation_bill_checkout_cancelled", args=["nope"]))
        assert response["Location"] == reverse("hub_home")

    def it_404s_another_members_bill(client: Client):
        _login(client, "bill_cx_stranger")
        booking = _bill(_account("bill_cx_owner", "Cyd Owner"))
        assert client.get(_url(booking)).status_code == 404


def describe_the_bookings_tab_row():
    def _staff_row(client: Client, **fields: Any) -> str:
        world = _world(client)
        booking = _bill(world["target"], **fields)
        OrientationBooking.objects.filter(pk=booking.pk).update(
            orientation_type=world["type"], guild=world["type"].guild
        )
        OrientationSlot.objects.filter(pk=booking.slot_id).update(
            orientation_type=world["type"], guild=world["type"].guild
        )
        return client.get(PAGE, {"view": "bookings", "show": "past"}).content.decode()

    def it_shows_staff_an_unpaid_chip(client: Client):
        content = _staff_row(client)
        assert '<span class="hub-pill hub-pill--warn" data-booking-bill="unpaid">Unpaid $40</span>' in content
        assert "data-booking-bill-pay" not in content

    def it_shows_staff_a_paid_chip(client: Client):
        content = _staff_row(client, paid_at=timezone.now(), amount_paid_cents=4000, stripe_payment_id="pi_row")
        assert 'data-booking-bill="paid">Paid $40</span>' in content

    def it_prints_what_was_paid_not_what_was_billed(client: Client):
        content = _staff_row(client, paid_at=timezone.now(), amount_paid_cents=3800, stripe_payment_id="pi_row")
        assert 'data-booking-bill="paid">Paid $38</span>' in content

    def it_says_refunded_after_a_full_refund(client: Client):
        from billing.models import PaymentRefund

        world_content = _staff_row(client, paid_at=timezone.now(), amount_paid_cents=4000, stripe_payment_id="pi_rf")
        assert 'data-booking-bill="paid"' in world_content
        booking = OrientationBooking.objects.get(billed_at__isnull=False)
        PaymentRefund.objects.create(
            orientation_booking=booking, amount_cents=4000, status=PaymentRefund.Status.SUCCEEDED
        )
        content = client.get(PAGE, {"view": "bookings", "show": "past"}).content.decode()
        assert 'data-booking-bill="refunded">Refunded $40</span>' in content

    def it_says_partially_refunded_after_a_partial_refund(client: Client):
        from billing.models import PaymentRefund

        _staff_row(client, paid_at=timezone.now(), amount_paid_cents=4000, stripe_payment_id="pi_rp")
        booking = OrientationBooking.objects.get(billed_at__isnull=False)
        PaymentRefund.objects.create(
            orientation_booking=booking, amount_cents=1000, status=PaymentRefund.Status.SUCCEEDED
        )
        content = client.get(PAGE, {"view": "bookings", "show": "past"}).content.decode()
        assert 'data-booking-bill="partial">Partially refunded $40</span>' in content

    def it_offers_no_move_on_a_billed_row(client: Client):
        content = _staff_row(client)
        assert "data-booking-move=" not in content

    def it_offers_no_move_on_a_billed_row_after_undo_oriented(client: Client):
        content = _staff_row(client, is_completed=False)
        assert "data-booking-move=" not in content

    def it_shows_staff_a_cancelled_chip(client: Client):
        content = _staff_row(client, bill_cancelled_at=timezone.now())
        assert 'data-booking-bill="cancelled">Bill cancelled</span>' in content

    def it_lists_the_bill_under_oriented_yes(client: Client):
        world = _world(client)
        booking = _bill(world["target"])
        OrientationBooking.objects.filter(pk=booking.pk).update(
            orientation_type=world["type"], guild=world["type"].guild
        )
        content = client.get(PAGE, {"view": "bookings", "show": "all", "oriented": "yes"}).content.decode()
        assert f'data-booking-row="{booking.pk}"' in content

    def it_gives_the_member_a_pay_button_and_view_bill_while_unpaid(client: Client):
        member = _login(client, "bill_row_member")
        booking = _bill(member)
        content = client.get(PAGE, {"view": "bookings", "show": "past"}).content.decode()
        assert f'data-booking-bill-pay="{booking.pk}"' in content
        assert f'action="{reverse("hub_orientation_bill_pay", args=[booking.pk])}"' in content
        assert f'data-booking-bill-view="{booking.pk}"' in content
        assert "data-booking-bill=" not in content

    def it_drops_the_member_pay_button_once_paid(client: Client):
        member = _login(client, "bill_row_paid")
        booking = _bill(member, paid_at=timezone.now(), amount_paid_cents=4000, stripe_payment_id="pi_mine")
        content = client.get(PAGE, {"view": "bookings", "show": "past"}).content.decode()
        assert "data-booking-bill-pay" not in content
        assert f'data-booking-bill-view="{booking.pk}"' in content

    def it_tells_the_member_a_bill_was_cancelled(client: Client):
        member = _login(client, "bill_row_cancelled")
        _bill(member, bill_cancelled_at=timezone.now())
        content = client.get(PAGE, {"view": "bookings", "show": "past"}).content.decode()
        assert 'data-booking-bill="cancelled">Bill cancelled</span>' in content
        assert "data-booking-bill-pay" not in content


def describe_undo_then_mark_oriented():
    def it_stays_undone_through_the_nightly_job_and_marks_again_silently(client: Client):
        from membership import orientations

        world = _world(client)
        booking = _bill(world["target"])
        OrientationBooking.objects.filter(pk=booking.pk).update(
            orientation_type=world["type"], guild=world["type"].guild
        )
        toggle = reverse("hub_orientation_toggle_completed", args=[booking.pk])
        mail.outbox.clear()
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            client.post(toggle, {"completed": "0"})
            booking.refresh_from_db()
            assert booking.is_completed is False
            assert orientations.auto_complete() == 0
            booking.refresh_from_db()
            assert booking.is_completed is False
            client.post(toggle, {"completed": "1"})
        booking.refresh_from_db()
        assert booking.is_completed is True
        assert mail.outbox == []
        assert not mock_post.called


def describe_refunding_a_paid_bill():
    def it_refunds_through_the_existing_orientation_refund(client: Client):
        from billing.models import PaymentRefund
        from membership.models import AdminCapability

        world = _world(client)
        world["lead"].admin_capabilities.create(capability=AdminCapability.Capability.REFUNDS)
        booking = _bill(
            world["target"], paid_at=timezone.now(), amount_paid_cents=4000, stripe_payment_id="pi_refund_bill"
        )
        OrientationBooking.objects.filter(pk=booking.pk).update(
            orientation_type=world["type"], guild=world["type"].guild
        )
        with patch(
            "billing.stripe_utils.create_refund",
            return_value={"id": "re_bill_1", "status": "succeeded", "amount": 4000},
        ) as mock_refund:
            response = client.post(
                reverse("billing_orientation_refund", args=[booking.pk]),
                {"amount": "40.00", "reason": "Changed their mind"},
            )
        assert response.status_code == 204
        assert mock_refund.called
        booking.refresh_from_db()
        assert booking.refund_state == "full"
        refund = PaymentRefund.objects.get(orientation_booking=booking)
        assert (refund.amount_cents, refund.status) == (4000, PaymentRefund.Status.SUCCEEDED)
        assert world["type"].pk in world["target"].completed_orientation_type_ids([world["type"]])


def describe_resend_and_cancel():
    """Part 2: Resend Bill and Cancel Bill on a staff row, gated like recording."""

    def _staff_bill(world: dict[str, Any], **fields: Any) -> OrientationBooking:
        booking = _bill(world["target"], **fields)
        OrientationBooking.objects.filter(pk=booking.pk).update(
            orientation_type=world["type"], guild=world["type"].guild
        )
        booking.refresh_from_db()
        return booking

    def _resend(client: Client, booking: OrientationBooking) -> HttpResponse:
        return client.post(reverse("hub_orientation_bill_resend", args=[booking.pk]), {"next": LANDING})

    def _cancel(client: Client, booking: OrientationBooking) -> HttpResponse:
        return client.post(reverse("hub_orientation_bill_cancel", args=[booking.pk]), {"next": LANDING})

    def describe_the_menu():
        def it_offers_both_on_an_unpaid_staff_row_with_their_confirms(client: Client):
            world = _world(client)
            booking = _staff_bill(world)
            content = client.get(PAGE, {"view": "bookings", "show": "past"}).content.decode()
            assert f'data-booking-bill-resend="{booking.pk}"' in content
            assert f'data-booking-bill-cancel="{booking.pk}"' in content
            assert f"booking-bill-resend-{booking.pk}" in content
            assert f'action="{reverse("hub_orientation_bill_cancel", args=[booking.pk])}"' in content
            assert "Cancel This Bill?" in content

        @pytest.mark.parametrize(
            "fields",
            [{"paid_at": "now", "amount_paid_cents": 4000, "stripe_payment_id": "pi_m"}, {"bill_cancelled_at": "now"}],
        )
        def it_offers_neither_once_paid_or_cancelled(client: Client, fields: dict[str, Any]):
            world = _world(client)
            values = {key: timezone.now() if value == "now" else value for key, value in fields.items()}
            _staff_bill(world, **values)
            content = client.get(PAGE, {"view": "bookings", "show": "past"}).content.decode()
            assert "data-booking-bill-resend" not in content
            assert "data-booking-bill-cancel" not in content

        def it_never_offers_them_on_the_members_own_row(client: Client):
            member = _login(client, "bill_menu_member")
            _bill(member)
            content = client.get(PAGE, {"view": "bookings", "show": "past"}).content.decode()
            assert "data-booking-bill-resend" not in content
            assert "data-booking-bill-cancel" not in content

    def describe_resend():
        def it_sends_the_same_bill_email_again(client: Client):
            world = _world(client)
            booking = _staff_bill(world)
            orientation_bills._send_bill_email(booking)
            mail.outbox.clear()
            response = _resend(client, booking)
            assert response["Location"] == LANDING
            assert [m.subject for m in mail.outbox] == ["Your bill for the Alpha Lathe orientation"]
            assert orientation_bills.bill_url(booking) in mail.outbox[0].body
            assert "Sent the $40 bill to Quincy Billee again." in _messages(response)
            _resend(client, booking)
            assert len(mail.outbox) == 2

        def it_logs_no_activity_row(client: Client):
            world = _world(client)
            booking = _staff_bill(world)
            before = SiteActivity.objects.count()
            _resend(client, booking)
            assert SiteActivity.objects.count() == before

    def describe_cancel():
        def it_cancels_the_bill_and_keeps_the_member_oriented(client: Client):
            world = _world(client)
            booking = _staff_bill(world)
            mail.outbox.clear()
            response = _cancel(client, booking)
            assert response["Location"] == LANDING
            assert "Cancelled the $40 bill for Quincy Billee." in _messages(response)
            booking.refresh_from_db()
            assert booking.bill_state == "cancelled"
            assert booking.bill_cancelled_by == world["lead"].user
            assert (booking.is_completed, booking.status) == (True, OrientationBooking.Status.CONFIRMED)
            assert world["type"].pk in world["target"].completed_orientation_type_ids([world["type"]])
            assert mail.outbox == []

        def it_logs_one_cancelled_row_naming_the_actor(client: Client):
            world = _world(client)
            booking = _staff_bill(world)
            _cancel(client, booking)
            row = SiteActivity.objects.get(kind=SiteActivity.Kind.ORIENTATION_BILL_CANCELLED)
            assert (row.actor, row.target, row.payload["amount_cents"]) == (world["lead"].user, booking, 4000)

        def it_expires_the_open_checkout(client: Client):
            world = _world(client)
            booking = _staff_bill(world, stripe_session_id="cs_open_tab")
            with patch("billing.stripe_utils.expire_checkout_session") as mock_expire:
                _cancel(client, booking)
            mock_expire.assert_called_once_with(session_id="cs_open_tab")

        def it_leaves_the_member_a_cancelled_page_and_refuses_their_pay(client: Client):
            world = _world(client)
            booking = _staff_bill(world)
            _cancel(client, booking)
            member_client = Client()
            member_client.force_login(world["target"].user)
            content = member_client.get(reverse("hub_orientation_bill", args=[booking.pk])).content.decode()
            assert 'data-orientation-bill-state="cancelled"' in content
            assert "data-orientation-bill-pay" not in content
            with patch("billing.stripe_utils.create_checkout_session") as mock_create:
                response = member_client.post(reverse("hub_orientation_bill_pay", args=[booking.pk]))
            assert response["Location"] == reverse("hub_orientation_bill", args=[booking.pk])
            assert not mock_create.called
            row = member_client.get(PAGE, {"view": "bookings", "show": "past"}).content.decode()
            assert 'data-booking-bill="cancelled">Bill cancelled</span>' in row

    def describe_who_may():
        def it_lets_an_admin_act_on_any_guilds_bill(client: Client):
            target = _account("bill_admin_target", "Ada Billee")
            booking = _bill(target)
            admin = _login(client, "bill_admin")
            admin.fog_role = Member.FogRole.ADMIN
            admin.save(update_fields=["fog_role"])
            admin.sync_user_permissions()
            _cancel(client, booking)
            booking.refresh_from_db()
            assert booking.bill_state == "cancelled"

        def it_lets_an_equipment_manager_act_on_their_tools_bill(client: Client):
            staffer = _login(client, "bill_tool_mgr")
            tool = EquipmentFactory(name="Managed Bill Lathe")
            EquipmentStaffMembershipFactory(equipment=tool, member=staffer)
            tool_type = OrientationTypeFactory(equipment_owned=True, equipment=tool, name="Managed Bill Basics")
            booking = _bill(_account("bill_tool_target", "Tia Billee"))
            OrientationBooking.objects.filter(pk=booking.pk).update(orientation_type=tool_type, guild=None)
            mail.outbox.clear()
            _resend(client, booking)
            assert len(mail.outbox) == 1

        @pytest.mark.parametrize("action", ["resend", "cancel"])
        def it_refuses_another_guilds_orientor_and_writes_nothing(client: Client, action: str):
            world = _world(client)
            outsider_client = Client()
            outsider = _login(outsider_client, f"bill_outsider_{action}")
            GuildStaffMembershipFactory(guild=world["other_type"].guild, member=outsider)
            booking = _staff_bill(world)
            mail.outbox.clear()
            response = (_resend if action == "resend" else _cancel)(outsider_client, booking)
            assert response.status_code == 403
            booking.refresh_from_db()
            assert booking.bill_state == "unpaid"
            assert mail.outbox == []
            assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_BILL_CANCELLED).exists()

        @pytest.mark.parametrize("action", ["resend", "cancel"])
        def it_refuses_a_plain_member_even_the_billed_one(client: Client, action: str):
            world = _world(client)
            booking = _staff_bill(world)
            member_client = Client()
            member_client.force_login(world["target"].user)
            mail.outbox.clear()
            response = (_resend if action == "resend" else _cancel)(member_client, booking)
            assert response.status_code == 403
            booking.refresh_from_db()
            assert booking.bill_state == "unpaid"
            assert mail.outbox == []

        @pytest.mark.parametrize("action", ["resend", "cancel"])
        @pytest.mark.parametrize("state", ["paid", "cancelled"])
        def it_refuses_a_bill_no_longer_owed_and_writes_nothing(client: Client, action: str, state: str):
            world = _world(client)
            fields: dict[str, Any] = (
                {"paid_at": timezone.now(), "amount_paid_cents": 4000, "stripe_payment_id": "pi_x"}
                if state == "paid"
                else {"bill_cancelled_at": timezone.now()}
            )
            booking = _staff_bill(world, **fields)
            before = (booking.paid_at, booking.bill_cancelled_at, booking.bill_cancelled_by_id)
            mail.outbox.clear()
            response = (_resend if action == "resend" else _cancel)(client, booking)
            assert response["Location"] == LANDING
            assert "This bill has nothing left to pay." in _messages(response)
            booking.refresh_from_db()
            assert (booking.paid_at, booking.bill_cancelled_at, booking.bill_cancelled_by_id) == before
            assert mail.outbox == []
            assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_BILL_CANCELLED).exists()

        def it_404s_a_booking_that_is_not_a_bill(client: Client):
            world = _world(client)
            plain = OrientationBookingFactory(
                member=world["target"],
                slot=OrientationSlotFactory(guild=world["type"].guild, orientation_type=world["type"]),
            )
            assert _cancel(client, plain).status_code == 404
            assert _resend(client, plain).status_code == 404

        @pytest.mark.parametrize("name", ["hub_orientation_bill_resend", "hub_orientation_bill_cancel"])
        def it_rejects_get(client: Client, name: str):
            world = _world(client)
            booking = _staff_bill(world)
            assert client.get(reverse(name, args=[booking.pk])).status_code == 405

        def it_falls_back_to_the_past_list_without_a_next(client: Client):
            world = _world(client)
            booking = _staff_bill(world)
            response = client.post(reverse("hub_orientation_bill_cancel", args=[booking.pk]))
            assert response["Location"] == "/orientations/?view=bookings&show=past"
