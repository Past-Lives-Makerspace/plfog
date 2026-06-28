"""Generate full-page screenshots of every copy-bearing CMS page, for copy review.

This is not an assertion test — it's a capture tool that borrows the e2e browser
harness (a real Chromium driven against a live Django server, plus the genuine
``login_via_code`` flow). It seeds a representative dataset, signs in as one
member who is *both* an admin and an instructor, then walks the book CMS across
both surfaces and saves a full-page PNG of each page:

- **public / book surface** — catalog, class detail, registration, /account/
- **members surface** — the ``/classes/admin/`` and ``/classes/teach/`` dashboards

(The SurfaceMiddleware serves each set only on its own host, so we flip
``settings.PUBLIC_HOSTS`` between the two passes — the middleware reads it per
request, exactly as the a11y spec does.)

The PNGs and an ``index.html`` contact sheet land in ``SHOT_DIR`` (default:
``<repo>/screenshots/``). Open ``screenshots/index.html`` to skim them all.

Dormant by default — it only runs when ``CAPTURE_SCREENSHOTS`` is set, so it
never fires during the ordinary ``pytest -m e2e`` CI sweep. Run it with:

    scripts/capture-cms-screenshots.sh

or directly:

    CAPTURE_SCREENSHOTS=1 pytest -m e2e tests/e2e/screenshots_spec.py
"""

from __future__ import annotations

import html
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    DiscountCodeFactory,
    RegistrationFactory,
)
from classes.models import ClassOffering, Registration, RegistrationQuestion
from membership.models import Member, MembershipPlan

# Whole module is opt-in: it loads ~40 pages and writes files, so we keep it out
# of the default e2e run. The runner script sets this for you.
pytestmark = pytest.mark.skipif(
    not os.environ.get("CAPTURE_SCREENSHOTS"),
    reason="screenshot generator — run scripts/capture-cms-screenshots.sh (sets CAPTURE_SCREENSHOTS=1)",
)

ADMIN_EMAIL = "studio.lead@example.com"


def _seed() -> dict[str, object]:
    """Create a representative slice of CMS data and return the handles we need.

    One member is the admin *and* the instructor of the seeded classes, so a
    single login reaches every dashboard. Pages are populated (a published
    class with sign-ups, one awaiting approval, a category, discount codes, a
    registration question) so the copy is reviewed against realistic content
    rather than empty states.
    """
    plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})

    user, _ = get_user_model().objects.get_or_create(username=ADMIN_EMAIL, defaults={"email": ADMIN_EMAIL})
    member, _ = Member.objects.update_or_create(
        user=user,
        defaults={
            "full_legal_name": "Robin Maker",
            "membership_plan": plan,
            "status": Member.Status.ACTIVE,
            "fog_role": Member.FogRole.ADMIN,
            "instructor_slug": "robin-maker",
            "about_me": "Longtime metalsmith and studio lead. Teaches casting and fabrication.",
        },
    )

    category = CategoryFactory(name="Metalworking", slug="metalworking")

    published = ClassOfferingFactory(
        title="Intro to Lost-Wax Casting",
        slug="intro-to-lost-wax-casting",
        category=category,
        instructor=member,
        status=ClassOffering.Status.PUBLISHED,
        is_private=False,
        price_cents=4500,
        capacity=8,
        description=(
            "Carve a model in wax, invest it, burn it out, and pour molten bronze to cast your own "
            "small sculpture. No experience needed — all tools and materials provided."
        ),
    )
    base = timezone.now() + timedelta(days=10)
    ClassSessionFactory(class_offering=published, starts_at=base, ends_at=base + timedelta(hours=3))

    # A class awaiting approval, so the admin overview / classes list show a
    # non-published state with its own copy.
    ClassOfferingFactory(
        title="Beginner Forge Welding",
        slug="beginner-forge-welding",
        category=category,
        instructor=member,
        status=ClassOffering.Status.PENDING,
        is_private=False,
        price_cents=6000,
        description="Heat, hammer, and join steel at the forge. Safety gear and steel stock included.",
    )

    # A spread of registration states populates the registration list pages.
    RegistrationFactory(
        class_offering=published, first_name="Avery", last_name="Lim", status=Registration.Status.CONFIRMED
    )
    RegistrationFactory(
        class_offering=published, first_name="Sam", last_name="Cole", status=Registration.Status.CONFIRMED
    )
    waitlisted = RegistrationFactory(
        class_offering=published, first_name="Dana", last_name="Reyes", status=Registration.Status.WAITLISTED
    )

    # One global code and one class-scoped code (the latter is editable from the
    # teaching portal because it belongs to this instructor's class).
    global_code = DiscountCodeFactory(code="WELCOME10", description="10% off any class", discount_pct=10)
    class_code = DiscountCodeFactory(
        code="CASTING20", description="20% off lost-wax casting", discount_pct=20, class_offering=published
    )

    question = RegistrationQuestion.objects.create(
        prompt="Do you have any allergies or access needs we should know about?",
        is_active=True,
    )

    confirmed = published.registrations.filter(status=Registration.Status.CONFIRMED).first()

    return {
        "instructor": member,
        "category": category,
        "published": published,
        "registration": confirmed or waitlisted,
        "global_code": global_code,
        "class_code": class_code,
        "question": question,
    }


