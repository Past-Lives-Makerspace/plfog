"""BDD specs for the lead side of open windows (issue #532, part 2): the How members book choice
in the Edit Hours modal, saving an open row and its refusals, the Orientation Schedule lines, the
Upcoming Times card with windows and their booked segments, Add a one off in both shapes, and
cancelling a window from the tab."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership import orientations
from membership.models import (
    GuildStaffMembership,
    Member,
    OrientationAvailability,
    OrientationAvailabilityBlock,
    OrientationSlot,
)
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityBlockFactory,
    OrientationAvailabilityFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

OPEN = OrientationAvailability.BookingStyle.OPEN


def _lead(username: str) -> tuple[User, object]:
    """A logged-in-able lead of an orientation guild with one active type."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    user.member.full_legal_name = "Lead Person"
    user.member.save(update_fields=["full_legal_name"])
    guild = GuildFactory(guild_lead=user.member)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    OrientationTypeFactory(guild=guild, name="Print Studio Orientation", duration_minutes=60)
    return user, guild


def _staffer(guild: object, name: str = "Amber Capwell") -> Member:
    member = MemberFactory(full_legal_name=name)
    GuildStaffMembershipFactory(guild=guild, member=member, role=GuildStaffMembership.Role.ORIENTER)
    return member


def _weekday_ahead(days: int) -> str:
    return str((timezone.localdate() + timedelta(days=days)).weekday())


def _tab(guild: object) -> str:
    return f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations"


def _modal_payload(orienter: Member, **overrides: str) -> dict[str, str]:
    data = {
        "orienter_scope": str(orienter.pk),
        "formset_prefix": "modal_rules",
        "modal_rules-TOTAL_FORMS": "1",
        "modal_rules-INITIAL_FORMS": "0",
        "modal_rules-MIN_NUM_FORMS": "0",
        "modal_rules-MAX_NUM_FORMS": "1000",
        "modal_rules-0-booking_style": "open",
        "modal_rules-0-orientation_type": "",
        "modal_rules-0-weekday": _weekday_ahead(3),
        "modal_rules-0-start_time": "11:00",
        "modal_rules-0-end_time": "18:00",
        "modal_rules-0-seats": "4",
        "modal_rules-0-slot_minutes": "60",
        "modal_rules-0-is_active": "on",
    }
    data.update(overrides)
    return data


def describe_edit_hours_modal_choice():
    def it_offers_how_members_book_on_a_persons_rows(client: Client):
        user, guild = _lead("ch1")
        amber = _staffer(guild)
        client.login(username="ch1", password="pass")
        content = client.get(
            f"{reverse('hub_guild_orientation_hours_form', args=[guild.pk])}?orienter={amber.pk}"
        ).content.decode()
        assert 'data-help-key="orientation.how-members-book"' in content
        assert "Fixed start times" in content and "Any time in the window" in content
        assert 'x-model="style"' in content
        assert "Any orientation" in content  # the type select's empty choice
        assert "x-show=\"style === 'fixed'\"" in content

    def it_keeps_the_legacy_shared_rows_fixed(client: Client):
        user, guild = _lead("ch2")
        OrientationAvailabilityFactory(guild=guild)  # a shared Any orienter row
        client.login(username="ch2", password="pass")
        content = client.get(_tab(guild)).content.decode()
        assert "guild_rules-0-weekday" in content
        assert "guild_rules-0-booking_style" not in content


