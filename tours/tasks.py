"""Facility tour work on the durable task queue (:mod:`core.tasks`)."""

from __future__ import annotations

from django.tasks import task


@task
def lapse_tour_request(booking_id: int) -> None:
    """Cancel the request if its guide has not answered by its confirm by time (#1166).

    Queued for that moment (``run_after``) when the request is made, never polled. Safe to run
    twice: a request already answered, cancelled or not yet due is left alone.
    """
    from tours.booking import lapse_request

    lapse_request(booking_id)
