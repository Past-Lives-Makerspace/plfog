"""Vendor Tables pull Kate's Google Sheet every morning (#1013): the pull's rule ("Kate's changes win": a cell changed
in the sheet since the last pull is applied, a blanked one clears its field, an untouched one leaves the card), the
sheet link (Google Sheets links only, the tab from gid), the one fetch (never a network call here: respx mocks its
httpx call, and every other spec replaces the function), the last pull on the page, the gates (the Team pulls, an
admin sets the link), and the daily job's registry entry and failure path. Every name, email and sheet id is made
up (this repo is public)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx
from django.contrib.messages import get_messages
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import ScheduledTaskRun
from core.scheduled_jobs import JOBS_BY_KEY, Cadence, Trigger, record_run
from hq import services, vendor_sheet
from hq.models import HubCard, VendorFloor, VendorTable
from hq.vendor_import import MAX_IMPORT_BYTES, ImportResult, apply_pull, cleared_value, parse_rows, read_csv
from hq.vendor_sheet import (
    NO_ANSWER,
    NOT_A_SHEET_LINK,
    NOT_FOUND,
    NOT_GOOGLE,
    NOT_SHARED,
    TOO_LARGE,
    SheetLink,
    SheetUnreadable,
    fetch_sheet_csv,
    sheet_link,
)
from hub.view_as import ROLE_MEMBER
from tests.hq.conftest import signed_in
from tests.hq.factories import EventHubFactory, HubCardFactory, VendorFloorFactory, VendorTableFactory
from tests.membership.factories import CommunityEventFactory, GuildFactory

pytestmark = pytest.mark.django_db

S = VendorTable.Status
SHEET_ID = "1SpecVendorSheetIdAbCdEfGhIjKlMnOpQrStUvW"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0"
EXPORT_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"
HEADER = "Name,Guild,Table size,Status,What they sell,Requested on,Email"


def _page(hub: Any) -> str:
    return reverse("hq:vendors", args=[hub.slug])


def _pull_url(hub: Any) -> str:
    return reverse("hq:vendor_pull", args=[hub.slug])


def _sheet_url(hub: Any) -> str:
    return reverse("hq:vendor_sheet", args=[hub.slug])


def _messages(response: Any) -> list[tuple[str, str]]:
    return [(message.level_tag, str(message)) for message in get_messages(response.wsgi_request)]


@pytest.fixture
def kate(make_member):
    return make_member(preferred_name="Kate Lindqvist")


@pytest.fixture
def card(open_hub, kate) -> HubCard:
    return HubCardFactory(
        hub=open_hub,
        kind=HubCard.Kind.VENDOR_TABLES,
        title="Vendor Tables",
        audience=HubCard.Audience.ADMINS,
        team=[kate],
    )


@pytest.fixture
def hub(card):
    return card.hub


@pytest.fixture
def linked(hub) -> VendorFloor:
    """The hub with Kate's sheet linked."""
    return VendorFloorFactory(hub=hub, capacity=None, sheet_url=SHEET_URL)


@pytest.fixture
def kate_client(kate) -> Client:
    return signed_in(kate)


class FakeSheet:
    """The sheet as Google would export it: replaces ``hq.vendor_sheet.fetch_sheet_csv`` so no spec reaches the
    network. ``text`` is what the next pull reads; ``error`` makes the next pull fail with it."""

    def __init__(self) -> None:
        self.text = f"{HEADER}\n"
        self.error = ""
        self.links: list[SheetLink] = []

    def __call__(self, link: SheetLink) -> bytes:
        self.links.append(link)
        if self.error:
            raise SheetUnreadable(self.error)
        return self.text.encode()


@pytest.fixture
def sheet() -> Iterator[FakeSheet]:
    fake = FakeSheet()
    with patch.object(vendor_sheet, "fetch_sheet_csv", fake):
        yield fake


def _pull(hub: Any, sheet: FakeSheet, *rows: str) -> ImportResult:
    sheet.text = "\n".join((HEADER, *rows)) + "\n"
    return services.pull_vendor_sheet(hub)


def _table(hub: Any, name: str) -> VendorTable:
    return hub.vendor_tables.get(name=name)