def describe_saving_an_open_row():
    def it_saves_the_row_and_materializes_windows(client: Client):
        user, guild = _lead("or1")
        amber = _staffer(guild)
        client.login(username="or1", password="pass")
        url = reverse("hub_guild_orientation_hours_save", args=[guild.pk])
        response = client.post(url, _modal_payload(amber), HTTP_HX_REQUEST="true")
        assert response.status_code == 204
        rule = OrientationAvailability.objects.get(guild=guild)
        assert rule.is_open and rule.orienter == amber and rule.orientation_type is None
        assert rule.slot_minutes is None  # the hidden fixed field's value was dropped
        assert rule.windows.count() == 8
        assert rule.slots.count() == 0

    def it_refuses_an_open_row_over_the_persons_fixed_hours_inside_the_modal(client: Client):
        user, guild = _lead("or2")
        amber = _staffer(guild)
        weekday = int(_weekday_ahead(3))
        OrientationAvailabilityFactory(
            guild=guild, orienter=amber, orientation_type=guild.orientation_types.first(), weekday=weekday
        )
        client.login(username="or2", password="pass")
        url = reverse("hub_guild_orientation_hours_save", args=[guild.pk])
        response = client.post(
            url,
            _modal_payload(amber, **{"modal_rules-0-start_time": "18:00", "modal_rules-0-end_time": "19:00"}),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b"One person can be booked one way at a time." in response.content
        assert OrientationAvailability.objects.filter(guild=guild).count() == 1

    def it_refuses_a_fixed_row_left_at_any_orientation(client: Client):
        user, guild = _lead("or3")
        amber = _staffer(guild)
        client.login(username="or3", password="pass")
        url = reverse("hub_guild_orientation_hours_save", args=[guild.pk])
        response = client.post(
            url, _modal_payload(amber, **{"modal_rules-0-booking_style": "fixed"}), HTTP_HX_REQUEST="true"
        )
        assert response.status_code == 200
        assert b"Pick an orientation." in response.content
        assert not OrientationAvailability.objects.filter(guild=guild).exists()

    def it_refuses_open_on_the_shared_guild_scope(client: Client):
        user, guild = _lead("or4")
        shared = OrientationAvailabilityFactory(guild=guild)
        client.login(username="or4", password="pass")
        url = reverse("hub_guild_orientation_hours_save", args=[guild.pk])
        data = {
            "orienter_scope": "",
            "guild_rules-TOTAL_FORMS": "1",
            "guild_rules-INITIAL_FORMS": "1",
            "guild_rules-MIN_NUM_FORMS": "0",
            "guild_rules-MAX_NUM_FORMS": "1000",
            "guild_rules-0-id": str(shared.pk),
            "guild_rules-0-booking_style": "open",
            "guild_rules-0-orientation_type": str(shared.orientation_type_id),
            "guild_rules-0-weekday": str(shared.weekday),
            "guild_rules-0-start_time": "18:00",
            "guild_rules-0-end_time": "19:00",
            "guild_rules-0-seats": "4",
            "guild_rules-0-is_active": "on",
        }
        response = client.post(url, data)
        assert response.status_code == 200
        # The legacy card never renders the field, so the refusal lives on the bound formset.
        errors = response.context["guild_rule_formset"].errors[0]
        assert errors["booking_style"] == ["Any time in the window is for a person's own hours in a guild."]
        shared.refresh_from_db()
        assert shared.is_open is False


def describe_orientation_schedule_lines():
    def it_names_the_style_only_for_a_person_who_mixes_them(client: Client):
        user, guild = _lead("sl1")
        amber = _staffer(guild)
        lane = _staffer(guild, "Lane Kidd")
        orientation_type = guild.orientation_types.first()
        OrientationAvailabilityFactory(guild=guild, orienter=amber, orientation_type=orientation_type, weekday=0)
        OrientationAvailabilityFactory(
            guild=guild, orienter=amber, booking_style=OPEN, orientation_type=None, weekday=6
        )
        OrientationAvailabilityFactory(guild=guild, orienter=lane, orientation_type=orientation_type, weekday=1)
        client.login(username="sl1", password="pass")
        content = client.get(_tab(guild)).content.decode()
        assert "Any orientation · Every Sunday" in content
        assert content.count("any time in the window</span>") == 1
        assert content.count("fixed start times</span>") == 1  # Amber's fixed row only, not Lane's


def _window_for(guild: object, orienter: Member) -> OrientationAvailabilityBlock:
    day = timezone.localdate() + timedelta(days=2)
    start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time())) + timedelta(hours=11)
    return OrientationAvailabilityBlockFactory(
        guild=guild, orienter=orienter, starts_at=start, ends_at=start + timedelta(hours=7)
    )


