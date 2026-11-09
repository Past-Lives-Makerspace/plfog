"""Email polls part 4 (#1142): the reminder job, Email Members' Polls tab, the Sent tab's reach and Home's Needs Attention."""

from __future__ import annotations

import re
from datetime import timedelta
from io import StringIO
from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.models.signals import post_save
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from factory.django import mute_signals

from core.models import NotificationPreference
from core.scheduled_jobs import SCHEDULED_JOBS
from membership.models import AnnouncementDraft, Member
from polls.models import EmailPoll, QuestionKind, QuestionSpec, ReminderLead, RunLength, send_due_poll_reminders
from tests.membership.factories import (
    AnnouncementDraftFactory,
    GuildFactory,
    GuildMembershipFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
)
from tests.polls.factories import sent_email_poll

pytestmark = pytest.mark.django_db

IDEAS = QuestionSpec("Any zorblax ideas?", QuestionKind.OPEN)
_seq = {"n": 0}


def _member(guild=None, *, name: str = "") -> Member:
    """A signed in member with an account, in ``guild`` when given."""
    _seq["n"] += 1
    member = MemberFactory(full_legal_name=name or f"Zorblax Person {_seq['n']}")
    with mute_signals(post_save):
        user = User.objects.create_user(
            username=f"zpa_{_seq['n']}", email=f"zpa{_seq['n']}@example.com", last_login=timezone.now()
        )
    member.user = user
    member.save(update_fields=["user"])
    if guild is not None:
        GuildMembershipFactory(guild=guild, member=member)
    return member


def _guild_poll(guild, *, opened, title: str = "Zorblax Survey", **kwargs: object) -> EmailPoll:
    draft = AnnouncementDraftFactory(
        kind=AnnouncementDraft.Kind.POLL, audience=AnnouncementDraft.Audience.GUILD, guild=guild, title=title
    )
    return sent_email_poll(IDEAS, now=opened, draft=draft, **kwargs)


def _due_poll(guild) -> EmailPoll:
    """A one week poll with a one day reminder, opened six and a half days ago: its reminder is due."""
    return _guild_poll(
        guild, opened=timezone.now() - timedelta(days=6, hours=12), remind_days_before=ReminderLead.ONE_DAY
    )


def _client_for(member: Member) -> Client:
    client = Client()
    client.force_login(member.user)
    return client


