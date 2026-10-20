"""The PL Ledger's team and tally (Track D, part 6c): the gates on every post and CSV, the running tally read live from
each door (parity with each source, the links, Lee's countdown and cheer lines, a fixed number of queries), the Goals
(her groups, chips and anchors, targets from the hub's goals, Change the goals), the Guilds taking part goal and its
top-up, Attendance and vendors (check-ins plus the door clicker, Undo floored at zero, the reports, the CSV), the
launch checklist (rows, the stale check, late on the Portland date), Staff time (the hours' bounds, raised per hour,
the CSV), Guild demos (one per guild, Add every active guild, the stale check), the read only Volunteer hours and Event
flyer sections, the whole board paragraph, and the page's queries as rows grow."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from hq import services
from hq.exports import PEOPLE_CSV_HEADERS, STAFF_TIME_CSV_HEADERS
from hq.forms import HOURS_ERROR, HOURS_STEP_ERROR
from hq.holiday_bizarre import GOALS
from hq.models import (
    BarSale,
    BudgetLine,
    DoorCount,
    GuildDemo,
    HubCard,
    HubFlyer,
    HubGoal,
    LaunchTask,
    LedgerEntry,
    MarketingKind,
    MarketReport,
    StaffTime,
    VolunteerHours,
    hours_words,
)
from hub.view_as import ROLE_MEMBER
from tests.hq.conftest import photo_upload, signed_in
from tests.hq.factories import (
    BarSaleFactory,
    BudgetLineFactory,
    DoorCountFactory,
    EventHubFactory,
    GuildDemoFactory,
    HubCardFactory,
    HubGoalFactory,
    LaunchTaskFactory,
    LedgerEntryFactory,
    LedgerSettingsFactory,
    MarketingActionFactory,
    SponsorFactory,
    StaffTimeFactory,
    VendorTableFactory,
    WineBottleFactory,
)
from tests.membership.factories import CommunityEventFactory, EventRSVPFactory, GuildFactory

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
PORTLAND = ZoneInfo("America/Los_Angeles")
K = LedgerEntry.Kind


def _page(hub: Any) -> str:
    return reverse("hq:ledger", args=[hub.slug])


def _url(name: str, hub: Any, *args: Any) -> str:
    return reverse(f"hq:{name}", args=[hub.slug, *args])


def _toast(response: Any) -> dict[str, str]:
    return json.loads(response["HX-Trigger"])["showToast"]


def _tally(hub: Any, now: datetime | None = None, **kwargs: Any) -> services.LedgerTally:
    return services.ledger_tally(hub, now=now or timezone.now(), **kwargs)


def _tiles(hub: Any, **kwargs: Any) -> dict[str, services.TallyTile]:
    return {tile.key: tile for tile in _tally(hub, **kwargs).tiles}


def _cheer(hub: Any, now: datetime | None = None) -> list[str]:
    return [f"{line.before}{line.bold}{line.after}" for line in _tally(hub, now).cheer]


def _goal_rows(hub: Any) -> dict[str, services.LedgerGoalRow]:
    band = services.ledger_band(hub)
    groups = services.ledger_goals(
        band,
        services.door_figures(hub, band),
        launch=services.launch_totals(hub),
        flyer_ready=bool(services.flyer_page(hub).flyer),
    )
    return {row.key: row for group in groups for row in group.rows}


def _hours(hub: Any, member: Any, hours: str) -> VolunteerHours:
    return VolunteerHours.objects.create(hub=hub, member=member, hours=Decimal(hours))


def _report(hub: Any, member: Any, **fields: Any) -> MarketReport:
    return MarketReport.objects.create(hub=hub, member=member, **{"name": "Riverbend Ceramics", **fields})


def _stamp(row: Any) -> str:
    return row.updated_at.isoformat()


def _task_post(row: LaunchTask, **fields: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "task": row.task,
        "owner": row.owner,
        "status": row.status,
        "due": row.due.isoformat() if row.due else "",
        "link": row.link,
        "updated_at": _stamp(row),
    }
    values.update(fields)
    return {f"task-{row.pk}-{key}": value for key, value in values.items()}


def _guild_post(row: GuildDemo, **fields: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "lead": row.lead,
        "contacted_by": row.contacted_by,
        "note": row.note,
        "updated_at": _stamp(row),
    }
    if row.contacted:
        values["contacted"] = "on"
    if row.demo:
        values["demo"] = "on"
    values.update(fields)
    return {f"guild-{row.pk}-{key}": value for key, value in values.items() if value is not None}


def _staff_post(**fields: Any) -> dict[str, Any]:
    values: dict[str, Any] = {"who": "Lee", "date": "2026-11-20", "hours": "1.5", "area": "planning", "note": ""}
    values.update(fields)
    return {f"staff-{key}": value for key, value in values.items()}


@pytest.fixture
def lee(make_member):
    return make_member(preferred_name="Lee Ashworth")


@pytest.fixture
def card(open_hub, lee) -> HubCard:
    """The ledger door, open to everyone (so the 403 is what stops a non-organiser), Lee named."""
    return HubCardFactory(
        hub=open_hub, kind=HubCard.Kind.LEDGER, title="PL Ledger", audience=HubCard.Audience.PUBLIC, named=[lee]
    )


@pytest.fixture
def hub(card):
    return card.hub


@pytest.fixture
def lee_client(lee) -> Client:
    return signed_in(lee)


@pytest.fixture
def doors(hub) -> dict[str, HubCard]:
    """Every other door on the hub, open to everyone."""
    kinds = (
        HubCard.Kind.SPONSORSHIP,
        HubCard.Kind.WINE_WALL,
        HubCard.Kind.VENDOR_TABLES,
        HubCard.Kind.MARKET_CHECKIN,
        HubCard.Kind.MARKETING,
        HubCard.Kind.FLYER,
        HubCard.Kind.VOLUNTEERS,
    )
    return {
        kind: HubCardFactory(hub=hub, kind=kind, title=f"Door {kind}", audience=HubCard.Audience.PUBLIC)
        for kind in kinds
    }


@pytest.fixture
def event(hub):
    """The hub's event, December 4 at 5 PM Portland, a community event so it brings no guild with it."""
    event = CommunityEventFactory(
        community=True, title="Holiday Bizarre", starts_at=datetime(2026, 12, 4, 17, 0, tzinfo=PORTLAND)
    )
    hub.event = event
    hub.save(update_fields=["event"])
    return event


@pytest.fixture
def busy(hub, event, make_member):
    """Every door with something on it: sponsors, the bar, the wine wall and its tickets, vendors and their reports,
    flyers, volunteer hours, check-ins and walk-ins, guild demos, staff time and launch tasks."""
    HubGoalFactory(hub=hub, kind=HubGoal.Kind.EVENT_CASH, label="Raised", target=10_000, is_money=True)
    HubGoalFactory(hub=hub, kind=HubGoal.Kind.BOTTLES, label="Bottles", target=100)
    HubGoalFactory(hub=hub, kind=HubGoal.Kind.RSVPS, label="RSVPs", target=250)
    SponsorFactory(hub=hub, status="received", amount_cents=150_000)
    SponsorFactory(hub=hub, status="committed", amount_cents=50_000)
    BarSaleFactory(hub=hub, qty=40, amount_cents=24_000)
    WineBottleFactory.create_batch(3, hub=hub, qty=4, value_cents=2_500)
    LedgerSettingsFactory(hub=hub, singles_sold=3, pairs_sold=1)
    VendorTableFactory.create_batch(2, hub=hub, status="confirmed")
    VendorTableFactory(hub=hub, status="requested")
    artist, other = make_member(), make_member()
    _report(hub, artist, name="Riverbend Ceramics", items_sold=14, sales_cents=42_000, experience="Steady all night")
    _report(hub, other, name="Ash and Oak", items_sold=None, sales_cents=0)
    MarketingActionFactory.create_batch(3, hub=hub, kind=MarketingKind.FLYER)
    _hours(hub, artist, "2.5")
    _hours(hub, other, "3.25")
    EventRSVPFactory(event=event, party_size=3, checked_in_at=timezone.now())
    EventRSVPFactory(event=event, party_size=2)
    DoorCountFactory(hub=hub, delta=10)
    DoorCountFactory(hub=hub, delta=-1)
    GuildDemoFactory(hub=hub, contacted=True, demo=True)
    GuildDemoFactory(hub=hub, contacted=True)
    GuildDemoFactory(hub=hub)
    StaffTimeFactory(hub=hub, who="Lee", hours=Decimal("6"))
    StaffTimeFactory(hub=hub, who="Augustin", hours=Decimal("2"), area=StaffTime.Area.BAR_WINE)
    LaunchTaskFactory(hub=hub, status=LaunchTask.Status.DONE)
    LaunchTaskFactory(hub=hub)
    kegs = BudgetLineFactory(hub=hub, name="Kegs")
    LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_APPROVAL, amount_cents=30_000)
    return hub


def describe_who_may_use_it():
    def it_refuses_every_write_and_csv_to_anyone_but_an_organiser_and_leaks_nothing(hub, member_client, lee):
        task = LaunchTaskFactory(hub=hub, task="Spec secret task")
        entry = StaffTimeFactory(hub=hub, who="Spec secret staffer")
        row = GuildDemoFactory(hub=hub, lead="Spec secret lead")
        GuildFactory(name="Spec Missing Guild")
        _report(hub, lee, experience="Spec secret experience")

        for client in (member_client, Client()):
            for name, args, data in (
                ("ledger_door", (), {"delta": "1"}),
                ("ledger_launch_add", (), {"launch-task": "New"}),
                ("ledger_launch_edit", (task.pk,), _task_post(task, task="Changed")),
                ("ledger_launch_delete", (task.pk,), {}),
                ("ledger_staff_add", (), _staff_post()),
                ("ledger_staff_delete", (entry.pk,), {}),
                ("ledger_guild_add", (), {"guild-add-guild": "1"}),
                ("ledger_guilds_add_all", (), {}),
                ("ledger_guild_edit", (row.pk,), _guild_post(row, lead="Changed")),
                ("ledger_guild_delete", (row.pk,), {}),
            ):
                response = client.post(_url(name, hub, *args), data, **HTMX)
                assert response.status_code == 403, name
                body = response.content.decode()
                assert "Spec secret" not in body, name
            for name in ("ledger_people_csv", "ledger_staff_csv"):
                response = client.get(_url(name, hub))
                assert response.status_code == 403, name
                assert b"Spec secret" not in response.content
        assert not DoorCount.objects.exists()
        assert list(LaunchTask.objects.values_list("task", flat=True)) == ["Spec secret task"]
        assert list(StaffTime.objects.values_list("who", flat=True)) == ["Spec secret staffer"]
        assert list(GuildDemo.objects.values_list("lead", flat=True)) == ["Spec secret lead"]

    def it_is_a_404_for_another_hubs_rows(hub, lee_client):
        elsewhere = EventHubFactory()
        task = LaunchTaskFactory(hub=elsewhere)
        entry = StaffTimeFactory(hub=elsewhere)
        row = GuildDemoFactory(hub=elsewhere)

        for name, pk, data in (
            ("ledger_launch_edit", task.pk, _task_post(task, task="Changed")),
            ("ledger_launch_delete", task.pk, {}),
            ("ledger_staff_delete", entry.pk, {}),
            ("ledger_guild_edit", row.pk, _guild_post(row, lead="Changed")),
            ("ledger_guild_delete", row.pk, {}),
        ):
            assert lee_client.post(_url(name, hub, pk), data).status_code == 404, name
        assert LaunchTask.objects.get(pk=task.pk).task == task.task
        assert StaffTime.objects.filter(pk=entry.pk).exists()
        assert GuildDemo.objects.get(pk=row.pk).lead == ""

    def it_is_a_404_on_a_hub_with_no_ledger_door(admin_client):
        bare = EventHubFactory()

        assert admin_client.post(_url("ledger_door", bare), {"delta": "1"}).status_code == 404
        assert admin_client.get(_url("ledger_people_csv", bare)).status_code == 404
        assert admin_client.get(_url("ledger_staff_csv", bare)).status_code == 404
        assert not DoorCount.objects.exists()

    def it_takes_posts_only(hub, lee_client):
        task, entry, row = LaunchTaskFactory(hub=hub), StaffTimeFactory(hub=hub), GuildDemoFactory(hub=hub)
        for url in (
            _url("ledger_door", hub),
            _url("ledger_launch_add", hub),
            _url("ledger_launch_edit", hub, task.pk),
            _url("ledger_launch_delete", hub, task.pk),
            _url("ledger_staff_add", hub),
            _url("ledger_staff_delete", hub, entry.pk),
            _url("ledger_guild_add", hub),
            _url("ledger_guilds_add_all", hub),
            _url("ledger_guild_edit", hub, row.pk),
            _url("ledger_guild_delete", hub, row.pk),
        ):
            assert lee_client.get(url).status_code == 405, url

    def it_lets_a_previewing_admin_act_on_their_actual_role(hub, admin_client, admin):
        session = admin_client.session
        session["view_as_role"] = ROLE_MEMBER
        session.save()

        assert admin_client.get(_page(hub)).status_code == 403
        assert admin_client.post(_url("ledger_door", hub), {"delta": "5"}).status_code == 302
        assert DoorCount.objects.get().created_by == admin


