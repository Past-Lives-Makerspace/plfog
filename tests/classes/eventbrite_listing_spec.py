"""BDD specs for a class's Eventbrite listing (#652, part 1): create, update, end, toggle off.

Every Eventbrite call goes to :class:`FakeEventbrite`, which records what it was asked; no spec
reaches Eventbrite.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    SeriesClassOfferingFactory,
    UserFactory,
)
from classes.forms import ClassOfferingForm, TeachClassOfferingForm, TeachPublishedClassForm
from classes.models import ClassOffering
from core.integrations.eventbrite import EventbriteClient, EventbriteError, EventbriteSync
from core.models import SiteConfiguration
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

State = ClassOffering.EventbriteSyncState


class FakeEventbrite:
    """Stands in for :class:`EventbriteClient`; ``fail`` maps a method name to the error it raises."""

    enabled = True
    venue_id = "venue-1"

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fail: dict[str, EventbriteError] = {}

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if name in self.fail:
            raise self.fail[name]

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def args(self, name: str) -> tuple[Any, ...]:
        return next(args for called, args in self.calls if called == name)

    def upload_logo(self, filename: str, content: bytes) -> str:
        self._record("upload_logo", filename)
        return "logo-1"

    def create_event(self, body: dict[str, Any]) -> dict[str, Any]:
        self._record("create_event", body)
        return {"id": "ev-1"}

    def update_event(self, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("update_event", event_id, body)
        return {"id": event_id}

    def create_ticket_class(self, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("create_ticket_class", event_id, body)
        return {"id": "tc-1"}

    def update_ticket_class(self, event_id: str, ticket_class_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("update_ticket_class", event_id, ticket_class_id, body)
        return {"id": ticket_class_id}

    def set_description(self, event_id: str, html: str) -> None:
        self._record("set_description", event_id, html)

    def publish(self, event_id: str) -> None:
        self._record("publish", event_id)

    def unpublish(self, event_id: str) -> None:
        self._record("unpublish", event_id)


@pytest.fixture
def eventbrite() -> Iterator[FakeEventbrite]:
    fake = FakeEventbrite()
    with patch.object(EventbriteClient, "from_settings", return_value=fake):
        yield fake


def _switch_integration_on(settings: Any) -> None:
    """The site toggle plus every credential: what ``EventbriteClient.enabled`` needs."""
    settings.EVENTBRITE_PRIVATE_TOKEN = "token"
    settings.EVENTBRITE_ORGANIZATION_ID = "org"
    settings.EVENTBRITE_VENUE_ID = "venue"
    config = SiteConfiguration.load()
    config.eventbrite_sync_enabled = True
    config.save(update_fields=["eventbrite_sync_enabled"])


def _opted_in(**kwargs: Any) -> ClassOffering:
    return ClassOfferingFactory(**{"ready": True, "eventbrite_enabled": True, **kwargs})


def _listed(**kwargs: Any) -> ClassOffering:
    """A live class already on Eventbrite, as a publish leaves it."""
    listed: dict[str, Any] = {
        "status": ClassOffering.Status.PUBLISHED,
        "eventbrite_event_id": "ev-9",
        "eventbrite_ticket_class_id": "tc-9",
        "eventbrite_sync_state": State.LISTED,
    }
    return _opted_in(**{**listed, **kwargs})


def describe_publishing_an_opted_in_class():
    def it_creates_the_event_with_one_ticket_type_and_publishes_it(eventbrite: FakeEventbrite):
        offering = _opted_in(title="Intro to Welding", capacity=8)
        session = offering.sessions.get()

        offering.publish(None)

        assert eventbrite.names() == [
            "upload_logo",
            "create_event",
            "create_ticket_class",
            "set_description",
            "publish",
        ]
        event = eventbrite.args("create_event")[0]["event"]
        assert event["name"] == {"html": "Intro to Welding"}
        assert event["venue_id"] == "venue-1"
        assert event["logo_id"] == "logo-1"
        assert event["start"]["utc"] == session.starts_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert event["end"]["timezone"] == "America/Los_Angeles"
        ticket = eventbrite.args("create_ticket_class")[1]["ticket_class"]
        assert ticket["cost"] == {"currency": "USD", "value": 5000}
        assert ticket["quantity_total"] == 8
        assert ticket["include_fee"] is False
        html = eventbrite.args("set_description")[1]
        assert offering.public_url in html
        offering.refresh_from_db()
        assert (offering.eventbrite_event_id, offering.eventbrite_ticket_class_id) == ("ev-1", "tc-1")
        assert offering.eventbrite_sync_state == State.LISTED
        assert offering.eventbrite_synced_at is not None

    def it_folds_the_fee_into_the_price_when_the_instructor_absorbs_it(eventbrite: FakeEventbrite):
        offering = _opted_in(eventbrite_fee_payer=ClassOffering.EventbriteFeePayer.INCLUDED)

        offering.publish(None)

        ticket = eventbrite.args("create_ticket_class")[1]["ticket_class"]
        assert ticket["include_fee"] is True
        assert ticket["cost"] == {"currency": "USD", "value": 5000}

    def it_lists_a_free_class_as_a_free_ticket(eventbrite: FakeEventbrite):
        _opted_in(price_cents=0).publish(None)

        ticket = eventbrite.args("create_ticket_class")[1]["ticket_class"]
        assert ticket["free"] is True
        assert "cost" not in ticket

    def it_runs_a_series_from_the_first_session_to_the_last_and_lists_every_date(eventbrite: FakeEventbrite):
        offering = SeriesClassOfferingFactory(session_count=3, ready=True, eventbrite_enabled=True)
        last = offering.sessions.order_by("starts_at").last()

        offering.publish(None)

        event = eventbrite.args("create_event")[0]["event"]
        assert event["end"]["utc"] == last.ends_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert eventbrite.args("set_description")[1].count("<li>") == 4  # the ready session plus three
        assert eventbrite.args("create_ticket_class")[1]["ticket_class"]["name"] == "Series ticket"

    def it_ends_ticket_sales_at_the_registration_cutoff(eventbrite: FakeEventbrite):
        offering = _opted_in(registration_cutoff_hours=24)

        offering.publish(None)

        sales_end = eventbrite.args("create_ticket_class")[1]["ticket_class"]["sales_end"]["utc"]
        closes = offering.registration_closes_at
        assert closes is not None
        assert sales_end == closes.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def describe_classes_that_are_never_listed():
    def it_pushes_nothing_for_a_class_not_opted_in(eventbrite: FakeEventbrite):
        offering = ClassOfferingFactory(ready=True)

        offering.publish(None)

        assert eventbrite.calls == []
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.IDLE

    def it_never_lists_a_demo_class(eventbrite: FakeEventbrite):
        _opted_in(slug="demo-welding").publish(None)

        assert eventbrite.calls == []

    def it_never_lists_a_private_class(eventbrite: FakeEventbrite):
        _opted_in(is_private=True).publish(None)

        assert eventbrite.calls == []

    def it_never_lists_a_flexible_class(eventbrite: FakeEventbrite):
        offering = _listed(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        offering.eventbrite_event_id = ""
        offering.save(update_fields=["eventbrite_event_id"])

        offering.sync_eventbrite_listing()

        assert eventbrite.calls == []


def describe_editing_a_listed_class():
    def it_updates_the_event_and_ticket_without_creating_or_republishing(eventbrite: FakeEventbrite):
        offering = _listed()
        offering.title = "Welding Two"
        offering.price_cents = 6500
        offering.save()

        offering.sync_eventbrite_listing()

        assert eventbrite.names() == ["update_event", "update_ticket_class", "set_description"]
        assert eventbrite.args("update_event")[1]["event"]["name"] == {"html": "Welding Two"}
        ticket = eventbrite.args("update_ticket_class")[2]["ticket_class"]
        assert ticket["cost"]["value"] == 6500

    def it_follows_a_sale_price(eventbrite: FakeEventbrite):
        offering = _listed(sale_enabled=True, sale_percent=20)
        offering.turn_sale_off()

        assert eventbrite.args("update_ticket_class")[2]["ticket_class"]["cost"]["value"] == 5000

    def it_creates_a_missing_ticket_type_on_the_next_push(eventbrite: FakeEventbrite):
        offering = _listed(eventbrite_ticket_class_id="", eventbrite_sync_state=State.FAILED)

        offering.sync_eventbrite_listing()

        assert eventbrite.names() == ["update_event", "create_ticket_class", "set_description", "publish"]


def describe_ending_a_listing():
    def it_closes_sales_and_unpublishes_on_unpublish(eventbrite: FakeEventbrite):
        offering = _listed()

        offering.unpublish()

        assert eventbrite.names() == ["update_ticket_class", "unpublish"]
        assert "sales_end" in eventbrite.args("update_ticket_class")[2]["ticket_class"]
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.ENDED
        assert offering.eventbrite_sync_error == ""

    def it_ends_the_listing_when_the_class_is_cancelled(eventbrite: FakeEventbrite):
        offering = _listed()

        offering.cancel(None, "The kiln is broken.")

        assert eventbrite.names() == ["update_ticket_class", "unpublish"]

    def it_ends_the_listing_when_the_instructor_switches_it_off(eventbrite: FakeEventbrite):
        offering = _listed()
        form = TeachPublishedClassForm(
            {"description": offering.description, "eventbrite_enabled": ""}, instance=offering
        )
        assert form.is_valid(), form.errors

        form.save().sync_eventbrite_listing()

        assert eventbrite.names() == ["update_ticket_class", "unpublish"]

    def it_notes_the_page_staying_up_when_eventbrite_refuses_to_unpublish(eventbrite: FakeEventbrite):
        eventbrite.fail["unpublish"] = EventbriteError("has orders", 400)
        offering = _listed()

        offering.unpublish()

        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.ENDED
        assert offering.eventbrite_sync_error == EventbriteSync.STILL_UP

    def it_only_unpublishes_when_no_ticket_type_was_made(eventbrite: FakeEventbrite):
        offering = _listed(eventbrite_ticket_class_id="")

        offering.unpublish()

        assert eventbrite.names() == ["unpublish"]

    def it_records_a_failure_when_eventbrite_errors_for_another_reason(eventbrite: FakeEventbrite):
        eventbrite.fail["unpublish"] = EventbriteError("POST unpublish: 503", 503)
        offering = _listed()

        offering.unpublish()

        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.FAILED

    def it_does_not_end_an_ended_listing_again(eventbrite: FakeEventbrite):
        offering = _listed(eventbrite_enabled=False, eventbrite_sync_state=State.ENDED)

        offering.sync_eventbrite_listing()

        assert eventbrite.calls == []

    def it_relists_and_publishes_when_switched_back_on(eventbrite: FakeEventbrite):
        offering = _listed(eventbrite_sync_state=State.ENDED)

        offering.sync_eventbrite_listing()

        assert eventbrite.names()[-1] == "publish"


def describe_when_a_push_fails():
    def it_records_the_failure_and_the_retry_command_lists_it(eventbrite: FakeEventbrite):
        eventbrite.fail["create_event"] = EventbriteError("POST /events/: 500 down", 500)
        offering = _opted_in()

        offering.publish(None)

        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED
        assert offering.eventbrite_sync_state == State.FAILED
        assert "500 down" in offering.eventbrite_sync_error

        del eventbrite.fail["create_event"]
        call_command("retry_eventbrite_pushes")

        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.LISTED

    def it_lists_a_class_with_no_photo_of_its_own_without_an_image(eventbrite: FakeEventbrite):
        # A live class can lose its own photo (it falls back to the category's); publish refuses one without.
        _listed(image="", eventbrite_event_id="", eventbrite_sync_state=State.FAILED).sync_eventbrite_listing()

        assert "upload_logo" not in eventbrite.names()
        assert "logo_id" not in eventbrite.args("create_event")[0]["event"]

    def it_still_lists_the_class_when_the_photo_will_not_upload(eventbrite: FakeEventbrite):
        eventbrite.fail["upload_logo"] = EventbriteError("bad image", 400)

        _opted_in().publish(None)

        assert "logo_id" not in eventbrite.args("create_event")[0]["event"]


def describe_when_sync_is_off():
    def it_sends_nothing_and_leaves_the_class_pending(settings: Any):
        settings.EVENTBRITE_PRIVATE_TOKEN = "token"
        settings.EVENTBRITE_ORGANIZATION_ID = "org"
        settings.EVENTBRITE_VENUE_ID = "venue"
        offering = _opted_in()

        with patch("core.integrations.eventbrite.httpx.request") as request:
            offering.publish(None)

        request.assert_not_called()
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED
        assert offering.eventbrite_sync_state == State.PENDING
        assert offering.eventbrite_sync_error == EventbriteSync.SYNC_OFF
        assert SiteConfiguration.load().eventbrite_sync_enabled is False


def describe_run_it_again():
    def it_keeps_the_opt_in_but_not_the_listing():
        offering = _listed(eventbrite_synced_at=timezone.now())

        run = offering.duplicate_as_new_run()

        assert run.eventbrite_enabled is True
        assert (run.eventbrite_event_id, run.eventbrite_ticket_class_id) == ("", "")
        assert run.eventbrite_sync_state == State.IDLE
        assert run.eventbrite_synced_at is None


def describe_the_class_forms():
    @pytest.fixture(autouse=True)
    def _integration_on(settings: Any) -> None:
        _switch_integration_on(settings)

    def _composer_data(offering: ClassOffering, **overrides: Any) -> dict[str, Any]:
        data = {
            "title": offering.title,
            "category": offering.category_id,
            "description": offering.description,
            "price_cents": "50.00",
            "capacity": 6,
            "scheduling_model": ClassOffering.SchedulingModel.FIXED,
            "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
            "eventbrite_enabled": "on",
            "eventbrite_fee_payer": ClassOffering.EventbriteFeePayer.INCLUDED,
        }
        return {**data, **overrides}

    def it_saves_both_fields_from_the_instructor_composer():
        offering = ClassOfferingFactory()
        form = TeachClassOfferingForm(_composer_data(offering), instance=offering)
        assert form.is_valid(), form.errors

        saved = form.save()

        assert saved.eventbrite_enabled is True
        assert saved.eventbrite_fee_payer == ClassOffering.EventbriteFeePayer.INCLUDED

    def it_keeps_the_buyer_paying_when_the_choice_is_not_posted():
        offering = ClassOfferingFactory()
        data = _composer_data(offering)
        del data["eventbrite_fee_payer"]
        form = TeachClassOfferingForm(data, instance=offering)
        assert form.is_valid(), form.errors

        assert form.save().eventbrite_fee_payer == ClassOffering.EventbriteFeePayer.BUYER

    def it_clears_the_opt_in_on_a_flexible_class():
        offering = ClassOfferingFactory()
        data = _composer_data(offering, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        form = TeachClassOfferingForm(data, instance=offering)
        assert form.is_valid(), form.errors

        assert form.save().eventbrite_enabled is False

    def it_defaults_to_off_with_the_buyer_paying():
        form = TeachClassOfferingForm()

        assert form.fields["eventbrite_enabled"].initial is False
        assert form.fields["eventbrite_fee_payer"].initial == ClassOffering.EventbriteFeePayer.BUYER

    def it_works_the_fee_example_at_the_class_price():
        offering = ClassOfferingFactory(price_cents=8000)

        help_text = TeachClassOfferingForm(instance=offering).fields["eventbrite_fee_payer"].help_text

        # 6.6% of $80 is $5.28, plus $1.79 per ticket.
        assert "On a $80 ticket that is about $7.07." in help_text
        assert "the buyer pays about $87.07" in help_text

    def it_offers_the_switch_on_a_live_fixed_class_but_not_a_flexible_one():
        fixed = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        flexible = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE
        )

        assert "eventbrite_enabled" in TeachPublishedClassForm(instance=fixed).fields
        assert "eventbrite_enabled" not in TeachPublishedClassForm(instance=flexible).fields


def describe_the_fields_while_the_integration_is_off():
    def it_leaves_both_fields_off_the_composer_forms():
        offering = ClassOfferingFactory()

        for form in (TeachClassOfferingForm(instance=offering), ClassOfferingForm(instance=offering)):
            assert "eventbrite_enabled" not in form.fields
            assert "eventbrite_fee_payer" not in form.fields

    def it_ignores_a_posted_opt_in():
        offering = ClassOfferingFactory()
        data = {
            "title": offering.title,
            "category": offering.category_id,
            "description": offering.description,
            "price_cents": "50.00",
            "capacity": 6,
            "scheduling_model": ClassOffering.SchedulingModel.FIXED,
            "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
            "eventbrite_enabled": "on",
            "eventbrite_fee_payer": ClassOffering.EventbriteFeePayer.INCLUDED,
        }
        form = TeachClassOfferingForm(data, instance=offering)
        assert form.is_valid(), form.errors

        saved = form.save()

        assert saved.eventbrite_enabled is False
        assert saved.eventbrite_fee_payer == ClassOffering.EventbriteFeePayer.BUYER

    def it_gives_a_live_class_no_switch():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)

        assert "eventbrite_enabled" not in TeachPublishedClassForm(instance=offering).fields

    def it_hides_them_while_a_credential_is_missing(settings: Any):
        _switch_integration_on(settings)
        settings.EVENTBRITE_VENUE_ID = ""

        assert "eventbrite_enabled" not in TeachClassOfferingForm().fields

    def it_shows_them_once_the_integration_is_on(settings: Any):
        _switch_integration_on(settings)

        assert {"eventbrite_enabled", "eventbrite_fee_payer"} <= set(TeachClassOfferingForm().fields)
        assert {"eventbrite_enabled", "eventbrite_fee_payer"} <= set(ClassOfferingForm().fields)


def describe_the_composer_page():
    def _composer_html(client: Any) -> str:
        MembershipPlanFactory()
        user = UserFactory(username="eb-composer@example.com", email="eb-composer@example.com")
        InstructorFactory(user=user)
        client.force_login(user)
        return client.get(reverse("classes:teach_class_create")).content.decode()

    def it_has_no_eventbrite_block_while_the_integration_is_off(client: Any):
        assert "data-eventbrite" not in _composer_html(client)

    def it_shows_the_eventbrite_block_once_the_integration_is_on(client: Any, settings: Any):
        _switch_integration_on(settings)

        html = _composer_html(client)

        assert "data-eventbrite" in html
        assert 'name="eventbrite_enabled"' in html


def describe_a_class_with_no_dates():
    def it_is_not_listed_even_when_live_and_opted_in(eventbrite: FakeEventbrite):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, eventbrite_enabled=True)

        offering.sync_eventbrite_listing()

        assert eventbrite.calls == []
        start = timezone.now() + timedelta(days=3)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=1))
        offering.sync_eventbrite_listing()
        assert "create_event" in eventbrite.names()
