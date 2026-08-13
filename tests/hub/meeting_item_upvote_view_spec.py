"""BDD specs for the upvote-blocked-on-locked-minutes fix at the view layer
(hub_meeting_item_upvote).

See tests/membership/models/meeting_agenda_item_upvote_lock_spec.py for the
model-level MeetingLockedError coverage; this file covers the view translating
that exception into a 403 (mirroring the other mutating meeting routes' lock
contract) and the normal toggle otherwise.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from tests.membership.factories import MeetingAgendaItemFactory, MeetingFactory, UserFactory

pytestmark = pytest.mark.django_db


def _login(client: Client):
    user = UserFactory()
    client.force_login(user)
    return user


def describe_hub_meeting_item_upvote():
    def describe_on_an_approved_meeting():
        def it_returns_403(client: Client):
            item = MeetingAgendaItemFactory(meeting=MeetingFactory(approved=True))
            _login(client)
            response = client.post(reverse("hub_meeting_item_upvote", args=[item.pk]))
            assert response.status_code == 403

        def it_does_not_record_the_upvote(client: Client):
            item = MeetingAgendaItemFactory(meeting=MeetingFactory(approved=True))
            _login(client)
            client.post(reverse("hub_meeting_item_upvote", args=[item.pk]))
            assert item.upvoters.count() == 0

    def describe_on_a_draft_meeting():
        def it_returns_200_and_adds_the_upvote(client: Client):
            item = MeetingAgendaItemFactory(meeting=MeetingFactory())
            user = _login(client)
            response = client.post(reverse("hub_meeting_item_upvote", args=[item.pk]))
            assert response.status_code == 200
            assert item.upvoters.filter(pk=user.pk).exists()

        def it_toggles_off_on_a_second_post(client: Client):
            item = MeetingAgendaItemFactory(meeting=MeetingFactory())
            user = _login(client)
            url = reverse("hub_meeting_item_upvote", args=[item.pk])
            client.post(url)
            response = client.post(url)
            assert response.status_code == 200
            assert not item.upvoters.filter(pk=user.pk).exists()
