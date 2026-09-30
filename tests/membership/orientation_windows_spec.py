"""BDD specs for open windows generated from recurring hours (issue #532, part 1): the open
row's shape and constraint, the one person one calendar overlap refusal, window generation
across the horizon and cadences, window retirement on pause, delete and re-grid, the orienter
wide busy check inside a window, and windows for one orientation only."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from membership import orientations
from membership.models import (
    GuildStaffMembership,
    OrientationAvailability,
    OrientationAvailabilityBlock,
    OrientationBooking,
    OrientationError,
    OrientationSlot,
)
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    OrientationAvailabilityBlockFactory,
    OrientationAvailabilityFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

OPEN = OrientationAvailability.BookingStyle.OPEN
FIXED = OrientationAvailability.BookingStyle.FIXED
SUNDAY = OrientationAvailability.Weekday.SUNDAY
MONDAY = OrientationAvailability.Weekday.MONDAY
TUESDAY = OrientationAvailability.Weekday.TUESDAY


def _local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime(year, month, day, hour, minute))


def _guild_with_orienter(*, enabled: bool = True) -> tuple[object, object, object]:
    """An orientation guild with one active type and Amber on staff as an orienter."""
    guild = GuildFactory(guild_lead=MemberFactory(full_legal_name="Lead Person"))
    GuildOrientationSettingsFactory(guild=guild, is_enabled=enabled)
    orientation_type = OrientationTypeFactory(guild=guild, name="Print Studio Orientation", duration_minutes=60)
    amber = MemberFactory(full_legal_name="Amber Capwell")
    GuildStaffMembershipFactory(guild=guild, member=amber, role=GuildStaffMembership.Role.ORIENTER)
    return guild, amber, orientation_type


def _open_row(guild: object, orienter: object, **overrides: object) -> OrientationAvailability:
    """An Any orientation open row, Sundays 11 to 6 unless told otherwise."""
    fields: dict[str, object] = {
        "guild": guild,
        "orienter": orienter,
        "booking_style": OPEN,
        "orientation_type": None,
        "weekday": SUNDAY,
        "start_time": time(11, 0),
        "end_time": time(18, 0),
    }
    fields.update(overrides)
    return OrientationAvailabilityFactory(**fields)


def _weekday_ahead(days: int) -> int:
    """The weekday ``days`` from today, so a spec never lands on an occurrence already under way."""
    return (timezone.localdate() + timedelta(days=days)).weekday()


def describe_open_row_shape():
    def it_refuses_a_fixed_row_with_no_type():
        guild, amber, _type = _guild_with_orienter()
        with pytest.raises(IntegrityError), transaction.atomic():
            OrientationAvailabilityFactory(guild=guild, orienter=amber, orientation_type=None)

    def it_refuses_an_any_orientation_open_row_with_no_orienter():
        guild, _amber, _type = _guild_with_orienter()
        with pytest.raises(IntegrityError), transaction.atomic():
            OrientationAvailabilityFactory(guild=guild, orienter=None, booking_style=OPEN, orientation_type=None)

    def it_accepts_an_any_orientation_open_row():
        guild, amber, _type = _guild_with_orienter()
        rule = _open_row(guild, amber)
        assert rule.is_open is True
        assert rule.type_display == "Any orientation"
        assert rule.style_display == "any time in the window"
        assert rule.owner_name == guild.name
        assert str(rule).startswith(f"{guild.name} orientation: Every Sunday 11:00")

    def it_names_the_type_on_a_typed_row():
        guild, amber, orientation_type = _guild_with_orienter()
        rule = _open_row(guild, amber, orientation_type=orientation_type)
        assert rule.type_display == "Print Studio Orientation"
        assert rule.owner_name == guild.name
        fixed = OrientationAvailabilityFactory(guild=guild, orienter=amber, orientation_type=orientation_type)
        assert fixed.style_display == "fixed start times"

    def it_offers_the_whole_window_as_one_span_even_with_a_stray_slot_length():
        guild, amber, _type = _guild_with_orienter()
        rule = OrientationAvailability(
            guild=guild,
            orienter=amber,
            booking_style=OPEN,
            weekday=SUNDAY,
            start_time=time(11, 0),
            end_time=time(18, 0),
        )
        rule.slot_minutes = 60
        assert rule.carve_spans(date(2026, 10, 4)) == [(_local(2026, 10, 4, 11), _local(2026, 10, 4, 18))]

    def it_accepts_the_open_shape_through_the_factory_trait():
        guild, _amber, _type = _guild_with_orienter()
        rule = OrientationAvailabilityFactory(guild=guild, open_window=True)
        assert rule.is_open and rule.orientation_type is None and rule.orienter is not None

    def describe_clean():
        def it_refuses_an_open_row_with_a_slot_length():
            guild, amber, _type = _guild_with_orienter()
            rule = _open_row(guild, amber)
            rule.slot_minutes = 60
            with pytest.raises(ValidationError) as excinfo:
                rule.clean()
            assert "slot_minutes" in excinfo.value.message_dict

        def it_refuses_an_open_row_with_no_orienter():
            guild, _amber, _type = _guild_with_orienter()
            rule = OrientationAvailability(
                guild=guild, booking_style=OPEN, weekday=SUNDAY, start_time=time(11, 0), end_time=time(18, 0)
            )
            with pytest.raises(ValidationError) as excinfo:
                rule.clean()
            assert excinfo.value.message_dict["booking_style"] == ["An open window needs a person."]

        def it_refuses_a_fixed_row_with_no_type():
            guild, amber, _type = _guild_with_orienter()
            rule = OrientationAvailability(
                guild=guild, orienter=amber, weekday=SUNDAY, start_time=time(11, 0), end_time=time(18, 0)
            )
            with pytest.raises(ValidationError) as excinfo:
                rule.clean()
            assert excinfo.value.message_dict["orientation_type"] == ["Pick an orientation."]

        def it_accepts_a_well_formed_open_row():
            guild, amber, _type = _guild_with_orienter()
            _open_row(guild, amber).clean()


def describe_overlap_refusal():
    def it_refuses_an_open_row_over_the_persons_fixed_hours():
        guild, amber, orientation_type = _guild_with_orienter()
        OrientationAvailabilityFactory(
            guild=guild,
            orienter=amber,
            orientation_type=orientation_type,
            weekday=MONDAY,
            start_time=time(11, 0),
            end_time=time(18, 0),
        )
        clash = _open_row(guild, amber, weekday=MONDAY, start_time=time(12, 0), end_time=time(15, 0))
        with pytest.raises(ValidationError) as excinfo:
            clash.clean()
        assert excinfo.value.message_dict["start_time"] == [
            "These hours overlap your Monday 11:00 AM–6:00 PM hours. One person can be booked one way at a time."
        ]

    def it_refuses_a_fixed_row_over_the_persons_open_hours():
        guild, amber, orientation_type = _guild_with_orienter()
        _open_row(guild, amber, weekday=MONDAY)
        clash = OrientationAvailabilityFactory(
            guild=guild,
            orienter=amber,
            orientation_type=orientation_type,
            weekday=MONDAY,
            start_time=time(12, 0),
            end_time=time(13, 0),
        )
        with pytest.raises(ValidationError):
            clash.clean()

    def it_lets_two_fixed_rows_overlap():
        guild, amber, orientation_type = _guild_with_orienter()
        other_type = OrientationTypeFactory(guild=guild, name="Sound System Orientation", duration_minutes=30)
        OrientationAvailabilityFactory(
            guild=guild, orienter=amber, orientation_type=orientation_type, weekday=MONDAY, slot_minutes=60
        )
        second = OrientationAvailabilityFactory(
            guild=guild, orienter=amber, orientation_type=other_type, weekday=MONDAY, slot_minutes=30
        )
        second.clean()
        assert second.overlapping_rule() is None

    def it_lets_open_rows_on_different_days_stand():
        guild, amber, _type = _guild_with_orienter()
        _open_row(guild, amber, weekday=MONDAY)
        _open_row(guild, amber, weekday=TUESDAY).clean()

    def it_lets_open_rows_that_touch_stand():
        guild, amber, _type = _guild_with_orienter()
        _open_row(guild, amber, weekday=MONDAY, start_time=time(11, 0), end_time=time(14, 0))
        _open_row(guild, amber, weekday=MONDAY, start_time=time(14, 0), end_time=time(18, 0)).clean()

    def it_looks_across_the_persons_guilds():
        guild, amber, orientation_type = _guild_with_orienter()
        other_guild = GuildFactory()
        GuildStaffMembershipFactory(guild=other_guild, member=amber, role=GuildStaffMembership.Role.ORIENTER)
        OrientationAvailabilityFactory(
            guild=other_guild,
            orienter=amber,
            orientation_type=OrientationTypeFactory(guild=other_guild),
            weekday=MONDAY,
            start_time=time(11, 0),
            end_time=time(18, 0),
        )
        with pytest.raises(ValidationError):
            _open_row(guild, amber, weekday=MONDAY).clean()

    def it_ignores_a_paused_row():
        guild, amber, _type = _guild_with_orienter()
        _open_row(guild, amber, weekday=MONDAY, is_active=False)
        _open_row(guild, amber, weekday=MONDAY).clean()

    def it_ignores_another_persons_hours():
        guild, amber, _type = _guild_with_orienter()
        lane = MemberFactory(full_legal_name="Lane Kidd")
        GuildStaffMembershipFactory(guild=guild, member=lane, role=GuildStaffMembership.Role.ORIENTER)
        _open_row(guild, lane, weekday=MONDAY)
        _open_row(guild, amber, weekday=MONDAY).clean()

    def it_does_not_clash_with_itself_on_edit():
        guild, amber, _type = _guild_with_orienter()
        rule = _open_row(guild, amber, weekday=MONDAY)
        rule.end_time = time(17, 0)
        rule.clean()

    def it_takes_no_part_when_the_row_is_paused_or_shared_or_has_no_times():
        guild, amber, orientation_type = _guild_with_orienter()
        _open_row(guild, amber, weekday=MONDAY)
        paused = _open_row(guild, amber, weekday=MONDAY, start_time=time(12, 0), end_time=time(13, 0), is_active=False)
        assert paused.overlapping_rule() is None
        shared = OrientationAvailability(
            guild=guild, orientation_type=orientation_type, weekday=MONDAY, start_time=time(12, 0), end_time=time(13, 0)
        )
        assert shared.overlapping_rule() is None
        blank = OrientationAvailability(guild=guild, orienter=amber, booking_style=OPEN, weekday=MONDAY)
        assert blank.overlapping_rule() is None

    def it_names_the_clashing_row():
        guild, amber, _type = _guild_with_orienter()
        first = _open_row(guild, amber, weekday=MONDAY)
        assert (
            _open_row(guild, amber, weekday=MONDAY, start_time=time(9, 0), end_time=time(12, 0)).overlapping_rule()
            == first
        )


def describe_window_generation():
    def it_materializes_one_window_per_occurrence_and_no_slots():
        guild, amber, _type = _guild_with_orienter()
        rule = _open_row(guild, amber, weekday=_weekday_ahead(3), location="Print Studio")
        assert orientations.generate_slots(guild=guild) == 8
        windows = list(rule.windows.order_by("starts_at"))
        assert len(windows) == 8
        assert rule.slots.count() == 0
        for window in windows:
            assert window.guild == guild
            assert window.orienter == amber
            assert window.orientation_type is None
            assert window.location == "Print Studio"
            assert timezone.localtime(window.starts_at).time() == time(11, 0)
            assert timezone.localtime(window.ends_at).time() == time(18, 0)
            assert window.is_cancelled is False

    def it_stays_idempotent():
        guild, amber, _type = _guild_with_orienter()
        rule = _open_row(guild, amber, weekday=_weekday_ahead(3))
        orientations.generate_slots(guild=guild)
        assert orientations.generate_slots(guild=guild) == 0
        assert rule.windows.count() == 8

    def it_refuses_two_windows_of_one_row_at_one_start():
        guild, amber, _type = _guild_with_orienter()
        rule = _open_row(guild, amber, weekday=_weekday_ahead(3))
        orientations.generate_slots(guild=guild)
        first = rule.windows.order_by("starts_at").first()
        with pytest.raises(IntegrityError), transaction.atomic():
            OrientationAvailabilityBlockFactory(
                guild=guild, orienter=amber, availability=rule, starts_at=first.starts_at, ends_at=first.ends_at
            )

    def it_carries_the_rows_type_onto_the_window():
        guild, amber, orientation_type = _guild_with_orienter()
        rule = _open_row(guild, amber, weekday=_weekday_ahead(3), orientation_type=orientation_type)
        orientations.generate_slots(guild=guild)
        assert {window.orientation_type for window in rule.windows.all()} == {orientation_type}

    def it_skips_an_occurrence_whose_start_already_passed():
        guild, amber, _type = _guild_with_orienter()
        _open_row(guild, amber, weekday=_weekday_ahead(3))
        reference = timezone.make_aware(datetime.combine(timezone.localdate() + timedelta(days=4), time(0, 0)))
        assert orientations.generate_slots(guild=guild, now=reference) == 7

    def it_still_materializes_slots_for_a_fixed_row():
        guild, amber, orientation_type = _guild_with_orienter()
        rule = OrientationAvailabilityFactory(
            guild=guild, orienter=amber, orientation_type=orientation_type, weekday=_weekday_ahead(3)
        )
        assert orientations.generate_slots(guild=guild) == 8
        assert rule.windows.count() == 0 and rule.slots.count() == 8

    def it_generates_nothing_while_the_guild_is_not_accepting():
        guild, amber, _type = _guild_with_orienter(enabled=False)
        _open_row(guild, amber, weekday=_weekday_ahead(3))
        assert orientations.generate_slots(guild=guild) == 0

    def it_generates_nothing_for_a_guild_with_no_settings_row():
        guild = GuildFactory()
        OrientationTypeFactory(guild=guild)
        amber = MemberFactory(full_legal_name="Amber Capwell")
        GuildStaffMembershipFactory(guild=guild, member=amber, role=GuildStaffMembership.Role.ORIENTER)
        _open_row(guild, amber, weekday=_weekday_ahead(3))
        assert orientations.generate_slots(guild=guild) == 0

    def it_generates_nothing_when_the_guild_has_no_active_type():
        guild, amber, orientation_type = _guild_with_orienter()
        orientation_type.is_active = False
        orientation_type.save(update_fields=["is_active"])
        _open_row(guild, amber, weekday=_weekday_ahead(3))
        assert orientations.generate_slots(guild=guild) == 0

    def it_generates_nothing_once_the_orienter_left_the_guilds_leadership():
        guild, amber, _type = _guild_with_orienter()
        _open_row(guild, amber, weekday=_weekday_ahead(3))
        GuildStaffMembership.objects.filter(guild=guild, member=amber).delete()
        assert orientations.generate_slots(guild=guild) == 0

    def describe_horizon():
        def it_yields_one_whole_window_per_sunday_across_dst():
            guild, amber, _type = _guild_with_orienter()
            rule = _open_row(guild, amber)
            spans = orientations._horizon_spans(rule, today=date(2026, 10, 1), window_weeks=8)
            assert [start.date() for start, _end in spans] == [
                date(2026, 10, 4),
                date(2026, 10, 11),
                date(2026, 10, 18),
                date(2026, 10, 25),
                date(2026, 11, 1),
                date(2026, 11, 8),
                date(2026, 11, 15),
                date(2026, 11, 22),
            ]
            assert all(timezone.localtime(start).time() == time(11, 0) for start, _end in spans)
            assert all(timezone.localtime(end).time() == time(18, 0) for _start, end in spans)

        def it_follows_every_other_week_from_the_start_day():
            guild, amber, _type = _guild_with_orienter()
            rule = _open_row(
                guild, amber, cadence=OrientationAvailability.Cadence.FORTNIGHTLY, anchor_date=date(2026, 10, 4)
            )
            spans = orientations._horizon_spans(rule, today=date(2026, 10, 1), window_weeks=8)
            assert [start.date() for start, _end in spans] == [
                date(2026, 10, 4),
                date(2026, 10, 18),
                date(2026, 11, 1),
                date(2026, 11, 15),
            ]

        def it_follows_a_monthly_ordinal():
            guild, amber, _type = _guild_with_orienter()
            rule = _open_row(
                guild, amber, cadence=OrientationAvailability.Cadence.MONTHLY, anchor_date=date(2026, 10, 11)
            )
            spans = orientations._horizon_spans(rule, today=date(2026, 10, 1), window_weeks=8)
            assert [start.date() for start, _end in spans] == [date(2026, 10, 11), date(2026, 11, 8)]


def _book_into(
    window: OrientationAvailabilityBlock, orientation_type: object, *, offset_minutes: int = 60
) -> OrientationBooking:
    start = window.starts_at + timedelta(minutes=offset_minutes)
    return orientations.request_block_orientation(window, MemberFactory(), start, orientation_type=orientation_type)


def describe_window_retirement():
    def it_deletes_every_empty_future_window_on_pause():
        guild, amber, _type = _guild_with_orienter()
        rule = _open_row(guild, amber, weekday=_weekday_ahead(3))
        orientations.generate_slots(guild=guild)
        assert orientations.retire_open_slots(rule) == (8, 0)
        assert rule.windows.count() == 0

    def it_keeps_a_booked_window_cancelled_and_detached():
        guild, amber, orientation_type = _guild_with_orienter()
        rule = _open_row(guild, amber, weekday=_weekday_ahead(3))
        orientations.generate_slots(guild=guild)
        booked = rule.windows.order_by("starts_at")[1]
        booking = _book_into(booked, orientation_type)
        assert orientations.retire_open_slots(rule) == (7, 1)
        booked.refresh_from_db()
        assert booked.is_cancelled is True
        assert booked.availability is None
        assert OrientationAvailabilityBlock.objects.filter(pk=booked.pk).exists()
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert booking.slot.source == OrientationSlot.Source.FROM_BLOCK
        assert booking.slot.is_cancelled is False
        assert rule.windows.count() == 0

    def it_regrids_when_the_day_changes():
        guild, amber, orientation_type = _guild_with_orienter()
        rule = _open_row(guild, amber, weekday=_weekday_ahead(3))
        orientations.generate_slots(guild=guild)
        old_windows = list(rule.windows.order_by("starts_at"))
        _book_into(old_windows[0], orientation_type)
        rule.weekday = _weekday_ahead(4)
        rule.save(update_fields=["weekday"])
        assert orientations.generate_slots(guild=guild) == 8
        assert {timezone.localtime(w.starts_at).weekday() for w in rule.windows.all()} == {rule.weekday}
        assert rule.windows.count() == 8
        old_windows[0].refresh_from_db()
        assert old_windows[0].is_cancelled is True and old_windows[0].availability is None
        assert not OrientationAvailabilityBlock.objects.filter(pk__in=[w.pk for w in old_windows[1:]]).exists()

    def it_retires_the_windows_when_the_row_is_deleted():
        guild, amber, orientation_type = _guild_with_orienter()
        rule = _open_row(guild, amber, weekday=_weekday_ahead(3))
        orientations.generate_slots(guild=guild)
        booked = rule.windows.order_by("starts_at")[2]
        _book_into(booked, orientation_type)
        assert orientations.retire_rule(rule) == (7, 1)
        assert not OrientationAvailability.objects.filter(pk=rule.pk).exists()
        assert OrientationAvailabilityBlock.objects.count() == 1
        booked.refresh_from_db()
        assert booked.is_cancelled is True

    def it_leaves_an_open_row_alone_on_a_seats_edit():
        guild, amber, _type = _guild_with_orienter()
        rule = _open_row(guild, amber, weekday=_weekday_ahead(3))
        orientations.generate_slots(guild=guild)
        orientations.reseat_slots(rule)
        assert rule.windows.count() == 8

    def it_marks_a_window_retired_in_one_save():
        window = OrientationAvailabilityBlockFactory()
        window.mark_retired()
        window.refresh_from_db()
        assert window.is_cancelled is True and window.availability is None
        assert not OrientationAvailabilityBlock.objects.upcoming().filter(pk=window.pk).exists()


def _window_for(guild: object, amber: object) -> OrientationAvailabilityBlock:
    day = timezone.localdate() + timedelta(days=2)
    return OrientationAvailabilityBlockFactory(
        guild=guild,
        orienter=amber,
        starts_at=timezone.make_aware(datetime.combine(day, time(11, 0))),
        ends_at=timezone.make_aware(datetime.combine(day, time(18, 0))),
    )


def _busy_slot(
    guild: object, amber: object, window: OrientationAvailabilityBlock, **overrides: object
) -> OrientationSlot:
    """A fixed slot of the same orienter from 12 to 1 inside the window's day, holding a REQUESTED booking."""
    fields: dict[str, object] = {
        "guild": guild,
        "orienter": amber,
        "starts_at": window.starts_at + timedelta(hours=1),
        "ends_at": window.starts_at + timedelta(hours=2),
    }
    fields.update(overrides)
    slot = OrientationSlotFactory(**fields)
    OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.REQUESTED)
    return slot


