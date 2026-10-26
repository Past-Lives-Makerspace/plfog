"""BDD specs for Home's Upcoming queue (#937): ``hub.home._upcoming_items`` and ``hub/partials/_home_upcoming.html``.

The first item of Upcoming is the featured card and the rest are the list under it. Each item carries what the card
shows: its kind, its photo or else a date poster, its View button and, for an event, its Flyer.
"""

from __future__ import annotations

import io
import itertools
import re
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as date_format
from PIL import Image

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from hub.calendar_service import sync_local_class_events
from hub.home import UpcomingItem, _local_class_session_pk, _upcoming_items
from membership.models import CalendarEvent, CommunityEvent, Member
from tests.membership.factories import (
    CommunityEventFactory,
    GuildFactory,
    GuildMembershipFactory,
    LocationFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

_BOOKINGS = itertools.count()


def _png(name: str = "hero.png") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 36), (9, 46, 76)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def _in(days: float, hours: float = 0) -> object:
    return timezone.now() + timedelta(days=days, hours=hours)


def _class(title: str = "Intro to Welding", *, days: float = 3, **kwargs: object) -> ClassOffering:
    """A published class with one session ``days`` from now, put on the calendar as the nightly sync does."""
    kwargs.setdefault("status", ClassOffering.Status.PUBLISHED)
    kwargs.setdefault("registration_cutoff_hours", None)
    offering = ClassOfferingFactory(title=title, **kwargs)
    start = _in(days)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    sync_local_class_events()
    return offering


def _booking(member: Member, *, days: float = 2, **type_kwargs: object) -> object:
    guild = GuildFactory()
    orientation_type = OrientationTypeFactory(guild=guild, name=f"Orientation {next(_BOOKINGS)}", **type_kwargs)
    slot = OrientationSlotFactory(
        guild=guild, orientation_type=orientation_type, starts_at=_in(days), ends_at=_in(days, 1)
    )
    return OrientationBookingFactory(slot=slot, member=member)


def _only(member: Member) -> UpcomingItem:
    items = _upcoming_items(member)
    assert len(items) == 1
    return items[0]


def _item(**kwargs: object) -> UpcomingItem:
    start = timezone.now()
    return UpcomingItem(title="T", start=start, end=start, url="/", **kwargs)


def describe_upcoming_item():
    @pytest.mark.parametrize(
        ("kind", "key", "label"),
        [
            ("Class", "class", "View Class"),
            ("Event", "event", "View Event"),
            ("Meeting", "meeting", "View Meeting"),
            ("Studio hours", "studio-hours", "View Studio Hours"),
            ("Orientation", "orientation", "View Orientation"),
        ],
    )
    def it_names_each_kinds_color_and_its_view_button_in_title_case(kind: str, key: str, label: str):
        item = _item(kind=kind)
        assert item.kind_key == key
        assert item.view_label == label

    def it_fails_loudly_on_a_kind_it_does_not_know():
        with pytest.raises(KeyError):
            _item(kind="Party").kind_key  # noqa: B018

    def it_has_no_picture_flyer_or_instructors_unless_given():
        item = _item(kind="Event")
        assert (item.photo_url, item.photo_position, item.flyer_url, item.instructors) == ("", "50% 50%", "", "")


def describe_local_class_session_pk():
    def it_reads_the_session_pk_the_class_sync_writes():
        assert _local_class_session_pk("local-class-42") == 42

    def it_is_none_for_any_other_uid():
        assert _local_class_session_pk("local-class-Intro to Welding") is None
        assert _local_class_session_pk("local-class-") is None
        assert _local_class_session_pk("classes-17") is None


