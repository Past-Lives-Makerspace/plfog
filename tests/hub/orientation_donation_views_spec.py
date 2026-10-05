"""BDD specs for donation based orientations in the hub (#636): the type editors, the three booking roads, the pages."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from hub.forms import OrientationAmountForm, OrientationCustomRequestForm, OrientationTypeForm
from membership.models import OrientationBooking, OrientationError, OrientationType
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationAvailabilityBlockFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

_SESSION = {"id": "cs_donation_view", "url": "https://checkout.stripe.example/cs_donation_view"}
AUTOSAVE = {"HTTP_X_AUTOSAVE": "1"}


def _login(client: Client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    client.login(username=username, password="pass")
    return user


def _donation_type(*, minimum: int = 0, suggested: int | None = None, **fields: object) -> OrientationType:
    settings_obj = GuildOrientationSettingsFactory(allow_custom_requests=True)
    fields.setdefault("name", "Jewelry Basics")
    fields.setdefault("is_donation", True)
    return OrientationTypeFactory(
        guild=settings_obj.guild,
        donation_minimum_cents=minimum,
        donation_suggested_cents=suggested,
        **fields,
    )


def _type_form(**data: object) -> OrientationTypeForm:
    payload: dict[str, object] = {
        "name": "Jewelry Basics",
        "duration_minutes": "60",
        "default_seats": "4",
        "sort_order": "0",
        "is_active": "on",
    }
    payload.update(data)
    return OrientationTypeForm(payload, instance=OrientationType(guild=GuildFactory()))


def _formset(row: dict[str, object], *, orientation_type: OrientationType) -> dict[str, object]:
    data: dict[str, object] = {
        "otypes-TOTAL_FORMS": "1",
        "otypes-INITIAL_FORMS": "1",
        "otypes-MIN_NUM_FORMS": "0",
        "otypes-MAX_NUM_FORMS": "1000",
        "otypes-0-id": str(orientation_type.pk),
        "otypes-0-name": orientation_type.name,
        "otypes-0-duration_minutes": "60",
        "otypes-0-default_seats": "4",
        "otypes-0-sort_order": "0",
        "otypes-0-is_active": "on",
    }
    data.update({f"otypes-0-{key}": value for key, value in row.items()})
    return data


def _messages(response) -> list[str]:
    return [str(m) for m in response.context["messages"]]


def describe_the_type_form():
    def it_saves_a_donation_type_with_its_minimum_and_suggestion():
        form = _type_form(is_donation="on", donation_minimum="5", donation_suggested="12.50")
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.is_donation is True
        assert saved.donation_minimum_cents == 500
        assert saved.donation_suggested_cents == 1250

    def it_defaults_a_blank_minimum_to_zero_and_a_blank_suggestion_to_none():
        form = _type_form(is_donation="on", donation_minimum="", donation_suggested="")
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.donation_minimum_cents == 0
        assert saved.donation_suggested_cents is None

    @pytest.mark.parametrize("minimum", ["0.50", "0.99", "-1"])
    def it_refuses_a_minimum_between_zero_and_one_dollar(minimum: str):
        form = _type_form(is_donation="on", donation_minimum=minimum)
        assert not form.is_valid()
        assert form.errors["donation_minimum"] == ["Set the minimum to $0 or at least $1."]

    def it_reports_an_unreadable_minimum_without_judging_the_suggestion():
        form = _type_form(is_donation="on", donation_minimum="lots", donation_suggested="0.50")
        assert not form.is_valid()
        assert "donation_minimum" in form.errors
        assert "donation_suggested" not in form.errors

    def it_refuses_a_suggestion_under_the_minimum():
        form = _type_form(is_donation="on", donation_minimum="10", donation_suggested="5")
        assert not form.is_valid()
        assert form.errors["donation_suggested"] == ["Set the suggestion to at least $10, or leave it blank."]

    def it_refuses_a_suggestion_under_one_dollar_even_with_no_minimum():
        form = _type_form(is_donation="on", donation_minimum="0", donation_suggested="0.50")
        assert not form.is_valid()
        assert form.errors["donation_suggested"] == ["Set the suggestion to at least $1, or leave it blank."]

    def it_ignores_the_hidden_donation_fields_while_the_toggle_is_off():
        orientation_type = _donation_type(minimum=500, suggested=1000, is_donation=False, price_cents=1500)
        form = OrientationTypeForm(
            {
                "name": orientation_type.name,
                "duration_minutes": "60",
                "default_seats": "4",
                "sort_order": "0",
                "is_active": "on",
                "price": "15",
                "donation_minimum": "0.25",
                "donation_suggested": "0.10",
            },
            instance=orientation_type,
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.is_donation is False
        assert saved.price_cents == 1500
        assert (saved.donation_minimum_cents, saved.donation_suggested_cents) == (500, 1000)

    def it_keeps_the_stored_price_while_donation_is_on():
        orientation_type = _donation_type(price_cents=1500)
        form = OrientationTypeForm(
            {
                "name": orientation_type.name,
                "duration_minutes": "60",
                "default_seats": "4",
                "sort_order": "0",
                "is_active": "on",
                "price": "15",
                "is_donation": "on",
            },
            instance=orientation_type,
        )
        assert form.is_valid(), form.errors
        assert form.save().price_cents == 1500

    def it_starts_a_saved_type_from_its_dollars():
        form = OrientationTypeForm(instance=_donation_type(minimum=500, suggested=1250))
        assert form.fields["donation_minimum"].initial == Decimal("5")
        assert form.fields["donation_suggested"].initial == Decimal("12.5")

    def it_leaves_a_zero_minimum_and_no_suggestion_empty():
        form = OrientationTypeForm(instance=_donation_type())
        assert form.fields["donation_minimum"].initial is None
        assert form.fields["donation_suggested"].initial is None


def describe_the_guild_editor():
    def it_renders_the_toggle_and_its_fields_on_saved_and_new_rows(client: Client):
        user = _login(client, "don_guild_render")
        guild = GuildFactory(guild_lead=user.member)
        OrientationTypeFactory(guild=guild, name="Shop Basics")
        content = client.get(reverse("hub_guild_edit", args=[guild.pk])).content.decode()
        assert 'name="otypes-0-is_donation"' in content
        assert 'name="otypes-0-donation_minimum"' in content
        assert 'name="otypes-0-donation_suggested"' in content
        template = content[content.index('<template id="otype-empty-template"') :]
        template = template[: template.index("</template>")]
        assert 'name="otypes-__prefix__-is_donation"' in template
        assert 'x-model="donation"' in template
        assert 'x-show="!donation"' in template

    def it_turns_donation_on_through_autosave(client: Client):
        user = _login(client, "don_guild_save")
        guild = GuildFactory(guild_lead=user.member)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        orientation_type = OrientationTypeFactory(guild=guild, name="Bench Basics")
        data = _formset(
            {"is_donation": "on", "donation_minimum": "0", "donation_suggested": "20"},
            orientation_type=orientation_type,
        )
        with patch("membership.orientations.generate_slots"):
            response = client.post(reverse("hub_guild_orientation_types_save", args=[guild.pk]), data, **AUTOSAVE)
        assert response.status_code == 200
        orientation_type.refresh_from_db()
        assert orientation_type.is_donation is True
        assert orientation_type.donation_suggested_cents == 2000


def describe_the_equipment_editor():
    def it_renders_the_toggle_on_saved_and_new_rows(client: Client):
        user = _login(client, "don_item_render")
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        OrientationTypeFactory(guild=None, equipment=equipment, name="Kiln")
        content = client.get(reverse("hub_equipment_manage", args=[equipment.slug])).content.decode()
        assert 'name="otypes-0-is_donation"' in content
        template = content[content.index('<template id="equip-otype-empty-template"') :]
        assert 'name="otypes-__prefix__-donation_suggested"' in template[: template.index("</template>")]

    def it_turns_donation_on_with_the_formset(client: Client):
        user = _login(client, "don_item_save")
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        orientation_type = OrientationTypeFactory(guild=None, equipment=equipment, name="Kiln")
        data = _formset({"is_donation": "on", "donation_minimum": "5"}, orientation_type=orientation_type)
        response = client.post(reverse("hub_equipment_orientation_types_save", args=[equipment.slug]), data)
        assert response.status_code == 302
        orientation_type.refresh_from_db()
        assert orientation_type.is_donation is True
        assert orientation_type.donation_minimum_cents == 500

    def it_shows_the_floor_error_on_the_row(client: Client):
        user = _login(client, "don_item_error")
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        orientation_type = OrientationTypeFactory(guild=None, equipment=equipment, name="Kiln")
        data = _formset({"is_donation": "on", "donation_minimum": "0.50"}, orientation_type=orientation_type)
        response = client.post(reverse("hub_equipment_orientation_types_save", args=[equipment.slug]), data)
        assert response.status_code == 200
        assert "Set the minimum to $0 or at least $1." in response.content.decode()
        orientation_type.refresh_from_db()
        assert orientation_type.is_donation is False


def describe_the_amount_form():
    def it_reads_dollars_as_cents():
        assert OrientationAmountForm({"amount": "12.34"}).amount_cents() == 1234
        assert OrientationAmountForm({"amount": "0"}).amount_cents() == 0

    def it_reads_a_blank_amount_as_none():
        assert OrientationAmountForm({"amount": ""}).amount_cents() is None
        assert OrientationAmountForm({}).amount_cents() is None

    def it_refuses_something_that_is_not_dollars():
        with pytest.raises(OrientationError, match=r"^Enter the amount in dollars, like 10 or 12\.50\.$"):
            OrientationAmountForm({"amount": "lots"}).amount_cents()

    def it_starts_from_the_suggestion_with_the_types_hint():
        form = OrientationAmountForm(orientation_type=_donation_type(minimum=500, suggested=1500))
        assert form.fields["amount"].initial == "15"
        assert form.fields["amount"].help_text == "At least $5, paid by card when you book."

    def it_binds_to_the_guild_pages_picker():
        widget = OrientationAmountForm(follows_picker=True).fields["amount"].widget
        assert widget.attrs["x-model"] == "amount"


def describe_the_custom_request_picker():
    def it_maps_only_donation_types_to_their_suggestions():
        orientation_type = _donation_type(suggested=1500)
        OrientationTypeFactory(guild=orientation_type.guild, name="Fixed", price_cents=1000)
        form = OrientationCustomRequestForm(guild=orientation_type.guild)
        assert form.donation_suggestions == {str(orientation_type.pk): "15"}
        assert form.fields["orientation_type"].widget.attrs["x-model"] == "typePk"

    def it_maps_nothing_for_a_guild_with_no_types():
        form = OrientationCustomRequestForm(guild=GuildFactory())
        assert form.has_types is False
        assert form.donation_suggestions_json == "{}"


def describe_booking_a_posted_slot():
    def _slot(**type_fields: object):
        orientation_type = _donation_type(**type_fields)
        return OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type)

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_books_zero_as_a_free_request_with_no_checkout(mock_create, client: Client):
        user = _login(client, "don_slot_free")
        slot = _slot()
        response = client.post(reverse("hub_orientation_book", args=[slot.pk]), {"amount": "0"})
        assert response.status_code == 302
        booking = OrientationBooking.objects.get(member=user.member)
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert booking.amount_paid_cents == 0
        mock_create.assert_not_called()

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_charges_exactly_the_entered_amount_through_checkout(mock_create, client: Client):
        user = _login(client, "don_slot_paid")
        slot = _slot(suggested=1500)
        response = client.post(reverse("hub_orientation_book", args=[slot.pk]), {"amount": "12.34"})
        assert response["Location"] == _SESSION["url"]
        assert mock_create.call_args.kwargs["amount_cents"] == 1234
        assert OrientationBooking.objects.get(member=user.member).status == OrientationBooking.Status.PENDING_PAYMENT

    @pytest.mark.parametrize("amount", ["0.01", "0.50", "0.99"])
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_refuses_an_amount_under_a_dollar(mock_create, client: Client, amount: str):
        user = _login(client, "don_slot_floor")
        slot = _slot()
        response = client.post(reverse("hub_orientation_book", args=[slot.pk]), {"amount": amount}, follow=True)
        assert "Any amount above $0 has to be at least $1. Enter $0 to book for free." in _messages(response)
        assert not OrientationBooking.objects.filter(member=user.member).exists()
        mock_create.assert_not_called()

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_refuses_an_amount_under_the_minimum(mock_create, client: Client):
        user = _login(client, "don_slot_min")
        slot = _slot(minimum=1000)
        response = client.post(reverse("hub_orientation_book", args=[slot.pk]), {"amount": "5"}, follow=True)
        assert "The minimum for this orientation is $10." in _messages(response)
        assert not OrientationBooking.objects.filter(member=user.member).exists()
        mock_create.assert_not_called()

    def it_refuses_a_missing_or_unreadable_amount(client: Client):
        user = _login(client, "don_slot_blank")
        slot = _slot()
        blank = client.post(reverse("hub_orientation_book", args=[slot.pk]), {}, follow=True)
        assert "Enter what you'd like to pay. $0 is fine." in _messages(blank)
        junk = client.post(reverse("hub_orientation_book", args=[slot.pk]), {"amount": "lots"}, follow=True)
        assert "Enter the amount in dollars, like 10 or 12.50." in _messages(junk)
        assert not OrientationBooking.objects.filter(member=user.member).exists()

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_charges_a_fixed_type_its_price_whatever_amount_is_posted(mock_create, client: Client):
        _login(client, "don_slot_fixed")
        settings_obj = GuildOrientationSettingsFactory()
        orientation_type = OrientationTypeFactory(guild=settings_obj.guild, price_cents=1500)
        slot = OrientationSlotFactory(guild=settings_obj.guild, orientation_type=orientation_type)
        client.post(reverse("hub_orientation_book", args=[slot.pk]), {"amount": "1"})
        assert mock_create.call_args.kwargs["amount_cents"] == 1500


def describe_booking_a_custom_time():
    def _post(client: Client, orientation_type: OrientationType, amount: str, *, follow: bool = False):
        starts = timezone.localtime(timezone.now() + timedelta(days=3))
        return client.post(
            reverse("hub_guild_orientation_request_custom", args=[orientation_type.guild.pk]),
            {
                "orientation_type": orientation_type.pk,
                "starts_at": starts.strftime("%Y-%m-%dT%H:%M"),
                "note": "",
                "amount": amount,
            },
            follow=follow,
        )

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_books_zero_as_a_free_request(mock_create, client: Client):
        user = _login(client, "don_custom_free")
        _post(client, _donation_type(), "0")
        assert OrientationBooking.objects.get(member=user.member).status == OrientationBooking.Status.REQUESTED
        mock_create.assert_not_called()

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_charges_the_entered_amount(mock_create, client: Client):
        _login(client, "don_custom_paid")
        response = _post(client, _donation_type(), "20")
        assert response["Location"] == _SESSION["url"]
        assert mock_create.call_args.kwargs["amount_cents"] == 2000

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_refuses_an_amount_under_a_dollar(mock_create, client: Client):
        user = _login(client, "don_custom_floor")
        response = _post(client, _donation_type(), "0.99", follow=True)
        assert "Any amount above $0 has to be at least $1. Enter $0 to book for free." in _messages(response)
        assert not OrientationBooking.objects.filter(member=user.member).exists()
        mock_create.assert_not_called()


def describe_booking_a_block_time():
    def _post(client: Client, amount: str, *, minimum: int = 0, follow: bool = False):
        orientation_type = _donation_type(minimum=minimum)
        block = OrientationAvailabilityBlockFactory(guild=orientation_type.guild)
        start = timezone.localtime(block.starts_at).strftime("%Y-%m-%dT%H:%M")
        return client.post(
            reverse("hub_orientation_block_book", args=[block.pk]),
            {"orientation_type": orientation_type.pk, "starts_at": start, "note": "", "amount": amount},
            follow=follow,
        )

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_books_zero_as_a_free_request(mock_create, client: Client):
        user = _login(client, "don_block_free")
        _post(client, "0")
        assert OrientationBooking.objects.get(member=user.member).status == OrientationBooking.Status.REQUESTED
        mock_create.assert_not_called()

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_charges_the_entered_amount(mock_create, client: Client):
        _login(client, "don_block_paid")
        response = _post(client, "15")
        assert response["Location"] == _SESSION["url"]
        assert mock_create.call_args.kwargs["amount_cents"] == 1500

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_refuses_an_amount_under_the_minimum(mock_create, client: Client):
        user = _login(client, "don_block_min")
        response = _post(client, "3", minimum=500, follow=True)
        assert "The minimum for this orientation is $5." in _messages(response)
        assert not OrientationBooking.objects.filter(member=user.member).exists()
        mock_create.assert_not_called()


def describe_the_member_pages():
    def it_shows_the_suggestion_chip_and_a_prefilled_amount_on_the_guild_page(client: Client):
        _login(client, "don_page_guild")
        orientation_type = _donation_type(suggested=1500, price_cents=4000)
        OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type)
        content = client.get(reverse("hub_guild_detail", args=[orientation_type.guild.slug])).content.decode()
        section = content.split('id="guild-orientation"')[1].split("</section>")[0]
        assert '<span class="pl-price-chip">Donation, $15 suggested</span>' in section
        assert "$40" not in section
        assert "amount: '15'" in content
        assert '<input type="hidden" name="amount" :value="amount">' in content

    def it_maps_the_custom_pickers_donation_types_on_the_guild_page(client: Client):
        _login(client, "don_page_custom")
        orientation_type = _donation_type(suggested=1500)
        OrientationTypeFactory(guild=orientation_type.guild, name="Fixed", price_cents=1000)
        content = client.get(reverse("hub_guild_detail", args=[orientation_type.guild.slug])).content.decode()
        assert f"suggestions: {{&quot;{orientation_type.pk}&quot;: &quot;15&quot;}}" in content
        assert 'id="id_custom_amount_amount"' in content
        assert 'x-show="typePk in suggestions"' in content

    def it_says_pay_what_you_can_on_the_orientations_card_and_prefills_nothing(client: Client):
        _login(client, "don_page_card")
        orientation_type = _donation_type()
        content = client.get(reverse("hub_orientations")).content.decode()
        card = content.split(f'id="orientation-type-{orientation_type.pk}"')[1]
        assert '<span class="pl-price-chip">Pay what you can</span>' in card
        assert f'id="id_custom_{orientation_type.pk}_amount"' in card

    def it_prefills_the_block_pickers_amount_with_the_suggestion(client: Client):
        _login(client, "don_page_block")
        orientation_type = _donation_type(suggested=2500)
        block = OrientationAvailabilityBlockFactory(guild=orientation_type.guild)
        content = client.get(
            reverse("hub_orientation_block_starts", args=[block.pk, orientation_type.pk])
        ).content.decode()
        assert 'name="amount" value="25"' in content
        assert "Book this time" in content

    def it_shows_the_chip_on_the_info_page(client: Client):
        _login(client, "don_page_info")
        orientation_type = _donation_type(suggested=1000)
        content = client.get(reverse("hub_orientation_info", args=[orientation_type.guild.pk])).content.decode()
        assert '<span class="pl-price-chip">Donation, $10 suggested</span>' in content

    def it_shows_the_chip_on_the_equipment_page(client: Client):
        _login(client, "don_page_item")
        equipment = EquipmentFactory()
        OrientationTypeFactory(guild=None, equipment=equipment, name="Kiln", is_donation=True)
        content = client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        assert '<span class="pl-price-chip">Pay what you can</span>' in content
