"""BDD specs for the per-class access model — the whole capability matrix, leg by leg.

The gate in ``classes/access.py`` is first-match-wins and the order is load-bearing, so
these specs pin each leg AND the order between them: the guest leg before the model-reading
ones, the reviewer leg before the instructor and guild legs. The four capability sets are
asserted one ``it_*`` per population per capability, because a matrix that is asserted in
bulk drifts a row at a time without anything going red.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import Http404, HttpResponse
from django.test import RequestFactory
from django.utils import timezone

from classes.access import (
    ADMIN_SHELL,
    ROLE_ADMIN,
    ROLE_GUILD,
    ROLE_INSTRUCTOR,
    ROLE_REVIEWER,
    TAB_EMAILS,
    TAB_OVERVIEW,
    TEACH_SHELL,
    ClassAccess,
    class_access,
    class_screen_required,
)
from classes.factories import CategoryFactory, ClassOfferingFactory, UserFactory
from classes.models import ClassOffering
from core.models import SiteConfiguration
from hub.view_as import ROLE_GUEST, ROLE_MEMBER, ViewAs, compute_actual_roles
from membership.models import AdminCapability, Member
from membership.permissions import can_edit_class
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory

CLASS_APPROVER = AdminCapability.Capability.CLASS_APPROVER


def _user_with_member(**fields):
    """A fresh user plus its auto-created Member, with ``fields`` applied and saved."""
    user = UserFactory()
    member = user.member
    for name, value in fields.items():
        setattr(member, name, value)
    member.save()
    return user, member


def _teaching_member():
    """A member who has been granted teaching access (``can_create_classes``)."""
    return _user_with_member(instructor_oriented_at=timezone.now())


def _request(user, *, picked=None):
    """A request carrying the same ``view_as`` the middleware would attach."""
    request = RequestFactory().get("/")
    request.user = user
    request.view_as = ViewAs(actual=compute_actual_roles(user), picked=picked)
    return request


def _guild_class(member, *, staff=False):
    """A class under a category owned by a guild this member leads (or staffs)."""
    if staff:
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=member)
    else:
        guild = GuildFactory(guild_lead=member)
    return ClassOfferingFactory(category=CategoryFactory(guild=guild))


def _access_or_fail(request, offering):
    access = class_access(request, offering)
    assert access is not None, "expected this population to reach the class screen"
    return access


@pytest.fixture
def admin_access(db) -> ClassAccess:
    user, _member = _user_with_member(fog_role=Member.FogRole.ADMIN)
    return _access_or_fail(_request(user), ClassOfferingFactory())


@pytest.fixture
def reviewer_access(db) -> ClassAccess:
    user, member = _user_with_member()
    member.admin_capabilities.create(capability=CLASS_APPROVER)
    return _access_or_fail(_request(user), ClassOfferingFactory())


@pytest.fixture
def instructor_access(db) -> ClassAccess:
    user, member = _teaching_member()
    return _access_or_fail(_request(user), ClassOfferingFactory(instructor=member))


@pytest.fixture
def guild_access(db) -> ClassAccess:
    user, member = _user_with_member()
    return _access_or_fail(_request(user), _guild_class(member))


def describe_the_admin_capability_set():
    def it_is_the_admin_role(admin_access):
        assert admin_access.role == ROLE_ADMIN

    def it_renders_in_the_admin_shell(admin_access):
        assert admin_access.shell == ADMIN_SHELL

    def it_lands_on_overview(admin_access):
        assert admin_access.landing_tab == TAB_OVERVIEW

    def it_can_view_overview(admin_access):
        assert admin_access.can_view_overview is True

    def it_can_view_registrations(admin_access):
        assert admin_access.can_view_registrations is True

    def it_can_view_the_waitlist(admin_access):
        assert admin_access.can_view_waitlist is True

    def it_can_view_discount_codes(admin_access):
        assert admin_access.can_view_discount_codes is True

    def it_can_view_emails(admin_access):
        assert admin_access.can_view_emails is True

    def it_can_edit(admin_access):
        assert admin_access.can_edit is True

    def it_can_approve(admin_access):
        assert admin_access.can_approve is True

    def it_can_administer(admin_access):
        assert admin_access.can_administer is True

    def it_cannot_submit(admin_access):
        # Submitting and withdrawing are the instructor's moves; an admin publishes instead.
        assert admin_access.can_submit is False

    def it_can_cancel(admin_access):
        assert admin_access.can_cancel is True

    def it_can_put_the_class_on_sale(admin_access):
        assert admin_access.can_sale is True

    def it_can_send_email(admin_access):
        assert admin_access.can_send_email is True

    def it_shows_every_tab_in_order(admin_access):
        assert [tab.key for tab in admin_access.tabs] == [
            "overview",
            "registrations",
            "waitlist",
            "discount_codes",
            "emails",
        ]


def describe_the_reviewer_capability_set():
    def it_is_the_reviewer_role(reviewer_access):
        assert reviewer_access.role == ROLE_REVIEWER

    def it_renders_in_the_admin_shell(reviewer_access):
        assert reviewer_access.shell == ADMIN_SHELL

    def it_lands_on_overview(reviewer_access):
        assert reviewer_access.landing_tab == TAB_OVERVIEW

    def it_can_view_overview(reviewer_access):
        assert reviewer_access.can_view_overview is True

    def it_cannot_view_registrations(reviewer_access):
        assert reviewer_access.can_view_registrations is False

    def it_cannot_view_the_waitlist(reviewer_access):
        assert reviewer_access.can_view_waitlist is False

    def it_cannot_view_discount_codes(reviewer_access):
        assert reviewer_access.can_view_discount_codes is False

    def it_cannot_view_emails(reviewer_access):
        assert reviewer_access.can_view_emails is False

    def it_cannot_edit(reviewer_access):
        assert reviewer_access.can_edit is False

    def it_can_approve(reviewer_access):
        assert reviewer_access.can_approve is True

    def it_cannot_administer(reviewer_access):
        assert reviewer_access.can_administer is False

    def it_cannot_submit(reviewer_access):
        assert reviewer_access.can_submit is False

    def it_cannot_cancel(reviewer_access):
        assert reviewer_access.can_cancel is False

    def it_cannot_put_the_class_on_sale(reviewer_access):
        assert reviewer_access.can_sale is False

    def it_cannot_send_email(reviewer_access):
        assert reviewer_access.can_send_email is False

    def it_shows_only_the_overview_tab(reviewer_access):
        # Criterion 5: the grant's contract is approve/validate, so it opens no other tab.
        assert [tab.key for tab in reviewer_access.tabs] == ["overview"]


def describe_the_instructor_capability_set():
    def it_is_the_instructor_role(instructor_access):
        assert instructor_access.role == ROLE_INSTRUCTOR

    def it_renders_in_the_teaching_shell(instructor_access):
        assert instructor_access.shell == TEACH_SHELL

    def it_lands_on_overview(instructor_access):
        assert instructor_access.landing_tab == TAB_OVERVIEW

    def it_can_view_overview(instructor_access):
        assert instructor_access.can_view_overview is True

    def it_can_view_registrations(instructor_access):
        assert instructor_access.can_view_registrations is True

    def it_can_view_the_waitlist(instructor_access):
        assert instructor_access.can_view_waitlist is True

    def it_can_view_discount_codes_while_the_site_flag_is_on(instructor_access):
        # The classes/spec conftest switches instructor self-service codes on.
        assert instructor_access.can_view_discount_codes is True

    def it_can_view_emails(instructor_access):
        assert instructor_access.can_view_emails is True

    def it_can_edit(instructor_access):
        assert instructor_access.can_edit is True

    def it_cannot_approve(instructor_access):
        assert instructor_access.can_approve is False

    def it_cannot_administer(instructor_access):
        assert instructor_access.can_administer is False

    def it_can_submit(instructor_access):
        assert instructor_access.can_submit is True

    def it_can_cancel(instructor_access):
        assert instructor_access.can_cancel is True

    def it_can_put_the_class_on_sale(instructor_access):
        assert instructor_access.can_sale is True

    def it_can_send_email(instructor_access):
        assert instructor_access.can_send_email is True

    def it_shows_every_tab_in_order_while_the_flag_is_on(instructor_access):
        assert [tab.key for tab in instructor_access.tabs] == [
            "overview",
            "registrations",
            "waitlist",
            "discount_codes",
            "emails",
        ]

    def describe_when_instructor_discount_codes_are_switched_off():
        def it_hides_the_discount_codes_capability(db):
            config = SiteConfiguration.load()
            config.instructor_discount_codes_enabled = False
            config.save(update_fields=["instructor_discount_codes_enabled"])
            user, member = _teaching_member()
            access = _access_or_fail(_request(user), ClassOfferingFactory(instructor=member))
            assert access.can_view_discount_codes is False

        def it_drops_the_discount_codes_tab_from_the_strip(db):
            config = SiteConfiguration.load()
            config.instructor_discount_codes_enabled = False
            config.save(update_fields=["instructor_discount_codes_enabled"])
            user, member = _teaching_member()
            access = _access_or_fail(_request(user), ClassOfferingFactory(instructor=member))
            assert [tab.key for tab in access.tabs] == ["overview", "registrations", "waitlist", "emails"]


def describe_the_guild_capability_set():
    def it_is_the_guild_role(guild_access):
        assert guild_access.role == ROLE_GUILD

    def it_renders_in_the_teaching_shell(guild_access):
        assert guild_access.shell == TEACH_SHELL

    def it_lands_on_emails(guild_access):
        # Ruling 12: no Overview on a class they do not teach, so Emails is the landing tab.
        assert guild_access.landing_tab == TAB_EMAILS

    def it_cannot_view_overview(guild_access):
        assert guild_access.can_view_overview is False

    def it_cannot_view_registrations(guild_access):
        assert guild_access.can_view_registrations is False

    def it_cannot_view_the_waitlist(guild_access):
        assert guild_access.can_view_waitlist is False

    def it_cannot_view_discount_codes(guild_access):
        assert guild_access.can_view_discount_codes is False

    def it_can_view_emails(guild_access):
        assert guild_access.can_view_emails is True

    def it_can_edit(guild_access):
        assert guild_access.can_edit is True

    def it_cannot_approve(guild_access):
        assert guild_access.can_approve is False

    def it_cannot_administer(guild_access):
        assert guild_access.can_administer is False

    def it_cannot_submit(guild_access):
        assert guild_access.can_submit is False

    def it_cannot_cancel(guild_access):
        assert guild_access.can_cancel is False

    def it_cannot_put_the_class_on_sale(guild_access):
        assert guild_access.can_sale is False

    def it_cannot_send_email(guild_access):
        assert guild_access.can_send_email is False

    def it_shows_only_the_emails_tab(guild_access):
        assert [tab.key for tab in guild_access.tabs] == ["emails"]


def describe_guild_authority_without_teaching_access():
    """Criterion 3 and ruling 23 — Edit and Emails, and nothing else."""

    def it_reaches_edit_and_emails_as_a_guild_lead(db):
        user, member = _user_with_member()
        assert member.can_create_classes is False
        access = _access_or_fail(_request(user), _guild_class(member))
        assert access.role == ROLE_GUILD
        assert (access.can_edit, access.can_view_emails) == (True, True)

    def it_reaches_edit_and_emails_as_guild_staff(db):
        # Ruling 23: staff get the same reach as the lead.
        user, member = _user_with_member()
        access = _access_or_fail(_request(user), _guild_class(member, staff=True))
        assert access.role == ROLE_GUILD
        assert (access.can_edit, access.can_view_emails) == (True, True)

    def it_still_admits_a_guild_lead_who_also_holds_the_teaching_grant(db):
        # The waiver is an OR, not a replacement: holding the grant still admits.
        user, member = _teaching_member()
        access = _access_or_fail(_request(user), _guild_class(member))
        assert access.role == ROLE_GUILD

    def describe_and_nobody_else_ruling_6():
        """The waiver is scoped to ruling 23's population; ``can_edit_class`` is broader."""

        def it_denies_a_site_wide_guild_officer_on_a_class_outside_their_guilds(db):
            # can_edit_class short-circuits on is_effective_staff, so it says yes here —
            # but a fog guild officer with no teaching grant is not ruling 23's population,
            # and PLAN.md §2 named exactly them as the reason the precondition exists.
            user, member = _user_with_member(fog_role=Member.FogRole.GUILD_OFFICER)
            assert member.can_create_classes is False
            offering = ClassOfferingFactory(category=CategoryFactory(guild=GuildFactory()))
            assert can_edit_class(_request(user), offering) is True
            assert class_access(_request(user), offering) is None

        def it_still_admits_a_site_wide_guild_officer_who_holds_the_teaching_grant(db):
            # Unchanged from before this ticket: the precondition is what they satisfy.
            user, _member = _user_with_member(
                fog_role=Member.FogRole.GUILD_OFFICER, instructor_oriented_at=timezone.now()
            )
            offering = ClassOfferingFactory(category=CategoryFactory(guild=GuildFactory()))
            assert _access_or_fail(_request(user), offering).role == ROLE_GUILD

        def it_denies_a_named_instructor_who_was_never_granted_teaching(db):
            # can_edit_class's last clause is the instructor check, so it says yes; leg 3
            # refuses them for want of the grant, and the waiver does not cover them.
            user, member = _user_with_member()
            assert member.can_create_classes is False
            offering = ClassOfferingFactory(category=CategoryFactory(guild=None), instructor=member)
            assert can_edit_class(_request(user), offering) is True
            assert class_access(_request(user), offering) is None

    def it_denies_every_other_capability_to_a_guild_lead(db):
        user, member = _user_with_member()
        access = _access_or_fail(_request(user), _guild_class(member))
        assert access.can_view_overview is False
        assert access.can_view_registrations is False
        assert access.can_view_waitlist is False
        assert access.can_view_discount_codes is False
        assert access.can_cancel is False
        assert access.can_sale is False

    def it_denies_a_lead_of_some_other_guild(db):
        user, member = _user_with_member()
        GuildFactory(guild_lead=member)
        other = ClassOfferingFactory(category=CategoryFactory(guild=GuildFactory()))
        assert class_access(_request(user), other) is None


