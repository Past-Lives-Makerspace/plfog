"""The Marketing Tracker (Track D, part 1): the door, its gates, the six totals in one query, the organisers'
Log a Marketing Action form over htmx and plain posts, the log newest first, and Delete."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from hq import services
from hq.forms import MarketingActionForm
from hq.models import HubCard, MarketingAction
from hub.view_as import ROLE_GUEST
from tests.hq.conftest import signed_in
from tests.hq.factories import HubCardFactory, MarketingActionFactory

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def _page(hub: Any) -> str:
    return reverse("hq:marketing", args=[hub.slug])


def _add(hub: Any) -> str:
    return reverse("hq:marketing_add", args=[hub.slug])


def _delete(action: MarketingAction) -> str:
    return reverse("hq:marketing_delete", args=[action.hub.slug, action.pk])


def _post(**fields: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "kind": MarketingAction.Kind.REEL,
        "date": "2026-11-03",
        "who": "Anjali",
        "reach": "1200",
        "details": "Reel: Metal Guild sparks demo teaser",
        "link": "https://instagram.com/p/spec",
    }
    data.update(fields)
    return data


@pytest.fixture
def card(open_hub) -> HubCard:
    """The Marketing Tracker door on the open hub, open to everyone."""
    return HubCardFactory(
        hub=open_hub, kind=HubCard.Kind.MARKETING, title="Marketing Tracker", audience=HubCard.Audience.PUBLIC
    )


def describe_the_door():
    def it_opens_the_tracker_from_the_hq_page(card):
        body = Client().get(card.hub.get_absolute_url()).content.decode()

        assert card.url == _page(card.hub)
        assert f'href="{_page(card.hub)}" class="pl-hq-card" data-hq-card-link' in body

    def it_carries_a_way_back_to_the_hq_in_its_header(card):
        body = Client().get(_page(card.hub)).content.decode()
        header = body.split('<header class="pl-hq-mk__header">')[1].split("</header>")[0]

        assert f'<a href="{card.hub.get_absolute_url()}" class="pl-hq-mk__back" data-hq-mk-back>&larr; HQ</a>' in header
        assert card.hub.get_absolute_url() == reverse("hq:page", args=[card.hub.slug])

    def it_shows_a_door_with_no_page_as_not_shared(open_hub):
        """Every Track D door has its page now (D6a built the last); a placeholder still opens nothing."""
        HubCardFactory(hub=open_hub, kind=HubCard.Kind.PLACEHOLDER, audience=HubCard.Audience.PUBLIC)

        body = Client().get(open_hub.get_absolute_url()).content.decode()

        assert open_hub.cards.get().url == ""
        assert f'data-hq-card="{HubCard.Kind.PLACEHOLDER}"' in body
        assert "data-hq-card-placeholder" in body
        assert "data-hq-card-link" not in body


def describe_who_may_open_it():
    def it_is_open_to_a_signed_out_visitor_when_public_and_read_only(card):
        response = Client().get(_page(card.hub))

        body = response.content.decode()
        assert response.status_code == 200
        assert "data-hq-mk-totals" in body
        assert "data-hq-mk-form" not in body

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

    def it_is_a_404_when_hidden_for_all_but_admins(card, member_client, admin_client):
        card.is_hidden = True
        card.save(update_fields=["is_hidden"])

        assert member_client.get(_page(card.hub)).status_code == 404
        assert admin_client.get(_page(card.hub)).status_code == 200

    def it_is_a_404_on_a_hub_with_no_marketing_door(open_hub, admin_client):
        assert admin_client.get(_page(open_hub)).status_code == 404

    def it_gives_the_form_to_a_named_member_on_an_admins_card(card, member):
        card.audience = HubCard.Audience.ADMINS
        card.save(update_fields=["audience"])
        card.team.set([member])

        body = signed_in(member).get(_page(card.hub)).content.decode()

        assert "data-hq-mk-form" in body

    def it_refuses_a_post_from_anyone_but_an_organiser(card, member_client):
        action = MarketingActionFactory(hub=card.hub)

        assert member_client.post(_add(card.hub), _post()).status_code == 403
        assert Client().post(_add(card.hub), _post()).status_code == 403
        assert member_client.post(_delete(action)).status_code == 403
        assert MarketingAction.objects.count() == 1

    def it_renders_a_previewing_admin_read_only_but_lets_them_act(card, admin_client):
        session = admin_client.session
        session["view_as_role"] = ROLE_GUEST
        session.save()
        MarketingActionFactory(hub=card.hub)

        body = admin_client.get(_page(card.hub)).content.decode()

        assert "data-hq-mk-form" not in body
        assert "data-hq-mk-delete" not in body
        assert admin_client.post(_add(card.hub), _post()).status_code == 302
        assert MarketingAction.objects.count() == 2

    def it_takes_posts_only(card, admin_client):
        action = MarketingActionFactory(hub=card.hub)

        assert admin_client.get(_add(card.hub)).status_code == 405
        assert admin_client.get(_delete(action)).status_code == 405


def describe_the_totals():
    def it_counts_each_kind_every_action_and_the_reach_in_one_query(card, django_assert_num_queries):
        MarketingActionFactory.create_batch(3, hub=card.hub, kind=MarketingAction.Kind.FLYER)
        MarketingActionFactory(hub=card.hub, kind=MarketingAction.Kind.REEL, reach=1200)
        MarketingActionFactory(hub=card.hub, kind=MarketingAction.Kind.PRESS, reach=34)
        MarketingActionFactory(kind=MarketingAction.Kind.FLYER, reach=999)  # another hub's

        with django_assert_num_queries(1):
            totals = services.marketing_totals(card.hub)

        assert totals["flyer"] == 3
        assert totals["reel"] == 1
        assert totals["press"] == 1
        assert totals["post"] == 0
        assert totals[services.MARKETING_ALL] == 5
        assert totals[services.MARKETING_REACH] == 1234
        assert set(totals) == set(MarketingAction.Kind.values) | {"all", "reach"}

    def it_reads_zero_everywhere_on_an_empty_log(open_hub):
        totals = services.marketing_totals(open_hub)

        assert set(totals.values()) == {0}

    def it_makes_lees_six_tiles_in_her_order(card):
        MarketingActionFactory(hub=card.hub, kind=MarketingAction.Kind.EMAIL, reach=2500)

        tiles = services.marketing_tiles(services.marketing_totals(card.hub))

        assert [(tile.key, tile.heading, tile.figure, tile.helper) for tile in tiles] == [
            ("flyer", "Businesses with flyers", 0, "Places the flyer is posted"),
            ("reel", "Instagram videos", 0, "Reels and video posts"),
            ("post", "Instagram posts", 0, "Posts and stories"),
            ("email", "Emails sent", 1, "Member and community emails"),
            ("listing", "Event listings", 0, "Eventbrite, calendars, the portal"),
            ("all", "Total actions", 1, "Reach so far: 2,500"),
        ]

    def it_renders_the_tiles_on_the_page(card):
        MarketingActionFactory(hub=card.hub, kind=MarketingAction.Kind.FLYER)

        body = Client().get(_page(card.hub)).content.decode()

        assert body.count("data-hq-mk-tile=") == 6
        assert 'data-hq-mk-tile="flyer"' in body
        assert "Reach so far: 0" in body


def describe_logging_an_action():
    def it_saves_over_htmx_with_a_toast_and_keeps_the_kind_date_and_who(card, admin, admin_client):
        response = admin_client.post(_add(card.hub), _post(), **HTMX)

        action = MarketingAction.objects.get()
        body = response.content.decode()
        assert response.status_code == 200
        assert json.loads(response["HX-Trigger"])["showToast"]["message"] == "Added."
        assert (action.hub, action.kind, action.date, action.who, action.reach, action.logged_by) == (
            card.hub,
            MarketingAction.Kind.REEL,
            date(2026, 11, 3),
            "Anjali",
            1200,
            admin,
        )
        assert action.details == "Reel: Metal Guild sparks demo teaser"
        assert "data-hq-mk-log" in body
        assert "data-hq-mk-subline" not in body
        assert '<option value="reel" selected>' in body
        assert 'value="2026-11-03"' in body
        assert 'value="Anjali"' in body
        assert 'value="https://instagram.com/p/spec"' not in body
        assert 'data-hq-mk-tile="all"' in body

    def it_lands_a_plain_post_back_on_the_page_with_a_message(card, admin_client):
        response = admin_client.post(_add(card.hub), _post(), follow=True)

        assert response.redirect_chain == [(_page(card.hub), 302)]
        assert "Added." in [str(message) for message in response.context["messages"]]
        assert MarketingAction.objects.count() == 1

    def it_refuses_an_action_with_no_details_or_a_bad_link(card, admin_client):
        for fields in ({"details": "  "}, {"link": "not a link"}, {"reach": "-3"}, {"date": ""}):
            response = admin_client.post(_add(card.hub), _post(**fields), **HTMX)

            assert response.status_code == 200
            assert 'class="pl-field-errors"' in response.content.decode()
            assert "HX-Trigger" not in response
        assert not MarketingAction.objects.exists()

    def it_renders_the_whole_page_again_for_a_plain_post_that_fails(card, admin_client):
        response = admin_client.post(_add(card.hub), _post(details=""))

        assert response.status_code == 200
        assert "data-hq-mk-subline" in response.content.decode()
        assert not MarketingAction.objects.exists()

    def it_stores_no_reach_as_zero_and_trims_the_words(card, admin_client):
        admin_client.post(_add(card.hub), _post(reach="", who="  Kate ", details="  Flyer at Rocking Frog  ", link=""))

        action = MarketingAction.objects.get()
        assert (action.reach, action.who, action.details, action.link) == (0, "Kate", "Flyer at Rocking Frog", "")

    def it_starts_on_a_flyer_today_and_suggests_the_cards_team(card, admin_client, make_member):
        card.team.set([make_member(preferred_name="Anjali"), make_member(preferred_name="Kate")])

        body = admin_client.get(_page(card.hub)).content.decode()

        today = timezone.localdate().isoformat()
        assert MarketingActionForm().initial == {}
        assert f'value="{today}"' in body
        assert '<option value="flyer" selected>' in body
        assert 'list="hq-marketing-team"' in body
        assert (
            '<datalist id="hq-marketing-team" data-hq-mk-team><option value="Anjali"></option><option value="Kate"></option></datalist>'
            in body
        )
        assert 'placeholder="e.g. Anjali"' in body
        assert 'placeholder="e.g. Reel: Metal Guild sparks demo teaser"' in body

    def it_is_dated_portland_today_by_default(card):
        action = MarketingAction.objects.create(hub=card.hub, details="Listing on the Mercury calendar")

        assert action.date == timezone.localdate()
        assert action.kind == MarketingAction.Kind.FLYER


def describe_the_log():
    def it_lists_newest_first_by_date_then_by_when_logged(card):
        today = timezone.localdate()
        old = MarketingActionFactory(hub=card.hub, date=today - timedelta(days=3), details="Old one")
        first = MarketingActionFactory(hub=card.hub, date=today, details="Earlier today")
        second = MarketingActionFactory(hub=card.hub, date=today, details="Later today")

        assert list(card.hub.marketing_actions.all()) == [second, first, old]
        body = Client().get(_page(card.hub)).content.decode()
        assert body.index("Later today") < body.index("Earlier today") < body.index("Old one")

    def it_links_details_only_to_a_web_address_and_dashes_an_unknown_reach(card):
        MarketingActionFactory(hub=card.hub, details="Linked post", link="https://example.com/post", reach=12345)
        MarketingActionFactory(hub=card.hub, details="FTP thing", link="ftp://example.com/file")

        body = Client().get(_page(card.hub)).content.decode()

        assert (
            '<a href="https://example.com/post" target="_blank" rel="noopener" data-hq-mk-link>Linked post</a>' in body
        )
        assert "ftp://example.com/file" not in body
        assert "12,345" in body
        assert "data-hq-mk-reach>–<" in body

    def it_names_each_kind_on_its_chip(card):
        MarketingActionFactory(hub=card.hub, kind=MarketingAction.Kind.LISTING)

        body = Client().get(_page(card.hub)).content.decode()

        assert 'data-hq-mk-kind="listing">Event listing (Eventbrite, calendars)</span>' in body

    def it_says_so_when_nothing_is_logged(card):
        body = Client().get(_page(card.hub)).content.decode()

        assert "data-hq-mk-empty" in body
        assert "No marketing logged yet. Add the first action above." in body

    def it_gives_organisers_a_delete_on_each_row(card, admin_client):
        actions = MarketingActionFactory.create_batch(2, hub=card.hub)

        admin_body = admin_client.get(_page(card.hub)).content.decode()
        guest_body = Client().get(_page(card.hub)).content.decode()

        assert admin_body.count("data-hq-mk-delete") == 2
        assert f'action="{_delete(actions[0])}"' in admin_body
        assert "data-hq-mk-delete" not in guest_body


def describe_deleting_an_action():
    def it_takes_the_row_off_the_log_for_an_organiser(card, member):
        card.team.set([member])
        action = MarketingActionFactory(hub=card.hub)

        response = signed_in(member).post(_delete(action), follow=True)

        assert response.redirect_chain == [(_page(card.hub), 302)]
        assert "Taken off the log." in [str(message) for message in response.context["messages"]]
        assert not MarketingAction.objects.exists()

    def it_is_a_404_for_another_hubs_action(card, admin_client):
        elsewhere = MarketingActionFactory()

        response = admin_client.post(reverse("hq:marketing_delete", args=[card.hub.slug, elsewhere.pk]))

        assert response.status_code == 404
        assert MarketingAction.objects.filter(pk=elsewhere.pk).exists()


def describe_the_model():
    def it_reads_as_its_kind_and_details(card):
        action = MarketingActionFactory(hub=card.hub, kind=MarketingAction.Kind.EMAIL, details="November newsletter")

        assert str(action) == "Email or newsletter: November newsletter"

    @pytest.mark.parametrize(
        ("link", "href"),
        [
            ("https://example.com", "https://example.com"),
            ("HTTP://example.com", "HTTP://example.com"),
            ("ftp://example.com", ""),
            ("", ""),
        ],
    )
    def it_links_only_http_and_https(link, href):
        assert MarketingAction(link=link).href == href

    def it_shows_the_reach_with_separators_or_a_dash():
        assert MarketingAction(reach=0).reach_display == "–"
        assert MarketingAction(reach=1).reach_display == "1"
        assert MarketingAction(reach=1234567).reach_display == "1,234,567"
