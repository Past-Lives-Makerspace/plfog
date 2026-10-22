"""The PL Ledger's money in (Track D, part 6b): Drink sales (the gates, +1 taps and Undo last, the batch with its
defaults and Lee's refusals, Set drink prices with the stale save check, the tiles with the bar's cost from the Drinks
lines and Lee's break-even, Delete and the CSV with its totals row), the Wine Wall section (read from the wall, Lee's
ticket math and revenue at goal, the tickets and ticket prices forms), the donors' Thanked toggle and the Under $25 flag,
the event cash Raised counts by itself (its value and query count, the adoption of a typed in Raised and the sponsor
meter preferring it), and the pages' query counts as sales grow."""

from __future__ import annotations

import csv
import io
import json
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from hq import services
from hq.exports import BAR_SALES_CSV_HEADERS
from hq.forms import BAR_TOTAL_TOO_BIG, PRICE_MAX_DOLLARS, TICKETS_MAX
from hq.holiday_bizarre import HUB
from hq.models import (
    BAR_QTY_MAX,
    BarSale,
    BudgetLine,
    HubCard,
    HubGoal,
    LedgerEntry,
    LedgerSettings,
    WineDonorThanks,
)
from hub.view_as import ROLE_MEMBER
from tests.hq.conftest import committed_once_we_wait, signed_in
from tests.hq.factories import (
    BarSaleFactory,
    BudgetLineFactory,
    EventHubFactory,
    HubCardFactory,
    HubGoalFactory,
    LedgerEntryFactory,
    LedgerSettingsFactory,
    SponsorFactory,
    WineBottleFactory,
    WineDonorThanksFactory,
)
from tests.membership.factories import CommunityEventFactory

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
K = LedgerEntry.Kind
C = BudgetLine.Category
D = BarSale.Drink
P = BarSale.Pay


def _page(hub: Any) -> str:
    return reverse("hq:ledger", args=[hub.slug])


def _url(name: str, hub: Any, *args: Any) -> str:
    return reverse(f"hq:{name}", args=[hub.slug, *args])


def _toast(response: Any) -> dict[str, str]:
    return json.loads(response["HX-Trigger"])["showToast"]


def _found(hub: Any) -> LedgerSettings:
    return services.ledger_settings(hub)


def _quick(drink: str = D.IPA, pay: str = P.CARD) -> dict[str, str]:
    return {"quick-drink": drink, "quick-pay": pay}


def _batch(**fields: Any) -> dict[str, Any]:
    values: dict[str, Any] = {"drink": D.IPA, "qty": "1", "total": "", "pay": P.CARD, "note": ""}
    values.update(fields)
    return {f"batch-{key}": value for key, value in values.items()}


def _seen(prefix: str, found: LedgerSettings, names: tuple[str, ...]) -> dict[str, Any]:
    return {f"{prefix}-seen_{name}": getattr(found, name) for name in names}


PRICE_FIGURES = ("ipa_cents", "pils_cents", "wine_cents", "na_cents", "last_year_bar_cents")


def _prices(found: LedgerSettings, **fields: Any) -> dict[str, Any]:
    values: dict[str, Any] = {"ipa": "7", "pils": "6.50", "wine": "9", "na": "2", "last_year": "650"}
    values.update(fields)
    return {**{f"prices-{key}": value for key, value in values.items()}, **_seen("prices", found, PRICE_FIGURES)}


def _tickets(found: LedgerSettings, singles: Any = 3, pairs: Any = 2) -> dict[str, Any]:
    return {
        "tickets-singles_sold": singles,
        "tickets-pairs_sold": pairs,
        **_seen("tickets", found, ("singles_sold", "pairs_sold")),
    }


def _approval(found: LedgerSettings, approved: str = "950", note: str | None = None) -> dict[str, Any]:
    """The approval form as it renders for ``found``, the amount changed (and the note, when given)."""
    return {
        "approved": approved,
        "approved_note": found.approved_note if note is None else note,
        "seen_approved_cents": found.approved_cents,
        "seen_approved_note": found.approved_note,
    }


def _ticket_prices(found: LedgerSettings, ticket: Any = "20", pair: Any = "35") -> dict[str, Any]:
    return {
        "ticket-prices-ticket": ticket,
        "ticket-prices-pair": pair,
        **_seen("ticket-prices", found, ("ticket_cents", "pair_cents")),
    }


@pytest.fixture
def lee(make_member):
    return make_member(preferred_name="Lee Ashworth")


@pytest.fixture
def card(open_hub, lee) -> HubCard:
    """The ledger door with Lee on its Team. A Team-only door (HQ card sharing): Admins only is its one audience."""
    return HubCardFactory(
        hub=open_hub, kind=HubCard.Kind.LEDGER, title="PL Ledger", audience=HubCard.Audience.ADMINS, team=[lee]
    )


@pytest.fixture
def hub(card):
    return card.hub


@pytest.fixture
def lee_client(lee) -> Client:
    return signed_in(lee)


@pytest.fixture
def wall(hub, lee) -> HubCard:
    """The wine wall door on the same hub, open to everyone, Lee organising it."""
    return HubCardFactory(
        hub=hub, kind=HubCard.Kind.WINE_WALL, title="Log a wine bottle", audience=HubCard.Audience.PUBLIC, team=[lee]
    )


@pytest.fixture
def kegs(hub) -> BudgetLine:
    return BudgetLineFactory(hub=hub, name="Kegs", category=C.DRINKS, budget_cents=60_000)


def _bar_cost(hub: Any, line: BudgetLine, cents: int) -> None:
    LedgerEntryFactory(hub=hub, line=line, kind=K.PAID_APPROVAL, amount_cents=cents)


def describe_the_sections():
    def it_puts_drink_sales_then_the_wine_wall_after_the_receipts(hub, lee_client):
        body = lee_client.get(_page(hub)).content.decode()

        order = ["data-hq-lg-receipts", "data-hq-lg-bar", "data-hq-lg-wine"]
        assert [body.index(marker) for marker in order] == sorted(body.index(marker) for marker in order)
        assert '<h2 class="pl-hq-lg__h2" id="hq-lg-bar-title">Drink Sales</h2>' in body
        assert "Bar sales at the event, against what the bar cost" in body
        assert "Separate from the $900 drinks budget · Bottles are gifts, not tax-deductible" in body
        assert "No drink sales yet. Use the quick-add buttons on the night, or log a batch." in body
        assert "data-hq-lg-bar-csv" not in body  # no CSV until there is a sale

    def it_gives_each_drink_a_large_plus_one_at_lees_prices_with_distinct_labels(hub, lee_client):
        body = lee_client.get(_page(hub)).content.decode()

        for drink, words, price in (("ipa", "IPA", "$6"), ("pils", "Pilsner", "$6"), ("wine", "Wine", "$8")):
            assert f'aria-label="Add one {words} at {price}" data-hq-lg-quick="{drink}"' in body
        assert 'aria-label="Add one N/A drink at $3" data-hq-lg-quick="na"' in body
        assert 'data-hq-lg-quick="mixed"' not in body
        assert 'name="quick-pay" value="rsvp"' in body
        assert "$5 RSVP credit" in body
        assert 'name="quick-pay" value="card"' in body and "checked" in body


