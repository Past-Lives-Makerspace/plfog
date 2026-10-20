"""The PL Ledger's budget (Track D, part 6a): the door and its organisers-only gate, the money (the band in a fixed
number of queries, refunds against the approval, Lee's line status rules, the bar's cost on Drinks lines only), the
board summary ported from Lee's ``summaryText``, the approval form, the budget lines with their one-line Save and the
PROTECT on delete, logging a sponsor or expense with its receipt, the ledger with its filters and Needs receipt, the
CSV, the receipts report, and the seed's adoption of the door."""

from __future__ import annotations

import csv
import io
import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.db.models import RestrictedError
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from hq import services
from hq.exports import LEDGER_CSV_HEADERS
from hq.forms import LEDGER_MAX_DOLLARS, RECEIPT_NOT_TAKEN, RECEIPT_TYPE_ERROR, checked_receipt
from hq.holiday_bizarre import LEDGER_INTRO, LEDGER_RECEIPT_HINT, LEDGER_TITLE
from hq.models import BUILT_DOORS, BudgetLine, HubCard, LedgerEntry, LedgerSettings, MarketingKind, receipt_upload_to
from hub.view_as import ROLE_GUEST, ROLE_MEMBER
from tests.hq.conftest import photo_upload, signed_in
from tests.hq.factories import (
    BudgetLineFactory,
    EventHubFactory,
    HubCardFactory,
    HubGoalFactory,
    LedgerEntryFactory,
    LedgerSettingsFactory,
    MarketingActionFactory,
    SponsorFactory,
    WineBottleFactory,
)

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
K = LedgerEntry.Kind
C = BudgetLine.Category


def _page(hub: Any, show: str = "") -> str:
    url = reverse("hq:ledger", args=[hub.slug])
    return f"{url}?filter={show}" if show else url


def _url(name: str, hub: Any, *args: Any) -> str:
    return reverse(f"hq:{name}", args=[hub.slug, *args])


def _toast(response: Any) -> str:
    return json.loads(response["HX-Trigger"])["showToast"]["message"]


def _pdf(name: str = "receipt.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4\n% spec receipt\n", content_type="application/pdf")


def _entry_post(line: BudgetLine, **fields: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "kind": K.SPONSORED,
        "line": line.pk,
        "source": "Cascade Brewing",
        "amount": "250",
        "date": "2026-11-02",
        "note": "1 half-barrel IPA, in-kind",
        "filter": "all",
    }
    data.update(fields)
    show = data.pop("filter")
    posted = {f"entry-{key}": value for key, value in data.items()}
    posted["filter"] = show
    return posted


def _approval_post(found: LedgerSettings, **fields: Any) -> dict[str, Any]:
    """The approval form as it renders for ``found`` (the unsaved defaults before a first save), with ``fields``
    changed: the figures and the approval it was rendered with."""
    data: dict[str, Any] = {
        "approved": f"{found.approved_cents / 100:.2f}",
        "approved_note": found.approved_note,
        "seen_approved_cents": found.approved_cents,
        "seen_approved_note": found.approved_note,
    }
    data.update(fields)
    return data


def _receipt_image(fmt: str) -> SimpleUploadedFile:
    """A small image in ``fmt`` that Pillow opens, named and declared as a JPEG whatever it is."""
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (9, 46, 76)).save(buffer, format=fmt)
    return SimpleUploadedFile("receipt.jpg", buffer.getvalue(), content_type="image/jpeg")


# An EPS file Pillow opens and verifies from its header alone, without Ghostscript.
_EPS = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 4 4\n%%EndComments\nshowpage\n%%EOF\n"


def _line_post(line: BudgetLine, **fields: Any) -> dict[str, Any]:
    """The line's edit form as it renders, with ``fields`` changed, under the line's prefix."""
    values: dict[str, Any] = {
        "name": line.name,
        "category": line.category,
        "budget": f"{line.budget_cents / 100:.2f}",
        "updated_at": line.updated_at.isoformat(),
    }
    show = fields.pop("filter", "all")
    values.update(fields)
    data = {f"line-{line.pk}-{key}": value for key, value in values.items()}
    data["filter"] = show
    return data


@pytest.fixture
def lee(make_member):
    return make_member(preferred_name="Lee Ashworth")


@pytest.fixture
def card(open_hub, lee) -> HubCard:
    """The ledger door on the open hub, open to everyone (so the 403 is what stops a non-organiser), Lee named."""
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
def kegs(hub) -> BudgetLine:
    return BudgetLineFactory(hub=hub, name="Kegs", category=C.DRINKS, budget_cents=60_000)


def _private_ledger(hub: Any) -> LedgerEntry:
    """A paid entry with every private field set to a string no page chrome could contain, and its receipt."""
    line = BudgetLineFactory(hub=hub, name="Spec Secret Line", budget_cents=12_345)
    return LedgerEntryFactory(
        hub=hub,
        line=line,
        kind=K.PAID_APPROVAL,
        source="Spec Secret Vendor",
        amount_cents=98_765,
        note="Spec secret note",
        receipt=_pdf("spec-secret.pdf"),
    )


def describe_the_door():
    def it_is_a_built_door_that_opens_the_ledger_from_the_hq_page(card):
        body = Client().get(card.hub.get_absolute_url()).content.decode()

        assert HubCard.Kind.LEDGER in BUILT_DOORS
        assert card.url == _page(card.hub)
        assert f'href="{_page(card.hub)}" class="pl-hq-card" data-hq-card-link' in body

    def it_carries_lees_title_line_and_intro_with_the_date(hub, lee_client):
        body = lee_client.get(_page(hub)).content.decode()

        assert f'<h1 class="pl-hq-lg__title">{LEDGER_TITLE}</h1>' in body
        assert LEDGER_INTRO.replace("'", "&#x27;") in body
        assert f"data-hq-lg-subline>{hub.title}</p>" in body
        assert f'href="{hub.get_absolute_url()}" class="pl-hq-lg__back"' in body

    def it_leaves_the_comments_where_d6b_and_d6c_go(hub, lee_client):
        """The template comments never render; the sections are in Lee's order."""
        body = lee_client.get(_page(hub)).content.decode()

        assert "D6b" not in body and "D6c" not in body
        order = ["data-hq-lg-board", "data-hq-lg-lines", "data-hq-lg-log", "data-hq-lg-entries", "data-hq-lg-receipts"]
        assert [body.index(marker) for marker in order] == sorted(body.index(marker) for marker in order)

    def it_turns_the_ledger_placeholder_into_the_live_door_in_place(open_hub, member):
        placeholder = HubCardFactory(
            hub=open_hub,
            kind=HubCard.Kind.PLACEHOLDER,
            title="PL Ledger",
            bullets="Money in and money out for the night\nSponsorships, the wine wall and the door\nKept by the core team",
            button_text="Open the ledger",
            audience=HubCard.Audience.MEMBERS,
            named=[member],
        )

        result = open_hub.seed_default_cards()

        door = HubCard.objects.get(pk=placeholder.pk)
        assert "PL Ledger" in result.adopted
        assert (door.kind, door.audience, door.button_text) == (
            HubCard.Kind.LEDGER,
            HubCard.Audience.MEMBERS,
            "Enter the ledger",
        )
        assert door.bullet_lines[0] == "Running tally and goals"
        assert list(door.named_members.all()) == [member]
        assert door.url == _page(open_hub)


