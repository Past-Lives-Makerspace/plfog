"""BDD specs for a class's Eventbrite listing (#652, part 1): create, update, end, toggle off.

Every Eventbrite call goes to :class:`FakeEventbrite`, which records what it was asked; no spec
reaches Eventbrite.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassFaqFactory,
    ClassImageFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    SeriesClassOfferingFactory,
    UserFactory,
)
from classes.forms import ClassOfferingForm, TeachClassOfferingForm, TeachPublishedClassForm, build_class_faq_formset
from classes.models import LOCKED_CLASS_FAQS, ClassImage, ClassOffering
from core.integrations.eventbrite import EventbriteClient, EventbriteError, EventbriteSync, _listing_text
from core.models import SiteConfiguration
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

State = ClassOffering.EventbriteSyncState

# What Eventbrite receives of the locked FAQs: the accessibility one as listing text, never the
# cancellation one (#720 keeps cancelling and refunds off Eventbrite).
_EVENTBRITE_LOCKED = [
    {"question": faq["question"], "answer": _listing_text(faq["answer"])}
    for faq in LOCKED_CLASS_FAQS
    if "cancel" not in faq["question"].lower()
]


class FakeEventbrite:
    """Stands in for :class:`EventbriteClient`; ``fail`` maps a method name to the error it raises."""

    enabled = True
    venue_id = "venue-1"
    organizer_id = "organizer-1"

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fail: dict[str, EventbriteError] = {}
        self.created_event: dict[str, Any] = {"id": "ev-1", "status": "draft"}
        self.event_status: str | None = "live"  # what an update answers; None leaves ``status`` out
        self.read_status = "live"  # what reading the event answers
        self.quantity_sold = 0
        self.refused_photos: dict[str, EventbriteError] = {}
        self.refuse_image_modules: EventbriteError | None = None
        self.refuse_widgets: EventbriteError | None = None
        self.uploads = 0

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
        return self.created_event

    def update_event(self, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("update_event", event_id, body)
        if self.event_status is None:
            return {"id": event_id}
        return {"id": event_id, "status": self.event_status}

    def create_ticket_class(self, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("create_ticket_class", event_id, body)
        return {"id": "tc-1"}

    def update_ticket_class(self, event_id: str, ticket_class_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._record("update_ticket_class", event_id, ticket_class_id, body)
        return {"id": ticket_class_id}

    def get_ticket_class(self, event_id: str, ticket_class_id: str) -> dict[str, Any]:
        self._record("get_ticket_class", event_id, ticket_class_id)
        return {"id": ticket_class_id, "quantity_sold": self.quantity_sold}

    def set_description(
        self, event_id: str, html: str, image_ids: Sequence[str] = (), faqs: Sequence[dict[str, str]] | None = None
    ) -> None:
        # faqs None is a request without widgets; a list (even empty) carries them.
        self._record("set_description", event_id, html, list(image_ids), None if faqs is None else list(faqs))
        if faqs is not None and self.refuse_widgets is not None:
            raise self.refuse_widgets
        if image_ids and self.refuse_image_modules is not None:
            raise self.refuse_image_modules

    def upload_content_image(self, filename: str, content: bytes) -> str:
        """``refused_photos`` maps a file name stem to the error Eventbrite answers that photo with."""
        self._record("upload_content_image", filename)
        for stem, error in self.refused_photos.items():
            if filename.startswith(stem):
                raise error
        self.uploads += 1
        return f"img-{self.uploads}"

    def descriptions(self) -> list[tuple[Any, ...]]:
        return [args for name, args in self.calls if name == "set_description"]

    def publish(self, event_id: str) -> None:
        self._record("publish", event_id)

    def unpublish(self, event_id: str) -> None:
        self._record("unpublish", event_id)

    def get_event(self, event_id: str) -> dict[str, Any]:
        self._record("get_event", event_id)
        return {"id": event_id, "status": self.read_status}


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
    settings.EVENTBRITE_ORGANIZER_ID = "organizer-1"
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
            "upload_content_image",
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
        assert ticket["cost"] == "USD,5000"
        assert ticket["quantity_total"] == 8
        assert ticket["include_fee"] is False
        html = eventbrite.args("set_description")[1]
        assert offering.public_url not in html  # no link back to the site's booking (#720)
        assert "booking" not in html
        offering.refresh_from_db()
        assert (offering.eventbrite_event_id, offering.eventbrite_ticket_class_id) == ("ev-1", "tc-1")
        assert offering.eventbrite_sync_state == State.LISTED
        assert offering.eventbrite_synced_at is not None

    def it_folds_the_fee_into_the_price_when_the_instructor_absorbs_it(eventbrite: FakeEventbrite):
        offering = _opted_in(eventbrite_fee_payer=ClassOffering.EventbriteFeePayer.INCLUDED)

        offering.publish(None)

        ticket = eventbrite.args("create_ticket_class")[1]["ticket_class"]
        assert ticket["include_fee"] is True
        assert ticket["cost"] == "USD,5000"

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

        sales_end = eventbrite.args("create_ticket_class")[1]["ticket_class"]["sales_end"]
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

    def it_never_lists_a_demo_class_even_in_demo_mode(eventbrite: FakeEventbrite):
        config = SiteConfiguration.load()
        config.display_demo_classes = True
        config.save(update_fields=["display_demo_classes"])
        offering = _opted_in(slug="demo-welding")

        offering.publish(None)

        assert ClassOffering.objects.public().filter(pk=offering.pk).exists()
        assert eventbrite.calls == []

    def it_never_lists_a_class_titled_demo_even_in_demo_mode(eventbrite: FakeEventbrite):
        config = SiteConfiguration.load()
        config.display_demo_classes = True
        config.save(update_fields=["display_demo_classes"])

        _opted_in(title="[DEMO] Intro to Welding", slug="intro-welding").publish(None)

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

        assert eventbrite.names() == [
            "update_event",
            "get_ticket_class",
            "update_ticket_class",
            "upload_content_image",
            "set_description",
        ]
        assert eventbrite.args("update_event")[1]["event"]["name"] == {"html": "Welding Two"}
        ticket = eventbrite.args("update_ticket_class")[2]["ticket_class"]
        assert ticket["cost"] == "USD,6500"

    def it_follows_a_sale_price_on_the_next_retry_tick(eventbrite: FakeEventbrite):
        offering = _listed(sale_enabled=True, sale_percent=20)
        offering.turn_sale_off()
        assert eventbrite.calls == []

        call_command("retry_eventbrite_pushes")

        assert eventbrite.args("update_ticket_class")[2]["ticket_class"]["cost"] == "USD,5000"

    def it_creates_a_missing_ticket_type_on_the_next_push(eventbrite: FakeEventbrite):
        eventbrite.event_status = "draft"
        offering = _listed(eventbrite_ticket_class_id="", eventbrite_sync_state=State.FAILED)

        offering.sync_eventbrite_listing()

        assert eventbrite.names() == [
            "update_event",
            "create_ticket_class",
            "upload_content_image",
            "set_description",
            "publish",
        ]


def describe_ending_a_listing():
    def it_closes_sales_and_unpublishes_on_unpublish(eventbrite: FakeEventbrite):
        offering = _listed()

        offering.unpublish()

        assert eventbrite.names() == ["update_ticket_class", "unpublish"]
        sales_end = eventbrite.args("update_ticket_class")[2]["ticket_class"]["sales_end"]
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", sales_end)
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.ENDED
        assert offering.eventbrite_sync_error == ""

    def it_ends_the_listing_when_the_class_is_archived(eventbrite: FakeEventbrite):
        offering = _listed()

        offering.archive()

        assert eventbrite.names() == ["update_ticket_class", "unpublish"]
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.ARCHIVED
        assert offering.eventbrite_sync_state == State.ENDED

    def it_ends_the_listing_when_the_class_is_cancelled(eventbrite: FakeEventbrite):
        offering = _listed()

        offering.cancel(None, "The kiln is broken.")

        assert eventbrite.names() == ["update_ticket_class", "unpublish"]

    def it_ends_the_listing_when_the_class_is_taken_off_eventbrite(eventbrite: FakeEventbrite):
        offering = _listed()

        offering.take_off_eventbrite()

        assert eventbrite.names() == ["update_ticket_class", "unpublish"]
        assert offering.eventbrite_enabled is False

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
        eventbrite.event_status = "draft"  # an unpublished event is a draft again
        offering = _listed(eventbrite_sync_state=State.ENDED)

        offering.sync_eventbrite_listing()

        assert eventbrite.names()[-1] == "publish"


def describe_an_event_taken_down_outside_plfog():
    """#720: plfog publishes an event once; a published event that reads draft again stays down."""

    def it_publishes_a_never_published_draft_and_remembers_it(eventbrite: FakeEventbrite):
        offering = _opted_in()

        offering.publish(None)

        assert eventbrite.names()[-1] == "publish"
        offering.refresh_from_db()
        assert offering.eventbrite_published is True
        assert offering.eventbrite_sync_state == State.LISTED

    def it_never_republishes_an_event_plfog_published_that_reads_draft_again(eventbrite: FakeEventbrite):
        eventbrite.event_status = "draft"
        offering = _listed(eventbrite_published=True)

        offering.sync_eventbrite_listing()

        assert "publish" not in eventbrite.names()
        assert "set_description" in eventbrite.names()  # the cleaned text still reaches the draft
        offering.refresh_from_db()
        assert offering.eventbrite_published is True
        assert offering.eventbrite_sync_state == State.ENDED
        assert offering.eventbrite_sync_error == EventbriteSync.TAKEN_DOWN
        assert offering.eventbrite_sync_label == (
            "Ended on Eventbrite. Unpublished on Eventbrite outside plfog; not republished."
        )

    def it_does_not_republish_it_on_the_next_retry_tick_after_an_edit(eventbrite: FakeEventbrite):
        eventbrite.event_status = "draft"
        offering = _listed(eventbrite_published=True)
        offering.mark_eventbrite_edit_saved()

        call_command("retry_eventbrite_pushes")

        assert "publish" not in eventbrite.names()
        offering.refresh_from_db()
        assert offering.eventbrite_sync_error == EventbriteSync.TAKEN_DOWN

    def it_leaves_the_taken_down_event_as_it_is_when_the_class_comes_off_eventbrite(eventbrite: FakeEventbrite):
        eventbrite.read_status = "draft"
        offering = _listed(eventbrite_published=True)

        offering.unpublish()

        assert eventbrite.names() == ["update_ticket_class", "get_event"]
        offering.refresh_from_db()
        assert offering.eventbrite_published is True  # so switching it back on never republishes it
        assert offering.eventbrite_sync_state == State.ENDED
        assert offering.eventbrite_sync_error == EventbriteSync.TAKEN_DOWN

    def it_forgets_the_publish_when_plfog_unpublishes_the_event_itself(eventbrite: FakeEventbrite):
        offering = _listed(eventbrite_published=True)

        offering.unpublish()

        assert eventbrite.names() == ["update_ticket_class", "get_event", "unpublish"]
        offering.refresh_from_db()
        assert offering.eventbrite_published is False
        assert offering.eventbrite_sync_state == State.ENDED

    def it_keeps_the_publish_when_eventbrite_refuses_the_unpublish(eventbrite: FakeEventbrite):
        eventbrite.fail["unpublish"] = EventbriteError("has orders", 400)
        offering = _listed(eventbrite_published=True)

        offering.unpublish()

        offering.refresh_from_db()
        assert offering.eventbrite_published is True
        assert offering.eventbrite_sync_error == EventbriteSync.STILL_UP


