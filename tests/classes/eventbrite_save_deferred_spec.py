"""BDD specs for #713: an edit save leaves Eventbrite to the retry tick, and admins can sync on demand.

Every Eventbrite call goes to the :class:`FakeEventbrite` the listing specs use, so an empty
``calls`` list is the proof a save made no Eventbrite call.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from core.integrations.eventbrite import EventbriteClient, EventbriteError, EventbriteSync
from membership.models import Member
from tests.classes.eventbrite_listing_spec import FakeEventbrite, _listed
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

State = ClassOffering.EventbriteSyncState
WAITING = "Waiting to sync: your changes are saved and go to Eventbrite within 15 minutes"


@pytest.fixture
def eventbrite() -> Iterator[FakeEventbrite]:
    fake = FakeEventbrite()
    with patch.object(EventbriteClient, "from_settings", return_value=fake):
        yield fake


def _admin() -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username="eb-sync-admin", email="eb-sync-admin@example.com")
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _instructor() -> Member:
    return InstructorFactory(user=UserFactory(username="eb-sync-teacher@example.com"), instructor_slug="eb-sync-t")


def _faq_management() -> dict[str, str]:
    return {
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
    }


def _published_edit_payload(**overrides: str) -> dict[str, str]:
    """What templates/classes/teach/class_form_published.html posts, the opt in kept on."""
    payload = {
        "description": "A hands-on class, edited.",
        "eventbrite_enabled": "on",
        "eventbrite_fee_payer": ClassOffering.EventbriteFeePayer.BUYER,
        **_faq_management(),
    }
    payload.update(overrides)
    return payload


def _composer_payload(offering: ClassOffering) -> dict[str, str]:
    """What the admin composer posts for a live class, its one session kept as it is."""
    session = offering.sessions.get()
    return {
        "title": "Welding, Edited",
        "category": str(offering.category_id),
        "instructor": str(offering.instructor_id),
        "description": "Hands-on intro.",
        "price_cents": "50.00",
        "capacity": str(offering.capacity),
        "scheduling_model": ClassOffering.SchedulingModel.FIXED,
        "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
        "eventbrite_enabled": "on",
        "eventbrite_fee_payer": ClassOffering.EventbriteFeePayer.BUYER,
        "sessions-TOTAL_FORMS": "1",
        "sessions-INITIAL_FORMS": "1",
        "sessions-MIN_NUM_FORMS": "0",
        "sessions-MAX_NUM_FORMS": "1000",
        "sessions-0-id": str(session.pk),
        "sessions-0-class_offering": str(offering.pk),
        "sessions-0-starts_at": session.starts_at.astimezone().strftime("%Y-%m-%dT%H:%M"),
        "sessions-0-ends_at": session.ends_at.astimezone().strftime("%Y-%m-%dT%H:%M"),
        **_faq_management(),
    }


def _busy_buttons(html: str) -> list[str]:
    """The labels of the composer bar's buttons that carry ``data-pl-busy``, in page order."""
    bar = html.split('<div class="pl-composer-bar">', 1)[1].split("</form>", 1)[0]
    tags = re.findall(r"<button\b([^>]*)>(.*?)</button>", bar, re.S)
    return [label.strip() for attrs, label in tags if re.search(r"\sdata-pl-busy(\s|$)", attrs)]


def _assert_waiting(offering: ClassOffering, eventbrite: FakeEventbrite) -> None:
    offering.refresh_from_db()
    assert eventbrite.calls == []
    assert offering.eventbrite_sync_state == State.PENDING
    assert offering.eventbrite_sync_label == WAITING