def describe_the_queue():
    def it_merges_every_kind_soonest_first():
        member = MemberFactory()
        guild = GuildFactory(name="Ceramics")
        GuildMembershipFactory(guild=guild, member=member)
        _booking(member, days=1)
        _class("Glass Fusing", days=2)
        CommunityEventFactory(community=True, title="Holiday Market", starts_at=_in(3), ends_at=_in(3, 2))
        CommunityEventFactory(guild=guild, title="Ceramics Meeting", starts_at=_in(4), ends_at=_in(4, 1))
        CommunityEventFactory(guild=guild, studio_hours=True, title="Open Wheel", starts_at=_in(5), ends_at=_in(5, 3))

        items = _upcoming_items(member)

        assert [item.kind for item in items] == ["Orientation", "Class", "Event", "Meeting", "Studio hours"]
        assert [item.start for item in items] == sorted(item.start for item in items)

    def describe_a_class():
        def it_carries_the_catalog_cards_picture_crop_and_instructors():
            teacher = MemberFactory(preferred_name="Alex", full_legal_name="Alex Eason")
            offering = _class("Glass Fusing", card_focus_x=10, card_focus_y=20, instructors=[teacher])

            item = _only(MemberFactory())

            assert item.kind == "Class"
            assert item.url == f"/classes/{offering.slug}/"
            assert item.photo_url == offering.card_photo_url
            assert item.photo_url
            assert item.photo_position == "10% 20%"
            assert item.instructors == "Alex"
            assert item.flyer_url == ""

        def it_falls_back_to_the_category_photo_centred_as_the_catalog_does():
            category = CategoryFactory(hero_image=_png("category.png"))
            offering = _class("Plain", image="", category=category, card_focus_x=10, card_focus_y=20)

            item = _only(MemberFactory())

            assert item.photo_url == offering.category.hero_image.url
            assert item.photo_position == "50% 50%"

        def it_has_no_picture_when_neither_the_class_nor_its_category_has_one():
            _class("Bare", image="")
            assert _only(MemberFactory()).photo_url == ""

        def it_has_no_picture_or_instructors_for_a_row_with_no_session_behind_it():
            start = _in(4)
            CalendarEvent.objects.create(
                source=CalendarEvent.Source.CLASSES,
                uid="local-class-999999",
                title="Gone",
                url="/classes/gone/",
                start_dt=start,
                end_dt=start + timedelta(hours=2),
                fetched_at=timezone.now(),
            )

            item = _only(MemberFactory())

            assert (item.title, item.photo_url, item.photo_position, item.instructors) == ("Gone", "", "50% 50%", "")

    def describe_an_event():
        def it_opens_its_page_and_flyer_with_its_place_and_photo_as_its_page_crops_it():
            area = LocationFactory(name="Main Floor")
            event = CommunityEventFactory(
                community=True, title="Holiday Market", area=area, location="Back door", photo=_png()
            )

            item = _only(MemberFactory())

            assert item.kind == "Event"
            assert item.url == reverse("hub_event_detail", args=[event.pk])
            assert item.flyer_url == reverse("hub_event_flyer", args=[event.pk])
            assert item.location == "Main Floor · Back door"
            assert item.photo_url == event.hero_image_url
            assert item.photo_url
            assert item.photo_position == event.hero_object_position

        def it_names_the_area_alone_or_the_location_alone():
            member = MemberFactory()
            CommunityEventFactory(community=True, area=LocationFactory(name="Wood Shop"), location="")
            assert _only(member).location == "Wood Shop"
            CommunityEvent.objects.all().delete()
            CommunityEventFactory(community=True, area=None, location="Front room")
            assert _only(member).location == "Front room"

        def it_has_no_photo_without_one_but_keeps_its_flyer():
            event = CommunityEventFactory(community=True)
            item = _only(MemberFactory())
            assert item.photo_url == ""
            assert item.flyer_url == reverse("hub_event_flyer", args=[event.pk])

        def it_opens_a_repeating_series_on_its_next_date():
            anchor = timezone.now() - timedelta(days=20)
            event = CommunityEventFactory(
                community=True,
                starts_at=anchor,
                ends_at=anchor + timedelta(hours=1),
                recurrence=CommunityEvent.Recurrence.WEEKLY,
            )

            item = _only(MemberFactory())

            assert item.start > timezone.now()
            assert item.url == reverse("hub_event_detail", args=[event.pk]) + event.date_query(item.start)
            assert "?date=" in item.url

        def it_calls_a_guild_meeting_a_meeting_and_studio_hours_studio_hours():
            member = MemberFactory()
            guild = GuildFactory(name="Metals")
            GuildMembershipFactory(guild=guild, member=member)
            CommunityEventFactory(guild=guild, title="Metals Meeting", starts_at=_in(2), ends_at=_in(2, 1))
            CommunityEventFactory(
                guild=guild, studio_hours=True, title="Forge Time", starts_at=_in(3), ends_at=_in(3, 2)
            )

            meeting, studio = _upcoming_items(member)

            assert (meeting.kind, meeting.guild_name, meeting.view_label) == ("Meeting", "Metals", "View Meeting")
            assert (studio.kind, studio.view_label) == ("Studio hours", "View Studio Hours")
            assert studio.flyer_url

    def describe_an_orientation_booking():
        def it_shows_the_orientations_own_photo():
            member = MemberFactory()
            booking = _booking(member, photo=_png("orientation.png"))

            item = _only(member)

            assert item.kind == "Orientation"
            assert item.photo_url == booking.orientation_type.photo.url
            assert item.flyer_url == ""

        def it_has_no_photo_without_one():
            member = MemberFactory()
            _booking(member)
            assert _only(member).photo_url == ""


