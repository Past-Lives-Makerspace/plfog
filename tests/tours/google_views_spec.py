"""Connect, pick a calendar and Disconnect on the My Hours tab, and the admin's view of each guide's
calendar (#951). Google is mocked with respx."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Member
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourCalendarConnectionFactory
from tours import google
from tours.models import TourCalendarConnection, TourCalendarSync
from tours.views import GOOGLE_STATE_SESSION_KEY

pytestmark = pytest.mark.django_db

HOURS = reverse("tours:hours")
CONNECT = reverse("tours:google_connect")
CALLBACK = reverse("tours:google_callback")
CHOOSE = reverse("tours:google_calendar")
DISCONNECT = reverse("tours:google_disconnect")
TOURS_CAL = "tours@group.calendar.google.com"
GRANTED = " ".join(google.SCOPES)


def _messages(response) -> list[str]:
    return [str(message) for message in response.context["messages"]] if response.context else []


def _with_state(client: Client, state: str = "the-state") -> Client:
    session = client.session
    session[GOOGLE_STATE_SESSION_KEY] = state
    session.save()
    return client


def _card(response) -> str:
    """The Google Calendar card's state marker on the My Hours tab."""
    html = response.content.decode()
    start = html.index('data-tour-gcal="') + len('data-tour-gcal="')
    return html[start : html.index('"', start)]


def describe_connect():
    def it_refuses_a_member_without_the_permission(plain: Member):
        assert signed_in(plain).get(CONNECT).status_code == 403

    def it_says_so_while_google_is_not_set_up(guide: Member):
        response = signed_in(guide).get(CONNECT, follow=True)
        assert response.redirect_chain[-1][0] == HOURS
        assert "Connecting a Google Calendar is not set up yet." in _messages(response)

    @pytest.mark.usefixtures("google_on")
    def it_sends_the_guide_to_google_with_a_state_kept_in_the_session(guide: Member):
        client = signed_in(guide)
        response = client.get(CONNECT)
        assert response.status_code == 302
        url = urlsplit(response["Location"])
        assert f"{url.scheme}://{url.netloc}{url.path}" == google.AUTHORIZE_URL
        query = parse_qs(url.query)
        assert query["state"] == [client.session[GOOGLE_STATE_SESSION_KEY]]
        assert query["login_hint"] == [guide.user.email]


@pytest.mark.usefixtures("google_on")
def describe_callback():
    def it_says_nothing_changed_when_the_guide_cancels_at_google(guide: Member):
        response = _with_state(signed_in(guide)).get(CALLBACK, {"error": "access_denied"}, follow=True)
        assert "Google Calendar was not connected." in _messages(response)
        assert not TourCalendarConnection.objects.exists()

    @pytest.mark.parametrize(
        ("session_state", "params"),
        [
            ("the-state", {"state": "forged", "code": "c"}),
            ("the-state", {"state": "the-state"}),
            ("", {"state": "the-state", "code": "c"}),
        ],
    )
    def it_refuses_a_state_that_does_not_match(guide: Member, session_state: str, params: dict[str, str]):
        client = signed_in(guide)
        if session_state:
            _with_state(client, session_state)
        with respx.mock(assert_all_called=False) as mock:
            response = client.get(CALLBACK, params, follow=True)
        assert not mock.calls
        assert any("could not check that Google sign in" in text for text in _messages(response))

    def it_works_once(guide: Member):
        client = _with_state(signed_in(guide))
        with respx.mock(assert_all_called=False):
            client.get(CALLBACK, {"error": "access_denied"})
        assert GOOGLE_STATE_SESSION_KEY not in client.session

    def it_says_so_when_google_was_switched_off_meanwhile(guide: Member, settings):
        client = _with_state(signed_in(guide))
        settings.GOOGLE_OAUTH_CLIENT_ID = ""
        response = client.get(CALLBACK, {"state": "the-state", "code": "c"}, follow=True)
        assert "Connecting a Google Calendar is not set up yet." in _messages(response)

    @respx.mock
    def it_connects_and_lands_on_my_hours(guide: Member):
        respx.post(google.TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600, "scope": GRANTED}
            )
        )
        respx.get(f"{google.API_URL}/users/me/calendarList").mock(
            return_value=httpx.Response(
                200, json={"items": [{"id": "gina@example.com", "summary": "Gina", "primary": True}]}
            )
        )
        response = _with_state(signed_in(guide)).get(CALLBACK, {"state": "the-state", "code": "c"}, follow=True)
        assert response.redirect_chain[-1][0] == HOURS
        assert any(text.startswith("Google Calendar connected.") for text in _messages(response))
        assert TourCalendarConnection.objects.get(guide=guide).google_email == "gina@example.com"
        assert _card(response) == "connected"

    @respx.mock
    def it_asks_to_leave_every_box_ticked(guide: Member):
        respx.post(google.TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "at", "refresh_token": "rt", "scope": google.EVENTS_SCOPE}
            )
        )
        response = _with_state(signed_in(guide)).get(CALLBACK, {"state": "the-state", "code": "c"}, follow=True)
        assert any("leave every box ticked" in text for text in _messages(response))
        assert not TourCalendarConnection.objects.exists()

    @respx.mock
    def it_says_to_try_again_when_google_fails(guide: Member):
        respx.post(google.TOKEN_URL).mock(return_value=httpx.Response(500, json={}))
        response = _with_state(signed_in(guide)).get(CALLBACK, {"state": "the-state", "code": "c"}, follow=True)
        assert "We could not connect your Google Calendar. Please try again." in _messages(response)


