"""BDD specs for #733: equipment lets members propose their own orientation time, like guilds.

The equipment Orientation tab carries the guilds' "Let members propose their own orientation
time" switch (``Equipment.allow_custom_requests``, on by default). While it is on and an
equipment orientation has no open time, the Orientations page card and the equipment page
offer the same propose a time form, which posts to the equipment road and books the same one
seat request, routed to the equipment's managers to confirm. Assertions anchor on markup,
URLs and factory names, never on copy a changelog entry could carry (STANDARDS.md).
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Notification
from hub.forms import CUSTOM_REQUESTS_LABEL, EquipmentOrientationRequestsForm
from membership import orientations
from membership.models import (
    Equipment,
    Member,
    OrientationBooking,
    OrientationError,
    OrientationSlot,
    OrientationType,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

ORIENTATIONS_PAGE = "/orientations/"
_SESSION = {"id": "cs_equip_custom", "url": "https://checkout.stripe.example/cs_equip_custom"}


def _member_user(username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.status = Member.Status.ACTIVE
    if not member.full_legal_name:
        member.full_legal_name = username.title()
    member.save()
    return user


def _login(client: Client, username: str) -> User:
    user = _member_user(username)
    client.login(username=username, password="pass")
    return user


def _owned_type(equipment: Equipment, **fields: object) -> OrientationType:
    fields.setdefault("name", "CNC Operator Basics")
    return OrientationTypeFactory(equipment_owned=True, equipment=equipment, **fields)


def _when(days: int = 3) -> str:
    return timezone.localtime(timezone.now() + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")


def _road(equipment: Equipment) -> str:
    return reverse("hub_equipment_orientation_request_custom", args=[equipment.slug])


def _detail(equipment: Equipment) -> str:
    return reverse("hub_equipment_detail", args=[equipment.slug])


def _section_html(content: bytes, pk: int) -> str:
    """One orientation's markup on a page: from its id to the next one (or the end)."""
    text = content.decode()
    start = text.index(f'id="orientation-type-{pk}"')
    end = text.find('id="orientation-type-', start + 1)
    return text[start : end if end != -1 else len(text)]


def _messages(response: object) -> list[str]:
    return [str(m) for m in response.context["messages"]]  # type: ignore[attr-defined]