def describe_a_guild_lead_who_is_also_the_instructor():
    """Criterion 4 — the instructor leg beats the guild leg."""

    def it_gets_the_instructor_role(db):
        user, member = _teaching_member()
        offering = _guild_class(member)
        offering.instructor = member
        offering.save(update_fields=["instructor"])
        assert _access_or_fail(_request(user), offering).role == ROLE_INSTRUCTOR

    def it_gets_the_full_instructor_set_not_the_guild_one(db):
        user, member = _teaching_member()
        offering = _guild_class(member)
        offering.instructor = member
        offering.save(update_fields=["instructor"])
        access = _access_or_fail(_request(user), offering)
        assert access.can_view_overview is True
        assert access.can_view_registrations is True
        assert access.can_submit is True
        assert access.landing_tab == TAB_OVERVIEW


def describe_a_class_approver_who_is_not_an_admin():
    """Criterion 5."""

    def it_gets_the_reviewer_role(db):
        user, member = _user_with_member()
        member.admin_capabilities.create(capability=CLASS_APPROVER)
        assert _access_or_fail(_request(user), ClassOfferingFactory()).role == ROLE_REVIEWER

    def it_gets_overview_and_no_other_tab(db):
        user, member = _user_with_member()
        member.admin_capabilities.create(capability=CLASS_APPROVER)
        access = _access_or_fail(_request(user), ClassOfferingFactory())
        assert [tab.key for tab in access.tabs] == ["overview"]


