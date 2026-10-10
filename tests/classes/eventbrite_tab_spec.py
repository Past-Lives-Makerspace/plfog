"""BDD specs for the Eventbrite tab on Manage this class (#725 part 2).

Every Eventbrite call goes to the :class:`FakeEventbrite` the listing specs use, so an empty
``calls`` list is the proof nothing reached Eventbrite.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from classes.factories import ClassOfferingFactory, InstructorFactory, RegistrationFactory, UserFactory
from classes.models import ClassOffering
from core.integrations.eventbrite import EventbriteClient, EventbriteSync
from membership.models import AdminCapability, Member
from tests.classes.eventbrite_listing_spec import (
    _INCLUDED,
    _KATE,
    _REFUSAL,
    FakeEventbrite,
    _described,
    _listed,
    _opted_in,
)
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def eventbrite() -> Iterator[FakeEventbrite]:
    fake = FakeEventbrite()
    with patch.object(EventbriteClient, "from_settings", return_value=fake):
        yield fake


State = ClassOffering.EventbriteSyncState
Stage = ClassOffering.EventbriteStage
_ARTS, _JEWELRY, _HOBBIES, _DIY = "105", "5014", "119", "19003"


def _admin() -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username="eb-tab-admin", email="eb-tab-admin@example.com")
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _instructor() -> Member:
    MembershipPlanFactory()
    return InstructorFactory(user=UserFactory(username="eb-tab-teacher@example.com"), instructor_slug="eb-tab-t")


def _live(**kwargs: Any) -> ClassOffering:
    """A live fixed class with nothing on Eventbrite yet."""
    return ClassOfferingFactory(**{"ready": True, "status": ClassOffering.Status.PUBLISHED, **kwargs})


def _url(name: str, offering: ClassOffering) -> str:
    return reverse(f"classes:teach_class_eventbrite{name}", kwargs={"pk": offering.pk})


def _tab(client: Client, offering: ClassOffering) -> str:
    response = client.get(_url("", offering))
    assert response.status_code == 200
    return response.content.decode()


def _taken_down(**kwargs: Any) -> ClassOffering:
    return _listed(
        **{
            "eventbrite_published": True,
            "eventbrite_sync_state": State.ENDED,
            "eventbrite_sync_error": EventbriteSync.TAKEN_DOWN,
            **kwargs,
        }
    )


def describe_the_stage():
    @pytest.mark.parametrize(
        ("fields", "stage"),
        [
            ({}, Stage.OFF),
            ({"eventbrite_enabled": True, "eventbrite_rules_agreed_at": None}, Stage.NEEDS_AGREEMENT),
            ({"eventbrite_enabled": True}, Stage.QUEUED),
            ({"eventbrite_enabled": True, "eventbrite_sync_state": State.PENDING}, Stage.QUEUED),
            ({"eventbrite_enabled": True, "eventbrite_sync_state": State.LISTED}, Stage.QUEUED),
            (
                {"eventbrite_enabled": True, "eventbrite_sync_state": State.LISTED, "eventbrite_published": True},
                Stage.LISTED,
            ),
            ({"eventbrite_enabled": True, "eventbrite_sync_state": State.FAILED}, Stage.FAILED),
            (
                {"eventbrite_sync_state": State.ENDED, "eventbrite_sync_error": EventbriteSync.TAKEN_DOWN},
                Stage.TAKEN_DOWN,
            ),
            (
                {
                    "eventbrite_enabled": True,
                    "eventbrite_rules_agreed_at": None,
                    "eventbrite_sync_state": State.ENDED,
                    "eventbrite_sync_error": EventbriteSync.TAKEN_DOWN,
                },
                Stage.TAKEN_DOWN,
            ),
            ({"eventbrite_sync_state": State.ENDED, "eventbrite_sync_error": EventbriteSync.STILL_UP}, Stage.OFF),
        ],
    )
    def it_reads_where_the_class_stands(fields: dict[str, Any], stage: ClassOffering.EventbriteStage):
        assert ClassOfferingFactory(**fields).eventbrite_stage == stage

    def it_knows_an_eventbrite_order_from_plfogs_own_records():
        offering = _live()
        RegistrationFactory(class_offering=offering)

        assert offering.holds_eventbrite_orders is False
        RegistrationFactory(class_offering=offering, eventbrite_order_id="ord-1")
        assert offering.holds_eventbrite_orders is True


def describe_who_sees_the_tab():
    def it_shows_the_tab_to_an_admin_and_the_instructor_on_a_live_fixed_class(client: Client):
        instructor = _instructor()
        offering = _live(instructor=instructor)
        tab = f'href="{_url("", offering)}"'

        client.force_login(instructor.user)
        assert tab in client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()
        client.force_login(_admin())
        assert tab in client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()

    @pytest.mark.parametrize(
        "fields",
        [
            {"status": ClassOffering.Status.DRAFT},
            {"status": ClassOffering.Status.PENDING},
            {"scheduling_model": ClassOffering.SchedulingModel.FLEXIBLE},
        ],
    )
    def it_has_no_tab_on_a_class_that_is_not_live_or_is_flexible(client: Client, fields: dict[str, Any]):
        offering = _live(**fields)
        client.force_login(_admin())

        overview = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()

        assert f'href="{_url("", offering)}"' not in overview
        assert client.get(_url("", offering)).status_code == 404

    def it_has_no_tab_for_a_reviewer_who_cannot_edit(client: Client):
        offering = _live(instructor=_instructor())
        reviewer = UserFactory(username="eb-tab-reviewer@example.com")
        AdminCapability.objects.create(member=reviewer.member, capability=AdminCapability.Capability.CLASS_APPROVER)
        client.force_login(reviewer)

        overview = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()

        assert ">Overview<" in overview
        assert f'href="{_url("", offering)}"' not in overview
        for name in ("", "_submit", "_settings", "_off"):
            assert client.post(_url(name, offering)).status_code == 404

    def it_takes_posts_only_on_the_actions(client: Client):
        offering = _live()
        client.force_login(_admin())

        for name in ("_submit", "_settings", "_off"):
            assert client.get(_url(name, offering)).status_code == 405


def describe_the_tab_page():
    @pytest.fixture
    def admin(client: Client) -> Client:
        client.force_login(_admin())
        return client

    def it_offers_validate_and_a_hidden_submit_on_a_class_not_on_eventbrite(admin: Client):
        offering = _live()

        html = _tab(admin, offering)

        assert 'data-eventbrite-stage="off"' in html
        assert "pl-lifecycle-badge--completed" in html
        assert "data-eventbrite-rules>Eventbrite takes down listings" in html
        assert f'hx-post="{reverse("classes:teach_class_eventbrite_check", kwargs={"pk": offering.pk})}"' in html
        assert "Validate Listing for Eventbrite</button>" in html
        assert 'data-eventbrite-submit x-show="ebPassed" x-cloak>Submit Listing to Eventbrite</button>' in html
        assert "ebPassed: false" in html
        assert "data-eventbrite-save" not in html
        assert "data-eventbrite-off" not in html
        assert "data-eventbrite-published" not in html
        assert "On a $50 ticket that is about" in html

    def it_shows_a_queued_class_with_save_and_off(admin: Client):
        offering = _live(eventbrite_enabled=True)

        html = _tab(admin, offering)

        assert 'data-eventbrite-stage="queued"' in html
        assert "pl-lifecycle-badge--awaiting_admin" in html
        assert "data-eventbrite-save" in html
        assert "data-eventbrite-off" in html

    def it_shows_a_listed_class_with_the_published_switch_and_the_no_orders_modal(admin: Client):
        offering = _listed(eventbrite_published=True)

        html = _tab(admin, offering)

        assert 'data-eventbrite-stage="listed"' in html
        assert '<span aria-hidden="true">&#10003;</span> Listed on Eventbrite</span>' in html
        assert "data-eventbrite-published" in html
        assert "$dispatch(&#x27;open-confirm&#x27;, &#x27;eventbrite-unpublish&#x27;)" in html
        assert "Unpublish from Eventbrite?" in html
        assert "The event goes back to a draft on Eventbrite and nobody can buy a ticket there." in html
        assert f'action="{_url("_off", offering)}"' in html
        assert "data-eventbrite-validate" not in html
        assert "data-eventbrite-submit" not in html
        assert "data-eventbrite-save" in html

    def it_offers_the_close_sales_copy_once_eventbrite_holds_orders(admin: Client):
        offering = _listed(eventbrite_published=True)
        RegistrationFactory(class_offering=offering, eventbrite_order_id="ord-1")

        html = _tab(admin, offering)

        assert "Close sales on Eventbrite?" in html
        assert "Eventbrite keeps the page up while it holds orders, but nobody can buy a ticket there." in html
        assert '<h2 class="pl-modal__title">Unpublish from Eventbrite?</h2>' not in html

    def it_shows_a_failure_with_its_reason(admin: Client):
        offering = _listed(eventbrite_sync_state=State.FAILED, eventbrite_sync_error=f"{_REFUSAL} Title: x")

        html = _tab(admin, offering)

        assert 'data-eventbrite-stage="failed"' in html
        assert "pl-lifecycle-badge--cancelled" in html
        assert f"data-eventbrite-sync>Failed: {_REFUSAL} Title: x</p>" in html
        assert "Validate Listing for Eventbrite" in html

    def it_shows_a_taken_down_event_with_no_switch_no_validate_and_no_submit(admin: Client):
        offering = _taken_down()

        html = _tab(admin, offering)

        assert 'data-eventbrite-stage="taken_down"' in html
        assert "data-eventbrite-taken-down>" in html
        assert EventbriteSync.TAKEN_DOWN in html
        assert "data-eventbrite-published" not in html
        assert "data-eventbrite-validate" not in html
        assert "data-eventbrite-settings" not in html
        assert "data-eventbrite-sync-button" not in html
        assert "data-eventbrite-off" in html

    def it_shows_a_class_on_without_an_agreement_as_needing_one_with_off(admin: Client):
        offering = _live(eventbrite_enabled=True, eventbrite_rules_agreed_at=None)

        html = _tab(admin, offering)

        assert 'data-eventbrite-stage="needs_agreement"' in html
        assert "data-eventbrite-awaiting>" in html
        assert "Validate Listing for Eventbrite" in html
        assert "data-eventbrite-off" in html


def describe_submit():
    @pytest.fixture
    def instructor_client(client: Client) -> tuple[Client, Member]:
        instructor = _instructor()
        client.force_login(instructor.user)
        return client, instructor

    def it_agrees_switches_on_saves_the_category_and_lists_in_the_request(
        eventbrite: FakeEventbrite, instructor_client: tuple[Client, Member]
    ):
        client, instructor = instructor_client
        offering = _live(instructor=instructor)
        data = {
            "eventbrite_fee_payer": ClassOffering.EventbriteFeePayer.INCLUDED,
            "eventbrite_category": _HOBBIES,
            "eventbrite_subcategory": _DIY,
        }

        response = client.post(_url("_submit", offering), data)

        assert response.status_code == 302
        assert response["Location"] == _url("", offering)
        offering.refresh_from_db()
        assert offering.eventbrite_enabled is True
        assert offering.eventbrite_rules_agreed_by == instructor.user
        assert offering.eventbrite_rules_agreed_at is not None
        assert (offering.eventbrite_category, offering.eventbrite_subcategory) == (_HOBBIES, _DIY)
        assert offering.eventbrite_fee_payer == ClassOffering.EventbriteFeePayer.INCLUDED
        assert "publish" in eventbrite.names()
        assert offering.eventbrite_sync_state == State.LISTED

    def it_records_the_agreement_for_a_class_on_without_one(
        eventbrite: FakeEventbrite, instructor_client: tuple[Client, Member]
    ):
        client, instructor = instructor_client
        offering = _live(instructor=instructor, eventbrite_enabled=True, eventbrite_rules_agreed_at=None)

        client.post(_url("_submit", offering), {})

        offering.refresh_from_db()
        assert offering.eventbrite_rules_agreed_by == instructor.user
        assert offering.eventbrite_stage == Stage.LISTED

    def it_keeps_the_first_agreement_on_a_second_submit(
        eventbrite: FakeEventbrite, instructor_client: tuple[Client, Member]
    ):
        client, instructor = instructor_client
        first = UserFactory()
        offering = _live(instructor=instructor, eventbrite_enabled=True, eventbrite_rules_agreed_by=first)

        client.post(_url("_submit", offering), {})

        offering.refresh_from_db()
        assert offering.eventbrite_rules_agreed_by == first
        assert offering.eventbrite_stage == Stage.LISTED

    def it_refuses_a_failing_class_and_changes_nothing(
        eventbrite: FakeEventbrite, instructor_client: tuple[Client, Member]
    ):
        client, instructor = instructor_client
        offering = _described(_live(instructor=instructor), f"<p>{_KATE}</p>")

        response = client.post(_url("_submit", offering), {"eventbrite_category": _ARTS})

        assert response.status_code == 200
        html = response.content.decode()
        assert "data-eventbrite-refusal" in html
        assert "Description: payment outside the ticket: “paid at the session”, “cash”, “venmo”" in html
        assert eventbrite.calls == []
        offering.refresh_from_db()
        assert (offering.eventbrite_enabled, offering.eventbrite_rules_agreed_at, offering.eventbrite_category) == (
            False,
            None,
            "",
        )

    def it_refuses_a_mismatched_category_pair(eventbrite: FakeEventbrite, instructor_client: tuple[Client, Member]):
        client, instructor = instructor_client
        offering = _live(instructor=instructor)

        response = client.post(
            _url("_submit", offering), {"eventbrite_category": _ARTS, "eventbrite_subcategory": _DIY}
        )

        assert response.status_code == 200
        assert "Pick a subcategory from the chosen Eventbrite category." in response.content.decode()
        assert eventbrite.calls == []

    def it_never_submits_a_taken_down_event_again(eventbrite: FakeEventbrite, instructor_client: tuple[Client, Member]):
        client, instructor = instructor_client
        offering = _taken_down(instructor=instructor)

        response = client.post(_url("_submit", offering), {}, follow=True)

        assert "Eventbrite took this listing down, so plfog does not submit it again." in response.content.decode()
        assert eventbrite.calls == []
        offering.refresh_from_db()
        assert offering.eventbrite_sync_state == State.ENDED

    def it_passes_with_the_ready_badge_shown(eventbrite: FakeEventbrite, instructor_client: tuple[Client, Member]):
        client, instructor = instructor_client
        offering = _described(_live(instructor=instructor), f"<p>{_INCLUDED}</p>")

        response = client.post(_url("_submit", offering), {}, follow=True)

        assert "Submitted to Eventbrite: Listed on Eventbrite." in response.content.decode()


def describe_save_settings():
    def it_saves_the_fee_and_category_and_queues_the_push(eventbrite: FakeEventbrite, client: Client):
        offering = _listed(eventbrite_published=True)
        client.force_login(_admin())

        response = client.post(
            _url("_settings", offering),
            {"eventbrite_fee_payer": ClassOffering.EventbriteFeePayer.INCLUDED, "eventbrite_category": _ARTS},
        )

        assert response.status_code == 302
        offering.refresh_from_db()
        assert (offering.eventbrite_fee_payer, offering.eventbrite_category) == (
            ClassOffering.EventbriteFeePayer.INCLUDED,
            _ARTS,
        )
        assert offering.eventbrite_sync_state == State.PENDING
        assert eventbrite.calls == []

    def it_refuses_a_mismatched_pair(eventbrite: FakeEventbrite, client: Client):
        offering = _listed(eventbrite_published=True)
        client.force_login(_admin())

        response = client.post(
            _url("_settings", offering), {"eventbrite_category": _ARTS, "eventbrite_subcategory": _DIY}
        )

        assert response.status_code == 200
        offering.refresh_from_db()
        assert offering.eventbrite_category == ""


def describe_taking_a_class_off_eventbrite():
    def it_unpublishes_a_listed_class_through_the_end_path(eventbrite: FakeEventbrite, client: Client):
        offering = _listed(eventbrite_published=True)
        client.force_login(_admin())

        response = client.post(_url("_off", offering))

        assert response.status_code == 302
        assert eventbrite.names() == ["update_ticket_class", "get_event", "unpublish"]
        offering.refresh_from_db()
        assert (offering.eventbrite_enabled, offering.eventbrite_published) == (False, False)
        assert offering.eventbrite_sync_state == State.ENDED
        assert offering.eventbrite_stage == Stage.OFF

    def it_closes_sales_and_keeps_the_page_up_when_eventbrite_holds_orders(eventbrite: FakeEventbrite, client: Client):
        from core.integrations.eventbrite import EventbriteError

        eventbrite.fail["unpublish"] = EventbriteError("has orders", 400)
        offering = _listed(eventbrite_published=True)
        RegistrationFactory(class_offering=offering, eventbrite_order_id="ord-1")
        client.force_login(_admin())

        client.post(_url("_off", offering))

        offering.refresh_from_db()
        assert offering.eventbrite_enabled is False
        assert offering.eventbrite_sync_error == EventbriteSync.STILL_UP

    def it_unqueues_a_class_with_nothing_on_eventbrite_without_a_call(eventbrite: FakeEventbrite, client: Client):
        offering = _live(eventbrite_enabled=True)
        client.force_login(_admin())

        client.post(_url("_off", offering))

        offering.refresh_from_db()
        assert eventbrite.calls == []
        assert offering.eventbrite_enabled is False
        assert offering.eventbrite_rules_agreed_at is not None  # the agreement is kept

    def it_leaves_a_taken_down_event_alone(eventbrite: FakeEventbrite, client: Client):
        offering = _taken_down(eventbrite_rules_agreed_at=None)
        client.force_login(_admin())

        client.post(_url("_off", offering))

        offering.refresh_from_db()
        assert eventbrite.calls == []
        assert (offering.eventbrite_enabled, offering.eventbrite_sync_state) == (False, State.ENDED)
        assert offering.eventbrite_stage == Stage.TAKEN_DOWN

    def it_is_a_404_on_a_class_that_is_not_live(eventbrite: FakeEventbrite, client: Client):
        offering = _opted_in()
        client.force_login(_admin())

        assert client.post(_url("_off", offering)).status_code == 404
        offering.refresh_from_db()
        assert offering.eventbrite_enabled is True