def describe_who_may_open_it():
    def it_refuses_a_member_the_card_is_open_to_and_a_visitor_and_leaks_nothing(hub, member_client):
        _private_ledger(hub)
        LedgerSettingsFactory(hub=hub, approved_note="Spec secret approval note")

        for client in (member_client, Client()):
            for url in (_page(hub), _url("ledger_receipts", hub), _url("ledger_csv", hub)):
                response = client.get(url)

                assert response.status_code == 403, url
                body = b"".join(response.streaming_content) if response.streaming else response.content
                text = body.decode()
                for private in (
                    "Spec Secret Line",
                    "Spec Secret Vendor",
                    "Spec secret note",
                    "spec-secret",
                    "$987.65",
                    "$123.45",
                    "Spec secret approval note",
                    "data-hq-lg-board",
                ):
                    assert private not in text, (url, private)

    def it_gives_the_page_to_a_named_member_on_an_admins_card(card, lee_client):
        card.audience = HubCard.Audience.ADMINS
        card.save(update_fields=["audience"])
        _private_ledger(card.hub)

        body = lee_client.get(_page(card.hub)).content.decode()

        assert "Spec Secret Vendor" in body
        assert "data-hq-lg-log-form" in body

    def it_is_a_404_for_a_member_on_an_admins_card(card, member_client):
        card.audience = HubCard.Audience.ADMINS
        card.save(update_fields=["audience"])

        assert member_client.get(_page(card.hub)).status_code == 404
        assert member_client.get(_url("ledger_receipts", card.hub)).status_code == 404

    def it_sends_a_signed_out_visitor_through_login_on_a_members_card(card):
        card.audience = HubCard.Audience.MEMBERS
        card.save(update_fields=["audience"])

        response = Client().get(_page(card.hub))

        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_is_a_404_when_hidden_for_all_but_admins(card, member_client, lee_client, admin_client):
        card.is_hidden = True
        card.save(update_fields=["is_hidden"])

        assert member_client.get(_page(card.hub)).status_code == 404
        assert lee_client.get(_page(card.hub)).status_code == 404
        assert admin_client.get(_page(card.hub)).status_code == 200

    def it_is_a_404_on_a_hub_with_no_ledger_door(open_hub, admin_client):
        assert admin_client.get(_page(open_hub)).status_code == 404
        assert admin_client.get(_url("ledger_csv", open_hub)).status_code == 404

    def it_refuses_every_write_to_anyone_but_an_organiser(hub, kegs, member_client):
        entry = LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_APPROVAL, amount_cents=1_000)
        empty = BudgetLineFactory(hub=hub, name="Empty")

        for client in (member_client, Client()):
            assert client.post(_url("ledger_approval", hub), {"approved": "5", "approved_note": "x"}).status_code == 403
            assert (
                client.post(_url("ledger_line_add", hub), {"name": "X", "category": C.DECOR, "budget": "1"}).status_code
                == 403
            )
            assert (
                client.post(_url("ledger_line_edit", hub, kegs.pk), _line_post(kegs, name="Hacked")).status_code == 403
            )
            assert client.post(_url("ledger_line_delete", hub, empty.pk)).status_code == 403
            assert client.post(_url("ledger_entry_add", hub), _entry_post(kegs)).status_code == 403
            receipt = {f"receipt-{entry.pk}-receipt": _pdf()}
            assert client.post(_url("ledger_receipt", hub, entry.pk), receipt).status_code == 403
            assert client.post(_url("ledger_entry_delete", hub, entry.pk)).status_code == 403
        kegs.refresh_from_db()
        entry.refresh_from_db()
        assert kegs.name == "Kegs"
        assert not entry.receipt
        assert (BudgetLine.objects.count(), LedgerEntry.objects.count()) == (2, 1)
        assert not LedgerSettings.objects.exists()

    @pytest.mark.parametrize("role", [ROLE_MEMBER, ROLE_GUEST])
    def it_shows_a_previewing_admin_the_403_that_viewer_gets_but_lets_them_act(hub, admin_client, role):
        session = admin_client.session
        session["view_as_role"] = role
        session.save()

        assert admin_client.get(_page(hub)).status_code == 403
        assert admin_client.get(_url("ledger_receipts", hub)).status_code == 403
        assert (
            admin_client.post(
                _url("ledger_line_add", hub), {"name": "Ice", "category": C.DRINKS, "budget": "40"}
            ).status_code
            == 302
        )
        assert admin_client.get(_url("ledger_csv", hub)).status_code == 200
        assert BudgetLine.objects.filter(name="Ice").exists()

    def it_takes_posts_only(hub, kegs, admin_client):
        entry = LedgerEntryFactory(hub=hub, line=kegs)
        urls = [
            _url("ledger_approval", hub),
            _url("ledger_line_add", hub),
            _url("ledger_line_edit", hub, kegs.pk),
            _url("ledger_line_delete", hub, kegs.pk),
            _url("ledger_entry_add", hub),
            _url("ledger_receipt", hub, entry.pk),
            _url("ledger_entry_delete", hub, entry.pk),
        ]

        for url in urls:
            assert admin_client.get(url).status_code == 405, url

    def it_is_a_404_for_another_hubs_line_or_entry_on_every_post(hub, admin_client):
        elsewhere = BudgetLineFactory(name="Elsewhere Line")
        entry = LedgerEntryFactory(hub=elsewhere.hub, line=elsewhere, kind=K.PAID_OTHER)

        assert (
            admin_client.post(_url("ledger_line_edit", hub, elsewhere.pk), _line_post(elsewhere, name="X")).status_code
            == 404
        )
        assert admin_client.post(_url("ledger_line_delete", hub, elsewhere.pk)).status_code == 404
        receipt = {f"receipt-{entry.pk}-receipt": _pdf()}
        assert admin_client.post(_url("ledger_receipt", hub, entry.pk), receipt).status_code == 404
        assert admin_client.post(_url("ledger_entry_delete", hub, entry.pk)).status_code == 404
        elsewhere.refresh_from_db()
        entry.refresh_from_db()
        assert (elsewhere.name, bool(entry.receipt)) == ("Elsewhere Line", False)
        assert LedgerEntry.objects.count() == 1

    def it_refuses_logging_against_another_hubs_line(hub, kegs, lee_client):
        elsewhere = BudgetLineFactory(name="Elsewhere Line")

        response = lee_client.post(_url("ledger_entry_add", hub), _entry_post(elsewhere))

        assert response.status_code == 200
        assert "Select a valid choice" in response.content.decode()
        assert not LedgerEntry.objects.exists()


