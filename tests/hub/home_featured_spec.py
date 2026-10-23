"""BDD specs for Home's Featured Class and Featured Event (#888), in ``hub.home`` and its partial."""

from __future__ import annotations

import io
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, RegistrationFactory
from classes.models import ClassOffering, RegistrationStatus
from hub.home import _featured_class, _featured_event, _pick_one
from membership.models import CommunityEvent, Member
from tests.features import coming_soon, hide, turn_on
from tests.membership.factories import (
    CommunityEventFactory,
    GuildFactory,
    GuildMembershipFactory,
    LocationFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db

PUBLISHED = ClassOffering.Status.PUBLISHED


def _png(name: str = "hero.png") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 36), (9, 46, 76)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def _class(title: str = "Intro to Welding", *, days: float = 3, **kwargs: object) -> ClassOffering:
    """A published class with one session ``days`` from now (negative for one already begun)."""
    kwargs.setdefault("status", PUBLISHED)
    # Off unless a spec is about it: the cutoff closes sign ups 48 hours out by default.
    kwargs.setdefault("registration_cutoff_hours", None)
    offering = ClassOfferingFactory(title=title, **kwargs)
    start = timezone.now() + timedelta(days=days)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def _member() -> Member:
    return MemberFactory()


def _first(candidates: object) -> int:
    """A stand in for ``random.randrange`` that always picks the first row."""
    return 0


def describe_pick_one():
    def it_returns_none_without_reading_a_row_when_there_are_no_candidates():
        with patch("hub.home.random.randrange") as randrange:
            assert _pick_one(ClassOffering.objects.featurable()) is None
        randrange.assert_not_called()

    def it_reads_the_row_at_the_random_offset_out_of_the_whole_count():
        first = _class("First")
        second = _class("Second")
        third = _class("Third")
        rows = sorted([first, second, third], key=lambda o: o.pk)
        with patch("hub.home.random.randrange", return_value=1) as randrange:
            picked = _pick_one(ClassOffering.objects.featurable())
        randrange.assert_called_once_with(3)
        assert picked == rows[1]

    def it_returns_none_when_the_row_went_between_the_count_and_the_read():
        _class("Only")
        with patch("hub.home.random.randrange", return_value=1):
            assert _pick_one(ClassOffering.objects.featurable()) is None