def describe_the_running_tally():
    def it_reads_every_tile_from_the_door_its_figure_lives_on(busy):
        hub = busy
        tiles = _tiles(hub)
        found = services.ledger_settings(hub)
        wine = services.wine_band(hub)
        checkin = services.checkin_band(hub)

        assert tiles["cash"].value == services.event_cash(hub).total_cents == 150_000 + 24_000 + 11_500
        assert (tiles["cash"].goal, tiles["cash"].sub) == (1_000_000, "of $10,000 goal")
        assert tiles["bottles"].value == wine.bottles == 12
        assert (tiles["bottles"].goal, tiles["bottles"].sub) == (100, "of 100 for the wine wall")
        assert tiles["tickets"].value == services.ticket_totals(found, wine).revenue_cents == 11_500
        assert tiles["tickets"].sub == "5 tickets · $25 a ticket or 2 for $40"
        assert tiles["tickets"].goal == 100 * 2_500
        assert tiles["guilds"].value == services.guild_demo_totals(hub).demos == 1
        assert (tiles["guilds"].goal, tiles["guilds"].sub) == (3, "of 3 guilds")
        assert tiles["tables"].value == services.vendor_band(hub).confirmed == 2
        assert tiles["sold"].value == checkin.sales_cents == 42_000
        assert tiles["sold"].sub == "14 handmade items found new homes"
        assert tiles["flyers"].value == services.marketing_totals(hub)[MarketingKind.FLYER] == 3
        assert tiles["people"].value == services.attendance(hub).total == 3 + 9
        assert (tiles["people"].goal, tiles["people"].sub) == (250, "of 250 hoped for")
        assert tiles["vol"].value == 6  # 5.75 hours, rounded half up as Lee's Math.round
        assert tiles["vol"].sub == "from 2 volunteers. Thank you!"
        assert [tile.label for tile in _tally(hub).tiles] == [
            "Cash to Past Lives",
            "Wine bottles gifted",
            "Wine wall ticket sales",
            "Guilds doing demos",
            "Members with tables",
            "Members sold",
            "Businesses with flyers",
            "Attendees",
            "Volunteer hours",
        ]

    def it_says_lees_empty_lines_with_nothing_in(hub):
        tiles = _tiles(hub)

        assert tiles["sold"].sub == "straight into artists' pockets"
        assert tiles["bottles"].sub == "for the wine wall"
        assert tiles["bottles"].goal == 0 and tiles["people"].goal == 0
        assert tiles["people"].sub == "0 checked in · 0 at the door"
        assert tiles["vol"].sub == "from 0 volunteers. Thank you!"
        assert tiles["cash"].value_display == "$0"

    def it_links_each_tile_to_its_door_only_while_that_door_is_open_to_the_viewer(busy, doors, lee_client):
        doors[HubCard.Kind.MARKETING].audience = HubCard.Audience.ADMINS
        doors[HubCard.Kind.MARKETING].save(update_fields=["audience"])
        doors[HubCard.Kind.VENDOR_TABLES].is_hidden = True
        doors[HubCard.Kind.VENDOR_TABLES].save(update_fields=["is_hidden"])

        body = lee_client.get(_page(busy)).content.decode()

        def _tile(key: str) -> str:
            start = body.index(f'data-hq-lg-tally-tile="{key}"')
            return body[body.rindex("<", 0, start) : start]

        assert f'href="{_url("sponsors", busy)}"' in _tile("cash")
        assert f'href="{_url("wine", busy)}"' in _tile("bottles")
        assert 'href="#hq-lg-wine"' in _tile("tickets")
        assert 'href="#hq-lg-guilds"' in _tile("guilds")
        assert 'href="#hq-lg-people"' in _tile("people")
        assert f'href="{_url("checkin", busy)}"' in _tile("sold")
        assert _tile("flyers").startswith("<div") and "href" not in _tile("flyers")
        assert _tile("tables").startswith("<div")

    def it_counts_in_a_fixed_number_of_queries_however_much_is_logged(busy, django_assert_num_queries):
        hub = busy
        now = timezone.now()
        with django_assert_num_queries(TALLY_QUERIES):
            _tally(hub, now)
        doors = services.door_figures(hub)
        with django_assert_num_queries(0):
            _tally(hub, now, doors=doors)
        GuildDemoFactory.create_batch(3, hub=hub, demo=True)
        StaffTimeFactory.create_batch(3, hub=hub)
        DoorCountFactory.create_batch(3, hub=hub)
        with django_assert_num_queries(TALLY_QUERIES):
            _tally(hub, now)

    def it_renders_lees_heading_and_the_nine_tiles_on_the_page(busy, lee_client):
        body = lee_client.get(_page(busy)).content.decode()

        assert '<p class="pl-hq-lg__tally-eyebrow">The running tally</p>' in body
        assert "Look what we&#x27;re building <em>together</em>" in body
        assert body.count("data-hq-lg-tally-tile=") == 9
        order = ["data-hq-lg-tally", "data-hq-lg-goals", "data-hq-lg-board"]
        assert [body.index(marker) for marker in order] == sorted(body.index(marker) for marker in order)


# The running tally's queries, every door read once: the ledger settings, the band (2), the sponsors (2), the wine wall
# (3), the bar, the marketing log, the check-in (2), the vendor tables (2), the guild demos (2), the staff time and the
# attendance (3, with an event).
TALLY_QUERIES = 20


def describe_the_countdown():
    @pytest.mark.parametrize(
        ("moment", "big", "small"),
        [
            (datetime(2026, 11, 20, 12, 0, tzinfo=PORTLAND), "14", "days to the Bizarre"),
            (datetime(2026, 12, 3, 23, 30, tzinfo=PORTLAND), "1", "day to go. Tomorrow!"),
            (datetime(2026, 12, 4, 9, 0, tzinfo=PORTLAND), "Today", "Doors at 5 PM"),
            (datetime(2026, 12, 4, 17, 0, tzinfo=PORTLAND), "Today", "We're live!"),
            (datetime(2026, 12, 5, 0, 30, tzinfo=PORTLAND), "Done", "Thank you, everyone"),
        ],
    )
    def it_says_lees_lines_by_the_portland_day(hub, event, moment, big, small):
        countdown = _tally(hub, moment).countdown

        assert (countdown.big, countdown.small) == (big, small)

    def it_says_minutes_when_the_doors_open_off_the_hour(hub, event):
        event.starts_at = datetime(2026, 12, 4, 17, 30, tzinfo=PORTLAND)
        event.save(update_fields=["starts_at"])

        assert _tally(hub, datetime(2026, 12, 4, 8, 0, tzinfo=PORTLAND)).countdown.small == "Doors at 5:30 PM"
        event.starts_at = datetime(2026, 12, 4, 10, 0, tzinfo=PORTLAND)
        event.save(update_fields=["starts_at"])
        assert _tally(hub, datetime(2026, 12, 4, 8, 0, tzinfo=PORTLAND)).countdown.small == "Doors at 10 AM"

    def it_leaves_the_countdown_out_with_no_event(hub, lee_client):
        assert _tally(hub).countdown.big == ""
        assert "data-hq-lg-countdown" not in lee_client.get(_page(hub)).content.decode()


def describe_the_cheer_lines():
    def it_says_the_cash_and_the_bottles_still_to_go_and_the_guilds_and_the_flyer_nudge(busy):
        GuildFactory()  # a fourth active guild with no row

        assert _cheer(busy, datetime(2026, 11, 20, 12, 0, tzinfo=PORTLAND)) == [
            "$8,145 more to hit our cash goal",
            "88 bottles to fill the wine wall",
            "3 guilds still to line up a demo",
            "Drop a flyer somewhere today. 3 businesses so far",
        ]

    def it_cheers_the_goals_reached_and_drops_the_nudge_at_25_flyers(busy):
        SponsorFactory(hub=busy, status="received", amount_cents=900_000)
        WineBottleFactory(hub=busy, qty=88)
        MarketingActionFactory.create_batch(22, hub=busy, kind=MarketingKind.FLYER)
        busy.guild_demos.update(demo=True)

        assert _cheer(busy, datetime(2026, 11, 20, 12, 0, tzinfo=PORTLAND)) == [
            "Cash goal reached!",
            "Wine wall is full!",
        ]

    def it_stops_the_flyer_nudge_on_the_day_and_after(busy):
        for moment in (datetime(2026, 12, 4, 9, 0, tzinfo=PORTLAND), datetime(2026, 12, 6, 9, 0, tzinfo=PORTLAND)):
            assert not any(line.startswith("Drop a flyer") for line in _cheer(busy, moment))

    def it_keeps_the_nudge_with_no_event_and_drops_the_lines_with_no_goal_or_guild(hub):
        assert _cheer(hub) == [
            "$10,000 more to hit our cash goal",
            "Drop a flyer somewhere today. 0 businesses so far",
        ]

    def it_bolds_the_figure_on_the_page(busy, lee_client):
        body = lee_client.get(_page(busy)).content.decode()

        assert "<b>$8,145</b> more to hit our cash goal" in body