def describe_who_may_use_it():
    def it_refuses_every_write_and_the_csv_to_anyone_but_an_organiser_and_leaks_nothing(hub, wall, member_client, kegs):
        sale = BarSaleFactory(hub=hub, note="Spec secret bar note", created_by=None)
        found = _found(hub)
        WineBottleFactory(hub=hub, giver_name="Spec Secret Giver")

        for client in (member_client, Client()):
            for name, data in (
                ("ledger_bar_quick", _quick()),
                ("ledger_bar_undo", {}),
                ("ledger_bar_batch", _batch(qty="3")),
                ("ledger_bar_prices", _prices(found)),
                ("ledger_tickets", _tickets(found)),
                ("ledger_ticket_prices", _ticket_prices(found)),
            ):
                response = client.post(_url(name, hub), data)
                assert response.status_code == 403, name
                assert "Spec secret bar note" not in response.content.decode(), name
            assert client.post(_url("ledger_bar_delete", hub, sale.pk)).status_code == 403
            response = client.get(_url("ledger_bar_csv", hub))
            assert response.status_code == 403
            assert b"Spec secret bar note" not in response.content
            # A Team-only door: off its Team the page is a closed door, a 404.
            assert client.get(_page(hub)).status_code == 404
        # The donors' toggle is the wall's organisers' (Lee), not the ledger's.
        assert (
            member_client.post(
                _url("wine_donor_thanked", hub), {"giver": "Spec Secret Giver", "thanked": "1"}
            ).status_code
            == 403
        )
        assert list(BarSale.objects.values_list("pk", flat=True)) == [sale.pk]
        assert not LedgerSettings.objects.exists()
        assert not WineDonorThanks.objects.exists()

    def it_is_a_404_for_another_hubs_sale_and_an_unknown_donor(hub, wall, lee_client):
        other = BarSaleFactory(hub=EventHubFactory())
        WineBottleFactory(hub=EventHubFactory(), giver_name="Elsewhere Donor")

        assert lee_client.post(_url("ledger_bar_delete", hub, other.pk)).status_code == 404
        assert (
            lee_client.post(_url("wine_donor_thanked", hub), {"giver": "Elsewhere Donor", "thanked": "1"}).status_code
            == 404
        )
        assert lee_client.post(_url("wine_donor_thanked", hub), {"thanked": "1"}).status_code == 404
        assert BarSale.objects.filter(pk=other.pk).exists()
        assert not WineDonorThanks.objects.exists()

    def it_is_a_404_on_a_hub_with_no_ledger_door(admin_client):
        bare = EventHubFactory()

        assert admin_client.post(_url("ledger_bar_quick", bare), _quick()).status_code == 404
        assert admin_client.get(_url("ledger_bar_csv", bare)).status_code == 404
        assert admin_client.post(_url("wine_donor_thanked", bare), {"giver": "Amy"}).status_code == 404
        assert not BarSale.objects.exists()

    def it_takes_posts_only(hub, lee_client):
        sale = BarSaleFactory(hub=hub)
        for url in (
            _url("ledger_bar_quick", hub),
            _url("ledger_bar_undo", hub),
            _url("ledger_bar_batch", hub),
            _url("ledger_bar_prices", hub),
            _url("ledger_bar_delete", hub, sale.pk),
            _url("ledger_tickets", hub),
            _url("ledger_ticket_prices", hub),
            _url("wine_donor_thanked", hub),
        ):
            assert lee_client.get(url).status_code == 405, url

    def it_lets_a_previewing_admin_act_on_their_actual_role(hub, admin_client, admin):
        session = admin_client.session
        session["view_as_role"] = ROLE_MEMBER
        session.save()

        assert admin_client.get(_page(hub)).status_code == 404
        assert admin_client.post(_url("ledger_bar_quick", hub), _quick()).status_code == 302
        assert BarSale.objects.get().created_by == admin


def describe_quick_add():
    def it_records_one_drink_at_the_price_and_answers_out_of_band(hub, lee, lee_client):
        LedgerSettingsFactory(hub=hub, wine_cents=850)

        response = lee_client.post(_url("ledger_bar_quick", hub), _quick(D.WINE, P.RSVP), **HTMX)

        sale = BarSale.objects.get()
        assert (sale.drink, sale.qty, sale.amount_cents, sale.pay, sale.source) == (D.WINE, 1, 850, P.RSVP, "quick")
        assert sale.created_by == lee
        assert _toast(response) == {"message": "Added 1 Wine ($5 RSVP credit).", "type": "success"}
        body = response.content.decode()
        parts = (
            "hq-lg-bar-tiles",
            "hq-lg-bar-buttons",
            "hq-lg-bar-table",
            "hq-lg-tally",
            "hq-lg-goals",
            "hq-lg-board-figures",
            "hq-lg-staff-figures",
        )
        for part in parts:
            assert f'id="{part}"' in body
        assert body.count('hx-swap-oob="true"') == len(parts)
        assert body.count("<template>") == len(parts)
        assert "data-hq-lg-quick-form" not in body  # Paid by is never swapped, so it keeps its choice
        assert 'data-hq-lg-quick="wine"' in body and "data-hq-lg-quick-sold>1<" in body

    def it_sends_the_board_paragraph_out_of_band_with_the_new_sales(hub, kegs, lee_client):
        LedgerEntryFactory(hub=hub, line=kegs)  # the paragraph speaks once the ledger has an entry
        BarSaleFactory(hub=hub, qty=2, amount_cents=1_200)

        body = lee_client.post(_url("ledger_bar_quick", hub), _quick(), **HTMX).content.decode()
        undone = lee_client.post(_url("ledger_bar_undo", hub), **HTMX).content.decode()

        board = body.split('id="hq-lg-board-figures"', 1)[1]
        assert board.lstrip().startswith('data-hq-lg-board-figures hx-swap-oob="true"')
        assert "x-data=" in board.split(">", 1)[0]  # its own Alpine component, so Copy starts afresh on the new text
        assert board.count("Drink sales: 3 drinks sold for $18.") == 2  # the paragraph and the box Copy copies
        assert "Drink sales: 2 drinks sold for $12." in undone.split('id="hq-lg-board-figures"', 1)[1]
        assert "data-hq-lg-approval" not in body  # a half typed approval is never reset by a tap

    def it_disables_every_button_of_a_tap_and_undo_while_in_flight(hub, lee_client):
        body = lee_client.get(_page(hub)).content.decode()

        assert (
            'hx-swap="none" hx-disabled-elt="[data-hq-lg-quick]" class="pl-hq-lg__form" data-hq-lg-quick-form' in body
        )
        assert 'hx-swap="none" hx-disabled-elt="find button" class="pl-hq-lg__undo" data-hq-lg-undo-form' in body
        assert body.count('data-hq-lg-quick="') == 4  # the selector reaches all four +1 buttons
        answer = lee_client.post(_url("ledger_bar_quick", hub), _quick(), **HTMX).content.decode()
        assert "disabled" not in answer  # the buttons come back enabled

    def it_lands_a_plain_post_back_on_the_section(hub, lee_client):
        response = lee_client.post(_url("ledger_bar_quick", hub), _quick(D.NA, P.CASH), follow=True)

        assert response.redirect_chain[-1][0] == f"{_page(hub)}#hq-lg-bar"
        assert "Added 1 N/A drink (Cash)." in [str(message) for message in response.context["messages"]]
        assert BarSale.objects.get().amount_cents == 300

    @pytest.mark.parametrize("data", [_quick(D.MIXED), _quick(pay="venmo"), {}])
    def it_refuses_a_drink_with_no_price_or_an_unknown_way_to_pay(hub, lee_client, data):
        fragment = lee_client.post(_url("ledger_bar_quick", hub), data, **HTMX)
        plain = lee_client.post(_url("ledger_bar_quick", hub), data, follow=True)

        assert fragment.content == b""
        assert _toast(fragment) == {"message": "Choose a drink and how it was paid.", "type": "error"}
        assert "Choose a drink and how it was paid." in [str(message) for message in plain.context["messages"]]
        assert not BarSale.objects.exists()

    def it_charges_the_price_of_the_moment(hub, lee_client):
        lee_client.post(_url("ledger_bar_quick", hub), _quick(), **HTMX)
        LedgerSettingsFactory(hub=hub, ipa_cents=750)
        lee_client.post(_url("ledger_bar_quick", hub), _quick(), **HTMX)

        assert sorted(BarSale.objects.values_list("amount_cents", flat=True)) == [600, 750]