def _linked_member() -> Member:
    from django.contrib.auth.models import User

    MembershipPlanFactory()
    return User.objects.create_user(username="queue", password="pass").member


def _home(client: Client, member: Member) -> str:
    client.force_login(member.user)
    response = client.get(reverse("hub_home"))
    assert response.status_code == 200
    return response.content.decode()


def _three_events() -> list[CommunityEvent]:
    return [
        CommunityEventFactory(community=True, title=f"Zorblax event {n}", starts_at=_in(n + 1), ends_at=_in(n + 1, 2))
        for n in range(3)
    ]


def _rows(html: str) -> list[tuple[str, bool]]:
    """Each list row's queue index, in page order, and whether it is hidden."""
    return [(index, bool(hidden)) for index, hidden in re.findall(r'data-queue-row="(\d+)"( hidden)?', html)]


def describe_the_home_page():
    def it_shows_the_empty_state_with_nothing_upcoming(client: Client):
        html = _home(client, _linked_member())
        assert "data-home-upcoming" not in html
        assert '<h2 class="pl-home-heading">Upcoming</h2>' in html
        assert "pl-home-empty" in html

    def it_heads_the_card_then_features_the_first_item_and_lists_the_rest_in_order(client: Client):
        member = _linked_member()
        events = _three_events()

        html = _home(client, member)

        heading = html.index('<h2 class="pl-home-heading">Upcoming</h2>')
        assert heading < html.index("data-home-upcoming") < html.index("data-queue-list")
        assert re.findall(r'pl-upnext__panel [^"]*is-active"[^>]*aria-label="([^"]+)"', html) == [events[0].title]
        assert _rows(html) == [("1", False), ("2", False), ("0", True)]

    def it_lists_no_item_twice(client: Client):
        member = _linked_member()
        _three_events()

        html = _home(client, member)

        shown = [index for index, hidden in _rows(html) if not hidden]
        assert "0" not in shown
        assert sorted(shown) == ["1", "2"]

    def it_draws_the_date_poster_for_an_item_with_no_photo(client: Client):
        member = _linked_member()
        event = CommunityEventFactory(community=True, title="Zorblax Market", starts_at=_in(3), ends_at=_in(3, 2))

        html = _home(client, member)

        start = timezone.localtime(event.starts_at)
        end = timezone.localtime(event.ends_at)
        poster = html.split("data-upnext-poster>", 1)[1].split("</a>", 1)[0]
        assert f'<span class="pl-upnext__poster-month">{date_format(start, "M")}</span>' in poster
        assert f'<span class="pl-upnext__poster-day">{date_format(start, "j")}</span>' in poster
        assert f'<span class="pl-upnext__poster-weekday">{date_format(start, "l")}</span>' in poster
        assert f"{date_format(start, 'g:i A')} to {date_format(end, 'g:i A')}" in poster
        assert '<span class="pl-upnext__poster-title">Zorblax Market</span>' in poster
        assert "data-upnext-photo" not in html
        assert "pl-upnext__panel--event" in html

    def it_shows_the_photo_and_no_poster_for_an_item_with_one(client: Client):
        member = _linked_member()
        event = CommunityEventFactory(community=True, title="Kiln Opening", photo=_png())

        html = _home(client, member)

        assert f'src="{event.hero_image_url}" alt="" width="640" height="360"' in html
        assert "data-upnext-poster" not in html

    def it_keeps_the_flyer_for_an_event_and_gives_a_class_none(client: Client):
        member = _linked_member()
        event = CommunityEventFactory(community=True, starts_at=_in(5), ends_at=_in(5, 1))
        _class("Zorblax Pottery", days=2)

        html = _home(client, member)

        assert html.count("data-upnext-flyer") == 1
        assert (
            f'href="{reverse("hub_event_flyer", args=[event.pk])}" class="hub-btn hub-btn--sm hub-btn--ghost" target="_blank"'
            in html
        )
        assert ">View Class</a>" in html
        assert ">View Event</a>" in html

    def it_gives_the_featured_card_join_online_and_a_listed_row_its_link(client: Client):
        member = _linked_member()
        for n in range(2):
            CommunityEventFactory(
                community=True, title=f"Talk {n}", video_url=f"https://meet.google.com/{n}", starts_at=_in(n + 1)
            )

        html = _home(client, member)

        assert 'href="https://meet.google.com/0" class="hub-btn hub-btn--sm hub-btn--ghost"' in html
        assert 'href="https://meet.google.com/1" class="pl-upcoming__join"' in html

    def it_colors_each_rows_pill_by_its_kind(client: Client):
        member = _linked_member()
        _booking(member, days=1)
        _class("Zorblax Pottery", days=2)

        html = _home(client, member)

        assert '<span class="pl-upcoming__kind pl-upcoming__kind--class">Class</span>' in html
        assert '<span class="pl-upcoming__kind pl-upcoming__kind--orientation">Orientation</span>' in html

    def describe_the_rotation():
        def it_is_a_queue_moving_every_six_seconds(client: Client):
            member = _linked_member()
            _three_events()
            html = _home(client, member)
            assert 'x-data="plUpcomingQueue" data-rotator-interval="6000" data-rotator-name="home-upcoming"' in html
            assert '<script defer src="/static/js/pl-upcoming-queue.js"></script>' in html
            assert 'data-queue-items="3"' in html

        def it_gives_several_items_arrows_a_time_bar_and_a_pause_button_and_no_dots(client: Client):
            member = _linked_member()
            _three_events()
            html = _home(client, member)
            assert 'aria-label="Previous upcoming item"' in html
            assert 'aria-label="Next upcoming item"' in html
            assert 'aria-label="Pause the upcoming list"' in html
            assert "data-rotator-bar" in html
            assert "data-rotator-dot" not in html

        def it_gives_one_item_no_controls_and_no_list(client: Client):
            member = _linked_member()
            CommunityEventFactory(community=True)
            html = _home(client, member)
            assert 'data-queue-items="1"' in html
            for absent in (
                "data-queue-prev",
                "data-queue-next",
                "data-queue-pause",
                "data-rotator-bar",
                "data-queue-list",
            ):
                assert absent not in html

        def it_loads_only_the_first_picture_and_defers_the_rest(client: Client):
            member = _linked_member()
            made = [
                CommunityEventFactory(community=True, title=f"Photo {n}", photo=_png(f"p{n}.png"), starts_at=_in(n + 1))
                for n in range(3)
            ]
            html = _home(client, member)
            assert f'src="{made[0].hero_image_url}"' in html
            for later in made[1:]:
                assert f'data-queue-src="{later.hero_image_url}"' in html
                assert f' src="{later.hero_image_url}"' not in html

    def describe_query_budget():
        def _count_home_queries(client: Client) -> int:
            with CaptureQueriesContext(connection) as captured:
                assert client.get(reverse("hub_home")).status_code == 200
            return len(captured.captured_queries)

        def _one_of_each(member: Member, guild: object, n: int) -> None:
            _booking(member, days=1 + n / 10)
            CommunityEventFactory(community=True, area=LocationFactory(), starts_at=_in(2 + n), photo=_png())
            CommunityEventFactory(guild=guild, starts_at=_in(2 + n, 1))
            _class(f"Class {n}", days=1 + n / 10, instructors=[MemberFactory()])

        def it_costs_the_same_however_many_items_of_every_kind_exist(client: Client):
            member = _linked_member()
            guild = GuildFactory()
            GuildMembershipFactory(guild=guild, member=member)
            client.force_login(member.user)
            _one_of_each(member, guild, 0)
            _count_home_queries(client)  # the first load also does once per session work
            few = _count_home_queries(client)
            for n in range(1, 8):
                _one_of_each(member, guild, n)
            many = _count_home_queries(client)
            assert many == few

        def it_reads_every_kind_in_five_queries(django_assert_num_queries):
            # The bookings, the events with their guild and area, the class rows, the sessions behind
            # them with each class and category, and every instructor between those classes.
            member = MemberFactory()
            guild = GuildFactory()
            GuildMembershipFactory(guild=guild, member=member)
            category = CategoryFactory(hero_image=_png("category.png"))
            for n in range(3):
                _booking(member, days=1 + n)
                CommunityEventFactory(guild=guild, area=LocationFactory(), starts_at=_in(2 + n))
                _class(f"Class {n}", days=1 + n, image="", category=category, instructors=[MemberFactory()])
            with django_assert_num_queries(5):
                items = _upcoming_items(member)
                assert all(item.instructors and item.photo_url for item in items if item.kind == "Class")
                assert all(item.location for item in items if item.kind == "Meeting")

        def it_reads_no_sessions_when_no_class_is_upcoming(django_assert_num_queries):
            member = MemberFactory()
            CommunityEventFactory(community=True)
            with django_assert_num_queries(3):
                assert len(_upcoming_items(member)) == 1


def describe_card_photo_url():
    def it_is_the_class_photo_first():
        offering = ClassOfferingFactory(category=CategoryFactory(hero_image=_png("category.png")))
        assert offering.card_photo_url == offering.hero_image_url

    def it_is_the_category_photo_without_one():
        offering = ClassOfferingFactory(image="", category=CategoryFactory(hero_image=_png("category.png")))
        assert offering.card_photo_url == offering.category.hero_image.url

    def it_is_empty_with_neither():
        assert ClassOfferingFactory(image="").card_photo_url == ""