def describe_the_reminder():
    def it_emails_only_recipients_who_have_not_answered_once():
        guild = GuildFactory()
        waiting, answered = _member(guild), _member(guild)
        poll = _due_poll(guild)
        poll.answer(member=answered, answers={poll.questions.get().pk: "Done"}, now=timezone.now())

        run = send_due_poll_reminders(timezone.now())
        again = send_due_poll_reminders(timezone.now())

        assert (run.polls, run.emailed, run.failed) == (1, 1, 0)
        assert (again.polls, again.emailed) == (0, 0)
        assert [m.to[0] for m in mail.outbox] == [waiting.user.email]
        message = mail.outbox[0]
        assert message.subject == "Poll closing soon: Zorblax Survey"
        assert "Answer the Poll: http" in message.body and "/polls/ask/link/" in message.body
        assert poll.closes_label in message.body
        assert ">Answer the Poll</a>" in message.alternatives[0][0]
        poll.refresh_from_db()
        assert poll.reminder_sent_at is not None

    def it_follows_the_announcement_email_switch_and_its_own():
        guild = GuildFactory()
        muted, own_off, heard = _member(guild), _member(guild), _member(guild)
        NotificationPreference.objects.create(
            user=muted.user, event_key="site_announcement", channel="email", enabled=False
        )
        NotificationPreference.objects.create(
            user=own_off.user, event_key="polls.email_poll_reminder", channel="email", enabled=False
        )
        _due_poll(guild)

        assert send_due_poll_reminders(timezone.now()).emailed == 1
        assert [m.to[0] for m in mail.outbox] == [heard.user.email]

    def it_reminds_even_when_the_live_email_was_off():
        guild = GuildFactory()
        member = _member(guild)
        poll = _due_poll(guild)
        AnnouncementDraft.objects.filter(pk=poll.draft_id).update(send_email=False)
        send_due_poll_reminders(timezone.now())
        assert [m.to[0] for m in mail.outbox] == [member.user.email]

    def it_leaves_a_poll_alone_before_its_time_or_without_a_reminder():
        guild = GuildFactory()
        _member(guild)
        _guild_poll(guild, opened=timezone.now(), remind_days_before=ReminderLead.ONE_DAY)
        _guild_poll(guild, opened=timezone.now() - timedelta(days=6, hours=12))
        assert send_due_poll_reminders(timezone.now()).polls == 0
        assert mail.outbox == []

    def it_gives_a_failed_poll_back_for_the_next_run():
        guild = GuildFactory()
        _member(guild)
        poll = _due_poll(guild)
        with mock.patch.object(EmailPoll, "remind", side_effect=RuntimeError("mail down")):
            run = send_due_poll_reminders(timezone.now())
        assert (run.polls, run.failed) == (0, 1)
        poll.refresh_from_db()
        assert poll.reminder_sent_at is None
        assert send_due_poll_reminders(timezone.now()).polls == 1

    def it_skips_a_poll_another_run_claimed_first():
        guild = GuildFactory()
        _member(guild)
        poll = _due_poll(guild)
        real_filter = EmailPoll.objects.filter

        def claimed_meanwhile(*args, **kwargs):
            EmailPoll.objects.all().update(reminder_sent_at=timezone.now())
            return real_filter(*args, **kwargs)

        due = list(EmailPoll.objects.reminders_due(timezone.now()).values_list("pk", flat=True))
        assert due == [poll.pk]
        with mock.patch.object(EmailPoll.objects, "filter", side_effect=claimed_meanwhile):
            run = send_due_poll_reminders(timezone.now())
        assert run.polls == 0
        assert mail.outbox == []

    def it_runs_as_a_15_minute_job_that_fails_loudly():
        job = next(job for job in SCHEDULED_JOBS if job.key == "send_email_poll_reminders")
        assert (job.command, job.schedule_label) == ("send_email_poll_reminders", "Every 15 min")
        guild = GuildFactory()
        _member(guild)
        _due_poll(guild)
        out = StringIO()
        call_command("send_email_poll_reminders", stdout=out)
        assert "Reminded 1 poll(s); emailed 1 member(s)." in out.getvalue()
        _due_poll(guild)
        with mock.patch.object(EmailPoll, "remind", side_effect=RuntimeError("mail down")):
            with pytest.raises(CommandError, match="1 poll reminder"):
                call_command("send_email_poll_reminders", stdout=StringIO())


