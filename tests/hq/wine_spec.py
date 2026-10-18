"""The wine wall (Track C, part 3): logging a bottle with or without a photo, the guests'
honeypot and rate limit, deleting by the logger or an organiser, the band and the goals it
feeds, the wineries with their logos, the organisers' donors table and CSV, and the wine
wall card now linking to its page."""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from PIL import Image

from core.abuse_limits import keyed_within_limits
from hq import services
from hq.forms import WineBottleForm
from hq.models import HubCard, HubGoal, WineBottle, WineryLogo, format_money
from hq.services import KindCount, goal_rows
from hub.view_as import ROLE_GUEST
from tests.hq.conftest import photo_upload, signed_in
from tests.hq.factories import HubCardFactory, HubGoalFactory, WineBottleFactory, WineryLogoFactory

pytestmark = pytest.mark.django_db

PUBLIC = HubCard.Audience.PUBLIC
BOTTLE = {
    "kind": "red",
    "name": "2021 Pinot Noir",
    "winery": "Abacela",
    "value": "25",
    "qty": "1",
    "giver_name": "Pat Giver",
    "giver_type": "person",
    "giver_email": "Pat@Example.com",
    "listing": "yes",
    "credited_name": "Lee",
    "note": "",
}


def _wall(hub: Any) -> str:
    return reverse("hq:wine", args=[hub.slug])


def _delete_url(bottle: Any) -> str:
    return reverse("hq:bottle_delete", args=[bottle.hub.slug, bottle.pk])


def _logo_url(hub: Any) -> str:
    return reverse("hq:winery_logo", args=[hub.slug])


def _donors_url(hub: Any) -> str:
    return reverse("hq:wine_donors", args=[hub.slug])


def _csv_url(hub: Any) -> str:
    return reverse("hq:wine_donors_csv", args=[hub.slug])


@pytest.fixture
def card(open_hub):
    return HubCardFactory(hub=open_hub, kind=HubCard.Kind.WINE_WALL, audience=PUBLIC, title="Log a wine bottle")


def _guest_with_info(hub: Any, email: str = "pat@example.com", name: str = "Pat Guest") -> Client:
    """A signed out browser that saved the Your Info step on the volunteer page (made public if needed)."""
    if not hub.cards.filter(kind=HubCard.Kind.VOLUNTEERS).exists():
        HubCardFactory(hub=hub, kind=HubCard.Kind.VOLUNTEERS, audience=PUBLIC)
    client = Client()
    response = client.post(reverse("hq:volunteer_info", args=[hub.slug]), {"name": name, "email": email})
    assert response.status_code == 302
    return client