def describe_the_switch():
    def it_is_on_for_new_equipment():
        assert EquipmentFactory().allow_custom_requests is True

    def it_shows_on_the_orientation_tab_with_the_guilds_label(client: Client):
        equipment = EquipmentFactory(name="Switch CNC")
        user = _login(client, "eq733_switch_view")
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        response = client.get(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation")
        content = response.content.decode()
        assert response.status_code == 200
        assert "data-orientation-requests" in content
        assert 'name="orientreq-allow_custom_requests"' in content
        assert f'name="{EquipmentOrientationRequestsForm.SHOWN_FIELD}"' in content
        assert CUSTOM_REQUESTS_LABEL in content

    def it_uses_the_same_label_as_the_guild_switch():
        from hub.forms import GuildOrientationSettingsForm

        assert GuildOrientationSettingsForm().fields["allow_custom_requests"].label == CUSTOM_REQUESTS_LABEL
        assert EquipmentOrientationRequestsForm().fields["allow_custom_requests"].label == CUSTOM_REQUESTS_LABEL

    def _save(client: Client, equipment: Equipment, extra: dict[str, str]) -> object:
        data = {
            "otypes-TOTAL_FORMS": "0",
            "otypes-INITIAL_FORMS": "0",
            "otypes-MIN_NUM_FORMS": "0",
            "otypes-MAX_NUM_FORMS": "1000",
            **extra,
        }
        return client.post(reverse("hub_equipment_orientation_types_save", args=[equipment.slug]), data)

    def it_saves_off_with_the_orientations_save(client: Client):
        equipment = EquipmentFactory()
        user = _login(client, "eq733_switch_off")
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        response = _save(client, equipment, {EquipmentOrientationRequestsForm.SHOWN_FIELD: "1"})
        assert response.status_code == 302
        assert response["Location"].endswith("?tab=orientation")
        equipment.refresh_from_db()
        assert equipment.allow_custom_requests is False

    def it_saves_on_again(client: Client):
        equipment = EquipmentFactory(allow_custom_requests=False)
        user = _login(client, "eq733_switch_on")
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        _save(
            client,
            equipment,
            {EquipmentOrientationRequestsForm.SHOWN_FIELD: "1", "orientreq-allow_custom_requests": "on"},
        )
        equipment.refresh_from_db()
        assert equipment.allow_custom_requests is True

    def it_leaves_the_switch_alone_when_the_card_did_not_draw_it(client: Client):
        equipment = EquipmentFactory()
        user = _login(client, "eq733_switch_stale")
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        assert _save(client, equipment, {}).status_code == 302
        equipment.refresh_from_db()
        assert equipment.allow_custom_requests is True

    def it_keeps_the_posted_switch_on_a_refused_save(client: Client):
        equipment = EquipmentFactory()
        user = _login(client, "eq733_switch_bad")
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        response = _save(
            client,
            equipment,
            {
                EquipmentOrientationRequestsForm.SHOWN_FIELD: "1",
                "otypes-TOTAL_FORMS": "1",
                "otypes-0-name": "",
                "otypes-0-duration_minutes": "not a number",
            },
        )
        assert response.status_code == 200
        assert response.context["orientation_requests_form"].is_bound
        equipment.refresh_from_db()
        assert equipment.allow_custom_requests is True

    def it_refuses_a_member_who_does_not_manage_it(client: Client):
        equipment = EquipmentFactory()
        _login(client, "eq733_switch_stranger")
        response = _save(client, equipment, {EquipmentOrientationRequestsForm.SHOWN_FIELD: "1"})
        assert response.status_code == 403
        equipment.refresh_from_db()
        assert equipment.allow_custom_requests is True


def describe_allows_custom_requests():
    def describe_an_equipment_type():
        def it_is_true_by_default():
            assert _owned_type(EquipmentFactory()).allows_custom_requests is True

        def it_is_false_with_the_switch_off():
            assert _owned_type(EquipmentFactory(allow_custom_requests=False)).allows_custom_requests is False

        def it_is_false_while_the_equipment_is_closed():
            assert _owned_type(EquipmentFactory(is_closed=True)).allows_custom_requests is False

        def it_is_false_for_retired_equipment():
            assert _owned_type(EquipmentFactory(is_active=False)).allows_custom_requests is False

        def it_is_false_for_a_retired_type():
            assert _owned_type(EquipmentFactory(), is_active=False).allows_custom_requests is False

    def describe_a_guild_type():
        def it_is_true_while_the_guild_takes_them():
            settings_obj = GuildOrientationSettingsFactory(is_enabled=True, allow_custom_requests=True)
            assert OrientationTypeFactory(guild=settings_obj.guild).allows_custom_requests is True

        def it_is_false_with_the_guild_switch_off():
            settings_obj = GuildOrientationSettingsFactory(is_enabled=True, allow_custom_requests=False)
            assert OrientationTypeFactory(guild=settings_obj.guild).allows_custom_requests is False

        def it_is_false_while_the_guild_is_closed():
            settings_obj = GuildOrientationSettingsFactory(is_enabled=True, is_closed=True)
            assert OrientationTypeFactory(guild=settings_obj.guild).allows_custom_requests is False

        def it_is_false_for_a_guild_with_no_settings():
            assert OrientationTypeFactory(guild=GuildFactory()).allows_custom_requests is False


def describe_the_equipment_page():
    def it_offers_the_form_when_nothing_is_posted(client: Client):
        _login(client, "eq733_page_bare")
        equipment = EquipmentFactory(name="Bare CNC")
        orientation_type = _owned_type(equipment)
        section = _section_html(client.get(_detail(equipment)).content, orientation_type.pk)
        assert f'action="{_road(equipment)}"' in section
        assert f'<input type="hidden" name="orientation_type" value="{orientation_type.pk}">' in section
        assert f'id="id_custom_{orientation_type.pk}_starts_at"' in section
        assert "data-custom-request" in section

    def it_offers_the_form_when_every_posted_time_is_full(client: Client):
        _login(client, "eq733_page_full")
        equipment = EquipmentFactory(name="Full CNC")
        orientation_type = _owned_type(equipment)
        slot = OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type, seats=1)
        OrientationBookingFactory(slot=slot, member=_member_user("eq733_page_full_other").member)
        section = _section_html(client.get(_detail(equipment)).content, orientation_type.pk)
        assert f'action="{_road(equipment)}"' in section

    def it_leaves_it_off_while_a_time_is_open(client: Client):
        _login(client, "eq733_page_open")
        equipment = EquipmentFactory(name="Open CNC")
        orientation_type = _owned_type(equipment)
        OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)
        section = _section_html(client.get(_detail(equipment)).content, orientation_type.pk)
        assert _road(equipment) not in section

    def it_leaves_it_off_with_the_switch_off(client: Client):
        _login(client, "eq733_page_off")
        equipment = EquipmentFactory(name="Off CNC", allow_custom_requests=False)
        orientation_type = _owned_type(equipment)
        section = _section_html(client.get(_detail(equipment)).content, orientation_type.pk)
        assert _road(equipment) not in section
        assert "data-custom-request" not in section

    def it_leaves_it_off_while_the_member_has_a_request_in(client: Client):
        user = _login(client, "eq733_page_booked")
        equipment = EquipmentFactory(name="Booked CNC")
        orientation_type = _owned_type(equipment)
        slot = OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type, seats=1)
        OrientationBookingFactory(slot=slot, member=user.member)
        section = _section_html(client.get(_detail(equipment)).content, orientation_type.pk)
        assert _road(equipment) not in section

    def it_asks_a_donation_type_for_its_amount(client: Client):
        _login(client, "eq733_page_donation")
        equipment = EquipmentFactory(name="Donation CNC")
        orientation_type = _owned_type(equipment, is_donation=True, donation_suggested_cents=1500)
        section = _section_html(client.get(_detail(equipment)).content, orientation_type.pk)
        assert f'id="id_custom_{orientation_type.pk}_amount"' in section

    def it_costs_no_more_queries_with_the_form_than_without(client: Client):
        _login(client, "eq733_page_queries")
        equipment = EquipmentFactory(name="Counted CNC", allow_custom_requests=False)
        for index in range(3):
            _owned_type(equipment, name=f"Counted Type {index}", is_donation=index == 1)
        client.get(_detail(equipment))  # warm the per process caches
        with CaptureQueriesContext(connection) as off:
            client.get(_detail(equipment))
        Equipment.objects.filter(pk=equipment.pk).update(allow_custom_requests=True)
        with CaptureQueriesContext(connection) as on:
            response = client.get(_detail(equipment))
        assert response.content.count(f'action="{_road(equipment)}"'.encode()) == 3
        assert len(on.captured_queries) == len(off.captured_queries)


