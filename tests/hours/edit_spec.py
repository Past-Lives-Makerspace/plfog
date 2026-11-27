"""Changing one set of hours, every kind together (#1300, part 4a): repeats for tours, dates for orientations, HoursEdit."""

from __future__ import annotations

from datetime import date, time, timedelta
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from hours.edit import (
    TIME,
    TOURS,
    HoursEdit,
    OrientationOffer,
    Owner,
    TourOffer,
    gives_tours,
    held_owners,
    owner_key,
)
from hours.models import Hours, TimeKey
from membership import orientations
from membership.models import Member, OrientationAvailability, OrientationSlot
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    OrientationAvailabilityFactory,
    OrientationBookingFactory,
    OrientationTypeFactory,
)
from tests.tours.conftest import make_guide
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours.models import TourBooking, TourHours

pytestmark = pytest.mark.django_db

WEEKLY = OrientationAvailability.Cadence.WEEKLY
FIXED = OrientationAvailability.BookingStyle.FIXED
OPEN = OrientationAvailability.BookingStyle.OPEN


def _next(weekday: int, *, weeks: int = 1) -> date:
    """The ``weekday`` at least a week ahead (inside generation's eight week window)."""
    today = timezone.localdate()
    return today + timedelta(days=(weekday - today.weekday()) % 7 + 7 * weeks)


def _key(*, day: date | None = None, weekday: int = 1, start: int = 16, end: int = 18, **repeat: Any) -> TimeKey:
    return TimeKey(
        date=day,
        weekday=day.weekday() if day is not None else weekday,
        cadence=repeat.get("cadence", WEEKLY),
        anchor_date=repeat.get("anchor_date"),
        last_of_month=repeat.get("last_of_month", False),
        start_time=time(start) if isinstance(start, int) else start,
        end_time=time(end) if isinstance(end, int) else end,
    )


def _orientor(**kwargs: Any) -> tuple[Member, Owner]:
    """A guild's lead, with one active orientation and orientations switched on."""
    person = make_guide(**kwargs)
    guild = GuildFactory(guild_lead=person)
    GuildOrientationSettingsFactory(guild=guild)
    OrientationTypeFactory(guild=guild, name="Intro")
    return person, Owner(owner_key(guild.pk, None), guild, None)


def _offer(owner: Owner, **kwargs: Any) -> OrientationOffer:
    values: dict[str, Any] = {"on": True, "orientation_type": owner.types().first()}
    values.update(kwargs)
    return OrientationOffer(owner=owner, **values)


def _save(edit: HoursEdit) -> Hours:
    with transaction.atomic():
        return edit.save()


def describe_tours_on_a_cadence():
    def it_offers_tours_every_other_week_from_the_start_day():
        start = _next(1)
        row = TourHoursFactory(cadence=OrientationAvailability.Cadence.FORTNIGHTLY, anchor_date=start)
        assert row.applies_on(start)
        assert not row.applies_on(start + timedelta(days=7))
        assert row.applies_on(start + timedelta(days=14))
        assert not row.applies_on(start - timedelta(days=14))

    def it_needs_a_start_day_on_its_weekday():
        row = TourHoursFactory.build(guide=MemberFactory(), cadence=OrientationAvailability.Cadence.MONTHLY)
        with pytest.raises(ValidationError) as missing:
            row.full_clean(exclude=["hours"])
        assert missing.value.message_dict["anchor_date"] == ["Pick the day these hours start."]
        row.anchor_date = _next(2)
        with pytest.raises(ValidationError) as wrong_day:
            row.full_clean(exclude=["hours"])
        assert "must be a Tuesday" in wrong_day.value.message_dict["anchor_date"][0]

    def it_never_puts_a_dated_row_on_a_cadence():
        day = _next(5)
        with pytest.raises(IntegrityError), transaction.atomic():
            TourHoursFactory(
                date=day, weekday=day.weekday(), cadence=OrientationAvailability.Cadence.MONTHLY, anchor_date=day
            )

    def it_groups_with_orientation_hours_on_the_same_cadence():
        person = MemberFactory()
        start = _next(1)
        tour = TourHoursFactory(guide=person, cadence=OrientationAvailability.Cadence.FORTNIGHTLY, anchor_date=start)
        rule = OrientationAvailabilityFactory(
            orienter=person,
            start_time=time(16),
            end_time=time(18),
            cadence=OrientationAvailability.Cadence.FORTNIGHTLY,
            anchor_date=start,
        )
        tour.refresh_from_db()
        assert tour.hours_id == rule.hours_id
        assert Hours.objects.get().repeat_label.startswith("Every other week")


