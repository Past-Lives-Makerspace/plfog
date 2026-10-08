"""BDD specs for the Eventbrite client (#652) and the listing bodies it posts (#716).

HTTP is mocked with respx; nothing reaches Eventbrite.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx

from classes.factories import ClassFaqFactory, ClassOfferingFactory
from classes.models import ClassOffering
from core.integrations.eventbrite import API_BASE, EventbriteClient, EventbriteError, estimate_fee_cents
from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db


def _credentials(settings: Any) -> None:
    settings.EVENTBRITE_PRIVATE_TOKEN = "secret"
    settings.EVENTBRITE_ORGANIZATION_ID = "org-1"
    settings.EVENTBRITE_VENUE_ID = "venue-1"


def _switch_on() -> None:
    config = SiteConfiguration.load()
    config.eventbrite_sync_enabled = True
    config.save(update_fields=["eventbrite_sync_enabled"])


def _client() -> EventbriteClient:
    return EventbriteClient(token="secret", organization_id="org-1", venue_id="venue-1")


def describe_from_settings():
    def it_is_disabled_while_the_site_toggle_is_off(settings: Any):
        _credentials(settings)

        assert EventbriteClient.from_settings().enabled is False

    def it_is_enabled_with_the_toggle_on_and_every_credential_set(settings: Any):
        _credentials(settings)
        _switch_on()

        client = EventbriteClient.from_settings()

        assert client.enabled is True
        assert (client.organization_id, client.venue_id) == ("org-1", "venue-1")

    @pytest.mark.parametrize("name", ["EVENTBRITE_PRIVATE_TOKEN", "EVENTBRITE_ORGANIZATION_ID", "EVENTBRITE_VENUE_ID"])
    def it_is_disabled_when_any_credential_is_blank(settings: Any, name: str):
        _credentials(settings)
        _switch_on()
        setattr(settings, name, "")

        assert EventbriteClient.from_settings().enabled is False

    def it_goes_dark_on_staging(settings: Any):
        _credentials(settings)
        _switch_on()
        settings.IS_STAGING = True

        assert EventbriteClient.from_settings().enabled is False


def describe_requests():
    @respx.mock
    def it_creates_the_event_under_the_organization_with_a_bearer_token():
        route = respx.post(f"{API_BASE}/organizations/org-1/events/").respond(json={"id": "ev-1"})

        assert _client().create_event({"event": {}}) == {"id": "ev-1"}

        assert route.calls.last.request.headers["Authorization"] == "Bearer secret"

    @respx.mock
    @pytest.mark.parametrize(
        ("call", "path"),
        [
            (lambda c: c.update_event("ev-1", {}), "/events/ev-1/"),
            (lambda c: c.create_ticket_class("ev-1", {}), "/events/ev-1/ticket_classes/"),
            (lambda c: c.update_ticket_class("ev-1", "tc-1", {}), "/events/ev-1/ticket_classes/tc-1/"),
            (lambda c: c.publish("ev-1"), "/events/ev-1/publish/"),
            (lambda c: c.unpublish("ev-1"), "/events/ev-1/unpublish/"),
        ],
    )
    def it_posts_to_each_endpoint(call: Any, path: str):
        route = respx.post(f"{API_BASE}{path}").respond(json={"id": "x"})

        call(_client())

        assert route.called

    @respx.mock
    def it_raises_with_the_status_on_an_error_response():
        respx.post(f"{API_BASE}/events/ev-1/unpublish/").respond(400, json={"error": "HAS_ORDERS"})

        with pytest.raises(EventbriteError) as raised:
            _client().unpublish("ev-1")

        assert raised.value.status == 400
        assert "HAS_ORDERS" in str(raised.value)

    @respx.mock
    def it_raises_without_a_status_when_eventbrite_cannot_be_reached():
        respx.post(f"{API_BASE}/events/ev-1/publish/").mock(side_effect=httpx.ConnectError("down"))

        with pytest.raises(EventbriteError) as raised:
            _client().publish("ev-1")

        assert raised.value.status is None

    @respx.mock
    def it_publishes_the_description_as_the_next_structured_content_version():
        respx.get(f"{API_BASE}/events/ev-1/structured_content/edit/").respond(json={"page_version_number": "3"})
        route = respx.post(f"{API_BASE}/events/ev-1/structured_content/4/").respond(json={})

        _client().set_description("ev-1", "<p>Hi</p>")

        body = json.loads(route.calls.last.request.content)
        assert body["publish"] is True
        assert body["modules"][0]["data"]["body"]["text"] == "<p>Hi</p>"

    @respx.mock
    def it_publishes_each_gallery_photo_as_an_image_module_after_the_text():
        # The body Eventbrite took on event 2003170172911 (#707): text first, then one image module per photo.
        respx.get(f"{API_BASE}/events/ev-1/structured_content/edit/").respond(json={"page_version_number": "3"})
        route = respx.post(f"{API_BASE}/events/ev-1/structured_content/4/").respond(json={})

        _client().set_description("ev-1", "<p>Hi</p>", ["111", "222"])

        assert json.loads(route.calls.last.request.content) == {
            "modules": [
                {"type": "text", "data": {"body": {"type": "text", "text": "<p>Hi</p>", "alignment": "left"}}},
                {"type": "image", "data": {"image": {"type": "image", "image_id": "111"}}},
                {"type": "image", "data": {"image": {"type": "image", "image_id": "222"}}},
            ],
            # #716: the widgets always go back; an event with none read sends none.
            "widgets": [],
            "publish": True,
            "purpose": "listing",
        }

    @respx.mock
    def it_uploads_a_gallery_photo_as_structured_content_media():
        ticket = respx.get(f"{API_BASE}/media/upload/").respond(
            json={
                "upload_url": "https://uploads.example.com/",
                "upload_data": {"key": "k"},
                "file_parameter_name": "file",
                "upload_token": "tok",
            }
        )
        respx.post("https://uploads.example.com/").respond(204)
        respx.post(f"{API_BASE}/media/upload/").respond(json={"id": 1195404694})

        assert _client().upload_content_image("bowl.jpg", b"jpeg") == "1195404694"

        assert ticket.calls.last.request.url.params["type"] == "image-structured-content"

    @respx.mock
    def it_uploads_the_main_image_as_an_event_logo():
        ticket = respx.get(f"{API_BASE}/media/upload/").respond(
            json={
                "upload_url": "https://uploads.example.com/",
                "upload_data": {},
                "file_parameter_name": "f",
                "upload_token": "t",
            }
        )
        respx.post("https://uploads.example.com/").respond(204)
        respx.post(f"{API_BASE}/media/upload/").respond(json={"id": 5})

        _client().upload_logo("hero.jpg", b"jpeg")

        assert ticket.calls.last.request.url.params["type"] == "image-event-logo"

    @respx.mock
    def it_uploads_an_image_in_three_steps_and_returns_its_media_id():
        respx.get(f"{API_BASE}/media/upload/").respond(
            json={
                "upload_url": "https://uploads.example.com/",
                "upload_data": {"key": "k"},
                "file_parameter_name": "file",
                "upload_token": "tok",
            }
        )
        bucket = respx.post("https://uploads.example.com/").respond(204)
        finish = respx.post(f"{API_BASE}/media/upload/").respond(json={"id": 77})

        assert _client().upload_logo("hero.jpg", b"jpeg") == "77"

        assert bucket.called
        assert json.loads(finish.calls.last.request.content) == {"upload_token": "tok"}

    @respx.mock
    def it_raises_when_the_image_bucket_cannot_be_reached():
        respx.get(f"{API_BASE}/media/upload/").respond(
            json={"upload_url": "https://uploads.example.com/", "upload_data": {}, "file_parameter_name": "f"}
        )
        respx.post("https://uploads.example.com/").mock(side_effect=httpx.ConnectError("down"))

        with pytest.raises(EventbriteError):
            _client().upload_logo("hero.jpg", b"jpeg")


def describe_malformed_answers():
    @respx.mock
    def it_raises_on_a_success_that_is_not_json():
        respx.post(f"{API_BASE}/events/ev-1/publish/").respond(200, text="<html>maintenance</html>")

        with pytest.raises(EventbriteError, match="not JSON"):
            _client().publish("ev-1")

    @respx.mock
    def it_raises_on_json_that_is_not_an_object():
        respx.post(f"{API_BASE}/events/ev-1/publish/").respond(json=True)

        with pytest.raises(EventbriteError, match="expected an object"):
            _client().publish("ev-1")

    @respx.mock
    def it_raises_when_the_description_version_is_missing():
        respx.get(f"{API_BASE}/events/ev-1/structured_content/edit/").respond(json={})

        with pytest.raises(EventbriteError, match="page_version_number"):
            _client().set_description("ev-1", "<p>Hi</p>")

    @respx.mock
    @pytest.mark.parametrize("version", ["x", None, {}, []])
    def it_raises_when_the_description_version_is_not_a_number(version: Any):
        respx.get(f"{API_BASE}/events/ev-1/structured_content/edit/").respond(json={"page_version_number": version})

        with pytest.raises(EventbriteError, match="Unreadable"):
            _client().set_description("ev-1", "<p>Hi</p>")

    @respx.mock
    def it_raises_when_the_upload_ticket_is_incomplete():
        respx.get(f"{API_BASE}/media/upload/").respond(json={"file_parameter_name": "file", "upload_url": "u"})

        with pytest.raises(EventbriteError, match="upload_data"):
            _client().upload_logo("hero.jpg", b"jpeg")

    @respx.mock
    def it_raises_when_the_image_bucket_refuses_the_file():
        respx.get(f"{API_BASE}/media/upload/").respond(
            json={"upload_url": "https://uploads.example.com/", "upload_data": {}, "file_parameter_name": "f"}
        )
        respx.post("https://uploads.example.com/").respond(403, text="denied")

        with pytest.raises(EventbriteError) as raised:
            _client().upload_logo("hero.jpg", b"jpeg")

        assert raised.value.status == 403


def describe_estimate_fee_cents():
    def it_adds_the_percentages_and_the_per_ticket_fee():
        # 3.7% + 2.9% of $50 is $3.30, plus $1.79.
        assert estimate_fee_cents(5000) == 509


def describe_order_and_ticket_reads():
    @respx.mock
    def it_fetches_an_order_with_its_attendees():
        route = respx.get(f"{API_BASE}/orders/o-1/", params={"expand": "attendees"}).respond(json={"id": "o-1"})

        assert _client().get_order("o-1") == {"id": "o-1"}
        assert route.called

    @respx.mock
    def it_fetches_a_ticket_class():
        route = respx.get(f"{API_BASE}/events/ev-1/ticket_classes/tc-1/").respond(json={"quantity_sold": 3})

        assert _client().get_ticket_class("ev-1", "tc-1") == {"quantity_sold": 3}
        assert route.called

    @respx.mock
    def it_posts_a_refund_to_the_order():
        route = respx.post(f"{API_BASE}/orders/o-1/refunds/").respond(json={})

        _client().refund_order("o-1", {"reason": "no_longer_able_to_attend"})

        assert json.loads(route.calls.last.request.content) == {"reason": "no_longer_able_to_attend"}

    def it_waits_only_as_long_as_the_clients_timeout():
        client = _client()
        client.timeout = 3.0

        with patch("httpx.request", return_value=httpx.Response(200, json={})) as request:
            client.get_ticket_class("ev-1", "tc-1")

        assert request.call_args.kwargs["timeout"] == 3.0


# Widgets as event 2003170172911's edit payload carried them after the owner added an FAQ in the dashboard.
CAROUSEL = {"id": "", "type": "herocarousel", "data": {"slides": [{"image_id": "m-1", "caption": "Kiln room"}]}}
DASHBOARD_FAQ = {"faqs": [{"question": "Test question", "answer": "Test answer"}]}


def _listing_routes(event_id: str, widgets: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Every call a class sync makes, answered as Eventbrite answers; returns the routes that carry a body."""
    edit: dict[str, Any] = {"page_version_number": "1", "modules": []}
    if widgets is not None:
        edit["widgets"] = widgets
    respx.get(f"{API_BASE}/events/{event_id}/structured_content/edit/").respond(json=edit)
    respx.get(f"{API_BASE}/events/{event_id}/ticket_classes/tc-1/").respond(json={"quantity_sold": 0})
    respx.post(f"{API_BASE}/events/{event_id}/ticket_classes/tc-1/").respond(json={})
    respx.post(f"{API_BASE}/events/{event_id}/publish/").respond(json={})
    return {
        "create": respx.post(f"{API_BASE}/organizations/org-1/events/").respond(
            json={"id": event_id, "status": "draft"}
        ),
        "update": respx.post(f"{API_BASE}/events/{event_id}/").respond(json={"id": event_id, "status": "live"}),
        "ticket": respx.post(f"{API_BASE}/events/{event_id}/ticket_classes/").respond(json={"id": "tc-1"}),
        "description": respx.post(f"{API_BASE}/events/{event_id}/structured_content/2/").respond(json={}),
    }


