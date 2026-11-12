"""The Tour Guide Get Set Up card on the Tours page, its buttons, the Guide tab and the badge (#1167).

A granted guide sees the card above every tab with one step open; Got It, Skip This Step, Hide and
Show post back to where they were; marking a first tour Attended finishes setup with a toast and
puts the badge on the Member Directory card. The Guide tab shows the wiki page an admin linked,
which an admin picks on the wiki's Edit page.

Names are factory strings no changelog could contain (STANDARDS.md section 8).
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.contrib.messages import get_messages
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone

from membership.models import AdminCapability, LeadershipBadge, Member, PermissionSetup, WikiPage
from tests.membership.factories import MemberFactory
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourHoursFactory
from tours import setup as tour_setup
from tours.models import TourBooking

pytestmark = pytest.mark.django_db

TOUR_GUIDE = AdminCapability.Capability.TOUR_GUIDE
UPCOMING = reverse("tours:upcoming")
GUIDE = reverse("tours:guide")
HTMX = {"HTTP_HX_REQUEST": "true"}
TAB_URLS = [UPCOMING, reverse("tours:past"), reverse("tours:hours"), GUIDE]


def _url(action: str) -> str:
    return reverse(f"hub_permission_setup_{action}", args=[TOUR_GUIDE])


def _flashes(response: HttpResponse) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def _past(guide: Member, **kwargs: object) -> TourBooking:
    starts = timezone.now() - timedelta(days=1)
    return TourBookingFactory(guide=guide, starts_at=starts, ends_at=starts + timedelta(hours=1), **kwargs)


def _ready_but_first_tour(guide: Member) -> None:
    TourHoursFactory(guide=guide)
    PermissionSetup.for_member(guide, TOUR_GUIDE).mark_read(tour_setup.LEARN)


def describe_the_card_on_the_tours_page():
    @pytest.mark.parametrize("url", TAB_URLS)
    def it_shows_above_every_tab_for_a_granted_guide(guide: Member, url: str):
        content = signed_in(guide).get(url).content.decode()
        assert f'data-permission-setup="{TOUR_GUIDE}"' in content
        assert "Get Set Up to Give Tours" in content
        assert 'data-setup-step="hours" data-setup-open' in content

    def it_shows_on_the_admin_settings_tab_for_an_admin_with_the_grant(admin: Member):
        admin.admin_capabilities.create(capability=TOUR_GUIDE)
        content = signed_in(admin).get(reverse("tours:settings")).content.decode()
        assert f'data-permission-setup="{TOUR_GUIDE}"' in content

    def it_never_shows_to_an_admin_without_the_grant(admin: Member):
        content = signed_in(admin).get(UPCOMING).content.decode()
        assert "data-permission-setup" not in content
        assert 'data-tours-tab="guide"' in content

    def it_marks_the_calendar_step_unavailable_without_google(guide: Member):
        content = signed_in(guide).get(UPCOMING).content.decode()
        assert "pl-perm-step--unavailable" in content
        assert "Not available yet" in content

    def it_opens_the_calendar_step_with_skip_when_google_is_set_up(guide: Member, google_on: None):
        content = signed_in(guide).get(UPCOMING).content.decode()
        assert 'data-setup-step="calendar" data-setup-open' in content
        assert _url("skip") in content

    def it_names_who_to_ask(guide: Member):
        content = signed_in(guide).get(UPCOMING).content.decode()
        assert "Questions? Ask an admin." in content

    def it_hides_a_hidden_card(guide: Member):
        PermissionSetup.for_member(guide, TOUR_GUIDE).hide()
        content = signed_in(guide).get(UPCOMING).content.decode()
        assert "data-permission-setup" not in content

    def it_folds_to_the_summary_once_done_with_a_toast_once(guide: Member):
        _ready_but_first_tour(guide)
        _past(guide, status=TourBooking.Status.ATTENDED, party_size=5)
        client = signed_in(guide)
        response = client.get(UPCOMING)
        content = response.content.decode()
        assert "data-setup-done" in content
        assert "You&#x27;re a Tour Guide" in content
        assert "You&#x27;re paid $50 when a visitor you toured becomes a member." in content
        assert 'class="pl-perm-done__num">5<' in content
        assert tour_setup.TOUR_GUIDE_SETUP.celebration in _flashes(response)
        assert tour_setup.TOUR_GUIDE_SETUP.celebration not in _flashes(client.get(UPCOMING))


def describe_the_buttons():
    def it_ticks_the_reading_step_on_got_it_and_lands_on_next(guide: Member):
        response = signed_in(guide).post(_url("got_it"), {"step": tour_setup.LEARN, "next": GUIDE})
        assert response.status_code == 302
        assert response["Location"] == GUIDE
        assert PermissionSetup.objects.get(member=guide).read_steps == [tour_setup.LEARN]

    def it_refuses_got_it_on_a_step_that_is_not_read(guide: Member):
        response = signed_in(guide).post(_url("got_it"), {"step": tour_setup.HOURS})
        assert response.status_code == 400

    def it_skips_the_optional_calendar(guide: Member):
        response = signed_in(guide).post(_url("skip"), {"step": tour_setup.CALENDAR})
        assert response["Location"] == UPCOMING
        assert PermissionSetup.objects.get(member=guide).skipped_steps == [tour_setup.CALENDAR]

    def it_refuses_to_skip_a_required_step(guide: Member):
        assert signed_in(guide).post(_url("skip"), {"step": tour_setup.HOURS}).status_code == 400

    def it_ignores_a_next_on_another_site(guide: Member):
        response = signed_in(guide).post(_url("hide"), {"next": "https://evil.example.com/"})
        assert response["Location"] == UPCOMING

    def it_hides_and_brings_the_card_back(guide: Member):
        client = signed_in(guide)
        response = client.post(_url("hide"), {"next": UPCOMING})
        assert "Get Set Up is hidden. Bring it back from the Guide tab." in _flashes(response)
        guide_tab = client.get(GUIDE).content.decode()
        assert "data-setup-restore" in guide_tab
        assert "Show the Get Set Up Card" in guide_tab
        client.post(_url("show"), {"next": UPCOMING})
        assert PermissionSetup.objects.get(member=guide).hidden_at is None

    def it_toasts_the_badge_when_got_it_finishes_setup(guide: Member):
        TourHoursFactory(guide=guide)
        _past(guide, status=TourBooking.Status.ATTENDED)
        response = signed_in(guide).post(_url("got_it"), {"step": tour_setup.LEARN})
        assert tour_setup.TOUR_GUIDE_SETUP.celebration in _flashes(response)
        assert guide.leadership_badges.filter(permission=TOUR_GUIDE).exists()

    @pytest.mark.parametrize("action", ["got_it", "skip", "hide", "show"])
    def it_is_a_404_to_a_member_without_the_grant(plain: Member, action: str):
        assert signed_in(plain).post(_url(action), {"step": tour_setup.LEARN}).status_code == 404

    def it_is_a_404_for_a_permission_with_no_card(guide: Member):
        url = reverse("hub_permission_setup_hide", args=[AdminCapability.Capability.REFUNDS])
        assert signed_in(guide).post(url).status_code == 404

    def it_refuses_a_get(guide: Member):
        assert signed_in(guide).get(_url("hide")).status_code == 405


def describe_marking_the_first_tour_attended():
    def it_finishes_setup_with_the_badge_in_the_toast(guide: Member):
        _ready_but_first_tour(guide)
        booking = _past(guide, visitor_name="Odalys Pentecost")
        response = signed_in(guide).post(
            reverse("tours:booking_mark", args=[booking.pk]), {"outcome": "attended"}, **HTMX
        )
        toast = json.loads(response["HX-Trigger"])["showToast"]["message"]
        assert toast == f"Odalys Pentecost marked attended. {tour_setup.TOUR_GUIDE_SETUP.celebration}"
        assert guide.leadership_badges.filter(permission=TOUR_GUIDE).exists()

    def it_keeps_the_plain_toast_while_steps_remain(guide: Member):
        booking = _past(guide, visitor_name="Odalys Pentecost")
        response = signed_in(guide).post(
            reverse("tours:booking_mark", args=[booking.pk]), {"outcome": "attended"}, **HTMX
        )
        assert json.loads(response["HX-Trigger"])["showToast"]["message"] == "Odalys Pentecost marked attended."

    def it_keeps_the_plain_toast_for_a_no_show(guide: Member):
        _ready_but_first_tour(guide)
        booking = _past(guide, visitor_name="Odalys Pentecost")
        response = signed_in(guide).post(
            reverse("tours:booking_mark", args=[booking.pk]), {"outcome": "no_show"}, **HTMX
        )
        assert json.loads(response["HX-Trigger"])["showToast"]["message"] == "Odalys Pentecost marked a no show."

    def it_finishes_quietly_when_an_admin_marks_it_and_toasts_the_guide_later(guide: Member, admin: Member):
        _ready_but_first_tour(guide)
        booking = _past(guide, visitor_name="Odalys Pentecost")
        response = signed_in(admin).post(
            reverse("tours:booking_mark", args=[booking.pk]), {"outcome": "attended"}, **HTMX
        )
        assert json.loads(response["HX-Trigger"])["showToast"]["message"] == "Odalys Pentecost marked attended."
        later = signed_in(guide).get(UPCOMING)
        assert tour_setup.TOUR_GUIDE_SETUP.celebration in _flashes(later)

    def it_toasts_through_a_plain_post_too(guide: Member):
        _ready_but_first_tour(guide)
        booking = _past(guide, visitor_name="Odalys Pentecost")
        response = signed_in(guide).post(reverse("tours:booking_mark", args=[booking.pk]), {"outcome": "attended"})
        assert f"Odalys Pentecost marked attended. {tour_setup.TOUR_GUIDE_SETUP.celebration}" in _flashes(response)

    def it_shows_the_badge_on_the_member_directory_card(guide: Member):
        _ready_but_first_tour(guide)
        _past(guide, status=TourBooking.Status.ATTENDED)
        signed_in(guide).get(UPCOMING)
        badge = LeadershipBadge.objects.get(permission=TOUR_GUIDE)
        viewer = MemberFactory(full_legal_name="Ines Lookwell")
        content = signed_in(viewer).get(reverse("hub_member_directory")).content.decode()
        assert f'style="background-color: {badge.color}; color: {badge.text_color};">Tour Guide</li>' in content


def describe_the_guide_tab():
    def it_shows_the_linked_wiki_page(guide: Member):
        page = WikiPage.guide_page_for(TOUR_GUIDE)
        assert page is not None
        content = signed_in(guide).get(GUIDE).content.decode()
        assert f'data-guide-page="{page.slug}"' in content
        assert 'aria-current="page" data-tours-tab="guide"' in content
        assert "pl-wp-toc__chip" in content
        assert reverse("hub_wiki_page", args=[page.slug]) in content
        assert "Open on the Wiki" in content
        assert "Ask an admin." in content

    def it_offers_an_admin_the_edit(admin: Member):
        content = signed_in(admin).get(GUIDE).content.decode()
        assert "Open to Edit on the Wiki" in content
        assert "Admins edit this page on the wiki." in content

    def it_says_there_is_none_yet_and_tells_an_admin_how(admin: Member):
        WikiPage.objects.filter(guide_for=TOUR_GUIDE).update(guide_for="")
        content = signed_in(admin).get(GUIDE).content.decode()
        assert "data-guide-empty" in content
        assert "pick Tour Guide under Guide For" in content

    def it_says_none_yet_to_a_guide_without_the_admin_line(guide: Member):
        WikiPage.objects.filter(guide_for=TOUR_GUIDE).update(guide_for="")
        content = signed_in(guide).get(GUIDE).content.decode()
        assert "data-guide-empty" in content
        assert "Guide For" not in content

    def it_is_refused_to_a_plain_member(plain: Member):
        assert signed_in(plain).get(GUIDE).status_code in (302, 403)


def describe_picking_the_guide_page_in_the_wiki():
    def _payload(page: WikiPage, **extra: str) -> dict[str, str]:
        return {
            "title": page.title,
            "body": "<p>Showing folks around.</p>",
            "facts-TOTAL_FORMS": "0",
            "facts-INITIAL_FORMS": "0",
            "facts-MIN_NUM_FORMS": "0",
            "facts-MAX_NUM_FORMS": "1000",
            "attachments-TOTAL_FORMS": "0",
            "attachments-INITIAL_FORMS": "0",
            "attachments-MIN_NUM_FORMS": "0",
            "attachments-MAX_NUM_FORMS": "1000",
            **extra,
        }

    def _new_page() -> WikiPage:
        return WikiPage.objects.create(title="Wayfinding for Visitors", kind=WikiPage.Kind.HOWTO, body="<p>a</p>")

    def it_offers_guide_for_to_an_admin_and_moves_the_link(admin: Member):
        page = _new_page()
        client = signed_in(admin)
        form = client.get(reverse("hub_wiki_edit", args=[page.slug])).content.decode()
        assert 'name="guide_for"' in form
        client.post(reverse("hub_wiki_edit", args=[page.slug]), _payload(page, guide_for=TOUR_GUIDE))
        assert WikiPage.guide_page_for(TOUR_GUIDE) == page

    def it_hides_guide_for_on_a_guild_page(admin: Member):
        from tests.membership.factories import GuildFactory

        page = WikiPage.objects.create(
            title="Guild Corner Notes", kind=WikiPage.Kind.HOWTO, body="<p>a</p>", guild=GuildFactory()
        )
        form = signed_in(admin).get(reverse("hub_wiki_edit", args=[page.slug])).content.decode()
        assert 'name="guide_for"' not in form

    def it_never_offers_guide_for_to_a_member():
        page = _new_page()
        member = make_guide(full_legal_name="Mirela Quast")
        form = signed_in(member).get(reverse("hub_wiki_edit", args=[page.slug])).content.decode()
        assert 'name="guide_for"' not in form
