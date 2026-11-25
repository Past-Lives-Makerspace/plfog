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
from classes.models import CmsActivity, Registration
from core.models import SiteActivity, SiteConfiguration

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
            status=Registration.Status.CONFIRMED,
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


def describe_registration_lifecycle():
    @respx.mock
    def it_posts_a_free_seat_as_registered_when_it_is_made(django_capture_on_commit_callbacks):
        route = _turn_on()
        with django_capture_on_commit_callbacks(execute=True):
            RegistrationFactory(
                class_offering=ClassOfferingFactory(title="Raku Firing"), status=Registration.Status.CONFIRMED
            )
        assert [content.split(" · ")[0] for content in _contents(route)] == ["Registered for class: Raku Firing"]

    @respx.mock
    def it_posts_a_paid_seat_only_once_the_payment_confirms_it(django_capture_on_commit_callbacks):
        route = _turn_on()
        with django_capture_on_commit_callbacks(execute=True):
            registration = RegistrationFactory(
                class_offering=ClassOfferingFactory(title="Raku Firing"), status=Registration.Status.PENDING
            )
        assert not route.called
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.CLASS_REGISTERED).exists()
        registration.status = Registration.Status.CONFIRMED
        with django_capture_on_commit_callbacks(execute=True):
            registration.save()
        assert [content.split(" · ")[0] for content in _contents(route)] == ["Registered for class: Raku Firing"]
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.CLASS_REGISTERED).count() == 1

    @respx.mock
    def it_posts_nothing_for_an_abandoned_checkout(django_capture_on_commit_callbacks):
        route = _turn_on()
        with django_capture_on_commit_callbacks(execute=True):
            registration = RegistrationFactory(status=Registration.Status.PENDING)
        with django_capture_on_commit_callbacks(execute=True):
            assert registration.release_hold(reason="Checkout expired") is True
        assert not route.called
        assert not SiteActivity.objects.filter(target_id=registration.pk).exists()

    @respx.mock
    def it_posts_the_cancel_of_a_confirmed_seat(django_capture_on_commit_callbacks):
        route = _turn_on()
        registration = RegistrationFactory(
            class_offering=ClassOfferingFactory(title="Raku Firing"), status=Registration.Status.CONFIRMED
        )
        with django_capture_on_commit_callbacks(execute=True):
            registration.cancel(reason="Can't make it")
        assert [content.split(" · ")[0] for content in _contents(route)] == ["Cancelled registration: Raku Firing"]


def describe_waitlist_promotion():
    @respx.mock
    def it_posts_a_promoted_seat_as_registered_exactly_once_then_its_cancel(django_capture_on_commit_callbacks):
        route = _turn_on()
        registration = RegistrationFactory(
            class_offering=ClassOfferingFactory(title="Raku Firing"), status=Registration.Status.WAITLISTED
        )
        with django_capture_on_commit_callbacks(execute=True):
            registration.promote_from_waitlist(actor=None)
        registration.refresh_from_db()
        assert registration.status == Registration.Status.CONFIRMED
        with django_capture_on_commit_callbacks(execute=True):
            registration.save()  # a later save of the confirmed seat confirms nothing again
        with django_capture_on_commit_callbacks(execute=True):
            registration.cancel(reason="Can't make it")
        assert [content.split(" · ")[0] for content in _contents(route)] == [
            "Registered for class: Raku Firing",
            "Cancelled registration: Raku Firing",
        ]
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.CLASS_REGISTERED).count() == 1

    @respx.mock
    def it_posts_a_waitlister_who_claims_their_seat_once_through_the_ordinary_confirm(
        django_capture_on_commit_callbacks,
    ):
        route = _turn_on()
        registration = RegistrationFactory(
            class_offering=ClassOfferingFactory(title="Raku Firing"), status=Registration.Status.WAITLISTED
        )
        registration.status = Registration.Status.CONFIRMED
        with django_capture_on_commit_callbacks(execute=True):
            registration.save()
        assert [content.split(" · ")[0] for content in _contents(route)] == ["Registered for class: Raku Firing"]
