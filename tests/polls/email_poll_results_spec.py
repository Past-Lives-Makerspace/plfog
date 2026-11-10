"""Email polls part 5b (#1142 AC14, AC15): Public results, and the results email draft made when a poll closes."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import CommandError, call_command
from django.db import IntegrityError, connection, transaction
from django.db.models.signals import post_save
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from factory.django import mute_signals

from core.scheduled_jobs import SCHEDULED_JOBS
from membership.models import AnnouncementDraft, Member
from polls.models import EmailPoll, QuestionKind, QuestionSpec, ResultsLevel, RunLength, draft_due_poll_results
from tests.membership.factories import (
    AnnouncementDraftFactory,
    GuildFactory,
    GuildMailingListEmailFactory,
    GuildMembershipFactory,
    MemberFactory,
)
from tests.polls.factories import sent_email_poll

pytestmark = pytest.mark.django_db

WHEN = QuestionSpec("Which Thursday?", QuestionKind.PICK_ONE, ("3rd", "2nd"), write_in="Another time")
FEEDBACK = QuestionSpec("Feedback on Council Meetings?", QuestionKind.OPEN)
_seq = {"n": 0}


def _member(guild=None, *, name: str = "", fog_role: str = Member.FogRole.MEMBER) -> Member:
    _seq["n"] += 1
    member = MemberFactory(full_legal_name=name or f"Zorblax Person {_seq['n']}", fog_role=fog_role)
    with mute_signals(post_save):
        user = User.objects.create_user(
            username=f"zpr_{_seq['n']}", email=f"zpr{_seq['n']}@example.com", last_login=timezone.now()
        )
    member.user = user
    member.save(update_fields=["user"])
    if guild is not None:
        GuildMembershipFactory(guild=guild, member=member)
    return member


def _council_poll(
    guild=None, *, opened=None, author=None, audience=AnnouncementDraft.Audience.GUILD, **kwargs
) -> EmailPoll:
    guild = guild if guild is not None or audience != AnnouncementDraft.Audience.GUILD else GuildFactory()
    draft = AnnouncementDraftFactory(
        kind=AnnouncementDraft.Kind.POLL,
        audience=audience,
        guild=guild,
        title="Council Zorblax",
        author=author if author is not None else _member().user,
    )
    return sent_email_poll(WHEN, FEEDBACK, now=opened or timezone.now(), draft=draft, **kwargs)


def _answer(poll: EmailPoll, member: Member, *, pick: str, other: str = "", feedback: str = "") -> None:
    when, open_q = poll.questions.order_by("position")
    option = when.options.get(text=pick)
    poll.answer(
        member=member,
        answers={when.pk: [option.pk], open_q.pk: feedback},
        write_ins={when.pk: other},
        now=timezone.now(),
    )


def _close(poll: EmailPoll) -> None:
    """Move the poll's window into the past, as if it had run its day and closed a minute ago."""
    now = timezone.now()
    EmailPoll.objects.filter(pk=poll.pk).update(
        opens_at=now - timedelta(days=1, minutes=1), closes_at=now - timedelta(minutes=1)
    )


def _client(member: Member) -> Client:
    client = Client()
    client.force_login(member.user)
    return client


