"""BDD specs for RegistrationMoveForm."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory, InstructorFactory
from classes.forms import RegistrationMoveForm
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db


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

    def describe_instructor_scoping():
        def it_offers_only_that_instructors_upcoming_classes():
            mine = InstructorFactory(instructor_slug="mv-mine")
            current = ClassOfferingFactory(slug="ins-cur", instructor=mine)
            my_other = ClassOfferingFactory(slug="ins-other", instructor=mine)
            my_past = ClassOfferingFactory(slug="ins-past", instructor=mine)
            ClassSessionFactory(class_offering=my_past, starts_at=timezone.now() - timedelta(days=1))
            not_mine = ClassOfferingFactory(slug="ins-foreign")
            form = RegistrationMoveForm(current=current, instructor=mine)
            choices = list(form.fields["target"].queryset)
            assert choices == [my_other]
            assert not_mine not in choices
            assert my_past not in choices

        def it_rejects_a_crafted_post_at_a_class_they_do_not_instruct():
            mine = InstructorFactory(instructor_slug="mv-craft")
            current = ClassOfferingFactory(slug="ins-craft-cur", instructor=mine)
            foreign = ClassOfferingFactory(slug="ins-craft-foreign")
            form = RegistrationMoveForm({"target": foreign.pk}, current=current, instructor=mine)
            assert not form.is_valid()

    def describe_has_targets():
        def it_is_true_when_a_class_can_be_picked():
            ClassOfferingFactory(slug="ht-yes")
            form = RegistrationMoveForm()
            assert form.has_targets is True

        def it_is_false_when_the_only_class_is_the_current_one():
            mine = InstructorFactory(instructor_slug="mv-lonely")
            current = ClassOfferingFactory(slug="ht-cur", instructor=mine)
            form = RegistrationMoveForm(current=current, instructor=mine)
            assert form.has_targets is False
