"""BDD specs for class review request and review decision emails."""

from __future__ import annotations

from django.core import mail

from classes.emails import _admin_review_recipients, send_class_review_decision, send_class_review_requests
from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassApproval, ClassOffering
from tests.membership.factories import GuildFactory, MemberFactory


def describe_admin_review_recipients():
    def it_deduplicates_repeated_addresses(settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com, admin@example.com, other@example.com"
        result = _admin_review_recipients()
        assert result == ["admin@example.com", "other@example.com"]


def _make_guilded_category():
    """Category linked to a Guild whose lead resolves to a known email address."""
    lead = MemberFactory(_pre_signup_email="emailguildlead@example.com")
    guild = GuildFactory(name="Email Test Guild", guild_lead=lead)
    return CategoryFactory(guild=guild)


def describe_send_class_review_requests():
    def it_emails_the_guild_lead_for_guild_lead_approvals(db, settings):
        """When an offering needs a GUILD_LEAD approval, the guild lead's email is used."""
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        cat = _make_guilded_category()
        inst_user = UserFactory(username="inst2@example.com")
        instructor = InstructorFactory(user=inst_user, full_legal_name="Inst2", instructor_slug="inst2")
        offering = ClassOfferingFactory(
            instructor=instructor,
            category=cat,
            status=ClassOffering.Status.DRAFT,
        )
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)

        send_class_review_requests(offering, [row])

        # One review-request email to the guild lead + one instructor notification
        assert len(mail.outbox) == 2
        review_email = next(m for m in mail.outbox if m.to == ["emailguildlead@example.com"])
        assert offering.title in review_email.subject

    def it_skips_guild_lead_email_when_lead_has_no_email(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        noemail_member = MemberFactory(_pre_signup_email="")
        guild = GuildFactory(name="Silent Guild", guild_lead=noemail_member)
        cat = CategoryFactory(guild=guild)
        offering = ClassOfferingFactory(category=cat, status=ClassOffering.Status.DRAFT)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)

        send_class_review_requests(offering, [row])

        # Guild lead has no email so no review-request email; only the instructor notification fires
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to != [""]  # not sent to the lead's empty address


def describe_send_class_review_decision():
    def it_skips_when_instructor_has_no_email(db):
        user = UserFactory(email="")
        instructor = InstructorFactory(user=user)
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.DRAFT)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        send_class_review_decision(offering, row)

        assert len(mail.outbox) == 0

    def it_emails_approved_but_not_yet_published(db):
        """When admin approves but another gate remains, subject reflects partial approval."""
        inst_user = UserFactory(username="teach5@example.com")
        instructor = InstructorFactory(user=inst_user, full_legal_name="Teach5", instructor_slug="teach5")
        offering = ClassOfferingFactory(
            instructor=instructor,
            status=ClassOffering.Status.PENDING,
        )
        row = ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.ADMIN,
            decision=ClassApproval.Decision.APPROVED,
        )

        send_class_review_decision(offering, row)

        assert len(mail.outbox) == 1
        assert "approved" in mail.outbox[0].subject.lower()
        assert "live" not in mail.outbox[0].subject.lower()

    def it_emails_changes_requested(db):
        inst_user = UserFactory(username="teach6@example.com")
        instructor = InstructorFactory(user=inst_user, full_legal_name="Teach6", instructor_slug="teach6")
        offering = ClassOfferingFactory(
            instructor=instructor,
            status=ClassOffering.Status.PENDING,
        )
        row = ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.ADMIN,
            decision=ClassApproval.Decision.CHANGES_REQUESTED,
        )

        send_class_review_decision(offering, row)

        assert len(mail.outbox) == 1
        assert "changes" in mail.outbox[0].subject.lower()
