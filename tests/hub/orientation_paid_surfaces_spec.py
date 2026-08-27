"""BDD specs for the paid-orientation surfaces: guild page, respond page, settings form, dashboard, CSV."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from billing.models import PaymentRefund
from hub.forms import GuildOrientationSettingsForm, OrientationAddMemberForm
from membership.models import Member, OrientationBooking, OrientationSlot
from tests.membership.factories import (
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _paid_slot(price_cents: int = 1500) -> OrientationSlot:
    settings_obj = GuildOrientationSettingsFactory(price_cents=price_cents)
    return OrientationSlotFactory(guild=settings_obj.guild)


def describe_guild_page():
    def it_shows_the_price_chip_and_refund_promise_on_a_paid_guild(client: Client):
        _user_with_role("gp1")
        slot = _paid_slot()
        client.login(username="gp1", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[slot.guild.slug])).content.decode()
        assert "pl-price-chip" in content
        assert "$15" in content
        assert "You pay when you book" in content
        assert "Continue to Payment" in content

    def it_renders_a_free_guild_without_payment_copy(client: Client):
        _user_with_role("gp2")
        slot = OrientationSlotFactory()
        client.login(username="gp2", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[slot.guild.slug])).content.decode()
        # Scope to the orientation section — the changelog renders into every hub
        # page and may legitimately mention payment copy elsewhere on the page.
        section = content.split('id="guild-orientation"')[1].split("</section>")[0]
        assert "pl-price-chip" not in section
        assert "You pay when you book" not in section

    def it_shows_the_finishing_payment_state_for_a_live_hold(client: Client):
        user = _user_with_role("gp3")
        slot = _paid_slot()
        hold = OrientationBookingFactory(
            slot=slot, member=user.member, status=OrientationBooking.Status.PENDING_PAYMENT, amount_paid_cents=1500
        )
        client.login(username="gp3", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[slot.guild.slug])).content.decode()
        assert "Finishing Your Booking" in content
        assert "Resume payment" in content
        assert reverse("hub_orientation_checkout_cancel_hold", args=[hold.pk]) in content

    def it_shows_the_paid_line_on_a_requested_booking(client: Client):
        user = _user_with_role("gp4")
        slot = _paid_slot()
        OrientationBookingFactory(slot=slot, member=user.member, amount_paid_cents=1500, stripe_payment_id="pi_1")
        client.login(username="gp4", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[slot.guild.slug])).content.decode()
        assert "Paid $15. Automatic refund if this is declined or cancelled." in content
        assert "automatic full refund" in content  # cancel confirm modal copy


def describe_orientation_info_page():
    def it_shows_the_price_line_for_a_paid_guild(client: Client):
        _user_with_role("oi1")
        settings_obj = GuildOrientationSettingsFactory(price_cents=1500)
        client.login(username="oi1", password="pass")
        content = client.get(reverse("hub_orientation_info", args=[settings_obj.guild.pk])).content.decode()
        assert "$15" in content
        assert "automatic full refund" in content

    def it_stays_clean_for_a_free_guild(client: Client):
        _user_with_role("oi2")
        settings_obj = GuildOrientationSettingsFactory()
        client.login(username="oi2", password="pass")
        content = client.get(reverse("hub_orientation_info", args=[settings_obj.guild.pk])).content.decode()
        assert "pl-price-chip" not in content


def describe_respond_page():
    def it_shows_the_paid_chip_and_decline_warning(client: Client):
        _user_with_role("rp1", fog_role=Member.FogRole.ADMIN)
        booking = OrientationBookingFactory(slot=_paid_slot(), amount_paid_cents=1500, stripe_payment_id="pi_1")
        client.login(username="rp1", password="pass")
        content = client.get(reverse("hub_orientation_respond", args=[booking.pk])).content.decode()
        assert "Paid $15" in content
        assert "Declining refunds their $15 automatically." in content

    def it_renders_a_free_booking_exactly_as_before(client: Client):
        _user_with_role("rp2", fog_role=Member.FogRole.ADMIN)
        booking = OrientationBookingFactory()
        client.login(username="rp2", password="pass")
        # The changelog renders into every hub page, so negative assertions target
        # this feature's exact strings, not generic words like "refund".
        content = client.get(reverse("hub_orientation_respond", args=[booking.pk])).content.decode()
        assert "pl-price-chip" not in content
        assert "Declining refunds their" not in content

    def it_shows_the_refund_failed_banner_with_a_panel_link_for_refund_authority(client: Client):
        _user_with_role("rp3", fog_role=Member.FogRole.ADMIN)
        booking = OrientationBookingFactory(
            slot=_paid_slot(),
            status=OrientationBooking.Status.DECLINED,
            amount_paid_cents=1500,
            stripe_payment_id="pi_1",
        )
        PaymentRefund.objects.create(orientation_booking=booking, amount_cents=1500, status=PaymentRefund.Status.FAILED)
        client.login(username="rp3", password="pass")
        content = client.get(reverse("hub_orientation_respond", args=[booking.pk])).content.decode()
        assert "refund failed" in content.lower()
        assert "tab=payments" in content

    def it_shows_the_refunded_chip_after_a_refund(client: Client):
        _user_with_role("rp4", fog_role=Member.FogRole.ADMIN)
        booking = OrientationBookingFactory(
            slot=_paid_slot(),
            status=OrientationBooking.Status.DECLINED,
            amount_paid_cents=1500,
            stripe_payment_id="pi_1",
        )
        PaymentRefund.objects.create(
            orientation_booking=booking, amount_cents=1500, status=PaymentRefund.Status.SUCCEEDED
        )
        client.login(username="rp4", password="pass")
        content = client.get(reverse("hub_orientation_respond", args=[booking.pk])).content.decode()
        assert "Refunded" in content


def describe_settings_price_form():
    def it_maps_dollars_to_cents(db):
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildOrientationSettingsForm(
            instance=settings_obj,
            data={
                "is_enabled": "on",
                "default_seats": "4",
                "default_duration_minutes": "60",
                "price": "15.50",
            },
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.price_cents == 1550

    def it_normalizes_blank_to_free(db):
        settings_obj = GuildOrientationSettingsFactory(price_cents=1500)
        form = GuildOrientationSettingsForm(
            instance=settings_obj,
            data={"is_enabled": "on", "default_seats": "4", "default_duration_minutes": "60", "price": ""},
        )
        assert form.is_valid(), form.errors
        assert form.save().price_cents == 0

    def it_renders_a_free_guild_with_an_empty_field(db):
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildOrientationSettingsForm(instance=settings_obj)
        assert form.fields["price"].initial is None

    def it_prefills_a_paid_guild_in_dollars(db):
        settings_obj = GuildOrientationSettingsFactory(price_cents=1550)
        form = GuildOrientationSettingsForm(instance=settings_obj)
        assert form.fields["price"].initial == Decimal("15.50")

    def it_rejects_a_price_over_the_sanity_cap(db):
        settings_obj = GuildOrientationSettingsFactory()
        form = GuildOrientationSettingsForm(
            instance=settings_obj,
            data={"is_enabled": "on", "default_seats": "4", "default_duration_minutes": "60", "price": "600"},
        )
        assert not form.is_valid()
        assert "between $0 and $500" in str(form.errors["price"])


def describe_orientations_dashboard():
    def it_shows_the_paid_column_variants(client: Client):
        _user_with_role("od1", fog_role=Member.FogRole.ADMIN)
        OrientationBookingFactory()  # free
        paid = OrientationBookingFactory(slot=_paid_slot(), amount_paid_cents=1500, stripe_payment_id="pi_1")
        refunded = OrientationBookingFactory(slot=_paid_slot(), amount_paid_cents=2000, stripe_payment_id="pi_2")
        PaymentRefund.objects.create(
            orientation_booking=refunded, amount_cents=2000, status=PaymentRefund.Status.SUCCEEDED
        )
        failed = OrientationBookingFactory(slot=_paid_slot(), amount_paid_cents=2500, stripe_payment_id="pi_3")
        PaymentRefund.objects.create(orientation_booking=failed, amount_cents=2500, status=PaymentRefund.Status.FAILED)
        client.login(username="od1", password="pass")
        content = client.get(reverse("hub_orientations_dashboard")).content.decode()
        assert "<th>Paid</th>" in content
        assert "$15" in content
        assert "$20 · Refunded" in content
        assert "Refund failed" in content
        assert paid.pk  # silence unused warnings

    def it_labels_slots_with_checkout_holds_in_the_add_member_dropdown(db):
        slot = OrientationSlotFactory(seats=2)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, amount_paid_cents=1500)
        form = OrientationAddMemberForm(slot_queryset=OrientationSlot.objects.with_pending_hold_count())
        labels = [label for _value, label in form.fields["slot"].choices]
        assert any("1 seat held by a checkout in progress" in label for label in labels)

    def it_maps_paid_slots_for_the_comp_note(client: Client):
        _user_with_role("od2", fog_role=Member.FogRole.ADMIN)
        slot = _paid_slot()
        client.login(username="od2", password="pass")
        response = client.get(reverse("hub_orientations_dashboard"))
        assert f'"{slot.pk}": "$15"' in response.context["paid_slot_prices_json"]
        assert "Members you add here are not charged." in response.content.decode()

    def it_names_the_hold_when_add_member_hits_a_hold_filled_slot(client: Client):
        _user_with_role("od3", fog_role=Member.FogRole.ADMIN)
        slot = OrientationSlotFactory(seats=1)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, amount_paid_cents=1500)
        client.login(username="od3", password="pass")
        response = client.post(
            reverse("hub_orientation_add_member"),
            {"member": MemberFactory().pk, "slot": slot.pk},
            follow=True,
        )
        assert any("finishing checkout" in str(m) for m in response.context["messages"])

    def it_comps_staff_added_members(client: Client):
        _user_with_role("od4", fog_role=Member.FogRole.ADMIN)
        slot = _paid_slot()
        target = MemberFactory()
        client.login(username="od4", password="pass")
        client.post(reverse("hub_orientation_add_member"), {"member": target.pk, "slot": slot.pk})
        booking = OrientationBooking.objects.get(member=target)
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert booking.amount_paid_cents == 0


def describe_csv_export():
    def it_includes_the_paid_and_refund_columns(client: Client):
        _user_with_role("csv1", fog_role=Member.FogRole.ADMIN)
        booking = OrientationBookingFactory(slot=_paid_slot(), amount_paid_cents=1500, stripe_payment_id="pi_1")
        client.login(username="csv1", password="pass")
        response = client.get(reverse("hub_orientations_export"))
        body = b"".join(response.streaming_content).decode()
        assert "Paid" in body.splitlines()[0]
        assert "Refund status" in body.splitlines()[0]
        assert "15.00" in body
        assert booking.refund_state == "none"


def describe_add_member_label_fallback():
    def it_falls_back_to_the_property_without_the_annotation(db):
        slot = OrientationSlotFactory(seats=3)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, amount_paid_cents=1500)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, amount_paid_cents=1500)
        form = OrientationAddMemberForm(slot_queryset=OrientationSlot.objects.all())
        labels = [label for _value, label in form.fields["slot"].choices]
        assert any("2 seats held by a checkout in progress" in label for label in labels)

    def it_leaves_hold_free_slots_unchanged(db):
        slot = OrientationSlotFactory()
        form = OrientationAddMemberForm(slot_queryset=OrientationSlot.objects.with_pending_hold_count())
        labels = [label for _value, label in form.fields["slot"].choices]
        assert str(slot) in labels


def describe_no_login_action_page():
    def it_warns_about_the_automatic_refund_on_a_paid_decline(client: Client):
        from membership import orientations

        booking = OrientationBookingFactory(slot=_paid_slot(), amount_paid_cents=1500, stripe_payment_id="pi_1")
        token = orientations.make_action_token(booking, "decline")
        content = client.get(reverse("hub_orientation_action", args=[token])).content.decode()
        assert "Declining refunds their $15 automatically." in content

    def it_keeps_the_free_confirm_step_clean(client: Client):
        from membership import orientations

        booking = OrientationBookingFactory()
        token = orientations.make_action_token(booking, "decline")
        content = client.get(reverse("hub_orientation_action", args=[token])).content.decode()
        assert "refunds their" not in content


def describe_guild_edit_upcoming_slots():
    def it_shows_held_seats_on_the_upcoming_slots_card(client: Client):
        _user_with_role("ge1", fog_role=Member.FogRole.ADMIN)
        slot = _paid_slot()
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, amount_paid_cents=1500)
        client.login(username="ge1", password="pass")
        content = client.get(reverse("hub_guild_edit", args=[slot.guild.pk]) + "?tab=orientations").content.decode()
        assert "1 seat held by a checkout in progress" in content


def describe_hold_transition_guards_in_views():
    def _hold_booking():
        slot = _paid_slot()
        return OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT)

    def it_blocks_a_crafted_decline_post_on_a_hold(client: Client):
        _user_with_role("hg1", fog_role=Member.FogRole.ADMIN)
        hold = _hold_booking()
        client.login(username="hg1", password="pass")
        response = client.post(
            reverse("hub_orientation_respond", args=[hold.pk]), {"action": "decline", "note": ""}, follow=True
        )
        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.PENDING_PAYMENT
        assert any("still finishing checkout" in str(m) for m in response.context["messages"])

    def it_blocks_a_crafted_confirm_post_on_a_hold(client: Client):
        _user_with_role("hg2", fog_role=Member.FogRole.ADMIN)
        hold = _hold_booking()
        client.login(username="hg2", password="pass")
        response = client.post(reverse("hub_orientation_respond", args=[hold.pk]), {"action": "confirm"}, follow=True)
        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.PENDING_PAYMENT
        assert any("still finishing checkout" in str(m) for m in response.context["messages"])

    def it_blocks_a_lead_cancel_on_a_hold(client: Client):
        _user_with_role("hg3", fog_role=Member.FogRole.ADMIN)
        hold = _hold_booking()
        client.login(username="hg3", password="pass")
        response = client.post(reverse("hub_orientation_lead_cancel", args=[hold.pk]), follow=True)
        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.PENDING_PAYMENT
        assert any("still finishing checkout" in str(m) for m in response.context["messages"])

    def it_blocks_a_member_cancel_on_their_own_hold(client: Client):
        user = _user_with_role("hg4")
        slot = _paid_slot()
        hold = OrientationBookingFactory(
            slot=slot, member=user.member, status=OrientationBooking.Status.PENDING_PAYMENT
        )
        client.login(username="hg4", password="pass")
        response = client.post(reverse("hub_orientation_cancel_mine", args=[hold.pk]), follow=True)
        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.PENDING_PAYMENT
        assert any("still finishing checkout" in str(m) for m in response.context["messages"])
