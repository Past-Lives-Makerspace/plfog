"""The crew's pages cost the same queries for one ticket or message as for fifteen (#691 part 2).

The makers and reply authors here have no name on file, so every one of them is named by
their email (``kiln.models.member_name``); that email must come from the prefetch, never a
query per tile or per message.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from kiln.models import KilnReply, KilnTicket
from membership.models import Member
from tests.kiln.factories import KilnFlagFactory, KilnTicketFactory, KilnTicketPhotoFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def nameless(make_member) -> Callable[[], Member]:
    def _make() -> Member:
        member = make_member(preferred_name="", full_legal_name="")
        assert member.display_name == "" and member.primary_email
        return member

    return _make


def _queries(client: Client, url: str) -> int:
    client.get(url)  # warm anything cached per process (site settings, the changelog)
    with CaptureQueriesContext(connection) as captured:
        response = client.get(url)
    assert response.status_code == 200
    return len(captured.captured_queries)


def _waiting(maker: Member) -> KilnTicket:
    ticket = KilnTicketFactory(maker=maker, status="submitted", firing_type="glaze", submitted_at=timezone.now())
    KilnTicketPhotoFactory(ticket=ticket, is_cover=True)
    KilnFlagFactory(ticket=ticket)
    return ticket


def describe_load_the_kiln():
    def it_names_nameless_makers_without_a_query_per_tile(crew_client, nameless):
        url = reverse("kiln:load")
        first = _waiting(nameless())
        one = _queries(crew_client, url)
        for _ in range(14):
            _waiting(nameless())

        fifteen = _queries(crew_client, url)

        assert fifteen == one
        assert first.maker.primary_email in crew_client.get(url).content.decode()


def describe_the_crew_ticket_view():
    def it_names_nameless_authors_without_a_query_per_message(crew, crew_client, nameless):
        maker = nameless()
        ticket = _waiting(maker)
        url = reverse("kiln:detail", args=[ticket.pk])
        KilnReply.objects.create(ticket=ticket, author=nameless(), body="message 0")
        one = _queries(crew_client, url)
        for i in range(14):
            KilnReply.objects.create(ticket=ticket, author=nameless(), body=f"message {i + 1}")

        fifteen = _queries(crew_client, url)

        assert fifteen == one
        assert maker.primary_email in crew_client.get(url).content.decode()

    def it_names_the_nameless_loader_in_the_history(crew_client, nameless):
        from kiln.services import load_kiln

        loader = nameless()
        ticket = _waiting(nameless())
        load_kiln(firing_type="glaze", ticket_pks=[ticket.pk], by=loader)

        body = crew_client.get(reverse("kiln:detail", args=[ticket.pk])).content.decode()

        assert f"by {loader.primary_email} in Glaze firing 1" in body


def _unloaded_with_a_note(loader: Member, unloader: Member, maker: Member) -> None:
    """A firing of one nameless maker's piece, unloaded by another nameless member with an exception note."""
    from kiln.forms import ExceptionNote
    from kiln.services import load_kiln, unload_kiln

    ticket = _waiting(maker)
    result = load_kiln(firing_type="glaze", ticket_pks=[ticket.pk], by=loader)
    assert result.firing is not None
    note = ExceptionNote(ticket_pk=ticket.pk, what_happened="cracked", note="Cracked at the foot.", outcome="fired")
    unload_kiln(firing_pk=result.firing.pk, exceptions={ticket.pk: note}, by=unloader)


def describe_unload():
    def it_names_nameless_makers_without_a_query_per_ticket(crew, crew_client, nameless):
        from kiln.services import load_kiln

        tickets = [_waiting(nameless())]
        firing = load_kiln(firing_type="glaze", ticket_pks=[tickets[0].pk], by=nameless()).firing
        assert firing is not None
        url = reverse("kiln:unload", args=[firing.pk])
        one = _queries(crew_client, url)
        more = [_waiting(nameless()) for _ in range(14)]
        KilnTicket.objects.filter(pk__in=[t.pk for t in more]).update(status="loaded", firing=firing)

        fifteen = _queries(crew_client, url)

        assert fifteen == one
        body = crew_client.get(url).content.decode()
        assert tickets[0].maker.primary_email in body and firing.loaded_by.primary_email in body

    def it_lists_firings_in_the_kiln_without_a_query_per_firing(crew_client, nameless):
        from kiln.services import load_kiln

        url = reverse("kiln:unload_list")
        loader = nameless()
        load_kiln(firing_type="glaze", ticket_pks=[_waiting(nameless()).pk], by=loader)
        one = _queries(crew_client, url)
        for _ in range(14):
            load_kiln(firing_type="glaze", ticket_pks=[_waiting(nameless()).pk], by=nameless())

        fifteen = _queries(crew_client, url)

        assert fifteen == one
        assert loader.primary_email in crew_client.get(url).content.decode()


def describe_the_kiln_log():
    def it_names_nameless_loaders_and_unloaders_without_a_query_per_firing(crew_client, nameless):
        url = reverse("kiln:log")
        unloader = nameless()
        _unloaded_with_a_note(nameless(), unloader, nameless())
        one = _queries(crew_client, url)
        for _ in range(14):
            _unloaded_with_a_note(nameless(), nameless(), nameless())

        fifteen = _queries(crew_client, url)

        assert fifteen == one
        body = crew_client.get(url).content.decode()
        assert unloader.primary_email in body and "1 exception: cracked" in body

    def it_shows_a_firings_tickets_without_a_query_per_ticket(crew_client, nameless):
        def unloaded_firing(fired: int, returned: int) -> tuple[str, KilnTicket]:
            from kiln.forms import ExceptionNote
            from kiln.services import load_kiln, unload_kiln

            tickets = [_waiting(nameless()) for _ in range(fired + returned)]
            firing = load_kiln(firing_type="glaze", ticket_pks=[t.pk for t in tickets], by=nameless()).firing
            assert firing is not None
            notes = {
                t.pk: ExceptionNote(ticket_pk=t.pk, what_happened="stuck", note="Stuck.", outcome="back_to_queue")
                for t in tickets[fired:]
            }
            unload_kiln(firing_pk=firing.pk, exceptions=notes, by=nameless())
            return reverse("kiln:firing", args=[firing.pk]), tickets[-1]

        small, _ = unloaded_firing(fired=1, returned=1)
        large, returned = unloaded_firing(fired=8, returned=7)

        assert _queries(crew_client, large) == _queries(crew_client, small)
        assert returned.maker.primary_email in crew_client.get(large).content.decode()
