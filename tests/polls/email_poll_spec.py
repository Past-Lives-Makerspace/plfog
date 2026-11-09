"""Email polls (#1142 part 1): the question rules, opening, recipients, the answer link, answering and results."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import User
from django.core import signing
from django.db import IntegrityError, connection, transaction
from django.db.models.signals import post_save
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from factory.django import mute_signals

from membership.models import AnnouncementDraft, Member
from polls.models import (
    BAD_LINK,
    DIFFERENT_GROUP,
    MAX_QUESTIONS,
    NO_ANSWERS,
    OPEN_ANSWER_MAX_LENGTH,
    POLL_CLOSED,
    AnswerRefusedError,
    BadPollLinkError,
    EmailPoll,
    EmailPollAnswer,
    EmailPollOption,
    EmailPollQuestion,
    EmailPollRecipient,
    EmailPollResponse,
    QuestionKind,
    QuestionSpec,
    ReminderLead,
    RunLength,
    validate_questions,
)
from tests.membership.factories import (
    AnnouncementDraftFactory,
    GuildFactory,
    GuildMembershipFactory,
    MemberFactory,
)
from tests.polls.factories import EmailPollFactory, sent_email_poll

pytestmark = pytest.mark.django_db

PORTLAND = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 9, 9, 0, tzinfo=PORTLAND)
SHOP = QuestionSpec("Which shop?", QuestionKind.PICK_ONE, ("Wood", "Metal", "Glass"))
TOOLS = QuestionSpec("Which tools?", QuestionKind.PICK_ANY, ("Lathe", "Laser", "Kiln"))
IDEAS = QuestionSpec("Any ideas?", QuestionKind.OPEN)

_seq = {"n": 0}


def _account(name: str = "", *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    """A member with a login account."""
    _seq["n"] += 1
    member = MemberFactory(full_legal_name=name or f"Zorblax Person {_seq['n']}", fog_role=fog_role)
    with mute_signals(post_save):
        user = User.objects.create_user(
            username=f"ep_{_seq['n']}", email=f"ep{_seq['n']}@example.com", last_login=timezone.now()
        )
    member.user = user
    member.save(update_fields=["user"])
    return member


def _question(poll: EmailPoll, position: int) -> EmailPollQuestion:
    return poll.questions.get(position=position)


def _option(poll: EmailPoll, position: int, text: str) -> int:
    return EmailPollOption.objects.get(question__poll=poll, question__position=position, text=text).pk


def _idea(poll: EmailPoll) -> dict[int, str]:
    """An answer to the poll's first question, written: enough for an all open-ended poll."""
    return {_question(poll, 0).pk: "Zorblax idea"}


def _survey(**kwargs: object) -> EmailPoll:
    """A poll open at NOW with a Pick one, a Pick any and an open-ended question."""
    return sent_email_poll(SHOP, TOOLS, IDEAS, now=NOW, **kwargs)


def describe_validate_questions():
    def it_takes_a_valid_list():
        validate_questions([SHOP, TOOLS, IDEAS])

    @pytest.mark.parametrize(
        ("specs", "message"),
        [
            ([], "Add at least one question."),
            ([IDEAS] * (MAX_QUESTIONS + 1), "at most 10 questions"),
            ([QuestionSpec("  ", QuestionKind.OPEN)], "Question 1 needs its text."),
            ([QuestionSpec("x" * 201, QuestionKind.OPEN)], "longer than 200 characters"),
            ([QuestionSpec("Why?", "essay")], "unknown kind"),
            ([QuestionSpec("Why?", QuestionKind.OPEN, ("A",))], "takes no options"),
            ([QuestionSpec("Pick", QuestionKind.PICK_ONE, ("A",))], "needs 2 to 4 filled options"),
            ([QuestionSpec("Pick", QuestionKind.PICK_ANY, ("A", "B", "C", "D", "E"))], "needs 2 to 4 filled options"),
            ([QuestionSpec("Pick", QuestionKind.PICK_ONE, ("A", " "))], "needs 2 to 4 filled options"),
            ([QuestionSpec("Pick", QuestionKind.PICK_ONE, ("A", "x" * 121))], "longer than 120 characters"),
        ],
    )
    def it_refuses_what_cannot_send(specs, message):
        with pytest.raises(ValueError, match=message):
            validate_questions(specs)

    def it_numbers_the_question_it_refuses():
        with pytest.raises(ValueError, match="Question 2 needs its text."):
            validate_questions([IDEAS, QuestionSpec("", QuestionKind.OPEN)])


