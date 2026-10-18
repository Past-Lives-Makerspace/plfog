"""BDD specs for the Eventbrite push read through :class:`ListingSource` (events round, B1).

An in-memory source (a party, not a class) stands in for the model the push reads, so what the
protocol promises is checked apart from any class: the format and venue in the event body, a
free or priced ticket, a fixed capacity that never reads Eventbrite's sales, an empty FAQ, and
the sync fields a list, an end and a takedown leave behind. Every Eventbrite call goes to the
recording :class:`FakeEventbrite`; nothing reaches Eventbrite.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

from classes import eventbrite_categories as shim
from classes.models import ClassOffering
from core.integrations import eventbrite_choices as choices
from core.integrations.eventbrite import (
    EVENTBRITE_EVENT_RULE_PARTS,
    EVENTBRITE_RULE_PARTS,
    EventbriteClient,
    EventbriteError,
    EventbriteSync,
    ListingCheck,
    ListingSchedule,
    _event_body,
    _faq_html,
    _ticket_body,
    check_listing,
    push_ticket_quantity,
    sync_listing,
)
from tests.classes.eventbrite_listing_spec import FakeEventbrite

State = choices.EventbriteSyncState
# Friday December 4, 2026, 5 to 10 PM in Portland, as UTC.
STARTS = datetime(2026, 12, 5, 1, 0, tzinfo=UTC)
ENDS = datetime(2026, 12, 5, 6, 0, tzinfo=UTC)


class Party:
    """The smallest listing source: one dated event with a fixed number of tickets and no FAQ."""

    EventbriteSyncState = choices.EventbriteSyncState
    quantity_tracks_sales = False
    eventbrite_format_id = choices.EventbriteFormat.PARTY_SOCIAL
    listing_image = None
    needs_eventbrite_agreement = False

    def __init__(self, **fields: Any) -> None:
        self.pk = 7
        self.listing_title = "Holiday Bizarre"
        self.listing_summary = ""
        self.listing_html = "<p>Bring a friend.</p>"
        self.ticket_name = "Ticket"
        self.ticket_price_cents = 0
        self.capacity = 40
        self.faqs: list[dict[str, str]] = []
        self.eventbrite_category = ""
        self.eventbrite_subcategory = ""
        self.eventbrite_fee_payer = choices.EventbriteFeePayer.BUYER
        self.eventbrite_event_id = ""
        self.eventbrite_ticket_class_id = ""
        self.eventbrite_sync_state = State.IDLE
        self.eventbrite_sync_error = ""
        self.eventbrite_synced_at: datetime | None = None
        self.eventbrite_published = False
        self.wants_eventbrite_listing = True
        for name, value in fields.items():
            setattr(self, name, value)

    def listing_faqs(self) -> list[dict[str, str]]:
        return list(self.faqs)

    def listing_schedule(self) -> ListingSchedule:
        return ListingSchedule(dates=(STARTS,), starts_at=STARTS, ends_at=ENDS, sales_end=STARTS, heading="When:")

    def listing_gallery(self) -> list[Any]:
        return []

    def ticket_quantity(self, sold: int) -> int:
        return self.capacity

    def eventbrite_listing_check(self) -> ListingCheck:
        return check_listing(self.listing_title, self.listing_summary, self.listing_html, self.faqs)


def _listed(**fields: Any) -> Party:
    """A party live on Eventbrite, as a publish leaves it."""
    listed = {
        "eventbrite_event_id": "ev-9",
        "eventbrite_ticket_class_id": "tc-9",
        "eventbrite_sync_state": State.LISTED,
        "eventbrite_published": True,
    }
    return Party(**{**listed, **fields})


@pytest.fixture
def eventbrite() -> Iterator[FakeEventbrite]:
    fake = FakeEventbrite()
    with patch.object(EventbriteClient, "from_settings", return_value=fake):
        yield fake


def describe_the_event_body():
    def it_takes_the_format_from_the_source_and_sends_the_venue(eventbrite: FakeEventbrite):
        party = Party()

        event = _event_body(party, eventbrite, party.listing_schedule())["event"]

        assert event["format_id"] == "11"
        assert (event["venue_id"], event["organizer_id"], event["online_event"]) == ("venue-1", "organizer-1", False)
        assert (event["start"]["utc"], event["end"]["utc"]) == ("2026-12-05T01:00:00Z", "2026-12-05T06:00:00Z")
        assert not {"category_id", "subcategory_id"} & set(event)

    def it_falls_back_to_the_title_when_the_summary_is_blank(eventbrite: FakeEventbrite):
        party = Party()

        event = _event_body(party, eventbrite, party.listing_schedule())["event"]

        assert event["name"] == {"html": "Holiday Bizarre"}
        assert event["summary"] == "Holiday Bizarre"

    def it_sends_the_summary_when_there_is_one(eventbrite: FakeEventbrite):
        party = Party(listing_summary="Wine wall, demos and music.")

        event = _event_body(party, eventbrite, party.listing_schedule())["event"]

        assert event["summary"] == "Wine wall, demos and music."

    def it_sends_a_chosen_category_and_subcategory(eventbrite: FakeEventbrite):
        party = Party(eventbrite_category="116", eventbrite_subcategory="16001")

        event = _event_body(party, eventbrite, party.listing_schedule())["event"]

        assert (event["category_id"], event["subcategory_id"]) == ("116", "16001")


def describe_the_ticket_body():
    def it_sends_a_free_ticket_with_no_cost():
        party = Party()

        ticket = _ticket_body(party, party.listing_schedule(), 40)["ticket_class"]

        assert ticket == {"name": "Ticket", "quantity_total": 40, "sales_end": "2026-12-05T01:00:00Z", "free": True}

    @pytest.mark.parametrize(
        ("payer", "included"),
        [(choices.EventbriteFeePayer.BUYER, False), (choices.EventbriteFeePayer.INCLUDED, True)],
    )
    def it_sends_a_priced_ticket_with_its_cost_and_who_pays_the_fee(payer: str, included: bool):
        party = Party(ticket_price_cents=500, eventbrite_fee_payer=payer)

        ticket = _ticket_body(party, party.listing_schedule(), 40)["ticket_class"]

        assert (ticket["cost"], ticket["include_fee"]) == ("USD,500", included)
        assert "free" not in ticket


def describe_a_source_with_a_fixed_capacity():
    def it_never_reads_eventbrites_sales_before_an_update(eventbrite: FakeEventbrite):
        party = _listed(capacity=120)

        sync_listing(party)

        assert "get_ticket_class" not in eventbrite.names()
        assert eventbrite.args("update_ticket_class")[2]["ticket_class"]["quantity_total"] == 120
        assert party.eventbrite_sync_state == State.LISTED

    def it_pushes_the_capacity_as_the_quantity_without_reading_sales(eventbrite: FakeEventbrite):
        party = _listed(capacity=120)

        assert push_ticket_quantity(party) is True

        assert eventbrite.names() == ["update_ticket_class"]
        assert eventbrite.args("update_ticket_class") == ("ev-9", "tc-9", {"ticket_class": {"quantity_total": 120}})


def describe_an_empty_faq():
    def it_is_no_description_text_at_all():
        assert _faq_html([]) == ""

    def it_lists_with_no_questions_heading_and_an_empty_faq_section(eventbrite: FakeEventbrite):
        sync_listing(Party())

        event_id, html, image_ids, faqs = eventbrite.args("set_description")
        assert (event_id, image_ids, faqs) == ("ev-1", [], [])
        assert html == "<p>Bring a friend.</p><p>When:</p><ul><li>Friday December 4, 2026 at 5:00 PM</li></ul>"

    def it_retries_as_the_description_alone_when_eventbrite_refuses_the_widgets(eventbrite: FakeEventbrite):
        eventbrite.refuse_widgets = EventbriteError("no widgets", 400)
        party = Party()

        sync_listing(party)

        first, retry = eventbrite.descriptions()
        assert (first[3], retry[3]) == ([], None)
        assert "Questions:" not in retry[1]
        assert party.eventbrite_sync_state == State.LISTED
        assert party.eventbrite_sync_error.startswith(
            "Eventbrite refused the page, so it went without its widgets (the FAQ went into the description as text)"
        )


def describe_listing_a_new_source():
    def it_creates_the_event_and_its_ticket_publishes_and_records_listed(eventbrite: FakeEventbrite):
        party = Party()

        sync_listing(party)

        assert eventbrite.names() == ["create_event", "create_ticket_class", "set_description", "publish"]
        assert (party.eventbrite_event_id, party.eventbrite_ticket_class_id) == ("ev-1", "tc-1")
        assert (party.eventbrite_sync_state, party.eventbrite_sync_error, party.eventbrite_published) == (
            State.LISTED,
            "",
            True,
        )
        assert party.eventbrite_synced_at is not None

    def it_updates_a_listed_source_in_place_after_reading_it(eventbrite: FakeEventbrite):
        party = _listed()

        sync_listing(party)

        assert eventbrite.names() == ["get_event", "update_event", "update_ticket_class", "set_description"]
        assert eventbrite.args("update_event")[0] == "ev-9"
        assert (party.eventbrite_sync_state, party.eventbrite_sync_error) == (State.LISTED, "")

    def it_records_a_failed_push_without_raising(eventbrite: FakeEventbrite):
        eventbrite.fail["create_event"] = EventbriteError("boom", 500)
        party = Party()

        sync_listing(party)

        assert (party.eventbrite_sync_state, party.eventbrite_sync_error) == (State.FAILED, "boom")
        assert party.eventbrite_event_id == ""

    def it_waits_while_sync_is_off(eventbrite: FakeEventbrite):
        eventbrite.enabled = False
        party = Party()

        sync_listing(party)

        assert eventbrite.names() == []
        assert (party.eventbrite_sync_state, party.eventbrite_sync_error) == (State.PENDING, EventbriteSync.SYNC_OFF)


def describe_ending_a_listing():
    def it_closes_sales_unpublishes_and_records_ended(eventbrite: FakeEventbrite):
        party = _listed(wants_eventbrite_listing=False)

        sync_listing(party)

        assert eventbrite.names() == ["get_event", "update_ticket_class", "unpublish"]
        assert set(eventbrite.args("update_ticket_class")[2]["ticket_class"]) == {"sales_end"}
        assert (party.eventbrite_sync_state, party.eventbrite_sync_error, party.eventbrite_published) == (
            State.ENDED,
            "",
            False,
        )

    def it_records_still_up_when_eventbrite_keeps_the_page_for_its_orders(eventbrite: FakeEventbrite):
        eventbrite.fail["unpublish"] = EventbriteError("has orders", 400)
        party = _listed(wants_eventbrite_listing=False)

        sync_listing(party)

        assert (party.eventbrite_sync_state, party.eventbrite_sync_error, party.eventbrite_published) == (
            State.ENDED,
            EventbriteSync.STILL_UP,
            True,
        )

    def it_goes_idle_with_nothing_on_eventbrite(eventbrite: FakeEventbrite):
        party = Party(wants_eventbrite_listing=False)

        sync_listing(party)

        assert eventbrite.names() == []
        assert (party.eventbrite_sync_state, party.eventbrite_sync_error) == (State.IDLE, "")


def describe_a_takedown_outside_plfog():
    def it_is_read_before_any_write_and_recorded_as_ended(eventbrite: FakeEventbrite):
        eventbrite.read_status = "draft"
        party = _listed()

        sync_listing(party)

        assert eventbrite.names() == ["get_event"]
        assert (party.eventbrite_sync_state, party.eventbrite_sync_error) == (State.ENDED, EventbriteSync.TAKEN_DOWN)
        assert party.eventbrite_published is True
        assert party.eventbrite_synced_at is not None


def describe_the_choices_core_keeps():
    def it_re_exports_the_browse_lists_through_the_classes_shim():
        assert shim.EventbriteFormat is choices.EventbriteFormat
        assert shim.EventbriteCategory is choices.EventbriteCategory
        assert shim.EventbriteSubcategory is choices.EventbriteSubcategory
        assert shim.SUBCATEGORY_PARENT is choices.SUBCATEGORY_PARENT
        assert shim.subcategory_fits is choices.subcategory_fits
        assert shim.CLASS_FORMAT_ID == choices.EventbriteFormat.CLASS == "9"

    def it_keeps_the_listing_states_reachable_on_the_class():
        assert ClassOffering.EventbriteFeePayer is choices.EventbriteFeePayer
        assert ClassOffering.EventbriteSyncState is choices.EventbriteSyncState
        assert ClassOffering.EventbriteStage is choices.EventbriteStage

    def it_lists_a_class_as_a_class_training_or_workshop():
        assert ClassOffering(title="Welding").eventbrite_format_id == "9"


def describe_the_event_rules():
    def it_reads_as_the_class_rules_with_no_faq_to_keep_free():
        assert EVENTBRITE_EVENT_RULE_PARTS.keep_free == (
            "To list this event on Eventbrite, keep the title, description and photos free of:"
        )
        assert EVENTBRITE_EVENT_RULE_PARTS.items == EVENTBRITE_RULE_PARTS.items
        assert EVENTBRITE_EVENT_RULE_PARTS.text.startswith(
            "Eventbrite takes down listings that send buyers anywhere else. To list this event on Eventbrite,"
        )