def describe_the_polls_tab():
    def _admin() -> Client:
        user = User.objects.create_superuser(username="zpa_admin", email="zpa_admin@x.com", password="p")
        client = Client()
        client.force_login(user)
        return client

    def it_lists_each_sent_poll_with_its_audience_and_answers():
        client = _admin()
        guild = GuildFactory(name="Zorblax Guild")
        first, second, _third = _member(guild), _member(guild), _member(guild)
        poll = _guild_poll(guild, opened=timezone.now())
        poll.answer(member=first, answers={poll.questions.get().pk: "Yes"}, now=timezone.now())
        poll.answer(member=second, answers={poll.questions.get().pk: "No"}, now=timezone.now())
        closed = _guild_poll(
            guild, opened=timezone.now() - timedelta(days=10), title="Old Zorblax", run_days=RunLength.ONE_DAY
        )
        draft_only = AnnouncementDraftFactory(kind=AnnouncementDraft.Kind.POLL, title="Unsent Zorblax")
        EmailPoll.objects.create(draft=draft_only)

        html = client.get(f"{reverse('hub_announcements')}?tab=polls").content.decode()

        assert 'data-announcements-tab="polls"' in html and 'data-announcements-count="polls">2<' in html
        row = html.split(f'data-announcement-poll="{poll.pk}"')[1].split("</tr>")[0]
        assert "Zorblax Survey" in row and "Zorblax Guild" in row
        assert "data-poll-answered>2 of 3 answered</td>" in row
        assert "data-poll-state>Open</span>" in row
        # The house table: the row's actions sit in the shared "..." menu, as the Sent tab's siblings would.
        assert 'aria-label="Actions for Zorblax Survey"' in row
        assert (
            f'href="{poll.answer_path}" class="pl-row-menu__item" role="menuitem" @click="closeAndRefocus()" data-announcement-poll-results>View Results'
            in row
        )
        assert f">{poll.closes_label}</td>" in row
        old = html.split(f'data-announcement-poll="{closed.pk}"')[1].split("</tr>")[0]
        assert "data-poll-state>Closed</span>" in old
        assert "Unsent Zorblax" not in html
        assert html.index(f'data-announcement-poll="{poll.pk}"') < html.index(f'data-announcement-poll="{closed.pk}"')

    def it_says_so_when_no_poll_was_sent():
        html = _admin().get(f"{reverse('hub_announcements')}?tab=polls").content.decode()
        assert 'data-announcements-empty="polls">No polls sent yet.' in html

    def it_shows_a_lead_only_their_guilds_polls():
        guild, other = GuildFactory(), GuildFactory()
        lead = _member(guild)
        guild.guild_lead = lead
        guild.save(update_fields=["guild_lead"])
        mine = _guild_poll(guild, opened=timezone.now(), title="Lead Zorblax")
        theirs = _guild_poll(other, opened=timezone.now(), title="Other Zorblax")
        html = _client_for(lead).get(f"{reverse('hub_announcements')}?tab=polls").content.decode()
        assert f'data-announcement-poll="{mine.pk}"' in html
        assert f'data-announcement-poll="{theirs.pk}"' not in html

    def it_costs_the_same_queries_for_more_polls():
        client = _admin()
        guild = GuildFactory()
        _member(guild)
        _guild_poll(guild, opened=timezone.now())
        url = f"{reverse('hub_announcements')}?tab=polls"
        client.get(url)

        def count() -> int:
            with CaptureQueriesContext(connection) as queries:
                client.get(url)
            return len(queries)

        one = count()
        for _ in range(3):
            _guild_poll(GuildFactory(), opened=timezone.now())
        assert count() == one

    def it_counts_the_polls_tab_on_the_other_tabs_too():
        client = _admin()
        _guild_poll(GuildFactory(), opened=timezone.now())
        html = client.get(f"{reverse('hub_announcements')}?tab=drafts").content.decode()
        assert 'data-announcements-count="polls">1<' in html


def describe_the_sent_tab_reach_of_a_poll():
    def it_reads_the_people_the_live_email_reached():
        guild = GuildFactory()
        _member(guild)
        _member(guild)
        draft = AnnouncementDraftFactory(
            kind=AnnouncementDraft.Kind.POLL, audience=AnnouncementDraft.Audience.GUILD, guild=guild
        )
        poll = EmailPoll.objects.create(draft=draft)
        poll.replace_questions([IDEAS])

        draft.send()

        draft.refresh_from_db()
        assert draft.event_key == "polls.email_poll_live"
        assert draft.reach().email == 2
        assert AnnouncementDraft.objects.reach_for([draft]) == {draft.pk: 2}
        assert AnnouncementDraft.objects.emails_for([draft]) == {draft.pk: 2}


