"""The live email and the send (#1142 part 2): EmailPoll.deliver and AnnouncementDraft.send for a poll."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Notification, NotificationPreference
from membership.models import AnnouncementDraft, Member
from membership.services.provisioning import provision_user_for_member
from polls.models import EMAIL_POLL_LIVE, EmailPoll, QuestionKind, QuestionSpec
from tests.membership.factories import (
    AnnouncementDraftFactory,
    GuildFactory,
    GuildMembershipFactory,
    MemberFactory,
)
from tests.polls.factories import EmailPollFactory

pytestmark = pytest.mark.django_db

SHOP = QuestionSpec("Which shop?", QuestionKind.PICK_ONE, ("Wood", "Metal"))
IDEAS = QuestionSpec("Any ideas?", QuestionKind.OPEN)


def _member(name: str) -> Member:
    """A signed in active member: an everyone poll reaches them."""
    member = MemberFactory(full_legal_name=name)
    provision_user_for_member(member)
    member.refresh_from_db()
    member.user.last_login = timezone.now()
    member.user.save(update_fields=["last_login"])
    return member


def _poll(*, send_email: bool = True, **kwargs: object) -> EmailPoll:
    """A draft site poll titled Zorblax Survey with two questions and an intro."""
    draft = AnnouncementDraftFactory(kind=AnnouncementDraft.Kind.POLL, title="Zorblax Survey", send_email=send_email)
    poll = EmailPollFactory(draft=draft, intro="Tell us about the shop.", **kwargs)
    poll.replace_questions([SHOP, IDEAS])
    return poll


def _link_in(message: mail.EmailMessage) -> str:
    match = re.search(r"Answer the Poll: (\S+)", message.body)
    assert match is not None
    return match.group(1)


def describe_deliver():
    def it_emails_each_recipient_their_own_link():
        ann, ben = _member("Ann Zorblax"), _member("Ben Zorblax")
        poll = _poll()
        poll.open(timezone.now())

        assert poll.deliver() == 2

        assert sorted(m.to[0] for m in mail.outbox) == sorted([ann.primary_email, ben.primary_email])
        for message in mail.outbox:
            assert message.subject == "Poll: Zorblax Survey"
            assert "Tell us about the shop." in message.body
            assert "2 questions. Closes " in message.body
            assert poll.closes_label in message.body
            token = _link_in(message).rsplit("/ask/link/", 1)[1].rstrip("/")
            _poll_read, member = EmailPoll.read_answer_token(token, timezone.now())
            assert member.primary_email == message.to[0]
            html = message.alternatives[0][0]
            assert _link_in(message) in html
            assert ">Answer the Poll</a>" in html

    def it_emails_only_the_recipients_recorded_at_open():
        _member("Ann Zorblax")
        poll = _poll()
        poll.open(timezone.now())
        late = _member("Late Zorblax")

        poll.deliver()

        assert late.primary_email not in [m.to[0] for m in mail.outbox]
        assert len(mail.outbox) == 1

    def it_follows_the_announcement_email_switch_and_its_own():
        ann, ben, cat = _member("Ann Zorblax"), _member("Ben Zorblax"), _member("Cat Zorblax")
        NotificationPreference.objects.create(
            user=ann.user, event_key="site_announcement", channel="email", enabled=False
        )
        NotificationPreference.objects.create(user=ben.user, event_key=EMAIL_POLL_LIVE, channel="email", enabled=False)
        poll = _poll()
        poll.open(timezone.now())

        assert poll.deliver() == 1

        assert [m.to[0] for m in mail.outbox] == [cat.primary_email]
        assert poll.recipients.count() == 3

    def it_sends_nothing_when_the_live_email_is_off():
        _member("Ann Zorblax")
        poll = _poll(send_email=False)
        poll.open(timezone.now())

        assert poll.deliver() == 0
        assert mail.outbox == []

    def it_emails_nobody_twice_when_a_send_is_retried():
        _member("Ann Zorblax")
        poll = _poll()
        poll.open(timezone.now())
        poll.deliver()
        poll.deliver()

        assert len(mail.outbox) == 1

    def it_rings_no_bell():
        _member("Ann Zorblax")
        poll = _poll()
        poll.open(timezone.now())
        poll.deliver()

        assert not Notification.objects.exists()

    def it_refuses_a_poll_that_is_not_open():
        with pytest.raises(ValueError, match="Open the poll"):
            _poll().deliver()


def describe_sending_a_poll_draft():
    def it_opens_emails_and_marks_the_row_sent():
        _member("Ann Zorblax")
        _member("Ben Zorblax")
        poll = _poll()

        assert poll.draft.send() == (2, 2)

        poll.refresh_from_db()
        poll.draft.refresh_from_db()
        assert poll.is_open(timezone.now())
        assert poll.draft.sent_at is not None
        assert poll.draft.delivery_period == f"email_poll:{poll.pk}:live"
        assert len(mail.outbox) == 2

    def it_counts_everyone_it_was_sent_to_when_email_is_off():
        _member("Ann Zorblax")
        poll = _poll(send_email=False)

        assert poll.draft.send() == (0, 1)

    def it_sends_a_guild_poll_in_the_request():
        guild = GuildFactory()
        ann = _member("Ann Zorblax")
        GuildMembershipFactory(guild=guild, member=ann)
        _member("Outside Zorblax")
        draft = AnnouncementDraftFactory(
            kind=AnnouncementDraft.Kind.POLL, audience=AnnouncementDraft.Audience.GUILD, guild=guild
        )
        poll = EmailPollFactory(draft=draft)
        poll.replace_questions([SHOP])

        assert draft.send_in_request() == (1, 1)
        assert [m.to[0] for m in mail.outbox] == [ann.primary_email]

    def it_keeps_the_window_send_opened_when_the_queue_delivers():
        _member("Ann Zorblax")
        poll = _poll()
        opened = timezone.now() - timedelta(minutes=12)
        poll.open(opened)
        poll.draft.queue_send()
        poll.draft.refresh_from_db()

        poll.draft.send_from_queue()

        poll.refresh_from_db()
        assert poll.opens_at == opened
        assert len(mail.outbox) == 1

    def it_refuses_a_poll_with_no_question():
        draft = AnnouncementDraftFactory(kind=AnnouncementDraft.Kind.POLL, body="")
        EmailPollFactory(draft=draft)

        with pytest.raises(ValidationError, match="Add at least one question"):
            draft.queue_send()

    def it_sends_a_poll_with_no_message_body():
        _member("Ann Zorblax")
        poll = _poll()
        AnnouncementDraft.objects.filter(pk=poll.draft.pk).update(body="")
        poll.draft.refresh_from_db()

        assert poll.draft.send() == (1, 1)

    def it_still_refuses_an_email_with_no_message():
        draft = AnnouncementDraftFactory(body="")

        with pytest.raises(ValidationError, match="Add a message"):
            draft.queue_send()
