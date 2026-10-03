"""BDD specs for cancelling several Upcoming Times at once (issue #574): the bulk cancel view
(the form that sorts the keys, the service that cancels, foreign and stale keys skipped), its
gate, and the card's selection markup."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.http import HttpResponse, QueryDict
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from hub.forms import OrientationTimesBulkCancelForm
from membership import orientations
from membership.models import Guild, OrientationBooking, OrientationSlot
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityBlockFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _lead(username: str) -> tuple[User, Guild]:
    """A logged-in-able lead of an orientation guild with one active type."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    guild = GuildFactory(guild_lead=user.member)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    OrientationTypeFactory(guild=guild, name="Print Studio Orientation", duration_minutes=60)
    return user, guild


def _slot(guild: Guild, *, days: int = 2) -> OrientationSlot:
    start = timezone.now() + timedelta(days=days)
    return OrientationSlotFactory(
        guild=guild,
        orientation_type=guild.orientation_types.first(),
        starts_at=start,
        ends_at=start + timedelta(hours=1),
    )


def _tab(guild: Guild) -> str:
    return f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations"


def _bulk_url(guild: Guild) -> str:
    return reverse("hub_guild_orientation_times_bulk_cancel", args=[guild.pk])


def _messages(response: HttpResponse) -> str:
    return " ".join(str(m) for m in response.context["messages"])


def describe_bulk_cancel():
    def it_cancels_slots_through_cancel_slot_and_windows_through_cancel(client: Client):
        user, guild = _lead("bc1")
        booked, empty = _slot(guild), _slot(guild, days=3)
        OrientationBookingFactory(slot=booked, status=OrientationBooking.Status.CONFIRMED)
        OrientationBookingFactory(slot=booked, status=OrientationBooking.Status.REQUESTED)
        window = OrientationAvailabilityBlockFactory(guild=guild, orienter=user.member)
        client.login(username="bc1", password="pass")
        with patch("membership.orientations.cancel_slot") as cancel_slot:
            response = client.post(
                _bulk_url(guild),
                {"selected": [f"slot:{booked.pk}", f"slot:{empty.pk}", f"window:{window.pk}"]},
                follow=True,
            )
        assert response.redirect_chain[-1][0] == _tab(guild)
        assert sorted(call.args[0].pk for call in cancel_slot.call_args_list) == sorted([booked.pk, empty.pk])
        window.refresh_from_db()
        assert window.is_cancelled is True
        assert "Cancelled 3 upcoming times. 2 booked members were emailed." in _messages(response)

    def it_really_cancels_the_rows_and_their_bookings(client: Client):
        user, guild = _lead("bc2")
        slot = _slot(guild)
        booking = OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        client.login(username="bc2", password="pass")
        response = client.post(_bulk_url(guild), {"selected": [f"slot:{slot.pk}"]}, follow=True)
        slot.refresh_from_db()
        booking.refresh_from_db()
        assert slot.is_cancelled is True
        assert booking.status == OrientationBooking.Status.CANCELLED
        assert "Cancelled 1 upcoming time. 1 booked member was emailed." in _messages(response)

    def it_skips_a_foreign_pk_a_cancelled_slot_and_a_nonsense_value(client: Client):
        user, guild = _lead("bc3")
        mine = _slot(guild)
        other_guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=other_guild, is_enabled=True)
        foreign = OrientationSlotFactory(guild=other_guild)
        gone = _slot(guild, days=4)
        gone.mark_cancelled()
        client.login(username="bc3", password="pass")
        response = client.post(
            _bulk_url(guild),
            {"selected": [f"slot:{foreign.pk}", f"slot:{gone.pk}", "slot:abc", "banana", f"slot:{mine.pk}"]},
            follow=True,
        )
        assert response.status_code == 200
        mine.refresh_from_db()
        foreign.refresh_from_db()
        assert mine.is_cancelled is True
        assert foreign.is_cancelled is False
        assert "Cancelled 1 upcoming time." in _messages(response)
        assert "emailed" not in _messages(response)

    def it_says_so_when_nothing_matched(client: Client):
        user, guild = _lead("bc4")
        other_guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=other_guild, is_enabled=True)
        foreign_window = OrientationAvailabilityBlockFactory(guild=other_guild)
        client.login(username="bc4", password="pass")
        with patch("membership.orientations.cancel_slot") as cancel_slot:
            response = client.post(
                _bulk_url(guild), {"selected": [f"window:{foreign_window.pk}", "window:9999999"]}, follow=True
            )
        assert response.redirect_chain[-1][0] == _tab(guild)
        cancel_slot.assert_not_called()
        foreign_window.refresh_from_db()
        assert foreign_window.is_cancelled is False
        assert "Nothing to cancel." in _messages(response)

    def it_skips_a_slot_carved_out_of_a_live_window(client: Client):
        user, guild = _lead("bc9")
        later = timezone.now() + timedelta(days=5)
        window = OrientationAvailabilityBlockFactory(
            guild=guild, orienter=user.member, starts_at=later, ends_at=later + timedelta(hours=3)
        )
        booking = orientations.request_block_orientation(
            window, MemberFactory(), later + timedelta(hours=1), orientation_type=guild.orientation_types.first()
        )
        client.login(username="bc9", password="pass")
        response = client.post(_bulk_url(guild), {"selected": [f"slot:{booking.slot.pk}"]}, follow=True)
        booking.refresh_from_db()
        booking.slot.refresh_from_db()
        assert booking.slot.source == OrientationSlot.Source.FROM_BLOCK
        assert booking.slot.is_cancelled is False
        assert booking.status != OrientationBooking.Status.CANCELLED
        assert "Nothing to cancel." in _messages(response)

    def it_forbids_a_member_who_cannot_manage_orientations(client: Client):
        user, guild = _lead("bc5")
        slot = _slot(guild)
        User.objects.create_user(username="bc5_member", password="pass")
        client.login(username="bc5_member", password="pass")
        response = client.post(_bulk_url(guild), {"selected": [f"slot:{slot.pk}"]})
        assert response.status_code == 403
        slot.refresh_from_db()
        assert slot.is_cancelled is False

    def it_rejects_get_requests(client: Client):
        user, guild = _lead("bc6")
        client.login(username="bc6", password="pass")
        assert client.get(_bulk_url(guild)).status_code == 405