def describe_public_results():
    def it_shows_any_member_the_totals_only():
        guild = GuildFactory()
        ann = _member(guild, name="Ann Zorblax")
        poll = _council_poll(guild, public_results=True, audience_only=True)
        _answer(poll, ann, pick="Another time", other="Tuesdays", feedback="Shorter please")
        outsider = _member()

        html = _client(outsider).get(poll.answer_path).content.decode()

        assert "data-email-poll-results" in html
        assert "data-email-poll-totals-only" in html
        assert 'class="pl-poll-results__bar" max="100" value="100"' in html
        assert "Ann Zorblax" not in html
        assert "Tuesdays" not in html and "Shorter please" not in html
        assert "data-email-poll-table" not in html
        assert "Feedback on Council Meetings?" not in html.split("data-email-poll-results")[1]
        assert "different group" not in html
        assert "data-email-poll-form" not in html

    def it_keeps_names_and_written_answers_from_an_answerer_while_public():
        poll = _council_poll(public_results=True)
        ann, ben = _member(name="Ann Zorblax"), _member(name="Ben Zorblax")
        _answer(poll, ann, pick="Another time", other="Tuesdays", feedback="Shorter please")
        _answer(poll, ben, pick="3rd")

        html = _client(ben).get(poll.answer_path).content.decode()

        assert "data-email-poll-totals-only" in html
        assert "Ann Zorblax" not in html and "Tuesdays" not in html and "Shorter please" not in html

    def it_shows_the_sender_and_an_admin_everything_while_public():
        sender = _member()
        poll = _council_poll(public_results=True, author=sender.user)
        _answer(poll, _member(name="Ann Zorblax"), pick="Another time", other="Tuesdays", feedback="Shorter please")
        admin = _member(fog_role=Member.FogRole.ADMIN)

        for viewer in (sender, admin):
            html = _client(viewer).get(poll.answer_path).content.decode()
            assert "data-email-poll-table" in html
            assert "Tuesdays" in html and "Shorter please" in html and "Ann Zorblax" in html

    def it_gives_the_token_route_totals_only_while_public():
        guild = GuildFactory()
        recipient = _member(guild)
        poll = _council_poll(guild, public_results=True)
        _answer(poll, _member(name="Ann Zorblax"), pick="Another time", other="Tuesdays")
        link = reverse("polls:email_poll_link", args=[poll.make_answer_token(recipient)])

        html = Client().get(link).content.decode()

        assert "data-email-poll-totals-only" in html
        assert "Tuesdays" not in html and "Ann Zorblax" not in html

    def it_keeps_todays_rule_while_off():
        poll = _council_poll()
        answered = _member()
        _answer(poll, _member(name="Ann Zorblax"), pick="Another time", other="Tuesdays")
        _answer(poll, answered, pick="3rd")
        assert poll.results_level(_member()) == ResultsLevel.NONE
        assert poll.results_level(answered) == ResultsLevel.FULL
        html = _client(answered).get(poll.answer_path).content.decode()
        assert "Tuesdays" in html and "data-email-poll-table" in html

    def it_reads_no_member_or_text_for_the_totals():
        poll = _council_poll(public_results=True)
        _answer(poll, _member(name="Ann Zorblax"), pick="Another time", other="Tuesdays", feedback="Shorter please")
        with CaptureQueriesContext(connection) as queries:
            results = poll.results(full=False)
        sql = " ".join(query["sql"] for query in queries.captured_queries)
        assert "membership_member" not in sql
        assert '"text"' not in sql.split("polls_emailpollanswer")[-1].split("FROM")[0]
        assert [(o.text, o.votes) for o in results.questions[0].options] == [
            ("3rd", 0),
            ("2nd", 0),
            ("Another time", 1),
        ]
        assert results.rows == () and results.response_count == 1
        assert len(queries) == 4

    def it_saves_the_switch_from_the_composer():
        user = User.objects.create_superuser(username="zpr_admin", email="zpr_admin@x.com", password="p")
        client = Client()
        client.force_login(user)
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
            "poll-public_results": "on",
            "question-TOTAL_FORMS": "1",
            "question-INITIAL_FORMS": "0",
            "question-MIN_NUM_FORMS": "0",
            "question-MAX_NUM_FORMS": "10",
            "question-0-text": "Which Thursday?",
            "question-0-kind": "one",
            "question-0-option_1": "3rd",
            "question-0-option_2": "2nd",
        }
        client.post(reverse("hub_compose_save_draft"), data)
        poll = EmailPoll.objects.get()
        assert poll.public_results is True
        html = client.get(reverse("hub_compose_resume", args=[poll.draft_id])).content.decode()
        assert 'name="poll-public_results"' in html and "checked" in html.split('name="poll-public_results"')[1][:40]


