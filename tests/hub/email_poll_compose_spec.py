"""The Poll side of Email Members (#1142 part 3): compose, save, resume, validate and send a poll."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core import mail
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from factory.django import mute_signals

from membership.models import AnnouncementDraft, GuildAnnouncement, Member
from polls.models import EmailPoll, EmailPollOption, QuestionKind, ReminderLead, RunLength
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db

SAVE = reverse("hub_compose_save_draft")
SEND = reverse("hub_compose_send")
COMPOSE = reverse("hub_compose")
_seq = {"n": 0}


def _admin(client: Client) -> User:
    user = User.objects.create_superuser(username="zorblax_admin", email="zorblax_admin@x.com", password="p")
    client.login(username="zorblax_admin", password="p")
    return user


def _member(guild=None) -> Member:
    """A signed in member with an account, in ``guild`` when given."""
    _seq["n"] += 1
    member = MemberFactory()
    with mute_signals(post_save):
        user = User.objects.create_user(
            username=f"zpc_{_seq['n']}", email=f"zpc{_seq['n']}@example.com", last_login=timezone.now()
        )
    member.user = user
    member.save(update_fields=["user"])
    if guild is not None:
        GuildMembershipFactory(guild=guild, member=member)
    return member


def _question(
    index: int, text: str, kind: str = QuestionKind.PICK_ONE, options: tuple[str, ...] = ("Zorwood", "Zormetal")
) -> dict:
    row = {f"question-{index}-text": text, f"question-{index}-kind": kind}
    for number, option in enumerate(options, start=1):
        row[f"question-{index}-option_{number}"] = option
    return row


def _poll_data(*questions: dict, total: int | None = None, **overrides: object) -> dict:
    """A posted Poll side: site audience, a title and the given questions (one Pick one by default)."""
    rows = questions or (_question(0, "Which zorblax shop?"),)
    data: dict = {
        "kind": "poll",
        "audience": "site",
        "discord_channel": "none",
        "mention": "none",
        "draft_pk": "",
        "body": "",
        "poll-title": "Zorblax Survey",
        "poll-intro": "Tell us, please.",
        "poll-run_days": str(RunLength.ONE_WEEK),
        "poll-send_email": "on",
        "poll-remind_days_before": str(ReminderLead.ONE_DAY),
        "question-TOTAL_FORMS": str(total if total is not None else len(rows)),
        "question-INITIAL_FORMS": "0",
        "question-MIN_NUM_FORMS": "0",
        "question-MAX_NUM_FORMS": "10",
    }
    for row in rows:
        data.update(row)
    data.update(overrides)
    return data


def _toast(response) -> dict:
    return json.loads(response["HX-Trigger"])["showToast"]


def describe_the_composer():
    def it_offers_email_or_poll_with_the_poll_editor():
        client = Client()
        _admin(client)

        html = client.get(COMPOSE).content.decode()

        assert 'data-compose-kind="email"' in html
        assert 'data-compose-kind="poll"' in html
        # Both choices show at every width: a radio group, never a tab row the phone picker would fold.
        choice = html.split("data-compose-kind-choice>")[1].split("</div>")[0]
        assert "<span>Email</span>" in choice and "<span>Poll</span>" in choice
        assert 'role="radiogroup" aria-label="What to send"' in html
        assert 'value="email" x-model="kind"' in choice and "checked" in choice.split("<span>Email</span>")[0]
        assert "pl-tabs" not in choice
        assert 'name="kind" value="email"' in html
        assert 'name="poll-title"' in html
        assert 'name="question-0-text"' in html
        assert "question-__prefix__-text" in html
        assert html.count('name="poll-run_days"') == len(RunLength.choices)
        assert "data-compose-poll-closes>Closes " in html
        assert 'name="poll-send_email"' in html and 'name="poll-remind"' in html and 'name="poll-audience_only"' in html
        assert "pl-btn pl-btn--danger pl-btn--sm pl-btn--spaced" in html
        assert "+ Add question" in html and "+ Add option" in html


def describe_saving_a_poll_draft():
    def it_saves_the_poll_with_its_questions_and_every_email_only_channel_off():
        client = Client()
        _admin(client)
        data = _poll_data(
            _question(0, "Which zorblax shop?", options=("Zorwood", "", "Zormetal", "  ")),
            _question(1, "Any zorblax ideas?", QuestionKind.OPEN, options=("left over",)),
            **{"poll-remind": "on", "poll-remind_days_before": "2", "poll-audience_only": "on"},
        )

        response = client.post(SAVE, data)

        assert response.status_code == 200
        draft = AnnouncementDraft.objects.get()
        assert draft.kind == AnnouncementDraft.Kind.POLL
        assert draft.title == "Zorblax Survey"
        assert (draft.push_enabled, draft.discord_enabled, draft.mark_as_urgent) == (False, False, False)
        assert draft.discord_channel == GuildAnnouncement.DiscordChannel.NONE
        assert draft.send_email is True
        poll = EmailPoll.objects.get(draft=draft)
        assert (poll.intro, poll.run_days, poll.remind_days_before, poll.audience_only) == (
            "Tell us, please.",
            7,
            2,
            True,
        )
        assert [q.text for q in poll.questions.all()] == ["Which zorblax shop?", "Any zorblax ideas?"]
        assert list(EmailPollOption.objects.values_list("text", flat=True)) == ["Zorwood", "Zormetal"]
        assert poll.opens_at is None

    def it_saves_no_reminder_when_the_switch_is_off():
        client = Client()
        _admin(client)
        client.post(SAVE, _poll_data(**{"poll-remind_days_before": "3"}))
        assert EmailPoll.objects.get().remind_days_before is None

    def it_saves_over_the_same_row_and_its_questions():
        client = Client()
        _admin(client)
        client.post(SAVE, _poll_data())
        draft = AnnouncementDraft.objects.get()
        client.post(SAVE, _poll_data(_question(0, "Renamed zorblax?"), draft_pk=str(draft.pk)))
        assert AnnouncementDraft.objects.count() == 1
        assert [q.text for q in EmailPoll.objects.get().questions.all()] == ["Renamed zorblax?"]

    def it_skips_a_deleted_question_left_as_a_gap():
        client = Client()
        _admin(client)
        client.post(SAVE, _poll_data(_question(0, "First zorblax?"), _question(2, "Third zorblax?"), total=3))
        assert [q.text for q in EmailPoll.objects.get().questions.all()] == ["First zorblax?", "Third zorblax?"]

    def it_turns_a_poll_back_into_an_email():
        client = Client()
        _admin(client)
        client.post(SAVE, _poll_data())
        draft = AnnouncementDraft.objects.get()
        client.post(
            SAVE,
            {
                "kind": "email",
                "audience": "site",
                "body": "<p>Zorblax mail.</p>",
                "discord_channel": "none",
                "mention": "none",
                "draft_pk": str(draft.pk),
            },
        )
        draft.refresh_from_db()
        assert draft.kind == AnnouncementDraft.Kind.EMAIL
        assert not EmailPoll.objects.exists()

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            (_poll_data(**{"poll-title": ""}), "Give the poll a title."),
            (
                _poll_data(_question(0, "One option?", options=("Zorwood", "  "))),
                "Question 1: Give this question 2 to 4 options.",
            ),
            (_poll_data(_question(0, "", options=("A", "B"))), "Question 1: Write the question."),
            (_poll_data(_question(0, "", options=()), total=1), "Add at least one question."),
            (_poll_data(total=0), "Add at least one question."),
            (_poll_data(*[_question(i, f"Zorblax {i}?") for i in range(11)]), "A poll has at most 10 questions."),
            (
                _poll_data(**{"poll-run_days": "1", "poll-remind": "on", "poll-remind_days_before": "1"}),
                "That reminder would go out before the poll opens.",
            ),
        ],
    )
    def it_refuses_a_poll_it_cannot_send_with_a_message(data, message):
        client = Client()
        _admin(client)
        response = client.post(SAVE, data)
        assert response.status_code == 204
        assert message in _toast(response)["message"]
        assert not AnnouncementDraft.objects.exists()


def describe_resuming_and_listing():
    def it_reopens_a_poll_draft_on_the_poll_side_filled_in():
        client = Client()
        _admin(client)
        client.post(SAVE, _poll_data(_question(0, "Which zorblax shop?", options=("Zorwood", "Zormetal", "Zorglass"))))
        draft = AnnouncementDraft.objects.get()

        html = client.get(reverse("hub_compose_resume", args=[draft.pk])).content.decode()

        assert "kind: 'poll'" in html
        assert 'name="kind" value="poll"' in html
        assert 'value="Zorblax Survey"' in html
        assert 'value="Which zorblax shop?"' in html
        assert 'value="Zorglass"' in html
        assert "plPollQuestion('one', 3)" in html

    def it_tags_a_poll_on_the_drafts_tab():
        client = Client()
        _admin(client)
        client.post(SAVE, _poll_data())

        html = client.get(f"{reverse('hub_announcements')}?tab=drafts").content.decode()

        assert 'data-announcement-kind="poll">Poll</span>' in html
        assert "Zorblax Survey" in html

    def it_deletes_a_poll_draft_with_its_poll():
        client = Client()
        _admin(client)
        client.post(SAVE, _poll_data())
        draft = AnnouncementDraft.objects.get()
        client.post(reverse("hub_compose_delete_draft", args=[draft.pk]))
        assert not EmailPoll.objects.exists()


def describe_sending_a_poll():
    def it_opens_a_site_poll_at_send_and_queues_its_email():
        client = Client()
        _admin(client)
        reached = _member()

        response = client.post(SEND, _poll_data())

        assert response.status_code == 302
        draft = AnnouncementDraft.objects.get()
        poll = EmailPoll.objects.get(draft=draft)
        assert draft.send_requested_at is not None
        assert poll.is_open(timezone.now())
        assert poll.is_recipient(reached)
        assert mail.outbox == []
        notes = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("Your poll is open for" in note and "within 15 minutes" in note for note in notes)

    def it_folds_a_repeated_send_into_the_first():
        client = Client()
        _admin(client)
        _member()
        client.post(SEND, _poll_data())
        opened = EmailPoll.objects.get().opens_at

        client.post(SEND, _poll_data())

        assert AnnouncementDraft.objects.count() == 1
        assert EmailPoll.objects.get().opens_at == opened

    def it_sends_a_different_poll_to_the_same_people():
        client = Client()
        _admin(client)
        client.post(SEND, _poll_data())
        client.post(SEND, _poll_data(**{"poll-title": "Another zorblax"}))
        assert AnnouncementDraft.objects.filter(kind=AnnouncementDraft.Kind.POLL).count() == 2

    def it_sends_a_guild_poll_in_the_request():
        client = Client()
        _admin(client)
        guild = GuildFactory()
        member = _member(guild)
        _member()

        response = client.post(SEND, _poll_data(audience=f"guild:{guild.pk}"))

        assert response.status_code == 302
        draft = AnnouncementDraft.objects.get()
        assert draft.sent_at is not None
        assert [m.to[0] for m in mail.outbox] == [member.user.email]
        assert mail.outbox[0].subject == "Poll: Zorblax Survey"

    def it_shows_a_refused_poll_again_with_the_error_beside_the_field():
        client = Client()
        _admin(client)

        response = client.post(SEND, _poll_data(_question(0, "Lonely zorblax?", options=("Zorwood",))))

        assert response.status_code == 200
        html = response.content.decode()
        assert "data-compose-poll-question-error>Give this question 2 to 4 options.</p>" in html
        assert "kind: 'poll'" in html
        assert 'value="Lonely zorblax?"' in html
        assert not AnnouncementDraft.objects.exists()

    def it_still_requires_a_message_for_an_email():
        client = Client()
        _admin(client)
        response = client.post(
            SEND,
            {
                "kind": "email",
                "audience": "site",
                "body": "",
                "discord_channel": "none",
                "mention": "none",
                "draft_pk": "",
            },
        )
        assert response.status_code == 200
        assert not AnnouncementDraft.objects.exists()
