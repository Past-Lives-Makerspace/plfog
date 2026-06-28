"""BDD specs for the sequential (Guild Lead → Admin) class-approval state machine."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory
from classes.models import ClassApproval, ClassOffering
from core.models import Notification
from tests.membership.factories import GuildFactory, MembershipPlanFactory


@pytest.fixture
def guild_lead_user(db):
    """A User whose auto-created Member leads a guild, with a real email."""
    MembershipPlanFactory()
    User = get_user_model()
    user = User.objects.create_user(username="lead@example.com", email="lead@example.com")
    from membership.models import Member

    member = Member.objects.get(user=user)
    member.full_legal_name = "Lead Person"
    member.save(update_fields=["full_legal_name"])
    return user


@pytest.fixture
def guilded_offering(db, guild_lead_user, settings):
    """A DRAFT offering whose category links a guild led by ``guild_lead_user``."""
    from membership.models import Member

    settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
    lead = Member.objects.get(user=guild_lead_user)
    guild = GuildFactory(name="Forge Guild", guild_lead=lead)
    cat = CategoryFactory(guild=guild)
    instructor = InstructorFactory(full_legal_name="Iris Smith", instructor_slug="iris")
    return ClassOfferingFactory(category=cat, instructor=instructor, status=ClassOffering.Status.DRAFT)


def describe_submit_for_review():
    def describe_with_a_guild_lead():
        def it_opens_only_the_guild_lead_gate(guilded_offering):
            rows = guilded_offering.submit_for_review()
            guilded_offering.refresh_from_db()
            assert guilded_offering.status == ClassOffering.Status.PENDING
            assert len(rows) == 1
            assert guilded_offering.approvals.count() == 1
            assert rows[0].role == ClassApproval.Role.GUILD_LEAD
            assert not guilded_offering.approvals.filter(role=ClassApproval.Role.ADMIN).exists()

    def describe_without_a_guild_lead():
        def it_opens_only_the_admin_gate(db, settings):
            settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
            offering = ClassOfferingFactory(category=CategoryFactory(guild=None), status=ClassOffering.Status.DRAFT)
            rows = offering.submit_for_review()
            assert len(rows) == 1
            assert offering.approvals.count() == 1
            assert rows[0].role == ClassApproval.Role.ADMIN


def describe_on_review_decision_recorded():
    def describe_guild_lead_approves():
        def it_opens_the_admin_gate_without_publishing(guilded_offering, guild_lead_user):
            (gl_row,) = guilded_offering.submit_for_review()
            gl_row.decide(ClassApproval.Decision.APPROVED, user=guild_lead_user)
            guilded_offering.refresh_from_db()
            assert guilded_offering.status == ClassOffering.Status.PENDING
            admin_rows = guilded_offering.approvals.filter(role=ClassApproval.Role.ADMIN)
            assert admin_rows.count() == 1
            assert admin_rows.first().decision == ""
            gl_row.refresh_from_db()
            assert gl_row.decision == ClassApproval.Decision.APPROVED

    def describe_admin_approves_after_lead():
        def it_publishes(guilded_offering, guild_lead_user, admin_user):
            (gl_row,) = guilded_offering.submit_for_review()
            gl_row.decide(ClassApproval.Decision.APPROVED, user=guild_lead_user)
            admin_row = guilded_offering.approvals.get(role=ClassApproval.Role.ADMIN, decision="")
            admin_row.class_offering = guilded_offering
            admin_row.decide(ClassApproval.Decision.APPROVED, user=admin_user)
            guilded_offering.refresh_from_db()
            assert guilded_offering.status == ClassOffering.Status.PUBLISHED
            assert guilded_offering.approved_by == admin_user
            assert guilded_offering.published_at is not None

    def describe_no_guild_lead_admin_approves():
        def it_publishes_directly(db, admin_user, settings):
            settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
            offering = ClassOfferingFactory(category=CategoryFactory(guild=None), status=ClassOffering.Status.DRAFT)
            (admin_row,) = offering.submit_for_review()
            admin_row.class_offering = offering
            admin_row.decide(ClassApproval.Decision.APPROVED, user=admin_user)
            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.PUBLISHED

    def describe_guild_lead_requests_changes():
        def it_returns_to_draft_without_opening_admin_gate(guilded_offering, guild_lead_user):
            (gl_row,) = guilded_offering.submit_for_review()
            gl_row.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=guild_lead_user, notes="Add prerequisites.")
            guilded_offering.refresh_from_db()
            assert guilded_offering.status == ClassOffering.Status.DRAFT
            assert not guilded_offering.approvals.filter(role=ClassApproval.Role.ADMIN).exists()


def describe_escalation_notifications():
    def it_notifies_the_guild_lead_on_submit(guilded_offering, guild_lead_user):
        guilded_offering.submit_for_review()
        note = Notification.objects.get(user=guild_lead_user, trigger="class_review_requested")
        assert "Iris Smith" in note.body
        assert "Forge Guild" in note.body

    def it_notifies_fog_admins_on_guild_lead_approval(guilded_offering, guild_lead_user, admin_user):
        # The validation in-app now resolves to FOG_ADMINS (was is_staff) — admin_user
        # is a fog admin, so they receive it. The copy names the guild lead by display
        # name (unified with the email shell) rather than by username.
        (gl_row,) = guilded_offering.submit_for_review()
        gl_row.decide(ClassApproval.Decision.APPROVED, user=guild_lead_user)
        note = Notification.objects.get(user=admin_user, trigger="class_validation_requested")
        assert "Iris Smith" in note.body
        # The guild lead is named by display name (full legal name) in the unified copy.
        assert "Lead Person" in note.body


def describe_instructor_approved_notification():
    @pytest.fixture
    def offering_with_instructor_user(db, guild_lead_user, settings):
        """A guilded DRAFT offering whose instructor has a real, notifiable User."""
        from membership.models import Member

        settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
        user_model = get_user_model()
        instr_user = user_model.objects.create_user(username="iris@example.com", email="iris@example.com")
        instructor = InstructorFactory(user=instr_user, full_legal_name="Iris Smith", instructor_slug="iris")
        lead = Member.objects.get(user=guild_lead_user)
        guild = GuildFactory(name="Forge Guild", guild_lead=lead)
        cat = CategoryFactory(guild=guild)
        offering = ClassOfferingFactory(category=cat, instructor=instructor, status=ClassOffering.Status.DRAFT)
        return offering, instr_user

    def it_does_not_notify_the_instructor_at_stage_one(offering_with_instructor_user, guild_lead_user):
        offering, instr_user = offering_with_instructor_user
        (gl_row,) = offering.submit_for_review()
        gl_row.decide(ClassApproval.Decision.APPROVED, user=guild_lead_user)
        offering.refresh_from_db()
        # Guild lead approved, but the class is still PENDING the admin gate —
        # the instructor must not yet be told it was "approved".
        assert offering.status == ClassOffering.Status.PENDING
        assert not Notification.objects.filter(user=instr_user, trigger="instructor_class_approved").exists()

    def it_notifies_the_instructor_exactly_once_on_publication(
        offering_with_instructor_user, guild_lead_user, admin_user
    ):
        from classes.emails import send_class_review_decision

        offering, instr_user = offering_with_instructor_user
        (gl_row,) = offering.submit_for_review()
        gl_row.decide(ClassApproval.Decision.APPROVED, user=guild_lead_user)
        admin_row = offering.approvals.get(role=ClassApproval.Role.ADMIN, decision="")
        admin_row.class_offering = offering
        admin_row.decide(ClassApproval.Decision.APPROVED, user=admin_user)
        offering.refresh_from_db()
        admin_row.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED
        # The "approved" bell row + the "live!" email now both fan out from the single
        # ``instructor_class_approved`` event the view emits via send_class_review_decision
        # after the publishing decision — exactly one bell row, even across the two gates.
        send_class_review_decision(offering, admin_row)
        assert Notification.objects.filter(user=instr_user, trigger="instructor_class_approved").count() == 1