def describe_the_results_draft():
    def _closed_guild_poll():
        guild = GuildFactory()
        ann, ben = _member(guild, name="Ann Zorblax"), _member(guild, name="Ben Zorblax")
        GuildMailingListEmailFactory(guild=guild, email="list-only-zorblax@example.com")
        sender = _member()
        poll = _council_poll(guild, author=sender.user, run_days=RunLength.ONE_DAY)
        _answer(poll, ann, pick="Another time", other="Tuesdays", feedback="Shorter please")
        _answer(poll, ben, pick="3rd")
        _close(poll)
        return poll, sender, ann, ben

    def it_makes_one_draft_with_the_polls_audience_and_a_blank_note():
        poll, sender, ann, ben = _closed_guild_poll()

        run = draft_due_poll_results(timezone.now())
        again = draft_due_poll_results(timezone.now())

        assert (run.drafted, run.failed, again.drafted) == (1, 0, 0)
        draft = AnnouncementDraft.objects.get(results_of_poll=poll)
        assert draft.kind == AnnouncementDraft.Kind.EMAIL
        assert draft.title == "Results: Council Zorblax"
        assert (draft.audience, draft.guild_id, draft.author_id, draft.body) == (
            poll.draft.audience,
            poll.draft.guild_id,
            sender.user_id,
            "",
        )
        assert set(draft.recipient_selection["users"]) == {ann.user_id, ben.user_id}
        assert draft.recipient_selection["custom"] == []
        assert draft.is_resumable
        with pytest.raises(IntegrityError), transaction.atomic():
            AnnouncementDraftFactory(results_of_poll=poll)

    def it_leaves_an_open_poll_alone_and_retries_a_failed_one():
        _council_poll()
        poll, *_ = _closed_guild_poll()
        with mock.patch.object(EmailPoll, "create_results_draft", side_effect=RuntimeError("db down")):
            run = draft_due_poll_results(timezone.now())
        assert (run.drafted, run.failed) == (0, 1)
        poll.refresh_from_db()
        assert poll.results_drafted_at is None
        assert draft_due_poll_results(timezone.now()).drafted == 1

    def it_puts_totals_only_bars_under_the_senders_note():
        poll, *_ = _closed_guild_poll()
        draft_due_poll_results(timezone.now())
        draft = AnnouncementDraft.objects.get(results_of_poll=poll)
        draft.body = "<p>Thanks, Zorblax council.</p>"

        message = draft.build_email_message("https://example.com/")

        html = message.html_body
        assert "Thanks, Zorblax council." in html
        assert "Poll results: 2 people answered" in html
        assert "Which Thursday?" in html and "Another time" in html
        assert 'width="50%"' in html
        assert "Tuesdays" not in html and "Shorter please" not in html and "Ann Zorblax" not in html
        assert "Feedback on Council Meetings?" not in html
        assert "  Another time: 1 (50%)" in message.body
        assert html.index("Thanks, Zorblax council.") < html.index("Poll results:")

    def it_sends_to_the_people_the_poll_reached_and_no_list_address():
        poll, _sender, ann, ben = _closed_guild_poll()
        draft_due_poll_results(timezone.now())
        draft = AnnouncementDraft.objects.get(results_of_poll=poll)
        draft.body = "<p>Thanks.</p>"
        draft.save(update_fields=["body"])

        draft.send_in_request()

        assert sorted(m.to[0] for m in mail.outbox) == sorted([ann.user.email, ben.user.email])

    def it_reaches_the_admins_of_a_guild_leads_poll():
        lead = _member()
        GuildFactory(guild_lead=lead)
        admin = _member(fog_role=Member.FogRole.ADMIN)
        poll = _council_poll(audience=AnnouncementDraft.Audience.LEADS, run_days=RunLength.ONE_DAY)
        _close(poll)
        draft_due_poll_results(timezone.now())
        draft = AnnouncementDraft.objects.get(results_of_poll=poll)
        draft.body = "<p>Thanks.</p>"
        draft.save(update_fields=["body"])

        assert draft.recipient_count() == 2
        draft.send_in_request()

        assert sorted(m.to[0] for m in mail.outbox) == sorted([lead.user.email, admin.user.email])
        assert draft._leads_recipient_ids() == {lead.user_id}

    def it_shows_the_bars_read_only_in_the_composer():
        poll, sender, *_ = _closed_guild_poll()
        draft_due_poll_results(timezone.now())
        draft = AnnouncementDraft.objects.get(results_of_poll=poll)
        admin = User.objects.create_superuser(username="zpr_admin2", email="zpr_admin2@x.com", password="p")
        client = Client()
        client.force_login(admin)

        html = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()

        block = html.split("data-compose-poll-results>")[1].split("</div>\n      </div>")[0]
        assert "Poll results: 2 people answered" in block and 'width="50%"' in block
        assert "Tuesdays" not in block
        assert 'value="Results: Council Zorblax"' in html

    def it_runs_as_a_15_minute_job_that_fails_loudly():
        job = next(job for job in SCHEDULED_JOBS if job.key == "draft_email_poll_results")
        assert (job.command, job.schedule_label) == ("draft_email_poll_results", "Every 15 min")
        _closed_guild_poll()
        out = StringIO()
        call_command("draft_email_poll_results", stdout=out)
        assert "Made 1 poll results draft(s); skipped 0 with no pick question." in out.getvalue()
        _closed_guild_poll()
        with mock.patch.object(EmailPoll, "create_results_draft", side_effect=RuntimeError("db down")):
            with pytest.raises(CommandError, match="1 poll results draft"):
                call_command("draft_email_poll_results", stdout=StringIO())

    def it_makes_an_empty_block_for_a_poll_with_no_pick_question():
        draft = AnnouncementDraftFactory(kind=AnnouncementDraft.Kind.POLL, title="Open Zorblax")
        poll = sent_email_poll(FEEDBACK, now=timezone.now(), draft=draft)
        assert str(poll.results_email_html()) == ""

    def it_makes_no_draft_for_a_poll_with_only_open_questions_and_marks_it_handled():
        draft = AnnouncementDraftFactory(kind=AnnouncementDraft.Kind.POLL, title="Open Zorblax")
        poll = sent_email_poll(FEEDBACK, now=timezone.now(), draft=draft)
        _close(poll)

        run = draft_due_poll_results(timezone.now())
        again = draft_due_poll_results(timezone.now())

        assert (run.drafted, run.skipped, run.failed) == (0, 1, 0)
        assert (again.drafted, again.skipped) == (0, 0)
        assert not AnnouncementDraft.objects.filter(results_of_poll=poll).exists()
        poll.refresh_from_db()
        assert poll.results_drafted_at is not None


