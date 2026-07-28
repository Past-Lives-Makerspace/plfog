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
import re
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
from membership.models import Guild, Member, MembershipPlan

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


# Feature-shot capture is framed (a fixed viewport, NOT full_page), so the shot fits a
# ~560px-wide email card at retina density (1200 captured → 560 displayed ≈ 2×).
FEATURE_SHOT_VIEWPORT = {"width": 1200, "height": 800}


def _seed_member_hub(member: Member) -> None:
    """Populate the member-hub feature pages so each screenshot shows a good state.

    Guilds (for the directory + My Guilds toggles), two official memberships for the
    signed-in member, a published announcement (for the home dashboard), a filled-in
    Space & Org Info page, a few notifications (for the Notifications page), a published
    upcoming community event (for the Community Calendar), and a few more members for the
    directory. This seeding is the bulk of the capture effort and grows with each
    FeaturePage in the registry.
    """
    from core.models import Notification
    from tests.membership.factories import (
        CommunityEventFactory,
        GuildAnnouncementFactory,
        GuildFactory,
        GuildFAQItemFactory,
        GuildMembershipFactory,
        MemberFactory,
        OrgInfoPageFactory,
    )

    guilds = [
        GuildFactory(name="Ceramics Guild", about="Wheel-throwing, glazing, and kiln firings — all skill levels."),
        GuildFactory(name="Textiles Guild", about="Weaving, dyeing, and sewing in the fiber studio."),
        GuildFactory(name="Woodshop Guild", about="Hand tools, the lathe, and safe machine time."),
    ]
    for guild in guilds[:2]:
        GuildMembershipFactory(guild=guild, member=member)
    GuildAnnouncementFactory(
        guild=guilds[0],
        title="Spring glaze restock is in",
        body="New celadons and a fresh batch of clay just landed. Come make something.",
    )
    # A couple of FAQ items so the guild detail page (the "guild pages" feature shot) shows a real FAQ.
    GuildFAQItemFactory(
        guild=guilds[0],
        question="Do I need to bring my own tools?",
        answer="Nope — the studio stocks wheels, tools, and glazes. Just bring an apron.",
    )
    GuildFAQItemFactory(
        guild=guilds[0],
        question="How do I get oriented?",
        answer="Book a guild orientation from the guild page and a lead will show you the ropes.",
    )
    OrgInfoPageFactory(
        intro="Everything you need to know about how our space and our guilds work.",
        parking="Free lot on the north side; street parking is open after 6pm.",
        who_to_contact="Front desk for access, your guild lead for studio-specific questions.",
        code_of_conduct="Be kind, clean your station, and ask before borrowing tools.",
    )
    # Notifications page (0.21.3): a couple bell rows — one unread (highlighted), one read.
    if member.user is not None:
        Notification.objects.create(
            user=member.user,
            trigger="class_published",
            title="New class: Intro to Lost-Wax Casting",
            body="A new class just went live in the Metalworking guild — grab a seat.",
            url="/classes/",
        )
        Notification.objects.create(
            user=member.user,
            trigger="guild_announcement",
            title="Ceramics Guild: Spring glaze restock is in",
            body="New celadons and a fresh batch of clay just landed.",
            url="/guilds/",
            read_at=timezone.now(),
        )
    # Community Calendar (0.21.1): a published, upcoming site-wide event for the Events tab.
    CommunityEventFactory(
        community=True,
        title="Open Studio Night",
        starts_at=timezone.now() + timedelta(days=5),
    )
    MemberFactory.create_batch(4)


def _feature_pages() -> list[tuple[str, str, str]]:
    """(slug, label, path) for each release-email feature page — driven by the registry."""
    from core.release_email import FEATURE_PAGES

    return [(fp.slug, fp.label, fp.path) for fp in FEATURE_PAGES]