def describe_logging_a_bottle():
    def it_logs_a_members_bottle_with_a_photo_resized_under_the_media_root(card, member, settings):
        client = signed_in(member)

        response = client.post(_wall(card.hub), {**BOTTLE, "photo": photo_upload("bottle.jpg", size=(3200, 2400))})

        assert response.status_code == 302
        assert response["Location"] == _wall(card.hub)
        bottle = WineBottle.objects.get()
        assert (bottle.hub, bottle.kind, bottle.name, bottle.winery) == (card.hub, "red", "2021 Pinot Noir", "Abacela")
        assert (bottle.value_cents, bottle.qty, bottle.value_total_cents) == (2500, 1, 2500)
        assert (bottle.giver_name, bottle.giver_type, bottle.giver_email) == ("Pat Giver", "person", "pat@example.com")
        assert (bottle.listing, bottle.credited_name, bottle.note) == ("yes", "Lee", "")
        assert bottle.logged_by == member
        assert bottle.logged_by_name == member.display_name
        assert bottle.logged_by_email == member.primary_email
        assert bottle.photo.name.startswith("hq/bottles/")
        assert bottle.photo.path.startswith(settings.MEDIA_ROOT)
        with Image.open(bottle.photo.path) as image:
            assert image.size == (1600, 1200)
        body = client.get(_wall(card.hub)).content.decode()
        assert f'data-hq-bottle="{bottle.pk}"' in body
        assert f'src="{bottle.photo.url}" alt="" loading="lazy" data-hq-bottle-photo' in body
        assert str(bottle) == "2021 Pinot Noir (Abacela)"

    def it_logs_a_guests_bottle_without_a_photo(card):
        client = Client()

        response = client.post(_wall(card.hub), BOTTLE)

        assert response.status_code == 302
        bottle = WineBottle.objects.get()
        assert not bottle.photo
        assert bottle.logged_by is None
        assert (bottle.logged_by_name, bottle.logged_by_email) == ("", "")
        body = client.get(_wall(card.hub)).content.decode()
        assert "data-hq-bottle-no-photo" in body
        assert "data-hq-bottle-photo" not in body
        assert "data-hq-wine-no-bottles" not in body

    def it_prefills_the_credited_volunteer_with_the_members_name(card, make_member):
        member = make_member(preferred_name="Lee")

        body = signed_in(member).get(_wall(card.hub)).content.decode()

        assert 'name="credited_name" value="Lee"' in body
        assert 'name="website"' not in body

    def it_prefills_a_guest_from_their_saved_your_info_step_and_keys_the_bottle_on_its_email(card):
        client = _guest_with_info(card.hub)

        body = client.get(_wall(card.hub)).content.decode()
        client.post(_wall(card.hub), {**BOTTLE, "credited_name": ""})

        assert 'name="credited_name" value="Pat Guest"' in body
        assert 'name="website"' in body
        bottle = WineBottle.objects.get()
        assert (bottle.logged_by, bottle.logged_by_name, bottle.logged_by_email) == (
            None,
            "Pat Guest",
            "pat@example.com",
        )

    def it_refuses_a_quantity_below_one(card):
        response = Client().post(_wall(card.hub), {**BOTTLE, "qty": "0"})

        assert response.status_code == 200
        assert "pl-field-error" in response.content.decode()
        assert not WineBottle.objects.exists()
        assert 'name="qty" value="1" min="1"' in Client().get(_wall(card.hub)).content.decode()
        with pytest.raises(ValidationError, match="qty"):
            WineBottleFactory.build(hub=card.hub, qty=0).full_clean()

    def it_turns_dollars_and_cents_into_cents_and_trims_the_words(card):
        form = WineBottleForm({**BOTTLE, "value": "12.50", "name": "  Rosé ", "winery": " Abacela "})

        assert form.is_valid(), form.errors
        fields = form.to_fields()
        assert fields["value_cents"] == 1250
        assert (fields["name"], fields["winery"]) == ("Rosé", "Abacela")
        assert "website" not in fields
        assert "value" not in fields
        assert isinstance(Decimal(form.cleaned_data["value"]), Decimal)

    def it_refuses_a_missing_name_and_reopens_the_form_with_the_error(card):
        response = Client().post(_wall(card.hub), {**BOTTLE, "name": ""})

        assert response.status_code == 200
        body = response.content.decode()
        assert 'x-data="{ open: true }"' in body
        assert "pl-field-error" in body
        assert not WineBottle.objects.exists()

    def it_starts_with_the_form_closed_behind_its_button(card):
        body = Client().get(_wall(card.hub)).content.decode()

        assert 'x-data="{ open: false }"' in body
        assert 'x-show="open" x-cloak data-hq-wine-form' in body
        assert "data-hq-wine-open" in body
        assert 'hx-boost="false"' in body

    def it_refuses_a_file_that_is_not_an_image(card):
        bogus = SimpleUploadedFile("bottle.jpg", b"not a picture", content_type="image/jpeg")

        response = Client().post(_wall(card.hub), {**BOTTLE, "photo": bogus})

        assert response.status_code == 200
        assert "pl-field-error" in response.content.decode()
        assert not WineBottle.objects.exists()

    def it_refuses_a_photo_over_the_cap(card, settings):
        settings.MAX_UPLOAD_IMAGE_BYTES = 10

        response = Client().post(_wall(card.hub), {**BOTTLE, "photo": photo_upload("bottle.jpg")})

        assert response.status_code == 200
        assert "MB or smaller" in response.content.decode()
        assert not WineBottle.objects.exists()

    def it_writes_nothing_when_the_honeypot_is_filled(card):
        response = Client().post(_wall(card.hub), {**BOTTLE, "website": "https://spam.example"})

        assert response.status_code == 302
        assert response["Location"] == _wall(card.hub)
        assert not WineBottle.objects.exists()

    def it_counts_guest_bottles_under_their_own_scope_and_refuses_past_the_cap(card):
        cache.clear()
        client = Client()

        with patch("hq.services.RATE_HOURLY_LIMIT", 2):
            first = client.post(_wall(card.hub), BOTTLE)
            second = client.post(_wall(card.hub), BOTTLE)
            third = client.post(_wall(card.hub), BOTTLE)

        assert (first.status_code, second.status_code, third.status_code) == (302, 302, 200)
        refused = third.content.decode()
        assert "data-hq-wine-error" in refused
        assert "Too many bottles from this connection" in refused
        assert WineBottle.objects.count() == 2
        assert services.RATE_SCOPE_BOTTLE == "hq_bottle"
        assert not keyed_within_limits(services.RATE_SCOPE_BOTTLE, "127.0.0.1", hourly_limit=2, daily_limit=100)
        assert keyed_within_limits(services.RATE_SCOPE, "127.0.0.1", hourly_limit=2, daily_limit=100)

    def it_never_counts_a_member(card, member):
        cache.clear()

        with patch("hq.services.RATE_HOURLY_LIMIT", 0):
            response = signed_in(member).post(_wall(card.hub), BOTTLE)

        assert response.status_code == 302
        assert WineBottle.objects.count() == 1

    def it_lowercases_the_emails_on_save(card):
        bottle = WineBottleFactory(hub=card.hub, giver_email="  PAT@Example.com ", logged_by_email=" LEE@Example.com ")

        assert (bottle.giver_email, bottle.logged_by_email) == ("pat@example.com", "lee@example.com")

    def it_renders_what_a_guest_sees_for_an_admin_previewing_as_guest(card, admin_client):
        """Rendering reads the effective role; the donors page and the CSV keep the actual one."""
        WineBottleFactory(hub=card.hub)
        session = admin_client.session
        session["view_as_role"] = ROLE_GUEST
        session.save()

        body = admin_client.get(_wall(card.hub)).content.decode()

        assert 'name="website"' in body
        assert "data-hq-wine-organizers" not in body
        assert "data-hq-logo-form" not in body
        assert "data-hq-bottle-delete" not in body
        assert admin_client.get(_donors_url(card.hub)).status_code == 200
        assert admin_client.get(_csv_url(card.hub)).status_code == 200

    def it_shows_a_bottles_note_to_organisers_and_the_logger_only(card, admin_client, member_client, make_member):
        logger = make_member()
        WineBottleFactory(hub=card.hub, logged_by=logger, note="Knows the giver personally")
        named = make_member()
        card.named_members.add(named)

        for client in (signed_in(logger), admin_client, signed_in(named)):
            assert "data-hq-bottle-note" in client.get(_wall(card.hub)).content.decode()
        for client in (Client(), member_client):
            body = client.get(_wall(card.hub)).content.decode()
            assert "data-hq-bottle-note" not in body
            assert "Knows the giver personally" not in body
        assert (
            "data-hq-donor-notes>Knows the giver personally<"
            in admin_client.get(_donors_url(card.hub)).content.decode()
        )