def describe_the_migration_marking_listed_events_published():
    def it_marks_every_class_with_an_event_plfog_has_not_ended():
        from importlib import import_module

        from django.apps import apps

        migration = import_module("classes.migrations.0085_classoffering_eventbrite_published")
        marked = [
            _listed(eventbrite_sync_state=state) for state in (State.LISTED, State.PENDING, State.FAILED, State.IDLE)
        ]
        ended = _listed(eventbrite_sync_state=State.ENDED)
        never = _opted_in()

        migration.mark_listed_events_published(apps, None)

        rows = [*marked, ended, never]
        published = dict(
            ClassOffering.objects.filter(pk__in=[o.pk for o in rows]).values_list("pk", "eventbrite_published")
        )
        assert published == {**{o.pk: True for o in marked}, ended.pk: False, never.pk: False}

    def it_clears_every_mark_on_the_way_back():
        from importlib import import_module

        from django.apps import apps

        migration = import_module("classes.migrations.0085_classoffering_eventbrite_published")
        offering = _listed(eventbrite_published=True)

        migration.unmark_published_events(apps, None)

        offering.refresh_from_db()
        assert offering.eventbrite_published is False


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

    def it_records_an_answer_with_no_event_id_as_a_failure_and_keeps_the_publish(eventbrite: FakeEventbrite):
        eventbrite.created_event = {}
        offering = _opted_in()

        offering.publish(None)

        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED
        assert offering.eventbrite_sync_state == State.FAILED
        assert "'id'" in offering.eventbrite_sync_error

    def it_moves_on_to_the_next_class_when_one_retry_fails(eventbrite: FakeEventbrite):
        first = _opted_in(status=ClassOffering.Status.PUBLISHED, eventbrite_sync_state=State.FAILED)
        second = _listed(eventbrite_sync_state=State.FAILED)
        eventbrite.created_event = {}

        call_command("retry_eventbrite_pushes")

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.eventbrite_sync_state == State.FAILED
        assert second.eventbrite_sync_state == State.LISTED

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
        settings.EVENTBRITE_ORGANIZER_ID = "organizer-1"
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
    def it_starts_the_run_off_eventbrite_with_no_listing():
        # #725: Submit to Eventbrite is ticked per class, with its own agreement.
        offering = _listed(eventbrite_synced_at=timezone.now())

        run = offering.duplicate_as_new_run()

        assert run.eventbrite_enabled is False
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

    def it_leaves_eventbrite_off_the_forms_of_a_live_class():
        # #725 part 2: a live class sells on Eventbrite from its own tab.
        live = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)

        assert "eventbrite_enabled" not in TeachPublishedClassForm(instance=live).fields
        assert "eventbrite_enabled" not in ClassOfferingForm(instance=live).fields
        assert "eventbrite_category" not in TeachPublishedClassForm(instance=live).fields


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


def _photo(offering: ClassOffering, name: str, sort_order: int, **kwargs: Any) -> Any:
    return ClassImageFactory(
        class_offering=offering,
        sort_order=sort_order,
        image__filename=f"{name}.jpg",
        **kwargs,
    )


def describe_gallery_photos_on_the_listing():
    def it_sends_every_gallery_photo_in_gallery_order_after_the_description(eventbrite: FakeEventbrite):
        offering = _opted_in(gallery=0)
        _photo(offering, "third", 2)
        _photo(offering, "first", 0)
        _photo(offering, "second", 1)

        offering.publish(None)

        uploaded = [args[0] for name, args in eventbrite.calls if name == "upload_content_image"]
        assert [PurePosixPath(n).stem.split("_")[0] for n in uploaded] == ["first", "second", "third"]
        assert eventbrite.args("set_description")[2] == ["img-1", "img-2", "img-3"]
        stored = list(offering.gallery_images.values_list("eventbrite_image_id", flat=True))
        assert stored == ["img-1", "img-2", "img-3"]
        assert eventbrite.names()[-1] == "publish"

    def it_breaks_a_sort_order_tie_by_upload_time(eventbrite: FakeEventbrite):
        offering = _listed(gallery=0)
        older = _photo(offering, "older", 0, eventbrite_image_id="img-older")
        newer = _photo(offering, "newer", 0, eventbrite_image_id="img-newer")
        ClassImage.objects.filter(pk=older.pk).update(created_at=newer.created_at - timedelta(minutes=5))

        offering.sync_eventbrite_listing()

        assert eventbrite.args("set_description")[2] == ["img-older", "img-newer"]

    def it_uploads_only_photos_not_sent_before(eventbrite: FakeEventbrite):
        offering = _listed(gallery=0)
        _photo(offering, "sent", 0, eventbrite_image_id="img-sent")
        _photo(offering, "fresh", 1)

        offering.sync_eventbrite_listing()

        assert eventbrite.names().count("upload_content_image") == 1
        assert eventbrite.args("set_description")[2] == ["img-sent", "img-1"]

    def it_makes_no_upload_call_when_the_gallery_is_unchanged(eventbrite: FakeEventbrite):
        offering = _listed(gallery=0)
        _photo(offering, "one", 0)
        offering.sync_eventbrite_listing()
        eventbrite.calls.clear()

        offering.sync_eventbrite_listing()

        assert "upload_content_image" not in eventbrite.names()
        assert eventbrite.args("set_description")[2] == ["img-1"]

    def it_sends_a_class_with_no_gallery_as_text_alone(eventbrite: FakeEventbrite):
        _listed(gallery=0).sync_eventbrite_listing()

        assert eventbrite.args("set_description")[2] == []