def describe_a_request_that_matches_no_leg():
    """Criteria 6 and 7."""

    def it_denies_a_plain_member(db):
        user, _member = _user_with_member()
        assert class_access(_request(user), ClassOfferingFactory()) is None

    def it_denies_a_member_without_teaching_access_who_is_neither_admin_nor_approver(db):
        # No teaching grant, no capability, no guild authority and not the instructor:
        # every leg's precondition fails.
        user, member = _user_with_member()
        assert member.can_create_classes is False
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None))
        assert class_access(_request(user), offering) is None

    def it_denies_an_authenticated_user_with_no_linked_member(db):
        from django.contrib.auth import get_user_model

        user = UserFactory()
        Member.objects.filter(user=user).delete()
        # Re-fetch so the reverse accessor is not serving the cached, now-deleted row.
        request = RequestFactory().get("/")
        request.user = get_user_model().objects.get(pk=user.pk)
        request.view_as = ViewAs(actual=frozenset({ROLE_MEMBER}), picked=None)
        assert class_access(request, ClassOfferingFactory()) is None

    def it_denies_a_request_the_middleware_never_touched(db):
        # No view_as means no established viewer, which is the same answer as Guest.
        user, _member = _user_with_member(fog_role=Member.FogRole.ADMIN)
        request = RequestFactory().get("/")
        request.user = user
        assert class_access(request, ClassOfferingFactory()) is None

    def it_denies_an_anonymous_request(db):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        request.view_as = ViewAs(actual=frozenset({ROLE_GUEST}), picked=None)
        assert class_access(request, ClassOfferingFactory()) is None


