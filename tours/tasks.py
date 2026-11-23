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


@task
def book_waiting_tour_requests() -> None:
    """Book every request still waiting for its guide (#1309), once, after Confirm First was removed.

    Queued by migration ``tours.0012``; safe to run twice (a request already booked is skipped).
    """
    from tours.booking import book_waiting_requests

    book_waiting_requests()


#: The Mailchimp tags a tour visitor's opt-in carries (feedback request #7).
NEWSLETTER_TAG = "tour-visitor"
STUDIO_TAG = "tour-studio-interest"


@task
def subscribe_tour_visitor(booking_id: int) -> None:
    """Subscribe a tour visitor who ticked the newsletter box to the Mailchimp list (#7).

    The class registration form's list (``MailchimpClient``), tagged ``tour-visitor``, plus
    ``tour-studio-interest`` when they ticked the studio box. Mailchimp's upsert makes a second
    run harmless; nothing happens while Mailchimp is not set up or the booking is gone.
    """
    from core.integrations.mailchimp import MailchimpClient
    from tours.models import TourBooking

    booking = TourBooking.objects.filter(pk=booking_id, wants_newsletter=True).first()
    if booking is None:
        return
    client = MailchimpClient.from_site_config()
    if not client.enabled:
        return
    first, _, last = booking.visitor_name.strip().partition(" ")
    tags = [NEWSLETTER_TAG, *([STUDIO_TAG] if booking.wants_studio else [])]
    client.subscribe(email=booking.visitor_email, first_name=first, last_name=last.strip(), tags=tags)
