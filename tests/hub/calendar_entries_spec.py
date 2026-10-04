"""BDD specs for the synthetic calendar-entry wrapper."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from core.models import SiteConfiguration
from hub.calendar_entries import (
    MAKERSPACE_LEGEND_KEY,
    ORIENTATION_PK_OFFSET,
    RESERVATION_PK_OFFSET,
    CalendarEntry,
    calendar_subscribe_links,
    google_calendar_add_url,
    google_calendar_event_url,
    google_calendar_subscribe_url,
    google_target_feed_keys,
    orientation_legend_key,
    orientation_page_entries,
    reservation_entries,
)
from membership.models import CommunityEvent, EquipmentReservation, OrientationBooking
from tests.membership.factories import (
    CommunityEventFactory,
    EquipmentFactory,
    EquipmentReservationFactory,
    GuildFactory,
    MemberFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)


def describe_google_calendar_subscribe_url():
    def it_builds_a_webcal_url_and_encodes_the_calendar_id():
        url = google_calendar_subscribe_url("abc123@group.calendar.google.com")
        assert url == "webcal://calendar.google.com/calendar/ical/abc123%40group.calendar.google.com/public/basic.ics"

    def it_returns_an_empty_string_for_a_blank_id():
        assert google_calendar_subscribe_url("") == ""


def _entry(**kwargs) -> CalendarEntry:
    now = timezone.now()
    base = {"pk": 1, "title": "x", "start_dt": now, "end_dt": now + timedelta(hours=1), "source": "classes"}
    base.update(kwargs)
    return CalendarEntry(**base)


def describe_CalendarEntry():
    def it_exposes_source_as_source_key():
        assert _entry(source="orientation").source_key == "orientation"

    def it_is_in_progress_between_start_and_end():
        now = timezone.now()
        assert _entry(start_dt=now - timedelta(hours=1), end_dt=now + timedelta(hours=1)).is_in_progress is True

    def it_is_not_in_progress_before_it_starts():
        now = timezone.now()
        assert _entry(start_dt=now + timedelta(hours=1), end_dt=now + timedelta(hours=2)).is_in_progress is False

    def it_is_never_in_progress_for_all_day_entries():
        now = timezone.now()
        entry = _entry(start_dt=now - timedelta(hours=1), end_dt=now + timedelta(hours=1), all_day=True)
        assert entry.is_in_progress is False


@pytest.mark.django_db
def describe_CalendarEntry_source_key():
    def it_keys_a_class_entry_by_its_guild():
        guild = GuildFactory()
        assert _entry(source="classes", guild=guild).source_key == str(guild.pk)

    def it_falls_back_to_classes_for_a_class_entry_with_no_guild():
        assert _entry(source="classes", guild=None).source_key == "classes"

    def it_keeps_orientation_its_own_color_even_with_a_guild():
        # Orientation stays amber — the guild must NOT recolor it.
        guild = GuildFactory()
        assert _entry(source="orientation", guild=guild).source_key == "orientation"

    def it_keeps_community_its_own_color_even_with_a_guild():
        # Community stays blue — the guild must NOT recolor it.
        guild = GuildFactory()
        assert _entry(source="community", guild=guild).source_key == "community"

    def it_prefers_a_stamped_feed_key_over_the_raw_source():
        assert _entry(source="community", feed_key="feed-7").source_key == "feed-7"

    def it_prefers_a_stamped_feed_key_even_when_a_guild_is_set():
        guild = GuildFactory()
        assert _entry(source="community", guild=guild, feed_key="feed-7").source_key == "feed-7"

    def it_prefers_a_page_legend_key_over_a_feed_key_and_a_guild():
        guild = GuildFactory()
        entry = _entry(source="classes", guild=guild, feed_key="feed-7", legend_key="makerspace")
        assert entry.source_key == "makerspace"

    def it_carries_no_chip_title_or_owner_label_unless_given():
        entry = _entry()
        assert (entry.legend_key, entry.chip_title, entry.owner_label) == ("", "", "")


@pytest.mark.django_db
def describe_google_target_feed_keys():
    def _configure(member_id: str = "", public_id: str = "") -> None:
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.member_google_calendar_id = member_id
        config.public_google_calendar_id = public_id
        config.save()

    def it_maps_each_target_to_the_feed_embedding_its_calendar_id():
        from core.models import CalendarFeed

        _configure(member_id="member@group.calendar.google.com", public_id="public@group.calendar.google.com")
        # One feed embeds the id percent-encoded (Google's iCal URL shape), the other raw —
        # both forms must match.
        member_feed = CalendarFeed.objects.create(
            name="Member Calendar",
            ical_url="https://calendar.google.com/calendar/ical/member%40group.calendar.google.com/public/basic.ics",
            color="#eeb44b",
        )
        public_feed = CalendarFeed.objects.create(
            name="Public Calendar",
            ical_url="https://calendar.google.com/calendar/ical/public@group.calendar.google.com/public/basic.ics",
            color="#6fd880",
        )
        assert google_target_feed_keys() == {
            "member": f"feed-{member_feed.pk}",
            "public": f"feed-{public_feed.pk}",
        }

    def it_skips_a_target_whose_calendar_id_is_unset():
        from core.models import CalendarFeed

        _configure(public_id="public@group.calendar.google.com")
        feed = CalendarFeed.objects.create(
            name="Public Calendar",
            ical_url="https://calendar.google.com/calendar/ical/public%40group.calendar.google.com/public/basic.ics",
            color="#6fd880",
        )
        assert google_target_feed_keys() == {"public": f"feed-{feed.pk}"}

    def it_skips_a_target_with_no_matching_feed():
        from core.models import CalendarFeed

        _configure(member_id="member@group.calendar.google.com", public_id="public@group.calendar.google.com")
        CalendarFeed.objects.create(name="Unrelated", ical_url="https://example.com/other.ics", color="#888888")
        assert google_target_feed_keys() == {}

    def it_maps_both_targets_to_the_same_feed_when_they_share_a_calendar_id():
        # Degenerate config: both targets point at one Google calendar → both keys
        # resolve to that single feed's chip rather than one target being dropped.
        from core.models import CalendarFeed

        _configure(member_id="shared@group.calendar.google.com", public_id="shared@group.calendar.google.com")
        feed = CalendarFeed.objects.create(
            name="Shared Calendar",
            ical_url="https://calendar.google.com/calendar/ical/shared%40group.calendar.google.com/public/basic.ics",
            color="#6fd880",
        )
        assert google_target_feed_keys() == {"member": f"feed-{feed.pk}", "public": f"feed-{feed.pk}"}


def _guild_series(day_offsets: list[int]) -> object:
    """A guild with a published series whose sessions fall at the given day offsets."""
    guild = GuildFactory()
    category = CategoryFactory(guild=guild)
    offering = ClassOfferingFactory(
        status=ClassOffering.Status.PUBLISHED,
        category=category,
        scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE,
    )
    for days in day_offsets:
        start = timezone.now() + timedelta(days=days)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return guild


@pytest.mark.django_db
def describe_guild_calendar_entries():
    def _window() -> tuple[object, object]:
        today = timezone.now().date()
        return today - timedelta(days=10), today + timedelta(days=30)

    def it_omits_a_started_series():
        # A guild's own calendar must drop a started series just like the catalog.
        from hub.calendar_entries import guild_calendar_entries

        guild = _guild_series([-3, 4, 11])
        fetch_from, fetch_to = _window()

        entries = guild_calendar_entries(guild, fetch_from, fetch_to)

        assert [e for e in entries if e.source == "classes"] == []

    def it_keeps_a_future_series():
        from hub.calendar_entries import guild_calendar_entries

        guild = _guild_series([2, 9, 16])
        fetch_from, fetch_to = _window()

        entries = guild_calendar_entries(guild, fetch_from, fetch_to)

        class_entries = [e for e in entries if e.source == "classes"]
        assert len(class_entries) == 3


def describe_google_calendar_add_url():
    def it_builds_google_calendars_add_link_with_the_id_encoded():
        assert (
            google_calendar_add_url("abc123@group.calendar.google.com")
            == "https://calendar.google.com/calendar/r?cid=abc123%40group.calendar.google.com"
        )

    def it_is_blank_when_no_calendar_is_configured():
        assert google_calendar_add_url("") == ""


@pytest.mark.django_db
def describe_calendar_subscribe_links():
    def _configure(member: str, public: str) -> SiteConfiguration:
        config = SiteConfiguration.load()
        config.member_google_calendar_id = member
        config.public_google_calendar_id = public
        config.save()
        return config

    def it_gives_each_configured_calendar_both_link_forms():
        rows = calendar_subscribe_links(_configure("mem@group.calendar.google.com", "pub@group.calendar.google.com"))
        assert [row["key"] for row in rows] == ["member", "public"]
        assert rows[0] == {
            "key": "member",
            "label": "Member calendar",
            "webcal_url": google_calendar_subscribe_url("mem@group.calendar.google.com"),
            "google_url": google_calendar_add_url("mem@group.calendar.google.com"),
        }
        assert rows[1]["label"] == "Public calendar"

    def it_skips_a_calendar_with_no_id():
        assert [row["key"] for row in calendar_subscribe_links(_configure("", "pub@group.calendar.google.com"))] == [
            "public"
        ]
        assert calendar_subscribe_links(_configure("", "")) == []


@pytest.mark.django_db
def describe_google_calendar_event_url():
    def _params(url: str) -> dict[str, str]:
        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://calendar.google.com/calendar/render"
        return {key: values[0] for key, values in parse_qs(parts.query).items()}

    def it_fills_in_one_event_in_the_makerspaces_local_time():
        pacific = ZoneInfo("America/Los_Angeles")
        event = CommunityEventFactory(
            community=True,
            title="Potluck & Pins",
            location="Common Area",
            description="Bring a dish.",
            starts_at=datetime(2026, 10, 10, 18, 0, tzinfo=pacific),
            ends_at=datetime(2026, 10, 10, 20, 30, tzinfo=pacific),
        )
        params = _params(google_calendar_event_url(event, event.starts_at, "https://pastlives.space/events/1/"))
        assert params == {
            "action": "TEMPLATE",
            "text": "Potluck & Pins",
            "dates": "20261010T180000/20261010T203000",
            "ctz": "America/Los_Angeles",
            "details": "Bring a dish.\n\nhttps://pastlives.space/events/1/",
            "location": "Common Area",
        }

    def it_lists_the_video_link_before_the_page_link_in_the_details():
        event = CommunityEventFactory(community=True, description="", video_url="https://meet.google.com/abc")
        params = _params(google_calendar_event_url(event, event.starts_at, "https://pastlives.space/events/1/"))
        assert params["details"] == "https://meet.google.com/abc\n\nhttps://pastlives.space/events/1/"

    def it_leaves_out_what_the_event_does_not_have():
        event = CommunityEventFactory(community=True, description="", location="", video_url="")
        params = _params(google_calendar_event_url(event, event.starts_at))
        assert set(params) == {"action", "text", "dates", "ctz"}

    def it_trims_a_long_description_so_the_link_stays_usable():
        event = CommunityEventFactory(community=True, description="x" * 5000)
        assert len(_params(google_calendar_event_url(event, event.starts_at))["details"]) == 3000

    def it_trims_by_encoded_length_so_emoji_cannot_overrun_it():
        event = CommunityEventFactory(community=True, description="\U0001f525" * 5000)
        details = _params(google_calendar_event_url(event, event.starts_at))["details"]
        assert details == "\U0001f525" * 250  # 12 encoded bytes each

    def it_carries_the_series_rule_on_the_local_weekday():
        # Monday 6 PM in Portland is Tuesday in UTC; the rule and the dates must both say Monday.
        event = CommunityEventFactory(
            community=True,
            recurrence=CommunityEvent.Recurrence.WEEKLY,
            starts_at=datetime(2026, 10, 6, 1, 0, tzinfo=UTC),
            ends_at=datetime(2026, 10, 6, 3, 0, tzinfo=UTC),
        )
        params = _params(google_calendar_event_url(event, event.starts_at))
        assert params["recur"] == "RRULE:FREQ=WEEKLY;BYDAY=MO"
        assert params["dates"] == "20261005T180000/20261005T200000"

    def it_starts_a_series_on_the_date_given_and_keeps_its_length():
        # A member opened the November date; the series starts there, not on its first date.
        event = CommunityEventFactory(
            community=True,
            recurrence=CommunityEvent.Recurrence.MONTHLY,
            starts_at=datetime(2026, 9, 2, 18, 0, tzinfo=ZoneInfo("America/Los_Angeles")),
            ends_at=datetime(2026, 9, 2, 20, 30, tzinfo=ZoneInfo("America/Los_Angeles")),
        )
        november = datetime(2026, 11, 4, 18, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        params = _params(google_calendar_event_url(event, november))
        assert params["dates"] == "20261104T180000/20261104T203000"
        assert params["recur"] == "RRULE:FREQ=MONTHLY;BYDAY=1WE"


def _calendar_range() -> tuple[date, date]:
    today = timezone.localdate()
    return today - timedelta(days=1), today + timedelta(days=30)


@pytest.mark.django_db
def describe_orientation_legend_key():
    def it_files_a_guild_type_under_its_guild():
        guild = GuildFactory(name="Legend Guild")
        assert orientation_legend_key(OrientationTypeFactory(guild=guild)) == str(guild.pk)

    def it_files_an_items_type_under_the_items_guild():
        guild = GuildFactory(name="Legend Item Guild")
        orientation_type = OrientationTypeFactory(guild=None, equipment=EquipmentFactory(guild=guild))
        assert orientation_legend_key(orientation_type) == str(guild.pk)

    def it_files_a_standalone_items_type_under_makerspace():
        orientation_type = OrientationTypeFactory(equipment_owned=True)
        assert orientation_legend_key(orientation_type) == MAKERSPACE_LEGEND_KEY == "makerspace"


@pytest.mark.django_db
def describe_orientation_page_entries():
    def it_makes_one_entry_per_bookable_slot_with_the_type_on_the_chip():
        guild = GuildFactory(name="Entries Guild")
        orientation_type = OrientationTypeFactory(guild=guild, name="Shop Basics")
        slot = OrientationSlotFactory(guild=guild, orientation_type=orientation_type, seats=4, location="Wood Shop")
        [entry] = orientation_page_entries([orientation_type], *_calendar_range())
        assert entry.pk == ORIENTATION_PK_OFFSET + slot.pk
        assert entry.chip_title == "Shop Basics"
        assert entry.title == "Shop Basics · Entries Guild · 4 seats left"
        assert entry.source == "orientation"
        assert entry.source_key == entry.legend_key == str(guild.pk)
        assert entry.owner_label == "Entries Guild"
        assert entry.location == "Wood Shop"
        assert (entry.start_dt, entry.end_dt) == (slot.starts_at, slot.ends_at)

    def it_links_the_types_card_on_the_list_view():
        orientation_type = OrientationTypeFactory(guild=GuildFactory(name="Link Guild"))
        OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type)
        [entry] = orientation_page_entries([orientation_type], *_calendar_range())
        assert (
            entry.url
            == orientation_type.orientations_page_path()
            == f"/orientations/#orientation-type-{orientation_type.pk}"
        )

    def it_says_seat_for_the_last_one():
        orientation_type = OrientationTypeFactory(guild=GuildFactory(name="One Seat Guild"), name="Lathe")
        slot = OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type, seats=2)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        [entry] = orientation_page_entries([orientation_type], *_calendar_range())
        assert entry.title == "Lathe · One Seat Guild · 1 seat left"

    def it_names_an_items_owner_and_files_it_under_the_items_guild():
        guild = GuildFactory(name="Woodworking Guild")
        item = EquipmentFactory(name="Lathe", guild=guild)
        orientation_type = OrientationTypeFactory(guild=None, equipment=item, name="Lathe Orientation")
        OrientationSlotFactory(guild=None, orientation_type=orientation_type, seats=3)
        [entry] = orientation_page_entries([orientation_type], *_calendar_range())
        assert entry.title == "Lathe Orientation · Lathe · 3 seats left"
        assert entry.legend_key == str(guild.pk)
        assert entry.owner_label == "Lathe · Woodworking Guild"

    def it_files_a_standalone_items_slot_under_makerspace():
        item = EquipmentFactory(name="Loading dock")
        orientation_type = OrientationTypeFactory(guild=None, equipment=item, name="Dock Basics")
        OrientationSlotFactory(guild=None, orientation_type=orientation_type)
        [entry] = orientation_page_entries([orientation_type], *_calendar_range())
        assert entry.legend_key == "makerspace"
        assert entry.owner_label == "Loading dock · Makerspace"

    def it_leaves_out_a_full_slot_and_a_cancelled_one():
        orientation_type = OrientationTypeFactory(guild=GuildFactory(name="Full Guild"))
        open_slot = OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type)
        full = OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type, seats=1)
        OrientationBookingFactory(slot=full, status=OrientationBooking.Status.PENDING_PAYMENT)
        OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type, is_cancelled=True)
        entries = orientation_page_entries([orientation_type], *_calendar_range())
        assert [entry.pk for entry in entries] == [ORIENTATION_PK_OFFSET + open_slot.pk]

    def it_leaves_out_slots_of_types_outside_the_set_and_outside_the_range():
        guild = GuildFactory(name="Range Guild")
        listed = OrientationTypeFactory(guild=guild, name="Listed")
        other = OrientationTypeFactory(guild=guild, name="Other")
        OrientationSlotFactory(guild=guild, orientation_type=other)
        far = timezone.now() + timedelta(days=60)
        OrientationSlotFactory(guild=guild, orientation_type=listed, starts_at=far, ends_at=far + timedelta(hours=1))
        assert orientation_page_entries([listed], *_calendar_range()) == []

    def it_leaves_out_a_slot_its_owner_is_not_taking_bookings_for():
        orientation_type = OrientationTypeFactory(guild=GuildFactory(name="Closed Guild"))
        OrientationSlotFactory(guild=orientation_type.guild, orientation_type=orientation_type)
        orientation_type.guild.orientation_settings.is_closed = True
        orientation_type.guild.orientation_settings.save()
        assert orientation_page_entries([orientation_type], *_calendar_range()) == []

    def it_reads_every_slot_in_one_query(django_assert_num_queries):
        guild = GuildFactory(name="Query Guild")
        types = [OrientationTypeFactory(guild=guild, name=f"Type {n}") for n in range(3)]
        for orientation_type in types:
            OrientationSlotFactory(guild=guild, orientation_type=orientation_type)
        with django_assert_num_queries(1):
            assert len(orientation_page_entries(types, *_calendar_range())) == 3


@pytest.mark.django_db
def describe_reservation_entries():
    def _item(name: str = "Laser cutter", guild_name: str = "") -> object:
        return EquipmentFactory(name=name, guild=GuildFactory(name=guild_name) if guild_name else None)

    def it_lists_a_confirmed_reservation_under_its_item():
        item = _item(guild_name="Fabrication Guild")
        reservation = EquipmentReservationFactory(
            equipment=item, member=MemberFactory(full_legal_name="Sam Reyes", preferred_name="")
        )
        [entry] = reservation_entries([item], *_calendar_range())
        assert entry.pk == RESERVATION_PK_OFFSET + reservation.pk
        assert entry.title == "Laser cutter · Sam R."
        assert entry.source == "reservation"
        assert entry.source_key == entry.legend_key == str(item.pk)
        assert entry.owner_label == "Laser cutter · Fabrication Guild"
        assert (entry.start_dt, entry.end_dt) == (reservation.starts_at, reservation.ends_at)

    def it_links_the_items_page_on_the_reservations_local_day():
        item = _item(name="Table saw")
        # 03:30 UTC is the evening before in Portland: the link names the local day.
        starts_at = (timezone.now() + timedelta(days=3)).replace(hour=3, minute=30, second=0, microsecond=0)
        reservation = EquipmentReservationFactory(equipment=item, starts_at=starts_at)
        [entry] = reservation_entries([item], *_calendar_range())
        day = timezone.localdate(reservation.starts_at)
        assert day != reservation.starts_at.date()
        assert entry.url == f"/equipment/{item.slug}/?day={day.isoformat()}"

    def it_lists_a_booked_orientation_on_an_items_own_type():
        item = _item(name="Lathe", guild_name="Woodworking Guild")
        orientation_type = OrientationTypeFactory(guild=None, equipment=item)
        slot = OrientationSlotFactory(guild=None, orientation_type=orientation_type, location="Wood Shop")
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.REQUESTED)
        [entry] = reservation_entries([item], *_calendar_range())
        assert entry.pk == ORIENTATION_PK_OFFSET + slot.pk
        assert entry.title == "Lathe · Orientation"
        assert entry.source == "orientation"
        assert entry.legend_key == str(item.pk)
        assert entry.location == "Wood Shop"
        assert entry.url == f"/equipment/{item.slug}/?day={timezone.localdate(slot.starts_at).isoformat()}"

    def it_links_the_bare_items_page_for_a_past_reservation():
        item = _item(name="Past saw")
        yesterday = timezone.now() - timedelta(days=1)
        EquipmentReservationFactory(equipment=item, starts_at=yesterday)
        [entry] = reservation_entries([item], timezone.localdate() - timedelta(days=3), timezone.localdate())
        assert entry.url == f"/equipment/{item.slug}/"

    def it_links_the_bare_items_page_for_a_hold_beyond_the_horizon():
        item = EquipmentFactory(name="Far lathe", max_advance_days=7)
        later = timezone.now() + timedelta(days=20)
        slot = OrientationSlotFactory(
            guild=None,
            orientation_type=OrientationTypeFactory(guild=None, equipment=item),
            starts_at=later,
            ends_at=later + timedelta(hours=1),
        )
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        [entry] = reservation_entries([item], timezone.localdate(), timezone.localdate() + timedelta(days=30))
        assert entry.url == f"/equipment/{item.slug}/"

    def it_keeps_the_day_on_the_last_day_of_the_horizon():
        item = EquipmentFactory(name="Edge saw", max_advance_days=7)
        last = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=7)
        EquipmentReservationFactory(equipment=item, starts_at=last)
        [entry] = reservation_entries([item], timezone.localdate(), timezone.localdate() + timedelta(days=30))
        assert entry.url == f"/equipment/{item.slug}/?day={last.date().isoformat()}"

    def it_leaves_out_a_cancelled_reservation_and_an_open_unbooked_slot():
        item = _item()
        EquipmentReservationFactory(equipment=item, status=EquipmentReservation.Status.CANCELLED)
        orientation_type = OrientationTypeFactory(guild=None, equipment=item)
        OrientationSlotFactory(guild=None, orientation_type=orientation_type)
        assert reservation_entries([item], *_calendar_range()) == []

    def it_leaves_out_other_items_and_a_cancelled_booked_slot():
        item, other = _item(name="Mine"), _item(name="Other")
        EquipmentReservationFactory(equipment=other)
        orientation_type = OrientationTypeFactory(guild=None, equipment=item)
        slot = OrientationSlotFactory(guild=None, orientation_type=orientation_type, is_cancelled=True)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        assert reservation_entries([item], *_calendar_range()) == []

    def it_reads_everything_in_two_queries(django_assert_num_queries):
        items = [_item(name=f"Item {n}") for n in range(3)]
        for item in items:
            EquipmentReservationFactory(equipment=item)
            slot = OrientationSlotFactory(
                guild=None, orientation_type=OrientationTypeFactory(guild=None, equipment=item)
            )
            OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        with django_assert_num_queries(2):
            assert len(reservation_entries(items, *_calendar_range())) == 6