def describe_the_money():
    def it_reads_the_band_in_three_queries_and_two_with_the_settings(hub, kegs, django_assert_num_queries):
        decor = BudgetLineFactory(hub=hub, name="Decor", category=C.DECOR)
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.SPONSORED, amount_cents=10_000, source="Cascade")
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_APPROVAL, amount_cents=40_000)
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.REFUND, amount_cents=5_000)
        LedgerEntryFactory(hub=hub, line=decor, kind=K.PAID_OTHER, amount_cents=7_500)
        LedgerEntryFactory(kind=K.PAID_APPROVAL, amount_cents=99_900)  # another hub's
        found = services.ledger_settings(hub)

        with django_assert_num_queries(3):
            band = services.ledger_band(hub)
        with django_assert_num_queries(2):
            services.ledger_band(hub, found)

        assert (band.approved_cents, band.sponsored_cents, band.paid_approval_cents) == (90_000, 10_000, 40_000)
        assert (band.refund_cents, band.paid_other_cents, band.entries, band.sponsors) == (5_000, 7_500, 4, 1)
        assert (band.used_cents, band.left_cents, band.used_percent) == (35_000, 55_000, 39)
        assert [totals.line.name for totals in band.lines] == ["Kegs", "Decor"]
        kegs_totals = band.lines[0]
        assert (kegs_totals.sponsored_cents, kegs_totals.paid_cents, kegs_totals.paid_other_cents) == (
            10_000,
            35_000,
            0,
        )
        assert kegs_totals.entries == 3
        assert band.totals_row == ("$600", "$100", "$350", "$75")

    def it_counts_a_refund_against_what_was_used_and_lets_used_and_left_cross_zero(hub, kegs):
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.REFUND, amount_cents=6_000)

        band = services.ledger_band(hub)

        assert (band.used_cents, band.left_cents, band.used_percent) == (-6_000, 96_000, 0)
        assert (band.used_display, band.left_display) == ("-$60", "$960")
        assert band.lines[0].paid_display == "-$60"
        assert band.used_line == "$0 spent, $60 refunded"

        LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_APPROVAL, amount_cents=106_050)
        over = services.ledger_band(hub)

        assert (over.used_cents, over.left_cents, over.used_percent) == (100_050, -10_050, 100)
        assert (over.left_display, over.left_line) == ("-$100.50", "Over the approval")

    def it_says_lees_tile_lines_before_anything_is_logged(hub):
        band = services.ledger_band(hub)

        assert (band.approved_display, band.approved_note) == ("$900", "Drinks budget, Oct 14 meeting")
        assert (band.used_line, band.left_line, band.sponsored_line) == (
            "Net of keg deposit refunds",
            "Not yet spent",
            "Cash + in-kind gifts",
        )
        assert (band.used_percent, band.lines, band.totals_row) == (0, (), ("$0", "$0", "$0", "$0"))

    def it_counts_sponsors_once_whatever_the_case_or_spaces(hub, kegs):
        for source in ("Cascade Brewing", "cascade brewing", "Cascade Brewing ", "Fred Meyer"):
            LedgerEntryFactory(hub=hub, line=kegs, kind=K.SPONSORED, source=source)
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_APPROVAL, source="Vendor Not A Sponsor")

        band = services.ledger_band(hub)

        assert band.sponsors == 2
        assert band.sponsored_line == "2 sponsors, cash + in-kind"

    def it_says_one_sponsor_in_the_singular(hub, kegs):
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.SPONSORED, source="Cascade")

        assert services.ledger_band(hub).sponsored_line == "1 sponsor, cash + in-kind"

    @pytest.mark.parametrize(
        "approved,used,percent", [(0, 0, 0), (0, 100, 100), (90_000, -500, 0), (90_000, 45_000, 50)]
    )
    def it_bounds_the_meter_and_a_zero_approval(hub, kegs, approved, used, percent):
        LedgerSettingsFactory(hub=hub, approved_cents=approved)
        if used > 0:
            LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_APPROVAL, amount_cents=used)
        elif used < 0:
            LedgerEntryFactory(hub=hub, line=kegs, kind=K.REFUND, amount_cents=-used)

        assert services.ledger_band(hub).used_percent == percent

    @pytest.mark.parametrize(
        "entries,label,tone",
        [
            ([], "Open", ""),
            ([(K.SPONSORED, 100)], "Sponsored", "ok"),
            ([(K.PAID_APPROVAL, 100)], "Purchased", "info"),
            ([(K.PAID_OTHER, 100)], "Purchased", "info"),
            ([(K.SPONSORED, 100), (K.PAID_APPROVAL, 100)], "Mixed", "warn"),
            ([(K.SPONSORED, 100), (K.PAID_OTHER, 100)], "Mixed", "warn"),
            # A full refund leaves the purchase net nothing: the line is sponsored again, or open.
            ([(K.SPONSORED, 100), (K.PAID_APPROVAL, 100), (K.REFUND, 100)], "Sponsored", "ok"),
            ([(K.PAID_APPROVAL, 100), (K.REFUND, 100)], "Open", ""),
            # Lee's rule reads the sum: a refund above what was paid can leave nothing covered.
            ([(K.SPONSORED, 100), (K.REFUND, 300)], "Open", ""),
            ([(K.REFUND, 100)], "Open", ""),
        ],
    )
    def it_rates_each_line_by_lees_rules(hub, kegs, entries, label, tone):
        for kind, cents in entries:
            LedgerEntryFactory(hub=hub, line=kegs, kind=kind, amount_cents=cents)

        status = services.ledger_band(hub).lines[0].status

        assert (status.label, status.tone) == (label, tone)

    def it_dashes_a_column_with_nothing_in_it(hub, kegs):
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.SPONSORED, amount_cents=1_250)

        totals = services.ledger_band(hub).lines[0]

        assert (totals.sponsored_display, totals.paid_display, totals.paid_other_display) == ("$12.50", "–", "–")

    def it_charges_only_drinks_lines_to_the_bar_and_never_below_zero(hub, kegs):
        decor = BudgetLineFactory(hub=hub, name="Lights", category=C.DECOR)
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_APPROVAL, amount_cents=40_000)
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.REFUND, amount_cents=5_000)
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_OTHER, amount_cents=2_000)
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.SPONSORED, amount_cents=9_000)
        LedgerEntryFactory(hub=hub, line=decor, kind=K.PAID_APPROVAL, amount_cents=30_000)

        assert services.bar_cost_cents(hub) == 37_000
        assert services.ledger_band(hub).bar_cost_cents == 37_000

        cups = BudgetLineFactory(hub=hub, name="Cups", category=C.DRINKS)
        LedgerEntryFactory(hub=hub, line=cups, kind=K.REFUND, amount_cents=50_000)

        assert services.bar_cost_cents(hub) == 0

    def it_names_the_kinds_with_the_hubs_approval(hub):
        assert services.ledger_kind_label(K.PAID_APPROVAL, "$1,200") == "Paid from board $1,200"
        assert services.ledger_kind_label(K.REFUND, "$1,200") == "Refund back to $1,200"
        assert services.ledger_kind_label(K.SPONSORED, "$1,200") == "Sponsored or donated"
        assert [services.ledger_kind_short(kind, "$900") for kind in K.values] == [
            "Sponsored",
            "From $900",
            "Other PLM $",
            "Refund",
        ]
        assert services.ledger_filters("$900") == [
            ("all", "All"),
            (K.SPONSORED, "Sponsored"),
            (K.PAID_APPROVAL, "From $900"),
            (K.PAID_OTHER, "Other PLM $"),
            (K.REFUND, "Refunds"),
        ]

    def it_writes_money_with_a_minus_below_zero():
        assert [services.signed_money(cents) for cents in (0, 5, 90_000, -1, -10_050)] == [
            "$0",
            "$0.05",
            "$900",
            "-$0.01",
            "-$100.50",
        ]


def _doors(**overrides: Any) -> services.DoorFigures:
    """No figures on any other door, with ``overrides`` in place."""
    hub = EventHubFactory()
    figures = services.door_figures(hub)
    values = {
        "sponsors": figures.sponsors,
        "flyers": 0,
        "checkin": figures.checkin,
        "wine": figures.wine,
        "cash": figures.cash,
        "bar": figures.bar,
        "tickets": figures.tickets,
        "vendors": figures.vendors,
        "guilds": figures.guilds,
        "staff": figures.staff,
        "attendance": figures.attendance,
    }
    values.update(overrides)
    return services.DoorFigures(**values)


def describe_the_board_summary():
    """Parity with Lee's ``summaryText``: her sentences, her order, her conditions."""

    def it_reads_the_drinks_budget_in_lees_four_sentences(hub, kegs):
        for source in ("Cascade Brewing", "Fred Meyer"):
            LedgerEntryFactory(hub=hub, line=kegs, kind=K.SPONSORED, amount_cents=12_500, source=source)
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_APPROVAL, amount_cents=40_000)
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.REFUND, amount_cents=5_000)
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_OTHER, amount_cents=7_550)

        text = services.board_summary(services.ledger_band(hub), _doors())

        assert text == (
            "Holiday Bizarre drinks: the board approved $900. "
            "So far $250 in drinks and supplies has been sponsored by 2 sponsors. "
            "We have used $350 of the approval (after $50 in refunds), leaving $550. "
            "Another $75.50 came from other PLM funds."
        )

    def it_leaves_out_what_has_nothing_to_say(hub, kegs):
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_APPROVAL, amount_cents=100_000)

        text = services.board_summary(services.ledger_band(hub), _doors())

        assert text == (
            "Holiday Bizarre drinks: the board approved $900. "
            "So far $0 in drinks and supplies has been sponsored. "
            "We have used $1,000 of the approval, leaving -$100."
        )

    def it_says_one_sponsor_in_the_singular(hub, kegs):
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.SPONSORED, amount_cents=500, source="Cascade")

        assert "has been sponsored by 1 sponsor." in services.board_summary(services.ledger_band(hub), _doors())

    def it_reads_the_other_doors_in_lees_order_each_from_its_source(hub, kegs):
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.SPONSORED, amount_cents=500, source="Cascade")
        SponsorFactory(hub=hub, status="received", amount_cents=150_000)
        SponsorFactory(hub=hub, status="committed", amount_cents=50_000)
        MarketingActionFactory.create_batch(3, hub=hub, kind=MarketingKind.FLYER)
        HubGoalFactory(hub=hub, kind="bottles", target=100)
        WineBottleFactory(hub=hub, qty=4, value_cents=2_500)

        band = services.ledger_band(hub)
        text = services.board_summary(band, services.door_figures(hub))

        assert text.endswith(
            "Cash sponsorships: $1,500 received and $500 committed. "
            "Flyers are up at 3 locations. "
            "Wine wall: 4 of 100 bottles donated, worth $100."
        )

    def it_says_the_volunteers_from_the_check_in(hub, kegs, make_member):
        from hq.models import VolunteerHours

        LedgerEntryFactory(hub=hub, line=kegs)
        for hours in ("2.5", "3"):
            VolunteerHours.objects.create(hub=hub, member=make_member(), hours=Decimal(hours))

        text = services.board_summary(services.ledger_band(hub), services.door_figures(hub))

        assert text.endswith("Volunteers: 2 people gave 5.5 hours to event prep.")

    def it_writes_the_singulars_and_a_wall_with_no_goal(hub, kegs, make_member):
        from hq.models import VolunteerHours

        LedgerEntryFactory(hub=hub, line=kegs)
        MarketingActionFactory(hub=hub, kind=MarketingKind.FLYER)
        VolunteerHours.objects.create(hub=hub, member=make_member(), hours=Decimal("1"))
        WineBottleFactory(hub=hub, qty=1, value_cents=2_000)

        text = services.board_summary(services.ledger_band(hub), services.door_figures(hub))

        assert "Flyers are up at 1 location." in text
        assert "Volunteers: 1 person gave 1 hour to event prep." in text
        assert text.endswith("Wine wall: 1 bottle donated, worth $20.")

    def it_shows_lees_empty_line_until_the_ledger_has_an_entry_but_copies_the_paragraph(hub, lee_client):
        SponsorFactory(hub=hub, status="received", amount_cents=10_000)

        body = lee_client.get(_page(hub)).content.decode()

        assert f"data-hq-lg-summary>{services.LEDGER_EMPTY_SUMMARY}</p>" in body
        assert "Holiday Bizarre drinks: the board approved $900." in body  # the hidden box Copy reads
        assert "data-hq-lg-summary-box>Holiday Bizarre drinks" in body
        assert "Board summary copied." in body

    def it_shows_the_paragraph_once_there_is_an_entry(hub, kegs, lee_client):
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.SPONSORED, amount_cents=500, source="Cascade")

        body = lee_client.get(_page(hub)).content.decode()

        assert "data-hq-lg-summary>Holiday Bizarre drinks: the board approved $900." in body


