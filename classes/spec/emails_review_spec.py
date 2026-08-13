"""BDD specs for class review request and review decision emails."""

from __future__ import annotations

from django.core import mail

from classes.emails import (
    _admin_recipients,
    send_admin_review_request,
    send_admin_validation_request,
    send_class_review_decision,
    send_guild_lead_review_request,
)
from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassApproval, ClassOffering
from membership.models import AdminCapability, GuildStaffMembership, Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MemberFactory


def _class_admin(email: str) -> Member:
    """A Member holding the CLASS_APPROVER capability, with a linked, email-bearing User.

    Class-review/validation now route to capability holders (not a static admin blast), and
    the resolver only addresses members whose linked User carries an email — so the holder
    needs a real User. Signals are muted so create_user doesn't auto-provision a second
    Member for the one-to-one ``user`` key.
    """
    from django.contrib.auth.models import User
    from django.db.models.signals import post_save
    from factory.django import mute_signals

    member = MemberFactory(_pre_signup_email=email)
    with mute_signals(post_save):
        user = User.objects.create_user(username=email, email=email)
    member.user = user
    member.save(update_fields=["user"])
    member.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
    return member


def describe_admin_recipients():
    def it_deduplicates_repeated_addresses(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = "admin@example.com, admin@example.com, other@example.com"
        result = _admin_recipients()
        assert result == ["admin@example.com", "other@example.com"]

    def it_includes_admin_members_from_the_db(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        MemberFactory(fog_role=Member.FogRole.ADMIN, _pre_signup_email="dbadmin@example.com")
        assert _admin_recipients() == ["dbadmin@example.com"]

    def it_unions_db_admins_with_the_setting_and_dedupes(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = "dbadmin@example.com, extra@example.com"
        MemberFactory(fog_role=Member.FogRole.ADMIN, _pre_signup_email="dbadmin@example.com")
        # DB admin comes first; the setting's duplicate is dropped, the extra kept.
        assert _admin_recipients() == ["dbadmin@example.com", "extra@example.com"]

    def it_excludes_admin_members_without_an_email(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        MemberFactory(fog_role=Member.FogRole.ADMIN, _pre_signup_email="")
        assert _admin_recipients() == []

    def it_ignores_non_admin_members(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        MemberFactory(fog_role=Member.FogRole.MEMBER, _pre_signup_email="member@example.com")
        assert _admin_recipients() == []


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

    def it_also_emails_guild_staff_on_the_review_request(db, settings):
        """The single review-request email fans out to the lead and every staff member."""
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        cat = _make_guilded_category()  # lead = emailguildlead@example.com
        staff_member = MemberFactory(_pre_signup_email="coleadstaff@example.com")
        GuildStaffMembershipFactory(guild=cat.guild, member=staff_member, role=GuildStaffMembership.Role.CO_LEAD)
        inst_user = UserFactory(username="inststaff@example.com")
        instructor = InstructorFactory(user=inst_user, full_legal_name="InstS", instructor_slug="insts")
        offering = ClassOfferingFactory(instructor=instructor, category=cat, status=ClassOffering.Status.DRAFT)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)

        send_guild_lead_review_request(offering, row)

        # The spine sends one review email per leadership address; the lead AND the staff
        # member are both addressed (recipient SET identical to the old multi-To send).
        review_recipients = {addr for m in mail.outbox if "Review request" in m.subject for addr in m.to}
        assert "emailguildlead@example.com" in review_recipients
        assert "coleadstaff@example.com" in review_recipients

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


def describe_guild_lead_review_request_no_double_send():
    def it_sends_one_email_and_one_in_app_to_an_opted_in_lead(db, settings):
        """Opted-in leadership get the dedicated review email only, plus one in-app row."""
        from core.models import Notification, NotificationPreference, SiteActivity

        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        lead_user = UserFactory(email="leaduser@example.com")
        lead = lead_user.member  # type: ignore[attr-defined]
        lead.full_legal_name = "Lead User"
        lead.save(update_fields=["full_legal_name"])
        guild = GuildFactory(name="Opt Guild", guild_lead=lead)
        cat = CategoryFactory(guild=guild)
        # Lead opts into class_review_requested email — would have produced a 2nd
        # generic email before the dispatch's suppress_email=True.
        NotificationPreference.objects.create(
            user=lead_user, event_key="class_review_requested", channel="email", enabled=True
        )
        instructor = InstructorFactory(user=UserFactory(email="i@example.com"), instructor_slug="i-rev")
        offering = ClassOfferingFactory(instructor=instructor, category=cat, status=ClassOffering.Status.DRAFT)
        SiteActivity.objects.all().delete()

        offering.submit_for_review()

        # Exactly one email to the lead (the dedicated review-request), not two.
        lead_emails = [m for m in mail.outbox if m.to == ["leaduser@example.com"]]
        assert len(lead_emails) == 1
        assert offering.title in lead_emails[0].subject
        # The in-app row is present, and the SiteActivity is logged exactly once.
        assert Notification.objects.filter(trigger="class_review_requested", user=lead_user).count() == 1
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.CLASS_SUBMITTED).count() == 1


def describe_send_admin_review_request():
    def it_emails_the_class_administrators_and_the_instructor(db, settings):
        """Stage one for lead-less categories: the Class Administrators get the request."""
        _class_admin("classadmin@example.com")
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
        review_email = next(m for m in mail.outbox if m.to == ["classadmin@example.com"])
        assert offering.title in review_email.subject


def describe_send_admin_validation_request():
    def it_emails_class_administrators_with_executive_validation_wording(db, settings):
        """Stage two: the Class Administrators get the executive-validation request after a lead approves."""
        _class_admin("classadmin@example.com")
        cat = _make_guilded_category()
        offering = ClassOfferingFactory(category=cat, status=ClassOffering.Status.PENDING)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        send_admin_validation_request(offering, row)

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert email.to == ["classadmin@example.com"]
        assert "validation" in email.subject.lower()
        assert "executive validation" in email.body.lower()
        assert f"/classes/review/{row.token}/" in email.body

    def it_does_nothing_when_there_are_no_class_administrators(db):
        # No CLASS_APPROVER holders → the event resolves to nobody → no email.
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

    def it_sends_one_email_and_no_in_app_row_on_partial_approval(db):
        """A partial approval (another gate pending) is email-only — no bell row, as before."""
        from core.models import Notification

        inst_user = UserFactory(username="teachpa@example.com", email="teachpa@example.com")
        instructor = InstructorFactory(user=inst_user, full_legal_name="TeachPA", instructor_slug="teachpa")
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PENDING)
        row = ClassApproval.objects.create(
            class_offering=offering, role=ClassApproval.Role.ADMIN, decision=ClassApproval.Decision.APPROVED
        )

        send_class_review_decision(offering, row)

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["teachpa@example.com"]
        assert Notification.objects.filter(user=inst_user).count() == 0

    def it_sends_one_email_and_one_in_app_row_on_changes_requested(db):
        """Changes-requested pings the instructor's bell exactly once + one email."""
        from core.models import Notification

        inst_user = UserFactory(username="teachcr@example.com", email="teachcr@example.com")
        instructor = InstructorFactory(user=inst_user, full_legal_name="TeachCR", instructor_slug="teachcr")
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PENDING)
        row = ClassApproval.objects.create(
            class_offering=offering, role=ClassApproval.Role.ADMIN, decision=ClassApproval.Decision.CHANGES_REQUESTED
        )

        send_class_review_decision(offering, row)

        assert len(mail.outbox) == 1
        rows = Notification.objects.filter(trigger="instructor_changes_requested", user=inst_user)
        assert rows.count() == 1
        assert rows.first().title == "Changes requested on your class"

    def it_sends_one_email_and_no_in_app_row_on_decline(db):
        """A declined submission is email-only — no bell row (matching the old behavior)."""
        from core.models import Notification

        inst_user = UserFactory(username="teachdn@example.com", email="teachdn@example.com")
        instructor = InstructorFactory(user=inst_user, full_legal_name="TeachDN", instructor_slug="teachdn")
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PENDING)
        row = ClassApproval.objects.create(
            class_offering=offering, role=ClassApproval.Role.ADMIN, decision=ClassApproval.Decision.DENIED
        )

        send_class_review_decision(offering, row)

        assert len(mail.outbox) == 1
        assert "declined" in mail.outbox[0].subject.lower()
        assert Notification.objects.filter(user=inst_user).count() == 0