def describe_deleting():
    def it_lets_the_logger_take_their_own_bottle_off_with_its_photo(card, member):
        bottle = WineBottleFactory(hub=card.hub, logged_by=member, photo=photo_upload("bottle.jpg"))
        path = bottle.photo.path
        assert os.path.exists(path)

        response = signed_in(member).post(_delete_url(bottle))

        assert response.status_code == 302
        assert response["Location"] == _wall(card.hub)
        assert not WineBottle.objects.exists()
        assert not os.path.exists(path)

    def it_lets_an_organiser_delete_any_bottle(card, admin_client, make_member):
        named = make_member()
        card.named_members.add(named)
        by_guest = WineBottleFactory(hub=card.hub)
        by_other = WineBottleFactory(hub=card.hub, logged_by=make_member())

        assert admin_client.post(_delete_url(by_guest)).status_code == 302
        assert signed_in(named).post(_delete_url(by_other)).status_code == 302
        assert not WineBottle.objects.exists()

    def it_refuses_another_member_and_a_guest_with_a_403(card, member_client, make_member):
        bottle = WineBottleFactory(hub=card.hub, logged_by=make_member())
        by_guest = WineBottleFactory(hub=card.hub)

        assert member_client.post(_delete_url(bottle)).status_code == 403
        assert Client().post(_delete_url(by_guest)).status_code == 403
        assert WineBottle.objects.count() == 2
        with pytest.raises(services.NotYourBottle):
            services.delete_bottle(bottle, member=None, email="", is_organizer=False)

    def it_lets_a_guest_delete_their_own_bottle_by_the_email_they_saved_and_nobody_elses(card):
        pat = _guest_with_info(card.hub)
        pat.post(_wall(card.hub), BOTTLE)
        bottle = WineBottle.objects.get()
        other = _guest_with_info(card.hub, email="other@example.com", name="Other Guest")

        assert f"delete-bottle-{bottle.pk}" in pat.get(_wall(card.hub)).content.decode()
        assert f"delete-bottle-{bottle.pk}" not in other.get(_wall(card.hub)).content.decode()
        assert other.post(_delete_url(bottle)).status_code == 403
        assert Client().post(_delete_url(bottle)).status_code == 403
        assert WineBottle.objects.filter(pk=bottle.pk).exists()
        assert pat.post(_delete_url(bottle)).status_code == 302
        assert not WineBottle.objects.exists()

    def it_offers_delete_to_the_logger_and_organisers_only(card, member, admin_client, make_member):
        mine = WineBottleFactory(hub=card.hub, logged_by=member)
        theirs = WineBottleFactory(hub=card.hub, logged_by=make_member())

        member_body = signed_in(member).get(_wall(card.hub)).content.decode()
        admin_body = admin_client.get(_wall(card.hub)).content.decode()

        assert member_body.count("data-hq-bottle-delete") == 1
        assert f"delete-bottle-{mine.pk}" in member_body
        assert f"delete-bottle-{theirs.pk}" not in member_body
        assert f'action="{_delete_url(mine)}"' in member_body
        assert admin_body.count("data-hq-bottle-delete") == 2

    def it_reads_the_actual_role_while_an_admin_previews(card, admin_client):
        bottle = WineBottleFactory(hub=card.hub)
        session = admin_client.session
        session["view_as_role"] = ROLE_GUEST
        session.save()

        assert admin_client.post(_delete_url(bottle)).status_code == 302
        assert not WineBottle.objects.exists()

    def it_is_a_post_on_a_bottle_of_this_hub(card, admin_client):
        bottle = WineBottleFactory(hub=card.hub)
        elsewhere = WineBottleFactory()

        assert admin_client.get(_delete_url(bottle)).status_code == 405
        assert admin_client.post(reverse("hq:bottle_delete", args=[card.hub.slug, elsewhere.pk])).status_code == 404

    def it_can_be_deleted_by_the_logger_by_member_or_email_or_an_organiser(member, make_member):
        bottle = WineBottleFactory(logged_by=member, logged_by_email="lee@example.com")
        guests = WineBottleFactory(logged_by_email="pat@example.com")
        nobodys = WineBottleFactory()

        assert bottle.can_be_deleted_by(member, email="", is_organizer=False) is True
        assert bottle.can_be_deleted_by(make_member(), email="", is_organizer=False) is False
        assert bottle.can_be_deleted_by(None, email=" LEE@example.com ", is_organizer=False) is True
        assert bottle.can_be_deleted_by(None, email="", is_organizer=True) is True
        assert guests.can_be_deleted_by(member, email="other@example.com", is_organizer=False) is False
        assert guests.can_be_deleted_by(None, email="pat@example.com", is_organizer=False) is True
        assert nobodys.can_be_deleted_by(None, email="", is_organizer=False) is False

    def it_removes_a_winerys_logo_with_its_last_bottle(card):
        first = WineBottleFactory(hub=card.hub, winery="Abacela")
        last = WineBottleFactory(hub=card.hub, winery="abacela")
        other_hub = WineBottleFactory(winery="Abacela")
        logo = WineryLogoFactory(hub=card.hub, name="ABACELA")
        elsewhere = WineryLogoFactory(hub=other_hub.hub, name="Abacela")
        path = logo.logo.path

        first.delete()
        assert WineryLogo.objects.filter(pk=logo.pk).exists()
        last.delete()

        assert not WineryLogo.objects.filter(pk=logo.pk).exists()
        assert not os.path.exists(path)
        assert WineryLogo.objects.filter(pk=elsewhere.pk).exists()
        assert os.path.exists(elsewhere.logo.path)


