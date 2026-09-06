"""BDD specs for the instructor side of the class lifecycle: the overview rows, the Classes list
facets and badge, the edit page pipeline and readiness cards, the workspace pipeline card, and the
honest submit messages."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    READY_DESCRIPTION,
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    UserFactory,
)
from classes.models import ClassApproval, ClassOffering
from tests.membership.factories import GuildFactory, MemberFactory

Status = ClassOffering.Status


def _messages(response) -> list[str]:
    return [m.message for m in get_messages(response.wsgi_request)]


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="lifecycle-teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher L", instructor_slug="teacher-l")


def _guilded_category(name: str = "Woodshop"):
    lead = MemberFactory(_pre_signup_email=f"{name.lower()}@example.com")
    return CategoryFactory(guild=GuildFactory(name=name, guild_lead=lead))


def _bounced(instructor, title="Bounced Class", notes="Add safety notes please") -> ClassOffering:
    offering = ClassOfferingFactory(instructor=instructor, title=title, status=Status.DRAFT)
    ClassApproval.objects.create(
        class_offering=offering,
        role=ClassApproval.Role.GUILD_LEAD,
        decision=ClassApproval.Decision.CHANGES_REQUESTED,
        notes=notes,
        decided_at=timezone.now(),
    )
    return offering


def _edit_payload(offering, cat) -> dict:
    return {
        "title": offering.title,
        "category": cat.pk,
        "description": READY_DESCRIPTION,
        "prerequisites": "",
        "materials_included": "",
        "materials_to_bring": "",
        "safety_requirements": "",
        "age_guardian_note": "",
        "price_cents": offering.price_cents,
        "member_discount_pct": offering.member_discount_pct,
        "capacity": offering.capacity,
        "scheduling_model": "flexible",
        "sale_kind": "percent",
        "scheduling_type": "single_session",
        "flexible_note": "We will find a time together.",
        "recurring_pattern": "",
        "sessions-TOTAL_FORMS": "0",
        "sessions-INITIAL_FORMS": "0",
        "sessions-MIN_NUM_FORMS": "0",
        "sessions-MAX_NUM_FORMS": "1000",
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
        "images-TOTAL_FORMS": "0",
        "images-INITIAL_FORMS": "0",
        "images-MIN_NUM_FORMS": "0",
        "images-MAX_NUM_FORMS": "1000",
        "action": "submit",
    }


def describe_teach_overview_rows():
    def it_lists_bounced_classes_first_with_the_note_and_fix_and_resubmit(instructor_fixture, client):
        bounced = _bounced(instructor_fixture)
        ClassOfferingFactory(instructor=instructor_fixture, title="Plain Draft", status=Status.DRAFT)
        with_lead = ClassOfferingFactory(
            instructor=instructor_fixture, title="Lead Has It", status=Status.PENDING, category=_guilded_category()
        )
        ClassApproval.objects.create(class_offering=with_lead, role=ClassApproval.Role.GUILD_LEAD)
        ClassOfferingFactory(instructor=instructor_fixture, title="Admin Has It", status=Status.PENDING)
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_overview"))
        assert resp.context["bounced_classes"] == [bounced]
        assert resp.context["stats"]["attention"] == 4
        html = resp.content.decode()
        assert "Fix and resubmit" in html
        assert "The guild lead asked for changes: Add safety notes please" in html
        assert "pl-lifecycle-badge--changes_requested" in html
        assert "With guild lead (Woodshop)" in html
        assert "Awaiting admin" in html
        assert "Awaiting admin review" not in html
        assert html.index("Bounced Class") < html.index("Plain Draft")


def describe_teach_classes_list():
    def it_renders_facets_with_counts_and_the_badge(instructor_fixture, client):
        _bounced(instructor_fixture)
        live = ClassOfferingFactory(instructor=instructor_fixture, title="Live Class", status=Status.PUBLISHED)
        start = timezone.now() + timedelta(days=2)
        ClassSessionFactory(class_offering=live, starts_at=start, ends_at=start + timedelta(hours=2))
        ClassOfferingFactory(instructor=instructor_fixture, title="Gone Class", status=Status.CANCELLED)
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_dashboard"))
        chips = {row.label: row.count for row in resp.context["facets"]}
        assert chips == {
            "All": 3,
            "Needs attention": 1,
            "In review": 0,
            "Upcoming": 1,
            "Completed": 0,
            "Cancelled": 1,
        }
        html = resp.content.decode()
        assert "pl-lifecycle-badge--upcoming" in html
        assert "pl-lifecycle-badge--cancelled" in html
        assert ">Open<" in html

    def it_filters_by_facet_and_shows_the_empty_state(instructor_fixture, client):
        ClassOfferingFactory(instructor=instructor_fixture, title="Draft Only", status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_dashboard") + "?facet=upcoming")
        assert b"Draft Only" not in resp.content
        assert b"No classes here yet." in resp.content
        assert resp.context["selected_facet"].key == "upcoming"

    def it_keeps_the_first_class_empty_state_without_facets(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_dashboard"))
        assert b"Create your first class" in resp.content
        assert b"pl-facets" not in resp.content


def describe_edit_gate():
    def it_sends_a_cancelled_class_back_to_the_list(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.CANCELLED)
        client.force_login(instructor_fixture.user)
        resp = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302
        assert resp.url == reverse("classes:teach_dashboard")
        assert "Cancelled and archived classes can only be edited by an admin." in _messages(resp)

    def it_still_opens_a_bounced_draft(instructor_fixture, client):
        offering = _bounced(instructor_fixture)
        client.force_login(instructor_fixture.user)
        assert client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).status_code == 200


def describe_query_counts():
    def it_holds_the_query_count_constant_on_the_list_and_overview(instructor_fixture, client):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        client.force_login(instructor_fixture.user)
        for i in range(2):
            _bounced(instructor_fixture, title=f"Bounced {i}", notes=f"Note {i}")
        pages = ("classes:teach_dashboard", "classes:teach_overview")
        for name in pages:
            # Warm-up: the first hub request creates the member's billing tab row
            # (savepoint, insert, release), which is first-request work, not row cost.
            client.get(reverse(name))
        counts_at_two = {}
        for name in pages:
            with CaptureQueriesContext(connection) as ctx:
                resp = client.get(reverse(name))
            assert resp.status_code == 200 and b"Note 1" in resp.content
            counts_at_two[name] = len(ctx)
        for i in range(2, 10):
            _bounced(instructor_fixture, title=f"Bounced {i}", notes=f"Note {i}")
        for name in pages:
            with CaptureQueriesContext(connection) as ctx:
                resp = client.get(reverse(name))
            assert b"Note 9" in resp.content
            assert len(ctx) == counts_at_two[name], name


def describe_edit_page_cards():
    def it_shows_the_pipeline_card_with_the_note_on_a_bounced_class(instructor_fixture, client):
        offering = _bounced(instructor_fixture, notes="More photos please")
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert "Where Your Class Is" in html
        assert "Changes requested by the guild lead" in html
        assert "More photos please" in html
        assert "Fix the notes below and submit again." in html
        assert "Ready to Submit?" in html

    def it_shows_not_submitted_yet_on_a_plain_draft(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert "Not submitted yet" in html
        assert "pl-pipeline__step--current" in html
        assert "Reviewer&#x27;s note" not in html and "Reviewer's note" not in html

    def it_lists_the_readiness_items_linking_to_their_fields(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, image="", gallery=0)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert 'href="#hero-preview">Add a hero photo.</a>' in html
        assert 'href="#gallery-manager">Add one gallery photo.</a>' in html
        assert 'href="#class-dates">Add at least one date.</a>' in html
        assert 'id="class-dates"' in html

    def it_names_the_stage_on_a_pending_class(instructor_fixture, client):
        offering = ClassOfferingFactory(
            instructor=instructor_fixture, status=Status.PENDING, category=_guilded_category()
        )
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert "Waiting on the guild lead (Woodshop)" in html
        assert "You can still edit it until it is approved." in html
        assert "pending admin review" not in html
        assert "Ready to Submit?" not in html

    def it_shows_no_cards_on_the_create_form(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        assert "Where Your Class Is" not in html
        assert "Ready to Submit?" not in html


def describe_workspace_overview_card():
    def it_shows_the_pipeline_card_while_in_review_and_the_badge_once_live(instructor_fixture, client):
        pending = ClassOfferingFactory(instructor=instructor_fixture, status=Status.PENDING)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_detail", kwargs={"pk": pending.pk})).content.decode()
        assert "Where Your Class Is" in html
        assert "pl-lifecycle-badge--awaiting_admin" in html
        live = ClassOfferingFactory(instructor=instructor_fixture, status=Status.PUBLISHED)
        html = client.get(reverse("classes:teach_class_detail", kwargs={"pk": live.pk})).content.decode()
        assert "Where Your Class Is" not in html
        assert "pl-lifecycle-badge--upcoming" in html

    def it_relabels_submit_to_fix_and_resubmit_on_a_bounced_class(instructor_fixture, client):
        offering = _bounced(instructor_fixture)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert "Fix and resubmit" in html
        assert "Add safety notes please" in html

    def it_shows_the_reason_on_a_cancelled_class(instructor_fixture, client):
        offering = ClassOfferingFactory(
            instructor=instructor_fixture, status=Status.CANCELLED, cancellation_reason="Kiln broke"
        )
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert "Cancelled: Kiln broke" in html


def describe_honest_submit_messages():
    def it_names_the_guild_lead_on_quick_submit(instructor_fixture, client):
        offering = ClassOfferingFactory(
            ready=True, instructor=instructor_fixture, title="Forge", status=Status.DRAFT, category=_guilded_category()
        )
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_submit", kwargs={"pk": offering.pk}))
        assert "Submitted “Forge” for review by the guild lead (Woodshop)." in _messages(resp)

    def it_names_an_admin_on_quick_submit_without_a_lead(instructor_fixture, client):
        offering = ClassOfferingFactory(ready=True, instructor=instructor_fixture, title="Solo", status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_submit", kwargs={"pk": offering.pk}))
        assert "Submitted “Solo” for review by an admin." in _messages(resp)

    def it_lists_the_failing_items_on_an_unready_quick_submit(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_submit", kwargs={"pk": offering.pk}))
        assert "Not ready to submit: Write a short description. Add at least one date." in _messages(resp)

    def it_names_the_guild_lead_on_the_edit_page_submit(instructor_fixture, client):
        cat = _guilded_category("Glass")
        offering = ClassOfferingFactory(instructor=instructor_fixture, title="Bead", status=Status.DRAFT, category=cat)
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _edit_payload(offering, cat),
        )
        assert resp.status_code == 302
        assert "Submitted “Bead” for review by the guild lead (Glass)." in _messages(resp)

    def it_names_the_first_gate_on_the_create_page_submit(instructor_fixture, client):
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        cat = _guilded_category("Metal")
        buf = BytesIO()
        Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, "PNG")
        payload = _edit_payload(
            ClassOfferingFactory.build(title="Anvil", price_cents=5000, member_discount_pct=10, capacity=6), cat
        )
        payload["image"] = SimpleUploadedFile("hero.png", buf.getvalue(), content_type="image/png")
        payload["gallery_images"] = [
            SimpleUploadedFile("g.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, content_type="image/png")
        ]
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_create"), payload)
        assert resp.status_code == 302
        assert "Submitted “Anvil” for review by the guild lead (Metal)." in _messages(resp)