def describe_the_orientations_page_card():
    def it_posts_an_equipment_card_to_the_equipment_road(client: Client):
        _login(client, "eq733_card")
        equipment = EquipmentFactory(name="Card CNC")
        orientation_type = _owned_type(equipment)
        card = _section_html(client.get(ORIENTATIONS_PAGE).content, orientation_type.pk)
        assert f'action="{_road(equipment)}"' in card
        assert "hub_guild_orientation_request_custom" not in card

    def it_keeps_a_guild_card_on_the_guild_road(client: Client):
        _login(client, "eq733_card_guild")
        settings_obj = GuildOrientationSettingsFactory(is_enabled=True, allow_custom_requests=True)
        orientation_type = OrientationTypeFactory(guild=settings_obj.guild, name="Guild Card Type")
        card = _section_html(client.get(ORIENTATIONS_PAGE).content, orientation_type.pk)
        assert reverse("hub_guild_orientation_request_custom", args=[settings_obj.guild.pk]) in card

    def it_costs_the_same_with_one_card_offering_it_as_with_six(client: Client, django_assert_num_queries):
        _login(client, "eq733_card_queries")

        def seed(indexes: range) -> None:
            for index in indexes:
                if index % 2:
                    _owned_type(EquipmentFactory(name=f"Counted Tool {index}"), name=f"Tool Type {index}")
                else:
                    settings_obj = GuildOrientationSettingsFactory(
                        guild=GuildFactory(name=f"Counted Guild {index}"), is_enabled=True, allow_custom_requests=True
                    )
                    OrientationTypeFactory(guild=settings_obj.guild, name=f"Guild Type {index}", is_donation=True)

        seed(range(2))
        client.get(ORIENTATIONS_PAGE)  # warm the per process caches
        with CaptureQueriesContext(connection) as few:
            assert client.get(ORIENTATIONS_PAGE).content.count(b"showCustom = true") == 2
        seed(range(2, 6))
        with django_assert_num_queries(len(few.captured_queries)):
            response = client.get(ORIENTATIONS_PAGE)
        assert response.content.count(b"showCustom = true") == 6


