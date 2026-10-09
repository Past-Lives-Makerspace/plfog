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
    settings.EVENTBRITE_ORGANIZER_ID = "organizer-1"


def _switch_on() -> None:
    config = SiteConfiguration.load()
    config.eventbrite_sync_enabled = True
    config.save(update_fields=["eventbrite_sync_enabled"])


def _client() -> EventbriteClient:
    return EventbriteClient(token="secret", organization_id="org-1", venue_id="venue-1", organizer_id="organizer-1")


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

    @pytest.mark.parametrize(
        "name",
        ["EVENTBRITE_PRIVATE_TOKEN", "EVENTBRITE_ORGANIZATION_ID", "EVENTBRITE_VENUE_ID", "EVENTBRITE_ORGANIZER_ID"],
    )
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
    def it_fetches_an_event():
        route = respx.get(f"{API_BASE}/events/ev-1/").respond(json={"id": "ev-1", "status": "draft"})

        assert _client().get_event("ev-1")["status"] == "draft"
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
    return {
        "publish": respx.post(f"{API_BASE}/events/{event_id}/publish/").respond(json={}),
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
    def it_writes_the_faq_as_both_faq_widgets_in_order_as_plain_text_without_addresses():
        routes = _listing_routes("ev-1")

        _with_faq(_live()).sync_eventbrite_listing()

        entries = [
            {"question": "Gloves and boots?", "answer": "Both & more.\nSee\n\nAsk us."},
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
    def it_retries_without_widgets_with_the_faq_after_the_dates_when_eventbrite_refuses_the_widgets():
        routes = _listing_routes("ev-1", widgets=[CAROUSEL])
        routes["description"].side_effect = [
            httpx.Response(400, json={"error": "BAD_WIDGET"}),
            httpx.Response(200, json={}),
        ]
        offering = _with_faq(_live())

        offering.sync_eventbrite_listing()

        retry = _sent(routes["description"])
        assert "widgets" not in retry
        faq = (
            "<p>Questions:</p>"
            "<p><strong>Gloves and boots?</strong></p>"
            "<p>Both &amp; more.<br>See</p>"
            "\n\n<p>Ask us.</p>"
            "<p><strong>What if I&#x27;m late?</strong></p><p>Text the shop.</p>"
        )
        html = retry["modules"][0]["data"]["body"]["text"]
        assert html.index("<p>Sessions:</p>") < html.index(faq)
        assert html.endswith(faq)
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == ClassOffering.EventbriteSyncState.LISTED
        assert offering.eventbrite_sync_error.startswith(
            "Eventbrite refused the page, so it went without its widgets (the FAQ went into the description as text): "
            "POST /events/ev-1/structured_content/2/: 400"
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

    @respx.mock
    def it_lists_a_class_with_no_faq_and_no_photos_when_eventbrite_refuses_its_widgets():
        routes = _listing_routes("ev-9", widgets=[CAROUSEL])
        routes["description"].side_effect = [
            httpx.Response(400, json={"error": "READ_ONLY"}),
            httpx.Response(200, json={}),
        ]
        offering = _listed()

        offering.sync_eventbrite_listing()

        first, retry = (json.loads(call.request.content) for call in routes["description"].calls)
        assert first["widgets"] == [CAROUSEL]
        assert "widgets" not in retry
        assert retry["modules"] == first["modules"]
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == ClassOffering.EventbriteSyncState.LISTED
        assert offering.eventbrite_sync_error.startswith(
            "Eventbrite refused the page, so it went without its widgets: "
        )

    @respx.mock
    def it_treats_a_null_widgets_list_as_none_to_keep():
        routes = _listing_routes("ev-1", widgets=None)
        respx.get(f"{API_BASE}/events/ev-1/structured_content/edit/").respond(
            json={"page_version_number": "1", "modules": [], "widgets": None}
        )
        offering = _live()

        offering.sync_eventbrite_listing()

        assert _sent(routes["description"])["widgets"] == []
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == ClassOffering.EventbriteSyncState.LISTED

    @respx.mock
    def it_keeps_a_widget_with_no_type_as_read():
        untyped = {"id": "w-1", "data": {"note": "no type"}}
        routes = _listing_routes("ev-1", widgets=[untyped, {"id": "", "type": "faq", "data": DASHBOARD_FAQ}])
        offering = _live()

        offering.sync_eventbrite_listing()

        assert _sent(routes["description"])["widgets"] == [untyped]
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == ClassOffering.EventbriteSyncState.LISTED


def describe_eventbrites_selling_rules():
    """#720: nothing in the listing sends buyers off Eventbrite, and every event names the organizer."""

    @pytest.fixture(autouse=True)
    def _integration_on(settings: Any) -> None:
        _credentials(settings)
        _switch_on()

    def _live(**kwargs: Any) -> ClassOffering:
        fields = {"ready": True, "status": ClassOffering.Status.PUBLISHED, "image": "", "gallery": 0}
        return ClassOfferingFactory(**{**fields, "eventbrite_enabled": True, **kwargs})

    def _text(route: Any) -> str:
        return json.loads(route.calls.last.request.content)["modules"][0]["data"]["body"]["text"]

    def _assert_no_address(text: str) -> None:
        for found in ("href", "http", "www.", "@", "pastlives.space", "aidu.glass", "example.org", "<img", "booking"):
            assert found not in text, (found, text)

    @respx.mock
    def it_sends_a_link_as_its_text_and_drops_every_address_and_the_booking_line():
        routes = _listing_routes("ev-1")
        offering = _live()  # the factory's ready trait writes its own description, so set it after
        offering.description = (
            '<p>Glass by <a href="https://www.aidu.glass">Aidu</a>. Mail ab@past-lives.org, or see '
            "https://pastlives.space, www.aidu.glass, classes.pastlives.space/x and example.org/path.</p>"
            '<p><img src="https://cdn.example.com/a.jpg"><strong>Bring gloves.</strong></p>'
        )

        offering.sync_eventbrite_listing()

        text = _text(routes["description"])
        _assert_no_address(text)
        assert offering.public_url not in text
        assert text.startswith("<p>Glass by Aidu. Mail , or see")
        assert "<strong>Bring gloves.</strong>" in text

    @respx.mock
    def it_drops_addresses_from_the_summary():
        routes = _listing_routes("ev-1")

        _live(subtitle="Book at classes.pastlives.space/x or mail a@pastlives.org today").sync_eventbrite_listing()

        assert _sent(routes["create"])["event"]["summary"] == "Book at or mail today"

    @respx.mock
    def it_falls_back_to_the_title_when_the_subtitle_is_only_an_address():
        routes = _listing_routes("ev-1")

        _live(title="Intro to Glass", subtitle="https://pastlives.space").sync_eventbrite_listing()

        assert _sent(routes["create"])["event"]["summary"] == "Intro to Glass"

    def _faqs(offering: ClassOffering) -> ClassOffering:
        rows = [
            ("What's your cancellation policy?", "Email classes@pastlives.space a week ahead."),
            ("Can I get my money back?", "Refunds go through the front desk."),
            ("What if I miss it?", "No-shows lose their seat."),
            ("Is it ever called off?", "We Cancel for snow."),
            ("Is the space accessible?", "There is a ramp; see pastlives.space/access."),
        ]
        for order, (question, answer) in enumerate(rows):
            ClassFaqFactory(class_offering=offering, sort_order=order, question=question, answer=answer)
        return offering

    @respx.mock
    def it_leaves_cancellation_refund_and_no_show_faqs_out_of_the_faq_widget():
        routes = _listing_routes("ev-1")

        _faqs(_live()).sync_eventbrite_listing()

        widgets = json.loads(routes["description"].calls.last.request.content)["widgets"]
        assert [w["data"]["faqs"] for w in widgets] == [
            [{"question": "Is the space accessible?", "answer": "There is a ramp; see"}]
        ] * 2

    @respx.mock
    def it_leaves_them_out_of_the_text_fallback_too():
        routes = _listing_routes("ev-1")
        routes["description"].side_effect = [
            httpx.Response(400, json={"error": "BAD_WIDGET"}),
            httpx.Response(200, json={}),
        ]

        _faqs(_live()).sync_eventbrite_listing()

        text = _text(routes["description"])
        _assert_no_address(text)
        assert "<p><strong>Is the space accessible?</strong></p><p>There is a ramp; see</p>" in text
        for left_out in ("cancellation", "money back", "miss it", "called off"):
            assert left_out not in text

    @respx.mock
    def it_creates_and_updates_every_event_under_the_organizer():
        created = _listing_routes("ev-1")
        _live().sync_eventbrite_listing()
        updated = _listing_routes("ev-9")
        _live(eventbrite_event_id="ev-9", eventbrite_ticket_class_id="tc-1").sync_eventbrite_listing()

        assert _sent(created["create"])["event"]["organizer_id"] == "organizer-1"
        assert _sent(updated["update"])["event"]["organizer_id"] == "organizer-1"

    def it_counts_a_blank_organizer_as_sync_off(settings: Any):
        settings.EVENTBRITE_ORGANIZER_ID = ""
        offering = _live()

        with patch("core.integrations.eventbrite.httpx.request") as request:
            offering.sync_eventbrite_listing()

        request.assert_not_called()
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == ClassOffering.EventbriteSyncState.PENDING


def describe_an_event_taken_down_on_the_http_layer():
    """#720 on the real client: what plfog sends once Eventbrite has taken an event it published down."""

    @pytest.fixture(autouse=True)
    def _integration_on(settings: Any) -> None:
        _credentials(settings)
        _switch_on()

    def _listed(**kwargs: Any) -> ClassOffering:
        fields = {"ready": True, "status": ClassOffering.Status.PUBLISHED, "image": "", "gallery": 0}
        listed = {"eventbrite_event_id": "ev-9", "eventbrite_ticket_class_id": "tc-1"}
        return ClassOfferingFactory(**{**fields, "eventbrite_enabled": True, **listed, **kwargs})

    def _answer_update(routes: dict[str, Any], status: str) -> None:
        routes["update"].return_value = httpx.Response(200, json={"id": "ev-9", "status": status})

    @respx.mock
    def it_sends_no_publish_when_a_published_event_reads_draft():
        routes = _listing_routes("ev-9")
        _answer_update(routes, "draft")
        offering = _listed(eventbrite_published=True)

        offering.sync_eventbrite_listing()

        assert not routes["publish"].called
        assert routes["description"].called
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == ClassOffering.EventbriteSyncState.ENDED

    @respx.mock
    def it_remembers_a_publish_that_timed_out_once_eventbrite_answers_live():
        routes = _listing_routes("ev-1")
        routes["publish"].side_effect = httpx.ReadTimeout("timed out")
        offering = ClassOfferingFactory(
            ready=True, status=ClassOffering.Status.PUBLISHED, image="", gallery=0, eventbrite_enabled=True
        )

        offering.sync_eventbrite_listing()  # created as a draft; the publish lands but times out
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == ClassOffering.EventbriteSyncState.FAILED
        assert offering.eventbrite_published is False

        respx.post(f"{API_BASE}/events/ev-1/").respond(json={"id": "ev-1", "status": "live"})
        offering.sync_eventbrite_listing()  # the retry reads it live
        offering.refresh_from_db()
        assert offering.eventbrite_published is True
        assert offering.eventbrite_sync_state == ClassOffering.EventbriteSyncState.LISTED

        respx.post(f"{API_BASE}/events/ev-1/").respond(json={"id": "ev-1", "status": "draft"})
        offering.sync_eventbrite_listing()  # Eventbrite takes it down

        assert routes["publish"].call_count == 1  # the one that timed out; never a second
        offering.refresh_from_db()
        assert offering.eventbrite_sync_error == "Unpublished on Eventbrite outside plfog; not republished."

    @respx.mock
    def it_reads_the_event_before_ending_and_unpublishes_one_that_is_live():
        respx.post(f"{API_BASE}/events/ev-9/ticket_classes/tc-1/").respond(json={})
        read = respx.get(f"{API_BASE}/events/ev-9/").respond(json={"id": "ev-9", "status": "live"})
        unpublish = respx.post(f"{API_BASE}/events/ev-9/unpublish/").respond(json={"unpublished": True})
        offering = _listed(eventbrite_enabled=False, eventbrite_published=True)

        offering.sync_eventbrite_listing()

        assert read.called
        assert unpublish.called
        offering.refresh_from_db()
        assert offering.eventbrite_published is False

    @respx.mock
    def it_reads_the_event_before_ending_and_leaves_a_taken_down_one_alone():
        respx.post(f"{API_BASE}/events/ev-9/ticket_classes/tc-1/").respond(json={})
        read = respx.get(f"{API_BASE}/events/ev-9/").respond(json={"id": "ev-9", "status": "draft"})
        unpublish = respx.post(f"{API_BASE}/events/ev-9/unpublish/").respond(json={"unpublished": True})
        offering = _listed(eventbrite_enabled=False, eventbrite_published=True)

        offering.sync_eventbrite_listing()

        assert read.called
        assert not unpublish.called
        offering.refresh_from_db()
        assert offering.eventbrite_published is True


def describe_the_address_and_faq_rules():
    """#720 review: any TLD counts as an address, plain numbers and abbreviations do not."""

    @pytest.mark.parametrize(
        ("typed", "sent"),
        [
            ("See pastlives.events now", "See now"),
            ("Photos at pastlives.gallery/x today", "Photos at today"),
            ("Learn at pastlives.academy", "Learn at"),
            ("Bring gloves, e.g. leather, i.e. thick ones", "Bring gloves, e.g. leather, i.e. thick ones"),
            ("Doors at 12.30, $5.00 for clay, 2.5 lbs", "Doors at 12.30, $5.00 for clay, 2.5 lbs"),
        ],
    )
    def it_drops_any_bare_host_and_keeps_abbreviations_times_and_prices(typed: str, sent: str):
        from core.integrations.eventbrite import _listing_text

        assert _listing_text(typed) == sent

    @pytest.mark.parametrize(
        "question", ["What about a no\u2013show?", "What about a no\u2014show?", "What about no shows?"]
    )
    def it_leaves_out_a_no_show_faq_however_the_dash_is_typed(question: str):
        from core.integrations.eventbrite import _sendable_faqs

        offering = ClassOfferingFactory()
        faq = ClassFaqFactory(class_offering=offering, question=question, answer="You lose the seat.")

        assert _sendable_faqs([faq]) == []
