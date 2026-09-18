"""BDD specs for the per-class access model — the whole capability matrix, leg by leg.

The gate in ``classes/access.py`` is first-match-wins and the order is load-bearing, so
these specs pin each leg AND the order between them: the guest leg before the model-reading
ones, the instructor leg before the guild leg. The four capability sets are asserted one
``it_*`` per population per capability, because a matrix that is asserted in bulk drifts a
row at a time without anything going red.

The ``CLASS_APPROVER`` grant is the one thing that is NOT a leg (ruling 25): it is UNIONED
into whichever set the holder already has on this class, and stands alone as the one-tab
reviewer set only for a holder who matched no leg. Three things are pinned below: the
composed sets keep every capability their own row carried, they carry every capability the
reviewer row carries (asserted against ``_reviewer_access`` itself, so the union cannot go
stale if that row ever gains one), and a holder who matched no leg still gets the reviewer
set.
"""

from __future__ import annotations

from dataclasses import fields

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

    def it_can_send_email(guild_access):
        # #371 item 1 flipped this. Emails is the only tab ruling 12 leaves this population,
        # and until now it was also the only thing they could not use: the link was withheld
        # and the composer's Send answered 403. Jo's call is that a lead may address a class in
        # their own guild, with the composer's per-person picker.
        assert guild_access.can_send_email is True

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


