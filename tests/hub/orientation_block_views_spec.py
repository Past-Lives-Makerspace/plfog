"""BDD specs for the availability-block views (issue #283): the HTMX start picker, member
booking, the dashboard post/cancel surfaces with their permission gates, and the guild-page
pick-a-time section."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership import orientations
from membership.models import Member, OrientationAvailabilityBlock, OrientationBooking
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
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


def describe_orientation_block_post():
    def _post_data(guild, *, days_out: int = 3) -> dict:
        day = timezone.localdate() + timedelta(days=days_out)
        return {
            "guild": guild.pk,
            "date": day.isoformat(),
            "start_time": "18:00",
            "end_time": "21:00",
            "location": "Woodshop",
        }

    def it_posts_a_block_for_a_guild_lead(client: Client):
        user = _user_with_role("bp1")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="bp1", password="pass")

        response = client.post(reverse("hub_orientation_block_post"), _post_data(guild))

        assert response.status_code == 302
        block = OrientationAvailabilityBlock.objects.get(guild=guild)
        assert block.orienter_id == user.member.pk
        assert block.location == "Woodshop"
        assert block.ends_at - block.starts_at == timedelta(hours=3)

    def it_posts_a_block_for_guild_staff(client: Client):
        user = _user_with_role("bp2")
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=user.member)
        client.login(username="bp2", password="pass")
        assert client.post(reverse("hub_orientation_block_post"), _post_data(guild)).status_code == 302
        assert OrientationAvailabilityBlock.objects.filter(guild=guild, orienter=user.member).exists()

    def it_forbids_a_regular_member(client: Client):
        _user_with_role("bp3")
        guild = GuildFactory()
        client.login(username="bp3", password="pass")
        response = client.post(reverse("hub_orientation_block_post"), _post_data(guild))
        assert response.status_code == 403
        assert not OrientationAvailabilityBlock.objects.exists()

    def it_rejects_a_guild_the_poster_does_not_staff(client: Client):
        user = _user_with_role("bp4")
        GuildFactory(guild_lead=user.member)  # they lead one guild, but post for another
        other = GuildFactory()
        client.login(username="bp4", password="pass")
        response = client.post(reverse("hub_orientation_block_post"), _post_data(other), follow=True)
        assert response.status_code == 200
        assert not OrientationAvailabilityBlock.objects.exists()

    def it_rejects_a_block_that_ends_before_it_starts(client: Client):
        user = _user_with_role("bp5")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="bp5", password="pass")
        data = _post_data(guild)
        data["start_time"] = "21:00"
        data["end_time"] = "18:00"
        response = client.post(reverse("hub_orientation_block_post"), data, follow=True)
        assert any("end after it starts" in str(m) for m in response.context["messages"])
        assert not OrientationAvailabilityBlock.objects.exists()

    def it_rejects_a_block_in_the_past(client: Client):
        user = _user_with_role("bp6")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="bp6", password="pass")
        response = client.post(reverse("hub_orientation_block_post"), _post_data(guild, days_out=-3), follow=True)
        assert any("in the future" in str(m) for m in response.context["messages"])
        assert not OrientationAvailabilityBlock.objects.exists()

    def it_rejects_a_submission_with_no_date(client: Client):
        user = _user_with_role("bp7")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="bp7", password="pass")
        data = _post_data(guild)
        del data["date"]
        response = client.post(reverse("hub_orientation_block_post"), data, follow=True)
        assert response.status_code == 200
        assert not OrientationAvailabilityBlock.objects.exists()

    def it_forbids_a_lead_whose_member_row_is_gone(client: Client):
        user = _user_with_role("bp8", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.force_login(user)
        Member.objects.filter(pk=user.member.pk).delete()
        response = client.post(reverse("hub_orientation_block_post"), _post_data(guild))
        assert response.status_code == 403
        assert not OrientationAvailabilityBlock.objects.exists()


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


def describe_orientations_dashboard_blocks_card():
    def it_lists_the_leaderships_upcoming_blocks_with_booked_segments(client: Client):
        user = _user_with_role("db1")
        guild = GuildFactory(guild_lead=user.member)
        block = OrientationAvailabilityBlockFactory(guild=guild, orienter=user.member)
        orientation_type = OrientationTypeFactory(guild=guild)
        booker = MemberFactory(full_legal_name="Zebulon Quartermain")
        orientations.request_block_orientation(
            block, booker, block.starts_at + timedelta(minutes=30), orientation_type=orientation_type
        )
        OrientationAvailabilityBlockFactory()  # someone else's guild — not listed for this lead
        client.login(username="db1", password="pass")

        content = client.get(reverse("hub_orientations_dashboard")).content.decode()

        assert reverse("hub_orientation_block_post") in content
        assert reverse("hub_orientation_block_cancel", args=[block.pk]) in content
        assert "Zebulon Quartermain" in content  # the booked segment lists who's inside
        other = OrientationAvailabilityBlock.objects.exclude(pk=block.pk).get()
        assert reverse("hub_orientation_block_cancel", args=[other.pk]) not in content

    def it_lists_every_upcoming_block_for_an_admin(client: Client):
        _user_with_role("db2", fog_role=Member.FogRole.ADMIN)
        block = OrientationAvailabilityBlockFactory()
        client.login(username="db2", password="pass")
        content = client.get(reverse("hub_orientations_dashboard")).content.decode()
        assert reverse("hub_orientation_block_cancel", args=[block.pk]) in content

    def it_renders_for_an_admin_whose_member_row_is_gone(client: Client):
        user = _user_with_role("db3", fog_role=Member.FogRole.ADMIN)
        client.force_login(user)
        Member.objects.filter(pk=user.member.pk).delete()
        # No member → no postable guilds, but the dashboard still renders.
        assert client.get(reverse("hub_orientations_dashboard")).status_code == 200