def describe_featured_class():
    def it_features_a_bookable_class_with_its_next_session_title_and_instructors():
        teacher = MemberFactory(preferred_name="Alex", full_legal_name="Alex Eason")
        offering = _class("Intro to Welding - 6/5/26", days=4, slug="intro-to-welding", instructors=[teacher])
        later = timezone.now() + timedelta(days=9)
        ClassSessionFactory(class_offering=offering, starts_at=later, ends_at=later + timedelta(hours=2))

        featured = _featured_class()

        assert featured is not None
        assert featured.title == "Intro to Welding"
        assert featured.url == reverse("classes:public_class_detail", kwargs={"slug": offering.slug})
        assert featured.start == offering.sessions.order_by("starts_at").first().starts_at
        assert featured.instructors == "Alex"

    def it_names_the_soonest_session_that_has_not_started():
        offering = _class("Series", days=10)
        sooner = timezone.now() + timedelta(days=2)
        ClassSessionFactory(class_offering=offering, starts_at=sooner, ends_at=sooner + timedelta(hours=2))

        featured = _featured_class()

        assert featured is not None
        assert featured.start == sooner

    def it_picks_the_row_the_chooser_names():
        first = _class("Pottery")
        second = _class("Weaving")
        later = max(first, second, key=lambda o: o.pk)
        with patch("hub.home.random.randrange", return_value=1):
            featured = _featured_class()
        assert featured is not None
        assert featured.title == later.title

    def it_never_features_a_class_that_has_already_begun():
        _class("Started", days=-1)
        assert _featured_class() is None

    def it_never_features_a_class_with_no_session_to_come():
        ClassOfferingFactory(
            title="Flexible",
            status=PUBLISHED,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
        )
        assert _featured_class() is None

    def it_never_features_a_class_past_its_registration_cutoff():
        _class("Closing", days=1, registration_cutoff_hours=48)
        assert _featured_class() is None

    def it_features_a_class_still_inside_its_registration_window():
        _class("Open", days=3, registration_cutoff_hours=48)
        assert _featured_class() is not None

    def it_never_features_a_sold_out_class():
        offering = _class("Full", capacity=2)
        RegistrationFactory(class_offering=offering, status=RegistrationStatus.CONFIRMED)
        RegistrationFactory(class_offering=offering, status=RegistrationStatus.PENDING)
        assert _featured_class() is None

    def it_features_a_class_with_a_seat_left_whoever_waits_or_cancelled():
        offering = _class("One seat left", capacity=2)
        RegistrationFactory(class_offering=offering, status=RegistrationStatus.CONFIRMED)
        RegistrationFactory(class_offering=offering, status=RegistrationStatus.WAITLISTED)
        RegistrationFactory(class_offering=offering, status=RegistrationStatus.CANCELLED)
        assert _featured_class() is not None

    def it_features_a_flexible_class_however_many_have_booked():
        # A flexible class has no seat cap (spots_remaining is None), so it is never full.
        offering = _class("Flexible with a date", capacity=1, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        RegistrationFactory(class_offering=offering, status=RegistrationStatus.CONFIRMED)
        RegistrationFactory(class_offering=offering, status=RegistrationStatus.CONFIRMED)
        assert _featured_class() is not None

    def it_counts_the_offset_from_the_lowest_pk_not_the_newest_class():
        # ClassOffering's default ordering is newest first; created lowest pk first, so only a pk
        # order puts pk 9100 at offset 0.
        lowest = _class("Lowest pk", pk=9100)
        _class("Highest pk", pk=9300)
        with patch("hub.home.random.randrange", return_value=0):
            featured = _featured_class()
        assert featured is not None
        assert featured.title == lowest.title

    def it_never_features_a_class_the_catalog_does_not_list():
        _class("Draft", status=ClassOffering.Status.DRAFT)
        _class("Private", is_private=True)
        assert _featured_class() is None

    def it_is_left_out_while_the_classes_switch_is_hidden():
        _class()
        hide("catalog")
        assert _featured_class() is None

    def it_is_left_out_while_the_classes_switch_is_coming_soon():
        _class()
        coming_soon("catalog")
        assert _featured_class() is None

    def it_shows_while_the_classes_switch_is_on():
        _class()
        turn_on("catalog")
        assert _featured_class() is not None

    def describe_its_picture():
        def it_uses_the_class_photo_with_the_catalog_cards_crop():
            offering = _class(card_focus_x=10, card_focus_y=20)

            featured = _featured_class()

            assert featured is not None
            assert featured.photo_url == offering.hero_image_url
            assert featured.photo_url
            assert featured.photo_position == "10% 20%"

        def it_falls_back_to_the_category_photo_centred_as_the_catalog_does():
            category = CategoryFactory(hero_image=_png("category.png"))
            offering = _class(image="", category=category, card_focus_x=10, card_focus_y=20)

            featured = _featured_class()

            assert featured is not None
            assert featured.photo_url == offering.category.hero_image.url
            assert featured.photo_position == "50% 50%"

        def it_has_no_picture_when_neither_the_class_nor_its_category_has_one():
            _class(image="")

            featured = _featured_class()

            assert featured is not None
            assert featured.photo_url == ""


def describe_card_photo_url():
    def it_is_the_class_photo_first():
        offering = ClassOfferingFactory(category=CategoryFactory(hero_image=_png("category.png")))
        assert offering.card_photo_url == offering.hero_image_url

    def it_is_the_category_photo_without_one():
        offering = ClassOfferingFactory(image="", category=CategoryFactory(hero_image=_png("category.png")))
        assert offering.card_photo_url == offering.category.hero_image.url

    def it_is_empty_with_neither():
        assert ClassOfferingFactory(image="").card_photo_url == ""


def describe_featured_event():
    def it_features_a_published_upcoming_community_event_with_its_links_and_place():
        member = _member()
        area = LocationFactory(name="Main Floor")
        event = CommunityEventFactory(community=True, title="Open Studio Night", area=area, location="Back door")

        featured = _featured_event(member)

        assert featured is not None
        assert featured.event == event
        assert featured.title == "Open Studio Night"
        assert featured.url == reverse("hub_event_detail", args=[event.pk])
        assert featured.flyer_url == reverse("hub_event_flyer", args=[event.pk])
        assert featured.start == event.starts_at
        assert featured.place == "Main Floor · Back door"

    def it_names_the_area_alone_or_the_location_alone():
        member = _member()
        CommunityEventFactory(community=True, area=LocationFactory(name="Wood Shop"), location="")
        assert _featured_event(member).place == "Wood Shop"
        CommunityEvent.objects.all().delete()
        CommunityEventFactory(community=True, area=None, location="Front room")
        assert _featured_event(member).place == "Front room"
        CommunityEvent.objects.all().delete()
        CommunityEventFactory(community=True, area=None, location="")
        assert _featured_event(member).place == ""

    def it_opens_a_repeating_series_on_its_next_date():
        member = _member()
        anchor = timezone.now() - timedelta(days=20)
        event = CommunityEventFactory(community=True, starts_at=anchor, recurrence=CommunityEvent.Recurrence.WEEKLY)

        featured = _featured_event(member)

        assert featured is not None
        assert featured.start == event.next_occurrence_start()
        assert featured.start > timezone.now()
        assert featured.url == reverse("hub_event_detail", args=[event.pk]) + event.date_query(featured.start)
        assert "?date=" in featured.url

    def it_features_a_meeting_of_a_guild_the_member_joined():
        member = _member()
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=member)
        event = CommunityEventFactory(guild=guild, guild_meeting=True)
        assert _featured_event(member).event == event

    def it_picks_the_row_the_chooser_names():
        member = _member()
        first = CommunityEventFactory(community=True)
        second = CommunityEventFactory(community=True)
        with patch("hub.home.random.randrange", return_value=1):
            featured = _featured_event(member)
        assert featured is not None
        assert featured.event == max(first, second, key=lambda e: e.pk)

    def it_never_features_a_meeting_of_a_guild_the_member_is_not_in():
        CommunityEventFactory(guild_meeting=True)
        assert _featured_event(_member()) is None

    def it_never_features_studio_hours_or_a_lead_meeting():
        member = _member()
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=member)
        CommunityEventFactory(guild=guild, studio_hours=True)
        CommunityEventFactory(lead_meeting=True)
        assert _featured_event(member) is None

    def it_never_features_a_one_off_event_that_has_already_begun():
        begun = timezone.now() - timedelta(hours=1)
        CommunityEventFactory(community=True, starts_at=begun, ends_at=begun + timedelta(hours=3))
        assert _featured_event(_member()) is None

    def it_counts_the_offset_from_the_lowest_pk_not_the_soonest_start():
        # CommunityEvent's default ordering is by start; the lowest pk starts last, so only a pk
        # order puts it at offset 0.
        member = _member()
        lowest = CommunityEventFactory(community=True, pk=9100, starts_at=timezone.now() + timedelta(days=9))
        CommunityEventFactory(community=True, pk=9300, starts_at=timezone.now() + timedelta(days=3))
        with patch("hub.home.random.randrange", return_value=0):
            featured = _featured_event(member)
        assert featured is not None
        assert featured.event == lowest

    def it_never_features_a_past_or_unpublished_event():
        past = timezone.now() - timedelta(days=3)
        CommunityEventFactory(community=True, starts_at=past)
        CommunityEventFactory(community=True, pending=True)
        assert _featured_event(_member()) is None

    def describe_its_picture():
        def it_uses_the_event_photo_as_the_event_page_crops_it_and_makes_no_flyer():
            event = CommunityEventFactory(community=True, photo=_png())

            featured = _featured_event(_member())

            assert featured is not None
            assert featured.photo_url == event.hero_image_url
            assert featured.photo_url
            assert featured.photo_position == event.hero_object_position
            assert featured.qr_svg == ""

        def it_brings_the_flyer_qr_without_a_photo():
            event = CommunityEventFactory(community=True)

            featured = _featured_event(_member())

            assert featured is not None
            assert featured.photo_url == ""
            assert featured.qr_svg == event.qr_svg()
            assert "<svg" in featured.qr_svg