def describe_a_photo_eventbrite_refuses():
    def it_leaves_the_photo_out_and_still_lists_and_publishes(eventbrite: FakeEventbrite):
        eventbrite.refused_photos["bad"] = EventbriteError("Image upload: 400 too small", 400)
        offering = _opted_in(gallery=0)
        _photo(offering, "good", 0)
        _photo(offering, "bad", 1)

        offering.publish(None)

        assert eventbrite.args("set_description")[2] == ["img-1"]
        assert "publish" in eventbrite.names()
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.LISTED
        assert offering.eventbrite_sync_error == "1 photo could not be sent: Image upload: 400 too small"
        assert (
            offering.eventbrite_sync_label
            == "Listed on Eventbrite. 1 photo could not be sent: Image upload: 400 too small"
        )

    def it_counts_every_photo_left_out(eventbrite: FakeEventbrite):
        eventbrite.refused_photos["bad"] = EventbriteError("Image upload: 400 too small", 400)
        offering = _listed(gallery=0)
        _photo(offering, "bad-one", 0)
        _photo(offering, "bad-two", 1)

        offering.sync_eventbrite_listing()

        offering.refresh_from_db()
        assert offering.eventbrite_sync_error.startswith("2 photos could not be sent: ")

    def it_leaves_out_a_photo_whose_file_is_missing(eventbrite: FakeEventbrite):
        offering = _listed(gallery=0)
        photo = _photo(offering, "gone", 0)
        photo.image.storage.delete(photo.image.name)

        offering.sync_eventbrite_listing()

        offering.refresh_from_db()
        assert eventbrite.args("set_description")[2] == []
        assert offering.eventbrite_sync_state == State.LISTED
        assert offering.eventbrite_sync_error.startswith("1 photo could not be sent: ")

    def it_clears_the_note_on_the_next_clean_sync(eventbrite: FakeEventbrite):
        eventbrite.refused_photos["bad"] = EventbriteError("Image upload: 500 down", 500)
        offering = _listed(gallery=0)
        _photo(offering, "bad", 0)
        offering.sync_eventbrite_listing()
        eventbrite.refused_photos.clear()

        offering.sync_eventbrite_listing()

        offering.refresh_from_db()
        assert offering.eventbrite_sync_error == ""
        assert offering.eventbrite_sync_label == "Listed on Eventbrite"
        assert eventbrite.descriptions()[-1][2] == ["img-1"]


def describe_a_description_eventbrite_refuses_with_its_photos():
    def it_sends_the_text_alone_lists_the_class_and_notes_it(eventbrite: FakeEventbrite):
        eventbrite.refuse_image_modules = EventbriteError("POST structured_content: 400 bad module", 400)
        offering = _opted_in(gallery=0)
        _photo(offering, "one", 0)

        offering.publish(None)

        # With widgets, then without (the request verified before #716), then without the photos.
        assert [(args[2], args[3]) for args in eventbrite.descriptions()] == [
            (["img-1"], _EVENTBRITE_LOCKED),
            (["img-1"], None),
            ([], None),
        ]
        assert "publish" in eventbrite.names()
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.LISTED
        assert offering.eventbrite_sync_error == (
            "Eventbrite refused the page, so it went without its widgets "
            "(the FAQ went into the description as text) and its photos: "
            "POST structured_content: 400 bad module"
        )

    def it_records_a_failure_when_the_refusal_is_not_about_the_content(eventbrite: FakeEventbrite):
        eventbrite.refuse_image_modules = EventbriteError("POST structured_content: 500 down", 500)
        offering = _listed(gallery=0)
        _photo(offering, "one", 0)

        offering.sync_eventbrite_listing()

        offering.refresh_from_db()
        assert len(eventbrite.descriptions()) == 1
        assert offering.eventbrite_sync_state == State.FAILED


def describe_a_gallery_change_on_a_listed_class():
    def it_waits_for_the_retry_tick_then_updates_the_page_without_republishing(eventbrite: FakeEventbrite):
        offering = _listed(gallery=0)
        _photo(offering, "new", 0)

        assert ClassOffering.objects.filter(pk=offering.pk).mark_eventbrite_gallery_changed() == 1

        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.PENDING
        assert offering.eventbrite_sync_label == "Waiting to sync: The gallery changed."
        assert eventbrite.calls == []

        call_command("retry_eventbrite_pushes")

        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.LISTED
        assert offering.eventbrite_sync_error == ""
        assert eventbrite.args("set_description")[2] == ["img-1"]
        assert "publish" not in eventbrite.names()

    @pytest.mark.parametrize("state", [State.IDLE, State.ENDED, State.FAILED])
    def it_leaves_a_class_that_is_not_listed_as_it_is(eventbrite: FakeEventbrite, state: str):
        offering = _listed(gallery=0, eventbrite_sync_state=state, eventbrite_sync_error="kept")

        assert ClassOffering.objects.filter(pk=offering.pk).mark_eventbrite_gallery_changed() == 0

        offering.refresh_from_db()
        assert (offering.eventbrite_sync_state, offering.eventbrite_sync_error) == (state, "kept")


def describe_publishing_follows_the_status_eventbrite_reports():
    """Publish is decided by the event's ``status`` on Eventbrite, never by the sync state or its note."""

    def it_does_not_publish_a_live_event_again_after_a_failed_push(eventbrite: FakeEventbrite):
        offering = _listed(eventbrite_sync_state=State.FAILED, eventbrite_sync_error="POST: 500 down")

        offering.sync_eventbrite_listing()

        assert "publish" not in eventbrite.names()
        offering.refresh_from_db()
        assert (offering.eventbrite_sync_state, offering.eventbrite_sync_error) == (State.LISTED, "")

    def it_does_not_publish_a_live_event_left_pending_while_sync_was_off(eventbrite: FakeEventbrite):
        offering = _listed(eventbrite_sync_state=State.PENDING, eventbrite_sync_error=EventbriteSync.SYNC_OFF)

        call_command("retry_eventbrite_pushes")

        assert "publish" not in eventbrite.names()
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.LISTED

    def it_publishes_a_draft_event_whose_publish_failed(eventbrite: FakeEventbrite):
        # Class 675's shape: the event exists on Eventbrite but its publish was refused.
        eventbrite.event_status = "draft"
        offering = _listed(eventbrite_sync_state=State.FAILED, eventbrite_sync_error="POST publish: 400")

        call_command("retry_eventbrite_pushes")

        assert eventbrite.names()[-1] == "publish"
        assert eventbrite.args("publish") == ("ev-9",)
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.LISTED

    @pytest.mark.parametrize("status", ["live", "started", "ended", "completed", "canceled"])
    def it_publishes_only_a_draft(eventbrite: FakeEventbrite, status: str):
        eventbrite.event_status = status

        _listed(eventbrite_sync_state=State.FAILED).sync_eventbrite_listing()

        assert "publish" not in eventbrite.names()

    def it_notes_an_event_canceled_on_eventbrite_that_nothing_is_for_sale(eventbrite: FakeEventbrite):
        eventbrite.event_status = "canceled"
        offering = _listed(eventbrite_sync_state=State.FAILED)

        offering.sync_eventbrite_listing()

        assert "publish" not in eventbrite.names()
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.LISTED
        assert offering.eventbrite_sync_label == (
            "Listed on Eventbrite. The event is canceled on Eventbrite, so nothing is for sale."
        )

    @pytest.mark.parametrize("status", ["live", "started"])
    def it_adds_no_note_while_the_event_is_selling_or_running(eventbrite: FakeEventbrite, status: str):
        eventbrite.event_status = status
        offering = _listed(eventbrite_sync_state=State.FAILED, eventbrite_sync_error="old")

        offering.sync_eventbrite_listing()

        offering.refresh_from_db()
        assert offering.eventbrite_sync_error == ""

    def it_records_an_answer_with_no_status_as_a_failure(eventbrite: FakeEventbrite):
        eventbrite.event_status = None
        offering = _listed(eventbrite_sync_state=State.FAILED)

        offering.sync_eventbrite_listing()

        assert "publish" not in eventbrite.names()
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.FAILED
        assert "'status'" in offering.eventbrite_sync_error


