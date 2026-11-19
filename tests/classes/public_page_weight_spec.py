"""A signed out class page carries no changelog (#1259): it was 90% of the page. Members keep it."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

CLASSES = "classes.pastlives.space"


@pytest.fixture(autouse=True)
def _hosts(settings) -> None:
    settings.ALLOWED_HOSTS = [CLASSES, "testserver"]
    settings.PUBLIC_HOSTS = [CLASSES]


@pytest.fixture
def offering() -> ClassOffering:
    offering = ClassOfferingFactory(title="Forge Basics", slug="forge-basics", status=ClassOffering.Status.PUBLISHED)
    start = timezone.now() + timedelta(days=5)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def describe_a_class_page():
    def it_carries_no_changelog_signed_out_and_stays_under_150_kb(client: Client, offering: ClassOffering):
        response = client.get("/classes/forge-basics/", HTTP_HOST=CLASSES)

        assert response.status_code == 200
        assert b"data-changelog-entry" not in response.content
        assert len(response.content) < 150_000

    def it_keeps_whats_new_for_a_signed_in_member(client: Client, offering: ClassOffering):
        MembershipPlanFactory()
        client.force_login(User.objects.create_user(username="maker", password="pw", email="maker@example.com"))

        response = client.get("/classes/forge-basics/")

        assert response.status_code == 200
        assert b"data-changelog-entry" in response.content
