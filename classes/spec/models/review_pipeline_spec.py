"""BDD specs for ClassOffering.review_pipeline(): the strip every page and review email draws.

One review per class (#1183), so the strip is Submitted, one review step, Live: "With your guild
lead" for a class whose guild has a lead, "With an admin" otherwise.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core import mail
from django.template.loader import render_to_string
from django.utils import timezone

from classes.emails import (
    send_admin_review_request,
    send_class_review_decision,
    send_guild_lead_review_request,
)
from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassApproval, ClassOffering
from membership.models import Member
from tests.membership.factories import GuildFactory, MemberFactory

Status = ClassOffering.Status


def _guilded_category(guild_name: str = "Woodshop"):
    # The lead has a login: the review request reaches only people who hold switches (#524).
    email = f"{guild_name.lower()}-lead@example.com"
    lead = UserFactory(username=email, email=email).member  # type: ignore[attr-defined]
    guild = GuildFactory(name=guild_name, guild_lead=lead)
    return CategoryFactory(guild=guild)


def _states(offering: ClassOffering) -> list[tuple[str, str]]:
    return [(step.key, step.state) for step in offering.review_pipeline().steps]


@pytest.fixture
def named_admin(db):
    return UserFactory(username="sam@example.com", email="sam@example.com", first_name="Sam", last_name="Reed")


def describe_review_pipeline():
    def it_renders_one_admin_review_step_without_a_guild_lead(db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        pipeline = offering.review_pipeline()
        assert [s.key for s in pipeline.steps] == ["submitted", "admin", "live"]
        assert pipeline.steps[1].label == "With an admin"
        assert pipeline.headline == "Not submitted yet"
        assert pipeline.is_bounced is False
        assert pipeline.is_live is False
        assert pipeline.fill_percent == 0

    def it_renders_one_guild_lead_review_step_with_a_guild_lead(db):
        offering = ClassOfferingFactory(status=Status.DRAFT, category=_guilded_category())
        pipeline = offering.review_pipeline()
        assert [s.key for s in pipeline.steps] == ["submitted", "guild_lead", "live"]
        assert pipeline.steps[1].label == "With your guild lead"

    def it_renders_the_admin_step_for_a_guild_with_no_lead(db):
        offering = ClassOfferingFactory(
            status=Status.DRAFT, category=CategoryFactory(guild=GuildFactory(name="Quiet", guild_lead=None))
        )
        assert [s.label for s in offering.review_pipeline().steps] == ["In draft", "With an admin", "Live"]

    def it_says_in_draft_not_submitted_on_a_plain_draft(db):
        # An admin read a gold dot beside "Submitted" on a draft as "this was submitted".
        offering = ClassOfferingFactory(status=Status.DRAFT, category=_guilded_category())
        pipeline = offering.review_pipeline()
        assert _states(offering) == [("submitted", "draft"), ("guild_lead", "ahead"), ("live", "ahead")]
        assert pipeline.steps[0].label == "In draft"
        assert pipeline.text_line == "[●] In draft  [ ] With your guild lead  [ ] Live"
        assert pipeline.headline == "Not submitted yet"
        assert pipeline.fill_percent == 0

    def it_says_submitted_once_the_class_is_pending(db):
        offering = ClassOfferingFactory(status=Status.PENDING)
        assert offering.review_pipeline().steps[0].label == "Submitted"

    def it_marks_the_guild_lead_current_while_their_row_is_open(db):
        offering = ClassOfferingFactory(status=Status.PENDING, category=_guilded_category())
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        pipeline = offering.review_pipeline()
        assert _states(offering) == [("submitted", "done"), ("guild_lead", "current"), ("live", "ahead")]
        assert pipeline.headline == "Waiting on the guild lead (Woodshop)"
        assert pipeline.steps[1].detail.startswith("Waiting since ")
        assert pipeline.fill_percent == 50

    def it_marks_the_admin_current_on_a_pending_class_with_no_rows(db):
        offering = ClassOfferingFactory(status=Status.PENDING)
        pipeline = offering.review_pipeline()
        assert _states(offering) == [("submitted", "done"), ("admin", "current"), ("live", "ahead")]
        assert pipeline.steps[1].detail == "Waiting on an admin"
        assert pipeline.headline == "Waiting on an admin"
        assert pipeline.fill_percent == 50

    def it_names_who_a_guilded_class_waits_on_when_it_has_no_rows(db):
        offering = ClassOfferingFactory(status=Status.PENDING, category=_guilded_category())
        pipeline = offering.review_pipeline()
        assert _states(offering) == [("submitted", "done"), ("guild_lead", "current"), ("live", "ahead")]
        assert pipeline.steps[1].detail == "Waiting on the guild lead"

    def it_marks_the_lead_step_done_once_their_approval_publishes(db, named_admin):
        offering = ClassOfferingFactory(
            status=Status.PUBLISHED, category=_guilded_category(), published_at=timezone.now()
        )
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.GUILD_LEAD,
            decision=ClassApproval.Decision.APPROVED,
            decided_by=named_admin,
            decided_at=timezone.now(),
        )
        pipeline = offering.review_pipeline()
        assert _states(offering) == [("submitted", "done"), ("guild_lead", "done"), ("live", "done")]
        assert pipeline.steps[1].detail.startswith("Approved by Sam Reed, ")
        assert pipeline.headline.startswith("Live since ")
        assert pipeline.fill_percent == 100

    def it_names_a_decider_by_their_member_when_the_account_has_no_name(db):
        decider = UserFactory(username="dana@example.com", email="dana@example.com", first_name="", last_name="")
        Member.objects.filter(user=decider).update(full_legal_name="Dana Weaver", preferred_name="")
        decider.refresh_from_db()
        offering = ClassOfferingFactory(
            status=Status.PUBLISHED, category=_guilded_category(), published_at=timezone.now()
        )
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.GUILD_LEAD,
            decision=ClassApproval.Decision.APPROVED,
            decided_by=decider,
            decided_at=timezone.now(),
        )
        assert offering.review_pipeline().steps[1].detail.startswith("Approved by Dana Weaver, ")

    def it_credits_the_admin_who_approved_a_guilded_class_early(db, named_admin):
        offering = ClassOfferingFactory(ready=True, status=Status.DRAFT, category=_guilded_category())
        offering.submit_for_review()
        offering.approve(named_admin)
        offering.refresh_from_db()
        pipeline = offering.review_pipeline()
        assert _states(offering) == [("submitted", "done"), ("admin", "done"), ("live", "done")]
        assert pipeline.steps[1].label == "With an admin"
        assert pipeline.steps[1].detail.startswith("Approved by Sam Reed, ")

    def it_credits_the_lead_when_their_approval_closed_an_admin_gate(db, named_admin):
        # A class carried into a guild while its admin gate was open holds both gates.
        offering = ClassOfferingFactory(ready=True, status=Status.PENDING, category=_guilded_category())
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        lead_row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        ClassApproval.objects.filter(pk=lead_row.pk).update(created_at=timezone.now() - timedelta(days=2))
        lead_row.refresh_from_db()
        lead_row.decide(ClassApproval.Decision.APPROVED, user=named_admin)
        offering.refresh_from_db()
        assert _states(offering) == [("submitted", "done"), ("guild_lead", "done"), ("live", "done")]

    def it_marks_everything_done_once_live(db):
        offering = ClassOfferingFactory(
            status=Status.PUBLISHED, category=_guilded_category(), published_at=timezone.now()
        )
        pipeline = offering.review_pipeline()
        assert _states(offering) == [("submitted", "done"), ("guild_lead", "done"), ("live", "done")]
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
        assert _states(offering) == [("submitted", "done"), ("guild_lead", "changes_requested"), ("live", "ahead")]
        assert pipeline.is_bounced is True
        assert pipeline.note == "Add safety notes"
        assert pipeline.steps[1].note == "Add safety notes"
        assert pipeline.steps[1].detail.startswith("Changes requested by Sam Reed, ")
        assert pipeline.headline == "Changes requested by the guild lead"
        assert pipeline.steps[1].marker == "↩"

    def it_marks_an_admin_decline_on_a_guilded_class_as_the_admin_step(db, named_admin):
        offering = ClassOfferingFactory(status=Status.DRAFT, category=_guilded_category())
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.ADMIN,
            decision=ClassApproval.Decision.DENIED,
            notes="Not this season.",
            decided_by=named_admin,
            decided_at=timezone.now(),
        )
        pipeline = offering.review_pipeline()
        assert _states(offering) == [("submitted", "done"), ("admin", "changes_requested"), ("live", "ahead")]
        assert pipeline.headline == "Declined by an admin"
        assert pipeline.steps[1].detail.startswith("Declined by Sam Reed, ")

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
        assert _states(offering) == [("submitted", "done"), ("guild_lead", "current"), ("live", "ahead")]
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
        assert _states(offering) == [("submitted", "ahead"), ("admin", "ahead"), ("live", "ahead")]
        assert pipeline.steps[0].label == "Submitted"

    def it_renders_an_archived_pending_class_with_its_open_gate(db):
        offering = ClassOfferingFactory(status=Status.ARCHIVED, category=_guilded_category())
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        assert _states(offering)[:2] == [("submitted", "done"), ("guild_lead", "current")]

    def it_reads_legacy_two_step_rows_as_one_step(db, named_admin):
        # Published under the old review: the lead approved, then an admin approved.
        offering = ClassOfferingFactory(
            status=Status.PUBLISHED, category=_guilded_category(), published_at=timezone.now()
        )
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.GUILD_LEAD,
            decision=ClassApproval.Decision.APPROVED,
            decided_at=timezone.now() - timedelta(days=1),
        )
        ClassApproval.objects.create(
            class_offering=offering,
            role=ClassApproval.Role.ADMIN,
            decision=ClassApproval.Decision.APPROVED,
            decided_by=named_admin,
            decided_at=timezone.now(),
        )
        assert _states(offering) == [("submitted", "done"), ("admin", "done"), ("live", "done")]

    def it_reads_legacy_rows_from_an_old_cycle_without_crashing(db):
        # A draft with an old APPROVED admin row and a guild row from a guild-less category.
        offering = ClassOfferingFactory(status=Status.DRAFT)
        ClassApproval.objects.create(
            class_offering=offering, role=ClassApproval.Role.ADMIN, decision=ClassApproval.Decision.APPROVED
        )
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        pipeline = offering.review_pipeline()
        assert [s.key for s in pipeline.steps] == ["submitted", "admin", "live"]
        assert _states(offering) == [("submitted", "done"), ("admin", "done"), ("live", "ahead")]
        assert pipeline.headline == "Not submitted yet"

    def it_formats_the_text_line_from_the_same_steps(db):
        offering = ClassOfferingFactory(status=Status.PENDING, category=_guilded_category())
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        assert offering.review_pipeline().text_line == "[✓] Submitted  [●] With your guild lead  [ ] Live"

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
    def with_lead(db):
        offering = ClassOfferingFactory(status=Status.PENDING, category=_guilded_category("Glass"))
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        return offering

    def it_renders_the_page_strip_with_the_headline_as_aria_label(with_lead):
        html = render_to_string("classes/_components/review_pipeline.html", {"pipeline": with_lead.review_pipeline()})
        assert 'aria-label="Waiting on the guild lead (Glass)"' in html
        assert " title=" not in html  # Rule 19: no native title bubbles; the inline detail carries it
        assert 'data-step="guild_lead"' in html and "pl-pipeline__step--current" in html
        assert 'data-step="admin"' not in html
        assert "With your guild lead" in html
        assert "width: 50%" in html
        assert html.count("pl-pipeline__step ") == 3

    def it_renders_the_email_table_and_text_line_from_one_call(with_lead):
        html = render_to_string("classes/emails/_review_pipeline.html", {"pipeline": with_lead.review_pipeline()})
        text = render_to_string("classes/emails/_review_pipeline.txt", {"pipeline": with_lead.review_pipeline()})
        assert "<table" in html and "<svg" not in html and "<link" not in html
        assert "Waiting on the guild lead (Glass)" in html
        assert html.count("<td") == 3
        assert "[✓] Submitted  [●] With your guild lead  [ ] Live" in text
        assert "Waiting on the guild lead (Glass)" in text

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

    def it_renders_a_draft_as_in_draft_with_no_gold_step_on_the_page(db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        html = render_to_string("classes/_components/review_pipeline.html", {"pipeline": offering.review_pipeline()})
        assert "pl-pipeline__step--draft" in html
        assert "pl-pipeline__step--current" not in html
        assert '<span class="pl-pipeline__label">In draft</span>' in html
        assert '<span class="pl-pipeline__label">Submitted</span>' not in html

    def it_renders_a_draft_as_in_draft_with_no_gold_step_in_the_email(db):
        offering = ClassOfferingFactory(status=Status.DRAFT)
        html = render_to_string("classes/emails/_review_pipeline.html", {"pipeline": offering.review_pipeline()})
        assert ">In draft</div>" in html
        assert "#EEB44B" not in html

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
        # The lead's request waits for the 8 AM digest (#894); the explainer goes at once.
        assert len(mail.outbox) == 1
        for message in mail.outbox:
            text, html = _bodies(message)
            assert "[✓] Submitted  [●] With your guild lead  [ ] Live" in text
            assert "Waiting on the guild lead (Metal)" in text
            assert "Waiting on the guild lead (Metal)" in html
            assert "[missing:" not in text and "[missing:" not in html

    def it_says_the_guild_lead_reviews_in_the_explainer(db):
        offering = ClassOfferingFactory(
            ready=True, status=Status.PENDING, instructor=_instructor(), category=_guilded_category("Leather")
        )
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        send_guild_lead_review_request(offering, row)
        text, html = _bodies(mail.outbox[0])
        for body in (text, html):
            assert "Your guild lead is reviewing your class. Once they approve it, it goes live." in body
            assert "An admin is reviewing" not in body

    def it_shows_the_admin_step_current_in_the_admin_request(db):
        from membership.models import AdminCapability

        holder = MemberFactory(_pre_signup_email="cms@example.com")
        holder.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        offering = ClassOfferingFactory(ready=True, status=Status.PENDING, instructor=_instructor())
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        send_admin_review_request(offering, row)
        explainer = next(m for m in mail.outbox if m.to == ["teacher@example.com"])
        text, html = _bodies(explainer)
        assert "[✓] Submitted  [●] With an admin  [ ] Live" in text
        assert "Waiting on an admin" in html
        assert "An admin is reviewing your class, since it has no guild lead." in text
        assert "An admin is reviewing your class, since it has no guild lead." in html

    def it_shows_every_check_and_live_when_the_guild_lead_approves(db, named_admin):
        offering = ClassOfferingFactory(
            ready=True, status=Status.PENDING, instructor=_instructor(), category=_guilded_category("Wood")
        )
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        row.decide(ClassApproval.Decision.APPROVED, user=named_admin)
        offering.refresh_from_db()
        mail.outbox = []
        send_class_review_decision(offering, row)
        assert mail.outbox[0].subject == f"Your class '{offering.title}' is live!"
        text, html = _bodies(mail.outbox[0])
        assert "[✓] Submitted  [✓] With your guild lead  [✓] Live" in text
        assert "Live since" in html

    def it_shows_every_check_and_live_in_the_live_email(db, named_admin):
        offering = ClassOfferingFactory(ready=True, status=Status.PENDING, instructor=_instructor())
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        row.decide(ClassApproval.Decision.APPROVED, user=named_admin)
        offering.refresh_from_db()
        mail.outbox = []
        send_class_review_decision(offering, row)
        text, html = _bodies(mail.outbox[0])
        assert "[✓] Submitted  [✓] With an admin  [✓] Live" in text
        assert "Live since" in html

    def it_shows_the_marked_step_and_the_note_in_the_changes_requested_email(db, named_admin):
        offering = ClassOfferingFactory(ready=True, status=Status.PENDING, instructor=_instructor())
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        row.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=named_admin, notes="Add the price.")
        offering.refresh_from_db()
        mail.outbox = []
        send_class_review_decision(offering, row)
        text, html = _bodies(mail.outbox[0])
        assert "[✓] Submitted  [↩] With an admin  [ ] Live" in text
        assert "Changes requested by an admin" in text
        assert "Changes requested by an admin" in html
        assert html.count("Add the price.") == 1  # the pipeline's note block is the one copy
        assert text.count("Add the price.") == 1