def describe_replace_questions():
    def it_saves_questions_and_options_in_order():
        poll = EmailPollFactory()
        poll.replace_questions([SHOP, IDEAS])
        assert [(q.position, q.text, q.kind) for q in poll.questions.all()] == [
            (0, "Which shop?", QuestionKind.PICK_ONE),
            (1, "Any ideas?", QuestionKind.OPEN),
        ]
        assert [o.text for o in _question(poll, 0).options.all()] == ["Wood", "Metal", "Glass"]
        assert not _question(poll, 1).options.exists()

    def it_replaces_what_the_draft_had():
        poll = EmailPollFactory()
        poll.replace_questions([SHOP, IDEAS])
        poll.replace_questions([QuestionSpec("  Zorblax?  ", QuestionKind.PICK_ANY, (" Yes ", "No"))])
        assert [q.text for q in poll.questions.all()] == ["Zorblax?"]
        assert [o.text for o in _question(poll, 0).options.all()] == ["Yes", "No"]
        assert EmailPollOption.objects.count() == 2

    def it_refuses_a_sent_poll():
        poll = _survey()
        with pytest.raises(ValueError, match="cannot change"):
            poll.replace_questions([IDEAS])

    def it_saves_nothing_when_refused():
        poll = EmailPollFactory()
        with pytest.raises(ValueError):
            poll.replace_questions([])
        assert not poll.questions.exists()


def describe_open():
    def it_opens_now_and_closes_after_the_run():
        poll = EmailPollFactory(run_days=RunLength.THREE_DAYS)
        poll.replace_questions([SHOP])
        poll.open(NOW)
        poll.refresh_from_db()
        assert poll.opens_at == NOW
        assert poll.closes_at == NOW + timedelta(days=3)
        assert poll.remind_at is None
        assert poll.is_sent

    def it_sets_the_reminder_before_closing():
        poll = sent_email_poll(SHOP, now=NOW, run_days=RunLength.ONE_WEEK, remind_days_before=ReminderLead.TWO_DAYS)
        assert poll.remind_at == NOW + timedelta(days=5)

    def it_keeps_the_first_window_when_sent_again():
        poll = _survey()
        poll.open(NOW + timedelta(hours=2))
        poll.refresh_from_db()
        assert poll.opens_at == NOW

    def it_refuses_a_poll_with_no_questions():
        poll = EmailPollFactory()
        with pytest.raises(ValueError, match="Add at least one question."):
            poll.open(NOW)
        assert not poll.is_sent


def describe_window():
    def it_is_closed_as_a_draft():
        poll = EmailPollFactory()
        assert not poll.is_sent
        assert not poll.is_open(NOW)

    def it_is_open_until_its_closing_time():
        poll = _survey()
        assert poll.is_open(NOW)
        assert poll.is_open(poll.closes_at - timedelta(seconds=1))
        assert not poll.is_open(poll.closes_at)
        assert not poll.is_open(NOW - timedelta(seconds=1))

    def it_names_its_closing_time_in_portland():
        poll = _survey()
        assert poll.closes_label == "Mon, Nov 16 at 9:00 AM"

    def it_has_no_closing_label_as_a_draft():
        with pytest.raises(ValueError, match="no closing time"):
            EmailPollFactory().closes_label