def describe_undo_last():
    def it_takes_back_only_your_own_newest_quick_sale(hub, lee, lee_client, make_member):
        kate = make_member(preferred_name="Kate")
        older = BarSaleFactory(hub=hub, created_by=lee, drink=D.PILS)
        newest = BarSaleFactory(hub=hub, created_by=lee, drink=D.WINE, pay=P.CASH, amount_cents=800)
        batch = BarSaleFactory(hub=hub, created_by=lee, source=BarSale.Source.BATCH, qty=4, amount_cents=2_400)
        theirs = BarSaleFactory(hub=hub, created_by=kate)
        elsewhere = BarSaleFactory(created_by=lee)

        response = lee_client.post(_url("ledger_bar_undo", hub), **HTMX)

        assert _toast(response) == {"message": "Took back 1 Wine (Cash).", "type": "success"}
        assert set(BarSale.objects.values_list("pk", flat=True)) == {older.pk, batch.pk, theirs.pk, elsewhere.pk}
        assert f'data-hq-lg-bar-sale="{newest.pk}"' not in response.content.decode()
        lee_client.post(_url("ledger_bar_undo", hub), **HTMX)
        assert not BarSale.objects.filter(pk=older.pk).exists()

    def it_says_so_when_there_is_nothing_of_yours(hub, lee_client, make_member):
        BarSaleFactory(hub=hub, created_by=make_member())

        fragment = lee_client.post(_url("ledger_bar_undo", hub), **HTMX)
        plain = lee_client.post(_url("ledger_bar_undo", hub), follow=True)

        assert _toast(fragment) == {"message": "Nothing of yours to undo.", "type": "info"}
        assert "Nothing of yours to undo." in [str(message) for message in plain.context["messages"]]
        assert BarSale.objects.count() == 1

    def it_lands_a_plain_undo_back_on_the_section(hub, lee, lee_client):
        BarSaleFactory(hub=hub, created_by=lee)

        response = lee_client.post(_url("ledger_bar_undo", hub), follow=True)

        assert response.redirect_chain[-1][0] == f"{_page(hub)}#hq-lg-bar"
        assert "Took back 1 IPA (Card)." in [str(message) for message in response.context["messages"]]

    def it_has_nothing_to_undo_for_no_member():
        hub = EventHubFactory()
        BarSaleFactory(hub=hub)

        assert services.undo_quick_sale(hub, None) is None
        assert BarSale.objects.count() == 1


def describe_log_a_batch():
    def it_defaults_a_blank_total_to_the_price_times_how_many(hub, lee, lee_client):
        LedgerSettingsFactory(hub=hub, pils_cents=650)

        response = lee_client.post(
            _url("ledger_bar_batch", hub),
            _batch(drink=D.PILS, qty="12", pay=P.CASH, note="6 to 8 PM shift, Augustin"),
            **HTMX,
        )

        sale = BarSale.objects.get()
        assert (sale.drink, sale.qty, sale.amount_cents, sale.pay, sale.source) == (D.PILS, 12, 7_800, P.CASH, "batch")
        assert (sale.note, sale.created_by) == ("6 to 8 PM shift, Augustin", lee)
        assert _toast(response)["message"] == "Sales added."
        body = response.content.decode()
        assert body.lstrip().startswith("<details")
        assert 'id="hq-lg-bar-batch" open' in body
        assert '<option value="pils" selected>' in body  # the drink and Paid by stay for the next batch
        assert '<option value="cash" selected>' in body
        assert 'id="hq-lg-bar-tiles" data-hq-lg-bar-tiles hx-swap-oob="true"' in body
        assert 'id="hq-lg-bar-table" data-hq-lg-bar-table hx-swap-oob="true"' in body
        assert 'id="hq-lg-board-figures" data-hq-lg-board-figures hx-swap-oob="true"' in body
        assert "6 to 8 PM shift, Augustin" in body

    def it_takes_a_total_collected_over_the_price(hub, lee_client):
        lee_client.post(_url("ledger_bar_batch", hub), _batch(qty="3", total="20.50"), **HTMX)

        assert BarSale.objects.get().amount_cents == 2_050

    def it_takes_the_tip_jar_as_a_total_with_no_drinks(hub, lee_client):
        lee_client.post(_url("ledger_bar_batch", hub), _batch(drink=D.MIXED, qty="0", total="37"), **HTMX)

        sale = BarSale.objects.get()
        assert (sale.drink, sale.qty, sale.amount_cents) == (D.MIXED, 0, 3_700)

    def it_needs_a_total_for_mixed_sales_in_lees_words(hub, lee_client):
        response = lee_client.post(_url("ledger_bar_batch", hub), _batch(drink=D.MIXED, qty="5"), **HTMX)

        body = response.content.decode()
        assert services.BAR_MIXED_NEEDS_TOTAL in body
        assert 'id="hq-lg-bar-batch" open' in body
        assert "HX-Trigger" not in response
        assert not BarSale.objects.exists()

    def it_refuses_a_batch_with_neither_drinks_nor_money(hub, lee_client):
        response = lee_client.post(_url("ledger_bar_batch", hub), _batch(qty="0", total="0"), **HTMX)

        assert services.BAR_NOTHING_ENTERED in response.content.decode()
        assert not BarSale.objects.exists()

    @pytest.mark.parametrize(
        ("fields", "error"),
        [
            ({"qty": str(BAR_QTY_MAX + 1)}, "Ensure this value is less than or equal to 10000."),
            ({"qty": "-1"}, "Ensure this value is greater than or equal to 0."),
            ({"total": "1000000.01"}, "Ensure this value is less than or equal to 1000000."),
            ({"total": "-5"}, "Ensure this value is greater than or equal to 0."),
            ({"drink": "cider"}, "Select a valid choice."),
        ],
    )
    def it_holds_the_figures_to_their_caps(hub, lee_client, fields, error):
        response = lee_client.post(_url("ledger_bar_batch", hub), _batch(**fields), **HTMX)

        assert error in response.content.decode()
        assert not BarSale.objects.exists()

    def it_refuses_a_price_times_count_over_the_cap(hub, lee_client):
        LedgerSettingsFactory(hub=hub, ipa_cents=PRICE_MAX_DOLLARS * 100)

        response = lee_client.post(_url("ledger_bar_batch", hub), _batch(qty=str(BAR_QTY_MAX)), **HTMX)

        assert BAR_TOTAL_TOO_BIG in response.content.decode()
        assert not BarSale.objects.exists()

    def it_renders_a_refused_plain_post_on_the_page_open_and_redirects_a_saved_one(hub, lee_client):
        refused = lee_client.post(_url("ledger_bar_batch", hub), _batch(drink=D.MIXED, qty="2"))
        saved = lee_client.post(_url("ledger_bar_batch", hub), _batch(qty="2"))

        body = refused.content.decode()
        assert refused.status_code == 200
        assert "data-hq-ledger" in body and services.BAR_MIXED_NEEDS_TOTAL in body
        assert 'id="hq-lg-bar-batch" open' in body
        assert saved.status_code == 302 and saved["Location"] == f"{_page(hub)}#hq-lg-bar"
        assert BarSale.objects.get().amount_cents == 1_200

    def it_names_a_sale_by_its_drinks_and_amount():
        assert str(BarSaleFactory(drink=D.PILS, qty=3, amount_cents=1_950)) == "3 Pilsner ($19.50)"

    def it_never_stores_a_sale_with_neither_drinks_nor_money():
        with pytest.raises(IntegrityError), transaction.atomic():
            BarSaleFactory(qty=0, amount_cents=0)


def describe_set_drink_prices():
    def it_saves_the_four_prices_and_last_year_and_sends_the_buttons_and_tiles_out_of_band(hub, admin_client):
        LedgerSettingsFactory(hub=hub, approved_cents=120_000, singles_sold=4)
        found = _found(hub)

        response = admin_client.post(_url("ledger_bar_prices", hub), _prices(found), **HTMX)

        found.refresh_from_db()
        assert (found.ipa_cents, found.pils_cents, found.wine_cents, found.na_cents) == (700, 650, 900, 200)
        assert found.last_year_bar_cents == 65_000
        assert (found.approved_cents, found.singles_sold) == (120_000, 4)  # nothing else is written
        assert _toast(response)["message"] == "Prices saved."
        body = response.content.decode()
        assert 'aria-label="Add one IPA at $7" data-hq-lg-quick="ipa"' in body
        assert 'id="hq-lg-bar-buttons" data-hq-lg-bar-buttons hx-swap-oob="true"' in body
        assert "2025: about $650" in body
        assert 'id="hq-lg-bar-prices"  data-hq-lg-prices' in body  # closed once saved

    def it_makes_the_settings_on_a_first_save(hub, admin_client):
        response = admin_client.post(_url("ledger_bar_prices", hub), _prices(_found(hub)))

        assert response["Location"] == f"{_page(hub)}#hq-lg-bar"
        assert LedgerSettings.objects.get(hub=hub).ipa_cents == 700

    def it_refuses_a_save_from_an_older_page_and_shows_what_is_saved(hub, admin_client):
        old = _found(hub)
        LedgerSettingsFactory(hub=hub, ipa_cents=800)

        fragment = admin_client.post(_url("ledger_bar_prices", hub), _prices(old), **HTMX)
        plain = admin_client.post(_url("ledger_bar_prices", hub), _prices(old))

        assert LedgerSettings.objects.get(hub=hub).ipa_cents == 800
        for response in (fragment, plain):
            body = response.content.decode()
            assert services.SETTINGS_STALE_SAVE in body
            assert 'name="prices-ipa" value="8.00"' in body
            assert 'id="hq-lg-bar-prices" open' in body
        assert "HX-Trigger" not in fragment

    def it_treats_a_form_with_no_figures_as_stale(hub, admin_client):
        data = {key: value for key, value in _prices(_found(hub)).items() if "seen" not in key}

        response = admin_client.post(_url("ledger_bar_prices", hub), data, **HTMX)

        assert services.SETTINGS_STALE_SAVE in response.content.decode()
        assert not LedgerSettings.objects.exists()  # a refusal writes nothing, not even the row

    @pytest.mark.parametrize(("field", "value"), [("ipa", "1000.01"), ("na", "-1"), ("last_year", "1000000.01")])
    def it_holds_the_prices_to_their_caps(hub, admin_client, field, value):
        fragment = admin_client.post(_url("ledger_bar_prices", hub), _prices(_found(hub), **{field: value}), **HTMX)
        plain = admin_client.post(_url("ledger_bar_prices", hub), _prices(_found(hub), **{field: value}))

        assert "Ensure this value is" in fragment.content.decode()
        assert "data-hq-ledger" in plain.content.decode() and "Ensure this value is" in plain.content.decode()
        assert not LedgerSettings.objects.exists()