def describe_saving_a_live_class_with_its_faq():
    def it_sends_the_new_question_on_the_next_tick(eventbrite: FakeEventbrite, client: Any):
        MembershipPlanFactory()
        instructor = InstructorFactory(user=UserFactory(username="eb-faq@example.com", email="eb-faq@example.com"))
        offering = _listed(instructor=instructor)
        client.force_login(instructor.user)
        payload = {
            "description": offering.description,
            "eventbrite_enabled": "on",
            "faq-TOTAL_FORMS": "1",
            "faq-INITIAL_FORMS": "0",
            "faq-MIN_NUM_FORMS": "0",
            "faq-MAX_NUM_FORMS": "1000",
            "faq-0-question": "Is the kiln vented?",
            "faq-0-answer": "Yes.",
        }

        response = client.post(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}), payload)

        assert response.status_code == 302
        call_command("retry_eventbrite_pushes")
        assert eventbrite.descriptions()[-1][3] == [
            *_EVENTBRITE_LOCKED,
            {"question": "Is the kiln vented?", "answer": "Yes."},
        ]


def describe_a_listing_eventbrite_refuses_with_its_faq_and_its_photos():
    def it_sends_the_faq_as_text_without_photos_and_notes_both(eventbrite: FakeEventbrite):
        eventbrite.refuse_widgets = EventbriteError("POST structured_content: 400 bad widget", 400)
        eventbrite.refuse_image_modules = EventbriteError("POST structured_content: 400 bad module", 400)
        offering = _opted_in()
        ClassFaqFactory(class_offering=offering, question="Is the kiln vented?", answer="Yes.")

        offering.publish(None)

        attempts = eventbrite.descriptions()
        assert [(bool(images), faqs is not None) for _, _, images, faqs in attempts] == [
            (True, True),
            (True, False),
            (False, False),
        ]
        assert "<p><strong>Is the kiln vented?</strong></p><p>Yes.</p>" in attempts[-1][1]
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.LISTED
        assert offering.eventbrite_sync_error == (
            "Eventbrite refused the page, so it went without its widgets (the FAQ went into the description as text) "
            "and its photos: POST structured_content: 400 bad module"
        )


# ── #725: Eventbrite's selling rules, checked before a class is sold there ──

_REFUSAL = "Eventbrite would take this listing down. Fix these, then save again."
_KATE = "Materials are paid at the session (cash/venmo)."  # Kate's #619 to #621
_LAB_FEES = "Lab fees apply and are not included in the price."  # #605 to #614
_INCLUDED = "A $10 materials fee is included in the class price."


def _faq_post(*rows: tuple[str, str], saved: Sequence[Any] = ()) -> dict[str, str]:
    """The FAQ formset as the composer posts it: the saved rows first (kept), then new ones."""
    data = {
        "faq-TOTAL_FORMS": str(len(saved) + len(rows)),
        "faq-INITIAL_FORMS": str(len(saved)),
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
    }
    for i, faq in enumerate(saved):
        data |= {f"faq-{i}-id": str(faq.pk), f"faq-{i}-question": faq.question, f"faq-{i}-answer": faq.answer}
    for i, (question, answer) in enumerate(rows, start=len(saved)):
        data |= {f"faq-{i}-question": question, f"faq-{i}-answer": answer}
    return data


def _composer_post(offering: ClassOffering | None = None, **overrides: str) -> dict[str, str]:
    """A fixed class with no dates yet, Eventbrite on and the rules agreed."""
    category = offering.category if offering is not None else CategoryFactory()
    data = {
        "title": offering.title if offering is not None else "Rules Check Class",
        "category": str(category.pk),
        "description": "A hands-on class.",
        "price_cents": "50.00",
        "capacity": "6",
        "scheduling_model": ClassOffering.SchedulingModel.FIXED,
        "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
        "eventbrite_enabled": "on",
        "eventbrite_fee_payer": ClassOffering.EventbriteFeePayer.BUYER,
        "sessions-TOTAL_FORMS": "0",
        "sessions-INITIAL_FORMS": "0",
        "sessions-MIN_NUM_FORMS": "0",
        "sessions-MAX_NUM_FORMS": "1000",
        **_faq_post(),
    }
    return {**data, **overrides}


def _described(offering: ClassOffering, description: str) -> ClassOffering:
    """Set the description after the factory, whose ``ready`` hook writes its own."""
    ClassOffering.objects.filter(pk=offering.pk).update(description=description)
    offering.refresh_from_db()
    return offering


def _rules_admin() -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username="eb-rules-admin", email="eb-rules-admin@example.com")
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _rules_instructor() -> Member:
    MembershipPlanFactory()
    return InstructorFactory(user=UserFactory(username="eb-rules-teacher@example.com"), instructor_slug="eb-rules-t")