def describe_public_results_deploy_safety():
    def it_fills_public_results_for_an_insert_that_leaves_it_out():
        """The release still serving during a deploy (parts 1 to 4) inserts polls without the column."""
        from django.db import connection

        draft = AnnouncementDraftFactory(kind=AnnouncementDraft.Kind.POLL)
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO polls_emailpoll (draft_id, intro, run_days, audience_only, created_at)"
                " VALUES (%s, '', 7, %s, %s)",
                [draft.pk, False, timezone.now()],
            )
        assert EmailPoll.objects.get(draft=draft).public_results is False


def describe_leads_find_polls():
    """AC16, AC17 (#1142): New Poll on Guild Settings, New poll on Email Members, and the guild guidance."""

    def it_offers_new_poll_beside_new_email_on_guild_settings():
        guild = GuildFactory()
        lead = _member()
        guild.guild_lead = lead
        guild.save(update_fields=["guild_lead"])

        html = _client(lead).get(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=announcements").content.decode()

        card = html.split("data-guild-email-card>")[1].split("data-guild-announcements-overview")[0]
        compose = reverse("hub_compose")
        pair = card.split('<div class="pl-btn-pair">')[1].split("</div>")[0]
        assert (
            f'href="{compose}?audience=guild:{guild.pk}" class="pl-btn pl-btn--primary" data-guild-new-email>New Email</a>'
            in pair
        )
        assert (
            f'href="{compose}?audience=guild:{guild.pk}&amp;kind=poll" class="pl-btn pl-btn--secondary" '
            'data-guild-send-poll data-guidance-target="guild.email-polls">New Poll</a>' in pair
        )
        assert pair.index("New Email") < pair.index("New Poll")
        assert f'href="{reverse("hub_announcements")}" class="pl-card-link"' in card
        assert "hub-btn" not in pair and "style=" not in card

    def it_opens_a_fresh_compose_on_poll_with_the_guild_chosen():
        guild = GuildFactory()
        lead = _member()
        guild.guild_lead = lead
        guild.save(update_fields=["guild_lead"])

        html = _client(lead).get(f"{reverse('hub_compose')}?audience=guild:{guild.pk}&kind=poll").content.decode()

        assert "kind: 'poll'" in html
        assert 'name="kind" value="poll"' in html
        assert f'value="guild:{guild.pk}" selected' in html

    def it_keeps_a_saved_email_on_email_whatever_the_query():
        user = User.objects.create_superuser(username="zpr_admin3", email="zpr_admin3@x.com", password="p")
        client = Client()
        client.force_login(user)
        draft = AnnouncementDraftFactory(author=user)
        html = client.get(f"{reverse('hub_compose_resume', args=[draft.pk])}?kind=poll").content.decode()
        assert "kind: 'email'" in html

    def it_offers_new_poll_beside_new_email_on_email_members():
        user = User.objects.create_superuser(username="zpr_admin4", email="zpr_admin4@x.com", password="p")
        client = Client()
        client.force_login(user)

        html = client.get(reverse("hub_announcements")).content.decode()

        actions = html.split('<span class="pl-btn-row">')[1].split("</span>")[0]
        compose = reverse("hub_compose")
        assert (
            f'href="{compose}?kind=poll" class="hub-btn hub-btn--ghost" data-announcements-new-poll>New poll</a>'
            in actions
        )
        assert (
            f'href="{compose}" class="hub-btn hub-btn--primary" data-help-key="announcements.new">New email</a>'
            in actions
        )

    def it_registers_the_guild_guidance_on_guild_settings():
        from core.guidance import GUIDANCE

        entry = GUIDANCE["guild.email-polls"]
        assert (entry.url_name, entry.query, entry.target) == (
            "hub_guild_edit",
            {"tab": "announcements"},
            "guild.email-polls",
        )