def describe_the_approval_beside_d6bs_figures():
    @pytest.mark.parametrize("saved_in_between", ["prices", "tickets", "ticket prices"])
    def it_saves_the_approval_from_a_page_older_than_a_price_or_tickets_save(hub, admin_client, saved_in_between):
        LedgerSettingsFactory(hub=hub, approved_cents=90_000, approved_note="Oct 14")
        page = _found(hub)
        posts = {
            "prices": ("ledger_bar_prices", _prices(page)),
            "tickets": ("ledger_tickets", _tickets(page)),
            "ticket prices": ("ledger_ticket_prices", _ticket_prices(page)),
        }
        name, data = posts[saved_in_between]
        assert admin_client.post(_url(name, hub), data, **HTMX).status_code == 200

        response = admin_client.post(_url("ledger_approval", hub), _approval(page, approved="1100"))

        found = _found(hub)
        assert response.status_code == 302
        assert (found.approved_cents, found.approved_note) == (110_000, "Oct 14")

    def it_still_refuses_the_approval_after_another_approval_save(hub, admin_client):
        LedgerSettingsFactory(hub=hub, approved_cents=90_000, approved_note="Oct 14")
        page = _found(hub)
        assert admin_client.post(_url("ledger_approval", hub), _approval(page, approved="1000")).status_code == 302

        response = admin_client.post(_url("ledger_approval", hub), _approval(page, approved="1100", note="Mine"))

        found = _found(hub)
        assert response.status_code == 200
        assert "Someone else changed the approval. Your page has been refreshed; make your change again." in (
            response.content.decode()
        )
        assert (found.approved_cents, found.approved_note) == (100_000, "Oct 14")

    def it_takes_a_first_approval_save_on_a_hub_whose_row_a_price_save_made(hub, admin_client):
        page = _found(hub)
        assert not LedgerSettings.objects.exists()
        admin_client.post(_url("ledger_bar_prices", hub), _prices(page), **HTMX)

        response = admin_client.post(_url("ledger_approval", hub), _approval(page, approved="1200", note="Nov vote"))

        found = _found(hub)
        assert response.status_code == 302
        assert (found.approved_cents, found.approved_note, found.ipa_cents) == (120_000, "Nov vote", 700)

    @pytest.mark.django_db(transaction=True)
    def it_holds_a_racing_first_save_to_the_row_the_other_made(hub):
        """Another organiser's first save inserts the row and commits only once ours waits on it, so ours finds no
        row to lock, then is held to the row the other made."""
        page = _found(hub)

        with (
            committed_once_we_wait(lambda: LedgerSettingsFactory(hub=hub, ipa_cents=900)),
            pytest.raises(services.StaleSave),
        ):
            services.save_ledger_figures(hub, {"ipa_cents": 700}, seen={"ipa_cents": page.ipa_cents})

        assert LedgerSettings.objects.get(hub=hub).ipa_cents == 900


def describe_the_tiles():
    def it_counts_the_bar_cost_from_drinks_lines_only_and_takes_the_profit(hub, kegs, lee_client):
        decor = BudgetLineFactory(hub=hub, name="Lights", category=C.DECOR)
        _bar_cost(hub, kegs, 30_000)
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.REFUND, amount_cents=5_000)
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.PAID_OTHER, amount_cents=2_000)
        _bar_cost(hub, decor, 50_000)  # decor is never charged to the bar
        BarSaleFactory.create_batch(10, hub=hub)  # $60
        BarSaleFactory(hub=hub, drink=D.PILS, qty=5, amount_cents=3_000, source="batch")

        bar = services.bar_totals(hub, _found(hub), cost_cents=services.bar_cost_cents(hub))

        assert (bar.count, bar.sales_cents, bar.cost_cents, bar.profit_cents) == (15, 9_000, 27_000, -18_000)
        assert bar.count_line == "10 IPA · 5 Pilsner"
        assert bar.profit_display == "−$180"
        assert (bar.profit_line, bar.profit_tone) == ("$180 to break even", "bad")
        assert (bar.percent, bar.meter_line) == (33, "$90 of $270")
        body = lee_client.get(_page(hub)).content.decode()
        assert "pl-hq-lg__tile--bad" in body
        assert "Break-even: about <strong>30</strong> more beers at $6" in body

    @pytest.mark.parametrize(
        ("cost", "sales", "ipa", "line"),
        [
            # Lee's renderBar: need = cost - sales; Math.ceil(need / ipa).
            (30_000, 10_000, 600, "Break-even: about <strong>34</strong> more beers at $6"),
            (30_000, 29_500, 600, "Break-even: about <strong>1</strong> more beer at $6"),
            (30_000, 30_000, 600, "<strong>Break-even reached</strong>"),
            (30_000, 45_000, 600, "<strong>Break-even reached</strong>"),
            (0, 4_000, 600, "Break-even shows once costs are logged"),
            (30_000, 10_000, 0, "Break-even: <strong>$200</strong> to go"),
        ],
    )
    def it_says_lees_break_even(hub, kegs, lee_client, cost, sales, ipa, line):
        LedgerSettingsFactory(hub=hub, ipa_cents=ipa)
        if cost:
            _bar_cost(hub, kegs, cost)
        BarSaleFactory(hub=hub, qty=1, amount_cents=sales)

        body = lee_client.get(_page(hub)).content.decode()

        assert f"data-hq-lg-break-even>{line}</span>" in body

    @pytest.mark.parametrize(
        ("sales", "cost", "sales_line", "profit_line", "tone"),
        [
            (0, 0, "2025: about $500", "No bar costs logged yet", ""),
            (60_000, 0, "Ahead of 2025 (about $500)", "No bar costs logged yet", "ok"),
            (40_000, 40_000, "Behind 2025 (about $500)", "Bar has paid for itself", ""),
            (50_000, 10_000, "Ahead of 2025 (about $500)", "Bar has paid for itself", "ok"),
        ],
    )
    def it_reads_the_sales_and_profit_lines_in_lees_words(sales, cost, sales_line, profit_line, tone):
        bar = services.BarTotals(
            count=1, sales_cents=sales, cost_cents=cost, last_year_cents=50_000, ipa_cents=600, by_drink={}, by_pay={}
        )

        assert (bar.sales_line, bar.profit_line, bar.profit_tone) == (sales_line, profit_line, tone)
        assert bar.count_line == "No sales logged yet"

    def it_says_2025_alone_when_last_year_is_zero():
        bar = services.BarTotals(
            count=1, sales_cents=500, cost_cents=0, last_year_cents=0, ipa_cents=600, by_drink={}, by_pay={}
        )

        assert bar.sales_line == "2025: about $0"

    def it_follows_a_line_moved_into_drinks_out_of_band(hub, admin_client):
        lights = BudgetLineFactory(hub=hub, name="Lights", category=C.DECOR)
        _bar_cost(hub, lights, 12_000)
        data = {
            f"line-{lights.pk}-name": "Lights",
            f"line-{lights.pk}-category": C.DRINKS,
            f"line-{lights.pk}-budget": "0.00",
            f"line-{lights.pk}-updated_at": lights.updated_at.isoformat(),
            "filter": "all",
        }

        response = admin_client.post(_url("ledger_line_edit", hub, lights.pk), data, **HTMX)

        body = response.content.decode()
        assert 'id="hq-lg-bar-tiles" data-hq-lg-bar-tiles hx-swap-oob="true"' in body
        assert '<strong class="pl-hq-lg__tile-figure" data-hq-lg-figure>$120</strong>' in body


