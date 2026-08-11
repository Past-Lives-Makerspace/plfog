"""Regenerate the help-center screenshots referenced by help articles (§9).

Not an assertion test — a capture tool on the e2e browser harness. It seeds the
shared screenshot dataset plus help-specific extras, signs in as one account per
role (member / guild lead / instructor / admin), and walks every ``ShotSpec``
declared in ``membership.help_content.ARTICLES``:

- element shots crop to the spec's CSS selector, padded 16px so the crop keeps
  visual context;
- selector-less shots are framed 1200×800 viewport captures, or ``full_page``
  when the spec asks for it.

PNGs land at ``static/help/<article-slug>/<file>`` (committed — WhiteNoise
serves them unhashed, see §9), and a review contact sheet lands at
``<SHOT_DIR>/help/index.html``.

Until phase 6 seeds ``ARTICLES`` with shot lists, there is nothing to capture
and the run reports zero shots defined and passes trivially.

Dormant by default — it only runs when ``CAPTURE_HELP_SCREENSHOTS`` is set, so
it never fires during the ordinary ``pytest -m e2e`` sweep. Run it with:

    scripts/capture-help-screenshots.sh

or directly:

    CAPTURE_HELP_SCREENSHOTS=1 pytest -m e2e tests/e2e/help_screenshots_spec.py
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from membership import help_content
from membership.models import Guild, Member, MembershipPlan
from tests.e2e.screenshot_seed import _seed, _seed_member_hub
from tests.e2e.screenshots_spec import FEATURE_SHOT_VIEWPORT, _write_index

if TYPE_CHECKING:
    from membership.help_content import ShotSpec

# Whole module is opt-in: it drives a browser and writes committed files, so we
# keep it out of the default e2e run. The runner script sets this for you.
pytestmark = pytest.mark.skipif(
    not os.environ.get("CAPTURE_HELP_SCREENSHOTS"),
    reason="help screenshot generator — run scripts/capture-help-screenshots.sh (sets CAPTURE_HELP_SCREENSHOTS=1)",
)

# One seeded account per ShotSpec role, in capture order. An unknown ``as_role``
# in a spec fails loudly on the dict lookup.
ROLE_EMAILS: dict[str, str] = {
    "member": "member@example.com",
    "guild_lead": "guild-lead@example.com",
    "instructor": "instructor@example.com",
    "admin": "admin@example.com",
}

# Padding (px) around an element shot so crops keep visual context.
ELEMENT_SHOT_PADDING = 16

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_HELP_DIR = REPO_ROOT / "static" / "help"


def _seed_personas() -> dict[str, Member]:
    """Create one signed-up member per ShotSpec role, keyed by role.

    Each persona carries exactly the trait its role implies — ``fog_role`` for
    the admin, a ``Guild.guild_lead`` FK for the guild lead (wired up in
    ``_seed_help_extras`` once the guilds exist), an ``instructor_slug`` for the
    instructor — so each persona's screenshots show exactly what that persona
    sees.
    """
    plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
    traits: dict[str, dict[str, object]] = {
        "member": {"full_legal_name": "Morgan Member"},
        "guild_lead": {"full_legal_name": "Lee Guildlead"},
        "instructor": {"full_legal_name": "Jules Instructor", "instructor_slug": "jules-instructor"},
        "admin": {"full_legal_name": "Alex Admin", "fog_role": Member.FogRole.ADMIN},
    }
    personas: dict[str, Member] = {}
    for role, email in ROLE_EMAILS.items():
        user, _ = get_user_model().objects.get_or_create(username=email, defaults={"email": email})
        member, _ = Member.objects.update_or_create(
            user=user,
            defaults={
                "membership_plan": plan,
                "status": Member.Status.ACTIVE,
                "fog_role": Member.FogRole.MEMBER,
                **traits[role],
            },
        )
        personas[role] = member
    return personas


def _seed_help_extras(personas: dict[str, Member]) -> None:
    """Layer the help-specific demo data the article shot lists demand.

    On top of ``_seed()`` + ``_seed_member_hub()``: the guild-lead persona leads
    the Ceramics Guild (which also gets a staff member), an upcoming bookable
    orientation slot, and a funding vote from the member persona. Plus the
    states the how-to shots must not capture empty: the instructor persona's
    published class (with registrations and a waitlist), a pending announcement
    proposal and a requested orientation booking for Ceramics, and a PENDING
    class waiting on the guild lead's undecided review gate.
    """
    from datetime import timedelta

    from django.utils import timezone

    from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, RegistrationFactory
    from classes.models import ClassApproval, ClassOffering, Registration
    from tests.membership.factories import (
        GuildAnnouncementFactory,
        GuildStaffMembershipFactory,
        OrientationBookingFactory,
        OrientationSlotFactory,
        VotePreferenceFactory,
    )

    # GATED surfaces stay out of the pictures, not just the prose (§10.5 rule 4):
    # the flag defaults on, which would leak My Tab, the balance pill, and the
    # Buyables guild tab into every screenshot.
    from core.models import SiteConfiguration

    config = SiteConfiguration.load()
    config.tab_payments_enabled = False
    config.save(update_fields=["tab_payments_enabled"])

    ceramics = Guild.objects.get(name="Ceramics Guild")
    textiles = Guild.objects.get(name="Textiles Guild")
    woodshop = Guild.objects.get(name="Woodshop Guild")

    # The running-a-guild ShotSpecs hardcode /guilds/1/… paths (and /guilds/1/
    # form-action selectors); Ceramics is the first guild _seed_member_hub
    # creates, so in this fresh capture DB it must be pk 1. Fail loudly here if
    # that assumption ever breaks rather than 404ing every guild-lead shot.
    assert ceramics.pk == 1, f"Ceramics Guild is pk {ceramics.pk}, not 1 — fix the /guilds/1/… ShotSpecs"

    # A guild with a lead and staff — the guild-lead persona's editable surface.
    ceramics.guild_lead = personas["guild_lead"]
    ceramics.save(update_fields=["guild_lead"])
    GuildStaffMembershipFactory(guild=ceramics)

    # An upcoming orientation slot (its factory enables orientation settings).
    slot = OrientationSlotFactory(guild=ceramics)

    # A funding vote, so voting pages show live standings instead of empty state.
    VotePreferenceFactory(member=personas["member"], guild_1st=ceramics, guild_2nd=textiles, guild_3rd=woodshop)

    # A second signed-up member fronts the request states below, so the member
    # persona's own booking/proposal surfaces stay in their pristine how-to state.
    # (Creating the User auto-provisions a linked Member via signal, so update
    # that row rather than creating a colliding one.)
    requester_user, _ = get_user_model().objects.get_or_create(
        username="casey@example.com", defaults={"email": "casey@example.com"}
    )
    requester, _ = Member.objects.update_or_create(
        user=requester_user,
        defaults={
            "full_legal_name": "Casey Requester",
            "membership_plan": MembershipPlan.objects.get(name="Standard"),
            "status": Member.Status.ACTIVE,
        },
    )

    # The instructor persona's own published class, with confirmed sign-ups and
    # a waitlisted student — the teaching-portal shots show a live roster.
    ceramics_category = CategoryFactory(name="Ceramics", slug="ceramics", guild=ceramics)
    taught = ClassOfferingFactory(
        title="Wheel-Throwing Fundamentals",
        slug="wheel-throwing-fundamentals",
        category=ceramics_category,
        instructor=personas["instructor"],
        status=ClassOffering.Status.PUBLISHED,
        is_private=False,
        price_cents=5500,
        capacity=6,
        description="Center, pull, and trim your first cylinders on the wheel. Clay and firing included.",
    )
    starts = timezone.now() + timedelta(days=14)
    ClassSessionFactory(class_offering=taught, starts_at=starts, ends_at=starts + timedelta(hours=2))
    RegistrationFactory(class_offering=taught, first_name="Noa", last_name="Park", status=Registration.Status.CONFIRMED)
    RegistrationFactory(class_offering=taught, first_name="Ira", last_name="Vale", status=Registration.Status.CONFIRMED)
    RegistrationFactory(
        class_offering=taught, first_name="Kit", last_name="Moss", status=Registration.Status.WAITLISTED
    )

    # A pending member proposal for Ceramics — the announcement review queue shot.
    GuildAnnouncementFactory(
        guild=ceramics,
        pending=True,
        submitted_by=requester_user,
        title="Raku firing night?",
        body="A few of us want to try a raku firing next month — could the guild host one?",
    )

    # A requested (unconfirmed) orientation booking — the dashboard's Respond row.
    OrientationBookingFactory(slot=slot, member=requester)

    # A PENDING class in a Ceramics-linked category with the guild lead's review
    # gate still undecided — the "Waiting on your review" teach-overview panel.
    pending = ClassOfferingFactory(
        title="Glaze Chemistry Basics",
        slug="glaze-chemistry-basics",
        category=ceramics_category,
        instructor=requester,
        status=ClassOffering.Status.PENDING,
        is_private=False,
        price_cents=4000,
        description="Mix, test, and read glaze recipes without the guesswork.",
    )
    ClassApproval.objects.create(class_offering=pending, role=ClassApproval.Role.GUILD_LEAD)
    ClassApproval.objects.create(class_offering=pending, role=ClassApproval.Role.ADMIN)


def _resolve_path(page_ref: str) -> str:
    """Resolve a ShotSpec ``page`` — a URL name via ``reverse``, else a literal path."""
    if page_ref.startswith("/"):
        return page_ref
    return reverse(page_ref)


def _shots_by_role() -> dict[str, list[tuple[str, ShotSpec]]]:
    """Group every declared ShotSpec as ``role -> [(article_slug, spec), …]``.

    Reads ``help_content.ARTICLES`` tolerantly — phase 6 defines it; until then
    (or for articles without a shot list) there is simply nothing to capture.
    """
    grouped: dict[str, list[tuple[str, ShotSpec]]] = {role: [] for role in ROLE_EMAILS}
    for article in getattr(help_content, "ARTICLES", []):
        for shot in article.get("screenshots", []):
            grouped[shot["as_role"]].append((article["slug"], shot))
    return grouped


def describe_help_screenshots():
    def it_captures_every_help_step(live_server, page, login_via_code):
        grouped = _shots_by_role()
        total = sum(len(shots) for shots in grouped.values())
        if not total:
            print("\n0 ShotSpecs defined in help_content.ARTICLES — nothing to capture (phase 6 adds shot lists)")
            return

        sheet_dir = Path(os.environ.get("SHOT_DIR", "screenshots")).resolve() / "help"
        sheet_dir.mkdir(parents=True, exist_ok=True)
        page.set_viewport_size(FEATURE_SHOT_VIEWPORT)

        data = _seed()
        _seed_member_hub(data["instructor"])  # type: ignore[arg-type]
        personas = _seed_personas()
        _seed_help_extras(personas)

        results: list[dict[str, object]] = []
        for role, shots in grouped.items():
            if not shots:
                continue
            # Fresh session per persona — drop the previous login's cookies.
            page.context.clear_cookies()
            login_via_code(ROLE_EMAILS[role])
            for article_slug, shot in shots:
                results.append(_capture(live_server, page, sheet_dir, role, article_slug, shot))

        _write_index(sheet_dir, results)

        captured = sum(1 for r in results if r["ok"])
        print(f"\nWrote {captured}/{len(results)} help screenshots to {STATIC_HELP_DIR}")
        print(f"Open {sheet_dir / 'index.html'} to review, then commit the changed PNGs.")
        # Surface failures loudly but don't fail the run for one stubborn shot.
        assert captured, "no help screenshots captured — check the dev harness and seed data"


def _capture(
    live_server: Any,
    page: Any,
    sheet_dir: Path,
    role: str,
    article_slug: str,
    shot: ShotSpec,
) -> dict[str, object]:
    """Capture one ShotSpec into ``static/help/<article_slug>/<file>``.

    Returns a ``_write_index``-shaped record; one bad shot must not abort the run,
    so failures land in the record's ``note`` instead of raising.
    """
    path = _resolve_path(shot["page"])
    out_path = STATIC_HELP_DIR / article_slug / shot["file"]
    record: dict[str, object] = {
        "surface": f"as {role}",
        "label": f"{article_slug} — {shot['caption']}",
        "path": path,
        "file": os.path.relpath(out_path, sheet_dir),
    }
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        page.goto(f"{live_server.url}{path}", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(250)  # let any entrance transitions settle
        selector = shot["selector"]
        if selector is not None:
            box = page.locator(selector).bounding_box()
            assert box is not None, f"selector {selector!r} matched nothing visible on {path}"
            clip = {
                "x": max(box["x"] - ELEMENT_SHOT_PADDING, 0),
                "y": max(box["y"] - ELEMENT_SHOT_PADDING, 0),
                "width": box["width"] + 2 * ELEMENT_SHOT_PADDING,
                "height": box["height"] + 2 * ELEMENT_SHOT_PADDING,
            }
            # full_page rendering so the clip may reach below the viewport fold.
            page.screenshot(path=str(out_path), full_page=True, clip=clip)
        else:
            page.screenshot(path=str(out_path), full_page=shot.get("full_page", False))
        record["ok"] = True
    except Exception as exc:  # noqa: BLE001 — one bad shot must not abort the run
        record["ok"] = False
        record["note"] = f"{type(exc).__name__}: {exc}".splitlines()[0][:200]
    return record
