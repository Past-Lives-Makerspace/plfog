"""BDD specs for the guild staff rules popup and the walkthrough buttons (#1076, part 2).

A new lead or staffer owed the rules sees a popup with no close button on any hub page, until they
turn on "I have read the rules" and press I Understand; then a glowing Take the Tour offer with an
Exit stays on every page until they choose. Each choice is logged. Ordinary members see neither,
and neither shows while the Member Agreement or the first-login welcome is waiting, or once the
member no longer leads or staffs a guild. The rules article seeder never touches an existing page.

Names are factory strings no changelog could contain (STANDARDS.md section 8).
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership import staff_onboarding
from membership.models import Guild, GuildStaffOnboardingEvent, Member, WikiPage
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

Action = GuildStaffOnboardingEvent.Action
Step = Member.StaffOnboardingStep
TOUR = staff_onboarding.ONBOARDING_TOUR


def _member(username: str) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = f"{username.title()} Larkspurrow"
    member.welcome_dismissed_at = timezone.now()
    member.save(update_fields=["status", "full_legal_name", "welcome_dismissed_at"])
    return member


@pytest.fixture
def guild() -> Guild:
    return GuildFactory(name="Hollowreed Guild")


@pytest.fixture
def lead(guild: Guild) -> Member:
    member = _member("tamsin")
    guild.assign_lead(member)
    member.refresh_from_db()
    return member


def _client(member: Member) -> Client:
    client = Client()
    client.force_login(member.user)
    return client


def _home(member: Member) -> str:
    return _client(member).get(reverse("hub_home")).content.decode()


def describe_the_rules_popup():
    def it_shows_to_a_new_lead_with_no_close_button(lead: Member):
        page = _home(lead)
        assert 'id="staff-rules-title"' in page
        assert "You Now Have Guild Staff Tools" in page
        assert "You can now run Hollowreed Guild from the Member Portal." in page
        assert "Managing any of these outside the Member Portal is no longer allowed." in page
        popup = page[page.index('aria-labelledby="staff-rules-title"') :]
        popup = popup[: popup.index("</form>")]
        assert "pl-modal__close" not in popup
        assert "@keydown.escape.window" not in popup
        assert ':disabled="!read"' in popup

    def it_never_shows_to_an_ordinary_member():
        page = _home(_member("wren"))
        assert 'id="staff-rules-title"' not in page
        assert "pl-walkthrough-dock" not in page

    def it_waits_for_the_first_login_welcome(lead: Member):
        lead.welcome_dismissed_at = None
        lead.save(update_fields=["welcome_dismissed_at"])
        assert 'id="staff-rules-title"' not in _home(lead)

    def it_waits_for_the_member_agreement(lead: Member, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(Member, "needs_member_agreement", property(lambda self: True))
        assert staff_onboarding_context(lead)["staff_onboarding_step"] == ""

    def it_goes_away_once_they_no_longer_lead_or_staff_a_guild(lead: Member, guild: Guild):
        guild.clear_lead()
        assert 'id="staff-rules-title"' not in _home(lead)


def staff_onboarding_context(member: Member) -> dict[str, object]:
    from django.test import RequestFactory

    from hub.context_processors import staff_onboarding

    request = RequestFactory().get("/")
    request.user = member.user
    return staff_onboarding(request)


def describe_the_context_on_a_post():
    def it_owes_nothing_on_a_post_re_render(lead: Member):
        from django.test import RequestFactory

        from hub.context_processors import staff_onboarding

        request = RequestFactory().post("/")
        request.user = lead.user
        assert staff_onboarding(request) == {"staff_onboarding_step": ""}


def describe_confirming_the_rules():
    def it_changes_nothing_while_the_switch_is_off(lead: Member):
        response = _client(lead).post(reverse("hub_staff_rules_confirm"), {"next": "/guilds/"})
        assert response.status_code == 302
        lead.refresh_from_db()
        assert lead.staff_onboarding_step == Step.RULES
        assert not lead.staff_onboarding_events.filter(action=Action.CONFIRMED).exists()

    def it_logs_the_confirm_and_returns_them_where_they_were(lead: Member):
        response = _client(lead).post(reverse("hub_staff_rules_confirm"), {"read_rules": "on", "next": "/guilds/"})
        assert response["Location"] == "/guilds/"
        lead.refresh_from_db()
        assert lead.staff_onboarding_step == Step.TOUR
        assert lead.staff_onboarding_events.filter(action=Action.CONFIRMED).count() == 1

    def it_never_follows_a_next_to_another_host(lead: Member):
        response = _client(lead).post(
            reverse("hub_staff_rules_confirm"), {"read_rules": "on", "next": "https://elsewhere.example/"}
        )
        assert response["Location"] == reverse("hub_home")


def describe_the_tour_offer():
    @pytest.fixture
    def confirmed(lead: Member) -> Member:
        staff_onboarding.confirm_rules(lead)
        return lead

    def it_glows_on_every_page_with_take_and_exit(confirmed: Member):
        page = _home(confirmed)
        assert 'id="staff-rules-title"' not in page
        dock = page[page.index('class="pl-walkthrough-dock"') :]
        dock = dock[: dock.index("</form>")]
        assert "pl-walkthrough-btn--glow" in dock
        assert f'name="tour_key" value="{TOUR}"' in dock
        assert 'value="take"' in dock
        assert 'value="exit"' in dock

    def it_starts_the_tour_on_take_and_logs_it(confirmed: Member, guild: Guild):
        response = _client(confirmed).post(
            reverse("hub_walkthrough_choice"), {"tour_key": TOUR, "choice": "take", "next": "/"}
        )
        assert response["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tour={TOUR}"
        confirmed.refresh_from_db()
        assert confirmed.staff_onboarding_step == ""
        assert confirmed.staff_onboarding_events.filter(action=Action.TOUR_TAKEN, tour_key=TOUR).count() == 1
        assert "pl-walkthrough-dock" not in _home(confirmed)

    def it_goes_back_on_exit_and_logs_it(confirmed: Member):
        response = _client(confirmed).post(
            reverse("hub_walkthrough_choice"), {"tour_key": TOUR, "choice": "exit", "next": "/guilds/"}
        )
        assert response["Location"] == "/guilds/"
        confirmed.refresh_from_db()
        assert confirmed.staff_onboarding_step == ""
        assert confirmed.staff_onboarding_events.filter(action=Action.TOUR_EXITED, tour_key=TOUR).count() == 1

    def it_refuses_an_unknown_tour_or_choice(confirmed: Member):
        client = _client(confirmed)
        url = reverse("hub_walkthrough_choice")
        assert client.post(url, {"tour_key": "no-such-tour", "choice": "take"}).status_code == 400
        assert client.post(url, {"tour_key": TOUR, "choice": "maybe"}).status_code == 400

    def it_refuses_a_member_the_tour_is_not_for():
        response = _client(_member("wren")).post(
            reverse("hub_walkthrough_choice"), {"tour_key": TOUR, "choice": "take"}
        )
        assert response.status_code == 403
        assert not GuildStaffOnboardingEvent.objects.exists()


def describe_seed_wiki_staff_rules():
    def it_adds_the_rules_article_once_and_the_welcome_links_it():
        call_command("seed_wiki_staff_rules")
        call_command("seed_wiki_staff_rules")
        [page] = WikiPage.objects.filter(title=staff_onboarding.RULES_PAGE_TITLE)
        assert "Managing them anywhere else is no longer allowed." in page.body
        assert staff_onboarding.rules_page_path() == reverse("hub_wiki_page", args=[page.slug])

    def it_writes_nothing_on_a_dry_run():
        call_command("seed_wiki_staff_rules", "--dry-run")
        assert not WikiPage.objects.filter(title=staff_onboarding.RULES_PAGE_TITLE).exists()


def describe_the_modal_component():
    def it_keeps_the_close_button_unless_the_modal_is_required():
        from django.template.loader import render_to_string

        plain = render_to_string("components/modal.html", {"modal_id": "x", "modal_title": "X"})
        required = render_to_string(
            "components/modal.html", {"modal_id": "y", "modal_title": "Y", "modal_required": True}
        )
        assert "pl-modal__close" in plain
        assert "@click.outside" in plain
        assert "pl-modal__close" not in required
        assert "@click.outside" not in required
        assert "open = true" in required
