"""The HQ page (Track C, part 1): the hero band, the countdown, the goal band, the doors."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from hq.models import Countdown, EventHub, HubCard, HubGoal, WineBottle
from hq.services import GoalRow, KindCount, WineBand, goal_rows, rsvp_count
from tests.hq.factories import HubCardFactory, HubGoalFactory, WineBottleFactory
from tests.membership.factories import CommunityEventFactory, EventRSVPFactory

pytestmark = pytest.mark.django_db


def _page(hub: EventHub) -> str:
    return reverse("hq:page", args=[hub.slug])


def _noon() -> datetime:
    """A fixed local noon, clear of midnight on either side of a day boundary."""
    return timezone.localtime(timezone.now()).replace(hour=12, minute=0, second=0, microsecond=0)


def describe_the_hero_band():
    def it_shows_the_eyebrow_title_and_the_events_date_line_with_its_location(open_hub):
        event = CommunityEventFactory(community=True, location="2808 SE 9th Ave")
        open_hub.event = event
        open_hub.eyebrow = "Past Lives Makerspace · 2nd Annual"
        open_hub.save()

        body = Client().get(_page(open_hub)).content.decode()

        assert "Past Lives Makerspace · 2nd Annual" in body
        assert f'<h1 class="pl-hq-hero__title">{open_hub.title}</h1>' in body
        assert "data-hq-date" in body
        assert "2808 SE 9th Ave" in body
        assert event.when_display in body

    def it_renders_with_no_event_at_all(open_hub):
        body = Client().get(_page(open_hub)).content.decode()

        assert "data-hq-hero" in body
        assert "data-hq-date" not in body
        assert "data-hq-countdown" not in body
        assert open_hub.date_line == ""

    def it_leaves_the_location_off_the_date_line_when_the_event_has_none(open_hub):
        event = CommunityEventFactory(community=True, location="")
        open_hub.event = event

        assert open_hub.date_line == event.when_display

    def it_shows_the_hero_image_when_there_is_one(open_hub, admin_client):
        from tests.hq.conftest import photo_upload

        open_hub.hero_image = photo_upload()
        open_hub.save()

        body = admin_client.get(_page(open_hub)).content.decode()

        assert 'class="pl-hq-hero__img"' in body
        assert open_hub.hero_image.url in body

    def it_shows_the_intro_as_paragraphs_and_the_footer_note(open_hub):
        open_hub.intro = "First line.\n\nSecond line."
        open_hub.footer_note = "An Oregon nonprofit corporation."
        open_hub.save()

        body = Client().get(_page(open_hub)).content.decode()

        assert "<p>First line.</p>" in body
        assert "<p>Second line.</p>" in body
        assert "data-hq-footnote" in body
        assert "An Oregon nonprofit corporation." in body


def describe_the_countdown():
    @pytest.mark.parametrize(
        ("days_ahead", "label"),
        [(12, "12 days to go"), (2, "2 days to go"), (1, "1 day to go"), (0, "Today"), (-1, "Thank you")],
    )
    def it_counts_portland_days_to_the_event(open_hub, days_ahead, label):
        now = _noon()
        open_hub.event = CommunityEventFactory(community=True, starts_at=now + timedelta(days=days_ahead))

        assert open_hub.countdown_label(now) == label

    def it_is_blank_without_an_event(open_hub):
        assert open_hub.countdown_label(timezone.now()) == ""
        assert open_hub.countdown_parts(timezone.now()) == Countdown("", "")

    @pytest.mark.parametrize(
        ("days_ahead", "big", "small"),
        [(12, "12", "days to go"), (1, "1", "day to go"), (0, "Today", ""), (-3, "Thank you", "")],
    )
    def it_splits_the_number_from_its_label_for_the_band(open_hub, days_ahead, big, small):
        now = _noon()
        open_hub.event = CommunityEventFactory(community=True, starts_at=now + timedelta(days=days_ahead))

        assert open_hub.countdown_parts(now) == Countdown(big, small)

    def it_renders_a_word_alone_in_the_big_slot(open_hub):
        now = _noon()
        open_hub.event = CommunityEventFactory(community=True, starts_at=now)
        open_hub.save()

        with patch("hq.views.timezone.now", return_value=now):
            body = Client().get(_page(open_hub)).content.decode()

        assert 'data-hq-countdown="Today"' in body
        assert '<span class="pl-hq-hero__figure">Today</span>' in body
        assert "pl-hq-hero__cd-label" not in body

    def it_renders_as_the_big_figure_on_the_page(open_hub):
        now = _noon()
        open_hub.event = CommunityEventFactory(community=True, starts_at=now + timedelta(days=3))
        open_hub.save()

        with patch("hq.views.timezone.now", return_value=now):
            body = Client().get(_page(open_hub)).content.decode()

        assert 'data-hq-countdown="3 days to go"' in body
        assert '<span class="pl-hq-hero__figure">3</span>' in body
        assert '<span class="pl-hq-hero__cd-label">days to go</span>' in body


def describe_goal_rows():
    def it_reads_a_manual_goal_from_its_typed_figure(open_hub):
        HubGoalFactory(
            hub=open_hub, label="Raised", kind=HubGoal.Kind.MANUAL, target=10000, current_value=2500, is_money=True
        )

        (row,) = goal_rows(open_hub)

        assert row == GoalRow(label="Raised", current=2500, target=10000, is_money=True)
        assert row.percent == 25
        assert row.current_display == "$2,500"
        assert row.target_display == "$10,000"

    def it_counts_the_people_going_to_the_event(open_hub):
        event = CommunityEventFactory(community=True)
        EventRSVPFactory(event=event)
        EventRSVPFactory(event=event, party_size=3)
        open_hub.event = event
        open_hub.save()
        HubGoalFactory(hub=open_hub, label="Going", kind=HubGoal.Kind.RSVPS, target=250)

        (row,) = goal_rows(open_hub)

        assert row.current == 4  # people, not rows: party sizes 1 and 3
        assert row.percent == 2
        assert row.current_display == "4"
        assert row.target_display == "250"

    def it_counts_no_rsvps_without_an_event(open_hub):
        HubGoalFactory(hub=open_hub, kind=HubGoal.Kind.RSVPS, target=250)

        assert goal_rows(open_hub)[0].current == 0
        assert rsvp_count(None) == 0

    @pytest.mark.parametrize("kind", [HubGoal.Kind.BOTTLES, HubGoal.Kind.BOTTLE_VALUE])
    def it_reads_zero_for_the_wine_wall_kinds_while_the_wall_is_empty(open_hub, kind):
        HubGoalFactory(hub=open_hub, kind=kind, target=82)

        assert goal_rows(open_hub)[0].current == 0

    def it_caps_the_meter_at_full_and_reads_empty_with_no_target(open_hub):
        HubGoalFactory(hub=open_hub, kind=HubGoal.Kind.MANUAL, target=10, current_value=30, sort_order=0)
        HubGoalFactory(hub=open_hub, kind=HubGoal.Kind.MANUAL, target=0, current_value=3, sort_order=1)

        full, empty = goal_rows(open_hub)

        assert full.percent == 100
        assert empty.percent == 0

    def it_counts_a_live_kind_once_however_many_goals_share_it(open_hub):
        open_hub.event = CommunityEventFactory(community=True)
        open_hub.save()
        HubGoalFactory(hub=open_hub, kind=HubGoal.Kind.RSVPS, target=100, sort_order=0)
        HubGoalFactory(hub=open_hub, kind=HubGoal.Kind.RSVPS, target=200, sort_order=1)
        HubGoalFactory(hub=open_hub, kind=HubGoal.Kind.MANUAL, target=5, current_value=1, sort_order=2)
        hub = EventHub.objects.select_related("event").get(pk=open_hub.pk)

        with CaptureQueriesContext(connection) as queries:
            rows = goal_rows(hub)

        assert [row.target for row in rows] == [100, 200, 5]
        assert len(queries) == 2  # the goals, and one RSVP count


def describe_the_goal_band():
    def it_renders_a_goal_with_a_meter_under_the_goal_tracker_heading(open_hub):
        HubGoalFactory(hub=open_hub, label="Bottles", target=100, current_value=40)

        body = Client().get(_page(open_hub)).content.decode()

        assert body.count("data-hq-goal>") == 1
        assert '<h2 class="pl-hq-goals__title" id="hq-goals-title">' in body
        assert '<strong class="pl-hq-goal__value">40 <small>of 100</small></strong>' in body
        assert '<progress class="pl-hq-meter" max="100" value="40"' in body
        assert "Bottles" in body

    def it_shows_the_empty_state_to_admins_and_nothing_to_anyone_else(open_hub, admin_client, member_client):
        assert "data-hq-goals-empty" in admin_client.get(_page(open_hub)).content.decode()
        member_body = member_client.get(_page(open_hub)).content.decode()
        assert "data-hq-goals-empty" not in member_body
        assert 'id="hq-goals-title"' not in member_body


def describe_bottles_by_type():
    def it_lists_every_kind_in_lees_order_with_zeros_once_a_bottle_is_logged(open_hub):
        HubGoalFactory(hub=open_hub, label="Wine wall bottles", kind=HubGoal.Kind.BOTTLES, target=100)
        WineBottleFactory(hub=open_hub, kind=WineBottle.Kind.WHITE, qty=3)
        WineBottleFactory(hub=open_hub, kind=WineBottle.Kind.CIDER)
        WineBottleFactory(hub=open_hub, kind=WineBottle.Kind.WHITE)
        WineBottleFactory(kind=WineBottle.Kind.BEER, qty=9)  # another hub's wall

        body = Client().get(_page(open_hub)).content.decode()

        line = body.split("data-hq-bottle-kinds>")[1].split("</p>")[0]
        assert line.startswith("Bottles by type:")
        chips = [chip.split(">")[1] for chip in line.split("</span>")[:-1]]
        assert chips == ["0 Red", "4 White", "0 Champagne", "1 Cider", "0 Beer"]
        goals = body.split('id="hq-goals-title"')[1].split("</section>")[0]
        assert "data-hq-bottle-kinds" in goals  # under the goal band

    def it_is_left_out_while_the_wall_is_empty(open_hub):
        HubGoalFactory(hub=open_hub, kind=HubGoal.Kind.BOTTLES, target=100)

        assert "data-hq-bottle-kinds" not in Client().get(_page(open_hub)).content.decode()

    def it_reads_the_wall_band_for_every_kind():
        band = WineBand(
            bottles=2, goal=0, value_cents=0, supporters=1, by_kind=[KindCount(key="beer", label="Beer", bottles=2)]
        )

        assert [(kind.key, kind.bottles) for kind in band.every_kind] == [
            ("red", 0),
            ("white", 0),
            ("champagne", 0),
            ("cider", 0),
            ("beer", 2),
        ]

    def it_keeps_the_page_to_a_fixed_number_of_queries_as_bottles_grow(open_hub, django_assert_num_queries):
        HubGoalFactory(hub=open_hub, kind=HubGoal.Kind.BOTTLES, target=100)
        WineBottleFactory(hub=open_hub)
        client = Client()
        client.get(_page(open_hub))  # warm the session and the site configuration
        with CaptureQueriesContext(connection) as first:
            client.get(_page(open_hub))
        for kind in WineBottle.Kind:
            WineBottleFactory.create_batch(3, hub=open_hub, kind=kind)

        with django_assert_num_queries(len(first)):
            response = client.get(_page(open_hub))

        assert response.content.decode().count("data-hq-bottle-kind=") == 5


def describe_the_tile_icons():
    @pytest.mark.parametrize(
        ("kind", "mark"),
        [
            (HubCard.Kind.VOLUNTEERS, '<circle cx="9" cy="7" r="4"/>'),
            (HubCard.Kind.WINE_WALL, '<path d="M8 14h8"/>'),
        ],
    )
    def it_gives_each_kind_its_own_tile_icon(open_hub, kind, mark):
        card = HubCardFactory(hub=open_hub, kind=kind, audience=HubCard.Audience.PUBLIC)

        body = Client().get(_page(open_hub)).content.decode()
        door = body.split(f'data-hq-card-pk="{card.pk}"')[1].split("</div>")[0]

        assert 'class="pl-hq-card__tile"' in door
        assert mark in door


def describe_the_doors():
    def it_links_a_live_card_to_its_page_and_shows_its_words(open_hub):
        event = CommunityEventFactory(community=True)
        open_hub.event = event
        open_hub.save()
        card = HubCardFactory(
            hub=open_hub,
            kind=HubCard.Kind.RSVP,
            audience=HubCard.Audience.PUBLIC,
            who_for="Members and guests",
            title="Going to the Bizarre?",
            bullets="Tell us you're coming\n\nGet email updates\n",
            button_text="I'm going",
        )

        body = Client().get(_page(open_hub)).content.decode()
        door = body.split(f'data-hq-card-pk="{card.pk}"')[1].split("</a>")[0]

        assert card.url == f"{reverse('hub_event_detail', args=[event.pk])}?from=hq"
        assert f'<a href="{card.url}" class="pl-hq-card" data-hq-card-link>' in door
        assert "M9.5 12l1.8 1.8 3.4-3.6" in door  # the ticket with a check, the RSVP door's icon
        assert "Members and guests" in door
        assert "<li>Tell us you&#x27;re coming</li>" in door
        assert "<li>Get email updates</li>" in door
        assert door.count("<li>") == 2
        assert 'class="pl-hq-card__button">I&#x27;m going</span>' in door

    def it_renders_a_placeholder_as_a_tile_that_links_nowhere(open_hub):
        card = HubCardFactory(hub=open_hub, kind=HubCard.Kind.PLACEHOLDER, audience=HubCard.Audience.PUBLIC)

        body = Client().get(_page(open_hub)).content.decode()
        door = body.split(f'data-hq-card-pk="{card.pk}"')[1].split("</div>")[0]

        assert card.url == ""
        assert "data-hq-card-placeholder" in door
        assert 'class="pl-hq-card__button pl-hq-card__button--off" aria-disabled="true">Not shared yet' in door
        assert "<a " not in door
        assert '<rect x="8" y="2" width="8" height="4"' in door  # the clipboard icon

    def it_links_the_wine_wall_card_to_the_wall(open_hub):
        card = HubCardFactory(hub=open_hub, kind=HubCard.Kind.WINE_WALL, audience=HubCard.Audience.PUBLIC)

        body = Client().get(_page(open_hub)).content.decode()
        door = body.split(f'data-hq-card-pk="{card.pk}"')[1].split("</div>")[0]

        assert card.url == reverse("hq:wine", args=[open_hub.slug])
        assert f'<a href="{card.url}" class="pl-hq-card" data-hq-card-link>' in door
        assert "data-hq-card-placeholder" not in door

    def it_renders_the_rsvp_card_as_a_tile_while_the_hub_has_no_event(open_hub):
        card = HubCardFactory(hub=open_hub, kind=HubCard.Kind.RSVP, audience=HubCard.Audience.PUBLIC)

        body = Client().get(_page(open_hub)).content.decode()

        assert card.url == ""
        assert "data-hq-card-placeholder" in body
        assert "data-hq-card-link" not in body

    def it_shows_the_audience_and_named_count_under_each_card_to_an_admin(open_hub, admin_client, member):
        HubCardFactory(hub=open_hub, audience=HubCard.Audience.MEMBERS, named=[member])

        body = admin_client.get(_page(open_hub)).content.decode()

        assert "Members · 1 named" in body

    def it_tells_a_visitor_with_no_open_door_and_offers_sign_in(open_hub, member_client):
        HubCardFactory(hub=open_hub, audience=HubCard.Audience.ADMINS)

        anonymous_body = Client().get(_page(open_hub)).content.decode()
        member_body = member_client.get(_page(open_hub)).content.decode()

        assert "data-hq-doors-empty" in anonymous_body
        assert f"/accounts/login/?next={_page(open_hub)}" in anonymous_body
        assert "data-hq-doors-empty" in member_body
        assert "/accounts/login/" not in member_body.split("data-hq-doors-empty")[1].split("</p>")[0]

    def it_keeps_its_queries_flat_however_many_doors_and_named_members(open_hub, admin_client, make_member):
        def render() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert admin_client.get(_page(open_hub)).status_code == 200
            return len(queries)

        HubCardFactory(hub=open_hub, named=[make_member()])
        render()  # the first visit pays for session and sign in bookkeeping; measure the second
        few = render()
        for _ in range(4):
            HubCardFactory(hub=open_hub, named=[make_member(), make_member()])

        assert render() == few


def describe_bullet_lines():
    def it_drops_blank_lines_and_trims():
        card = HubCard(bullets="  one \n\n two\n   \nthree")

        assert card.bullet_lines == ["one", "two", "three"]
