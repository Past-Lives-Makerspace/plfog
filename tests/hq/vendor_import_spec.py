"""Vendor Tables' Import CSV (#1012): the sheet's four extra columns, the one reader the CSV upload and the sheet pull
share (headers by name, loose values, problems by row), the upload's rule (match by name, blank cells change
nothing, add the rest, never delete, a repeated name reported), the refusals that write nothing, Kate's sheet as she
keeps it, an export that imports back unchanged, the gate, and the answer on the page."""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from hq import services
from hq.models import HubCard, HubGoal, VendorTable
from hq.vendor_import import (
    MAX_IMPORT_ROWS,
    ImportProblem,
    ImportRefused,
    ImportResult,
    apply_upload,
    header_columns,
    normalise,
    parse_rows,
    read_csv,
)
from hub.view_as import ROLE_MEMBER
from membership.models import Guild
from tests.hq.conftest import signed_in
from tests.hq.factories import HubCardFactory, HubGoalFactory, VendorFloorFactory, VendorTableFactory
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db

S = VendorTable.Status
HEADER = (
    "Name,Guild,Table size,Status,What they sell,Setup time,Table #,Email,Phone,Source,Requested on,"
    "Request source,Discord handle"
)

# A sheet in the shape Kate downloads: her header, then rows with trailing spaces, emoji handles and the rest. Every
# person, handle, email and phone is invented (this repo is public).
KATE_SHEET = f"""{HEADER}
"Jewelry Guild","Jewelry Guild","6 ft","Requested","","","","","","From Kate's sign-up doc. Confirm who in the guild is running this table.","2026-09-22","Kate's sign-up doc",""
"Wren","Ceramics","Not sure","Requested","","","","","","Asked Lee on Oct 1","2026-10-01","Asked Lee",""
"Rowan","Woodshop","Not sure","Requested","","","","","","Asked Lee on Oct 1","2026-10-01","Asked Lee",""
"Spec Silversmith","Jewelry Guild","Bringing their own","Requested","Lapidary, jewelry, belts","Thu Dec 3, 3 to 7 PM","","spec@example.com","503-555-0101","Added by Kate","2026-10-17","Added by Kate",""
"Spec Pair","","Half table (shared)","Requested","Pottery","","","","","Asked in Discord","2026-10-20","Discord","Z | SpecPotter"
"Juniper","Wood","8 ft","Confirmed","Cutting boards","Fri Dec 4, 3 to 5 PM","7","juniper@example.com","","Asked Lee","10/21/2026","Asked Lee","juniper.makes"
"Marisol","Metal","4 ft","Waitlist","Rings","","","","","Asked Lee","2026-10-21","Asked Lee",""
"Spec Weaver ","Textiles","Not sure","Requested","Textiles","","","Spec.Weaver@Example.com","","Asked Lee","2026-10-22","Asked Lee","N/A"
"Ⓩinnia 🎃 [PRNT]","Printmaking","6 ft","Cancelled","Prints","","","","","Asked in Discord","2026-10-23","Discord","Ⓩinnia 🎃 [PRNT]"
"Moss and Fern","Leather","6 ft","Requested","Wallets","","","","","Asked Lee","2026-10-24","Asked Lee",""
"""


def _page(hub: Any) -> str:
    return reverse("hq:vendors", args=[hub.slug])


def _import(hub: Any) -> str:
    return reverse("hq:vendor_import", args=[hub.slug])


def _csv(hub: Any) -> str:
    return reverse("hq:vendors_csv", args=[hub.slug])


def _upload(text: str | bytes, name: str = "tables.csv") -> SimpleUploadedFile:
    data = text.encode() if isinstance(text, str) else text
    return SimpleUploadedFile(name, data, content_type="text/csv")


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def _messages(response: Any) -> list[tuple[str, str]]:
    return [(message.level_tag, str(message)) for message in get_messages(response.wsgi_request)]


def _snapshot(hub: Any) -> list[dict[str, Any]]:
    return list(hub.vendor_tables.order_by("pk").values())


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
def kate_client(kate) -> Client:
    return signed_in(kate)


@pytest.fixture
def guilds() -> dict[str, Guild]:
    """The guilds Kate's sheet names in short: Ceramics, Woodshop and Wood, Metal, Textiles, Printmaking."""
    names = (
        "Jewelry Guild",
        "Ceramics Guild",
        "Woodworking Guild",
        "Metalsmithing Guild",
        "Textiles Guild",
        "Printmaking Guild",
    )
    return {name: GuildFactory(name=name) for name in names}


def _parse(text: str, guilds: Any = ()) -> Any:
    return parse_rows(_rows(text), guilds=list(guilds))


def _one(text: str, guilds: Any = ()) -> Any:
    sheet = _parse(text, guilds)
    assert len(sheet.rows) == 1
    return sheet.rows[0]


