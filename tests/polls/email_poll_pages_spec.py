"""The email poll pages (#1142 part 2): the plain link, the personal email link, results and the Polls page."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from membership.models import AnnouncementDraft, Member
from membership.services.provisioning import provision_user_for_member
from polls.models import EmailPoll, EmailPollResponse, QuestionKind, QuestionSpec, RunLength
from tests.membership.factories import AnnouncementDraftFactory, GuildFactory, MemberFactory
from tests.polls.factories import EmailPollFactory, sent_email_poll

pytestmark = pytest.mark.django_db

SHOP = QuestionSpec("Which zorblax shop?", QuestionKind.PICK_ONE, ("Zorwood", "Zormetal"))
TOOLS = QuestionSpec("Which zorblax tools?", QuestionKind.PICK_ANY, ("Zorlathe", "Zorlaser", "Zorkiln"))
IDEAS = QuestionSpec("Any zorblax ideas?", QuestionKind.OPEN)
MINE = reverse("polls:mine")


def _client_for(name: str = "", *, fog_role: str = Member.FogRole.MEMBER) -> tuple[Client, Member]:
    member = MemberFactory(full_legal_name=name or "Zorblax Person", fog_role=fog_role)
    provision_user_for_member(member)
    member.refresh_from_db()
    client = Client()
    client.force_login(member.user)
    return client, member


def _quiet_draft() -> AnnouncementDraft:
    """A poll row to an empty guild: open() records nobody, so each spec names its recipients."""
    return AnnouncementDraftFactory(
        kind=AnnouncementDraft.Kind.POLL, audience=AnnouncementDraft.Audience.GUILD, guild=GuildFactory()
    )


def _survey(**kwargs: object) -> EmailPoll:
    kwargs.setdefault("draft", _quiet_draft())
    return sent_email_poll(SHOP, TOOLS, IDEAS, now=timezone.now(), **kwargs)


def _closed(**kwargs: object) -> EmailPoll:
    kwargs.setdefault("draft", _quiet_draft())
    return sent_email_poll(IDEAS, now=timezone.now() - timedelta(days=10), run_days=RunLength.ONE_DAY, **kwargs)


def _pk(poll: EmailPoll, position: int, text: str = "") -> int:
    question = poll.questions.get(position=position)
    return question.options.get(text=text).pk if text else question.pk


def _post(poll: EmailPoll, *, shop: str = "Zorwood", tools: tuple[str, ...] = ("Zorlathe",), idea: str = "") -> dict:
    data: dict = {f"q{_pk(poll, 2)}": idea}
    if shop:
        data[f"q{_pk(poll, 0)}"] = str(_pk(poll, 0, shop))
    data[f"q{_pk(poll, 1)}"] = [str(_pk(poll, 1, text)) for text in tools]
    return data


def _html(response) -> str:
    return response.content.decode()


def describe_the_plain_link():
    def it_sends_a_signed_out_visitor_to_sign_in():
        poll = _survey()
        response = Client().get(poll.answer_path)
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_hides_a_poll_still_in_drafts():
        client, _member = _client_for()
        poll = EmailPollFactory()
        poll.replace_questions([IDEAS])
        assert client.get(reverse("polls:email_poll", args=[poll.pk])).status_code == 404

    def it_offers_each_question_as_tappable_rows_and_a_textarea():
        client, _member = _client_for()
        poll = _survey()

        html = _html(client.get(poll.answer_path))

        assert "data-email-poll-form" in html
        assert f'type="radio" name="q{_pk(poll, 0)}" value="{_pk(poll, 0, "Zorwood")}"' in html
        assert f'type="checkbox" name="q{_pk(poll, 1)}" value="{_pk(poll, 1, "Zorkiln")}"' in html
        assert 'class="hub-form-group pl-ask__written-field"' in html
        assert f'<textarea name="q{_pk(poll, 2)}"' in html
        assert 'data-guidance-target="home.email-polls-answer">Send Answers</button>' in html
        assert "Closes " + poll.closes_label in html
        assert "data-email-poll-results" not in html

    def it_saves_answers_and_then_shows_them_filled_in_with_results():
        client, member = _client_for("Ann Zorblax")
        poll = _survey()

        response = client.post(poll.answer_path, _post(poll, tools=("Zorlathe", "Zorkiln"), idea="Zorblax nights"))

        assert response.status_code == 302
        assert response.url == poll.answer_path
        html = _html(client.get(poll.answer_path))
        assert 'data-guidance-target="home.email-polls-answer">Update Answers</button>' in html
        assert f'value="{_pk(poll, 0, "Zorwood")}" checked' in html
        assert f'value="{_pk(poll, 1, "Zorkiln")}" checked' in html
        assert f'value="{_pk(poll, 1, "Zorlaser")}" checked' not in html
        assert ">Zorblax nights</textarea>" in html
        assert "data-email-poll-results" in html
        assert 'class="pl-poll-results__bar" max="100" value="100"' in html
        assert '<span class="pl-ask__reply-name">Ann Zorblax</span>' in html
        assert 'role="region" aria-label="Responses"' in html
        assert '<th scope="row">Ann Zorblax</th><td>Zorwood</td><td>Zorlathe, Zorkiln</td>' in html
        assert EmailPollResponse.objects.get().member == member
        assert "pl-poll-results__mine" not in html
        assert "data-my-vote" not in html

    def it_updates_answers_in_place():
        client, member = _client_for()
        poll = _survey()
        client.post(poll.answer_path, _post(poll))
        client.post(poll.answer_path, _post(poll, shop="Zormetal"))
        assert poll.answers_of(member)[_pk(poll, 0)] == [_pk(poll, 0, "Zormetal")]
        assert EmailPollResponse.objects.count() == 1

    def it_shows_a_missing_pick_beside_its_question_and_saves_nothing():
        client, _member = _client_for()
        poll = _survey()

        response = client.post(poll.answer_path, _post(poll, shop=""))

        assert response.status_code == 200
        html = _html(response)
        assert f'data-email-poll-question="{_pk(poll, 0)}" aria-invalid="true"' in html
        assert "Pick an answer." in html
        assert not EmailPollResponse.objects.exists()

    def it_refuses_an_all_blank_submission_with_a_message():
        client, _member = _client_for()
        poll = sent_email_poll(IDEAS, now=timezone.now(), draft=_quiet_draft())

        response = client.post(poll.answer_path, {f"q{_pk(poll, 0)}": "  "})

        assert response.status_code == 200
        assert "Answer at least one question." in _html(response)
        assert not EmailPollResponse.objects.exists()

    def it_tells_someone_outside_an_audience_only_poll_and_nothing_more():
        client, _member = _client_for()
        poll = _survey(audience_only=True)

        html = _html(client.get(poll.answer_path))

        assert "This poll was sent to a different group." in html
        assert "data-email-poll-form" not in html
        assert "data-email-poll-results" not in html
        assert "data-email-poll-closes" not in html
        assert client.post(poll.answer_path, _post(poll)).status_code == 200
        assert not EmailPollResponse.objects.exists()

    def it_lets_a_recipient_answer_an_audience_only_poll():
        client, member = _client_for()
        poll = _survey(audience_only=True)
        poll.record_recipients({member.user_id})
        assert "data-email-poll-form" in _html(client.get(poll.answer_path))

    def it_shows_the_sender_results_without_an_answer_form_on_an_audience_only_poll():
        client, member = _client_for()
        draft = AnnouncementDraftFactory(
            kind=AnnouncementDraft.Kind.POLL,
            author=member.user,
            audience=AnnouncementDraft.Audience.GUILD,
            guild=GuildFactory(),
        )
        poll = _survey(draft=draft, audience_only=True)

        html = _html(client.get(poll.answer_path))

        assert "data-email-poll-results" in html
        assert "data-email-poll-empty>No answers yet." in html
        assert "data-email-poll-form" not in html
        assert "different group" not in html

    def it_shows_an_admin_results_and_still_lets_them_answer():
        client, _member = _client_for(fog_role=Member.FogRole.ADMIN)
        html = _html(client.get(_survey().answer_path))
        assert "data-email-poll-results" in html
        assert "data-email-poll-form" in html

    def it_shows_closed_to_someone_who_never_answered():
        client, _member = _client_for()
        poll = _closed()

        html = _html(client.get(poll.answer_path))

        assert "data-email-poll-closed" in html
        assert "data-email-poll-form" not in html
        assert "data-email-poll-results" not in html

    def it_shows_results_after_closing_to_someone_who_answered():
        client, member = _client_for()
        poll = _closed()
        EmailPollResponse.objects.create(poll=poll, member=member)

        html = _html(client.get(poll.answer_path))

        assert "data-email-poll-results" in html
        assert "data-email-poll-form" not in html


def describe_the_email_link():
    def _recipient_link(poll: EmailPoll) -> tuple[Member, str]:
        member = MemberFactory(full_legal_name="Ben Zorblax")
        provision_user_for_member(member)
        member.refresh_from_db()
        poll.record_recipients({member.user_id})
        return member, reverse("polls:email_poll_link", args=[poll.make_answer_token(member)])

    def it_answers_without_signing_in_as_the_member_it_names():
        poll = _survey()
        member, link = _recipient_link(poll)
        client = Client()

        assert "data-email-poll-form" in _html(client.get(link))
        response = client.post(link, _post(poll, idea="Zorblax from email"))

        assert response.status_code == 302
        assert response.url == link
        assert EmailPollResponse.objects.get().member == member
        assert "_auth_user_id" not in client.session
        html = _html(client.get(link))
        assert "Update Answers" in html
        assert '<span class="pl-ask__reply-name">Ben Zorblax</span>' in html

    def it_answers_nothing_on_a_plain_open():
        poll = _survey()
        _member, link = _recipient_link(poll)
        Client().get(link)
        assert not EmailPollResponse.objects.exists()

    def it_lands_a_tampered_link_on_a_friendly_page_with_sign_in():
        poll = _survey()
        _member, link = _recipient_link(poll)

        response = Client().get(link[:-3] + "xx/")

        assert response.status_code == 400
        html = _html(response)
        assert "This Link Doesn't Work" in html
        assert f'href="{reverse("account_login")}?next={MINE}"' in html

    def it_lands_a_closed_poll_link_on_a_friendly_page():
        poll = _closed()
        _member, link = _recipient_link(poll)

        response = Client().get(link)

        assert response.status_code == 200
        assert "This Poll Has Closed" in _html(response)
        assert "data-email-poll-sign-in" in _html(response)

    def it_refuses_a_link_for_someone_the_poll_was_not_sent_to():
        poll = _survey()
        stranger = MemberFactory()
        link = reverse("polls:email_poll_link", args=[poll.make_answer_token(stranger)])
        assert Client().get(link).status_code == 400


def describe_the_polls_page():
    def it_sends_a_signed_out_visitor_to_sign_in():
        response = Client().get(MINE)
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_says_so_when_none_were_sent():
        client, _member = _client_for()
        assert "data-email-poll-list-empty>No polls yet." in _html(client.get(MINE))

    def it_lists_the_members_polls_newest_first_with_what_they_can_do():
        client, member = _client_for()
        old_closed = _closed()
        answered_closed = _closed()
        open_answered = _survey()
        open_new = _survey()
        not_mine = _survey()
        for poll in (old_closed, answered_closed, open_answered, open_new):
            poll.record_recipients({member.user_id})
        EmailPollResponse.objects.create(poll=answered_closed, member=member)
        EmailPollResponse.objects.create(poll=open_answered, member=member)

        html = _html(client.get(MINE))

        order = [
            html.index(f'data-email-poll-row="{poll.pk}"')
            for poll in (open_new, open_answered, answered_closed, old_closed)
        ]
        assert order == sorted(order)
        assert f'data-email-poll-row="{not_mine.pk}"' not in html
        rows = {
            poll.pk: html.split(f'data-email-poll-row="{poll.pk}"')[1].split("</li>")[0]
            for poll in (old_closed, answered_closed, open_answered, open_new)
        }
        assert "data-email-poll-action>Answer</a>" in rows[open_new.pk]
        assert "data-email-poll-action>Update</a>" in rows[open_answered.pk]
        assert "data-email-poll-action>View Results</a>" in rows[answered_closed.pk]
        assert "data-email-poll-action" not in rows[old_closed.pk]
        assert "Closed" in rows[old_closed.pk]

    def it_costs_the_same_queries_for_more_polls():
        client, member = _client_for()

        def count() -> int:
            with CaptureQueriesContext(connection) as queries:
                client.get(MINE)
            return len(queries)

        _survey().record_recipients({member.user_id})
        client.get(MINE)  # The first visit makes the member's tab and settles the session.
        one = count()
        for _ in range(3):
            _survey().record_recipients({member.user_id})
        assert count() == one


def describe_an_account_with_no_member():
    def it_is_refused_on_the_poll_and_the_polls_page():
        from django.contrib.auth.models import User
        from django.db.models.signals import post_save
        from factory.django import mute_signals

        with mute_signals(post_save):
            user = User.objects.create_user(username="zorblax_nomember", email="nomember@example.com")
        client = Client()
        client.force_login(user)

        assert client.get(_survey().answer_path).status_code == 403
        assert client.get(MINE).status_code == 403
