"""How many pieces, and the size of one (#1024): the form asks in plain words, every read surface shows both."""

from __future__ import annotations

from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from kiln.models import KilnTicket
from kiln.services import load_kiln
from tests.kiln.factories import KilnTicketFactory, KilnTicketPhotoFactory

Status = KilnTicket.Status

HINT = "Several pieces can share one ticket when they are the same clay, glaze and firing. Blank means one piece."


def _set_of_three(maker, status: str = Status.SUBMITTED) -> KilnTicket:
    ticket = KilnTicketFactory(
        maker=maker,
        status=status,
        firing_type=KilnTicket.FiringType.GLAZE,
        submitted_at=timezone.now(),
        quantity=3,
        height_in=Decimal("4"),
        width_in=Decimal("6"),
        length_in=Decimal("6"),
    )
    KilnTicketPhotoFactory(ticket=ticket, is_cover=True)
    return ticket


def describe_the_ticket_form():
    def it_asks_how_many_pieces_before_the_size_of_one(maker_client, kiln_guild):
        html = maker_client.get(reverse("kiln:new")).content.decode()
        assert "How many pieces are on this ticket?" in html
        assert HINT in html
        assert "Size of one piece" in html
        assert html.index("How many pieces are on this ticket?") < html.index("Size of one piece")

    def it_drops_the_old_labels(maker_client, kiln_guild):
        html = maker_client.get(reverse("kiln:new")).content.decode()
        assert "Quantity of identical pieces" not in html
        assert "Piece size" not in html


def describe_the_count_beside_the_size():
    def it_shows_on_the_makers_ticket(maker, maker_client, kiln_guild):
        ticket = _set_of_three(maker)
        html = maker_client.get(reverse("kiln:detail", args=[ticket.pk])).content.decode()
        assert "Glaze · 3 pieces · 4 x 6 x 6 in each" in html
        assert "<dt>Pieces</dt><dd>3</dd>" in html

    def it_shows_on_the_crews_ticket(maker, crew_client, kiln_guild):
        ticket = _set_of_three(maker)
        html = crew_client.get(reverse("kiln:detail", args=[ticket.pk])).content.decode()
        assert "Glaze · 3 pieces · 4 x 6 x 6 in each" in html

    def it_shows_on_the_load_tile(maker, crew_client, kiln_guild):
        _set_of_three(maker)
        html = crew_client.get(reverse("kiln:load") + "?type=glaze").content.decode()
        assert '<span class="pl-kiln-tile__qty">3 pieces</span>' in html
        assert '<span class="pl-kiln-tile__size">4 x 6 x 6 in each</span>' in html

    def it_shows_on_the_unload_row(maker, crew, crew_client, kiln_guild):
        ticket = _set_of_three(maker)
        firing = load_kiln(firing_type=ticket.firing_type, ticket_pks=[ticket.pk], by=crew).firing
        assert firing is not None
        html = crew_client.get(reverse("kiln:unload", args=[firing.pk])).content.decode()
        assert f"Ticket {ticket.pk} · Glaze · 3 pieces · 4 x 6 x 6 in each" in html
