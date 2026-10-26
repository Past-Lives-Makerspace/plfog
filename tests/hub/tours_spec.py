"""BDD-style tests for the guided-tour views (Spec C): the state endpoint, the
offer context on the three entry pages, the settings toggle, entry buttons, and
the Help-page "Guided tours" card."""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from core.models import TourState
from membership.models import Member
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

PASSWORD = "pw12345!"
BANNER = "data-tour-banner"
FLOATING_CARD = 'class="pl-tour-offer"'
HEADER_LINK = '<a href="?tour=member-welcome" class="hub-btn hub-btn--sm">Show Me Around</a>'


def _login_member(client, name: str, **member_fields) -> Member:
    """A logged-in, linked ACTIVE member with the welcome modal already dismissed."""
    user = User.objects.create_user(username=name, email=f"{name}@example.com", password=PASSWORD)
    member = Member.objects.get(user=user)  # auto-provisioned by ensure_user_has_member
    member_fields.setdefault("welcome_dismissed_at", timezone.now())
    for field, value in member_fields.items():
        setattr(member, field, value)
    member.save()
    client.login(username=name, password=PASSWORD)
    return member


def describe_tour_state_endpoint():
    def it_requires_login(client):
        response = client.post(reverse("hub_tour_state", kwargs={"tour_key": "member-welcome"}))
        assert response.status_code == 302
        assert "/login/" in response["Location"]

    def it_rejects_get(client):
        _login_member(client, "ts-get")
        response = client.get(reverse("hub_tour_state", kwargs={"tour_key": "member-welcome"}))
        assert response.status_code == 405

    def it_404s_an_unknown_tour_key(client):
        _login_member(client, "ts-404")
        response = client.post(reverse("hub_tour_state", kwargs={"tour_key": "not-a-tour"}), {"status": "completed"})
        assert response.status_code == 404
        assert TourState.objects.count() == 0

    def it_400s_a_bad_status_with_the_form_errors(client):
        _login_member(client, "ts-400")
        response = client.post(reverse("hub_tour_state", kwargs={"tour_key": "member-welcome"}), {"status": "offered"})
        assert response.status_code == 400
        assert "status" in response.json()["errors"]
        assert TourState.objects.count() == 0

    def it_records_a_dismissal(client):
        member = _login_member(client, "ts-dis")
        response = client.post(
            reverse("hub_tour_state", kwargs={"tour_key": "member-welcome"}), {"status": "dismissed"}
        )
        assert response.status_code == 204
        assert TourState.objects.status_for(member.user, "member-welcome") == TourState.Status.DISMISSED

    def it_records_a_completion(client):
        member = _login_member(client, "ts-comp")
        response = client.post(
            reverse("hub_tour_state", kwargs={"tour_key": "member-welcome"}), {"status": "completed"}
        )
        assert response.status_code == 204
        assert TourState.objects.status_for(member.user, "member-welcome") == TourState.Status.COMPLETED

    def it_keeps_completed_sticky_across_a_later_dismissal(client):
        member = _login_member(client, "ts-sticky")
        client.post(reverse("hub_tour_state", kwargs={"tour_key": "member-welcome"}), {"status": "completed"})
        response = client.post(
            reverse("hub_tour_state", kwargs={"tour_key": "member-welcome"}), {"status": "dismissed"}
        )
        assert response.status_code == 204
        assert TourState.objects.status_for(member.user, "member-welcome") == TourState.Status.COMPLETED