def describe_home_page():
    def _home(client: Client, member: Member) -> str:
        client.force_login(member.user)
        response = client.get(reverse("hub_home"))
        assert response.status_code == 200
        return response.content.decode()

    def _linked_member() -> Member:
        from django.contrib.auth.models import User

        from tests.membership.factories import MembershipPlanFactory

        MembershipPlanFactory()
        return User.objects.create_user(username="featured", password="pass").member

    def it_leaves_the_upcoming_card_as_it_was_with_nothing_to_feature(client: Client):
        html = _home(client, _linked_member())
        assert "data-home-featured" not in html
        assert "home-featured.css" in html

    def it_shows_both_cards_side_by_side_above_the_list(client: Client):
        member = _linked_member()
        offering = _class("Glass Fusing")
        event = CommunityEventFactory(community=True, title="Holiday Market")

        html = _home(client, member)

        assert 'class="pl-home-featured pl-home-featured--pair"' in html
        assert "data-featured-class" in html
        assert f'data-featured-event="{event.pk}"' in html
        assert html.index("data-home-featured") < html.index('class="pl-upcoming"')
        assert f'href="{reverse("classes:public_class_detail", kwargs={"slug": offering.slug})}"' in html
        assert f'href="{reverse("hub_event_flyer", args=[event.pk])}"' in html

    def it_lets_the_class_card_take_the_row_alone(client: Client):
        member = _linked_member()
        _class("Glass Fusing")
        html = _home(client, member)
        assert 'class="pl-home-featured"' in html
        assert "data-featured-class" in html
        assert "data-featured-event" not in html

    def it_lets_the_event_card_take_the_row_alone(client: Client):
        member = _linked_member()
        CommunityEventFactory(community=True)
        hide("catalog")
        _class("Hidden by the switch")
        html = _home(client, member)
        assert 'class="pl-home-featured"' in html
        assert "data-featured-event" in html
        assert "data-featured-class" not in html

    def it_draws_the_class_photo_lazily_with_its_title_for_alt(client: Client):
        member = _linked_member()
        offering = _class("Glass Fusing")
        html = _home(client, member)
        assert f'src="{offering.hero_image_url}" alt="Glass Fusing" width="640" height="360" loading="lazy"' in html
        assert "data-featured-accent" not in html

    def it_draws_the_accent_block_for_a_class_with_no_picture(client: Client):
        member = _linked_member()
        _class("Plain", image="")
        html = _home(client, member)
        assert "data-featured-accent" in html
        assert "data-featured-photo" not in html

    def it_shows_the_flyer_preview_for_an_event_with_no_photo(client: Client):
        member = _linked_member()
        event = CommunityEventFactory(community=True)
        html = _home(client, member)
        assert "data-featured-flyer" in html
        assert f'data-event-flyer="{event.pk}"' in html
        assert "data-featured-photo" not in html

    def it_shows_the_photo_and_no_flyer_preview_for_an_event_with_one(client: Client):
        member = _linked_member()
        event = CommunityEventFactory(community=True, title="Kiln Opening", photo=_png())
        html = _home(client, member)
        assert f'src="{event.hero_image_url}" alt="Kiln Opening"' in html
        assert "data-event-flyer=" not in html
        assert "data-featured-flyer-link" in html

    def describe_query_budget():
        def _count_home_queries(client: Client) -> int:
            with CaptureQueriesContext(connection) as captured:
                assert client.get(reverse("hub_home")).status_code == 200
            return len(captured.captured_queries)

        def it_costs_the_same_however_many_classes_and_events_exist(client: Client):
            member = _linked_member()
            client.force_login(member.user)
            _class("One")
            CommunityEventFactory(community=True)
            with patch("hub.home.random.randrange", side_effect=_first):
                _count_home_queries(client)  # the first load also does once per session work
                few = _count_home_queries(client)
            for n in range(4):
                _class(f"More {n}")
                CommunityEventFactory(community=True)
            with patch("hub.home.random.randrange", side_effect=_first):
                many = _count_home_queries(client)
            assert many == few

        def it_reads_the_featured_class_in_five_queries(django_assert_num_queries):
            # The Classes switch, the demo gate's settings row, the count, the row and its instructors.
            # No photo of their own, so the card reads the category's: one query only while it is joined.
            category = CategoryFactory(hero_image=_png("category.png"))
            for n in range(3):
                _class(f"Class {n}", image="", category=category)
            with django_assert_num_queries(5):
                assert _featured_class() is not None

        def it_reads_the_featured_event_in_two_queries(django_assert_num_queries):
            # The count and the row, its area joined.
            member = _member()
            for _ in range(3):
                CommunityEventFactory(community=True, area=LocationFactory())
            with django_assert_num_queries(2):
                assert _featured_event(member) is not None