def describe_the_rules_check_on_the_class_forms():
    @pytest.fixture(autouse=True)
    def _integration_on(eventbrite: FakeEventbrite) -> None:
        """Every form here offers the Eventbrite fields."""

    def _form(offering: ClassOffering, **overrides: str) -> TeachClassOfferingForm:
        form = TeachClassOfferingForm(_composer_post(offering, **overrides), instance=offering)
        assert form.is_valid(), form.errors
        return form

    def it_refuses_payment_outside_the_ticket_quoting_the_words():
        offering = ClassOfferingFactory()
        form = _form(offering, description=_KATE)

        assert form.accepts_eventbrite_listing(UserFactory()) is False
        assert form.errors["eventbrite_enabled"] == [
            _REFUSAL,
            "Description: payment outside the ticket: “paid at the session”, “cash”, “venmo”",
        ]

    def it_refuses_a_fee_not_included_and_passes_one_stated_as_included():
        refused = _form(ClassOfferingFactory(), description=_LAB_FEES)
        passed = _form(ClassOfferingFactory(), description=_INCLUDED)

        assert refused.accepts_eventbrite_listing(UserFactory()) is False
        assert (
            refused.errors["eventbrite_enabled"][1] == "Description: a cost not included in the price: “Lab fees apply”"
        )
        assert passed.accepts_eventbrite_listing(UserFactory()) is True
        assert passed.errors == {}

    def it_refuses_a_discount_code_and_an_address_in_the_title():
        form = _form(ClassOfferingFactory(), title="Rings, text 503-555-0182", description="Use code PLHalfOff.")

        assert form.accepts_eventbrite_listing(UserFactory()) is False
        assert form.errors["eventbrite_enabled"] == [
            _REFUSAL,
            "Title: a link, email, phone number or handle in the title: “503-555-0182”",
            "Description: a discount code: “Use code PLHalfOff”",
        ]

    def it_checks_the_faq_rows_as_posted_not_as_saved():
        offering = ClassOfferingFactory()
        saved = ClassFaqFactory(class_offering=offering, question="Payment?", answer="Venmo the instructor.")
        form = _form(offering)
        deleting = build_class_faq_formset(
            {**_faq_post(saved=[saved]), "faq-0-DELETE": "on"}
            | _faq_post(("Deals?", "Use code PLMetal10"), saved=[saved]),
            offering,
        )
        assert deleting.is_valid(), deleting.errors

        assert form.accepts_eventbrite_listing(UserFactory(), deleting) is False
        assert form.errors["eventbrite_enabled"] == [_REFUSAL, "FAQ “Deals?”: a discount code: “Use code PLMetal10”"]

    def it_passes_once_the_bad_faq_row_is_deleted():
        offering = ClassOfferingFactory()
        saved = ClassFaqFactory(class_offering=offering, question="Payment?", answer="Venmo the instructor.")
        formset = build_class_faq_formset({**_faq_post(saved=[saved]), "faq-0-DELETE": "on"}, offering)
        assert formset.is_valid(), formset.errors

        assert _form(offering).accepts_eventbrite_listing(UserFactory(), formset) is True

    def it_lists_what_is_left_out_without_blocking():
        form = _form(ClassOfferingFactory(), description="Follow @covo.studio for photos.")
        formset = build_class_faq_formset(_faq_post(("Can I cancel?", "Email us.")), ClassOfferingFactory())
        assert formset.is_valid(), formset.errors

        assert form.accepts_eventbrite_listing(UserFactory(), formset) is True
        assert form.eventbrite_check is not None
        assert [str(finding) for finding in form.eventbrite_check.left_out] == [
            "Description: a link, email, phone number or handle: “@covo.studio”",
            "FAQ “Can I cancel?”: a cancellation, refund or no-show question",
        ]

    def it_leaves_a_class_with_eventbrite_off_alone():
        offering = ClassOfferingFactory()
        data = _composer_post(offering, description=_KATE)
        del data["eventbrite_enabled"]
        form = TeachClassOfferingForm(data, instance=offering)
        assert form.is_valid(), form.errors

        assert form.accepts_eventbrite_listing(UserFactory()) is True
        assert form.eventbrite_check is None

    def it_leaves_a_form_without_the_switch_alone():
        offering = ClassOfferingFactory(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE, description=_KATE)
        form = TeachPublishedClassForm({"description": _KATE, **_faq_post()}, instance=offering)
        assert form.is_valid(), form.errors

        assert "eventbrite_enabled" not in form.fields
        assert form.accepts_eventbrite_listing(UserFactory()) is True

    def describe_the_agreement():
        """AC8: turning the switch on is the agreement; a save with it on records who and when, once."""

        def it_says_so_beside_the_switch():
            help_text = TeachClassOfferingForm().fields["eventbrite_enabled"].help_text

            assert help_text.startswith(
                "Ticking this is my agreement to keep the listing within Eventbrite's rules below."
            )

        def it_records_who_agreed_and_when_on_the_save():
            offering = ClassOfferingFactory()
            user = UserFactory()
            form = _form(offering)

            assert form.accepts_eventbrite_listing(user) is True
            before = timezone.now()
            form.save()
            offering.refresh_from_db()

            assert offering.eventbrite_rules_agreed_by == user
            assert before - timedelta(seconds=5) <= offering.eventbrite_rules_agreed_at <= timezone.now()
            assert offering.needs_eventbrite_agreement is False

        def describe_a_class_on_without_an_agreement():
            """Class 675's case: switched on before the rules; the box shows unticked and the sync waits."""

            def _unagreed(**kwargs: Any) -> ClassOffering:
                """Live and on Eventbrite, with nobody's agreement."""
                return _listed(eventbrite_rules_agreed_at=None, **kwargs)

            def _unagreed_draft() -> ClassOffering:
                """Not live yet, so its box is on the composer's last page."""
                return _opted_in(eventbrite_rules_agreed_at=None)

            def it_offers_the_box_unticked():
                offering = _unagreed_draft()
                form = TeachClassOfferingForm(instance=offering)

                assert form.eventbrite_awaiting_agreement is True
                assert form["eventbrite_enabled"].value() is False
                assert form.eventbrite_check is None

            def it_keeps_the_opt_in_on_an_unticked_save_and_checks_and_records_nothing():
                offering = _described(_unagreed_draft(), _KATE)
                data = _composer_post(offering, description=_KATE)
                del data["eventbrite_enabled"]
                form = TeachClassOfferingForm(data, instance=offering)
                assert form.is_valid(), form.errors

                assert form.accepts_eventbrite_listing(UserFactory()) is True
                saved = form.save()
                assert (saved.eventbrite_enabled, saved.eventbrite_rules_agreed_at) == (True, None)

            def it_checks_and_records_the_agreement_once_ticked():
                offering = _unagreed_draft()
                user = UserFactory()
                form = _form(offering)

                assert form.accepts_eventbrite_listing(user) is True
                assert form.save().eventbrite_rules_agreed_by == user

            def it_refuses_a_ticked_save_that_fails_the_check():
                offering = _unagreed_draft()

                assert _form(offering, description=_KATE).accepts_eventbrite_listing(UserFactory()) is False

            def it_does_not_sync_or_end_the_listing_until_someone_agrees(eventbrite: FakeEventbrite):
                offering = _unagreed()

                offering.sync_eventbrite_listing()
                offering.refresh_from_db()

                assert eventbrite.calls == []
                assert (offering.eventbrite_sync_state, offering.eventbrite_enabled) == (State.LISTED, True)

            def it_leaves_a_taken_down_event_alone_on_an_unticked_live_page_save(
                eventbrite: FakeEventbrite, client: Any
            ):
                instructor = _rules_instructor()
                offering = _unagreed(
                    instructor=instructor,
                    eventbrite_published=True,
                    eventbrite_sync_state=State.ENDED,
                    eventbrite_sync_error=EventbriteSync.TAKEN_DOWN,
                )
                client.force_login(instructor.user)

                response = client.post(
                    reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
                    {"description": "A hands-on class, edited.", **_faq_post()},
                )

                assert response.status_code == 302
                offering.refresh_from_db()
                assert eventbrite.calls == []
                assert (offering.eventbrite_enabled, offering.eventbrite_sync_state) == (True, State.ENDED)

        def it_keeps_the_first_agreement():
            first = UserFactory()
            offering = ClassOfferingFactory(
                eventbrite_enabled=True, eventbrite_rules_agreed_by=first, eventbrite_rules_agreed_at=timezone.now()
            )
            form = _form(offering)

            assert form.accepts_eventbrite_listing(UserFactory()) is True
            assert form.save().eventbrite_rules_agreed_by == first

        def it_records_nothing_while_eventbrite_is_off():
            offering = ClassOfferingFactory()
            data = _composer_post(offering)
            del data["eventbrite_enabled"]
            form = TeachClassOfferingForm(data, instance=offering)
            assert form.is_valid(), form.errors

            assert form.accepts_eventbrite_listing(UserFactory()) is True
            assert form.save().eventbrite_rules_agreed_at is None

        def it_records_nothing_for_a_refused_save():
            offering = ClassOfferingFactory()
            form = _form(offering, description=_KATE)

            assert form.accepts_eventbrite_listing(UserFactory()) is False
            assert offering.eventbrite_rules_agreed_at is None

    def describe_the_submit_box():
        """AC8: the opt in reads Submit to Eventbrite, and ticking it reveals the rest (Alpine ``ebOn``)."""

        @pytest.mark.parametrize("form_class", [TeachClassOfferingForm, ClassOfferingForm])
        def it_names_the_box_and_binds_it_to_the_reveal(form_class: Any):
            field = form_class(instance=_opted_in()).fields["eventbrite_enabled"]

            assert field.label == "Submit to Eventbrite"
            assert field.widget.attrs == {"x-model": "ebOn"}

    def describe_the_check_on_a_fresh_page():
        def it_shows_a_saved_class_with_eventbrite_on_what_is_left_out():
            offering = ClassOfferingFactory(eventbrite_enabled=True, description="Photos at pastlives.space")
            ClassFaqFactory(class_offering=offering, question="Refunds?", answer="Ask us.")

            form = TeachClassOfferingForm(instance=offering)

            assert form.eventbrite_check is not None
            assert [finding.where for finding in form.eventbrite_check.left_out] == ["Description", "FAQ “Refunds?”"]

        @pytest.mark.parametrize("enabled", [False, True])
        def it_checks_nothing_for_a_new_class_or_one_with_eventbrite_off(enabled: bool):
            off = ClassOfferingFactory(eventbrite_enabled=False)
            new = ClassOffering(eventbrite_enabled=enabled)

            assert TeachClassOfferingForm(instance=off).eventbrite_check is None
            assert TeachClassOfferingForm(instance=new).eventbrite_check is None

        def it_checks_nothing_on_a_posted_form_until_asked():
            offering = ClassOfferingFactory(eventbrite_enabled=True, description=_KATE)

            assert TeachClassOfferingForm(_composer_post(offering), instance=offering).eventbrite_check is None