def describe_the_sales_table():
    def it_lists_newest_first_with_a_labelled_delete_through_the_confirm_modal(hub, lee_client):
        first = BarSaleFactory(hub=hub, note="first tap")
        second = BarSaleFactory(hub=hub, drink=D.MIXED, qty=0, amount_cents=4_200, pay=P.CASH, note="tip jar")

        body = lee_client.get(_page(hub)).content.decode()

        assert body.index(f'data-hq-lg-bar-sale="{second.pk}"') < body.index(f'data-hq-lg-bar-sale="{first.pk}"')
        assert "Mixed / total only" in body and "$42" in body and "tip jar" in body
        assert f"open-confirm', 'delete-sale-{first.pk}'" in body
        assert 'aria-label="Delete 1 IPA (Card) at' in body
        assert 'aria-label="Delete 0 Mixed / total only (Cash) at' in body
        assert f'href="{_url("ledger_bar_csv", hub)}"' in body

    def it_deletes_one_sale_then_lands_on_the_section(hub, lee_client):
        sale = BarSaleFactory(hub=hub)
        kept = BarSaleFactory(hub=hub)

        response = lee_client.post(_url("ledger_bar_delete", hub, sale.pk), follow=True)

        assert response.redirect_chain[-1][0] == f"{_page(hub)}#hq-lg-bar"
        assert "Deleted 1 IPA (Card)." in [str(message) for message in response.context["messages"]]
        assert list(BarSale.objects.values_list("pk", flat=True)) == [kept.pk]


def describe_the_csv():
    def it_streams_lees_columns_oldest_first_with_the_totals_row_last(hub, kegs, lee_client):
        _bar_cost(hub, kegs, 10_000)
        BarSaleFactory(hub=hub, pay=P.CASH, note="=HYPERLINK(1)")
        BarSaleFactory(hub=hub, drink=D.WINE, qty=6, amount_cents=4_800, pay=P.RSVP, source="batch")
        BarSaleFactory(hub=EventHubFactory(), note="another hub's")

        response = lee_client.get(_url("ledger_bar_csv", hub))

        assert response.status_code == 200
        assert response["Content-Disposition"].startswith(f'attachment; filename="{hub.slug}-drink-sales-')
        rows = list(csv.reader(io.StringIO(b"".join(response.streaming_content).decode())))
        assert rows[0] == BAR_SALES_CSV_HEADERS == ["Time", "Drink", "Qty", "Amount", "Paid by", "Note", "Logged as"]
        assert [row[1:] for row in rows[1:3]] == [
            ["IPA", "1", "6.00", "Cash", "'=HYPERLINK(1)", "Quick add"],
            ["Wine", "6", "48.00", "$5 RSVP credit", "", "Batch"],
        ]
        assert rows[3] == []
        assert rows[4] == [
            "Totals",
            "",
            "7",
            "54.00",
            "Card 0.00 / Cash 6.00 / $5 RSVP credit 48.00 / Other 0.00",
            "Bar cost 100.00",
            "Profit -46.00",
        ]
        assert len(rows) == 5


def describe_the_wine_wall_section():
    def it_reads_the_wall_and_links_to_it_and_its_donors(hub, wall, lee_client):
        HubGoalFactory(hub=hub, kind=HubGoal.Kind.BOTTLES, target=100)
        WineBottleFactory(hub=hub, qty=3, value_cents=2_000, giver_name="Amy")
        WineBottleFactory(hub=hub, qty=1, value_cents=3_000, giver_name="Bo")
        WineDonorThanksFactory(hub=hub, giver_key="amy")
        WineDonorThanksFactory(hub=hub, giver_key="gone donor")  # no bottle names them any more

        body = lee_client.get(_page(hub)).content.decode()

        assert "data-hq-lg-figure>4 / 100</strong>" in body
        assert "96 to go" in body
        assert "data-hq-lg-figure>$90</strong>" in body and "Avg $22.50 per bottle" in body
        assert (
            f'href="{_url("wine_donors", hub)}" class="pl-hq-lg__chip pl-hq-lg__chip--info" data-hq-lg-thanked>1 of 2 donors thanked</a>'
            in body
        )
        assert f'href="{wall.url}" class="pl-hq-lg__link" data-hq-lg-wall-link>' in body
        assert 'aria-label="Bottles toward the goal: 4 percent"' in body
        assert "<span>50</span><span>100 bottles</span>" in body
        assert "data-hq-lg-tickets-form" in body
        assert "add to wine wall" not in body.lower()  # bottles are logged on the wall's own door

    def it_leaves_out_links_the_viewer_may_not_open(hub, card, make_member):
        kate = make_member(preferred_name="Kate")
        card.team.add(kate)
        wall = HubCardFactory(hub=hub, kind=HubCard.Kind.WINE_WALL, audience=HubCard.Audience.MEMBERS)
        client = signed_in(kate)

        body = client.get(_page(hub)).content.decode()
        assert f'href="{wall.url}"' in body  # open to members, but Kate does not organise it
        assert f'href="{_url("wine_donors", hub)}"' not in body
        assert '<span class="pl-hq-lg__chip" data-hq-lg-thanked>0 of 0 donors thanked</span>' in body

        wall.is_hidden = True
        wall.save(update_fields=["is_hidden"])
        assert "data-hq-lg-wall-link" not in client.get(_page(hub)).content.decode()

    @pytest.mark.parametrize(
        ("goal", "bottles", "line"),
        [(0, 3, "No bottles goal yet"), (5, 5, "Goal reached"), (5, 7, "Goal reached, 2 extra"), (5, 2, "3 to go")],
    )
    def it_says_lees_bottles_line(goal, bottles, line):
        band = services.WineBand(bottles=bottles, goal=goal, value_cents=0, supporters=0, by_kind=[])

        assert band.goal_line == line

    def it_rounds_the_average_half_up_and_says_retail_with_none():
        assert services.WineBand(3, 0, 1_000, 1, []).average_line == "Avg $3.33 per bottle"
        assert services.WineBand(8, 0, 2_004, 1, []).average_line == "Avg $2.51 per bottle"  # 250.5 cents rounds up
        assert services.WineBand(0, 0, 0, 0, []).average_line == "Retail value of bottles"
        assert services.WineBand(0, 7, 0, 0, []).half_goal == 4

    def it_shows_no_meter_with_no_bottles_goal(hub, lee_client):
        body = lee_client.get(_page(hub)).content.decode()

        assert "data-hq-lg-wine-meter" not in body
        assert "No bottles goal yet" in body