@pytest.mark.usefixtures("google_on")
def describe_choose_calendar():
    def it_writes_tours_to_the_picked_calendar(guide: Member):
        TourCalendarConnectionFactory(guide=guide)
        booking = TourBookingFactory(guide=guide)
        response = signed_in(guide).post(CHOOSE, {"calendar": TOURS_CAL}, follow=True)
        assert "Tours will be written to Tours." in _messages(response)
        assert TourCalendarConnection.objects.get(guide=guide).calendar_id == TOURS_CAL
        assert TourCalendarSync.objects.get(booking=booking).due

    def it_changes_nothing_when_the_same_calendar_is_saved(guide: Member):
        TourCalendarConnectionFactory(guide=guide, busy_checked_at=timezone.now())
        signed_in(guide).post(CHOOSE, {"calendar": "primary"})
        assert TourCalendarConnection.objects.get(guide=guide).busy_checked_at is not None

    def it_refuses_a_calendar_google_never_listed(guide: Member):
        TourCalendarConnectionFactory(guide=guide)
        response = signed_in(guide).post(CHOOSE, {"calendar": "someone-else@group"}, follow=True)
        assert "Pick one of your calendars from the list." in _messages(response)
        assert TourCalendarConnection.objects.get(guide=guide).calendar_id == "primary"

    def it_404s_without_a_working_connection(guide: Member):
        TourCalendarConnectionFactory(guide=guide, status=TourCalendarConnection.Status.BROKEN)
        assert signed_in(guide).post(CHOOSE, {"calendar": "primary"}).status_code == 404


@pytest.mark.usefixtures("google_on")
def describe_disconnect():
    @respx.mock
    def it_revokes_and_deletes_the_token(guide: Member):
        respx.post(google.REVOKE_URL).mock(return_value=httpx.Response(200))
        TourCalendarConnectionFactory(guide=guide)
        response = signed_in(guide).post(DISCONNECT, follow=True)
        assert "Google Calendar disconnected. Tours already on it stay there." in _messages(response)
        assert not TourCalendarConnection.objects.exists()
        assert _card(response) == "none"

    @respx.mock
    def it_says_where_to_finish_when_google_does_not_confirm(guide: Member):
        respx.post(google.REVOKE_URL).mock(side_effect=httpx.ConnectError("down"))
        TourCalendarConnectionFactory(guide=guide)
        response = signed_in(guide).post(DISCONNECT, follow=True)
        assert any("Google did not confirm it" in text for text in _messages(response))
        assert not TourCalendarConnection.objects.exists()

    def it_only_disconnects_the_viewers_own(guide: Member):
        other = TourCalendarConnectionFactory(guide=make_guide())
        assert signed_in(guide).post(DISCONNECT).status_code == 404
        assert TourCalendarConnection.objects.filter(pk=other.pk).exists()

    def it_is_post_only(guide: Member):
        TourCalendarConnectionFactory(guide=guide)
        assert signed_in(guide).get(DISCONNECT).status_code == 405