def describe_the_goals():
    def it_groups_the_rows_as_lee_does_and_jumps_to_each_section(busy, doors, lee_client):
        body = lee_client.get(_page(busy)).content.decode()
        section = body.split('id="hq-lg-goals"', 1)[1].split("</section>", 1)[0]

        headings = [part.split("<", 1)[0] for part in section.split("data-hq-lg-goal-group>")[1:]]
        assert headings == ["Parts of the cash goal", "Budget", "Not part of the cash goal"]
        keys = [part.split('"', 1)[0] for part in section.split('data-hq-lg-goal="')[1:]]
        assert keys == ["cash", "sponsors", "bar", "wine", "budget", "people", "launch", "guilds", "flyer", "vendors"]
        assert "Tap a goal to jump to its section" in body
        for anchor in ("#hq-lg-bar", "#hq-lg-wine", "#hq-lg-board", "#hq-lg-people", "#hq-lg-launch", "#hq-lg-guilds"):
            assert f'href="{anchor}"' in section, anchor
            assert f'id="{anchor[1:]}"' in body, anchor
        assert f'href="{_url("sponsors", busy)}"' in section

    def it_reads_the_figures_and_lees_chips(busy):
        rows = _goal_rows(busy)

        assert (rows["cash"].figure, rows["cash"].figure_sub) == ("$1,855 of $10,000", "+ $500 committed")
        assert rows["cash"].sub == "Sponsorships $1,500 · Drinks $240 · Wine tickets $115"
        assert (rows["cash"].chip, rows["cash"].tone, rows["cash"].percent) == ("$8,145 to go", "", 19)
        assert (rows["sponsors"].chip, rows["sponsors"].tone) == ("15% of goal", "info")
        assert (rows["bar"].figure, rows["bar"].figure_sub, rows["bar"].chip) == (
            "$240",
            "Bar cost $300",
            "In progress",
        )
        assert (rows["wine"].figure, rows["wine"].chip, rows["wine"].tone) == ("12 / 100", "88 to go", "warn")
        assert rows["wine"].figure_sub == "$115 in tickets"
        assert (rows["budget"].figure, rows["budget"].figure_sub) == ("$300 of $900", "$600 left")
        assert (rows["budget"].chip, rows["budget"].tone) == ("Within budget", "ok")
        assert (rows["people"].figure, rows["people"].sub) == ("12 / 250", "3 checked in · 9 at the door")
        assert rows["people"].chip == "238 to go"
        assert (rows["launch"].figure, rows["launch"].chip, rows["launch"].tone) == ("1 / 2 done", "1 to go", "warn")
        assert (rows["guilds"].figure, rows["guilds"].figure_sub) == ("2 / 3 contacted", "1 demo planned")
        assert (rows["guilds"].chip, rows["guilds"].tone) == ("1 to contact", "warn")
        assert (rows["flyer"].figure, rows["flyer"].chip, rows["flyer"].percent) == (
            "3 locations",
            "Flyer not uploaded",
            None,
        )
        assert (rows["vendors"].figure, rows["vendors"].figure_sub) == ("2 vendors", "$420 in sales")
        assert (rows["vendors"].chip, rows["vendors"].tone) == ("1 reported sales", "info")

    @pytest.mark.parametrize(
        ("received", "committed", "chip", "tone"),
        [
            (1_000_000, 0, "Reached", "ok"),
            (600_000, 400_000, "Covered once collected", "warn"),
            (600_000, 399_999, "$4,000 to go", ""),
        ],
    )
    def it_reads_the_event_cash_chip_by_lees_rules(hub, received, committed, chip, tone):
        HubGoalFactory(hub=hub, kind=HubGoal.Kind.EVENT_CASH, label="Raised", target=10_000, is_money=True)
        SponsorFactory(hub=hub, status="received", amount_cents=received)
        SponsorFactory(hub=hub, status="committed", amount_cents=committed)

        row = _goal_rows(hub)["cash"]

        assert (row.chip, row.tone) == (chip, tone)

    def it_reads_the_reached_and_all_done_chips(busy):
        SponsorFactory(hub=busy, status="received", amount_cents=900_000)
        WineBottleFactory(hub=busy, qty=88)
        busy.launch_tasks.update(status=LaunchTask.Status.DONE)
        busy.guild_demos.update(contacted=True)
        BarSaleFactory(hub=busy, qty=10, amount_cents=50_000)
        HubFlyer.objects.create(hub=busy, flyer=photo_upload("flyer.jpg"))

        rows = _goal_rows(busy)

        assert (rows["cash"].chip, rows["wine"].chip, rows["launch"].chip) == ("Reached", "Reached", "All done")
        assert (rows["guilds"].chip, rows["bar"].chip, rows["flyer"].chip) == ("All contacted", "Ahead", "Flyer ready")

    def it_reads_the_quiet_chips_before_anything_is_in(hub):
        rows = _goal_rows(hub)

        assert (rows["sponsors"].chip, rows["bar"].chip, rows["people"].chip) == (
            "Not started",
            "Event night",
            "Event night",
        )
        assert (rows["wine"].chip, rows["wine"].percent, rows["people"].percent) == ("No bottles goal", None, None)
        assert (rows["vendors"].chip, rows["launch"].figure, rows["guilds"].figure) == (
            "Add vendors",
            "0 / 0 done",
            "0 / 0 contacted",
        )

    def it_says_over_budget_once_the_approval_is_spent(hub):
        line = BudgetLineFactory(hub=hub)
        LedgerEntryFactory(hub=hub, line=line, kind=K.PAID_APPROVAL, amount_cents=95_000)

        row = _goal_rows(hub)["budget"]

        assert (row.chip, row.tone, row.figure_sub) == ("Over budget", "warn", "-$50 left")

    def it_takes_every_target_from_the_hubs_goals_never_a_copy(busy):
        busy.goals.filter(kind=HubGoal.Kind.RSVPS).update(target=20)
        busy.goals.filter(kind=HubGoal.Kind.EVENT_CASH).update(target=2_000)
        busy.goals.filter(kind=HubGoal.Kind.BOTTLES).update(target=12)

        rows = _goal_rows(busy)

        assert rows["people"].figure == "12 / 20"
        assert rows["cash"].figure == "$1,855 of $2,000"
        assert (rows["wine"].figure, rows["wine"].chip) == ("12 / 12", "Reached")

    def it_links_change_the_goals_to_edit_hq_for_an_admin_only(busy, lee_client, admin_client, admin):
        assert "data-hq-lg-change-goals" not in lee_client.get(_page(busy)).content.decode()

        body = admin_client.get(_page(busy)).content.decode()

        assert f'href="{_url("edit", busy)}#hq-edit-goals"' in body
        assert 'id="hq-edit-goals"' in admin_client.get(_url("edit", busy)).content.decode()


def describe_the_guilds_goal():
    def it_counts_the_guilds_with_a_demo_planned(hub):
        HubGoalFactory(hub=hub, kind=HubGoal.Kind.GUILDS, label="Guilds taking part", target=0)
        GuildDemoFactory.create_batch(2, hub=hub, demo=True)
        GuildDemoFactory(hub=hub, contacted=True)
        GuildDemoFactory(hub=EventHubFactory(), demo=True)

        assert [(row.label, row.current, row.has_target) for row in services.goal_rows(hub)] == [
            ("Guilds taking part", 2, False)
        ]

    def it_is_lees_and_the_top_up_adds_it_to_a_hub_that_lacks_it_once(hub):
        assert GOALS[-1].label == "Guilds taking part"
        assert (GOALS[-1].kind, GOALS[-1].target) == (HubGoal.Kind.GUILDS, 0)
        for position, spec in enumerate(GOALS[:-1]):
            HubGoalFactory(hub=hub, label=spec.label, kind=spec.kind, target=spec.target, sort_order=position)

        first = services.seed_goals(hub)
        again = services.seed_goals(hub)

        assert (first.added, again.added) == (["Guilds taking part"], [])
        assert hub.goals.get(kind=HubGoal.Kind.GUILDS).sort_order == len(GOALS) - 1

    def it_says_so_when_update_the_doors_adds_it(hub, admin_client):
        hub.seed_default_cards()
        for position, spec in enumerate(GOALS[:-1]):
            HubGoalFactory(hub=hub, label=spec.label, kind=spec.kind, target=spec.target, sort_order=position)

        response = admin_client.post(_url("doors_update", hub), follow=True)

        assert [str(message) for message in response.context["messages"]] == [
            "Doors updated: added the Guilds taking part goal."
        ]

    def it_shows_on_the_hq_page_as_a_plain_count(hub, client):
        HubGoalFactory(hub=hub, kind=HubGoal.Kind.GUILDS, label="Guilds taking part", target=0)
        GuildDemoFactory(hub=hub, demo=True)

        body = client.get(hub.get_absolute_url()).content.decode()

        assert '<strong class="pl-hq-goal__value" data-hq-goal-count>1</strong>' in body


