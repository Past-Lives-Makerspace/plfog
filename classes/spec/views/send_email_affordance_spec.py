"""BDD specs for the Send Email link on the per-class screen, and the composer behind it.

Issue #371 item 1. Two separate things are pinned here.

**The link never lies.** Wherever Send Email renders, the composer it points at admits that same
request for that same class. Covered in two halves rather than as one implication over all five
roles, because an implication has nothing to say about a role that is offered no link: the roles
that ARE offered it assert that the composer takes them, and the roles that are not assert both
that the composer would have refused and which status each tab answered to reach its zero. A
single `offered implies admitted` test would have passed on three of five rows without ever
asking the composer anything.

**A refusal is never silent.** ``hub_compose`` answered a viewer it would not admit with a bare
``redirect`` to the propose flow, and ``templates/hub/base.html`` puts ``hx-boost="true"`` on the
body, so the page simply became an unrelated form with no explanation. It now carries a message,
which :class:`core.middleware.ToastFlashMiddleware` hands across the boosted redirect in the
``pl_toast`` cookie. The composer's three HTMX POST paths carry the same sentence as a toast on
their 403, the ``hub/views.py`` ``_skills_no_member_response`` idiom.

Both templates that carry the link are covered, and they are one screen: the header link lives in
``templates/classes/_components/class_screen_base.html`` and the roster-foot link in
``templates/classes/teach/class_registrations.html``, which extends it. So the Registrations tab
renders TWO links and every other tab renders one, and the per-tab counts below are what tells
"the base template hid it" apart from "both templates hid it".

``hub.view_as`` has no "instructor" role. The parent issue's "admin previewing as instructor" row
is the class's own instructor, who is a different VIEWER, and gets their own block.
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassOffering, Registration
from membership.models import Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

Status = ClassOffering.Status

#: Every tab of the per-class screen. The header link is judged on all of them, not only on the
#: one tab that also carries the roster-foot link.
CLASS_SCREEN_TABS = (
    "classes:teach_class_detail",
    "classes:teach_class_registrations",
    "classes:teach_class_waitlist",
    "classes:teach_class_discount_codes",
    "classes:teach_class_emails",
)

#: Every tab open, and the link on all of them: one in the header, plus the roster-foot link on
#: Registrations. Each entry is ``(status, links)`` — see :func:`_screen_probe` for why the status
#: travels with the count.
FULLY_OFFERED = {
    "classes:teach_class_detail": (200, 1),
    "classes:teach_class_registrations": (200, 2),
    "classes:teach_class_waitlist": (200, 1),
    "classes:teach_class_discount_codes": (200, 1),
    "classes:teach_class_emails": (200, 1),
}

#: ``class_access`` is view-as aware, so a previewing admin gets no per-class screen at all. This
#: is WHY those rows count zero links, and a bare zero could not tell it from a 500.
SCREEN_CLOSED = dict.fromkeys(CLASS_SCREEN_TABS, (404, 0))

#: The guild set: ruling 12 gives a lead or staffer Emails and nothing else on a class they do
#: not teach, and that one open tab carries no link.
GUILD_SCREEN = {
    "classes:teach_class_detail": (404, 0),
    "classes:teach_class_registrations": (404, 0),
    "classes:teach_class_waitlist": (404, 0),
    "classes:teach_class_discount_codes": (404, 0),
    "classes:teach_class_emails": (200, 0),
}

#: The exact sentence every viewer who simply cannot compose is given.
GENERIC_REFUSAL = "Your account cannot send announcements. You can propose one here for a lead to review."


@pytest.fixture
def instructor(db) -> Member:
    """The class's own instructor: granted teaching access, with a public instructor page."""
    MembershipPlanFactory()
    user = UserFactory(username="affordance-teacher@example.com")
    member = InstructorFactory(user=user, full_legal_name="Ren Alvarez", instructor_slug="ren-alvarez")
    member.instructor_oriented_at = timezone.now()
    member.save(update_fields=["instructor_oriented_at"])
    return member


@pytest.fixture
def guild_lead(db) -> Member:
    """A member who leads the guild behind the class's category but teaches nothing."""
    MembershipPlanFactory()
    user = UserFactory(username="affordance-lead@example.com")
    member = Member.objects.get(user=user)
    member.full_legal_name = "Wren Halloway"
    member.save(update_fields=["full_legal_name"])
    return member


