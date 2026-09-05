"""BDD specs for ClassOffering.review_pipeline(): the strip every page and review email draws."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core import mail
from django.template.loader import render_to_string
from django.utils import timezone

from classes.emails import (
    send_admin_review_request,
    send_admin_validation_request,
    send_class_review_decision,
    send_guild_lead_review_request,
)
from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassApproval, ClassOffering
from tests.membership.factories import GuildFactory, MemberFactory

Status = ClassOffering.Status


def _guilded_category(guild_name: str = "Woodshop"):
    lead = MemberFactory(_pre_signup_email=f"{guild_name.lower()}-lead@example.com")
    guild = GuildFactory(name=guild_name, guild_lead=lead)
    return CategoryFactory(guild=guild)


def _states(offering: ClassOffering) -> list[tuple[str, str]]:
    return [(step.key, step.state) for step in offering.review_pipeline().steps]


@pytest.fixture
def named_admin(db):
    return UserFactory(username="sam@example.com", email="sam@example.com", first_name="Sam", last_name="Reed")


def describe_review_pipeline():
    def it_renders_three_steps_without_a_guild_lead(db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        pipeline = offering.review_pipeline()
        assert [s.key for s in pipeline.steps] == ["submitted", "admin", "live"]
        assert pipeline.headline == "Not submitted yet"
        assert pipeline.is_bounced is False
        assert pipeline.is_live is False
        assert pipeline.fill_percent == 0

    def it_renders_four_steps_with_a_guild_lead(db):
        offering = ClassOfferingFactory(status=Status.DRAFT, category=_guilded_category())
        assert [s.key for s in offering.review_pipeline().steps] == ["submitted", "guild_lead", "admin", "live"]

    def it_marks_submitted_current_on_a_plain_draft(db):
        offering = ClassOfferingFactory(status=Status.DRAFT, category=_guilded_category())
        assert _states(offering) == [
            ("submitted", "current"),
            ("guild_lead", "ahead"),
            ("admin", "ahead"),
            ("live", "ahead"),
        ]

    def it_marks_the_guild_lead_current_while_their_row_is_open(db):
        offering = ClassOfferingFactory(status=Status.PENDING, category=_guilded_category())
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        pipeline = offering.review_pipeline()
        assert _states(offering) == [
            ("submitted", "done"),
            ("guild_lead", "current"),
            ("admin", "ahead"),
            ("live", "ahead"),
        ]
        assert pipeline.headline == "Waiting on the guild lead (Woodshop)"
        assert pipeline.steps[1].detail.startswith("Waiting since ")
        assert pipeline.fill_percent == 33

    def it_keeps_the_admin_step_ahead_while_the_guild_gate_is_open_even_with_an_admin_row(db):
        # An admin who opened the review page early minted an admin row; the class is
        # still waiting on the guild lead, so only one step reads current.
        offering = ClassOfferingFactory(status=Status.PENDING, category=_guilded_category())
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        assert _states(offering)[1:3] == [("guild_lead", "current"), ("admin", "ahead")]

    def it_marks_guild_done_and_admin_current_after_the_lead_approves(db, named_admin):
        offering = ClassOfferingFactory(status=Status.PENDING, category=_guilded_category())
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.GUILD_LEAD,
            decision=ClassApproval.Decision.APPROVED,
            decided_by=named_admin,
            decided_at=timezone.now(),
        )
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        pipeline = offering.review_pipeline()
        assert _states(offering) == [
            ("submitted", "done"),
            ("guild_lead", "done"),
            ("admin", "current"),
            ("live", "ahead"),
        ]
        assert pipeline.headline == "Waiting on an admin"
        assert pipeline.steps[1].detail.startswith("Approved by Sam Reed, ")
        assert pipeline.fill_percent == 67

    def it_marks_the_admin_current_on_a_pending_class_with_no_rows(db):
        offering = ClassOfferingFactory(status=Status.PENDING)
        pipeline = offering.review_pipeline()
        assert _states(offering) == [("submitted", "done"), ("admin", "current"), ("live", "ahead")]
        assert pipeline.headline == "Waiting on an admin"
        assert pipeline.fill_percent == 50

    def it_marks_everything_done_once_live(db):
        offering = ClassOfferingFactory(
            status=Status.PUBLISHED, category=_guilded_category(), published_at=timezone.now()
        )
        pipeline = offering.review_pipeline()
        assert _states(offering) == [
            ("submitted", "done"),
            ("guild_lead", "done"),
            ("admin", "done"),
            ("live", "done"),
        ]
        assert pipeline.headline.startswith("Live since ")
        assert pipeline.is_live is True
        assert pipeline.fill_percent == 100

    def it_says_live_without_a_date_when_the_publish_stamp_is_missing(db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED, published_at=None)
        assert offering.review_pipeline().headline == "Live"

    def it_marks_the_guild_step_changes_requested_with_the_note(db, named_admin):
        offering = ClassOfferingFactory(status=Status.DRAFT, category=_guilded_category())
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.GUILD_LEAD,
            decision=ClassApproval.Decision.CHANGES_REQUESTED,
            notes="Add  safety\nnotes",
            decided_by=named_admin,
            decided_at=timezone.now(),
        )
        pipeline = offering.review_pipeline()
        assert _states(offering) == [
            ("submitted", "done"),
            ("guild_lead", "changes_requested"),
            ("admin", "ahead"),
            ("live", "ahead"),
        ]
        assert pipeline.is_bounced is True
        assert pipeline.note == "Add safety notes"
        assert pipeline.steps[1].note == "Add safety notes"
        assert pipeline.steps[1].detail.startswith("Changes requested by Sam Reed, ")
        assert pipeline.headline == "Changes requested by the guild lead"
        assert pipeline.steps[1].marker == "↩"

    def it_marks_the_admin_step_declined_after_the_lead_approved(db, named_admin):
        offering = ClassOfferingFactory(status=Status.DRAFT, category=_guilded_category())
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.GUILD_LEAD,
            decision=ClassApproval.Decision.APPROVED,
            decided_at=timezone.now() - timedelta(days=1),
        )
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.ADMIN,
            decision=ClassApproval.Decision.DENIED,
            notes="Not this season.",
            decided_by=named_admin,
            decided_at=timezone.now(),
        )
        pipeline = offering.review_pipeline()
        assert _states(offering) == [
            ("submitted", "done"),
            ("guild_lead", "done"),
            ("admin", "changes_requested"),
            ("live", "ahead"),
        ]
        assert pipeline.headline == "Declined by an admin"
        assert pipeline.steps[1].detail.startswith("Approved, ")
        assert pipeline.steps[2].detail.startswith("Declined by Sam Reed, ")

    def it_resets_the_strip_on_resubmit_after_a_bounce(db, named_admin):
        offering = ClassOfferingFactory(ready=True, status=Status.DRAFT, category=_guilded_category())
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.GUILD_LEAD,
            decision=ClassApproval.Decision.CHANGES_REQUESTED,
            notes="More photos",
            decided_by=named_admin,
            decided_at=timezone.now(),
        )
        offering.submit_for_review()
        pipeline = offering.review_pipeline()
        assert _states(offering) == [
            ("submitted", "done"),
            ("guild_lead", "current"),
            ("admin", "ahead"),
            ("live", "ahead"),
        ]
        assert pipeline.is_bounced is False
        assert pipeline.note == ""

    def it_renders_a_cancelled_class_muted_with_its_last_known_strip(db):
        offering = ClassOfferingFactory(status=Status.CANCELLED, published_at=timezone.now())
        pipeline = offering.review_pipeline()
        assert pipeline.muted is True
        assert pipeline.headline == "Cancelled"
        assert _states(offering) == [("submitted", "done"), ("admin", "done"), ("live", "done")]

    def it_renders_an_archived_draft_muted_without_error(db):
        offering = ClassOfferingFactory(status=Status.ARCHIVED)
        pipeline = offering.review_pipeline()
        assert pipeline.muted is True
        assert pipeline.headline == "Archived"
        assert _states(offering) == [("submitted", "current"), ("admin", "ahead"), ("live", "ahead")]

    def it_renders_an_archived_pending_class_with_its_open_gate(db):
        offering = ClassOfferingFactory(status=Status.ARCHIVED, category=_guilded_category())
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        assert _states(offering)[:2] == [("submitted", "done"), ("guild_lead", "current")]

    def it_reads_legacy_rows_from_an_old_cycle_without_crashing(db):
        # A draft with an old APPROVED admin row and a guild row from a guild-less category.
        offering = ClassOfferingFactory(status=Status.DRAFT)
        ClassApproval.objects.create(
            class_offering=offering, role=ClassApproval.Role.ADMIN, decision=ClassApproval.Decision.APPROVED
        )
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        pipeline = offering.review_pipeline()
        assert [s.key for s in pipeline.steps] == ["submitted", "guild_lead", "admin", "live"]
        assert _states(offering) == [
            ("submitted", "done"),
            ("guild_lead", "ahead"),
            ("admin", "done"),
            ("live", "ahead"),
        ]
        assert pipeline.headline == "Not submitted yet"

    def it_formats_the_text_line_from_the_same_steps(db):
        offering = ClassOfferingFactory(status=Status.PENDING, category=_guilded_category())
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        assert offering.review_pipeline().text_line == "[✓] Submitted  [●] Guild lead  [ ] Admin  [ ] Live"

    def it_has_no_fill_for_a_single_step_strip(db):
        from classes.models import PipelineStep, ReviewPipeline

        one = ReviewPipeline(
            steps=(PipelineStep("submitted", "Submitted", "done"),),
            headline="",
            note="",
            is_live=False,
            is_bounced=False,
        )
        assert one.fill_percent == 0


def describe_pipeline_components():
    @pytest.fixture
    def half_way(db, named_admin):
        offering = ClassOfferingFactory(status=Status.PENDING, category=_guilded_category("Glass"))
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.GUILD_LEAD,
            decision=ClassApproval.Decision.APPROVED,
            decided_by=named_admin,
            decided_at=timezone.now(),
        )
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        return offering

    def it_renders_the_page_strip_with_the_headline_as_aria_label(half_way):
        html = render_to_string("classes/_components/review_pipeline.html", {"pipeline": half_way.review_pipeline()})
        assert 'aria-label="Waiting on an admin"' in html
        assert 'data-step="guild_lead"' in html and "pl-pipeline__step--done" in html
        assert 'data-step="admin"' in html and "pl-pipeline__step--current" in html
        assert "width: 67%" in html
        assert html.count("pl-pipeline__step ") == 4

    def it_renders_the_email_table_and_text_line_from_one_call(half_way):
        html = render_to_string("classes/emails/_review_pipeline.html", {"pipeline": half_way.review_pipeline()})
        text = render_to_string("classes/emails/_review_pipeline.txt", {"pipeline": half_way.review_pipeline()})
        assert "<table" in html and "<svg" not in html and "<link" not in html
        assert "Waiting on an admin" in html
        assert html.count("<td") == 4
        assert "[✓] Submitted  [✓] Guild lead  [●] Admin  [ ] Live" in text
        assert "Waiting on an admin" in text

    def it_renders_the_bounce_note_in_the_email_table(db, named_admin):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.ADMIN,
            decision=ClassApproval.Decision.CHANGES_REQUESTED,
            notes="Add the price.",
            decided_by=named_admin,
            decided_at=timezone.now(),
        )
        html = render_to_string("classes/emails/_review_pipeline.html", {"pipeline": offering.review_pipeline()})
        assert "Add the price." in html
        assert "&#8617;" in html

    def it_renders_the_page_strip_muted_for_an_archived_class(db):
        offering = ClassOfferingFactory(status=Status.ARCHIVED)
        html = render_to_string("classes/_components/review_pipeline.html", {"pipeline": offering.review_pipeline()})
        assert "pl-pipeline--muted" in html
        assert "pl-pipeline__headline--muted" in html


def describe_review_emails_carry_the_pipeline():
    @pytest.fixture(autouse=True)
    def _outbox(settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        mail.outbox = []

    def _instructor():
        return InstructorFactory(user=UserFactory(email="teacher@example.com"), instructor_slug="teach-pipe")

    def _bodies(message):
        html = next((alt for alt, kind in message.alternatives if kind == "text/html"), "")
        return message.body, html

    def it_shows_the_guild_step_current_in_the_lead_request_and_the_explainer(db):
        offering = ClassOfferingFactory(
            ready=True, status=Status.PENDING, instructor=_instructor(), category=_guilded_category("Metal")
        )
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        send_guild_lead_review_request(offering, row)
        assert len(mail.outbox) == 2
        for message in mail.outbox:
            text, html = _bodies(message)
            assert "[✓] Submitted  [●] Guild lead  [ ] Admin  [ ] Live" in text
            assert "Waiting on the guild lead (Metal)" in text
            assert "Waiting on the guild lead (Metal)" in html
            assert "[missing:" not in text and "[missing:" not in html

    def it_shows_the_admin_step_current_in_the_admin_request(db):
        from membership.models import AdminCapability

        holder = MemberFactory(_pre_signup_email="cms@example.com")
        holder.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        offering = ClassOfferingFactory(ready=True, status=Status.PENDING, instructor=_instructor())
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        send_admin_review_request(offering, row)
        explainer = next(m for m in mail.outbox if m.to == ["teacher@example.com"])
        text, html = _bodies(explainer)
        assert "[✓] Submitted  [●] Admin  [ ] Live" in text
        assert "Waiting on an admin" in html

    def it_shows_the_guild_step_done_in_the_validation_request(db, named_admin):
        offering = ClassOfferingFactory(ready=True, status=Status.PENDING, category=_guilded_category("Clay"))
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.GUILD_LEAD,
            decision=ClassApproval.Decision.APPROVED,
            decided_by=named_admin,
            decided_at=timezone.now(),
        )
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        # The validation request resolves to CLASS_APPROVER holders with a linked, email-bearing user.
        from membership.models import AdminCapability, Member

        holder_user = UserFactory(username="cms2@example.com", email="cms2@example.com")
        Member.objects.get(user=holder_user).admin_capabilities.create(
            capability=AdminCapability.Capability.CLASS_APPROVER
        )
        send_admin_validation_request(offering, row)
        text, html = _bodies(mail.outbox[0])
        assert "[✓] Submitted  [✓] Guild lead  [●] Admin  [ ] Live" in text
        assert "Waiting on an admin" in html
        assert "[missing:" not in html

    def it_shows_the_half_full_strip_in_the_lead_approved_email(db, named_admin):
        offering = ClassOfferingFactory(
            ready=True, status=Status.PENDING, instructor=_instructor(), category=_guilded_category("Wood")
        )
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        row.decide(ClassApproval.Decision.APPROVED, user=named_admin)
        offering.refresh_from_db()
        mail.outbox = []
        send_class_review_decision(offering, row)
        text, html = _bodies(mail.outbox[0])
        assert "[✓] Submitted  [✓] Guild lead  [●] Admin  [ ] Live" in text
        assert "Waiting on an admin" in html

    def it_shows_every_check_and_live_in_the_live_email(db, named_admin):
        offering = ClassOfferingFactory(ready=True, status=Status.PENDING, instructor=_instructor())
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        row.decide(ClassApproval.Decision.APPROVED, user=named_admin)
        offering.refresh_from_db()
        mail.outbox = []
        send_class_review_decision(offering, row)
        text, html = _bodies(mail.outbox[0])
        assert "[✓] Submitted  [✓] Admin  [✓] Live" in text
        assert "Live since" in html

    def it_shows_the_marked_step_and_the_note_in_the_changes_requested_email(db, named_admin):
        offering = ClassOfferingFactory(ready=True, status=Status.PENDING, instructor=_instructor())
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        row.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=named_admin, notes="Add the price.")
        offering.refresh_from_db()
        mail.outbox = []
        send_class_review_decision(offering, row)
        text, html = _bodies(mail.outbox[0])
        assert "[✓] Submitted  [↩] Admin  [ ] Live" in text
        assert "Changes requested by an admin" in text
        assert "Changes requested by an admin" in html
        assert html.count("Add the price.") >= 2  # the reviewer note block and the pipeline note
