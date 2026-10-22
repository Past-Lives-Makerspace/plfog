"""HQ card sharing: Can open and Team on each card, the Team-only doors, the admin-only settings and uploads, the
share panel and the HQ page's door list (docs/superpowers/plans/2026-10-22-hq-card-sharing.md).

Every permission edge is a crafted request: a Can open member on each Team action, a Team member on each admin-only
action, a stranger at each closed door, and an admin previewing as a member, who sees that member's buttons.
"""

from __future__ import annotations

import json

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from hq.forms import CardSharingForm
from hq.models import TEAM_ONLY_KINDS, HubCard
from hub.view_as import ROLE_MEMBER
from membership.models import Member
from tests.hq.conftest import signed_in
from tests.hq.factories import BudgetLineFactory, EventHubFactory, HubCardFactory

ADMINS = HubCard.Audience.ADMINS
MEMBERS = HubCard.Audience.MEMBERS
PUBLIC = HubCard.Audience.PUBLIC
Kind = HubCard.Kind

# A pk no row has: the gate answers before the view looks the row up, so a refusal is the gate's.
NO_ROW = 999999


def _url(name: str, hub_slug: str, *args: object) -> str:
    return reverse(f"hq:{name}", args=[hub_slug, *args])


# The Team's work behind each door a Can open member may also open: (url name, extra args, method).
TEAM_ACTIONS: dict[str, list[tuple[str, tuple[object, ...], str]]] = {
    Kind.VOLUNTEERS: [("volunteer_roster", (), "get"), ("volunteer_roster_csv", (), "get")],
    Kind.WINE_WALL: [("wine_donors", (), "get"), ("wine_donors_csv", (), "get"), ("wine_donor_thanked", (), "post")],
    Kind.MARKETING: [("marketing_add", (), "post"), ("marketing_delete", (NO_ROW,), "post")],
    Kind.SPONSORSHIP: [
        ("sponsor_add", (), "post"),
        ("sponsor_edit", (NO_ROW,), "post"),
        ("sponsor_log", (NO_ROW,), "post"),
        ("sponsor_delete", (NO_ROW,), "post"),
        ("sponsors_csv", (), "get"),
    ],
    Kind.MARKET_CHECKIN: [
        ("checkin_reports", (), "get"),
        ("checkin_hours", (), "get"),
        ("checkin_reports_csv", (), "get"),
        ("checkin_hours_csv", (), "get"),
    ],
}

# The settings and uploads, admins only whatever the card's lists say: (door, url name, extra args).
ADMIN_ACTIONS: list[tuple[str, str, tuple[object, ...]]] = [
    (Kind.WINE_WALL, "winery_logo", ()),
    (Kind.VENDOR_TABLES, "vendor_floor", ()),
    (Kind.FLYER, "flyer_upload", ("flyer",)),
    (Kind.FLYER, "flyer_remove", ("flyer",)),
    (Kind.FLYER, "flyer_links", ()),
    (Kind.LEDGER, "ledger_approval", ()),
    (Kind.LEDGER, "ledger_line_add", ()),
    (Kind.LEDGER, "ledger_line_edit", (NO_ROW,)),
    (Kind.LEDGER, "ledger_line_delete", (NO_ROW,)),
    (Kind.LEDGER, "ledger_bar_prices", ()),
    (Kind.LEDGER, "ledger_ticket_prices", ()),
]

# Each door's page, for the stranger at a closed door.
DOOR_PAGES = {
    Kind.VOLUNTEERS: "volunteers",
    Kind.WINE_WALL: "wine",
    Kind.MARKETING: "marketing",
    Kind.SPONSORSHIP: "sponsors",
    Kind.VENDOR_TABLES: "vendors",
    Kind.MARKET_CHECKIN: "checkin",
    Kind.FLYER: "flyer",
    Kind.LEDGER: "ledger",
}


def _door(hub, kind: str, **kwargs) -> HubCard:
    return HubCardFactory(hub=hub, kind=kind, title=f"{kind} door", **kwargs)


def _call(client: Client, method: str, url: str) -> int:
    return (client.post(url, {}) if method == "post" else client.get(url)).status_code


