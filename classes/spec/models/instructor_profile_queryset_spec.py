"""BDD specs for the instructor-profile querysets behind the bio page and modal."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory, InstructorFactory
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db


def _published(instructor, **kwargs):
    kwargs.setdefault("status", ClassOffering.Status.PUBLISHED)
    return ClassOfferingFactory(instructor=instructor, **kwargs)


def _session(offering, days):
    start = timezone.now() + timedelta(days=days)
    return ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))


def describe_public_upcoming_for_instructor():
    def it_includes_a_class_with_a_future_session(db):
        instructor = InstructorFactory()
        offering = _published(instructor)
        _session(offering, 7)

        assert list(ClassOffering.objects.public_upcoming_for_instructor(instructor)) == [offering]

    def it_includes_a_flexible_class_with_no_sessions(db):
        instructor = InstructorFactory()
        offering = _published(instructor, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)

        assert list(ClassOffering.objects.public_upcoming_for_instructor(instructor)) == [offering]

    def it_excludes_a_dated_class_whose_sessions_have_all_passed(db):
        instructor = InstructorFactory()
        offering = _published(instructor)
        _session(offering, -7)

        assert list(ClassOffering.objects.public_upcoming_for_instructor(instructor)) == []

    def it_excludes_another_instructors_class(db):
        instructor = InstructorFactory()
        other = InstructorFactory()
        _session(_published(other), 7)

        assert list(ClassOffering.objects.public_upcoming_for_instructor(instructor)) == []

    def it_excludes_a_private_class(db):
        instructor = InstructorFactory()
        offering = _published(instructor, is_private=True)
        _session(offering, 7)

        assert list(ClassOffering.objects.public_upcoming_for_instructor(instructor)) == []

    def it_orders_by_the_next_session(db):
        instructor = InstructorFactory()
        later = _published(instructor)
        _session(later, 30)
        sooner = _published(instructor)
        _session(sooner, 3)

        assert list(ClassOffering.objects.public_upcoming_for_instructor(instructor)) == [sooner, later]


def describe_archived_for_instructor():
    def it_returns_only_archived_classes_for_that_instructor(db):
        instructor = InstructorFactory()
        archived = _published(instructor, status=ClassOffering.Status.ARCHIVED)
        _session(_published(instructor), 7)  # a live class — not "past"
        _published(InstructorFactory(), status=ClassOffering.Status.ARCHIVED)  # someone else's

        assert list(ClassOffering.objects.archived_for_instructor(instructor)) == [archived]