def describe_constraints():
    def it_refuses_a_reminder_that_lands_before_the_poll_opens():
        with pytest.raises(IntegrityError), transaction.atomic():
            EmailPollFactory(run_days=RunLength.ONE_DAY, remind_days_before=ReminderLead.ONE_DAY)

    def it_refuses_a_window_that_closes_before_it_opens():
        poll = EmailPollFactory()
        with pytest.raises(IntegrityError), transaction.atomic():
            EmailPoll.objects.filter(pk=poll.pk).update(opens_at=NOW, closes_at=NOW)

    def it_refuses_an_answer_with_both_an_option_and_text():
        poll = _survey()
        response = EmailPollResponse.objects.create(poll=poll, member=_account())
        with pytest.raises(IntegrityError), transaction.atomic():
            EmailPollAnswer.objects.create(
                response=response, question=_question(poll, 0), option_id=_option(poll, 0, "Wood"), text="also"
            )

    def it_refuses_two_written_answers_to_one_question():
        poll = _survey()
        response = EmailPollResponse.objects.create(poll=poll, member=_account())
        EmailPollAnswer.objects.create(response=response, question=_question(poll, 2), text="One")
        with pytest.raises(IntegrityError), transaction.atomic():
            EmailPollAnswer.objects.create(response=response, question=_question(poll, 2), text="Two")


def describe_recipients():
    def it_records_the_member_of_each_account_once():
        poll = _survey()
        alice, bob = _account(), _account()
        with mute_signals(post_save):
            loner = User.objects.create_user(username="ep_loner", email="loner@example.com")
        poll.record_recipients({alice.user_id, bob.user_id, loner.pk})
        poll.record_recipients({alice.user_id})
        assert set(poll.recipients.values_list("member_id", flat=True)) == {alice.pk, bob.pk}
        assert poll.is_recipient(alice)
        assert not poll.is_recipient(_account())


def describe_answer_link():
    def it_names_the_poll_and_the_member():
        poll = _survey()
        alice = _account()
        poll.record_recipients({alice.user_id})
        assert EmailPoll.read_answer_token(poll.make_answer_token(alice), NOW) == (poll, alice)

    def it_refuses_a_tampered_token():
        poll = _survey()
        alice = _account()
        poll.record_recipients({alice.user_id})
        token = poll.make_answer_token(alice)
        with pytest.raises(BadPollLinkError, match=BAD_LINK):
            EmailPoll.read_answer_token(token[:-2] + "xx", NOW)

    def it_refuses_a_token_signed_for_something_else():
        poll = _survey()
        alice = _account()
        poll.record_recipients({alice.user_id})
        forged = signing.dumps({"poll": poll.pk, "member": alice.pk}, salt="orientation-bill-checkout")
        with pytest.raises(BadPollLinkError, match=BAD_LINK):
            EmailPoll.read_answer_token(forged, NOW)

    def it_refuses_a_member_the_poll_was_not_sent_to():
        poll = _survey()
        with pytest.raises(BadPollLinkError, match=BAD_LINK):
            EmailPoll.read_answer_token(poll.make_answer_token(_account()), NOW)

    def it_stops_working_when_the_poll_closes():
        poll = _survey()
        alice = _account()
        poll.record_recipients({alice.user_id})
        with pytest.raises(BadPollLinkError, match=POLL_CLOSED):
            EmailPoll.read_answer_token(poll.make_answer_token(alice), poll.closes_at)