def describe_the_band():
    @pytest.fixture
    def wall(card):
        """Two reds (one giver, twice), a white from the same giver spelled differently, three ciders."""
        WineBottleFactory(hub=card.hub, kind="red", qty=2, value_cents=2500, giver_name="Pat")
        WineBottleFactory(hub=card.hub, kind="white", qty=1, value_cents=4000, giver_name="pat")
        WineBottleFactory(hub=card.hub, kind="cider", qty=3, value_cents=800, giver_name="Bee")

    def it_sums_bottles_value_and_supporters_and_counts_by_kind(card, wall):
        band = services.wine_band(card.hub)

        assert (band.bottles, band.value_cents, band.supporters) == (6, 11400, 2)
        assert band.by_kind == [
            KindCount("red", "Red", 2),
            KindCount("white", "White", 1),
            KindCount("cider", "Cider", 3),
        ]
        assert band.value_display == "$114"
        assert band.goal == 0
        assert band.percent == 0

    def it_is_empty_with_no_bottles(card):
        band = services.wine_band(card.hub)

        assert (band.bottles, band.value_cents, band.supporters, band.by_kind) == (0, 0, 0, [])

    def it_reads_the_goal_from_the_hubs_first_bottles_goal(card, wall):
        HubGoalFactory(hub=card.hub, kind=HubGoal.Kind.BOTTLES, target=100, sort_order=1)
        HubGoalFactory(hub=card.hub, kind=HubGoal.Kind.BOTTLES, target=50, sort_order=0)

        band = services.wine_band(card.hub)

        assert band.goal == 50
        assert band.percent == 12

    def it_caps_the_meter_at_full(card, wall):
        HubGoalFactory(hub=card.hub, kind=HubGoal.Kind.BOTTLES, target=4)

        assert services.wine_band(card.hub).percent == 100

    def it_renders_on_the_page_with_the_meter_only_while_there_is_a_goal(card, wall):
        before = Client().get(_wall(card.hub)).content.decode()
        HubGoalFactory(hub=card.hub, kind=HubGoal.Kind.BOTTLES, target=100)
        after = Client().get(_wall(card.hub)).content.decode()

        assert "data-hq-wine-bottles>6<" in before
        assert "data-hq-wine-value>$114<" in before
        assert "data-hq-wine-supporters>2<" in before
        assert 'data-hq-wine-kind="red">Red <strong>2</strong>' in before
        assert 'data-hq-wine-kind="cider">Cider <strong>3</strong>' in before
        assert 'data-hq-wine-kind="beer"' not in before
        assert "data-hq-wine-meter" not in before
        assert "data-hq-wine-goal" not in before
        assert 'max="100" value="6"' in after
        assert "data-hq-wine-goal>of a 100 bottle goal<" in after

    def it_hides_the_kind_row_with_no_bottles(card):
        assert "data-hq-wine-kinds" not in Client().get(_wall(card.hub)).content.decode()

    def it_feeds_the_bottles_and_value_goals_from_one_band(card, wall):
        HubGoalFactory(hub=card.hub, kind=HubGoal.Kind.BOTTLES, target=100, sort_order=0)
        HubGoalFactory(hub=card.hub, kind=HubGoal.Kind.BOTTLE_VALUE, target=1000, is_money=True, sort_order=1)
        HubGoalFactory(hub=card.hub, kind=HubGoal.Kind.BOTTLES, target=200, sort_order=2)

        with CaptureQueriesContext(connection) as queries:
            bottles, value, again = goal_rows(card.hub)

        assert (bottles.current, bottles.target) == (6, 100)
        assert (value.current, value.target, value.current_display) == (114, 1000, "$114")
        assert again.current == 6
        assert len(queries) == 4  # the goals, then the band: by kind, supporters, the goal

    def it_formats_money_as_whole_dollars_unless_there_are_cents():
        assert format_money(2500) == "$25"
        assert format_money(123456) == "$1,234.56"
        assert format_money(0) == "$0"


