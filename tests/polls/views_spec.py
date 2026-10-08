"""Members vote and browse /polls/ (#708 part 2)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Member
from membership.services.provisioning import provision_user_for_member
from polls.models import Poll, PollVote
from tests.membership.factories import MemberFactory
from tests.polls.factories import PollVoteFactory, poll_with

pytestmark = pytest.mark.django_db

INDEX = reverse("polls:index")


def _client_for(status: str = Member.Status.ACTIVE) -> tuple[Client, Member]:
    member = MemberFactory()
    provision_user_for_member(member)
    member.refresh_from_db()
    if member.status != status:
        member.status = status
        member.save(update_fields=["status"])
    client = Client()
    client.force_login(member.user)
    return client, member


def _closed(question: str, days_ago: int, votes: tuple[int, ...] = (1, 0)) -> Poll:
    opens = timezone.now() - timedelta(days=days_ago + 7)
    return poll_with(
        "Laser", "Lathe", votes=votes, question=question, opens_at=opens, closes_at=opens + timedelta(days=7)
    )


def _vote(client: Client, poll: Poll, text: str, *, htmx: bool, next_url: str = "/home/"):
    choice = poll.choices.get(text=text)
    headers = {"HTTP_HX_REQUEST": "true"} if htmx else {}
    return client.post(reverse("polls:vote", args=[poll.pk]), {"choice": choice.pk, "next": next_url}, **headers)


def describe_the_polls_page():
    def it_sends_a_signed_out_visitor_to_sign_in():
        response = Client().get(INDEX)

        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_keeps_a_guest_account_out():
        client, _member = _client_for(Member.Status.GUEST)
        poll_with("Laser", "Lathe", question="Zorblax guest?")

        response = client.get(INDEX)

        assert response.status_code != 200
        assert b"Zorblax guest?" not in response.content

    def it_refuses_a_signed_in_user_with_no_member_record():
        user = User.objects.create_user(username="nomember", password="pass")
        Member.objects.filter(user=user).delete()
        client = Client()
        client.force_login(user)

        assert client.get(INDEX).status_code == 403

    def it_shows_the_empty_state():
        client, _member = _client_for()

        assert b"data-polls-empty" in client.get(INDEX).content

    def it_lists_every_poll_newest_first_with_results():
        client, _member = _client_for()
        _closed("Zorblax older", days_ago=20, votes=(0, 3))
        _closed("Zorblax newer", days_ago=3, votes=(2, 2))
        poll_with("Laser", "Lathe", question="Zorblax open")

        html = client.get(INDEX).content.decode()

        assert html.index("Zorblax open") < html.index("Zorblax newer") < html.index("Zorblax older")
        older = html.split("Zorblax older", 1)[1]
        assert "3 votes · 100%" in older
        assert "3 votes</span>" in older

    def it_offers_the_open_polls_answers_until_the_member_votes():
        client, _member = _client_for()
        poll = poll_with("Laser", "Lathe", question="Zorblax open")

        html = client.get(INDEX).content.decode()

        assert f'hx-post="{reverse("polls:vote", args=[poll.pk])}"' in html
        assert f'data-poll-choice="{poll.choices.get(text="Laser").pk}"' in html

    def it_says_when_the_open_poll_closes_with_the_time():
        client, _member = _client_for()
        poll = poll_with("Laser", "Lathe", question="Zorblax open")

        html = client.get(INDEX).content.decode()

        assert f"<span data-poll-closes>Closes {poll.closes_label}</span>" in html
        assert " at " in poll.closes_label

    def it_marks_the_members_own_answer_after_voting():
        client, member = _client_for()
        poll = poll_with("Laser", "Lathe")
        lathe = poll.choices.get(text="Lathe")
        PollVoteFactory(choice=lathe, member=member)

        html = client.get(INDEX).content.decode()

        assert "data-poll-choices" not in html
        assert f'data-poll-result="{lathe.pk}" data-my-vote' in html
        assert "Your vote" in html

    def it_pages_ten_at_a_time():
        client, _member = _client_for()
        for day in range(12):
            _closed(f"Zorblax {day:02d}", days_ago=day + 1, votes=())

        page_one = client.get(INDEX).content.decode()
        page_two = client.get(f"{INDEX}?page=2").content.decode()

        assert "Zorblax 09" in page_one
        assert "Zorblax 10" not in page_one
        assert "Zorblax 10" in page_two
        assert "Page 2 of 2" in page_two

    def it_never_names_a_voter():
        client, _member = _client_for()
        poll = _closed("Zorblax anonymous", days_ago=3, votes=(0, 0))
        PollVoteFactory(
            choice=poll.choices.get(text="Laser"), member=MemberFactory(full_legal_name="Zorblax Voterperson")
        )

        assert b"Zorblax Voterperson" not in client.get(INDEX).content


def describe_voting():
    def it_swaps_in_the_results_with_the_answer_marked_over_htmx():
        client, member = _client_for()
        poll = poll_with("Laser", "Lathe")

        response = _vote(client, poll, "Lathe", htmx=True)

        html = response.content.decode()
        assert response.status_code == 200
        assert html.lstrip().startswith(f'<article class="pl-poll-card pl-poll-card--open" id="poll-{poll.pk}"')
        assert "data-poll-choices" not in html
        assert "1 vote · 100%" in html
        assert "Your vote" in html
        assert "Thanks, your vote is in." in response["HX-Trigger"]
        assert PollVote.objects.get().member == member

    def it_goes_back_to_next_after_a_plain_post():
        client, _member = _client_for()
        poll = poll_with("Laser", "Lathe")

        response = _vote(client, poll, "Laser", htmx=False, next_url="/home/")

        assert response.status_code == 302
        assert response.url == "/home/"
        assert PollVote.objects.count() == 1

    def it_ignores_a_next_that_leaves_the_site():
        client, _member = _client_for()

        response = _vote(client, poll_with("Laser", "Lathe"), "Laser", htmx=False, next_url="https://evil.example/")

        assert response.url == INDEX

    def it_refuses_a_second_vote_and_keeps_the_first():
        client, _member = _client_for()
        poll = poll_with("Laser", "Lathe")
        _vote(client, poll, "Laser", htmx=True)

        response = _vote(client, poll, "Lathe", htmx=True)

        assert "You already voted in this poll." in response["HX-Trigger"]
        assert list(PollVote.objects.values_list("choice__text", flat=True)) == ["Laser"]

    def it_refuses_a_closed_poll():
        client, _member = _client_for()
        poll = _closed("Zorblax closed", days_ago=1)

        response = _vote(client, poll, "Lathe", htmx=False)

        assert response.status_code == 302
        assert PollVote.objects.count() == 1  # only the factory's vote
        assert "This poll has closed." in [m.message for m in response.wsgi_request._messages]

    def it_rejects_an_answer_from_another_poll():
        client, _member = _client_for()
        poll = poll_with("Laser", "Lathe")
        other = poll_with(
            "Kiln", "Wheel", opens_at=timezone.now() - timedelta(days=30), closes_at=timezone.now() - timedelta(days=20)
        )

        response = client.post(reverse("polls:vote", args=[poll.pk]), {"choice": other.choices.first().pk})

        assert response.status_code == 400
        assert not PollVote.objects.filter(poll=poll).exists()

    def it_rejects_a_missing_answer():
        client, _member = _client_for()

        response = client.post(reverse("polls:vote", args=[poll_with("Laser", "Lathe").pk]), {"choice": ""})

        assert response.status_code == 400

    def it_is_post_only():
        client, _member = _client_for()

        assert client.get(reverse("polls:vote", args=[poll_with("Laser", "Lathe").pk])).status_code == 405

    def it_sends_nothing():
        client, _member = _client_for()

        _vote(client, poll_with("Laser", "Lathe"), "Laser", htmx=True)

        assert mail.outbox == []
