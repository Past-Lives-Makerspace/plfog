"""BDD specs for class review request and review decision emails."""

from __future__ import annotations

from django.core import mail

from classes.emails import (
    _admin_recipients,
    _guild_lead_lane_status,
    send_admin_review_request,
    send_admin_validation_request,
    send_class_review_decision,
    send_guild_lead_review_request,
    send_review_requests,
)
from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassApproval, ClassOffering
from membership.models import AdminCapability, GuildStaffMembership, Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MemberFactory

#: The sentence the admin's at-submit email must carry while the guild lead's lane is open.
#: Pinned here because the admin is the one person who can publish over that lane, so a wrong
#: or missing answer is the difference between "wait for the room check" and "publish now".
NOT_DECIDED_WORDING = "The guild lead has not answered yet."


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


def _instructor(slug: str, email: str):
    return InstructorFactory(
        user=UserFactory(username=email, email=email),
        full_legal_name=slug.replace("-", " ").title(),
        instructor_slug=slug,
    )


def describe_guild_lead_lane_status():
    """The one sentence the admin's email uses to report the other lane."""

    def it_says_not_answered_while_the_lead_row_is_open(db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING)
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        assert _guild_lead_lane_status(offering) == NOT_DECIDED_WORDING

    def it_says_the_space_is_free_once_the_lead_approves(db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING)
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.GUILD_LEAD,
            decision=ClassApproval.Decision.APPROVED,
        )
        assert _guild_lead_lane_status(offering) == "The guild lead has already said the space is free."

    def it_reports_a_request_for_changes(db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING)
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.GUILD_LEAD,
            decision=ClassApproval.Decision.CHANGES_REQUESTED,
        )
        assert _guild_lead_lane_status(offering) == "The guild lead has asked the instructor for changes."

    def it_reports_a_denial(db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING)
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.GUILD_LEAD,
            decision=ClassApproval.Decision.DENIED,
        )
        assert _guild_lead_lane_status(offering) == "The guild lead has turned these dates down."

    def it_reports_a_lane_an_admin_closed_by_publishing(db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING)
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.GUILD_LEAD,
            decision=ClassApproval.Decision.OVERRIDDEN_BY_ADMIN,
        )
        assert _guild_lead_lane_status(offering) == "The guild lead's check was closed when an admin published."

    def it_says_nothing_when_the_category_has_no_guild_lead(db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING)
        assert _guild_lead_lane_status(offering) == ""


def describe_review_pipeline_email_strip():
    """``_review_pipeline.html``: the two-lane draw, and when it stops explaining itself."""

    def _strip(offering) -> str:
        from django.template.loader import render_to_string

        return render_to_string("classes/emails/_review_pipeline.html", {"pipeline": offering.review_pipeline()})

    def it_stacks_both_lanes_inside_one_step_while_review_is_open(db):
        offering = ClassOfferingFactory(category=_make_guilded_category(), status=ClassOffering.Status.PENDING)
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        html = _strip(offering)

        # One outer strip plus one nested table per column: Submitted, the review branch, Live.
        assert html.count("<table") == 4
        assert "These two run at the same time. Neither waits for the other." in html
        # Email-safe: tables and inline styles only.
        assert "<svg" not in html and "<link" not in html and "class=" not in html

    def it_does_not_explain_the_branch_on_a_single_lane_class(db):
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None), status=ClassOffering.Status.PENDING)
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        html = _strip(offering)

        assert "These two run at the same time" not in html
        assert ">Guild lead</div>" not in html

    def it_drops_the_branch_note_once_both_lanes_have_closed(db):
        """A finished review needs no explaining; the note is only useful while it is a gate."""
        offering = ClassOfferingFactory(category=_make_guilded_category(), status=ClassOffering.Status.PUBLISHED)
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.GUILD_LEAD,
            decision=ClassApproval.Decision.APPROVED,
        )
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.ADMIN,
            decision=ClassApproval.Decision.APPROVED,
        )

        html = _strip(offering)

        assert ">Guild lead</div>" in html and ">Admin</div>" in html
        assert "These two run at the same time" not in html

    def it_draws_an_overridden_lane_without_a_tick(db):
        offering = ClassOfferingFactory(category=_make_guilded_category(), status=ClassOffering.Status.PUBLISHED)
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.GUILD_LEAD,
            decision=ClassApproval.Decision.OVERRIDDEN_BY_ADMIN,
        )
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.ADMIN,
            decision=ClassApproval.Decision.APPROVED,
        )

        html = _strip(offering)

        # The en-dash marker, not the check: nobody in the guild said yes.
        assert "&#8211;" in html


