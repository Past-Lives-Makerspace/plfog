"""BDD specs for the legacy ``/classes/admin/<pk>/…`` paths after the per-class merge.

Sixteen of them had a teaching-portal twin and the twins are one screen now, so each of those
paths is a dispatcher onto the merged route: a GET redirects, a POST goes straight through.
The rest of this file is about what must NOT have moved — the preview iframe, the
member-only prefix, and the two URLs that get stamped into persisted rows and emails.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    UserFactory,
)
from classes.models import ClassOffering

Status = ClassOffering.Status

#: Every ``classes:admin_class_*`` name the repo, an old email or a bookmark may still reverse.
#: Spelled out rather than derived, because the point of criterion 20 is that not one of them
#: went missing — a list built from the resolver could never notice a name it had lost.
ADMIN_CLASS_URL_NAMES = [
    "admin_class_create",
    "admin_class_detail",
    "admin_class_registrations",
    "admin_class_registrations_table",
    "admin_class_waitlist",
    "admin_class_discount_codes",
    "admin_class_emails",
    "admin_class_edit",
    "admin_class_email",
    "admin_class_approve",
    "admin_class_review",
    "admin_class_archive",
    "admin_class_cancel",
    "admin_class_sale",
    "admin_class_restore",
    "admin_class_unpublish",
    "admin_class_remind_lead",
    "admin_class_duplicate",
    "admin_class_duplicate_run",
    "admin_class_delete",
    "admin_class_hero_upload",
    "admin_class_image_upload",
    "admin_class_image_reorder",
    "admin_class_image_delete",
    "admin_class_image_alt",
]

#: The sixteen that dispatch, paired with the merged name a GET lands on.
LEGACY_PAIRS = [
    ("admin_class_detail", "teach_class_detail"),
    ("admin_class_registrations", "teach_class_registrations"),
    ("admin_class_registrations_table", "teach_class_registrations_table"),
    ("admin_class_waitlist", "teach_class_waitlist"),
    ("admin_class_discount_codes", "teach_class_discount_codes"),
    ("admin_class_emails", "teach_class_emails"),
    ("admin_class_edit", "teach_class_edit"),
    ("admin_class_email", "teach_class_email"),
    ("admin_class_cancel", "teach_class_cancel"),
    ("admin_class_sale", "teach_class_sale"),
    ("admin_class_duplicate_run", "teach_class_duplicate_run"),
    ("admin_class_hero_upload", "teach_class_hero_upload"),
    ("admin_class_image_upload", "teach_class_image_upload"),
    ("admin_class_image_reorder", "teach_class_image_reorder"),
    ("admin_class_image_delete", "teach_class_image_delete"),
    ("admin_class_image_alt", "teach_class_image_alt"),
]

REPO = Path(settings.BASE_DIR)


def describe_every_legacy_name_still_reverses():
    def it_reverses_all_twenty_five(db):
        """Criterion 20. A ``NoReverseMatch`` here is a 500 on a page nobody edited."""
        missing = []
        for name in ADMIN_CLASS_URL_NAMES:
            try:
                reverse(f"classes:{name}", kwargs={} if name == "admin_class_create" else {"pk": 1})
            except NoReverseMatch:
                missing.append(name)
        assert missing == []


def describe_a_get_on_a_legacy_path():
    @pytest.fixture
    def offering(db) -> ClassOffering:
        return ClassOfferingFactory(slug="legacy-target", status=Status.PUBLISHED)

    def it_302s_every_dispatched_path_onto_its_merged_route(admin_user, offering, client):
        # 302 and not 301: a permanent redirect is a one-way door a revert cannot close.
        client.force_login(admin_user)
        for legacy, merged in LEGACY_PAIRS:
            response = client.get(reverse(f"classes:{legacy}", kwargs={"pk": offering.pk}))
            assert response.status_code == 302, legacy
            assert response["Location"] == reverse(f"classes:{merged}", kwargs={"pk": offering.pk}), legacy

    def it_carries_a_filter_query_string_through(admin_user, offering, client):
        client.force_login(admin_user)
        url = reverse("classes:admin_class_registrations", kwargs={"pk": offering.pk})
        response = client.get(f"{url}?status=confirmed&mine=1")
        merged = reverse("classes:teach_class_registrations", kwargs={"pk": offering.pk})
        assert response["Location"] == f"{merged}?status=confirmed&mine=1"

    def it_carries_the_composers_step_through(admin_user, offering, client):
        # Lose this and an admin following an old "changes requested" email lands on step 1
        # of a five step composer with no idea which step the note was about.
        client.force_login(admin_user)
        url = reverse("classes:admin_class_edit", kwargs={"pk": offering.pk})
        response = client.get(f"{url}?step=3&missing=1")
        merged = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        assert response["Location"] == f"{merged}?step=3&missing=1"

    def it_still_refuses_a_viewer_with_no_claim_on_the_class(member_user, offering, client):
        # The hop is not a way around the gate: it lands on the merged view, which refuses.
        client.force_login(member_user)
        response = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk}), follow=True)
        assert response.status_code == 404


def describe_a_post_to_a_legacy_path():
    """Criterion 22, and the reason this is a dispatcher rather than a redirect table.

    ``RedirectView`` maps ``post = get``. The composer posts to the current URL with no
    ``action`` attribute, so an admin who had it open across the deploy would press Save, get
    a 302, and lose every session and FAQ row with no error — ``composer_draft.js`` keeps no
    formset rows.
    """

    def it_saves_a_whole_composer_post_with_its_sessions_and_faq_intact(admin_user, client, db):
        category = CategoryFactory()
        instructor = InstructorFactory()
        offering = ClassOfferingFactory(
            slug="legacy-save", status=Status.DRAFT, category=category, instructor=instructor
        )
        starts = timezone.now() + timedelta(days=30)
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}),
            {
                "title": "Saved Across The Hop",
                "category": category.pk,
                "instructor": instructor.pk,
                "description": "A long enough description to satisfy the readiness checklist here.",
                "prerequisites": "",
                "materials_included": "",
                "materials_to_bring": "",
                "safety_requirements": "",
                "age_minimum": "16",
                "age_guardian_note": "",
                "price_cents": "80.00",
                "member_discount_pct": "10",
                "capacity": "8",
                "scheduling_model": "fixed",
                "scheduling_type": "single_session",
                "flexible_note": "",
                "private_for_name": "",
                "video_url": "",
                "hero_crop": json.dumps({"x": 0, "y": 0, "w": 320, "h": 180}),
                "card_focus": json.dumps({"x": 50, "y": 50}),
                "sessions-TOTAL_FORMS": "1",
                "sessions-INITIAL_FORMS": "0",
                "sessions-MIN_NUM_FORMS": "0",
                "sessions-MAX_NUM_FORMS": "1000",
                "sessions-0-starts_at": starts.strftime("%Y-%m-%dT%H:%M"),
                "sessions-0-ends_at": (starts + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M"),
                "faq-TOTAL_FORMS": "1",
                "faq-INITIAL_FORMS": "0",
                "faq-MIN_NUM_FORMS": "0",
                "faq-MAX_NUM_FORMS": "1000",
                "faq-0-question": "What should I bring?",
                "faq-0-answer": "Closed-toe shoes and tied-back hair.",
            },
        )
        # A save redirects; what matters is that it SAVED, not where it landed.
        assert response.status_code == 302
        offering.refresh_from_db()
        assert offering.title == "Saved Across The Hop"
        assert offering.sessions.count() == 1
        assert offering.faqs.count() == 1
        assert offering.faqs.first().question == "What should I bring?"

    def it_dispatches_a_cancel_post_rather_than_bouncing_it(admin_user, client, db):
        offering = ClassOfferingFactory(slug="legacy-cancel", status=Status.PUBLISHED)
        starts = timezone.now() + timedelta(days=10)
        ClassSessionFactory(class_offering=offering, starts_at=starts, ends_at=starts + timedelta(hours=2))
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_class_cancel", kwargs={"pk": offering.pk}), {"reason": "The kiln is down."}
        )
        assert response.status_code == 302
        offering.refresh_from_db()
        assert offering.status == Status.CANCELLED
        assert offering.cancellation_reason == "The kiln is down."


def describe_the_preview_iframe():
    """Criterion 23. The preview is the one admin per-class path that did not move at all."""

    def it_keeps_its_path_its_view_and_its_frame_header(admin_user, client, db):
        offering = ClassOfferingFactory(slug="preview-intact", status=Status.PUBLISHED)
        client.force_login(admin_user)
        url = reverse("classes:class_preview", kwargs={"pk": offering.pk})
        assert url == f"/classes/admin/{offering.pk}/preview/"
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["X-Frame-Options"] == "SAMEORIGIN"

    def it_still_renders_chrome_free_under_framed(admin_user, client, db):
        offering = ClassOfferingFactory(slug="preview-framed", status=Status.PUBLISHED)
        client.force_login(admin_user)
        url = reverse("classes:class_preview", kwargs={"pk": offering.pk})
        framed = client.get(f"{url}?framed=1").content.decode()
        assert "hub-sidebar" not in framed

    def it_declares_the_legacy_entries_below_the_preview(db):
        # Patterns are first-match; a broader one above would swallow the preview.
        source = (REPO / "classes/urls.py").read_text()
        assert source.index('name="class_preview"') < source.index('name="admin_class_detail"')

    def it_registers_no_catch_all_under_the_admin_prefix(db):
        # A <path:subpath> catch-all would carry neither @xframe_options_sameorigin nor the
        # query string, so the iframe would come back chrome-on and un-framed.
        source = (REPO / "classes/urls.py").read_text()
        for match in re.finditer(r'path\(\s*"(admin/[^"]*)"', source):
            assert "<path:" not in match.group(1), match.group(1)


def describe_the_member_only_prefix():
    def it_still_lists_the_classes_admin_prefix(db):
        """Criterion 24. Asserted, not edited — the prefix is what hides the CMS off-host."""
        assert "/classes/admin/" in settings.MEMBER_ONLY_PATH_PREFIXES

    def it_flatly_404s_an_anonymous_visitor_on_a_public_host(client, db, settings):
        offering = ClassOfferingFactory(slug="offhost", status=Status.PUBLISHED)
        public = settings.PUBLIC_HOSTS[0]
        settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, public]
        response = client.get(
            reverse("classes:admin_class_detail", kwargs={"pk": offering.pk}),
            headers={"host": public},
        )
        # A flat 404, not a redirect to a login: the booking surface must not admit that the
        # CMS is there at all. The dispatcher is not a hole in that — the prefix wins first.
        assert response.status_code == 404


def describe_urls_stamped_into_rows_and_emails():
    """Criterion 39, and the worst rollback bug the plan's review caught.

    ``Notification.url`` and ``EventDelivery.url`` are persisted CharFields rendered once at
    creation, and the emails beside them cannot be recalled. A notice minted while this change
    is live and tapped after a revert must still land somewhere an admin can use, so these two
    keep reversing the LEGACY names: one 302 hop now, a native resolve after a revert.
    """

    def it_keeps_the_legacy_name_on_the_cancelled_class_admin_notice(db):
        source = (REPO / "classes/models.py").read_text()
        assert 'reverse("classes:admin_class_registrations", kwargs={"pk": self.pk})' in source

    def it_keeps_the_legacy_name_on_the_change_request_notice(db):
        source = (REPO / "classes/models.py").read_text()
        assert 'reverse("classes:admin_class_edit", kwargs={"pk": self.pk})' in source

    def it_puts_an_admin_on_the_roster_when_an_instructor_cancels_a_paid_class(admin_user, db):
        # Not vacuous: the stamped path has to actually reach the roster, hop and all.
        from django.contrib.auth import get_user_model

        from classes.factories import RegistrationFactory
        from classes.models import Registration

        instructor = InstructorFactory(user=UserFactory(username="cancels@example.com"))
        offering = ClassOfferingFactory(slug="paid-cancel", status=Status.PUBLISHED, instructor=instructor)
        starts = timezone.now() + timedelta(days=9)
        ClassSessionFactory(class_offering=offering, starts_at=starts, ends_at=starts + timedelta(hours=2))
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED, amount_paid_cents=8000)
        offering.cancel(get_user_model().objects.get(pk=instructor.user.pk), "No kiln.")

        from core.models import Notification

        stamped = [n.url for n in Notification.objects.all() if n.url and "/classes/admin/" in n.url]
        assert stamped, "the admin notice carried no /classes/admin/ URL"
        assert all(url.endswith(f"/classes/admin/{offering.pk}/registrations/") for url in stamped)


def describe_the_in_repo_sweep():
    """Criterion 19: nothing inside the repo takes the hop it does not have to."""

    def it_leaves_no_legacy_reverse_in_the_views(db):
        source = (REPO / "classes/views.py").read_text()
        names = set(re.findall(r'"classes:(admin_class_\w+)"', source))
        # ``admin_class_review`` has no merged twin — it is one of the eight admin-only
        # actions, which kept their own views at their own paths.
        assert names == {"admin_class_review"}

    def it_leaves_no_legacy_reverse_on_a_live_template(db):
        offenders = []
        for path in (REPO / "templates/classes").rglob("*.html"):
            for name in re.findall(r"'classes:(admin_class_\w+)'", path.read_text()):
                if name in {n for n, _ in LEGACY_PAIRS}:
                    offenders.append(f"{path.relative_to(REPO)}:{name}")
        assert offenders == []


def describe_the_dead_admin_templates():
    def it_leaves_no_per_class_template_under_the_admin_folder(db):
        """Criterion 1. Nothing under templates/classes/admin/ renders a per-class tab."""
        gone = [
            "templates/classes/admin/class_detail_base.html",
            "templates/classes/admin/class_detail.html",
            "templates/classes/admin/class_registrations.html",
            "templates/classes/admin/class_waitlist.html",
            "templates/classes/admin/class_emails.html",
            "templates/classes/admin/class_discount_codes.html",
            "templates/classes/admin/partials/cancel_class_form.html",
            "templates/classes/teach/class_detail_base.html",
            "templates/classes/teach/class_form.html",
        ]
        assert [name for name in gone if (REPO / name).exists()] == []

    def it_leaves_nothing_extending_a_deleted_base(db):
        offenders = []
        for path in (REPO / "templates/classes").rglob("*.html"):
            text = path.read_text()
            if "class_detail_base.html" in text:
                offenders.append(str(path.relative_to(REPO)))
        assert offenders == []