def describe_a_class_approver_who_is_also_the_instructor_or_the_guild():
    """Ruling 25 — the grant composes with the set they already hold, it does not replace it.

    Jo: "A Guild Lead can approve their own class since they don't have the full ability to
    get the class published; only admins/CMS admins can do that. A CMS Admin can approve
    their own class too."

    So the assertion is two-sided every time: they KEEP their tabs and their actions, and
    they GAIN approve. Before ruling 25 the first-match order gave this population the
    one-tab reviewer set and nothing else — no edit, no roster, no welcome email, on their
    own class.
    """

    def describe_and_teaches_the_class():
        @pytest.fixture
        def approving_instructor(db) -> ClassAccess:
            user, member = _teaching_member()
            member.admin_capabilities.create(capability=CLASS_APPROVER)
            return _access_or_fail(_request(user), ClassOfferingFactory(instructor=member))

        def it_keeps_the_instructor_role(approving_instructor):
            assert approving_instructor.role == ROLE_INSTRUCTOR

        def it_keeps_the_teaching_shell(approving_instructor):
            assert approving_instructor.shell == TEACH_SHELL

        def it_keeps_every_instructor_tab(approving_instructor):
            assert [tab.key for tab in approving_instructor.tabs] == [
                "overview",
                "registrations",
                "waitlist",
                "discount_codes",
                "emails",
            ]

        def it_keeps_edit(approving_instructor):
            assert approving_instructor.can_edit is True

        def it_keeps_the_roster_and_the_waitlist(approving_instructor):
            assert (approving_instructor.can_view_registrations, approving_instructor.can_view_waitlist) == (True, True)

        def it_keeps_the_mailbox_and_the_composer(approving_instructor):
            assert (approving_instructor.can_view_emails, approving_instructor.can_send_email) == (True, True)

        def it_keeps_submit_cancel_and_sale(approving_instructor):
            assert approving_instructor.can_submit is True
            assert approving_instructor.can_cancel is True
            assert approving_instructor.can_sale is True

        def it_gains_approve(approving_instructor):
            # The whole point: the approve button on their own class, which is what the
            # grant is for. can_approve gates admin_class_approve and admin_class_review.
            assert approving_instructor.can_approve is True

        def it_gains_nothing_administrative(approving_instructor):
            # Composing adds exactly one field. Archive, delete, unpublish and the rest
            # stay admin-only.
            assert approving_instructor.can_administer is False

    def describe_and_leads_the_classes_guild():
        @pytest.fixture
        def approving_lead(db) -> ClassAccess:
            user, member = _user_with_member()
            member.admin_capabilities.create(capability=CLASS_APPROVER)
            return _access_or_fail(_request(user), _guild_class(member))

        def it_keeps_the_guild_role(approving_lead):
            assert approving_lead.role == ROLE_GUILD

        def it_keeps_the_teaching_shell(approving_lead):
            assert approving_lead.shell == TEACH_SHELL

        def it_keeps_edit(approving_lead):
            # The changelog fragment promises exactly this to guild leads and staff.
            assert approving_lead.can_edit is True

        def it_keeps_the_emails_tab(approving_lead):
            assert "emails" in [tab.key for tab in approving_lead.tabs]

        def it_keeps_the_composer_the_guild_row_gave_them(approving_lead):
            # Since #371 item 1 the GUILD row carries can_send_email, so the composed set
            # carries it too. It is a "keeps", not a "gains": the reviewer row has never
            # carried it and still does not, which the reviewer-set specs above pin. Holding
            # CLASS_APPROVER must not be a way to acquire the composer.
            assert approving_lead.can_send_email is True

        def it_gains_approve(approving_lead):
            assert approving_lead.can_approve is True

        def it_gains_the_overview_the_grant_already_opened(approving_lead):
            # The Approve button renders on the Overview and nowhere else, and the CMS
            # Administrator's own queue links every row there. Without this the composed
            # set would carry can_approve that no page could render, and the queue row
            # would 404 on their own guild's class — which is where they were sent before
            # this ticket. The grant alone already opens the Overview on every class, so
            # the union takes nothing from ruling 12: it is the reviewer row, not the
            # guild row, that carries it.
            assert approving_lead.can_view_overview is True

        def it_shows_the_overview_and_emails_tabs_in_order(approving_lead):
            assert [tab.key for tab in approving_lead.tabs] == ["overview", "emails"]

        def it_gains_nothing_else(approving_lead):
            # Ruling 6: the union is the reviewer row and nothing beyond it.
            assert approving_lead.can_view_registrations is False
            assert approving_lead.can_view_waitlist is False
            assert approving_lead.can_view_discount_codes is False
            assert approving_lead.can_administer is False
            assert approving_lead.can_submit is False
            assert approving_lead.can_cancel is False
            assert approving_lead.can_sale is False

    def describe_and_staffs_the_classes_guild():
        """Ruling 23: staff get the same reach as the lead, here too."""

        @pytest.fixture
        def approving_staffer(db) -> ClassAccess:
            user, member = _user_with_member()
            member.admin_capabilities.create(capability=CLASS_APPROVER)
            return _access_or_fail(_request(user), _guild_class(member, staff=True))

        def it_keeps_the_guild_role(approving_staffer):
            assert approving_staffer.role == ROLE_GUILD

        def it_keeps_edit(approving_staffer):
            assert approving_staffer.can_edit is True

        def it_keeps_the_mailbox(approving_staffer):
            assert approving_staffer.can_view_emails is True

        def it_gains_approve(approving_staffer):
            assert approving_staffer.can_approve is True

        def it_gains_the_overview(approving_staffer):
            assert approving_staffer.can_view_overview is True

        def it_gains_nothing_administrative(approving_staffer):
            assert approving_staffer.can_administer is False

        def it_gains_no_roster(approving_staffer):
            assert approving_staffer.can_view_registrations is False

    def describe_the_reviewer_row_is_unioned_in():
        """Every capability the reviewer row carries is carried by the composed sets.

        Asserted against :func:`classes.access._reviewer_access` itself rather than against
        a hand-copied list, so ``_with_reviewer_grant`` cannot go stale: give the reviewer
        row a capability it does not have today and the guild-side assertions go red until
        the union follows. The guild row is where that guard bites, because it is the sparse
        one — the instructor row already carries all but two of the capabilities, so its
        subset check can only ever catch a missing ``can_approve``. It is kept as the
        statement of the rule for both sets, not as the guard.
        """

        def _capabilities(access: ClassAccess) -> set[str]:
            return {
                field.name
                for field in fields(ClassAccess)
                if field.name.startswith("can_") and getattr(access, field.name)
            }

        def it_covers_the_reviewer_row_on_the_composed_instructor_set(db, reviewer_access):
            user, member = _teaching_member()
            member.admin_capabilities.create(capability=CLASS_APPROVER)
            access = _access_or_fail(_request(user), ClassOfferingFactory(instructor=member))
            assert _capabilities(reviewer_access) <= _capabilities(access)

        def it_covers_the_reviewer_row_on_the_composed_guild_set(db, reviewer_access):
            user, member = _user_with_member()
            member.admin_capabilities.create(capability=CLASS_APPROVER)
            access = _access_or_fail(_request(user), _guild_class(member))
            assert _capabilities(reviewer_access) <= _capabilities(access)

        def it_adds_nothing_the_two_rows_did_not_already_carry(db, reviewer_access):
            # The other half of "union": the composed set is the OR of the two rows and
            # not one capability more. Built without the grant to get the plain guild row.
            plain_user, plain_member = _user_with_member()
            plain = _access_or_fail(_request(plain_user), _guild_class(plain_member))
            user, member = _user_with_member()
            member.admin_capabilities.create(capability=CLASS_APPROVER)
            composed = _access_or_fail(_request(user), _guild_class(member))
            assert _capabilities(composed) == _capabilities(plain) | _capabilities(reviewer_access)

    def describe_and_is_a_site_wide_guild_officer():
        """A fog guild officer with teaching access reaches the guild leg on EVERY class.

        Not ruling 25's named population, but ``can_edit_class`` short-circuits on
        ``is_effective_staff`` and lets them through, so they compose. That is safe for the
        same reason the ruling's own population is: without the grant this officer already
        held the guild row on every class, and with it alone they already held the reviewer
        row on every class, so the union hands them nothing new. The point of these specs is
        that the grant must not *cost* them anything either — before ruling 25 it took the
        guild row away, and a naive fix would take the Overview away instead.
        """

        @pytest.fixture
        def approving_officer(db) -> ClassAccess:
            user, member = _user_with_member(
                fog_role=Member.FogRole.GUILD_OFFICER, instructor_oriented_at=timezone.now()
            )
            member.admin_capabilities.create(capability=CLASS_APPROVER)
            return _access_or_fail(_request(user), ClassOfferingFactory(category=CategoryFactory(guild=GuildFactory())))

        def it_gets_the_composed_guild_set(approving_officer):
            assert approving_officer.role == ROLE_GUILD

        def it_keeps_the_edit_the_guild_row_gave_them(approving_officer):
            assert approving_officer.can_edit is True

        def it_keeps_the_overview_the_grant_gave_them(approving_officer):
            # The reviewer set they used to get was Overview and Approve; the union keeps
            # both, so their review queue still opens.
            assert approving_officer.can_view_overview is True

        def it_keeps_approve(approving_officer):
            assert approving_officer.can_approve is True

        def it_is_still_denied_the_roster_and_the_lifecycle_actions(approving_officer):
            assert approving_officer.can_view_registrations is False
            assert approving_officer.can_administer is False
            assert approving_officer.can_submit is False

        def it_composes_the_same_way_on_a_guild_less_category(db):
            # The 86 production classes under a NULL guild. The officer reaches the leg
            # through is_effective_staff, so the union must hold there too. (The NULL guard
            # in leads_or_staffs is covered by it_denies_a_fog_guild_officer_without_teaching_access
            # below, which is the case that actually calls it.)
            user, member = _user_with_member(
                fog_role=Member.FogRole.GUILD_OFFICER, instructor_oriented_at=timezone.now()
            )
            member.admin_capabilities.create(capability=CLASS_APPROVER)
            access = _access_or_fail(_request(user), ClassOfferingFactory(category=CategoryFactory(guild=None)))
            assert access.role == ROLE_GUILD
            assert (access.can_edit, access.can_view_overview, access.can_approve) == (True, True, True)

        def it_stays_the_plain_reviewer_without_the_teaching_grant(db, reviewer_access):
            # No teaching grant means the guild leg's precondition fails (ruling 6), so
            # there is no row to union with and nothing about them changes.
            user, member = _user_with_member(fog_role=Member.FogRole.GUILD_OFFICER)
            member.admin_capabilities.create(capability=CLASS_APPROVER)
            offering = ClassOfferingFactory(category=CategoryFactory(guild=GuildFactory()))
            assert _access_or_fail(_request(user), offering) == reviewer_access

    def describe_the_standalone_row_is_not_status_scoped():
        """The premise the union rests on, pinned rather than asserted in prose.

        :func:`_with_reviewer_grant` hands the Overview to a composed viewer unconditionally,
        and that widens nothing only because the grant standing alone already opens the
        Overview on every class in the catalog whatever state it is in. Scope the standalone
        row later — to pending classes, say, which is a plausible reading of a grant whose
        contract is approve/validate — and the composed sets would quietly be the wider of
        the two. These go red first.
        """

        @pytest.mark.parametrize(
            "status",
            [
                ClassOffering.Status.DRAFT,
                ClassOffering.Status.PENDING,
                ClassOffering.Status.PUBLISHED,
                ClassOffering.Status.CANCELLED,
                ClassOffering.Status.ARCHIVED,
            ],
        )
        def it_opens_the_overview_on_a_class_in_any_state(db, reviewer_access, status):
            user, member = _user_with_member()
            member.admin_capabilities.create(capability=CLASS_APPROVER)
            offering = ClassOfferingFactory(category=CategoryFactory(guild=None), status=status)
            assert _access_or_fail(_request(user), offering) == reviewer_access

    def describe_and_is_neither():
        def it_is_the_untouched_reviewer_set(db, reviewer_access):
            # The regression: a plain holder still falls to _reviewer_access() and not to
            # some composed set. Both sides come from the same builder, so this pins the
            # SET, not its contents — the contents are pinned one capability at a time in
            # describe_the_reviewer_capability_set above.
            user, member = _user_with_member()
            member.admin_capabilities.create(capability=CLASS_APPROVER)
            offering = ClassOfferingFactory(category=CategoryFactory(guild=None))
            assert _access_or_fail(_request(user), offering) == reviewer_access

        def it_is_the_reviewer_set_on_someone_elses_guilds_class(db, reviewer_access):
            # Lead of SOME guild, but not this class's: no claim to compose with.
            user, member = _user_with_member()
            member.admin_capabilities.create(capability=CLASS_APPROVER)
            GuildFactory(guild_lead=member)
            other = ClassOfferingFactory(category=CategoryFactory(guild=GuildFactory()))
            assert _access_or_fail(_request(user), other) == reviewer_access

        def it_is_the_reviewer_set_for_a_named_instructor_without_teaching_access(db, reviewer_access):
            # Ruling 6: being named instructor without the teaching grant matches no leg,
            # so there is nothing to compose and the grant stands alone.
            user, member = _user_with_member()
            member.admin_capabilities.create(capability=CLASS_APPROVER)
            offering = ClassOfferingFactory(category=CategoryFactory(guild=None), instructor=member)
            assert _access_or_fail(_request(user), offering) == reviewer_access

    def describe_and_is_previewing_a_lower_role():
        def it_composes_nothing_onto_the_guild_set(db):
            # The grant is read once, before the legs, so the preview suppression governs
            # the composed set too: they edit as a guild lead, and they get neither half of
            # the reviewer row. Both halves are asserted, because a refactor that keeps the
            # suppression on can_approve while hoisting can_view_overview out of the branch
            # would hand a previewing admin the Overview on someone else's class.
            user, member = _user_with_member(fog_role=Member.FogRole.GUILD_OFFICER)
            member.admin_capabilities.create(capability=CLASS_APPROVER)
            offering = _guild_class(member)
            access = _access_or_fail(_request(user, picked=ROLE_MEMBER), offering)
            assert access.role == ROLE_GUILD
            assert access.can_edit is True
            assert access.can_approve is False
            assert access.can_view_overview is False

        def it_grants_no_approve_on_the_composed_instructor_set(db):
            user, member = _user_with_member(
                fog_role=Member.FogRole.GUILD_OFFICER, instructor_oriented_at=timezone.now()
            )
            member.admin_capabilities.create(capability=CLASS_APPROVER)
            offering = ClassOfferingFactory(instructor=member)
            access = _access_or_fail(_request(user, picked=ROLE_MEMBER), offering)
            assert access.role == ROLE_INSTRUCTOR
            assert access.can_approve is False

    def describe_and_is_a_full_admin():
        def it_is_unaffected_because_the_admin_leg_already_grants_everything(db, admin_access):
            user, member = _user_with_member(fog_role=Member.FogRole.ADMIN)
            member.admin_capabilities.create(capability=CLASS_APPROVER)
            offering = _guild_class(member)
            offering.instructor = member
            offering.save(update_fields=["instructor"])
            assert _access_or_fail(_request(user), offering) == admin_access


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
    """Criterion 17. One population, and the divergence only ever runs one way.

    Nothing gets ``can_edit`` that ``can_edit_class`` refuses: the guild leg asks it
    outright, the instructor leg implies it (the instructor clause) and the admin leg
    implies it (``is_effective_staff``). So the whole of the disagreement is
    "``can_edit_class`` says yes, the screen says no", and since ruling 25 it has exactly
    one cause.

    **The teaching-grant precondition on the guild leg.** ``can_edit_class`` is broader than
    ruling 23's population — it ends on the instructor check, so anyone merely *named*
    instructor of a class satisfies it. The waiver covers guild leads and staff and nobody
    else, so a named-but-ungranted instructor is denied the screen entirely while
    ``can_edit_class`` still says yes. NOT new: that gap is the teaching portal's own gate
    (``teaching_member_required``), carried forward unchanged, which is what ruling 6 asks
    for. Pinned in ``describe_and_nobody_else_ruling_6`` above.

    The reviewer grant used to be a second cause, introduced by this ticket and removed by
    ruling 25: a non-admin ``CLASS_APPROVER`` holder who was ALSO the instructor or the
    guild lead/staff got the reviewer set, which has no ``can_edit``, on their own class.
    The grant is unioned in now rather than replacing the row, so both populations get
    ``can_edit`` from both answers. Pinned below, and in
    ``describe_a_class_approver_who_is_also_the_instructor_or_the_guild``.

    Ruling 23's own population — a guild lead or staffer with no teaching grant — used to
    sit in that bucket too and no longer does: they now get ``can_edit`` from both.
    """

    def it_agrees_with_an_instructor_who_also_holds_class_approver(db):
        # Ruling 25. Before it, this was the ticket's own new disagreement: the reviewer
        # leg fired first and took can_edit away from the instructor of the class.
        user, member = _teaching_member()
        member.admin_capabilities.create(capability=CLASS_APPROVER)
        offering = ClassOfferingFactory(instructor=member)
        access = _access_or_fail(_request(user), offering)
        assert access.role == ROLE_INSTRUCTOR
        assert access.can_edit is True
        assert can_edit_class(_request(user), offering) is True

    def it_agrees_with_a_guild_lead_who_also_holds_class_approver(db):
        user, member = _user_with_member()
        member.admin_capabilities.create(capability=CLASS_APPROVER)
        offering = _guild_class(member)
        access = _access_or_fail(_request(user), offering)
        assert access.role == ROLE_GUILD
        assert access.can_edit is True
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
        # leads_or_staffs would raise if it read guild.guild_lead_id unguarded.
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
