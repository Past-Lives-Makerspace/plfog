"""BDD specs for class review request and review decision emails."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core import mail

from classes.emails import _admin_review_recipients, send_class_review_decision, send_class_review_requests
from classes.factories import ClassOfferingFactory, CategoryFactory, InstructorFactory, UserFactory
from classes.models import ClassApproval, ClassOffering


def describe_admin_review_recipients():
    def it_deduplicates_repeated_addresses(settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com, admin@example.com, other@example.com"
        result = _admin_review_recipients()
        assert result == ["admin@example.com", "other@example.com"]


def _make_guilded_category(db):
    """Category linked to a Guild whose lead has a user with an email address."""
    from membership.models import Guild, Member, MembershipPlan

    User = get_user_model()
    plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
    lead_user, _ = User.objects.get_or_create(
        username="emailguildlead@example.com", defaults={"email": "emailguildlead@example.com"}
    )
    lead_member, _ = Member.objects.get_or_create(
        user=lead_user, defaults={"full_legal_name": "Email Guild Lead", "membership_plan": plan}
    )
    guild = Guild.objects.create(name="Email Test Guild", guild_lead=lead_member)
    return CategoryFactory(guild=guild)


def describe_send_class_review_requests():
    def it_emails_the_guild_lead_for_guild_lead_approvals(db, settings):
        """When an offering needs a GUILD_LEAD approval, the guild lead's email is used."""
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        cat = _make_guilded_category(db)
        inst_user = UserFactory(username="inst2@example.com")
        instructor = InstructorFactory(user=inst_user, display_name="Inst2", slug="inst2")
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
        from membership.models import Guild, Member, MembershipPlan

        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        User = get_user_model()
        plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
        noemail_user, _ = User.objects.get_or_create(
            username="noemail2@example.com", defaults={"email": ""}
        )
        noemail_member, _ = Member.objects.get_or_create(
            user=noemail_user, defaults={"full_legal_name": "No Email Lead", "membership_plan": plan}
        )
        guild = Guild.objects.create(name="Silent Guild", guild_lead=noemail_member)
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
        instructor = InstructorFactory(user=inst_user, display_name="Teach5", slug="teach5")
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
        instructor = InstructorFactory(user=inst_user, display_name="Teach6", slug="teach6")
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