def describe_attendance():
    def it_is_the_check_ins_party_sizes_plus_the_door_clicker(hub, event):
        EventRSVPFactory(event=event, party_size=4, checked_in_at=timezone.now())
        EventRSVPFactory(event=event, guest=True, party_size=2, checked_in_at=timezone.now())
        EventRSVPFactory(event=event, party_size=5)
        EventRSVPFactory(party_size=3, checked_in_at=timezone.now())  # another event
        DoorCountFactory(hub=hub, delta=5)
        DoorCountFactory(hub=hub, delta=1)
        DoorCountFactory(hub=EventHubFactory(), delta=10)

        found = services.attendance(hub)

        assert (found.checked_in, found.walk_ins, found.total) == (6, 6, 12)
        assert found.parts_line == "6 checked in · 6 at the door"

    def it_counts_walk_ins_with_no_event(hub):
        DoorCountFactory(hub=hub, delta=10)

        assert services.attendance(hub).total == 10

    def it_says_lees_goal_line(hub):
        assert services.Attendance(checked_in=1, walk_ins=2, goal=10).goal_line == "of 10 goal"
        assert services.Attendance(checked_in=8, walk_ins=2, goal=10).goal_line == "of 10 goal · reached"
        assert services.Attendance(checked_in=1, walk_ins=2, goal=0).goal_line == "1 checked in · 2 at the door"

    def it_counts_a_tap_over_htmx_and_sends_the_figures_back_out_of_band(hub, event, lee_client, lee):
        HubGoalFactory(hub=hub, kind=HubGoal.Kind.RSVPS, target=250)

        response = lee_client.post(_url("ledger_door", hub), {"delta": "5"}, **HTMX)

        body = response.content.decode()
        assert response.status_code == 200
        assert _toast(response) == {"message": "Added 5 at the door.", "type": "success"}
        assert DoorCount.objects.get().created_by == lee
        assert 'id="hq-lg-people-stats" data-hq-lg-people-stats hx-swap-oob="true"' in body
        assert 'id="hq-lg-tally" aria-labelledby="hq-lg-tally-title" data-hq-lg-tally hx-swap-oob="true"' in body
        assert 'id="hq-lg-goals" aria-labelledby="hq-lg-goals-title" data-hq-lg-goals hx-swap-oob="true"' in body
        assert 'id="hq-lg-board-figures" data-hq-lg-board-figures hx-swap-oob="true"' in body
        assert 'id="hq-lg-staff-figures" data-hq-lg-staff-figures hx-swap-oob="true"' in body
        assert body.count("<template>") == 5
        assert "0 checked in · 5 at the door · of 250 goal" in body

    def it_floors_undo_at_zero_and_writes_nothing_then(hub, lee_client):
        DoorCountFactory(hub=hub, delta=1)

        took = lee_client.post(_url("ledger_door", hub), {"delta": "-1"}, **HTMX)
        refused = lee_client.post(_url("ledger_door", hub), {"delta": "-1"}, **HTMX)

        assert _toast(took) == {"message": "Took 1 back.", "type": "success"}
        assert _toast(refused) == {"message": "Nothing to take back: the door count is at zero.", "type": "info"}
        assert list(hub.door_counts.order_by("pk").values_list("delta", flat=True)) == [1, -1]
        assert services.walk_ins(hub) == 0
        with pytest.raises(services.NothingToUndo):
            services.count_at_door(hub, -1, member=None)
        with pytest.raises(ValueError, match="A door tap is one of"):
            services.count_at_door(hub, 3, member=None)
        assert hub.door_counts.count() == 2

    def it_never_goes_below_zero_even_from_old_rows(hub):
        DoorCountFactory(hub=hub, delta=-1)

        assert services.walk_ins(hub) == 0

    def it_refuses_a_step_that_is_not_a_button(hub, lee_client):
        response = lee_client.post(_url("ledger_door", hub), {"delta": "3"}, **HTMX)
        plain = lee_client.post(_url("ledger_door", hub), {"delta": "100"}, follow=True)

        assert _toast(response) == {"message": "Choose +1, +5, +10 or Undo 1.", "type": "error"}
        assert plain.redirect_chain[-1][0] == f"{_page(hub)}#hq-lg-people"
        assert not DoorCount.objects.exists()
        with pytest.raises(IntegrityError), transaction.atomic():
            DoorCount.objects.create(hub=hub, delta=3)

    def it_lands_a_plain_tap_back_on_the_section_with_a_message(hub, lee_client):
        response = lee_client.post(_url("ledger_door", hub), {"delta": "10"}, follow=True)
        undo = lee_client.post(_url("ledger_door", hub), {"delta": "-1"}, follow=True)
        DoorCount.objects.all().delete()
        nothing = lee_client.post(_url("ledger_door", hub), {"delta": "-1"}, follow=True)

        assert response.redirect_chain[-1][0] == f"{_page(hub)}#hq-lg-people"
        assert "Added 10 at the door." in [str(message) for message in response.context["messages"]]
        assert "Took 1 back." in [str(message) for message in undo.context["messages"]]
        assert "Nothing to take back: the door count is at zero." in [
            str(message) for message in nothing.context["messages"]
        ]

    def it_renders_big_buttons_that_disable_while_a_tap_is_in_flight(hub, lee_client):
        body = lee_client.get(_page(hub)).content.decode()

        assert 'hx-disabled-elt="[data-hq-lg-door-step]"' in body
        for step, label in (("1", "Add 1"), ("5", "Add 5"), ("10", "Add 10"), ("-1", "Undo 1")):
            assert f'value="{step}" ' in body
            assert f'aria-label="{label} at the door" data-hq-lg-door-step="{step}"' in body
        assert "Tap for each walk-in without an RSVP. RSVPs check in on the Guest list." in body
        assert "data-hq-lg-guest-list" not in body  # Lee may not open Manage Events, so the Guest list is plain words

    def it_links_the_guest_list_to_the_events_guest_list_tab_for_whoever_may_open_it(hub, event, admin_client):
        body = admin_client.get(_page(hub)).content.decode()

        guests = reverse("hub_admin_event_guests", args=[event.pk])
        assert (
            "Tap for each walk-in without an RSVP. RSVPs check in on the "
            f'<a href="{guests}" class="pl-hq-lg__link" data-hq-lg-guest-list>Guest list</a>.'
        ) in body
        assert admin_client.get(guests).status_code == 200

    def it_names_the_guest_list_without_a_link_with_no_event(hub, admin_client):
        body = admin_client.get(_page(hub)).content.decode()

        assert "RSVPs check in on the Guest list." in body
        assert "data-hq-lg-guest-list" not in body

    def it_lists_the_reports_and_the_no_double_entry_doors(busy, doors, lee_client):
        body = lee_client.get(_page(busy)).content.decode()
        section = body.split('id="hq-lg-people"', 1)[1].split("</section>", 1)[0]

        assert "Riverbend Ceramics" in section and "&ldquo;Steady all night&rdquo;" in section
        assert section.index("Ash and Oak") < section.index("Riverbend Ceramics")
        assert "No double entry: each list lives on one page" in section
        assert f'href="{_url("vendors", busy)}" class="pl-hq-lg__inline-link" data-hq-lg-vendors-link' in section
        assert f'href="{_url("checkin", busy)}" class="pl-hq-lg__inline-link" data-hq-lg-checkin-link' in section
        assert "<form" in section  # the door clicker only
        assert section.count("<form") == 1
        stats = section.split('id="hq-lg-people-stats"', 1)[1]
        for figure in (">12<", ">2<", ">$420<", ">$420<"):
            assert figure in stats

    def it_names_the_doors_without_links_when_they_are_closed(busy, lee_client):
        section = lee_client.get(_page(busy)).content.decode().split('id="hq-lg-people"', 1)[1]

        assert "data-hq-lg-vendors-link" not in section.split("</section>", 1)[0]
        assert "Who's vending is entered on Vendor Tables." in section

    def it_downloads_the_vendors_and_the_attendance(busy, lee_client):
        response = lee_client.get(_url("ledger_people_csv", busy))

        rows = list(csv.reader(io.StringIO(b"".join(response.streaming_content).decode())))
        assert response["Content-Disposition"].startswith(f'attachment; filename="{busy.slug}-vendors-attendance-')
        assert rows[0] == PEOPLE_CSV_HEADERS
        assert rows[1] == ["Ash and Oak", "Not in a guild", "", "0.00", ""]
        assert rows[2] == ["Riverbend Ceramics", "Not in a guild", "14", "420.00", "Steady all night"]
        assert rows[3] == []
        assert rows[4] == ["Total", "2 vendors", "14", "420.00", ""]
        assert rows[5] == ["Attendance", "12", "3 checked in", "9 at the door", ""]

    def it_neutralises_a_formula_an_artist_typed(hub, lee_client, lee):
        _report(hub, lee, name="=HYPERLINK(1)", experience="+cmd")

        rows = list(
            csv.reader(io.StringIO(b"".join(lee_client.get(_url("ledger_people_csv", hub)).streaming_content).decode()))
        )

        assert rows[1][0] == "'=HYPERLINK(1)" and rows[1][4] == "'+cmd"


def describe_the_launch_checklist():
    def it_adds_a_task_at_the_end_over_htmx_with_the_figures_out_of_band(hub, lee_client):
        LaunchTaskFactory(hub=hub, sort_order=4)

        response = lee_client.post(
            _url("ledger_launch_add", hub),
            {"launch-task": "Post on Nextdoor", "launch-owner": "Amy", "launch-due": ""},
            **HTMX,
        )

        body = response.content.decode()
        task = hub.launch_tasks.get(task="Post on Nextdoor")
        assert (task.owner, task.due, task.sort_order, task.status) == ("Amy", None, 5, LaunchTask.Status.NOT_STARTED)
        assert _toast(response)["message"] == "Task added."
        assert body.lstrip().startswith('<section class="pl-hq-lg__section" id="hq-lg-launch"')
        assert f'data-hq-lg-task="{task.pk}"' in body
        assert 'id="hq-lg-goals" aria-labelledby="hq-lg-goals-title" data-hq-lg-goals hx-swap-oob="true"' in body
        assert "0 of 2 done" in body
        assert "open data-hq-lg-launch-add" in body

    def it_refuses_a_task_with_no_words_and_writes_nothing(hub, lee_client):
        response = lee_client.post(_url("ledger_launch_add", hub), {"launch-task": ""}, **HTMX)
        plain = lee_client.post(_url("ledger_launch_add", hub), {"launch-task": ""})

        assert response.status_code == 200 and plain.status_code == 200
        assert "This field is required." in response.content.decode()
        assert "This field is required." in plain.content.decode()
        assert not LaunchTask.objects.exists()

    def it_saves_one_row_over_htmx_with_the_summary_out_of_band(hub, lee_client):
        task = LaunchTaskFactory(hub=hub, task="Website live")

        response = lee_client.post(
            _url("ledger_launch_edit", hub, task.pk),
            _task_post(task, status="done", owner="Lee", link="https://pastlives.space/bizarre"),
            **HTMX,
        )

        body = response.content.decode()
        task.refresh_from_db()
        assert (task.status, task.owner, task.link) == ("done", "Lee", "https://pastlives.space/bizarre")
        assert _toast(response)["message"] == "Saved."
        assert body.lstrip().startswith(f'<tbody class="pl-hq-lg__row-body" data-hq-lg-task="{task.pk}"')
        assert 'id="hq-lg-launch-summary" data-hq-lg-launch-summary hx-swap-oob="true">1 of 1 done</span>' in body
        assert 'href="https://pastlives.space/bizarre" target="_blank" rel="noopener"' in body

    def it_refuses_a_stale_save_whole_and_shows_the_row_as_saved(hub, lee_client):
        task = LaunchTaskFactory(hub=hub, task="Website live")
        posted = _task_post(task, owner="Me")
        LaunchTask.objects.filter(pk=task.pk).update(owner="Someone", updated_at=timezone.now() + timedelta(seconds=5))

        response = lee_client.post(_url("ledger_launch_edit", hub, task.pk), posted, **HTMX)
        plain = lee_client.post(_url("ledger_launch_edit", hub, task.pk), posted)
        missing = lee_client.post(
            _url("ledger_launch_edit", hub, task.pk), {**posted, f"task-{task.pk}-updated_at": ""}, **HTMX
        )

        assert LaunchTask.objects.get(pk=task.pk).owner == "Someone"
        for answer in (response, plain, missing):
            assert answer.status_code == 200
            assert services_launch_stale() in answer.content.decode()
        assert 'value="Someone"' in response.content.decode()
        assert "HX-Trigger" not in response

    def it_refuses_a_link_that_is_not_a_web_address(hub, lee_client):
        task = LaunchTaskFactory(hub=hub)

        response = lee_client.post(
            _url("ledger_launch_edit", hub, task.pk), _task_post(task, link="javascript:alert(1)"), **HTMX
        )
        plain = lee_client.post(_url("ledger_launch_edit", hub, task.pk), _task_post(task, link="ftp://x.example"))

        assert "Enter a valid URL." in response.content.decode()
        assert "Enter a valid URL." in plain.content.decode()
        assert LaunchTask.objects.get(pk=task.pk).link == ""

    def it_lands_a_plain_save_back_on_the_section(hub, lee_client):
        task = LaunchTaskFactory(hub=hub, task="Eventbrite")

        response = lee_client.post(
            _url("ledger_launch_edit", hub, task.pk), _task_post(task, owner="Dixie"), follow=True
        )
        added = lee_client.post(_url("ledger_launch_add", hub), {"launch-task": "Flyer"}, follow=True)

        assert response.redirect_chain[-1][0] == f"{_page(hub)}#hq-lg-launch"
        assert "Eventbrite saved." in [str(message) for message in response.context["messages"]]
        assert "Task added." in [str(message) for message in added.context["messages"]]

    def it_deletes_a_task(hub, lee_client):
        task = LaunchTaskFactory(hub=hub, task="Old task")

        response = lee_client.post(_url("ledger_launch_delete", hub, task.pk), follow=True)

        assert not LaunchTask.objects.exists()
        assert "Old task is off the checklist." in [str(message) for message in response.context["messages"]]

    def it_is_late_only_before_today_and_not_done():
        today = date(2026, 11, 20)
        assert LaunchTask(due=date(2026, 11, 19)).is_late(today)
        assert not LaunchTask(due=today).is_late(today)
        assert not LaunchTask(due=date(2026, 11, 19), status=LaunchTask.Status.DONE).is_late(today)
        assert not LaunchTask(due=None).is_late(today)

    def it_reads_late_on_the_portland_date(hub, lee_client, monkeypatch):
        # 11 PM in Portland on November 19 is already November 20 in UTC: a task due the 19th is not late yet.
        evening = datetime(2026, 11, 19, 23, 0, tzinfo=PORTLAND)
        due_today = LaunchTaskFactory(hub=hub, task="Due today", due=date(2026, 11, 19))
        due_before = LaunchTaskFactory(hub=hub, task="Due before", due=date(2026, 11, 18))
        real_localdate = timezone.localdate
        monkeypatch.setattr(timezone, "localdate", lambda value=None, tz=None: real_localdate(value or evening, tz))

        body = lee_client.get(_page(hub)).content.decode()

        def _row(task: LaunchTask) -> str:
            return body.split(f'data-hq-lg-task="{task.pk}"', 1)[1].split("</tbody>", 1)[0]

        assert "data-hq-lg-late" not in _row(due_today)
        assert "data-hq-lg-late" in _row(due_before) and ">Late<" in _row(due_before)

    def it_labels_every_control_with_the_task(hub, lee_client):
        task = LaunchTaskFactory(hub=hub, task="Post on Nextdoor")

        body = lee_client.get(_page(hub)).content.decode()

        for label in ("Task", "Owner", "Status", "Due", "Link"):
            assert f"{label} for Post on Nextdoor" in body
        assert f'form="hq-lg-task-{task.pk}"' in body
        assert 'aria-label="Save Post on Nextdoor"' in body and 'aria-label="Delete Post on Nextdoor"' in body
        assert 'list="hq-lg-team"' in body
        assert '<datalist id="hq-lg-team" data-hq-lg-team><option value="Lee Ashworth"></option></datalist>' in body
        assert "Getting the word out" not in body and "0 of 1 done" in body


