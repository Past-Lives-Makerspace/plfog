"""Specs for classes on the portal activity log (#1330, AC 5).

Submitted, approved, published and cancelled each post one line naming the class; a
registration and a cancelled registration each post naming the class and the registrant,
never the registrant's email. Each links to the class in the teaching portal.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from django.urls import reverse

from classes import activity as cms_activity
from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import CmsActivity
from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db

_WEBHOOK = "https://discord.com/api/webhooks/1330/activity"


def _turn_on() -> respx.Route:
    config = SiteConfiguration.load()
    config.discord_activity_webhook_url = _WEBHOOK
    config.save()
    return respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))


def _contents(route: respx.Route) -> list[str]:
    return [json.loads(call.request.content)["content"] for call in route.calls]


def describe_class_posts():
    @respx.mock
    @pytest.mark.parametrize(
        ("cms_kind", "headline"),
        [
            (CmsActivity.Kind.CLASS_SUBMITTED, "Class submitted"),
            (CmsActivity.Kind.CLASS_APPROVED, "Class approved"),
            (CmsActivity.Kind.CLASS_PUBLISHED, "Class published"),
            (CmsActivity.Kind.CLASS_CANCELLED, "Class cancelled"),
        ],
    )
    def it_posts_each_class_happening_naming_the_class(
        cms_kind, headline, django_capture_on_commit_callbacks, settings
    ):
        settings.MEMBER_BASE_URL = "https://members.example"
        route = _turn_on()
        offering = ClassOfferingFactory(title="Raku Firing")
        with django_capture_on_commit_callbacks(execute=True):
            cms_activity.log(cms_kind, class_offering=offering)
        link = "https://members.example" + reverse("classes:teach_class_detail", args=[offering.pk])
        assert _contents(route) == [f"{headline}: Raku Firing · System · <{link}>"]

    @respx.mock
    @pytest.mark.parametrize(
        ("cms_kind", "headline"),
        [
            (CmsActivity.Kind.REGISTRATION_CREATED, "Registered for class"),
            (CmsActivity.Kind.REGISTRATION_CANCELLED, "Cancelled registration"),
        ],
    )
    def it_posts_a_registration_naming_the_class_and_registrant_not_their_email(
        cms_kind, headline, django_capture_on_commit_callbacks, settings
    ):
        settings.MEMBER_BASE_URL = "https://members.example"
        route = _turn_on()
        registration = RegistrationFactory(
            class_offering=ClassOfferingFactory(title="Raku Firing"),
            first_name="Juno",
            last_name="Reyes",
            email="juno@example.com",
        )
        with django_capture_on_commit_callbacks(execute=True):
            cms_activity.log(cms_kind, class_offering=registration.class_offering, registration=registration)
        link = "https://members.example" + reverse(
            "classes:teach_class_registrations", args=[registration.class_offering_id]
        )
        [content] = _contents(route)
        assert content.startswith(f"{headline}: Raku Firing · Juno")
        assert content.endswith(f" · <{link}>")
        assert "juno@example.com" not in content

    @respx.mock
    def it_posts_nothing_for_a_quiet_archive(django_capture_on_commit_callbacks):
        route = _turn_on()
        with django_capture_on_commit_callbacks(execute=True):
            cms_activity.log(CmsActivity.Kind.CLASS_ARCHIVED, class_offering=ClassOfferingFactory())
        assert not route.called