def describe_an_admin_with_no_teaching_grant():
    """Criterion 8 — the admin leg is independent of ``can_create_classes``."""

    def it_still_gets_every_admin_capability(db):
        user, member = _user_with_member(fog_role=Member.FogRole.ADMIN)
        assert member.can_create_classes is False
        access = _access_or_fail(_request(user), ClassOfferingFactory())
        assert access.role == ROLE_ADMIN
        # Every capability the admin row carries. can_submit is the one deliberate False:
        # submitting is the instructor's move, and an admin publishes instead.
        assert access.can_view_overview is True
        assert access.can_view_registrations is True
        assert access.can_view_waitlist is True
        assert access.can_view_discount_codes is True
        assert access.can_view_emails is True
        assert access.can_edit is True
        assert access.can_approve is True
        assert access.can_administer is True
        assert access.can_cancel is True
        assert access.can_sale is True
        assert access.can_send_email is True
        assert access.can_submit is False


def describe_an_admin_previewing_as_member():
    """Criterion 12 — they see what that member sees, class by class."""

    def it_is_denied_on_a_class_they_neither_instruct_nor_hold_guild_authority_over(db):
        user, _member = _user_with_member(fog_role=Member.FogRole.ADMIN)
        assert class_access(_request(user, picked=ROLE_MEMBER), ClassOfferingFactory()) is None

    def it_is_the_instructor_on_a_class_they_instruct(db):
        user, member = _user_with_member(fog_role=Member.FogRole.ADMIN, instructor_oriented_at=timezone.now())
        offering = ClassOfferingFactory(instructor=member)
        assert _access_or_fail(_request(user, picked=ROLE_MEMBER), offering).role == ROLE_INSTRUCTOR

    def it_is_the_guild_set_on_a_class_in_a_guild_they_lead(db):
        user, member = _user_with_member(fog_role=Member.FogRole.ADMIN)
        offering = _guild_class(member)
        assert _access_or_fail(_request(user, picked=ROLE_MEMBER), offering).role == ROLE_GUILD

    def it_is_the_guild_set_on_a_class_in_a_guild_they_staff(db):
        user, member = _user_with_member(fog_role=Member.FogRole.ADMIN)
        offering = _guild_class(member, staff=True)
        assert _access_or_fail(_request(user, picked=ROLE_MEMBER), offering).role == ROLE_GUILD