def describe_answer():
    def it_saves_every_kind_of_answer():
        poll = _survey()
        alice = _account()
        wood, lathe, kiln = _option(poll, 0, "Wood"), _option(poll, 1, "Lathe"), _option(poll, 1, "Kiln")
        response = poll.answer(
            member=alice,
            answers={
                _question(poll, 0).pk: [wood],
                _question(poll, 1).pk: [lathe, kiln, lathe],
                _question(poll, 2).pk: " More kilns ",
            },
            now=NOW,
        )
        assert sorted(response.answers.exclude(option=None).values_list("option_id", flat=True)) == sorted(
            [wood, lathe, kiln]
        )
        assert response.answers.get(option=None).text == "More kilns"

    def it_stores_nothing_for_a_blank_or_skipped_open_question():
        poll = _survey()
        answers = {
            _question(poll, 0).pk: [_option(poll, 0, "Wood")],
            _question(poll, 1).pk: [_option(poll, 1, "Laser")],
        }
        response = poll.answer(member=_account(), answers={**answers, _question(poll, 2).pk: "   "}, now=NOW)
        assert not response.answers.filter(option=None).exists()
        assert poll.answer(member=_account(), answers=answers, now=NOW).answers.count() == 2

    def it_replaces_earlier_answers_in_the_same_response():
        poll = _survey()
        alice = _account()
        first = poll.answer(
            member=alice,
            answers={
                _question(poll, 0).pk: [_option(poll, 0, "Wood")],
                _question(poll, 1).pk: [_option(poll, 1, "Lathe")],
                _question(poll, 2).pk: "Old",
            },
            now=NOW,
        )
        second = poll.answer(
            member=alice,
            answers={
                _question(poll, 0).pk: [_option(poll, 0, "Metal")],
                _question(poll, 1).pk: [_option(poll, 1, "Kiln")],
            },
            now=NOW + timedelta(hours=1),
        )
        assert second.pk == first.pk
        assert EmailPollResponse.objects.count() == 1
        assert sorted(second.answers.values_list("option__text", flat=True)) == ["Kiln", "Metal"]

    def it_refuses_a_closed_poll():
        poll = _survey()
        with pytest.raises(AnswerRefusedError, match=POLL_CLOSED):
            poll.answer(member=_account(), answers={}, now=poll.closes_at)

    def it_refuses_a_draft():
        poll = EmailPollFactory()
        poll.replace_questions([IDEAS])
        with pytest.raises(AnswerRefusedError, match=POLL_CLOSED):
            poll.answer(member=_account(), answers={}, now=NOW)

    def it_lets_only_recipients_answer_an_audience_only_poll():
        poll = sent_email_poll(IDEAS, now=NOW, audience_only=True)
        alice, outsider = _account(), _account()
        poll.record_recipients({alice.user_id})
        assert poll.answer_refusal(outsider, NOW) == DIFFERENT_GROUP
        with pytest.raises(AnswerRefusedError, match=DIFFERENT_GROUP):
            poll.answer(member=outsider, answers=_idea(poll), now=NOW)
        assert poll.answer_refusal(alice, NOW) == ""
        poll.answer(member=alice, answers=_idea(poll), now=NOW)

    def it_lets_anyone_answer_a_poll_open_to_all():
        poll = sent_email_poll(IDEAS, now=NOW)
        assert poll.answer_refusal(_account(), NOW) == ""

    def it_refuses_an_unanswered_pick_question():
        poll = _survey()
        with pytest.raises(AnswerRefusedError, match="Answer this question: Which shop?"):
            poll.answer(member=_account(), answers={_question(poll, 1).pk: [_option(poll, 1, "Lathe")]}, now=NOW)

    def it_refuses_two_picks_on_a_pick_one_question():
        poll = sent_email_poll(SHOP, now=NOW)
        picks = [_option(poll, 0, "Wood"), _option(poll, 0, "Metal")]
        with pytest.raises(AnswerRefusedError, match="Pick one answer for"):
            poll.answer(member=_account(), answers={_question(poll, 0).pk: picks}, now=NOW)

    def it_refuses_an_option_from_another_question():
        poll = _survey()
        with pytest.raises(AnswerRefusedError, match="not part of this poll"):
            poll.answer(member=_account(), answers={_question(poll, 0).pk: [_option(poll, 1, "Laser")]}, now=NOW)

    def it_refuses_text_for_a_pick_question():
        poll = sent_email_poll(SHOP, now=NOW)
        with pytest.raises(AnswerRefusedError, match="Pick from the choices"):
            poll.answer(member=_account(), answers={_question(poll, 0).pk: "Wood"}, now=NOW)

    def it_refuses_picks_for_an_open_question():
        poll = sent_email_poll(IDEAS, now=NOW)
        with pytest.raises(AnswerRefusedError, match="Write your answer to"):
            poll.answer(member=_account(), answers={_question(poll, 0).pk: [1]}, now=NOW)

    def it_refuses_a_written_answer_that_is_too_long():
        poll = sent_email_poll(IDEAS, now=NOW)
        with pytest.raises(AnswerRefusedError, match="2000 characters"):
            poll.answer(member=_account(), answers={_question(poll, 0).pk: "x" * (OPEN_ANSWER_MAX_LENGTH + 1)}, now=NOW)

    def it_keeps_earlier_answers_when_an_update_is_refused():
        poll = sent_email_poll(SHOP, now=NOW)
        alice = _account()
        poll.answer(member=alice, answers={_question(poll, 0).pk: [_option(poll, 0, "Wood")]}, now=NOW)
        with pytest.raises(AnswerRefusedError):
            poll.answer(member=alice, answers={}, now=NOW)
        assert list(EmailPollAnswer.objects.values_list("option__text", flat=True)) == ["Wood"]