def describe_needs_attention_on_home():
    def it_lists_open_polls_the_member_has_not_answered_closing_soonest_first():
        guild = GuildFactory()
        member = _member(guild)
        later = _guild_poll(guild, opened=timezone.now(), title="Later Zorblax", run_days=RunLength.TWO_WEEKS)
        sooner = _guild_poll(guild, opened=timezone.now(), title="Sooner Zorblax", run_days=RunLength.ONE_DAY)
        answered = _guild_poll(guild, opened=timezone.now(), title="Answered Zorblax")
        answered.answer(member=member, answers={answered.questions.get().pk: "Done"}, now=timezone.now())
        _guild_poll(
            guild, opened=timezone.now() - timedelta(days=10), title="Closed Zorblax", run_days=RunLength.ONE_DAY
        )
        _guild_poll(GuildFactory(), opened=timezone.now(), title="Elsewhere Zorblax")

        html = _client_for(member).get(reverse("hub_home")).content.decode()

        block = html.split('data-block="needs-attention"')[1].split("</section>")[0]
        assert re.findall(r"data-needs-attention-poll=\"(\d+)\"", block) == [str(sooner.pk), str(later.pk)]
        assert '<h2 class="pl-home-heading">Needs Attention</h2>' in block
        assert f"Closes {sooner.closes_label}" in block
        assert (
            f'href="{sooner.answer_path}" class="pl-btn pl-btn--primary pl-btn--sm" data-needs-attention-answer>Answer</a>'
            in block
        )
        assert f'href="{reverse("polls:mine")}" class="pl-home-block__more" data-needs-attention-polls-link>' in block

    def it_disappears_when_nothing_needs_an_answer():
        member = _member()
        html = _client_for(member).get(reverse("hub_home")).content.decode()
        assert 'data-block="needs-attention"' not in html

    def it_costs_the_same_queries_for_more_polls():
        guild = GuildFactory()
        member = _member(guild)
        client = _client_for(member)
        _guild_poll(guild, opened=timezone.now())
        client.get(reverse("hub_home"))

        def count() -> int:
            with CaptureQueriesContext(connection) as queries:
                client.get(reverse("hub_home"))
            return len(queries)

        one = count()
        for _ in range(3):
            _guild_poll(guild, opened=timezone.now())
        assert count() == one


def describe_the_guidance():
    def it_registers_the_polls_page_and_carries_its_whats_new_button():
        from core.guidance import GUIDANCE

        entry = GUIDANCE["home.email-polls"]
        assert entry.url_name == "polls:mine"
        assert 2 <= len(entry.steps) <= 5
        from pathlib import Path

        from django.conf import settings

        template = (Path(settings.BASE_DIR) / "templates/polls/mine.html").read_text()
        assert 'new_key="home.email-polls"' in template


def describe_senders_see_results():
    """Fix round 1 on part 4: whoever may open the poll's row on Email Members counts as a sender."""

    def _audience_only_guild_poll():
        guild = GuildFactory()
        lead = _member(guild)
        guild.guild_lead = lead
        guild.save(update_fields=["guild_lead"])
        draft = AnnouncementDraftFactory(
            kind=AnnouncementDraft.Kind.POLL,
            audience=AnnouncementDraft.Audience.GUILD,
            guild=guild,
            author=lead.user,
            title="Lead Zorblax",
        )
        return guild, sent_email_poll(IDEAS, now=timezone.now(), draft=draft, audience_only=True)

    def it_shows_a_co_lead_the_results_of_another_senders_audience_only_poll():
        guild, poll = _audience_only_guild_poll()
        co_lead = _member()
        GuildStaffMembershipFactory(guild=guild, member=co_lead)

        html = _client_for(co_lead).get(poll.answer_path).content.decode()

        assert "data-email-poll-results" in html
        assert "different group" not in html
        assert "data-email-poll-form" not in html

    def it_still_turns_away_a_member_outside_the_row():
        _guild, poll = _audience_only_guild_poll()
        other_guild = GuildFactory()
        other_lead = _member()
        other_guild.guild_lead = other_lead
        other_guild.save(update_fields=["guild_lead"])

        html = _client_for(other_lead).get(poll.answer_path).content.decode()

        assert "This poll was sent to a different group." in html
        assert "data-email-poll-results" not in html

    def it_does_not_make_the_emailed_link_a_sender():
        guild, poll = _audience_only_guild_poll()
        recipient = _member(guild)
        poll.record_recipients({recipient.user_id})
        link = reverse("polls:email_poll_link", args=[poll.make_answer_token(recipient)])
        html = Client().get(link).content.decode()
        assert "data-email-poll-results" not in html
        assert "data-email-poll-form" in html