def describe_orientations_on_one_date():
    def it_yields_its_one_day_only():
        day = _next(5)
        rule = OrientationAvailabilityFactory(date=day, weekday=day.weekday())
        assert rule.occurs_on(day)
        assert not rule.occurs_on(day + timedelta(days=7))
        assert rule.cadence_display == f"{day:%a, %b} {day.day}"

    def it_keeps_the_dates_weekday_and_no_cadence():
        day = _next(5)
        rule = OrientationAvailabilityFactory.build(date=day, weekday=1, orienter=MemberFactory())
        with pytest.raises(ValidationError) as wrong_day:
            rule.full_clean(exclude=["hours"])
        assert wrong_day.value.message_dict["weekday"] == [f"{day:%b} {day.day} is not a Tuesday."]
        rule.weekday = day.weekday()
        rule.cadence = OrientationAvailability.Cadence.MONTHLY
        with pytest.raises(ValidationError) as repeats:
            rule.full_clean(exclude=["hours"])
        assert repeats.value.message_dict["cadence"] == ["Hours on one date do not repeat."]
        with pytest.raises(IntegrityError), transaction.atomic():
            OrientationAvailabilityFactory(date=day, weekday=1)

    def it_generates_slots_on_its_date_only():
        person, owner = _orientor()
        day = _next(5)
        rule = OrientationAvailabilityFactory(
            guild=owner.guild,
            orientation_type=owner.types().first(),
            orienter=person,
            date=day,
            weekday=day.weekday(),
        )
        orientations.generate_slots(guild=owner.guild)
        assert [timezone.localtime(slot.starts_at).date() for slot in rule.slots.all()] == [day]

    def it_clashes_with_its_weekdays_repeating_hours_but_not_with_another_date():
        person = MemberFactory()
        day = _next(5)
        OrientationAvailabilityFactory(orienter=person, weekday=day.weekday(), open_window=True)
        other_date = OrientationAvailabilityFactory(
            orienter=person, date=day + timedelta(days=7), weekday=day.weekday(), open_window=True
        )
        rule = OrientationAvailabilityFactory.build(
            orienter=person, date=day, weekday=day.weekday(), booking_style=OPEN, orientation_type=None
        )
        clash = rule.overlapping_rule()
        assert clash is not None and clash.date is None
        assert clash.pk != other_date.pk


