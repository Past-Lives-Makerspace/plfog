"""Specs for classes on the portal activity log (#1330, AC 5).

Submitted, approved, published and cancelled each post one line naming the class; a
registration and a cancelled registration each post naming the class and the registrant,
never the registrant's email. Each links to the class in the teaching portal.

Each line names the person who acted: the instructor who submitted, the reviewer who approved
(signed in, or the guild lead through the emailed link). An approval publishes the class, and
posts one line for both.
"""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
import respx
from django.urls import reverse
from django.utils import timezone

from classes import activity as cms_activity
from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassApproval, ClassOffering, CmsActivity, Registration
from core.models import SiteActivity, SiteConfiguration
from membership.models import Member
from tests.membership.factories import GuildFactory

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
            (CmsActivity.Kind.CLASS_APPROVED, "Class approved and published"),
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
        assert _contents(route) == [f"{headline}: Raku Firing · <{link}>"]

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


def _member(email: str, preferred: str) -> Member:
    member = Member.objects.get(user=UserFactory(username=email, email=email))
    member.preferred_name = preferred
    member.save(update_fields=["preferred_name"])
    return member


def _guilded_pending(lead: Member, *, dates: int = 1) -> ClassOffering:
    offering = ClassOfferingFactory(
        title="Hand Embroidery 101",
        ready=True,
        status=ClassOffering.Status.DRAFT,
        category=CategoryFactory(guild=GuildFactory(name="Textiles", guild_lead=lead)),
    )
    for day in range(1, dates):
        start = timezone.now() + timedelta(days=20 + day)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    offering.submit_for_review()
    return offering


def describe_who_acted():
    @respx.mock
    def it_names_the_guild_lead_who_approved_from_the_emailed_link_signed_out(django_capture_on_commit_callbacks):
        offering = _guilded_pending(_member("lead@example.com", "Wren"))
        route = _turn_on()
        gate = offering.approvals.get()
        with django_capture_on_commit_callbacks(execute=True):
            assert gate.decide(ClassApproval.Decision.APPROVED) is True
        [content] = _contents(route)
        assert content.startswith("Class approved and published: Hand Embroidery 101 · Wren · <")
        offering.refresh_from_db()
        assert offering.approved_by == gate.opened_for_lead.user

    @respx.mock
    def it_names_the_signed_in_reviewer_who_approved(django_capture_on_commit_callbacks):
        lead = _member("signed-in-lead@example.com", "Wren")
        offering = _guilded_pending(lead)
        route = _turn_on()
        with django_capture_on_commit_callbacks(execute=True):
            offering.approvals.get().decide(ClassApproval.Decision.APPROVED, user=lead.user)
        assert [content.split(" · ")[:2] for content in _contents(route)] == [
            ["Class approved and published: Hand Embroidery 101", "Wren"]
        ]

    @respx.mock
    def it_posts_one_line_for_an_approval_that_splits_a_class_into_runs(django_capture_on_commit_callbacks):
        offering = _guilded_pending(_member("split-lead@example.com", "Wren"), dates=3)
        route = _turn_on()
        with django_capture_on_commit_callbacks(execute=True):
            offering.approvals.get().decide(ClassApproval.Decision.APPROVED)
        assert ClassOffering.objects.filter(grouping_key=offering.grouping_key).count() == 3
        assert [content.split(" · ")[0] for content in _contents(route)] == [
            "Class approved and published: Hand Embroidery 101"
        ]
        # The feed still shows every publish; only Discord gets the one line.
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.CLASS_PUBLISHED).count() == 3

    @respx.mock
    def it_still_posts_a_direct_publish_naming_who_published(admin_user, django_capture_on_commit_callbacks):
        admin_user.member.preferred_name = "Felix"
        admin_user.member.save(update_fields=["preferred_name"])
        offering = ClassOfferingFactory(title="Raku Firing", ready=True, status=ClassOffering.Status.DRAFT)
        route = _turn_on()
        with django_capture_on_commit_callbacks(execute=True):
            offering.publish(admin_user)
        assert [content.split(" · ")[:2] for content in _contents(route)] == [["Class published: Raku Firing", "Felix"]]

    @respx.mock
    def it_names_the_instructor_who_submitted(client, django_capture_on_commit_callbacks):
        instructor = InstructorFactory(user=UserFactory(username="teacher@example.com"), preferred_name="Juno")
        draft = ClassOfferingFactory(
            title="Raku Firing", ready=True, instructor=instructor, status=ClassOffering.Status.DRAFT
        )
        route = _turn_on()
        client.force_login(instructor.user)
        with django_capture_on_commit_callbacks(execute=True):
            client.post(reverse("classes:teach_class_submit", kwargs={"pk": draft.pk}))
        assert [content.split(" · ")[:2] for content in _contents(route)] == [["Class submitted: Raku Firing", "Juno"]]

    def it_names_nobody_when_no_reviewer_was_recorded():
        assert ClassApproval(role=ClassApproval.Role.ADMIN).decider is None