def describe_the_upcoming_times_card():
    def it_renders_a_pick_box_per_row_the_bar_and_the_hidden_form(client: Client):
        user, guild = _lead("bc7")
        slot = _slot(guild)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        later = timezone.now() + timedelta(days=5)
        window = OrientationAvailabilityBlockFactory(
            guild=guild, orienter=user.member, starts_at=later, ends_at=later + timedelta(hours=3)
        )
        client.login(username="bc7", password="pass")
        content = client.get(_tab(guild)).content.decode()
        assert content.count('class="pl-slot-admin__pick"') == 2
        assert f'data-time-key="slot:{slot.pk}" data-time-index="0" data-emails="1"' in content
        assert f'data-time-key="window:{window.pk}" data-time-index="1" data-emails="0"' in content
        assert 'class="pl-slot-admin__bar"' in content
        assert "x-text=\"selected.length + ' selected'\"" in content
        assert f'id="times-bulk-form" method="post" action="{_bulk_url(guild)}"' in content
        assert (
            '<template x-for="key in selected" :key="key"><input type="hidden" name="selected" :value="key">' in content
        )
        assert 'x-text="bulkMessage()"' in content
        assert "onclick=\"document.getElementById('times-bulk-form').requestSubmit();\"" in content

    def it_renders_none_of_it_without_upcoming_times(client: Client):
        user, guild = _lead("bc8")
        client.login(username="bc8", password="pass")
        content = client.get(_tab(guild)).content.decode()
        assert "pl-slot-admin__pick" not in content
        assert "times-bulk-form" not in content


def describe_the_bulk_cancel_form():
    def it_sorts_slot_and_window_keys():
        form = OrientationTimesBulkCancelForm(QueryDict("selected=slot:4&selected=window:9&selected=slot:12"))
        assert form.is_valid()
        assert form.cleaned_data["slot_pks"] == [4, 12]
        assert form.cleaned_data["window_pks"] == [9]

    def it_ignores_anything_that_is_not_a_key():
        form = OrientationTimesBulkCancelForm(
            QueryDict("selected=slot:abc&selected=banana&selected=slot:&selected=window:7x&selected=window:3")
        )
        assert form.is_valid()
        assert form.cleaned_data["slot_pks"] == []
        assert form.cleaned_data["window_pks"] == [3]

    def it_is_valid_and_empty_with_no_selection():
        form = OrientationTimesBulkCancelForm(QueryDict(""))
        assert form.is_valid()
        assert form.cleaned_data["slot_pks"] == []
        assert form.cleaned_data["window_pks"] == []
