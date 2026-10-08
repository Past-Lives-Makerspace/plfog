"""BDD specs for a live class's Eventbrite listing on the admin screens (#652).

The Overview shows admins the sync state; deleting a listed class ends the listing first. Eventbrite
is a ``MagicMock`` client throughout; no spec reaches it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from core.integrations.eventbrite import EventbriteClient, EventbriteError, EventbriteSync

pytestmark = pytest.mark.django_db

State = ClassOffering.EventbriteSyncState


@pytest.fixture
def eventbrite() -> Iterator[MagicMock]:
    client = MagicMock(spec=EventbriteClient)
    client.enabled = True
    with patch.object(EventbriteClient, "from_settings", return_value=client):
        yield client


def _live(**kwargs: Any) -> ClassOffering:
    defaults: dict[str, Any] = {
        "ready": True,
        "status": ClassOffering.Status.PUBLISHED,
        "eventbrite_enabled": True,
        "eventbrite_event_id": "123",
        "eventbrite_ticket_class_id": "456",
        "eventbrite_sync_state": State.LISTED,
    }
    return ClassOfferingFactory(**{**defaults, **kwargs})


def _overview(client: Any, user: Any, offering: ClassOffering) -> str:
    client.force_login(user)
    return client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()


def describe_the_overview_eventbrite_row():
    def it_shows_admins_a_listed_class_with_a_link(client: Any, admin_user: Any):
        html = _overview(client, admin_user, _live())

        assert (
            '<span data-eventbrite-sync><span class="pl-eventbrite-sync__ok" aria-hidden="true">&#10003;</span> Listed on Eventbrite</span>'
            in html
        )
        assert 'href="https://www.eventbrite.com/e/123"' in html

    def it_shows_admins_a_failure_with_its_reason(client: Any, admin_user: Any):
        offering = _live(eventbrite_sync_state=State.FAILED, eventbrite_sync_error="503 busy")

        html = _overview(client, admin_user, offering)

        assert "<span data-eventbrite-sync>Failed: 503 busy</span>" in html
        assert "www.eventbrite.com/e/" not in html

    def it_has_no_row_for_a_class_never_pushed(client: Any, admin_user: Any):
        offering = _live(eventbrite_event_id="", eventbrite_sync_state=State.IDLE)

        assert "data-overview-eventbrite" not in _overview(client, admin_user, offering)

    def it_keeps_the_row_from_the_instructor(client: Any):
        instructor = InstructorFactory(user=UserFactory(username="eb-teacher@example.com"))
        offering = _live(instructor=instructor)

        html = _overview(client, instructor.user, offering)

        assert ">Capacity</td>" in html  # the Overview rendered; the row is withheld, not the page
        assert "data-overview-eventbrite" not in html


def describe_deleting_a_listed_class():
    def _delete(client: Any, admin_user: Any, offering: ClassOffering) -> Any:
        client.force_login(admin_user)
        return client.post(reverse("classes:admin_class_delete", kwargs={"pk": offering.pk}), follow=True)

    def it_ends_the_listing_and_then_deletes(client: Any, admin_user: Any, eventbrite: MagicMock):
        offering = _live()

        _delete(client, admin_user, offering)

        eventbrite.update_ticket_class.assert_called_once()
        eventbrite.unpublish.assert_called_once_with("123")
        assert not ClassOffering.objects.filter(pk=offering.pk).exists()

    def it_deletes_when_eventbrite_keeps_the_page_up_with_sales_closed(
        client: Any, admin_user: Any, eventbrite: MagicMock
    ):
        eventbrite.unpublish.side_effect = EventbriteError("has orders", 400)
        offering = _live()

        _delete(client, admin_user, offering)

        assert not ClassOffering.objects.filter(pk=offering.pk).exists()

    def it_refuses_when_the_listing_could_not_be_ended(client: Any, admin_user: Any, eventbrite: MagicMock):
        eventbrite.unpublish.side_effect = EventbriteError("POST unpublish: 503 busy", 503)
        offering = _live()

        response = _delete(client, admin_user, offering)

        assert "the Eventbrite listing could not be ended (POST unpublish: 503 busy)" in response.content.decode()
        offering.refresh_from_db()
        assert offering.eventbrite_enabled is True
        assert offering.eventbrite_sync_state == State.FAILED

    def it_refuses_while_eventbrite_sync_is_off(client: Any, admin_user: Any):
        offering = _live()

        response = _delete(client, admin_user, offering)

        assert EventbriteSync.SYNC_OFF in response.content.decode()
        assert ClassOffering.objects.filter(pk=offering.pk).exists()

    def it_deletes_a_class_never_listed_without_calling_eventbrite(client: Any, admin_user: Any, eventbrite: MagicMock):
        offering = _live(eventbrite_event_id="", eventbrite_sync_state=State.IDLE)

        _delete(client, admin_user, offering)

        assert eventbrite.mock_calls == []
        assert not ClassOffering.objects.filter(pk=offering.pk).exists()

    def it_deletes_an_ended_listing_without_calling_eventbrite_again(
        client: Any, admin_user: Any, eventbrite: MagicMock
    ):
        offering = _live(eventbrite_enabled=False, eventbrite_sync_state=State.ENDED)

        _delete(client, admin_user, offering)

        eventbrite.unpublish.assert_not_called()
        assert not ClassOffering.objects.filter(pk=offering.pk).exists()
