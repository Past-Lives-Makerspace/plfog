"""The seam other parts hang off a booking's life (#948).

:data:`tour_booking_changed` is sent once the transaction that booked, moved or cancelled a
:class:`~tours.models.TourBooking` has committed, never before, so a receiver never sees a
change that rolled back. Part 5 (#951) connects the guide's Google Calendar sync here; the
visitor and guide emails are sent by :mod:`tours.notify` directly, since the booking service
knows it wants them.

Arguments: ``booking`` (the saved row) and ``change``, one of :data:`BOOKED`, :data:`MOVED`
or :data:`CANCELLED`. A move also passes ``previous_guide`` and ``previous_starts_at``; a move to
another guide's tour (a visitor's, or an admin's reassignment, #949) has ``previous_guide`` apart
from ``booking.guide``. A change a Tour Guide or an admin made from the Tours page (#949) also
passes ``actor`` (the User) and ``staff_message``; receivers take ``**kwargs``.
"""

from __future__ import annotations

from django.dispatch import Signal

BOOKED = "booked"
MOVED = "moved"
CANCELLED = "cancelled"

tour_booking_changed = Signal()
