"""BDD specs for ClassSessionQuerySet's public visibility gate.

``public()`` is the date-free gate ``upcoming_public()`` and ``public_between()`` both
delegate to, so a class that must stay off the public catalog stays off the signage wall
too, without the rule being written twice.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassSession
from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db


def _session(offset: timedelta, **offering_fields) -> ClassSession:
    defaults = {"status": "published", "is_private": False}
    offering = ClassOfferingFactory(**{**defaults, **offering_fields})
    start = timezone.now() + offset
    return ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))


def describe_public_between():
    def it_includes_a_past_session_inside_the_window():
        past = _session(timedelta(days=-5))
        window = ClassSession.objects.public_between(timezone.now() - timedelta(days=10), timezone.now())
        assert list(window) == [past]

    def it_includes_a_future_session_inside_the_window():
        future = _session(timedelta(days=3))
        window = ClassSession.objects.public_between(timezone.now(), timezone.now() + timedelta(days=10))
        assert list(window) == [future]

    def it_excludes_a_session_outside_the_window():
        _session(timedelta(days=40))
        window = ClassSession.objects.public_between(timezone.now(), timezone.now() + timedelta(days=10))
        assert list(window) == []

    def it_excludes_an_unpublished_class():
        _session(timedelta(days=-5), status="draft")
        window = ClassSession.objects.public_between(timezone.now() - timedelta(days=10), timezone.now())
        assert list(window) == []

    def it_excludes_a_private_class():
        _session(timedelta(days=-5), is_private=True)
        window = ClassSession.objects.public_between(timezone.now() - timedelta(days=10), timezone.now())
        assert list(window) == []

    def it_excludes_a_demo_class_while_the_demo_setting_is_off():
        _session(timedelta(days=-5), slug="demo-lathe")
        window = ClassSession.objects.public_between(timezone.now() - timedelta(days=10), timezone.now())
        assert list(window) == []

    def describe_when_demo_classes_are_displayed():
        def it_includes_a_demo_class():
            demo = _session(timedelta(days=-5), slug="demo-lathe")
            config = SiteConfiguration.load()
            config.display_demo_classes = True
            config.save()
            window = ClassSession.objects.public_between(timezone.now() - timedelta(days=10), timezone.now())
            assert list(window) == [demo]


def describe_upcoming_public():
    def it_still_excludes_the_past():
        _session(timedelta(days=-5))
        future = _session(timedelta(days=5))
        assert list(ClassSession.objects.upcoming_public()) == [future]

    def it_still_excludes_a_private_or_unpublished_class():
        _session(timedelta(days=1), status="draft")
        _session(timedelta(days=1), is_private=True)
        assert list(ClassSession.objects.upcoming_public()) == []
