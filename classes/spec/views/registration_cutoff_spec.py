"""BDD specs for the registration cutoff across register, the class page, the catalog and the composer.

A class past its cutoff is still listed (``bookable()`` is untouched) but takes no sign-up and
offers no waitlist. Assertions anchor on markup (data attributes, URLs, factory strings), never
on copy: the changelog renders on every page.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.contrib.messages import get_messages
from django.template.defaultfilters import date as date_filter
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import localtime

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassOffering, Registration

pytestmark = pytest.mark.django_db

CLOSED_ATTR = "data-registration-closed"
CLOSES_AT_ATTR = "data-registration-closes-at"
CTA = 'class="cp-detail__cta"'
WAITLIST_CTA = "?waitlist=1"


def _dated(slug: str, *offsets_hours: float, hours: int | None = 48, **kwargs) -> ClassOffering:
    """A published class with one session per offset (hours from now) and the given cutoff."""
    kwargs.setdefault("title", slug.replace("-", " ").title())
    kwargs.setdefault("category", CategoryFactory(name=f"{slug} type", slug=f"{slug}-cat"))
    kwargs.setdefault("instructor", InstructorFactory())
    kwargs.setdefault("status", ClassOffering.Status.PUBLISHED)
    offering = ClassOfferingFactory(slug=slug, registration_cutoff_hours=hours, **kwargs)
    base = timezone.now()
    for offset in offsets_hours:
        start = base + timedelta(hours=offset)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def _detail(client, offering: ClassOffering) -> str:
    return client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()


def _register_url(offering: ClassOffering) -> str:
    return reverse("classes:register", kwargs={"slug": offering.slug})


def _detail_url(offering: ClassOffering) -> str:
    return reverse("classes:public_class_detail", kwargs={"slug": offering.slug})


def _rail(html: str) -> str:
    """The sign-up card only, so a CTA elsewhere on the page cannot vouch for it."""
    start = html.index('class="cp-detail__rail-card"')
    return html[start : html.index("cp-detail__rail-meta", start)]


def describe_register():
    def it_redirects_a_closed_class_to_its_page_with_the_closed_message(client):
        closed = _dated("closed-register", 10)
        response = client.get(_register_url(closed))
        assert response.status_code == 302
        assert response.url == _detail_url(closed)
        assert [m.message for m in get_messages(response.wsgi_request)] == [
            "Registration has closed. Sign-ups end 48 hours before class starts."
        ]

    def it_names_the_hours_the_class_was_saved_with(client):
        closed = _dated("closed-register-12", 10, hours=12)
        response = client.get(_register_url(closed))
        assert (
            "Sign-ups end 12 hours before class starts." in [m.message for m in get_messages(response.wsgi_request)][0]
        )

    def it_creates_no_registration_for_a_closed_class(client):
        closed = _dated("closed-register-post", 10)
        client.post(
            _register_url(closed),
            data={"first_name": "Sam", "last_name": "Smith", "email": "sam@example.com"},
        )
        assert not Registration.objects.filter(class_offering=closed).exists()

    def it_still_serves_the_form_for_an_open_class(client):
        assert client.get(_register_url(_dated("open-register", 100))).status_code == 200

    def it_still_serves_the_form_right_up_to_the_start_with_the_cutoff_off(client):
        assert client.get(_register_url(_dated("off-register", 1, hours=None))).status_code == 200

    def it_ignores_the_cutoff_for_a_flexible_class(client):
        flexible = _dated("flex-register", scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        assert client.get(_register_url(flexible)).status_code == 200

    def it_still_redirects_a_newcomer_asking_for_the_waitlist(client):
        closed = _dated("closed-waitlist", 10, capacity=0)
        response = client.get(_register_url(closed) + "?waitlist=1")
        assert response.status_code == 302
        assert response.url == _detail_url(closed)


def _claim_post_data() -> dict:
    return {
        "first_name": "Sam",
        "last_name": "Smith",
        "pronouns": "",
        "email": "sam@example.com",
        "phone": "",
        "prior_experience": "",
        "looking_for": "",
        "discount_code": "",
        "liability_signature": "Sam Smith",
        "accepts_liability": "on",
    }


def describe_a_waitlist_claim_inside_the_cutoff():
    """A seat freed 47 hours out mails a claim link; the person it names goes through.

    They were already in the headcount when the class filled and were told the seat is
    theirs, so the cutoff is not theirs to hit. Everyone else stays closed.
    """

    def _waiting(offering: ClassOffering) -> Registration:
        return RegistrationFactory(
            class_offering=offering,
            first_name="Sam",
            last_name="Smith",
            email="sam@example.com",
            status=Registration.Status.WAITLISTED,
        )

    def _claim_url(offering: ClassOffering, waiting: Registration) -> str:
        return f"{_register_url(offering)}?waitlist_token={waiting.self_serve_token}"

    def it_lets_the_promoted_waitlister_reach_the_form(client):
        closed = _dated("claim-form", 10)
        response = client.get(_claim_url(closed, _waiting(closed)))
        assert response.status_code == 200
        assert [m.message for m in get_messages(response.wsgi_request)] == []

    def it_lets_the_promoted_waitlister_confirm_the_seat(client):
        closed = _dated("claim-confirm", 10, price_cents=0)
        waiting = _waiting(closed)
        response = client.post(_claim_url(closed, waiting), data=_claim_post_data())
        assert response.status_code == 302
        assert response.url == reverse("classes:register_success", kwargs={"slug": closed.slug})
        waiting.refresh_from_db()
        assert waiting.status == Registration.Status.CONFIRMED
        assert Registration.objects.filter(class_offering=closed).count() == 1

    def it_still_redirects_a_claim_on_a_class_that_has_started(client):
        started = _dated("claim-started", -1)
        response = client.get(_claim_url(started, _waiting(started)))
        assert response.status_code == 302
        assert response.url == _detail_url(started)

    def it_still_redirects_a_token_that_matches_no_waiting_row(client):
        closed = _dated("claim-bogus", 10)
        response = client.get(_register_url(closed) + "?waitlist_token=not-a-token")
        assert response.status_code == 302
        assert response.url == _detail_url(closed)


def describe_the_class_page_rail():
    def it_says_closed_and_offers_no_cta_past_the_cutoff(client):
        rail = _rail(_detail(client, _dated("rail-closed", 10)))
        assert CLOSED_ATTR in rail
        assert CTA not in rail
        assert WAITLIST_CTA not in rail
        assert CLOSES_AT_ATTR not in rail

    def it_offers_no_waitlist_on_a_closed_sold_out_class(client):
        closed = _dated("rail-closed-full", 10, capacity=0)
        rail = _rail(_detail(client, closed))
        assert CLOSED_ATTR in rail
        assert WAITLIST_CTA not in rail

    def it_says_when_registration_closes_under_the_cta_while_open(client):
        offering = _dated("rail-open", 100)
        rail = _rail(_detail(client, offering))
        assert CTA in rail
        # The page renders the instant in the site's local time, as the date filter does for a template.
        assert f'{CLOSES_AT_ATTR}="{date_filter(localtime(offering.registration_closes_at), "c")}"' in rail
        assert CLOSED_ATTR not in rail

    def it_keeps_the_closes_line_under_the_waitlist_cta(client):
        offering = _dated("rail-open-full", 100, capacity=0)
        rail = _rail(_detail(client, offering))
        assert WAITLIST_CTA in rail
        assert CLOSES_AT_ATTR in rail

    def it_shows_neither_line_with_the_cutoff_off(client):
        rail = _rail(_detail(client, _dated("rail-off", 100, hours=None)))
        assert CTA in rail
        assert CLOSES_AT_ATTR not in rail
        assert CLOSED_ATTR not in rail

    def it_leaves_a_started_class_as_before(client):
        rail = _rail(_detail(client, _dated("rail-started", -1)))
        assert CTA not in rail
        assert CLOSED_ATTR not in rail
        assert CLOSES_AT_ATTR not in rail

    def it_leaves_a_flexible_class_as_before(client):
        flexible = _dated("rail-flex", scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        rail = _rail(_detail(client, flexible))
        assert CTA in rail
        assert CLOSES_AT_ATTR not in rail
        assert CLOSED_ATTR not in rail

    def it_leaves_the_site_switch_as_before(client, settings):
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.class_registration_enabled = False
        config.save()
        rail = _rail(_detail(client, _dated("rail-switch", 100)))
        assert "cp-detail__cta--disabled" in rail
        assert CLOSES_AT_ATTR not in rail
        assert CLOSED_ATTR not in rail


def _runs(prefix: str, *offsets_hours: float) -> list[ClassOffering]:
    """Runs of one class (same title and category, so one grouping key), one per offset."""
    category = CategoryFactory(name=f"{prefix} type", slug=f"{prefix}-cat")
    instructor = InstructorFactory()
    return [
        _dated(f"{prefix}-{index}", offset, title=f"{prefix} class", category=category, instructor=instructor)
        for index, offset in enumerate(offsets_hours)
    ]


def _other_dates(html: str) -> str:
    """The Other Dates list only: a closed run is still a related class lower on the page."""
    start = html.index('class="cp-detail__other-dates"')
    return html[start : html.index("</ul>", start)]


def describe_other_dates_for_this_class():
    def it_lists_only_the_runs_still_open(client):
        current, open_run, closed_run = _runs("sibling", 100, 200, 10)
        other_dates = _other_dates(_detail(client, current))
        assert _detail_url(open_run) in other_dates
        assert _detail_url(closed_run) not in other_dates


def describe_the_run_switcher():
    def it_offers_only_the_runs_still_open(client):
        current, open_run, closed_run = _runs("switcher", 100, 200, 10)
        html = client.get(_register_url(current)).content.decode()
        assert f'<option value="{_register_url(open_run)}"' in html
        assert _register_url(closed_run) not in html


def _catalog(client) -> str:
    return client.get(reverse("classes:public_list")).content.decode()


def describe_the_catalog():
    def it_lists_a_closed_class_with_a_closed_pill_in_place_of_the_seats(client):
        closed = _dated("catalog-closed", 10)
        html = _catalog(client)
        assert _detail_url(closed) in html
        assert html.count(CLOSED_ATTR) == 1
        assert "cls-spots ok" not in html

    def it_shows_the_seats_on_an_open_class(client):
        _dated("catalog-open", 100)
        html = _catalog(client)
        assert CLOSED_ATTR not in html
        assert "cls-spots ok" in html

    def it_marks_only_the_closed_run_inside_a_grouped_card(client):
        open_run, closed_run = _runs("catalog-group", 100, 10)
        html = _catalog(client)
        assert _detail_url(open_run) in html
        assert _detail_url(closed_run) in html
        assert html.count(CLOSED_ATTR) == 1
        assert html.count("cls-spots ok") == 1

    def it_shows_no_pill_at_all_on_a_flexible_class(client):
        _dated("catalog-flex", scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        html = _catalog(client)
        assert CLOSED_ATTR not in html
        assert "cls-spots" not in html

    def _cost(client, django_assert_max_num_queries) -> int:
        with django_assert_max_num_queries(500) as captured:
            client.get(reverse("classes:public_list"))
        return len(captured.captured_queries)

    def it_spends_no_query_per_closed_class(client, django_assert_max_num_queries):
        """``registration_open`` reads the ``first_session_at`` annotation ``bookable()`` already
        carries, so the closed pill costs the page nothing per row."""
        category = CategoryFactory(name="flat type", slug="flat-cat")
        instructor = InstructorFactory()
        for index in range(2):
            _dated(f"flat-{index}", 10, category=category, instructor=instructor)
        two = _cost(client, django_assert_max_num_queries)
        for index in range(2, 6):
            _dated(f"flat-{index}", 10, category=category, instructor=instructor)
        assert _cost(client, django_assert_max_num_queries) == two

    def it_spends_no_query_per_run_inside_a_grouped_card(client, django_assert_max_num_queries):
        _runs("flat-group", 100, 10)
        two = _cost(client, django_assert_max_num_queries)
        _runs("flat-group-more", 100, 10, 200, 300)
        html = _catalog(client)
        assert html.count(CLOSED_ATTR) == 2
        assert _cost(client, django_assert_max_num_queries) == two


def _management() -> dict:
    return {
        "sessions-TOTAL_FORMS": "0",
        "sessions-INITIAL_FORMS": "0",
        "sessions-MIN_NUM_FORMS": "0",
        "sessions-MAX_NUM_FORMS": "1000",
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
    }


def describe_the_composer():
    @pytest.fixture
    def instructor(db):
        user = UserFactory(username="cutoff-teacher@example.com")
        return InstructorFactory(user=user, full_legal_name="Cutoff Teacher", instructor_slug="cutoff-teacher")

    def it_opens_with_the_toggle_on_and_the_hours_box_under_it(client, instructor):
        client.force_login(instructor.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        assert "registrationCutoff: true," in html
        assert 'x-model="registrationCutoff"' in html
        assert 'data-registration-cutoff-hours x-show="registrationCutoff"' in html
        hours_box = re.search(r'<input[^>]*id="id_registration_cutoff_hours"[^>]*>', html)
        assert hours_box is not None
        assert 'value="48"' in hours_box.group(0)

    def it_opens_with_the_toggle_off_for_a_class_saved_without_a_cutoff(client, instructor):
        client.force_login(instructor.user)
        offering = ClassOfferingFactory(instructor=instructor, registration_cutoff_hours=None)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert "registrationCutoff: false," in html

    def it_renders_the_same_fields_for_an_admin(client, admin_user):
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_create")).content.decode()
        assert "registrationCutoff: true," in html
        assert 'data-registration-cutoff-hours x-show="registrationCutoff"' in html

    def it_saves_null_when_the_toggle_is_off(client, instructor):
        client.force_login(instructor.user)
        response = client.post(
            reverse("classes:teach_class_create"),
            {
                **_management(),
                "title": "No Cutoff",
                "category": CategoryFactory().pk,
                "description": "A class with sign-ups right up to the start.",
                "price_cents": "40.00",
                "capacity": "6",
                "scheduling_model": ClassOffering.SchedulingModel.FIXED,
                "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
                "registration_cutoff_hours": "48",
                "action": "save",
                "step": "3",
            },
        )
        assert response.status_code == 302
        assert ClassOffering.objects.get(title="No Cutoff").registration_cutoff_hours is None

    def it_saves_the_hours_when_the_toggle_is_on(client, instructor):
        client.force_login(instructor.user)
        response = client.post(
            reverse("classes:teach_class_create"),
            {
                **_management(),
                "title": "Day Before",
                "category": CategoryFactory().pk,
                "description": "A class that closes a day out.",
                "price_cents": "40.00",
                "capacity": "6",
                "scheduling_model": ClassOffering.SchedulingModel.FIXED,
                "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
                "registration_cutoff_enabled": "on",
                "registration_cutoff_hours": "24",
                "action": "save",
                "step": "3",
            },
        )
        assert response.status_code == 302
        assert ClassOffering.objects.get(title="Day Before").registration_cutoff_hours == 24