def _public_pages(d: dict[str, object]) -> list[tuple[str, str]]:
    """(label, path) for pages a visitor/registrant sees on the book surface."""
    pub = d["published"]
    return [
        ("Public — class catalog", reverse("classes:public_list")),
        ("Public — browse category", reverse("classes:public_category", kwargs={"slug": d["category"].slug})),
        (
            "Public — instructor page",
            reverse("classes:public_instructor", kwargs={"slug": d["instructor"].instructor_slug}),
        ),
        ("Public — class detail", reverse("classes:public_class_detail", kwargs={"slug": pub.slug})),
        ("Public — registration form", reverse("classes:register", kwargs={"slug": pub.slug})),
        ("Account — overview", reverse("account:overview")),
        ("Account — order history", reverse("account:history")),
        ("Account — receipts", reverse("account:receipts")),
        ("Account — profile", reverse("account:profile")),
        ("Account — find my registrations", reverse("account:lookup")),
    ]


def _members_pages(d: dict[str, object]) -> list[tuple[str, str]]:
    """(label, path) for the admin + teaching dashboards on the members surface."""
    pub_pk = d["published"].pk
    return [
        # CMS admin
        ("Admin — overview", reverse("classes:admin_overview")),
        ("Admin — all classes", reverse("classes:admin_classes")),
        ("Admin — new class", reverse("classes:admin_class_create")),
        ("Admin — class detail", reverse("classes:admin_class_detail", kwargs={"pk": pub_pk})),
        ("Admin — edit class", reverse("classes:admin_class_edit", kwargs={"pk": pub_pk})),
        ("Admin — class preview", reverse("classes:class_preview", kwargs={"pk": pub_pk})),
        ("Admin — class registrations", reverse("classes:admin_class_registrations", kwargs={"pk": pub_pk})),
        ("Admin — class waitlist", reverse("classes:admin_class_waitlist", kwargs={"pk": pub_pk})),
        ("Admin — class discount codes", reverse("classes:admin_class_discount_codes", kwargs={"pk": pub_pk})),
        ("Admin — email class", reverse("classes:admin_class_email", kwargs={"pk": pub_pk})),
        ("Admin — class emails (welcome)", reverse("classes:admin_class_emails", kwargs={"pk": pub_pk})),
        ("Admin — all registrations", reverse("classes:admin_registrations")),
        (
            "Admin — registration detail",
            reverse("classes:admin_registration_detail", kwargs={"pk": d["registration"].pk}),
        ),
        ("Admin — categories", reverse("classes:admin_categories")),
        ("Admin — new category", reverse("classes:admin_category_create")),
        ("Admin — edit category", reverse("classes:admin_category_edit", kwargs={"pk": d["category"].pk})),
        ("Admin — discount codes", reverse("classes:admin_discount_codes")),
        ("Admin — new discount code", reverse("classes:admin_discount_code_create")),
        ("Admin — edit discount code", reverse("classes:admin_discount_code_edit", kwargs={"pk": d["global_code"].pk})),
        ("Admin — registration questions", reverse("classes:admin_registration_questions")),
        ("Admin — new question", reverse("classes:admin_registration_question_create")),
        ("Admin — edit question", reverse("classes:admin_registration_question_edit", kwargs={"pk": d["question"].pk})),
        ("Admin — settings hub", reverse("classes:admin_settings_hub")),
        ("Admin — settings (waivers)", reverse("classes:admin_settings")),
        ("Admin — activity feed", reverse("classes:admin_activity")),
        # Teaching portal
        ("Teach — overview", reverse("classes:teach_overview")),
        ("Teach — my classes", reverse("classes:teach_dashboard")),
        ("Teach — new class", reverse("classes:teach_class_create")),
        ("Teach — class detail", reverse("classes:teach_class_detail", kwargs={"pk": pub_pk})),
        ("Teach — edit class", reverse("classes:teach_class_edit", kwargs={"pk": pub_pk})),
        ("Teach — class registrations", reverse("classes:teach_class_registrations", kwargs={"pk": pub_pk})),
        ("Teach — class waitlist", reverse("classes:teach_class_waitlist", kwargs={"pk": pub_pk})),
        ("Teach — class discount codes", reverse("classes:teach_class_discount_codes", kwargs={"pk": pub_pk})),
        ("Teach — class emails", reverse("classes:teach_class_emails", kwargs={"pk": pub_pk})),
        ("Teach — all registrations", reverse("classes:teach_registrations")),
        ("Teach — discount codes", reverse("classes:teach_discount_codes")),
        ("Teach — new discount code", reverse("classes:teach_discount_code_create")),
        ("Teach — edit discount code", reverse("classes:teach_discount_code_edit", kwargs={"pk": d["class_code"].pk})),
        ("Teach — profile", reverse("classes:teach_profile")),
    ]