def describe_the_model():
    def it_keeps_the_joiners_an_emoji_name_is_made_of():
        artist = "\U0001f469\u200d\U0001f3a8 Studio"
        scotland = "\U0001f3f4\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f Flags"

        assert _one(f"Name\n{artist}\n").values["name"] == artist
        assert _one(f"Name\n{scotland}\n").values["name"] == scotland

    def it_treats_a_name_of_joiners_alone_as_no_name():
        sheet = _parse('Name,Status\n"\u200d\u200b",Requested\n')

        assert [str(problem) for problem in sheet.rows[0].problems] == ["Row 2: No Name, so the row was skipped."]

    def it_carries_the_sheets_four_columns_blank_by_default(hub):
        table = VendorTableFactory(hub=hub)

        assert (table.note, table.requested_on, table.request_source, table.discord_handle) == ("", None, "", "")


def describe_reading_the_header():
    def it_reads_columns_by_name_with_case_and_spacing_ignored_in_any_order():
        columns = header_columns(["  table   SIZE ", "Bogus", "name", "DISCORD handle", "Name"])

        assert columns == {0: "size", 2: "name", 3: "discord_handle"}

    def it_reads_the_old_export_headers_and_notes_as_source():
        columns = header_columns(["Artist or group", "Setup day", "Notes", "Needs", "Added by", "Added"])

        assert columns == {0: "name", 1: "setup", 2: "note", 3: "needs"}

    def it_refuses_a_file_with_no_name_column():
        with pytest.raises(ImportRefused, match="no Name column"):
            _parse("Guild,Status\nCeramics,Requested\n")

    def it_refuses_an_empty_file():
        with pytest.raises(ImportRefused, match="empty"):
            parse_rows([], guilds=[])

    def it_skips_a_row_with_nothing_in_it_and_reports_one_with_no_name():
        sheet = _parse("Name,Status,Bogus\n,,\n  , ,ignored\n,Requested,\nWren,,\n")

        assert sheet.columns == ("name", "status")
        assert [(row.line, row.key) for row in sheet.rows] == [(4, ""), (5, "wren")]
        assert [str(problem) for problem in sheet.rows[0].problems] == ["Row 4: No Name, so the row was skipped."]

    def it_reads_a_short_row_as_blank_cells():
        row = _one("Name,Guild,Status\nWren\n")

        assert row.cells == {"name": "Wren", "guild": "", "status": ""}
        assert row.values == {"name": "Wren"}