def describe_the_sync_with_the_rules_check():
    """AC9: a class edited through any other path is refused by the sync, with the same list."""

    def it_marks_a_failing_class_failed_with_the_list_and_sends_nothing(eventbrite: FakeEventbrite):
        offering = _described(_listed(), f"<p>{_KATE}</p>")

        offering.sync_eventbrite_listing()
        offering.refresh_from_db()

        assert eventbrite.calls == []
        assert offering.eventbrite_sync_state == State.FAILED
        assert offering.eventbrite_sync_error == (
            f"{_REFUSAL} Description: payment outside the ticket: “paid at the session”, “cash”, “venmo”"
        )

    def it_reads_the_saved_faq_rows(eventbrite: FakeEventbrite):
        offering = _opted_in(status=ClassOffering.Status.PUBLISHED)
        ClassFaqFactory(class_offering=offering, question="Deals?", answer="Use code PLMetal10")

        offering.sync_eventbrite_listing()

        assert eventbrite.calls == []
        assert offering.eventbrite_sync_error == f"{_REFUSAL} FAQ “Deals?”: a discount code: “Use code PLMetal10”"

    def it_refuses_even_while_sync_is_off():
        offering = _opted_in(status=ClassOffering.Status.PUBLISHED, title="Rings @covo")

        offering.sync_eventbrite_listing()

        assert offering.eventbrite_sync_state == State.FAILED

    def it_keeps_the_error_to_the_column_length(eventbrite: FakeEventbrite):
        faqs = [(f"Question {n}?", f"Pay at the door, venmo, Lab fees apply, code PL{n}0OFF") for n in range(12)]
        offering = _opted_in(status=ClassOffering.Status.PUBLISHED)
        for question, answer in faqs:
            ClassFaqFactory(class_offering=offering, question=question, answer=answer)

        offering.sync_eventbrite_listing()

        assert len(offering.eventbrite_sync_error) == 500
        assert offering.eventbrite_sync_error.startswith(_REFUSAL)

    def it_still_ends_a_listing_whose_class_fails(eventbrite: FakeEventbrite):
        offering = _described(_listed(), f"<p>{_KATE}</p>")

        assert offering.end_eventbrite_listing() is True
        assert offering.eventbrite_sync_state == State.ENDED

    def it_lists_a_clean_class_as_before(eventbrite: FakeEventbrite):
        offering = _described(_opted_in(status=ClassOffering.Status.PUBLISHED), f"<p>{_INCLUDED}</p>")

        offering.sync_eventbrite_listing()

        assert offering.eventbrite_sync_state == State.LISTED


