"""Vendor Tables (Track D, part 3): the door, its organisers-only gate, the band in two queries, + Add a Table
Request with the waitlist when the map is full, the list's order and filters, the one Save per row that swaps only
that row, Delete, the space map's capacity, the CSV, the Member tables goal with its plain count, and the seed's
adoption of the placeholder."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from hq import services
from hq.exports import VENDOR_TABLES_CSV_HEADERS
from hq.forms import HubGoalForm, VendorTableForm
from hq.holiday_bizarre import GOALS, VENDOR_SETUP_NOTE, VENDOR_SETUP_WINDOWS
from hq.models import BUILT_DOORS, HubCard, HubGoal, VendorFloor, VendorTable
from hub.view_as import ROLE_GUEST, ROLE_MEMBER
from membership.models import Guild
from tests.hq.conftest import signed_in
from tests.hq.factories import HubCardFactory, HubGoalFactory, VendorFloorFactory, VendorTableFactory
from tests.membership.factories import CommunityEventFactory, GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
S = VendorTable.Status


def _page(hub: Any) -> str:
    return reverse("hq:vendors", args=[hub.slug])


def _add(hub: Any) -> str:
    return reverse("hq:vendor_add", args=[hub.slug])


def _floor(hub: Any) -> str:
    return reverse("hq:vendor_floor", args=[hub.slug])


def _edit(table: VendorTable) -> str:
    return reverse("hq:vendor_edit", args=[table.hub.slug, table.pk])


def _delete(table: VendorTable) -> str:
    return reverse("hq:vendor_delete", args=[table.hub.slug, table.pk])


def _csv(hub: Any) -> str:
    return reverse("hq:vendors_csv", args=[hub.slug])


def _add_post(**fields: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "Riverbend Ceramics",
        "guild": "",
        "sells": "Mugs and planters",
        "size": VendorTable.Size.EIGHT_FT,
        "email": "Rae@Riverbend.example",
        "phone": "503-555-0142",
        "setup": VendorTable.Setup.THU,
        "needs": "Power, near a wall",
    }
    data.update(fields)
    return data


def _row_post(table: VendorTable, **fields: Any) -> dict[str, Any]:
    """The row's form as it renders for ``table``, with ``fields`` changed, under the row's prefix."""
    values: dict[str, Any] = {
        "name": table.name,
        "guild": table.guild_id or "",
        "sells": table.sells,
        "size": table.size,
        "email": table.email,
        "phone": table.phone,
        "setup": table.setup,
        "needs": table.needs,
        "status": table.status,
        "table_no": table.table_no,
        "updated_at": table.updated_at.isoformat(),
    }
    show = fields.pop("filter", "all")
    values.update(fields)
    data = {f"vendor-{table.pk}-{key}": value for key, value in values.items()}
    data["filter"] = show
    return data


def _toast(response: Any) -> str:
    return json.loads(response["HX-Trigger"])["showToast"]["message"]


def _private_table(hub: Any, creator: Any) -> VendorTable:
    """A request with every private field set to a string no page chrome could contain."""
    return VendorTableFactory(
        hub=hub,
        name="Spec Secret Studio",
        guild=GuildFactory(name="Spec Secret Guild"),
        sells="Spec secret wares",
        email="secret@vendor.example",
        phone="503-555-0199",
        setup=VendorTable.Setup.FRI,
        needs="Spec secret needs",
        table_no="Q7",
        status=S.CONFIRMED,
        created_by=creator,
    )


@pytest.fixture
def kate(make_member):
    return make_member(preferred_name="Kate Lindqvist")


@pytest.fixture
def card(open_hub, kate) -> HubCard:
    """The Vendor Tables door on the open hub with Kate on its Team. A Team-only door (HQ card sharing): Admins only
    is its one audience, and nobody off the Team opens it whatever the database says."""
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


def describe_the_door():
    def it_is_a_built_door_that_opens_the_page_from_the_hq_page(card, kate_client):
        body = kate_client.get(card.hub.get_absolute_url()).content.decode()

        assert HubCard.Kind.VENDOR_TABLES in BUILT_DOORS
        assert card.url == _page(card.hub) == f"/hq/{card.hub.slug}/vendors/"
        assert f'href="{_page(card.hub)}" class="pl-hq-card" data-hq-card-link' in body
        assert "M3 9h18v2a3 3 0 0 1-6 0" in body  # the market stall icon

    def it_carries_lees_header_with_the_date_and_who_manages_it(hub, kate_client):
        hub.event = CommunityEventFactory(community=True, title="Holiday Bizarre", location="Past Lives")
        hub.save(update_fields=["event"])

        body = kate_client.get(_page(hub)).content.decode()

        assert "Vendor Tables</h1>" in body
        assert f"data-hq-vt-subline>{hub.title} · {hub.date_line} · Managed by Kate Lindqvist</p>" in body
        assert f'href="{hub.get_absolute_url()}" class="pl-hq-vt__back" data-hq-vt-back>&larr; HQ</a>' in body
        assert "data-hq-vt-helper>Waiting on Kate</span>" in body

    def it_leaves_out_the_date_and_the_manager_it_does_not_have(card, admin_client):
        card.team.clear()

        body = admin_client.get(_page(card.hub)).content.decode()

        assert f"data-hq-vt-subline>{card.hub.title}</p>" in body
        assert "data-hq-vt-helper>Waiting on the vendor team</span>" in body

    def it_reads_the_first_named_member_by_pk_and_falls_back_on_a_blank_name(card, kate, make_member):
        blank = make_member(preferred_name="   ", full_legal_name="   ")
        card.team.add(blank)
        later = make_member(preferred_name="Zed Ames")
        card.team.add(later)
        assert kate.pk < blank.pk < later.pk

        card.team.remove(kate)
        body = signed_in(blank).get(_page(card.hub)).content.decode()

        assert f"data-hq-vt-subline>{card.hub.title}</p>" in body
        assert "data-hq-vt-helper>Waiting on the vendor team</span>" in body


def describe_who_may_open_it():
    def it_is_a_404_for_anyone_off_the_team_and_leaks_nothing(hub, member_client, kate):
        _private_table(hub, kate)
        VendorFloorFactory(hub=hub, map_note="Spec secret map note")

        for client in (member_client, Client()):
            response = client.get(_page(hub))

            # The themed 404 draws the sidebar, whose Guilds list names every active guild; the page is what must
            # not leak, so the check reads the main content.
            body = response.content.decode().split('id="main-content"')[-1]
            assert response.status_code == 404
            for private in (
                "Spec Secret Studio",
                "Spec Secret Guild",
                "Spec secret wares",
                "secret@vendor.example",
                "503-555-0199",
                "Spec secret needs",
                "Q7",
                "Kate Lindqvist",
                "Spec secret map note",
                "data-hq-vt-band",
            ):
                assert private not in body, private

    def it_gives_the_page_to_a_named_member_on_an_admins_card(card, kate, kate_client):
        card.audience = HubCard.Audience.ADMINS
        card.save(update_fields=["audience"])
        _private_table(card.hub, kate)

        body = kate_client.get(_page(card.hub)).content.decode()

        assert "data-hq-vt-add-form" in body
        assert 'value="secret@vendor.example"' in body
        assert "data-hq-vt-csv" in body

    def it_is_a_404_for_a_member_on_an_admins_card(card, member_client):
        card.audience = HubCard.Audience.ADMINS
        card.save(update_fields=["audience"])

        assert member_client.get(_page(card.hub)).status_code == 404

    def it_sends_a_signed_out_visitor_through_login_on_a_members_card(card):
        card.audience = HubCard.Audience.MEMBERS
        card.save(update_fields=["audience"])

        response = Client().get(_page(card.hub))

        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_is_a_404_when_hidden_for_all_but_admins(card, member_client, kate_client, admin_client):
        card.is_hidden = True
        card.save(update_fields=["is_hidden"])

        assert member_client.get(_page(card.hub)).status_code == 404
        assert kate_client.get(_page(card.hub)).status_code == 404
        assert admin_client.get(_page(card.hub)).status_code == 200

    def it_is_a_404_on_a_hub_with_no_vendor_door(open_hub, admin_client):
        assert admin_client.get(_page(open_hub)).status_code == 404

    def it_refuses_every_write_and_the_csv_to_anyone_but_an_organiser(hub, member_client):
        table = VendorTableFactory(hub=hub)

        for client in (member_client, Client()):
            assert client.post(_add(hub), _add_post()).status_code == 403
            assert client.post(_edit(table), _row_post(table, status=S.CONFIRMED)).status_code == 403
            assert client.post(_delete(table)).status_code == 403
            assert client.post(_floor(hub), {"capacity": "10"}).status_code == 403
            assert client.get(_csv(hub)).status_code == 403
        table.refresh_from_db()
        assert table.status == S.REQUESTED
        assert VendorTable.objects.count() == 1
        assert not VendorFloor.objects.exists()

    @pytest.mark.parametrize("role", [ROLE_MEMBER, ROLE_GUEST])
    def it_shows_a_previewing_admin_the_404_that_viewer_gets_but_lets_them_act(hub, admin_client, role):
        session = admin_client.session
        session["view_as_role"] = role
        session.save()

        assert admin_client.get(_page(hub)).status_code == 404
        assert admin_client.post(_add(hub), _add_post()).status_code == 302
        assert admin_client.get(_csv(hub)).status_code == 200
        assert VendorTable.objects.count() == 1

    def it_takes_posts_only(hub, admin_client):
        table = VendorTableFactory(hub=hub)

        for url in (_add(hub), _edit(table), _delete(table), _floor(hub)):
            assert admin_client.get(url).status_code == 405, url

    def it_is_a_404_for_another_hubs_request_on_every_post(hub, admin_client):
        elsewhere = VendorTableFactory(name="Elsewhere", status=S.REQUESTED)

        edit = reverse("hq:vendor_edit", args=[hub.slug, elsewhere.pk])
        delete = reverse("hq:vendor_delete", args=[hub.slug, elsewhere.pk])

        assert admin_client.post(edit, _row_post(elsewhere, status=S.CONFIRMED)).status_code == 404
        assert admin_client.post(delete).status_code == 404
        elsewhere.refresh_from_db()
        assert elsewhere.status == S.REQUESTED


def describe_the_band():
    def it_counts_each_status_and_reads_the_map_in_two_queries(hub, django_assert_num_queries):
        VendorTableFactory.create_batch(3, hub=hub, status=S.CONFIRMED)
        VendorTableFactory.create_batch(2, hub=hub, status=S.REQUESTED)
        VendorTableFactory(hub=hub, status=S.WAITLIST)
        VendorTableFactory(hub=hub, status=S.CANCELLED)
        VendorTableFactory(status=S.CONFIRMED)  # another hub's
        VendorFloorFactory(hub=hub, capacity=10, map_note="18 in the gallery")

        with django_assert_num_queries(2):
            band = services.vendor_band(hub)

        assert (band.confirmed, band.requested, band.waitlist, band.cancelled, band.total) == (3, 2, 1, 1, 7)
        assert (band.capacity, band.map_note, band.space_left, band.is_full) == (10, "18 in the gallery", 7, False)
        assert (band.space_left_display, band.space_left_line, band.percent) == ("7", "of 10 tables on the map", 30)

    def it_waits_on_the_space_map_until_one_is_entered(hub):
        VendorTableFactory.create_batch(4, hub=hub, status=S.CONFIRMED)

        band = services.vendor_band(hub)

        assert (band.capacity, band.space_left, band.is_full, band.map_note) == (None, None, False, "")
        assert (band.space_left_display, band.space_left_line, band.percent) == (
            "–",
            "Waiting on the space map",
            0,
        )

    @pytest.mark.parametrize(
        ("capacity", "confirmed", "left", "line", "percent"),
        [
            (4, 4, "0", "Full: new requests go to the waitlist", 100),
            (4, 6, "-2", "Full: new requests go to the waitlist", 100),
            (0, 0, "0", "Full: new requests go to the waitlist", 100),
            (3, 2, "1", "of 3 tables on the map", 67),
        ],
    )
    def it_counts_space_left_below_zero_and_caps_the_meter(hub, capacity, confirmed, left, line, percent):
        VendorTableFactory.create_batch(confirmed, hub=hub, status=S.CONFIRMED)
        VendorFloorFactory(hub=hub, capacity=capacity)

        band = services.vendor_band(hub)

        assert (band.space_left_display, band.space_left_line, band.percent) == (left, line, percent)
        assert band.is_full is (left != "1")

    def it_renders_lees_four_tiles_the_meter_and_both_notes(hub, kate_client):
        VendorTableFactory.create_batch(5, hub=hub, status=S.CONFIRMED)
        VendorTableFactory(hub=hub, status=S.WAITLIST)
        VendorFloorFactory(hub=hub, capacity=4, map_note="18 in the gallery, 6 in the shop")

        body = kate_client.get(_page(hub)).content.decode()

        assert "data-hq-vt-figure>5</strong>" in body
        assert "data-hq-vt-helper>Tables locked in</span>" in body
        assert "data-hq-vt-helper>If space opens up</span>" in body
        assert "data-hq-vt-figure>-1</strong>" in body
        assert "data-hq-vt-helper>Full: new requests go to the waitlist</span>" in body
        assert 'max="100" value="100" aria-hidden="true" data-hq-vt-meter' in body
        assert (
            f"data-hq-vt-note>{VENDOR_SETUP_NOTE} <span data-hq-vt-map-note>Map: 18 in the gallery, 6 in the shop</span>"
        ) in body
        assert VENDOR_SETUP_NOTE == (
            "Setup: Thursday Dec 3, 3 to 7 PM, or Friday Dec 4, 3 to 5 PM. Sale runs Friday 5 to 10 PM."
        )

    def it_shows_a_dash_and_no_map_note_before_the_map(hub, kate_client):
        body = kate_client.get(_page(hub)).content.decode()

        assert "data-hq-vt-figure>–</strong>" in body
        assert "data-hq-vt-helper>Waiting on the space map</span>" in body
        assert "data-hq-vt-map-note" not in body


def describe_adding_a_request():
    def it_saves_a_request_over_htmx_and_brings_the_board_back_with_a_toast(hub, kate, kate_client):
        guild = GuildFactory(name="Ceramics Guild")

        response = kate_client.post(_add(hub), _add_post(guild=str(guild.pk)), **HTMX)

        table = VendorTable.objects.get()
        body = response.content.decode()
        assert response.status_code == 200
        assert _toast(response) == "Request added."
        assert (table.hub, table.name, table.guild, table.sells, table.size) == (
            hub,
            "Riverbend Ceramics",
            guild,
            "Mugs and planters",
            VendorTable.Size.EIGHT_FT,
        )
        assert (table.email, table.phone, table.setup, table.needs) == (
            "rae@riverbend.example",
            "503-555-0142",
            VendorTable.Setup.THU,
            "Power, near a wall",
        )
        assert (table.status, table.table_no, table.created_by) == (S.REQUESTED, "", kate)
        assert "data-hq-vt-board" in body
        assert "data-hq-vt-subline" not in body
        assert "open data-hq-vt-add" in body
        assert f'data-hq-vt-row="{table.pk}"' in body

    def it_puts_a_new_request_on_the_waitlist_once_the_confirmed_tables_fill_the_map(hub, kate_client):
        VendorTableFactory.create_batch(2, hub=hub, status=S.CONFIRMED)
        VendorTableFactory(hub=hub, status=S.REQUESTED)
        VendorFloorFactory(hub=hub, capacity=2)

        response = kate_client.post(_add(hub), _add_post(name="Spec Late Comer"), **HTMX)

        assert _toast(response) == "Added to the waitlist (tables are full)."
        assert VendorTable.objects.get(name="Spec Late Comer").status == S.WAITLIST

    def it_keeps_requesting_while_there_is_room_or_no_map(hub, kate_client):
        VendorTableFactory.create_batch(3, hub=hub, status=S.CONFIRMED)

        kate_client.post(_add(hub), _add_post(name="Before The Map"))
        VendorFloorFactory(hub=hub, capacity=4)
        kate_client.post(_add(hub), _add_post(name="Room For One"))

        assert set(hub.vendor_tables.filter(status=S.REQUESTED).values_list("name", flat=True)) == {
            "Before The Map",
            "Room For One",
        }

    def it_lands_a_plain_post_back_on_the_page_keeping_the_filter(hub, kate_client):
        response = kate_client.post(_add(hub), _add_post(filter="waitlist"), follow=True)

        assert response.redirect_chain == [(f"{_page(hub)}?filter=waitlist", 302)]
        assert "Request added." in [str(message) for message in response.context["messages"]]

    def it_says_so_in_the_message_when_a_plain_post_goes_to_the_waitlist(hub, kate_client):
        VendorFloorFactory(hub=hub, capacity=0)

        response = kate_client.post(_add(hub), _add_post(), follow=True)

        assert "Added to the waitlist (tables are full)." in [str(m) for m in response.context["messages"]]

    def it_keeps_the_new_request_in_view_under_a_filter_it_does_not_match(hub, kate_client):
        VendorTableFactory(hub=hub, name="Spec Confirmed", status=S.CONFIRMED)

        response = kate_client.post(_add(hub), _add_post(name="Spec Just Added", filter="confirmed"), **HTMX)

        body = response.content.decode()
        assert "Spec Confirmed" in body
        assert 'value="Spec Just Added"' in body
        assert 'data-hq-vt-filter="confirmed" aria-current="true"' in body

    def it_stores_the_defaults_for_what_was_left_blank(hub, kate_client):
        kate_client.post(_add(hub), {"name": "Bare Minimum", "size": VendorTable.Size.SIX_FT, "guild": "", "setup": ""})

        table = VendorTable.objects.get()
        assert (table.guild, table.setup, table.email, table.sells, table.needs) == (None, "", "", "", "")
        assert table.guild_display == "Not in a guild"

    @pytest.mark.parametrize(
        "fields",
        [{"name": "  "}, {"email": "not an email"}, {"size": "huge"}, {"setup": "sat"}, {"guild": "999999"}],
    )
    def it_refuses_a_bad_request_and_saves_nothing(hub, kate_client, fields):
        response = kate_client.post(_add(hub), _add_post(**fields), **HTMX)

        body = response.content.decode()
        assert response.status_code == 200
        assert 'class="pl-field-errors"' in body
        assert "open data-hq-vt-add" in body
        assert "HX-Trigger" not in response
        assert not VendorTable.objects.exists()

    def it_renders_the_whole_page_again_for_a_plain_post_that_fails(hub, kate_client):
        response = kate_client.post(_add(hub), _add_post(name=""))

        assert response.status_code == 200
        assert "data-hq-vt-subline" in response.content.decode()
        assert not VendorTable.objects.exists()

    def it_offers_only_the_active_guilds_and_not_in_a_guild(hub, kate_client):
        GuildFactory(name="Woodshop Guild")
        GuildFactory(name="Ceramics Guild")
        inactive = GuildFactory(name="Spec Sleeping Guild", is_active=False)
        gone = GuildFactory(name="Spec Gone Guild")
        Guild.all_objects.filter(pk=gone.pk).update(deleted_at="2026-01-01T00:00:00Z")

        form = VendorTableForm(guilds=services.active_guilds())

        labels = [label for _, label in form.fields["guild"].choices]  # type: ignore[attr-defined]
        assert labels == ["Not in a guild", "Ceramics Guild", "Woodshop Guild"]
        response = kate_client.post(_add(hub), _add_post(guild=str(inactive.pk)), **HTMX)
        assert "HX-Trigger" not in response
        assert not VendorTable.objects.exists()

    def it_offers_lees_sizes_and_setup_days_in_her_words(hub, kate_client):
        form = VendorTableForm(guilds=[])

        assert [label for _, label in form.fields["size"].choices] == [  # type: ignore[attr-defined]
            "6 ft",
            "8 ft",
            "4 ft",
            "Half table (share)",
            "Bringing their own",
            "Not sure",
        ]
        assert [label for _, label in form.fields["setup"].choices] == [  # type: ignore[attr-defined]
            "Not chosen",
            "Thu Dec 3, 3 to 7 PM",
            "Fri Dec 4, 3 to 5 PM",
        ]
        assert VENDOR_SETUP_WINDOWS == {"thu": "Thu Dec 3, 3 to 7 PM", "fri": "Fri Dec 4, 3 to 5 PM"}
        assert form.initial == {} and form["size"].value() == VendorTable.Size.SIX_FT
        body = kate_client.get(_page(hub)).content.decode()
        for placeholder in ("e.g. Riverbend Ceramics", "e.g. Mugs and planters", "e.g. Power, near a wall"):
            assert f'placeholder="{placeholder}"' in body
        assert "+ Add a table request</span>" in body
        assert ">Add request</button>" in body


def describe_the_list():
    def it_orders_requested_confirmed_waitlist_cancelled_then_by_name(hub):
        cancelled = VendorTableFactory(hub=hub, name="Aardvark", status=S.CANCELLED)
        waitlist = VendorTableFactory(hub=hub, name="Bee", status=S.WAITLIST)
        confirmed = VendorTableFactory(hub=hub, name="Cat", status=S.CONFIRMED)
        requested_b = VendorTableFactory(hub=hub, name="zebra", status=S.REQUESTED)
        requested_a = VendorTableFactory(hub=hub, name="Yak", status=S.REQUESTED)

        assert list(hub.vendor_tables.all()) == [requested_a, requested_b, confirmed, waitlist, cancelled]

    @pytest.mark.parametrize(
        ("show", "expected"),
        [
            ("all", {"Requested Co", "Confirmed Co", "Waitlist Co", "Cancelled Co"}),
            ("requested", {"Requested Co"}),
            ("confirmed", {"Confirmed Co"}),
            ("waitlist", {"Waitlist Co"}),
            ("cancelled", {"Cancelled Co"}),
        ],
    )
    def it_filters_by_status(hub, show, expected):
        for status in S:
            VendorTableFactory(hub=hub, name=f"{status.label} Co", status=status)

        assert {table.name for table in services.filtered_vendor_tables(hub, show)} == expected

    def it_shows_every_request_for_a_filter_it_does_not_know(hub, kate_client):
        VendorTableFactory(hub=hub, name="Spec Cancelled", status=S.CANCELLED)

        body = kate_client.get(f"{_page(hub)}?filter=nonsense").content.decode()

        assert services.vendor_filter("nonsense") == "all"
        assert services.vendor_filter("waitlist") == "waitlist"
        assert 'value="Spec Cancelled"' in body
        assert 'data-hq-vt-filter="all" aria-current="true"' in body

    def it_offers_lees_five_filters_and_marks_the_one_in_view(hub, kate_client):
        body = kate_client.get(f"{_page(hub)}?filter=waitlist").content.decode()

        assert [label for _, label in services.VENDOR_FILTERS] == [
            "All",
            "Requested",
            "Confirmed",
            "Waitlist",
            "Cancelled",
        ]
        assert f'href="{_page(hub)}" class="pl-hq-vt__filter" data-hq-vt-filter="all">All</a>' in body
        assert (
            f'href="{_page(hub)}?filter=waitlist" class="pl-hq-vt__filter" data-hq-vt-filter="waitlist" '
            'aria-current="true">Waitlist</a>'
        ) in body

    def it_says_so_when_the_list_or_the_group_is_empty(hub, kate_client):
        body = kate_client.get(_page(hub)).content.decode()
        assert "data-hq-vt-empty>No table requests yet. Add the first one above.</td>" in body

        VendorTableFactory(hub=hub, status=S.CANCELLED)

        body = kate_client.get(f"{_page(hub)}?filter=confirmed").content.decode()
        assert "data-hq-vt-empty>Nothing in this group.</td>" in body

    def it_makes_every_field_of_a_row_a_control_with_the_needs_under_the_name(hub, kate_client):
        guild = GuildFactory(name="Glass Guild")
        table = VendorTableFactory(
            hub=hub,
            name="Spec Glassworks",
            guild=guild,
            sells="Ornaments",
            size=VendorTable.Size.HALF,
            email="glass@spec.example",
            phone="503-555-0111",
            setup=VendorTable.Setup.FRI,
            needs="Power",
            status=S.CONFIRMED,
            table_no="12",
        )

        body = kate_client.get(_page(hub)).content.decode()
        row = body.split(f'data-hq-vt-row="{table.pk}"')[1].split("</tr>")[0]

        name_cell = row.split("pl-hq-vt__cell--name")[1].split("</td>")[0]
        assert 'value="Spec Glassworks"' in name_cell
        assert 'value="Power"' in name_cell
        form_id = f"hq-vt-row-{table.pk}"
        assert row.count(f'form="{form_id}"') == 10
        assert f'<option value="{guild.pk}" selected>Glass Guild</option>' in row
        assert '<option value="half" selected>Half table (share)</option>' in row
        assert '<option value="confirmed" selected>Confirmed</option>' in row
        assert '<option value="fri" selected>Fri Dec 4, 3 to 5 PM</option>' in row
        assert 'value="12"' in row and 'value="glass@spec.example"' in row and 'value="503-555-0111"' in row
        assert f'<form id="{form_id}" method="post" action="{_edit(table)}"' in row
        assert 'data-hq-vt-status="confirmed"' in body
        assert "pl-hq-vt__row--confirmed" in body

    def it_names_the_row_in_every_label_and_on_save_and_delete(hub, kate_client):
        table = VendorTableFactory(hub=hub, name="Spec O'Kiln")

        body = kate_client.get(_page(hub)).content.decode()
        row = body.split(f'data-hq-vt-row="{table.pk}"')[1].split("</tr>")[0]

        name = "Spec O&#x27;Kiln"
        labels = ("Artist or group", "Needs", "Guild", "What they sell", "Table size", "Status", "Table #", "Setup day")
        for label in (*labels, "Email", "Phone"):
            assert f"{label} for {name}" in row
        assert f'aria-label="Save {name}"' in row
        assert f'aria-label="Delete {name}"' in row

    def it_runs_a_fixed_number_of_queries_however_many_requests(hub, kate_client):
        def _count() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert kate_client.get(_page(hub)).status_code == 200
            return len(queries)

        VendorTableFactory(hub=hub, guild=GuildFactory(), created_by=MemberFactory())
        GuildFactory.create_batch(3)
        _count()  # the first request warms the session and the caches
        few = _count()
        for _ in range(4):
            VendorTableFactory(hub=hub, guild=GuildFactory(), created_by=MemberFactory())

        assert _count() == few

    def it_says_who_added_each_request_under_the_name(hub, kate, kate_client):
        table = VendorTableFactory(hub=hub, name="Spec Kilnworks", needs="Power", created_by=kate)

        body = kate_client.get(_page(hub)).content.decode()

        name_cell = body.split(f'data-hq-vt-row="{table.pk}"')[1].split("</td>")[0]
        assert "data-hq-vt-added-by>Added by Kate Lindqvist</p>" in name_cell
        assert (
            name_cell.index('value="Spec Kilnworks"') < name_cell.index("Added by") < name_cell.index('value="Power"')
        )

    def it_leaves_added_by_out_when_nobody_is_on_record(hub, kate_client):
        VendorTableFactory(hub=hub, created_by=None)
        VendorTableFactory(hub=hub, created_by=MemberFactory(preferred_name="", full_legal_name=" "))

        assert "data-hq-vt-added-by" not in kate_client.get(_page(hub)).content.decode()

    def it_reads_who_added_each_request_in_the_lists_one_query(hub, kate):
        VendorTableFactory.create_batch(3, hub=hub, created_by=kate)

        with CaptureQueriesContext(connection) as queries:
            names = [table.created_by.display_name for table in services.filtered_vendor_tables(hub, "all")]

        assert names == ["Kate Lindqvist"] * 3
        assert len(queries) == 1

    def it_keeps_added_by_on_the_row_a_save_sends_back(hub, kate, kate_client):
        table = VendorTableFactory(hub=hub, name="Spec Saved", created_by=kate)

        response = kate_client.post(_edit(table), _row_post(table, sells="Mugs"), **HTMX)

        assert "data-hq-vt-added-by>Added by Kate Lindqvist</p>" in response.content.decode()


def describe_editing_a_row():
    def it_saves_every_field_and_sends_back_only_the_row_and_the_band(hub, kate_client):
        guild = GuildFactory(name="Metal Guild")
        table = VendorTableFactory(hub=hub, name="Spec Forge", email="old@forge.example")

        response = kate_client.post(
            _edit(table),
            _row_post(
                table,
                name="Spec Forge and Anvil",
                guild=str(guild.pk),
                sells="Hooks",
                size=VendorTable.Size.OWN,
                email="New@Forge.example",
                phone="503-555-0123",
                setup=VendorTable.Setup.FRI,
                needs="Near the door",
                status=S.CONFIRMED,
                table_no=" 7 ",
                filter="requested",
            ),
            **HTMX,
        )

        table.refresh_from_db()
        body = response.content.decode()
        assert _toast(response) == "Saved."
        assert (table.name, table.guild, table.sells, table.size, table.email) == (
            "Spec Forge and Anvil",
            guild,
            "Hooks",
            VendorTable.Size.OWN,
            "new@forge.example",
        )
        assert (table.phone, table.setup, table.needs, table.status, table.table_no) == (
            "503-555-0123",
            VendorTable.Setup.FRI,
            "Near the door",
            S.CONFIRMED,
            "7",
        )
        assert f'<tr class="pl-hq-vt__row pl-hq-vt__row--confirmed" data-hq-vt-row="{table.pk}"' in body
        assert 'id="hq-vt-band" aria-label="Table capacity" data-hq-vt-band hx-swap-oob="true"' in body
        assert "data-hq-vt-figure>1</strong>" in body
        assert "data-hq-vt-board" not in body
        assert "data-hq-vt-add-form" not in body
        assert '<input type="hidden" name="filter" value="requested">' in body

    def it_clears_the_guild_to_not_in_a_guild(hub, kate_client):
        table = VendorTableFactory(hub=hub, guild=GuildFactory())

        kate_client.post(_edit(table), _row_post(table, guild=""), **HTMX)

        table.refresh_from_db()
        assert table.guild is None

    def it_keeps_a_guild_that_went_inactive_on_the_row_that_names_it(hub, kate_client):
        sleeping = GuildFactory(name="Spec Sleeping Guild", is_active=False)
        table = VendorTableFactory(hub=hub, guild=sleeping)
        other = VendorTableFactory(hub=hub)

        body = kate_client.get(_page(hub)).content.decode()
        row = body.split(f'data-hq-vt-row="{table.pk}"')[1].split("</tr>")[0]
        other_row = body.split(f'data-hq-vt-row="{other.pk}"')[1].split("</tr>")[0]
        response = kate_client.post(_edit(table), _row_post(table, needs="Shade"), **HTMX)

        table.refresh_from_db()
        assert f'<option value="{sleeping.pk}" selected>Spec Sleeping Guild</option>' in row
        assert "Spec Sleeping Guild" not in other_row
        assert _toast(response) == "Saved."
        assert (table.guild, table.needs) == (sleeping, "Shade")

    def it_brings_back_a_refused_row_with_its_errors_and_saves_nothing(hub, kate_client):
        table = VendorTableFactory(hub=hub, name="Spec Kept", email="ok@spec.example")

        response = kate_client.post(
            _edit(table), _row_post(table, email="nope", status=S.CANCELLED, name="Spec Changed"), **HTMX
        )

        body = response.content.decode()
        table.refresh_from_db()
        assert response.status_code == 200
        assert "HX-Trigger" not in response
        assert (table.email, table.status, table.name) == ("ok@spec.example", S.REQUESTED, "Spec Kept")
        assert f'<tr class="pl-hq-vt__row pl-hq-vt__row--requested" data-hq-vt-row="{table.pk}"' in body
        assert 'class="pl-field-errors"' in body
        assert 'value="nope"' in body
        assert 'value="Spec Changed"' in body

    def it_lands_a_plain_post_back_on_the_page_with_a_message(hub, kate_client):
        table = VendorTableFactory(hub=hub, name="Spec Studio")

        response = kate_client.post(_edit(table), _row_post(table, filter="waitlist"), follow=True)

        assert response.redirect_chain == [(f"{_page(hub)}?filter=waitlist", 302)]
        assert "Spec Studio saved." in [str(message) for message in response.context["messages"]]

    def it_renders_the_page_with_the_refused_row_in_view_for_a_plain_post(hub, kate_client):
        table = VendorTableFactory(hub=hub, name="Spec Requested", status=S.REQUESTED)

        response = kate_client.post(_edit(table), _row_post(table, email="nope", filter="cancelled"))

        body = response.content.decode()
        assert response.status_code == 200
        assert "data-hq-vt-subline" in body
        assert 'value="nope"' in body
        assert 'class="pl-field-errors"' in body
        table.refresh_from_db()
        assert table.email == ""


def describe_a_stale_save():
    def it_carries_the_rows_stamp_in_its_form(hub, kate_client):
        table = VendorTableFactory(hub=hub)

        body = kate_client.get(_page(hub)).content.decode()

        assert (
            f'<input type="hidden" name="vendor-{table.pk}-updated_at" value="{table.updated_at.isoformat()}"' in body
        )

    def it_refuses_a_save_from_an_older_page_and_brings_the_row_back_as_the_database_holds_it(hub, kate_client):
        table = VendorTableFactory(hub=hub, name="Spec Original", needs="Power")
        stale = _row_post(table, needs="Shade", status=S.CANCELLED)
        other = VendorTable.objects.get(pk=table.pk)  # someone else saves the row after this page loaded
        other.needs = "A wall"
        other.save()

        response = kate_client.post(_edit(table), stale, **HTMX)

        body = response.content.decode()
        table.refresh_from_db()
        assert response.status_code == 200
        assert "HX-Trigger" not in response
        assert (table.needs, table.status) == ("A wall", S.REQUESTED)
        assert (
            "data-hq-vt-row-error>Someone else changed this table. Your page has been refreshed; "
            "make your change again.</p>"
        ) in body
        assert f'<tr class="pl-hq-vt__row pl-hq-vt__row--requested" data-hq-vt-row="{table.pk}"' in body
        assert 'value="A wall"' in body and 'value="Shade"' not in body
        assert f'value="{table.updated_at.isoformat()}"' in body
        assert 'id="hq-vt-band" aria-label="Table capacity" data-hq-vt-band hx-swap-oob="true"' in body

    def it_refuses_a_save_with_no_stamp(hub, kate_client):
        table = VendorTableFactory(hub=hub, needs="Power")
        data = _row_post(table, needs="Shade")
        del data[f"vendor-{table.pk}-updated_at"]

        body = kate_client.post(_edit(table), data, **HTMX).content.decode()

        table.refresh_from_db()
        assert table.needs == "Power"
        assert "data-hq-vt-row-error>Someone else changed this table." in body

    def it_saves_again_from_the_row_it_sent_back(hub, kate_client):
        table = VendorTableFactory(hub=hub, needs="Power")
        first = kate_client.post(_edit(table), _row_post(table, needs="Shade"), **HTMX)
        table.refresh_from_db()

        second = kate_client.post(_edit(table), _row_post(table, needs="Shade and a wall"), **HTMX)

        table.refresh_from_db()
        assert _toast(first) == _toast(second) == "Saved."
        assert table.needs == "Shade and a wall"
        assert f'value="{table.updated_at.isoformat()}"' in second.content.decode()

    def it_renders_the_page_with_the_row_as_saved_and_the_message_for_a_plain_post(hub, kate_client):
        table = VendorTableFactory(hub=hub, name="Spec Plain", needs="Power", status=S.REQUESTED)
        stale = _row_post(table, needs="Shade", filter="cancelled")
        table.save()

        response = kate_client.post(_edit(table), stale)

        body = response.content.decode()
        row = body.split(f'data-hq-vt-row="{table.pk}"')[1].split("</tr>")[0]
        assert response.status_code == 200
        assert "data-hq-vt-subline" in body
        assert "data-hq-vt-row-error>Someone else changed this table." in row
        assert 'value="Power"' in row
        assert body.count("data-hq-vt-row-error") == 1
        table.refresh_from_db()
        assert table.needs == "Power"


def describe_deleting_a_request():
    def it_takes_the_request_off_for_an_organiser(hub, kate_client):
        table = VendorTableFactory(hub=hub, name="Spec Gone Studio")

        response = kate_client.post(_delete(table), {"filter": "requested"}, follow=True)

        assert response.redirect_chain == [(f"{_page(hub)}?filter=requested", 302)]
        assert "Spec Gone Studio is off the list." in [str(message) for message in response.context["messages"]]
        assert not VendorTable.objects.exists()

    def it_asks_first_through_the_confirm_modal(hub, kate_client):
        table = VendorTableFactory(hub=hub)

        body = kate_client.get(_page(hub)).content.decode()

        assert f"$dispatch('open-confirm', 'delete-vendor-{table.pk}')" in body
        assert f'action="{_delete(table)}"' in body
        assert "Delete this table request?" in body


def describe_the_space_map_capacity():
    def it_saves_the_capacity_over_htmx_and_sends_the_band_out_of_band(hub, admin_client):
        VendorTableFactory.create_batch(3, hub=hub, status=S.CONFIRMED)

        response = admin_client.post(_floor(hub), {"capacity": "24", "map_note": " 18 in the gallery "}, **HTMX)

        floor = VendorFloor.objects.get()
        body = response.content.decode()
        assert _toast(response) == "Capacity saved."
        assert (floor.hub, floor.capacity, floor.map_note) == (hub, 24, "18 in the gallery")
        assert "open data-hq-vt-floor" in body
        assert 'value="24"' in body
        assert 'hx-swap-oob="true"' in body
        assert "data-hq-vt-figure>21</strong>" in body
        assert "data-hq-vt-helper>of 24 tables on the map</span>" in body
        assert "data-hq-vt-board" not in body

    def it_updates_the_one_map_and_clears_it_when_left_blank(hub, admin_client):
        VendorFloorFactory(hub=hub, capacity=10, map_note="old")

        admin_client.post(_floor(hub), {"capacity": "", "map_note": ""}, **HTMX)

        floor = VendorFloor.objects.get()
        assert (floor.capacity, floor.map_note) == (None, "")

    @pytest.mark.parametrize("capacity", ["0", "-1", "1001", "abc", "2.5"])
    def it_refuses_a_capacity_out_of_range(hub, admin_client, capacity):
        response = admin_client.post(_floor(hub), {"capacity": capacity}, **HTMX)

        body = response.content.decode()
        assert "HX-Trigger" not in response
        assert 'class="pl-field-errors"' in body
        assert "open data-hq-vt-floor" in body
        assert not VendorFloor.objects.exists()

    def it_lands_a_plain_post_back_on_the_page_with_a_message(hub, admin_client):
        response = admin_client.post(_floor(hub), {"capacity": "12"}, follow=True)

        assert response.redirect_chain == [(_page(hub), 302)]
        assert "Capacity saved." in [str(message) for message in response.context["messages"]]

    def it_renders_the_page_open_with_the_errors_for_a_plain_post_that_fails(hub, admin_client):
        response = admin_client.post(_floor(hub), {"capacity": "0"})

        body = response.content.decode()
        assert "data-hq-vt-subline" in body
        assert "open data-hq-vt-floor" in body

    def it_starts_closed_with_lees_words_and_the_saved_figures(hub, admin_client):
        VendorFloorFactory(hub=hub, capacity=18, map_note="Gallery only")

        body = admin_client.get(_page(hub)).content.decode()

        assert '<details class="pl-disclosure pl-hq-vt__disclosure"  data-hq-vt-floor>' in body
        assert "Space map capacity</span>" in body
        assert (
            "data-hq-vt-floor-helper>Damon and Dixie are mapping how many tables fit. Enter the number when it's "
            "ready and this page will show space left and start a waitlist.</p>"
        ) in body.replace("&#x27;", "'")
        assert 'value="18"' in body and 'value="Gallery only"' in body
        assert ">Save capacity</button>" in body
        assert 'placeholder="e.g. 24"' in body and 'placeholder="e.g. 18 in the gallery, 6 in the shop"' in body


def describe_the_csv():
    def it_streams_one_row_per_request_in_list_order(hub, kate, admin_client):
        VendorTableFactory(
            hub=hub,
            name="Spec Studio",
            guild=GuildFactory(name="Jewelry Guild"),
            sells="=HYPERLINK(1)",
            size=VendorTable.Size.FOUR_FT,
            status=S.CONFIRMED,
            table_no="4",
            setup=VendorTable.Setup.THU,
            email="studio@spec.example",
            phone="503-555-0100",
            needs="Power",
            created_by=kate,
        )
        VendorTableFactory(hub=hub, name="Spec Requested")
        VendorTableFactory(name="Elsewhere")

        response = admin_client.get(_csv(hub))

        rows = list(csv.reader(io.StringIO(b"".join(response.streaming_content).decode())))
        assert response["Content-Type"] == "text/csv"
        assert response["Content-Disposition"].startswith(f'attachment; filename="{hub.slug}-vendor-tables-')
        assert rows[0] == VENDOR_TABLES_CSV_HEADERS
        assert [row[0] for row in rows[1:]] == ["Spec Requested", "Spec Studio"]
        studio = dict(zip(VENDOR_TABLES_CSV_HEADERS, rows[2], strict=True))
        assert studio == {
            **studio,
            "Guild": "Jewelry Guild",
            "What they sell": "'=HYPERLINK(1)",
            "Table size": "4 ft",
            "Status": "Confirmed",
            "Table #": "4",
            "Setup day": "Thu Dec 3, 3 to 7 PM",
            "Email": "studio@spec.example",
            "Phone": "503-555-0100",
            "Needs": "Power",
            "Added by": "Kate Lindqvist",
        }
        requested = dict(zip(VENDOR_TABLES_CSV_HEADERS, rows[1], strict=True))
        assert (requested["Guild"], requested["Setup day"], requested["Added by"]) == (
            "Not in a guild",
            "Not chosen",
            "",
        )

    def it_links_the_download_out_of_hx_boost(hub, admin_client):
        body = admin_client.get(_page(hub)).content.decode()

        assert f'href="{_csv(hub)}" class="pl-hq-vt__csv" hx-boost="false" data-pl-download' in body


def describe_the_member_tables_goal():
    def it_counts_the_confirmed_tables_live_as_a_plain_count(hub, django_assert_num_queries):
        HubGoalFactory(hub=hub, label="Member tables", kind=HubGoal.Kind.MEMBER_TABLES, target=0, sort_order=0)
        HubGoalFactory(hub=hub, label="Tables of 20", kind=HubGoal.Kind.MEMBER_TABLES, target=20, sort_order=1)
        VendorTableFactory.create_batch(3, hub=hub, status=S.CONFIRMED)
        VendorTableFactory(hub=hub, status=S.REQUESTED)
        VendorTableFactory(status=S.CONFIRMED)  # another hub's

        with django_assert_num_queries(3):
            plain, measured = services.goal_rows(hub)

        assert (plain.current, plain.has_target, plain.percent) == (3, False, 0)
        assert (measured.current, measured.has_target, measured.percent) == (3, True, 15)

    def it_renders_a_goal_with_no_target_without_of_or_a_meter(hub, kate_client):
        HubGoalFactory(hub=hub, label="Member tables", kind=HubGoal.Kind.MEMBER_TABLES, target=0, sort_order=0)
        HubGoalFactory(hub=hub, label="Bottles", kind=HubGoal.Kind.BOTTLES, target=100, sort_order=1)
        VendorTableFactory.create_batch(2, hub=hub, status=S.CONFIRMED)

        body = kate_client.get(hub.get_absolute_url()).content.decode()
        plain = body.split("Member tables</span>")[1].split("</div>")[0]

        assert '<strong class="pl-hq-goal__value" data-hq-goal-count>2</strong>' in plain
        assert "of 0" not in plain
        assert "<progress" not in plain
        assert "0 <small>of 100</small></strong>" in body

    def it_is_one_of_lees_goals_with_no_target(hub):
        member_tables = next(goal for goal in GOALS if goal.kind == HubGoal.Kind.MEMBER_TABLES)

        assert (member_tables.label, member_tables.kind, member_tables.target) == (
            "Member tables",
            HubGoal.Kind.MEMBER_TABLES,
            0,
        )

    def it_is_offered_by_the_goals_formset_on_edit_hq(open_hub, admin_client):
        assert (HubGoal.Kind.MEMBER_TABLES, "Vendor tables confirmed") in HubGoalForm().fields["kind"].choices  # type: ignore[attr-defined]

        response = admin_client.post(
            reverse("hq:edit", args=[open_hub.slug]),
            {
                "title": open_hub.title,
                "slug": open_hub.slug,
                "eyebrow": open_hub.eyebrow,
                "event": "",
                "intro": "",
                "footer_note": "",
                "is_open": "on",
                "goals-TOTAL_FORMS": "1",
                "goals-INITIAL_FORMS": "0",
                "goals-MIN_NUM_FORMS": "0",
                "goals-MAX_NUM_FORMS": "1000",
                "goals-0-label": "Member tables",
                "goals-0-kind": HubGoal.Kind.MEMBER_TABLES,
                "goals-0-target": "0",
                "goals-0-current_value": "0",
            },
        )

        assert response.status_code == 302
        goal = open_hub.goals.get()
        assert (goal.label, goal.kind, goal.target) == ("Member tables", HubGoal.Kind.MEMBER_TABLES, 0)


def describe_adopting_the_placeholder():
    def it_turns_the_vendor_tables_placeholder_into_the_live_door_in_place(open_hub, member):
        placeholder = HubCardFactory(
            hub=open_hub,
            kind=HubCard.Kind.PLACEHOLDER,
            title="Vendor Tables",
            bullets="Every table and who is at it\nLoad in times and what each vendor needs\nA map of the floor",
            button_text="See the tables",
            audience=HubCard.Audience.MEMBERS,
            team=[member],
        )

        result = open_hub.seed_default_cards()

        card = HubCard.objects.get(pk=placeholder.pk)
        assert "Vendor Tables" in result.adopted
        assert (card.kind, card.audience, card.button_text) == (
            HubCard.Kind.VENDOR_TABLES,
            HubCard.Audience.MEMBERS,
            "Enter vendor tables",
        )
        assert card.bullet_lines[0] == "Every table request in one place"
        assert list(card.team.all()) == [member]
        assert card.url == _page(open_hub)


def describe_the_model():
    def it_reads_as_its_name_and_lowercases_the_email(hub):
        table = VendorTableFactory(hub=hub, name="Spec Studio", email="  Rae@Spec.Example ")

        assert str(table) == "Spec Studio"
        assert table.email == "rae@spec.example"
        assert (table.size, table.setup, table.status) == (VendorTable.Size.SIX_FT, "", S.REQUESTED)
        assert table.get_setup_display() == "Not chosen"

    def it_names_its_guild_or_says_not_in_a_guild():
        guild = Guild(name="Textiles Guild")

        assert VendorTable(guild=guild).guild_display == "Textiles Guild"
        assert VendorTable().guild_display == "Not in a guild"

    def it_reads_the_setup_labels_from_lees_windows():
        assert str(VendorTable.Setup.THU.label) == VENDOR_SETUP_WINDOWS["thu"]
        assert VendorTable(setup=VendorTable.Setup.FRI).get_setup_display() == VENDOR_SETUP_WINDOWS["fri"]

    def it_reads_the_floor_as_its_hub_and_capacity(hub):
        assert str(VendorFloorFactory(hub=hub, capacity=24)) == f"{hub.title}: 24"
        assert str(VendorFloor(hub=hub)) == f"{hub.title}: not mapped"

    def it_drops_a_deleted_guild_from_the_request_but_keeps_the_request(hub):
        guild = GuildFactory()
        table = VendorTableFactory(hub=hub, guild=guild)

        Guild.all_objects.filter(pk=guild.pk).delete()

        table.refresh_from_db()
        assert table.guild is None
