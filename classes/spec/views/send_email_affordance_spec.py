"""BDD specs for the Send Email link on the per-class screen, and the composer behind it.

Issue #371 item 1. Two separate things are pinned here.

**The link never lies.** Wherever Send Email renders, the composer it points at admits that
same request for that same class. Asserted as an implication over every view-as role an admin
can pick, so it holds for the whole matrix rather than for the rows somebody remembered.

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

#: Every role an admin can pick in the topbar, plus ``None`` for "not previewing at all".
PREVIEW_ROLES = (None, "admin", "guild_officer", "member", "guest")

#: Every tab of the per-class screen. The header link is judged on all of them, not only on the
#: one tab that also carries the roster-foot link.
CLASS_SCREEN_TABS = (
    "classes:teach_class_detail",
    "classes:teach_class_registrations",
    "classes:teach_class_waitlist",
    "classes:teach_class_discount_codes",
    "classes:teach_class_emails",
)

#: What a viewer who is offered the link everywhere sees: one header link per tab, plus the
#: roster-foot link on Registrations.
FULLY_OFFERED = {
    "classes:teach_class_detail": 1,
    "classes:teach_class_registrations": 2,
    "classes:teach_class_waitlist": 1,
    "classes:teach_class_discount_codes": 1,
    "classes:teach_class_emails": 1,
}

#: What a viewer who is offered it nowhere sees.
NEVER_OFFERED = dict.fromkeys(CLASS_SCREEN_TABS, 0)


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


def _links_offered(client, offering: ClassOffering) -> dict[str, int]:
    """How many Send Email links this viewer is offered, per tab.

    A tab that refuses the viewer outright offers zero, which is the answer the implication
    needs — a 404'd tab shows nobody a link.
    """
    counts = {}
    for tab in CLASS_SCREEN_TABS:
        response = client.get(reverse(tab, kwargs={"pk": offering.pk}))
        counts[tab] = response.content.decode().count(_compose_href(offering)) if response.status_code == 200 else 0
    return counts


def _preview_as(client, role: str | None) -> None:
    """The shipped session idiom. ``None`` clears the pick: an admin looking as themselves."""
    session = client.session
    if role is None:
        session.pop("view_as_role", None)
    else:
        session["view_as_role"] = role
    session.save()


def describe_the_send_email_link():
    def describe_wherever_it_renders_the_composer_admits_the_same_request():
        @pytest.mark.parametrize("preview", PREVIEW_ROLES)
        def it_holds_for_an_admin_in_every_view_as_role(preview, admin_user, roster_class, client):
            # The implication itself, which is the acceptance criterion. Asserting it rather
            # than a remembered matrix is what makes it survive a future role being added.
            client.force_login(admin_user)
            _preview_as(client, preview)
            offered = _links_offered(client, roster_class)
            admitted = client.get(_compose_url(roster_class)).status_code == 200
            assert admitted or not any(offered.values()), (
                f"view_as={preview} offered {offered} but the composer refused"
            )

        @pytest.mark.parametrize(
            ("preview", "expected_links", "expected_compose"),
            [
                (None, FULLY_OFFERED, 200),
                ("admin", FULLY_OFFERED, 200),
                ("guild_officer", NEVER_OFFERED, 302),
                ("member", NEVER_OFFERED, 302),
                ("guest", NEVER_OFFERED, 302),
            ],
        )
        def it_pins_the_matrix_behind_that_implication(
            preview, expected_links, expected_compose, admin_user, roster_class, client
        ):
            # The implication above passes vacuously if nothing ever renders. This is the other
            # half: the counts are what they should be, tab by tab and template by template.
            client.force_login(admin_user)
            _preview_as(client, preview)
            assert _links_offered(client, roster_class) == expected_links
            assert client.get(_compose_url(roster_class)).status_code == expected_compose

        def it_holds_for_the_classs_own_instructor(instructor, roster_class, client):
            client.force_login(instructor.user)
            assert _links_offered(client, roster_class) == FULLY_OFFERED
            assert client.get(_compose_url(roster_class)).status_code == 200

        def it_holds_for_a_guild_lead_who_does_not_teach_the_class(guild_lead, roster_class, client):
            # The safe direction: the guild set carries Emails but not the roster, so the link is
            # withheld whatever the composer would have said.
            client.force_login(guild_lead.user)
            assert _links_offered(client, roster_class) == NEVER_OFFERED

        @pytest.mark.parametrize("preview", ["member", "guest"])
        def it_is_the_whole_screen_that_closes_not_only_the_link(preview, admin_user, roster_class, client):
            # WHY the counts above are zero, pinned separately. `class_access` is view-as aware,
            # so a previewing admin gets no per-class screen at all. Without this, a change that
            # reopened the screen while leaving `can_send_email` alone would keep the counts at
            # zero for a different reason and the next regression would be invisible.
            client.force_login(admin_user)
            _preview_as(client, preview)
            for tab in CLASS_SCREEN_TABS:
                assert client.get(reverse(tab, kwargs={"pk": roster_class.pk})).status_code == 404, tab

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

        def it_carries_a_reason_the_destination_page_can_show(member_user, roster_class, client):
            client.force_login(member_user)
            response = client.get(_compose_url(roster_class), follow=True)
            assert [str(m) for m in response.context["messages"]] != []

        def it_hands_that_reason_across_a_boosted_redirect(member_user, roster_class, client):
            # hx-boost swaps the destination's body, and the message-bearing render is not
            # reliably the one swapped in. ToastFlashMiddleware moves it to a cookie for
            # exactly this case, so the cookie is what proves the reason is actually visible.
            client.force_login(member_user)
            response = client.get(_compose_url(roster_class), HTTP_HX_REQUEST="true")
            assert response.cookies.get("pl_toast") is not None

        def it_tells_a_previewing_admin_to_switch_back_rather_than_claiming_they_lack_rights(
            admin_user, roster_class, client
        ):
            # A previewing admin is not short of permission; the role they are looking through
            # is. Saying otherwise would be the same lie this ticket exists to remove.
            client.force_login(admin_user)
            _preview_as(client, "member")
            response = client.get(_compose_url(roster_class), follow=True)
            said = " ".join(str(m) for m in response.context["messages"])
            assert "Viewing as" in said
            assert "permission" not in said

    def describe_the_htmx_post_paths():
        @pytest.mark.parametrize("route", ["hub_compose_preview", "hub_compose_test", "hub_compose_push_test"])
        def it_refuses_with_a_toast_instead_of_an_unexplained_403(route, member_user, roster_class, client):
            client.force_login(member_user)
            response = client.post(reverse(route), {"audience": f"class:{roster_class.pk}"})
            assert response.status_code == 403
            assert "showToast" in json.loads(response["HX-Trigger"])
