"""BDD specs for class review request and review decision emails."""

from __future__ import annotations

from allauth.account.models import EmailAddress
from django.core import mail

from classes.emails import (
    send_admin_review_request,
    send_admin_validation_request,
    send_class_review_decision,
    send_guild_lead_review_reminder,
    send_guild_lead_review_request,
)
from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassApproval, ClassOffering
from core.models import Notification, NotificationPreference
from membership.models import AdminCapability, Guild, GuildStaffMembership, Member
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


def _leader(email: str) -> Member:
    """A member with a login: the review request reaches only people who hold switches."""
    return UserFactory(username=email, email=email).member  # type: ignore[attr-defined]


def _guild_led_by(lead: Member, name: str = "Email Test Guild") -> Guild:
    return GuildFactory(name=name, guild_lead=lead)


def _email_off(member: Member) -> None:
    NotificationPreference.objects.create(
        user=member.user, event_key="class_review_requested", channel="email", enabled=False
    )


def _review_emails(address: str) -> list:
    return [m for m in mail.outbox if m.to == [address] and m.subject.startswith("Review request:")]


def _bells(member: Member) -> int:
    return Notification.objects.filter(trigger="class_review_requested", user=member.user).count()


def _pending_guild_class(guild: Guild) -> tuple[ClassOffering, ClassApproval]:
    inst_user = UserFactory(username=f"inst-{guild.pk}@example.com")
    instructor = InstructorFactory(user=inst_user, full_legal_name="Inst", instructor_slug=f"inst-{guild.pk}")
    offering = ClassOfferingFactory(instructor=instructor, category=CategoryFactory(guild=guild))
    row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
    return offering, row


def describe_send_guild_lead_review_request():
    def it_emails_the_guild_lead_and_the_instructor(db):
        """Stage one: the guild lead gets the request, the instructor gets the explainer."""
        lead = _leader("emailguildlead@example.com")
        offering, row = _pending_guild_class(_guild_led_by(lead))

        send_guild_lead_review_request(offering, row)

        # One review-request email to the guild lead + one instructor explainer.
        assert len(mail.outbox) == 2
        review_email = _review_emails("emailguildlead@example.com")[0]
        assert offering.title in review_email.subject
        assert f"/classes/review/{row.token}/" in review_email.body
        assert "Guild Lead" in review_email.body

    def it_also_emails_guild_staff_on_the_review_request(db):
        """The review request fans out to the lead and every staff member."""
        lead = _leader("emailguildlead@example.com")
        guild = _guild_led_by(lead)
        staff_member = _leader("coleadstaff@example.com")
        GuildStaffMembershipFactory(guild=guild, member=staff_member, role=GuildStaffMembership.Role.CO_LEAD)
        offering, row = _pending_guild_class(guild)

        send_guild_lead_review_request(offering, row)

        assert len(_review_emails("emailguildlead@example.com")) == 1
        assert len(_review_emails("coleadstaff@example.com")) == 1

    def it_skips_a_leader_with_no_login(db):
        """A member with no login has no switches to obey, so the event system skips them."""
        guild = _guild_led_by(MemberFactory(_pre_signup_email="nologin@example.com"), "Silent Guild")
        offering, row = _pending_guild_class(guild)

        send_guild_lead_review_request(offering, row)

        # Only the instructor's explainer goes out.
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to != ["nologin@example.com"]

    def describe_the_email_switch():
        def it_sends_no_review_email_to_a_leader_who_switched_it_off(db):
            lead = _leader("offlead@example.com")
            _email_off(lead)
            offering, row = _pending_guild_class(_guild_led_by(lead))

            send_guild_lead_review_request(offering, row)

            assert _review_emails("offlead@example.com") == []
            # The switch is the Email switch only: the bell still rings.
            assert _bells(lead) == 1

        def it_emails_a_leader_who_left_it_on_at_their_notification_address(db):
            lead = _leader("primarylead@example.com")
            EmailAddress.objects.create(user=lead.user, email="shop@example.com", verified=True, primary=False)
            lead.notification_email = "shop@example.com"
            lead.save(update_fields=["notification_email"])
            offering, row = _pending_guild_class(_guild_led_by(lead))

            send_guild_lead_review_request(offering, row)

            assert len(_review_emails("shop@example.com")) == 1
            assert _review_emails("primarylead@example.com") == []
            assert _bells(lead) == 1

        def it_still_reaches_the_rest_of_the_leadership(db):
            lead = _leader("quietlead@example.com")
            _email_off(lead)
            guild = _guild_led_by(lead)
            staff_member = _leader("loudstaff@example.com")
            GuildStaffMembershipFactory(guild=guild, member=staff_member, role=GuildStaffMembership.Role.SECRETARY)
            offering, row = _pending_guild_class(guild)

            send_guild_lead_review_request(offering, row)

            assert _review_emails("quietlead@example.com") == []
            assert len(_review_emails("loudstaff@example.com")) == 1


