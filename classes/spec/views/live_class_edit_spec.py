"""BDD specs for #1067: an instructor edits everything on a live class except its dates."""

from __future__ import annotations

from datetime import date, timedelta
from unittest import mock

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.emails import eventbrite_rules_failed_email
from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, InstructorFactory, UserFactory
from classes.forms import TeachPublishedClassForm
from classes.models import ClassOffering, ClassSession
from classes.spec.live_edit import live_facts
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, LocationFactory

pytestmark = pytest.mark.django_db

Status = ClassOffering.Status


@pytest.fixture
def instructor(db):
    user = UserFactory(username="live-edit-teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher Live", instructor_slug="teacher-live")


def _live(instructor, **kwargs) -> ClassOffering:
    offering = ClassOfferingFactory(
        instructor=instructor, status=Status.PUBLISHED, published_at=timezone.now(), **kwargs
    )
    start = timezone.now() + timedelta(days=3)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def _post(offering: ClassOffering, **overrides: str) -> dict[str, str]:
    """The live class edit page's POST with ``overrides`` typed over the class's own values."""
    return {
        **live_facts(offering, **overrides),
        "description": offering.description,
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
    }


def _edit_url(offering: ClassOffering) -> str:
    return reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})


def describe_the_facts_an_instructor_now_edits():
    def it_saves_the_title_class_type_price_capacity_location_and_age_minimum(instructor, client):
        offering = _live(instructor, title="Old Title", price_cents=8000, capacity=4)
        category = CategoryFactory(name="Metal")
        location = LocationFactory(name="Forge Bay")
        client.force_login(instructor.user)

        resp = client.post(
            _edit_url(offering),
            _post(
                offering,
                title="New Title",
                category=str(category.pk),
                price_cents="95.00",
                capacity="7",
                area=str(location.pk),
                age_minimum="16",
            ),
        )

        assert resp.status_code == 302
        offering.refresh_from_db()
        assert (offering.title, offering.category_id, offering.price_cents) == ("New Title", category.pk, 9500)
        assert (offering.capacity, offering.area_id, offering.age_minimum) == (7, location.pk, 16)
        assert offering.status == Status.PUBLISHED

    def it_labels_the_category_class_type_and_offers_the_location_picker(instructor):
        form = TeachPublishedClassForm(instance=_live(instructor))
        assert form.fields["category"].label == "Class Type"
        assert form.fields["area"].label == "Location"
        assert form.fields["price_cents"].help_text.startswith("In dollars")

    def it_refuses_a_price_under_the_floor(instructor, client):
        offering = _live(instructor, price_cents=8000)
        client.force_login(instructor.user)
        resp = client.post(_edit_url(offering), _post(offering, price_cents="0.50"))
        assert resp.status_code == 200
        assert "Classes cost at least $1.00." in resp.content.decode()
        offering.refresh_from_db()
        assert offering.price_cents == 8000

    def it_refuses_a_capacity_under_one_seat(instructor, client):
        offering = _live(instructor, capacity=4)
        client.force_login(instructor.user)
        resp = client.post(_edit_url(offering), _post(offering, capacity="0"))
        assert resp.status_code == 200
        assert "A class needs at least 1 seat." in resp.content.decode()
        offering.refresh_from_db()
        assert offering.capacity == 4

    def it_queues_the_eventbrite_update_after_a_save(instructor, client):
        offering = _live(instructor)
        client.force_login(instructor.user)
        with mock.patch.object(ClassOffering, "mark_eventbrite_edit_saved") as marked:
            resp = client.post(_edit_url(offering), _post(offering, title="Renamed"))
        assert resp.status_code == 302
        marked.assert_called_once_with()


def describe_a_price_edit_while_a_sale_runs():
    def it_is_refused_as_the_composer_refuses_it(instructor, client):
        offering = _live(
            instructor,
            price_cents=8000,
            sale_enabled=True,
            sale_kind=ClassOffering.SaleKind.FIXED,
            sale_amount_cents=3000,
        )
        client.force_login(instructor.user)
        resp = client.post(_edit_url(offering), _post(offering, price_cents="25.00"))
        assert resp.status_code == 200
        assert "This class is on sale for" in resp.content.decode()
        offering.refresh_from_db()
        assert offering.price_cents == 8000

    def it_saves_a_price_the_sale_still_fits(instructor, client):
        offering = _live(
            instructor,
            price_cents=8000,
            sale_enabled=True,
            sale_kind=ClassOffering.SaleKind.FIXED,
            sale_amount_cents=3000,
        )
        client.force_login(instructor.user)
        resp = client.post(_edit_url(offering), _post(offering, price_cents="90.00"))
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.price_cents == 9000


