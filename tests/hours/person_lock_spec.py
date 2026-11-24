"""Every booking path takes the person lock before it checks (#1300, part 2), on PostgreSQL.

The lock is ``hours.follow.lock_person``: ``FOR NO KEY UPDATE`` on the person's member row, the one
lock every tour booking, every orientation booking (request, checkout hold, open window, move,
staff add) and every save of their hours shares, so a tour and an orientation racing for one
person cannot both land. SQLite writes no ``FOR UPDATE``, so these run on PostgreSQL only.
"""

from __future__ import annotations

import threading
import time as clock
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from hours.follow import lock_person
from membership import orientations
from membership.models import Member, OrientationBooking, OrientationError, OrientationSlot
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    OrientationAvailabilityBlockFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)
from tests.tours.conftest import make_guide
from tests.tours.factories import TourBookingFactory, TourHoursFactory
from tours.booking import TourUnavailableError, Visitor, book_tour

# Committed setup: a factory saving a person's hours takes their lock inside the test's own
# transaction otherwise, and the path under test would find it already held and send nothing.
pytestmark = [pytest.mark.postgres_only, pytest.mark.django_db(transaction=True)]

SESSION = {"id": "cs_lock_1", "url": "https://checkout.stripe.example/cs_lock_1"}


def _day() -> date:
    return timezone.localdate() + timedelta(days=5)


def _at(hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime.combine(_day(), time(hour, minute)))


def _slot(orienter: Member, hour: int = 10, **fields: Any) -> OrientationSlot:
    guild = fields.pop("guild", None) or GuildFactory(guild_lead=orienter)
    return OrientationSlotFactory(
        guild=guild, orienter=orienter, starts_at=_at(hour), ends_at=_at(hour + 1), seats=2, **fields
    )


def _visitor() -> Visitor:
    return Visitor(name="Sam Rivera", email="sam@example.com", phone="5035550100", party_size=1)


def _sql(action: Callable[[], object]) -> list[str]:
    with CaptureQueriesContext(connection) as queries:
        action()
    return [query["sql"] for query in queries.captured_queries]


def _locked_before(sql: list[str], person: Member, statement: str) -> None:
    """The person's member row is locked FOR NO KEY UPDATE before the first ``statement``."""
    lock = next(
        index
        for index, text in enumerate(sql)
        if "FOR NO KEY UPDATE" in text and '"membership_member"' in text and str(person.pk) in text
    )
    first = next(index for index, text in enumerate(sql) if statement in text)
    assert lock < first


def describe_the_lock_order():
    def it_locks_the_guide_before_reading_their_orientations_and_booking_a_tour():
        guide = make_guide()
        TourHoursFactory(guide=guide, weekday=_day().weekday(), start_time=time(10), end_time=time(11))
        sql = _sql(lambda: book_tour(guide=guide, starts_at=_at(10), visitor=_visitor()))
        _locked_before(sql, guide, 'INSERT INTO "tours_tourbooking"')
        # The time is read once to find whose it is, then again under that guide's lock.
        reads = [index for index, text in enumerate(sql) if '"membership_orientationslot"' in text]
        assert len(reads) == 2
        _locked_before(sql[reads[0] + 1 :], guide, '"membership_orientationslot"')

    def it_locks_the_orientor_before_a_request_and_a_staff_add():
        orientor = MemberFactory()
        slot = _slot(orientor)
        sql = _sql(lambda: orientations.request_orientation(slot, MemberFactory()))
        _locked_before(sql, orientor, 'INSERT INTO "membership_orientationbooking"')
        sql = _sql(lambda: orientations.request_orientation(slot, MemberFactory(), by_staff=True))
        _locked_before(sql, orientor, 'INSERT INTO "membership_orientationbooking"')

    @patch("billing.stripe_utils.create_checkout_session", return_value=SESSION)
    def it_locks_the_orientor_before_a_checkout_holds_the_seat(_create: Any):
        orientor = MemberFactory()
        settings_obj = GuildOrientationSettingsFactory(guild=GuildFactory(guild_lead=orientor), is_enabled=True)
        paid = OrientationTypeFactory(guild=settings_obj.guild, price_cents=2500)
        slot = _slot(orientor, guild=settings_obj.guild, orientation_type=paid)
        sql = _sql(lambda: orientations.start_orientation_checkout(slot, MemberFactory()))
        _locked_before(sql, orientor, 'INSERT INTO "membership_orientationbooking"')
        assert OrientationBooking.objects.get(slot=slot).status == OrientationBooking.Status.PENDING_PAYMENT

    def it_locks_the_orientor_before_carving_an_open_window():
        orientor = MemberFactory()
        window = OrientationAvailabilityBlockFactory(orienter=orientor, starts_at=_at(9), ends_at=_at(12))
        kind = OrientationTypeFactory(guild=window.guild)
        sql = _sql(
            lambda: orientations.request_block_orientation(window, MemberFactory(), _at(10), orientation_type=kind)
        )
        _locked_before(sql, orientor, 'INSERT INTO "membership_orientationslot"')

    def it_locks_the_orientor_before_a_move_lands():
        orientor = MemberFactory()
        booking = OrientationBookingFactory(slot=_slot(orientor), status=OrientationBooking.Status.CONFIRMED)
        sql = _sql(lambda: orientations.move_orientation(booking, _at(14), actor=None))
        _locked_before(sql, orientor, 'INSERT INTO "membership_orientationslot"')


def _race(hold: Callable[[], None], book: Callable[[], None]) -> None:
    """Run ``hold`` in a thread inside a transaction that keeps the person lock a moment, then ``book`` here."""
    held = threading.Event()

    def holder() -> None:
        try:
            with transaction.atomic():
                hold()
                held.set()
                # Without the lock, the booking below would not wait and would not see this one.
                clock.sleep(0.5)
        finally:
            connection.close()

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert held.wait(5)
        book()
    finally:
        thread.join()


def describe_a_tour_and_an_orientation_racing_for_one_person():
    def it_lets_only_the_first_land_when_the_orientation_is_first():
        guide = make_guide()
        TourHoursFactory(guide=guide, weekday=_day().weekday(), start_time=time(10), end_time=time(11))
        slot = _slot(guide)

        def hold() -> None:
            lock_person(guide.pk)
            OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.REQUESTED)

        def book() -> None:
            with pytest.raises(TourUnavailableError):
                book_tour(guide=guide, starts_at=_at(10), visitor=_visitor())

        _race(hold, book)

    def it_lets_only_the_first_land_when_the_tour_is_first():
        guide = make_guide()
        slot = _slot(guide)

        def hold() -> None:
            lock_person(guide.pk)
            TourBookingFactory(guide=guide, starts_at=_at(10), ends_at=_at(11))

        def book() -> None:
            with pytest.raises(OrientationError):
                orientations.request_orientation(slot, MemberFactory())

        _race(hold, book)
        assert not OrientationBooking.objects.filter(slot=slot).exists()