def services_launch_stale() -> str:
    from hq.views import LAUNCH_STALE_SAVE

    return LAUNCH_STALE_SAVE


def describe_staff_time():
    @pytest.mark.parametrize(
        ("hours", "error"),
        [
            ("0", HOURS_ERROR),
            ("24.25", HOURS_ERROR),
            ("1.3", HOURS_STEP_ERROR),
            ("", HOURS_ERROR),
            ("lots", HOURS_ERROR),
        ],
    )
    def it_holds_the_hours_to_quarter_hours_from_a_quarter_to_a_day(hub, lee_client, hours, error):
        response = lee_client.post(_url("ledger_staff_add", hub), _staff_post(hours=hours), **HTMX)

        assert error in response.content.decode()
        assert not StaffTime.objects.exists()

    @pytest.mark.parametrize(
        ("hours", "stored"), [("0.25", Decimal("0.25")), ("24", Decimal("24")), ("2.500", Decimal("2.5"))]
    )
    def it_takes_the_bounds_and_trailing_zeros(hub, lee_client, hours, stored):
        lee_client.post(_url("ledger_staff_add", hub), _staff_post(hours=hours), **HTMX)

        assert StaffTime.objects.get().hours == stored

    def it_refuses_out_of_range_hours_in_the_database(hub):
        with pytest.raises(IntegrityError), transaction.atomic():
            StaffTime.objects.create(hub=hub, who="Lee", hours=Decimal("25"))

    def it_logs_over_htmx_keeping_who_date_and_area_and_sends_the_figures_back(hub, lee_client, lee):
        response = lee_client.post(
            _url("ledger_staff_add", hub),
            _staff_post(who="lee ashworth", hours="1.5", area="board", note="Board report"),
            **HTMX,
        )

        body = response.content.decode()
        entry = StaffTime.objects.get()
        assert (entry.who, entry.member, entry.created_by, entry.area) == ("lee ashworth", lee, lee, "board")
        assert _toast(response) == {"message": "Logged 1.5 hours.", "type": "success"}
        assert 'value="lee ashworth"' in body and 'value="2026-11-20"' in body
        assert '<option value="board" selected>' in body
        assert 'name="staff-hours"' in body and 'value="1.5"' not in body.split('name="staff-hours"', 1)[1][:80]
        assert 'id="hq-lg-board-figures" data-hq-lg-board-figures hx-swap-oob="true"' in body
        assert "Staff time: 1.5 hours (lee ashworth 1.5)" in body

    def it_says_one_hour_in_the_singular_and_links_no_member_for_a_name_off_the_team(hub, lee_client):
        response = lee_client.post(_url("ledger_staff_add", hub), _staff_post(who="Dixie", hours="1"), **HTMX)

        assert _toast(response)["message"] == "Logged 1 hour."
        assert StaffTime.objects.get().member is None

    def it_refuses_a_plain_post_on_the_page_and_lands_a_saved_one_on_the_section(hub, lee_client):
        refused = lee_client.post(_url("ledger_staff_add", hub), _staff_post(hours="99"))
        saved = lee_client.post(_url("ledger_staff_add", hub), _staff_post(), follow=True)

        assert refused.status_code == 200 and HOURS_ERROR in refused.content.decode()
        assert "open data-hq-lg-staff-add" in refused.content.decode()
        assert saved.redirect_chain[-1][0] == f"{_page(hub)}#hq-lg-staff"
        assert StaffTime.objects.count() == 1

    def it_adds_each_persons_hours_top_first_whatever_the_capitalisation(hub):
        StaffTimeFactory(hub=hub, who="Lee", hours=Decimal("2"))
        StaffTimeFactory(hub=hub, who=" lee ", hours=Decimal("3.5"), area=StaffTime.Area.BOARD)
        StaffTimeFactory(hub=hub, who="Augustin", hours=Decimal("4"), area=StaffTime.Area.BOARD)
        StaffTimeFactory(hub=EventHubFactory(), who="Lee", hours=Decimal("10"))

        totals = services.staff_totals(hub, cash_cents=0)

        assert [(person.name, person.hours) for person in totals.people] == [
            ("Lee", Decimal("5.5")),
            ("Augustin", Decimal("4")),
        ]
        assert [(area.name, area.hours) for area in totals.areas] == [
            ("Board approvals and reporting", Decimal("7.5")),
            ("Planning and admin", Decimal("2")),
        ]
        assert totals.hours == Decimal("9.5")

    @pytest.mark.parametrize(
        ("cash_cents", "hours", "rate"),
        [(185_500, "8", "$232"), (100, "8", "$0"), (1_250, "1", "$13"), (1_249, "1", "$12"), (0, "0", "–")],
    )
    def it_reads_raised_per_staff_hour_as_event_cash_over_staff_hours(cash_cents, hours, rate):
        totals = services.StaffTotals(hours=Decimal(hours), people=[], areas=[], cash_cents=cash_cents)

        assert totals.rate_display == rate

    def it_shows_the_figures_and_reads_the_event_cash_and_the_volunteers(busy, lee_client):
        body = lee_client.get(_page(busy)).content.decode()
        section = body.split('id="hq-lg-staff"', 1)[1].split("</section>", 1)[0]

        assert services.staff_totals(busy, cash_cents=services.event_cash(busy).total_cents).rate_display == "$232"
        for tile, figure in (("hours", "8"), ("rate", "$232"), ("volunteers", "5.75")):
            assert f'data-hq-lg-staff-tile="{tile}"' in section
            part = section.split(f'data-hq-lg-staff-tile="{tile}"', 1)[1]
            assert f"data-hq-lg-figure>{figure}</strong>" in part.split("</div>", 1)[0]
        assert section.index(">Lee<") < section.index(">Augustin<")
        assert "Bar and wine wall" in section
        assert "aria-label=\"Delete Lee's 6 hours on" in section
        assert "data-hq-lg-staff-csv" in section

    def it_deletes_an_entry(hub, lee_client):
        entry = StaffTimeFactory(hub=hub, who="Lee", hours=Decimal("2"))

        response = lee_client.post(_url("ledger_staff_delete", hub, entry.pk), follow=True)

        assert not StaffTime.objects.exists()
        assert "Deleted Lee&#x27;s 2 hours." in response.content.decode()

    def it_downloads_the_staff_time_oldest_first_with_the_total(hub, lee_client, lee):
        StaffTimeFactory(
            hub=hub, who="Lee", hours=Decimal("6"), date=date(2026, 11, 2), note="=SUM(A1)", created_by=lee
        )
        StaffTimeFactory(hub=hub, who="Augustin", hours=Decimal("2.25"), date=date(2026, 11, 1))

        response = lee_client.get(_url("ledger_staff_csv", hub))

        rows = list(csv.reader(io.StringIO(b"".join(response.streaming_content).decode())))
        assert response["Content-Disposition"].startswith(f'attachment; filename="{hub.slug}-staff-time-')
        assert rows[0] == STAFF_TIME_CSV_HEADERS
        assert rows[1] == ["2026-11-01", "Augustin", "2.25", "Planning and admin", "", ""]
        assert rows[2] == ["2026-11-02", "Lee", "6.00", "Planning and admin", "'=SUM(A1)", "Lee Ashworth"]
        assert rows[3] == []
        assert rows[4] == ["Total", "", "8.25", "Lee: 6 / Augustin: 2.25", "", ""]


