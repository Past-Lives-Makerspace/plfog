"""BDD specs for class review request and review decision emails."""

from __future__ import annotations

from django.core import mail

from classes.emails import (
    _admin_review_recipients,
    send_admin_review_request,
    send_admin_validation_request,
    send_class_review_decision,
    send_guild_lead_review_request,
)
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


def describe_send_guild_lead_review_request():
    def it_emails_the_guild_lead_and_the_instructor(db, settings):
        """Stage one: the guild lead gets the request, the instructor gets the explainer."""
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

        send_guild_lead_review_request(offering, row)

        # One review-request email to the guild lead + one instructor notification
        assert len(mail.outbox) == 2
        review_email = next(m for m in mail.outbox if m.to == ["emailguildlead@example.com"])
        assert offering.title in review_email.subject
        assert f"/classes/review/{row.token}/" in review_email.body
        assert "Guild Lead" in review_email.body

    def it_skips_guild_lead_email_when_lead_has_no_email(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        noemail_member = MemberFactory(_pre_signup_email="")
        guild = GuildFactory(name="Silent Guild", guild_lead=noemail_member)
        cat = CategoryFactory(guild=guild)
        offering = ClassOfferingFactory(category=cat, status=ClassOffering.Status.DRAFT)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)

        send_guild_lead_review_request(offering, row)

        # Guild lead has no email so no review-request email; only the instructor notification fires
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to != [""]  # not sent to the lead's empty address


def describe_send_admin_review_request():
    def it_emails_the_admins_and_the_instructor(db, settings):
        """Stage one for lead-less categories: admins get the request."""
        settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
        inst_user = UserFactory(username="inst3@example.com")
        instructor = InstructorFactory(user=inst_user, full_legal_name="Inst3", instructor_slug="inst3")
        offering = ClassOfferingFactory(
            instructor=instructor,
            category=CategoryFactory(guild=None),
            status=ClassOffering.Status.DRAFT,
        )
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        send_admin_review_request(offering, row)

        assert len(mail.outbox) == 2
        review_email = next(m for m in mail.outbox if m.to == ["admin@example.com"])
        assert offering.title in review_email.subject


def describe_send_admin_validation_request():
    def it_emails_admins_with_executive_validation_wording(db, settings):
        """Stage two: admins get the executive-validation request after a lead approves."""
        settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com"
        cat = _make_guilded_category()
        offering = ClassOfferingFactory(category=cat, status=ClassOffering.Status.PENDING)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        send_admin_validation_request(offering, row)

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert email.to == ["admin@example.com"]
        assert "validation" in email.subject.lower()
        assert "executive validation" in email.body.lower()
        assert f"/classes/review/{row.token}/" in email.body

    def it_does_nothing_when_no_admin_recipients(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        send_admin_validation_request(offering, row)

        assert len(mail.outbox) == 0


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
