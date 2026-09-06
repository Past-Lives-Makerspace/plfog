"""BDD specs for the tokenized (unauthenticated) class review view."""

from __future__ import annotations

from django.urls import reverse

from classes.factories import ClassOfferingFactory
from classes.models import ClassApproval, ClassOffering


def describe_class_review():
    def it_returns_200_with_valid_token(client, db):
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
        (row,) = offering.submit_for_review()
        response = client.get(reverse("classes:class_review", kwargs={"token": row.token}))
        assert response.status_code == 200

    def it_wraps_the_notes_field_in_a_themed_wrapper(client, db):
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
        (row,) = offering.submit_for_review()
        response = client.get(reverse("classes:class_review", kwargs={"token": row.token}))
        # The notes control is rendered through components/form_field.html, so it sits in the
        # theme-correct .pl-form-group wrapper instead of falling through to a bare white textarea.
        assert b"pl-form-group" in response.content
        assert b'name="notes"' in response.content

    def it_renders_the_not_awaiting_state_for_an_unknown_token_without_class_details(client, db):
        offering = ClassOfferingFactory(title="Secret Draft", status=ClassOffering.Status.DRAFT)
        response = client.get(reverse("classes:class_review", kwargs={"token": "not-a-real-token"}))
        assert response.status_code == 200
        assert b"not awaiting review" in response.content
        assert b"Secret Draft" not in response.content
        assert b'name="decision"' not in response.content
        assert offering.approvals.count() == 0

    def it_does_not_require_login(client, db):
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
        (row,) = offering.submit_for_review()
        # Unauthenticated client should reach the page
        response = client.get(reverse("classes:class_review", kwargs={"token": row.token}))
        assert response.status_code == 200

    def it_records_approved_decision_on_post(client, db):
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
        (row,) = offering.submit_for_review()
        response = client.post(
            reverse("classes:class_review", kwargs={"token": row.token}),
            {"decision": ClassApproval.Decision.APPROVED, "notes": ""},
        )
        assert response.status_code == 302
        row.refresh_from_db()
        assert row.decision == ClassApproval.Decision.APPROVED
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED

    def it_records_denial_on_post(client, db):
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
        (row,) = offering.submit_for_review()
        response = client.post(
            reverse("classes:class_review", kwargs={"token": row.token}),
            {"decision": ClassApproval.Decision.DENIED, "notes": "Not suitable."},
        )
        assert response.status_code == 302
        row.refresh_from_db()
        assert row.decision == ClassApproval.Decision.DENIED
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.DRAFT

    def it_shows_the_notes_error_once_when_declining_without_notes(client, db):
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
        (row,) = offering.submit_for_review()
        response = client.post(
            reverse("classes:class_review", kwargs={"token": row.token}),
            {"decision": ClassApproval.Decision.DENIED, "notes": ""},
        )
        assert response.status_code == 200
        # form_field.html renders the notes error inline; the top-of-form loop is scoped to
        # `decision` only, so the notes error must appear exactly once (not duplicated, not orphaned).
        assert response.content.decode().count("Please leave a note so the instructor knows what to change.") == 1
        row.refresh_from_db()
        assert row.decision == ""

    def it_ignores_post_when_decision_already_recorded(client, db):
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
        (row,) = offering.submit_for_review()
        # Record first decision
        client.post(
            reverse("classes:class_review", kwargs={"token": row.token}),
            {"decision": ClassApproval.Decision.APPROVED, "notes": ""},
        )
        # Second POST: decision guard blocks it; view re-renders (200) rather than redirecting
        response = client.post(
            reverse("classes:class_review", kwargs={"token": row.token}),
            {"decision": ClassApproval.Decision.DENIED, "notes": "Changed my mind"},
        )
        assert response.status_code == 200
        row.refresh_from_db()
        assert row.decision == ClassApproval.Decision.APPROVED