def describe_the_board_on_the_page():
    def it_renders_lees_four_tiles_and_the_meter(hub, kegs, lee_client):
        LedgerSettingsFactory(hub=hub, approved_cents=100_000, approved_note="Spec board note")
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_APPROVAL, amount_cents=110_000)

        body = lee_client.get(_page(hub)).content.decode()

        for tile, figure, helper in (
            ("approved", "$1,000", "Spec board note"),
            ("used", "$1,100", "Net of keg deposit refunds"),
            ("left", "-$100", "Over the approval"),
            ("sponsored", "$0", "Cash + in-kind gifts"),
        ):
            assert f'data-hq-lg-tile="{tile}"' in body
            assert f"data-hq-lg-figure>{figure}</strong>" in body
            assert f"data-hq-lg-helper>{helper}</span>" in body
        assert "pl-hq-lg__tile--over" in body
        assert 'max="100" value="100"' in body

    def it_links_the_csv_out_of_hx_boost(hub, lee_client):
        body = lee_client.get(_page(hub)).content.decode()

        assert (
            f'href="{_url("ledger_csv", hub)}" class="pl-hq-lg__btn pl-hq-lg__btn--panel" hx-boost="false" data-pl-download'
            in body
        )


def describe_the_approval():
    def it_saves_the_amount_and_note_and_names_the_kinds_filters_and_column_with_it(hub, kegs, lee_client):
        posted = _approval_post(services.ledger_settings(hub), approved="1200.50", approved_note="Nov 3 vote")

        response = lee_client.post(_url("ledger_approval", hub), posted)

        assert response.status_code == 302
        found = LedgerSettings.objects.get(hub=hub)
        assert (found.approved_cents, found.approved_note) == (120_050, "Nov 3 vote")
        body = lee_client.get(_page(hub)).content.decode()
        assert "Approval saved." in body
        assert "Paid from board $1,200.50" in body
        assert "Refund back to $1,200.50" in body
        assert ">From $1,200.50</a>" in body
        assert "Paid from $1,200.50</th>" in body
        assert "$900" not in body.split("data-hq-lg-board", 1)[1].split("data-hq-lg-receipts", 1)[0]

    def it_writes_only_the_approval_so_d6bs_figures_stay(hub, lee_client):
        found = LedgerSettingsFactory(hub=hub, ipa_cents=700, singles_sold=12)

        lee_client.post(_url("ledger_approval", hub), _approval_post(found, approved="800", approved_note=""))

        found = LedgerSettings.objects.get(hub=hub)
        assert (found.approved_cents, found.approved_note, found.ipa_cents, found.singles_sold) == (80_000, "", 700, 12)

    def it_never_writes_the_settings_on_a_get(hub, lee_client):
        lee_client.get(_page(hub))
        lee_client.get(_url("ledger_receipts", hub))

        assert not LedgerSettings.objects.exists()

    @pytest.mark.parametrize("approved", ["", "-1", "abc", str(LEDGER_MAX_DOLLARS + 1), "1.234"])
    def it_refuses_an_approval_out_of_range_and_opens_the_form(hub, lee_client, approved):
        response = lee_client.post(_url("ledger_approval", hub), {"approved": approved, "approved_note": "x"})

        body = response.content.decode()
        assert response.status_code == 200
        assert "open data-hq-lg-approval" in body
        assert "pl-field-error" in body
        assert not LedgerSettings.objects.exists()

    def it_starts_from_the_saved_figures_and_carries_them_as_seen(hub, lee_client):
        LedgerSettingsFactory(hub=hub, approved_cents=95_025, approved_note="Spec note")

        body = lee_client.get(_page(hub)).content.decode()

        assert 'value="950.25"' in body
        assert 'value="Spec note"' in body
        assert 'max="1000000"' in body
        assert '<input type="hidden" name="seen_approved_cents" value="95025" id="id_seen_approved_cents">' in body
        assert '<input type="hidden" name="seen_approved_note" value="Spec note" id="id_seen_approved_note">' in body
        assert 'name="updated_at"' not in body

    def it_carries_lees_defaults_as_seen_before_the_first_save(hub, lee_client):
        body = lee_client.get(_page(hub)).content.decode()

        assert 'name="seen_approved_cents" value="90000"' in body
        assert 'name="seen_approved_note" value="Drinks budget, Oct 14 meeting"' in body

    @pytest.mark.parametrize(
        "seen",
        [
            {"seen_approved_cents": 70_000},
            {"seen_approved_note": "An older note"},
            {"seen_approved_note": ""},
            {"seen_approved_cents": None},
            {"seen_approved_note": None},
        ],
        ids=["older amount", "older note", "blank note", "no amount", "no note"],
    )
    def it_refuses_a_save_after_someone_else_changed_the_approval(hub, lee_client, seen):
        found = LedgerSettingsFactory(hub=hub, approved_cents=80_000, approved_note="Their note")
        posted = _approval_post(found, approved="1500", approved_note="Mine")
        posted.update(seen)
        posted = {key: value for key, value in posted.items() if value is not None}

        response = lee_client.post(_url("ledger_approval", hub), posted)

        body = response.content.decode()
        found.refresh_from_db()
        assert response.status_code == 200
        assert (found.approved_cents, found.approved_note) == (80_000, "Their note")
        assert (
            '<p class="pl-field-error" role="alert" data-hq-lg-approval-error>Someone else changed the approval. '
            "Your page has been refreshed; make your change again.</p>" in body
        )
        assert "open data-hq-lg-approval" in body
        assert 'value="800.00"' in body and 'value="Their note"' in body
        assert 'name="seen_approved_cents" value="80000"' in body

    def it_matches_a_blank_note_with_a_blank_note(hub, lee_client):
        found = LedgerSettingsFactory(hub=hub, approved_cents=80_000, approved_note="")

        response = lee_client.post(_url("ledger_approval", hub), _approval_post(found, approved="850"))

        found.refresh_from_db()
        assert response.status_code == 302
        assert (found.approved_cents, found.approved_note) == (85_000, "")

    def it_saves_again_from_the_page_it_was_given(hub, lee_client):
        lee_client.post(
            _url("ledger_approval", hub), _approval_post(services.ledger_settings(hub), approved_note="First")
        )
        found = LedgerSettings.objects.get(hub=hub)

        response = lee_client.post(_url("ledger_approval", hub), _approval_post(found, approved="950"))

        found.refresh_from_db()
        assert response.status_code == 302
        assert (found.approved_cents, found.approved_note) == (95_000, "First")