def describe_who_may_open_a_card():
    @pytest.fixture
    def people(make_member):
        return {
            "active": make_member(),
            "guest": make_member(status=Member.Status.GUEST),
            "listed": make_member(status=Member.Status.GUEST),
            "team": make_member(),
        }

    @pytest.mark.parametrize("audience", [ADMINS, MEMBERS, PUBLIC])
    def it_opens_to_the_can_open_people_and_the_team_whatever_the_audience(open_hub, people, audience):
        card = _door(open_hub, Kind.WINE_WALL, audience=audience, can_open=[people["listed"]], team=[people["team"]])

        assert card.is_open_to(people["listed"], is_admin=False)
        assert card.is_open_to(people["team"], is_admin=False)
        assert card.can_organize(people["team"], is_admin=False)
        assert not card.can_organize(people["listed"], is_admin=False)

    @pytest.mark.parametrize("kind", sorted(TEAM_ONLY_KINDS))
    def it_keeps_a_team_only_door_to_the_team_whatever_the_database_says(open_hub, people, kind):
        card = _door(open_hub, kind, audience=PUBLIC, can_open=[people["listed"]], team=[people["team"]])

        assert card.is_open_to(people["team"], is_admin=False)
        assert not card.is_open_to(people["listed"], is_admin=False)
        assert not card.is_open_to(people["active"], is_admin=False)
        assert not card.is_open_to(None, is_admin=False)
        assert card.is_open_to(None, is_admin=True)

    @pytest.mark.parametrize("kind", sorted(TEAM_ONLY_KINDS))
    def it_refuses_a_wider_audience_on_a_team_only_door(open_hub, kind):
        card = HubCard(hub=open_hub, kind=kind, title="Door", audience=MEMBERS)

        with pytest.raises(ValidationError) as refused:
            card.full_clean()

        assert "for the Team only" in str(refused.value)

    @pytest.mark.parametrize("kind", [Kind.WINE_WALL, Kind.LEDGER])
    @pytest.mark.parametrize("audience", [ADMINS, MEMBERS, PUBLIC])
    @pytest.mark.parametrize("hidden", [False, True])
    def it_answers_the_same_as_one_query(open_hub, people, kind, audience, hidden):
        """``HubCard.objects.open_to`` is ``is_open_to`` (hidden cards left out) for every viewer."""
        card = _door(
            open_hub, kind, audience=audience, is_hidden=hidden, can_open=[people["listed"]], team=[people["team"]]
        )
        for viewer in [None, *people.values()]:
            expected = not hidden and card.is_open_to(viewer, is_admin=False)
            assert HubCard.objects.open_to(viewer).filter(pk=card.pk).exists() is expected, (viewer, audience)

    def it_counts_each_person_once_on_the_chip(open_hub, people):
        card = _door(open_hub, Kind.MARKETING, can_open=[people["listed"], people["team"]], team=[people["team"]])

        assert card.people_count == 2
        assert card.audience_chip == "Admins only"


def describe_a_can_open_member_at_the_teams_work():
    @pytest.mark.parametrize("kind", sorted(TEAM_ACTIONS))
    def it_gets_a_403_from_every_team_action(open_hub, make_member, kind):
        listed = make_member()
        _door(open_hub, kind, can_open=[listed])
        client = signed_in(listed)

        for name, args, method in TEAM_ACTIONS[kind]:
            assert _call(client, method, _url(name, open_hub.slug, *args)) == 403, name

    @pytest.mark.parametrize("kind", sorted(TEAM_ONLY_KINDS))
    def it_is_refused_a_team_only_door_even_when_the_database_lists_them(open_hub, make_member, kind):
        listed = make_member()
        _door(open_hub, kind, audience=PUBLIC, can_open=[listed])
        client = signed_in(listed)
        writes = {Kind.VENDOR_TABLES: "vendor_add", Kind.LEDGER: "ledger_entry_add"}

        assert client.get(_url(DOOR_PAGES[kind], open_hub.slug)).status_code == 404
        assert client.post(_url(writes[kind], open_hub.slug), {}).status_code == 403


def describe_a_team_member_at_the_admin_settings():
    @pytest.mark.parametrize(("kind", "name", "args"), ADMIN_ACTIONS)
    def it_gets_a_403_from_every_admin_only_action(open_hub, make_member, kind, name, args):
        team = make_member()
        _door(open_hub, kind, team=[team])

        assert signed_in(team).post(_url(name, open_hub.slug, *args), {}).status_code == 403

    @pytest.mark.parametrize(("kind", "name", "args"), ADMIN_ACTIONS)
    def it_still_lets_an_admin_through_the_gate(open_hub, admin_client, kind, name, args):
        _door(open_hub, kind)

        assert admin_client.post(_url(name, open_hub.slug, *args), {}).status_code != 403

    def it_404s_a_hub_without_that_door(open_hub, admin_client):
        assert admin_client.post(_url("ledger_approval", open_hub.slug), {}).status_code == 404


