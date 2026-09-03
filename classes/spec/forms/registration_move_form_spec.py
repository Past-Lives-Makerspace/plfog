"""BDD specs for RegistrationMoveForm."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory, InstructorFactory
from classes.forms import RegistrationMoveForm
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db


def _bookable_for(instructor, slug: str, **kwargs) -> ClassOffering:
    """A published class of this instructor's with a future first session."""
    offering = ClassOfferingFactory(slug=slug, instructor=instructor, status=ClassOffering.Status.PUBLISHED, **kwargs)
    ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=7))
    return offering


def describe_RegistrationMoveForm():
    def it_excludes_the_current_class_from_choices():
        current = ClassOfferingFactory(slug="cur")
        other = ClassOfferingFactory(slug="oth")
        form = RegistrationMoveForm(current=current)
        choices = list(form.fields["target"].queryset)
        assert other in choices
        assert current not in choices

    def it_is_valid_with_a_different_class():
        current = ClassOfferingFactory(slug="cur2")
        other = ClassOfferingFactory(slug="oth2")
        form = RegistrationMoveForm({"target": other.pk}, current=current)
        assert form.is_valid()

    def it_is_invalid_when_target_is_the_current_class():
        current = ClassOfferingFactory(slug="cur3")
        form = RegistrationMoveForm({"target": current.pk}, current=current)
        assert not form.is_valid()

    def it_lists_all_classes_when_no_current_is_given():
        offering = ClassOfferingFactory(slug="all-a")
        form = RegistrationMoveForm()
        assert offering in list(form.fields["target"].queryset)

    def describe_upcoming_scoping():
        def it_excludes_a_class_whose_first_session_has_passed():
            past = ClassOfferingFactory(slug="up-past")
            ClassSessionFactory(class_offering=past, starts_at=timezone.now() - timedelta(days=1))
            form = RegistrationMoveForm()
            assert past not in list(form.fields["target"].queryset)

        def it_excludes_a_started_series_even_with_a_later_future_session():
            started = ClassOfferingFactory(slug="up-started")
            ClassSessionFactory(class_offering=started, starts_at=timezone.now() - timedelta(days=2))
            ClassSessionFactory(class_offering=started, starts_at=timezone.now() + timedelta(days=5))
            form = RegistrationMoveForm()
            assert started not in list(form.fields["target"].queryset)

        def it_includes_a_class_with_a_future_first_session():
            future = ClassOfferingFactory(slug="up-future")
            ClassSessionFactory(class_offering=future, starts_at=timezone.now() + timedelta(days=3))
            form = RegistrationMoveForm()
            assert future in list(form.fields["target"].queryset)

        def it_includes_a_flexible_class_regardless_of_past_sessions():
            flexible = ClassOfferingFactory(slug="up-flex", scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
            ClassSessionFactory(class_offering=flexible, starts_at=timezone.now() - timedelta(days=1))
            form = RegistrationMoveForm()
            assert flexible in list(form.fields["target"].queryset)

        def it_includes_an_undated_class():
            undated = ClassOfferingFactory(slug="up-undated")
            form = RegistrationMoveForm()
            assert undated in list(form.fields["target"].queryset)

        def it_rejects_a_crafted_post_at_a_past_class():
            current = ClassOfferingFactory(slug="up-cur")
            past = ClassOfferingFactory(slug="up-past-post")
            ClassSessionFactory(class_offering=past, starts_at=timezone.now() - timedelta(days=1))
            form = RegistrationMoveForm({"target": past.pk}, current=current)
            assert not form.is_valid()

    def describe_admin_scope():
        def it_includes_draft_and_private_upcoming_classes():
            draft = ClassOfferingFactory(slug="adm-draft", status=ClassOffering.Status.DRAFT)
            private = ClassOfferingFactory(slug="adm-priv", status=ClassOffering.Status.PUBLISHED, is_private=True)
            form = RegistrationMoveForm()
            choices = list(form.fields["target"].queryset)
            assert draft in choices
            assert private in choices

        def it_allows_moving_into_a_full_class():
            from classes.factories import RegistrationFactory
            from classes.models import Registration

            current = ClassOfferingFactory(slug="adm-full-cur")
            full = ClassOfferingFactory(slug="adm-full", capacity=1)
            RegistrationFactory(class_offering=full, status=Registration.Status.CONFIRMED)
            form = RegistrationMoveForm({"target": full.pk}, current=current)
            assert form.is_valid()

    def describe_instructor_scoping():
        def it_offers_only_that_instructors_bookable_classes():
            mine = InstructorFactory(instructor_slug="mv-mine")
            current = ClassOfferingFactory(slug="ins-cur", instructor=mine)
            my_other = _bookable_for(mine, "ins-other")
            my_past = ClassOfferingFactory(slug="ins-past", instructor=mine, status=ClassOffering.Status.PUBLISHED)
            ClassSessionFactory(class_offering=my_past, starts_at=timezone.now() - timedelta(days=1))
            not_mine = _bookable_for(InstructorFactory(instructor_slug="mv-other"), "ins-foreign")
            form = RegistrationMoveForm(current=current, instructor=mine)
            choices = list(form.fields["target"].queryset)
            assert choices == [my_other]
            assert not_mine not in choices
            assert my_past not in choices

        def it_excludes_their_own_draft_class():
            mine = InstructorFactory(instructor_slug="mv-draft")
            current = _bookable_for(mine, "ins-draft-cur")
            draft = _bookable_for(mine, "ins-draft")
            draft.status = ClassOffering.Status.DRAFT
            draft.save(update_fields=["status"])
            form = RegistrationMoveForm(current=current, instructor=mine)
            assert draft not in list(form.fields["target"].queryset)

        def it_excludes_their_own_undated_class():
            mine = InstructorFactory(instructor_slug="mv-undated")
            current = _bookable_for(mine, "ins-und-cur")
            undated = ClassOfferingFactory(slug="ins-undated", instructor=mine, status=ClassOffering.Status.PUBLISHED)
            form = RegistrationMoveForm(current=current, instructor=mine)
            assert undated not in list(form.fields["target"].queryset)

        def it_includes_their_own_flexible_published_class():
            mine = InstructorFactory(instructor_slug="mv-flexi")
            current = _bookable_for(mine, "ins-flex-cur")
            flexible = ClassOfferingFactory(
                slug="ins-flex",
                instructor=mine,
                status=ClassOffering.Status.PUBLISHED,
                scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
            )
            form = RegistrationMoveForm(current=current, instructor=mine)
            assert flexible in list(form.fields["target"].queryset)

        def it_rejects_a_crafted_post_at_a_class_they_do_not_instruct():
            mine = InstructorFactory(instructor_slug="mv-craft")
            current = ClassOfferingFactory(slug="ins-craft-cur", instructor=mine)
            foreign = _bookable_for(InstructorFactory(instructor_slug="mv-craft-f"), "ins-craft-foreign")
            form = RegistrationMoveForm({"target": foreign.pk}, current=current, instructor=mine)
            assert not form.is_valid()

        def it_rejects_a_full_class():
            from classes.factories import RegistrationFactory
            from classes.models import Registration

            mine = InstructorFactory(instructor_slug="mv-full")
            current = _bookable_for(mine, "ins-full-cur")
            full = _bookable_for(mine, "ins-full", capacity=1)
            RegistrationFactory(class_offering=full, status=Registration.Status.CONFIRMED)
            form = RegistrationMoveForm({"target": full.pk}, current=current, instructor=mine)
            assert not form.is_valid()
            assert form.errors["target"] == ["That class is full."]

        def it_accepts_a_class_with_a_seat_open():
            mine = InstructorFactory(instructor_slug="mv-seat")
            current = _bookable_for(mine, "ins-seat-cur")
            open_class = _bookable_for(mine, "ins-seat", capacity=2)
            form = RegistrationMoveForm({"target": open_class.pk}, current=current, instructor=mine)
            assert form.is_valid()

    def describe_query_count():
        def it_builds_admin_choices_in_one_query_and_renders_without_more(django_assert_num_queries):
            for i in range(3):
                ClassOfferingFactory(slug=f"nq-adm-{i}")
            with django_assert_num_queries(1):
                form = RegistrationMoveForm()
                assert form.has_targets
                str(form["target"])
                str(form["target"])
                str(form["target"])

        def it_builds_instructor_choices_in_two_queries_and_renders_without_more(django_assert_num_queries):
            from core.models import SiteConfiguration

            mine = InstructorFactory(instructor_slug="mv-nq")
            current = _bookable_for(mine, "nq-cur")
            for i in range(3):
                _bookable_for(mine, f"nq-ins-{i}")
            SiteConfiguration.load()  # warm the singleton row so get_or_create can't INSERT inside the pin
            # One query for the SiteConfiguration singleton (bookable -> public), one for the choices.
            with django_assert_num_queries(2):
                form = RegistrationMoveForm(current=current, instructor=mine)
                assert form.has_targets
                str(form["target"])
                str(form["target"])
                str(form["target"])

    def describe_has_targets():
        def it_is_true_when_a_class_can_be_picked():
            ClassOfferingFactory(slug="ht-yes")
            form = RegistrationMoveForm()
            assert form.has_targets is True

        def it_is_false_when_the_only_class_is_the_current_one():
            mine = InstructorFactory(instructor_slug="mv-lonely")
            current = _bookable_for(mine, "ht-cur")
            form = RegistrationMoveForm(current=current, instructor=mine)
            assert form.has_targets is False

    def describe_price_note_helpers():
        def it_reports_instructor_scoping():
            mine = InstructorFactory(instructor_slug="mv-scope")
            assert RegistrationMoveForm(instructor=mine).is_instructor_scoped is True
            assert RegistrationMoveForm().is_instructor_scoped is False

        def it_detects_a_price_mismatch_against_what_the_student_paid():
            mine = InstructorFactory(instructor_slug="mv-price")
            _bookable_for(mine, "pr-a", price_cents=5000)
            form = RegistrationMoveForm(instructor=mine)
            assert form.any_target_price_differs(4000) is True
            assert form.any_target_price_differs(5000) is False