def describe_the_budget_lines():
    def it_adds_a_line_over_htmx_with_the_log_forms_select_out_of_band(hub, kegs, lee_client):
        response = lee_client.post(
            _url("ledger_line_add", hub),
            {"name": "Ice", "category": C.DRINKS, "budget": "40.25", "filter": "refund"},
            **HTMX,
        )

        body = response.content.decode()
        ice = BudgetLine.objects.get(name="Ice")
        assert (ice.hub, ice.category, ice.budget_cents, ice.sort_order) == (hub, C.DRINKS, 4_025, kegs.sort_order + 1)
        assert _toast(response) == "Line added."
        assert body.index("data-hq-lg-lines") < body.index('id="hq-lg-entry-line"')
        assert 'id="hq-lg-entry-line" data-hq-lg-entry-line hx-swap-oob="true"' in body
        assert f'<option value="{ice.pk}" selected>Ice</option>' in body
        assert "open data-hq-lg-line-add" in body
        assert 'name="filter" value="refund"' in body

    def it_lands_a_plain_add_back_on_the_page_with_the_filter(hub, lee_client):
        response = lee_client.post(
            _url("ledger_line_add", hub), {"name": "Ice", "category": C.DRINKS, "budget": "0", "filter": "refund"}
        )

        assert response.status_code == 302
        assert response["Location"] == _page(hub, "refund")

    @pytest.mark.parametrize(
        "fields",
        [
            {"name": "", "category": C.DRINKS, "budget": "1"},
            {"name": "Ice", "category": "nope", "budget": "1"},
            {"name": "Ice", "category": C.DRINKS, "budget": "-1"},
            {"name": "Ice", "category": C.DRINKS, "budget": str(LEDGER_MAX_DOLLARS + 1)},
        ],
    )
    def it_refuses_a_bad_line_and_saves_nothing(hub, lee_client, fields):
        htmx = lee_client.post(_url("ledger_line_add", hub), fields, **HTMX)
        plain = lee_client.post(_url("ledger_line_add", hub), fields)

        assert htmx.status_code == 200
        assert "pl-field-error" in htmx.content.decode()
        assert "open data-hq-lg-line-add" in htmx.content.decode()
        assert "data-hq-ledger" in plain.content.decode()
        assert not BudgetLine.objects.exists()

    def it_orders_the_lines_and_names_each_in_its_labels(hub, lee_client):
        second = BudgetLineFactory(hub=hub, name="beer cups", sort_order=1)
        first = BudgetLineFactory(hub=hub, name="Ales", sort_order=1)
        top = BudgetLineFactory(hub=hub, name="Zinfandel", sort_order=0)

        body = lee_client.get(_page(hub)).content.decode()

        names = [line.name for line in (top, first, second)]
        assert [body.index(f"data-hq-lg-line-name>{name}<") for name in names] == sorted(
            body.index(f"data-hq-lg-line-name>{name}<") for name in names
        )
        for name in names:
            assert f"Line name for {name}" in body
            assert f'aria-label="Save {name}"' in body
            assert f'aria-label="Edit {name}"' in body
            assert f'aria-label="Delete {name}"' in body

    def it_saves_a_line_and_sends_back_only_it_with_the_totals_select_and_ledger(hub, kegs, lee_client):
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_APPROVAL, amount_cents=1_000, source="Spec Vendor")

        response = lee_client.post(
            _url("ledger_line_edit", hub, kegs.pk), _line_post(kegs, name="Kegs and taps", budget="700"), **HTMX
        )

        body = response.content.decode()
        kegs.refresh_from_db()
        assert (kegs.name, kegs.budget_cents) == ("Kegs and taps", 70_000)
        assert _toast(response) == "Saved."
        assert f'data-hq-lg-line="{kegs.pk}"' in body
        assert 'id="hq-lg-lines-total" data-hq-lg-lines-total hx-swap-oob="true"' in body
        assert f'<option value="{kegs.pk}" selected>Kegs and taps</option>' in body
        assert 'id="hq-lg-entries" aria-labelledby="hq-lg-entries-title" data-hq-lg-entries hx-swap-oob="true"' in body
        assert "<td>Kegs and taps</td>" in body
        assert "data-hq-lg-line-add" not in body

    def it_brings_back_a_refused_line_open_with_its_errors_and_saves_nothing(hub, kegs, lee_client):
        response = lee_client.post(_url("ledger_line_edit", hub, kegs.pk), _line_post(kegs, name=""), **HTMX)

        body = response.content.decode()
        kegs.refresh_from_db()
        assert kegs.name == "Kegs"
        assert "pl-field-error" in body
        assert "{ editing: true }" in body
        assert "data-hq-lg-line-name>Kegs<" in body
        assert "hx-swap-oob" not in body
        assert "HX-Trigger" not in response

    def it_lands_a_plain_save_back_with_a_message_and_renders_a_refused_one(hub, kegs, lee_client):
        saved = lee_client.post(
            _url("ledger_line_edit", hub, kegs.pk), _line_post(kegs, category=C.DECOR, filter="refund")
        )
        kegs.refresh_from_db()
        refused = lee_client.post(_url("ledger_line_edit", hub, kegs.pk), _line_post(kegs, budget="-5"))

        assert saved.status_code == 302
        assert saved["Location"] == _page(hub, "refund")
        assert kegs.category == C.DECOR
        assert refused.status_code == 200
        assert "data-hq-ledger" in refused.content.decode()
        assert "{ editing: true }" in refused.content.decode()

    def it_refuses_a_save_from_an_older_page_and_brings_the_line_back_as_saved(hub, kegs, lee_client):
        stale = _line_post(kegs, name="From an old page")
        BudgetLine.objects.filter(pk=kegs.pk).update(
            name="Kegs (renamed)", updated_at=timezone.now() + timedelta(seconds=5)
        )

        response = lee_client.post(_url("ledger_line_edit", hub, kegs.pk), stale, **HTMX)
        plain = lee_client.post(_url("ledger_line_edit", hub, kegs.pk), stale)

        body = response.content.decode()
        kegs.refresh_from_db()
        assert kegs.name == "Kegs (renamed)"
        assert "Someone else changed this line." in body
        assert "data-hq-lg-line-name>Kegs (renamed)<" in body
        assert "{ editing: true }" in body
        assert "HX-Trigger" not in response
        assert "Someone else changed this line." in plain.content.decode()
        assert "data-hq-ledger" in plain.content.decode()

    def it_refuses_a_save_with_no_stamp(hub, kegs, lee_client):
        posted = _line_post(kegs, name="No stamp")
        del posted[f"line-{kegs.pk}-updated_at"]

        lee_client.post(_url("ledger_line_edit", hub, kegs.pk), posted, **HTMX)

        kegs.refresh_from_db()
        assert kegs.name == "Kegs"

    def it_deletes_an_empty_line_through_the_confirm_modal(hub, kegs, lee_client):
        body = lee_client.get(_page(hub)).content.decode()

        assert f"open-confirm', 'delete-line-{kegs.pk}'" in body
        response = lee_client.post(_url("ledger_line_delete", hub, kegs.pk), {"filter": "sponsored"})

        assert response["Location"] == _page(hub, "sponsored")
        assert not BudgetLine.objects.exists()
        assert "Kegs is off the budget." in lee_client.get(_page(hub)).content.decode()

    def it_refuses_by_name_to_delete_a_line_with_entries(hub, kegs, lee_client):
        LedgerEntryFactory.create_batch(2, hub=hub, line=kegs)

        body = lee_client.get(_page(hub)).content.decode()
        response = lee_client.post(_url("ledger_line_delete", hub, kegs.pk), follow=True)

        assert "2 entries in the ledger, so it can't be deleted" in body
        assert f"delete-line-{kegs.pk}" not in body
        assert BudgetLine.objects.filter(pk=kegs.pk).exists()
        assert (
            "A budget line with entries cannot be deleted: Kegs has 2 entries in the ledger. Delete those entries first."
            in response.content.decode()
        )

    def it_restricts_a_line_with_entries_in_the_database_too(hub, kegs):
        LedgerEntryFactory(hub=hub, line=kegs)

        with pytest.raises(RestrictedError):
            kegs.delete()

    def it_names_one_entry_in_the_singular(hub, kegs):
        LedgerEntryFactory(hub=hub, line=kegs)

        with pytest.raises(services.LineHasEntries, match="Kegs has 1 entry in the ledger"):
            services.delete_budget_line(kegs)

    def it_locks_the_line_before_counting_its_entries(hub, kegs):
        with CaptureQueriesContext(connection) as queries:
            services.delete_budget_line(kegs)

        sql = [query["sql"] for query in queries.captured_queries]
        lock = next(i for i, text in enumerate(sql) if "FOR UPDATE" in text)
        count = next(i for i, text in enumerate(sql) if "COUNT(" in text)
        assert '"hq_budgetline"' in sql[lock]
        assert lock < count
        assert not BudgetLine.objects.exists()

    @pytest.mark.parametrize(
        "refusal",
        [
            IntegrityError('insert or update on table "hq_ledgerentry" violates foreign key constraint'),
            RestrictedError("an entry landed", set()),
        ],
        ids=["integrity", "restricted"],
    )
    def it_refuses_by_name_when_an_entry_lands_between_the_count_and_the_delete(hub, monkeypatch, refusal):
        racing = BudgetLineFactory(hub=hub, name="Racing")

        def _late_entry(*args: Any, **kwargs: Any) -> None:
            raise refusal

        monkeypatch.setattr(BudgetLine, "delete", _late_entry)
        with pytest.raises(services.LineHasEntries, match="Racing has entries") as raised:
            services.delete_budget_line(racing)

        assert raised.value.__cause__ is refusal
        assert BudgetLine.objects.filter(pk=racing.pk).exists()  # the connection is still usable

    def it_answers_a_late_entry_on_the_page_with_the_same_refusal(hub, lee_client, monkeypatch):
        racing = BudgetLineFactory(hub=hub, name="Racing")

        def _late_entry(*args: Any, **kwargs: Any) -> None:
            raise IntegrityError("violates foreign key constraint")

        monkeypatch.setattr(BudgetLine, "delete", _late_entry)
        response = lee_client.post(_url("ledger_line_delete", hub, racing.pk), {"filter": "all"}, follow=True)

        assert "A budget line with entries cannot be deleted: Racing has entries." in response.content.decode()

    def it_says_so_when_there_are_no_lines(hub, lee_client):
        body = lee_client.get(_page(hub)).content.decode()

        assert "data-hq-lg-lines-empty>No budget lines yet. Add one below." in body
        assert "data-hq-lg-lines-total" not in body