def _sent(route: Any) -> dict[str, Any]:
    return json.loads(route.calls.last.request.content)


def describe_the_listing_eventbrite_receives():
    """#716: the event's format and category, and the FAQ in the description, as posted to Eventbrite."""

    @pytest.fixture(autouse=True)
    def _integration_on(settings: Any) -> None:
        _credentials(settings)
        _switch_on()

    def _live(**kwargs: Any) -> ClassOffering:
        fields = {"ready": True, "status": ClassOffering.Status.PUBLISHED, "image": "", "gallery": 0}
        return ClassOfferingFactory(**{**fields, "eventbrite_enabled": True, **kwargs})

    def _listed(**kwargs: Any) -> ClassOffering:
        return _live(eventbrite_event_id="ev-9", eventbrite_ticket_class_id="tc-1", **kwargs)

    @respx.mock
    def it_creates_every_class_as_a_class_training_or_workshop_with_no_category_by_default():
        routes = _listing_routes("ev-1")

        _live().sync_eventbrite_listing()

        event = _sent(routes["create"])["event"]
        assert event["format_id"] == "9"
        assert "category_id" not in event
        assert "subcategory_id" not in event

    @respx.mock
    def it_creates_the_event_with_the_chosen_category_and_subcategory():
        routes = _listing_routes("ev-1")

        _live(eventbrite_category="105", eventbrite_subcategory="5014").sync_eventbrite_listing()

        event = _sent(routes["create"])["event"]
        assert (event["format_id"], event["category_id"], event["subcategory_id"]) == ("9", "105", "5014")

    @respx.mock
    def it_sends_a_category_alone_without_a_subcategory():
        routes = _listing_routes("ev-1")

        _live(eventbrite_category="119").sync_eventbrite_listing()

        event = _sent(routes["create"])["event"]
        assert event["category_id"] == "119"
        assert "subcategory_id" not in event

    @respx.mock
    def it_updates_a_listed_event_with_a_changed_category():
        routes = _listing_routes("ev-9")
        offering = _listed(eventbrite_category="105", eventbrite_subcategory="5014")
        offering.eventbrite_category, offering.eventbrite_subcategory = "119", "19003"

        offering.sync_eventbrite_listing()

        assert not routes["create"].called
        event = _sent(routes["update"])["event"]
        assert (event["format_id"], event["category_id"], event["subcategory_id"]) == ("9", "119", "19003")

    @respx.mock
    def it_updates_a_listed_event_with_no_category_as_before():
        routes = _listing_routes("ev-9")

        _listed().sync_eventbrite_listing()

        event = _sent(routes["update"])["event"]
        assert event["format_id"] == "9"
        assert not {"category_id", "subcategory_id"} & set(event)

    def _with_faq(offering: ClassOffering) -> ClassOffering:
        ClassFaqFactory(class_offering=offering, sort_order=1, question="What if I'm late?", answer="Text the shop.")
        ClassFaqFactory(
            class_offering=offering,
            sort_order=0,
            question="<b>Gloves</b> and boots?",
            answer="Both & more.\nSee https://pastlives.space\n\nAsk us.",
        )
        return offering

    def _text(route: Any) -> str:
        return _sent(route)["modules"][0]["data"]["body"]["text"]

    @respx.mock
    def it_writes_the_faq_as_both_faq_widgets_in_order_as_plain_text():
        routes = _listing_routes("ev-1")

        _with_faq(_live()).sync_eventbrite_listing()

        entries = [
            {"question": "Gloves and boots?", "answer": "Both & more.\nSee https://pastlives.space\n\nAsk us."},
            {"question": "What if I'm late?", "answer": "Text the shop."},
        ]
        assert _sent(routes["description"])["widgets"] == [
            {"id": "", "type": "faqs", "data": {"faqs": entries}},
            {"id": "", "type": "faq", "data": {"faqs": entries}},
        ]
        assert "Questions:" not in _text(routes["description"])

    @respx.mock
    def it_sends_every_other_widget_back_unchanged_and_replaces_the_dashboards_faq():
        dashboard = [
            CAROUSEL,
            {"id": "", "type": "faqs", "data": DASHBOARD_FAQ},
            {"id": "", "type": "faq", "data": DASHBOARD_FAQ},
        ]
        routes = _listing_routes("ev-9", widgets=dashboard)
        offering = _listed()
        ClassFaqFactory(class_offering=offering, question="Is the kiln vented?", answer="Yes.")

        offering.sync_eventbrite_listing()

        sent = _sent(routes["description"])["widgets"]
        assert sent[0] == CAROUSEL
        assert [w["type"] for w in sent] == ["herocarousel", "faqs", "faq"]
        assert sent[1]["data"] == {"faqs": [{"question": "Is the kiln vented?", "answer": "Yes."}]}

    @respx.mock
    def it_drops_the_faq_widgets_but_keeps_the_others_for_a_class_without_faq_rows():
        routes = _listing_routes("ev-9", widgets=[CAROUSEL, {"id": "", "type": "faqs", "data": DASHBOARD_FAQ}])

        _listed().sync_eventbrite_listing()

        assert _sent(routes["description"])["widgets"] == [CAROUSEL]
        assert "Questions:" not in _text(routes["description"])

    @respx.mock
    def it_puts_the_faq_in_the_description_after_the_dates_when_eventbrite_refuses_the_faq_widgets():
        routes = _listing_routes("ev-1", widgets=[CAROUSEL])
        routes["description"].side_effect = [
            httpx.Response(400, json={"error": "BAD_WIDGET"}),
            httpx.Response(200, json={}),
        ]
        offering = _with_faq(_live())

        offering.sync_eventbrite_listing()

        retry = _sent(routes["description"])
        assert retry["widgets"] == [CAROUSEL]
        faq = (
            "<p>Questions:</p>"
            "<p><strong>&lt;b&gt;Gloves&lt;/b&gt; and boots?</strong></p>"
            '<p>Both &amp; more.<br>See <a href="https://pastlives.space" rel="nofollow">https://pastlives.space</a></p>'
            "\n\n<p>Ask us.</p>"
            "<p><strong>What if I&#x27;m late?</strong></p><p>Text the shop.</p>"
        )
        html = retry["modules"][0]["data"]["body"]["text"]
        assert html.index("<p>Sessions:</p>") < html.index(faq) < html.index("Full details and booking")
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == ClassOffering.EventbriteSyncState.LISTED
        assert offering.eventbrite_sync_error.startswith(
            "The FAQ went into the description, Eventbrite refused its FAQ section: POST /events/ev-1/structured_content/2/: 400"
        )

    @respx.mock
    def it_records_a_failure_when_the_text_fallback_is_refused_too():
        routes = _listing_routes("ev-1")
        routes["description"].respond(400, json={"error": "BAD"})
        offering = _with_faq(_live())

        offering.sync_eventbrite_listing()

        offering.refresh_from_db()
        assert routes["description"].call_count == 2
        assert offering.eventbrite_sync_state == ClassOffering.EventbriteSyncState.FAILED
