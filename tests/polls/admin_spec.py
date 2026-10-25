"""Admin Tools > Polls (#925 AC 1 and 2): permissions, posting, one open poll, Close now, the past polls table."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from polls.models import Poll
from tests.membership.factories import MemberFactory
from tests.polls.factories import PollVoteFactory, poll_with

pytestmark = pytest.mark.django_db

PAGE = reverse("polls:admin")
NEW = reverse("polls:admin_new")


@pytest.fixture
def admin_client(client: Client) -> Client:
    User.objects.create_superuser(username="polladmin", password="pass", email="polladmin@example.com")
    client.login(username="polladmin", password="pass")
    return client


@pytest.fixture
def member_client(client: Client) -> Client:
    User.objects.create_user(username="pollmember", password="pass", email="pollmember@example.com")
    client.login(username="pollmember", password="pass")
    return client


def _poll_post(question: str = "Zorblax next?", choices: tuple[str, ...] = ("Laser", "Lathe"), **extra: str) -> dict:
    return {"question": question, "choice": list(choices), "days": "7", **extra}


def _past_poll(question: str, days_ago: int, votes: tuple[int, ...] = (1, 0)) -> Poll:
    opens = timezone.now() - timedelta(days=days_ago + 7)
    return poll_with(
        "Laser", "Lathe", votes=votes, question=question, opens_at=opens, closes_at=opens + timedelta(days=7)
    )


def describe_permissions():
    @pytest.mark.parametrize(
        ("method", "url"),
        [
            ("get", PAGE),
            ("post", NEW),
            ("post", reverse("polls:admin_close", args=[1])),
            ("get", reverse("polls:admin_poll", args=[1])),
        ],
    )
    def it_refuses_a_member_who_is_not_an_admin(member_client: Client, method: str, url: str):
        assert getattr(member_client, method)(url).status_code == 403

    def it_sends_a_signed_out_visitor_to_sign_in(client: Client):
        response = client.get(PAGE)

        assert response.status_code == 302
        assert "/accounts/login/" in response.url


def describe_posting_a_poll():
    def it_opens_a_poll_now(admin_client: Client):
        response = admin_client.post(NEW, _poll_post(choices=("Laser", "Lathe", "Kiln")))

        assert response.status_code == 302
        assert response.url == PAGE
        poll = Poll.objects.get()
        assert poll.is_open(timezone.now())
        assert [choice.text for choice in poll.choices.all()] == ["Laser", "Lathe", "Kiln"]
        assert poll.closes_at - poll.opens_at == timedelta(days=7)

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            (_poll_post(choices=("Laser",)), "at least 2 answers"),
            (_poll_post(choices=tuple(f"Answer {n}" for n in range(7))), "at most 6 answers"),
            (_poll_post(choices=("Laser", " ")), "Fill in every answer"),
            (_poll_post(choices=("Laser", "laser")), "Two answers are the same"),
            (_poll_post(question=""), "This field is required"),
            ({**_poll_post(), "days": "0"}, "greater than or equal to 1"),
            ({**_poll_post(), "days": "61"}, "less than or equal to 60"),
            (_poll_post(choices=("Laser", "L" * 121)), "Keep each answer to 120 characters or fewer"),
            (_poll_post(question="Z" * 201), "at most 200 characters"),
        ],
    )
    def it_refuses_a_poll_with_a_readable_message(admin_client: Client, data: dict, message: str):
        response = admin_client.post(NEW, data)

        assert response.status_code == 200
        assert message in response.content.decode()
        assert not Poll.objects.exists()

    def it_keeps_what_was_typed_after_a_refusal(admin_client: Client):
        response = admin_client.post(NEW, _poll_post(choices=("Zorblax one",)))

        assert '["Zorblax one"]' in response.content.decode()

    def it_refuses_while_a_poll_is_open_with_a_message(admin_client: Client):
        current = poll_with("Laser", "Lathe")

        response = admin_client.post(NEW, _poll_post(question="Zorblax second?"))

        html = response.content.decode()
        assert response.status_code == 200
        assert "A poll is already open. Close it first, then post this one." in html
        assert "Zorblax second?" in html
        assert list(Poll.objects.open_at(timezone.now())) == [current]

    def it_says_on_the_page_that_the_open_poll_must_close_first(admin_client: Client):
        poll_with("Laser", "Lathe")

        assert "data-new-poll-blocked" in admin_client.get(PAGE).content.decode()

    def it_says_nothing_of_the_kind_with_no_poll_open(admin_client: Client):
        assert "data-new-poll-blocked" not in admin_client.get(PAGE).content.decode()

    def it_sends_nothing(admin_client: Client):
        admin_client.post(NEW, _poll_post())

        assert mail.outbox == []


def describe_the_open_poll():
    def it_shows_counts_percents_and_time_left(admin_client: Client):
        poll_with("Laser", "Lathe", votes=(3, 1), question="Zorblax next?")

        html = admin_client.get(PAGE).content.decode()
        block = html.split('id="open-poll"', 1)[1].split('id="new-poll"', 1)[0]

        assert "Zorblax next?" in block
        assert "3 votes · 75%" in block
        assert "1 vote · 25%" in block
        assert "Closes in" in block
        assert "data-close-poll" in block

    def it_says_so_when_none_is_open(admin_client: Client):
        assert "data-open-poll-empty" in admin_client.get(PAGE).content.decode()

    def it_closes_now(admin_client: Client):
        poll = poll_with("Laser", "Lathe")

        response = admin_client.post(reverse("polls:admin_close", args=[poll.pk]))

        assert response.status_code == 302
        assert response.url == PAGE
        poll.refresh_from_db()
        assert not poll.is_open(timezone.now())

    def it_lets_a_new_poll_post_once_closed(admin_client: Client):
        poll = poll_with("Laser", "Lathe")
        admin_client.post(reverse("polls:admin_close", args=[poll.pk]))

        admin_client.post(NEW, _poll_post(question="Zorblax after?"))

        assert Poll.objects.open_poll(timezone.now()).question == "Zorblax after?"

    def it_is_post_only(admin_client: Client):
        assert admin_client.get(reverse("polls:admin_close", args=[poll_with("A", "B").pk])).status_code == 405


def describe_past_polls():
    def it_shows_the_empty_state(admin_client: Client):
        assert "data-past-polls-empty" in admin_client.get(PAGE).content.decode()

    def it_lists_closed_polls_newest_first_with_totals(admin_client: Client):
        _past_poll("Zorblax older", days_ago=20, votes=(0, 2))
        _past_poll("Zorblax newer", days_ago=3, votes=(1, 0))
        poll_with("Laser", "Lathe", question="Zorblax open now")

        html = admin_client.get(PAGE).content.decode()
        table = html.split("data-past-polls>", 1)[1].split("</table>", 1)[0]

        assert table.index("Zorblax newer") < table.index("Zorblax older")
        assert "Zorblax open now" not in table
        assert '<td data-label="Top answer">Lathe</td>' in table
        assert '<td data-label="Total votes">2</td>' in table

    def it_says_no_votes_for_a_poll_nobody_answered(admin_client: Client):
        _past_poll("Zorblax quiet", days_ago=3, votes=(0, 0))

        assert '<td data-label="Top answer">No votes</td>' in admin_client.get(PAGE).content.decode()

    def it_sorts_by_total_votes(admin_client: Client):
        _past_poll("Zorblax few", days_ago=3, votes=(1, 0))
        _past_poll("Zorblax many", days_ago=10, votes=(3, 2))

        html = admin_client.get(f"{PAGE}?sort=total_votes&dir=desc").content.decode()

        assert html.index("Zorblax many") < html.index("Zorblax few")

    def it_paginates(admin_client: Client):
        for day in range(30):
            _past_poll(f"Zorblax {day:02d}", days_ago=day + 1, votes=())

        page_one = admin_client.get(PAGE).content.decode()
        page_two = admin_client.get(f"{PAGE}?page=2").content.decode()

        assert "Zorblax 00" in page_one
        assert "Zorblax 29" not in page_one
        assert "Zorblax 29" in page_two

    def it_opens_a_polls_results_without_names(admin_client: Client):
        voter = MemberFactory(full_legal_name="Zorblax Voterperson")
        poll = _past_poll("Zorblax results", days_ago=3, votes=(0, 0))
        PollVoteFactory(choice=poll.choices.get(text="Laser"), member=voter)

        html = admin_client.get(reverse("polls:admin_poll", args=[poll.pk])).content.decode()

        assert "Zorblax results" in html
        assert "1 vote · 100%" in html
        assert ", closed " in html
        assert "Zorblax Voterperson" not in html

    def it_says_an_open_polls_results_are_still_open(admin_client: Client):
        poll = poll_with("Laser", "Lathe", question="Zorblax live")

        html = admin_client.get(reverse("polls:admin_poll", args=[poll.pk])).content.decode()

        assert ", open until " in html