def describe_guild_demos():
    def it_holds_one_row_per_guild_on_a_hub(hub):
        guild = GuildFactory()
        GuildDemoFactory(hub=hub, guild=guild)
        GuildDemoFactory(hub=EventHubFactory(), guild=guild)

        with pytest.raises(IntegrityError), transaction.atomic():
            GuildDemo.objects.create(hub=hub, guild=guild)

    def it_protects_a_guild_with_demo_plans_from_a_hard_delete(hub):
        row = GuildDemoFactory(hub=hub)

        with pytest.raises(ProtectedError):
            row.guild.delete()

    def it_adds_every_active_guild_missing_once_each_with_its_lead(hub, lee_client, make_member):
        lead = make_member(preferred_name="Gwen Glass")
        glass = GuildFactory(name="Glass Guild", guild_lead=lead)
        listed = GuildDemoFactory(hub=hub, guild=GuildFactory(name="Wood Guild"), lead="Kept")
        GuildFactory(name="Old Guild", is_active=False)

        response = lee_client.post(_url("ledger_guilds_add_all", hub), **HTMX)
        again = lee_client.post(_url("ledger_guilds_add_all", hub), **HTMX)

        assert _toast(response)["message"] == "Added 1 guild."
        assert _toast(again)["message"] == "Every active guild is on the list."
        assert list(hub.guild_demos.values_list("guild__name", "lead")) == [
            ("Glass Guild", "Gwen Glass"),
            ("Wood Guild", "Kept"),
        ]
        assert GuildDemo.objects.get(pk=listed.pk).lead == "Kept"
        assert glass.hq_guild_demos.count() == 1
        assert "Every active guild is on the list." in again.content.decode()
        assert 'id="hq-lg-tally"' in response.content.decode()

    def it_lands_a_plain_add_back_on_the_section(hub, lee_client):
        guild = GuildFactory(name="Metal Guild")

        response = lee_client.post(_url("ledger_guild_add", hub), {"guild-add-guild": str(guild.pk)}, follow=True)
        refused = lee_client.post(_url("ledger_guild_add", hub), {"guild-add-guild": "0"})

        assert response.redirect_chain[-1][0] == f"{_page(hub)}#hq-lg-guilds"
        assert "Metal Guild added." in [str(message) for message in response.context["messages"]]
        assert refused.status_code == 200
        assert hub.guild_demos.count() == 1

    def it_says_how_many_were_added_on_a_plain_post(hub, lee_client):
        GuildFactory.create_batch(2)

        response = lee_client.post(_url("ledger_guilds_add_all", hub), follow=True)

        assert response.redirect_chain[-1][0] == f"{_page(hub)}#hq-lg-guilds"
        assert "Added 2 guilds." in [str(message) for message in response.context["messages"]]

    def it_adds_one_active_guild_not_yet_listed(hub, lee_client):
        guild = GuildFactory(name="Metal Guild")
        listed = GuildDemoFactory(hub=hub)
        gone = GuildFactory(name="Gone Guild", is_active=False)

        twice = lee_client.post(_url("ledger_guild_add", hub), {"guild-add-guild": str(listed.guild.pk)}, **HTMX)
        inactive = lee_client.post(_url("ledger_guild_add", hub), {"guild-add-guild": str(gone.pk)})
        added = lee_client.post(
            _url("ledger_guild_add", hub), {"guild-add-guild": str(guild.pk), "guild-add-lead": "Morlock"}, **HTMX
        )

        assert "HX-Trigger" not in twice
        assert _toast(added)["message"] == "Metal Guild added."
        assert hub.guild_demos.get(guild=guild).lead == "Morlock"
        assert "Select a valid choice." in twice.content.decode()
        assert "Select a valid choice." in inactive.content.decode()
        assert hub.guild_demos.count() == 2

    def it_saves_one_row_over_htmx_with_the_summary_out_of_band(hub, lee_client):
        row = GuildDemoFactory(hub=hub, guild=GuildFactory(name="Glass Guild"))
        GuildFactory(name="Another Guild")

        response = lee_client.post(
            _url("ledger_guild_edit", hub, row.pk),
            _guild_post(row, contacted="on", demo="on", contacted_by="Lee", note="Flameworking"),
            **HTMX,
        )

        body = response.content.decode()
        row.refresh_from_db()
        assert (row.contacted, row.demo, row.contacted_by, row.note) == (True, True, "Lee", "Flameworking")
        assert _toast(response)["message"] == "Saved."
        assert body.lstrip().startswith(f'<tbody class="pl-hq-lg__row-body" data-hq-lg-guild="{row.pk}"')
        assert 'data-hq-lg-guild-summary hx-swap-oob="true">1 of 2 contacted · 1 demo planned</span>' in body
        assert 'data-hq-lg-tally-tile="guilds"' in body

    def it_turns_a_switch_off_when_it_is_left_unticked(hub, lee_client):
        row = GuildDemoFactory(hub=hub, contacted=True, demo=True)

        lee_client.post(
            _url("ledger_guild_edit", hub, row.pk), _guild_post(row, contacted=None, demo=None) | {}, **HTMX
        )
        row.refresh_from_db()
        posted = {key: value for key, value in _guild_post(row).items() if not key.endswith(("contacted", "demo"))}
        lee_client.post(_url("ledger_guild_edit", hub, row.pk), posted, **HTMX)

        row.refresh_from_db()
        assert (row.contacted, row.demo) == (False, False)

    def it_refuses_a_stale_save_whole(hub, lee_client):
        from hq.views import GUILD_STALE_SAVE

        row = GuildDemoFactory(hub=hub)
        posted = _guild_post(row, lead="Mine")
        GuildDemo.objects.filter(pk=row.pk).update(lead="Theirs", updated_at=timezone.now() + timedelta(seconds=5))

        response = lee_client.post(_url("ledger_guild_edit", hub, row.pk), posted, **HTMX)
        plain = lee_client.post(_url("ledger_guild_edit", hub, row.pk), posted)

        assert GuildDemo.objects.get(pk=row.pk).lead == "Theirs"
        assert GUILD_STALE_SAVE in response.content.decode() and GUILD_STALE_SAVE in plain.content.decode()
        assert 'value="Theirs"' in response.content.decode()
        assert "HX-Trigger" not in response

    def it_refuses_a_lead_too_long_and_keeps_the_typing(hub, lee_client):
        row = GuildDemoFactory(hub=hub)

        response = lee_client.post(_url("ledger_guild_edit", hub, row.pk), _guild_post(row, lead="x" * 121), **HTMX)
        plain = lee_client.post(_url("ledger_guild_edit", hub, row.pk), _guild_post(row, lead="x" * 121))

        assert "Ensure this value has at most 120 characters" in response.content.decode()
        assert "Ensure this value has at most 120 characters" in plain.content.decode()
        assert GuildDemo.objects.get(pk=row.pk).lead == ""

    def it_lands_a_plain_save_and_a_remove_back_on_the_section(hub, lee_client):
        row = GuildDemoFactory(hub=hub, guild=GuildFactory(name="Glass Guild"))

        saved = lee_client.post(_url("ledger_guild_edit", hub, row.pk), _guild_post(row, lead="Gwen"), follow=True)
        removed = lee_client.post(_url("ledger_guild_delete", hub, row.pk), follow=True)

        assert saved.redirect_chain[-1][0] == f"{_page(hub)}#hq-lg-guilds"
        assert "Glass Guild saved." in [str(message) for message in saved.context["messages"]]
        assert "Glass Guild is off the list." in [str(message) for message in removed.context["messages"]]
        assert not GuildDemo.objects.exists()

    def it_says_lees_summary_with_n_the_active_guilds(hub, event):
        totals = services.GuildDemoTotals(guilds=12, rows=3, contacted=2, demos=1)

        assert totals.summary(" on Dec 4") == "2 of 12 contacted · 1 demo planned"
        assert services.GuildDemoTotals(guilds=12, rows=0, contacted=0, demos=0).summary(" on Dec 4") == (
            "Each guild, who reached out, and what they'll demo on Dec 4"
        )

    def it_reads_lees_lede_with_the_events_day_on_the_page(hub, event, lee_client):
        body = lee_client.get(_page(hub)).content.decode()

        assert "Each guild, who reached out, and what they&#x27;ll demo on Dec 4" in body

    def it_labels_every_control_with_the_guild_and_marks_an_inactive_one(hub, lee_client):
        row = GuildDemoFactory(hub=hub, guild=GuildFactory(name="Glass Guild", is_active=False))

        body = lee_client.get(_page(hub)).content.decode()

        for label in ("Guild lead", "Contacted", "Contacted by", "Demo planned", "What they&#x27;ll demo"):
            assert f"{label} for Glass Guild" in body
        assert f'form="hq-lg-guild-{row.pk}"' in body
        assert 'aria-label="Save Glass Guild"' in body and 'aria-label="Remove Glass Guild"' in body
        assert '<span class="pl-hq-lg__cat">Inactive</span>' in body


def describe_volunteer_hours():
    def it_reads_the_totals_and_the_top_ten_from_the_market_check_in(hub, doors, lee_client, make_member):
        for position in range(12):
            _hours(hub, make_member(preferred_name=f"Helper {position:02d}"), str(position + 1))

        body = lee_client.get(_page(hub)).content.decode()
        section = body.split('id="hq-lg-vol"', 1)[1].split("</section>", 1)[0]

        assert section.count("data-hq-lg-vol-row") == 10
        assert section.index("Helper 11") < section.index("Helper 02")
        assert "Helper 01" not in section and "Helper 00" not in section
        assert "78 hours from 12 volunteers so far" in section
        assert (
            f'<a href="{_url("volunteers", hub)}" class="pl-hq-lg__inline-link" data-hq-lg-signup-link>Volunteer Sign-up</a>'
            in section
        )
        assert (
            f'<a href="{_url("checkin", hub)}" class="pl-hq-lg__inline-link" data-hq-lg-vol-checkin-link>Market Check-in</a>'
            in section
        )
        assert "<form" not in section

    def it_says_lees_empty_line_and_names_closed_doors_without_links(hub, lee_client):
        section = lee_client.get(_page(hub)).content.decode().split('id="hq-lg-vol"', 1)[1].split("</section>", 1)[0]

        assert "No hours yet. Be the first to log some!" in section
        assert "Sign up for shifts on Volunteer Sign-up. Log hours on Market Check-in." in section


def describe_the_flyer_section():
    def it_shows_the_flyer_and_the_businesses_with_it_up_linking_both_doors(hub, doors, lee_client):
        HubFlyer.objects.create(hub=hub, flyer=photo_upload("flyer.jpg"))
        MarketingActionFactory(hub=hub, kind=MarketingKind.FLYER)

        body = lee_client.get(_page(hub)).content.decode()
        section = body.split('id="hq-lg-flyer"', 1)[1].split("</section>", 1)[0]

        assert f'src="{hub.hub_flyer.flyer.url}"' in section
        assert "<strong>1</strong> business has the flyer up" in section
        assert f'href="{_url("flyer", hub)}"' in section and f'href="{_url("marketing", hub)}"' in section
        assert "<form" not in section

    def it_says_a_pdf_is_ready_and_none_is_uploaded(hub, lee_client):
        assert "No flyer uploaded yet" in lee_client.get(_page(hub)).content.decode()
        HubFlyer.objects.create(hub=hub, flyer="hq/flyers/flyer.pdf")
        MarketingActionFactory.create_batch(2, hub=hub, kind=MarketingKind.FLYER)

        body = lee_client.get(_page(hub)).content.decode()

        assert "Flyer ready (PDF)" in body
        assert "<strong>2</strong> businesses have the flyer up" in body
        assert "data-hq-lg-flyer-link" not in body


def describe_the_board_summary():
    def it_reads_lees_whole_paragraph_in_her_order(busy, make_member):
        band = services.ledger_band(busy)

        text = services.board_summary(band, services.door_figures(busy, band))

        assert text == (
            "Holiday Bizarre drinks: the board approved $900. "
            "So far $0 in drinks and supplies has been sponsored. "
            "We have used $300 of the approval, leaving $600. "
            "Event cash goal: $1,855 raised toward $10,000 (plus $500 committed). "
            "Drink sales: 40 drinks sold for $240, $60 short of break-even after $300 in bar costs. "
            "Cash sponsorships: $1,500 received and $500 committed. "
            "Guild demos: 2 of 3 guilds contacted, 1 demo planned. "
            "Flyers are up at 3 locations. "
            "Staff time: 8 hours (Lee 6, Augustin 2), about $232 raised per staff hour. "
            "Volunteers: 2 people gave 5.75 hours to event prep. "
            "Attendance: 12 people. Vendors: 2 artists sold $420 in total. "
            "Wine wall: 12 of 100 bottles donated, worth $300. "
            "5 tickets sold (3 singles at $25 + 1 deal at $40 for 2) raised $115."
        )

    def it_writes_the_singulars_and_leaves_out_what_has_nothing_to_say(hub):
        line = BudgetLineFactory(hub=hub)
        LedgerEntryFactory(hub=hub, line=line)
        GuildDemoFactory(hub=hub, contacted=True, demo=True)
        DoorCountFactory(hub=hub, delta=1)
        VendorTableFactory(hub=hub, status="confirmed")
        StaffTimeFactory(hub=hub, who="Lee", hours=Decimal("1"))

        text = services.board_summary(services.ledger_band(hub), services.door_figures(hub))

        assert "Guild demos: 1 of 1 guild contacted, 1 demo planned." in text
        assert "Staff time: 1 hour (Lee 1), about $0 raised per staff hour." in text
        assert "Attendance: 1 person. Vendors: 1 artist sold $0 in total." in text

        hub.guild_demos.update(contacted=False)
        hub.staff_time.all().delete()
        hub.door_counts.all().delete()
        hub.vendor_tables.all().delete()
        text = services.board_summary(services.ledger_band(hub), services.door_figures(hub))
        assert "Guild demos" not in text and "Staff time" not in text and "Attendance" not in text

    def it_says_the_attendance_with_no_vendors_and_the_vendors_with_no_attendance(hub):
        DoorCountFactory(hub=hub, delta=5)

        assert "Attendance: 5 people. Vendors: 0 artists sold $0 in total." in services.board_summary(
            services.ledger_band(hub), services.door_figures(hub)
        )

    def it_puts_the_paragraph_on_the_page_and_in_the_box_copy_reads(busy, lee_client):
        body = lee_client.get(_page(busy)).content.decode()

        assert "data-hq-lg-summary>Holiday Bizarre drinks" in body
        assert "data-hq-lg-summary-box>Holiday Bizarre drinks" in body
        assert "const box = this.$root.querySelector('[data-hq-lg-summary-box]');" in body
        assert 'x-ref="summary"' not in body


