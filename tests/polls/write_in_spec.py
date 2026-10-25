"""Write your own answer (#925 part 3, AC 11 to 13): the toggle, the one vote, Anonymous, and the results list."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from membership.models import Member
from polls.forms import WriteInForm
from polls.models import (
    ANONYMOUS,
    AlreadyVotedError,
    InvalidAnswerError,
    Poll,
    PollCard,
    PollVote,
    WrittenAnswer,
)
from tests.membership.factories import GuildFactory, MemberFactory, MembershipPlanFactory
from tests.polls.factories import PollFactory, poll_with

pytestmark = pytest.mark.django_db

PORTLAND = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 8, 9, 0, tzinfo=PORTLAND)
SECRET_NAME = "Zorblaxia Quuxworth"


def _open(*, allows_write_in: bool = True, votes: tuple[int, ...] = ()) -> Poll:
    return poll_with(
        "Laser",
        "Lathe",
        votes=votes,
        allows_write_in=allows_write_in,
        opens_at=NOW - timedelta(hours=1),
        closes_at=NOW + timedelta(days=6),
    )


def _named(name: str = SECRET_NAME) -> Member:
    return MemberFactory(full_legal_name=name, preferred_name="")


def describe_posting_with_the_toggle():
    def it_is_off_by_default():
        user = User.objects.create_user(username="zorblaxwritein")

        poll = Poll.post(question="Zorblax?", choices=["A", "B"], days=7, by=user, now=NOW)

        assert poll.allows_write_in is False

    def it_stores_the_toggle_when_on():
        user = User.objects.create_user(username="zorblaxwriteon")

        poll = Poll.post(question="Zorblax?", choices=["A", "B"], days=7, by=user, now=NOW, allows_write_in=True)

        assert poll.allows_write_in is True


def describe_voting_with_a_written_answer():
    def it_records_the_written_answer_as_the_one_vote_anonymous_by_default():
        poll = _open()
        member = MemberFactory()

        vote = poll.vote(member=member, now=NOW, write_in="  A bandsaw  ")

        assert (vote.choice, vote.write_in, vote.anonymous, vote.poll) == (None, "A bandsaw", True, poll)
        assert PollVote.objects.count() == 1

    def it_keeps_the_name_when_asked():
        vote = _open().vote(member=MemberFactory(), now=NOW, write_in="A bandsaw", anonymous=False)

        assert vote.anonymous is False

    def it_refuses_a_pick_after_a_written_answer():
        poll = _open()
        member = MemberFactory()
        poll.vote(member=member, now=NOW, write_in="A bandsaw")

        with pytest.raises(AlreadyVotedError):
            poll.vote(member=member, now=NOW, choice_pk=poll.choices.first().pk)

        assert PollVote.objects.count() == 1

    def it_refuses_a_written_answer_after_a_pick():
        poll = _open()
        member = MemberFactory()
        poll.vote(member=member, now=NOW, choice_pk=poll.choices.first().pk)

        with pytest.raises(AlreadyVotedError):
            poll.vote(member=member, now=NOW, write_in="A bandsaw")

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({}, "Pick an answer or write your own."),
            ({"write_in": "   "}, "Pick an answer or write your own."),
            ({"write_in": "A bandsaw", "choice_pk": 0}, "Pick an answer or write your own."),
            ({"write_in": "Z" * 121}, "Keep your answer to 120 characters or fewer."),
        ],
    )
    def it_refuses_neither_both_or_too_long(kwargs: dict, message: str):
        poll = _open()

        with pytest.raises(InvalidAnswerError, match=message):
            poll.vote(member=MemberFactory(), now=NOW, **kwargs)

        assert not PollVote.objects.exists()

    def it_accepts_an_answer_at_the_limit():
        assert len(_open().vote(member=MemberFactory(), now=NOW, write_in="Z" * 120).write_in) == 120

    def it_refuses_a_written_answer_on_a_poll_that_does_not_take_them():
        poll = _open(allows_write_in=False)

        with pytest.raises(InvalidAnswerError, match="picked answers only"):
            poll.vote(member=MemberFactory(), now=NOW, write_in="A bandsaw")


def describe_the_one_of_constraint():
    def it_refuses_a_vote_with_neither_answer():
        poll = _open()

        with pytest.raises(IntegrityError), transaction.atomic():
            PollVote(poll=poll, member=MemberFactory(), write_in="").save()

    def it_refuses_a_vote_with_both_answers():
        poll = _open()

        with pytest.raises(IntegrityError), transaction.atomic():
            PollVote(poll=poll, choice=poll.choices.first(), member=MemberFactory(), write_in="Both").save()


def describe_written_answers():
    def it_shows_anonymous_without_ever_reading_the_name():
        poll = _open()
        poll.vote(member=_named(), now=NOW, write_in="A bandsaw")

        answers = poll.written_answers()

        assert answers == [WrittenAnswer(text="A bandsaw", author=ANONYMOUS, is_mine=False)]
        assert SECRET_NAME not in repr(answers)

    def it_shows_the_name_when_the_member_chose_it():
        poll = _open()
        poll.vote(member=_named(), now=NOW, write_in="A bandsaw", anonymous=False)

        assert poll.written_answers()[0].author == SECRET_NAME

    def it_marks_the_viewers_own_answer():
        poll = _open()
        member = MemberFactory()
        poll.vote(member=member, now=NOW, write_in="Mine")
        poll.vote(member=MemberFactory(), now=NOW, write_in="Theirs")

        assert [answer.is_mine for answer in poll.written_answers(member)] == [True, False]

    def it_lists_none_for_a_poll_without_the_toggle_in_no_query():
        poll = _open(allows_write_in=False)

        with CaptureQueriesContext(connection) as queries:
            assert poll.written_answers() == []

        assert len(queries) == 0

    def it_counts_written_answers_in_the_total_and_the_percents():
        poll = _open(votes=(2, 1))
        poll.vote(member=MemberFactory(), now=NOW, write_in="A bandsaw")

        card = PollCard.for_poll(poll, MemberFactory(), NOW + timedelta(days=7))

        assert card.total_votes == 4
        assert [result.percent for result in card.results] == [50, 25]
        assert [result.percent for result in poll.results()] == [50, 25]


def describe_the_card():
    def it_offers_the_write_in_box_only_where_the_poll_allows_it():
        member = MemberFactory()

        assert PollCard.for_poll(_open(), member, NOW).offers_write_in is True
        assert PollCard.for_poll(_open(allows_write_in=False), member, NOW).offers_write_in is False

    def it_shows_results_once_the_member_has_written_their_answer():
        poll = _open()
        member = MemberFactory()
        poll.vote(member=member, now=NOW, write_in="A bandsaw")

        card = PollCard.for_poll(poll, member, NOW)

        assert card.has_voted is True
        assert card.shows_choices is False
        assert card.my_choice_pk is None
        assert card.written[0].is_mine is True

    def it_reads_written_answers_in_one_more_query_only_when_a_poll_takes_them():
        plain = [_open(allows_write_in=False) for _ in range(2)]
        mixed = [*plain, _open()]
        member = MemberFactory()

        with CaptureQueriesContext(connection) as without:
            PollCard.for_polls(plain, member, NOW)
        with CaptureQueriesContext(connection) as with_write_in:
            PollCard.for_polls(mixed, member, NOW)

        assert len(without) == 2
        assert len(with_write_in) == 3


def describe_the_write_in_form():
    def it_reads_a_post_without_the_marker_as_anonymous():
        poll = _open()
        form = WriteInForm.for_poll(poll, {f"poll-{poll.pk}-write_in": "A bandsaw"})

        assert form.is_valid()
        assert form.is_anonymous is True

    def it_shows_the_name_only_when_the_rendered_switch_was_turned_off():
        poll = _open()
        form = WriteInForm.for_poll(poll, {f"poll-{poll.pk}-write_in": "A bandsaw", f"poll-{poll.pk}-shown": "True"})

        assert form.is_valid()
        assert form.is_anonymous is False

    def it_keeps_the_ticked_box_anonymous():
        poll = _open()
        data = {
            f"poll-{poll.pk}-write_in": "A bandsaw",
            f"poll-{poll.pk}-shown": "True",
            f"poll-{poll.pk}-anonymous": "on",
        }

        form = WriteInForm.for_poll(poll, data)

        assert form.is_valid()
        assert form.is_anonymous is True


def _client() -> tuple[Client, Member]:
    MembershipPlanFactory()
    user = User.objects.create_user(username="zorblaxwriter", password="pass")
    member = user.member
    member.full_legal_name = SECRET_NAME
    member.preferred_name = ""
    member.save(update_fields=["full_legal_name", "preferred_name"])
    client = Client()
    client.force_login(user)
    return client, member


def _write(client: Client, poll: Poll, text: str, *, named: bool = False, htmx: bool = True) -> object:
    data = {f"poll-{poll.pk}-write_in": text, f"poll-{poll.pk}-shown": "True", "variant": "home", "next": "/"}
    if not named:
        data[f"poll-{poll.pk}-anonymous"] = "on"
    headers = {"HTTP_HX_REQUEST": "true"} if htmx else {}
    return client.post(reverse("polls:vote", args=[poll.pk]), data, **headers)


def describe_voting_over_the_web():
    def it_swaps_in_the_results_with_the_answer_as_anonymous_and_never_the_name():
        client, _member = _client()
        poll = poll_with("Laser", "Lathe", allows_write_in=True)

        response = _write(client, poll, "A bandsaw")

        html = response.content.decode()
        assert "A bandsaw" in html
        assert f">{ANONYMOUS}" in html
        assert "Your answer" in html
        assert SECRET_NAME not in html
        assert SECRET_NAME not in response["HX-Trigger"]
        assert PollVote.objects.get().anonymous is True

    def it_shows_the_name_when_the_switch_was_turned_off():
        client, _member = _client()
        poll = poll_with("Laser", "Lathe", allows_write_in=True)

        html = _write(client, poll, "A bandsaw", named=True).content.decode()

        assert SECRET_NAME in html
        assert PollVote.objects.get().anonymous is False

    def it_refuses_a_written_answer_on_a_poll_without_the_toggle():
        client, _member = _client()
        poll = poll_with("Laser", "Lathe")

        response = _write(client, poll, "A bandsaw")

        assert "This poll takes picked answers only." in response["HX-Trigger"]
        assert not PollVote.objects.exists()

    @pytest.mark.parametrize(("text", "message"), [("", "This field is required."), ("Z" * 121, "at most 120")])
    def it_refuses_an_empty_or_long_answer_with_its_reason(text: str, message: str):
        client, _member = _client()
        poll = poll_with("Laser", "Lathe", allows_write_in=True)

        response = _write(client, poll, text)

        assert message in response["HX-Trigger"]
        assert not PollVote.objects.exists()

    def it_goes_back_after_a_plain_post():
        client, _member = _client()
        poll = poll_with("Laser", "Lathe", allows_write_in=True)

        response = _write(client, poll, "A bandsaw", htmx=False)

        assert response.status_code == 302
        assert PollVote.objects.get().write_in == "A bandsaw"

    def it_offers_the_box_with_post_anonymously_ticked_on_home():
        client, _member = _client()
        poll = poll_with("Laser", "Lathe", allows_write_in=True)

        html = client.get(reverse("hub_home")).content.decode()

        assert "data-poll-write-in" in html
        assert f'name="poll-{poll.pk}-write_in"' in html
        assert 'maxlength="120"' in html
        assert f'name="poll-{poll.pk}-anonymous" id="id_poll-{poll.pk}-anonymous" checked' in html
        assert "Post anonymously" in html
        assert "Turn off to show your name beside your answer." in html

    def it_leaves_the_box_off_a_poll_without_the_toggle():
        client, _member = _client()
        poll_with("Laser", "Lathe")

        assert "data-poll-write-in" not in client.get(reverse("hub_home")).content.decode()


def describe_everyone_who_sees_results():
    def it_lists_named_and_anonymous_answers_on_past_polls_without_the_hidden_name():
        client, _member = _client()
        opens = timezone.now() - timedelta(days=10)
        poll = PollFactory(allows_write_in=True, opens_at=opens, closes_at=opens + timedelta(days=7))
        PollVote.objects.create(poll=poll, member=_named("Zorblax Hidden"), write_in="Quiet idea", anonymous=True)
        PollVote.objects.create(poll=poll, member=_named("Zorblax Shown"), write_in="Loud idea", anonymous=False)

        html = client.get(reverse("polls:index")).content.decode()
        written = html.split("data-poll-written", 1)[1].split("</ul>", 1)[0]

        assert written.index("Quiet idea") < written.index("Loud idea")
        assert ANONYMOUS in written
        assert "Zorblax Shown" in written
        assert "Zorblax Hidden" not in html

    def it_lists_them_for_the_admin_on_the_open_poll_and_the_results_page(client: Client):
        User.objects.create_superuser(username="zorblaxwriteadmin", password="pass", email="zw@example.com")
        client.login(username="zorblaxwriteadmin", password="pass")
        poll = poll_with("Laser", "Lathe", allows_write_in=True)
        PollVote.objects.create(poll=poll, member=_named("Zorblax Secret"), write_in="Quiet idea")

        page = client.get(reverse("polls:admin")).content.decode()
        results = client.get(reverse("polls:admin_poll", args=[poll.pk])).content.decode()

        for html in (page, results):
            assert "Quiet idea" in html
            assert ANONYMOUS in html
            assert "Zorblax Secret" not in html


def describe_the_create_forms():
    def it_offers_the_toggle_off_on_admin_tools_polls(client: Client):
        User.objects.create_superuser(username="zorblaxtoggle", password="pass", email="zt@example.com")
        client.login(username="zorblaxtoggle", password="pass")

        html = client.get(reverse("polls:admin")).content.decode()

        assert "Let members write their own answer" in html
        assert 'name="allows_write_in"' in html
        assert 'name="allows_write_in" checked' not in html

    def it_posts_a_site_wide_poll_with_the_toggle_on(client: Client):
        User.objects.create_superuser(username="zorblaxtoggleon", password="pass", email="zo@example.com")
        client.login(username="zorblaxtoggleon", password="pass")
        data = {"question": "Zorblax?", "choice": ["A", "B"], "days": "7", "allows_write_in": "on"}

        client.post(reverse("polls:admin_new"), data)

        assert Poll.objects.get().allows_write_in is True

    def it_posts_a_guild_poll_with_the_toggle_on(client: Client):
        MembershipPlanFactory()
        user = User.objects.create_user(username="zorblaxtogglelead", password="pass")
        guild = GuildFactory(guild_lead=user.member)
        client.force_login(user)
        data = {"question": "Zorblax?", "choice": ["A", "B"], "days": "7", "allows_write_in": "on"}

        page = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        client.post(reverse("polls:guild_new", args=[guild.slug]), data)

        assert "Let members write their own answer" in page
        assert Poll.objects.get().allows_write_in is True
