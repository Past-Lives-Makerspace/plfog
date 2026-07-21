"""BDD specs for the #classes Discord posts (weekly classes digest + new-class announcer).

HTTP is mocked with respx (never the models/DB): the service posts through the real
``core.integrations.discord_channel`` client against a mocked Discord messages endpoint.
"""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
import respx
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from core.models import SiteConfiguration
from hub import discord_calendar_posts as calendar_posts
from hub import discord_class_posts as dcp

pytestmark = pytest.mark.django_db

CHANNEL_ID = "946149249178021949"
_MESSAGES_URL = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages"


def _enable_posts(channel_id: str = CHANNEL_ID) -> SiteConfiguration:
    config = SiteConfiguration.load()
    config.discord_classes_posts_enabled = True
    config.discord_classes_channel_id = channel_id
    config.save(update_fields=["discord_classes_posts_enabled", "discord_classes_channel_id"])
    return config


def _published_class(title: str, *, days: float = 2, **kwargs) -> ClassOffering:
    """A publicly bookable FIXED offering with one upcoming session ``days`` out."""
    offering = ClassOfferingFactory(title=title, status=ClassOffering.Status.PUBLISHED, **kwargs)
    start = timezone.now() + timedelta(days=days)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def _flexible_class(title: str, **kwargs) -> ClassOffering:
    return ClassOfferingFactory(
        title=title,
        status=ClassOffering.Status.PUBLISHED,
        scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
        **kwargs,
    )


def _sent_embeds(route: respx.Route) -> list[dict]:
    embeds: list[dict] = []
    for call in route.calls:
        embeds.extend(json.loads(call.request.content)["embeds"])
    return embeds


def describe_build_weekly_classes_digest_embeds():
    def it_groups_sessions_by_local_day_with_time_link_and_instructor():
        offering = _published_class("Intro to Welding", days=2)
        _published_class("Wheel Throwing", days=4)
        now = timezone.now()

        embeds = dcp.build_weekly_classes_digest_embeds(now)
        assert len(embeds) == 1
        description = embeds[0]["description"]
        for day_offset in (2, 4):
            day = timezone.localtime(now + timedelta(days=day_offset)).strftime("%A, %B %-d")
            assert f"**{day}**" in description
        assert f"[Intro to Welding]({offering.public_url})" in description
        assert offering.public_url.startswith("http")
        assert f"with {offering.instructor.display_name}" in description

    def it_lists_bookable_flexible_classes_in_their_own_section():
        flexible = _flexible_class("Open Studio Ceramics")
        description = dcp.build_weekly_classes_digest_embeds(timezone.now())[0]["description"]
        assert "**Flexible scheduling — book anytime**" in description
        assert f"[Open Studio Ceramics]({flexible.public_url})" in description

    def it_footers_with_a_browse_all_classes_link():
        _published_class("Intro to Welding", days=2)
        from classes.emails import _absolute_url

        description = dcp.build_weekly_classes_digest_embeds(timezone.now())[0]["description"]
        assert f"[Browse all classes →]({_absolute_url('/')})" in description

    def it_excludes_private_draft_and_beyond_window_sessions():
        _published_class("Secret Workshop", days=2, is_private=True)
        draft = ClassOfferingFactory(title="Unfinished Draft", status=ClassOffering.Status.DRAFT)
        start = timezone.now() + timedelta(days=2)
        ClassSessionFactory(class_offering=draft, starts_at=start, ends_at=start + timedelta(hours=2))
        _published_class("Next Month Intensive", days=20)

        assert dcp.build_weekly_classes_digest_embeds(timezone.now()) == []

    def it_is_empty_when_the_week_has_no_sessions_and_no_flexible_classes():
        assert dcp.build_weekly_classes_digest_embeds(timezone.now()) == []

    def it_chunks_and_batches_a_huge_week_under_the_message_caps():
        for i in range(30):
            # Long titles force chunking; explicit short slugs keep the SlugField in bounds.
            _published_class(f"Marathon class {i:02d} " + "x" * 300, days=2, slug=f"marathon-{i:02d}")
        embeds = dcp.build_weekly_classes_digest_embeds(timezone.now())
        assert len(embeds) > 1
        assert all(len(e["description"]) <= calendar_posts.EMBED_DESCRIPTION_MAX for e in embeds)
        assert "(continued)" in embeds[1]["title"]
        # Payload-level batching assertion: every message batch fits the 6,000 combined cap.
        batches = calendar_posts._batch_embeds(embeds)
        for batch in batches:
            assert sum(calendar_posts._embed_chars(e) for e in batch) <= calendar_posts.MESSAGE_EMBED_TOTAL_MAX
        assert [e for batch in batches for e in batch] == embeds