def describe_the_wineries():
    def it_groups_case_insensitively_with_bottle_totals_and_a_logo_when_one_exists(card):
        WineBottleFactory(hub=card.hub, winery="Abacela", qty=2)
        WineBottleFactory(hub=card.hub, winery="abacela", qty=1)  # the first spelling logged is the tile's
        WineBottleFactory(hub=card.hub, winery="Bee Cider", qty=1)
        logo = WineryLogoFactory(hub=card.hub, name="ABACELA")
        WineryLogoFactory(name="Bee Cider")  # another hub's logo never joins

        abacela, bee = services.wineries(card.hub)

        assert (abacela.name, abacela.bottles, abacela.logo) == ("Abacela", 3, logo)
        assert (bee.name, bee.bottles, bee.logo) == ("Bee Cider", 1, None)
        body = Client().get(_wall(card.hub)).content.decode()
        assert 'data-hq-winery="Abacela"' in body
        assert 'data-hq-winery="Bee Cider"' in body
        assert body.count("data-hq-winery-logo") == 1
        assert body.count("data-hq-winery-monogram") == 1
        assert f'src="{logo.logo.url}" alt="Abacela logo"' in body
        assert "data-hq-winery-bottles>3 bottles<" in body
        assert "data-hq-winery-bottles>1 bottle<" in body
        assert str(logo) == "ABACELA"

    def it_shows_the_empty_state_with_no_bottles(card):
        assert "data-hq-wine-no-wineries" in Client().get(_wall(card.hub)).content.decode()

    def it_resizes_a_logo_and_holds_one_name_per_hub(card):
        logo = WineryLogoFactory(hub=card.hub, name="Abacela", logo=photo_upload("logo.png", size=(2400, 600)))

        assert logo.logo.name.startswith("hq/wineries/")
        with Image.open(logo.logo.path) as image:
            assert image.size == (1200, 300)
        with pytest.raises(IntegrityError):
            WineryLogo.objects.create(hub=card.hub, name="ABACELA", logo=photo_upload("logo2.png"))

    def it_uploads_a_logo_for_an_organiser_and_replaces_it_by_name(card, admin_client):
        WineBottleFactory(hub=card.hub, winery="Abacela")
        body = admin_client.get(_wall(card.hub)).content.decode()
        assert "data-hq-logo-form" in body
        assert '<option value="Abacela">Abacela</option>' in body

        first = admin_client.post(_logo_url(card.hub), {"name": "Abacela", "logo": photo_upload("one.png")})
        assert first.status_code == 302
        assert first["Location"] == _wall(card.hub)
        logo = WineryLogo.objects.get()
        old_path = logo.logo.path

        second = admin_client.post(_logo_url(card.hub), {"name": "Abacela", "logo": photo_upload("two.png")})

        assert second.status_code == 302
        logo.refresh_from_db()
        assert WineryLogo.objects.count() == 1
        assert logo.logo.path != old_path
        assert not os.path.exists(old_path)
        assert os.path.exists(logo.logo.path)

    def it_replaces_by_name_whatever_the_capitalisation(card):
        old = WineryLogoFactory(hub=card.hub, name="ABACELA")

        replaced = services.set_winery_logo(card.hub, "Abacela", photo_upload("new.png"))

        assert replaced.pk == old.pk
        assert replaced.name == "Abacela"
        assert WineryLogo.objects.count() == 1

    def it_refuses_a_winery_not_on_the_wall_and_a_file_that_is_not_an_image(card, admin_client):
        WineBottleFactory(hub=card.hub, winery="Abacela")

        unknown = admin_client.post(_logo_url(card.hub), {"name": "Nowhere", "logo": photo_upload("one.png")})
        bogus = SimpleUploadedFile("logo.png", b"nope", content_type="image/png")
        not_image = admin_client.post(_logo_url(card.hub), {"name": "Abacela", "logo": bogus})

        assert unknown.status_code == 200
        assert "pl-field-error" in unknown.content.decode()
        assert not_image.status_code == 200
        assert not WineryLogo.objects.exists()

    def it_refuses_a_logo_over_the_cap(card, admin_client, settings):
        WineBottleFactory(hub=card.hub, winery="Abacela")
        settings.MAX_UPLOAD_IMAGE_BYTES = 10

        response = admin_client.post(_logo_url(card.hub), {"name": "Abacela", "logo": photo_upload("one.png")})

        assert response.status_code == 200
        assert "MB or smaller" in response.content.decode()

    def it_keeps_the_logo_form_and_its_endpoint_to_organisers(card, member_client, make_member):
        WineBottleFactory(hub=card.hub, winery="Abacela")
        named = make_member()
        card.named_members.add(named)

        def upload() -> dict[str, Any]:
            return {"name": "Abacela", "logo": photo_upload("one.png")}

        assert "data-hq-logo-form" not in member_client.get(_wall(card.hub)).content.decode()
        assert member_client.post(_logo_url(card.hub), upload()).status_code == 403
        assert Client().post(_logo_url(card.hub), upload()).status_code == 403
        assert "data-hq-logo-form" in signed_in(named).get(_wall(card.hub)).content.decode()
        assert signed_in(named).post(_logo_url(card.hub), upload()).status_code == 302
        assert not WineryLogo.objects.exclude(hub=card.hub).exists()
        assert WineryLogo.objects.count() == 1

    def it_disables_add_logo_while_no_winery_is_named(card, admin_client):
        assert "data-hq-logo-add disabled" in admin_client.get(_wall(card.hub)).content.decode()