def describe_the_dates_stay_locked():
    def it_ignores_a_crafted_post_of_sessions_scheduling_and_cutoff(instructor, client):
        offering = _live(instructor, registration_cutoff_hours=48)
        session = offering.sessions.get()
        client.force_login(instructor.user)

        resp = client.post(
            _edit_url(offering),
            {
                **_post(offering),
                "scheduling_model": ClassOffering.SchedulingModel.FLEXIBLE,
                "scheduling_type": ClassOffering.SchedulingType.SERIES_PACKAGE,
                "registration_cutoff_enabled": "",
                "registration_cutoff_hours": "1",
                "flexible_starts_on": "2030-01-01",
                "flexible_ends_on": "2030-02-01",
                "sessions-TOTAL_FORMS": "1",
                "sessions-INITIAL_FORMS": "1",
                "sessions-0-id": str(session.pk),
                "sessions-0-starts_at": "2030-01-01T10:00",
                "sessions-0-ends_at": "2030-01-01T12:00",
            },
        )

        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.scheduling_model == ClassOffering.SchedulingModel.FIXED
        assert offering.scheduling_type == ClassOffering.SchedulingType.SINGLE_SESSION
        assert offering.registration_cutoff_hours == 48
        assert (offering.flexible_starts_on, offering.flexible_ends_on) == (None, None)
        assert list(ClassSession.objects.filter(class_offering=offering)) == [session]
        assert ClassSession.objects.get(pk=session.pk).starts_at == session.starts_at

    def it_carries_no_date_field(instructor):
        fields = TeachPublishedClassForm(instance=_live(instructor)).fields
        for name in (
            "scheduling_model",
            "scheduling_type",
            "registration_cutoff_enabled",
            "registration_cutoff_hours",
            "flexible_starts_on",
            "flexible_ends_on",
        ):
            assert name not in fields

    def it_keeps_a_flexible_windows_days(instructor, client):
        offering = _live(
            instructor,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
            flexible_starts_on=date(2031, 3, 1),
            flexible_ends_on=date(2031, 4, 1),
        )
        client.force_login(instructor.user)
        resp = client.post(
            _edit_url(offering),
            {**_post(offering), "flexible_starts_on": "2031-05-01", "flexible_ends_on": "2031-06-01"},
        )
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert (offering.flexible_starts_on, offering.flexible_ends_on) == (date(2031, 3, 1), date(2031, 4, 1))


def describe_capacity_on_a_flexible_class():
    def it_has_no_capacity_field_and_says_there_is_no_seat_cap(instructor, client):
        offering = _live(instructor, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE, capacity=6)
        assert "capacity" not in TeachPublishedClassForm(instance=offering).fields
        client.force_login(instructor.user)
        html = client.get(_edit_url(offering)).content.decode()
        assert 'name="capacity"' not in html
        assert "Flexible classes have no seat cap." in html

    def it_ignores_a_crafted_capacity(instructor, client):
        offering = _live(instructor, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE, capacity=6)
        client.force_login(instructor.user)
        resp = client.post(_edit_url(offering), {**_post(offering), "capacity": "40"})
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.capacity == 6


def describe_guild_staff_on_a_live_class():
    def it_saves_the_facts_but_never_the_instructors_list(instructor, client):
        guild = GuildFactory(name="Live Edit Guild")
        GuildStaffMembershipFactory(guild=guild, member=instructor)
        owner = InstructorFactory(instructor_slug="live-edit-owner")
        stranger = InstructorFactory(instructor_slug="live-edit-stranger")
        offering = _live(owner, category=CategoryFactory(guild=guild), title="Guild Class")
        client.force_login(instructor.user)

        resp = client.post(
            _edit_url(offering),
            {**_post(offering, title="Guild Class Renamed"), "instructors": [str(stranger.pk)]},
        )

        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.title == "Guild Class Renamed"
        assert offering.instructor_list == [owner]


def describe_the_copy_that_sends_instructors_to_an_admin():
    def it_says_only_dates_need_an_admin_on_the_page(instructor, client):
        offering = _live(instructor)
        client.force_login(instructor.user)
        html = client.get(_edit_url(offering)).content.decode()
        assert "You can change everything here any time except the dates. To change a date, ask an admin." in html
        assert "Dates change through an admin because people booked on them." in html
        assert "To change the title, dates, price, or capacity" not in html

    def it_sends_the_eventbrite_refusal_to_the_edit_page_unless_the_fix_is_a_date(instructor):
        offering = _live(instructor)
        _subject, body = eventbrite_rules_failed_email(offering, ["Title: a phone number"])
        assert f"/teach/classes/{offering.pk}/edit/" in body
        assert "If the fix is a date, open the class and use Request a Change" in body