def _seed_signage() -> "object":
    """Create an enabled zone + a couple of slides so the Slideshow tab and the kiosk
    player both capture with real content. Returns the zone (reused by the player shot)."""
    from tests.membership.factories import SlideshowSlideFactory, SlideshowZoneFactory

    zone = SlideshowZoneFactory(name="Woodshop wall", slug="woodshop", is_enabled=True)
    SlideshowSlideFactory(zone=zone, title="Welcome to Past Lives", body="Ask the front desk for a tour.", sort_order=0)
    SlideshowSlideFactory(zone=zone, title="Open studio tonight", body="Doors from 6pm.", sort_order=1)
    return zone


def _member_hub_pages() -> list[tuple[str, str]]:
    """(label, path) for the 0.21 member-hub feature pages on the members surface."""
    return [
        ("Member hub — notifications", reverse("notification_list")),
        ("Member hub — slideshow settings", reverse("hub_admin_site_settings") + "?tab=slideshow"),
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


# ── TEMPORARY — remove on/after 2026-08-10 ─────────────────────────────────────
# Anonymous copy-review comments. Each gallery <section> gets a stable slug key and
# a vanilla-JS widget that talks to the plfog comment API. A ~2-week review aid, NOT
# a member-facing feature. See docs/superpowers/plans/2026-07-27-copy-review-comments.md.
def _section_key(surface: str, label: str) -> str:
    """Stable slug used as the comment section_key AND the <section> id/anchor.

    ``slug(surface + "--" + label)``: lowercase, every run of non-alphanumeric
    characters collapses to a single ``-``, leading/trailing ``-`` stripped. The
    server just stores this string; it stays stable across rebuilds while a page
    keeps its label. TEMPORARY — remove on/after 2026-08-10.
    """
    return re.sub(r"[^a-z0-9]+", "-", f"{surface}--{label}".lower()).strip("-")


# Self-contained CSS + vanilla-JS comment widget injected before </body>. No external
# libraries (the gallery is standalone). It fetches the comment API once, renders a
# per-section thread + add form, and degrades to "Comments unavailable" if the API is
# unreachable (e.g. the bundled offline file) — it must never break the gallery.
_COPY_REVIEW_WIDGET = """<!-- TEMPORARY — remove on/after 2026-08-10: copy-review comment widget -->
<style>
  .crc { margin-top: 14px; border-top: 1px solid #e2e2e2; padding-top: 10px; font-size: 14px; }
  .crc-head { font-weight: 600; color: #444; margin-bottom: 8px; }
  .crc-muted { color: #999; font-weight: 400; font-style: italic; }
  .crc-list { display: flex; flex-direction: column; gap: 8px; }
  .crc-item { border: 1px solid #ddd; border-radius: 6px; padding: 8px 10px; background: #fafafa; }
  .crc-meta { display: flex; justify-content: space-between; gap: 8px; margin-bottom: 4px; }
  .crc-author { font-weight: 600; color: #222; }
  .crc-time { color: #999; font-size: 12px; white-space: nowrap; }
  .crc-body { margin: 0; white-space: pre-wrap; color: #222; }
  .crc-actions, .crc-confirm { display: flex; gap: 10px; margin-top: 6px; align-items: center; }
  .crc-link { background: none; border: none; padding: 0; color: #06c; cursor: pointer; font-size: 13px; }
  .crc-link:hover { text-decoration: underline; }
  .crc-danger { color: #b00; }
  .crc-editor, .crc-form { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
  .crc-input, .crc-textarea { font: inherit; padding: 6px 8px; border: 1px solid #ccc; border-radius: 6px; width: 100%; box-sizing: border-box; }
  .crc-textarea { min-height: 60px; resize: vertical; }
  .crc-btn { align-self: flex-start; font: inherit; padding: 6px 14px; border: 1px solid #06c; background: #06c; color: #fff; border-radius: 6px; cursor: pointer; }
  .crc-btn:disabled { opacity: 0.6; cursor: default; }
</style>
<script>
(function () {
  "use strict";
  var params = new URLSearchParams(window.location.search);
  var API_BASE = params.get("api") || "https://book.pastlives.space";
  var LIST_URL = API_BASE + "/copy-review/comments/";

  function readTokens() {
    try { return JSON.parse(window.localStorage.getItem("crc_tokens") || "{}"); }
    catch (e) { return {}; }
  }
  function writeTokens(map) {
    try { window.localStorage.setItem("crc_tokens", JSON.stringify(map)); } catch (e) {}
  }
  function tokenFor(id) { return readTokens()[id]; }
  function rememberToken(id, token) { var m = readTokens(); m[id] = token; writeTokens(m); }
  function forgetToken(id) { var m = readTokens(); delete m[id]; writeTokens(m); }
  function readName() { try { return window.localStorage.getItem("crc_name") || ""; } catch (e) { return ""; } }
  function writeName(name) { try { window.localStorage.setItem("crc_name", name); } catch (e) {} }

  function fmtTime(iso) { try { return new Date(iso).toLocaleString(); } catch (e) { return iso || ""; } }
  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }
  function request(method, url, payload) {
    var opts = { method: method, headers: { "Content-Type": "application/json" } };
    if (payload) { opts.body = JSON.stringify(payload); }
    return fetch(url, opts).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    });
  }
  function updateCount(head, count) { head.textContent = "Comments (" + count + ")"; }

  function commentNode(sectionKey, c, head, list) {
    var item = make("div", "crc-item");
    item.setAttribute("data-id", c.id);
    var meta = make("div", "crc-meta");
    meta.appendChild(make("span", "crc-author", c.author_name));
    meta.appendChild(make("span", "crc-time", fmtTime(c.updated_at || c.created_at)));
    item.appendChild(meta);
    var body = make("p", "crc-body", c.body);
    item.appendChild(body);

    if (tokenFor(c.id)) {
      var actions = make("div", "crc-actions");
      var editBtn = make("button", "crc-link", "Edit");
      var delBtn = make("button", "crc-link", "Delete");
      actions.appendChild(editBtn);
      actions.appendChild(delBtn);
      item.appendChild(actions);
      editBtn.addEventListener("click", function () { startEdit(c, item, body, meta, actions); });
      delBtn.addEventListener("click", function () { startDelete(c, item, actions, head, list); });
    }
    return item;
  }

  function startEdit(c, item, body, meta, actions) {
    actions.style.display = "none";
    body.style.display = "none";
    var editor = make("div", "crc-editor");
    var nameInput = make("input", "crc-input");
    nameInput.value = c.author_name;
    var textArea = make("textarea", "crc-textarea");
    textArea.value = c.body;
    var save = make("button", "crc-btn", "Save");
    var cancel = make("button", "crc-link", "Cancel");
    editor.appendChild(nameInput);
    editor.appendChild(textArea);
    editor.appendChild(save);
    editor.appendChild(cancel);
    item.insertBefore(editor, actions);
    function done() { editor.remove(); body.style.display = ""; actions.style.display = ""; }
    cancel.addEventListener("click", done);
    save.addEventListener("click", function () {
      request("POST", API_BASE + "/copy-review/comments/" + c.id + "/edit/", {
        edit_token: tokenFor(c.id), author_name: nameInput.value, body: textArea.value
      }).then(function (r) {
        if (r.ok && r.data && r.data.comment) {
          c.author_name = r.data.comment.author_name;
          c.body = r.data.comment.body;
          body.textContent = c.body;
          meta.querySelector(".crc-author").textContent = c.author_name;
          meta.querySelector(".crc-time").textContent = fmtTime(r.data.comment.updated_at);
          done();
        } else { save.textContent = "Save failed"; }
      }).catch(function () { save.textContent = "Save failed"; });
    });
  }

  function startDelete(c, item, actions, head, list) {
    actions.style.display = "none";
    var confirm = make("div", "crc-confirm");
    confirm.appendChild(make("span", null, "Delete?"));
    var yes = make("button", "crc-link crc-danger", "yes");
    var no = make("button", "crc-link", "no");
    confirm.appendChild(yes);
    confirm.appendChild(no);
    item.appendChild(confirm);
    function done() { confirm.remove(); actions.style.display = ""; }
    no.addEventListener("click", done);
    yes.addEventListener("click", function () {
      request("POST", API_BASE + "/copy-review/comments/" + c.id + "/delete/", {
        edit_token: tokenFor(c.id)
      }).then(function (r) {
        if (r.ok) {
          forgetToken(c.id);
          item.remove();
          updateCount(head, list.querySelectorAll(".crc-item").length);
        } else { done(); }
      }).catch(done);
    });
  }

  function formNode(sectionKey, head, list) {
    var form = make("form", "crc-form");
    var nameInput = make("input", "crc-input");
    nameInput.placeholder = "Your name";
    nameInput.value = readName();
    var textArea = make("textarea", "crc-textarea");
    textArea.placeholder = "Leave a comment on this area";
    var submit = make("button", "crc-btn", "Post");
    submit.type = "submit";
    form.appendChild(nameInput);
    form.appendChild(textArea);
    form.appendChild(submit);
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var name = nameInput.value.trim();
      var text = textArea.value.trim();
      if (!name || !text) { return; }
      submit.disabled = true;
      request("POST", LIST_URL, {
        section: sectionKey, author_name: name, body: text, website: ""
      }).then(function (r) {
        submit.disabled = false;
        if (r.status === 201 && r.data && r.data.comment) {
          rememberToken(r.data.comment.id, r.data.edit_token);
          writeName(name);
          list.appendChild(commentNode(sectionKey, r.data.comment, head, list));
          updateCount(head, list.querySelectorAll(".crc-item").length);
          textArea.value = "";
        } else {
          submit.textContent = "Try again";
          window.setTimeout(function () { submit.textContent = "Post"; }, 2000);
        }
      }).catch(function () {
        submit.disabled = false;
        submit.textContent = "Try again";
        window.setTimeout(function () { submit.textContent = "Post"; }, 2000);
      });
    });
    return form;
  }

  function mountSection(sectionEl, sectionKey, comments) {
    var box = make("div", "crc");
    var head = make("div", "crc-head");
    updateCount(head, comments.length);
    box.appendChild(head);
    var list = make("div", "crc-list");
    comments.forEach(function (c) { list.appendChild(commentNode(sectionKey, c, head, list)); });
    box.appendChild(list);
    box.appendChild(formNode(sectionKey, head, list));
    sectionEl.appendChild(box);
  }

  function mountUnavailable(sectionEl) {
    var box = make("div", "crc");
    box.appendChild(make("div", "crc-head crc-muted", "Comments unavailable"));
    sectionEl.appendChild(box);
  }

  function init() {
    var sections = Array.prototype.slice.call(document.querySelectorAll("section[data-section-key]"));
    request("GET", LIST_URL, null).then(function (r) {
      if (!r.ok || !r.data || !r.data.sections) { sections.forEach(mountUnavailable); return; }
      var grouped = r.data.sections;
      sections.forEach(function (sectionEl) {
        var key = sectionEl.getAttribute("data-section-key");
        mountSection(sectionEl, key, grouped[key] || []);
      });
    }).catch(function () { sections.forEach(mountUnavailable); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else { init(); }
})();
</script>
<!-- END TEMPORARY (copy-review comment widget) -->"""
# ── END TEMPORARY (copy-review comments) ───────────────────────────────────────


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
        # TEMPORARY — remove on/after 2026-08-10: data-section-key/id anchor the comment widget.
        key = _section_key(section, str(r["label"]))
        rows.append(
            f'<section data-section-key="{key}" id="{key}"><h3>{label}</h3><p class="path">{path}</p>{body}</section>'
        )

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
    # TEMPORARY — remove on/after 2026-08-10: inject the copy-review comment widget.
    doc = doc.replace("</body></html>", f"{_COPY_REVIEW_WIDGET}\n</body></html>")
    (out_dir / "index.html").write_text(doc, encoding="utf-8")


def describe_cms_screenshots():
    def it_captures_every_cms_page(live_server, page, login_via_code, settings):
        out_dir = Path(os.environ.get("SHOT_DIR", "screenshots")).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        page.set_viewport_size({"width": 1366, "height": 900})

        data = _seed()
        zone = _seed_signage()
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

        # Pass 2b — member-hub 0.21 features (also members surface; ADMIN_EMAIL is a fog admin).
        for label, path in _member_hub_pages():
            capture("Member hub (members surface)", label, path)

        # Pass 3 — signage kiosk surface: point the live host at SIGNAGE_HOSTS so the
        # public player resolves, then shoot the deck.
        settings.SIGNAGE_HOSTS = [host]
        capture("Signage (kiosk surface)", "Signage — slideshow player", f"/{zone.slug}/")

        _write_index(out_dir, results)

        captured = sum(1 for r in results if r["ok"])
        print(f"\nWrote {captured}/{len(results)} screenshots to {out_dir}\nOpen {out_dir / 'index.html'}")
        # Surface failures loudly but don't fail the run for one stubborn page.
        assert captured, "no pages captured — check the dev harness and seed data"


def describe_feature_screenshots():
    def it_captures_member_hub_feature_pages(live_server, page, login_via_code, settings):
        """Shoot each release-email FeaturePage framed (not full_page) and upload to R2.

        Runs on the members surface (the autouse _e2e_settings keeps the live host off
        PUBLIC_HOSTS). Each shot lands at ``email/features/<slug>.png`` via
        ``save_feature_shot`` — R2 in CI (needs the R2 secrets), the local media dir in
        dev. Seeded fake data only → no member PII ever appears in a shot.
        """
        from core.release_email import save_feature_shot

        data = _seed()
        _seed_member_hub(data["instructor"])  # type: ignore[arg-type]
        login_via_code(ADMIN_EMAIL)
        page.set_viewport_size(FEATURE_SHOT_VIEWPORT)

        # The static registry pages (framed viewport shots) plus two dynamic, kwargs-based pages
        # that can't live in the static registry: the polished guild detail page (the "guild pages"
        # card) and its print QR flyer (the "QR codes" card — shot full-page so the whole flyer fits).
        guild = Guild.objects.get(name="Ceramics Guild")
        targets: list[tuple[str, str, str, bool]] = [(s, lbl, p, False) for s, lbl, p in _feature_pages()]
        targets.append(("guild-pages", "Guild page", reverse("hub_guild_detail", kwargs={"slug": guild.slug}), False))
        targets.append(("qr-codes", "Guild QR flyer", reverse("hub_guild_flyer", kwargs={"pk": guild.pk}), True))

        results: list[dict[str, object]] = []
        for slug, label, path, full_page in targets:
            record: dict[str, object] = {"slug": slug, "label": label, "path": path}
            try:
                page.goto(f"{live_server.url}{path}", wait_until="networkidle", timeout=15000)
                page.wait_for_timeout(250)  # let entrance transitions settle
                if slug == "qr-codes":
                    page.add_style_tag(content=".no-print{display:none !important}")  # drop the flyer's screen toolbar
                png = page.screenshot(full_page=full_page)
                record["url"] = save_feature_shot(slug, png)
                record["ok"] = True
            except Exception as exc:  # noqa: BLE001 — one bad page must not abort the run
                record["ok"] = False
                record["note"] = f"{type(exc).__name__}: {exc}".splitlines()[0][:200]
            results.append(record)

        captured = sum(1 for r in results if r["ok"])
        print(f"\nUploaded {captured}/{len(results)} feature screenshots to email/features/")
        for r in results:
            print(f"  {r['slug']}: {'ok' if r['ok'] else r.get('note')}")
        assert captured, "no feature pages captured — check the dev harness and member-hub seed data"