def describe_who_offers_what():
    def it_names_tour_guides_and_admins_as_giving_tours():
        assert gives_tours(make_guide())
        assert gives_tours(MemberFactory(fog_role=Member.FogRole.ADMIN))
        assert not gives_tours(MemberFactory())

    def it_offers_the_guilds_and_tools_someone_orients_for_by_name():
        person = MemberFactory()
        led = GuildFactory(name="Zinc Guild", guild_lead=person)
        OrientationTypeFactory(guild=led, name="Zinc basics")
        staffed = GuildFactory(name="Bead Guild")
        GuildStaffMembershipFactory(guild=staffed, member=person)
        OrientationTypeFactory(guild=staffed, name="Beads")
        tool = EquipmentFactory(name="Kiln")
        EquipmentStaffMembershipFactory(equipment=tool, member=person)
        OrientationTypeFactory(guild=None, equipment=tool, name="Kiln basics")
        guild_tool = EquipmentFactory(name="Lathe", guild=led)
        OrientationTypeFactory(guild=None, equipment=guild_tool, name="Lathe basics")
        quiet = GuildFactory(name="Quiet Guild", guild_lead=person)
        OrientationTypeFactory(guild=quiet, name="Retired", is_active=False)
        owners = held_owners(person)
        assert [owner.label for owner in owners] == [
            "Bead Guild orientations",
            "Kiln orientations",
            "Lathe orientations",
            "Zinc Guild orientations",
        ]
        assert [owner.key for owner in owners] == [
            f"guild-{staffed.pk}",
            f"equipment-{tool.pk}",
            f"equipment-{guild_tool.pk}",
            f"guild-{led.pk}",
        ]
        assert list(owners[1].types()) == list(tool.owned_orientation_types.all())

    def it_offers_nothing_to_someone_no_longer_active():
        person = MemberFactory(status=Member.Status.FORMER)
        OrientationTypeFactory(guild=GuildFactory(guild_lead=person))
        assert held_owners(person) == []

    def it_reads_a_saved_rows_owner():
        rule = OrientationAvailabilityFactory(equipment_owned=True)
        owner = Owner.of(rule)
        assert owner.equipment == rule.orientation_type.equipment and owner.guild is None
        any_type = OrientationAvailabilityFactory(open_window=True)
        assert Owner.of(any_type) == Owner(f"guild-{any_type.guild_id}", any_type.guild, None)
        typed = OrientationAvailabilityFactory()
        assert Owner.of(typed).guild == typed.guild