def describe_may_see_results():
    def it_shows_results_to_the_sender_an_admin_and_whoever_answered():
        sender = _account()
        poll = sent_email_poll(
            IDEAS, now=NOW, draft=AnnouncementDraftFactory(kind=AnnouncementDraft.Kind.POLL, author=sender.user)
        )
        answered = _account()
        poll.answer(member=answered, answers=_idea(poll), now=NOW)
        assert poll.may_see_results(sender)
        assert poll.may_see_results(_account(fog_role=Member.FogRole.ADMIN))
        assert poll.may_see_results(answered)

    def it_hides_results_from_everyone_else():
        poll = sent_email_poll(IDEAS, now=NOW)
        assert not poll.may_see_results(None)
        assert not poll.may_see_results(_account())
        assert not poll.may_see_results(MemberFactory())


def describe_results():
    def it_counts_picks_lists_written_answers_and_rows_each_person():
        poll = _survey()
        ann, ben, cat = _account("Ann Zorblax"), _account("Ben Zorblax"), _account("Cat Zorblax")
        q0, q1, q2 = (_question(poll, n).pk for n in range(3))
        wood, metal = _option(poll, 0, "Wood"), _option(poll, 0, "Metal")
        lathe, laser = _option(poll, 1, "Lathe"), _option(poll, 1, "Laser")
        poll.answer(member=ann, answers={q0: [wood], q1: [lathe, laser], q2: "Night hours"}, now=NOW)
        poll.answer(member=ben, answers={q0: [wood], q1: [laser]}, now=NOW)
        poll.answer(member=cat, answers={q0: [metal], q1: [laser], q2: "A bigger kiln"}, now=NOW)

        results = poll.results()

        assert results.response_count == 3
        shop, tools, ideas = results.questions
        assert [(o.text, o.votes, o.percent) for o in shop.options] == [
            ("Wood", 2, 67),
            ("Metal", 1, 33),
            ("Glass", 0, 0),
        ]
        assert [(o.text, o.votes, o.percent) for o in tools.options] == [
            ("Lathe", 1, 33),
            ("Laser", 3, 100),
            ("Kiln", 0, 0),
        ]
        assert ideas.options == ()
        assert [(w.name, w.text) for w in ideas.written] == [
            ("Ann Zorblax", "Night hours"),
            ("Cat Zorblax", "A bigger kiln"),
        ]
        assert shop.written == ()
        assert [(row.name, row.cells) for row in results.rows] == [
            ("Ann Zorblax", ("Wood", "Lathe, Laser", "Night hours")),
            ("Ben Zorblax", ("Wood", "Laser", "")),
            ("Cat Zorblax", ("Metal", "Laser", "A bigger kiln")),
        ]

    def it_shows_zero_percent_with_no_answers():
        results = _survey().results()
        assert results.response_count == 0
        assert {o.percent for q in results.questions for o in q.options} == {0}

    def it_loads_in_four_queries_however_many_answered():
        poll = _survey()
        q0, q1 = _question(poll, 0).pk, _question(poll, 1).pk
        for _ in range(5):
            poll.answer(
                member=_account(), answers={q0: [_option(poll, 0, "Glass")], q1: [_option(poll, 1, "Kiln")]}, now=NOW
            )
        with CaptureQueriesContext(connection) as queries:
            poll.results()
        assert len(queries) == 4


