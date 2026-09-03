"""BDD specs for the Related classes heading on the public class detail page."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory
from classes.models import Category, ClassOffering

pytestmark = pytest.mark.django_db


def _bookable_offering(category: Category, title: str) -> ClassOffering:
    offering = ClassOfferingFactory(category=category, title=title, status=ClassOffering.Status.PUBLISHED)
    starts = timezone.now() + timedelta(days=7)
    ClassSessionFactory(class_offering=offering, starts_at=starts, ends_at=starts + timedelta(hours=2))
    return offering


def describe_related_classes_heading():
    def it_avoids_the_doubled_word_for_a_category_named_class(client: Client):
        # The legacy CMS sync creates a category literally named "Class"; the default
        # heading pattern would render "More Class classes".
        category = CategoryFactory(name="Class")
        offering = _bookable_offering(category, "Spooky Lanterns")
        _bookable_offering(category, "Spooky Wreaths")

        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))

        assert response.status_code == 200
        assert b'<h2 class="cp-detail__h2">More classes like this</h2>' in response.content
        assert b"More Class classes" not in response.content

    def it_names_the_category_in_the_heading_for_a_descriptive_one(client: Client):
        category = CategoryFactory(name="Woodworking")
        offering = _bookable_offering(category, "Spoon Carving")
        _bookable_offering(category, "Box Joinery")

        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))

        assert response.status_code == 200
        assert b'<h2 class="cp-detail__h2">More Woodworking classes</h2>' in response.content