def describe_a_stranger_at_a_closed_door():
    @pytest.mark.parametrize("kind", sorted(DOOR_PAGES))
    def it_gets_a_404(open_hub, member_client, kind):
        _door(open_hub, kind, audience=ADMINS)
        # Something the stranger may open, so the HQ itself is not what 404s.
        _door(open_hub, Kind.RSVP, audience=PUBLIC)

        assert member_client.get(_url(DOOR_PAGES[kind], open_hub.slug)).status_code == 404


def describe_the_admin_only_buttons():
    """Every admin-only control disappears for the Team, by the effective role, while their own work stays."""

    @pytest.fixture
    def team(make_member):
        return make_member()

    def it_hides_the_ledgers_settings_from_the_team(open_hub, team, admin_client):
        _door(open_hub, Kind.LEDGER, team=[team])
        BudgetLineFactory(hub=open_hub, name="Kegs")

        team_body = signed_in(team).get(_url("ledger", open_hub.slug)).content.decode()
        admin_body = admin_client.get(_url("ledger", open_hub.slug)).content.decode()

        for marker in [
            "data-hq-lg-approval-form",
            "data-hq-lg-prices-form",
            "data-hq-lg-ticket-prices-form",
            "data-hq-lg-line-add-form",
            "data-hq-lg-line-form",
            "data-hq-lg-line-delete",
            "data-hq-lg-line-edit",
        ]:
            assert marker not in team_body, marker
            assert marker in admin_body, marker
        for marker in ["data-hq-lg-log-form", "data-hq-lg-quick-form", "data-hq-lg-tickets-form"]:
            assert marker in team_body, marker

    def it_hides_the_vendor_floor_from_the_team(open_hub, team, admin_client):
        _door(open_hub, Kind.VENDOR_TABLES, team=[team])

        team_body = signed_in(team).get(_url("vendors", open_hub.slug)).content.decode()
        admin_body = admin_client.get(_url("vendors", open_hub.slug)).content.decode()

        assert "data-hq-vt-floor-form" not in team_body
        assert "data-hq-vt-add-form" in team_body
        assert "data-hq-vt-floor-form" in admin_body

    def it_hides_update_this_page_from_the_flyers_team(open_hub, team, admin_client):
        _door(open_hub, Kind.FLYER, team=[team])

        assert "data-hq-fl-organizers" not in signed_in(team).get(_url("flyer", open_hub.slug)).content.decode()
        assert "data-hq-fl-organizers" in admin_client.get(_url("flyer", open_hub.slug)).content.decode()

    def it_hides_the_logo_form_from_the_walls_team_but_keeps_the_donors(open_hub, team, admin_client):
        _door(open_hub, Kind.WINE_WALL, team=[team])

        team_body = signed_in(team).get(_url("wine", open_hub.slug)).content.decode()

        assert "data-hq-logo-form" not in team_body
        assert "data-hq-wine-organizers" in team_body
        assert "data-hq-logo-form" in admin_client.get(_url("wine", open_hub.slug)).content.decode()

    def it_shows_an_admin_previewing_as_a_member_that_members_buttons(open_hub, admin, admin_client):
        _door(open_hub, Kind.LEDGER, team=[admin])
        session = admin_client.session
        session["view_as_role"] = ROLE_MEMBER
        session.save()

        body = admin_client.get(_url("ledger", open_hub.slug)).content.decode()

        # On the Team, so the Team's work shows; previewing a member, so the settings do not.
        assert "data-hq-lg-log-form" in body
        assert "data-hq-lg-approval-form" not in body
        assert "data-hq-lg-prices-form" not in body
        # The POST still reads the actual role.
        assert admin_client.post(_url("ledger_approval", open_hub.slug), {}).status_code != 403


