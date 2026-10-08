"""Saving a kiln ticket: answers, photos, cover and flags in one step (#691)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction

from kiln.access import maker_type_for
from kiln.forms import ACTION_COVER, ACTION_REMOVE, KilnTicketForm
from kiln.models import KilnTicket

if TYPE_CHECKING:
    from membership.models import Member


def save_ticket(form: KilnTicketForm, maker: Member) -> KilnTicket:
    """Save a valid ticket form for ``maker`` and apply the button that was pressed.

    New photos are stored first, so a Remove or Make cover pressed in the same request sees
    them. A strict save (Submit, or any save of a ticket already in the queue) puts the
    ticket in the queue and re-runs the automatic flags.

    Args:
        form: A bound, valid :class:`KilnTicketForm`.
        maker: The signed-in member saving it; becomes the maker of a new ticket.

    Returns:
        The saved ticket.
    """
    with transaction.atomic():
        ticket = form.save(commit=False)
        if ticket.pk is None:
            ticket.maker = maker
        ticket.maker_type = maker_type_for(ticket.maker)
        ticket.save()
        form.save_glazes(ticket)
        for upload in form.new_photos:
            ticket.add_photo(upload)
        if form.action == ACTION_REMOVE and form.target_photo_pk is not None:
            ticket.remove_photo(form.target_photo_pk)
        if form.action == ACTION_COVER and form.target_photo_pk is not None:
            ticket.set_cover(form.target_photo_pk)
        if form.is_strict:
            ticket.submit()
    return ticket