def describe_an_admin_previewing_as_guest():
    """Criterion 13 — leg 0, and it comes first for exactly this reason."""

    def it_is_denied_on_an_ordinary_class(db):
        user, _member = _user_with_member(fog_role=Member.FogRole.ADMIN)
        assert class_access(_request(user, picked=ROLE_GUEST), ClassOfferingFactory()) is None

    def it_is_denied_even_on_a_class_they_instruct(db):
        user, member = _user_with_member(fog_role=Member.FogRole.ADMIN, instructor_oriented_at=timezone.now())
        offering = ClassOfferingFactory(instructor=member)
        # instructor_id reads the model, not the preview, so without leg 0 this would pass.
        assert class_access(_request(user, picked=ROLE_GUEST), offering) is None

    def it_is_denied_even_on_a_class_in_a_guild_they_lead(db):
        user, member = _user_with_member(fog_role=Member.FogRole.ADMIN)
        assert class_access(_request(user, picked=ROLE_GUEST), _guild_class(member)) is None


def describe_a_guild_officer_who_downgrades_to_member():
    """Criterion 14 — a non-admin loses a CLASS_APPROVER Overview while the pick stands."""

    def it_is_the_reviewer_while_viewing_as_themselves(db):
        user, member = _user_with_member(fog_role=Member.FogRole.GUILD_OFFICER)
        member.admin_capabilities.create(capability=CLASS_APPROVER)
        assert _access_or_fail(_request(user), ClassOfferingFactory()).role == ROLE_REVIEWER

    def it_loses_the_reviewer_set_while_the_member_pick_stands(db):
        user, member = _user_with_member(fog_role=Member.FogRole.GUILD_OFFICER)
        member.admin_capabilities.create(capability=CLASS_APPROVER)
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None))
        # They picked Member, so they see Member: no reviewer Overview, and no guild leg
        # either, because is_effective_staff reads the effective role.
        assert class_access(_request(user, picked=ROLE_MEMBER), offering) is None

    def it_regains_the_reviewer_set_on_switching_back(db):
        user, member = _user_with_member(fog_role=Member.FogRole.GUILD_OFFICER)
        member.admin_capabilities.create(capability=CLASS_APPROVER)
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None))
        assert class_access(_request(user, picked=ROLE_MEMBER), offering) is None
        assert _access_or_fail(_request(user), offering).role == ROLE_REVIEWER