def describe_queries():
    def it_finds_sent_open_and_received_polls():
        draft = EmailPollFactory()
        open_poll = _survey()
        closed = sent_email_poll(IDEAS, now=NOW - timedelta(days=30), run_days=RunLength.ONE_DAY)
        alice = _account()
        open_poll.record_recipients({alice.user_id})
        assert set(EmailPoll.objects.sent()) == {open_poll, closed}
        assert list(EmailPoll.objects.open_at(NOW)) == [open_poll]
        assert list(EmailPoll.objects.sent_to(alice)) == [open_poll]
        assert draft not in EmailPoll.objects.sent()

    def it_finds_reminders_that_are_due_and_not_sent():
        due = sent_email_poll(IDEAS, now=NOW - timedelta(days=6), remind_days_before=ReminderLead.ONE_DAY)
        sent = sent_email_poll(IDEAS, now=NOW - timedelta(days=6), remind_days_before=ReminderLead.ONE_DAY)
        EmailPoll.objects.filter(pk=sent.pk).update(reminder_sent_at=NOW)
        sent_email_poll(IDEAS, now=NOW, remind_days_before=ReminderLead.ONE_DAY)
        sent_email_poll(IDEAS, now=NOW - timedelta(days=6))
        assert list(EmailPoll.objects.reminders_due(NOW)) == [due]


def describe_rows():
    def it_names_each_row_readably():
        poll = _survey()
        alice = _account()
        poll.record_recipients({alice.user_id})
        response = poll.answer(
            member=alice,
            answers={
                _question(poll, 0).pk: [_option(poll, 0, "Wood")],
                _question(poll, 1).pk: [_option(poll, 1, "Kiln")],
            },
            now=NOW,
        )
        answer = response.answers.first()
        assert str(poll) == poll.draft.title
        assert str(_question(poll, 0)) == "Which shop?"
        assert str(EmailPollOption.objects.get(pk=_option(poll, 0, "Wood"))) == "Wood"
        assert str(EmailPollRecipient.objects.get()) == f"Poll {poll.pk} sent to member {alice.pk}"
        assert str(response) == f"Member {alice.pk} answered poll {poll.pk}"
        assert str(answer) == f"Answer to question {answer.question_id}"

    def it_marks_a_new_composer_row_as_an_email():
        assert AnnouncementDraftFactory().kind == AnnouncementDraft.Kind.EMAIL

    def it_goes_when_its_draft_is_deleted():
        poll = EmailPollFactory()
        poll.replace_questions([SHOP])
        poll.draft.delete()
        assert not EmailPoll.objects.exists()
        assert not EmailPollQuestion.objects.exists()


