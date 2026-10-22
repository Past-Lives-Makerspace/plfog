"""The way back from the event page to the HQ: the RSVP card marks its link ``?from=hq``, and the event page opened
with that marker leads with "← {hub title}" to the hub the viewer may open. Without the marker, or with no such hub,
the page is unchanged, and the marker never reaches the page's canonical, QR or share links."""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import quote, unquote

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from hq.models import EventHub, HubCard
from hub.view_as import ROLE_MEMBER
from membership.models import CommunityEvent
from tests.hq.factories import EventHubFactory, HubCardFactory
from tests.membership.factories import CommunityEventFactory

pytestmark = pytest.mark.django_db

BACK = "data-event-hq-back"


def _page(event: CommunityEvent, query: str = "") -> str:
    return reverse("hub_event_detail", args=[event.pk]) + query


def _back_link(hub: EventHub) -> str:
    return f'<a href="{hub.get_absolute_url()}" class="pl-event-page__back" {BACK}>&larr; {hub.title}</a>'


@pytest.fixture
def event() -> CommunityEvent:
    return CommunityEventFactory(community=True)


@pytest.fixture
def hub(open_hub, event) -> EventHub:
    """The open hub, linked to the event, with its RSVP card open to everyone."""
    open_hub.event = event
    open_hub.save()
    HubCardFactory(hub=open_hub, kind=HubCard.Kind.RSVP, audience=HubCard.Audience.PUBLIC)
    return open_hub


def describe_the_rsvp_card():
    def it_marks_its_link_as_from_the_hq(hub, event):
        assert hub.cards.get(kind=HubCard.Kind.RSVP).url == _page(event, "?from=hq")

    def it_carries_the_marker_on_the_hq_page_button(hub, event):
        body = Client().get(hub.get_absolute_url()).content.decode()

        assert f'<a href="{_page(event, "?from=hq")}" class="pl-hq-card" data-hq-card-link>' in body


