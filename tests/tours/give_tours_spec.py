"""Give Tours (#1278): the Tours page recruits guides, and a member becomes one at the end of its tutorial.

Every active member has Tours in the sidebar. A Tour Guide or admin lands on Upcoming; anyone else who
opens a Tours page lands on Give Tours, never a 403. Become a Tour Guide, the tutorial's last step,
grants Tour Guide through the path an admin's grant takes, recorded as the member's own, sends the
"You have been granted" email and marks Learn How a Tour Day Goes done.

Names are factory strings no changelog could contain (STANDARDS.md section 8).
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, RequestFactory
from django.urls import reverse

from hub.context_processors import _tours_nav
from membership.models import AdminCapability, Member, PermissionSetup
from tests.membership.factories import MemberFactory
from tests.tours.conftest import signed_in
from tours import setup as tour_setup
from tours.views import BECAME_GUIDE_MESSAGE

pytestmark = pytest.mark.django_db

TOUR_GUIDE = AdminCapability.Capability.TOUR_GUIDE
GIVE = reverse("tours:give")
BECOME = reverse("tours:become_guide")
UPCOMING = reverse("tours:upcoming")
HOURS = reverse("tours:hours")


def _granted_emails() -> list:
    return [email for email in mail.outbox if email.subject.startswith("You have been granted")]


def _member(**kwargs: object) -> Member:
    return MemberFactory(full_legal_name="Quillon Arbeck", **kwargs)


def describe_the_tours_entry():
    def it_lands_a_member_who_gives_no_tours_on_give_tours_from_every_tab(plain: Member):
        client = signed_in(plain)
        for name in ("upcoming", "past", "calendar", "hours", "guide", "settings"):
            response = client.get(reverse(f"tours:{name}"))
            assert response.status_code == 302, name
            assert response["Location"] == GIVE, name

    def it_moves_a_htmx_request_to_give_tours_as_a_whole_page(plain: Member):
        response = signed_in(plain).get(reverse("tours:calendar_events"), HTTP_HX_REQUEST="true")
        assert response.status_code == 204
        assert response["HX-Redirect"] == GIVE

    def it_still_refuses_a_post_from_a_member_who_gives_no_tours(plain: Member):
        assert signed_in(plain).post(reverse("tours:hours_save"), {"guide": str(plain.pk)}).status_code == 403

    def it_opens_upcoming_for_a_tour_guide_and_an_admin(guide: Member, admin: Member):
        for member in (guide, admin):
            response = signed_in(member).get(UPCOMING)
            assert response.status_code == 200
            assert 'aria-current="page" data-tours-tab="upcoming">Upcoming</a>' in response.content.decode()

    def it_shows_no_entry_to_a_guest_account():
        guest = _member(status=Member.Status.GUEST)
        content = signed_in(guest).get(reverse("kiln:mine")).content.decode()
        assert 'data-nav="tours"' not in content

    def describe_tours_nav():
        def _request(user: object, *, admin: bool = False, path: str = "/members/") -> object:
            request = RequestFactory().get(path)
            request.user = user  # type: ignore[attr-defined]
            request.view_as = type("ViewAs", (), {"has_actual": lambda self, role: admin})()  # type: ignore[attr-defined]
            return request

        def it_is_there_for_an_active_member_and_lights_on_tours_paths(plain: Member):
            assert _tours_nav(_request(plain.user), plain) == {"url": UPCOMING, "active_class": ""}  # type: ignore[arg-type]
            lit = _tours_nav(_request(plain.user, path=HOURS), plain)  # type: ignore[arg-type]
            assert lit == {"url": UPCOMING, "active_class": "active"}

        def it_is_absent_for_a_lapsed_member_or_no_member():
            former = _member(status=Member.Status.FORMER)
            assert _tours_nav(_request(former.user), former) is None  # type: ignore[arg-type]
            assert _tours_nav(_request(None), None) is None  # type: ignore[arg-type]

        def it_is_there_for_an_admin_with_no_member_record():
            assert _tours_nav(_request(None, admin=True), None) == {"url": UPCOMING, "active_class": ""}  # type: ignore[arg-type]


def describe_give_tours():
    def it_shows_a_member_the_pitch_and_a_five_step_tutorial(plain: Member):
        content = signed_in(plain).get(GIVE).content.decode()
        assert '<h1 class="pl-guild-hero__title">Give Tours</h1>' in content
        for title in ("What You Get", "How It Works", "Getting Paid", "What We Ask Of You", "Common Questions"):
            assert f'<h2 class="pl-teach-section__title">{title}</h2>' in content
        assert re.findall(r'data-give-step="(\d)"', content) == ["1", "2", "3", "4", "5"]
        assert content.count("data-give-start") == 2
        assert 'href="{}" class="hub-sidebar__link active" data-nav="tours"'.format(UPCOMING) in content

    def it_puts_become_a_tour_guide_in_the_last_step_alone(plain: Member):
        content = signed_in(plain).get(GIVE).content.decode()
        assert content.count("Become a Tour Guide</button>") == 1
        assert re.search(
            rf'action="{BECOME}" class="pl-give-tutorial__become" x-show="step === 5" data-give-become', content
        )
        assert content.index("data-give-back") < content.index("data-give-become")
        assert "data-give-hours" not in content

    def it_gives_a_tour_guide_open_my_hours_in_place_of_the_grant(guide: Member, admin: Member):
        for member in (guide, admin):
            content = signed_in(member).get(GIVE).content.decode()
            assert f'action="{BECOME}"' not in content
            assert f'href="{HOURS}" x-show="step === 5" data-give-hours>Open My Hours</a>' in content
            assert "data-give-go-tours>Go to Tours</a>" in content

    def it_refuses_a_guest_a_lapsed_member_and_an_account_with_no_member():
        # Guests and lapsed members are locked out before the view (MemberLockoutMiddleware); the gate's 403 backs it.
        for status in (Member.Status.GUEST, Member.Status.FORMER):
            assert signed_in(_member(status=status)).get(GIVE)["Location"].startswith("/accounts/locked/")
        user = User.objects.create_user("unlinked_quillon", "unlinked_quillon@example.com", "pw")
        Member.objects.filter(user=user).delete()
        client = Client()
        client.force_login(user)
        assert client.get(GIVE).status_code == 403

    def it_sends_a_signed_out_visitor_to_sign_in():
        assert Client().get(GIVE).status_code == 302


def describe_become_a_tour_guide():
    def it_grants_tour_guide_as_their_own_and_lands_on_my_hours_with_a_toast(
        plain: Member, django_capture_on_commit_callbacks
    ):
        client = signed_in(plain)
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(BECOME)
        assert response["Location"] == HOURS
        grant = AdminCapability.objects.get(member=plain, capability=TOUR_GUIDE)
        assert grant.granted_by == plain.user
        page = client.get(HOURS)
        assert page.status_code == 200
        assert BECAME_GUIDE_MESSAGE in [str(message) for message in page.context["messages"]]

    def it_sends_the_granted_email_worded_as_their_own_doing(plain: Member, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            signed_in(plain).post(BECOME)
        [email] = _granted_emails()
        assert email.subject == "You have been granted Tour Guide"
        assert "You turned this on yourself in the Member Portal" in email.body
        assert "turned this on for you" not in email.body
        assert "don't hesitate to ask an admin." in email.body
        assert "you turned this on yourself in the" in email.alternatives[0][0]

    def it_marks_learn_how_a_tour_day_goes_done(plain: Member):
        signed_in(plain).post(BECOME)
        assert tour_setup.LEARN in PermissionSetup.for_member(plain, TOUR_GUIDE).read_steps

    def it_grants_through_the_path_an_admins_grant_takes(plain: Member):
        with patch.object(Member, "set_admin_capability", autospec=True, wraps=Member.set_admin_capability) as grant:
            signed_in(plain).post(BECOME)
        grant.assert_called_once_with(plain, TOUR_GUIDE, True, granted_by=plain.user)

    def it_changes_nothing_on_a_second_press(plain: Member, django_capture_on_commit_callbacks):
        client = signed_in(plain)
        with django_capture_on_commit_callbacks(execute=True):
            client.post(BECOME)
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(BECOME)
        assert response["Location"] == HOURS
        assert AdminCapability.objects.filter(member=plain, capability=TOUR_GUIDE).count() == 1
        assert len(_granted_emails()) == 1

    def it_returns_false_for_a_member_who_already_guides(guide: Member, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            assert tour_setup.become_tour_guide(guide) is False
        assert _granted_emails() == []

    def it_refuses_a_guest_and_a_lapsed_member(django_capture_on_commit_callbacks):
        for status in (Member.Status.GUEST, Member.Status.FORMER):
            member = _member(status=status)
            with django_capture_on_commit_callbacks(execute=True):
                assert signed_in(member).post(BECOME)["Location"].startswith("/accounts/locked/")
            assert not member.has_admin_capability(TOUR_GUIDE)
        assert _granted_emails() == []

    def it_refuses_an_account_with_no_member_record():
        user = User.objects.create_user("unlinked_pell", "unlinked_pell@example.com", "pw")
        Member.objects.filter(user=user).delete()
        client = Client()
        client.force_login(user)
        assert client.post(BECOME).status_code == 403
        assert not AdminCapability.objects.filter(capability=TOUR_GUIDE).exists()

    def it_answers_only_a_post(plain: Member):
        assert signed_in(plain).get(BECOME).status_code == 405

    def it_lets_a_revoked_guide_become_one_again(plain: Member):
        client = signed_in(plain)
        client.post(BECOME)
        plain.set_admin_capability(TOUR_GUIDE, False)
        assert client.get(UPCOMING)["Location"] == GIVE
        assert client.post(BECOME)["Location"] == HOURS
        assert plain.has_admin_capability(TOUR_GUIDE)