def describe_upcoming_times_card():
    def it_lists_a_window_with_its_booked_segments_and_not_the_carved_slot(client: Client):
        user, guild = _lead("ut1")
        amber = _staffer(guild)
        window = _window_for(guild, amber)
        orientation_type = guild.orientation_types.first()
        booker = MemberFactory(full_legal_name="Zebulon Quartermain")
        booking = orientations.request_block_orientation(
            window, booker, window.starts_at + timedelta(hours=1), orientation_type=orientation_type
        )
        client.login(username="ut1", password="pass")
        content = client.get(_tab(guild)).content.decode()
        assert "Upcoming Times" in content
        assert "open window · one off" in content
        assert "1 booked" in content
        assert "Zebulon Quartermain" in content and "requested" in content
        assert reverse("hub_orientation_block_cancel", args=[window.pk]) in content
        assert reverse("hub_guild_orientation_slot_cancel", args=[guild.pk, booking.slot.pk]) not in content

    def it_lists_a_recurring_window_and_an_empty_one(client: Client):
        user, guild = _lead("ut2")
        amber = _staffer(guild)
        rule = OrientationAvailabilityFactory(
            guild=guild, orienter=amber, booking_style=OPEN, orientation_type=None, weekday=int(_weekday_ahead(3))
        )
        orientations.generate_slots(guild=guild)
        client.login(username="ut2", password="pass")
        content = client.get(_tab(guild)).content.decode()
        assert "open window · recurring" in content
        assert "nothing booked yet" in content
        assert rule.windows.count() == 8

    def it_shows_the_empty_state(client: Client):
        user, guild = _lead("ut3")
        client.login(username="ut3", password="pass")
        assert b"No upcoming times yet." in client.get(_tab(guild)).content


def _oneoff(guild: object, **overrides: str) -> dict[str, str]:
    data = {
        "booking_style": "open",
        "orientation_type": "",
        "date": (timezone.localdate() + timedelta(days=3)).isoformat(),
        "start_time": "18:00",
        "end_time": "21:00",
        "duration_minutes": "60",
        "seats": "3",
        "location": "Print Studio",
    }
    data.update(overrides)
    return data


def describe_add_a_one_off_open_window():
    def it_posts_an_open_window_for_the_chosen_person(client: Client):
        user, guild = _lead("oo1")
        amber = _staffer(guild)
        client.login(username="oo1", password="pass")
        url = reverse("hub_guild_orientation_slot_add", args=[guild.pk])
        response = client.post(url, _oneoff(guild, orienter=str(amber.pk)))
        assert response.status_code == 302
        window = OrientationAvailabilityBlock.objects.get(guild=guild)
        assert window.orienter == amber and window.orientation_type is None and window.availability is None
        assert window.ends_at - window.starts_at == timedelta(hours=3)
        assert window.location == "Print Studio"
        assert not OrientationSlot.objects.filter(guild=guild).exists()

    def it_carries_one_orientation_when_chosen(client: Client):
        user, guild = _lead("oo2")
        amber = _staffer(guild)
        orientation_type = guild.orientation_types.first()
        client.login(username="oo2", password="pass")
        url = reverse("hub_guild_orientation_slot_add", args=[guild.pk])
        client.post(url, _oneoff(guild, orienter=str(amber.pk), orientation_type=str(orientation_type.pk)))
        assert OrientationAvailabilityBlock.objects.get(guild=guild).orientation_type == orientation_type

    def it_forces_a_plain_staffer_onto_their_own_window(client: Client):
        MembershipPlanFactory()
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        OrientationTypeFactory(guild=guild)
        user = User.objects.create_user(username="oo3", password="pass")
        GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
        alice = _staffer(guild, "Alice Ash")
        client.login(username="oo3", password="pass")
        client.post(reverse("hub_guild_orientation_slot_add", args=[guild.pk]), _oneoff(guild, orienter=str(alice.pk)))
        assert OrientationAvailabilityBlock.objects.get(guild=guild).orienter == user.member

    def it_needs_a_person(client: Client):
        user, guild = _lead("oo4")
        client.login(username="oo4", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]), _oneoff(guild, orienter=""), follow=True
        )
        joined = " ".join(str(m) for m in response.context["messages"])
        assert "An open window needs a person." in joined
        assert not OrientationAvailabilityBlock.objects.exists()

    def it_needs_an_end_after_its_start(client: Client):
        user, guild = _lead("oo5")
        amber = _staffer(guild)
        client.login(username="oo5", password="pass")
        url = reverse("hub_guild_orientation_slot_add", args=[guild.pk])
        response = client.post(url, _oneoff(guild, orienter=str(amber.pk), end_time=""), follow=True)
        assert "Pick when the window ends." in " ".join(str(m) for m in response.context["messages"])
        response = client.post(url, _oneoff(guild, orienter=str(amber.pk), end_time="17:00"), follow=True)
        assert "end after it starts" in " ".join(str(m) for m in response.context["messages"])
        assert not OrientationAvailabilityBlock.objects.exists()

    def it_refuses_a_fixed_one_off_left_at_any_orientation(client: Client):
        user, guild = _lead("oo6")
        client.login(username="oo6", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]),
            _oneoff(guild, booking_style="fixed", orienter=""),
            follow=True,
        )
        assert "Pick an orientation." in " ".join(str(m) for m in response.context["messages"])
        assert not OrientationSlot.objects.filter(guild=guild).exists()

    def it_refuses_a_fixed_one_off_with_no_duration(client: Client):
        user, guild = _lead("oo7")
        client.login(username="oo7", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]),
            _oneoff(
                guild,
                booking_style="fixed",
                orienter="",
                orientation_type=str(guild.orientation_types.first().pk),
                duration_minutes="",
            ),
            follow=True,
        )
        assert "Pick how long it runs." in " ".join(str(m) for m in response.context["messages"])

    def it_saves_a_fixed_one_off_with_seats_left_blank(client: Client):
        user, guild = _lead("oo8")
        client.login(username="oo8", password="pass")
        client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]),
            _oneoff(
                guild,
                booking_style="fixed",
                orienter="",
                orientation_type=str(guild.orientation_types.first().pk),
                seats="",
            ),
        )
        assert OrientationSlot.objects.get(guild=guild).seats == 4