def describe_the_ticket_math():
    @pytest.mark.parametrize(
        ("singles", "pairs", "ticket", "pair", "count", "revenue"),
        [(0, 0, 2_500, 4_000, 0, 0), (3, 2, 2_500, 4_000, 7, 15_500), (1, 1, 2_000, 3_500, 3, 5_500)],
    )
    def it_counts_a_deal_as_two_tickets_and_prices_each_kind(singles, pairs, ticket, pair, count, revenue):
        totals = services.TicketTotals(
            singles=singles, pairs=pairs, ticket_cents=ticket, pair_cents=pair, goal=0, bottles=0
        )

        assert (totals.count, totals.revenue_cents) == (count, revenue)

    @pytest.mark.parametrize(
        ("goal", "bottles", "ticket", "pair", "display", "line"),
        [
            # Lee's renderWine: nb = max(goal, bottles); lo = floor(nb/2)*pair + (nb%2)*price; hi = nb*price.
            (100, 0, 2_500, 4_000, "$2,000 to $2,500", "100 tickets at $25, or 2 for $40"),
            (100, 101, 2_500, 4_000, "$2,025 to $2,525", "101 tickets at $25, or 2 for $40"),
            (100, 40, 2_500, 5_000, "$2,500", "100 tickets at $25, or 2 for $50"),
            (0, 0, 2_500, 4_000, "$0", "0 tickets at $25, or 2 for $40"),
            (3, 0, 2_000, 3_500, "$55 to $60", "3 tickets at $20, or 2 for $35"),
        ],
    )
    def it_ports_lees_revenue_at_goal(goal, bottles, ticket, pair, display, line):
        totals = services.TicketTotals(
            singles=0, pairs=0, ticket_cents=ticket, pair_cents=pair, goal=goal, bottles=bottles
        )

        assert (totals.at_goal_display, totals.at_goal_line) == (display, line)

    def it_describes_the_tickets_in_lees_words():
        one = services.TicketTotals(singles=1, pairs=1, ticket_cents=2_500, pair_cents=4_000, goal=0, bottles=0)
        many = services.TicketTotals(singles=3, pairs=2, ticket_cents=2_500, pair_cents=4_000, goal=0, bottles=0)

        assert one.description == "1 single at $25 + 1 deal at $40 for 2"
        assert many.description == "3 singles at $25 + 2 deals at $40 for 2"
        assert many.price_line == "$25 a ticket or 2 for $40. Each ticket wins a bottle."


def describe_the_tickets_form():
    def it_saves_the_tickets_and_swaps_the_section(hub, lee_client):
        LedgerSettingsFactory(hub=hub, ipa_cents=900)
        found = _found(hub)

        response = lee_client.post(_url("ledger_tickets", hub), _tickets(found), **HTMX)

        found.refresh_from_db()
        assert (found.singles_sold, found.pairs_sold, found.ipa_cents) == (3, 2, 900)
        assert _toast(response)["message"] == "Tickets updated."
        body = response.content.decode()
        assert body.lstrip().startswith('<section class="pl-hq-lg__section" id="hq-lg-wine"')
        assert "data-hq-lg-figure>7</strong>" in body and "$155 raised" in body
        assert 'id="hq-lg-board-figures" data-hq-lg-board-figures hx-swap-oob="true"' in body

    def it_sends_the_board_paragraph_out_of_band_with_the_new_tickets(hub, kegs, lee_client):
        LedgerEntryFactory(hub=hub, line=kegs)
        WineBottleFactory(hub=hub)  # the tickets are said after the wine wall's bottles

        body = lee_client.post(_url("ledger_tickets", hub), _tickets(_found(hub)), **HTMX).content.decode()

        board = body.split('id="hq-lg-board-figures"', 1)[1]
        assert board.count("7 tickets sold (3 singles at $25 + 2 deals at $40 for 2) raised $155.") == 2

    def it_labels_the_inputs_with_the_prices_and_follows_a_price_change(hub, lee_client, admin_client):
        body = lee_client.get(_page(hub)).content.decode()
        assert "Single tickets sold ($25)" in body and "2-ticket deals sold ($40)" in body

        response = admin_client.post(_url("ledger_ticket_prices", hub), _ticket_prices(_found(hub)), **HTMX)

        found = _found(hub)
        assert (found.ticket_cents, found.pair_cents) == (2_000, 3_500)
        assert _toast(response)["message"] == "Ticket prices saved."
        body = response.content.decode()
        assert "Single tickets sold ($20)" in body and "2-ticket deals sold ($35)" in body
        assert "$20 a ticket or 2 for $35. Each ticket wins a bottle." in body

    def it_refuses_a_save_from_an_older_page(hub, lee_client, admin_client):
        old = _found(hub)
        LedgerSettingsFactory(hub=hub, singles_sold=9)

        fragment = lee_client.post(_url("ledger_tickets", hub), _tickets(old), **HTMX)
        plain = admin_client.post(
            _url("ledger_ticket_prices", hub), _ticket_prices(LedgerSettings(hub=hub, ticket_cents=1))
        )

        assert LedgerSettings.objects.get(hub=hub).singles_sold == 9
        assert services.SETTINGS_STALE_SAVE in fragment.content.decode()
        assert 'name="tickets-singles_sold" value="9"' in fragment.content.decode()
        assert "HX-Trigger" not in fragment
        assert services.SETTINGS_STALE_SAVE in plain.content.decode()
        assert "data-hq-ledger" in plain.content.decode()
        assert LedgerSettings.objects.get(hub=hub).ticket_cents == 2_500

    @pytest.mark.parametrize(
        ("singles", "error"), [(TICKETS_MAX + 1, "less than or equal to 10000"), (-1, "greater than or equal to 0")]
    )
    def it_holds_the_counts_to_their_caps(hub, lee_client, singles, error):
        fragment = lee_client.post(_url("ledger_tickets", hub), _tickets(_found(hub), singles=singles), **HTMX)
        plain = lee_client.post(_url("ledger_tickets", hub), _tickets(_found(hub), singles=singles))

        assert error in fragment.content.decode()
        assert error in plain.content.decode() and "data-hq-ledger" in plain.content.decode()
        assert not LedgerSettings.objects.exists()

    def it_holds_the_ticket_prices_to_their_cap(hub, admin_client):
        response = admin_client.post(
            _url("ledger_ticket_prices", hub), _ticket_prices(_found(hub), ticket="1000.01"), **HTMX
        )

        assert "less than or equal to 1000" in response.content.decode()
        assert "open data-hq-lg-ticket-prices>" in response.content.decode()  # open with its errors
        assert not LedgerSettings.objects.exists()

    def it_lands_a_plain_save_on_the_section(hub, lee_client):
        response = lee_client.post(_url("ledger_tickets", hub), _tickets(_found(hub)), follow=True)

        assert response.redirect_chain[-1][0] == f"{_page(hub)}#hq-lg-wine"
        assert "Tickets updated." in [str(message) for message in response.context["messages"]]


def describe_the_board_summary():
    def it_says_the_event_cash_the_drink_sales_and_the_tickets_in_lees_places(hub, kegs):
        HubGoalFactory(hub=hub, kind=HubGoal.Kind.EVENT_CASH, label="Raised", target=10_000, is_money=True)
        HubGoalFactory(hub=hub, kind=HubGoal.Kind.BOTTLES, target=100)
        LedgerEntryFactory(hub=hub, line=kegs, kind=K.SPONSORED, amount_cents=500, source="Cascade")
        _bar_cost(hub, kegs, 30_000)
        SponsorFactory(hub=hub, status="received", amount_cents=150_000)
        SponsorFactory(hub=hub, status="committed", amount_cents=50_000)
        BarSaleFactory(hub=hub, qty=40, amount_cents=24_000)
        WineBottleFactory(hub=hub, qty=4, value_cents=2_500)
        LedgerSettingsFactory(hub=hub, singles_sold=3, pairs_sold=1)

        band = services.ledger_band(hub)
        text = services.board_summary(band, services.door_figures(hub, band))

        assert (
            "Event cash goal: $1,855 raised toward $10,000 (plus $500 committed). "
            "Drink sales: 40 drinks sold for $240, $60 short of break-even after $300 in bar costs. "
            "Cash sponsorships: $1,500 received and $500 committed."
        ) in text
        assert text.endswith(
            "Wine wall: 4 of 100 bottles donated, worth $100. "
            "5 tickets sold (3 singles at $25 + 1 deal at $40 for 2) raised $115."
        )

    def it_says_a_profit_and_leaves_out_what_has_nothing_to_say(hub, kegs):
        LedgerEntryFactory(hub=hub, line=kegs)
        BarSaleFactory(hub=hub, qty=2, amount_cents=1_200)
        WineBottleFactory(hub=hub)

        text = services.board_summary(services.ledger_band(hub), services.door_figures(hub))

        assert "Event cash goal: $12 raised toward $10,000." in text
        assert "Drink sales: 2 drinks sold for $12." in text
        assert "tickets sold" not in text

        _bar_cost(hub, kegs, 1_000)
        text = services.board_summary(services.ledger_band(hub), services.door_figures(hub))
        assert "Drink sales: 2 drinks sold for $12, a profit of $2 after $10 in bar costs." in text

    def it_says_nothing_of_cash_with_none(hub, kegs):
        LedgerEntryFactory(hub=hub, line=kegs)

        text = services.board_summary(services.ledger_band(hub), services.door_figures(hub))

        assert "Event cash goal" not in text and "Drink sales" not in text


