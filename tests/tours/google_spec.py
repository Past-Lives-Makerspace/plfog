"""The HTTP client for a Tour Guide's own Google Calendar (#951), Google mocked with respx."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from django.utils import timezone

from tours import google

pytestmark = pytest.mark.django_db

EVENTS = f"{google.API_URL}/calendars/cal%40example.com/events"
CALENDAR_LIST = f"{google.API_URL}/users/me/calendarList"
GRANTED = " ".join(google.SCOPES)


@pytest.fixture(autouse=True)
def _client(settings):
    settings.GOOGLE_OAUTH_CLIENT_ID = "client-id"
    settings.GOOGLE_OAUTH_CLIENT_SECRET = "client-secret"
    settings.MEMBER_BASE_URL = "https://members.example.org"


def _error(status: int, reason: str = "", message: str = "nope") -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": message, "errors": [{"reason": reason}]}})


def describe_is_configured():
    def it_needs_both_halves_of_the_client(settings):
        assert google.is_configured()
        settings.GOOGLE_OAUTH_CLIENT_SECRET = ""
        assert not google.is_configured()

    def it_stays_dark_on_staging(settings):
        settings.IS_STAGING = True
        assert not google.is_configured()


def describe_authorize_url():
    def it_asks_for_offline_access_with_the_two_scopes_and_the_members_callback():
        url = google.authorize_url("state-123", login_hint="gina@example.com")
        query = parse_qs(urlsplit(url).query)
        assert url.startswith(google.AUTHORIZE_URL)
        assert query["scope"] == [GRANTED]
        assert query["access_type"] == ["offline"]
        assert query["prompt"] == ["consent"]
        assert query["state"] == ["state-123"]
        assert query["login_hint"] == ["gina@example.com"]
        assert query["redirect_uri"] == ["https://members.example.org/facility-tours/google/callback/"]

    def it_leaves_out_an_empty_login_hint():
        assert "login_hint" not in parse_qs(urlsplit(google.authorize_url("s")).query)

    def it_names_exactly_the_narrow_scopes():
        assert google.SCOPES == (
            "https://www.googleapis.com/auth/calendar.events.owned",
            "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
        )


def describe_exchange_code():
    @respx.mock
    def it_returns_the_tokens_and_their_expiry():
        route = respx.post(google.TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 120, "scope": GRANTED}
            )
        )
        tokens = google.exchange_code("the-code")
        assert (tokens.access_token, tokens.refresh_token) == ("at", "rt")
        assert timezone.now() < tokens.expires_at <= timezone.now() + timedelta(seconds=120)
        sent = parse_qs(route.calls.last.request.content.decode())
        assert sent["grant_type"] == ["authorization_code"]
        assert sent["code"] == ["the-code"]
        assert sent["client_secret"] == ["client-secret"]

    @respx.mock
    def it_refuses_a_grant_missing_a_scope():
        respx.post(google.TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "at", "refresh_token": "rt", "scope": google.EVENTS_SCOPE}
            )
        )
        with pytest.raises(google.GoogleMissingScopeError):
            google.exchange_code("code")

    @respx.mock
    def it_refuses_an_answer_with_no_refresh_token():
        respx.post(google.TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "at", "scope": GRANTED})
        )
        with pytest.raises(google.GoogleError, match="no refresh token"):
            google.exchange_code("code")

    @respx.mock
    def it_refuses_an_answer_with_no_access_token():
        respx.post(google.TOKEN_URL).mock(return_value=httpx.Response(200, json={"scope": GRANTED}))
        with pytest.raises(google.GoogleError, match="no access token"):
            google.exchange_code("code")


def describe_refresh():
    @respx.mock
    def it_defaults_the_lifetime_to_an_hour():
        respx.post(google.TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "new"}))
        tokens = google.refresh("rt")
        assert tokens.access_token == "new"
        assert tokens.refresh_token == ""
        assert tokens.expires_at > timezone.now() + timedelta(minutes=59)

    @respx.mock
    def it_calls_a_revoked_token_an_auth_error():
        respx.post(google.TOKEN_URL).mock(
            return_value=httpx.Response(
                400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
            )
        )
        with pytest.raises(google.GoogleAuthError, match="expired or revoked"):
            google.refresh("rt")

    @respx.mock
    def it_says_so_when_invalid_grant_has_no_description():
        respx.post(google.TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
        with pytest.raises(google.GoogleAuthError, match="expired or revoked"):
            google.refresh("rt")

    @respx.mock
    def it_calls_our_own_setup_error_worth_retrying():
        respx.post(google.TOKEN_URL).mock(return_value=httpx.Response(401, json={"error": "invalid_client"}))
        with pytest.raises(google.GoogleError) as raised:
            google.refresh("rt")
        assert not isinstance(raised.value, google.GoogleAuthError)

    @respx.mock
    def it_wraps_a_network_failure():
        respx.post(google.TOKEN_URL).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(google.GoogleError, match="token call failed"):
            google.refresh("rt")

    @respx.mock
    def it_reads_an_answer_that_is_not_json_as_empty():
        respx.post(google.TOKEN_URL).mock(return_value=httpx.Response(502, text="<html>bad gateway</html>"))
        with pytest.raises(google.GoogleError, match="502"):
            google.refresh("rt")


def describe_revoke():
    @respx.mock
    def it_revokes_the_token():
        route = respx.post(google.REVOKE_URL).mock(return_value=httpx.Response(200))
        google.revoke("rt")
        assert parse_qs(route.calls.last.request.content.decode()) == {"token": ["rt"]}

    @respx.mock
    def it_accepts_a_token_already_dead():
        respx.post(google.REVOKE_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_token"}))
        google.revoke("rt")

    @respx.mock
    def it_raises_on_any_other_answer():
        respx.post(google.REVOKE_URL).mock(return_value=httpx.Response(503, json=[]))
        with pytest.raises(google.GoogleError, match="503"):
            google.revoke("rt")

    @respx.mock
    def it_wraps_a_network_failure():
        respx.post(google.REVOKE_URL).mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(google.GoogleError, match="revoke failed"):
            google.revoke("rt")


def describe_list_calendars():
    @respx.mock
    def it_lists_owned_calendars_over_every_page_main_one_first():
        route = respx.get(CALENDAR_LIST).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "items": [{"id": "zed@group", "summary": "Zed"}, {"id": "a@group", "summaryOverride": "Art"}],
                        "nextPageToken": "p2",
                    },
                ),
                httpx.Response(200, json={"items": [{"id": "gina@example.com", "summary": "Gina", "primary": True}]}),
            ]
        )
        calendars = google.list_calendars("at")
        assert [(cal.id, cal.name, cal.primary) for cal in calendars] == [
            ("gina@example.com", "Gina", True),
            ("a@group", "Art", False),
            ("zed@group", "Zed", False),
        ]
        first, second = (call.request for call in route.calls)
        assert first.headers["Authorization"] == "Bearer at"
        assert first.url.params["minAccessRole"] == "owner"
        assert second.url.params["pageToken"] == "p2"
        assert calendars[0].as_json() == {"id": "gina@example.com", "name": "Gina", "primary": True}

    @respx.mock
    def it_stops_listing_after_the_page_cap(monkeypatch):
        monkeypatch.setattr(google, "MAX_EVENT_PAGES", 1)
        respx.get(CALENDAR_LIST).mock(
            return_value=httpx.Response(200, json={"items": [{"id": "a@group"}], "nextPageToken": "more"})
        )
        assert [calendar.name for calendar in google.list_calendars("at")] == ["a@group"]


def describe_list_events():
    @respx.mock
    def it_reads_every_page_with_repeats_expanded():
        route = respx.get(EVENTS).mock(
            side_effect=[
                httpx.Response(200, json={"items": [{"id": "a"}, "junk"], "nextPageToken": "next"}),
                httpx.Response(200, json={"items": [{"id": "b"}]}),
            ]
        )
        start = datetime(2030, 1, 1, tzinfo=UTC)
        events = google.list_events("at", "cal@example.com", start, start + timedelta(days=30))
        assert [event["id"] for event in events] == ["a", "b"]
        params = route.calls[0].request.url.params
        assert params["singleEvents"] == "true"
        assert params["timeMin"] == "2030-01-01T00:00:00+00:00"
        assert "extendedProperties/private" in params["fields"]

    @respx.mock
    def it_stops_after_the_page_cap(monkeypatch, caplog):
        monkeypatch.setattr(google, "MAX_EVENT_PAGES", 2)
        respx.get(EVENTS).mock(return_value=httpx.Response(200, json={"items": [{"id": "x"}], "nextPageToken": "more"}))
        start = datetime(2030, 1, 1, tzinfo=UTC)
        assert len(google.list_events("at", "cal@example.com", start, start + timedelta(days=1))) == 2
        assert "more than 2 pages" in caplog.text


def describe_writes():
    @respx.mock
    def it_creates_an_event_with_our_id_and_no_invites():
        route = respx.post(EVENTS).mock(return_value=httpx.Response(200, json={"id": "pltour1"}))
        assert google.insert_event("at", "cal@example.com", {"id": "pltour1", "summary": "x"}) == "pltour1"
        assert route.calls.last.request.url.params["sendUpdates"] == "none"

    @respx.mock
    def it_falls_back_to_our_id_when_google_echoes_none():
        respx.post(EVENTS).mock(return_value=httpx.Response(200, json={}))
        assert google.insert_event("at", "cal@example.com", {"id": "pltour2"}) == "pltour2"

    @respx.mock
    def it_replaces_an_event():
        route = respx.put(f"{EVENTS}/ev1").mock(return_value=httpx.Response(200, json={"id": "ev1"}))
        google.update_event("at", "cal@example.com", "ev1", {"summary": "y"})
        assert route.called

    @respx.mock
    def it_deletes_an_event_and_accepts_one_already_gone():
        respx.delete(f"{EVENTS}/ev1").mock(return_value=httpx.Response(204))
        respx.delete(f"{EVENTS}/gone").mock(return_value=_error(410, "deleted"))
        google.delete_event("at", "cal@example.com", "ev1")
        google.delete_event("at", "cal@example.com", "gone")


def describe_api_errors():
    @pytest.mark.parametrize(
        ("response", "error"),
        [
            (_error(401, "authError"), google.GoogleAuthError),
            (_error(403, "insufficientPermissions"), google.GoogleAuthError),
            (_error(403, "forbidden"), google.GoogleAuthError),
            (_error(404, "notFound"), google.GoogleNotFoundError),
            (_error(409, "duplicate"), google.GoogleConflictError),
        ],
    )
    @respx.mock
    def it_sorts_the_answer_into_its_error(response: httpx.Response, error: type[Exception]):
        respx.put(f"{EVENTS}/ev").mock(return_value=response)
        with pytest.raises(error):
            google.update_event("at", "cal@example.com", "ev", {})

    @respx.mock
    def it_treats_a_rate_limit_as_worth_retrying():
        respx.put(f"{EVENTS}/ev").mock(return_value=_error(403, "rateLimitExceeded"))
        with pytest.raises(google.GoogleError) as raised:
            google.update_event("at", "cal@example.com", "ev", {})
        assert not isinstance(raised.value, google.GoogleAuthError)

    @respx.mock
    def it_names_the_status_of_a_server_error_with_no_body():
        respx.put(f"{EVENTS}/ev").mock(return_value=httpx.Response(500, json={"error": "flat"}))
        with pytest.raises(google.GoogleError, match="500"):
            google.update_event("at", "cal@example.com", "ev", {})

    @respx.mock
    def it_wraps_a_network_failure():
        respx.put(f"{EVENTS}/ev").mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(google.GoogleError, match="call failed"):
            google.update_event("at", "cal@example.com", "ev", {})