def describe_one_off_overlap_with_the_persons_calendar():
    def it_refuses_any_one_off_over_the_persons_open_window(client: Client):
        user, guild = _lead("ov1")
        amber = _staffer(guild)
        day = timezone.localdate() + timedelta(days=3)
        start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time())) + timedelta(hours=18)
        OrientationAvailabilityBlockFactory(
            guild=guild, orienter=amber, starts_at=start, ends_at=start + timedelta(hours=3)
        )
        client.login(username="ov1", password="pass")
        url = reverse("hub_guild_orientation_slot_add", args=[guild.pk])
        response = client.post(
            url,
            _oneoff(
                guild,
                booking_style="fixed",
                orienter=str(amber.pk),
                orientation_type=str(guild.orientation_types.first().pk),
                start_time="19:00",
            ),
            follow=True,
        )
        assert "open window that day" in " ".join(str(m) for m in response.context["messages"])
        assert not OrientationSlot.objects.filter(guild=guild).exists()

    def it_refuses_an_open_one_off_over_the_persons_fixed_time(client: Client):
        user, guild = _lead("ov2")
        amber = _staffer(guild)
        day = timezone.localdate() + timedelta(days=3)
        start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time())) + timedelta(hours=19)
        OrientationSlotFactory(guild=guild, orienter=amber, starts_at=start, ends_at=start + timedelta(hours=1))
        client.login(username="ov2", password="pass")
        response = client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]),
            _oneoff(guild, orienter=str(amber.pk)),
            follow=True,
        )
        assert "fixed times that day" in " ".join(str(m) for m in response.context["messages"])
        assert not OrientationAvailabilityBlock.objects.exists()

    def it_lets_a_fixed_one_off_sit_over_the_persons_fixed_time(client: Client):
        user, guild = _lead("ov3")
        amber = _staffer(guild)
        day = timezone.localdate() + timedelta(days=3)
        start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time())) + timedelta(hours=18)
        OrientationSlotFactory(guild=guild, orienter=amber, starts_at=start, ends_at=start + timedelta(hours=1))
        client.login(username="ov3", password="pass")
        client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]),
            _oneoff(
                guild,
                booking_style="fixed",
                orienter=str(amber.pk),
                orientation_type=str(guild.orientation_types.first().pk),
            ),
        )
        assert OrientationSlot.objects.filter(guild=guild).count() == 2


def describe_window_cancel_from_the_tab():
    def it_cancels_and_returns_to_the_orientations_tab(client: Client):
        user, guild = _lead("wc1")
        window = OrientationAvailabilityBlockFactory(guild=guild, orienter=user.member)
        client.login(username="wc1", password="pass")
        response = client.post(reverse("hub_orientation_block_cancel", args=[window.pk]))
        assert response.status_code == 302
        assert response["Location"] == _tab(guild)
        window.refresh_from_db()
        assert window.is_cancelled is True
