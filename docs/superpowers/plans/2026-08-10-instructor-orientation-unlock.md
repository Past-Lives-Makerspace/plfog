# Instructor Orientation & Teaching Unlock — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-10
**Surface:** FOG hub `pastlives.test` — new `/classes/teach/orientation/` page, every `/classes/teach/*` view, admin member edit (`/manage/members/<pk>/edit/`)
**Related:** `2026-08-10-help-center-knowledge-base.md` (Spec A — seeds the orientation content + article 12; **build prerequisite**), `2026-08-10-info-view-hover-help.md` (Spec B — help keys), `2026-08-10-guided-tours.md` (Spec C — its instructor tour complements this feature post-unlock; **no build dependency, can land in parallel**)

---

## 1. Summary

Today any active member can walk into the teaching portal and create a class — there is no onboarding at all. This feature adds an official **instructor orientation**: a short "Become an instructor" page that lays out expectations, how class review works, and the quality bar, and ends in an explicit **"Unlock teaching"** button. Completing it auto-grants the permission to enter the teach portal and create classes — no manual admin sign-off; Spec C's instructor tour then greets them on their first visit to the unlocked portal. Members who already teach are grandfathered in, and admins can grant or revoke the unlock manually from the member edit page.

The per-class review chain is **unchanged**: every submitted class still goes through the guild-lead gate (when the category's guild has a lead) and then admin approval before publishing. The orientation gates *portal access and class creation*, nothing downstream.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| What the orientation gates | Entering `/classes/teach/*` and creating classes. The per-class review chain (`submit_for_review` → guild-lead gate → admin, `classes/models.py:697`) is untouched. |
| Orientation form | A dedicated page: seeded must-read content + an explicit completion button. Not a bare tour, not a quiz; no tour launcher on the page (see "Tour relationship" below). |
| Where the unlock lives | New `Member.instructor_oriented_at` nullable datetime (mirrors `welcome_dismissed_at`). Not derived from — and not mirrored into — Spec C's TourState; see §4 for why. |
| Single source of truth | `Member.can_create_classes` property; `teaching_member_required` reads only this. |
| Locked-out UX | Friendly redirect to the orientation page — never a 403 for an active member. Entry links stay visible to all active members (no hidden dead ends). |
| Grandfathering | Data migration unlocks members with any `ClassOffering` (any status) **or** an `instructor_slug`. Reverse included. |
| Admin override | Grant/Revoke action button on the hub member edit page, confirm modal on revoke. Revoke does nothing to existing classes. |
| Admin notification on completion | **No** new spine event — `SiteActivity` row only (rationale in §7). |
| Tour relationship | The orientation page carries no tour launcher. Spec C's instructor tour auto-offers on the first *post-unlock* visit to the teach overview (its start route and DOM targets live there). The required act here is reading + the acknowledge checkbox + the button. |

## 2. What already exists (reuse, don't reinvent)

All verified in code 2026-08-10.

| Need | Existing thing | Location |
|---|---|---|
| The gate to change | `teaching_member_required` — currently admits any active member, sets `request.teaching_member` | `classes/views.py:842` |
| Views it guards (all change behavior at once) | 21 `teach_*` views: overview, dashboard, create/edit/submit, duplicate-run, detail, registrations (+export/email), waitlist, class discount codes, emails, registrations list (+email), discount codes CRUD/approve, profile | `classes/views.py:930–1528`, routes `classes/urls.py:17–76` |
| One-time-completion field shape | `Member.welcome_dismissed_at` + `dismiss_welcome()` | `membership/models.py:483,585` |
| Post-unlock portal walkthrough | Spec C's instructor tour auto-offers on the member's first visit to `classes:teach_overview` — referenced only, no code dependency (see §4: this spec writes no `TourState` rows) | `2026-08-10-guided-tours.md` |
| "Completing an orientation unlocks a thing" house analog | `Member.is_oriented_for(guild)` → completed `OrientationBooking` gates guild stuff | `membership/models.py:907` |
| Seeded must-read content | Spec A's seed pipeline (extend `seed_wiki_articles` pattern: `update_or_create` on slug, `--dry-run`, report) | `membership/management/commands/seed_wiki_articles.py` |
| Narrative home | KB article 12 "Become an instructor & create your first class" (Spec A, P1) | Spec A article list |
| Help keys | `teach.become-instructor` (new), `teach.create-class` (brief) — registry per Spec A | Spec A registry module |
| Admin member edit surface | `admin_member_edit` + `MemberAdminEditForm` (role dropdown incl. "Instructor", `can_self_approve_discounts` boolean) → `templates/hub/admin/member_edit.html` (fields loop through `form_field.html`, "Save member" button) | `hub/views.py:4083`, `hub/forms.py:521` |
| Instructor-role promotion (stays separate) | `Member.apply_admin_role("instructor")` sets `instructor_slug` (public page); `is_instructor` property | `membership/models.py:903,930` |
| Activity log | `SiteActivity.log(kind, actor=, target=, payload=)` + `Kind` TextChoices | `core/models.py:916,1001` |
| Trigger spine (checked, deliberately not used) | `core/triggers.py` catalogue + `core/events/registry.py` EventType/`activity_kind` | see §7 |
| Guild-lead review path (proves revoke/lock-out is safe) | In-portal queue links the **tokenized** `classes:class_review` page; review-request emails carry the same token | `classes/views.py:1005`, `templates/classes/teach/overview.html:15` |
| Confirm modal, toasts, form fields, toggles | `components/confirm_modal.html`, `trigger_toast()`, `components/form_field.html` | `templates/components/`, FRONTEND.md |
| Markdown prose rendering | `guild_markdown` filter into `.pl-md` (Spec A's help rendering path if it lands first) | `membership/templatetags/membership_md.py`, `hub.css` `.pl-md` |

**Genuine gaps (all small):** the `instructor_oriented_at` field + `can_create_classes` + grant/revoke/complete methods; the redirect branch in the decorator; one new page + completion POST; one seeded article; the admin action button; the backfill migration.

**Entry points to the teach portal today (verified — none are a sidebar link):**
- Catalog hero "Manage My Classes" → `classes:teach_overview` (`templates/classes/public/list.html:19`, shown when `view_as.is_member`).
- Guild page "Teach a Class" → `classes:teach_class_create` (`templates/hub/guild_detail.html:312`).
- Book-account instructor banner "Teaching dashboard" → `/classes/teach/` (`templates/classes/account/_components/instructor_banner.html:13`).
- Instructor-facing emails deep-linking teach views — class detail/edit links and the approval-request `/classes/teach/` link (`classes/emails.py:208,324,344,482,489,500`).
- Legacy `/classes/instructor/...` 301s (`classes/urls.py:75`).
- (After Spec A) KB article 12.

All stay exactly as they are — the decorator sits on every one of these destinations, so a locked member clicking any of them (banner, email link, catalog CTA) lands on the orientation page, not a dead end.

## 3. Where the code lives

```
membership/
  models.py                      # instructor_oriented_at, can_create_classes, complete/grant/revoke methods
  migrations/
    0106_member_instructor_oriented_at.py    # schema
    0107_backfill_instructor_oriented.py     # data migration + reverse
classes/
  views.py                       # teaching_member_required change, active_member_required,
                                 # teach_orientation, teach_orientation_complete
  urls.py                        # two new routes
  forms.py                       # InstructorOrientationCompleteForm
core/
  models.py                      # SiteActivity.Kind additions
hub/
  views.py                       # admin_member_teaching_set
  urls.py                        # hub_admin_member_teaching route
templates/
  classes/teach/orientation.html # the page (extends hub/base.html)
  hub/admin/member_edit.html     # Teaching access card
<Spec A's seed module>           # orientation article content (slug: instructor-orientation)
classes/spec/views/              # teach_gating_spec.py, teach_orientation_spec.py
tests/membership/                # instructor_unlock_spec.py (model + migration)
tests/hub/                       # admin_member_teaching_spec.py
```

## 4. Data model

One new field on `Member` — no new model.

| Field | Type | Notes |
|---|---|---|
| `instructor_oriented_at` | `DateTimeField(null=True, blank=True)` | `help_text="When the member completed the instructor orientation (or an admin granted teaching access); null = teaching portal locked. Cleared when an admin revokes access."` |

**Why a field, not a derivation from Spec C's `TourState`:**

- **Permissions must not couple to tour storage.** Deleting/resetting a `TourState` row (a legitimate tour-infra operation — "re-offer the tour") must never silently revoke someone's teaching access.
- **Admin grant/revoke** shouldn't fabricate or delete tour records to change a permission.
- **Backfill** for grandfathered members would otherwise mean inventing fake "completed a tour they never ran" rows.
- Cost is one nullable column mirroring the proven `welcome_dismissed_at` shape — admin-editable, backfillable, trivially indexed if ever needed (it isn't: it's only read per-request for one member).

**No `TourState` row is written at all.** The orientation is not a Spec C tour: C's `mark_completed(user, tour_key)` validates `tour_key` against its `TOURS` registry (`member-welcome` / `guild-lead` / `instructor`) and raises `ValueError` on unknown keys, so an `instructor-orientation` write would crash — and registering a fake tour just to store a permission would recreate the coupling the bullets above reject. `instructor_oriented_at` is the entire record. Dropping the write also removes any build dependency on Spec C (see §8); C's instructor tour keeps its own key and auto-offers on the first post-unlock portal visit.

**Single source of truth:**

```python
@property
def can_create_classes(self) -> bool:
    """True when this member may enter the teaching portal and create classes."""
    return self.instructor_oriented_at is not None
```

**Migrations:**
1. `0106` — add the field (auto-reversible).
2. `0107` — data migration (depends on `0106` and the current head of `classes` migrations, since it queries `ClassOffering`). The two querysets, stated once and finally:
   - **Base predicate:** `_GRANDFATHERED = Q(classes__isnull=False) | ~Q(instructor_slug="")` — `classes` is `ClassOffering.instructor`'s `related_name` (`classes/models.py:402`); "any status" needs no status filter because the join matches every offering row (draft, pending, published, cancelled, archived). `instructor_slug` holders are included because `apply_admin_role("instructor")` is an explicit admin grant of teaching (see §5).
   - **Forward:** `Member.objects.filter(_GRANDFATHERED, instructor_oriented_at__isnull=True).distinct().update(instructor_oriented_at=timezone.now())` — via `apps.get_model`. The `isnull=True` guard means the forward never stomps a timestamp that already exists (re-runs, or an admin grant that landed between migrations).
   - **Reverse:** `Member.objects.filter(_GRANDFATHERED).distinct().update(instructor_oriented_at=None)` — the base predicate **without** the isnull guard (which would match zero rows after the forward). Honest caveat, stated in the migration docstring: the reverse also clears anyone in that set who was unlocked by other means before/after the forward run — acceptable for a rollback (they redo a two-minute page), and per house rules a real reverse beats `RunPython.noop`.

## 5. Business logic (fat models)

All on `Member` (`membership/models.py`), typed, with docstrings:

- `complete_instructor_orientation(self) -> None` — the member-facing completion. Guards: raises `ValueError` if the member is not `Status.ACTIVE`. Idempotent: if already unlocked, returns without side effects (no duplicate activity rows on a double-submit). Otherwise: sets `instructor_oriented_at = timezone.now()`, `save(update_fields=[...])`, and logs `SiteActivity.Kind.INSTRUCTOR_ORIENTED` with `actor=self.user`, `target=self`. No `TourState` write (§4).
- `grant_teaching(self, *, granted_by: Member) -> None` — admin override. Sets the timestamp if null (idempotent), logs `SiteActivity.Kind.TEACHING_GRANTED` with `actor=granted_by.user`, `target=self`.
- `revoke_teaching(self, *, revoked_by: Member) -> None` — clears the timestamp (idempotent if already null → no-op, no log), logs `SiteActivity.Kind.TEACHING_REVOKED`. **Existing classes are untouched**: drafts, pending, and published offerings all keep their status, registrations, and emails; the member simply can't enter the teach portal or create new classes until re-unlocked. (Admins manage the orphaned classes from the classes CMS as usual.) After a revoke there are exactly **two** roads back: the admin Grant button, or the member completing the orientation again — the intended remedy ("go re-read the rules"), stated here deliberately.
- `apply_admin_role` (`membership/models.py:930`) — addition scoped to **first-time promotion only**: the unlock line goes *inside* the `if not self.instructor_slug:` branch (`membership/models.py:947`), setting `instructor_oriented_at` if null and logging `SiteActivity.Kind.TEACHING_GRANTED` with `actor=None` (the method has no actor parameter — a system-attributed row) and `payload={"via": "instructor_promotion"}`. An explicit admin "make them an Instructor" must not strand them at the orientation redirect. **Why the scoping is load-bearing:** `admin_member_edit` calls `apply_admin_role(cleaned_role)` on *every* save (`hub/views.py:4092`), and `MemberAdminEditForm._derive_initial_role` pre-fills "Instructor" for anyone holding an `instructor_slug` (`hub/forms.py:569–575`); since revoke doesn't clear the slug, an unscoped hook would silently re-grant a revoked instructor's teaching access on any unrelated edit — no confirm, no honest log. Scoped to the slug-minting branch, the hook fires exactly once per member, and a revoked instructor is re-unlocked only via the two roads above. (Demoting does **not** clear the unlock — the unlock and the public-page role stay independent, matching the backfill.)

**Decorator changes (`classes/views.py`):**

- `active_member_required` — new decorator, exactly today's `teaching_member_required` body (login → active `Member` or 403 → set `request.teaching_member`). Guards only the two orientation views, so a locked member can reach them.
- `teaching_member_required` — keeps its name and its 21 call sites. New body: run the active-member check as above; then if `not member.can_create_classes`, return `redirect("classes:teach_orientation")`. The 403 branch for non-members/inactive is unchanged. Docstring updated ("Teaching portal access — active members who completed the instructor orientation").
- Registrant-facing and public views (`public_list`, `register`, `my_registration`, tokenized `class_review`, instructor public pages) use other gates and are untouched.

**Known consequence, accepted:** a guild lead who has never taught and isn't grandfathered loses the *in-portal* class-review convenience panel until they complete the two-minute orientation. Their actual review powers are unaffected — the queue's Review buttons and the review-request emails both go through the tokenized `classes:class_review` page, which has no portal gate (verified `templates/classes/teach/overview.html:15`). Uniform gating beats a role carve-out that would let leads create classes without orientation.

**Views (skinny):**

- `teach_orientation` (GET, `active_member_required`) — loads the seeded article and `member.can_create_classes`, renders (the locked/unlocked flag drives the banner vs. completed state, §6).
- `teach_orientation_complete` (POST, `active_member_required`) — binds `InstructorOrientationCompleteForm`; invalid → re-render page with the field error; valid → `member.complete_instructor_orientation()`, Django success message ("Teaching unlocked — welcome, instructor."), `redirect("classes:teach_overview")`.
- `admin_member_teaching_set` (POST, `fog_admin_required`, `hub/views.py`) — reads `action` (`"grant"`/`"revoke"`, anything else → 400), calls the model method, `messages.success`, `redirect("hub_admin_member_edit", pk=member.pk)`. Full-page POST + message, matching the sibling member-edit actions (not HTMX).

**Form (`classes/forms.py`):** `InstructorOrientationCompleteForm` — one field, `acknowledge = forms.BooleanField(required=True, error_messages={"required": "Please confirm you've read the orientation before unlocking teaching."})`. Validation lives here, not the view.

**URLs (`classes/urls.py`):**
```
path("teach/orientation/", views.teach_orientation, name="teach_orientation")
path("teach/orientation/complete/", views.teach_orientation_complete, name="teach_orientation_complete")
```
(No collision with `teach/classes/<pk>/` routes.)

**Help keys:** `teach.become-instructor` → article 12 + this page; the page's DOM root carries `data-help-key="teach.become-instructor"` per the Spec B convention.

## 6. UI / UX

### Screen 1 — the orientation page (`templates/classes/teach/orientation.html`)

- **Route:** `/classes/teach/orientation/`. **Extends `hub/base.html` directly** — not `classes/teach/base.html`, whose tab bar links four gated pages; showing locked tabs on the unlock page would be a wall of dead ends.
- **Layout:** single column of `hub-card` sections, max-width matching other hub content pages. Top-to-bottom:
  1. **Explainer banner** (only when locked, i.e. `not member.can_create_classes`): a `pl-` alert card — heading "One quick step before you can teach", body "The teaching portal unlocks after this short orientation. Read it once, tick the box, and you're in." No error styling — this is friendly, and it doubles as the redirect landing state (see Screen 2). No `came_from_gate` query param needed: the banner shows for every locked visitor, which is exactly who gets redirected here.
  2. **Content card**: `<h1>` "Become an instructor at Past Lives", then the seeded article body rendered through the Spec A help rendering path into `.pl-md` prose. Three short `##` sections (seeded, slug `instructor-orientation`, **unlisted** — `category=None` plus membership in Spec A's `UNLISTED_SLUGS` (§10.6), which hides it from the landing (including the "All guides" fallback), category pages, and search while keeping it URL-resolvable; audience metadata is moot for a flow page since audience lives on `HelpCategory`, and article 12 is the browsable one): *What we expect from instructors*, *How class review works* (draft → submit → guild-lead gate → admin approval → published; you never self-publish), *The quality bar* (photo required to submit, description, pricing norms). Each section carries its help-key-derived `id` anchor per the Spec A contract. Ends with a "Read the full guide: **Become an instructor & create your first class**" link to article 12.
  3. **Completion card** (the primary action): a `<form method="post" action="{% url 'classes:teach_orientation_complete' %}">` with `{% csrf_token %}`, the `acknowledge` field via `{% include "components/form_field.html" with field=form.acknowledge %}` — a BooleanField, so it auto-renders as a `components/toggle.html` toggle, labeled "I've read the expectations above and I'm ready to teach." Below it, clear of the toggle (≥`1rem` margin, per FRONTEND.md rule 18), the submit: `pl-btn pl-btn--primary` **"Unlock teaching"**. Alpine wires `x-data="{ ok: false }"` on the card; the toggle's input sets `ok`, the button gets `:disabled="!ok"` + a disabled style — the button visibly wakes up when the box is ticked. Server still enforces via the form (`required=True`), so a JS-less submit gets the field error, not a bypass.
- **Progress through sections — decided: none.** No scroll-spy, no per-section checkmarks, no "next" stepper. The content is three short sections on one page; the acknowledge toggle *is* the explicit completion act the brief asks for. Scroll-gating is fiddly, hostile on mobile, and trivially gamed anyway.
- **No tour launcher on this page** — decided. Spec C's instructor tour starts on `classes:teach_overview` (its `?tour=` route and DOM targets live there); launched from here it would either 302 a locked member straight back to this page or abort with no targets. Instead, C's existing auto-offer greets the member on their first *post-unlock* visit to the teach overview — the natural next page after the success redirect.
- **States:**
  - *Locked (default):* as above.
  - *Invalid submit:* full-page re-render; `form_field.html` shows the error under the toggle ("Please confirm you've read the orientation before unlocking teaching."); banner and content unchanged — nothing lost (there's nothing else to lose; one field).
  - *Success:* redirect to `/classes/teach/` with the Django success message "Teaching unlocked — welcome, instructor." rendered by the base template's message area (full-page flow → messages, not toast, per FRONTEND.md).
  - *Already completed:* banner and completion card are replaced by a single "You're an instructor" card — check icon, "Orientation completed {{ member.instructor_oriented_at|date }}.", primary link "Go to the teaching portal" (`classes:teach_overview`), secondary links "Create a class" and the article-12 guide. The content card stays readable (it's still the reference page; article 12 links here).
  - *Empty/error (seed missing):* if the seeded article isn't present (fresh env before seeds run), the content card shows a quiet placeholder ("Orientation content hasn't been loaded yet — run the help-center seed command.") **and the completion card still works** — the gate must never be un-passable because a seed is missing. Fail soft here, loudly in logs.
- **Dark + light:** everything on theme tokens — `hub-card`, `.pl-md`, `pl-btn`; the toggle component is already theme-correct; no form controls beyond the toggle, so no input-token pitfalls; no date/time inputs. New classes (`pl-orientation-banner`, disabled-button style) use the `pl-` prefix in `hub.css` on `--hub-*` tokens. Verify both themes.
- **Mobile:** single column already; cards stack; the button is full-width under 480px (house pattern); tap targets are real buttons. 8px-grid spacing throughout.

### Screen 2 — the redirect explainer (locked member hits any `/classes/teach/*` URL)

- Not a separate template: the decorator 302s to `/classes/teach/orientation/`, and the page's explainer banner (Screen 1, state *Locked*) is the explainer. One page to maintain, zero dead ends.
- The member's entry link keeps working ("Manage My Classes", "Teach a Class", the book-account "Teaching dashboard" banner, article links) — it just lands here until unlocked. After unlocking they land on the teach overview; from there "Teach a Class"-style deep links work normally on the next click. (No `?next=` return-to-target — one extra click after a once-ever ceremony isn't worth threading a redirect param through; noted in §10.)
- **The revoked-instructor-clicks-an-email-link walk:** an instructor whose access was revoked still receives instructor emails for their existing classes (new registration, edit links — `classes/emails.py:208,324,344,482,489,500`, untouched per §5). Clicking any of those deep links 302s them here; the explainer banner tells them the portal is locked, and the completion card below it is the self-re-unlock road from §5 — tick the box, "Unlock teaching", and the next click on the same email link works. No dead end, and no special-casing: it's the same Screen 1 locked state.
- An **inactive** member or non-member still gets the existing 403 ("An active member account is required…") — unchanged, and correct: orientation can't fix an inactive account.

### Screen 3 — admin override (`templates/hub/admin/member_edit.html`, Details tab)

- **Placement:** a new "Teaching access" block in the Details column, below the existing form card (which ends in "Save member") with clear separation — it is its own card with its own action, **not** a field inside `MemberAdminEditForm` (grant/revoke is an event with an actor and an activity log, not a row attribute to batch-save; and revoke needs a confirm, which a form toggle can't give cleanly).
- **Contents:**
  - Status line: either "Can create classes — unlocked {{ member.instructor_oriented_at|date }}." or "Locked — hasn't completed the instructor orientation."
  - One action button, label by state:
    - Locked → `pl-btn pl-btn--secondary pl-btn--sm` **"Grant teaching access"** — a plain `<form method="post">` to `{% url 'hub_admin_member_teaching' member.pk %}` with `action=grant`. Not destructive → no confirm. Redirects back with message "Granted teaching access for {{ name }}."
    - Unlocked → `pl-btn pl-btn--danger pl-btn--sm` **"Revoke teaching access"** — opens `components/confirm_modal.html` (`confirm_id="revoke-teaching"`, title "Revoke teaching access?", message "{{ name }} will be locked out of the teaching portal and can't create new classes. Their existing classes and registrations are not affected. They can re-unlock by completing the orientation again.", `confirm_action_url` = the same endpoint with `action=revoke`, button "Revoke access"). Redirects back with message "Revoked teaching access for {{ name }}."
  - Hidden entirely for the non-member `admin_user_edit` mode of this template (`is_member` guard, like the existing member-only blocks).
- **States:** the two status states above; a POST with a bad `action` → 400 (never reachable from the UI); success feedback is the Django message on the redirected page (full-page POST, matching this page's sibling actions). Empty state n/a (the card always renders one of two states).
- **Dark + light:** card + buttons + confirm modal are all existing themed components; the status line uses `hub-text-muted` for the date. Verify both themes.
- **Mobile:** the member edit page already stacks its columns; the card follows; buttons are full-width at narrow widths like its neighbors.
- Note for admins in the card's fine print: "The Instructor *role* (public instructor page) is separate — set it with the Role dropdown above." Prevents the obvious confusion between the two.

### Screen 4 — entry points (unchanged, verified)

- Catalog hero "Manage My Classes" (`classes/public/list.html:19`), guild page "Teach a Class" (`hub/guild_detail.html:312`), book-account instructor banner (`classes/account/_components/instructor_banner.html:13`), instructor email deep links (`classes/emails.py`), legacy 301s: **no template or email changes.** Visible to all active members; the decorator routes locked members to orientation. This is the "no hidden dead ends" decision made concrete.
- Article 12 (Spec A) links to `/classes/teach/orientation/` as its "start here" CTA; the orientation page links back to article 12 (help key `teach.become-instructor` covers both).

### Checklist walk (ux-completeness-checklist)

- **§1 list editors:** none on any screen (no formsets).
- **§2 forms:** both forms have a named submit ("Unlock teaching", "Grant/Revoke…"); the one field goes through `form_field.html`/`toggle.html`; validation in `InstructorOrientationCompleteForm.clean` with a stated error message; 1-field forms live inline in their cards (a modal for a one-toggle acknowledgment on a content page would be worse).
- **§3 destructive:** revoke goes through `confirm_modal.html` with the consequence named (existing classes untouched, self-re-unlock possible); `pl-btn--danger pl-btn--sm`. Grant and complete aren't destructive.
- **§4 states:** empty (seed missing), error (invalid submit, bad action), success (messages), loading (full-page navigations — no HTMX swaps to spin), already-completed, locked/unlocked — all specified per screen. No dead ends: every locked path lands on the orientation page, which always shows the way through.
- **§5 themes:** tokens only; the sole form control is the shared toggle; no date/time inputs; no inline background/color; both themes to be verified.
- **§6 mobile:** single-column stacks, real buttons, 8px grid.
- **§7 spacing:** completion button clears the toggle (margin per rule 18); new classes are `pl-` prefixed in `hub.css`.
- **§8 components:** `form_field`, `toggle`, `confirm_modal`, messages/toast conventions — all named above; nothing reinvented.
- **§9 emails:** none sent (see §7).
- **§10 user lens:** one page, one checkbox, one button; a locked member can always see *why* and *what to do*; nothing half-built (grant has revoke, complete has a completed state, the article links both ways).

## 7. Notifications / emails / activity

- **`SiteActivity.Kind` additions** (`core/models.py:926`): `INSTRUCTOR_ORIENTED = "instructor_oriented", "Completed instructor orientation"`, `TEACHING_GRANTED = "teaching_granted", "Teaching access granted"`, `TEACHING_REVOKED = "teaching_revoked", "Teaching access revoked"`. Logged from the model methods (§5) with `actor`/`target` set, so `/manage/activity/` shows the stream.
- **Admin "X just unlocked teaching" notification — weighed, and skipped.** Adding a spine `EventType` (`core/events/registry.py`) means a trigger, resolver (`fog_admins` exists), copy, and a settings-matrix row — real surface for a signal with no action attached: any active member may complete orientation, and the moment an admin actually cares is the first class *submission*, which already fires `class_review_requested` / admin review email. The activity feed carries the completion record. If Josh later wants the ping, the spine addition is a small follow-up (`activity_kind` would then move onto the event per the registry convention, one vocabulary).
- **No emails.** The success message + unlocked portal is the confirmation; an email teaching-unlocked receipt is noise.

## 8. Build order (phased; each phase ships green)

**No build dependency on Spec C** (the TourState write was dropped in §4) — C can land before, after, or in parallel; its instructor tour simply auto-offers post-unlock once both are live. Phase 2 assumes Spec A's seed pipeline + rendering path exist (A is first in the overall order A → B/C/D). Each phase passes the full suite + `ruff` + `mypy`.

1. **Model + migrations (no behavior change).** `instructor_oriented_at`, `can_create_classes`, `complete_instructor_orientation` / `grant_teaching` / `revoke_teaching`, `apply_admin_role` addition, `SiteActivity` kinds, migrations 0106 + 0107 (backfill + reverse). Decorator untouched, so the app behaves identically. Tests for all of the above.
2. **The gate + the page (the feature flips on here, atomically).** `active_member_required`, new `teaching_member_required` body, `teach_orientation` + `teach_orientation_complete` views/routes, `InstructorOrientationCompleteForm`, `orientation.html`, `pl-` CSS, seeded `instructor-orientation` article content (through Spec A's pipeline), help-key registry entry, article-12 cross-links. Gate and unlock path must land in one phase — never ship the lock without the door. If Spec C is already live, this phase also flips the instructor tour's audience in `core/tours.py` to `member.can_create_classes` and retouches its step-1 copy (C §7.3); if C lands after D, C ships that audience from day one.
3. **Admin override.** `admin_member_teaching_set` view + route, the Teaching access card + confirm modal in `member_edit.html`. Tests.
4. **Housekeeping.** Bump `plfog/version.py` VERSION; one member-facing CHANGELOG entry for the current release line, e.g. — *"Become an instructor: New to teaching? A short orientation now walks you through expectations and how class review works. Complete it once and the teaching portal unlocks automatically — no waiting on an admin. Already taught a class? You're grandfathered in."* (Per CLAUDE.md: fold into/alongside the release line's help-center entry if these ship together.)

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*`, factory-boy, 100% branch coverage. **`context_*` is NOT a collected prefix** — every nested block, including conditionals, uses `describe_*` (e.g. `describe_when_locked`).

- `tests/membership/instructor_unlock_spec.py`
  - `describe_Member_can_create_classes`: null field → False; set → True.
  - `describe_complete_instructor_orientation`: sets timestamp; logs `INSTRUCTOR_ORIENTED`; writes no `TourState` row; idempotent (second call → no second activity row, timestamp unchanged); inactive member → `ValueError`.
  - `describe_grant_teaching` / `describe_revoke_teaching`: timestamp set/cleared; activity kinds + actor; idempotency (revoke when already null → no log); revoke leaves the member's existing `ClassOffering` rows and statuses untouched.
  - `describe_apply_admin_role`: first-time promotion to instructor unlocks and logs `TEACHING_GRANTED` (`actor=None`, `payload={"via": "instructor_promotion"}`); **re-saving an already-slugged instructor after a revoke does NOT re-grant** (the loophole test — `_derive_initial_role` pre-fills "Instructor", so simulate the plain member-edit save path); demoting doesn't clear the unlock.
  - `describe_backfill_migration`: exercise the forward/reverse functions (via `apps.get_model` against test data), asserting §4's final querysets: member with a draft offering → unlocked; with only a cancelled/archived offering → unlocked; with `instructor_slug` and zero offerings → unlocked; plain member → still null; already-unlocked member keeps their original timestamp (the forward's `isnull=True` guard). Reverse clears exactly the base-predicate members, including one unlocked by other means (the documented over-clear).
- `classes/spec/views/teach_gating_spec.py`
  - `describe_teaching_member_required`: anonymous → login redirect; no member / inactive → 403 (unchanged); active + locked → 302 to `classes:teach_orientation` (parametrize across a representative set of teach routes: overview, create, registrations, profile); active + unlocked → 200; grandfathered member → 200. Public/registrant routes (`public_list`, tokenized `class_review`) unaffected by lock state.
- `classes/spec/views/teach_orientation_spec.py`
  - `describe_teach_orientation`: active locked member → 200 with banner + completion form; unlocked member → completed-state card (no form); inactive → 403; seed-missing → placeholder renders, form still present.
  - `describe_teach_orientation_complete`: unchecked box → 200 re-render with the field error, member still locked; checked → redirect to `teach_overview`, member unlocked, activity logged; double-POST → still one activity row.
  - Form unit: `InstructorOrientationCompleteForm` required error message.
- `tests/hub/admin_member_teaching_spec.py`
  - `describe_admin_member_teaching_set`: non-admin → denied; grant on locked member → unlocked + message + activity; revoke on unlocked → locked + activity; bad `action` → 400; card renders correct state/button per lock state; hidden on the non-member user-edit mode.
- Template lint: run `tests/template_comment_lint_spec.py` (new template, house rule).
- Gotcha to watch: the two timestamp writes (`complete_…` and the migration) both use `timezone.now()` — no date-window logic anywhere, so no tz edge cases beyond displaying `|date` in project tz.

## 10. Open / deferred — out of scope

- **Per-class approval chain: no changes.** `submit_for_review`, the guild-lead → admin gates, tokenized review pages, and reviewer notifications stay byte-for-byte as they are.
- **Instructor role / public pages: no changes.** `is_instructor` / `instructor_slug` remains admin-granted via the Role dropdown, separate from the unlock (the one-way coupling — *first-time* instructor promotion implies unlock, §5 — is the only interaction).
- **No `?next=` return-to-target** after completing orientation (land on teach overview; one extra click, once ever). Revisit only if anyone actually complains.
- **No admin spine notification** on completion (rationale §7); small follow-up if wanted.
- **No mandatory tour, no quiz, no scroll-tracking** — deliberate simplicity; the acknowledge toggle is the contract.
- **No re-orientation campaigns** (e.g. "re-acknowledge annually" or forced re-orientation on content changes) — YAGNI until policy demands it.
- **Sidebar "Teach" link** — there is none today and this spec doesn't add one; the entry points in §2 stand. A nav link is a Spec A/IA decision if ever.
