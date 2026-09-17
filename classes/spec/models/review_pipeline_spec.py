"""BDD specs for ClassOffering.review_pipeline(): the strip every page and review email draws."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core import mail
from django.template.loader import render_to_string
from django.utils import timezone

from classes.emails import (
    _emit_instructor_review_explainer,
    send_admin_review_request,
    send_admin_validation_request,
    send_class_review_decision,
    send_guild_lead_review_request,
)
from classes.factories import (
    CategoryFactory,
    ClassApprovalFactory,
    ClassOfferingFactory,
    InstructorFactory,
    UserFactory,
)
from classes.models import ClassApproval, ClassOffering
from tests.membership.factories import GuildFactory, MemberFactory

Status = ClassOffering.Status
Decision = ClassApproval.Decision
Role = ClassApproval.Role


def _guilded_category(guild_name: str = "Woodshop"):
    lead = MemberFactory(_pre_signup_email=f"{guild_name.lower()}-lead@example.com")
    guild = GuildFactory(name=guild_name, guild_lead=lead)
    return CategoryFactory(guild=guild)


def _states(offering: ClassOffering) -> list[tuple[str, str]]:
    return [(step.key, step.state) for step in offering.review_pipeline().steps]


def _columns(offering: ClassOffering) -> list[tuple[str, str]]:
    """Each column's key and rolled-up state — the shape the page strip draws."""
    return [(column.key, column.state) for column in offering.review_pipeline().columns]