def describe_reading_a_cell():
    def it_keys_the_name_trimmed_collapsed_and_casefolded_and_stores_it_cleaned():
        row = _one('Name\n"  Spec \u00a0 Weaver\u200b "\n')

        assert row.key == "spec weaver" == normalise(" SPEC Weaver")
        assert row.values == {"name": "Spec Weaver"}

    def it_matches_a_fullwidth_name_and_drops_invisible_characters():
        assert normalise("\uff2b\uff41\uff54\uff45 Studio") == normalise("Kate Studio") == "kate studio"
        assert normalise("Kate\u200d\u200e Studio") == "kate studio"

    def it_reads_a_name_of_only_invisible_characters_as_no_name():
        sheet = _parse('Name,Status\n"\u200b\u200b",Requested\n')

        assert sheet.rows[0].key == ""
        assert [str(problem) for problem in sheet.rows[0].problems] == ["Row 2: No Name, so the row was skipped."]

    def it_cuts_the_text_a_problem_repeats_at_sixty_characters():
        long = "x" * 80

        row = _one(f"Name,Status,Guild,Setup time,Requested on,Email\nWren,{long},{long},{long},{long},{long}\n")

        cut = "x" * 59 + "\u2026"
        assert [str(problem) for problem in row.problems] == [
            f'Row 2: Status "{cut}" is not one of Requested, Confirmed, Waitlist, Cancelled.',
            f'Row 2: Guild "{cut}" is not one of the active guilds.',
            f'Row 2: Setup time "{cut}" is not one of Thu Dec 3, 3 to 7 PM, Fri Dec 4, 3 to 5 PM.',
            f'Row 2: Requested on "{cut}" is not a date like 2026-10-21 or 10/21/2026.',
            f'Row 2: Email "{cut}" is not an email address.',
        ]

    def it_leaves_blank_cells_out_of_the_values():
        row = _one(f'{HEADER}\n"Wren","","","","","","","","","","","",""\n')

        assert row.values == {"name": "Wren"}
        assert row.problems == ()

    def it_takes_back_the_apostrophe_the_export_puts_before_a_formula():
        row = _one("Name,What they sell,Phone,Source\n'=Spec,'hello,'+1 503 555 0100,'-dash\n")

        assert row.values == {"name": "=Spec", "sells": "'hello", "phone": "+1 503 555 0100", "note": "-dash"}

    def it_reports_text_longer_than_its_field_and_leaves_the_field_out():
        row = _one(f"Name,Request source\nWren,{'x' * 61}\n")

        assert row.values == {"name": "Wren"}
        assert [str(problem) for problem in row.problems] == ["Row 2: Request source is longer than 60 characters."]
        assert row.problems[0].column == "Request source"

    def it_keeps_n_a_and_emoji_in_a_discord_handle_as_text():
        sheet = _parse('Name,Discord handle\nIvy,N/A\n"Ⓩinnia 🎃 [PRNT]","Ⓩinnia 🎃 [PRNT]"\n')

        assert [row.values["discord_handle"] for row in sheet.rows] == ["N/A", "Ⓩinnia 🎃 [PRNT]"]
        assert all(row.problems == () for row in sheet.rows)

    @pytest.mark.parametrize(
        ("text", "value"),
        [
            ("6 ft", "six_ft"),
            ("6FT", "six_ft"),
            ("eight_ft", "eight_ft"),
            ("Half table (share)", "half"),
            ("Half table (shared)", "half"),
            ("half table", "half"),
            ("Bringing their own", "own"),
            ("Bringing own", "own"),
            ("not sure", "unsure"),
        ],
    )
    def it_reads_a_size_by_label_value_or_kates_words(text, value):
        assert _one(f"Name,Table size\nWren,{text}\n").values["size"] == value

    def it_reports_a_size_it_cannot_read():
        row = _one("Name,Table size\nWren,big\n")

        assert "size" not in row.values
        assert [str(problem) for problem in row.problems] == [
            'Row 2: Table size "big" is not one of 6 ft, 8 ft, 4 ft, Half table (share), Bringing their own, Not sure.'
        ]

    @pytest.mark.parametrize(
        ("text", "value"),
        [("Confirmed", "confirmed"), ("waitlist", "waitlist"), ("Wait list", "waitlist"), ("Canceled", "cancelled")],
    )
    def it_reads_a_status_by_label_or_value(text, value):
        assert _one(f"Name,Status\nWren,{text}\n").values["status"] == value

    def it_reports_a_status_it_cannot_read():
        row = _one("Name,Status\nWren,Maybe\n")

        assert [str(problem) for problem in row.problems] == [
            'Row 2: Status "Maybe" is not one of Requested, Confirmed, Waitlist, Cancelled.'
        ]

    @pytest.mark.parametrize(
        ("text", "value"),
        [
            ("Thu Dec 3, 3 to 7 PM", "thu"),
            ("thu", "thu"),
            ("Thursday", "thu"),
            ("Fri Dec 4, 3 to 5 PM", "fri"),
            ("Friday afternoon", "fri"),
            ("Thurs", "thu"),
            ("thur, Dec 3", "thu"),
            ("FRI", "fri"),
            ("Not chosen", ""),
        ],
    )
    def it_reads_a_setup_window_by_label_or_day(text, value):
        assert _one(f'Name,Setup time\nWren,"{text}"\n').values["setup"] == value

    @pytest.mark.parametrize("text", ["Saturday", "Friendly", "thumbs up", "th"])
    def it_reports_a_setup_window_it_cannot_read(text):
        row = _one(f"Name,Setup day\nWren,{text}\n")

        assert "setup" not in row.values
        assert [str(problem) for problem in row.problems] == [
            f'Row 2: Setup time "{text}" is not one of Thu Dec 3, 3 to 7 PM, Fri Dec 4, 3 to 5 PM.'
        ]

    @pytest.mark.parametrize(
        ("text", "value"),
        [("2026-10-21", date(2026, 10, 21)), ("10/21/2026", date(2026, 10, 21)), ("1/2/2026", date(2026, 1, 2))],
    )
    def it_reads_a_date_as_iso_or_month_day_year(text, value):
        assert _one(f"Name,Requested on\nWren,{text}\n").values["requested_on"] == value

    @pytest.mark.parametrize(
        "text",
        [
            "Oct 21",
            "2026-13-01",
            "13/45/2026",
            "21.10.2026",
            "2026-W43-1",
            "20261021",
            "2026-10-21T10:00",
            "0001-01-01",
            "10/21/2101",
            "\u0661\u0660/\u0662\u0661/\u0662\u0660\u0662\u0666",
        ],
    )
    def it_reports_a_date_it_cannot_read(text):
        row = _one(f"Name,Requested on\nWren,{text}\n")

        assert "requested_on" not in row.values
        assert [str(problem) for problem in row.problems] == [
            f'Row 2: Requested on "{text}" is not a date like 2026-10-21 or 10/21/2026.'
        ]

    def it_lowercases_an_email_and_reports_a_bad_or_long_one():
        sheet = _parse(f"Name,Email\nIvy,Spec.Weaver@Example.com\nBo,not an email\nCy,{'a' * 250}@example.com\n")

        assert sheet.rows[0].values["email"] == "spec.weaver@example.com"
        assert [str(problem) for row in sheet.rows[1:] for problem in row.problems] == [
            'Row 3: Email "not an email" is not an email address.',
            "Row 4: Email is longer than 254 characters.",
        ]