def describe_one_person_one_calendar_inside_a_window():
    def it_hides_starts_that_overlap_the_orienters_booked_fixed_slot():
        guild, amber, orientation_type = _guild_with_orienter()
        window = _window_for(guild, amber)
        _busy_slot(guild, amber, window)
        starts = window.valid_starts_for(orientation_type)
        assert window.starts_at in starts
        assert window.starts_at + timedelta(minutes=15) not in starts
        assert window.starts_at + timedelta(hours=1) not in starts
        assert window.starts_at + timedelta(hours=1, minutes=45) not in starts
        assert window.starts_at + timedelta(hours=2) in starts

    def it_refuses_the_start_at_booking_time():
        guild, amber, orientation_type = _guild_with_orienter()
        window = _window_for(guild, amber)
        _busy_slot(guild, amber, window)
        with pytest.raises(OrientationError, match="just taken"):
            window.ensure_start_valid(orientation_type, window.starts_at + timedelta(hours=1, minutes=15))

    def it_frees_the_starts_when_that_booking_is_cancelled():
        guild, amber, orientation_type = _guild_with_orienter()
        window = _window_for(guild, amber)
        slot = _busy_slot(guild, amber, window)
        for booking in slot.bookings.all():
            booking.cancel()
        assert window.starts_at + timedelta(hours=1) in window.valid_starts_for(orientation_type)

    def it_ignores_another_persons_slot():
        guild, amber, orientation_type = _guild_with_orienter()
        window = _window_for(guild, amber)
        _busy_slot(guild, MemberFactory(full_legal_name="Lane Kidd"), window)
        assert window.starts_at + timedelta(hours=1) in window.valid_starts_for(orientation_type)

    def it_ignores_a_cancelled_slot():
        guild, amber, orientation_type = _guild_with_orienter()
        window = _window_for(guild, amber)
        _busy_slot(guild, amber, window, is_cancelled=True)
        assert window.starts_at + timedelta(hours=1) in window.valid_starts_for(orientation_type)

    def it_counts_the_orienters_slot_in_another_guild():
        guild, amber, orientation_type = _guild_with_orienter()
        window = _window_for(guild, amber)
        _busy_slot(GuildFactory(), amber, window)
        assert window.starts_at + timedelta(hours=1) not in window.valid_starts_for(orientation_type)

    def it_still_counts_the_windows_own_segments():
        guild, amber, orientation_type = _guild_with_orienter()
        window = _window_for(guild, amber)
        _book_into(window, orientation_type, offset_minutes=120)
        starts = window.valid_starts_for(orientation_type)
        assert window.starts_at + timedelta(hours=2) not in starts
        assert window.starts_at + timedelta(hours=3) in starts


