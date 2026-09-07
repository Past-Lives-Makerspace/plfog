"""factory-boy factories for classes models."""

from __future__ import annotations

from datetime import timedelta

import factory
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import DjangoModelFactory, mute_signals

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


@mute_signals(post_save)
class InstructorFactory(DjangoModelFactory):
    """Creates a Member configured as an instructor (instructor_slug set).

    When ``user=`` is supplied the signal will have already auto-created a
    Member for that user. In that case we find and update it so the test isn't
    left with two conflicting records.

    ``post_save`` is muted so the ``auto_provision_member_user`` signal does not
    auto-provision a User for a default (``user=None``) instructor — instructors
    stay unlinked unless a test supplies a user, matching prior behaviour.
    """

    class Meta:
        model = Member

    membership_plan = factory.SubFactory(_MembershipPlanFactory)
    full_legal_name = factory.Sequence(lambda n: f"Instructor {n}")
    _pre_signup_email = factory.Sequence(lambda n: f"instructor{n}@example.com")
    instructor_slug = factory.LazyAttribute(lambda o: o.full_legal_name.lower().replace(" ", "-"))
    about_me = "A great teacher."
    status = Member.Status.ACTIVE
    # Slug holders are grandfathered into the teaching unlock by migration 0110;
    # the factory mirrors that so instructor fixtures pass the teach-portal gate.
    instructor_oriented_at = factory.LazyFunction(timezone.now)
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


READY_DESCRIPTION = "A hands-on class where you build a real project, learn the tools safely, and take your work home."


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
    scheduling_type = models.ClassOffering.SchedulingType.SINGLE_SESSION
    # Submitting a class for review requires its own hero photo AND one gallery
    # photo (see ClassOffering.has_submittable_image), so a factory-built offering
    # carries both by default. Pass image="" to model a class with no hero of its
    # own (e.g. category-hero-fallback tests) and gallery=0 for one with no
    # gallery (empty-state or exact-count tests).
    image = factory.django.ImageField(width=4, height=4, color="blue")

    @factory.post_generation
    def gallery(obj, create, extracted, **kwargs):  # noqa: N805  # factory-boy hook, obj is the instance
        if not create:
            return
        count = 1 if extracted is None else extracted
        for i in range(count):
            ClassImageFactory(class_offering=obj, sort_order=i)

    @factory.post_generation
    def ready(obj, create, extracted, **kwargs):  # noqa: N805  # factory-boy hook, obj is the instance
        """``ready=True`` makes the class pass every readiness check (``ClassOffering.readiness``).

        Adds a real description and one session two days out on top of the default hero,
        gallery photo, and capacity. Specs that submit or publish a class use it; everything
        else keeps the lean default so unrelated suites never gain a session.
        """
        if not create or not extracted:
            return
        obj.description = READY_DESCRIPTION
        obj.save(update_fields=["description"])
        start = timezone.now() + timedelta(days=2)
        ClassSessionFactory(class_offering=obj, starts_at=start, ends_at=start + timedelta(hours=2))


class SeriesClassOfferingFactory(ClassOfferingFactory):
    """A series-package offering. Pass ``session_count=N`` to attach N weekly sessions."""

    class Meta:
        model = models.ClassOffering
        skip_postgeneration_save = True

    scheduling_type = models.ClassOffering.SchedulingType.SERIES_PACKAGE

    @factory.post_generation
    def session_count(self, create, extracted, **kwargs):
        if not create or not extracted:
            return
        # Start one week out so every session is upcoming (views filter on
        # ``starts_at >= now``); spacing one week apart mirrors a real course.
        base = timezone.now() + timedelta(days=7)
        for i in range(extracted):
            ClassSessionFactory(
                class_offering=self,
                starts_at=base + timedelta(days=7 * i),
                ends_at=base + timedelta(days=7 * i, hours=2),
            )


class ClassImageFactory(DjangoModelFactory):
    class Meta:
        model = models.ClassImage

    class_offering = factory.SubFactory(ClassOfferingFactory)
    image = factory.django.ImageField(width=4, height=4, color="red")
    alt_text = ""
    sort_order = 0


class ClassFaqFactory(DjangoModelFactory):
    class Meta:
        model = models.ClassFaq

    class_offering = factory.SubFactory(ClassOfferingFactory)
    question = factory.Sequence(lambda n: f"Question {n}?")
    answer = "An answer."
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
    # Codes default to unapproved on the model (approval is a deliberate act); the
    # factory approves by default so fixtures represent a usable code. Tests that
    # exercise the pending state pass ``is_approved=False`` explicitly.
    is_approved = True


class RegistrationFactory(DjangoModelFactory):
    class Meta:
        model = models.Registration

    class_offering = factory.SubFactory(ClassOfferingFactory)
    first_name = "Test"
    last_name = "User"
    email = factory.Sequence(lambda n: f"test{n}@example.com")
    amount_paid_cents = 0