def describe_reading_a_guild():
    @pytest.mark.parametrize(
        ("text", "name"),
        [
            ("Jewelry Guild", "Jewelry Guild"),
            ("jewelry", "Jewelry Guild"),
            ("Ceramics", "Ceramics Guild"),
            ("Ceramic", "Ceramics Guild"),
            ("Textiles", "Textiles Guild"),
            ("Wood", "Woodworking Guild"),
            ("Woodshop", "Woodworking Guild"),
            ("Metal", "Metalsmithing Guild"),
        ],
    )
    def it_reads_a_guild_by_name_with_or_without_guild_and_a_plural_then_a_unique_start(guilds, text, name):
        assert _one(f"Name,Guild\nWren,{text}\n", guilds.values()).values["guild"] == guilds[name]

    @pytest.mark.parametrize("text", ["C", "W", "Wo", "Me"])
    def it_reports_a_start_shorter_than_three_characters_instead_of_guessing(guilds, text):
        row = _one(f"Name,Guild\nWren,{text}\n", guilds.values())

        assert "guild" not in row.values
        assert [str(problem) for problem in row.problems] == [f'Row 2: Guild "{text}" is not one of the active guilds.']

    def it_reads_not_in_a_guild_as_none(guilds):
        assert _one("Name,Guild\nWren,Not in a guild\n", guilds.values()).values["guild"] is None

    def it_reports_a_guild_it_does_not_know_and_does_not_guess(guilds):
        row = _one("Name,Guild\nWren,Leather\n", guilds.values())

        assert "guild" not in row.values
        assert [str(problem) for problem in row.problems] == ['Row 2: Guild "Leather" is not one of the active guilds.']

    def it_reports_a_start_that_more_than_one_guild_shares(guilds):
        GuildFactory(name="Wood Turning Guild")
        offered = Guild.objects.all()

        row = _one("Name,Guild\nWren,Wood\n", offered)

        assert [str(problem) for problem in row.problems] == ['Row 2: Guild "Wood" matches more than one guild.']

    def it_reports_a_name_two_guilds_share_once_the_plural_is_dropped():
        offered = [GuildFactory(name="Ceramic Guild"), GuildFactory(name="Ceramics")]

        row = _one("Name,Guild\nWren,Ceramics\n", offered)

        assert [str(problem) for problem in row.problems] == ['Row 2: Guild "Ceramics" matches more than one guild.']


def describe_reading_a_file():
    def it_reads_utf8_with_or_without_a_byte_order_mark():
        assert read_csv("Name\nÉlise\n".encode()) == [["Name"], ["Élise"]]
        assert read_csv(b"\xef\xbb\xbfName\nWren\n") == [["Name"], ["Wren"]]

    @pytest.mark.parametrize(
        ("data", "reason"),
        [
            (b"Name\n\xff\xfeEllie\n", "not a CSV saved as UTF-8"),
            (b"Name\nEl\x00lie\n", "not a CSV."),
            (b'Name\n"Wren\n', "could not be read as a CSV"),
            (b"Name\n" + b"x" * (1024 * 1024), "over 1 MB"),
            (b"Name;Guild\nWren;Ceramics\n", "uses semicolons; save it as a comma separated CSV"),
            (b'"Name";"Guild"\n"Wren";"Ceramics"\n', "uses semicolons; save it as a comma separated CSV"),
        ],
    )
    def it_refuses_a_file_it_cannot_read(data, reason):
        with pytest.raises(ImportRefused, match=reason):
            read_csv(data)

    def it_takes_a_thousand_rows_and_refuses_more_not_counting_blank_lines():
        rows = "".join(f"Vendor {n}\n" for n in range(MAX_IMPORT_ROWS))

        assert len(read_csv(f"Name\n{rows},\n\n".encode())) == MAX_IMPORT_ROWS + 3
        with pytest.raises(ImportRefused, match="more than 1000 rows"):
            read_csv(f"Name\n{rows}One more\n".encode())


