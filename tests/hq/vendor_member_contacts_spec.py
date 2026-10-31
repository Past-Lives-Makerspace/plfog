"""Vendor Tables fill a member vendor's blank contact details from their member record (#1049): the match (Discord
handle first, then preferred or full legal name, case and spacing ignored, a shared key matching nobody, active
members only), only blank boxes filled, every write path (+ Add, the row's autosave, CSV import, the sheet pull with
its whole list pass, Discord add), "Kate's changes win" kept, and the fixed number of queries. Every person, handle
and address here is made up (this repo is public)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from allauth.account.models import EmailAddress
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from hq import services, vendor_sheet
from hq.models import HubCard, VendorTable
from hq.vendor_discord import ChannelRead
from hq.vendor_sheet import SheetLink
from membership.models import Member
from tests.hq.conftest import signed_in
from tests.hq.factories import HubCardFactory, VendorFloorFactory, VendorTableFactory
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
SHEET_URL = "https://docs.google.com/spreadsheets/d/1SpecMemberContactsSheetAbCdEfGhIjKlMnOp/edit#gid=0"
HEADER = "Name,Status,Email,Phone,Discord handle"


@pytest.fixture
def kate(make_member):
    return make_member(preferred_name="Spec Organiser")


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
def odile(make_member) -> Member:
    """An active member with a login, whose primary email is on her account, and a phone and a Discord handle."""
    member = make_member(
        full_legal_name="Odile Farrow",
        preferred_name="Odie Farrow",
        phone="503-555-0161",
        discord_handle="odie.makes",
        _pre_signup_email="odile@farrow.example",
    )
    EmailAddress.objects.filter(user=member.user).update(email="Odile.Primary@Farrow.example")
    return member


def _fill(*tables: VendorTable) -> list[VendorTable]:
    filled = services.fill_vendor_contacts(tables)
    for table in tables:
        table.refresh_from_db()
    return filled


def _contacts(table: VendorTable) -> tuple[str, str, str]:
    return (table.email, table.phone, table.discord_handle)


ODILE = ("odile.primary@farrow.example", "503-555-0161", "odie.makes")


def describe_the_match():
    def it_fills_every_blank_box_from_the_member_named_by_preferred_name(hub, odile):
        table = VendorTableFactory(hub=hub, name="Odie Farrow")

        assert _fill(table) == [table]

        assert _contacts(table) == ODILE

    def it_matches_the_full_legal_name_with_case_and_spacing_ignored_and_never_renames(hub, odile):
        table = VendorTableFactory(hub=hub, name="  odile   FARROW ")

        _fill(table)

        assert _contacts(table) == ODILE
        assert table.name == "  odile   FARROW "

    def it_matches_a_handle_with_a_leading_at_in_any_case(hub, make_member):
        make_member(discord_handle="moss", phone="503-555-0177", _pre_signup_email="moss@spec.example")
        table = VendorTableFactory(hub=hub, name="Mossy Glassworks", discord_handle=" @Moss ")

        _fill(table)

        assert _contacts(table) == ("moss@spec.example", "503-555-0177", " @Moss ")

    def it_takes_the_handle_match_over_a_name_match(hub, odile, make_member):
        make_member(discord_handle="@quill.ink", phone="503-555-0188")
        table = VendorTableFactory(hub=hub, name="Odie Farrow", discord_handle="quill.ink")

        _fill(table)

        assert table.phone == "503-555-0188"

    def it_falls_back_on_the_name_when_the_handle_matches_nobody(hub, odile):
        table = VendorTableFactory(hub=hub, name="Odie Farrow", discord_handle="not.a.member")

        _fill(table)

        assert _contacts(table) == ("odile.primary@farrow.example", "503-555-0161", "not.a.member")

    def it_fills_from_a_member_hidden_from_the_directory(hub, make_member):
        make_member(preferred_name="Juno Hale", phone="503-555-0110", show_in_directory=False)
        table = VendorTableFactory(hub=hub, name="Juno Hale")

        _fill(table)

        assert table.phone == "503-555-0110"

    def it_reads_an_unlinked_members_email(hub):
        MemberFactory(full_legal_name="Pell Arden", _pre_signup_email="Pell@Arden.example", user=None)
        table = VendorTableFactory(hub=hub, name="Pell Arden")

        _fill(table)

        assert table.email == "pell@arden.example"

    def it_falls_back_on_the_accounts_email_when_there_is_no_primary_row(hub, odile):
        EmailAddress.objects.filter(user=odile.user).delete()
        odile.user.email = "Odile.Account@Farrow.example"
        odile.user.save()
        table = VendorTableFactory(hub=hub, name="Odie Farrow")

        _fill(table)

        assert table.email == "odile.account@farrow.example"

    def it_counts_one_member_whose_two_names_are_the_same_once(hub, make_member):
        make_member(full_legal_name="Rook Vale", preferred_name="Rook Vale", phone="503-555-0122")
        table = VendorTableFactory(hub=hub, name="Rook Vale")

        _fill(table)

        assert table.phone == "503-555-0122"


def describe_only_blank_boxes():
    def it_keeps_a_typed_email_even_when_the_members_differs_and_fills_the_rest(hub, odile):
        table = VendorTableFactory(hub=hub, name="Odie Farrow", email="studio@odie.example")

        _fill(table)

        assert _contacts(table) == ("studio@odie.example", "503-555-0161", "odie.makes")

    def it_leaves_a_box_blank_when_the_member_has_nothing_for_it(hub, make_member):
        make_member(preferred_name="Tamsin Reed", phone="", discord_handle="")
        table = VendorTableFactory(hub=hub, name="Tamsin Reed", phone="")

        _fill(table)

        assert table.phone == "" and table.discord_handle == ""
        assert table.email != ""

    def it_changes_nothing_and_keeps_the_stamp_when_there_is_nothing_new(hub, make_member):
        make_member(preferred_name="Tamsin Reed", phone="", discord_handle="", _pre_signup_email="t@reed.example")
        table = VendorTableFactory(hub=hub, name="Tamsin Reed", email="mine@reed.example")
        stamp = table.updated_at

        assert _fill(table) == []

        assert table.updated_at == stamp and table.email == "mine@reed.example"

    def it_stamps_a_filled_row_so_an_older_page_is_stale_and_leaves_the_rest(hub, odile):
        filled = VendorTableFactory(hub=hub, name="Odie Farrow")
        other = VendorTableFactory(hub=hub, name="Nobody We Know")
        stamps = (filled.updated_at, other.updated_at)

        _fill(filled, other)

        assert filled.updated_at > stamps[0]
        assert other.updated_at == stamps[1]


def describe_who_matches_nobody():
    def it_fills_nothing_for_a_name_two_active_members_share(hub, make_member):
        make_member(preferred_name="Sam Ash", phone="503-555-0131")
        make_member(full_legal_name="Sam Ash", phone="503-555-0132")
        table = VendorTableFactory(hub=hub, name="Sam Ash")

        assert _fill(table) == []

        assert _contacts(table) == ("", "", "")

    def it_fills_nothing_for_a_handle_two_active_members_share_and_tries_the_name(hub, make_member, odile):
        make_member(discord_handle="twin", phone="503-555-0141")
        make_member(discord_handle="@Twin", phone="503-555-0142")
        shared = VendorTableFactory(hub=hub, name="Twin Studio", discord_handle="twin")
        named = VendorTableFactory(hub=hub, name="Odie Farrow", discord_handle="twin")

        _fill(shared, named)

        assert shared.phone == ""
        assert named.phone == "503-555-0161"

    @pytest.mark.parametrize("status", [status for status in Member.Status if status != Member.Status.ACTIVE])
    def it_fills_nothing_from_a_member_who_is_not_active(hub, make_member, status):
        make_member(status=status, preferred_name="Wynn Calder", phone="503-555-0151", discord_handle="wynn")
        by_name = VendorTableFactory(hub=hub, name="Wynn Calder")
        by_handle = VendorTableFactory(hub=hub, name="Wynn's Prints", discord_handle="wynn")

        assert _fill(by_name, by_handle) == []

        assert by_name.phone == "" and by_handle.phone == ""

    def it_lets_an_inactive_namesake_leave_the_active_member_matched(hub, make_member):
        make_member(status=Member.Status.FORMER, preferred_name="Ivy Moor", phone="503-555-0161")
        make_member(preferred_name="Ivy Moor", phone="503-555-0162")
        table = VendorTableFactory(hub=hub, name="Ivy Moor")

        _fill(table)

        assert table.phone == "503-555-0162"

    def it_never_matches_a_blank_name_or_handle(hub, make_member):
        make_member(preferred_name="", discord_handle="", phone="503-555-0171")
        table = VendorTableFactory(hub=hub, name="Unknown Studio")

        assert _fill(table) == []


def describe_the_queries():
    @pytest.mark.parametrize("rows", [1, 12])
    def it_fills_in_four_queries_however_many_rows(hub, make_member, rows, django_assert_num_queries):
        tables = []
        for n in range(rows):
            make_member(preferred_name=f"Spec Maker {n}", phone=f"503-555-{1000 + n}")
            tables.append(VendorTableFactory(hub=hub, name=f"Spec Maker {n}"))

        # The active members' keys, the matched members, their primary email rows, the one update.
        with django_assert_num_queries(4):
            filled = services.fill_vendor_contacts(tables)

        assert len(filled) == rows
        assert hub.vendor_tables.filter(phone="").count() == 0

    def it_reads_nothing_when_no_row_has_a_blank_box(hub, django_assert_num_queries):
        table = VendorTableFactory(hub=hub, email="a@spec.example", phone="1", discord_handle="a")

        with django_assert_num_queries(0):
            assert services.fill_vendor_contacts([table]) == []

    def it_stops_after_the_keys_when_no_row_matches(hub, django_assert_num_queries):
        table = VendorTableFactory(hub=hub, name="Nobody We Know")

        with django_assert_num_queries(1):
            assert services.fill_vendor_contacts([table]) == []


def describe_the_page():
    def it_adds_a_request_named_for_a_member_with_their_email_and_phone(hub, odile, kate_client):
        kate_client.post(
            reverse("hq:vendor_add", args=[hub.slug]),
            {
                "name": "Odie Farrow",
                "guild": "",
                "size": VendorTable.Size.SIX_FT,
                "email": "",
                "phone": "",
                "setup": VendorTable.Setup.THU,
                "needs": "",
            },
            **HTMX,
        )

        assert _contacts(hub.vendor_tables.get()) == ODILE

    def it_fills_on_a_row_autosave_and_sends_back_the_filled_boxes_and_the_new_stamp(hub, odile, kate_client):
        table = VendorTableFactory(hub=hub, name="Spec Placeholder", email="typed@odie.example")
        prefix = f"vendor-{table.pk}"

        response = kate_client.post(
            reverse("hq:vendor_edit", args=[hub.slug, table.pk]),
            _row_post(table, name="Odie Farrow", email="typed@odie.example"),
            **HTMX,
        )

        table.refresh_from_db()
        body = response.content.decode()
        assert _contacts(table) == ("typed@odie.example", "503-555-0161", "odie.makes")
        assert f'name="{prefix}-updated_at" value="{table.updated_at.isoformat()}"' in body
        # The two filled boxes come back alone, out of band; the typed email and every other box do not.
        assert f'name="{prefix}-phone" value="503-555-0161"' in body
        assert f'name="{prefix}-discord_handle" value="odie.makes"' in body
        assert body.count('hx-swap-oob="true"') == 4
        assert f'name="{prefix}-email"' not in body and f'name="{prefix}-name"' not in body
        assert "<tr" not in body

        # The next autosave carries the new stamp and is taken, not refused as stale.
        again = kate_client.post(
            reverse("hq:vendor_edit", args=[hub.slug, table.pk]),
            _row_post(table, sells="Rings"),
            **HTMX,
        )

        table.refresh_from_db()
        assert table.sells == "Rings"
        assert "data-hq-vt-row-error" not in again.content.decode()

    def it_sends_no_box_back_when_the_save_fills_nothing(hub, odile, kate_client):
        table = VendorTableFactory(hub=hub, name="Odie Farrow", email="a@spec.example", phone="1", discord_handle="b")

        response = kate_client.post(
            reverse("hq:vendor_edit", args=[hub.slug, table.pk]), _row_post(table, sells="Rings"), **HTMX
        )

        assert '<input type="tel"' not in response.content.decode()
        assert response.content.decode().count('hx-swap-oob="true"') == 2

    def it_fills_on_a_plain_post_too(hub, odile, kate_client):
        table = VendorTableFactory(hub=hub, name="Spec Placeholder")

        kate_client.post(reverse("hq:vendor_edit", args=[hub.slug, table.pk]), _row_post(table, name="Odie Farrow"))

        table.refresh_from_db()
        assert _contacts(table) == ODILE


def _row_post(table: VendorTable, **fields: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": table.name,
        "guild": "",
        "sells": table.sells,
        "size": table.size,
        "email": table.email,
        "phone": table.phone,
        "setup": table.setup,
        "needs": table.needs,
        "status": table.status,
        "table_no": table.table_no,
        "note": table.note,
        "requested_on": "",
        "request_source": table.request_source,
        "discord_handle": table.discord_handle,
        "updated_at": table.updated_at.isoformat(),
    }
    values.update(fields)
    data = {f"vendor-{table.pk}-{key}": value for key, value in values.items()}
    data["filter"] = "all"
    return data


def describe_the_csv_import():
    def it_fills_the_rows_it_adds_and_updates(hub, odile, make_member):
        make_member(preferred_name="Lark Sutter", phone="503-555-0191")
        updated = VendorTableFactory(hub=hub, name="Lark Sutter", sells="Old")

        services.import_vendor_tables(
            hub, [["Name", "What they sell"], ["Odie Farrow", "Rings"], ["Lark Sutter", "Bowls"]], member=None
        )

        assert _contacts(hub.vendor_tables.get(name="Odie Farrow")) == ODILE
        updated.refresh_from_db()
        assert (updated.sells, updated.phone) == ("Bowls", "503-555-0191")

    def it_keeps_an_imported_email_over_the_members(hub, odile):
        services.import_vendor_tables(hub, [["Name", "Email"], ["Odie Farrow", "shop@odie.example"]], member=None)

        assert _contacts(hub.vendor_tables.get()) == ("shop@odie.example", "503-555-0161", "odie.makes")


class FakeSheet:
    """Replaces ``hq.vendor_sheet.fetch_sheet_csv``, so no spec reaches the network."""

    def __init__(self) -> None:
        self.text = f"{HEADER}\n"

    def __call__(self, link: SheetLink) -> bytes:
        return self.text.encode()


@pytest.fixture
def sheet(hub) -> Iterator[FakeSheet]:
    VendorFloorFactory(hub=hub, sheet_url=SHEET_URL)
    fake = FakeSheet()
    with patch.object(vendor_sheet, "fetch_sheet_csv", fake):
        yield fake


def _pull(hub: Any, sheet: FakeSheet, *rows: str) -> None:
    sheet.text = "\n".join((HEADER, *rows)) + "\n"
    services.pull_vendor_sheet(hub)


def describe_the_sheet_pull():
    def it_fills_a_row_the_sheet_adds(hub, odile, sheet):
        _pull(hub, sheet, "Odie Farrow,Requested,,,")

        assert _contacts(hub.vendor_tables.get()) == ODILE

    def it_fills_every_row_on_the_list_including_ones_the_sheet_leaves_alone(hub, odile, make_member, sheet):
        make_member(preferred_name="Lark Sutter", phone="503-555-0191")
        typed = VendorTableFactory(hub=hub, name="Lark Sutter")
        _pull(hub, sheet, "Odie Farrow,Requested,,,")

        typed.refresh_from_db()
        assert typed.phone == "503-555-0191"
        assert typed.sheet_cells == {}

    def it_fills_the_whole_list_on_pull_from_sheet_now(hub, odile, sheet, kate_client):
        table = VendorTableFactory(hub=hub, name="Odie Farrow")

        kate_client.post(reverse("hq:vendor_pull", args=[hub.slug]), {"filter": "all"}, **HTMX)

        table.refresh_from_db()
        assert _contacts(table) == ODILE

    def it_fills_on_the_daily_run(hub, odile, sheet):
        table = VendorTableFactory(hub=hub, name="Odie Farrow")

        assert services.pull_every_vendor_sheet() == []

        table.refresh_from_db()
        assert _contacts(table) == ODILE

    def it_keeps_a_filled_value_while_kates_cell_stays_blank_and_takes_her_later_change(hub, odile, sheet):
        _pull(hub, sheet, "Odie Farrow,Requested,,,")
        _pull(hub, sheet, "Odie Farrow,Requested,,,")
        table = hub.vendor_tables.get()
        assert table.email == "odile.primary@farrow.example"

        _pull(hub, sheet, "Odie Farrow,Requested,kate.typed@odie.example,,")

        table.refresh_from_db()
        assert table.email == "kate.typed@odie.example"
        assert table.phone == "503-555-0161"


def describe_discord_add():
    def it_fills_a_poster_whose_name_is_a_members_handle(hub, odile, kate):
        author = {"id": "8101", "username": "odie.makes", "global_name": None}
        message = {
            "id": "10",
            "type": 0,
            "author": author,
            "content": "Table please",
            "timestamp": (timezone.now() - timedelta(hours=1)).isoformat(),
            "mentions": [],
        }
        read = ChannelRead(channel_id="1234567890123456789", name="holiday-bizarre", messages=(message,), nicks={})

        table = services.add_discord_poster(hub, read, author_id="8101", message_ids=["10"], member=kate)

        table.refresh_from_db()
        assert _contacts(table) == ODILE


def describe_the_query_scan_of_a_pull():
    def it_fills_a_pull_of_many_rows_without_a_query_per_row(hub, make_member, sheet):
        rows = []
        for n in range(8):
            make_member(preferred_name=f"Spec Potter {n}", phone=f"503-555-{2000 + n}")
            rows.append(f"Spec Potter {n},Requested,,,")
        _pull(hub, sheet, *rows[:1])

        with CaptureQueriesContext(connection) as few:
            _pull(hub, sheet, *rows[:2])
        with CaptureQueriesContext(connection) as many:
            _pull(hub, sheet, *rows)

        # Each added row costs its insert; the fill adds nothing per row.
        assert len(many) - len(few) == 5
        assert hub.vendor_tables.filter(phone="").count() == 0