def describe_the_donors():
    @pytest.fixture
    def givers(card, member):
        """Zed Shop twice (the newest spelling, type and listing win; the blank email does not), then Amy."""
        WineBottleFactory(
            hub=card.hub,
            giver_name="Zed Shop",
            giver_type="wine_shop",
            giver_email="zed@example.com",
            listing="not_asked",
            qty=2,
            value_cents=1000,
            logged_by_name="Lee",
        )
        WineBottleFactory(
            hub=card.hub,
            giver_name="zed shop",
            giver_type="restaurant",
            giver_email="",
            listing="yes",
            qty=1,
            value_cents=500,
            logged_by=member,
            logged_by_name=member.display_name,
        )
        WineBottleFactory(
            hub=card.hub, giver_name="Amy", giver_email="amy@example.com", logged_by_name="Lee", note="Drop off Friday"
        )

    def it_groups_a_giver_across_bottles_with_their_newest_details(card, givers, member):
        amy, zed = services.donor_rows(card.hub)

        assert (amy.name, amy.giver_type, amy.email, amy.listing, amy.bottles) == (
            "Amy",
            "Person",
            "amy@example.com",
            "Not asked",
            1,
        )
        assert (zed.name, zed.giver_type, zed.email, zed.listing) == (
            "zed shop",
            "Restaurant",
            "zed@example.com",
            "Yes",
        )
        assert (zed.bottles, zed.value_cents, zed.value_display) == (3, 2500, "$25")
        assert zed.logged_by == ["Lee", member.display_name]
        assert amy.logged_by == ["Lee"]
        assert (amy.notes, zed.notes) == (["Drop off Friday"], [])
        assert services.donor_emails([amy, zed, amy]) == "amy@example.com, zed@example.com"

    def it_names_a_guest_logger_once(card):
        WineBottleFactory(hub=card.hub, giver_name="Amy")
        WineBottleFactory(hub=card.hub, giver_name="amy")

        (amy,) = services.donor_rows(card.hub)

        assert amy.logged_by == ["Guest"]
        assert services.donor_emails([amy]) == ""

    def it_is_on_the_page_for_admins_and_named_members_only(card, givers, admin_client, member_client, make_member):
        named = make_member()
        card.named_members.add(named)

        admin_body = admin_client.get(_wall(card.hub)).content.decode()
        named_body = signed_in(named).get(_wall(card.hub)).content.decode()
        member_body = member_client.get(_wall(card.hub)).content.decode()

        for body in (admin_body, named_body):
            assert "data-hq-wine-organizers" in body
            assert body.count("data-hq-donor-row") == 2
            assert "amy@example.com, zed@example.com" in body
            assert 'hx-boost="false" data-pl-download data-hq-donors-csv' in body
            assert "data-hq-donors-private" in body
        assert "data-hq-wine-organizers" not in member_body
        assert "data-hq-donors-csv" not in member_body
        assert "zed@example.com" not in member_body

    def it_has_its_own_page_for_organisers_and_refuses_everyone_else(
        card, givers, admin_client, member_client, make_member
    ):
        named = make_member()
        card.named_members.add(named)

        assert admin_client.get(_donors_url(card.hub)).status_code == 200
        named_body = signed_in(named).get(_donors_url(card.hub)).content.decode()
        assert "data-hq-donors-page" in named_body
        assert named_body.count("data-hq-donor-row") == 2
        assert member_client.get(_donors_url(card.hub)).status_code == 403
        assert Client().get(_donors_url(card.hub)).status_code == 403

    def it_streams_the_csv_for_organisers_only(card, givers, admin_client, member_client, member):
        response = admin_client.get(_csv_url(card.hub))

        assert response.status_code == 200
        assert response["Content-Disposition"].startswith(f'attachment; filename="{card.hub.slug}-wine-donors-')
        rows = b"".join(response.streaming_content).decode().splitlines()
        assert rows[0] == "Giver,Type,Email,Sponsor listing,Bottles,Value,Logged by,Notes"
        assert rows[1] == "Amy,Person,amy@example.com,Not asked,1,25.00,Lee,Drop off Friday"
        assert rows[2] == f"zed shop,Restaurant,zed@example.com,Yes,3,25.00,Lee; {member.display_name},"
        assert member_client.get(_csv_url(card.hub)).status_code == 403
        assert Client().get(_csv_url(card.hub)).status_code == 403

    def it_neutralises_a_formula_in_what_someone_typed(card, admin_client):
        WineBottleFactory(hub=card.hub, giver_name="=HYPERLINK(1)", giver_email="+1@example.com")

        rows = b"".join(admin_client.get(_csv_url(card.hub)).streaming_content).decode().splitlines()

        assert rows[1].startswith("'=HYPERLINK(1),Person,'+1@example.com,")

    def it_shows_the_empty_panel_with_copy_disabled(card, admin_client):
        body = admin_client.get(_donors_url(card.hub)).content.decode()

        assert "data-hq-donors-empty" in body
        assert "data-hq-copy-donor-emails disabled" in body