def describe_review_fixes():
    """Fix round 1 on #1143: each of the second reviewer's four defects."""

    def describe_a_submission_must_answer_something():
        def it_refuses_an_all_blank_submission_and_counts_nobody():
            poll = sent_email_poll(IDEAS, QuestionSpec("Anything else?", QuestionKind.OPEN), now=NOW)
            member = _account()
            blanks = {_question(poll, 0).pk: "  ", _question(poll, 1).pk: ""}
            for answers in (blanks, {}):
                with pytest.raises(AnswerRefusedError, match=NO_ANSWERS):
                    poll.answer(member=member, answers=answers, now=NOW)
            assert not EmailPollResponse.objects.exists()
            assert not poll.may_see_results(member)
            assert poll.results().response_count == 0

        def it_keeps_earlier_answers_when_an_update_blanks_everything():
            poll = sent_email_poll(IDEAS, now=NOW)
            member = _account()
            poll.answer(member=member, answers=_idea(poll), now=NOW)
            with pytest.raises(AnswerRefusedError, match=NO_ANSWERS):
                poll.answer(member=member, answers={_question(poll, 0).pk: ""}, now=NOW)
            assert list(EmailPollAnswer.objects.values_list("text", flat=True)) == ["Zorblax idea"]

    def describe_recipients_are_recorded_when_it_opens():
        def it_lets_its_recipients_answer_an_audience_only_poll_before_any_email_goes_out():
            guild_member, outsider = _account(), _account()
            guild = GuildFactory()
            GuildMembershipFactory(guild=guild, member=guild_member)
            draft = AnnouncementDraftFactory(
                kind=AnnouncementDraft.Kind.POLL, audience=AnnouncementDraft.Audience.GUILD, guild=guild
            )
            poll = sent_email_poll(IDEAS, now=NOW, draft=draft, audience_only=True)
            assert list(poll.recipients.values_list("member_id", flat=True)) == [guild_member.pk]
            assert poll.answer_refusal(guild_member, NOW) == ""
            assert poll.answer_refusal(outsider, NOW) == DIFFERENT_GROUP
            assert list(EmailPoll.objects.sent_to(guild_member)) == [poll]

        def it_records_a_site_poll_for_everyone_signed_in():
            alice, bob = _account(), _account()
            poll = sent_email_poll(IDEAS, now=NOW)
            assert set(poll.recipients.values_list("member_id", flat=True)) == {alice.pk, bob.pk}

        def it_records_nobody_new_when_sent_again():
            poll = sent_email_poll(IDEAS, now=NOW)
            latecomer = _account()
            poll.open(NOW + timedelta(minutes=10))
            assert not poll.is_recipient(latecomer)

    def describe_blank_option_rows_are_dropped():
        def it_counts_filled_options_and_stores_only_them():
            spec = QuestionSpec("Which shop?", QuestionKind.PICK_ONE, ("Wood", "   ", " Metal ", ""))
            validate_questions([spec])
            poll = EmailPollFactory()
            poll.replace_questions([spec])
            assert [o.text for o in _question(poll, 0).options.all()] == ["Wood", "Metal"]

        def it_takes_an_open_question_with_only_blank_option_rows():
            poll = EmailPollFactory()
            poll.replace_questions([QuestionSpec("Ideas?", QuestionKind.OPEN, ("", " "))])
            assert not EmailPollOption.objects.exists()

        def it_still_refuses_one_filled_option_among_blanks():
            with pytest.raises(ValueError, match="needs 2 to 4 filled options"):
                validate_questions([QuestionSpec("Pick", QuestionKind.PICK_ANY, ("A", "", "  "))])

    def describe_picks_arrive_in_any_posted_shape():
        @pytest.mark.parametrize("shape", ["int", "str", "list_of_str", "tuple", "padded"])
        def it_accepts_a_pick_as_an_int_a_digit_string_or_a_list(shape):
            poll = sent_email_poll(SHOP, now=NOW)
            wood = _option(poll, 0, "Wood")
            given = {
                "int": wood,
                "str": str(wood),
                "list_of_str": [str(wood)],
                "tuple": (wood,),
                "padded": f" {wood} ",
            }[shape]
            response = poll.answer(member=_account(), answers={_question(poll, 0).pk: given}, now=NOW)
            assert list(response.answers.values_list("option_id", flat=True)) == [wood]

        def it_accepts_pick_any_as_mixed_ints_and_strings_once_each():
            poll = sent_email_poll(TOOLS, now=NOW)
            lathe, kiln = _option(poll, 0, "Lathe"), _option(poll, 0, "Kiln")
            response = poll.answer(
                member=_account(), answers={_question(poll, 0).pk: [lathe, str(kiln), str(lathe)]}, now=NOW
            )
            assert sorted(response.answers.values_list("option_id", flat=True)) == sorted([lathe, kiln])

        @pytest.mark.parametrize("given", ["Wood", "", True, 2.0, [[1]], ["1x"], ["٣"], {"pk": 1}])
        def it_refuses_anything_else_as_a_message(given):
            poll = sent_email_poll(SHOP, now=NOW)
            with pytest.raises(AnswerRefusedError, match="Pick from the choices for: Which shop?"):
                poll.answer(member=_account(), answers={_question(poll, 0).pk: given}, now=NOW)

        def it_refuses_a_bare_int_that_is_not_an_option():
            poll = sent_email_poll(SHOP, now=NOW)
            with pytest.raises(AnswerRefusedError, match="not part of this poll"):
                poll.answer(member=_account(), answers={_question(poll, 0).pk: 999999}, now=NOW)
