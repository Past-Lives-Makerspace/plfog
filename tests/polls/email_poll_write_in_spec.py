"""Email polls part 5a (#1142 AC12, AC13): a Guild leads poll reaches admins; a pick question may end with a write-in."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from factory.django import mute_signals

from membership.models import AnnouncementDraft, Member
from polls.models import (
    AnswerRefusedError,
    EmailPoll,
    EmailPollOption,
    QuestionKind,
    QuestionSpec,
    validate_questions,
)
from tests.membership.factories import AnnouncementDraftFactory, GuildFactory, MemberFactory
from tests.polls.factories import EmailPollFactory, sent_email_poll

pytestmark = pytest.mark.django_db

WHEN = QuestionSpec("Which Thursday?", QuestionKind.PICK_ONE, ("3rd", "2nd", "4th"), write_in="Another time")
_seq = {"n": 0}


def _member(*, name: str = "", fog_role: str = Member.FogRole.MEMBER, status: str = Member.Status.ACTIVE) -> Member:
    _seq["n"] += 1
    member = MemberFactory(full_legal_name=name or f"Zorblax Person {_seq['n']}", fog_role=fog_role, status=status)
    with mute_signals(post_save):
        user = User.objects.create_user(
            username=f"zpw_{_seq['n']}", email=f"zpw{_seq['n']}@example.com", last_login=timezone.now()
        )
    member.user = user
    member.save(update_fields=["user"])
    return member


def _when_poll(**kwargs: object) -> EmailPoll:
    draft = AnnouncementDraftFactory(
        kind=AnnouncementDraft.Kind.POLL, audience=AnnouncementDraft.Audience.GUILD, guild=GuildFactory()
    )
    return sent_email_poll(WHEN, now=timezone.now(), draft=draft, **kwargs)


def _ids(poll: EmailPoll) -> tuple[int, dict[str, int]]:
    question = poll.questions.get()
    return question.pk, {option.text: option.pk for option in question.options.all()}


def describe_guild_leads_polls_reach_admins():
    def it_adds_every_active_admin_to_a_poll_but_not_to_the_email():
        lead = _member()
        GuildFactory(guild_lead=lead)
        admin = _member(fog_role=Member.FogRole.ADMIN)
        _member(fog_role=Member.FogRole.ADMIN, status=Member.Status.FORMER)
        _member()
        draft = AnnouncementDraftFactory(kind=AnnouncementDraft.Kind.POLL, audience=AnnouncementDraft.Audience.LEADS)

        assert draft.poll_recipient_ids() == {lead.user_id, admin.user_id}
        assert draft._leads_recipient_ids() == {lead.user_id}

    def it_counts_the_admins_in_the_poll_confirm():
        lead = _member()
        GuildFactory(guild_lead=lead)
        _member(fog_role=Member.FogRole.ADMIN)
        user = User.objects.create_superuser(username="zpw_super", email="zpw_super@x.com", password="p")
        client = Client()
        client.force_login(user)

        html = client.get(reverse("hub_compose")).content.decode()

        assert "} else if (this.audience === 'leads') {\n           members = 2;" in html


def describe_write_in_rules():
    def it_counts_the_write_in_toward_the_four():
        validate_questions([QuestionSpec("Q?", QuestionKind.PICK_ANY, ("A",), write_in="Other")])
        validate_questions([QuestionSpec("Q?", QuestionKind.PICK_ONE, ("A", "B", "C"), write_in="Other")])
        with pytest.raises(ValueError, match="the write-in included"):
            validate_questions([QuestionSpec("Q?", QuestionKind.PICK_ONE, ("A", "B", "C", "D"), write_in="Other")])
        with pytest.raises(ValueError, match="the write-in included"):
            validate_questions([QuestionSpec("Q?", QuestionKind.PICK_ONE, (), write_in="Other")])

    def it_refuses_a_write_in_on_an_open_question_or_one_too_long():
        with pytest.raises(ValueError, match="takes no options"):
            validate_questions([QuestionSpec("Q?", QuestionKind.OPEN, write_in="Other")])
        with pytest.raises(ValueError, match="longer than 120"):
            validate_questions([QuestionSpec("Q?", QuestionKind.PICK_ONE, ("A",), write_in="x" * 121)])

    def it_stores_the_write_in_last_and_only_once():
        poll = EmailPollFactory()
        poll.replace_questions([WHEN])
        options = list(poll.questions.get().options.all())
        assert [(o.text, o.is_write_in) for o in options] == [
            ("3rd", False),
            ("2nd", False),
            ("4th", False),
            ("Another time", True),
        ]
        with pytest.raises(IntegrityError), transaction.atomic():
            EmailPollOption.objects.create(question=options[0].question, position=9, text="Second", is_write_in=True)

    def it_tells_a_poll_with_a_write_in_from_one_without():
        plain = QuestionSpec("Which Thursday?", QuestionKind.PICK_ONE, ("3rd", "2nd", "4th", "Another time"))
        poll = EmailPollFactory()
        poll.replace_questions([WHEN])
        assert poll.signature[0][2] == ("3rd", "2nd", "4th", "write-in: Another time")
        assert poll.signature != [(plain.text, plain.kind, tuple(plain.filled_options))]


def describe_answering_with_a_write_in():
    def it_saves_what_the_member_typed_for_the_write_in():
        poll = _when_poll()
        qpk, options = _ids(poll)
        member = _member()

        poll.answer(
            member=member, answers={qpk: [options["Another time"]]}, write_ins={qpk: " Tuesdays "}, now=timezone.now()
        )

        saved = poll.answers_of(member)
        assert saved.answers == {qpk: [options["Another time"]]}
        assert saved.write_ins == {qpk: "Tuesdays"}

    def it_refuses_a_picked_write_in_with_no_text_or_too_much():
        poll = _when_poll()
        qpk, options = _ids(poll)
        with pytest.raises(AnswerRefusedError, match="Write your answer for Another time."):
            poll.answer(member=_member(), answers={qpk: [options["Another time"]]}, now=timezone.now())
        with pytest.raises(AnswerRefusedError, match="120 characters"):
            poll.answer(
                member=_member(),
                answers={qpk: [options["Another time"]]},
                write_ins={qpk: "x" * 121},
                now=timezone.now(),
            )

    def it_drops_text_typed_for_a_write_in_nobody_picked():
        poll = _when_poll()
        qpk, options = _ids(poll)
        member = _member()
        poll.answer(member=member, answers={qpk: [options["3rd"]]}, write_ins={qpk: "Stray"}, now=timezone.now())
        assert poll.answers_of(member).write_ins == {}

    def it_counts_the_write_in_and_lists_what_each_person_typed():
        poll = _when_poll()
        qpk, options = _ids(poll)
        ann, ben, cat = _member(name="Ann Zorblax"), _member(name="Ben Zorblax"), _member(name="Cat Zorblax")
        now = timezone.now()
        poll.answer(member=ann, answers={qpk: [options["Another time"]]}, write_ins={qpk: "Tuesdays"}, now=now)
        poll.answer(member=ben, answers={qpk: [options["3rd"]]}, now=now)
        poll.answer(member=cat, answers={qpk: [options["Another time"]]}, write_ins={qpk: "Weekends"}, now=now)

        results = poll.results()

        write_in = results.questions[0].options[-1]
        assert (write_in.text, write_in.votes, write_in.percent, write_in.is_write_in) == ("Another time", 2, 67, True)
        assert [(r.name, r.text) for r in write_in.written] == [
            ("Ann Zorblax", "Tuesdays"),
            ("Cat Zorblax", "Weekends"),
        ]
        assert results.questions[0].options[0].written == ()
        assert [row.cells for row in results.rows] == [
            ("Another time: Tuesdays",),
            ("3rd",),
            ("Another time: Weekends",),
        ]


def describe_the_answer_page_write_in():
    def it_opens_a_text_box_for_the_write_in_and_keeps_what_was_typed():
        poll = _when_poll()
        qpk, options = _ids(poll)
        member = _member()
        client = Client()
        client.force_login(member.user)

        html = client.get(poll.answer_path).content.decode()
        assert (
            f'value="{options["Another time"]}" class="pl-ask__input" x-ref="writeIn" data-email-poll-write-in>' in html
        )
        assert f'name="q{qpk}_other"' in html
        assert 'x-data="{ other: false }"' in html

        response = client.post(poll.answer_path, {f"q{qpk}": str(options["Another time"]), f"q{qpk}_other": "Tuesdays"})
        assert response.status_code == 302, response.content.decode()[-3000:]

        html = client.get(poll.answer_path).content.decode()
        assert 'value="Tuesdays"' in html
        assert 'x-data="{ other: true }"' in html
        assert f'data-email-poll-write-ins="{options["Another time"]}"' in html
        assert '<p class="pl-ask__reply-text">Tuesdays</p>' in html

    def it_shows_a_missing_write_in_text_as_a_message():
        poll = _when_poll()
        qpk, options = _ids(poll)
        member = _member()
        client = Client()
        client.force_login(member.user)
        response = client.post(poll.answer_path, {f"q{qpk}": str(options["Another time"])})
        assert response.status_code == 200
        assert "Write your answer for Another time." in response.content.decode()


def describe_the_composer_write_in():
    def _admin_client() -> Client:
        user = User.objects.create_superuser(username="zpw_admin", email="zpw_admin@x.com", password="p")
        client = Client()
        client.force_login(user)
        return client

    def _data(**question: str) -> dict:
        data = {
            "kind": "poll",
            "audience": "site",
            "discord_channel": "none",
            "mention": "none",
            "draft_pk": "",
            "body": "",
            "poll-title": "Council Zorblax",
            "poll-run_days": "7",
            "poll-remind_days_before": "1",
            "question-TOTAL_FORMS": "1",
            "question-INITIAL_FORMS": "0",
            "question-MIN_NUM_FORMS": "0",
            "question-MAX_NUM_FORMS": "10",
            "question-0-text": "Which Thursday?",
            "question-0-kind": "one",
        }
        data.update({f"question-0-{key}": value for key, value in question.items()})
        return data

    def it_saves_and_reopens_a_question_with_a_write_in():
        client = _admin_client()
        response = client.post(
            reverse("hub_compose_save_draft"),
            _data(option_1="3rd", option_2="2nd", option_3="4th", write_in="Another time"),
        )
        assert response.status_code == 200
        poll = EmailPoll.objects.get()
        assert [(o.text, o.is_write_in) for o in poll.questions.get().options.all()][-1] == ("Another time", True)

        html = client.get(reverse("hub_compose_resume", args=[poll.draft_id])).content.decode()
        assert "plPollQuestion('one', 3, true)" in html
        assert 'name="question-0-write_in" id="id_question-0-write_in" value="Another time"' in html

    def it_counts_the_write_in_toward_the_four_options():
        client = _admin_client()
        response = client.post(
            reverse("hub_compose_save_draft"),
            _data(option_1="A", option_2="B", option_3="C", option_4="D", write_in="Other"),
        )
        assert response.status_code == 204
        assert "a write-in counting as one" in response["HX-Trigger"]

    def it_drops_a_write_in_left_on_an_open_question():
        client = _admin_client()
        client.post(reverse("hub_compose_save_draft"), _data(kind="open", write_in="Other"))
        assert not EmailPollOption.objects.exists()


def describe_deploy_safety():
    def it_fills_is_write_in_for_an_insert_that_leaves_it_out():
        """The release still serving during a deploy (parts 1 to 4) inserts options without the column."""
        from django.db import connection

        poll = EmailPollFactory()
        poll.replace_questions([QuestionSpec("Q?", QuestionKind.PICK_ONE, ("A", "B"))])
        question = poll.questions.get()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO polls_emailpolloption (question_id, position, text) VALUES (%s, %s, %s)",
                [question.pk, 7, "Old release"],
            )
        assert EmailPollOption.objects.get(text="Old release").is_write_in is False