def describe_the_card():
    def it_links_the_wine_wall_card_to_its_page_with_the_bottle_icon(card):
        assert card.url == _wall(card.hub)

        body = Client().get(reverse("hq:page", args=[card.hub.slug])).content.decode()
        door = body.split(f'data-hq-card-pk="{card.pk}"')[1].split("</div>")[0]

        assert f'<a href="{_wall(card.hub)}" class="pl-hq-card" data-hq-card-link>' in door
        assert '<path d="M8 14h8"/>' in door


def describe_access():
    def it_follows_the_cards_audience(open_hub, member_client):
        members_only = HubCardFactory(hub=open_hub, kind=HubCard.Kind.WINE_WALL, audience=HubCard.Audience.MEMBERS)

        anonymous = Client().get(_wall(open_hub))
        assert anonymous.status_code == 302
        assert anonymous["Location"] == f"/accounts/login/?next={_wall(open_hub)}"
        assert member_client.get(_wall(open_hub)).status_code == 200
        members_only.audience = HubCard.Audience.ADMINS
        members_only.save()
        assert member_client.get(_wall(open_hub)).status_code == 404
        assert member_client.post(_wall(open_hub), BOTTLE).status_code == 404
        assert not WineBottle.objects.exists()

    def it_404s_without_a_wine_wall_card(open_hub, admin_client):
        assert admin_client.get(_wall(open_hub)).status_code == 404


def describe_queries():
    def it_keeps_the_page_flat_however_many_bottles_wineries_and_logos(card, admin_client, make_member):
        def render() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert admin_client.get(_wall(card.hub)).status_code == 200
            return len(queries)

        WineBottleFactory(hub=card.hub, winery="Abacela", logged_by=make_member(), photo=photo_upload("a.jpg"))
        WineryLogoFactory(hub=card.hub, name="Abacela")
        render()  # the first visit pays for session and sign in bookkeeping; measure the second
        few = render()
        for index in range(4):
            WineBottleFactory(hub=card.hub, winery=f"Winery {index}", logged_by=make_member(), photo=photo_upload())
            WineryLogoFactory(hub=card.hub, name=f"Winery {index}")

        assert render() == few

    def it_keeps_a_guests_page_flat_too(card):
        client = Client()

        def render() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert client.get(_wall(card.hub)).status_code == 200
            return len(queries)

        WineBottleFactory(hub=card.hub)
        few = render()
        for _ in range(3):
            WineBottleFactory(hub=card.hub, winery="Other")

        assert render() == few
