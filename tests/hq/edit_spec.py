"""The HQ edit pages (Track C, part 1): the hub form with its goals and hero, a card's form
with its named members and its place in the order, and the nine cards a new hub starts with."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from PIL import Image

from hq.models import DEFAULT_CARDS, EventHub, HubCard, HubGoal
from membership.models import CommunityEvent, Member
from tests.hq.conftest import photo_upload
from tests.hq.factories import EventHubFactory, HubCardFactory, HubGoalFactory
from tests.membership.factories import CommunityEventFactory

pytestmark = pytest.mark.django_db

SETUP = reverse("hq:setup")


def _edit(hub: EventHub) -> str:
    return reverse("hq:edit", args=[hub.slug])


def _card_edit(card: HubCard) -> str:
    return reverse("hq:card_edit", args=[card.hub.slug, card.pk])


def _hub_post(goals: list[dict[str, Any]] | None = None, initial: int = 0, **fields: Any) -> dict[str, Any]:
    """A full hub form POST: the fields plus the goals formset management form."""
    goals = goals or []
    data: dict[str, Any] = {
        "title": "Holiday Bizarre HQ",
        "slug": "holiday-bizarre-2026",
        "eyebrow": "Past Lives Makerspace · 2nd Annual",
        "event": "",
        "intro": "",
        "footer_note": "",
        "goals-TOTAL_FORMS": str(len(goals)),
        "goals-INITIAL_FORMS": str(initial),
        "goals-MIN_NUM_FORMS": "0",
        "goals-MAX_NUM_FORMS": "1000",
    }
    for index, goal in enumerate(goals):
        row = {"label": "Goal", "kind": HubGoal.Kind.MANUAL, "target": "100", "current_value": "0"}
        row.update(goal)
        for key, value in row.items():
            data[f"goals-{index}-{key}"] = value
    data.update(fields)
    return data


def describe_loading_lees_hq():
    def it_is_for_admins(member_client, admin_client):
        assert Client().get(SETUP).status_code == 302
        assert "/accounts/login/" in Client().get(SETUP)["Location"]
        assert member_client.get(SETUP).status_code == 403
        assert member_client.post(SETUP).status_code == 403
        assert not EventHub.objects.exists()

    def it_offers_one_button_and_no_form_fields(admin_client):
        body = admin_client.get(SETUP).content.decode()

        assert "data-hq-setup-submit" in body
        assert "Load Lee&#x27;s HQ" in body or "Load Lee's HQ" in body
        assert 'name="title"' not in body
        assert 'name="slug"' not in body

    def it_loads_her_doors_goals_and_shifts_and_lands_on_the_page(admin_client):
        response = admin_client.post(SETUP)

        hub = EventHub.objects.get(slug="holiday-bizarre-2026")
        assert response.status_code == 302
        assert response["Location"] == hub.get_absolute_url()
        assert hub.is_open
        assert hub.event is None
        cards = list(hub.cards.all())
        assert [card.kind for card in cards] == [door.kind for door in DEFAULT_CARDS]
        assert {card.audience for card in cards} == {HubCard.Audience.ADMINS}
        assert cards[0].title == "Going to the Bizarre?"
        assert hub.goals.count() == 4
        assert hub.shifts.count() == 21

    def it_sends_an_admin_to_the_hq_once_one_exists(open_hub, admin_client):
        for response in (admin_client.get(SETUP), admin_client.post(SETUP)):
            assert response.status_code == 302
            assert response["Location"] == open_hub.get_absolute_url()
        assert EventHub.objects.count() == 1

    def it_adds_nothing_when_the_doors_are_seeded_again(open_hub):
        first = open_hub.seed_default_cards()
        pks = set(open_hub.cards.values_list("pk", flat=True))
        rsvp = open_hub.cards.get(kind=HubCard.Kind.RSVP)
        rsvp.title = "Renamed by Lee"
        rsvp.save(update_fields=["title"])

        again = open_hub.seed_default_cards()

        assert (first.created, first.kept) == (9, 0)
        assert (again.created, again.kept, again.adopted, again.reworded) == (0, 9, [], 0)
        assert set(open_hub.cards.values_list("pk", flat=True)) == pks
        assert open_hub.cards.get(kind=HubCard.Kind.RSVP).title == "Renamed by Lee"


def describe_updating_the_doors():
    def _url(hub: EventHub) -> str:
        return reverse("hq:doors_update", args=[hub.slug])

    def it_is_an_admins_post(open_hub, member_client, admin_client):
        assert member_client.post(_url(open_hub)).status_code == 403
        assert admin_client.get(_url(open_hub)).status_code == 405
        assert not open_hub.cards.exists()

    def it_adopts_the_built_door_and_says_what_changed(open_hub, admin_client):
        placeholder = HubCardFactory(
            hub=open_hub,
            kind=HubCard.Kind.PLACEHOLDER,
            title="Marketing Tracker",
            bullets=DEFAULT_CARDS[-1].old_bullets,
            button_text=DEFAULT_CARDS[-1].old_button_text,
        )
        HubCardFactory(hub=open_hub, kind=HubCard.Kind.PLACEHOLDER, title="PL Ledger")

        response = admin_client.post(_url(open_hub), follow=True)

        placeholder.refresh_from_db()
        assert response.redirect_chain == [(open_hub.get_absolute_url(), 302)]
        assert placeholder.kind == HubCard.Kind.MARKETING
        assert placeholder.button_text == "Enter marketing"
        messages = [str(message) for message in response.context["messages"]]
        assert messages == ["Doors updated: opened Marketing Tracker; added 3 doors; gave 1 card Lee's latest words."]
        assert open_hub.cards.count() == 5

    def it_offers_the_button_on_edit_hq(open_hub, admin_client):
        body = admin_client.get(_edit(open_hub)).content.decode()

        assert f'action="{_url(open_hub)}"' in body
        assert "data-hq-doors-update-submit" in body


def describe_editing_a_hub():
    def it_is_for_admins(open_hub, member_client):
        assert member_client.get(_edit(open_hub)).status_code == 403
        assert member_client.post(_edit(open_hub), _hub_post()).status_code == 403

    def it_saves_the_words_and_lands_on_the_page(open_hub, admin_client):
        response = admin_client.post(
            _edit(open_hub),
            _hub_post(slug=open_hub.slug, title="A New Title", intro="Come one, come all.", is_open="on"),
        )

        open_hub.refresh_from_db()
        assert response.status_code == 302
        assert response["Location"] == open_hub.get_absolute_url()
        assert open_hub.title == "A New Title"
        assert open_hub.intro == "Come one, come all."
        assert open_hub.is_open is True

    def it_lists_published_events_by_date_in_the_picker_and_saves_the_pick(open_hub, admin_client):
        later = CommunityEventFactory(community=True, title="Later Party")
        sooner = CommunityEventFactory(
            community=True, title="Sooner Party", starts_at=later.starts_at - timedelta(days=30)
        )
        pending = CommunityEventFactory(community=True, pending=True, title="Pending Party")

        body = admin_client.get(_edit(open_hub)).content.decode()
        response = admin_client.post(_edit(open_hub), _hub_post(slug=open_hub.slug, event=str(sooner.pk)))

        assert "Pending Party" not in body
        assert body.index("Sooner Party") < body.index("Later Party")
        assert response.status_code == 302
        open_hub.refresh_from_db()
        assert open_hub.event == sooner
        assert pending.moderation_state == CommunityEvent.ModerationState.PENDING

    def it_refuses_to_open_a_second_hub(open_hub, admin_client):
        other = EventHubFactory(is_open=False, title="The Other One")

        response = admin_client.post(_edit(other), _hub_post(slug=other.slug, is_open="on"))

        assert response.status_code == 200
        assert f"{open_hub.title} is open. Close it first" in response.content.decode()
        other.refresh_from_db()
        assert other.is_open is False

    def it_lets_the_open_hub_save_while_staying_open(open_hub, admin_client):
        response = admin_client.post(_edit(open_hub), _hub_post(slug=open_hub.slug, is_open="on"))

        assert response.status_code == 302


def describe_goals():
    def it_adds_a_goal_from_a_new_row(open_hub, admin_client):
        goals = [{"label": "Bottles", "kind": HubGoal.Kind.BOTTLES, "target": "100", "is_money": ""}]

        response = admin_client.post(_edit(open_hub), _hub_post(goals=goals, slug=open_hub.slug))

        assert response.status_code == 302
        (goal,) = open_hub.goals.all()
        assert goal.label == "Bottles"
        assert goal.kind == HubGoal.Kind.BOTTLES
        assert goal.target == 100

    def it_deletes_a_saved_goal_through_the_delete_field_and_keeps_the_rest(open_hub, admin_client):
        first = HubGoalFactory(hub=open_hub, label="First", sort_order=0)
        second = HubGoalFactory(hub=open_hub, label="Second", sort_order=1)
        goals = [
            {"id": str(first.pk), "label": "First", "DELETE": "on"},
            {"id": str(second.pk), "label": "Second, renamed"},
        ]

        response = admin_client.post(_edit(open_hub), _hub_post(goals=goals, initial=2, slug=open_hub.slug))

        assert response.status_code == 302
        (kept,) = open_hub.goals.all()
        assert kept.pk == second.pk
        assert kept.label == "Second, renamed"
        assert kept.sort_order == 0

    def it_numbers_the_rows_in_page_order(open_hub, admin_client):
        goals = [{"label": "A", "sort_order": "9"}, {"label": "B"}, {"label": "C"}]

        admin_client.post(_edit(open_hub), _hub_post(goals=goals, slug=open_hub.slug))

        assert list(open_hub.goals.values_list("label", "sort_order")) == [("A", 0), ("B", 1), ("C", 2)]

    def it_skips_a_row_added_and_left_as_it_rendered(open_hub, admin_client):
        """A + Add goal row posts its rendered defaults (Typed in by hand, 0 and 0) with no label."""
        goals = [{"label": "", "kind": HubGoal.Kind.MANUAL, "target": "0", "current_value": "0"}]

        response = admin_client.post(_edit(open_hub), _hub_post(goals=goals, slug=open_hub.slug))

        assert response.status_code == 302
        assert not open_hub.goals.exists()

    def it_renders_saved_rows_with_a_delete_button_that_posts_the_page(open_hub, admin_client):
        goal = HubGoalFactory(hub=open_hub)

        body = admin_client.get(_edit(open_hub)).content.decode()

        assert "data-hq-goal-delete" in body
        assert f'name="goals-0-id" value="{goal.pk}"' in body
        assert "requestSubmit()" in body


def describe_the_hero_image():
    def it_stores_a_normalized_copy_under_hq_hero(open_hub, admin_client, settings):
        settings.IMAGE_MAX_LONG_EDGE_HERO = 800

        response = admin_client.post(
            _edit(open_hub), _hub_post(slug=open_hub.slug, hero_image=photo_upload(size=(1600, 600)))
        )

        assert response.status_code == 302
        open_hub.refresh_from_db()
        assert open_hub.hero_image.name.startswith("hq/hero/")
        with Image.open(open_hub.hero_image.path) as image:
            assert image.size == (800, 300)

    def it_refuses_a_file_that_is_not_an_image(open_hub, admin_client):
        from django.core.files.uploadedfile import SimpleUploadedFile

        bogus = SimpleUploadedFile("hero.jpg", b"not a picture", content_type="image/jpeg")

        response = admin_client.post(_edit(open_hub), _hub_post(slug=open_hub.slug, hero_image=bogus))

        assert response.status_code == 200
        assert "pl-field-error" in response.content.decode()
        open_hub.refresh_from_db()
        assert not open_hub.hero_image

    def it_refuses_a_file_over_the_size_cap(open_hub, admin_client, settings):
        settings.MAX_UPLOAD_IMAGE_BYTES = 10

        response = admin_client.post(_edit(open_hub), _hub_post(slug=open_hub.slug, hero_image=photo_upload()))

        assert response.status_code == 200
        assert "MB or smaller" in response.content.decode()

    def it_offers_delete_only_once_there_is_an_image(open_hub, admin_client):
        delete_url = reverse("hq:hero_delete", args=[open_hub.slug])
        assert delete_url not in admin_client.get(_edit(open_hub)).content.decode()

        open_hub.hero_image = photo_upload()
        open_hub.save()

        body = admin_client.get(_edit(open_hub)).content.decode()
        assert delete_url in body
        assert "delete-hq-hero" in body

    def it_deletes_the_image_and_its_file(open_hub, admin_client):
        open_hub.hero_image = photo_upload()
        open_hub.save()
        path = open_hub.hero_image.path

        response = admin_client.post(reverse("hq:hero_delete", args=[open_hub.slug]))

        assert response.status_code == 302
        assert response["Location"] == _edit(open_hub)
        open_hub.refresh_from_db()
        assert not open_hub.hero_image
        import os

        assert not os.path.exists(path)

    def it_is_a_post_for_admins(open_hub, admin_client, member_client):
        url = reverse("hq:hero_delete", args=[open_hub.slug])

        assert admin_client.get(url).status_code == 405
        assert member_client.post(url).status_code == 403

    def it_does_nothing_without_an_image(open_hub):
        open_hub.remove_hero()

        assert not open_hub.hero_image


def _card_post(**fields: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title": "Volunteer Sign-up",
        "who_for": "Volunteers",
        "bullets": "One\nTwo",
        "button_text": "Sign up",
        "audience": HubCard.Audience.MEMBERS,
    }
    data.update(fields)
    return data


def describe_editing_a_card():
    def it_is_for_admins(open_hub, member_client):
        card = HubCardFactory(hub=open_hub)

        assert member_client.get(_card_edit(card)).status_code == 403
        assert member_client.post(_card_edit(card), _card_post()).status_code == 403

    def it_404s_a_card_on_another_hub(open_hub, admin_client):
        card = HubCardFactory()

        assert admin_client.get(reverse("hq:card_edit", args=[open_hub.slug, card.pk])).status_code == 404

    def it_saves_the_words_audience_hidden_and_named_members(open_hub, admin_client, make_member):
        card = HubCardFactory(hub=open_hub)
        one, two = make_member(), make_member()

        response = admin_client.post(
            _card_edit(card),
            _card_post(audience=HubCard.Audience.PUBLIC, is_hidden="on", named_members=[str(one.pk), str(two.pk)]),
        )

        assert response.status_code == 302
        assert response["Location"] == open_hub.get_absolute_url()
        card.refresh_from_db()
        assert card.title == "Volunteer Sign-up"
        assert card.who_for == "Volunteers"
        assert card.button_text == "Sign up"
        assert card.audience == HubCard.Audience.PUBLIC
        assert card.is_hidden is True
        assert set(card.named_members.all()) == {one, two}

    def it_caps_the_bullets_at_four_and_tidies_them(open_hub, admin_client):
        card = HubCardFactory(hub=open_hub)

        refused = admin_client.post(_card_edit(card), _card_post(bullets="1\n2\n3\n4\n5"))
        accepted = admin_client.post(_card_edit(card), _card_post(bullets=" 1 \n\n2\n3\n4\n"))

        assert refused.status_code == 200
        assert "Up to 4 bullets, one per line. This has 5." in refused.content.decode()
        assert accepted.status_code == 302
        card.refresh_from_db()
        assert card.bullets == "1\n2\n3\n4"

    def it_refuses_a_named_member_who_is_not_active(open_hub, admin_client, make_member):
        card = HubCardFactory(hub=open_hub)
        former = make_member(status=Member.Status.FORMER)

        response = admin_client.post(_card_edit(card), _card_post(named_members=[str(former.pk)]))

        assert response.status_code == 200
        assert card.named_members.count() == 0
        # A refused pick opens the named members row so the error is in view.
        assert 'x-data="{ editing: true,' in response.content.decode()

    def it_lists_active_members_with_their_emails_and_ticks_the_named(open_hub, admin_client, make_member):
        named = make_member(preferred_name="Lee")
        other = make_member(preferred_name="Other Person")
        make_member(status=Member.Status.FORMER, preferred_name="Gone Former")
        card = HubCardFactory(hub=open_hub, named=[named])

        body = admin_client.get(_card_edit(card)).content.decode()

        assert f'value="{named.pk}" data-name="Lee" checked' in body
        assert f'value="{other.pk}" data-name="Other Person" @change' in body
        assert named.primary_email in body
        assert "Gone Former" not in body
        assert 'x-data="{ editing: false,' in body
        assert "data-hq-named-pills" in body

    def it_offers_move_up_and_move_down(open_hub, admin_client):
        card = HubCardFactory(hub=open_hub)

        body = admin_client.get(_card_edit(card)).content.decode()

        assert 'data-hq-move="up"' in body
        assert 'data-hq-move="down"' in body
        assert reverse("hq:card_move", args=[open_hub.slug, card.pk]) in body


def describe_moving_a_card():
    @pytest.fixture
    def three(open_hub):
        return [HubCardFactory(hub=open_hub, sort_order=n, title=f"Door {n}") for n in range(3)]

    def _order(hub: EventHub) -> list[str]:
        return list(hub.cards.values_list("title", flat=True))

    def it_moves_a_card_up_and_lands_back_on_its_edit_page(open_hub, admin_client, three):
        url = reverse("hq:card_move", args=[open_hub.slug, three[2].pk])

        response = admin_client.post(url, {"dir": "up"})

        assert response.status_code == 302
        assert response["Location"] == _card_edit(three[2])
        assert _order(open_hub) == ["Door 0", "Door 2", "Door 1"]

    def it_moves_a_card_down(open_hub, admin_client, three):
        admin_client.post(reverse("hq:card_move", args=[open_hub.slug, three[0].pk]), {"dir": "down"})

        assert _order(open_hub) == ["Door 1", "Door 0", "Door 2"]

    def it_leaves_the_order_alone_at_either_end(open_hub, admin_client, three):
        admin_client.post(reverse("hq:card_move", args=[open_hub.slug, three[0].pk]), {"dir": "up"})
        admin_client.post(reverse("hq:card_move", args=[open_hub.slug, three[2].pk]), {"dir": "down"})

        assert _order(open_hub) == ["Door 0", "Door 1", "Door 2"]

    def it_renumbers_cards_that_shared_a_position(open_hub, admin_client):
        a = HubCardFactory(hub=open_hub, sort_order=0, title="A")
        b = HubCardFactory(hub=open_hub, sort_order=0, title="B")

        admin_client.post(reverse("hq:card_move", args=[open_hub.slug, b.pk]), {"dir": "up"})

        assert list(open_hub.cards.values_list("title", "sort_order")) == [("B", 0), ("A", 1)]
        a.refresh_from_db()
        assert a.sort_order == 1

    def it_refuses_any_other_direction(open_hub, admin_client, three):
        response = admin_client.post(reverse("hq:card_move", args=[open_hub.slug, three[0].pk]), {"dir": "sideways"})

        assert response.status_code == 400
        with pytest.raises(ValueError):
            three[0].move("sideways")

    def it_is_a_post_for_admins(open_hub, admin_client, member_client, three):
        url = reverse("hq:card_move", args=[open_hub.slug, three[0].pk])

        assert admin_client.get(url).status_code == 405
        assert member_client.post(url, {"dir": "up"}).status_code == 403


def describe_the_card_url():
    def it_points_the_rsvp_card_at_the_event_page(open_hub):
        event = CommunityEventFactory(community=True)
        open_hub.event = event
        open_hub.save()
        card = HubCardFactory(hub=open_hub, kind=HubCard.Kind.RSVP)

        assert HubCard.objects.get(pk=card.pk).url == reverse("hub_event_detail", args=[event.pk])

    def it_reads_the_c2_and_c3_routes_once_they_exist(open_hub):
        from unittest.mock import patch

        card = HubCardFactory(hub=open_hub, kind=HubCard.Kind.VOLUNTEERS)

        with patch("hq.models.reverse", return_value="/hq/x/volunteers/") as fake:
            assert card.url == "/hq/x/volunteers/"
        fake.assert_called_once_with("hq:volunteers", args=[open_hub.slug])


def describe_one_open_hub():
    def it_is_kept_by_the_database_too(open_hub):
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            EventHubFactory(is_open=True)

    def it_finds_the_open_one_or_none(open_hub):
        assert EventHub.objects.open() == open_hub
        open_hub.is_open = False
        open_hub.save()
        assert EventHub.objects.open() is None

    def it_keeps_one_live_card_of_a_kind_per_hub(open_hub):
        from django.db import IntegrityError

        HubCardFactory(hub=open_hub, kind=HubCard.Kind.RSVP)
        HubCardFactory(hub=open_hub, kind=HubCard.Kind.PLACEHOLDER)
        HubCardFactory(hub=open_hub, kind=HubCard.Kind.PLACEHOLDER)
        with pytest.raises(IntegrityError):
            HubCardFactory(hub=open_hub, kind=HubCard.Kind.RSVP)


def describe_str():
    def it_names_the_hub_card_and_goal(open_hub):
        card = HubCardFactory(hub=open_hub, title="A Door")
        goal = HubGoalFactory(hub=open_hub, label="Bottles", target=100)

        assert str(open_hub) == open_hub.title
        assert str(card) == "A Door"
        assert str(goal) == "Bottles (100)"
