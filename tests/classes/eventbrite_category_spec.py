"""BDD specs for the Eventbrite category and subcategory a class is listed under (#716).

The lists come from Eventbrite and live in code (:mod:`classes.eventbrite_categories`); the
browser narrows the subcategory to the chosen category, and the forms refuse any other pair.
What reaches Eventbrite is asserted in ``tests/core/integrations/eventbrite_spec.py``.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from classes.composer import step_for_field
from classes.eventbrite_categories import (
    SUBCATEGORY_PARENT,
    EventbriteCategory,
    EventbriteSubcategory,
    subcategory_fits,
)
from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.forms import ClassOfferingForm, TeachClassOfferingForm, TeachPublishedClassForm
from classes.models import ClassOffering
from core.models import SiteConfiguration
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

ARTS = EventbriteCategory.PERFORMING_VISUAL_ARTS
HOBBIES = EventbriteCategory.HOBBIES_SPECIAL_INTEREST
JEWELRY = EventbriteSubcategory.JEWELRY_5014
DIY = EventbriteSubcategory.DIY_19003
MISMATCH = "Pick a subcategory from the chosen Eventbrite category."


def _switch_integration_on(settings: Any) -> None:
    """The site toggle plus every credential: what ``EventbriteClient.enabled`` needs."""
    settings.EVENTBRITE_PRIVATE_TOKEN = "token"
    settings.EVENTBRITE_ORGANIZATION_ID = "org"
    settings.EVENTBRITE_VENUE_ID = "venue"
    settings.EVENTBRITE_ORGANIZER_ID = "organizer-1"
    config = SiteConfiguration.load()
    config.eventbrite_sync_enabled = True
    config.save(update_fields=["eventbrite_sync_enabled"])


def _composer_data(offering: ClassOffering, **overrides: Any) -> dict[str, Any]:
    data = {
        "title": offering.title,
        "category": offering.category_id,
        "instructor": offering.instructor_id,
        "description": offering.description,
        "price_cents": "50.00",
        "capacity": 6,
        "scheduling_model": ClassOffering.SchedulingModel.FIXED,
        "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
        "eventbrite_enabled": "on",
        "eventbrite_rules_agreed": "on",
    }
    return {**data, **overrides}


def _published_data(**overrides: Any) -> dict[str, Any]:
    return {"description": "A hands-on class.", **overrides}


def _instructor_client(client: Client) -> Any:
    MembershipPlanFactory()
    instructor = InstructorFactory(user=UserFactory(username="eb-cat@example.com", email="eb-cat@example.com"))
    client.force_login(instructor.user)
    return instructor


def _select(html: str, name: str) -> str:
    match = re.search(rf'<select name="{name}".*?</select>', html, re.S)
    assert match, f"no <select name={name!r}>"
    return match.group()


def describe_the_lists():
    def it_matches_the_examples_eventbrite_returned():
        assert (ARTS, HOBBIES) == ("105", "119")
        assert {EventbriteSubcategory(i).label for i in ("5007", "5008", "5011", "5012", "5013", "5014")} == {
            "Craft",
            "Fine Art",
            "Sculpture",
            "Painting",
            "Design",
            "Jewelry",
        }
        assert {SUBCATEGORY_PARENT[i] for i in ("5007", "5014")} == {ARTS}
        assert {SUBCATEGORY_PARENT[i] for i in ("19003", "19004", "19005", "19008")} == {HOBBIES}

    def it_gives_every_subcategory_a_parent_from_the_category_list():
        assert set(SUBCATEGORY_PARENT) == set(EventbriteSubcategory.values)
        assert set(SUBCATEGORY_PARENT.values()) <= set(EventbriteCategory.values)
        assert (len(EventbriteCategory.values), len(EventbriteSubcategory.values)) == (21, 216)

    @pytest.mark.parametrize(
        ("category", "subcategory", "fits"),
        [("", "", True), (ARTS, "", True), (ARTS, JEWELRY, True), (HOBBIES, JEWELRY, False), ("", DIY, False)],
    )
    def it_fits_only_a_child_of_the_category(category: str, subcategory: str, fits: bool):
        assert subcategory_fits(category, subcategory) is fits

    def it_starts_a_class_with_neither():
        offering = ClassOfferingFactory()

        assert (offering.eventbrite_category, offering.eventbrite_subcategory) == ("", "")


def describe_the_composer_forms():
    @pytest.fixture(autouse=True)
    def _integration_on(settings: Any) -> None:
        _switch_integration_on(settings)

    @pytest.mark.parametrize("form_class", [TeachClassOfferingForm, ClassOfferingForm])
    def it_saves_a_category_and_its_subcategory(form_class: Any):
        offering = ClassOfferingFactory()
        form = form_class(
            _composer_data(offering, eventbrite_category=ARTS, eventbrite_subcategory=JEWELRY), instance=offering
        )
        assert form.is_valid(), form.errors

        saved = form.save()

        assert (saved.eventbrite_category, saved.eventbrite_subcategory) == (ARTS, JEWELRY)

    def it_saves_a_category_alone():
        offering = ClassOfferingFactory()
        form = TeachClassOfferingForm(_composer_data(offering, eventbrite_category=HOBBIES), instance=offering)
        assert form.is_valid(), form.errors

        assert (form.save().eventbrite_category, offering.eventbrite_subcategory) == (HOBBIES, "")

    def it_saves_neither_when_none_is_picked():
        offering = ClassOfferingFactory()
        form = TeachClassOfferingForm(_composer_data(offering), instance=offering)
        assert form.is_valid(), form.errors

        saved = form.save()

        assert (saved.eventbrite_category, saved.eventbrite_subcategory) == ("", "")

    @pytest.mark.parametrize("form_class", [TeachClassOfferingForm, ClassOfferingForm])
    def it_refuses_a_subcategory_from_another_category(form_class: Any):
        offering = ClassOfferingFactory()
        form = form_class(
            _composer_data(offering, eventbrite_category=HOBBIES, eventbrite_subcategory=JEWELRY), instance=offering
        )

        assert form.is_valid() is False
        assert form.errors["eventbrite_subcategory"] == [MISMATCH]

    def it_refuses_a_subcategory_with_no_category():
        offering = ClassOfferingFactory()
        form = TeachClassOfferingForm(_composer_data(offering, eventbrite_subcategory=DIY), instance=offering)

        assert form.errors["eventbrite_subcategory"] == [MISMATCH]

    def it_refuses_an_id_that_is_not_on_eventbrites_list():
        offering = ClassOfferingFactory()
        form = TeachClassOfferingForm(_composer_data(offering, eventbrite_category="999"), instance=offering)

        assert "eventbrite_category" in form.errors

    def it_puts_both_on_the_dates_step_beside_the_opt_in():
        assert step_for_field("eventbrite_category") == step_for_field("eventbrite_enabled") == 3
        assert step_for_field("eventbrite_subcategory") == 3

    def it_ties_each_subcategory_option_to_its_parent_in_the_browser():
        html = str(TeachClassOfferingForm()["eventbrite_subcategory"])

        assert '<option value="" selected>' in html
        jewelry = re.search(r'<option value="5014"[^>]*>', html)
        assert jewelry
        # Django escapes the quotes; the browser reads them back as ebCategory !== '105'.
        assert ':disabled="ebCategory !== &#x27;105&#x27;"' in jewelry.group()
        assert ':hidden="ebCategory !== &#x27;105&#x27;"' in jewelry.group()
        assert 'x-ref="ebSubcategory"' in html

    def it_clears_the_subcategory_when_the_category_changes():
        attrs = TeachClassOfferingForm().fields["eventbrite_category"].widget.attrs

        assert attrs["x-init"] == "ebCategory = $el.value"
        assert attrs["@change"] == "ebCategory = $el.value; $refs.ebSubcategory.value = ''"


def describe_while_the_integration_is_off():
    def it_leaves_both_off_every_form():
        fixed = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)

        for form in (TeachClassOfferingForm(), ClassOfferingForm(), TeachPublishedClassForm(instance=fixed)):
            assert "eventbrite_category" not in form.fields
            assert "eventbrite_subcategory" not in form.fields

    def it_ignores_a_posted_pair():
        offering = ClassOfferingFactory()
        form = TeachClassOfferingForm(
            _composer_data(offering, eventbrite_category=HOBBIES, eventbrite_subcategory=JEWELRY), instance=offering
        )
        assert form.is_valid(), form.errors

        assert form.save().eventbrite_category == ""


def describe_the_live_class_edit_form():
    @pytest.fixture(autouse=True)
    def _integration_on(settings: Any) -> None:
        _switch_integration_on(settings)

    def it_saves_a_category_and_its_subcategory_on_a_live_class():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        form = TeachPublishedClassForm(
            _published_data(eventbrite_category=HOBBIES, eventbrite_subcategory=DIY), instance=offering
        )
        assert form.is_valid(), form.errors

        saved = form.save()

        assert (saved.eventbrite_category, saved.eventbrite_subcategory) == (HOBBIES, DIY)

    def it_refuses_a_mismatched_pair():
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        form = TeachPublishedClassForm(
            _published_data(eventbrite_category=ARTS, eventbrite_subcategory=DIY), instance=offering
        )

        assert form.errors["eventbrite_subcategory"] == [MISMATCH]

    def it_offers_neither_on_a_flexible_class():
        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE
        )
        form = TeachPublishedClassForm(
            _published_data(eventbrite_category=ARTS, eventbrite_subcategory=DIY), instance=offering
        )

        assert "eventbrite_category" not in form.fields
        assert form.is_valid(), form.errors


def describe_the_pages():
    @pytest.fixture(autouse=True)
    def _integration_on(settings: Any) -> None:
        _switch_integration_on(settings)

    def it_puts_both_dropdowns_in_the_composers_eventbrite_block(client: Client):
        _instructor_client(client)

        html = client.get(reverse("classes:teach_class_create")).content.decode()

        block = html[html.index("data-eventbrite>") :]
        component = block[block.index("data-eventbrite-category") :]
        assert "x-data=\"{ ebCategory: '' }\"" in component
        assert _select(component, "eventbrite_category").count("<option") == 22
        assert _select(component, "eventbrite_subcategory").count("<option") == 217

    def it_puts_both_dropdowns_on_the_live_class_edit_page_with_the_saved_choice(client: Client):
        instructor = _instructor_client(client)
        offering = ClassOfferingFactory(
            instructor=instructor,
            status=ClassOffering.Status.PUBLISHED,
            eventbrite_category=ARTS,
            eventbrite_subcategory=JEWELRY,
        )

        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()

        component = html[html.index("data-eventbrite-category") :]
        assert '<option value="105" selected>' in _select(component, "eventbrite_category")
        assert re.search(r'<option value="5014" selected[ >]', _select(component, "eventbrite_subcategory"))
        assert html.count('name="eventbrite_subcategory"') == 1

    def it_saves_the_pair_from_the_live_class_edit_page(client: Client):
        instructor = _instructor_client(client)
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PUBLISHED)
        payload = {
            **_published_data(eventbrite_category=HOBBIES, eventbrite_subcategory=DIY),
            "faq-TOTAL_FORMS": "0",
            "faq-INITIAL_FORMS": "0",
            "faq-MIN_NUM_FORMS": "0",
            "faq-MAX_NUM_FORMS": "1000",
        }

        response = client.post(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}), payload)

        assert response.status_code == 302
        offering.refresh_from_db()
        assert (offering.eventbrite_category, offering.eventbrite_subcategory) == (HOBBIES, DIY)