def describe_the_rules_on_the_pages():
    """AC7 and AC8: Submit to Eventbrite on the composer's last page and the live class edit page."""

    @pytest.fixture(autouse=True)
    def _integration_on(eventbrite: FakeEventbrite) -> None:
        """The Eventbrite section renders."""

    def _section(html: str) -> str:
        return html.split("data-eventbrite-submit", 1)[1].split("</div>\n</div>", 1)[0]

    def _step(html: str, number: int) -> str:
        return html.split(f'data-composer-step="{number}"', 1)[1].split("data-composer-step=", 1)[0]

    def it_puts_the_box_on_the_last_page_of_the_instructor_composer_and_nowhere_else(client: Any):
        client.force_login(_rules_instructor().user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()

        last = html.split('data-composer-step="6"', 1)[1]
        assert 'name="eventbrite_enabled"' in last
        assert 'name="eventbrite_enabled"' not in _step(html, 3)
        assert html.count('name="eventbrite_enabled"') == 1
        assert "Submit to Eventbrite" in last

    def it_shows_the_rules_beside_the_box_and_hides_the_rest_until_ticked(client: Any):
        client.force_login(_rules_instructor().user)

        section = _section(client.get(reverse("classes:teach_class_create")).content.decode())

        assert section.index('name="eventbrite_enabled"') < section.index("data-eventbrite-rules>")
        assert "data-eventbrite-rules>Eventbrite takes down listings that send buyers anywhere else." in section
        assert "ebOn: false" in section
        details = section.split("data-eventbrite-details", 1)[1]
        assert details.startswith(' x-show="ebOn" x-cloak>')
        assert 'name="eventbrite_fee_payer"' in details
        assert 'name="eventbrite_category"' in details
        assert section.index("data-eventbrite-rules>") < section.index("data-eventbrite-details")

    def it_checks_the_typed_text_only_while_ticked(client: Any):
        client.force_login(_rules_instructor().user)

        section = _section(client.get(reverse("classes:teach_class_create")).content.decode())

        assert f'hx-post="{reverse("classes:teach_new_class_eventbrite_check")}"' in section
        trigger = section.split('hx-trigger="', 1)[1].split('"', 1)[0]
        ticked = "[this.closest('[data-eventbrite-submit]').querySelector('input[name=eventbrite_enabled]').checked]"
        assert trigger == f"change{ticked} from:closest form delay:400ms, input{ticked} from:closest form delay:800ms"
        assert "data-eventbrite-check-result" not in section

    def it_posts_the_admin_composer_check_to_the_admin_route(client: Any):
        client.force_login(_rules_admin())

        section = _section(client.get(reverse("classes:admin_class_create")).content.decode())

        assert f'hx-post="{reverse("classes:admin_new_class_eventbrite_check")}"' in section

    def it_shows_a_ticked_class_its_result_on_load(client: Any):
        instructor = _rules_instructor()
        offering = ClassOfferingFactory(instructor=instructor, eventbrite_enabled=True, description="Text 503-555-0182")
        client.force_login(instructor.user)

        section = _section(client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode())

        assert f'hx-post="{reverse("classes:teach_class_eventbrite_check", kwargs={"pk": offering.pk})}"' in section
        assert "data-eventbrite-ready" in section
        assert (
            '<li class="pl-compose-section__note">Description: a link, email, phone number or handle: “503-555-0182”</li>'
            in section
        )
        assert "ebOn: true" in section

    def it_shows_a_failing_ticked_class_its_problems_on_load(client: Any):
        instructor = _rules_instructor()
        offering = _described(ClassOfferingFactory(instructor=instructor, eventbrite_enabled=True), _KATE)
        client.force_login(instructor.user)

        section = _section(client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode())

        assert "data-eventbrite-refusal" in section
        assert f"<p>{_REFUSAL}</p>" in section

    def it_shows_who_agreed_and_when(client: Any):
        instructor = _rules_instructor()
        instructor.user.first_name, instructor.user.last_name = "Kate", "Rivers"
        instructor.user.save(update_fields=["first_name", "last_name"])
        offering = ClassOfferingFactory(
            instructor=instructor,
            eventbrite_enabled=True,
            eventbrite_rules_agreed_by=instructor.user,
            eventbrite_rules_agreed_at=datetime(2026, 10, 9, 19, 0, tzinfo=UTC),  # noon in Portland
        )
        client.force_login(instructor.user)

        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()

        assert "data-eventbrite-agreed>Eventbrite's rules agreed to on Oct 9, 2026 by Kate Rivers." in html

    def it_shows_no_agreement_line_before_anyone_agreed(client: Any):
        client.force_login(_rules_instructor().user)

        assert "data-eventbrite-agreed" not in client.get(reverse("classes:teach_class_create")).content.decode()

    def it_points_the_live_class_edit_page_to_the_eventbrite_tab(client: Any):
        instructor = _rules_instructor()
        offering = _listed(instructor=instructor)
        client.force_login(instructor.user)

        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()

        tab = reverse("classes:teach_class_eventbrite", kwargs={"pk": offering.pk})
        assert f'data-eventbrite-tab-link>Selling this class on Eventbrite has its own tab: <a href="{tab}">' in html
        assert 'name="eventbrite_enabled"' not in html
        assert 'name="eventbrite_category"' not in html

    def it_points_the_admin_composer_of_a_live_class_to_the_eventbrite_tab(client: Any):
        client.force_login(_rules_admin())
        offering = _listed()

        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()

        assert "data-eventbrite-tab-link" in html
        assert 'name="eventbrite_enabled"' not in html


def describe_the_check_as_typed():
    """AC8: the Eventbrite section checks the text as typed, unsaved edits included, and saves nothing."""

    @pytest.fixture(autouse=True)
    def _integration_on(eventbrite: FakeEventbrite) -> None:
        """The composer forms offer Eventbrite."""

    def it_checks_the_unsaved_text_of_a_saved_class_and_saves_nothing(client: Any):
        instructor = _rules_instructor()
        offering = ClassOfferingFactory(instructor=instructor)
        ClassFaqFactory(class_offering=offering, question="Pay?", answer="Venmo me.")
        client.force_login(instructor.user)
        url = reverse("classes:teach_class_eventbrite_check", kwargs={"pk": offering.pk})
        data = {
            "title": "Rings @covo",
            "subtitle": "",
            "description": _LAB_FEES,
            **_faq_post(("Deals?", "Use code PLHalfOff")),
        }

        html = client.post(url, data).content.decode()

        assert "data-eventbrite-refusal" in html
        assert "<li>Title: a link, email, phone number or handle in the title: “@covo”</li>" in html
        assert "<li>Description: a cost not included in the price: “Lab fees apply”</li>" in html
        assert "<li>FAQ “Deals?”: a discount code: “Use code PLHalfOff”</li>" in html
        assert "Venmo" not in html  # the saved FAQ row was not posted, so it is not read
        offering.refresh_from_db()
        assert (offering.title.startswith("Class"), offering.description) == (True, "A hands-on class.")

    def it_passes_clean_text_and_lists_what_is_left_out(client: Any):
        instructor = _rules_instructor()
        offering = _described(ClassOfferingFactory(instructor=instructor), _KATE)
        client.force_login(instructor.user)
        url = reverse("classes:teach_class_eventbrite_check", kwargs={"pk": offering.pk})

        html = client.post(url, {"title": "Rings", "description": f"{_INCLUDED} See pastlives.space"}).content.decode()

        assert "data-eventbrite-ready" in html
        assert "Ready for Eventbrite</span>" in html
        assert ">Description: a link, email, phone number or handle: “pastlives.space”</li>" in html

    def it_reads_a_field_the_page_does_not_post_from_the_saved_class(client: Any):
        instructor = _rules_instructor()
        offering = ClassOfferingFactory(instructor=instructor, title="Rings, text 503-555-0182")
        ClassFaqFactory(class_offering=offering, question="Deals?", answer="Use code PLMetal10")
        client.force_login(instructor.user)
        url = reverse("classes:teach_class_eventbrite_check", kwargs={"pk": offering.pk})

        html = client.post(url, {"description": "Clean."}).content.decode()

        assert "Title: a link, email, phone number or handle in the title: “503-555-0182”" in html
        assert "FAQ “Deals?”: a discount code: “Use code PLMetal10”" in html

    def it_skips_a_deleted_faq_row(client: Any):
        instructor = _rules_instructor()
        offering = ClassOfferingFactory(instructor=instructor)
        saved = ClassFaqFactory(class_offering=offering, question="Pay?", answer="Venmo me.")
        client.force_login(instructor.user)
        url = reverse("classes:teach_class_eventbrite_check", kwargs={"pk": offering.pk})

        html = client.post(url, {**_faq_post(saved=[saved]), "faq-0-DELETE": "on"}).content.decode()

        assert "data-eventbrite-ready" in html

    def it_checks_a_new_class_for_an_instructor(client: Any):
        client.force_login(_rules_instructor().user)

        html = client.post(reverse("classes:teach_new_class_eventbrite_check"), {"description": _KATE}).content.decode()

        assert "Description: payment outside the ticket: “paid at the session”, “cash”, “venmo”" in html
        assert not ClassOffering.objects.exists()

    def it_checks_a_new_class_for_an_admin(client: Any):
        client.force_login(_rules_admin())

        html = client.post(reverse("classes:admin_new_class_eventbrite_check"), {"title": "Rings"}).content.decode()

        assert "data-eventbrite-ready" in html

    def describe_who_may_check():
        def it_hides_a_class_from_a_member_who_cannot_edit_it(client: Any):
            offering = ClassOfferingFactory(instructor=_rules_instructor())
            MembershipPlanFactory()
            client.force_login(UserFactory(username="eb-stranger@example.com"))

            response = client.post(reverse("classes:teach_class_eventbrite_check", kwargs={"pk": offering.pk}), {})

            assert response.status_code == 404

        def it_hides_a_class_from_a_reviewer_who_may_read_it_but_not_edit_it(client: Any):
            from membership.models import AdminCapability

            offering = ClassOfferingFactory(instructor=_rules_instructor())
            reviewer = UserFactory(username="eb-reviewer@example.com")
            AdminCapability.objects.create(member=reviewer.member, capability=AdminCapability.Capability.CLASS_APPROVER)
            client.force_login(reviewer)
            url = reverse("classes:teach_class_eventbrite_check", kwargs={"pk": offering.pk})

            assert client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).status_code == 200
            assert client.post(url, {}).status_code == 404

        def it_sends_a_member_without_teaching_access_away_from_the_new_class_check(client: Any):
            MembershipPlanFactory()
            client.force_login(UserFactory(username="eb-not-teaching@example.com"))

            response = client.post(reverse("classes:teach_new_class_eventbrite_check"), {})

            assert response.status_code == 302

        def it_refuses_a_non_admin_on_the_admin_check(client: Any):
            client.force_login(_rules_instructor().user)

            response = client.post(reverse("classes:admin_new_class_eventbrite_check"), {})

            assert response.status_code in {302, 403, 404}
            assert b"data-eventbrite-check-result" not in response.content

        def it_takes_posts_only(client: Any):
            client.force_login(_rules_instructor().user)

            assert client.get(reverse("classes:teach_new_class_eventbrite_check")).status_code == 405