def describe_logging_an_entry():
    def it_logs_a_sponsored_gift_and_lands_back_with_lees_message(hub, kegs, lee, lee_client):
        response = lee_client.post(_url("ledger_entry_add", hub), _entry_post(kegs, filter="sponsored"))

        entry = LedgerEntry.objects.get()
        assert response.status_code == 302
        assert response["Location"] == _page(hub, "sponsored")
        assert (entry.hub, entry.line, entry.kind, entry.source) == (hub, kegs, K.SPONSORED, "Cascade Brewing")
        assert (entry.amount_cents, entry.date, entry.note, entry.created_by) == (
            25_000,
            date(2026, 11, 2),
            "1 half-barrel IPA, in-kind",
            lee,
        )
        assert not entry.receipt
        assert "Added to the ledger." in lee_client.get(_page(hub)).content.decode()

    def it_stores_a_photo_receipt_as_a_jpeg_under_a_random_name(hub, kegs, lee_client):
        upload = photo_upload("IMG_0001.png", size=(3000, 1000))

        lee_client.post(
            _url("ledger_entry_add", hub), {**_entry_post(kegs, kind=K.PAID_APPROVAL), "entry-receipt": upload}
        )

        entry = LedgerEntry.objects.get()
        assert entry.receipt.name.startswith("hq/receipts/")
        assert entry.receipt.name.endswith(".jpg")
        assert "IMG_0001" not in entry.receipt.name
        assert default_storage.exists(entry.receipt.name)
        assert not entry.receipt_is_pdf
        assert "Added with receipt." in lee_client.get(_page(hub)).content.decode()

    def it_stores_a_pdf_receipt_as_it_came(hub, kegs, lee_client):
        lee_client.post(
            _url("ledger_entry_add", hub), {**_entry_post(kegs, kind=K.REFUND), "entry-receipt": _pdf("scan.txt")}
        )

        entry = LedgerEntry.objects.get()
        assert entry.receipt.name.endswith(".pdf")
        assert entry.receipt_is_pdf
        with default_storage.open(entry.receipt.name) as stored:
            assert stored.read().startswith(b"%PDF-")

    def it_refuses_a_receipt_on_a_sponsored_gift(hub, kegs, lee_client):
        response = lee_client.post(_url("ledger_entry_add", hub), {**_entry_post(kegs), "entry-receipt": _pdf()})

        assert response.status_code == 200
        assert RECEIPT_NOT_TAKEN in response.content.decode()
        assert not LedgerEntry.objects.exists()

    def it_refuses_the_same_through_the_service(hub, kegs):
        with pytest.raises(services.ReceiptNotTaken):
            services.add_ledger_entry(
                hub,
                {"kind": K.SPONSORED, "line": kegs, "source": "X", "amount_cents": 1, "receipt": _pdf()},
                member=None,
            )

    def it_refuses_a_file_that_is_not_a_photo_or_a_pdf(hub, kegs, lee_client):
        text = SimpleUploadedFile("receipt.jpg", b"not an image at all", content_type="image/jpeg")

        response = lee_client.post(
            _url("ledger_entry_add", hub), {**_entry_post(kegs, kind=K.PAID_OTHER), "entry-receipt": text}
        )

        assert RECEIPT_TYPE_ERROR in response.content.decode()
        assert not LedgerEntry.objects.exists()

    @pytest.mark.parametrize(
        "upload",
        [
            pytest.param(lambda: _receipt_image("GIF"), id="gif"),
            pytest.param(lambda: _receipt_image("TIFF"), id="tiff"),
            pytest.param(lambda: _receipt_image("BMP"), id="bmp"),
            pytest.param(
                lambda: SimpleUploadedFile("receipt.jpg", _EPS, content_type="image/jpeg"),
                id="eps",
            ),
        ],
    )
    def it_refuses_an_image_pillow_opens_in_a_format_we_do_not_keep(hub, kegs, lee_client, upload):
        response = lee_client.post(
            _url("ledger_entry_add", hub), {**_entry_post(kegs, kind=K.PAID_OTHER), "entry-receipt": upload()}
        )

        assert RECEIPT_TYPE_ERROR in response.content.decode().replace("&#x27;", "'")
        assert not LedgerEntry.objects.exists()

    def it_refuses_an_eps_through_the_form_helper_too():
        with pytest.raises(ValidationError, match="not a receipt we can keep"):
            checked_receipt(SimpleUploadedFile("scan.eps", _EPS, content_type="application/postscript"))

    @pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP", "HEIF"])
    def it_keeps_a_jpg_png_webp_or_heic_photo_as_a_jpeg(hub, kegs, lee_client, fmt):
        lee_client.post(
            _url("ledger_entry_add", hub),
            {**_entry_post(kegs, kind=K.PAID_OTHER), "entry-receipt": _receipt_image(fmt)},
        )

        entry = LedgerEntry.objects.get()
        assert entry.receipt.name.endswith(".jpg")
        assert not entry.receipt_is_pdf

    def it_names_the_same_formats_in_the_help_bubble_and_its_label(hub, kegs, lee_client):
        body = lee_client.get(_page(hub)).content.decode()

        help_box = body.split("data-hq-lg-receipt>", 1)[1].split("data-hq-lg-zone", 1)[0]
        label = help_box.split('aria-label="', 1)[1].split('"', 1)[0]
        bubble = help_box.split('<span class="pl-help__bubble">', 1)[1].split("</span>", 1)[0]
        assert label == bubble
        assert "a JPG, PNG, WEBP or HEIC photo up to" in label

    def it_refuses_a_photo_over_the_pixel_cap(hub, kegs, lee_client, monkeypatch):
        monkeypatch.setattr("core.validators.MAX_IMAGE_PIXELS", 1_000)

        response = lee_client.post(
            _url("ledger_entry_add", hub), {**_entry_post(kegs, kind=K.PAID_OTHER), "entry-receipt": photo_upload()}
        )

        assert "too large to open" in response.content.decode()
        assert not LedgerEntry.objects.exists()

    def it_refuses_files_over_their_byte_caps(hub, kegs, lee_client, settings):
        settings.MAX_UPLOAD_IMAGE_BYTES = 10
        settings.MAX_UPLOAD_DOCUMENT_BYTES = 10

        photo = lee_client.post(
            _url("ledger_entry_add", hub), {**_entry_post(kegs, kind=K.PAID_OTHER), "entry-receipt": photo_upload()}
        )
        pdf = lee_client.post(
            _url("ledger_entry_add", hub), {**_entry_post(kegs, kind=K.PAID_OTHER), "entry-receipt": _pdf()}
        )

        assert "Image must be" in photo.content.decode()
        assert "A PDF must be 0 MB or smaller." in pdf.content.decode()
        assert not LedgerEntry.objects.exists()

    @pytest.mark.parametrize("amount", ["0", "-5", "", str(LEDGER_MAX_DOLLARS + 1), "1.005"])
    def it_refuses_an_amount_that_is_not_above_zero_or_over_the_cap(hub, kegs, lee_client, amount):
        response = lee_client.post(_url("ledger_entry_add", hub), _entry_post(kegs, amount=amount))

        assert response.status_code == 200
        assert "pl-field-error" in response.content.decode()
        assert not LedgerEntry.objects.exists()

    def it_renders_lees_form_with_the_kinds_and_the_labels_that_follow_them(hub, kegs, lee_client):
        body = lee_client.get(_page(hub)).content.decode()

        for label in (
            "Sponsored or donated",
            "Paid from board $900",
            "Paid from other PLM funds",
            "Refund back to $900",
        ):
            assert f'<span class="pl-radio-option__label">{label}</span>' in body
        assert 'x-model="kind"' in body
        assert "kind === 'sponsored' ? 'Sponsor' : (kind === 'refund' ? 'Refunded by' : 'Vendor')" in body
        assert "kind === 'sponsored' ? 'Value ($)' : 'Amount ($)'" in body
        assert LEDGER_RECEIPT_HINT in body
        assert "x-show=\"kind !== 'sponsored'\" x-cloak data-hq-lg-receipt" in body
        assert ":disabled=\"kind === 'sponsored'\"" in body
        assert 'enctype="multipart/form-data" hx-boost="false"' in body
        assert f'value="{timezone.localdate():%Y-%m-%d}"' in body
        assert f'max="{LEDGER_MAX_DOLLARS}"' in body
        assert 'data-help-key="hq.ledger"' in body

    def it_labels_a_refused_refund_in_its_own_words_with_the_receipt_open(hub, kegs, lee_client):
        response = lee_client.post(_url("ledger_entry_add", hub), _entry_post(kegs, kind=K.REFUND, source=""))

        body = response.content.decode()
        assert "Refunded by" in body.split('x-text="kind', 1)[1].split("</label>", 1)[0]
        assert "Amount ($)" in body
        assert "x-cloak data-hq-lg-receipt" not in body

    def it_labels_a_refused_purchase_as_from_a_vendor(hub, kegs, lee_client):
        response = lee_client.post(_url("ledger_entry_add", hub), _entry_post(kegs, kind=K.PAID_OTHER, source=""))

        body = response.content.decode()
        assert "Vendor" in body.split("kind === 'refund' ? 'Refunded by' : 'Vendor')\"", 1)[1].split("</label>", 1)[0]


