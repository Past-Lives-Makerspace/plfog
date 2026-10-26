"""Home's Poll card and Past Polls link (#925 AC 3, 4, 5 and 10)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from polls.models import Poll
from tests.membership.factories import MembershipPlanFactory
from tests.polls.factories import PollVoteFactory, poll_with

pytestmark = pytest.mark.django_db

HOME = reverse("hub_home")
PAST_POLLS = reverse("polls:index")


@pytest.fixture
def member_client(client: Client) -> Client:
    MembershipPlanFactory()
    client.force_login(User.objects.create_user(username="pollhomer", password="pass"))
    return client


def _closed(question: str) -> Poll:
    opens = timezone.now() - timedelta(days=10)
    return poll_with("Laser", "Lathe", question=question, opens_at=opens, closes_at=opens + timedelta(days=7))


def describe_the_poll_card():
    def it_shows_the_open_poll_with_its_answers(member_client: Client):
        poll = poll_with("Laser", "Lathe", question="Zorblax on home?")

        html = member_client.get(HOME).content.decode()

        block = html.split('data-block="poll"', 1)[1].split("</section>", 1)[0]
        assert "Zorblax on home?" in block
        assert f'hx-post="{reverse("polls:vote", args=[poll.pk])}"' in block
        assert '<input type="hidden" name="variant" value="home">' in block
        assert f'<input type="hidden" name="next" value="{HOME}">' in block
        assert f'href="{PAST_POLLS}"' in block

    def it_shows_the_counts_and_the_members_answer_once_voted(member_client: Client):
        poll = poll_with("Laser", "Lathe", votes=(1, 0), question="Zorblax voted?")
        member = User.objects.get(username="pollhomer").member
        PollVoteFactory(choice=poll.choices.get(text="Lathe"), member=member)

        block = member_client.get(HOME).content.decode().split('data-block="poll"', 1)[1].split("</section>", 1)[0]

        assert "data-poll-choices" not in block
        assert "1 vote · 50%" in block
        assert "Your vote" in block

    def it_shows_nothing_for_polls_with_none_open(member_client: Client):
        _closed("Zorblax long gone")

        html = member_client.get(HOME).content.decode()

        assert 'data-block="poll"' not in html
        assert "data-poll-card" not in html
        assert "Zorblax long gone" not in html

    def it_shows_nothing_with_no_polls_at_all(member_client: Client):
        html = member_client.get(HOME).content.decode()

        assert 'data-block="poll"' not in html
        assert "data-poll-card" not in html


def describe_the_past_polls_link():
    def it_sits_under_the_open_poll_in_title_case(member_client: Client):
        poll_with("Laser", "Lathe")

        html = member_client.get(HOME).content.decode()

        assert (
            f'href="{PAST_POLLS}" class="pl-home-block__more pl-poll-home__more" data-poll-past-link>See Past Polls →</a>'
            in html
        )

    def it_is_no_longer_a_quick_link(member_client: Client):
        poll_with("Laser", "Lathe")

        html = member_client.get(HOME).content.decode()

        assert "data-quicklink-past-polls" not in html
        assert f'href="{PAST_POLLS}" class="pl-quicklink"' not in html


def describe_folding_the_poll():
    def it_offers_hide_on_an_unanswered_poll(member_client: Client):
        poll = poll_with("Laser", "Lathe", question="Zorblax fold?")

        block = member_client.get(HOME).content.decode().split('data-block="poll"', 1)[1].split("</section>", 1)[0]

        assert f'data-poll-home="{poll.pk}"' in block
        assert "data-poll-answered" not in block
        assert "data-poll-toggle" in block

    def it_marks_an_answered_poll_so_it_starts_folded(member_client: Client):
        poll = poll_with("Laser", "Lathe", question="Zorblax folded?")
        member = User.objects.get(username="pollhomer").member
        PollVoteFactory(choice=poll.choices.get(text="Lathe"), member=member)

        block = member_client.get(HOME).content.decode().split('data-block="poll"', 1)[1].split("</section>", 1)[0]

        assert "data-poll-answered" in block
        assert '<span class="pl-poll-home__voted" data-poll-summary-voted>Voted</span>' in block
        assert "Zorblax folded?" in block


def describe_its_place_on_home():
    def it_sits_last_in_the_member_blocks_just_above_get_the_app(member_client: Client):
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.google_play_url = "https://play.google.com/store/apps/details?id=zorblax"
        config.save()
        poll_with("Laser", "Lathe")

        html = member_client.get(HOME).content.decode()

        poll = html.index('data-block="poll"')
        assert (
            html.index('<h2 class="pl-home-heading">Your Guilds</h2>')
            < poll
            < html.index("hub-card pl-home-block pl-app-badges")
        )


def describe_the_spotlight():
    def it_never_carries_the_poll(member_client: Client):
        poll_with("Laser", "Lathe", question="Zorblax not in the spotlight?")

        settings_page = member_client.get(reverse("hub_user_settings")).content.decode()
        home = member_client.get(HOME).content.decode()

        assert "data-poll-card" not in settings_page
        assert "Zorblax not in the spotlight?" not in settings_page
        assert home.count("data-poll-card=") == 1