def describe_the_existing_edit_rules_are_untouched():
    """Criterion 16 — asserted as behaviour, not by reading the source."""

    def it_still_lets_a_guild_lead_edit_their_guilds_class(db):
        user, member = _user_with_member()
        offering = _guild_class(member)
        assert can_edit_class(_request(user), offering) is True
        assert list(ClassOffering.objects.editable_by(member)) == [offering]

    def it_still_refuses_a_plain_member(db):
        user, member = _user_with_member()
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None))
        assert can_edit_class(_request(user), offering) is False
        assert list(ClassOffering.objects.editable_by(member)) == []

    def it_still_returns_every_class_for_an_admin(db):
        _user, member = _user_with_member(fog_role=Member.FogRole.ADMIN)
        ClassOfferingFactory()
        ClassOfferingFactory()
        assert ClassOffering.objects.editable_by(member).count() == ClassOffering.objects.count()


def describe_where_class_access_and_can_edit_class_disagree():
    """Criterion 17. Two populations, and the divergence only ever runs one way.

    Nothing gets ``can_edit`` that ``can_edit_class`` refuses: leg 4 asks it outright, leg 3
    implies it (the instructor clause) and leg 1 implies it (``is_effective_staff``). So the
    whole of the disagreement is "``can_edit_class`` says yes, the screen says no", and it
    has exactly two causes.

    **The reviewer leg shadowing the legs below it.** A non-admin ``CLASS_APPROVER`` holder
    who is ALSO the instructor or guild lead/staff gets the reviewer set, which has no
    ``can_edit``. Intended: a reviewer opening a class is reviewing it. New in this ticket.

    **The teaching-grant precondition on leg 4.** ``can_edit_class`` is broader than ruling
    23's population — it short-circuits on ``is_effective_staff`` (every site-wide guild
    officer, on every class) and ends on the instructor check (anyone merely named
    instructor). Ruling 23 waives the precondition for guild leads and staff and for nobody
    else, so those two are denied the screen entirely while ``can_edit_class`` still says
    yes. NOT new: that gap is the teaching portal's own gate
    (``teaching_member_required``), carried forward unchanged, which is what ruling 6 asks
    for. Pinned in ``describe_and_nobody_else_ruling_6`` above.

    Ruling 23's own population — a guild lead or staffer with no teaching grant — used to
    sit in the second bucket and no longer does: they now get ``can_edit`` from both.
    """

    def it_gives_an_instructor_who_holds_class_approver_the_reviewer_set(db):
        user, member = _teaching_member()
        member.admin_capabilities.create(capability=CLASS_APPROVER)
        offering = ClassOfferingFactory(instructor=member)
        access = _access_or_fail(_request(user), offering)
        assert access.role == ROLE_REVIEWER
        assert access.can_edit is False
        assert can_edit_class(_request(user), offering) is True

    def it_gives_a_guild_lead_who_holds_class_approver_the_reviewer_set(db):
        user, member = _user_with_member()
        member.admin_capabilities.create(capability=CLASS_APPROVER)
        offering = _guild_class(member)
        access = _access_or_fail(_request(user), offering)
        assert access.role == ROLE_REVIEWER
        assert access.can_edit is False
        assert can_edit_class(_request(user), offering) is True

    def it_agrees_again_once_the_holder_previews_a_lower_role(db):
        # Previewing suppresses the reviewer leg, so the guild leg is reached and the two
        # answers line up: both say this person may edit.
        user, member = _user_with_member(fog_role=Member.FogRole.GUILD_OFFICER)
        member.admin_capabilities.create(capability=CLASS_APPROVER)
        offering = _guild_class(member)
        request = _request(user, picked=ROLE_MEMBER)
        assert _access_or_fail(request, offering).can_edit is True
        assert can_edit_class(request, offering) is True

    def it_never_grants_edit_to_anyone_can_edit_class_refuses(db):
        # The divergence is one-directional: every leg that sets can_edit either asks
        # can_edit_class or implies it, so the screen can be narrower but never wider.
        user, member = _teaching_member()
        own = ClassOfferingFactory(instructor=member)
        led = _guild_class(member)
        stranger = ClassOfferingFactory(category=CategoryFactory(guild=None))
        assert _access_or_fail(_request(user), own).can_edit is True
        assert can_edit_class(_request(user), own) is True
        assert _access_or_fail(_request(user), led).can_edit is True
        assert can_edit_class(_request(user), led) is True
        assert class_access(_request(user), stranger) is None
        assert can_edit_class(_request(user), stranger) is False