def describe_an_edit_save_on_a_listed_class():
    def it_leaves_the_admin_composer_save_for_the_retry_tick(eventbrite: FakeEventbrite, client: Client):
        offering = _listed()
        client.force_login(_admin())

        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}), _composer_payload(offering)
        )

        assert response.status_code == 302, response.context and response.context["form"].errors
        _assert_waiting(offering, eventbrite)
        assert offering.title == "Welding, Edited"

    def it_leaves_the_published_edit_page_save_for_the_retry_tick(eventbrite: FakeEventbrite, client: Client):
        instructor = _instructor()
        offering = _listed(instructor=instructor)
        client.force_login(instructor.user)

        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}), _published_edit_payload()
        )

        assert response.status_code == 302
        _assert_waiting(offering, eventbrite)
        assert offering.description == "A hands-on class, edited."

    def it_leaves_a_sale_switched_on_for_the_retry_tick(eventbrite: FakeEventbrite, client: Client):
        instructor = _instructor()
        offering = _listed(instructor=instructor)
        client.force_login(instructor.user)
        sale = {"action": "on", "sale_kind": "percent", "sale_percent": "20", "sale_amount_cents": ""}

        response = client.post(reverse("classes:teach_class_sale", kwargs={"pk": offering.pk}), sale)

        assert response.status_code == 302
        _assert_waiting(offering, eventbrite)
        assert offering.sale_enabled

    def it_leaves_a_sale_switched_off_for_the_retry_tick(eventbrite: FakeEventbrite, client: Client):
        instructor = _instructor()
        offering = _listed(instructor=instructor, sale_enabled=True, sale_percent=20)
        client.force_login(instructor.user)

        response = client.post(reverse("classes:teach_class_sale", kwargs={"pk": offering.pk}), {"action": "off"})

        assert response.status_code == 302
        _assert_waiting(offering, eventbrite)
        assert not offering.sale_enabled

    def it_lists_the_saved_edit_on_the_next_retry_tick(eventbrite: FakeEventbrite, client: Client):
        instructor = _instructor()
        offering = _listed(instructor=instructor)
        client.force_login(instructor.user)
        client.post(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}), _published_edit_payload())

        call_command("retry_eventbrite_pushes")

        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.LISTED
        assert offering.eventbrite_sync_label == "Listed on Eventbrite"
        assert "update_event" in eventbrite.names()
        assert "publish" not in eventbrite.names()

    def it_ends_the_listing_in_the_request_when_the_class_is_taken_off_eventbrite(
        eventbrite: FakeEventbrite, client: Client
    ):
        instructor = _instructor()
        offering = _listed(instructor=instructor)
        client.force_login(instructor.user)

        client.post(reverse("classes:teach_class_eventbrite_off", kwargs={"pk": offering.pk}))

        offering.refresh_from_db()
        assert eventbrite.names() == ["update_ticket_class", "unpublish"]
        assert offering.eventbrite_sync_state == State.ENDED

    def it_trades_a_failure_for_the_waiting_note(eventbrite: FakeEventbrite):
        offering = _listed(eventbrite_sync_state=State.FAILED, eventbrite_sync_error="boom")

        offering.mark_eventbrite_edit_saved()

        _assert_waiting(offering, eventbrite)

    def it_still_publishes_a_draft_event_whose_publish_failed_on_the_retry_tick(eventbrite: FakeEventbrite):
        # #710's status rule survives the deferral: Eventbrite says draft, so the retry publishes it.
        eventbrite.event_status = "draft"
        offering = _listed(eventbrite_sync_state=State.FAILED, eventbrite_sync_error="POST publish: 400")

        offering.mark_eventbrite_edit_saved()
        _assert_waiting(offering, eventbrite)
        call_command("retry_eventbrite_pushes")

        assert eventbrite.names()[-1] == "publish"
        assert eventbrite.args("publish") == ("ev-9",)
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.LISTED
        assert offering.eventbrite_sync_label == "Listed on Eventbrite"


def describe_mark_eventbrite_edit_saved():
    def it_notes_sync_off_while_the_integration_is_off():
        offering = _listed()
        off = MagicMock(spec=EventbriteClient)
        off.enabled = False

        with patch.object(EventbriteClient, "from_settings", return_value=off):
            offering.mark_eventbrite_edit_saved()

        offering.refresh_from_db()
        assert (offering.eventbrite_sync_state, offering.eventbrite_sync_error) == (
            State.PENDING,
            EventbriteSync.SYNC_OFF,
        )

    def it_leaves_an_ended_listing_alone(eventbrite: FakeEventbrite):
        offering = _listed(eventbrite_enabled=False, eventbrite_sync_state=State.ENDED)

        offering.mark_eventbrite_edit_saved()

        offering.refresh_from_db()
        assert eventbrite.calls == []
        assert offering.eventbrite_sync_state == State.ENDED

    def it_leaves_an_opted_in_draft_that_was_never_listed_alone(eventbrite: FakeEventbrite):
        offering = ClassOfferingFactory(ready=True, eventbrite_enabled=True, status=ClassOffering.Status.DRAFT)

        offering.mark_eventbrite_edit_saved()

        offering.refresh_from_db()
        assert eventbrite.calls == []
        assert offering.eventbrite_sync_state == State.IDLE


