"""BDD specs for the Eventbrite line on the class review screen (#652): admins see the sync state."""

from __future__ import annotations

from typing import Any

import pytest
from django.urls import reverse

from classes.factories import ClassOfferingFactory
from classes.models import ClassApproval, ClassOffering

pytestmark = pytest.mark.django_db

State = ClassOffering.EventbriteSyncState


def _offering(**kwargs: Any) -> ClassOffering:
    defaults: dict[str, Any] = {"ready": True, "status": ClassOffering.Status.PENDING, "eventbrite_enabled": True}
    return ClassOfferingFactory(**{**defaults, **kwargs})


def _admin_page(client: Any, admin_user: Any, offering: ClassOffering) -> str:
    client.force_login(admin_user)
    return client.get(reverse("classes:admin_class_review", kwargs={"pk": offering.pk})).content.decode()


def describe_the_eventbrite_line_for_admins():
    def it_says_listed_and_links_the_event(client: Any, admin_user: Any):
        offering = _offering(eventbrite_event_id="123", eventbrite_sync_state=State.LISTED)

        html = _admin_page(client, admin_user, offering)

        assert "<span data-review-eventbrite-sync>Listed on Eventbrite</span>" in html
        assert 'href="https://www.eventbrite.com/e/123"' in html

    def it_shows_a_failure_with_its_reason(client: Any, admin_user: Any):
        offering = _offering(
            eventbrite_event_id="123", eventbrite_sync_state=State.FAILED, eventbrite_sync_error="500 down"
        )

        html = _admin_page(client, admin_user, offering)

        assert "<span data-review-eventbrite-sync>Failed: 500 down</span>" in html
        assert "www.eventbrite.com/e/" not in html

    def it_shows_waiting_while_sync_is_off(client: Any, admin_user: Any):
        offering = _offering(eventbrite_sync_state=State.PENDING, eventbrite_sync_error="Eventbrite sync is off.")

        html = _admin_page(client, admin_user, offering)

        assert "<span data-review-eventbrite-sync>Waiting to sync: Eventbrite sync is off.</span>" in html

    def it_says_nothing_about_sync_before_any_push(client: Any, admin_user: Any):
        html = _admin_page(client, admin_user, _offering())

        assert "data-review-eventbrite-sync" not in html
        assert "Also sold on Eventbrite." in html


def describe_the_eventbrite_line_for_a_guild_lead():
    def it_keeps_the_sync_state_off_the_tokenized_page(client: Any):
        offering = _offering(eventbrite_event_id="123", eventbrite_sync_state=State.FAILED, eventbrite_sync_error="x")
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)

        html = client.get(reverse("classes:class_review", kwargs={"token": row.token})).content.decode()

        assert "Also sold on Eventbrite." in html
        assert "data-review-eventbrite-sync" not in html


def describe_eventbrite_sync_label():
    @pytest.mark.parametrize(
        ("state", "error", "label"),
        [
            (State.IDLE, "", "Not on Eventbrite yet"),
            (State.PENDING, "", "Waiting to sync"),
            (State.ENDED, "", "Ended on Eventbrite"),
            (State.ENDED, "Sales are closed.", "Ended on Eventbrite. Sales are closed."),
        ],
    )
    def it_names_each_state(state: str, error: str, label: str):
        offering = ClassOffering(eventbrite_sync_state=state, eventbrite_sync_error=error)

        assert offering.eventbrite_sync_label == label