def describe_send_review_requests():
    """The orchestrator: both lanes notified, the instructor told exactly once."""

    def it_sends_the_instructor_exactly_one_explainer_naming_both_reviewers(db, settings):
        """Regression: an explainer per lane landed two identical emails on every guilded submit."""
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _class_admin("cmsadmin@example.com")
        cat = _make_guilded_category()
        instructor = _instructor("both-lanes", "bothlanes@example.com")
        offering = ClassOfferingFactory(
            ready=True, instructor=instructor, category=cat, status=ClassOffering.Status.DRAFT
        )
        rows = [
            ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD),
            ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN),
        ]
        offering.status = ClassOffering.Status.PENDING
        offering.save(update_fields=["status"])

        send_review_requests(offering, rows)

        explainers = [m for m in mail.outbox if m.to == ["bothlanes@example.com"]]
        assert len(explainers) == 1
        # Names both reviewers, in the same words the portal uses.
        assert "the guild lead (Email Test Guild) and an admin" in explainers[0].body

    def it_notifies_both_reviewer_lanes(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _class_admin("cmsadmin2@example.com")
        cat = _make_guilded_category()
        instructor = _instructor("two-lanes", "twolanes@example.com")
        offering = ClassOfferingFactory(
            ready=True, instructor=instructor, category=cat, status=ClassOffering.Status.PENDING
        )
        rows = [
            ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD),
            ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN),
        ]

        send_review_requests(offering, rows)

        recipients = {addr for m in mail.outbox for addr in m.to}
        assert recipients == {"emailguildlead@example.com", "cmsadmin2@example.com", "twolanes@example.com"}

    def it_sends_one_explainer_for_a_category_with_no_guild_lead(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _class_admin("cmsadmin3@example.com")
        instructor = _instructor("lone-lane", "lonelane@example.com")
        offering = ClassOfferingFactory(
            ready=True,
            instructor=instructor,
            category=CategoryFactory(guild=None),
            status=ClassOffering.Status.PENDING,
        )
        rows = [ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)]

        send_review_requests(offering, rows)

        explainers = [m for m in mail.outbox if m.to == ["lonelane@example.com"]]
        assert len(explainers) == 1
        assert "an admin" in explainers[0].body
        assert "guild lead" not in explainers[0].body

    def it_sends_exactly_one_explainer_through_the_whole_submit_path(db, settings):
        """End to end: the model's own submit fans out both lanes and ONE instructor email.

        This is the member-facing half of the regression, and it is red until
        ``ClassOffering._notify_reviewers`` calls ``emails.send_review_requests(self, rows)``
        instead of dispatching to the two lane senders itself. Until that one line lands, a
        guilded submit delivers two identical "Your class is in review" emails: the ledger
        cannot collapse them, because each carries a period keyed on a different approval row.
        """
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _class_admin("cmsadmin4@example.com")
        cat = _make_guilded_category()
        instructor = _instructor("submit-path", "submitpath@example.com")
        offering = ClassOfferingFactory(
            ready=True, instructor=instructor, category=cat, status=ClassOffering.Status.DRAFT
        )

        offering.submit_for_review()

        explainers = [m for m in mail.outbox if m.to == ["submitpath@example.com"]]
        assert len(explainers) == 1
        assert "is in review" in explainers[0].subject


