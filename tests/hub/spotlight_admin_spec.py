"""Admin Tools > Spotlight (#708): permissions and the page; polls removed in #890, the text and meeting in #935, the Crawl speed slider (#942)."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.http import HttpResponse
from django.test import Client
from django.urls import NoReverseMatch, reverse

from core.factories import AnnouncementFactory
from core.models import SiteConfiguration
from hub.spotlight_forms import CRAWL_SPEED_REFUSED, SpotlightCrawlSpeedForm
from hub.spotlight_views import CRAWL_SPEED_SAMPLE, CRAWL_SPEED_SAVED

pytestmark = pytest.mark.django_db

PAGE = reverse("hub_admin_spotlight")
SPEED = reverse("hub_admin_spotlight_crawl_speed")
HTMX = {"HTTP_HX_REQUEST": "true"}


def _speed() -> int:
    return SiteConfiguration.load().spotlight_crawl_speed


def _toast(response: HttpResponse) -> dict[str, str]:
    return json.loads(response["HX-Trigger"])["showToast"]


def _messages(response: HttpResponse) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def _crawl_speed(html: str) -> str:
    start = html.index('<form class="pl-crawl-speed"')
    return html[start : html.index("</form>", start)]


@pytest.fixture
def admin_client(client: Client) -> Client:
    User.objects.create_superuser(username="spotadmin", password="pass", email="spotadmin@example.com")
    client.login(username="spotadmin", password="pass")
    return client


@pytest.fixture
def member_client(client: Client) -> Client:
    User.objects.create_user(username="spotmember", password="pass", email="spotmember@example.com")
    client.login(username="spotmember", password="pass")
    return client


def describe_permissions():
    def it_refuses_a_member_who_is_not_an_admin(member_client: Client):
        assert member_client.get(PAGE).status_code == 403

    def it_sends_a_signed_out_visitor_to_sign_in(client: Client):
        response = client.get(PAGE)

        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_shows_the_card_in_admin_tools_to_admins(admin_client: Client):
        html = admin_client.get(reverse("hub_admin_tools")).content.decode()

        assert f'href="{PAGE}" data-tool-spotlight' in html

    def it_hides_the_card_from_members(member_client: Client):
        assert "data-tool-spotlight" not in member_client.get(reverse("hub_admin_tools")).content.decode()


def describe_the_page():
    def it_says_the_messages_crawl_across_the_top_of_home(admin_client: Client):
        html = admin_client.get(PAGE).content.decode()

        assert "Short messages that crawl across the top of Home, one after another." in html
        assert "Short notices on one line that crawls across the top of Home." in html

    def it_describes_the_spotlight_card_in_admin_tools(admin_client: Client):
        html = admin_client.get(reverse("hub_admin_tools")).content.decode()
        card = html.split("data-tool-spotlight>", 1)[1].split("</a>", 1)[0]

        assert '<span class="pl-tool-card__desc">Short messages that crawl across the top of Home.</span>' in card

    def it_holds_only_the_spotlight_messages(admin_client: Client):
        AnnouncementFactory(body="<p>Zorblax admin row</p>")

        html = admin_client.get(PAGE).content.decode()

        assert 'id="announcements"' in html
        assert "Zorblax admin row" in html
        for gone in (
            'id="spotlight-text"',
            "Spotlight Text and Meeting",
            "data-spotlight-text-form",
            "data-spotlight-preview",
            "spotlight_meeting_event",
            "spotlight_first_line",
            "spotlight_show_when_empty",
        ):
            assert gone not in html

    def it_no_longer_answers_the_text_save(admin_client: Client):
        with pytest.raises(NoReverseMatch):
            reverse("hub_admin_spotlight_text")
        assert admin_client.post("/manage/spotlight/text/").status_code == 404

    def it_shows_the_admin_the_static_card_in_the_sidebar(admin_client: Client):
        AnnouncementFactory(body="<p>Zorblax sidebar</p>")

        html = admin_client.get(PAGE).content.decode()
        sidebar = html.split('class="hub-sidebar__spotlight"', 1)[1].split("hub-sidebar__nav", 1)[0]

        assert "data-spotlight-details" in sidebar
        assert "Zorblax sidebar" not in sidebar


def describe_polls_have_left_the_page():
    @pytest.mark.parametrize(
        "path", ["/manage/spotlight/polls/new/", "/manage/spotlight/polls/1/close/", "/manage/spotlight/polls/1/"]
    )
    def it_no_longer_answers_new_poll_close_now_or_a_polls_results(admin_client: Client, path: str):
        assert admin_client.post(path).status_code == 404

    def it_has_no_open_poll_or_new_poll_section(admin_client: Client):
        html = admin_client.get(PAGE).content.decode()

        for gone in ('id="open-poll"', 'id="new-poll"', "data-close-poll", "data-post-poll", "replace-poll"):
            assert gone not in html
        assert 'id="past-polls"' not in html


def describe_the_crawl_speed_slider():
    def it_is_25_px_a_second_with_nothing_saved():
        assert SiteConfiguration().spotlight_crawl_speed == 25
        assert _speed() == 25

    def it_is_a_slow_to_fast_slider_from_10_to_80_in_steps_of_5_showing_its_value_in_words(admin_client: Client):
        section = _crawl_speed(admin_client.get(PAGE).content.decode())

        assert f'hx-post="{SPEED}" hx-trigger="change"' in section
        assert 'type="range" name="spotlight_crawl_speed" value="25"' in section
        assert 'min="10" max="80" step="5"' in section
        assert '<label for="id_spotlight_crawl_speed" class="pl-form-label">' in section
        assert "Crawl speed" in section
        assert ">Slow</span>" in section and ">Fast</span>" in section
        assert "data-crawl-speed-value>25 px a second</output>" in section
        assert 'data-crawl-speed-stored="25"' in section

    def it_shows_the_saved_speed(admin_client: Client):
        config = SiteConfiguration.load()
        config.spotlight_crawl_speed = 45
        config.save(update_fields=["spotlight_crawl_speed"])

        section = _crawl_speed(admin_client.get(PAGE).content.decode())

        assert 'value="45"' in section
        assert "data-crawl-speed-value>45 px a second</output>" in section
        assert 'data-ticker-speed="45"' in section

    def it_previews_the_live_messages_with_the_real_ticker_track(admin_client: Client):
        AnnouncementFactory(body="<p>Zorblax live preview</p>", position=0)
        AnnouncementFactory(body="<p>Zorblax switched off</p>", position=1, is_active=False)

        preview = _crawl_speed(admin_client.get(PAGE).content.decode()).split("data-crawl-speed-preview", 1)[1]

        assert '<div class="pl-ticker__track" data-ticker-track inert>' in preview
        assert "data-ticker-copy" in preview
        assert "Zorblax live preview" in preview
        assert "Zorblax switched off" not in preview
        assert CRAWL_SPEED_SAMPLE not in preview

    def it_previews_a_sample_line_when_none_are_live(admin_client: Client):
        AnnouncementFactory(body="<p>Zorblax switched off</p>", is_active=False)

        preview = _crawl_speed(admin_client.get(PAGE).content.decode()).split("data-crawl-speed-preview", 1)[1]

        assert "data-ticker-sample" in preview
        assert preview.count(CRAWL_SPEED_SAMPLE) == 2  # the list and its copy
        assert "Zorblax switched off" not in preview

    def it_paces_the_preview_with_homes_script(admin_client: Client):
        html = admin_client.get(PAGE).content.decode()

        assert "window.plTicker = {" in html
        assert "window.plTicker.pace(preview);" in html


def describe_saving_the_crawl_speed():
    def it_saves_over_htmx_with_the_saved_toast(admin_client: Client):
        response = admin_client.post(SPEED, {"spotlight_crawl_speed": "40"}, **HTMX)

        assert response.status_code == 204
        assert _toast(response) == {"message": CRAWL_SPEED_SAVED, "type": "success"}
        assert _speed() == 40

    @pytest.mark.parametrize("value", ["5", "85", "27", "fast", ""])
    def it_refuses_a_speed_off_the_slider_with_an_error_toast(admin_client: Client, value: str):
        response = admin_client.post(SPEED, {"spotlight_crawl_speed": value}, **HTMX)

        assert response.status_code == 400
        assert _toast(response) == {"message": CRAWL_SPEED_REFUSED, "type": "error"}
        assert _speed() == 25

    def it_accepts_both_ends(admin_client: Client):
        for value in (10, 80):
            assert admin_client.post(SPEED, {"spotlight_crawl_speed": str(value)}, **HTMX).status_code == 204
            assert _speed() == value

    def it_saves_without_htmx_and_returns_to_the_card(admin_client: Client):
        response = admin_client.post(SPEED, {"spotlight_crawl_speed": "30"})

        assert response.status_code == 302
        assert response.url == f"{PAGE}#announcements"
        assert _messages(response) == [CRAWL_SPEED_SAVED]
        assert _speed() == 30

    def it_refuses_without_htmx_and_returns_to_the_card(admin_client: Client):
        response = admin_client.post(SPEED, {"spotlight_crawl_speed": "99"})

        assert response.status_code == 302
        assert response.url == f"{PAGE}#announcements"
        assert _messages(response) == [CRAWL_SPEED_REFUSED]
        assert _speed() == 25

    def it_writes_only_its_own_column(admin_client: Client):
        stale = SiteConfiguration.load()
        SiteConfiguration.objects.filter(pk=1).update(door_code="4242")

        form = SpotlightCrawlSpeedForm({"spotlight_crawl_speed": "50"}, instance=stale)
        assert form.is_valid()
        form.save()

        fresh = SiteConfiguration.load()
        assert fresh.spotlight_crawl_speed == 50
        assert fresh.door_code == "4242"

    def it_refuses_a_member_who_is_not_an_admin(member_client: Client):
        response = member_client.post(SPEED, {"spotlight_crawl_speed": "40"}, **HTMX)

        assert response.status_code == 403
        assert _speed() == 25

    def it_sends_a_signed_out_visitor_to_sign_in(client: Client):
        response = client.post(SPEED, {"spotlight_crawl_speed": "40"})

        assert response.status_code == 302
        assert "/accounts/login/" in response.url
        assert _speed() == 25

    def it_answers_only_a_post(admin_client: Client):
        assert admin_client.get(SPEED).status_code == 405
