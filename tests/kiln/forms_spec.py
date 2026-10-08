"""The ticket form saved on its own, outside the view (#691)."""

from __future__ import annotations

import pytest

from kiln.forms import KilnTicketForm
from kiln.models import KilnTicket
from tests.kiln.factories import ClayOptionFactory, GlazeOptionFactory, KilnTicketFactory

pytestmark = pytest.mark.django_db


def describe_kiln_ticket_form_save():
    def it_saves_answers_and_studio_glazes_when_committing():
        glaze = GlazeOptionFactory()
        ticket = KilnTicketFactory()
        data = {
            "firing_type": "glaze",
            "clay_choice": str(ClayOptionFactory().pk),
            "glaze_studio": "on",
            "studio_glazes": [str(glaze.pk)],
        }
        form = KilnTicketForm(data, instance=ticket)

        assert form.is_valid(), form.errors
        saved = form.save()

        saved.refresh_from_db()
        assert saved.firing_type == KilnTicket.FiringType.GLAZE
        assert list(saved.studio_glazes.all()) == [glaze]

    def it_clears_studio_glazes_when_the_branch_no_longer_asks():
        glaze = GlazeOptionFactory()
        ticket = KilnTicketFactory(firing_type="glaze", glaze_studio=True)
        ticket.studio_glazes.add(glaze)
        form = KilnTicketForm({"firing_type": "bisque"}, instance=ticket)

        assert form.is_valid(), form.errors
        form.save()

        assert not ticket.studio_glazes.exists()

    def it_leaves_the_database_alone_without_commit():
        ticket = KilnTicketFactory()
        form = KilnTicketForm({"quantity": "3"}, instance=ticket)

        assert form.is_valid(), form.errors
        unsaved = form.save(commit=False)

        assert unsaved.quantity == 3
        assert KilnTicket.objects.get(pk=ticket.pk).quantity is None