def describe_a_refused_save_through_each_page():
    """AC5 end to end: every save path shows the list and saves nothing."""

    @pytest.fixture(autouse=True)
    def _integration_on(eventbrite: FakeEventbrite) -> None:
        """The Eventbrite fields are on every form."""

    def _refused(response: Any) -> str:
        html = response.content.decode()
        assert response.status_code == 200
        assert _REFUSAL in html
        return html

    def it_refuses_a_new_class_on_the_instructor_composer(client: Any):
        client.force_login(_rules_instructor().user)

        html = _refused(client.post(reverse("classes:teach_class_create"), _composer_post(description=_KATE)))

        assert "Description: payment outside the ticket: “paid at the session”, “cash”, “venmo”" in html
        assert not ClassOffering.objects.filter(title="Rules Check Class").exists()

    def it_refuses_a_new_class_on_the_admin_composer(client: Any):
        client.force_login(_rules_admin())
        data = _composer_post(description=_LAB_FEES, instructor=str(InstructorFactory().pk))

        _refused(client.post(reverse("classes:admin_class_create"), data))

        assert not ClassOffering.objects.filter(title="Rules Check Class").exists()

    def it_refuses_a_draft_edit_on_the_instructor_composer_by_its_posted_faq(client: Any):
        instructor = _rules_instructor()
        offering = ClassOfferingFactory(instructor=instructor)
        client.force_login(instructor.user)
        data = _composer_post(offering, description="Edited.") | _faq_post(("Deals?", "Use code PLHalfOff"))

        _refused(client.post(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}), data))

        offering.refresh_from_db()
        assert (offering.description, offering.eventbrite_enabled, offering.faqs.count()) == (
            "A hands-on class.",
            False,
            0,
        )

    def it_refuses_an_edit_on_the_admin_composer(client: Any):
        offering = _listed()  # on Eventbrite and agreed: the save still runs the check, without the box
        session = offering.sessions.get()
        client.force_login(_rules_admin())
        data = _composer_post(offering, title="Welding, call 503-555-0182", instructor=str(offering.instructor_id)) | {
            "sessions-TOTAL_FORMS": "1",
            "sessions-INITIAL_FORMS": "1",
            "sessions-0-id": str(session.pk),
            "sessions-0-class_offering": str(offering.pk),
            "sessions-0-starts_at": session.starts_at.astimezone().strftime("%Y-%m-%dT%H:%M"),
            "sessions-0-ends_at": session.ends_at.astimezone().strftime("%Y-%m-%dT%H:%M"),
        }

        _refused(client.post(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}), data))

        offering.refresh_from_db()
        assert "503" not in offering.title

    def it_refuses_an_edit_on_the_live_class_page(client: Any):
        instructor = _rules_instructor()
        offering = _listed(instructor=instructor)
        client.force_login(instructor.user)
        data = {"description": _LAB_FEES, **_faq_post()}

        _refused(client.post(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}), data))

        offering.refresh_from_db()
        assert "Lab fees" not in offering.description

    def it_saves_a_clean_live_class_edit(client: Any):
        instructor = _rules_instructor()
        offering = _listed(instructor=instructor)
        client.force_login(instructor.user)

        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            {"description": _INCLUDED, **_faq_post()},
        )

        assert response.status_code == 302
        offering.refresh_from_db()
        assert _INCLUDED in offering.description

    def it_saves_a_failing_live_class_that_is_not_on_eventbrite(client: Any):
        instructor = _rules_instructor()
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PUBLISHED, ready=True)
        client.force_login(instructor.user)

        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            {"description": _LAB_FEES, **_faq_post()},
        )

        assert response.status_code == 302


def describe_a_copied_class():
    """Second review of #741: the agreement is per class, so a copy asks for it again."""

    @pytest.mark.parametrize("copy", ["duplicate", "duplicate_as_new_run"])
    def it_clears_the_agreement_on_the_copy_and_keeps_it_on_the_source(copy: str):
        agreed_by = UserFactory()
        source = ClassOfferingFactory(
            eventbrite_enabled=True, eventbrite_rules_agreed_by=agreed_by, eventbrite_rules_agreed_at=timezone.now()
        )
        source_pk = source.pk

        clone = getattr(ClassOffering.objects.get(pk=source_pk), copy)()
        clone.refresh_from_db()

        assert (clone.eventbrite_rules_agreed_by, clone.eventbrite_rules_agreed_at) == (None, None)
        assert clone.needs_eventbrite_agreement is True
        assert clone.eventbrite_enabled is False
        assert ClassOffering.objects.get(pk=source_pk).eventbrite_rules_agreed_by == agreed_by

    def it_lets_a_copy_of_a_listed_class_publish_its_own_event(eventbrite: FakeEventbrite):
        """Fix round 2 of #741: a kept ``eventbrite_published`` read the copy's new draft event as taken down."""
        source = _listed(eventbrite_published=True)

        copy = ClassOffering.objects.get(pk=source.pk).duplicate()
        copy.refresh_from_db()
        assert (copy.eventbrite_published, copy.eventbrite_event_id) == (False, "")

        start = timezone.now() + timedelta(days=3)
        ClassSessionFactory(class_offering=copy, starts_at=start, ends_at=start + timedelta(hours=1))
        ClassOffering.objects.filter(pk=copy.pk).update(  # Submit to Eventbrite ticked and saved on the copy
            status=ClassOffering.Status.PUBLISHED, eventbrite_enabled=True, eventbrite_rules_agreed_at=timezone.now()
        )
        copy.refresh_from_db()
        copy.sync_eventbrite_listing()

        assert "publish" in eventbrite.names()
        assert (copy.eventbrite_sync_state, copy.eventbrite_published) == (State.LISTED, True)
        assert ClassOffering.objects.get(pk=source.pk).eventbrite_published is True


def describe_a_queued_class_going_live():
    """AC8: a class with Submit to Eventbrite ticked is checked again when it goes live."""

    _SUBJECT = "was not listed on Eventbrite"

    def _queued(**kwargs: Any) -> ClassOffering:
        """A ready draft with Submit to Eventbrite ticked: queued, nothing sent yet."""
        instructor = _rules_instructor()
        return _opted_in(instructor=instructor, **kwargs)

    def _rules_emails() -> list[Any]:
        from django.core import mail

        return [message for message in mail.outbox if _SUBJECT in message.subject]

    def it_lists_a_passing_class_when_it_publishes_and_emails_nobody(eventbrite: FakeEventbrite):
        offering = _queued()

        offering.publish(UserFactory())

        assert "publish" in eventbrite.names()
        assert offering.eventbrite_sync_state == State.LISTED
        assert _rules_emails() == []

    def it_sends_nothing_and_emails_the_instructor_the_problems_with_the_class_link(eventbrite: FakeEventbrite):
        offering = _described(_queued(), f"<p>{_KATE}</p>")

        offering.publish(UserFactory())

        assert eventbrite.calls == []
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.FAILED
        assert offering.eventbrite_sync_error.startswith(_REFUSAL)
        [email] = _rules_emails()
        assert email.to == [offering.instructor.primary_email]
        assert email.subject == f'"{offering.title}" {_SUBJECT}'
        assert "- Description: payment outside the ticket: “paid at the session”, “cash”, “venmo”" in email.body
        overview = reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        assert "use Request a Change: http" in email.body
        assert email.body.split("use Request a Change: ", 1)[1].split("\n", 1)[0].endswith(overview)
        assert "Once the change is saved, the class lists on Eventbrite on its own." in email.body

    def it_does_not_email_again_when_a_retry_finds_the_same_failure(eventbrite: FakeEventbrite):
        offering = _described(_queued(), f"<p>{_KATE}</p>")
        offering.publish(UserFactory())

        offering.sync_eventbrite_listing()
        call_command("retry_eventbrite_pushes")

        assert len(_rules_emails()) == 1

    def it_does_not_email_again_after_a_save_that_changes_nothing_on_the_listing(eventbrite: FakeEventbrite):
        offering = _described(_queued(), f"<p>{_KATE}</p>")
        offering.publish(UserFactory())

        offering.mark_eventbrite_edit_saved()  # what a sale modal save does
        assert offering.eventbrite_sync_state == State.PENDING
        call_command("retry_eventbrite_pushes")

        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.FAILED
        assert len(_rules_emails()) == 1

    def it_lists_a_failed_class_on_the_next_tick_once_it_is_fixed(eventbrite: FakeEventbrite):
        offering = _described(_queued(), f"<p>{_KATE}</p>")
        offering.publish(UserFactory())
        _described(offering, f"<p>{_INCLUDED}</p>")

        offering.mark_eventbrite_edit_saved()  # the fix saved through the class editor
        call_command("retry_eventbrite_pushes")

        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.LISTED
        assert "publish" in eventbrite.names()
        assert len(_rules_emails()) == 1

    def it_emails_again_for_a_different_failure(eventbrite: FakeEventbrite):
        offering = _described(_queued(), f"<p>{_KATE}</p>")
        offering.publish(UserFactory())
        _described(offering, f"<p>{_LAB_FEES}</p>")

        offering.sync_eventbrite_listing()

        assert len(_rules_emails()) == 2
        assert "Lab fees apply" in _rules_emails()[1].body

    def it_emails_nobody_when_the_instructor_has_no_email(eventbrite: FakeEventbrite):
        offering = _described(_queued(), f"<p>{_KATE}</p>")
        ClassOffering.objects.filter(pk=offering.pk).update(instructor=None, status=ClassOffering.Status.PUBLISHED)
        offering.refresh_from_db()

        offering.sync_eventbrite_listing()

        assert offering.eventbrite_sync_state == State.FAILED
        assert _rules_emails() == []
