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


def _field(embed: dict, name: str) -> str:
    """The value of the labelled row ``name``; a KeyError when the row is absent."""
    return {field["name"]: field["value"] for field in embed["fields"]}[name]


def _field_names(embed: dict) -> list[str]:
    return [field["name"] for field in embed["fields"]]


def _announced(title: str = "Forge Basics", **kwargs) -> list[dict]:
    """The embeds the announcer would post for one new class, loaded as the announcer loads it."""
    _published_class(title, **kwargs)
    (offering,) = dcp._pending_offerings()
    return dcp.class_announcement_embeds(offering)


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

    def it_links_a_class_imported_from_the_old_site_to_its_page_here():
        offering = _published_class("Intro to Welding", days=2, legacy_cms_id="node-abc", slug="intro-welding")
        description = dcp.build_weekly_classes_digest_embeds(timezone.now())[0]["description"]
        assert f"[Intro to Welding]({offering.public_url})" in description
        assert "/class/intro-welding" not in description

    def it_lists_bookable_flexible_classes_in_their_own_section():
        flexible = _flexible_class("Open Studio Ceramics")
        description = dcp.build_weekly_classes_digest_embeds(timezone.now())[0]["description"]
        assert "**Flexible scheduling — book anytime**" in description
        assert f"[Open Studio Ceramics]({flexible.public_url})" in description

    def it_footers_with_a_browse_all_classes_link_to_the_catalog(settings):
        settings.BOOK_BASE_URL = "https://classes.example"
        _published_class("Intro to Welding", days=2)

        embed = dcp.build_weekly_classes_digest_embeds(timezone.now())[0]
        assert "[Browse all classes →](https://classes.example/classes/)" in embed["description"]
        assert embed["url"] == "https://classes.example/classes/"

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
            # Long titles force chunking (30 x ~250 chars far exceeds one embed's 4096 cap);
            # 230 x's keep the title inside the CharField's max_length=255 so Postgres accepts it.
            _published_class(f"Marathon class {i:02d} " + "x" * 230, days=2, slug=f"marathon-{i:02d}")
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
        respx.get(_MESSAGES_URL).mock(return_value=httpx.Response(200, json=[]))
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        _published_class("Intro to Welding", days=2)

        assert dcp.post_weekly_classes_digest() == 1
        assert route.call_count == 1
        assert "Intro to Welding" in _sent_embeds(route)[0]["description"]

    @respx.mock
    def it_does_not_post_again_when_this_weeks_digest_is_already_in_the_channel(settings):
        # A timed-out post can land anyway; the scheduler's retry must not post it twice.
        settings.DISCORD_BOT_TOKEN = "tok"
        _enable_posts()
        _published_class("Intro to Welding", days=2)
        title = dcp.build_weekly_classes_digest_embeds(timezone.now())[0]["title"]
        landed = {"id": "1", "author": {"bot": True}, "embeds": [{"title": title}]}
        respx.get(_MESSAGES_URL).mock(return_value=httpx.Response(200, json=[landed]))
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))

        assert dcp.post_weekly_classes_digest() == 0
        assert not route.called


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
        session_day = timezone.localtime(timezone.now() + timedelta(days=3)).strftime("%A, %B %-d")
        assert session_day in _field(embed, "When")
        assert _field(embed, "Price") == "$50"  # 5000 cents, whole dollars drop decimals
        offering.refresh_from_db()
        assert offering.channel_announced_at is not None

    @respx.mock
    def it_links_a_class_imported_from_the_old_site_to_its_page_here(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        offering = _published_class("Forge Basics", days=3, legacy_cms_id="node-abc", slug="forge-basics")

        assert dcp.announce_new_classes() == 1
        embed = _sent_embeds(route)[0]
        assert embed["url"] == offering.public_url

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
        assert _field(_sent_embeds(route)[0], "When") == "Flexible scheduling — arrange with the instructor"

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


def describe_a_flexible_class_with_a_date_window():
    """The digest and the announcer follow bookable(): listed through the last day, gone the day after (#545)."""

    def it_keeps_the_class_in_the_digest_on_its_last_day_and_drops_it_the_day_after():
        from datetime import date
        from unittest import mock

        _flexible_class("Open Forge", flexible_starts_on=date(2026, 11, 2), flexible_ends_on=date(2026, 12, 1))
        with mock.patch("classes.models.timezone.localdate", return_value=date(2026, 12, 1)):
            description = dcp.build_weekly_classes_digest_embeds(timezone.now())[0]["description"]
        assert "**Flexible scheduling — book anytime**" in description
        assert "• Nov 2 to Dec 1, 2026 — [Open Forge]" in description
        with mock.patch("classes.models.timezone.localdate", return_value=date(2026, 12, 2)):
            assert dcp.build_weekly_classes_digest_embeds(timezone.now()) == []

    def it_lists_a_class_with_no_window_as_before():
        flexible = _flexible_class("Open Studio Ceramics")
        description = dcp.build_weekly_classes_digest_embeds(timezone.now())[0]["description"]
        assert f"• [Open Studio Ceramics]({flexible.public_url})" in description

    @respx.mock
    def it_announces_the_window_on_the_when_line(settings):
        from datetime import date

        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        _flexible_class("Open Forge", flexible_starts_on=date(2026, 11, 2), flexible_ends_on=date(2026, 12, 1))

        assert dcp.announce_new_classes() == 1
        assert (
            _field(_sent_embeds(route)[0], "When")
            == "Flexible scheduling — arrange with the instructor · Nov 2 to Dec 1, 2026"
        )

    @respx.mock
    def it_never_announces_a_class_whose_window_has_ended(settings):
        from datetime import date

        settings.DISCORD_BOT_TOKEN = "tok"
        respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        _flexible_class("Over", flexible_ends_on=date(2020, 1, 1))

        assert dcp.announce_new_classes() == 0


def describe_a_class_with_two_instructors():
    def it_names_both_in_the_digest_line():
        # #686: the weekly digest reads the instructors list, in the order added.
        from classes.factories import InstructorFactory

        first = InstructorFactory(full_legal_name="Alex Eason")
        second = InstructorFactory(full_legal_name="Jenelle Giordano")
        _published_class("Picture Framing", days=2, instructor=first, instructors=[first, second])
        description = dcp.build_weekly_classes_digest_embeds(timezone.now())[0]["description"]
        assert "with Alex Eason and Jenelle Giordano" in description


def describe_class_announcement_embeds():
    """#879: the new class post matches the #calendar event card, with the class's photos."""

    def it_opens_with_the_description_cut_at_600_with_more_on_the_class_page():
        (embed, *_) = _announced(description="<p>" + "x" * 700 + "</p>")
        assert embed["description"] == "x" * 600 + "… more on the class page"

    def it_shows_a_short_description_whole_as_plain_text():
        (embed, *_) = _announced(description="<p>Learn to <strong>weld</strong>.</p>")
        assert embed["description"] == "Learn to weld."

    def it_leaves_the_description_out_when_the_class_has_none():
        (embed, *_) = _announced(description="")
        assert "description" not in embed

    def it_shows_every_labelled_row_with_a_value():
        from tests.membership.factories import LocationFactory

        area = LocationFactory(name="Hot Shop")
        (embed, *_) = _announced(days=3, area=area, price_cents=4500, capacity=8)
        assert _field_names(embed) == ["When", "Duration", "Location", "Instructor", "Price", "Spots"]
        session_day = timezone.localtime(timezone.now() + timedelta(days=3)).strftime("%A, %B %-d")
        assert _field(embed, "When").startswith(f"{session_day} · ")
        assert _field(embed, "Duration") == "2 hours"
        assert _field(embed, "Location") == "Hot Shop"
        assert _field(embed, "Price") == "$45"
        assert _field(embed, "Spots") == "8"

    def it_counts_the_sessions_only_when_there_is_more_than_one():
        offering = _published_class("Forge Series", days=3)
        start = timezone.now() + timedelta(days=10)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=3))
        (loaded,) = dcp._pending_offerings()
        embed = dcp.class_announcement_embeds(loaded)[0]
        assert _field(embed, "Sessions") == "2"
        assert _field(embed, "Duration") == "2 hours"  # the first session's
        session_day = timezone.localtime(timezone.now() + timedelta(days=3)).strftime("%A, %B %-d")
        assert _field(embed, "When").startswith(session_day)  # the next one

    def it_words_an_odd_length_like_the_event_card():
        offering = ClassOfferingFactory(title="Short", status=ClassOffering.Status.PUBLISHED)
        start = timezone.now() + timedelta(days=2)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(minutes=90))
        (loaded,) = dcp._pending_offerings()
        assert _field(dcp.class_announcement_embeds(loaded)[0], "Duration") == "1 hour 30 minutes"

    def it_names_every_instructor():
        from classes.factories import InstructorFactory

        first = InstructorFactory(full_legal_name="Alex Eason")
        second = InstructorFactory(full_legal_name="Jenelle Giordano")
        (embed, *_) = _announced(instructor=first, instructors=[first, second])
        assert _field(embed, "Instructor") == "Alex Eason and Jenelle Giordano"

    def it_leaves_out_location_and_sessions_when_they_have_no_value():
        (embed, *_) = _announced()
        assert "Location" not in _field_names(embed)
        assert "Sessions" not in _field_names(embed)

    def it_gives_a_flexible_class_its_window_and_no_spots_or_duration():
        _flexible_class("Open Studio Ceramics")
        (offering,) = dcp._pending_offerings()
        embed = dcp.class_announcement_embeds(offering)[0]
        assert _field_names(embed) == ["When", "Instructor", "Price"]

    def it_shows_the_hero_photo_as_an_absolute_url(settings):
        settings.BOOK_BASE_URL = "https://classes.example.org"
        (embed, *_) = _announced(gallery=0)
        assert embed["image"]["url"].startswith("https://classes.example.org/")

    def it_keeps_an_object_storage_url_as_it_is():
        from unittest import mock

        r2 = "https://pub.r2.dev/classes/hero.jpg"
        with mock.patch.object(ClassOffering, "hero_image_url", new_callable=mock.PropertyMock, return_value=r2):
            (embed,) = _announced(gallery=0)
        assert embed["image"] == {"url": r2}

    def it_leaves_out_a_photo_it_cannot_make_absolute(settings):
        settings.BOOK_BASE_URL = ""
        (embed,) = _announced()
        assert "image" not in embed

    def it_shows_up_to_three_gallery_photos_with_the_hero_as_one_grid():
        embeds = _announced(gallery=5)
        main, *grid = embeds
        assert len(embeds) == 4  # the hero and three gallery photos, one message
        assert main["image"]["url"].startswith("http")
        assert [set(extra) for extra in grid] == [{"url", "image"}] * 3
        assert all(extra["url"] == main["url"] for extra in grid)
        urls = [embed["image"]["url"] for embed in embeds]
        assert len(set(urls)) == 4 and all(url.startswith("http") for url in urls)

    def it_posts_a_class_with_no_photos_as_a_card_with_no_picture():
        embeds = _announced(image="", gallery=0)
        assert len(embeds) == 1
        assert "image" not in embeds[0]

    def it_footers_sign_up_on_the_class_page_and_links_the_title_there():
        (embed, *_) = _announced()
        assert embed["footer"] == {"text": "Sign up on the class page"}
        assert embed["url"].startswith("http") and embed["url"].endswith("/")

    def it_fits_discords_caps_for_the_longest_class():
        from classes.factories import InstructorFactory

        people = [InstructorFactory(full_legal_name=f"Instructor With A Long Name {i:03d}") for i in range(60)]
        embeds = _announced(title="T" * 255, description="y" * 5000, instructor=people[0], instructors=people)
        main = embeds[0]
        assert all(len(field["value"]) <= 1024 for field in main["fields"])
        assert _field(main, "Instructor").endswith("…")
        text = len(main["title"]) + len(main["description"]) + len(main["footer"]["text"])
        text += sum(len(field["name"]) + len(field["value"]) for field in main["fields"])
        assert text <= 6000
        assert len(embeds) <= 10

    def it_builds_the_post_without_a_query_per_class(django_assert_num_queries):
        for i in range(3):
            _published_class(f"Class {i}", gallery=2)
        pending = dcp._pending_offerings()
        with django_assert_num_queries(0):
            for offering in pending:
                dcp.class_announcement_embeds(offering)

    @respx.mock
    def it_sends_the_card_and_its_photos_in_one_message(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        _published_class("Forge Basics", gallery=3)

        assert dcp.announce_new_classes() == 1
        assert route.call_count == 1
        assert len(_sent_embeds(route)) == 4
