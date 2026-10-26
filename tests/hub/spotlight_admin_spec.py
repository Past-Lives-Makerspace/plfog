"""Admin Tools > Spotlight (#708): permissions and the page; polls removed in #890, the text and meeting in #935."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import NoReverseMatch, reverse

from core.factories import AnnouncementFactory

pytestmark = pytest.mark.django_db

PAGE = reverse("hub_admin_spotlight")


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
