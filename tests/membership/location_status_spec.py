"""Specs for the guild page's location lights (#616): ``membership.services.location_status``.

Every case pins ``now`` to Tuesday 6 October 2026, 2:00 PM in Portland, and builds what uses the
area relative to it, so the clock never matters.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from core.models import SiteConfiguration
from membership.models import CommunityEvent, EquipmentReservation, OrientationBooking
from membership.services.location_status import Light, guild_location_statuses
from tests.membership.factories import (
    CommunityEventFactory,
    EquipmentFactory,
    EquipmentReservationFactory,
    GuildFactory,
    LocationFactory,
    MemberFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

NOW = timezone.make_aware(datetime(2026, 10, 6, 14, 0))


def _class_session(area, *, start, minutes=120, status=ClassOffering.Status.PUBLISHED, **offering):
    offering_row = ClassOfferingFactory(area=area, status=status, **offering)
    return ClassSessionFactory(class_offering=offering_row, starts_at=start, ends_at=start + timedelta(minutes=minutes))


def _booked_slot(area, *, start, minutes=60, booking_status=OrientationBooking.Status.CONFIRMED, **slot):
    orientation_type = OrientationTypeFactory(name="Lathe Basics", area=area)
    slot_row = OrientationSlotFactory(
        guild=orientation_type.guild,
        orientation_type=orientation_type,
        starts_at=start,
        ends_at=start + timedelta(minutes=minutes),
        **slot,
    )
    OrientationBookingFactory(slot=slot_row, status=booking_status)
    return slot_row


def _event(area, *, start, minutes=120, **fields):
    return CommunityEventFactory(
        area=area, title="Glass Night", starts_at=start, ends_at=start + timedelta(minutes=minutes), **fields
    )


def _reservation(area, *, start, minutes=60, **fields):
    equipment = EquipmentFactory(name="Kiln", area=area)
    return EquipmentReservationFactory(
        equipment=equipment, starts_at=start, ends_at=start + timedelta(minutes=minutes), **fields
    )


def _only(guild):
    statuses = guild_location_statuses(guild, now=NOW)
    assert len(statuses) == 1
    return statuses[0]


def describe_guild_location_statuses():
    def it_is_empty_without_a_location():
        assert guild_location_statuses(GuildFactory(), now=NOW) == []

    def it_leaves_out_inactive_locations_and_other_guilds():
        guild = GuildFactory()
        LocationFactory(guild=guild, is_active=False)
        LocationFactory(guild=GuildFactory())
        assert guild_location_statuses(guild, now=NOW) == []

    def it_lists_active_locations_in_name_order():
        guild = GuildFactory()
        LocationFactory(guild=guild, name="Woodshop Sanding Area")
        LocationFactory(guild=guild, name="Woodshop")
        names = [status.location.name for status in guild_location_statuses(guild, now=NOW)]
        assert names == ["Woodshop", "Woodshop Sanding Area"]

    def it_is_green_with_nothing_on():
        location = LocationFactory(guild=GuildFactory())
        status = _only(location.guild)
        assert status.light == Light.FREE
        assert status.activity is None
        assert status.message == "Free now"

    def it_judges_against_the_current_time_when_none_is_given():
        location = LocationFactory(guild=GuildFactory())
        _class_session(location, start=timezone.now() - timedelta(minutes=10))
        [status] = guild_location_statuses(location.guild)
        assert status.light == Light.IN_USE

    def describe_classes():
        def it_is_red_during_a_session():
            location = LocationFactory(guild=GuildFactory())
            _class_session(location, start=NOW - timedelta(minutes=30), minutes=120, title="Intro to Lampwork")
            status = _only(location.guild)
            assert status.light == Light.IN_USE
            assert status.message == "In use: Intro to Lampwork until 3:30 PM. This area might not be available."

        def it_is_red_from_the_minute_a_session_starts():
            location = LocationFactory(guild=GuildFactory())
            _class_session(location, start=NOW)
            assert _only(location.guild).light == Light.IN_USE

        def it_is_amber_when_a_session_starts_within_the_hour():
            location = LocationFactory(guild=GuildFactory())
            _class_session(location, start=NOW + timedelta(minutes=45), title="Stained Glass")
            status = _only(location.guild)
            assert status.light == Light.SOON
            assert status.message == "Starting soon: Stained Glass at 2:45 PM"

        def it_counts_a_session_starting_exactly_an_hour_out():
            location = LocationFactory(guild=GuildFactory())
            _class_session(location, start=NOW + timedelta(minutes=60))
            assert _only(location.guild).light == Light.SOON

        def it_is_green_when_the_session_is_more_than_an_hour_out():
            location = LocationFactory(guild=GuildFactory())
            _class_session(location, start=NOW + timedelta(minutes=61))
            assert _only(location.guild).light == Light.FREE

        def it_is_green_once_the_session_has_ended():
            location = LocationFactory(guild=GuildFactory())
            _class_session(location, start=NOW - timedelta(minutes=120), minutes=120)
            assert _only(location.guild).light == Light.FREE

        @pytest.mark.parametrize(
            "status",
            [ClassOffering.Status.CANCELLED, ClassOffering.Status.DRAFT, ClassOffering.Status.PENDING],
        )
        def it_ignores_a_class_that_is_not_published(status):
            location = LocationFactory(guild=GuildFactory())
            _class_session(location, start=NOW - timedelta(minutes=10), status=status)
            assert _only(location.guild).light == Light.FREE

        def it_counts_a_private_class_but_names_it_only_a_private_class():
            location = LocationFactory(guild=GuildFactory())
            _class_session(location, start=NOW - timedelta(minutes=30), title="Members Only Qv", is_private=True)
            status = _only(location.guild)
            assert status.light == Light.IN_USE
            assert status.message == "In use: Private class until 3:30 PM. This area might not be available."

        def it_hides_a_demo_class_while_demo_classes_are_off():
            location = LocationFactory(guild=GuildFactory())
            _class_session(location, start=NOW - timedelta(minutes=10), title="Demo Glass", slug="demo-glass")
            assert _only(location.guild).light == Light.FREE

        def it_shows_a_demo_class_while_demo_classes_are_on():
            site = SiteConfiguration.load()
            site.display_demo_classes = True
            site.save()
            location = LocationFactory(guild=GuildFactory())
            _class_session(location, start=NOW - timedelta(minutes=10), title="Demo Glass", slug="demo-glass")
            assert _only(location.guild).light == Light.IN_USE

        def it_ignores_a_class_in_another_location():
            location = LocationFactory(guild=GuildFactory())
            _class_session(LocationFactory(), start=NOW - timedelta(minutes=10))
            _class_session(None, start=NOW - timedelta(minutes=10))
            assert _only(location.guild).light == Light.FREE

    def describe_orientations():
        def it_is_red_during_a_booked_slot_named_by_its_type():
            location = LocationFactory(guild=GuildFactory())
            _booked_slot(location, start=NOW - timedelta(minutes=15))
            status = _only(location.guild)
            assert status.light == Light.IN_USE
            assert status.message == "In use: Lathe Basics until 2:45 PM. This area might not be available."

        def it_is_amber_before_a_booked_slot():
            location = LocationFactory(guild=GuildFactory())
            _booked_slot(
                location, start=NOW + timedelta(minutes=30), booking_status=OrientationBooking.Status.REQUESTED
            )
            status = _only(location.guild)
            assert status.light == Light.SOON
            assert status.message == "Starting soon: Lathe Basics at 2:30 PM"

        @pytest.mark.parametrize(
            "booking_status",
            [
                OrientationBooking.Status.CANCELLED,
                OrientationBooking.Status.DECLINED,
                OrientationBooking.Status.PENDING_PAYMENT,
            ],
        )
        def it_ignores_a_slot_without_an_active_booking(booking_status):
            location = LocationFactory(guild=GuildFactory())
            _booked_slot(location, start=NOW - timedelta(minutes=15), booking_status=booking_status)
            assert _only(location.guild).light == Light.FREE

        def it_ignores_an_unbooked_slot():
            location = LocationFactory(guild=GuildFactory())
            orientation_type = OrientationTypeFactory(area=location)
            OrientationSlotFactory(
                guild=orientation_type.guild,
                orientation_type=orientation_type,
                starts_at=NOW - timedelta(minutes=15),
                ends_at=NOW + timedelta(minutes=45),
            )
            assert _only(location.guild).light == Light.FREE

        def it_ignores_a_cancelled_slot():
            location = LocationFactory(guild=GuildFactory())
            _booked_slot(location, start=NOW - timedelta(minutes=15), is_cancelled=True)
            assert _only(location.guild).light == Light.FREE

    def describe_events():
        def it_is_red_during_a_published_event():
            location = LocationFactory(guild=GuildFactory())
            _event(location, start=NOW - timedelta(hours=1))
            status = _only(location.guild)
            assert status.light == Light.IN_USE
            assert status.message == "In use: Glass Night until 3:00 PM. This area might not be available."

        def it_is_amber_before_a_published_event():
            location = LocationFactory(guild=GuildFactory())
            _event(location, start=NOW + timedelta(minutes=20))
            assert _only(location.guild).light == Light.SOON

        @pytest.mark.parametrize(
            "state",
            [
                CommunityEvent.ModerationState.PENDING,
                CommunityEvent.ModerationState.SCHEDULED,
                CommunityEvent.ModerationState.DECLINED,
                CommunityEvent.ModerationState.CHANGES_REQUESTED,
            ],
        )
        def it_ignores_an_event_that_is_not_published(state):
            location = LocationFactory(guild=GuildFactory())
            _event(location, start=NOW - timedelta(hours=1), moderation_state=state)
            assert _only(location.guild).light == Light.FREE

        def it_ignores_studio_hours():
            location = LocationFactory(guild=GuildFactory())
            _event(location, start=NOW - timedelta(hours=1), studio_hours=True, guild=location.guild)
            assert _only(location.guild).light == Light.FREE

        def it_lights_a_weekly_event_on_todays_date():
            location = LocationFactory(guild=GuildFactory())
            _event(location, start=NOW - timedelta(days=14, minutes=30), recurrence=CommunityEvent.Recurrence.WEEKLY)
            status = _only(location.guild)
            assert status.light == Light.IN_USE
            assert status.activity is not None
            assert status.activity.starts_at == NOW - timedelta(minutes=30)

        def it_warns_before_todays_date_of_a_weekly_event():
            location = LocationFactory(guild=GuildFactory())
            _event(location, start=NOW - timedelta(days=7) + timedelta(minutes=40), recurrence="weekly")
            assert _only(location.guild).light == Light.SOON

        def it_lights_a_repeating_date_that_began_last_night():
            location = LocationFactory(guild=GuildFactory())
            late = timezone.make_aware(datetime(2026, 9, 28, 23, 0))  # a Monday, a week before the night in question
            _event(location, start=late, minutes=180, recurrence=CommunityEvent.Recurrence.WEEKLY)
            after_midnight = timezone.make_aware(datetime(2026, 10, 6, 1, 0))
            [status] = guild_location_statuses(location.guild, now=after_midnight)
            assert status.light == Light.IN_USE
            assert status.message == "In use: Glass Night until 2:00 AM. This area might not be available."

        def it_warns_when_a_repeating_date_starts_exactly_an_hour_out():
            location = LocationFactory(guild=GuildFactory())
            _event(location, start=NOW - timedelta(days=7) + timedelta(hours=1), recurrence="weekly")
            assert _only(location.guild).light == Light.SOON

        def it_stays_green_when_a_repeating_date_ends_right_now():
            location = LocationFactory(guild=GuildFactory())
            _event(location, start=NOW - timedelta(days=7, hours=2), minutes=120, recurrence="weekly")
            assert _only(location.guild).light == Light.FREE

        def it_stays_green_once_todays_date_of_a_repeating_event_is_over():
            location = LocationFactory(guild=GuildFactory())
            _event(location, start=NOW - timedelta(days=7, hours=4), recurrence=CommunityEvent.Recurrence.WEEKLY)
            assert _only(location.guild).light == Light.FREE

        def describe_across_a_daylight_saving_change():
            # Clocks fall back on Sunday 1 November 2026. A weekly 6 PM event anchored the
            # Tuesday before stays at 6 PM on the wall clock, an hour later in UTC.
            def _weekly_six_pm(location):
                anchor = timezone.make_aware(datetime(2026, 10, 27, 18, 0))
                return _event(location, start=anchor, minutes=120, recurrence=CommunityEvent.Recurrence.WEEKLY)

            def it_is_red_at_six_thirty_by_the_clock_after_the_change():
                location = LocationFactory(guild=GuildFactory())
                _weekly_six_pm(location)
                [status] = guild_location_statuses(
                    location.guild, now=timezone.make_aware(datetime(2026, 11, 3, 18, 30))
                )
                assert status.light == Light.IN_USE
                assert status.message == "In use: Glass Night until 8:00 PM. This area might not be available."

            def it_is_amber_at_five_thirty_by_the_clock_after_the_change():
                # 5:30 PM PST is 6:30 PM PDT: a date that drifted with UTC would read in use here.
                location = LocationFactory(guild=GuildFactory())
                _weekly_six_pm(location)
                [status] = guild_location_statuses(
                    location.guild, now=timezone.make_aware(datetime(2026, 11, 3, 17, 30))
                )
                assert status.light == Light.SOON
                assert status.message == "Starting soon: Glass Night at 6:00 PM"

        def it_stays_green_on_a_day_a_repeating_event_does_not_meet():
            location = LocationFactory(guild=GuildFactory())
            _event(location, start=NOW - timedelta(days=3), recurrence=CommunityEvent.Recurrence.WEEKLY)
            assert _only(location.guild).light == Light.FREE

    def describe_reservations():
        def it_is_red_during_a_confirmed_reservation_without_naming_the_member():
            location = LocationFactory(guild=GuildFactory())
            member = MemberFactory(full_legal_name="Quentin Zebulon", preferred_name="Quentin")
            _reservation(location, start=NOW - timedelta(minutes=5), minutes=90, member=member)
            status = _only(location.guild)
            assert status.light == Light.IN_USE
            assert status.message == "In use: Reserved: Kiln until 3:25 PM. This area might not be available."
            assert "Quentin" not in status.message

        def it_is_amber_before_a_confirmed_reservation():
            location = LocationFactory(guild=GuildFactory())
            _reservation(location, start=NOW + timedelta(minutes=10))
            assert _only(location.guild).message == "Starting soon: Reserved: Kiln at 2:10 PM"

        def it_ignores_a_cancelled_reservation():
            location = LocationFactory(guild=GuildFactory())
            _reservation(location, start=NOW - timedelta(minutes=5), status=EquipmentReservation.Status.CANCELLED)
            assert _only(location.guild).light == Light.FREE

    def describe_shared_space():
        def it_counts_activity_in_a_linked_location():
            front_studio = LocationFactory(guild=GuildFactory(), name="Front Studio")
            events_stage = LocationFactory(guild=GuildFactory(), name="Events Stage")
            front_studio.shares_space_with.add(events_stage)
            _event(events_stage, start=NOW - timedelta(minutes=10))
            assert _only(front_studio.guild).light == Light.IN_USE

        def it_counts_the_link_from_either_side():
            front_studio = LocationFactory(guild=GuildFactory(), name="Front Studio")
            events_stage = LocationFactory(guild=GuildFactory(), name="Events Stage")
            front_studio.shares_space_with.add(events_stage)
            _class_session(front_studio, start=NOW + timedelta(minutes=30))
            assert _only(events_stage.guild).light == Light.SOON

        def it_counts_a_linked_location_that_has_been_deactivated():
            front_studio = LocationFactory(guild=GuildFactory())
            retired = LocationFactory(is_active=False)
            front_studio.shares_space_with.add(retired)
            _reservation(retired, start=NOW - timedelta(minutes=5))
            assert _only(front_studio.guild).light == Light.IN_USE

        def it_does_not_count_an_unlinked_neighbour():
            guild = GuildFactory()
            LocationFactory(guild=guild, name="Woodshop")
            sanding = LocationFactory(guild=guild, name="Woodshop Sanding Area")
            _event(sanding, start=NOW - timedelta(minutes=10))
            lights = {status.location.name: status.light for status in guild_location_statuses(guild, now=NOW)}
            assert lights == {"Woodshop": Light.FREE, "Woodshop Sanding Area": Light.IN_USE}

    def describe_picking_what_to_name():
        def it_prefers_in_use_over_starting_soon():
            location = LocationFactory(guild=GuildFactory())
            _class_session(location, start=NOW + timedelta(minutes=5), title="Later Class")
            _reservation(location, start=NOW - timedelta(minutes=5))
            status = _only(location.guild)
            assert status.light == Light.IN_USE
            assert status.activity is not None
            assert status.activity.title == "Reserved: Kiln"

        def it_names_the_in_use_activity_that_ends_last():
            location = LocationFactory(guild=GuildFactory())
            _class_session(location, start=NOW - timedelta(minutes=30), minutes=60, title="Short Class")
            _class_session(location, start=NOW - timedelta(minutes=10), minutes=180, title="Long Class")
            assert _only(location.guild).activity.title == "Long Class"

        def it_names_the_soonest_of_several_starting_soon():
            location = LocationFactory(guild=GuildFactory())
            _class_session(location, start=NOW + timedelta(minutes=50), title="Second Class")
            _class_session(location, start=NOW + timedelta(minutes=20), title="First Class")
            assert _only(location.guild).activity.title == "First Class"

        def it_adds_the_date_when_the_time_is_not_today():
            location = LocationFactory(guild=GuildFactory())
            _reservation(location, start=NOW - timedelta(minutes=5), minutes=60 * 20)
            assert _only(location.guild).message == (
                "In use: Reserved: Kiln until Oct 7, 9:55 AM. This area might not be available."
            )

    def describe_query_count():
        def _queries_for(guild):
            with CaptureQueriesContext(connection) as ctx:
                guild_location_statuses(guild, now=NOW)
            return len(ctx.captured_queries)

        def it_runs_one_query_when_the_guild_has_no_location():
            SiteConfiguration.load()
            assert _queries_for(GuildFactory()) == 1

        def it_does_not_grow_with_locations_or_activity():
            SiteConfiguration.load()
            small = GuildFactory()
            LocationFactory(guild=small)
            big = GuildFactory()
            locations = [LocationFactory(guild=big) for _ in range(3)]
            locations[0].shares_space_with.add(locations[1], LocationFactory())
            for location in locations:
                _class_session(location, start=NOW - timedelta(minutes=10))
                _booked_slot(location, start=NOW + timedelta(minutes=10))
                _event(location, start=NOW - timedelta(minutes=10))
                _event(location, start=NOW - timedelta(days=7, minutes=10), recurrence="weekly")
                _reservation(location, start=NOW + timedelta(minutes=10))
            # Locations, their links, the site row, then one query per source.
            assert _queries_for(small) == 7
            assert _queries_for(big) == 7
