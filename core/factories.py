"""factory-boy factories for the core app."""

from __future__ import annotations

import secrets

import factory
from django.contrib.auth import get_user_model

from core.models import CopyReviewComment, ScheduledJobState, ScheduledTaskRun, UserProfile
from core.scheduled_jobs import Trigger

User = get_user_model()


class CoreUserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ("username",)

    username = factory.Sequence(lambda n: f"profileuser{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")


class UserProfileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UserProfile

    user = factory.SubFactory(CoreUserFactory)
    preferred_name = ""
    pronouns = ""
    phone = ""


class ScheduledTaskRunFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ScheduledTaskRun

    task_key = factory.Sequence(lambda n: f"job_{n}")
    status = ScheduledTaskRun.Status.OK
    trigger = Trigger.SCHEDULED


class ScheduledJobStateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ScheduledJobState
        django_get_or_create = ("task_key",)

    task_key = factory.Sequence(lambda n: f"job_{n}")
    enabled = True


# TEMPORARY — remove on/after 2026-08-10. Copy-review gallery anonymous comments.
class CopyReviewCommentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CopyReviewComment

    section_key = factory.Sequence(lambda n: f"public-account--overview-{n}")
    author_name = factory.Sequence(lambda n: f"Reviewer {n}")
    body = "The heading copy reads well; consider softening the button label."
    edit_token = factory.LazyFunction(lambda: secrets.token_hex(16))