def _open_gates(offering: ClassOffering, *roles: str) -> None:
    for role in roles:
        ClassApprovalFactory(class_offering=offering, role=role)


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

    def it_marks_both_lanes_current_while_both_rows_are_open(db):
        offering = ClassOfferingFactory(status=Status.PENDING, category=_guilded_category())
        _open_gates(offering, Role.GUILD_LEAD, Role.ADMIN)
        pipeline = offering.review_pipeline()
        assert _states(offering) == [
            ("submitted", "done"),
            ("guild_lead", "current"),
            ("admin", "current"),
            ("live", "ahead"),
        ]
        assert _columns(offering) == [("submitted", "done"), ("review", "current"), ("live", "ahead")]
        assert pipeline.columns[1].is_parallel is True
        assert pipeline.columns[1].label == "Guild lead + Admin"
        assert pipeline.headline == "Waiting on the guild lead (Woodshop) and an admin"
        assert pipeline.steps[1].detail.startswith("Waiting since ")
        assert pipeline.fill_percent == 50

    def it_marks_only_the_guild_lead_current_when_the_admin_lane_is_the_only_one_open(db):
        # A lead-less category: one lane, and the review column is not parallel.
        offering = ClassOfferingFactory(status=Status.PENDING)
        _open_gates(offering, Role.ADMIN)
        pipeline = offering.review_pipeline()
        assert pipeline.columns[1].is_parallel is False
        assert pipeline.columns[1].label == "Admin"
        assert pipeline.headline == "Waiting on an admin"

    def it_marks_guild_done_and_admin_current_after_the_lead_approves(db, named_admin):
        offering = ClassOfferingFactory(status=Status.PENDING, category=_guilded_category())
        ClassApprovalFactory(
            class_offering=offering,
            role=Role.GUILD_LEAD,
            decision=Decision.APPROVED,
            decided_by=named_admin,
        )
        _open_gates(offering, Role.ADMIN)
        pipeline = offering.review_pipeline()
        assert _states(offering) == [
            ("submitted", "done"),
            ("guild_lead", "done"),
            ("admin", "current"),
            ("live", "ahead"),
        ]
        assert pipeline.headline == "Waiting on an admin"
        assert pipeline.steps[1].detail.startswith("Approved by Sam Reed, ")
        # Half of the parallel column has closed, so the connector is three quarters along.
        assert pipeline.fill_percent == 75

    def it_marks_the_admin_done_and_the_guild_lead_current_on_a_held_class(db, named_admin):
        """ "Approve, hold for the room check": the admin has answered, the lead has not."""
        offering = ClassOfferingFactory(status=Status.PENDING, category=_guilded_category())
        _open_gates(offering, Role.GUILD_LEAD)
        ClassApprovalFactory(
            class_offering=offering, role=Role.ADMIN, decision=Decision.APPROVED, decided_by=named_admin
        )
        pipeline = offering.review_pipeline()
        assert _states(offering) == [
            ("submitted", "done"),
            ("guild_lead", "current"),
            ("admin", "done"),
            ("live", "ahead"),
        ]
        assert pipeline.headline == "Waiting on the guild lead (Woodshop)"
        assert pipeline.fill_percent == 75

    def it_never_ticks_a_guild_lane_an_admin_published_over(db, named_admin):
        offering = ClassOfferingFactory(
            status=Status.PUBLISHED, category=_guilded_category(), published_at=timezone.now()
        )
        ClassApprovalFactory(class_offering=offering, role=Role.GUILD_LEAD, decision=Decision.OVERRIDDEN_BY_ADMIN)
        ClassApprovalFactory(
            class_offering=offering, role=Role.ADMIN, decision=Decision.APPROVED, decided_by=named_admin
        )
        pipeline = offering.review_pipeline()
        assert _states(offering) == [
            ("submitted", "done"),
            ("guild_lead", "overridden"),
            ("admin", "done"),
            ("live", "done"),
        ]
        assert pipeline.steps[1].marker != "✓"
        assert pipeline.steps[1].detail.startswith("Closed when an admin published")
        assert pipeline.fill_percent == 100

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

    def it_marks_the_admin_step_changes_requested_with_the_lead_lane_left_open(db, named_admin):
        offering = ClassOfferingFactory(status=Status.DRAFT, category=_guilded_category())
        _open_gates(offering, Role.GUILD_LEAD)
        ClassApprovalFactory(
            class_offering=offering,
            role=Role.ADMIN,
            decision=Decision.CHANGES_REQUESTED,
            notes="Set a price.",
            decided_by=named_admin,
        )
        pipeline = offering.review_pipeline()
        assert _states(offering) == [
            ("submitted", "done"),
            ("guild_lead", "ahead"),
            ("admin", "changes_requested"),
            ("live", "ahead"),
        ]
        assert _columns(offering) == [("submitted", "done"), ("review", "changes_requested"), ("live", "ahead")]
        assert pipeline.is_bounced is True
        assert pipeline.headline == "Changes requested by an admin"
        assert pipeline.note == "Set a price."

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
            ("admin", "current"),
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

    def it_formats_the_text_line_by_flattening_every_lane(db):
        offering = ClassOfferingFactory(status=Status.PENDING, category=_guilded_category())
        _open_gates(offering, Role.GUILD_LEAD, Role.ADMIN)
        assert offering.review_pipeline().text_line == "[✓] Submitted  [●] Guild lead  [●] Admin  [ ] Live"

    def it_names_an_unnamed_guild_lead_in_the_headline(db):
        # A guild-less category cannot open a guild lane in practice; a stale row can, and
        # the headline still has to name somebody.
        offering = ClassOfferingFactory(status=Status.PENDING, category=CategoryFactory(guild=None))
        _open_gates(offering, Role.GUILD_LEAD, Role.ADMIN)
        assert offering.review_pipeline().headline == "Waiting on the guild lead and an admin"

    def it_has_no_fill_for_a_strip_nothing_has_reached(db):
        """Not constructible from a real class — submitted is never "ahead" — but the guard is real."""
        from classes.models import PipelineColumn, PipelineStep, ReviewPipeline

        nothing = ReviewPipeline(
            columns=(
                PipelineColumn("submitted", (PipelineStep("submitted", "Submitted", "ahead"),)),
                PipelineColumn("live", (PipelineStep("live", "Live", "ahead"),)),
            ),
            headline="",
            note="",
            is_live=False,
            is_bounced=False,
        )
        assert nothing.fill_percent == 0

    def it_has_no_fill_for_a_single_column_strip(db):
        from classes.models import PipelineColumn, PipelineStep, ReviewPipeline

        one = ReviewPipeline(
            columns=(PipelineColumn("submitted", (PipelineStep("submitted", "Submitted", "done"),)),),
            headline="",
            note="",
            is_live=False,
            is_bounced=False,
        )
        assert one.fill_percent == 0
        assert one.steps == (PipelineStep("submitted", "Submitted", "done"),)


