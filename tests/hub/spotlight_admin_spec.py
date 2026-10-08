"""Admin Tools > Spotlight (#708): permissions, the text and meeting, polls, the past polls table."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import SiteConfiguration
from membership.models import CommunityEvent
from polls.models import Poll
from tests.membership.factories import CommunityEventFactory, MemberFactory
from tests.polls.factories import PollVoteFactory, poll_with

pytestmark = pytest.mark.django_db

PAGE = reverse("hub_admin_spotlight")


@pytest.fixture
def admin_client(client: Client) -> Client:
    User.objects.create_superuser(username="spotadmin", password="pass", email="spotadmin@example.com")
    client.login(username="spotadmin", password="pass")
    return client


@pytest.fixture
def member_client(client: Client) -> Client:
    User.objects.create_user(username="spotmember", password="pass", email="spotmember@example.com")
    client.login(username="spotmember", password="pass")
    return client


def _meeting() -> CommunityEvent:
    start = timezone.now() + timedelta(days=5)
    return CommunityEventFactory(
        community=True,
        title="Zorblax Feature Request Meeting",
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        video_url="https://meet.example.org/zorblax",
    )


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
            ("post", reverse("hub_admin_spotlight_text")),
            ("post", reverse("hub_admin_spotlight_poll_new")),
            ("post", reverse("hub_admin_spotlight_poll_close", args=[1])),
            ("get", reverse("hub_admin_spotlight_poll", args=[1])),
        ],
    )
    def it_refuses_a_member_who_is_not_an_admin(member_client: Client, method: str, url: str):
        assert getattr(member_client, method)(url).status_code == 403

    def it_sends_a_signed_out_visitor_to_sign_in(client: Client):
        response = client.get(PAGE)

        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_shows_the_card_in_admin_tools_to_admins(admin_client: Client):
        html = admin_client.get(reverse("hub_admin_tools")).content.decode()

        assert f'href="{PAGE}" data-tool-spotlight' in html

    def it_hides_the_card_from_members(member_client: Client):
        assert "data-tool-spotlight" not in member_client.get(reverse("hub_admin_tools")).content.decode()


def describe_the_text_and_meeting():
    def it_saves_the_meeting_and_both_lines(admin_client: Client):
        event = _meeting()

        response = admin_client.post(
            reverse("hub_admin_spotlight_text"),
            {
                "spotlight_meeting_event": event.pk,
                "spotlight_first_line": "Vote now",
                "spotlight_second_line": "Say hi",
            },
        )

        assert response.status_code == 302
        config = SiteConfiguration.load()
        assert config.spotlight_meeting_event == event
        assert (config.spotlight_first_line, config.spotlight_second_line) == ("Vote now", "Say hi")

    def it_stamps_the_text_changed_time_when_a_line_changes(admin_client: Client):
        admin_client.post(reverse("hub_admin_spotlight_text"), {"spotlight_first_line": "Vote now"})

        assert SiteConfiguration.load().spotlight_text_changed_at is not None

    def it_does_not_stamp_when_only_the_meeting_changes(admin_client: Client):
        admin_client.post(reverse("hub_admin_spotlight_text"), {"spotlight_meeting_event": _meeting().pk})

        assert SiteConfiguration.load().spotlight_text_changed_at is None

    def it_does_not_restamp_when_the_lines_are_unchanged(admin_client: Client):
        admin_client.post(reverse("hub_admin_spotlight_text"), {"spotlight_first_line": "Vote now"})
        stamped = SiteConfiguration.load().spotlight_text_changed_at

        admin_client.post(reverse("hub_admin_spotlight_text"), {"spotlight_first_line": "Vote now"})

        assert SiteConfiguration.load().spotlight_text_changed_at == stamped

    def it_saves_only_the_spotlight_columns_so_a_concurrent_edit_survives():
        from hub.spotlight_forms import SpotlightTextForm

        form = SpotlightTextForm({"spotlight_first_line": "Vote now"}, instance=SiteConfiguration.load())
        assert form.is_valid(), form.errors
        # Another admin saves a different setting after this form loaded the row.
        SiteConfiguration.objects.filter(pk=1).update(org_name="Zorblax Makerspace")

        form.save_at(timezone.now())

        config = SiteConfiguration.load()
        assert config.org_name == "Zorblax Makerspace"
        assert config.spotlight_first_line == "Vote now"
        assert config.spotlight_text_changed_at is not None

    def it_offers_published_upcoming_events_and_no_meeting(admin_client: Client):
        event = _meeting()
        pending = _meeting()
        pending.moderation_state = CommunityEvent.ModerationState.PENDING
        pending.save()

        field = admin_client.get(PAGE).context["text_form"].fields["spotlight_meeting_event"]

        assert event in field.queryset
        assert pending not in field.queryset
        assert field.empty_label == "No meeting"

    def it_previews_both_states_with_the_fallbacks(admin_client: Client):
        poll_with("Laser", "Lathe", question="Zorblax next?")
        SiteConfiguration.objects.filter(pk=SiteConfiguration.load().pk).update(spotlight_meeting_event=_meeting())

        html = admin_client.get(PAGE).content.decode()
        preview = html.split("data-spotlight-preview", 1)[1].split('id="open-poll"', 1)[0]

        assert 'data-spotlight-state="standard"' in preview
        assert 'data-spotlight-state="minimized"' in preview
        assert 'x-text="first || pollQuestion">Zorblax next?</span>' in preview
        assert 'x-text="second || secondDefault">Feature Request Meeting</span>' in preview
        assert "Next Feature Request Meeting" in preview
        assert 'data-spotlight-choice="' in preview
        assert '"pollQuestion": "Zorblax next?"' in html

    def it_escapes_the_lines_in_the_preview(admin_client: Client):
        SiteConfiguration.objects.filter(pk=SiteConfiguration.load().pk).update(spotlight_first_line="<b>Zorblax</b>")

        html = admin_client.get(PAGE).content.decode()

        assert "&lt;b&gt;Zorblax&lt;/b&gt;</span>" in html
        assert "<b>Zorblax</b>" not in html


def describe_posting_a_poll():
    def it_opens_a_poll_now(admin_client: Client):
        response = admin_client.post(
            reverse("hub_admin_spotlight_poll_new"), _poll_post(choices=("Laser", "Lathe", "Kiln"))
        )

        assert response.status_code == 302
        poll = Poll.objects.get()
        assert poll.is_open(timezone.now())
        assert [choice.text for choice in poll.choices.all()] == ["Laser", "Lathe", "Kiln"]

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            (_poll_post(choices=("Laser",)), "at least 2 answers"),
            (_poll_post(choices=tuple(f"Answer {n}" for n in range(7))), "at most 6 answers"),
            (_poll_post(choices=("Laser", " ")), "Fill in every answer"),
            (_poll_post(choices=("Laser", "laser")), "Two answers are the same"),
            (_poll_post(question=""), "This field is required"),
            ({**_poll_post(), "days": "61"}, "less than or equal to 60"),
            (_poll_post(choices=("Laser", "L" * 121)), "Keep each answer to 120 characters or fewer"),
            (_poll_post(question="Z" * 201), "at most 200 characters"),
        ],
    )
    def it_refuses_a_poll_with_a_readable_message(admin_client: Client, data: dict, message: str):
        response = admin_client.post(reverse("hub_admin_spotlight_poll_new"), data)

        assert response.status_code == 400
        assert message in response.content.decode()
        assert not Poll.objects.exists()

    def it_keeps_what_was_typed_after_a_refusal(admin_client: Client):
        response = admin_client.post(reverse("hub_admin_spotlight_poll_new"), _poll_post(choices=("Zorblax one",)))

        assert '["Zorblax one"]' in response.content.decode()

    def it_asks_to_close_the_open_poll_before_posting(admin_client: Client):
        poll_with("Laser", "Lathe")

        html = admin_client.get(PAGE).content.decode()

        assert "open-confirm', 'replace-poll'" in html
        assert "document.getElementById('id_close_current').value = 'on'" in html

    def it_refuses_without_the_confirm_and_keeps_one_open(admin_client: Client):
        current = poll_with("Laser", "Lathe")

        response = admin_client.post(reverse("hub_admin_spotlight_poll_new"), _poll_post())

        assert response.status_code == 409
        assert "A poll is already open" in response.content.decode()
        assert list(Poll.objects.open_at(timezone.now())) == [current]

    def it_closes_the_open_poll_and_posts_when_confirmed(admin_client: Client):
        current = poll_with("Laser", "Lathe")

        admin_client.post(reverse("hub_admin_spotlight_poll_new"), _poll_post(close_current="on"))

        open_now = list(Poll.objects.open_at(timezone.now()))
        assert len(open_now) == 1
        assert open_now[0] != current

    def it_never_renders_close_current_as_already_set(admin_client: Client):
        poll_with("Laser", "Lathe")

        response = admin_client.post(
            reverse("hub_admin_spotlight_poll_new"), _poll_post(choices=("Laser",), close_current="on")
        )

        assert '<input type="hidden" name="close_current" id="id_close_current" value="">' in response.content.decode()

    def it_sends_nothing(admin_client: Client):
        admin_client.post(reverse("hub_admin_spotlight_poll_new"), _poll_post())

        assert mail.outbox == []


def describe_the_open_poll():
    def it_shows_counts_percents_and_time_left(admin_client: Client):
        poll_with("Laser", "Lathe", votes=(3, 1), question="Zorblax next?")

        html = admin_client.get(PAGE).content.decode()
        block = html.split('id="open-poll"', 1)[1].split('id="new-poll"', 1)[0]

        assert "Zorblax next?" in block
        assert "3 votes · 75%" in block
        assert "1 vote · 25%" in block
        assert "4 votes so far. Closes in" in block
        assert "data-close-poll" in block

    def it_says_so_when_none_is_open(admin_client: Client):
        assert "data-open-poll-empty" in admin_client.get(PAGE).content.decode()

    def it_closes_now(admin_client: Client):
        poll = poll_with("Laser", "Lathe")

        response = admin_client.post(reverse("hub_admin_spotlight_poll_close", args=[poll.pk]))

        assert response.status_code == 302
        poll.refresh_from_db()
        assert not poll.is_open(timezone.now())


def describe_past_polls():
    def it_shows_the_empty_state(admin_client: Client):
        assert "No past polls yet." in admin_client.get(PAGE).content.decode()

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

        html = admin_client.get(reverse("hub_admin_spotlight_poll", args=[poll.pk])).content.decode()

        assert "Zorblax results" in html
        assert "1 vote · 100%" in html
        assert "Zorblax Voterperson" not in html


def describe_the_admins_own_spotlight_on_this_page():
    """The page's preview reads the Spotlight as nobody; the sidebar must still be the admin's own (#709)."""

    def _sidebar(html: str) -> str:
        return html.split('class="hub-sidebar__spotlight"', 1)[1].split("hub-sidebar__nav", 1)[0]

    def it_offers_the_answers_to_an_admin_who_has_not_voted(admin_client: Client):
        poll_with("Laser", "Lathe", question="Zorblax admin?")

        sidebar = _sidebar(admin_client.get(PAGE).content.decode())

        assert "data-poll-choices" in sidebar
        assert "data-my-vote" not in sidebar

    def it_marks_the_admins_own_vote(admin_client: Client):
        poll = poll_with("Laser", "Lathe")
        admin = User.objects.get(username="spotadmin")
        lathe = poll.choices.get(text="Lathe")
        PollVoteFactory(choice=lathe, member=admin.member)

        html = admin_client.get(PAGE).content.decode()

        assert f'data-poll-result="{lathe.pk}" data-my-vote' in _sidebar(html)
        assert "Your vote" in html.split("data-spotlight-panel", 1)[1]

    def it_still_previews_as_nobody(admin_client: Client):
        poll = poll_with("Laser", "Lathe")
        PollVoteFactory(choice=poll.choices.first(), member=User.objects.get(username="spotadmin").member)

        preview = (
            admin_client.get(PAGE).content.decode().split("data-spotlight-preview", 1)[1].split('id="open-poll"', 1)[0]
        )

        assert "data-spotlight-choice=" in preview
        assert "data-my-vote" not in preview


def describe_the_show_when_empty_toggle():
    def it_renders_as_a_toggle_in_the_text_section(admin_client: Client):
        html = admin_client.get(PAGE).content.decode()
        section = html.split('id="spotlight-text"', 1)[1].split('id="open-poll"', 1)[0]

        assert "Show the Spotlight when there is no poll and no meeting" in section
        assert 'name="spotlight_show_when_empty"' in section
        assert "pl-toggle-row" in section

    def it_saves_off_without_stamping_the_text(admin_client: Client):
        admin_client.post(reverse("hub_admin_spotlight_text"), {"spotlight_first_line": ""})
        config = SiteConfiguration.load()

        assert config.spotlight_show_when_empty is False
        assert config.spotlight_text_changed_at is None

    def it_saves_on(admin_client: Client):
        SiteConfiguration.objects.filter(pk=1).update(spotlight_show_when_empty=False)

        admin_client.post(reverse("hub_admin_spotlight_text"), {"spotlight_show_when_empty": "on"})

        assert SiteConfiguration.load().spotlight_show_when_empty is True

    def it_previews_the_hidden_state(admin_client: Client):
        SiteConfiguration.objects.filter(pk=SiteConfiguration.load().pk).update(spotlight_show_when_empty=False)

        html = admin_client.get(PAGE).content.decode()

        assert "data-spotlight-preview-hidden" in html
        assert '"showWhenEmpty": false' in html
        assert '"hasPoll": false' in html
