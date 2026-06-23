"""factory-boy factories for the core app."""

from __future__ import annotations

import factory
from django.contrib.auth import get_user_model

from core.models import UserProfile

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