def describe_the_hq_page():
    def it_shows_a_can_open_member_only_the_cards_opened_to_them(open_hub, make_member):
        listed = make_member()
        mine = _door(open_hub, Kind.WINE_WALL, can_open=[listed])
        _door(open_hub, Kind.MARKETING)

        body = signed_in(listed).get(_url("page", open_hub.slug)).content.decode()

        assert f'data-hq-card-pk="{mine.pk}"' in body
        assert body.count("data-hq-card-pk=") == 1

    def it_is_the_themed_404_for_a_signed_in_member_with_no_card(open_hub, member_client):
        _door(open_hub, Kind.WINE_WALL, audience=ADMINS)

        assert member_client.get(_url("page", open_hub.slug)).status_code == 404

    def it_shows_a_signed_out_visitor_the_everyone_cards_and_404s_without_one(open_hub):
        visitor = Client()
        _door(open_hub, Kind.MARKETING, audience=MEMBERS)

        assert visitor.get(_url("page", open_hub.slug)).status_code == 404
        public = _door(open_hub, Kind.WINE_WALL, audience=PUBLIC)
        body = visitor.get(_url("page", open_hub.slug)).content.decode()
        assert f'data-hq-card-pk="{public.pk}"' in body
        assert body.count("data-hq-card-pk=") == 1

    def it_shows_admins_the_chip_and_a_share_button_on_every_card(open_hub, admin_client, make_member):
        card = _door(open_hub, Kind.SPONSORSHIP, audience=MEMBERS, can_open=[make_member()], team=[make_member()])
        _door(open_hub, Kind.MARKETING, audience=PUBLIC)

        body = admin_client.get(_url("page", open_hub.slug)).content.decode()

        assert f'id="hq-share-chip-{card.pk}"' in body
        assert "Members · 2 people</span>" in body
        assert "Everyone</span>" in body
        assert f'hx-get="{_url("card_share", open_hub.slug, card.pk)}"' in body
        assert f'id="hq-share-{card.pk}-body"' in body

    def it_shows_no_chip_or_share_to_a_member(open_hub, make_member):
        listed = make_member()
        _door(open_hub, Kind.WINE_WALL, can_open=[listed])

        body = signed_in(listed).get(_url("page", open_hub.slug)).content.decode()

        assert "data-hq-share-chip" not in body
        assert "data-hq-card-share" not in body