def describe_the_uploads_rule():
    @pytest.mark.postgres_only
    def it_locks_the_hub_before_reading_its_tables_so_two_imports_take_turns(hub, kate):
        with CaptureQueriesContext(connection) as queries:
            apply_upload(hub, _parse("Name\nWren\n"), member=kate)

        sql = [query["sql"] for query in queries.captured_queries]
        hub_lock = next(i for i, text in enumerate(sql) if "FOR UPDATE" in text)
        assert '"hq_eventhub"' in sql[hub_lock]
        assert hub_lock < next(i for i, text in enumerate(sql) if '"hq_vendortable"' in text)

    def it_adds_an_unmatched_row_made_by_the_importer_and_requested_unless_the_file_says(hub, kate):
        sheet = _parse("Name,Status\nWren,\nJuniper,Confirmed\n")

        result = apply_upload(hub, sheet, member=kate)

        assert (result.added, result.updated, result.unchanged, result.problems) == (2, 0, 0, [])
        assert list(hub.vendor_tables.order_by("name").values_list("name", "status", "created_by")) == [
            ("Juniper", S.CONFIRMED, kate.pk),
            ("Wren", S.REQUESTED, kate.pk),
        ]

    def it_updates_a_match_from_its_filled_cells_only_and_a_blank_never_clears(hub, kate, guilds):
        creator = MemberFactory()
        table = VendorTableFactory(
            hub=hub,
            name="Juniper Woods",
            guild=guilds["Woodworking Guild"],
            sells="Boards",
            email="juniper@example.com",
            phone="503-555-0102",
            needs="Power",
            note="Asked Lee",
            created_by=creator,
        )
        sheet = _parse(
            f'{HEADER}\n"  juniper   WOODS ","","8 ft","Confirmed","","","7","","","","10/21/2026","Discord",""\n',
            guilds.values(),
        )

        result = apply_upload(hub, sheet, member=kate)

        table.refresh_from_db()
        assert (result.added, result.updated, result.unchanged) == (0, 1, 0)
        assert (table.name, table.guild, table.sells, table.size, table.status, table.table_no) == (
            "Juniper Woods",
            guilds["Woodworking Guild"],
            "Boards",
            VendorTable.Size.EIGHT_FT,
            S.CONFIRMED,
            "7",
        )
        assert (table.email, table.phone, table.needs, table.note) == (
            "juniper@example.com",
            "503-555-0102",
            "Power",
            "Asked Lee",
        )
        assert (table.requested_on, table.request_source, table.created_by) == (date(2026, 10, 21), "Discord", creator)

    @pytest.mark.parametrize(
        "cell",
        ["juniper woods", "JUNIPER  WOODS", " Juniper\u00a0Woods ", "\uff2a\uff55\uff4e\uff49\uff50\uff45\uff52 Woods"],
    )
    def it_keeps_the_stored_name_when_the_cell_differs_only_in_case_or_spacing(hub, kate, cell):
        table = VendorTableFactory(hub=hub, name="Juniper Woods")
        stamp = table.updated_at

        result = apply_upload(hub, _parse(f"Name\n{cell}\n"), member=kate)

        table.refresh_from_db()
        assert (result.updated, result.unchanged) == (0, 1)
        assert (table.name, table.updated_at) == ("Juniper Woods", stamp)

    def it_stores_a_new_name_cleaned_of_invisible_characters_and_extra_spaces(hub, kate):
        apply_upload(hub, _parse('Name\n" Kate\u200b  \u00a0Studio "\n'), member=kate)

        assert hub.vendor_tables.get().name == "Kate Studio"

    def it_leaves_a_field_it_could_not_read_as_it_was(hub, kate, guilds):
        table = VendorTableFactory(hub=hub, name="Wren", guild=guilds["Ceramics Guild"], size=VendorTable.Size.FOUR_FT)

        result = apply_upload(hub, _parse("Name,Guild,Table size\nWren,Leather,huge\n", guilds.values()), member=kate)

        table.refresh_from_db()
        assert (table.guild, table.size) == (guilds["Ceramics Guild"], VendorTable.Size.FOUR_FT)
        assert (result.updated, result.unchanged) == (0, 1)
        assert [str(problem) for problem in result.problems] == [
            'Row 2: Guild "Leather" is not one of the active guilds.',
            'Row 2: Table size "huge" is not one of 6 ft, 8 ft, 4 ft, Half table (share), Bringing their own, '
            "Not sure.",
        ]

    def it_counts_a_match_that_changes_nothing_as_unchanged_and_saves_nothing(hub, kate):
        table = VendorTableFactory(hub=hub, name="Wren", sells="Mugs")
        stamp = table.updated_at

        result = apply_upload(hub, _parse("Name,What they sell\n Wren ,Mugs\n"), member=kate)

        table.refresh_from_db()
        assert (result.added, result.updated, result.unchanged) == (0, 0, 1)
        assert table.updated_at == stamp

    def it_uses_the_first_of_two_rows_with_one_name_and_reports_the_second(hub, kate):
        sheet = _parse("Name,What they sell\nWren,Mugs\nJuniper,Boards\n wren ,Vases\n")

        result = apply_upload(hub, sheet, member=kate)

        assert hub.vendor_tables.get(name="Wren").sells == "Mugs"
        assert (result.added, result.updated, result.unchanged) == (2, 0, 0)
        assert [str(problem) for problem in result.problems] == [
            'Row 4: "wren" is also on row 2, so this row was skipped.'
        ]

    def it_matches_the_earliest_of_two_tables_with_one_name(hub, kate):
        first = VendorTableFactory(hub=hub, name="Wren")
        second = VendorTableFactory(hub=hub, name="WREN")

        apply_upload(hub, _parse("Name,What they sell\nWren,Mugs\n"), member=kate)

        first.refresh_from_db()
        second.refresh_from_db()
        assert (first.sells, second.sells) == ("Mugs", "")

    def it_never_deletes_and_never_matches_another_hubs_table(hub, kate):
        kept = VendorTableFactory(hub=hub, name="Kept")
        elsewhere = VendorTableFactory(name="Wren", sells="Elsewhere")

        result = apply_upload(hub, _parse("Name,What they sell\nWren,Mugs\n"), member=kate)

        elsewhere.refresh_from_db()
        assert result.added == 1
        assert hub.vendor_tables.filter(pk=kept.pk).exists()
        assert elsewhere.sells == "Elsewhere"

    def it_orders_the_problems_by_row(hub, kate):
        sheet = _parse("Name,Status\n,Requested\nWren,Maybe\nWren,\n")

        result = apply_upload(hub, sheet, member=kate)

        assert [problem.line for problem in result.problems] == [2, 3, 4]