def describe_the_event_page():
    def it_leads_with_the_way_back_to_the_hub_when_the_marker_is_there(hub, event):
        body = Client().get(_page(event, "?from=hq")).content.decode()

        assert _back_link(hub) in body
        assert "&larr; Holiday Bizarre HQ</a>" in body
        assert body.index(BACK) < body.index("data-event-hero")

    def it_is_unchanged_without_the_marker(hub, event):
        plain = Client().get(_page(event)).content.decode()
        other = Client().get(_page(event, "?from=elsewhere")).content.decode()

        assert BACK not in plain
        assert BACK not in other
        assert "from=hq" not in plain

    def it_shows_nothing_with_the_marker_when_no_hub_is_linked_to_the_event(open_hub, event):
        """The open hub points at another event (or none), so this page has no HQ to go back to."""
        body = Client().get(_page(event, "?from=hq")).content.decode()

        assert BACK not in body

    def it_shows_nothing_to_a_member_when_the_linked_hub_is_closed(event, member_client):
        EventHubFactory(event=event, is_open=False)

        body = member_client.get(_page(event, "?from=hq")).content.decode()

        assert BACK not in body

    def it_links_an_admin_to_a_closed_hub_they_may_open(event, admin_client):
        closed = EventHubFactory(event=event, is_open=False, title="Next Year HQ")

        body = admin_client.get(_page(event, "?from=hq")).content.decode()

        assert _back_link(closed) in body

    def it_reads_the_previewed_role_so_an_admin_previewing_as_a_member_sees_no_closed_hub(event, admin_client):
        EventHubFactory(event=event, is_open=False)
        session = admin_client.session
        session["view_as_role"] = ROLE_MEMBER
        session.save()

        body = admin_client.get(_page(event, "?from=hq")).content.decode()

        assert BACK not in body

    def it_prefers_the_open_hub_when_an_admin_has_several(hub, event, admin_client):
        EventHubFactory(event=event, is_open=False, title="Draft HQ")

        body = admin_client.get(_page(event, "?from=hq")).content.decode()

        assert _back_link(hub) in body
        assert "Draft HQ" not in body

    def it_keeps_the_marker_off_the_canonical_qr_and_share_links(hub, event, admin_client):
        body = admin_client.get(_page(event, "?from=hq")).content.decode()

        assert event.public_url.endswith(_page(event))
        assert f'data-share-url="{event.public_url}"' in body
        assert f'data-share-copy="{event.public_url}"' in body
        assert "data-event-qr" in body
        assert "from=hq" not in event.public_url
        assert "from=hq" not in event.qr_url  # what the QR encodes
        assert "from=hq" not in body.split("data-share")[1]  # the share menu, the QR card and everything after

    def it_keeps_the_marker_on_the_pages_own_rsvp_forms(hub, event, member_client):
        body = member_client.get(_page(event, "?from=hq")).content.decode()

        assert f'action="{reverse("hub_event_rsvp", args=[event.pk])}?from=hq"' in body

    def it_adds_the_marker_after_the_date_on_a_series(open_hub):
        series = CommunityEventFactory(community=True, recurrence=CommunityEvent.Recurrence.WEEKLY)
        open_hub.event = series
        open_hub.save()

        body = Client().get(_page(series, "?from=hq")).content.decode()

        assert _back_link(open_hub) in body
        guest_action = body.split('class="pl-event-page__guest-form"')[0].rsplit("<form", 1)[1]
        assert "?date=" in guest_action
        assert "&amp;from=hq" in guest_action

    def it_keeps_the_way_back_through_the_log_in_link(hub, event):
        body = Client().get(_page(event, "?from=hq")).content.decode()

        login = body.split("data-guest-login")[0].rsplit("<a ", 1)[1]
        assert f'href="{reverse("account_login")}?next={quote(_page(event, "?from=hq"))}"' in login
        assert unquote(login.split("?next=")[1].split('"')[0]) == _page(event, "?from=hq")

    def it_leaves_the_log_in_link_on_the_plain_path_without_the_marker(hub, event):
        body = Client().get(_page(event)).content.decode()

        assert f'href="{reverse("account_login")}?next={_page(event)}"' in body

    def it_hands_the_marked_page_to_the_login_form_as_next(hub, event):
        """Following "log in" from the HQ's event page puts the marked page in the login form, where the sign in
        returns to."""
        client = Client()
        body = client.get(_page(event, "?from=hq")).content.decode()
        login_url = body.split("data-guest-login")[0].rsplit('href="', 1)[1].split('"')[0]

        login = client.get(login_url).content.decode()

        assert f'<input type="hidden" name="next" value="{_page(event, "?from=hq")}">' in login


def describe_rsvping_from_the_hq():
    @pytest.fixture(autouse=True)
    def _fresh_counters():
        cache.clear()

    def it_lands_a_member_back_on_the_page_with_the_way_back(hub, event, member_client):
        with patch.object(CommunityEvent, "refresh_discord_announcement"):
            response = member_client.post(reverse("hub_event_rsvp", args=[event.pk]) + "?from=hq")

        assert response.status_code == 302
        assert response["Location"] == _page(event, "?from=hq")
        assert _back_link(hub) in member_client.get(response["Location"]).content.decode()

    def it_lands_a_member_without_the_marker_on_the_plain_page(hub, event, member_client):
        with patch.object(CommunityEvent, "refresh_discord_announcement"):
            response = member_client.post(reverse("hub_event_rsvp", args=[event.pk]))

        assert response["Location"] == _page(event)

    def it_lands_a_guest_on_the_going_state_with_the_way_back(hub, event):
        client = Client()
        data = {"name": "Lee Organiser", "email": "lee@example.com", "phone": "", "party_size": "1", "website": ""}
        with patch.object(CommunityEvent, "refresh_discord_announcement"):
            response = client.post(reverse("hub_event_rsvp_guest", args=[event.pk]) + "?from=hq", data)

        assert response["Location"] == _page(event, "?from=hq&going=1")
        body = client.get(response["Location"]).content.decode()
        assert _back_link(hub) in body
        assert "data-rsvp-going" in body

    def it_keeps_the_way_back_on_a_refused_guest_form(hub, event):
        data = {"name": "", "email": "not an email", "phone": "", "party_size": "1", "website": ""}
        response = Client().post(reverse("hub_event_rsvp_guest", args=[event.pk]) + "?from=hq", data)

        assert response.status_code == 200
        assert _back_link(hub) in response.content.decode()