def describe_pipeline_components():
    @pytest.fixture
    def half_way(db, named_admin):
        offering = ClassOfferingFactory(status=Status.PENDING, category=_guilded_category("Glass"))
        ClassApprovalFactory(
            class_offering=offering,
            role=Role.GUILD_LEAD,
            decision=Decision.APPROVED,
            decided_by=named_admin,
        )
        ClassApprovalFactory(class_offering=offering, role=Role.ADMIN)
        return offering

    def it_renders_the_page_strip_with_the_headline_as_aria_label(half_way):
        html = render_to_string("classes/_components/review_pipeline.html", {"pipeline": half_way.review_pipeline()})
        assert 'aria-label="Waiting on an admin"' in html
        assert " title=" not in html  # Rule 19: no native title bubbles; the inline detail carries it
        assert "Approved by Sam Reed" in html
        assert 'data-step="guild_lead"' in html and "pl-pipeline__step--done" in html
        assert 'data-step="admin"' in html and "pl-pipeline__step--current" in html
        assert "width: 75%" in html
        assert html.count("pl-pipeline__step ") == 4

    def it_renders_the_email_table_and_text_line_from_one_call(half_way):
        html = render_to_string("classes/emails/_review_pipeline.html", {"pipeline": half_way.review_pipeline()})
        text = render_to_string("classes/emails/_review_pipeline.txt", {"pipeline": half_way.review_pipeline()})
        assert "<table" in html and "<svg" not in html and "<link" not in html
        assert "Waiting on an admin" in html
        # One outer strip plus one nested table per column, so the two review lanes stack
        # inside a single step instead of queueing as two steps of four.
        assert html.count("<table") == 4
        assert "These two run at the same time. Neither waits for the other." in html
        for label in ("Submitted", "Guild lead", "Admin", "Live"):
            assert f">{label}</div>" in html
        # The branch is one line in text, never ASCII art.
        assert "[✓] Submitted  [✓] Guild lead  [●] Admin  [ ] Live" in text
        assert "Waiting on an admin" in text
        assert len([line for line in text.splitlines() if line.strip()]) == 2

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
        row = ClassApprovalFactory(class_offering=offering, role=Role.GUILD_LEAD)
        admin_row = ClassApprovalFactory(class_offering=offering, role=Role.ADMIN)
        # The explainer is the submission's, not a lane's: one per submit, keyed on the admin
        # row (``classes.emails.send_review_requests``), so it is emitted alongside here.
        send_guild_lead_review_request(offering, row)
        _emit_instructor_review_explainer(offering, admin_row)
        assert len(mail.outbox) == 2
        for message in mail.outbox:
            text, html = _bodies(message)
            # Both lanes open, so the strip and the headline name both reviewers.
            assert "[✓] Submitted  [●] Guild lead  [●] Admin  [ ] Live" in text
            assert "Waiting on the guild lead (Metal) and an admin" in text
            assert "Waiting on the guild lead (Metal) and an admin" in html
            assert "[missing:" not in text and "[missing:" not in html

    def it_shows_the_admin_step_current_in_the_admin_request(db):
        from membership.models import AdminCapability

        holder = MemberFactory(_pre_signup_email="cms@example.com")
        holder.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        offering = ClassOfferingFactory(ready=True, status=Status.PENDING, instructor=_instructor())
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        send_admin_review_request(offering, row)
        _emit_instructor_review_explainer(offering, row)
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
        assert html.count("Add the price.") == 1  # the pipeline's note block is the one copy
        assert text.count("Add the price.") == 1
