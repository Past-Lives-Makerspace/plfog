"""BDD specs for the Send Email link on the per-class screen, and the composer behind it.

Issue #371 item 1, in both of its halves: #414 stopped the composer refusing in silence, and this
one made the Emails tab a guild lead lands on actually send. Three things are pinned here.

**The link never lies.** Wherever Send Email renders, the composer it points at admits that same
request for that same class. Covered in two halves rather than as one implication over all five
roles, because an implication has nothing to say about a role that is offered no link: the roles
that ARE offered it assert that the composer takes them, and the roles that are not assert both
that the composer would have refused and which status each tab answered to reach its zero. A
single `offered implies admitted` test would have passed on three of five rows without ever
asking the composer anything.

**The link and the Send are one predicate, not two that agree.** ``_can_announce_to_class``
delegates to ``classes.access.class_access`` and reads the same ``can_send_email`` the template
reads, so :data:`ROLE_MATRIX` judges twelve roles on both halves at once and
``describe_the_send_gate_reads_the_capability_rather_than_re_deriving_it`` pins that the gate
reads the capability rather than reproducing its answers. Before this, the two expressions agreed
only because ``classes/access.py`` was the stricter of them, which #414's review called "not by
construction".

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
from dataclasses import replace

import pytest
from django.urls import reverse
from django.utils import timezone

from classes import access as access_module
from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassOffering, Registration
from membership.models import AdminCapability, AnnouncementDraft, Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MembershipPlanFactory

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
#: not teach, and since #371 item 1 that one open tab carries the link. It was ``(200, 0)`` until
#: then, which is the defect the issue was opened about — the only tab this population lands on
#: was the only thing they could not use.
GUILD_SCREEN = {
    "classes:teach_class_detail": (404, 0),
    "classes:teach_class_registrations": (404, 0),
    "classes:teach_class_waitlist": (404, 0),
    "classes:teach_class_discount_codes": (404, 0),
    "classes:teach_class_emails": (200, 1),
}

#: A ``CLASS_APPROVER`` holder with no other claim on this class: the Overview and nothing else.
#: The grant's contract is approve/validate, so it opens no mailbox and no composer, and
#: :func:`classes.access._with_reviewer_grant` must never be a way to acquire one.
REVIEWER_SCREEN = {
    "classes:teach_class_detail": (200, 0),
    "classes:teach_class_registrations": (404, 0),
    "classes:teach_class_waitlist": (404, 0),
    "classes:teach_class_discount_codes": (404, 0),
    "classes:teach_class_emails": (404, 0),
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

        def it_admits_a_guild_lead_who_does_not_teach_the_class(guild_lead, roster_class, client):
            # #371 item 1 reversed this row, and the comment that used to stand here with it.
            # The old reading was that withholding the link was correct because
            # _can_announce_to_class refused the Send, so offering it would have rebuilt the
            # #371 defect. The premise was true and the conclusion was backwards: the composer
            # PAGE already opened for this population (_can_enter_compose admits them on their
            # staffed guild) and only the Send refused, so the surface was shown and the action
            # refused either way. Ruling 12 leaves a lead no Overview on a class they do not
            # teach, which makes Emails the tab they land on, and it was the one thing they
            # could not use. Jo's call is to let them send, with the composer's per-person
            # picker. The two halves are now one predicate rather than two that agree, so the
            # link is offered here because the Send is accepted here.
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


# --- The role matrix, and the one predicate behind both halves --------------------------------

#: A Send that actually went out: the full-page POST redirects back to the composer, and the
#: draft it wrote is stamped ``sent_at``. The stamp is what tells a real send from the other 302s.
SEND_ACCEPTED = (302, 1)

#: The audience gate's refusal. ``_compose_audience_forbidden`` answers a bare 403 and writes
#: nothing, so no draft is stamped.
SEND_REFUSED = (403, 0)

#: ``hub_compose_send`` is ``@login_required``, so a logged-out POST redirects too — to the login
#: page, having sent nothing. Counting stamped drafts alongside the status is what keeps this
#: distinguishable from :data:`SEND_ACCEPTED`, and is why the outcome is a pair and not a status.
SEND_LOGIN_REDIRECT = (302, 0)

#: Every role this feature touches, against the two halves that have to stay in step: whether the
#: class screen offers the Send Email link on any tab, and what a real Send then does.
#:
#: **The roles are deliberately not one fixture wearing different hats.** PR #419 paid for that
#: lesson twice in one round — one ``capacity=4`` fixture hid a blocker on the last-seat path, and
#: one shared email address hid an unauthenticated account takeover. This feature is *about*
#: roles, so the role axis is varied on purpose and the refusals are carried in the same table as
#: the admissions, at the same weight. Four of these rows send and eight do not, and each of the
#: eight is refused for a different reason: a preview that closes the screen, a guild that is not
#: theirs, a grant whose contract is approve-and-nothing-else, a name on a class without the
#: teaching access behind it, no claim at all, and no session at all.
ROLE_MATRIX = {
    "admin": (True, SEND_ACCEPTED),
    "instructor": (True, SEND_ACCEPTED),
    "guild_lead": (True, SEND_ACCEPTED),
    "guild_staffer": (True, SEND_ACCEPTED),
    "admin_previewing_guild_officer": (False, SEND_REFUSED),
    "admin_previewing_member": (False, SEND_REFUSED),
    "admin_previewing_guest": (False, SEND_REFUSED),
    "other_guild_lead": (False, SEND_REFUSED),
    "reviewer": (False, SEND_REFUSED),
    "named_only_instructor": (False, SEND_REFUSED),
    "plain_member": (False, SEND_REFUSED),
    "logged_out": (False, SEND_LOGIN_REDIRECT),
}


def _link_is_offered(client, offering: ClassOffering) -> bool:
    """Whether the class screen renders Send Email on ANY tab this viewer can open.

    Asked across the whole strip rather than on one tab, because the roles hold different tabs:
    a guild lead lands on Emails, a reviewer holds only the Overview, and a previewing admin
    holds none. Probing a single tab would read "this role's tab is closed" as "the link was
    correctly withheld" on at least one row.
    """
    return any(count for _status, count in _screen_probe(client, offering).values())


def _send_outcome(client, offering: ClassOffering) -> tuple[int, int]:
    """Post a real Send at this class: ``(status code, drafts actually stamped sent)``."""
    response = client.post(
        reverse("hub_compose_send"),
        {
            "audience": f"class:{offering.pk}",
            "title": "Bring an apron",
            "body": "<p>Bring an apron on Saturday.</p>",
            "discord_channel": "",
            "mention": "none",
            "draft_pk": "",
        },
    )
    return response.status_code, AnnouncementDraft.objects.filter(sent_at__isnull=False).count()


@pytest.fixture
def sign_in(db, admin_user, member_user, instructor, guild_lead, roster_class, client):
    """Log ``client`` in as one row of :data:`ROLE_MATRIX`; returns the class that row is judged on.

    Every role gets its own member. ``named_only_instructor`` gets its own class too, because a
    class carries exactly one instructor and that row's whole point is a member named on one
    without the teaching access behind it.
    """

    def _fresh_member(username: str, name: str) -> Member:
        MembershipPlanFactory()
        member = Member.objects.get(user=UserFactory(username=username))
        member.full_legal_name = name
        member.save(update_fields=["full_legal_name"])
        return member

    def _sign_in(role: str) -> ClassOffering:
        if role == "logged_out":
            return roster_class
        if role == "admin":
            client.force_login(admin_user)
            return roster_class
        if role.startswith("admin_previewing_"):
            client.force_login(admin_user)
            _preview_as(client, role.removeprefix("admin_previewing_"))
            return roster_class
        if role == "instructor":
            client.force_login(instructor.user)
            return roster_class
        if role == "guild_lead":
            client.force_login(guild_lead.user)
            return roster_class
        if role == "plain_member":
            # Carries the teaching-orientation unlock and teaches nothing, so this row also
            # pins that the unlock on its own admits no one to anybody else's roster.
            client.force_login(member_user)
            return roster_class
        if role == "guild_staffer":
            # Ruling 23 gives a staffer the lead's reach, so this row must match guild_lead.
            # A lead-only fixture would leave the staff half of the population untested.
            staffer = _fresh_member("affordance-staffer@example.com", "Jules Behn")
            GuildStaffMembershipFactory(guild=roster_class.category.guild, member=staffer)
            client.force_login(staffer.user)
            return roster_class
        if role == "other_guild_lead":
            # Leading *a* guild is not leading *this* one. Without this row, "guild lead" and
            # "guild lead of this class's guild" would be indistinguishable in this file.
            other_lead = _fresh_member("affordance-other-lead@example.com", "Robin Vasquez")
            GuildFactory(name="Woodshop", guild_lead=other_lead)
            client.force_login(other_lead.user)
            return roster_class
        if role == "reviewer":
            reviewer = _fresh_member("affordance-reviewer@example.com", "Tam Reyes")
            reviewer.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
            client.force_login(reviewer.user)
            return roster_class
        assert role == "named_only_instructor", f"unknown role {role!r}"
        named_only = _fresh_member("affordance-named-only@example.com", "Sam Okafor")
        assert named_only.can_create_classes is False
        their_class = ClassOfferingFactory(
            title="Named Only",
            slug="affordance-named-only",
            instructor=named_only,
            category=CategoryFactory(name="Bookbinding", guild=None),
            status=Status.PUBLISHED,
            price_cents=4000,
        )
        RegistrationFactory(class_offering=their_class, status=Registration.Status.CONFIRMED)
        client.force_login(named_only.user)
        return their_class

    return _sign_in


def describe_the_link_and_the_send_are_one_predicate():
    """Criterion 6, in two parts: that the halves agree on every role, and that they agree *because*.

    The matrix is the first part. It is not the whole criterion on its own — a second expression
    that happens to agree passes a matrix exactly as well as a delegation does, which is the state
    #414's review found and named "not by construction". The block below it pins the mechanism.
    """

    @pytest.mark.parametrize("role", sorted(ROLE_MATRIX))
    def it_offers_the_link_exactly_where_the_send_goes_through(role, sign_in, client):
        offering = sign_in(role)
        offered = _link_is_offered(client, offering)
        outcome = _send_outcome(client, offering)
        # Each half against the table, so no row can drift without being named.
        assert (offered, outcome) == ROLE_MATRIX[role]
        # And the two measured halves against each other, which is the criterion itself, stated
        # about what the running system did rather than about what the table says.
        assert offered is (outcome == SEND_ACCEPTED)


def describe_the_send_gate_reads_the_capability_rather_than_re_deriving_it():
    """Criterion 6's teeth: move the capability, and BOTH halves have to move with it.

    ``classes/access.py`` is edited here and ``hub/views.py`` is not touched at all. Take
    ``can_send_email`` off the guild row and the link must vanish AND the Send must start
    refusing, because ``_can_announce_to_class`` delegates to ``class_access`` and reads that
    same attribute.

    **This is the half the role matrix structurally cannot reach**, and the distinction is worth
    being exact about rather than hand-waving. The matrix catches a gate that disagrees with the
    affordance on some role — reverting ``_can_announce_to_class`` to its pre-#371 "an effective
    admin, or the class's own instructor" turns three of its rows red. What the matrix cannot
    catch is a gate that agrees with the affordance today for a different reason: a gate reading
    ``access.role in {admin, instructor, guild}`` reproduces every row of the matrix exactly,
    including the refusals, and the matrix passes it whole. It fails only here, because the role
    survives the patch and the capability does not. That mutant was run against this file; the
    matrix scored twelve for twelve on it and
    :func:`it_withdraws_the_send_with_the_same_capability` was the only spec that went red.

    Which is the whole point of the criterion: the two halves have to be one predicate, not two
    that happen to agree, and only a test that moves the predicate can tell those apart.
    """

    @pytest.fixture
    def guild_row_without_the_composer(monkeypatch):
        original = access_module._guild_access
        monkeypatch.setattr(access_module, "_guild_access", lambda: replace(original(), can_send_email=False))

    def it_withdraws_the_link_with_the_capability(guild_row_without_the_composer, guild_lead, roster_class, client):
        client.force_login(guild_lead.user)
        assert _link_is_offered(client, roster_class) is False

    def it_withdraws_the_send_with_the_same_capability(
        guild_row_without_the_composer, guild_lead, roster_class, client
    ):
        client.force_login(guild_lead.user)
        assert _send_outcome(client, roster_class) == SEND_REFUSED

    def it_still_sends_without_the_patch(guild_lead, roster_class, client):
        # The control. Without it the two tests above would keep passing if the guild row lost
        # can_send_email for real, and this whole block would be asserting nothing.
        client.force_login(guild_lead.user)
        assert _send_outcome(client, roster_class) == SEND_ACCEPTED


def describe_a_guild_lead_gets_the_whole_composer():
    """Criterion 1's middle, which the matrix's booleans skip over."""

    def it_locks_the_composer_to_that_class(guild_lead, roster_class, client):
        client.force_login(guild_lead.user)
        content = client.get(_compose_url(roster_class)).content.decode()
        assert f"Email the registrants of {roster_class.title}" in content

    def it_lists_the_registrants_by_name_in_the_per_person_picker(guild_lead, roster_class, client):
        # The accepted consequence of the decision, asserted rather than left implicit. Ruling 12
        # still closes the Registrations tab to this population (can_view_registrations stays
        # False), but the composer's picker names them, so a guild lead who may address a class
        # can see who is in it. Jo was shown this and chose it. If that is ever revisited, this
        # spec is where the reversal has to argue with something concrete.
        client.force_login(guild_lead.user)
        content = client.get(_compose_url(roster_class)).content.decode()
        registration = roster_class.registrations.get()
        assert 'name="recipients"' in content
        assert f"{registration.first_name} {registration.last_name}" in content
        assert registration.email in content

    def it_keeps_the_roster_tab_shut(guild_lead, roster_class, client):
        # The other half of the same sentence: the picker is the ONLY place the names appear.
        client.force_login(guild_lead.user)
        url = reverse("classes:teach_class_registrations", kwargs={"pk": roster_class.pk})
        assert client.get(url).status_code == 404


def describe_the_populations_that_are_still_refused():
    """The refusals that the matrix reports as a pair, restated where each one's reason lives."""

    def it_refuses_a_lead_of_another_guild(sign_in, client):
        # Criterion 2. The screen closes outright, so there is no tab to hide a link on.
        offering = sign_in("other_guild_lead")
        assert _screen_probe(client, offering) == SCREEN_CLOSED
        assert _send_outcome(client, offering) == SEND_REFUSED

    def it_refuses_a_reviewer_grant_holder(sign_in, client):
        # Criterion 5. The grant unions the reviewer row into whatever set the holder already
        # had (ruling 25), and that row has never carried can_send_email. Holding CLASS_APPROVER
        # must not become a way to acquire the composer, which is the widening the union could
        # have introduced by accident once the guild row gained the capability.
        offering = sign_in("reviewer")
        assert _screen_probe(client, offering) == REVIEWER_SCREEN
        assert _send_outcome(client, offering) == SEND_REFUSED

    def it_refuses_a_member_merely_named_as_the_instructor(sign_in, client):
        # This one MOVED with #371 item 1, in the opposite direction to the guild lead, and it
        # is the delegation's stricter half. The old gate compared instructor_id alone, so a
        # member named on a class who was never granted teaching access could email its roster.
        # class_access's instructor leg has always also required can_create_classes
        # (classes/spec/access_spec.py, it_denies_a_named_instructor_who_was_never_granted_teaching),
        # so they already had no class screen at all and the composer was the last door open to
        # them. Reachable in production through Member.revoke_teaching, which clears the unlock
        # and leaves the class rows pointing at them.
        offering = sign_in("named_only_instructor")
        assert _screen_probe(client, offering) == SCREEN_CLOSED
        assert _send_outcome(client, offering) == SEND_REFUSED

    @pytest.mark.parametrize("role", ["admin_previewing_guild_officer", "admin_previewing_member"])
    def it_refuses_an_admin_previewing_a_lower_role(role, sign_in, client):
        # Criterion 4, unchanged: class_access is view-as aware, so the whole screen 404s and the
        # send is refused on the role being looked through, not on the account holding it.
        offering = sign_in(role)
        assert _screen_probe(client, offering) == SCREEN_CLOSED
        assert _send_outcome(client, offering) == SEND_REFUSED