def describe_the_share_panel():
    @pytest.fixture
    def card(open_hub):
        return _door(open_hub, Kind.WINE_WALL)

    def _share(card: HubCard) -> str:
        return _url("card_share", card.hub.slug, card.pk)

    def it_is_admins_only_by_the_actual_role(card, member_client, make_member):
        team = make_member()
        card.team.add(team)

        assert member_client.get(_share(card)).status_code == 403
        assert signed_in(team).post(_share(card), {"audience": PUBLIC}).status_code == 403
        assert Client().get(_share(card)).status_code == 302

    def it_loads_the_panel_into_the_modal_over_htmx(card, admin_client):
        response = admin_client.get(_share(card), HTTP_HX_REQUEST="true")
        body = response.content.decode()

        assert "<html" not in body
        assert f'data-hq-share-form="{card.pk}"' in body
        assert f'hx-target="#hq-share-{card.pk}-body"' in body
        assert 'data-hq-people="can_open"' in body
        assert 'data-hq-people="team"' in body
        assert "Anyone with the link, signed in or not." in body

    def it_is_its_own_page_with_no_script(card, admin_client):
        body = admin_client.get(_share(card)).content.decode()

        assert "data-hq-share-page" in body
        assert f'data-hq-share-form="{card.pk}"' in body
        assert "hx-post" not in body.split("data-hq-share-page", 1)[1].split("</form>", 1)[0]

    def it_saves_in_one_post_over_htmx_with_the_chip_out_of_band_and_a_toast(card, admin_client, make_member):
        one, two = make_member(), make_member()

        response = admin_client.post(
            _share(card), {"audience": MEMBERS, "can_open": [one.pk], "team": [two.pk]}, HTTP_HX_REQUEST="true"
        )
        body = response.content.decode()
        trigger = json.loads(response["HX-Trigger"])

        card.refresh_from_db()
        assert card.audience == MEMBERS
        assert list(card.can_open.all()) == [one]
        assert list(card.team.all()) == [two]
        assert f'id="hq-share-chip-{card.pk}" hx-swap-oob="true"' in body
        assert "Members · 2 people" in body
        assert trigger["showToast"]["type"] == "success"
        assert trigger["hq-share-saved"] == {"pk": card.pk}

    def it_saves_a_plain_post_and_lands_on_the_hq(card, admin_client, make_member):
        one = make_member()

        response = admin_client.post(_share(card), {"audience": PUBLIC, "team": [one.pk]})

        assert response.status_code == 302
        assert response["Location"] == card.hub.get_absolute_url()
        card.refresh_from_db()
        assert card.audience == PUBLIC
        assert list(card.team.all()) == [one]

    def it_empties_both_lists_when_every_chip_is_removed(card, admin_client, make_member):
        card.can_open.add(make_member())
        card.team.add(make_member())

        admin_client.post(_share(card), {"audience": ADMINS})

        assert card.can_open.count() == 0
        assert card.team.count() == 0

    def it_keeps_someone_on_both_lists_on_the_team_alone(card, admin_client, make_member):
        both = make_member()

        admin_client.post(_share(card), {"audience": ADMINS, "can_open": [both.pk], "team": [both.pk]})

        assert list(card.team.all()) == [both]
        assert card.can_open.count() == 0

    @pytest.mark.parametrize("kind", sorted(TEAM_ONLY_KINDS))
    def it_offers_a_team_only_door_admins_only_and_the_team_and_refuses_more(open_hub, admin_client, make_member, kind):
        door = _door(open_hub, kind)
        member = make_member()

        body = admin_client.get(_share(door), HTTP_HX_REQUEST="true").content.decode()
        widened = admin_client.post(_share(door), {"audience": MEMBERS}, HTTP_HX_REQUEST="true")
        listed = admin_client.post(_share(door), {"audience": ADMINS, "can_open": [member.pk]}, HTTP_HX_REQUEST="true")

        assert 'value="members"' not in body
        assert 'value="public"' not in body
        assert 'data-hq-people="can_open"' not in body
        assert "is for the Team only" in body
        assert "HX-Trigger" not in widened
        assert "HX-Trigger" not in listed
        assert "This door is for the Team only" in listed.content.decode()
        door.refresh_from_db()
        assert door.audience == ADMINS
        assert door.can_open.count() == 0

    @pytest.mark.parametrize("kind", sorted(TEAM_ONLY_KINDS))
    @pytest.mark.parametrize("stored", [MEMBERS, PUBLIC])
    def it_ticks_admins_only_on_a_team_only_door_stored_wider_and_saves_it(open_hub, admin_client, kind, stored):
        """A door saved with a wider audience before sharing existed still has its one choice ticked."""
        door = _door(open_hub, kind)
        HubCard.objects.filter(pk=door.pk).update(audience=stored)
        door.refresh_from_db()

        assert CardSharingForm(instance=door)["audience"].value() == ADMINS
        body = admin_client.get(_share(door), HTTP_HX_REQUEST="true").content.decode()
        assert "checked" in body.split('value="admins"', 1)[1].split(">", 1)[0]
        saved = admin_client.post(_share(door), {"audience": ADMINS}, HTTP_HX_REQUEST="true")
        assert "hq-share-saved" in saved["HX-Trigger"]
        door.refresh_from_db()
        assert door.audience == ADMINS

    def it_answers_a_refused_htmx_post_with_the_panel_and_its_errors(card, admin_client):
        response = admin_client.post(_share(card), {"audience": "nobody"}, HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        assert "HX-Trigger" not in response
        assert f'data-hq-share-form="{card.pk}"' in response.content.decode()
        assert "Select a valid choice" in response.content.decode()

    def it_renders_a_refused_plain_post_on_the_share_page(card, admin_client):
        response = admin_client.post(_share(card), {"audience": "nobody"})

        assert response.status_code == 200
        assert "data-hq-share-page" in response.content.decode()

    def it_404s_a_card_on_another_hub(open_hub, admin_client):
        other = HubCardFactory(hub=EventHubFactory())

        assert admin_client.get(_url("card_share", open_hub.slug, other.pk)).status_code == 404


def describe_the_sharing_form():
    def it_offers_active_members_and_anyone_already_listed(open_hub, make_member):
        active = make_member(preferred_name="Active Ann")
        gone = make_member(preferred_name="Gone Gil", status=Member.Status.FORMER)
        make_member(preferred_name="Never Nell", status=Member.Status.FORMER)
        card = _door(open_hub, Kind.MARKETING, can_open=[gone])

        names = {option.name for option in CardSharingForm(instance=card).people_options()}

        assert active.display_name in names
        assert gone.display_name in names
        assert "Never Nell" not in names

    def it_shows_the_chosen_as_chips_in_order_of_the_lists(open_hub, make_member):
        one = make_member()
        card = _door(open_hub, Kind.MARKETING, team=[one])

        pickers = {picker.field.name: picker for picker in CardSharingForm(instance=card).pickers()}

        assert [person.pk for person in pickers["team"].chosen] == [one.pk]
        assert pickers["can_open"].chosen == []
        assert pickers["team"].dom_id == f"hq-share-{card.pk}-team"