def describe_the_pulls_rule():
    def it_adds_a_new_name_made_by_nobody_with_the_sheets_requested_on(hub, linked, sheet):
        result = _pull(hub, sheet, "Juniper,,8 ft,Requested,Cutting boards,2026-10-21,juniper@example.com")

        table = _table(hub, "Juniper")
        assert (result.added, result.updated, result.unchanged) == (1, 0, 0)
        assert table.created_by is None
        assert table.requested_on == date(2026, 10, 21)
        assert table.size == VendorTable.Size.EIGHT_FT
        assert table.sheet_cells["status"] == "Requested"

    def it_keeps_a_status_confirmed_on_the_card_while_the_sheets_cell_has_not_changed(hub, linked, sheet):
        row = "Juniper,,8 ft,Requested,Cutting boards,2026-10-21,juniper@example.com"
        _pull(hub, sheet, row)
        table = _table(hub, "Juniper")
        table.status = S.CONFIRMED
        table.save()

        result = _pull(hub, sheet, row)

        table.refresh_from_db()
        assert table.status == S.CONFIRMED
        assert (result.added, result.updated, result.unchanged) == (0, 0, 1)

    def it_sets_waitlist_once_kate_changes_that_cell(hub, linked, sheet):
        _pull(hub, sheet, "Juniper,,8 ft,Requested,Cutting boards,,")
        table = _table(hub, "Juniper")
        table.status = S.CONFIRMED
        table.sells = "Boards and spoons"
        table.save()

        result = _pull(hub, sheet, "Juniper,,8 ft,Waitlist,Cutting boards,,")

        table.refresh_from_db()
        assert table.status == S.WAITLIST
        assert table.sells == "Boards and spoons"  # Kate did not touch that cell
        assert result.updated == 1

    def it_clears_a_field_whose_cell_kate_blanked(hub, linked, sheet):
        guild = GuildFactory(name="Ceramics Guild")
        _pull(hub, sheet, "Wren,Ceramics,4 ft,Waitlist,Mugs,2026-10-01,wren@example.com")

        _pull(hub, sheet, "Wren,,,,,,")

        table = _table(hub, "Wren")
        assert guild.pk  # the guild is still there; only the table's link to it cleared
        assert table.guild is None
        assert table.size == VendorTable.Size.SIX_FT
        assert table.status == S.REQUESTED
        assert table.sells == ""
        assert table.requested_on is None
        assert table.email == ""

    def it_gives_a_never_pulled_table_the_sheets_filled_cells_without_a_duplicate(hub, linked, sheet):
        table = VendorTableFactory(hub=hub, name="Spec Silversmith", sells="Belts", status=S.CONFIRMED, email="")

        result = _pull(hub, sheet, "spec  silversmith,,Bringing own,,Lapidary,,spec@example.com")

        table.refresh_from_db()
        assert hub.vendor_tables.count() == 1
        assert (result.added, result.updated) == (0, 1)
        assert table.sells == "Lapidary"
        assert table.email == "spec@example.com"
        assert table.size == VendorTable.Size.OWN
        assert table.status == S.CONFIRMED  # a blank cell on a first pull never clears
        assert table.sheet_cells["name"] == "spec  silversmith"

    def it_never_renames_a_table_whose_name_matches_only_after_normalising(hub, linked, sheet):
        _pull(hub, sheet, "Moss and Fern,,,,Wallets,,")

        _pull(hub, sheet, "MOSS AND  FERN,,,,Wallets,,")

        assert _table(hub, "Moss and Fern").sells == "Wallets"

    def it_leaves_a_table_the_sheet_does_not_have_untouched(hub, linked, sheet):
        only_on_card = VendorTableFactory(hub=hub, name="Card Only", sells="Prints", status=S.CONFIRMED)
        before = VendorTable.objects.filter(pk=only_on_card.pk).values().get()

        _pull(hub, sheet, "Juniper,,,,,,")
        _pull(hub, sheet, "Juniper,,,Waitlist,,,")

        assert VendorTable.objects.filter(pk=only_on_card.pk).values().get() == before

    def it_keeps_the_cards_value_for_a_cell_it_cannot_read_and_reports_it_every_pull(hub, linked, sheet):
        _pull(hub, sheet, "Juniper,,8 ft,Requested,,,")
        table = _table(hub, "Juniper")

        first = _pull(hub, sheet, "Juniper,,huge,Requested,,,")
        again = _pull(hub, sheet, "Juniper,,huge,Requested,,,")

        table.refresh_from_db()
        assert table.size == VendorTable.Size.EIGHT_FT
        assert [problem.line for problem in first.problems] == [2]
        assert [problem.line for problem in again.problems] == [2]
        assert table.sheet_cells["size"] == "8 ft"  # an unreadable cell is never remembered, so it is retried

    def it_applies_an_unreadable_cell_once_the_portal_knows_its_value(hub, linked, sheet):
        _pull(hub, sheet, "Juniper,Glassblowing,,,,,")
        assert _table(hub, "Juniper").guild is None
        assert "guild" not in _table(hub, "Juniper").sheet_cells

        guild = GuildFactory(name="Glassblowing Guild")
        result = _pull(hub, sheet, "Juniper,Glassblowing,,,,,")

        assert _table(hub, "Juniper").guild == guild
        assert (result.updated, result.problems) == (1, [])

    def it_keeps_a_columns_memory_while_the_sheet_lacks_it_so_restoring_it_changes_nothing(hub, linked, sheet):
        _pull(hub, sheet, "Juniper,,,Requested,Boards,,")
        table = _table(hub, "Juniper")
        table.status = S.CONFIRMED
        table.save()

        sheet.text = "Name,What they sell\nJuniper,Boards\n"  # Kate deleted the Status column for a day
        services.pull_vendor_sheet(hub)
        _pull(hub, sheet, "Juniper,,,Requested,Boards,,")  # and put it back

        table.refresh_from_db()
        assert table.status == S.CONFIRMED
        assert table.sheet_cells["status"] == "Requested"

    def it_keeps_a_columns_memory_while_its_header_is_renamed_and_restored(hub, linked, sheet):
        _pull(hub, sheet, "Juniper,,,Requested,Boards,,")
        table = _table(hub, "Juniper")
        table.status = S.CONFIRMED
        table.save()

        sheet.text = "Name,State,What they sell\nJuniper,Requested,Boards\n"  # Status renamed to State
        services.pull_vendor_sheet(hub)
        _pull(hub, sheet, "Juniper,,,Requested,Boards,,")

        table.refresh_from_db()
        assert table.status == S.CONFIRMED

    def it_applies_the_cell_once_kate_fixes_what_could_not_be_read(hub, linked, sheet):
        _pull(hub, sheet, "Juniper,,huge,Requested,,,")

        _pull(hub, sheet, "Juniper,,4 ft,Requested,,,")

        assert _table(hub, "Juniper").size == VendorTable.Size.FOUR_FT

    def it_reads_a_column_new_to_the_sheet_as_changed_when_filled(hub, linked, sheet):
        sheet.text = "Name,Status\nJuniper,Requested\n"
        services.pull_vendor_sheet(hub)
        table = _table(hub, "Juniper")
        table.sells = "Boards"
        table.save()

        sheet.text = "Name,Status,What they sell,Phone\nJuniper,Requested,,503-555-0199\n"
        services.pull_vendor_sheet(hub)

        table.refresh_from_db()
        assert table.sells == "Boards"  # a new column left blank changes nothing
        assert table.phone == "503-555-0199"

    def it_uses_the_first_of_two_rows_with_one_name_and_reports_the_second(hub, linked, sheet):
        result = _pull(hub, sheet, "Juniper,,,Requested,Boards,,", "juniper,,,Waitlist,Spoons,,")

        table = _table(hub, "Juniper")
        assert hub.vendor_tables.count() == 1
        assert (table.status, table.sells) == (S.REQUESTED, "Boards")
        assert [str(problem) for problem in result.problems] == [
            'Row 3: "juniper" is also on row 2, so this row was skipped.'
        ]

    def it_reports_a_row_with_no_name_and_writes_nothing_for_it(hub, linked, sheet):
        result = _pull(hub, sheet, ",,,Requested,Boards,,")

        assert hub.vendor_tables.count() == 0
        assert [problem.line for problem in result.problems] == [2]

    def it_remembers_new_cells_without_a_save_when_nothing_on_the_card_changes(hub, linked, sheet):
        _pull(hub, sheet, "Juniper,,,Requested,Boards,,")
        table = _table(hub, "Juniper")
        table.status = S.CONFIRMED
        table.save()
        stamp = VendorTable.objects.get(pk=table.pk).updated_at

        result = _pull(hub, sheet, "Juniper,,,Confirmed,Boards,,")

        table.refresh_from_db()
        assert result.unchanged == 1
        assert table.updated_at == stamp  # an open row form on the page is not made stale
        assert table.sheet_cells["status"] == "Confirmed"

    @pytest.mark.postgres_only
    def it_locks_the_hub_before_reading_its_tables_so_two_pulls_take_turns(hub):
        sheet_rows = parse_rows(read_csv(f"{HEADER}\nJuniper,,,,,,\n".encode()), guilds=[])

        with CaptureQueriesContext(connection) as queries:
            apply_pull(hub, sheet_rows)

        sql = [query["sql"] for query in queries.captured_queries]
        hub_lock = next(i for i, text in enumerate(sql) if "FOR UPDATE" in text)
        assert '"hq_eventhub"' in sql[hub_lock]
        assert hub_lock < next(i for i, text in enumerate(sql) if '"hq_vendortable"' in text)

    def it_writes_in_one_transaction_under_the_rows_lock(hub, sheet):
        VendorTableFactory(hub=hub, name="Juniper")
        sheet_rows = parse_rows(read_csv(f"{HEADER}\nJuniper,,,Waitlist,,,\n".encode()), guilds=[])

        result = apply_pull(hub, sheet_rows)

        assert result.updated == 1

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("guild", None),
            ("requested_on", None),
            ("size", VendorTable.Size.SIX_FT),
            ("status", S.REQUESTED),
            ("setup", ""),
            ("email", ""),
            ("sells", ""),
        ],
    )
    def it_clears_each_kind_of_field_to_its_empty_value(key, value):
        assert cleared_value(key) == value


