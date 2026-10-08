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