def describe_kates_sheet():
    def it_imports_every_row_and_every_column_into_an_empty_hub(hub, kate, kate_client, guilds):
        response = kate_client.post(_import(hub), {"file": _upload(KATE_SHEET)})

        assert response.status_code == 302
        assert response["Location"] == _page(hub)
        assert _messages(response) == [
            ("warning", "Import done: 10 added, 0 updated, 0 unchanged. 1 problem to check.")
        ]
        tables = {table.name: table for table in hub.vendor_tables.select_related("guild", "created_by")}
        assert len(tables) == 10
        rows = {
            name: (
                table.guild.name if table.guild else "",
                table.get_size_display(),
                table.status,
                table.sells,
                table.setup,
                table.table_no,
                table.email,
                table.phone,
                table.note,
                table.requested_on,
                table.request_source,
                table.discord_handle,
            )
            for name, table in tables.items()
        }
        doc = "From Kate's sign-up doc. Confirm who in the guild is running this table."
        assert rows == {
            "Jewelry Guild": (
                "Jewelry Guild", "6 ft", S.REQUESTED, "", "", "", "", "", doc, date(2026, 9, 22),
                "Kate's sign-up doc", "",
            ),
            "Wren": (
                "Ceramics Guild", "Not sure", S.REQUESTED, "", "", "", "", "", "Asked Lee on Oct 1",
                date(2026, 10, 1), "Asked Lee", "",
            ),
            "Rowan": (
                "Woodworking Guild", "Not sure", S.REQUESTED, "", "", "", "", "", "Asked Lee on Oct 1",
                date(2026, 10, 1), "Asked Lee", "",
            ),
            "Spec Silversmith": (
                "Jewelry Guild", "Bringing their own", S.REQUESTED, "Lapidary, jewelry, belts", "thu", "",
                "spec@example.com", "503-555-0101", "Added by Kate", date(2026, 10, 17), "Added by Kate", "",
            ),
            "Spec Pair": (
                "", "Half table (share)", S.REQUESTED, "Pottery", "", "", "", "", "Asked in Discord",
                date(2026, 10, 20), "Discord", "Z | SpecPotter",
            ),
            "Juniper": (
                "Woodworking Guild", "8 ft", S.CONFIRMED, "Cutting boards", "fri", "7", "juniper@example.com", "",
                "Asked Lee", date(2026, 10, 21), "Asked Lee", "juniper.makes",
            ),
            "Marisol": (
                "Metalsmithing Guild", "4 ft", S.WAITLIST, "Rings", "", "", "", "", "Asked Lee",
                date(2026, 10, 21), "Asked Lee", "",
            ),
            "Spec Weaver": (
                "Textiles Guild", "Not sure", S.REQUESTED, "Textiles", "", "", "spec.weaver@example.com", "",
                "Asked Lee", date(2026, 10, 22), "Asked Lee", "N/A",
            ),
            "Ⓩinnia 🎃 [PRNT]": (
                "Printmaking Guild", "6 ft", S.CANCELLED, "Prints", "", "", "", "", "Asked in Discord",
                date(2026, 10, 23), "Discord", "Ⓩinnia 🎃 [PRNT]",
            ),
            "Moss and Fern": (
                "", "6 ft", S.REQUESTED, "Wallets", "", "", "", "", "Asked Lee", date(2026, 10, 24), "Asked Lee", "",
            ),
        }  # fmt: skip
        assert {table.created_by for table in tables.values()} == {kate}

        body = kate_client.get(_page(hub)).content.decode()
        panel = body.split("data-hq-vt-import-answer>")[1].split("</div>")[0]
        assert "Import done: 10 added, 0 updated, 0 unchanged. 1 problem to check." in panel
        assert "<li>Row 11: Guild &quot;Leather&quot; is not one of the active guilds.</li>" in panel

    def it_adds_nothing_the_second_time_and_reports_every_row_unchanged(hub, kate_client, guilds):
        kate_client.post(_import(hub), {"file": _upload(KATE_SHEET)})
        kate_client.get(_page(hub))  # the first import's answer, read
        before = _snapshot(hub)

        response = kate_client.post(_import(hub), {"file": _upload(KATE_SHEET)})

        assert _messages(response) == [
            ("warning", "Import done: 0 added, 0 updated, 10 unchanged. 1 problem to check.")
        ]
        assert _snapshot(hub) == before

    def it_keeps_the_confirmed_count_and_the_member_tables_goal_in_step(hub, kate_client, guilds):
        HubGoalFactory(hub=hub, label="Member tables", kind=HubGoal.Kind.MEMBER_TABLES, target=10, sort_order=0)
        VendorFloorFactory(hub=hub, capacity=3)
        VendorTableFactory(hub=hub, name="Wren", status=S.REQUESTED)

        kate_client.post(_import(hub), {"file": _upload("Name,Status\nWren,Confirmed\nNew Studio,Confirmed\n")})

        band = services.vendor_band(hub)
        (goal,) = services.goal_rows(hub)
        assert (band.confirmed, band.requested, band.space_left) == (2, 0, 1)
        assert (goal.current, goal.percent) == (2, 20)
        body = kate_client.get(_page(hub)).content.decode()
        confirmed = body.split('data-hq-vt-tile="confirmed"')[1].split("</div>")[0]
        assert "data-hq-vt-figure>2</strong>" in confirmed


