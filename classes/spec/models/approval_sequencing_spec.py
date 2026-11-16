"""BDD specs for class review: one gate per class, and its approval publishes (#1183)."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.urls import reverse

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory
from classes.models import ClassApproval, ClassOffering
from core.models import EventDelivery, Notification
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
    return ClassOfferingFactory(ready=True, category=cat, instructor=instructor, status=ClassOffering.Status.DRAFT)


@pytest.fixture
def class_approver(admin_user):
    """``admin_user`` holding the CMS Administrator capability: the staff who would get a sign off request."""
    from membership.models import AdminCapability, Member

    Member.objects.get(user=admin_user).admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
    return admin_user


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

    def describe_without_a_guild():
        def it_opens_only_the_admin_gate(db, settings):
            settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
            offering = ClassOfferingFactory(
                ready=True, category=CategoryFactory(guild=None), status=ClassOffering.Status.DRAFT
            )
            rows = offering.submit_for_review()
            assert len(rows) == 1
            assert offering.approvals.count() == 1
            assert rows[0].role == ClassApproval.Role.ADMIN

    def describe_with_a_guild_that_has_no_lead():
        def it_opens_only_the_admin_gate(db):
            offering = ClassOfferingFactory(
                ready=True,
                category=CategoryFactory(guild=GuildFactory(name="Leaderless Guild", guild_lead=None)),
                status=ClassOffering.Status.DRAFT,
            )
            (row,) = offering.submit_for_review()
            assert row.role == ClassApproval.Role.ADMIN
            assert offering.required_review_roles == [ClassApproval.Role.ADMIN]

    def describe_resubmitting_after_the_lead_approved_these_dates():
        def it_goes_to_the_guild_lead_again_not_to_an_admin(guilded_offering, guild_lead_user):
            # Legacy shape: a draft still holding the guild's approval of these exact dates.
            # The old shortcut sent such a class to an admin; one review means the guild.
            (gl_row,) = guilded_offering.submit_for_review()
            ClassApproval.objects.filter(pk=gl_row.pk).update(
                decision=ClassApproval.Decision.APPROVED,
                approved_schedule_fingerprint=guilded_offering.schedule_fingerprint,
            )
            ClassOffering.objects.filter(pk=guilded_offering.pk).update(status=ClassOffering.Status.DRAFT)
            guilded_offering.refresh_from_db()
            (row,) = guilded_offering.submit_for_review()
            assert row.role == ClassApproval.Role.GUILD_LEAD


def describe_on_review_decision_recorded():
    def describe_guild_lead_approves():
        def it_publishes_the_class(guilded_offering, guild_lead_user):
            (gl_row,) = guilded_offering.submit_for_review()
            gl_row.decide(ClassApproval.Decision.APPROVED, user=guild_lead_user)
            guilded_offering.refresh_from_db()
            assert guilded_offering.status == ClassOffering.Status.PUBLISHED
            assert guilded_offering.published_at is not None
            assert guilded_offering.approved_by == guild_lead_user

        def it_creates_no_admin_row(guilded_offering, guild_lead_user):
            (gl_row,) = guilded_offering.submit_for_review()
            gl_row.decide(ClassApproval.Decision.APPROVED, user=guild_lead_user)
            assert not guilded_offering.approvals.filter(role=ClassApproval.Role.ADMIN).exists()
            gl_row.refresh_from_db()
            assert gl_row.decision == ClassApproval.Decision.APPROVED

        def it_sends_staff_no_email_and_no_bell_row(guilded_offering, guild_lead_user, class_approver):
            (gl_row,) = guilded_offering.submit_for_review()
            mail.outbox.clear()
            gl_row.decide(ClassApproval.Decision.APPROVED, user=guild_lead_user)
            assert not Notification.objects.filter(user=class_approver).exists()
            assert not EventDelivery.objects.filter(target_ref=f"user:{class_approver.pk}").exists()
            assert all(class_approver.email not in message.to for message in mail.outbox)

        def it_closes_an_admin_gate_left_open_beside_it(guilded_offering, guild_lead_user):
            # A class re-filed into a guild while its admin gate was open carries both gates.
            (gl_row,) = guilded_offering.submit_for_review()
            admin_row = ClassApproval.objects.create(class_offering=guilded_offering, role=ClassApproval.Role.ADMIN)
            gl_row.decide(ClassApproval.Decision.APPROVED, user=guild_lead_user)
            admin_row.refresh_from_db()
            assert admin_row.decision == ClassApproval.Decision.APPROVED
            assert admin_row.decided_by is None
            assert admin_row.notes == "Approved automatically when the guild lead approved the class."

        def it_refuses_an_unready_class_and_keeps_the_gate_open(guilded_offering, guild_lead_user):
            (gl_row,) = guilded_offering.submit_for_review()
            ClassOffering.objects.filter(pk=guilded_offering.pk).update(capacity=0)
            gl_row.class_offering.refresh_from_db()
            with pytest.raises(ValidationError, match="Not ready to publish"):
                gl_row.decide(ClassApproval.Decision.APPROVED, user=guild_lead_user)
            gl_row.refresh_from_db()
            assert gl_row.decision == ""
            guilded_offering.refresh_from_db()
            assert guilded_offering.status == ClassOffering.Status.PENDING

    def describe_no_guild_lead_admin_approves():
        def it_publishes_directly(db, admin_user, settings):
            settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
            offering = ClassOfferingFactory(
                ready=True, category=CategoryFactory(guild=None), status=ClassOffering.Status.DRAFT
            )
            (admin_row,) = offering.submit_for_review()
            admin_row.class_offering = offering
            admin_row.decide(ClassApproval.Decision.APPROVED, user=admin_user)
            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.PUBLISHED
            assert offering.published_at is not None

    def describe_admin_approves_while_guild_lead_gate_is_open():
        """An admin can still approve any pending class early; it publishes before the lead reviews."""

        def it_publishes_immediately(guilded_offering, admin_user):
            guilded_offering.submit_for_review()
            guilded_offering.approve(admin_user)
            guilded_offering.refresh_from_db()
            assert guilded_offering.status == ClassOffering.Status.PUBLISHED
            assert guilded_offering.approved_by == admin_user
            assert guilded_offering.published_at is not None

        def it_supersedes_the_open_guild_lead_gate_without_deleting_it(guilded_offering, admin_user):
            (gl_row,) = guilded_offering.submit_for_review()
            guilded_offering.approve(admin_user)
            gl_row.refresh_from_db()
            assert gl_row.decision == ClassApproval.Decision.APPROVED
            assert gl_row.decided_by is None
            assert gl_row.decided_at is not None
            assert gl_row.notes == "Approved automatically when an admin gave final approval."

        def it_drops_the_class_out_of_the_guild_lead_queue(guilded_offering, guild_lead_user, admin_user):
            from membership.models import Member

            guilded_offering.submit_for_review()
            lead = Member.objects.get(user=guild_lead_user)
            assert ClassOffering.objects.awaiting_guild_lead(lead).count() == 1
            guilded_offering.approve(admin_user)
            assert ClassOffering.objects.awaiting_guild_lead(lead).count() == 0

    def describe_guild_lead_requests_changes():
        def it_returns_to_draft_without_opening_admin_gate(guilded_offering, guild_lead_user):
            (gl_row,) = guilded_offering.submit_for_review()
            gl_row.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=guild_lead_user, notes="Add prerequisites.")
            guilded_offering.refresh_from_db()
            assert guilded_offering.status == ClassOffering.Status.DRAFT
            assert guilded_offering.published_at is None
            assert not guilded_offering.approvals.filter(role=ClassApproval.Role.ADMIN).exists()

    def describe_guild_lead_denies():
        def it_returns_to_draft(guilded_offering, guild_lead_user):
            (gl_row,) = guilded_offering.submit_for_review()
            gl_row.decide(ClassApproval.Decision.DENIED, user=guild_lead_user, notes="Not this season.")
            guilded_offering.refresh_from_db()
            assert guilded_offering.status == ClassOffering.Status.DRAFT


def describe_review_notifications():
    def it_notifies_the_guild_lead_on_submit(guilded_offering, guild_lead_user):
        guilded_offering.submit_for_review()
        note = Notification.objects.get(user=guild_lead_user, trigger="class_review_requested")
        assert "Iris Smith" in note.body
        assert "Forge Guild" in note.body

    def it_links_the_guild_lead_to_the_class_they_are_asked_to_review(guilded_offering, guild_lead_user):
        # It once linked to the lead's own teach overview, which does not show the class.
        (gl_row,) = guilded_offering.submit_for_review()
        note = Notification.objects.get(user=guild_lead_user, trigger="class_review_requested")
        assert note.url == reverse("classes:class_review", kwargs={"token": gl_row.token})

    def it_asks_no_admin_on_a_guilded_submit(guilded_offering, class_approver):
        guilded_offering.submit_for_review()
        assert not Notification.objects.filter(user=class_approver).exists()


def describe_decide_status_guard():
    """decide() refuses to act unless the offering is PENDING — the single choke point for every path."""

    def it_refuses_a_decision_when_the_offering_is_not_pending(db):
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        with pytest.raises(ValueError, match="Only pending"):
            row.decide(ClassApproval.Decision.APPROVED)
        row.refresh_from_db()
        assert row.decision == ""
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.DRAFT


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
        offering = ClassOfferingFactory(
            ready=True, category=cat, instructor=instructor, status=ClassOffering.Status.DRAFT
        )
        return offering, instr_user

    def it_sends_the_live_email_once_when_the_guild_lead_approves(offering_with_instructor_user, guild_lead_user):
        from classes.emails import send_class_review_decision

        offering, instr_user = offering_with_instructor_user
        (gl_row,) = offering.submit_for_review()
        gl_row.decide(ClassApproval.Decision.APPROVED, user=guild_lead_user)
        offering.refresh_from_db()
        gl_row.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED
        mail.outbox.clear()
        # The view emits this right after the publishing decision: one bell row and the
        # existing "Your class is live" email.
        send_class_review_decision(offering, gl_row)
        assert Notification.objects.filter(user=instr_user, trigger="instructor_class_approved").count() == 1
        assert [message.subject for message in mail.outbox] == [f"Your class '{offering.title}' is live!"]
