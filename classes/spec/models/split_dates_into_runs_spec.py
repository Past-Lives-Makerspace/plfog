"""BDD specs for #823: a single class published with several dates becomes one run per date.

``ClassOffering.publish`` splits the class (``split_dates_into_runs``): it keeps its earliest
date and every later date moves onto a published run of the same class. A series, a flexible
class, a class with one date and a class someone holds a seat on are never split.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from django.utils import timezone

from classes.factories import (
    READY_DESCRIPTION,
    ClassFaqFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    SeriesClassOfferingFactory,
    UserFactory,
)
from classes.models import ClassOffering, ClassSession, CmsActivity, Registration
from core.models import SiteConfiguration
from hub import discord_class_posts as dcp

pytestmark = pytest.mark.django_db

CHANNEL_ID = "946149249178021949"
_MESSAGES_URL = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages"
DAYS = (10, 31, 45, 59)


def _single(*days: int, **kwargs: Any) -> ClassOffering:
    """A ready draft single class with one session ``days`` out per entry."""
    offering = ClassOfferingFactory(
        title="Make a Sushi Set",
        slug="make-a-sushi-set",
        description=READY_DESCRIPTION,
        price_cents=7500,
        capacity=5,
        **kwargs,
    )
    for day in days:
        start = timezone.now() + timedelta(days=day)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=3))
    return offering


def _runs(offering: ClassOffering) -> list[ClassOffering]:
    return list(
        ClassOffering.objects.filter(grouping_key=offering.grouping_key)
        .exclude(pk=offering.pk)
        .order_by("sessions__starts_at")
    )


def _session_starts(offering: ClassOffering) -> list[Any]:
    return list(ClassSession.objects.filter(class_offering=offering).values_list("starts_at", flat=True))


def describe_publishing_a_single_class_with_several_dates():
    @pytest.fixture
    def published() -> tuple[ClassOffering, list[Any], Any]:
        first, second = InstructorFactory(), InstructorFactory()
        offering = _single(*DAYS, instructors=[first, second])
        ClassFaqFactory(class_offering=offering, question="What do I take home?", answer="A sushi set.")
        starts = sorted(_session_starts(offering))
        actor = UserFactory()
        offering.publish(actor)
        return offering, starts, actor

    def it_keeps_the_earliest_date_on_the_class(published):
        offering, starts, _ = published
        assert _session_starts(offering) == [starts[0]]
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED

    def it_makes_one_published_run_per_later_date(published):
        offering, starts, actor = published
        runs = _runs(offering)
        assert [_session_starts(run) for run in runs] == [[start] for start in starts[1:]]
        for run in runs:
            assert run.status == ClassOffering.Status.PUBLISHED
            assert run.published_at is not None
            assert run.approved_by == actor

    def it_carries_what_run_it_again_carries(published):
        offering, _, _ = published
        runs = _runs(offering)
        assert len(runs) == 3
        for run in runs:
            assert run.title == offering.title
            assert run.grouping_key == offering.grouping_key
            assert run.price_cents == 7500
            assert run.capacity == 5
            assert run.instructor_list == offering.instructor_list
            assert run.gallery_images.count() == offering.gallery_images.count() == 1
            assert run.own_faqs() == [{"question": "What do I take home?", "answer": "A sushi set."}]

    def it_logs_each_run_as_published_without_review(published):
        offering, _, actor = published
        for run in _runs(offering):
            kinds = set(CmsActivity.objects.filter(class_offering=run).values_list("kind", flat=True))
            assert CmsActivity.Kind.CLASS_PUBLISHED in kinds
            assert not run.approvals.exists()
            assert CmsActivity.objects.get(class_offering=run, kind=CmsActivity.Kind.CLASS_PUBLISHED).actor == actor

    def it_stamps_the_runs_announced_and_leaves_the_class_for_the_announcer(published):
        offering, _, _ = published
        offering.refresh_from_db()
        assert offering.channel_announced_at is None
        assert all(run.channel_announced_at is not None for run in _runs(offering))

    def it_lets_each_run_take_its_own_seats(published):
        offering, _, _ = published
        third = _runs(offering)[1]
        RegistrationFactory(class_offering=third, status=Registration.Status.CONFIRMED)
        assert third.spots_remaining == 4
        assert offering.spots_remaining == 5
        assert [run.spots_remaining for run in _runs(offering) if run.pk != third.pk] == [5, 5]


def describe_a_class_already_announced():
    def it_copies_the_announcement_stamp_onto_the_runs():
        offering = _single(10, 20)
        announced = timezone.now() - timedelta(days=3)
        offering.channel_announced_at = announced
        offering.save(update_fields=["channel_announced_at"])
        offering.publish(UserFactory())
        assert [run.channel_announced_at for run in _runs(offering)] == [announced]


def describe_classes_that_are_never_split():
    def it_leaves_a_class_with_a_seat_holding_registration_alone():
        offering = _single(*DAYS)
        RegistrationFactory(class_offering=offering, status=Registration.Status.WAITLISTED)
        offering.publish(UserFactory())
        assert len(_session_starts(offering)) == 4
        assert _runs(offering) == []
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED

    def it_still_splits_when_the_only_registration_was_cancelled():
        offering = _single(*DAYS)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CANCELLED)
        offering.publish(UserFactory())
        assert len(_runs(offering)) == 3

    def it_leaves_a_series_alone():
        offering = SeriesClassOfferingFactory(description=READY_DESCRIPTION, session_count=4)
        offering.publish(UserFactory())
        assert offering.sessions.count() == 4
        assert _runs(offering) == []

    def it_leaves_a_flexible_class_alone():
        offering = _single(*DAYS, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        assert offering.split_dates_into_runs(UserFactory()) == []
        assert len(_session_starts(offering)) == 4

    def it_publishes_a_one_date_class_exactly_as_before():
        offering = _single(10)
        actor = UserFactory()
        offering.publish(actor)
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED
        assert offering.approved_by == actor
        assert len(_session_starts(offering)) == 1
        assert ClassOffering.objects.count() == 1


def describe_a_split_that_fails():
    def it_leaves_the_class_unsplit_and_unpublished(monkeypatch):
        offering = _single(*DAYS)
        real = ClassOffering.duplicate_as_new_run
        calls = {"n": 0}

        def flaky(self: ClassOffering) -> ClassOffering:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("database went away")
            return real(self)

        monkeypatch.setattr(ClassOffering, "duplicate_as_new_run", flaky)
        with pytest.raises(RuntimeError):
            offering.publish(UserFactory())
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.DRAFT
        assert ClassOffering.objects.count() == 1
        assert len(_session_starts(offering)) == 4


def describe_the_classes_channel():
    @respx.mock
    def it_posts_the_split_class_once(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        config = SiteConfiguration.load()
        config.discord_classes_posts_enabled = True
        config.discord_classes_channel_id = CHANNEL_ID
        config.save(update_fields=["discord_classes_posts_enabled", "discord_classes_channel_id"])
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        offering = _single(*DAYS)
        offering.publish(UserFactory())

        assert dcp.announce_new_classes() == 1
        embeds = [embed for call in route.calls for embed in json.loads(call.request.content)["embeds"]]
        assert route.call_count == 1  # one post; its photo embeds share the class url (#879)
        assert embeds[0]["url"] == offering.public_url
        assert dcp.announce_new_classes() == 0


def _enable_class_posts(settings: Any) -> respx.Route:
    settings.DISCORD_BOT_TOKEN = "tok"
    config = SiteConfiguration.load()
    config.discord_classes_posts_enabled = True
    config.discord_classes_channel_id = CHANNEL_ID
    config.save(update_fields=["discord_classes_posts_enabled", "discord_classes_channel_id"])
    return respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))


def describe_a_class_whose_first_date_has_passed():
    @respx.mock
    def it_posts_once_for_the_earliest_future_run(settings):
        route = _enable_class_posts(settings)
        offering = _single(-6, 8, 22)
        offering.publish(UserFactory())
        first_run, second_run = _runs(offering)

        assert first_run.channel_announced_at is None
        assert second_run.channel_announced_at is not None
        assert dcp.announce_new_classes() == 1
        embeds = [embed for call in route.calls for embed in json.loads(call.request.content)["embeds"]]
        assert route.call_count == 1  # one post; its photo embeds share the class url (#879)
        assert embeds[0]["url"] == first_run.public_url
        assert dcp.announce_new_classes() == 0

    def it_hands_an_announced_class_stamp_to_every_run():
        offering = _single(-6, 8, 22)
        announced = timezone.now() - timedelta(days=10)
        offering.channel_announced_at = announced
        offering.save(update_fields=["channel_announced_at"])
        offering.publish(UserFactory())
        assert [run.channel_announced_at for run in _runs(offering)] == [announced, announced]


def describe_an_admin_approval_whose_split_fails():
    def it_leaves_the_gate_undecided_and_the_class_pending(monkeypatch):
        offering = _single(*DAYS)
        (row,) = offering.submit_for_review()
        assert row.role == row.Role.ADMIN

        def broken(self: ClassOffering, actor: Any) -> list[ClassOffering]:
            raise RuntimeError("split failed")

        monkeypatch.setattr(ClassOffering, "split_dates_into_runs", broken)
        with pytest.raises(RuntimeError):
            row.decide(row.Decision.APPROVED, user=UserFactory())
        row.refresh_from_db()
        offering.refresh_from_db()
        assert row.decision == ""
        assert offering.status == ClassOffering.Status.PENDING
        assert offering.published_at is None