def describe_the_round_trip():
    def it_imports_an_export_back_with_no_change(hub, kate, kate_client, guilds):
        retired = GuildFactory(name="Retired Guild", is_active=False)
        VendorTableFactory(
            hub=hub,
            name="=Spec Formula",
            guild=guilds["Ceramics Guild"],
            sells="+ mugs, - vases",
            size=VendorTable.Size.HALF,
            status=S.CONFIRMED,
            table_no="12",
            setup=VendorTable.Setup.FRI,
            email="spec@example.com",
            phone="+1 503 555 0100",
            needs="@ the wall",
            note='Said "yes", then\nasked again',
            requested_on=date(2026, 10, 21),
            request_source="Discord",
            discord_handle="Ⓩinnia 🎃 [PRNT]",
            created_by=kate,
        )
        VendorTableFactory(hub=hub, name="Spec  Blank", size=VendorTable.Size.OWN, status=S.WAITLIST)
        VendorTableFactory(
            hub=hub, name="Spec Retired", guild=retired, status=S.CANCELLED, size=VendorTable.Size.UNSURE
        )
        before = _snapshot(hub)
        exported = b"".join(kate_client.get(_csv(hub)).streaming_content)

        response = kate_client.post(_import(hub), {"file": _upload(exported)})

        assert _messages(response) == [("success", "Import done: 0 added, 0 updated, 3 unchanged.")]
        assert _snapshot(hub) == before


def describe_refusing_a_file():
    @pytest.mark.parametrize(
        ("data", "reason"),
        [
            (b"%PDF-1.7\n\xe2\xe3\xcf\xd3 binary", "That file is not a CSV saved as UTF-8. Nothing was imported."),
            (b"Guild,Status\nCeramics,Requested\n", "That file has no Name column. Nothing was imported."),
            (b'Name\n"Wren\n', "That file could not be read as a CSV. Nothing was imported."),
            (
                b"Name\n" + b"Vendor\n" * (MAX_IMPORT_ROWS + 1),
                "That file has more than 1000 rows. Nothing was imported.",
            ),
            (b"Name\n" + b"x" * (1024 * 1024), "That file is over 1 MB. Nothing was imported."),
            (b"", "That file is empty. Nothing was imported."),
        ],
    )
    def it_refuses_the_file_whole_says_why_and_writes_nothing(hub, kate_client, data, reason):
        VendorTableFactory(hub=hub, name="Vendor", sells="Kept")

        response = kate_client.post(_import(hub), {"file": _upload(data)})

        assert response.status_code == 302
        assert _messages(response) == [("error", reason)]
        assert list(hub.vendor_tables.values_list("name", "sells")) == [("Vendor", "Kept")]
        body = kate_client.get(_page(hub)).content.decode()
        panel = body.split("pl-hq-vt__import-answer--refused")[1].split("</div>")[0]
        assert reason in panel

    def it_asks_for_a_file_when_none_was_chosen(hub, kate_client):
        response = kate_client.post(_import(hub), {})

        assert _messages(response) == [("error", "Choose a CSV file to import.")]
        assert not hub.vendor_tables.exists()