@pytest.fixture
def roster_class(instructor, guild_lead) -> ClassOffering:
    """A published class with one confirmed registrant.

    The registrant is load-bearing: the roster-foot link is inside ``{% if registrations %}``,
    so without one this spec would silently stop covering the second template.
    """
    guild = GuildFactory(name="Metalsmithing", guild_lead=guild_lead)
    offering = ClassOfferingFactory(
        title="Intro to Lost Wax Casting",
        slug="affordance-lost-wax",
        instructor=instructor,
        category=CategoryFactory(name="Metalsmithing", guild=guild),
        status=Status.PUBLISHED,
        price_cents=8500,
    )
    starts = timezone.now() + timezone.timedelta(days=14)
    ClassSessionFactory(class_offering=offering, starts_at=starts, ends_at=starts + timezone.timedelta(hours=4))
    RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
    return offering


def _compose_href(offering: ClassOffering) -> str:
    """The exact href both templates render. Django escapes the ``&``, so the needle must too."""
    return f"{reverse('hub_compose')}?audience=class:{offering.pk}&amp;lock=1"


def _compose_url(offering: ClassOffering) -> str:
    """The same link as a URL to request: what a click on it actually sends."""
    return f"{reverse('hub_compose')}?audience=class:{offering.pk}&lock=1"


def _screen_probe(client, offering: ClassOffering) -> dict[str, tuple[int, int]]:
    """Per tab: the status it answered, and how many Send Email links it rendered.

    The status travels with the count deliberately. A count on its own cannot tell "this tab
    correctly withheld the link" from "this tab 500'd", and a spec that reads a crash as a
    successful hide is worse than no spec at all.
    """
    probe = {}
    for tab in CLASS_SCREEN_TABS:
        response = client.get(reverse(tab, kwargs={"pk": offering.pk}))
        html = response.content.decode() if response.status_code == 200 else ""
        probe[tab] = (response.status_code, html.count(_compose_href(offering)))
    return probe


def _refusal_said(client, offering: ClassOffering) -> str:
    """Everything the destination page was handed to say about the refusal, as one string."""
    response = client.get(_compose_url(offering), follow=True)
    return " ".join(str(m) for m in response.context["messages"])


def _preview_as(client, role: str | None) -> None:
    """The shipped session idiom. ``None`` clears the pick: an admin looking as themselves."""
    session = client.session
    if role is None:
        session.pop("view_as_role", None)
    else:
        session["view_as_role"] = role
    session.save()