def _write_index(out_dir: Path, results: list[dict[str, object]]) -> None:
    """Write a single scrollable index.html that labels every captured page."""
    rows: list[str] = []
    current_section = None
    for r in results:
        section = str(r["surface"])
        if section != current_section:
            rows.append(f"<h2>{html.escape(section)}</h2>")
            current_section = section
        label = html.escape(str(r["label"]))
        path = html.escape(str(r["path"]))
        if r["ok"]:
            img = html.escape(str(r["file"]))
            body = f'<a href="{img}"><img loading="lazy" src="{img}" alt="{label}"></a>'
        else:
            body = f'<p class="err">Could not capture: {html.escape(str(r["note"]))}</p>'
        rows.append(f'<section><h3>{label}</h3><p class="path">{path}</p>{body}</section>')

    ok = sum(1 for r in results if r["ok"])
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>CMS copy review — screenshots</title>
<style>
  body {{ font: 15px/1.5 system-ui, sans-serif; margin: 0 auto; max-width: 1100px; padding: 24px; color: #1a1a1a; }}
  h1 {{ margin-bottom: 4px; }}
  .meta {{ color: #666; margin-bottom: 32px; }}
  h2 {{ margin-top: 48px; border-bottom: 2px solid #ddd; padding-bottom: 6px; }}
  section {{ margin: 28px 0; }}
  h3 {{ margin: 0 0 2px; }}
  .path {{ margin: 0 0 10px; color: #888; font-family: ui-monospace, monospace; font-size: 13px; }}
  img {{ max-width: 100%; border: 1px solid #ccc; border-radius: 6px; display: block; }}
  .err {{ color: #b00; font-style: italic; }}
</style></head>
<body>
<h1>CMS copy review</h1>
<p class="meta">{ok} of {len(results)} pages captured. Click any image to open it full size.</p>
{chr(10).join(rows)}
</body></html>
"""
    (out_dir / "index.html").write_text(doc, encoding="utf-8")


def describe_cms_screenshots():
    def it_captures_every_cms_page(live_server, page, login_via_code, settings):
        out_dir = Path(os.environ.get("SHOT_DIR", "screenshots")).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        page.set_viewport_size({"width": 1366, "height": 900})

        data = _seed()
        login_via_code(ADMIN_EMAIL)

        host = urlparse(live_server.url).hostname
        results: list[dict[str, object]] = []
        counter = 0

        def capture(surface: str, label: str, path: str) -> None:
            nonlocal counter
            counter += 1
            slug = "".join(c if c.isalnum() else "-" for c in label.lower()).strip("-")
            filename = f"{counter:02d}-{slug}.png"
            record: dict[str, object] = {"surface": surface, "label": label, "path": path, "file": filename}
            try:
                page.goto(f"{live_server.url}{path}", wait_until="networkidle", timeout=15000)
                page.wait_for_timeout(250)  # let any entrance transitions settle
                page.screenshot(path=str(out_dir / filename), full_page=True)
                record["ok"] = True
            except Exception as exc:  # noqa: BLE001 — one bad page must not abort the run
                record["ok"] = False
                record["note"] = f"{type(exc).__name__}: {exc}".splitlines()[0][:200]
            results.append(record)

        # Pass 1 — public / book surface (host is treated as public).
        settings.PUBLIC_HOSTS = [host]
        for label, path in _public_pages(data):
            capture("Public & account (book surface)", label, path)

        # Pass 2 — members surface (host is no longer public, so admin/teach resolve).
        settings.PUBLIC_HOSTS = ["book.pastlives.space"]
        for label, path in _members_pages(data):
            capture("Admin & teaching (members surface)", label, path)

        _write_index(out_dir, results)

        captured = sum(1 for r in results if r["ok"])
        print(f"\nWrote {captured}/{len(results)} screenshots to {out_dir}\nOpen {out_dir / 'index.html'}")
        # Surface failures loudly but don't fail the run for one stubborn page.
        assert captured, "no pages captured — check the dev harness and seed data"
