"""BDD specs for the availability-block views (issue #283): the HTMX start picker, member
booking, the window cancel gate, the dashboard without its retired blocks card (#532), and the
guild-page pick-a-time section."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from membership import orientations
from membership.models import Member, OrientationBooking
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityBlockFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

_SESSION = {"id": "cs_test_blkv", "url": "https://checkout.stripe.example/cs_test_blkv"}


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _block_with_type(*, duration_minutes: int = 60, price_cents: int = 0):
    block = OrientationAvailabilityBlockFactory()
    orientation_type = OrientationTypeFactory(
        guild=block.guild, duration_minutes=duration_minutes, price_cents=price_cents
    )
    return block, orientation_type


def _option_value(start) -> str:
    return timezone.localtime(start).strftime("%Y-%m-%dT%H:%M")


def describe_orientation_block_starts():
    def it_returns_only_the_live_valid_starts(client: Client):
        _user_with_role("bs1")
        block, orientation_type = _block_with_type()
        taken = block.starts_at + timedelta(minutes=60)
        orientations.request_block_orientation(block, MemberFactory(), taken, orientation_type=orientation_type)
        client.login(username="bs1", password="pass")

        response = client.get(reverse("hub_orientation_block_starts", args=[block.pk, orientation_type.pk]))

        assert response.status_code == 200
        content = response.content.decode()
        assert _option_value(block.starts_at) in content
        assert _option_value(taken) not in content
        assert reverse("hub_orientation_block_book", args=[block.pk]) in content

    def it_says_when_every_time_is_taken(client: Client):
        _user_with_role("bs2")
        block, orientation_type = _block_with_type(duration_minutes=180)  # one 3-hour fit
        orientations.request_block_orientation(
            block, MemberFactory(), block.starts_at, orientation_type=orientation_type
        )
        client.login(username="bs2", password="pass")
        content = client.get(
            reverse("hub_orientation_block_starts", args=[block.pk, orientation_type.pk])
        ).content.decode()
        assert reverse("hub_orientation_block_book", args=[block.pk]) not in content

    def it_404s_for_a_cancelled_block(client: Client):
        _user_with_role("bs3")
        block, orientation_type = _block_with_type()
        block.cancel()
        client.login(username="bs3", password="pass")
        response = client.get(reverse("hub_orientation_block_starts", args=[block.pk, orientation_type.pk]))
        assert response.status_code == 404

    def it_requires_login(client: Client):
        block, orientation_type = _block_with_type()
        response = client.get(reverse("hub_orientation_block_starts", args=[block.pk, orientation_type.pk]))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]


def describe_orientation_block_book():
    def it_books_a_valid_start_inside_the_block(client: Client):
        user = _user_with_role("bb1")
        block, orientation_type = _block_with_type()
        start = block.starts_at + timedelta(minutes=30)
        client.login(username="bb1", password="pass")

        response = client.post(
            reverse("hub_orientation_block_book", args=[block.pk]),
            {"orientation_type": orientation_type.pk, "starts_at": _option_value(start), "note": "hi"},
        )

        assert response.status_code == 302
        booking = OrientationBooking.objects.get(member=user.member)
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert booking.slot.block_id == block.pk
        assert booking.member_note == "hi"

    def it_errors_when_the_time_was_just_taken(client: Client):
        _user_with_role("bb2")
        block, orientation_type = _block_with_type()
        start = block.starts_at + timedelta(minutes=30)
        orientations.request_block_orientation(block, MemberFactory(), start, orientation_type=orientation_type)
        client.login(username="bb2", password="pass")

        response = client.post(
            reverse("hub_orientation_block_book", args=[block.pk]),
            {"orientation_type": orientation_type.pk, "starts_at": _option_value(start), "note": ""},
            follow=True,
        )

        assert any("just taken" in str(m) for m in response.context["messages"])
        assert OrientationBooking.objects.count() == 1  # only the pre-existing booking

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_redirects_a_paid_type_into_checkout(mock_create, client: Client):
        user = _user_with_role("bb3")
        block, orientation_type = _block_with_type(price_cents=2000)
        client.login(username="bb3", password="pass")

        response = client.post(
            reverse("hub_orientation_block_book", args=[block.pk]),
            {"orientation_type": orientation_type.pk, "starts_at": _option_value(block.starts_at), "note": ""},
        )

        assert response.status_code == 302
        assert response["Location"] == _SESSION["url"]
        hold = OrientationBooking.objects.get(member=user.member)
        assert hold.status == OrientationBooking.Status.PENDING_PAYMENT

    def it_rejects_get_requests(client: Client):
        _user_with_role("bb4")
        block, _orientation_type = _block_with_type()
        client.login(username="bb4", password="pass")
        assert client.get(reverse("hub_orientation_block_book", args=[block.pk])).status_code == 405

    def it_errors_when_the_user_has_no_member(client: Client):
        user = _user_with_role("bb5")
        block, orientation_type = _block_with_type()
        client.force_login(user)
        Member.objects.filter(pk=user.member.pk).delete()
        response = client.post(
            reverse("hub_orientation_block_book", args=[block.pk]),
            {"orientation_type": orientation_type.pk, "starts_at": _option_value(block.starts_at), "note": ""},
        )
        assert response.status_code == 302
        assert not OrientationBooking.objects.exists()

    def it_errors_on_an_unparseable_submission(client: Client):
        _user_with_role("bb6")
        block, _orientation_type = _block_with_type()
        client.login(username="bb6", password="pass")
        response = client.post(
            reverse("hub_orientation_block_book", args=[block.pk]),
            {"orientation_type": "999999", "starts_at": "not-a-time", "note": ""},
            follow=True,
        )
        assert any("one of the listed times" in str(m) for m in response.context["messages"])
        assert not OrientationBooking.objects.exists()

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_surfaces_a_friendly_error_when_a_paid_time_was_just_taken(mock_create, client: Client):
        _user_with_role("bb7")
        block, orientation_type = _block_with_type(price_cents=2000)
        start = block.starts_at + timedelta(minutes=30)
        orientations.start_block_orientation_checkout(block, MemberFactory(), start, orientation_type=orientation_type)
        client.login(username="bb7", password="pass")
        response = client.post(
            reverse("hub_orientation_block_book", args=[block.pk]),
            {"orientation_type": orientation_type.pk, "starts_at": _option_value(start), "note": ""},
            follow=True,
        )
        assert any("just taken" in str(m) for m in response.context["messages"])
        assert OrientationBooking.objects.count() == 1  # only the pre-existing hold

    @patch("billing.stripe_utils.create_checkout_session", side_effect=RuntimeError("stripe down"))
    def it_reports_a_checkout_failure_without_leaving_a_slot(mock_create, client: Client):
        _user_with_role("bb8")
        block, orientation_type = _block_with_type(price_cents=2000)
        client.login(username="bb8", password="pass")
        response = client.post(
            reverse("hub_orientation_block_book", args=[block.pk]),
            {"orientation_type": orientation_type.pk, "starts_at": _option_value(block.starts_at), "note": ""},
            follow=True,
        )
        assert any("payment checkout" in str(m) for m in response.context["messages"])
        assert not OrientationBooking.objects.exists()
        assert not block.slots.exists()


def describe_orientation_block_cancel():
    def it_cancels_for_the_guild_lead(client: Client):
        user = _user_with_role("bc1")
        block = OrientationAvailabilityBlockFactory(guild=GuildFactory(guild_lead=user.member))
        client.login(username="bc1", password="pass")

        response = client.post(reverse("hub_orientation_block_cancel", args=[block.pk]))

        assert response.status_code == 302
        block.refresh_from_db()
        assert block.is_cancelled is True

    def it_forbids_a_regular_member(client: Client):
        _user_with_role("bc2")
        block = OrientationAvailabilityBlockFactory()
        client.login(username="bc2", password="pass")
        response = client.post(reverse("hub_orientation_block_cancel", args=[block.pk]))
        assert response.status_code == 403
        block.refresh_from_db()
        assert block.is_cancelled is False


def describe_guild_page_pick_a_time_section():
    def it_shows_the_section_when_a_type_has_free_room(client: Client):
        _user_with_role("gp1")
        block, orientation_type = _block_with_type()
        client.login(username="gp1", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[block.guild.slug])).content.decode()
        assert reverse("hub_orientation_block_starts", args=[block.pk, orientation_type.pk]) in content

    def it_hides_the_section_when_the_block_has_no_room_for_the_type(client: Client):
        _user_with_role("gp2")
        block, orientation_type = _block_with_type(duration_minutes=180)  # exactly one fit
        orientations.request_block_orientation(
            block, MemberFactory(), block.starts_at, orientation_type=orientation_type
        )
        client.login(username="gp2", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[block.guild.slug])).content.decode()
        assert reverse("hub_orientation_block_starts", args=[block.pk, orientation_type.pk]) not in content

    def it_hides_the_section_when_the_block_is_cancelled(client: Client):
        _user_with_role("gp3")
        block, orientation_type = _block_with_type()
        block.cancel()
        client.login(username="gp3", password="pass")
        content = client.get(reverse("hub_guild_detail", args=[block.guild.slug])).content.decode()
        assert reverse("hub_orientation_block_starts", args=[block.pk, orientation_type.pk]) not in content


def describe_orientations_dashboard_without_blocks():
    # Markup anchors, not the card's words: the changelog renders on every page, and a
    # fragment that mentions Availability Blocks would fail a copy assertion (STANDARDS.md §8).
    def it_renders_no_availability_blocks_card(client: Client):
        user = _user_with_role("nb1")
        guild = GuildFactory(guild_lead=user.member)
        OrientationAvailabilityBlockFactory(guild=guild, orienter=user.member)
        client.login(username="nb1", password="pass")
        content = client.get(reverse("hub_orientations_dashboard")).content.decode()
        assert 'data-help-key="orientation.availability-blocks"' not in content
        assert "/orientation/blocks/post/" not in content
        assert "Add a member to a slot" in content  # the page still ends with its own tools

    def it_has_no_post_route():
        with pytest.raises(NoReverseMatch):
            reverse("hub_orientation_block_post")

    def it_renders_for_an_admin_whose_member_row_is_gone(client: Client):
        user = _user_with_role("db3", fog_role=Member.FogRole.ADMIN)
        client.force_login(user)
        Member.objects.filter(pk=user.member.pk).delete()
        assert client.get(reverse("hub_orientations_dashboard")).status_code == 200