def describe_a_window_for_one_orientation_only():
    def it_offers_no_starts_to_another_orientation():
        guild, amber, orientation_type = _guild_with_orienter()
        other = OrientationTypeFactory(guild=guild, name="Intro to Cyanoprinting", duration_minutes=60)
        window = _window_for(guild, amber)
        window.orientation_type = orientation_type
        window.save(update_fields=["orientation_type"])
        assert window.valid_starts_for(other) == []
        assert window.valid_starts_for(orientation_type) != []
        assert window.type_display == "Print Studio Orientation"
        assert window.admits(orientation_type) and not window.admits(other)

    def it_refuses_another_orientation_at_booking_time():
        guild, amber, orientation_type = _guild_with_orienter()
        other = OrientationTypeFactory(guild=guild, name="Intro to Cyanoprinting", duration_minutes=60)
        window = _window_for(guild, amber)
        window.orientation_type = orientation_type
        window.save(update_fields=["orientation_type"])
        with pytest.raises(OrientationError, match="That window is for Print Studio Orientation only."):
            orientations.request_block_orientation(window, MemberFactory(), window.starts_at, orientation_type=other)
        assert OrientationSlot.objects.filter(block=window).count() == 0

    def it_says_any_orientation_when_untyped():
        assert OrientationAvailabilityBlockFactory().type_display == "Any orientation"