def describe_the_equipment_road():
    def it_books_a_one_seat_request_and_sends_it_to_the_managers(client: Client):
        manager = _member_user("eq733_road_manager").member
        bystander = _member_user("eq733_road_bystander").member
        GuildFactory(guild_lead=bystander)
        equipment = EquipmentFactory(name="Road CNC")
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        orientation_type = _owned_type(equipment, duration_minutes=90, default_location="Back bay")
        user = _login(client, "eq733_road_member")
        mail.outbox.clear()

        response = client.post(
            _road(equipment), {"orientation_type": orientation_type.pk, "starts_at": _when(), "note": "evenings"}
        )

        assert response.status_code == 302
        assert response["Location"] == f"{_detail(equipment)}#equipment-orientation"
        booking = OrientationBooking.objects.get(member=user.member)
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert booking.orientation_type == orientation_type
        assert booking.guild is None
        assert booking.member_note == "evenings"
        slot = booking.slot
        assert slot.source == OrientationSlot.Source.MANUAL
        assert slot.seats == 1
        assert slot.guild is None
        assert slot.orienter is None
        assert slot.location == "Back bay"
        assert slot.ends_at - slot.starts_at == timedelta(minutes=90)
        addressed = {addr for m in mail.outbox if "New orientation request" in m.subject for addr in m.to}
        assert addressed == {manager.primary_email}
        assert Notification.objects.filter(user=manager.user, trigger="orientation_requested").exists()
        assert not Notification.objects.filter(user=bystander.user, trigger="orientation_requested").exists()

    def it_lists_the_request_for_a_manager_who_can_confirm_it(client: Client):
        equipment = EquipmentFactory(name="Confirm CNC")
        orientation_type = _owned_type(equipment)
        member = _member_user("eq733_confirm_member").member
        booking = orientations.request_custom_orientation(
            equipment, member, timezone.now() + timedelta(days=4), orientation_type=orientation_type
        )
        manager = _login(client, "eq733_confirm_manager")
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager.member)

        manage = client.get(f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation")
        assert booking in manage.context["orientation_pending_requests"]
        response = client.post(reverse("hub_orientation_respond", args=[booking.pk]), {"action": "confirm"})

        assert response.status_code == 302
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CONFIRMED
        assert booking.oriented_by == manager.member

    def it_lands_back_on_the_posted_next(client: Client):
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        _login(client, "eq733_road_next")
        response = client.post(
            _road(equipment),
            {"orientation_type": orientation_type.pk, "starts_at": _when(), "next": ORIENTATIONS_PAGE},
        )
        assert response["Location"] == ORIENTATIONS_PAGE

    def it_refuses_a_crafted_post_with_the_switch_off(client: Client):
        equipment = EquipmentFactory(allow_custom_requests=False)
        orientation_type = _owned_type(equipment)
        user = _login(client, "eq733_road_off")
        response = client.post(
            _road(equipment), {"orientation_type": orientation_type.pk, "starts_at": _when()}, follow=True
        )
        assert "This equipment isn't taking custom orientation requests right now." in _messages(response)
        assert not OrientationBooking.objects.filter(member=user.member).exists()
        assert not OrientationSlot.objects.filter(orientation_type=orientation_type).exists()

    def it_refuses_closed_equipment(client: Client):
        equipment = EquipmentFactory(is_closed=True)
        orientation_type = _owned_type(equipment)
        user = _login(client, "eq733_road_closed")
        client.post(_road(equipment), {"orientation_type": orientation_type.pk, "starts_at": _when()})
        assert not OrientationBooking.objects.filter(member=user.member).exists()

    def it_refuses_another_equipments_orientation(client: Client):
        equipment = EquipmentFactory()
        foreign = _owned_type(EquipmentFactory(), name="Foreign Basics")
        user = _login(client, "eq733_road_foreign")
        response = client.post(_road(equipment), {"orientation_type": foreign.pk, "starts_at": _when()}, follow=True)
        assert "Pick one of this equipment's orientations and a valid future time." in _messages(response)
        assert not OrientationBooking.objects.filter(member=user.member).exists()

    def it_refuses_a_guild_orientation(client: Client):
        equipment = EquipmentFactory()
        settings_obj = GuildOrientationSettingsFactory(is_enabled=True, allow_custom_requests=True)
        guild_type = OrientationTypeFactory(guild=settings_obj.guild)
        user = _login(client, "eq733_road_guild_type")
        client.post(_road(equipment), {"orientation_type": guild_type.pk, "starts_at": _when()})
        assert not OrientationBooking.objects.filter(member=user.member).exists()

    def it_refuses_a_time_in_the_past(client: Client):
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        user = _login(client, "eq733_road_past")
        past = timezone.localtime(timezone.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        client.post(_road(equipment), {"orientation_type": orientation_type.pk, "starts_at": past})
        assert not OrientationBooking.objects.filter(member=user.member).exists()

    def it_requires_a_member_profile(client: Client):
        MembershipPlanFactory()
        user = User.objects.create_user(username="eq733_road_unlinked", password="pass")
        Member.objects.filter(user=user).delete()
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        client.login(username="eq733_road_unlinked", password="pass")
        response = client.post(_road(equipment), {"orientation_type": orientation_type.pk, "starts_at": _when()})
        assert response.status_code == 302
        assert not OrientationBooking.objects.exists()

    def it_keeps_no_slot_when_the_booking_is_refused(client: Client):
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        user = _login(client, "eq733_road_dupe")
        OrientationBookingFactory(
            slot=OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type), member=user.member
        )
        client.post(_road(equipment), {"orientation_type": orientation_type.pk, "starts_at": _when()})
        assert OrientationBooking.objects.filter(member=user.member).count() == 1
        assert OrientationSlot.objects.filter(orientation_type=orientation_type).count() == 1

    def it_only_answers_a_post(client: Client):
        _login(client, "eq733_road_get")
        assert client.get(_road(EquipmentFactory())).status_code == 405

    def describe_a_priced_orientation():
        @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
        def it_takes_payment_first_and_tells_nobody_yet(mock_create, client: Client):
            manager = _member_user("eq733_paid_manager").member
            equipment = EquipmentFactory(name="Paid CNC")
            EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
            orientation_type = _owned_type(equipment, price_cents=2500)
            user = _login(client, "eq733_paid_member")
            mail.outbox.clear()

            response = client.post(_road(equipment), {"orientation_type": orientation_type.pk, "starts_at": _when()})

            assert response["Location"] == _SESSION["url"]
            assert mock_create.call_args.kwargs["amount_cents"] == 2500
            hold = OrientationBooking.objects.get(member=user.member)
            assert hold.status == OrientationBooking.Status.PENDING_PAYMENT
            assert hold.slot.seats == 1
            assert not [m for m in mail.outbox if "New orientation request" in m.subject]

        @patch("billing.stripe_utils.create_checkout_session", side_effect=RuntimeError("stripe down"))
        def it_cleans_up_when_checkout_fails(mock_create, client: Client):
            equipment = EquipmentFactory()
            orientation_type = _owned_type(equipment, price_cents=2500)
            user = _login(client, "eq733_paid_down")
            response = client.post(
                _road(equipment), {"orientation_type": orientation_type.pk, "starts_at": _when()}, follow=True
            )
            assert "We couldn't start the payment checkout. Please try again in a minute." in _messages(response)
            assert not OrientationBooking.objects.filter(member=user.member).exists()
            assert not OrientationSlot.objects.filter(orientation_type=orientation_type).exists()

    def describe_a_donation_orientation():
        @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
        def it_books_zero_as_a_free_request(mock_create, client: Client):
            equipment = EquipmentFactory()
            orientation_type = _owned_type(equipment, is_donation=True)
            user = _login(client, "eq733_donation_free")
            client.post(
                _road(equipment), {"orientation_type": orientation_type.pk, "starts_at": _when(), "amount": "0"}
            )
            assert OrientationBooking.objects.get(member=user.member).status == OrientationBooking.Status.REQUESTED
            mock_create.assert_not_called()

        @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
        def it_charges_the_entered_amount(mock_create, client: Client):
            equipment = EquipmentFactory()
            orientation_type = _owned_type(equipment, is_donation=True)
            _login(client, "eq733_donation_paid")
            response = client.post(
                _road(equipment), {"orientation_type": orientation_type.pk, "starts_at": _when(), "amount": "12"}
            )
            assert response["Location"] == _SESSION["url"]
            assert mock_create.call_args.kwargs["amount_cents"] == 1200

        @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
        def it_refuses_a_missing_amount_under_a_minimum(mock_create, client: Client):
            equipment = EquipmentFactory()
            orientation_type = _owned_type(equipment, is_donation=True, donation_minimum_cents=1000)
            user = _login(client, "eq733_donation_missing")
            response = client.post(
                _road(equipment), {"orientation_type": orientation_type.pk, "starts_at": _when()}, follow=True
            )
            assert "Enter what you'd like to pay, $10 or more." in _messages(response)
            assert not OrientationBooking.objects.filter(member=user.member).exists()
            mock_create.assert_not_called()


def describe_the_service():
    def it_refuses_equipment_with_the_switch_off_and_keeps_no_slot():
        equipment = EquipmentFactory(allow_custom_requests=False)
        orientation_type = _owned_type(equipment)
        member = _member_user("eq733_svc_off").member
        with pytest.raises(OrientationError, match="This equipment isn't taking custom orientation requests"):
            orientations.request_custom_orientation(
                equipment, member, timezone.now() + timedelta(days=2), orientation_type=orientation_type
            )
        assert not OrientationSlot.objects.filter(orientation_type=orientation_type).exists()

    def it_refuses_a_retired_type():
        equipment = EquipmentFactory()
        retired = _owned_type(equipment, is_active=False)
        member = _member_user("eq733_svc_retired").member
        with pytest.raises(OrientationError, match="isn't offered right now"):
            orientations.request_custom_orientation(
                equipment, member, timezone.now() + timedelta(days=2), orientation_type=retired
            )

    def it_refuses_a_checkout_on_equipment_with_the_switch_off():
        equipment = EquipmentFactory(allow_custom_requests=False)
        orientation_type = _owned_type(equipment, price_cents=1000)
        member = _member_user("eq733_svc_checkout_off").member
        with pytest.raises(OrientationError, match="This equipment isn't taking custom orientation requests"):
            orientations.start_custom_orientation_checkout(
                equipment, member, timezone.now() + timedelta(days=2), orientation_type=orientation_type
            )
        assert not OrientationSlot.objects.filter(orientation_type=orientation_type).exists()

    def it_still_refuses_another_guilds_type_for_a_guild():
        settings_obj = GuildOrientationSettingsFactory(is_enabled=True, allow_custom_requests=True)
        foreign = OrientationTypeFactory(guild=GuildFactory(), name="Elsewhere")
        member = _member_user("eq733_svc_guild").member
        with pytest.raises(OrientationError, match="isn't offered right now"):
            orientations.request_custom_orientation(
                settings_obj.guild, member, timezone.now() + timedelta(days=2), orientation_type=foreign
            )