def describe_the_donors_thanked():
    @pytest.fixture
    def givers(hub, wall):
        WineBottleFactory(hub=hub, giver_name="Amy", value_cents=2_000)
        WineBottleFactory(hub=hub, giver_name="AMY", value_cents=3_000, qty=2)
        WineBottleFactory(hub=hub, giver_name="Bo", value_cents=1_500)

    def it_shows_a_thanked_column_and_toggles_it_over_htmx(hub, givers, lee_client):
        page = lee_client.get(_url("wine_donors", hub)).content.decode()
        assert "<th>Thanked</th>" in page
        assert 'aria-label="Thank-you sent to AMY" data-hq-donor-thanked="no">Mark thanked</button>' in page
        assert "2 donors to thank · 0 thanked" in page

        response = lee_client.post(_url("wine_donor_thanked", hub), {"giver": " amy ", "thanked": "1"}, **HTMX)

        assert list(WineDonorThanks.objects.values_list("giver_key", flat=True)) == ["amy"]
        assert _toast(response) == {"message": "AMY is marked thanked.", "type": "success"}
        body = response.content.decode()
        assert body.lstrip().startswith("<tr data-hq-donor-row=")
        assert 'aria-pressed="true"' in body and "&#10003; Thanked" in body
        assert 'name="thanked" value=""' in body  # the next click takes it off
        assert "<template>" in body
        assert (
            '<p class="pl-hq-wine__donor-count" id="hq-donor-count" data-hq-donor-count hx-swap-oob="true">'
            "2 donors to thank · 1 thanked</p>"
        ) in body

        lee_client.post(_url("wine_donor_thanked", hub), {"giver": "Amy", "thanked": "1"}, **HTMX)
        assert WineDonorThanks.objects.count() == 1  # saying it twice changes nothing
        response = lee_client.post(_url("wine_donor_thanked", hub), {"giver": "Amy", "thanked": ""}, **HTMX)
        assert not WineDonorThanks.objects.exists()
        assert _toast(response)["message"] == "AMY is back on the list to thank."

    def it_lands_a_plain_post_where_it_came_from(hub, givers, lee_client):
        donors = lee_client.post(_url("wine_donor_thanked", hub), {"giver": "Bo", "thanked": "1"}, follow=True)
        on_wall = lee_client.post(
            _url("wine_donor_thanked", hub), {"giver": "Bo", "thanked": "", "back": "wall"}, follow=True
        )

        assert donors.redirect_chain[-1][0] == _url("wine_donors", hub)
        assert "Bo is marked thanked." in [str(message) for message in donors.context["messages"]]
        assert on_wall.redirect_chain[-1][0] == _url("wine", hub)
        assert "Bo is back on the list to thank." in [str(message) for message in on_wall.context["messages"]]

    def it_carries_thanked_into_the_csv_and_the_wall_panel(hub, givers, lee_client):
        WineDonorThanksFactory(hub=hub, giver_key="bo")

        rows = b"".join(lee_client.get(_url("wine_donors_csv", hub)).streaming_content).decode().splitlines()
        wall_page = lee_client.get(_url("wine", hub)).content.decode()

        assert rows[1:] == ["AMY,Person,,Not asked,3,80.00,no,Guest,", "Bo,Person,,Not asked,1,15.00,yes,Guest,"]
        assert '<input type="hidden" name="back" value="wall">' in wall_page
        assert "2 donors to thank · 1 thanked" in wall_page

    def it_keeps_one_thanks_per_donor_whatever_the_capitalisation(hub):
        WineDonorThanksFactory(hub=hub, giver_key="Amy")

        with pytest.raises(IntegrityError), transaction.atomic():
            WineDonorThanks.objects.bulk_create([WineDonorThanks(hub=hub, giver_key="AMY")])
        assert str(WineDonorThanks.objects.get()) == "Thanked amy"

    def it_counts_the_ledgers_line_in_two_queries(hub, givers, django_assert_num_queries):
        WineDonorThanksFactory(hub=hub, giver_key="bo")
        WineDonorThanksFactory(hub=EventHubFactory(), giver_key="amy")

        with django_assert_num_queries(2):
            thanks = services.donor_thanks(hub)

        assert thanks == services.DonorThanks(thanked=1, donors=2)
        assert services.DonorThanks(thanked=0, donors=1).line == "0 of 1 donor thanked"


def describe_the_under_ticket_price_flag():
    def it_flags_a_donor_with_bottles_under_one_ticket_in_lees_words(hub, wall, lee_client):
        WineBottleFactory(hub=hub, giver_name="Cheap", value_cents=1_200, qty=2)
        WineBottleFactory(hub=hub, giver_name="Mixed", value_cents=2_499)
        WineBottleFactory(hub=hub, giver_name="Mixed", value_cents=4_000, qty=2)
        WineBottleFactory(hub=hub, giver_name="Fine", value_cents=2_500)

        rows = {row.name: row.under_line for row in services.donor_rows(hub)}
        body = lee_client.get(_url("wine_donors", hub)).content.decode()

        assert rows == {"Cheap": "Under $25", "Mixed": "1 under $25", "Fine": ""}
        assert body.count("data-hq-donor-under") == 2

    def it_follows_the_ticket_price(hub, wall):
        LedgerSettingsFactory(hub=hub, ticket_cents=2_000)
        WineBottleFactory(hub=hub, giver_name="Twenty", value_cents=1_999)

        assert [row.under_line for row in services.donor_rows(hub)] == ["Under $20"]

    def it_flags_the_walls_bottles_for_organisers_only(hub, wall, lee_client, member_client):
        cheap = WineBottleFactory(hub=hub, value_cents=1_000)

        organiser = lee_client.get(_url("wine", hub)).content.decode()
        member = member_client.get(_url("wine", hub)).content.decode()
        visitor = Client().get(_url("wine", hub)).content.decode()

        assert f'data-hq-bottle="{cheap.pk}"' in member
        assert '<span class="pl-hq-wine__under" data-hq-bottle-under>Under $25</span>' in organiser
        assert "data-hq-bottle-under" not in member and "data-hq-bottle-under" not in visitor
        assert "data-hq-donor-thanked" not in member


def describe_the_event_cash():
    def it_adds_sponsors_received_bar_sales_and_tickets(hub):
        SponsorFactory(hub=hub, status="received", amount_cents=100_000)
        SponsorFactory(hub=hub, status="committed", amount_cents=99_900)
        BarSaleFactory(hub=hub, amount_cents=12_345)
        BarSaleFactory(hub=EventHubFactory(), amount_cents=50_000)
        LedgerSettingsFactory(hub=hub, singles_sold=2, pairs_sold=1)

        cash = services.event_cash(hub)

        assert cash == services.EventCash(
            sponsors_cents=100_000, committed_cents=99_900, bar_cents=12_345, tickets_cents=9_000
        )
        assert cash.total_cents == 121_345

    def it_counts_the_raised_goal_in_whole_dollars_in_a_fixed_number_of_queries(hub, django_assert_num_queries):
        HubGoalFactory(hub=hub, label="Raised", kind=HubGoal.Kind.EVENT_CASH, target=10_000, is_money=True)
        HubGoalFactory(hub=hub, label="Cash again", kind=HubGoal.Kind.EVENT_CASH, target=0, is_money=True)
        SponsorFactory(hub=hub, status="received", amount_cents=100_050)
        BarSaleFactory(hub=hub, amount_cents=99)

        with django_assert_num_queries(5):
            raised, again = services.goal_rows(hub)
        SponsorFactory.create_batch(3, hub=hub, status="received", amount_cents=100)
        BarSaleFactory.create_batch(4, hub=hub)
        with django_assert_num_queries(5):
            services.goal_rows(hub)

        assert (raised.current, raised.target, raised.current_display) == (1_001, 10_000, "$1,001")
        assert again.current == 1_001

    def it_shows_on_the_hq_page_counted(hub, lee_client):
        HubGoalFactory(hub=hub, label="Raised", kind=HubGoal.Kind.EVENT_CASH, target=10_000, is_money=True)
        BarSaleFactory(hub=hub, amount_cents=60_000)

        body = lee_client.get(hub.get_absolute_url()).content.decode()

        assert '<strong class="pl-hq-goal__value">$600 <small>of $10,000</small></strong>' in body


