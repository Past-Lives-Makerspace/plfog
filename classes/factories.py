"""factory-boy factories for classes models."""

from __future__ import annotations

from datetime import timedelta

import factory
from django.contrib.auth import get_user_model
from django.utils import timezone
from factory.django import DjangoModelFactory

from classes import models
from membership.models import Member, MembershipPlan


class _MembershipPlanFactory(DjangoModelFactory):
    class Meta:
        model = MembershipPlan
        django_get_or_create = ("name",)

    name = "Standard"
    monthly_price = "50.00"


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
    """Creates a Member configured as an instructor (instructor_slug set).

    When ``user=`` is supplied the signal will have already auto-created a
    Member for that user. In that case we find and update it so the test isn't
    left with two conflicting records.
    """

    class Meta:
        model = Member

    membership_plan = factory.SubFactory(_MembershipPlanFactory)
    full_legal_name = factory.Sequence(lambda n: f"Instructor {n}")
    _pre_signup_email = factory.Sequence(lambda n: f"instructor{n}@example.com")
    instructor_slug = factory.LazyAttribute(lambda o: o.full_legal_name.lower().replace(" ", "-"))
    about_me = "A great teacher."
    status = Member.Status.ACTIVE
    user = None

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        user = kwargs.get("user")
        if user is not None:
            # The signal auto-created a Member when the User was saved. Update it.
            member, _ = Member.objects.update_or_create(
                user=user,
                defaults={k: v for k, v in kwargs.items() if k != "user"},
            )
            return member
        return super()._create(model_class, *args, **kwargs)


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
