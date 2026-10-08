"""factory-boy factories for core models."""

from __future__ import annotations

import factory

from core.models import FeedbackRequest
from tests.membership.factories import UserFactory


class FeedbackRequestFactory(factory.django.DjangoModelFactory):
    """A feature request sent from the Feedback page, still Received (#693)."""

    class Meta:
        model = FeedbackRequest

    user = factory.SubFactory(UserFactory)
    category = FeedbackRequest.Category.FEATURE
    subject = factory.Sequence(lambda n: f"Request number {n}")
    message = "Please add a way to do the thing."