def describe_send_guild_lead_review_request():
    def it_emails_the_guild_leadership_only(db, settings):
        """The lane's own email. The instructor explainer belongs to the orchestrator now."""
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        cat = _make_guilded_category()
        instructor = _instructor("inst2", "inst2@example.com")
        offering = ClassOfferingFactory(
            instructor=instructor,
            category=cat,
            status=ClassOffering.Status.DRAFT,
        )
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)

        send_guild_lead_review_request(offering, row)

        assert len(mail.outbox) == 1
        review_email = mail.outbox[0]
        assert review_email.to == ["emailguildlead@example.com"]
        assert offering.title in review_email.subject
        # The guild lead answers without a hub login, so their link stays tokenized.
        assert f"/classes/review/{row.token}/" in review_email.body
        assert "GUILD LEAD REVIEW REQUESTED" in review_email.body
        assert "check that the room, the kit and the guild calendar are free" in review_email.body

    def it_also_emails_guild_staff_on_the_review_request(db, settings):
        """The single review-request email fans out to the lead and every staff member."""
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        cat = _make_guilded_category()  # lead = emailguildlead@example.com
        staff_member = MemberFactory(_pre_signup_email="coleadstaff@example.com")
        GuildStaffMembershipFactory(guild=cat.guild, member=staff_member, role=GuildStaffMembership.Role.CO_LEAD)
        instructor = _instructor("insts", "inststaff@example.com")
        offering = ClassOfferingFactory(
            ready=True, instructor=instructor, category=cat, status=ClassOffering.Status.DRAFT
        )
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)

        send_guild_lead_review_request(offering, row)

        # The spine sends one review email per leadership address; the lead AND the staff
        # member are both addressed (recipient SET identical to the old multi-To send).
        review_recipients = {addr for m in mail.outbox if "Review request" in m.subject for addr in m.to}
        assert "emailguildlead@example.com" in review_recipients
        assert "coleadstaff@example.com" in review_recipients

    def it_skips_the_email_when_the_leadership_has_no_email(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        noemail_member = MemberFactory(_pre_signup_email="")
        guild = GuildFactory(name="Silent Guild", guild_lead=noemail_member)
        cat = CategoryFactory(guild=guild)
        offering = ClassOfferingFactory(category=cat, status=ClassOffering.Status.DRAFT)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)

        send_guild_lead_review_request(offering, row)

        assert mail.outbox == []


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
    def it_emails_the_class_administrators_with_no_bearer_token(db, settings):
        """The admin is a logged-in reviewer, so the link is the admin review screen."""
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _class_admin("classadmin@example.com")
        instructor = _instructor("inst3", "inst3@example.com")
        offering = ClassOfferingFactory(
            instructor=instructor,
            category=CategoryFactory(guild=None),
            status=ClassOffering.Status.PENDING,
        )
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        send_admin_review_request(offering, row)

        assert len(mail.outbox) == 1
        review_email = mail.outbox[0]
        assert review_email.to == ["classadmin@example.com"]
        assert offering.title in review_email.subject
        assert f"/classes/admin/{offering.pk}/review/" in review_email.body
        assert f"/classes/review/{row.token}/" not in review_email.body

    def it_rides_the_executive_validation_event_so_one_opt_out_governs_both_emails(db, settings):
        from core.models import Notification

        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        holder = _class_admin("optin@example.com")
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None), status=ClassOffering.Status.PENDING)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        send_admin_review_request(offering, row)

        assert Notification.objects.filter(trigger="class_validation_requested", user=holder.user).count() == 1
        assert not Notification.objects.filter(trigger="class_review_requested").exists()

    def it_respects_the_class_needs_executive_validation_opt_out(db, settings):
        from core.models import NotificationPreference

        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        holder = _class_admin("optout@example.com")
        NotificationPreference.objects.create(
            user=holder.user, event_key="class_validation_requested", channel="email", enabled=False
        )
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None), status=ClassOffering.Status.PENDING)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        send_admin_review_request(offering, row)

        assert mail.outbox == []

    def it_tells_the_admin_the_guild_lead_has_not_answered_yet(db, settings):
        """Criterion 5: the admin reads where the other lane stands before they publish."""
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _class_admin("statusadmin@example.com")
        cat = _make_guilded_category()
        offering = ClassOfferingFactory(category=cat, status=ClassOffering.Status.PENDING)
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        send_admin_review_request(offering, row)

        body = mail.outbox[0].body
        assert NOT_DECIDED_WORDING in body
        html = next(alt for alt, kind in mail.outbox[0].alternatives if kind == "text/html")
        assert NOT_DECIDED_WORDING in html

    def it_says_the_admin_is_the_only_approval_for_a_lead_less_category(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _class_admin("soloadmin@example.com")
        offering = ClassOfferingFactory(category=CategoryFactory(guild=None), status=ClassOffering.Status.PENDING)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        send_admin_review_request(offering, row)

        assert "yours is the only approval it needs" in mail.outbox[0].body


def describe_send_admin_validation_request():
    def it_tells_the_admins_theirs_is_the_last_approval(db, settings):
        """The lane was already open; this is news about the guild lead's lane, not a new gate."""
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _class_admin("classadmin@example.com")
        cat = _make_guilded_category()
        offering = ClassOfferingFactory(category=cat, status=ClassOffering.Status.PENDING)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        send_admin_validation_request(offering, row)

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert email.to == ["classadmin@example.com"]
        assert email.subject == f"Last approval needed: {offering.title}"
        assert "last approval this class needs" in email.body
        assert "has checked the space" in email.body

    def it_links_the_admin_review_screen_and_emails_no_token(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _class_admin("classadmin@example.com")
        offering = ClassOfferingFactory(category=_make_guilded_category(), status=ClassOffering.Status.PENDING)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        send_admin_validation_request(offering, row)

        body = mail.outbox[0].body
        assert f"/classes/admin/{offering.pk}/review/" in body
        assert f"/classes/review/{row.token}/" not in body

    def it_does_not_dedupe_against_the_submit_time_email_on_the_same_row(db, settings):
        """Both rides sit on ``class_validation_requested``; only the period keeps them apart."""
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _class_admin("classadmin@example.com")
        cat = _make_guilded_category()
        offering = ClassOfferingFactory(category=cat, status=ClassOffering.Status.PENDING)
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        send_admin_review_request(offering, row)
        send_admin_validation_request(offering, row)

        subjects = [m.subject for m in mail.outbox]
        assert subjects == [f"Review request: {offering.title}", f"Last approval needed: {offering.title}"]

    def it_does_nothing_when_there_are_no_class_administrators(db):
        # No CLASS_APPROVER holders → the event resolves to nobody → no email.
        offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        send_admin_validation_request(offering, row)

        assert len(mail.outbox) == 0


def describe_send_guild_lead_review_reminder():
    def it_carries_the_review_strip(db, settings):
        """The reminder reuses the review_request shell, so it inherits the two-lane strip."""
        from classes.emails import send_guild_lead_review_reminder

        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        cat = _make_guilded_category()
        offering = ClassOfferingFactory(
            ready=True,
            instructor=_instructor("remind", "remind@example.com"),
            category=cat,
            status=ClassOffering.Status.PENDING,
        )
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)

        send_guild_lead_review_reminder(row)

        assert "[✓] Submitted  [●] Guild lead  [●] Admin  [ ] Live" in mail.outbox[0].body

    def it_returns_none_when_the_guild_has_nobody_left_to_remind(db):
        from classes.emails import send_guild_lead_review_reminder

        guild = GuildFactory(name="Empty Guild", guild_lead=MemberFactory(_pre_signup_email=""))
        offering = ClassOfferingFactory(category=CategoryFactory(guild=guild), status=ClassOffering.Status.PENDING)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)

        assert send_guild_lead_review_reminder(row) is None


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
        instructor = _instructor("teach5", "teach5@example.com")
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

    def it_explains_an_approval_held_for_the_room_check(db):
        """ "Approve, hold for the room check": the lead's yes is what publishes, not a second admin."""
        instructor = _instructor("teachheld", "teachheld@example.com")
        offering = ClassOfferingFactory(
            instructor=instructor, category=_make_guilded_category(), status=ClassOffering.Status.PENDING
        )
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        row = ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.ADMIN,
            decision=ClassApproval.Decision.APPROVED,
        )

        send_class_review_decision(offering, row)

        body = mail.outbox[0].body
        html = next(alt for alt, kind in mail.outbox[0].alternatives if kind == "text/html")
        assert "holding it for the space check" in body
        assert "as soon as the guild lead confirms the room is free" in body
        assert "Approved, waiting on the space" in html
        assert "holding it for the space check" in html

    def it_lists_the_remaining_gate_when_the_guild_lead_approved_first(db):
        """The mirror case: the lead said yes, the admin has not, and that is NOT a hold."""
        instructor = _instructor("teachlead", "teachlead@example.com")
        offering = ClassOfferingFactory(
            instructor=instructor, category=_make_guilded_category(), status=ClassOffering.Status.PENDING
        )
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        row = ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.GUILD_LEAD,
            decision=ClassApproval.Decision.APPROVED,
        )

        send_class_review_decision(offering, row)

        body = mail.outbox[0].body
        assert "holding it for the space check" not in body
        assert "Still waiting on: Admin." in body

    def it_emails_changes_requested(db):
        instructor = _instructor("teach6", "teach6@example.com")
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