def describe_the_sheet_link():
    @pytest.mark.parametrize(
        ("url", "gid"),
        [
            (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0", "0"),
            (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?usp=sharing", "0"),
            (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?gid=1234#gid=1234", "1234"),
            (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=987", "987"),
            (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}", "0"),
            (f"  https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?gid=abc  ", "0"),
        ],
    )
    def it_reads_the_sheet_id_and_its_tab(url, gid):
        link = sheet_link(url)

        assert link == SheetLink(sheet_id=SHEET_ID, gid=gid)
        assert link.edit_url == f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid={gid}"
        assert link.export_url == f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}"

    @pytest.mark.parametrize(
        "url",
        [
            f"http://docs.google.com/spreadsheets/d/{SHEET_ID}/edit",
            f"https://docs.google.com.example.com/spreadsheets/d/{SHEET_ID}/edit",
            f"https://example.com/spreadsheets/d/{SHEET_ID}/edit",
            f"https://docs.google.com:8443/spreadsheets/d/{SHEET_ID}/edit",
            f"https://docs.google.com/document/d/{SHEET_ID}/edit",
            "https://docs.google.com/spreadsheets/d/e/2PACX-1vSpecPublishedSheet/pubhtml",
            "https://docs.google.com/spreadsheets/d/short/edit",
            f"https://evil.example\\@docs.google.com/spreadsheets/d/{SHEET_ID}/edit",
            f"https://evil.example@docs.google.com/spreadsheets/d/{SHEET_ID}/edit",
            f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit\\..\\x",
            "not a link",
        ],
    )
    def it_refuses_anything_but_a_google_sheets_link(url):
        with pytest.raises(ValueError, match="not a Google Sheets link"):
            sheet_link(url)


def describe_fetching_the_sheet():
    LINK = SheetLink(sheet_id=SHEET_ID, gid="0")

    @respx.mock
    def it_reads_the_public_csv_export_following_googles_redirect():
        content = "https://doc-0s-sheets.googleusercontent.com/export/spec"
        respx.get(EXPORT_URL).mock(return_value=httpx.Response(307, headers={"location": content}))
        respx.get(content).mock(
            return_value=httpx.Response(200, content=b"Name\nJuniper\n", headers={"content-type": "text/csv"})
        )

        assert fetch_sheet_csv(LINK) == b"Name\nJuniper\n"

    @respx.mock
    def it_says_the_sheet_is_not_shared_when_google_sends_a_sign_in_page():
        sign_in = "https://accounts.google.com/ServiceLogin?continue=spec"
        respx.get(EXPORT_URL).mock(return_value=httpx.Response(302, headers={"location": sign_in}))
        respx.get(sign_in).mock(
            return_value=httpx.Response(200, content=b"<html>Sign in</html>", headers={"content-type": "text/html"})
        )

        with pytest.raises(SheetUnreadable, match="not shared with anyone with the link"):
            fetch_sheet_csv(LINK)

    @respx.mock
    def it_says_the_sheet_is_not_shared_for_an_html_page():
        respx.get(EXPORT_URL).mock(
            return_value=httpx.Response(
                200, content=b"<!doctype html><title>Sign in</title>", headers={"content-type": "text/html"}
            )
        )

        with pytest.raises(SheetUnreadable) as refused:
            fetch_sheet_csv(LINK)
        assert str(refused.value) == NOT_SHARED

    @respx.mock
    @pytest.mark.parametrize(
        ("status", "message"),
        [
            (401, NOT_SHARED),
            (403, NOT_SHARED),
            (404, NOT_FOUND),
            (500, "Google answered with an error (500). The pull will try again tomorrow."),
        ],
    )
    def it_refuses_an_answer_that_is_not_the_csv(status, message):
        respx.get(EXPORT_URL).mock(return_value=httpx.Response(status))

        with pytest.raises(SheetUnreadable) as refused:
            fetch_sheet_csv(LINK)
        assert str(refused.value) == message

    @respx.mock
    def it_refuses_a_redirect_that_leads_outside_google_without_following_it():
        respx.get(EXPORT_URL).mock(return_value=httpx.Response(302, headers={"location": "https://evil.example/x"}))
        elsewhere = respx.get("https://evil.example/x")

        with pytest.raises(SheetUnreadable) as refused:
            fetch_sheet_csv(LINK)
        assert str(refused.value) == NOT_GOOGLE
        assert not elsewhere.called

    @respx.mock
    def it_says_google_did_not_answer_when_the_request_fails():
        respx.get(EXPORT_URL).mock(side_effect=httpx.ConnectTimeout("spec"))

        with pytest.raises(SheetUnreadable) as refused:
            fetch_sheet_csv(LINK)
        assert str(refused.value) == NO_ANSWER

    @respx.mock
    def it_refuses_a_sheet_over_the_size_cap_without_reading_it_whole():
        respx.get(EXPORT_URL).mock(
            return_value=httpx.Response(
                200, content=b"x" * (MAX_IMPORT_BYTES + 1), headers={"content-type": "text/csv"}
            )
        )

        with pytest.raises(SheetUnreadable) as refused:
            fetch_sheet_csv(LINK)
        assert str(refused.value) == TOO_LARGE


def describe_pulling_a_hub():
    def it_records_when_it_ran_what_it_did_and_its_problems(hub, linked, sheet):
        _pull(hub, sheet, "Juniper,,huge,,,,", "Wren,,,,,,")

        state = services.vendor_sheet_state(hub)
        assert state.pulled_at is not None
        assert state.error == ""
        assert state.summary == "Pulled from the sheet: 2 added, 0 updated, 0 unchanged. 1 problem to check."
        assert len(state.problems) == 1
        assert state.problems[0].startswith('Row 2: Table size "huge"')
        assert sheet.links == [SheetLink(sheet_id=SHEET_ID, gid="0")]

    def it_leaves_every_row_as_it_was_and_records_why_when_the_sheet_cannot_be_read(hub, linked, sheet):
        table = VendorTableFactory(hub=hub, name="Juniper", status=S.CONFIRMED)
        before = list(hub.vendor_tables.values())
        sheet.error = NOT_SHARED

        with pytest.raises(SheetUnreadable):
            _pull(hub, sheet, "Juniper,,,Waitlist,,,", "Wren,,,,,,")

        state = services.vendor_sheet_state(hub)
        assert list(hub.vendor_tables.values()) == before
        assert table.pk
        assert (state.error, state.summary, state.problems) == (NOT_SHARED, "", ())
        assert state.pulled_at is not None

    def it_records_a_sheet_the_reader_refuses_as_the_error(hub, linked, sheet):
        sheet.text = "Artist,Status\nJuniper,Requested\n"

        with pytest.raises(SheetUnreadable, match="no Name column"):
            services.pull_vendor_sheet(hub)

        assert "no Name column" in services.vendor_sheet_state(hub).error
        assert hub.vendor_tables.count() == 0

    def it_clears_the_last_error_once_a_pull_works(hub, linked, sheet):
        sheet.error = NOT_SHARED
        with pytest.raises(SheetUnreadable):
            _pull(hub, sheet)
        sheet.error = ""

        _pull(hub, sheet, "Juniper,,,,,,")

        state = services.vendor_sheet_state(hub)
        assert state.error == ""
        assert state.summary == "Pulled from the sheet: 1 added, 0 updated, 0 unchanged."

    def it_refuses_a_hub_with_no_sheet_link(hub, sheet):
        VendorFloorFactory(hub=hub, sheet_url="")

        with pytest.raises(services.NoVendorSheet):
            services.pull_vendor_sheet(hub)
        assert sheet.links == []

    def it_reads_blanks_for_a_hub_with_no_floor_row(hub):
        assert services.vendor_sheet_state(hub) == services.VendorSheetState()

    def it_pulls_every_hub_with_a_link_and_names_the_ones_that_failed(hub, linked, sheet):
        other = EventHubFactory(title="Spec Spring Market")
        VendorFloorFactory(hub=other, sheet_url=SHEET_URL)
        VendorFloorFactory(hub=EventHubFactory(), sheet_url="")
        calls: list[str] = []

        def fetch(link: SheetLink) -> bytes:
            calls.append(link.sheet_id)
            if len(calls) == 1:
                return b"Name\nJuniper\n"
            raise SheetUnreadable(NOT_SHARED)

        with patch.object(vendor_sheet, "fetch_sheet_csv", fetch):
            failed = services.pull_every_vendor_sheet()

        assert len(calls) == 2
        assert failed == [(other, NOT_SHARED)]
        assert hub.vendor_tables.filter(name="Juniper").exists()

    def it_records_any_other_failure_on_that_hub_and_still_pulls_the_rest(hub, linked, sheet, caplog):
        other = EventHubFactory(title="Spec Spring Market")
        VendorFloorFactory(hub=other, sheet_url=SHEET_URL)
        calls: list[str] = []

        def fetch(link: SheetLink) -> bytes:
            calls.append(link.sheet_id)
            if len(calls) == 1:
                raise RuntimeError("spec bug")
            return b"Name\nJuniper\n"

        with patch.object(vendor_sheet, "fetch_sheet_csv", fetch):
            failed = services.pull_every_vendor_sheet()

        assert failed == [(hub, services.PULL_FAILED)]
        assert services.vendor_sheet_state(hub).error == services.PULL_FAILED
        assert other.vendor_tables.filter(name="Juniper").exists()
        assert "Vendor sheet pull failed" in caplog.text

    def it_leaves_out_hubs_whose_event_has_ended(hub, linked, sheet):
        now = timezone.now()
        past = EventHubFactory(
            event=CommunityEventFactory(starts_at=now - timedelta(days=2), ends_at=now - timedelta(days=1))
        )
        VendorFloorFactory(hub=past, sheet_url=SHEET_URL)
        coming = EventHubFactory(
            event=CommunityEventFactory(starts_at=now + timedelta(days=30), ends_at=now + timedelta(days=30, hours=5))
        )
        VendorFloorFactory(hub=coming, sheet_url=SHEET_URL)
        sheet.text = "Name\nJuniper\n"

        assert services.pull_every_vendor_sheet() == []

        assert len(sheet.links) == 2  # this hub (no event) and the coming one
        assert not past.vendor_tables.exists()
        assert coming.vendor_tables.filter(name="Juniper").exists()


def describe_saving_the_link():
    def it_makes_the_floor_row_and_saves_the_link(hub):
        floor = services.set_vendor_sheet(hub, SHEET_URL)

        assert (floor.sheet_url, floor.capacity) == (SHEET_URL, None)

    def it_forgets_the_last_pull_when_the_link_changes_and_keeps_the_remembered_cells(hub, linked, sheet):
        _pull(hub, sheet, "Juniper,,,Requested,,,")

        services.set_vendor_sheet(hub, f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=55")

        state = services.vendor_sheet_state(hub)
        assert (state.pulled_at, state.summary) == (None, "")
        assert _table(hub, "Juniper").sheet_cells["status"] == "Requested"

    def it_stores_the_address_built_from_the_sheets_id_not_the_pasted_text(hub):
        floor = services.set_vendor_sheet(
            hub, f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?usp=sharing&gid=7"
        )

        assert floor.sheet_url == f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=7"

    def it_keeps_the_last_pull_when_the_same_link_is_saved_again(hub, linked, sheet):
        _pull(hub, sheet, "Juniper,,,,,,")

        services.set_vendor_sheet(hub, SHEET_URL)

        assert services.vendor_sheet_state(hub).summary.startswith("Pulled from the sheet")

    def it_keeps_the_space_map_when_the_link_is_saved(hub):
        VendorFloorFactory(hub=hub, capacity=24, map_note="18 in the gallery")

        services.set_vendor_sheet(hub, SHEET_URL)

        floor = VendorFloor.objects.get(hub=hub)
        assert (floor.capacity, floor.map_note, floor.sheet_url) == (24, "18 in the gallery", SHEET_URL)


def describe_the_page():
    def it_offers_the_team_pull_from_sheet_now_only_with_a_link(hub, kate_client):
        assert "data-hq-vt-pull" not in kate_client.get(_page(hub)).content.decode()

        VendorFloorFactory(hub=hub, sheet_url=SHEET_URL)
        body = kate_client.get(_page(hub)).content.decode()

        assert f'action="{_pull_url(hub)}"' in body
        assert "data-hq-vt-pull-answer" in body
        assert "not pulled yet" in body

    def it_shows_the_last_pull_with_its_problems_by_row(hub, linked, sheet, kate_client):
        rows = [f"Spec Vendor {n},,huge,,,," for n in range(12)]
        _pull(hub, sheet, *rows)

        body = kate_client.get(_page(hub)).content.decode()

        answer = body.split("data-hq-vt-pull-answer")[1].split("</div>")[0]
        assert "Last pulled" in answer
        assert "Pulled from the sheet: 12 added, 0 updated, 0 unchanged. 12 problems to check." in answer
        assert answer.count("<li>") == 10
        assert "And 2 more." in answer
        assert f'href="{SHEET_URL}"' in answer

    def it_shows_why_the_sheet_could_not_be_read_in_red(hub, linked, sheet, kate_client):
        sheet.error = NOT_SHARED
        with pytest.raises(SheetUnreadable):
            _pull(hub, sheet)

        body = kate_client.get(_page(hub)).content.decode()

        opening = body.split("data-hq-vt-pull-answer")[0].rsplit("<div", 1)[1]
        answer = body.split("data-hq-vt-pull-answer")[1].split("</div>")[0]
        assert "pl-hq-vt__import-answer--refused" in opening
        assert "data-hq-vt-pull-error" in answer
        assert "The sheet is not shared with anyone with the link." in answer

    def it_shows_the_link_field_to_admins_only(hub, kate_client, admin_client):
        assert "data-hq-vt-sheet-form" not in kate_client.get(_page(hub)).content.decode()

        body = admin_client.get(_page(hub)).content.decode()

        assert f'action="{_sheet_url(hub)}"' in body
        assert 'name="sheet_url"' in body


def describe_pull_from_sheet_now():
    def it_pulls_for_the_team_and_answers_with_a_toast(hub, linked, sheet, kate_client):
        sheet.text = f"{HEADER}\nJuniper,,,,,,\n"

        response = kate_client.post(_pull_url(hub))

        assert response.status_code == 302
        assert response["Location"] == _page(hub)
        assert _messages(response) == [("success", "Pulled from the sheet: 1 added, 0 updated, 0 unchanged.")]
        assert hub.vendor_tables.filter(name="Juniper").exists()

    def it_warns_when_the_pull_has_problems(hub, linked, sheet, kate_client):
        sheet.text = f"{HEADER}\nJuniper,,huge,,,,\n"

        response = kate_client.post(_pull_url(hub))

        assert _messages(response) == [
            ("warning", "Pulled from the sheet: 1 added, 0 updated, 0 unchanged. 1 problem to check.")
        ]

    def it_says_why_when_the_sheet_cannot_be_read_and_writes_nothing(hub, linked, sheet, kate_client):
        sheet.error = NOT_SHARED

        response = kate_client.post(_pull_url(hub))

        assert response.status_code == 302
        assert _messages(response) == [("error", f"The sheet could not be read. {NOT_SHARED}")]
        assert hub.vendor_tables.count() == 0

    def it_says_there_is_no_link_to_pull(hub, sheet, kate_client):
        response = kate_client.post(_pull_url(hub))

        assert _messages(response) == [("error", "There is no sheet link to pull. An admin adds it on this page.")]
        assert sheet.links == []

    def it_is_the_teams_only(hub, linked, sheet, make_member):
        stranger = signed_in(make_member())

        assert stranger.post(_pull_url(hub)).status_code == 403
        assert sheet.links == []

    def it_is_a_post(hub, linked, kate_client):
        assert kate_client.get(_pull_url(hub)).status_code == 405


def describe_saving_the_sheet_link():
    def it_saves_a_google_sheets_link_for_an_admin(hub, admin_client):
        response = admin_client.post(_sheet_url(hub), {"sheet_url": f"  {SHEET_URL}  "})

        assert response.status_code == 302
        assert _messages(response) == [("success", "Sheet link saved.")]
        assert VendorFloor.objects.get(hub=hub).sheet_url == SHEET_URL

    def it_refuses_a_link_whose_host_a_browser_reads_as_another_site(hub, admin_client):
        url = f"https://evil.example\\@docs.google.com/spreadsheets/d/{SHEET_ID}/edit"

        response = admin_client.post(_sheet_url(hub), {"sheet_url": url})

        assert response.status_code == 200
        assert "not a Google Sheets link" in response.content.decode()
        assert not VendorFloor.objects.filter(hub=hub).exists()

    def it_links_the_page_to_the_stored_address_only(hub, admin_client, kate_client):
        admin_client.post(
            _sheet_url(hub), {"sheet_url": f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?usp=sharing"}
        )

        body = kate_client.get(_page(hub)).content.decode()

        assert f'href="https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0"' in body
        assert "usp=sharing" not in body

    def it_refuses_a_link_that_is_not_a_google_sheet_and_saves_nothing(hub, admin_client):
        response = admin_client.post(_sheet_url(hub), {"sheet_url": "https://example.com/vendors.csv"})

        assert response.status_code == 200
        body = response.content.decode()
        assert "not a Google Sheets link" in body
        assert NOT_A_SHEET_LINK.split(".")[0] in body
        assert '<details class="pl-disclosure pl-hq-vt__disclosure" open data-hq-vt-sheet>' in body
        assert not VendorFloor.objects.filter(hub=hub).exists()

    def it_clears_the_link_when_saved_blank(hub, linked, admin_client):
        response = admin_client.post(_sheet_url(hub), {"sheet_url": ""})

        assert _messages(response) == [("success", "Sheet link removed.")]
        assert VendorFloor.objects.get(hub=hub).sheet_url == ""

    def it_reads_the_actual_role_so_a_previewing_admin_still_saves_it(hub, admin_client):
        session = admin_client.session
        session["view_as_role"] = ROLE_MEMBER
        session.save()

        assert admin_client.post(_sheet_url(hub), {"sheet_url": SHEET_URL}).status_code == 302
        assert VendorFloor.objects.get(hub=hub).sheet_url == SHEET_URL

    def it_is_admins_only_never_the_team(hub, kate_client):
        assert kate_client.post(_sheet_url(hub), {"sheet_url": SHEET_URL}).status_code == 403
        assert not VendorFloor.objects.filter(hub=hub).exists()

    def it_is_a_post(hub, admin_client):
        assert admin_client.get(_sheet_url(hub)).status_code == 405


def describe_the_daily_job():
    def it_is_registered_daily_in_the_morning():
        job = JOBS_BY_KEY["pull_vendor_sheets"]

        assert (job.command, job.cadence) == ("pull_vendor_sheets", Cadence.DAILY)

    def it_pulls_every_hub_with_a_link(hub, linked, sheet, capsys):
        sheet.text = f"{HEADER}\nJuniper,,,,,,\n"

        call_command("pull_vendor_sheets")

        assert hub.vendor_tables.filter(name="Juniper").exists()
        assert "Vendor sheets pulled." in capsys.readouterr().out

    def it_fails_the_run_and_alerts_the_webmasters_when_a_sheet_cannot_be_read(hub, linked, sheet):
        sheet.error = NOT_SHARED

        with (
            patch("core.scheduled_jobs.alert_webmasters") as alert,
            pytest.raises(CommandError, match="Holiday Bizarre HQ: The sheet is not shared"),
            record_run("pull_vendor_sheets", trigger=Trigger.MANUAL),
        ):
            call_command("pull_vendor_sheets")

        run = ScheduledTaskRun.objects.get(task_key="pull_vendor_sheets")
        assert run.status == ScheduledTaskRun.Status.FAILED
        assert alert.call_count == 1
        assert services.vendor_sheet_state(hub).error == NOT_SHARED