def describe_home_tour_offer():
    def it_offers_the_member_welcome_tour_on_first_eligible_get(client):
        member = _login_member(client, "home-offer")
        response = client.get(reverse("hub_home"))
        content = response.content.decode()
        assert "pl-tour-offer" in content
        assert "pl-tour-data" in content
        assert "pl_tour.js" in content
        assert TourState.objects.status_for(member.user, "member-welcome") == TourState.Status.OFFERED

    def it_keeps_offering_across_refreshes_while_ignored(client):
        member = _login_member(client, "home-again")
        client.get(reverse("hub_home"))
        response = client.get(reverse("hub_home"))
        assert "pl-tour-offer" in response.content.decode()
        assert TourState.objects.filter(user=member.user, tour_key="member-welcome").count() == 1

    def it_suppresses_the_offer_while_the_welcome_modal_shows(client):
        _login_member(client, "home-new", welcome_dismissed_at=None)
        response = client.get(reverse("hub_home"))
        content = response.content.decode()
        assert "pl-tour-offer" not in content
        assert "pl-tour-data" not in content
        assert TourState.objects.count() == 0

    def it_suppresses_the_offer_when_toggled_off(client):
        _login_member(client, "home-off", guided_tours_enabled=False)
        assert "pl-tour-offer" not in client.get(reverse("hub_home")).content.decode()

    def it_suppresses_the_offer_after_no_thanks(client):
        member = _login_member(client, "home-dis")
        TourState.objects.mark_dismissed(member.user, "member-welcome")
        assert "pl-tour-offer" not in client.get(reverse("hub_home")).content.decode()

    def it_suppresses_the_offer_after_completion(client):
        member = _login_member(client, "home-done")
        TourState.objects.mark_completed(member.user, "member-welcome")
        assert "pl-tour-offer" not in client.get(reverse("hub_home")).content.decode()

    def it_hides_show_me_around_once_the_tour_is_taken(client):
        member = _login_member(client, "home-btn")
        TourState.objects.mark_completed(member.user, "member-welcome")
        content = client.get(reverse("hub_home")).content.decode()
        assert HEADER_LINK not in content
        assert 'class="pl-home-tour-link"' not in content

    def it_shows_show_me_around_while_the_tour_is_not_taken_and_no_banner_offers_it(client):
        _login_member(client, "home-btn-new", guided_tours_enabled=False)
        content = client.get(reverse("hub_home")).content.decode()
        assert HEADER_LINK in content

    def it_still_offers_a_retake_on_help_once_show_me_around_is_gone(client):
        member = _login_member(client, "home-btn-help")
        TourState.objects.mark_completed(member.user, "member-welcome")
        assert HEADER_LINK not in client.get(reverse("hub_home")).content.decode()
        assert f'href="{reverse("hub_home")}?tour=member-welcome"' in client.get(reverse("hub_help")).content.decode()

    def describe_manual_start():
        def it_autostarts_without_writing_a_row_even_when_dismissed(client):
            member = _login_member(client, "home-manual")
            TourState.objects.mark_dismissed(member.user, "member-welcome")
            content = client.get(f"{reverse('hub_home')}?tour=member-welcome").content.decode()
            assert '"autostart": true' in content
            assert "pl-tour-offer" not in content
            assert TourState.objects.status_for(member.user, "member-welcome") == TourState.Status.DISMISSED

        def it_silently_ignores_an_unknown_tour_param(client):
            _login_member(client, "home-badparam")
            response = client.get(f"{reverse('hub_home')}?tour=<script>alert(1)</script>")
            assert response.status_code == 200
            assert '"autostart": true' not in response.content.decode()