def describe_who_may_import():
    def it_is_the_teams_alone(hub, member_client):
        for client in (member_client, Client()):
            assert client.post(_import(hub), {"file": _upload("Name\nWren\n")}).status_code == 403
        assert not hub.vendor_tables.exists()

    def it_takes_posts_only(hub, kate_client):
        assert kate_client.get(_import(hub)).status_code == 405

    def it_reads_the_actual_role_so_a_previewing_admin_still_imports(hub, admin_client):
        session = admin_client.session
        session["view_as_role"] = ROLE_MEMBER
        session.save()

        assert admin_client.post(_import(hub), {"file": _upload("Name\nWren\n")}).status_code == 302
        assert hub.vendor_tables.get().name == "Wren"

    def it_is_a_404_on_a_hub_with_no_vendor_door(open_hub, admin_client):
        assert admin_client.post(_import(open_hub), {"file": _upload("Name\nWren\n")}).status_code == 404
        assert not VendorTable.objects.exists()


def describe_the_answer_on_the_page():
    def it_lists_the_first_ten_problems_then_how_many_more(hub, kate_client):
        rows = "".join(f"Vendor {n},Maybe\n" for n in range(13))

        response = kate_client.post(_import(hub), {"file": _upload(f"Name,Status\n{rows}")})

        assert _messages(response) == [
            ("warning", "Import done: 13 added, 0 updated, 0 unchanged. 13 problems to check.")
        ]
        body = kate_client.get(_page(hub)).content.decode()
        problems = body.split("data-hq-vt-import-problems>")[1].split("</ul>")[0]
        assert problems.count("<li>Row ") == 10
        assert "<li>Row 2: Status &quot;Maybe&quot;" in problems
        assert "<li>Row 11: " in problems and "Row 12: " not in problems
        assert "<li data-hq-vt-import-more>And 3 more.</li>" in problems

    def it_shows_the_answer_on_the_one_page_load_after_the_import(hub, kate_client):
        kate_client.post(_import(hub), {"file": _upload("Name\nWren\n")})

        first = kate_client.get(_page(hub)).content.decode()
        second = kate_client.get(_page(hub)).content.decode()

        assert "data-hq-vt-import-summary>Import done: 1 added, 0 updated, 0 unchanged.</p>" in first
        assert "data-hq-vt-import-problems" not in first
        assert "data-hq-vt-import-answer" not in second

    def it_keeps_each_hubs_answer_to_that_hub(hub, kate, kate_client):
        other = HubCardFactory(kind=HubCard.Kind.VENDOR_TABLES, audience=HubCard.Audience.ADMINS, team=[kate]).hub
        kate_client.post(_import(hub), {"file": _upload("Name\nWren\n")})

        assert "data-hq-vt-import-answer" not in kate_client.get(_page(other)).content.decode()
        assert "data-hq-vt-import-answer" in kate_client.get(_page(hub)).content.decode()

    def it_offers_import_csv_beside_download_csv_as_a_multipart_plain_post(hub, kate_client):
        body = kate_client.get(_page(hub)).content.decode()

        head = body.split('class="pl-hq-vt__list-head"')[1].split("</div>\n    </div>")[0]
        assert "data-hq-vt-import-open>Import CSV</button>" in head
        assert "data-hq-vt-csv>Download CSV</a>" in head
        assert "$dispatch('open-modal', 'hq-vt-import')" in head
        assert f'<form method="post" action="{_import(hub)}" enctype="multipart/form-data" hx-boost="false"' in body
        assert '<input type="file" name="file" id="hq-vt-import-file" accept=".csv,text/csv" required>' in body
        assert 'id="hq-vt-import-title">Import CSV</h2>' in body


def describe_the_summary_line():
    def it_counts_the_problems_in_the_right_number():
        one = ImportResult(added=1, problems=[ImportProblem(2, "Status", "x")])

        assert services.import_summary(ImportResult()) == "Import done: 0 added, 0 updated, 0 unchanged."
        assert services.import_summary(one) == "Import done: 1 added, 0 updated, 0 unchanged. 1 problem to check."