def describe_a_class_not_opted_in():
    def it_makes_no_query_and_no_call(eventbrite: FakeEventbrite, django_assert_num_queries: Any):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)

        with django_assert_num_queries(0):
            offering.mark_eventbrite_edit_saved()

        assert eventbrite.calls == []

    def it_saves_from_the_published_edit_page_exactly_as_before(eventbrite: FakeEventbrite, client: Client):
        instructor = _instructor()
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PUBLISHED)
        client.force_login(instructor.user)

        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _published_edit_payload(eventbrite_enabled=""),
        )

        assert response.status_code == 302
        offering.refresh_from_db()
        assert eventbrite.calls == []
        assert offering.eventbrite_sync_state == State.IDLE

    def it_shows_no_sync_button_on_the_overview(client: Client):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        client.force_login(_admin())

        html = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()

        assert "data-eventbrite-sync-button" not in html


def describe_the_sync_to_eventbrite_button():
    def _url(offering: ClassOffering) -> str:
        return reverse("classes:teach_class_eventbrite_sync", kwargs={"pk": offering.pk})

    def it_shows_on_the_eventbrite_tab_for_an_admin_and_not_the_overview(eventbrite: FakeEventbrite, client: Client):
        offering = _listed(eventbrite_sync_state=State.PENDING)
        client.force_login(_admin())

        overview = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()
        html = client.get(reverse("classes:teach_class_eventbrite", kwargs={"pk": offering.pk})).content.decode()

        assert "data-eventbrite-sync-button" not in overview

        assert f'action="{_url(offering)}"' in html
        assert "data-eventbrite-sync-button" in html

    def it_is_not_shown_to_the_instructor(client: Client):
        instructor = _instructor()
        offering = _listed(instructor=instructor, eventbrite_sync_state=State.PENDING)
        client.force_login(instructor.user)

        html = client.get(reverse("classes:teach_class_eventbrite", kwargs={"pk": offering.pk})).content.decode()

        assert "data-eventbrite-sync-button" not in html

    def it_syncs_now_and_the_row_reads_listed_with_a_check(eventbrite: FakeEventbrite, client: Client):
        offering = _listed(eventbrite_sync_state=State.PENDING, eventbrite_sync_error=EventbriteSync.EDIT_SAVED)
        client.force_login(_admin())

        response = client.post(_url(offering), follow=True)

        offering.refresh_from_db()
        assert response.redirect_chain[0] == (
            reverse("classes:teach_class_eventbrite", kwargs={"pk": offering.pk}),
            302,
        )
        assert offering.eventbrite_sync_state == State.LISTED
        assert "update_event" in eventbrite.names()
        html = response.content.decode()
        assert '<span aria-hidden="true">&#10003;</span> Listed on Eventbrite</span>' in html

    def it_shows_the_failure_reason_when_eventbrite_refuses(eventbrite: FakeEventbrite, client: Client):
        eventbrite.fail["update_event"] = EventbriteError("Qzx refused", 500)
        offering = _listed(eventbrite_sync_state=State.PENDING)
        client.force_login(_admin())

        html = client.post(_url(offering), follow=True).content.decode()

        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.FAILED
        assert "Failed: Qzx refused" in html
        assert "pl-eventbrite-sync__ok" not in html

    def it_is_a_404_for_the_instructor(eventbrite: FakeEventbrite, client: Client):
        instructor = _instructor()
        offering = _listed(instructor=instructor, eventbrite_sync_state=State.PENDING)
        client.force_login(instructor.user)

        assert client.post(_url(offering)).status_code == 404
        assert eventbrite.calls == []

    def it_is_a_404_on_a_class_never_on_eventbrite(eventbrite: FakeEventbrite, client: Client):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        client.force_login(_admin())

        assert client.post(_url(offering)).status_code == 404
        assert eventbrite.calls == []

    def it_refuses_a_get(eventbrite: FakeEventbrite, client: Client):
        offering = _listed(eventbrite_sync_state=State.PENDING)
        client.force_login(_admin())

        assert client.get(_url(offering)).status_code == 405
        assert eventbrite.calls == []


def describe_the_busy_save_buttons():
    def it_marks_the_published_edit_save_busy_on_submit(client: Client):
        instructor = _instructor()
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PUBLISHED)
        client.force_login(instructor.user)

        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()

        assert '<form method="post" data-pl-busy-submit>' in html
        assert 'type="submit" data-pl-busy>Save</button>' in html
        assert "window.plBusySubmitBound" in html

    def it_marks_the_admin_composer_save_and_publish_busy(client: Client):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        client.force_login(_admin())

        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()

        assert 'id="composer-form" x-ref="composerForm" novalidate data-pl-busy-submit>' in html
        assert _busy_buttons(html) == ["Save Draft", "Publish"]
        assert "window.plBusySubmitBound" in html

    def it_marks_the_instructor_composer_save_and_submit_busy(client: Client):
        instructor = _instructor()
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.DRAFT)
        client.force_login(instructor.user)

        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()

        assert _busy_buttons(html) == ["Save Draft", "Submit for Review"]