def describe_home_tour_banner():
    def it_shows_the_banner_with_take_the_tour_while_the_tour_is_on_offer(client):
        _login_member(client, "banner-new")
        content = client.get(reverse("hub_home")).content.decode()
        start = content.rindex("<div", 0, content.index(BANNER))
        banner = content[start : content.index("</div>\n", content.index("data-tour-accept"))]
        assert 'class="hub-card pl-tour-banner"' in banner
        assert "New here? Take a 2 minute tour" in banner
        assert "data-pl-tour-offer" in banner  # the hooks pl_tour.js binds: accept starts, decline dismisses
        assert "data-tour-accept>Take the tour</button>" in banner
        assert 'class="pl-btn pl-btn--primary' in banner
        assert 'aria-label="Dismiss the tour" data-tour-decline' in banner
        assert "tour-banner.css" in content

    def it_puts_the_banner_above_the_get_started_card(client):
        _login_member(client, "banner-order")
        content = client.get(reverse("hub_home")).content.decode()
        assert content.index(BANNER) < content.index('data-help-key="home.get-started"')
        assert content.index('class="hub-page-header"') < content.index(BANNER)

    def it_never_renders_the_floating_card_beside_the_banner(client):
        _login_member(client, "banner-alone")
        content = client.get(reverse("hub_home")).content.decode()
        assert BANNER in content
        assert FLOATING_CARD not in content
        assert content.count("data-pl-tour-offer") == 1

    def it_drops_show_me_around_from_the_header_while_the_banner_shows(client):
        _login_member(client, "banner-header")
        content = client.get(reverse("hub_home")).content.decode()
        assert BANNER in content
        assert HEADER_LINK not in content

    def it_keeps_offering_the_banner_while_the_offer_is_ignored(client):
        member = _login_member(client, "banner-ignored")
        client.get(reverse("hub_home"))
        assert TourState.objects.status_for(member.user, "member-welcome") == TourState.Status.OFFERED
        assert BANNER in client.get(reverse("hub_home")).content.decode()

    def it_is_gone_for_good_after_the_close_button_records_dismissed(client):
        member = _login_member(client, "banner-dismiss")
        client.get(reverse("hub_home"))
        response = client.post(
            reverse("hub_tour_state", kwargs={"tour_key": "member-welcome"}), {"status": "dismissed"}
        )
        assert response.status_code == 204
        assert TourState.objects.status_for(member.user, "member-welcome") == TourState.Status.DISMISSED
        content = client.get(reverse("hub_home")).content.decode()
        assert BANNER not in content
        assert FLOATING_CARD not in content
        # Dismissed is the checklist's Done too, so Show Me Around stays away as well.
        assert HEADER_LINK not in content

    def it_is_gone_once_the_tour_is_completed_and_so_is_show_me_around(client):
        member = _login_member(client, "banner-done")
        TourState.objects.mark_completed(member.user, "member-welcome")
        content = client.get(reverse("hub_home")).content.decode()
        assert BANNER not in content
        assert HEADER_LINK not in content

    def it_stays_away_while_the_welcome_modal_shows(client):
        _login_member(client, "banner-welcome", welcome_dismissed_at=None)
        content = client.get(reverse("hub_home")).content.decode()
        assert BANNER not in content
        assert HEADER_LINK in content

    def it_stays_away_when_the_member_turned_tours_off(client):
        _login_member(client, "banner-off", guided_tours_enabled=False)
        content = client.get(reverse("hub_home")).content.decode()
        assert BANNER not in content
        assert HEADER_LINK in content

    def it_stays_away_when_the_site_switch_is_off(client):
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.guided_tours_enabled = False
        config.save()
        _login_member(client, "banner-site-off")
        content = client.get(reverse("hub_home")).content.decode()
        assert BANNER not in content
        assert HEADER_LINK in content

    def it_stays_away_during_a_manual_start(client):
        _login_member(client, "banner-manual")
        content = client.get(f"{reverse('hub_home')}?tour=member-welcome").content.decode()
        assert '"autostart": true' in content
        assert BANNER not in content

    def it_lists_take_the_tour_first_in_get_started_tagged_optional(client):
        _login_member(client, "banner-checklist")
        content = client.get(reverse("hub_home")).content.decode()
        steps = content[content.index('class="pl-onboarding__steps"') :]
        first = steps[steps.index("<a ") : steps.index("</a>")]
        assert f'href="{reverse("hub_home")}?tour=member-welcome"' in first
        assert "Take the tour" in first
        assert 'class="pl-onboarding-step__tag">Optional<' in first
        assert "pl-onboarding-step--done" not in first
        assert "0 of 2 done" in content

    def it_checks_the_take_the_tour_row_once_dismissed(client):
        member = _login_member(client, "banner-check")
        TourState.objects.mark_dismissed(member.user, "member-welcome")
        content = client.get(reverse("hub_home")).content.decode()
        steps = content[content.index('class="pl-onboarding__steps"') :]
        first = steps[steps.index("<a ") : steps.index("</a>")]
        assert "Take the tour" in first
        assert "pl-onboarding-step--done" in first
        assert "0 of 2 done" in content


def describe_guild_edit_tour_offer():
    def it_offers_the_guild_lead_tour_to_a_lead(client):
        member = _login_member(client, "ge-lead")
        guild = GuildFactory(name="Lead Offer Guild", guild_lead=member)
        response = client.get(reverse("hub_guild_edit", kwargs={"pk": guild.pk}))
        content = response.content.decode()
        assert FLOATING_CARD in content
        assert "?tour=guild-lead" in content  # the manual entry link
        assert TourState.objects.status_for(member.user, "guild-lead") == TourState.Status.OFFERED

    def it_stamps_the_tab_strip_and_tab_buttons(client):
        member = _login_member(client, "ge-stamps")
        guild = GuildFactory(name="Stamp Guild", guild_lead=member)
        content = client.get(reverse("hub_guild_edit", kwargs={"pk": guild.pk})).content.decode()
        for key in ("guild.edit-tabs", "guild.run-orientations", "guild.announcements", "guild.manage-staff"):
            assert f'data-help-key="{key}"' in content


