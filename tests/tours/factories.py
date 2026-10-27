"""factory-boy factories for facility tours (#947)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import factory
from django.utils import timezone

from tests.membership.factories import MemberFactory
from tours.models import TourBooking, TourDayOff, TourHours


class TourHoursFactory(factory.django.DjangoModelFactory):
    """Tuesday 4:00 to 6:00 PM, 60 minute tours, 4 seats."""

    class Meta:
        model = TourHours

    guide = factory.SubFactory(MemberFactory)
    weekday = TourHours.Weekday.TUESDAY
    start_time = time(16, 0)
    end_time = time(18, 0)
    tour_minutes = 60
    seats = 4


class TourDayOffFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TourDayOff

    guide = factory.SubFactory(MemberFactory)
    starts_on = factory.LazyFunction(lambda: timezone.localdate() + timedelta(days=7))
    ends_on = factory.LazyAttribute(lambda day_off: day_off.starts_on)


def local(day: date, hour: int, minute: int = 0) -> datetime:
    """An aware local datetime, as the carve makes them."""
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


class TourBookingFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TourBooking

    guide = factory.SubFactory(MemberFactory)
    starts_at = factory.LazyFunction(lambda: local(timezone.localdate() + timedelta(days=7), 16))
    ends_at = factory.LazyAttribute(lambda booking: booking.starts_at + timedelta(hours=1))
    party_size = 2
    visitor_name = factory.Faker("name")
    visitor_email = factory.Sequence(lambda n: f"visitor{n}@example.com")