def describe_the_rows():
    def it_names_each_new_row_plainly(hub):
        hub.title = "Spec HQ"
        guild = GuildFactory(name="Glass Guild")

        assert str(DoorCountFactory(hub=hub, delta=5)) == "+5 at the door"
        assert str(DoorCountFactory(hub=hub, delta=-1)) == "-1 at the door"
        assert str(LaunchTaskFactory(hub=hub, task="Post on Nextdoor")) == "Post on Nextdoor"
        assert str(StaffTimeFactory(hub=hub, who="Lee", hours=Decimal("2.5"), date=date(2026, 11, 2))) == (
            "Lee 2.5 h on 2026-11-02"
        )
        assert str(GuildDemoFactory(hub=hub, guild=guild)) == "Glass Guild (Spec HQ)"

    def it_links_only_the_doors_with_a_page(hub, doors):
        from hq.views import _door_urls

        HubCardFactory(hub=hub, kind=HubCard.Kind.RSVP, title="Going?", audience=HubCard.Audience.PUBLIC)
        HubCardFactory(hub=hub, kind=HubCard.Kind.PLACEHOLDER, title="Soon", audience=HubCard.Audience.PUBLIC)

        urls = _door_urls(hub, None, False)

        assert HubCard.Kind.RSVP not in urls  # no event, so the RSVP door has no page
        assert urls[HubCard.Kind.LEDGER] == _page(hub)
        assert set(urls) == {HubCard.Kind.LEDGER, *doors}


def describe_the_page():
    def it_lays_the_sections_out_in_lees_order(busy, lee_client):
        body = lee_client.get(_page(busy)).content.decode()

        order = [
            "data-hq-lg-tally",
            "data-hq-lg-goals",
            "data-hq-lg-board",
            "data-hq-lg-bar",
            "data-hq-lg-people",
            "data-hq-lg-wine",
            "data-hq-lg-launch",
            "data-hq-lg-staff",
            "data-hq-lg-guilds",
            "data-hq-lg-vol",
            "data-hq-lg-flyer",
        ]
        assert [body.index(marker) for marker in order] == sorted(body.index(marker) for marker in order)

    def it_runs_a_fixed_number_of_queries_however_many_rows(busy, lee_client, make_member):
        def _count() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert lee_client.get(_page(busy)).status_code == 200
            return len(queries)

        _count()  # the first request warms the session and the caches
        few = _count()
        LaunchTaskFactory.create_batch(3, hub=busy, due=date(2026, 1, 1))
        StaffTimeFactory.create_batch(3, hub=busy, who="Dixie")
        GuildDemoFactory.create_batch(3, hub=busy, demo=True)
        DoorCountFactory.create_batch(3, hub=busy)
        for _ in range(3):
            member = make_member()
            _report(busy, member, name=f"Artist {member.pk}", guild=GuildFactory())
            _hours(busy, member, "1")

        assert _count() == few

    def it_runs_a_fixed_number_of_queries_for_a_tap_and_a_row_save(busy, lee_client):
        task = LaunchTaskFactory(hub=busy)
        row = GuildDemoFactory(hub=busy)

        def _count(url: str, data: dict[str, Any]) -> int:
            with CaptureQueriesContext(connection) as queries:
                assert lee_client.post(url, data, **HTMX).status_code == 200
            return len(queries)

        lee_client.get(_page(busy))  # warms the session and the caches
        tap = _count(_url("ledger_door", busy), {"delta": "1"})
        task.refresh_from_db()
        save = _count(_url("ledger_launch_edit", busy, task.pk), _task_post(task))
        row.refresh_from_db()
        guild = _count(_url("ledger_guild_edit", busy, row.pk), _guild_post(row))
        GuildDemoFactory.create_batch(3, hub=busy)
        DoorCountFactory.create_batch(3, hub=busy)
        LaunchTaskFactory.create_batch(3, hub=busy)
        task.refresh_from_db()
        row.refresh_from_db()

        assert _count(_url("ledger_door", busy), {"delta": "1"}) == tap
        assert _count(_url("ledger_launch_edit", busy, task.pk), _task_post(task)) == save
        assert _count(_url("ledger_guild_edit", busy, row.pk), _guild_post(row)) == guild


# The four figures every answer that moves a tallied figure sends out of band (hq.views.FIGURES_ALL).
FIGURE_IDS = {"hq-lg-tally", "hq-lg-goals", "hq-lg-board-figures", "hq-lg-staff-figures"}
BAR_IDS = {"hq-lg-bar-tiles", "hq-lg-bar-buttons", "hq-lg-bar-table"}


def _oob_ids(body: str) -> set[str]:
    """The ids an htmx answer swaps out of band, each in a template tag."""
    assert body.count("<template>") == body.count('hx-swap-oob="true"')
    return set(re.findall(r'id="([^"]+)"[^>]*hx-swap-oob="true"', body))


def _seen(prefix: str, found: Any, names: tuple[str, ...]) -> dict[str, Any]:
    return {f"{prefix}-seen_{name}": getattr(found, name) for name in names}


def _quick() -> dict[str, str]:
    return {"quick-drink": BarSale.Drink.IPA, "quick-pay": BarSale.Pay.CARD}


def _batch() -> dict[str, str]:
    return {
        "batch-drink": BarSale.Drink.IPA,
        "batch-qty": "2",
        "batch-total": "",
        "batch-pay": "cash",
        "batch-note": "",
    }


def _prices(hub: Any, last_year: str = "650") -> dict[str, Any]:
    figures = ("ipa_cents", "pils_cents", "wine_cents", "na_cents", "last_year_bar_cents")
    values = {"ipa": "6", "pils": "6", "wine": "8", "na": "3", "last_year": last_year}
    found = services.ledger_settings(hub)
    return {**{f"prices-{key}": value for key, value in values.items()}, **_seen("prices", found, figures)}


def _tickets(hub: Any, singles: int = 4) -> dict[str, Any]:
    found = services.ledger_settings(hub)
    return {
        "tickets-singles_sold": singles,
        "tickets-pairs_sold": 1,
        **_seen("tickets", found, ("singles_sold", "pairs_sold")),
    }


def _ticket_prices(hub: Any) -> dict[str, Any]:
    found = services.ledger_settings(hub)
    return {
        "ticket-prices-ticket": "20",
        "ticket-prices-pair": "35",
        **_seen("ticket-prices", found, ("ticket_cents", "pair_cents")),
    }


def _line_post(line: BudgetLine, **fields: Any) -> dict[str, Any]:
    line.refresh_from_db()
    values: dict[str, Any] = {
        "name": line.name,
        "category": line.category,
        "budget": f"{line.budget_cents / 100:.2f}",
        "updated_at": line.updated_at.isoformat(),
    }
    values.update(fields)
    return {**{f"line-{line.pk}-{key}": value for key, value in values.items()}, "filter": "all"}


def _tile_figure(body: str, attribute: str, key: str) -> str:
    """The figure of the tile marked ``attribute="key"`` in ``body``."""
    after = body.split(f'{attribute}="{key}"', 1)[1]
    return re.search(r"data-hq-lg-figure>([^<]*)<", after).group(1)  # type: ignore[union-attr]  # the tile has one


def describe_the_figures_out_of_band():
    """Every answer that changes a tallied figure sends the figures that read it, so nothing on the page goes stale."""

    def it_sends_every_figure_with_a_tap_and_the_new_cash(hub, lee_client):
        StaffTimeFactory(hub=hub, who="Lee", hours=Decimal("1"))

        body = lee_client.post(_url("ledger_bar_quick", hub), _quick(), **HTMX).content.decode()

        assert _oob_ids(body) == BAR_IDS | FIGURE_IDS
        tally = body.split('id="hq-lg-tally"', 1)[1]
        assert re.search(r'data-hq-lg-tally-tile="cash".*?\$6\b', tally, re.S)
        assert _tile_figure(body, "data-hq-lg-staff-tile", "rate") == "$6"
        goals = body.split('id="hq-lg-goals"', 1)[1]
        assert re.search(r'data-hq-lg-goal="cash".*?\$6 of', goals, re.S)

    def it_sends_every_figure_with_an_undo(hub, lee, lee_client):
        BarSaleFactory(hub=hub, created_by=lee)

        response = lee_client.post(_url("ledger_bar_undo", hub), **HTMX)

        assert _toast(response)["message"] == "Took back 1 IPA (Card)."
        assert _oob_ids(response.content.decode()) == BAR_IDS | FIGURE_IDS

    def it_sends_every_figure_with_a_batch_and_none_with_a_refused_one(hub, lee_client):
        saved = lee_client.post(_url("ledger_bar_batch", hub), _batch(), **HTMX).content.decode()
        refused = lee_client.post(
            _url("ledger_bar_batch", hub), {**_batch(), "batch-qty": "", "batch-total": ""}, **HTMX
        ).content.decode()

        assert _oob_ids(saved) == {"hq-lg-bar-tiles", "hq-lg-bar-table"} | FIGURE_IDS
        assert _oob_ids(refused) == set()

    def it_sends_the_goals_with_the_drink_prices(hub, lee_client):
        body = lee_client.post(_url("ledger_bar_prices", hub), _prices(hub, "650"), **HTMX).content.decode()

        assert _oob_ids(body) == {"hq-lg-bar-buttons", "hq-lg-bar-tiles", "hq-lg-goals"}
        assert "Beat 2025 (about $650) and cover bar costs" in body.split('id="hq-lg-goals"', 1)[1]

    @pytest.mark.parametrize("form", ["tickets", "ticket_prices"])
    def it_sends_every_figure_with_the_tickets_and_their_prices(hub, lee_client, form):
        LedgerSettingsFactory(hub=hub, singles_sold=1)
        name, data = (
            ("ledger_tickets", _tickets(hub)) if form == "tickets" else ("ledger_ticket_prices", _ticket_prices(hub))
        )

        body = lee_client.post(_url(name, hub), data, **HTMX).content.decode()

        assert _oob_ids(body) == FIGURE_IDS
        assert 'data-hq-lg-tally-tile="tickets"' in body

    def it_sends_the_goals_and_the_board_with_a_budget_lines_save(hub, lee_client):
        line = BudgetLineFactory(hub=hub, name="Kegs")

        saved = lee_client.post(_url("ledger_line_edit", hub, line.pk), _line_post(line, category="drinks"), **HTMX)
        refused = lee_client.post(_url("ledger_line_edit", hub, line.pk), _line_post(line, name=""), **HTMX)

        assert _toast(saved)["message"] == "Saved."
        assert _oob_ids(saved.content.decode()) == {
            "hq-lg-lines-total",
            "hq-lg-entry-line",
            "hq-lg-entries",
            "hq-lg-bar-tiles",
            "hq-lg-goals",
            "hq-lg-board-figures",
        }
        assert _oob_ids(refused.content.decode()) == set()

    def it_sends_the_staff_figures_with_log_time(hub, lee_client):
        body = lee_client.post(_url("ledger_staff_add", hub), _staff_post(), **HTMX).content.decode()

        assert _oob_ids(body) == FIGURE_IDS

    def it_puts_the_staff_figures_in_the_section_on_the_page(hub, lee_client):
        body = lee_client.get(_page(hub)).content.decode()
        section = body.split('id="hq-lg-staff"', 1)[1].split("</section>", 1)[0]

        assert 'id="hq-lg-staff-figures" data-hq-lg-staff-figures>' in section
        assert 'hx-swap-oob="true"' not in body

    @pytest.mark.parametrize(
        "answer", ["tap", "undo", "batch", "prices", "tickets", "ticket_prices", "line", "staff", "guild_add"]
    )
    def it_answers_in_a_fixed_number_of_queries_however_many_rows(busy, lee, lee_client, answer):
        line = BudgetLine.objects.get(hub=busy, name="Kegs")

        def _post() -> tuple[str, dict[str, Any]]:
            BarSaleFactory(hub=busy, created_by=lee)  # Undo last always has a tap of Lee's to take back
            guild = GuildFactory()
            return {
                "tap": (_url("ledger_bar_quick", busy), _quick()),
                "undo": (_url("ledger_bar_undo", busy), {}),
                "batch": (_url("ledger_bar_batch", busy), _batch()),
                "prices": (_url("ledger_bar_prices", busy), _prices(busy)),
                "tickets": (_url("ledger_tickets", busy), _tickets(busy)),
                "ticket_prices": (_url("ledger_ticket_prices", busy), _ticket_prices(busy)),
                "line": (_url("ledger_line_edit", busy, line.pk), _line_post(line)),
                "staff": (_url("ledger_staff_add", busy), _staff_post()),
                "guild_add": (_url("ledger_guild_add", busy), {"guild-add-guild": str(guild.pk)}),
            }[answer]

        def _count() -> int:
            url, data = _post()
            with CaptureQueriesContext(connection) as queries:
                assert lee_client.post(url, data, **HTMX).status_code == 200
            return len(queries)

        lee_client.get(_page(busy))  # warms the session and the caches
        _count()
        few = _count()
        BarSaleFactory.create_batch(4, hub=busy, created_by=lee)
        StaffTimeFactory.create_batch(3, hub=busy, who="Dixie")
        GuildDemoFactory.create_batch(3, hub=busy, demo=True)
        DoorCountFactory.create_batch(3, hub=busy)
        WineBottleFactory.create_batch(3, hub=busy)
        LedgerEntryFactory.create_batch(3, hub=busy, line=line)
        LaunchTaskFactory.create_batch(3, hub=busy)

        assert _count() == few