def describe_guild_lead_review_request_no_double_send():
    def it_sends_one_email_and_one_in_app_to_an_opted_in_lead(db, settings):
        """Opted-in leadership get the dedicated review email only, plus one in-app row."""
        from core.models import SiteActivity

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
        offering = ClassOfferingFactory(
            ready=True, instructor=instructor, category=cat, status=ClassOffering.Status.DRAFT
        )
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
    def _leadless_class() -> tuple[ClassOffering, ClassApproval]:
        inst_user = UserFactory(username="inst3@example.com")
        instructor = InstructorFactory(user=inst_user, full_legal_name="Inst3", instructor_slug="inst3")
        offering = ClassOfferingFactory(
            instructor=instructor,
            category=CategoryFactory(guild=None),
            status=ClassOffering.Status.DRAFT,
        )
        return offering, ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

    def it_emails_the_class_administrators_and_the_instructor(db):
        """Stage one for lead-less categories: the CMS Administrators get the request."""
        _class_admin("classadmin@example.com")
        offering, row = _leadless_class()

        send_admin_review_request(offering, row)

        assert len(mail.outbox) == 2
        review_email = _review_emails("classadmin@example.com")[0]
        assert offering.title in review_email.subject

    def it_sends_no_review_email_to_a_cms_administrator_who_switched_it_off(db):
        admin = _class_admin("quietadmin@example.com")
        _email_off(admin)
        other = _class_admin("loudadmin@example.com")
        offering, row = _leadless_class()

        send_admin_review_request(offering, row)

        assert _review_emails("quietadmin@example.com") == []
        assert len(_review_emails("loudadmin@example.com")) == 1
        # Push, Discord and the bell are untouched by the Email switch.
        assert _bells(admin) == 1
        assert _bells(other) == 1


def describe_send_guild_lead_review_reminder():
    def it_returns_none_for_a_guild_with_no_leadership(db):
        _offering, row = _pending_guild_class(GuildFactory(name="Empty Guild", guild_lead=None))
        assert send_guild_lead_review_reminder(row) is None
        assert mail.outbox == []

    def it_returns_none_for_a_class_whose_category_has_no_guild(db):
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None))
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        assert send_guild_lead_review_reminder(row) is None

    def it_emails_leadership_whose_switch_is_on_and_skips_those_whose_switch_is_off(db):
        lead = _leader("remindlead@example.com")
        guild = _guild_led_by(lead, "Remind Guild")
        quiet = _leader("remindquiet@example.com")
        GuildStaffMembershipFactory(guild=guild, member=quiet, role=GuildStaffMembership.Role.TREASURER)
        _email_off(quiet)
        _offering, row = _pending_guild_class(guild)

        result = send_guild_lead_review_reminder(row)

        assert result is not None
        assert len(_review_emails("remindlead@example.com")) == 1
        assert _review_emails("remindquiet@example.com") == []
        assert _bells(quiet) == 1

    def it_returns_none_when_no_leader_can_be_reached(db):
        # A lead with no login cannot hold switches or receive the email, so there is
        # nobody to remind: the admin reviews it instead of being told a reminder went out.
        guild = _guild_led_by(MemberFactory(_pre_signup_email="ghost@example.com"), "Ghost Guild")
        _offering, row = _pending_guild_class(guild)

        assert send_guild_lead_review_reminder(row) is None
        assert mail.outbox == []


def describe_send_admin_validation_request():
    def it_emails_class_administrators_asking_for_admin_sign_off(db, settings):
        """Stage two: the CMS Administrators get the admin sign-off request after a lead approves."""
        _class_admin("classadmin@example.com")
        cat = CategoryFactory(guild=_guild_led_by(MemberFactory(_pre_signup_email="emailguildlead@example.com")))
        offering = ClassOfferingFactory(category=cat, status=ClassOffering.Status.PENDING)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        send_admin_validation_request(offering, row)

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert email.to == ["classadmin@example.com"]
        assert email.subject == f"Admin sign-off needed: {offering.title}"
        assert "request admin sign-off" in email.body.lower()
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