def describe_class_review_preview():
    def it_renders_the_student_preview_for_a_valid_token(client, db):
        offering = ClassOfferingFactory(ready=True, slug="prev-ok", status=ClassOffering.Status.PENDING)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        response = client.get(reverse("classes:class_review_preview", kwargs={"token": row.token}))
        assert response.status_code == 200
        assert b"Preview" in response.content  # the public detail preview banner
        assert offering.title.encode() in response.content

    def it_does_not_require_login(client, db):
        offering = ClassOfferingFactory(ready=True, slug="prev-anon", status=ClassOffering.Status.DRAFT)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        # Anonymous client (no force_login) still reaches the preview.
        response = client.get(reverse("classes:class_review_preview", kwargs={"token": row.token}))
        assert response.status_code == 200

    def it_returns_404_with_invalid_token(client, db):
        response = client.get(reverse("classes:class_review_preview", kwargs={"token": "not-a-real-token"}))
        assert response.status_code == 404

    def it_is_framable_same_origin(client, db):
        offering = ClassOfferingFactory(ready=True, slug="prev-frame", status=ClassOffering.Status.PENDING)
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        response = client.get(reverse("classes:class_review_preview", kwargs={"token": row.token}))
        assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


def describe_the_review_page_layout():
    """Pipeline and details lead, the decision follows, the preview closes the page."""

    def _review_html(client, db) -> str:
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT, title="Kiln Basics")
        (row,) = offering.submit_for_review()
        return client.get(reverse("classes:class_review", kwargs={"token": row.token})).content.decode()

    def it_puts_the_pipeline_and_details_above_the_decision_and_the_preview_last(client, db):
        html = _review_html(client, db)
        pipeline = html.index("Review Pipeline")
        details = html.index("Class Details")
        decision = html.index("Submit decision")
        preview = html.index("Student Preview")
        assert pipeline < details < decision < preview

    def it_frames_the_preview_chromeless_but_links_the_real_page_for_a_new_tab(client, db):
        offering = ClassOfferingFactory(ready=True, slug="layout-frame", status=ClassOffering.Status.DRAFT)
        (row,) = offering.submit_for_review()
        html = client.get(reverse("classes:class_review", kwargs={"token": row.token})).content.decode()
        preview_url = reverse("classes:class_review_preview", kwargs={"token": row.token})
        # The iframe strips the chrome; the "Open in a new tab" link deliberately does not,
        # so that opens the page a student actually lands on.
        assert f'src="{preview_url}?framed=1"' in html
        assert f'href="{preview_url}" target="_blank"' in html

    def it_lays_readiness_out_as_a_grid_with_a_count(client, db):
        # A class can only be submitted once it is ready, so build the pending review row
        # directly to reach the readiness card while the class is still awaiting a decision.
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.PENDING, title="Grid Readiness")
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        html = client.get(reverse("classes:class_review", kwargs={"token": row.token})).content.decode()
        assert "pl-readiness--grid" in html
        assert "pl-readiness-count" in html
        assert "5 of 5 ready" in html


def describe_the_framed_preview():
    """``?framed=1`` strips every chrome layer; without it the page is unchanged."""

    def _preview(client, offering, *, framed: bool):
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        url = reverse("classes:class_review_preview", kwargs={"token": row.token})
        return client.get(f"{url}?framed=1" if framed else url)

    def it_drops_the_sidebar_and_every_topbar_when_framed(client, db):
        offering = ClassOfferingFactory(ready=True, slug="framed-on", status=ClassOffering.Status.PENDING)
        html = _preview(client, offering, framed=True).content.decode()
        assert "hub-sidebar" not in html
        assert "pl-topbar" not in html
        assert "pl-public-topbar" not in html
        assert "cp-topbar" not in html
        assert offering.title in html  # the class page itself is still all there

    def it_keeps_the_chrome_without_the_flag(client, db):
        offering = ClassOfferingFactory(ready=True, slug="framed-off", status=ClassOffering.Status.PENDING)
        html = _preview(client, offering, framed=False).content.decode()
        assert "topbar" in html
        assert offering.title in html

    def it_ignores_the_flag_on_any_other_page(client, db, admin_user):
        # Only the preview honors it: no other surface can be stripped by a query param.
        client.force_login(admin_user)
        html = client.get(f"{reverse('hub_home')}?framed=1").content.decode()
        assert "hub-sidebar" in html