def describe_the_ledger():
    def it_lists_newest_first_with_lees_columns(hub, kegs, lee_client):
        old = LedgerEntryFactory(hub=hub, line=kegs, date=date(2026, 10, 1), source="Spec Old")
        new = LedgerEntryFactory(hub=hub, line=kegs, date=date(2026, 11, 1), source="Spec New")
        same_day_later = LedgerEntryFactory(hub=hub, line=kegs, date=date(2026, 11, 1), source="Spec Later")
        refund = LedgerEntryFactory(hub=hub, line=kegs, date=date(2026, 9, 1), kind=K.REFUND, amount_cents=4_000)

        body = lee_client.get(_page(hub)).content.decode()

        order = [same_day_later, new, old, refund]
        positions = [body.index(f'data-hq-lg-entry="{entry.pk}"') for entry in order]
        assert positions == sorted(positions)
        assert "−$40</td>" in body
        assert '<span class="pl-hq-lg__chip pl-hq-lg__chip--refund">Refund</span>' in body
        assert '<span class="pl-hq-lg__chip pl-hq-lg__chip--sponsored">Sponsored</span>' in body
        for heading in ("Date", "Type", "Line", "Sponsor / vendor", "Amount", "Note", "Receipt"):
            assert f">{heading}</th>" in body

    @pytest.mark.parametrize("show", [K.SPONSORED, K.PAID_APPROVAL, K.PAID_OTHER, K.REFUND])
    def it_filters_by_kind(hub, kegs, lee_client, show):
        entries = {kind: LedgerEntryFactory(hub=hub, line=kegs, kind=kind) for kind in K.values}

        body = lee_client.get(_page(hub, show)).content.decode()

        for kind, entry in entries.items():
            assert (f'data-hq-lg-entry="{entry.pk}"' in body) == (kind == show)
        assert f'data-hq-lg-filter="{show}" aria-current="true"' in body

    def it_shows_every_entry_for_a_filter_it_does_not_know(hub, kegs, lee_client):
        LedgerEntryFactory.create_batch(2, hub=hub, line=kegs)

        body = lee_client.get(_page(hub, "nonsense")).content.decode()

        assert body.count("data-hq-lg-entry=") == 2
        assert 'data-hq-lg-filter="all" aria-current="true"' in body

    def it_says_so_when_the_ledger_or_the_filter_is_empty(hub, kegs, lee_client):
        assert "No entries yet. Log your first sponsor" in lee_client.get(_page(hub)).content.decode()
        LedgerEntryFactory(hub=hub, line=kegs)

        assert "No entries match this filter." in lee_client.get(_page(hub, K.REFUND)).content.decode()

    def it_marks_what_needs_a_receipt_and_links_what_has_one(hub, kegs, lee_client):
        needs = LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_APPROVAL, source="Spec Needs")
        pdf = LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_OTHER, source="Spec Pdf", receipt=_pdf())
        photo = LedgerEntryFactory(hub=hub, line=kegs, kind=K.REFUND, source="Spec Photo", receipt=photo_upload())
        gift = LedgerEntryFactory(hub=hub, line=kegs, kind=K.SPONSORED, source="Spec Gift")

        body = lee_client.get(_page(hub)).content.decode()

        def _cell(entry: LedgerEntry) -> str:
            row = body.split(f'data-hq-lg-entry="{entry.pk}"', 1)[1]
            return row.split("data-hq-lg-receipt-cell>", 1)[1].split("</td>", 1)[0]

        assert "data-hq-lg-needs-receipt>Needs receipt" in _cell(needs)
        assert f'action="{_url("ledger_receipt", hub, needs.pk)}"' in _cell(needs)
        assert f'id="id_receipt-{needs.pk}-receipt"' in _cell(needs)
        assert "PDF on file" in _cell(pdf)
        assert f'href="{pdf.receipt.url}"' in _cell(pdf)
        assert f'<img src="{photo.receipt.url}"' in _cell(photo)
        assert "Needs receipt" not in _cell(gift) and "<img" not in _cell(gift)
        assert 'aria-label="Delete Spec Needs on' in body
        assert f"delete-entry-{needs.pk}" in body

    def it_runs_a_fixed_number_of_queries_however_many_lines_and_entries(hub, kegs, lee_client):
        def _count() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert lee_client.get(_page(hub)).status_code == 200
            return len(queries)

        LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_APPROVAL)
        _count()  # the first request warms the session and the caches
        few = _count()
        for kind in K.values:
            line = BudgetLineFactory(hub=hub)
            LedgerEntryFactory.create_batch(2, hub=hub, line=line, kind=kind)

        assert _count() == few


def describe_adding_a_receipt():
    def it_attaches_a_receipt_from_the_row_and_lands_back_with_the_filter(hub, kegs, lee_client):
        entry = LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_APPROVAL)

        response = lee_client.post(
            _url("ledger_receipt", hub, entry.pk), {f"receipt-{entry.pk}-receipt": _pdf(), "filter": K.PAID_APPROVAL}
        )

        entry.refresh_from_db()
        assert response["Location"] == _page(hub, K.PAID_APPROVAL)
        assert entry.receipt.name.endswith(".pdf")
        assert "Receipt attached." in lee_client.get(_page(hub)).content.decode()

    def it_deletes_the_replaced_file_once_the_save_commits(hub, kegs, django_capture_on_commit_callbacks):
        entry = LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_APPROVAL, receipt=_pdf())
        old = entry.receipt.name

        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            services.attach_receipt(entry, _pdf("new.pdf"))
        assert default_storage.exists(old)  # still there until the commit
        for callback in callbacks:
            callback()

        assert not default_storage.exists(old)
        assert default_storage.exists(entry.receipt.name)

    def it_refuses_a_receipt_on_a_sponsored_gift_with_a_message(hub, kegs, lee_client):
        gift = LedgerEntryFactory(hub=hub, line=kegs, kind=K.SPONSORED)

        response = lee_client.post(
            _url("ledger_receipt", hub, gift.pk), {f"receipt-{gift.pk}-receipt": _pdf()}, follow=True
        )

        gift.refresh_from_db()
        assert not gift.receipt
        assert RECEIPT_NOT_TAKEN in response.content.decode()
        with pytest.raises(services.ReceiptNotTaken):
            services.attach_receipt(gift, _pdf())

    def it_renders_a_refused_file_on_its_row_open(hub, kegs, lee_client):
        entry = LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_OTHER)
        other = LedgerEntryFactory(hub=hub, line=kegs, kind=K.REFUND)
        junk = SimpleUploadedFile("r.png", b"junk", content_type="image/png")

        response = lee_client.post(_url("ledger_receipt", hub, entry.pk), {f"receipt-{entry.pk}-receipt": junk})

        body = response.content.decode()
        row = body.split(f'data-hq-lg-entry="{entry.pk}"', 1)[1].split("</tr>", 1)[0]
        other_row = body.split(f'data-hq-lg-entry="{other.pk}"', 1)[1].split("</tr>", 1)[0]
        assert response.status_code == 200
        assert RECEIPT_TYPE_ERROR in row
        assert "open data-hq-lg-attach" in row
        assert "open data-hq-lg-attach" not in other_row
        entry.refresh_from_db()
        assert not entry.receipt


def describe_deleting_an_entry():
    def it_deletes_the_entry_and_then_its_receipt(hub, kegs, lee_client, django_capture_on_commit_callbacks):
        entry = LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_OTHER, source="Spec Vendor", receipt=_pdf())
        name = entry.receipt.name

        with django_capture_on_commit_callbacks(execute=True):
            response = lee_client.post(_url("ledger_entry_delete", hub, entry.pk), {"filter": K.PAID_OTHER})

        assert response["Location"] == _page(hub, K.PAID_OTHER)
        assert not LedgerEntry.objects.exists()
        assert not default_storage.exists(name)
        assert "Spec Vendor is out of the ledger." in lee_client.get(_page(hub)).content.decode()


