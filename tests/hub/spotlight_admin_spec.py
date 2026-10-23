"""Admin Tools > Spotlight (#708): permissions, the text and meeting with its preview, the past polls table; no poll posting since #890."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.factories import AnnouncementFactory
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

    def it_previews_both_states_with_the_real_announcements_and_the_fallbacks(admin_client: Client):
        AnnouncementFactory(body="<p>Zorblax <strong>first</strong></p>", position=0)
        AnnouncementFactory(body="<p>Zorblax second</p>", position=1)
        SiteConfiguration.objects.filter(pk=SiteConfiguration.load().pk).update(spotlight_meeting_event=_meeting())

        html = admin_client.get(PAGE).content.decode()
        preview = html.split("data-spotlight-preview", 1)[1].split('id="past-polls"', 1)[0]

        assert 'data-spotlight-state="standard"' in preview
        assert 'data-spotlight-state="minimized"' in preview
        assert '<div class="pl-ann-body pl-spotlight__ann-body"><p>Zorblax <strong>first</strong></p></div>' in preview
        assert '<div class="pl-ann-body pl-spotlight__ann-body"><p>Zorblax second</p></div>' in preview
        assert "of 2</span>" in preview
        assert 'x-text="first || fallback">Zorblax first</span>' in preview
        assert 'x-text="second || secondDefault">Feature Request Meeting</span>' in preview
        assert "Next Feature Request Meeting" in preview
        assert "data-spotlight-foot" in preview
        assert '"fallback": "Zorblax first"' in html
        assert '"hasAnnouncement": true' in html
        assert 'x-show="hasAnnouncement || meetings[meeting]" data-spotlight-preview-states' in html

    def it_never_previews_a_poll(admin_client: Client):
        poll_with("Laser", "Lathe", question="Zorblax poll?")

        html = admin_client.get(PAGE).content.decode()
        preview = html.split("data-spotlight-preview", 1)[1].split('id="past-polls"', 1)[0]

        assert "Zorblax poll?" not in preview
        assert "data-spotlight-choice" not in preview
        assert '"hasAnnouncement": false' in html

    def it_rerenders_an_invalid_save_at_200_with_its_errors_and_what_was_typed(admin_client: Client):
        too_long = "Z" * 121

        response = admin_client.post(reverse("hub_admin_spotlight_text"), {"spotlight_first_line": too_long})

        assert response.status_code == 200
        html = response.content.decode()
        assert "Ensure this value has at most 120 characters" in html
        assert f'value="{too_long}"' in html
        assert SiteConfiguration.load().spotlight_first_line == ""

    def it_says_what_an_empty_first_line_falls_back_to(admin_client: Client):
        html = admin_client.get(PAGE).content.decode()

        assert "Empty uses the first announcement, else the latest update." in html

    def it_escapes_the_lines_in_the_preview(admin_client: Client):
        SiteConfiguration.objects.filter(pk=SiteConfiguration.load().pk).update(spotlight_first_line="<b>Zorblax</b>")

        html = admin_client.get(PAGE).content.decode()

        assert "&lt;b&gt;Zorblax&lt;/b&gt;</span>" in html
        assert "<b>Zorblax</b>" not in html


def describe_polls_have_left_the_page():
    @pytest.mark.parametrize("path", ["/manage/spotlight/polls/new/", "/manage/spotlight/polls/1/close/"])
    def it_no_longer_answers_new_poll_or_close_now(admin_client: Client, path: str):
        assert admin_client.post(path).status_code == 404

    def it_has_no_open_poll_or_new_poll_section(admin_client: Client):
        poll_with("Laser", "Lathe", question="Zorblax open?")

        html = admin_client.get(PAGE).content.decode()

        for gone in ('id="open-poll"', 'id="new-poll"', "data-close-poll", "data-post-poll", "replace-poll"):
            assert gone not in html
        assert "Zorblax open?" not in html

    def it_says_the_page_is_about_announcements_and_the_meeting(admin_client: Client):
        html = admin_client.get(PAGE).content.decode()

        assert "Announcements for the banner on Home and the top left of the Member Portal" in html
        assert "Short notices in a banner at the top of Home and in the Spotlight on every page." in html


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
    """The page's preview is its own copy; the sidebar is still the admin's own Spotlight (#709)."""

    def _sidebar(html: str) -> str:
        return html.split('class="hub-sidebar__spotlight"', 1)[1].split("hub-sidebar__nav", 1)[0]

    def it_shows_the_announcements_in_the_sidebar_too(admin_client: Client):
        announcement = AnnouncementFactory(body="<p>Zorblax sidebar</p>")

        sidebar = _sidebar(admin_client.get(PAGE).content.decode())

        assert f'data-spotlight-announcement="{announcement.pk}"' in sidebar
        assert "data-spotlight-signature=" in sidebar


def describe_the_show_when_empty_toggle():
    def it_renders_as_a_toggle_in_the_text_section(admin_client: Client):
        html = admin_client.get(PAGE).content.decode()
        section = html.split('id="spotlight-text"', 1)[1].split('id="past-polls"', 1)[0]

        assert "Show the Spotlight when there is no announcement and no meeting" in section
        assert "On: members see the version, its latest update and Details." in section
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
        assert '"hasAnnouncement": false' in html
        assert "Hidden. With no announcement and no meeting, members see the logo and version number." in html

    def it_previews_the_quiet_state_while_there_is_no_announcement_and_no_meeting(admin_client: Client):
        html = admin_client.get(PAGE).content.decode()
        preview = html.split("data-spotlight-preview ", 1)[1].split('id="past-polls"', 1)[0]

        assert "data-spotlight-preview-quiet" in preview
        assert 'data-spotlight-state="quiet"' in preview