def describe_the_card():
    def it_says_the_feature_is_not_set_up(guide: Member):
        response = signed_in(guide).get(HOURS)
        assert _card(response) == "dormant"
        assert b"data-tour-gcal-connect" not in response.content

    @pytest.mark.usefixtures("google_on")
    def it_offers_connect_with_boost_off(guide: Member):
        html = signed_in(guide).get(HOURS).content.decode()
        assert f'href="{CONNECT}"' in html and 'hx-boost="false"' in html

    @pytest.mark.usefixtures("google_on")
    def it_shows_the_account_the_picker_and_disconnect(guide: Member):
        TourCalendarConnectionFactory(guide=guide, busy_checked_at=timezone.now(), calendar_id=TOURS_CAL)
        response = signed_in(guide).get(HOURS)
        html = response.content.decode()
        assert _card(response) == "connected"
        assert "gina@example.com" in html
        assert f'<option value="{TOURS_CAL}" selected>Tours</option>' in html
        assert '<option value="primary">Gina Guide (main calendar)</option>' in html
        assert "data-tour-gcal-disconnect" in html and "Disconnect Google Calendar?" in html
        assert "Busy times last read" in html

    @pytest.mark.usefixtures("google_on")
    def it_says_when_busy_times_have_not_been_read(guide: Member):
        TourCalendarConnectionFactory(guide=guide)
        assert b"Busy times are read within 15 minutes." in signed_in(guide).get(HOURS).content

    @pytest.mark.usefixtures("google_on")
    def it_shows_reconnect_when_broken(guide: Member):
        TourCalendarConnectionFactory(guide=guide, status=TourCalendarConnection.Status.BROKEN)
        response = signed_in(guide).get(HOURS)
        assert _card(response) == "broken"
        assert b"data-tour-gcal-reconnect" in response.content
        assert b"data-tour-gcal-form" not in response.content


@pytest.mark.usefixtures("google_on")
def describe_the_admins_view():
    def it_names_each_guides_calendar_on_the_guide_picker(admin: Member):
        connected = make_guide(full_legal_name="Cora Connected")
        TourCalendarConnectionFactory(guide=connected)
        broken = make_guide(full_legal_name="Bo Broken")
        TourCalendarConnectionFactory(guide=broken, status=TourCalendarConnection.Status.BROKEN)
        make_guide(full_legal_name="Nell None")
        html = signed_in(admin).get(HOURS).content.decode()
        assert "Cora Connected · Calendar connected" in html
        assert "Bo Broken · Calendar needs reconnecting" in html
        assert "Nell None · No calendar" in html

    def it_leaves_the_picker_plain_while_google_is_not_set_up(admin: Member, settings):
        make_guide(full_legal_name="Nell None")
        settings.GOOGLE_OAUTH_CLIENT_ID = ""
        html = signed_in(admin).get(HOURS).content.decode()
        assert ">Nell None</option>" in html

    def it_reads_another_guides_state_without_any_controls(admin: Member):
        other = make_guide(full_legal_name="Cora Connected")
        TourCalendarConnectionFactory(guide=other, busy_checked_at=timezone.now() - timedelta(minutes=3))
        response = signed_in(admin).get(HOURS, {"guide": other.pk})
        html = response.content.decode()
        assert _card(response) == "connected"
        assert "Connected as <strong>gina@example.com</strong>. Tours are written to Gina Guide." in html
        assert "data-tour-gcal-disconnect" not in html and "data-tour-gcal-connect" not in html

    def it_says_when_another_guides_busy_times_were_never_read(admin: Member):
        other = make_guide(full_legal_name="Cora Connected")
        TourCalendarConnectionFactory(guide=other)
        assert b"Busy times not read yet." in signed_in(admin).get(HOURS, {"guide": other.pk}).content

    def it_reads_another_guides_broken_or_missing_calendar(admin: Member):
        broken = make_guide(full_legal_name="Bo Broken")
        TourCalendarConnectionFactory(
            guide=broken, status=TourCalendarConnection.Status.BROKEN, broken_at=timezone.now()
        )
        none = make_guide(full_legal_name="Nell None")
        client = signed_in(admin)
        assert b"Needs reconnecting since" in client.get(HOURS, {"guide": broken.pk}).content
        assert b"Nell None has not connected a Google Calendar" in client.get(HOURS, {"guide": none.pk}).content