def describe_the_csv():
    def it_streams_lees_columns_oldest_first_with_refunds_negative(hub, kegs, lee_client):
        LedgerSettingsFactory(hub=hub, approved_cents=120_000)
        decor = BudgetLineFactory(hub=hub, name="=Lights", category=C.DECOR)
        LedgerEntryFactory(
            hub=hub, line=kegs, kind=K.REFUND, amount_cents=4_050, date=date(2026, 11, 3), source="Keg return"
        )
        LedgerEntryFactory(
            hub=hub,
            line=decor,
            kind=K.PAID_APPROVAL,
            amount_cents=1_000,
            date=date(2026, 10, 1),
            source="-Shop",
            note="+1",
        )
        LedgerEntryFactory(
            hub=hub, line=kegs, kind=K.SPONSORED, amount_cents=25_000, date=date(2026, 10, 15), source="Cascade"
        )
        LedgerEntryFactory(kind=K.SPONSORED, source="Elsewhere")

        response = lee_client.get(_url("ledger_csv", hub))

        rows = list(csv.reader(io.StringIO(b"".join(response.streaming_content).decode())))
        assert response["Content-Type"] == "text/csv"
        assert response["Content-Disposition"].startswith(f'attachment; filename="{hub.slug}-ledger-')
        assert rows == [
            LEDGER_CSV_HEADERS,
            ["2026-10-01", "From $1,200", "'=Lights", "Decor", "'-Shop", "10.00", "'+1"],
            ["2026-10-15", "Sponsored", "Kegs", "Drinks", "Cascade", "250.00", ""],
            ["2026-11-03", "Refund", "Kegs", "Drinks", "Keg return", "-40.50", ""],
        ]
        assert LEDGER_CSV_HEADERS == ["Date", "Type", "Budget line", "Category", "Sponsor or vendor", "Amount", "Note"]


def describe_the_receipts_report():
    def it_lists_every_purchase_and_refund_oldest_first_with_its_receipt(hub, kegs, lee_client):
        LedgerSettingsFactory(hub=hub, approved_note="Oct 14 vote")
        photo = LedgerEntryFactory(
            hub=hub,
            line=kegs,
            kind=K.PAID_APPROVAL,
            amount_cents=40_000,
            date=date(2026, 10, 2),
            receipt=photo_upload(),
            source="Spec Photo",
        )
        pdf = LedgerEntryFactory(
            hub=hub,
            line=kegs,
            kind=K.REFUND,
            amount_cents=5_000,
            date=date(2026, 11, 2),
            receipt=_pdf(),
            source="Spec Refund",
        )
        missing = LedgerEntryFactory(
            hub=hub, line=kegs, kind=K.PAID_OTHER, amount_cents=7_500, date=date(2026, 10, 20), source="Spec Missing"
        )
        gift = LedgerEntryFactory(hub=hub, line=kegs, kind=K.SPONSORED, source="Spec Gift")

        body = lee_client.get(_url("ledger_receipts", hub)).content.decode()

        positions = [body.index(f'data-hq-rc-entry="{entry.pk}"') for entry in (photo, missing, pdf)]
        assert positions == sorted(positions)
        assert f'data-hq-rc-entry="{gift.pk}"' not in body
        assert f'src="{photo.receipt.url}"' in body
        assert f'href="{pdf.receipt.url}"' in body and "data-hq-rc-pdf" in body
        assert "No receipt on file" in body.split(f'data-hq-rc-entry="{missing.pk}"', 1)[1]
        assert "Board approval (Oct 14 vote): <strong>$900</strong>" in body
        assert "Spent from approval: <strong>$400</strong> · Refunds back: <strong>$50</strong>" in body
        assert "Net used: <strong>$350</strong> · Left: <strong>$550</strong>" in body
        assert "Paid from other PLM funds: <strong>$75</strong>" in body
        assert "data-hq-rc-count>3 transactions, 1 missing a receipt</p>" in body
        assert "−$50" in body
        assert "data-hq-rc-print" in body
        assert f"Prepared {timezone.localdate():%b}" in body

    def it_says_so_when_there_is_nothing_and_when_every_receipt_is_in(hub, kegs, lee_client):
        assert "No purchases logged yet." in lee_client.get(_url("ledger_receipts", hub)).content.decode()
        assert "data-hq-rc-count>0 transactions</p>" in lee_client.get(_url("ledger_receipts", hub)).content.decode()
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_APPROVAL, receipt=_pdf())

        body = lee_client.get(_url("ledger_receipts", hub)).content.decode()

        assert "data-hq-rc-count>1 transaction, all with receipts</p>" in body
        assert "Refunds back" not in body
        assert "data-hq-rc-other" not in body

    def it_is_linked_from_the_ledger_in_lees_words(hub, lee_client):
        body = lee_client.get(_page(hub)).content.decode()

        assert (
            f'href="{_url("ledger_receipts", hub)}" class="pl-hq-lg__btn pl-hq-lg__btn--ghost" data-hq-lg-receipts-link>Download receipts report</a>'
            in body
        )
        assert "Sushuma" not in body

    def it_runs_a_fixed_number_of_queries_however_many_receipts(hub, kegs, lee_client):
        def _count() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert lee_client.get(_url("ledger_receipts", hub)).status_code == 200
            return len(queries)

        LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_APPROVAL)
        _count()
        few = _count()
        for _ in range(3):
            LedgerEntryFactory(hub=hub, line=BudgetLineFactory(hub=hub), kind=K.REFUND, receipt=_pdf())

        assert _count() == few


def describe_the_model():
    def it_reads_as_its_parts(hub, kegs):
        entry = LedgerEntryFactory(hub=hub, line=kegs, kind=K.REFUND, source="Keg return", amount_cents=4_000)
        found = LedgerSettingsFactory(hub=hub)

        assert str(entry) == "Keg return (Refund back to $900)"
        assert (entry.signed_cents, entry.amount_display) == (-4_000, "−$40")
        assert (entry.takes_receipt, entry.needs_receipt) == (True, True)
        assert str(kegs) == "Kegs"
        assert kegs.budget_display == "$600"
        assert str(found) == f"Ledger settings for {hub.title}"
        assert (found.approval_words, found.ipa_cents, found.ticket_cents, found.last_year_bar_cents) == (
            "$900",
            600,
            2_500,
            50_000,
        )

    def it_names_a_receipt_at_random_keeping_its_type():
        first = receipt_upload_to(None, "IMG_1234.JPG")  # type: ignore[arg-type]
        second = receipt_upload_to(None, "IMG_1234.JPG")  # type: ignore[arg-type]

        assert first.startswith("hq/receipts/") and first.endswith(".jpg")
        assert first != second
        assert "IMG_1234" not in first

    def it_holds_the_amount_above_zero_in_the_database(hub, kegs):
        with pytest.raises(IntegrityError), transaction.atomic():
            LedgerEntryFactory(hub=hub, line=kegs, amount_cents=0)

    def it_holds_a_sponsored_gift_to_no_receipt_in_the_database(hub, kegs):
        with pytest.raises(IntegrityError), transaction.atomic():
            LedgerEntryFactory(hub=hub, line=kegs, kind=K.SPONSORED, receipt=_pdf())

    def it_deletes_an_entry_with_no_receipt_and_touches_no_file(hub, kegs, django_capture_on_commit_callbacks):
        entry = LedgerEntryFactory(hub=hub, line=kegs)

        with django_capture_on_commit_callbacks() as callbacks:
            entry.delete()

        assert callbacks == []
        assert not LedgerEntry.objects.exists()

    def it_lets_the_hub_go_with_its_lines_and_entries(hub, kegs):
        LedgerEntryFactory(hub=hub, line=kegs)
        LedgerSettingsFactory(hub=hub)

        hub.delete()

        assert not (BudgetLine.objects.exists() or LedgerEntry.objects.exists() or LedgerSettings.objects.exists())

    def it_takes_the_receipts_out_of_storage_when_the_hub_goes(hub, kegs, django_capture_on_commit_callbacks):
        entry = LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_OTHER, receipt=_pdf())
        name = entry.receipt.name

        with django_capture_on_commit_callbacks() as pending:
            hub.delete()

        assert not LedgerEntry.objects.exists()
        assert default_storage.exists(name)  # still there until the delete has committed
        for callback in pending:
            callback()
        assert not default_storage.exists(name)

    def it_keeps_a_receipt_another_entry_still_points_at(hub, kegs, django_capture_on_commit_callbacks):
        first = LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_OTHER, receipt=_pdf())
        second = LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_OTHER)
        LedgerEntry.objects.filter(pk=second.pk).update(receipt=first.receipt.name)

        with django_capture_on_commit_callbacks(execute=True):
            first.delete()

        second.refresh_from_db()
        assert default_storage.exists(second.receipt.name)