def describe_the_plurals():
    @pytest.mark.parametrize(
        ("hours", "words"),
        [("1", "1 hour"), ("1.00", "1 hour"), ("1.5", "1.5 hours"), ("2", "2 hours"), ("0.25", "0.25 hours")],
    )
    def it_says_hours_in_the_right_number(hours, words):
        assert hours_words(Decimal(hours)) == words

    def it_writes_every_singular_in_the_board_paragraph(hub):
        line = BudgetLineFactory(hub=hub)
        LedgerEntryFactory(hub=hub, line=line)
        BarSaleFactory(hub=hub)
        WineBottleFactory(hub=hub, qty=1)
        LedgerSettingsFactory(hub=hub, singles_sold=1)

        text = services.board_summary(services.ledger_band(hub), services.door_figures(hub))

        assert "Drink sales: 1 drink sold for $6." in text
        assert "1 ticket sold (1 single at $25 + 0 deals at $40 for 2) raised $25." in text

    def it_writes_the_volunteer_and_staff_hours_in_the_right_number(hub, make_member):
        line = BudgetLineFactory(hub=hub)
        LedgerEntryFactory(hub=hub, line=line)
        _hours(hub, make_member(), "1.5")
        StaffTimeFactory(hub=hub, who="Lee", hours=Decimal("0.25"))

        text = services.board_summary(services.ledger_band(hub), services.door_figures(hub))

        assert "Staff time: 0.25 hours (Lee 0.25)" in text
        assert "Volunteers: 1 person gave 1.5 hours to event prep." in text

    def it_writes_the_singulars_on_the_tally_and_its_cheer(hub, make_member):
        HubGoalFactory(hub=hub, kind=HubGoal.Kind.BOTTLES, label="Bottles", target=2)
        WineBottleFactory(hub=hub, qty=1)
        LedgerSettingsFactory(hub=hub, singles_sold=1)
        _report(hub, make_member(), items_sold=1, sales_cents=1_000)
        MarketingActionFactory(hub=hub, kind=MarketingKind.FLYER)
        GuildDemoFactory(hub=hub)

        tiles = _tiles(hub)
        cheer = _cheer(hub)

        assert tiles["tickets"].sub.startswith("1 ticket · ")
        assert tiles["sold"].sub == "1 handmade item found new homes"
        assert "1 bottle to fill the wine wall" in cheer
        assert "1 guild still to line up a demo" in cheer
        assert "Drop a flyer somewhere today. 1 business so far" in cheer

    def it_writes_the_plurals_on_the_tally_and_its_cheer(busy):
        tiles = _tiles(busy)

        assert tiles["tickets"].sub.startswith("5 tickets · ")
        assert tiles["sold"].sub == "14 handmade items found new homes"
        assert "88 bottles to fill the wine wall" in _cheer(busy)

    def it_says_one_ticket_at_goal_in_the_singular(hub):
        HubGoalFactory(hub=hub, kind=HubGoal.Kind.BOTTLES, label="Bottles", target=1)
        totals = services.ticket_totals(services.ledger_settings(hub), services.wine_band(hub))

        assert totals.at_goal_line == "1 ticket at $25, or 2 for $40"

    def it_says_one_hour_when_an_entry_is_deleted(hub, lee_client):
        entry = StaffTimeFactory(hub=hub, who="Lee", hours=Decimal("1"))
        two = StaffTimeFactory(hub=hub, who="Lee", hours=Decimal("2"))

        one = lee_client.post(_url("ledger_staff_delete", hub, entry.pk), follow=True)
        many = lee_client.post(_url("ledger_staff_delete", hub, two.pk), follow=True)

        assert "Deleted Lee&#x27;s 1 hour." in one.content.decode()
        assert "Deleted Lee&#x27;s 2 hours." in many.content.decode()

    def it_totals_one_vendor_in_the_singular_in_the_csv(hub, lee_client):
        VendorTableFactory(hub=hub, status="confirmed")

        response = lee_client.get(_url("ledger_people_csv", hub))
        rows = list(csv.reader(io.StringIO(b"".join(response.streaming_content).decode("utf-8-sig"))))

        assert ["Total", "1 vendor"] == next(row for row in rows if row and row[0] == "Total")[:2]


def describe_live_guilds():
    """A guild that went inactive or was soft deleted keeps its row, but only live guilds count anywhere."""

    @pytest.fixture
    def mixed(hub):
        reached = GuildFactory(name="Glass Guild")
        GuildFactory(name="Wood Guild")  # active, never reached
        retired = GuildFactory(name="Old Guild", is_active=False)
        deleted = GuildFactory(name="Gone Guild")
        GuildDemoFactory(hub=hub, guild=reached, contacted=True, demo=True)
        GuildDemoFactory(hub=hub, guild=retired, contacted=True, demo=True)
        GuildDemoFactory(hub=hub, guild=deleted, contacted=True, demo=True)
        deleted.soft_delete()
        LedgerEntryFactory(hub=hub, line=BudgetLineFactory(hub=hub))  # the paragraph speaks once there is an entry
        HubGoalFactory(hub=hub, kind=HubGoal.Kind.GUILDS, label="Guilds taking part", target=0)
        return hub

    def it_counts_only_the_live_guilds_on_the_list(mixed):
        totals = services.guild_demo_totals(mixed)

        assert (totals.guilds, totals.rows, totals.contacted, totals.demos) == (2, 1, 1, 1)
        assert totals.summary("") == "1 of 2 contacted · 1 demo planned"
        assert services.guilds_with_demos(mixed) == 1
        assert [row.current for row in services.goal_rows(mixed) if row.label == "Guilds taking part"] == [1]

    def it_never_reads_all_contacted_with_a_live_guild_never_reached(mixed):
        row = _goal_rows(mixed)["guilds"]
        tile = _tiles(mixed)["guilds"]

        assert (row.figure, row.chip) == ("1 / 2 contacted", "1 to contact")
        assert (tile.value, tile.goal) == (1, 2)
        assert "1 guild still to line up a demo" in _cheer(mixed)
        assert "Guild demos: 1 of 2 guilds contacted, 1 demo planned." in services.board_summary(
            services.ledger_band(mixed), services.door_figures(mixed)
        )

    def it_marks_a_deleted_guilds_row_as_an_inactive_ones(mixed, lee_client):
        body = lee_client.get(_page(mixed)).content.decode()

        for name in ("Old Guild", "Gone Guild"):
            assert f'{name} <span class="pl-hq-lg__cat">Inactive</span>' in body
        assert 'Glass Guild <span class="pl-hq-lg__cat">' not in body
        assert "1 of 2 contacted · 1 demo planned" in body


def describe_the_add_guild_race():
    def it_locks_the_hubs_row_before_it_checks_and_adds(hub):
        guild = GuildFactory(name="Glass Guild")

        with CaptureQueriesContext(connection) as queries:
            row = services.add_guild_demo(hub, guild, lead="")

        sql = [query["sql"] for query in queries]
        lock = next(i for i, text in enumerate(sql) if "FOR UPDATE" in text and '"hq_eventhub"' in text)
        insert = next(i for i, text in enumerate(sql) if text.startswith('INSERT INTO "hq_guilddemo"'))
        assert lock < insert
        assert row.guild == guild

    def it_refuses_a_guild_another_add_listed_after_the_form_was_read(hub, lee_client, monkeypatch):
        listed = GuildDemoFactory(hub=hub, guild=GuildFactory(name="Glass Guild"), lead="Kept")
        # The race: the form read the choices before Add every active guild listed the guild.
        monkeypatch.setattr(services, "guilds_to_add", lambda hub: [listed.guild])

        response = lee_client.post(_url("ledger_guild_add", hub), {"guild-add-guild": str(listed.guild.pk)}, **HTMX)
        plain = lee_client.post(_url("ledger_guild_add", hub), {"guild-add-guild": str(listed.guild.pk)}, follow=True)

        assert response.status_code == 200
        assert _toast(response) == {"message": "Glass Guild is already on the list.", "type": "error"}
        assert plain.redirect_chain[-1][0] == f"{_page(hub)}#hq-lg-guilds"
        assert "Glass Guild is already on the list." in [str(message) for message in plain.context["messages"]]
        assert list(hub.guild_demos.values_list("lead", flat=True)) == ["Kept"]

    def it_refuses_by_name_when_a_row_lands_between_the_check_and_the_insert(hub, monkeypatch):
        guild = GuildFactory(name="Glass Guild")
        clash = IntegrityError('duplicate key value violates unique constraint "uq_guilddemo_hub_guild"')

        def _late_row(*args: Any, **kwargs: Any) -> None:
            raise clash

        monkeypatch.setattr(GuildDemo.objects, "create", _late_row)
        with pytest.raises(services.GuildAlreadyListed, match="Glass Guild is already on the list.") as raised:
            services.add_guild_demo(hub, guild, lead="")

        assert raised.value.__cause__ is clash
        assert not hub.guild_demos.exists()  # the connection is still usable
