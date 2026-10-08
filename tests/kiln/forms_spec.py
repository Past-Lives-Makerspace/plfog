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


def describe_unload_form():
    from django.utils.datastructures import MultiValueDict

    from kiln.forms import UnloadForm

    @pytest.fixture
    def tickets(db):
        return [KilnTicketFactory(), KilnTicketFactory()]

    def it_starts_with_every_ticket_ticked(tickets):
        form = UnloadForm(tickets=tickets)

        assert [row.ticked for row in form.rows] == [True, True]
        assert form.initial["fired"] == [t.pk for t in tickets]

    def it_reads_a_garbled_tick_as_unticked_and_refuses_it(tickets):
        data = MultiValueDict({"fired": ["abc", str(tickets[1].pk)]})

        form = UnloadForm(data, tickets=tickets)

        assert [row.ticked for row in form.rows] == [False, True]
        assert not form.is_valid()

    def it_refuses_a_note_over_the_limit_once(tickets):
        pk = tickets[0].pk
        data = MultiValueDict(
            {
                "fired": [str(tickets[1].pk)],
                f"what-{pk}": ["cracked"],
                f"note-{pk}": ["x" * 2001],
                f"next-{pk}": ["fired"],
            }
        )

        form = UnloadForm(data, tickets=tickets)

        assert not form.is_valid()
        assert form.errors[f"note-{pk}"] == ["Keep the note under 2000 characters."]

    def it_gives_the_exceptions_for_the_unticked_tickets(tickets):
        pk = tickets[0].pk
        data = MultiValueDict(
            {
                "fired": [str(tickets[1].pk)],
                f"what-{pk}": ["other"],
                f"note-{pk}": [" Odd. "],
                f"next-{pk}": ["back_to_queue"],
            }
        )

        form = UnloadForm(data, tickets=tickets)

        assert form.is_valid(), form.errors
        [note] = form.exception_notes.values()
        assert (note.ticket_pk, note.what_happened, note.note, note.back_to_queue) == (pk, "other", "Odd.", True)