def describe_teach_overview_tour_offer():
    # Spec D gated the teach portal on the instructor-orientation unlock, so the
    # tour's audience is ``member.can_create_classes`` — these members are unlocked.
    def it_offers_the_instructor_tour_to_an_unlocked_member(client):
        member = _login_member(client, "teach-offer", instructor_oriented_at=timezone.now())
        response = client.get(reverse("classes:teach_overview"))
        content = response.content.decode()
        assert FLOATING_CARD in content
        assert "?tour=instructor" in content
        assert TourState.objects.status_for(member.user, "instructor") == TourState.Status.OFFERED

    def it_renders_the_persistent_new_class_button_on_the_empty_state(client):
        _login_member(client, "teach-empty", instructor_oriented_at=timezone.now())
        content = client.get(reverse("classes:teach_overview")).content.decode()
        assert 'data-help-key="teach.create-class"' in content
        assert "+ New Class" in content

    def it_renders_the_persistent_new_class_button_with_classes(client):
        from classes.factories import ClassOfferingFactory

        member = _login_member(client, "teach-full", instructor_oriented_at=timezone.now())
        ClassOfferingFactory(instructor=member)
        content = client.get(reverse("classes:teach_overview")).content.decode()
        assert 'data-help-key="teach.create-class"' in content
        assert "+ New Class" in content
        assert 'data-help-key="teach.roster"' in content


def describe_settings_tours_toggle():
    def it_renders_the_guided_tours_card_in_the_notifications_tab(client):
        _login_member(client, "set-render")
        content = client.get(f"{reverse('hub_user_settings')}?tab=notifications").content.decode()
        assert "Guided tours" in content
        assert 'value="tours"' in content

    def it_saves_the_toggle_off_and_redirects_back(client):
        member = _login_member(client, "set-off")
        response = client.post(reverse("hub_user_settings"), {"form_id": "tours"})  # unchecked box
        assert response.status_code == 302
        assert response["Location"].endswith("?tab=notifications")
        member.refresh_from_db()
        assert member.guided_tours_enabled is False

    def it_saves_the_toggle_back_on(client):
        member = _login_member(client, "set-on", guided_tours_enabled=False)
        client.post(reverse("hub_user_settings"), {"form_id": "tours", "guided_tours_enabled": "on"})
        member.refresh_from_db()
        assert member.guided_tours_enabled is True

    def it_shows_a_success_message(client):
        _login_member(client, "set-msg")
        response = client.post(reverse("hub_user_settings"), {"form_id": "tours"}, follow=True)
        assert "Guided tour preference saved." in response.content.decode()

    def it_errors_for_an_unlinked_account(client):
        user = User.objects.create_user(username="set-unlinked", password=PASSWORD)
        Member.objects.filter(user=user).delete()
        client.login(username="set-unlinked", password=PASSWORD)
        response = client.post(reverse("hub_user_settings"), {"form_id": "tours"}, follow=True)
        assert "not linked to a membership" in response.content.decode()


def describe_help_page_tours_card():
    def it_does_not_render_for_anonymous_visitors(client):
        content = client.get(reverse("hub_help")).content.decode()
        # The changelog modal legitimately mentions "Guided tours" on every page,
        # so assert on the card's heading markup, not the bare phrase.
        assert '<h3 class="hub-detail-label">Guided tours</h3>' not in content

    def it_lists_eligible_tours_with_start_links(client):
        _login_member(client, "help-rows")
        content = client.get(reverse("hub_help")).content.decode()
        assert "Guided tours" in content
        assert "Not taken" in content
        assert ">Start<" in content
        assert "?tour=member-welcome" in content
        assert "?tour=instructor" not in content  # locked — hasn't unlocked teaching (Spec D)
        assert "?tour=guild-lead" not in content  # not a lead

    def it_lists_the_instructor_tour_once_teaching_is_unlocked(client):
        _login_member(client, "help-unlocked", instructor_oriented_at=timezone.now())
        content = client.get(reverse("hub_help")).content.decode()
        assert "?tour=instructor" in content

    def it_shows_taken_and_retake_for_a_completed_tour(client):
        member = _login_member(client, "help-done")
        TourState.objects.mark_completed(member.user, "member-welcome")
        content = client.get(reverse("hub_help")).content.decode()
        assert "✓ Taken" in content
        assert "pl-tour-row__status--done" in content
        assert ">Retake<" in content

    def it_links_the_guild_lead_tour_through_a_staffed_guild(client):
        member = _login_member(client, "help-lead")
        guild = GuildFactory(name="Help Lead Guild", guild_lead=member)
        content = client.get(reverse("hub_help")).content.decode()
        assert f"/guilds/{guild.pk}/edit/?tour=guild-lead" in content