def describe_post_weekly_classes_digest():
    @respx.mock
    def it_noops_when_the_toggle_is_off():
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _published_class("Intro to Welding")
        assert dcp.post_weekly_classes_digest() == 0
        assert not route.called

    @respx.mock
    def it_noops_when_no_channel_id_is_set():
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts(channel_id="")
        _published_class("Intro to Welding")
        assert dcp.post_weekly_classes_digest() == 0
        assert not route.called

    @respx.mock
    def it_never_posts_an_empty_digest():
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        assert dcp.post_weekly_classes_digest() == 0
        assert not route.called

    @respx.mock
    def it_posts_the_digest(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        _published_class("Intro to Welding", days=2)

        assert dcp.post_weekly_classes_digest() == 1
        assert route.call_count == 1
        assert "Intro to Welding" in _sent_embeds(route)[0]["description"]


def describe_announce_new_classes():
    @respx.mock
    def it_noops_when_the_toggle_is_off():
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        offering = _published_class("Forge Basics")
        assert dcp.announce_new_classes() == 0
        assert not route.called
        offering.refresh_from_db()
        assert offering.channel_announced_at is None  # left for a future enabled run

    @respx.mock
    def it_posts_one_embed_per_new_class_and_stamps_it(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        offering = _published_class("Forge Basics", days=3)

        assert dcp.announce_new_classes() == 1
        assert route.call_count == 1
        embed = _sent_embeds(route)[0]
        assert embed["title"] == "Forge Basics"
        assert embed["url"] == offering.public_url
        assert f"New class in {offering.category.name}" in embed["description"]
        session_day = timezone.localtime(timezone.now() + timedelta(days=3)).strftime("%A, %B %-d")
        assert session_day in embed["description"]
        assert "**Price:** $50" in embed["description"]  # 5000 cents, whole dollars drop decimals
        assert f"[Sign up →]({offering.public_url})" in embed["description"]
        offering.refresh_from_db()
        assert offering.channel_announced_at is not None

    @respx.mock
    def it_never_announces_the_same_class_twice(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        _published_class("Forge Basics")

        assert dcp.announce_new_classes() == 1
        assert dcp.announce_new_classes() == 0
        assert route.call_count == 1

    @respx.mock
    def it_skips_draft_private_and_sessionless_fixed_classes(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        draft = ClassOfferingFactory(title="Still Drafting", status=ClassOffering.Status.DRAFT)
        start = timezone.now() + timedelta(days=2)
        ClassSessionFactory(class_offering=draft, starts_at=start, ends_at=start + timedelta(hours=2))
        _published_class("Invite Only", is_private=True)
        ClassOfferingFactory(title="No Dates Yet", status=ClassOffering.Status.PUBLISHED)  # FIXED, no sessions

        assert dcp.announce_new_classes() == 0
        assert not route.called

    @respx.mock
    def it_never_announces_a_backfilled_offering(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        # The migration stamps every pre-existing published offering — modelled here
        # by an offering that already carries the stamp.
        _published_class("Old Faithful", channel_announced_at=timezone.now())

        assert dcp.announce_new_classes() == 0
        assert not route.called

    @respx.mock
    def it_does_not_reannounce_after_unpublish_and_republish(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        offering = _published_class("Comeback Class")
        assert dcp.announce_new_classes() == 1

        offering.refresh_from_db()
        offering.status = ClassOffering.Status.DRAFT
        offering.save(update_fields=["status"])
        offering.status = ClassOffering.Status.PUBLISHED
        offering.save(update_fields=["status"])

        assert dcp.announce_new_classes() == 0  # the stamp survives the round-trip
        assert route.call_count == 1

    @respx.mock
    def it_announces_a_flexible_class_with_the_flexible_note(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        _flexible_class("Open Studio Ceramics")

        assert dcp.announce_new_classes() == 1
        assert "Flexible scheduling — arrange with the instructor" in _sent_embeds(route)[0]["description"]

    @respx.mock
    def it_caps_at_ten_posts_and_silently_stamps_the_overflow(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        for i in range(13):
            _published_class(f"Backlog class {i:02d}", days=1 + i * 0.1)

        assert dcp.announce_new_classes() == calendar_posts.ANNOUNCE_CAP
        assert route.call_count == calendar_posts.ANNOUNCE_CAP
        assert ClassOffering.objects.filter(channel_announced_at__isnull=True).count() == 0  # overflow stamped
        assert dcp.announce_new_classes() == 0  # nothing left to drip out later