def describe_the_send_email_link():
    def describe_it_is_offered_only_where_the_composer_admits():
        # Deliberately two tests rather than one implication over all five roles. An implication
        # has no content on a role that is offered nothing: `offered or not admitted` short
        # circuits true before it ever asks the composer anything. So the offering roles assert
        # the implication where it bites, and the withholding roles assert the two facts that
        # make it hold trivially, each row saying honestly which of the two it is doing.

        @pytest.mark.parametrize("preview", [None, "admin"])
        def it_admits_every_request_it_offers_the_link_to(preview, admin_user, roster_class, client):
            client.force_login(admin_user)
            _preview_as(client, preview)
            # Both halves carry content: the links really are rendered, so this cannot go vacuous
            # by the affordance quietly disappearing, and the composer really takes that request.
            assert _screen_probe(client, roster_class) == FULLY_OFFERED
            assert client.get(_compose_url(roster_class)).status_code == 200

        @pytest.mark.parametrize(
            "preview",
            ["guild_officer", "member", "guest"],
        )
        def it_offers_nothing_where_the_composer_refuses(preview, admin_user, roster_class, client):
            client.force_login(admin_user)
            _preview_as(client, preview)
            assert client.get(_compose_url(roster_class)).status_code == 302
            # Every zero is pinned to the status that explains it, so a tab that started
            # answering 500 could never be mistaken for a tab that correctly hid the link.
            # All three previews close the screen outright, Guild Officer included: an admin
            # previewing it neither leads nor staffs this class's guild, so no leg matches.
            assert _screen_probe(client, roster_class) == SCREEN_CLOSED

        def it_admits_the_classs_own_instructor_it_offers_the_link_to(instructor, roster_class, client):
            client.force_login(instructor.user)
            assert _screen_probe(client, roster_class) == FULLY_OFFERED
            assert client.get(_compose_url(roster_class)).status_code == 200

        def it_offers_nothing_to_a_guild_lead_who_does_not_teach_the_class(guild_lead, roster_class, client):
            # The safe direction. Leading a guild opens the composer PAGE for them, because
            # _can_enter_compose asks only whether they may compose something. It does not follow
            # that they may address this roster: that is _can_announce_to_class, which refuses, so
            # Send returns 403. can_send_email tracks the second predicate, not the first, which
            # is why withholding the link here is correct rather than over-cautious. Showing it
            # would rebuild the #371 defect exactly: a button opening a composer locked to a class
            # that then refuses on Send.
            client.force_login(guild_lead.user)
            assert _screen_probe(client, roster_class) == GUILD_SCREEN
            assert client.get(_compose_url(roster_class)).status_code == 200

    def describe_both_templates_carry_it():
        def it_renders_the_header_link_from_the_shared_screen_base(admin_user, roster_class, client):
            client.force_login(admin_user)
            html = client.get(reverse("classes:teach_class_emails", kwargs={"pk": roster_class.pk})).content.decode()
            # The Emails tab has no roster-foot link, so a hit here can only be the base template.
            assert html.count(_compose_href(roster_class)) == 1

        def it_renders_a_second_link_under_the_roster_itself(admin_user, roster_class, client):
            client.force_login(admin_user)
            url = reverse("classes:teach_class_registrations", kwargs={"pk": roster_class.pk})
            assert client.get(url).content.decode().count(_compose_href(roster_class)) == 2

        def it_drops_the_roster_foot_link_when_nobody_is_registered(admin_user, roster_class, client):
            # Guards the fixture: without a registrant the second template renders nothing, and
            # the counts above would stop distinguishing the two templates.
            roster_class.registrations.all().delete()
            client.force_login(admin_user)
            url = reverse("classes:teach_class_registrations", kwargs={"pk": roster_class.pk})
            assert client.get(url).content.decode().count(_compose_href(roster_class)) == 1


def describe_a_refused_composer_says_why():
    def describe_the_page_entry():
        def it_still_sends_a_refused_viewer_to_the_propose_flow(member_user, roster_class, client):
            client.force_login(member_user)
            response = client.get(_compose_url(roster_class))
            assert response.status_code == 302
            assert response.url == reverse("hub_guild_announcement_propose")

        def it_hands_that_reason_across_a_boosted_redirect(member_user, roster_class, client):
            # hx-boost swaps the destination's body, and the message-bearing render is not
            # reliably the one swapped in. ToastFlashMiddleware moves it to a cookie for
            # exactly this case, so the cookie is what proves the reason is actually visible.
            client.force_login(member_user)
            response = client.get(_compose_url(roster_class), HTTP_HX_REQUEST="true")
            assert response.cookies.get("pl_toast") is not None

        def it_points_a_previewing_admin_at_the_switcher(admin_user, roster_class, client):
            # A previewing admin is not short of rights; the role they are looking through is.
            # Saying otherwise would be the same lie this ticket exists to remove. Each of these
            # three assertions fails if the OTHER branch's sentence came back, which is what
            # makes this a test of the branch rather than of the string.
            client.force_login(admin_user)
            _preview_as(client, "member")
            said = _refusal_said(client, roster_class)
            assert "Viewing as switcher is set to Member" in said
            assert "Switch it back to Admin" in said
            assert GENERIC_REFUSAL not in said

        def it_points_everyone_else_at_the_propose_flow(member_user, roster_class, client):
            # The other branch, pinned against the same pair: a plain member is never told to go
            # and change a switcher they do not have.
            client.force_login(member_user)
            said = _refusal_said(client, roster_class)
            assert said == GENERIC_REFUSAL
            assert "Viewing as" not in said

    def describe_the_htmx_post_paths():
        @pytest.mark.parametrize("route", ["hub_compose_preview", "hub_compose_test", "hub_compose_push_test"])
        def it_refuses_with_a_toast_instead_of_an_unexplained_403(route, member_user, roster_class, client):
            client.force_login(member_user)
            response = client.post(reverse(route), {"audience": f"class:{roster_class.pk}"})
            assert response.status_code == 403
            assert "showToast" in json.loads(response["HX-Trigger"])