def describe_adopting_the_typed_in_raised():
    def it_turns_a_manual_raised_into_event_cash_in_place_and_never_doubles_it(open_hub, admin_client):
        open_hub.seed_default_cards()
        raised = HubGoalFactory(
            hub=open_hub, label="raised", kind=HubGoal.Kind.MANUAL, target=12_000, current_value=4_000, sort_order=3
        )
        HubGoalFactory(hub=open_hub, label="Bottles", kind=HubGoal.Kind.BOTTLES, sort_order=1)

        first = admin_client.post(_url("doors_update", open_hub), follow=True)
        second = admin_client.post(_url("doors_update", open_hub), follow=True)

        assert [str(message) for message in first.context["messages"]] == [
            "Doors updated: added the Volunteer spots, RSVPs, Member tables and Guilds taking part goals; "
            "Raised now counts itself."
        ]
        assert [str(message) for message in second.context["messages"]] == ["The doors are up to date."]
        adopted = HubGoal.objects.get(pk=raised.pk)
        assert (adopted.kind, adopted.label, adopted.target, adopted.sort_order, adopted.is_money) == (
            HubGoal.Kind.EVENT_CASH,
            "raised",
            12_000,
            3,
            True,
        )
        assert open_hub.goals.filter(kind=HubGoal.Kind.EVENT_CASH).count() == 1
        assert not open_hub.goals.filter(kind=HubGoal.Kind.MANUAL).exists()

    def it_leaves_a_typed_in_raised_beside_an_event_cash_goal(open_hub):
        HubGoalFactory(hub=open_hub, label="Cash", kind=HubGoal.Kind.EVENT_CASH, sort_order=0)
        typed = HubGoalFactory(hub=open_hub, label="Raised", kind=HubGoal.Kind.MANUAL, sort_order=1)

        seeded = services.seed_goals(open_hub)

        assert seeded.adopted == ""
        assert HubGoal.objects.get(pk=typed.pk).kind == HubGoal.Kind.MANUAL

    def it_adopts_the_goal_labelled_raised_trimmed_and_in_any_case_over_other_money_goals(open_hub):
        HubGoalFactory(hub=open_hub, label="Pledges", kind=HubGoal.Kind.MANUAL, is_money=True, sort_order=0)
        raised = HubGoalFactory(hub=open_hub, label="  RAISED ", kind=HubGoal.Kind.MANUAL, sort_order=1)

        seeded = services.seed_goals(open_hub)

        assert seeded.adopted == "  RAISED "
        assert HubGoal.objects.get(pk=raised.pk).kind == HubGoal.Kind.EVENT_CASH
        assert HubGoal.objects.get(label="Pledges").kind == HubGoal.Kind.MANUAL
        assert open_hub.goals.filter(kind=HubGoal.Kind.EVENT_CASH).count() == 1

    def it_adopts_the_one_manual_money_goal_in_place_whatever_its_label(open_hub, admin_client):
        open_hub.seed_default_cards()
        HubGoalFactory(hub=open_hub, label="Volunteer pizza", kind=HubGoal.Kind.MANUAL, sort_order=0)
        cash = HubGoalFactory(
            hub=open_hub, label="Cash raised", kind=HubGoal.Kind.MANUAL, target=9_000, is_money=True, sort_order=4
        )

        response = admin_client.post(_url("doors_update", open_hub), follow=True)

        adopted = HubGoal.objects.get(pk=cash.pk)
        assert (adopted.kind, adopted.label, adopted.target, adopted.sort_order, adopted.is_money) == (
            HubGoal.Kind.EVENT_CASH,
            "Cash raised",
            9_000,
            4,
            True,
        )
        assert HubGoal.objects.get(label="Volunteer pizza").kind == HubGoal.Kind.MANUAL  # not money, never adopted
        assert open_hub.goals.filter(kind=HubGoal.Kind.EVENT_CASH).count() == 1
        assert not open_hub.goals.filter(label="Raised").exists()
        [message] = [str(message) for message in response.context["messages"]]
        assert message.endswith("; Raised now counts itself.")

    def it_adopts_nothing_from_two_money_goals_with_none_labelled_raised(open_hub):
        for position, label in enumerate(("Cash raised", "Pledges")):
            HubGoalFactory(hub=open_hub, label=label, kind=HubGoal.Kind.MANUAL, is_money=True, sort_order=position)

        seeded = services.seed_goals(open_hub)

        assert seeded.adopted == ""
        assert "Raised" in seeded.added
        assert open_hub.goals.filter(kind=HubGoal.Kind.MANUAL).count() == 2
        assert open_hub.goals.get(kind=HubGoal.Kind.EVENT_CASH).label == "Raised"

    def it_says_so_in_the_seed_command():
        event = CommunityEventFactory(community=True, title="Holiday Bizarre")
        hub = EventHubFactory(slug=HUB.slug, title="Lee's HQ", event=event)
        for position, label in enumerate(("Raised", "Wine wall bottles")):
            kind = HubGoal.Kind.MANUAL if label == "Raised" else HubGoal.Kind.BOTTLES
            HubGoalFactory(hub=hub, label=label, kind=kind, target=10_000, sort_order=position)
        out = StringIO()

        call_command("seed_holiday_bizarre", "--event", str(event.pk), stdout=out)

        assert (
            "Goals: 4 created, 2 kept as edited. Added: Volunteer spots, RSVPs, Member tables, Guilds taking part. "
            "Raised now counts itself." in out.getvalue()
        )
        assert hub.goals.filter(kind=HubGoal.Kind.EVENT_CASH).count() == 1
        assert hub.goals.count() == 6


def describe_the_sponsor_meter():
    def it_prefers_the_event_cash_goal_then_falls_back_as_before(open_hub):
        HubGoalFactory(hub=open_hub, label="Raised", kind=HubGoal.Kind.MANUAL, target=7_000, is_money=True)
        assert services.sponsor_goal(open_hub) == 7_000

        HubGoalFactory(hub=open_hub, label="Money in", kind=HubGoal.Kind.EVENT_CASH, target=12_000, is_money=True)
        assert services.sponsor_goal(open_hub) == 12_000

        HubGoal.objects.filter(hub=open_hub, kind=HubGoal.Kind.EVENT_CASH).update(target=0)
        assert services.sponsor_goal(open_hub) == 7_000

    def it_falls_back_to_any_typed_in_money_goal_then_lees_default(open_hub):
        assert services.sponsor_goal(open_hub) == services.DEFAULT_SPONSOR_GOAL
        HubGoalFactory(hub=open_hub, label="Pledges", kind=HubGoal.Kind.MANUAL, target=3_000, is_money=True)
        assert services.sponsor_goal(open_hub) == 3_000


def describe_query_counts():
    def it_runs_a_fixed_number_of_queries_on_the_ledger_however_many_sales(hub, wall, kegs, lee, lee_client):
        def _count() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert lee_client.get(_page(hub)).status_code == 200
            return len(queries)

        BarSaleFactory(hub=hub, created_by=lee)
        WineBottleFactory(hub=hub)
        _count()  # the first request warms the session and the caches
        few = _count()
        BarSaleFactory.create_batch(20, hub=hub, created_by=lee)
        for drink in (D.PILS, D.WINE, D.NA, D.MIXED):
            BarSaleFactory(hub=hub, drink=drink, pay=P.CASH, source="batch", qty=3, amount_cents=1_500)
        WineBottleFactory.create_batch(5, hub=hub)
        WineDonorThanksFactory(hub=hub, giver_key="giver 1")

        assert _count() == few

    def it_answers_a_tap_in_a_fixed_number_of_queries_however_many_sales(hub, lee, lee_client):
        def _count() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert lee_client.post(_url("ledger_bar_quick", hub), _quick(), **HTMX).status_code == 200
            return len(queries)

        _count()
        few = _count()
        BarSaleFactory.create_batch(20, hub=hub, created_by=lee)

        assert _count() == few

    def it_runs_a_fixed_number_of_queries_on_the_donors_page(hub, wall, lee_client):
        def _count() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert lee_client.get(_url("wine_donors", hub)).status_code == 200
            return len(queries)

        WineBottleFactory(hub=hub)
        _count()
        few = _count()
        WineBottleFactory.create_batch(6, hub=hub)
        WineDonorThanksFactory.create_batch(3, hub=hub)

        assert _count() == few
