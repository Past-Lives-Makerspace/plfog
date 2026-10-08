"""The Kiln Log and a firing's own page (#691 part 3)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from kiln.forms import ExceptionNote
from kiln.models import KilnFiring, KilnReply, KilnTicket
from kiln.services import kiln_log, load_kiln, unload_kiln
from membership.models import Member
from tests.kiln.factories import KilnTicketFactory, KilnTicketPhotoFactory

pytestmark = pytest.mark.django_db

LOG = reverse("kiln:log")
Outcome = KilnReply.Outcome


def _waiting(maker: Member, firing_type: str = "glaze") -> KilnTicket:
    ticket = KilnTicketFactory(
        maker=maker, status=KilnTicket.Status.SUBMITTED, firing_type=firing_type, submitted_at=timezone.now()
    )
    KilnTicketPhotoFactory(ticket=ticket, is_cover=True)
    return ticket


def _loaded(by: Member, *tickets: KilnTicket) -> KilnFiring:
    result = load_kiln(firing_type=tickets[0].firing_type, ticket_pks=[t.pk for t in tickets], by=by)
    assert result.firing is not None
    return result.firing


def _note(ticket: KilnTicket, outcome: str, what: str = "cracked") -> ExceptionNote:
    return ExceptionNote(ticket_pk=ticket.pk, what_happened=what, note="A note.", outcome=outcome)


def describe_the_kiln_log():
    def it_lists_every_firing_newest_first_with_who_and_how_many(crew, crew_client, maker, make_member, kiln_guild):
        unloader = make_member(preferred_name="Sam Whitlock")
        bisque = _loaded(crew, _waiting(maker, "bisque"), _waiting(maker, "bisque"))
        KilnFiring.objects.filter(pk=bisque.pk).update(loaded_at=timezone.now() - timedelta(days=3))
        unload_kiln(firing_pk=bisque.pk, exceptions={}, by=unloader)
        glaze = _loaded(crew, _waiting(maker))

        response = crew_client.get(LOG)

        assert [f.pk for f in response.context["firings"]] == [glaze.pk, bisque.pk]
        body = response.content.decode()
        row = body[body.index(f'<tr data-firing="{bisque.pk}"') :]
        row = row[: row.index("</tr>")]
        assert "Bisque firing 1" in row and "Sam Whitlock" in row and crew.display_name in row
        assert '<td class="pl-kiln-num">2</td>' in row
        assert f'href="{reverse("kiln:firing", args=[bisque.pk])}"' in row
        in_kiln = body[body.index(f'<tr data-firing="{glaze.pk}"') :]
        in_kiln = in_kiln[: in_kiln.index("</tr>")]
        assert "In the kiln" in in_kiln and f'href="{reverse("kiln:unload", args=[glaze.pk])}"' in in_kiln
        assert response.context["unload_count"] == 1

    def it_counts_a_piece_sent_back_and_names_the_exceptions(crew, crew_client, maker, kiln_guild):
        out, ran, back = _waiting(maker), _waiting(maker), _waiting(maker)
        firing = _loaded(crew, out, ran, back)
        unload_kiln(
            firing_pk=firing.pk,
            exceptions={ran.pk: _note(ran, Outcome.FIRED, "glaze_ran"), back.pk: _note(back, Outcome.BACK_TO_QUEUE)},
            by=crew,
        )

        [logged] = kiln_log()

        assert logged.ticket_count == 3
        assert logged.exceptions_label == "2 exceptions: glaze ran, cracked"
        assert "2 exceptions: glaze ran, cracked" in crew_client.get(LOG).content.decode()

    def it_still_counts_a_piece_sent_back_once_it_is_loaded_again(crew, maker, kiln_guild):
        back = _waiting(maker)
        first = _loaded(crew, back)
        unload_kiln(firing_pk=first.pk, exceptions={back.pk: _note(back, Outcome.BACK_TO_QUEUE)}, by=crew)
        second = _loaded(crew, back)

        counts = {f.pk: f.ticket_count for f in kiln_log()}

        assert counts == {first.pk: 1, second.pk: 1}

    def it_names_one_exception_in_the_singular(crew, maker, kiln_guild):
        ticket = _waiting(maker)
        firing = _loaded(crew, ticket)
        unload_kiln(firing_pk=firing.pk, exceptions={ticket.pk: _note(ticket, Outcome.FIRED, "stuck")}, by=crew)

        assert kiln_log()[0].exceptions_label == "1 exception: stuck to the shelf"

    def it_holds_back_older_firings_behind_a_link(crew, crew_client, maker, kiln_guild):
        _loaded(crew, _waiting(maker))
        _loaded(crew, _waiting(maker))

        with patch("kiln.views.LOG_PAGE", 1):
            first_page = crew_client.get(LOG)
            everything = crew_client.get(f"{LOG}?older=1")

        assert len(first_page.context["firings"]) == 1 and first_page.context["has_older"]
        assert "Show older firings" in first_page.content.decode()
        assert len(everything.context["firings"]) == 2 and not everything.context["has_older"]

    def it_says_when_there_are_no_firings(crew_client):
        assert b"data-kiln-empty" in crew_client.get(LOG).content

    def it_is_for_the_crew_only(maker_client):
        assert maker_client.get(LOG).status_code == 403


def describe_a_firings_page():
    def it_lists_every_piece_that_went_in_with_the_crews_notes(crew, crew_client, maker, kiln_guild):
        out, ran, back = _waiting(maker), _waiting(maker), _waiting(maker)
        firing = _loaded(crew, out, ran, back)
        unload_kiln(
            firing_pk=firing.pk,
            exceptions={ran.pk: _note(ran, Outcome.FIRED, "glaze_ran"), back.pk: _note(back, Outcome.BACK_TO_QUEUE)},
            by=crew,
        )

        response = crew_client.get(reverse("kiln:firing", args=[firing.pk]))

        rows = response.context["rows"]
        assert [r.ticket.pk for r in rows] == sorted([out.pk, ran.pk, back.pk])
        assert [r.returned for r in rows] == [False, False, True]
        body = response.content.decode()
        assert "Fired, with a note: glaze ran" in body
        assert "Back to the queue: cracked" in body
        assert f"Unloaded {timezone.localtime(firing.loaded_at):%a}" in body
        for ticket in (out, ran, back):
            assert f'href="{reverse("kiln:detail", args=[ticket.pk])}"' in body

    def it_offers_unload_while_the_firing_is_in_the_kiln(crew, crew_client, maker, kiln_guild):
        firing = _loaded(crew, _waiting(maker))

        body = crew_client.get(reverse("kiln:firing", args=[firing.pk])).content.decode()

        assert "Still in the kiln" in body and f'href="{reverse("kiln:unload", args=[firing.pk])}"' in body

    def it_is_a_404_for_no_such_firing(crew_client):
        assert crew_client.get(reverse("kiln:firing", args=[99999])).status_code == 404

    def it_is_for_the_crew_only(crew, maker, maker_client, kiln_guild):
        firing = _loaded(crew, _waiting(maker))

        assert maker_client.get(reverse("kiln:firing", args=[firing.pk])).status_code == 403
