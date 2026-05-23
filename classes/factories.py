"""factory-boy factories for classes models."""

from __future__ import annotations

from datetime import timedelta

import factory
from django.contrib.auth import get_user_model
from django.utils import timezone
from factory.django import DjangoModelFactory

from classes import models


class CategoryFactory(DjangoModelFactory):
    class Meta:
        model = models.Category

    name = factory.Sequence(lambda n: f"Category {n}")
    slug = factory.LazyAttribute(lambda o: o.name.lower().replace(" ", "-"))
    sort_order = 0


class UserFactory(DjangoModelFactory):
    class Meta:
        model = get_user_model()
        django_get_or_create = ("username",)

    username = factory.Sequence(lambda n: f"user{n}@example.com")
    email = factory.LazyAttribute(lambda o: o.username)


class InstructorFactory(DjangoModelFactory):
    class Meta:
        model = models.Instructor

    user = factory.SubFactory(UserFactory)
    display_name = factory.Sequence(lambda n: f"Instructor {n}")
    slug = factory.LazyAttribute(lambda o: o.display_name.lower().replace(" ", "-"))
    bio = "A great teacher."
    is_active = True


class ClassOfferingFactory(DjangoModelFactory):
    class Meta:
        model = models.ClassOffering

    title = factory.Sequence(lambda n: f"Class {n}")
    slug = factory.LazyAttribute(lambda o: o.title.lower().replace(" ", "-"))
    category = factory.SubFactory(CategoryFactory)
    instructor = factory.SubFactory(InstructorFactory)
    description = "A hands-on class."
    price_cents = 5000
    member_discount_pct = 10
    capacity = 6
    status = models.ClassOffering.Status.DRAFT


class ClassImageFactory(DjangoModelFactory):
    class Meta:
        model = models.ClassImage

    class_offering = factory.SubFactory(ClassOfferingFactory)
    alt_text = ""
    sort_order = 0


class ClassSessionFactory(DjangoModelFactory):
    class Meta:
        model = models.ClassSession

    class_offering = factory.SubFactory(ClassOfferingFactory)
    starts_at = factory.LazyFunction(timezone.now)
    ends_at = factory.LazyAttribute(lambda o: o.starts_at + timedelta(hours=2))


class DiscountCodeFactory(DjangoModelFactory):
    class Meta:
        model = models.DiscountCode

    code = factory.Sequence(lambda n: f"CODE{n}")
    discount_pct = 20
    is_active = True


class RegistrationFactory(DjangoModelFactory):
    class Meta:
        model = models.Registration

    class_offering = factory.SubFactory(ClassOfferingFactory)
    first_name = "Test"
    last_name = "User"
    email = factory.Sequence(lambda n: f"test{n}@example.com")
    amount_paid_cents = 0


class RegistrationReminderFactory(DjangoModelFactory):
    class Meta:
        model = models.RegistrationReminder

    registration = factory.SubFactory(RegistrationFactory)
    session = factory.SubFactory(ClassSessionFactory)