def describe_HoursEdit():
    def describe_adding():
        def it_makes_one_set_open_to_tours_and_an_orientation_and_generates_its_slots():
            person, owner = _orientor()
            hours = _save(HoursEdit(person, None, _key(weekday=_next(1).weekday()), TourOffer(60, 6), [_offer(owner)]))
            tour = TourHours.objects.get(guide=person)
            rule = OrientationAvailability.objects.get(orienter=person)
            assert tour.hours_id == rule.hours_id == hours.pk
            assert (tour.tour_minutes, tour.seats) == (60, 6)
            assert (rule.guild, rule.booking_style, rule.is_active) == (owner.guild, FIXED, True)
            assert rule.slots.exists()

        def it_makes_a_dated_set():
            person, owner = _orientor()
            day = _next(5)
            hours = _save(HoursEdit(person, None, _key(day=day), None, [_offer(owner)]))
            assert hours.date == day
            assert OrientationAvailability.objects.get().date == day

        def it_makes_an_open_window_with_no_seats_shape():
            person, owner = _orientor()
            offer = _offer(owner, booking_style=OPEN, orientation_type=None, slot_minutes=60, buffer_minutes=15)
            _save(HoursEdit(person, None, _key(), None, [offer]))
            rule = OrientationAvailability.objects.get()
            assert (rule.is_open, rule.slot_minutes, rule.buffer_minutes) == (True, None, 0)

    def describe_changing():
        def it_moves_every_kind_to_the_new_time_and_keeps_paused_rows_with_it():
            person, owner = _orientor()
            hours = _save(HoursEdit(person, None, _key(), TourOffer(60, 4), [_offer(owner)]))
            paused = OrientationAvailabilityFactory(
                guild=owner.guild, orienter=person, start_time=time(16), end_time=time(18), is_active=False
            )
            hours.refresh_from_db()
            rule = OrientationAvailability.objects.get(is_active=True)
            moved = _save(
                HoursEdit(person, hours, _key(start=10, end=12), TourOffer(30, 2), [_offer(owner, rule=rule, seats=2)])
            )
            assert moved.key == _key(start=10, end=12)
            assert set(moved.tour_hours.values_list("tour_minutes", "seats")) == {(30, 2)}
            assert {row.pk for row in moved.orientation_rules.all()} == {rule.pk, paused.pk}
            assert Hours.objects.filter(member=person).count() == 1

        def it_deletes_the_tour_row_when_tours_are_ticked_off_and_keeps_booked_tours():
            person, owner = _orientor()
            hours = _save(HoursEdit(person, None, _key(), TourOffer(60, 4), [_offer(owner)]))
            booking = TourBookingFactory(guide=person, starts_at=local(_next(1), 16))
            rule = OrientationAvailability.objects.get()
            kept = _save(HoursEdit(person, hours, _key(), None, [_offer(owner, rule=rule)]))
            assert kept.pk == hours.pk
            assert not TourHours.objects.exists()
            assert TourBooking.objects.filter(pk=booking.pk).exists()

        def it_pauses_an_orientation_ticked_off_and_keeps_its_booked_slot():
            person, owner = _orientor()
            hours = _save(HoursEdit(person, None, _key(), TourOffer(60, 4), [_offer(owner)]))
            rule = OrientationAvailability.objects.get()
            booked = rule.slots.order_by("starts_at").first()
            OrientationBookingFactory(slot=booked, status="confirmed")
            _save(HoursEdit(person, hours, _key(), TourOffer(60, 4), [_offer(owner, rule=rule, on=False)]))
            rule.refresh_from_db()
            # Paused, as the old Active switch did: settings kept, open times gone, the booked one stays.
            assert rule.is_active is False
            assert rule.hours_id == hours.pk
            assert list(OrientationSlot.objects.all()) == [booked]

        def it_moves_a_row_paused_in_the_same_edit_to_the_new_time():
            person, owner = _orientor()
            hours = _save(HoursEdit(person, None, _key(), TourOffer(60, 4), [_offer(owner)]))
            rule = OrientationAvailability.objects.get()
            moved = _key(start=time(17, 0), end=19)
            saved = _save(HoursEdit(person, hours, moved, TourOffer(60, 4), [_offer(owner, rule=rule, on=False)]))
            rule.refresh_from_db()
            assert (rule.start_time, rule.end_time, rule.hours_id) == (time(17, 0), time(19, 0), saved.pk)

        def it_resumes_a_paused_orientation_ticked_on_with_its_settings():
            person, owner = _orientor()
            hours = _save(HoursEdit(person, None, _key(), TourOffer(60, 4), [_offer(owner)]))
            rule = OrientationAvailability.objects.get()
            _save(HoursEdit(person, hours, _key(), TourOffer(60, 4), [_offer(owner, rule=rule, on=False)]))
            rule.refresh_from_db()
            _save(HoursEdit(person, hours, _key(), TourOffer(60, 4), [_offer(owner, rule=rule)]))
            rule.refresh_from_db()
            assert rule.is_active is True
            assert rule.slots.exists()

        def it_leaves_a_paused_orientation_paused_while_it_stays_off():
            person, owner = _orientor()
            hours = _save(HoursEdit(person, None, _key(), TourOffer(60, 4), [_offer(owner)]))
            rule = OrientationAvailability.objects.get()
            _save(HoursEdit(person, hours, _key(), TourOffer(60, 4), [_offer(owner, rule=rule, on=False)]))
            rule.refresh_from_db()
            _save(HoursEdit(person, hours, _key(), TourOffer(90, 4), [_offer(owner, rule=rule, on=False)]))
            rule.refresh_from_db()
            assert rule.is_active is False
            assert not rule.slots.exists()

        def it_retires_the_old_shape_when_the_booking_style_changes():
            person, owner = _orientor()
            hours = _save(HoursEdit(person, None, _key(), None, [_offer(owner)]))
            rule = OrientationAvailability.objects.get()
            assert rule.slots.exists()
            _save(HoursEdit(person, hours, _key(), None, [_offer(owner, rule=rule, booking_style=OPEN)]))
            assert not rule.slots.exists()
            assert rule.windows.exists()

    def describe_problems():
        def it_needs_something_ticked():
            person, _ = _orientor()
            problems = HoursEdit(person, None, _key(), None, []).problems()
            assert problems == {TIME: {"__all__": ["Tick at least one thing these hours are open to, or delete them."]}}

        def it_refuses_until_before_from_and_times_off_the_half_hour():
            person, _ = _orientor()
            key = _key(start=time(16, 15), end=16)
            problems = HoursEdit(person, None, key, TourOffer(60, 4)).problems()[TIME]
            assert problems["end_time"] == ["Until must be after From."]
            assert problems["start_time"] == ["Hours line up on half hour marks, for example 4:00 or 4:30."]

        def it_refuses_a_cadence_with_no_start_day():
            person, _ = _orientor()
            key = _key(cadence=OrientationAvailability.Cadence.FORTNIGHTLY)
            problems = HoursEdit(person, None, key, TourOffer(60, 4)).problems()
            assert problems[TIME]["anchor_date"] == ["Pick the day these hours start."]

        def it_refuses_the_exact_time_of_another_set():
            person, owner = _orientor()
            hours = _save(HoursEdit(person, None, _key(), TourOffer(60, 4)))
            again = HoursEdit(person, None, _key(), None, [_offer(owner)]).problems()
            assert again[TIME]["__all__"] == ["You already have hours at exactly this time. Edit those instead."]
            assert HoursEdit(person, hours, _key(), TourOffer(60, 2)).problems() == {}

        def it_refuses_tours_over_another_set_of_tour_hours():
            person = make_guide()
            TourHoursFactory(guide=person, start_time=time(17), end_time=time(19))
            problems = HoursEdit(person, None, _key(), TourOffer(60, 4)).problems()
            assert problems[TOURS]["__all__"] == [
                "These hours overlap your tours on Tuesdays, 5:00 PM to 7:00 PM. You give one tour at a time."
            ]
            day = _next(1)
            TourHoursFactory(guide=person, date=day, weekday=day.weekday(), start_time=time(9), end_time=time(10))
            dated = HoursEdit(person, None, _key(day=day, start=9, end=11), TourOffer(60, 4)).problems()
            assert dated[TOURS]["__all__"][0].startswith(f"These hours overlap your tours on {day:%a, %b} {day.day}")

        def it_puts_a_kind_rows_own_problems_under_it():
            person, owner = _orientor()
            problems = HoursEdit(
                person, None, _key(), TourOffer(180, 4), [_offer(owner, orientation_type=None)]
            ).problems()
            assert problems[TOURS]["tour_minutes"] == ["These hours are shorter than one tour."]
            assert problems[owner.key]["orientation_type"] == ["Pick an orientation."]

        def it_gives_an_open_window_its_hours_to_itself():
            person, owner = _orientor()
            other = OrientationTypeFactory(guild=owner.guild, name="Other")
            offers = [
                _offer(owner, booking_style=OPEN, orientation_type=None),
                _offer(owner, orientation_type=other),
            ]
            problems = HoursEdit(person, None, _key(), None, offers).problems()
            assert problems[owner.key]["booking_style"] == ["Any time in the window needs these hours to itself."]

        def it_refuses_to_save_with_problems():
            person, _ = _orientor()
            with pytest.raises(ValueError, match="no problems"):
                _save(HoursEdit(person, None, _key(), None, []))


def describe_removing_hours():
    def it_deletes_tours_retires_orientations_and_the_set_goes():
        person, owner = _orientor()
        hours = _save(HoursEdit(person, None, _key(), TourOffer(60, 4), [_offer(owner)]))
        rule = OrientationAvailability.objects.get()
        booked = rule.slots.order_by("starts_at").first()
        OrientationBookingFactory(slot=booked, status="confirmed")
        with transaction.atomic():
            removed, kept = hours.remove()
        assert kept == 1 and removed >= 1
        assert not Hours.objects.exists()
        assert not TourHours.objects.exists() and not OrientationAvailability.objects.exists()
        assert list(OrientationSlot.objects.all()) == [booked]