def describe_a_class_whose_category_has_no_guild():
    """Criterion 44 — production carries 86 of these; every leg must answer, none may raise."""

    def it_denies_a_guest_preview(db):
        user, _member = _user_with_member(fog_role=Member.FogRole.ADMIN)
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None))
        assert class_access(_request(user, picked=ROLE_GUEST), offering) is None

    def it_gives_an_admin_the_admin_set(db):
        user, _member = _user_with_member(fog_role=Member.FogRole.ADMIN)
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None))
        assert _access_or_fail(_request(user), offering).role == ROLE_ADMIN

    def it_gives_a_class_approver_the_reviewer_set(db):
        user, member = _user_with_member()
        member.admin_capabilities.create(capability=CLASS_APPROVER)
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None))
        assert _access_or_fail(_request(user), offering).role == ROLE_REVIEWER

    def it_gives_the_instructor_the_instructor_set(db):
        user, member = _teaching_member()
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None), instructor=member)
        assert _access_or_fail(_request(user), offering).role == ROLE_INSTRUCTOR

    def it_gives_a_fog_guild_officer_with_teaching_access_the_guild_set(db):
        # The guild leg reached through is_effective_staff rather than through a guild FK,
        # which is the only way to reach leg 4 at all on a guild-less category. The waiver
        # runs on the way past and must not raise on the NULL guild.
        user, _member = _user_with_member(fog_role=Member.FogRole.GUILD_OFFICER, instructor_oriented_at=timezone.now())
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None))
        assert _access_or_fail(_request(user), offering).role == ROLE_GUILD

    def it_denies_a_fog_guild_officer_without_teaching_access(db):
        # Ruling 6: the waiver cannot cover them, and a NULL guild is exactly where
        # _leads_or_staffs would raise if it read guild.guild_lead_id unguarded.
        user, _member = _user_with_member(fog_role=Member.FogRole.GUILD_OFFICER)
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None))
        assert class_access(_request(user), offering) is None

    def it_denies_a_named_instructor_without_teaching_access(db):
        user, member = _user_with_member()
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None), instructor=member)
        assert class_access(_request(user), offering) is None

    def it_denies_a_plain_member(db):
        user, _member = _user_with_member()
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None))
        assert class_access(_request(user), offering) is None


def describe_class_screen_required():
    @class_screen_required
    def _view(request, pk):
        return HttpResponse(f"{request.class_access.role}:{request.class_offering.pk}")

    def it_attaches_the_access_and_the_offering(db):
        user, _member = _user_with_member(fog_role=Member.FogRole.ADMIN)
        offering = ClassOfferingFactory()
        response = _view(_request(user), pk=offering.pk)
        assert response.content.decode() == f"{ROLE_ADMIN}:{offering.pk}"

    def it_404s_when_no_leg_matches(db):
        user, _member = _user_with_member()
        offering = ClassOfferingFactory()
        with pytest.raises(Http404):
            _view(_request(user), pk=offering.pk)

    def it_404s_for_a_class_that_does_not_exist(db):
        user, _member = _user_with_member(fog_role=Member.FogRole.ADMIN)
        with pytest.raises(Http404):
            _view(_request(user), pk=999999)

    def it_redirects_an_anonymous_request_to_login(db):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        request.view_as = ViewAs(actual=frozenset({ROLE_GUEST}), picked=None)
        response = _view(request, pk=ClassOfferingFactory().pk)
        assert response.status_code == 302
