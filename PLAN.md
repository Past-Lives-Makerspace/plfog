# cms-revisions-round-2 — plan

> Branch: `cms-revisions-round-2` (off `main` @ cfaecb1).
> Target version: **2.3.0** (next minor; previous release 2.2.2). User mentioned both "2.3.0" and "2.4 branch" in one breath — confirming as 2.3.0 unless redirected.

This is one PR. Everything below ships together as v2.3.0.

---

## 0. What I verified before writing this

- **Brand blue.** The "correct" brand color across the Past Lives ecosystem is **navy `#092E4C`** (already present in the FOG palette as `--color-navy` / `--color-matte-navy`, and used as the dark header on classes.pastlives.space). The bright link-blue `#0b5be0` shows up on the Drupal classes site for CTAs/links; we'll introduce it as `--color-brand-link` so the existing FOG navy stays the dominant brand surface and the bright blue becomes the underline/CTA accent that customers already associate with us. The dusty-blue page background `#96ACBB` is already the FOG `--hub-text-muted` token — no new color is needed.
- **The "blue background image" the user wants gone** is the Squarespace BACKDROP JPG hard-linked in two places:
  - `templates/classes/base_public.html:17` (public class portal — book.pastlives.space)
  - `static/css/book-account.css:6` (the lightweight book.pastlives.space account pages)
  Removing both lets the FOG palette show through.
- **Category↔Guild is already wired** (`classes.Category.guild → membership.Guild`). And **Guild Lead already exists** (`membership.Guild.guild_lead → Member`, plus a `FogRole.GUILD_LEAD` role enum, plus a `Member.is_guild_lead` property). The dual-approval feature has all the data plumbing it needs — only the workflow and notifications are missing. We do not need to "match categories with guilds" as a schema change; we need a human pass through the admin to fill in `Category.guild` where it's NULL today.
- **Approval is currently single-tier**: `ClassOffering.approve(admin_user)` flips status from PENDING → PUBLISHED in one step. No guild-lead gate, no per-approver records, no per-class notes.
- **classes.pastlives.space has no SVG guild icons** — it relies on photographic thumbnails. The 14 categories shown there are: Art Framing, Ceramics, Creative Business, Education, Food Independence, Glass, Jewelry, Leather, Metalworking, Tech, Textiles, Visual Arts, Woodworking, Writing. We will have to source/produce icons ourselves (see §10).
- **DiscountCode is global** (no class FK) and already gates instructor-created codes behind `is_approved`. A per-class scope is an optional FK plus the right queryset filters at registration time.
- **No video field exists** on `ClassOffering`. New field needed.

---

## 1. Branding & theme parity (CMS = FOG)

### 1a. Remove the Squarespace backdrop everywhere

- `templates/classes/base_public.html:17` — strip the `background-image:url(…BACKDROP…)` rule. Page should fall back to `var(--hub-bg)`.
- `static/css/book-account.css:6` — same deletion.
- Anywhere the templates rely on the JPG washing out content (frosted blur on `.cp-page #cat-nav`, `book-account.css` glass panels) — verify legibility and remove `backdrop-filter` where it no longer reads against the new background.

### 1b. Inherit the FOG palette

The FOG palette in `hub.css` is already correct. Public CMS templates currently fork their own colors inside `base_public.html`. We will:

- Move `base_public.html`'s inline `<style>` out into a new `static/css/cms-public.css` so it can be themed properly.
- Map every color reference in there to the existing CSS variables (`--hub-bg`, `--hub-card-bg`, `--hub-text`, `--color-navy`, `--color-tuscan-yellow`, `--color-cream`).
- Introduce one new token: `--color-brand-link: #0b5be0` for hyperlink/CTA accents. Map both themes.

### 1c. Add dark/light mode to the CMS surfaces

The hub already has dark/light via `[data-theme="light"]` override blocks in `hub.css`. Extend it to:

- The public class portal pages (`base_public.html` and everything that extends it).
- The CMS admin tabs (`admin/base.html`) — currently inherits the hub topbar; verify both themes work end-to-end on `classes_list.html`, `class_form.html`, `class_detail.html`, `discount_codes.html`.
- The book.pastlives.space lightweight account pages (`book-account.css`).
- Persist the choice the same way the hub persists it (existing `localStorage` key + `data-theme` attribute on `<html>`). Add a toggle to the CMS topbar — re-use the hub's existing toggle component rather than building a second one.

### 1d. CMS public header matching pastlives.space

The Squarespace site uses a tall wordmark header with horizontal nav (Home / Guilds / Membership / Classes / Events / Studios / Contact / Become a Member). The CMS public surface should mirror that shape so a customer landing on book.pastlives.space feels they're on the same site:

- Replace the current centered logo-on-blue header with a slim navy bar carrying the PAST LIVES wordmark on the left and a small nav row on the right (Classes, Membership, Login). Same height and weight as Squarespace.
- Pull the wordmark from the existing logo asset; do not embed external Squarespace URLs.
- This header is only for the *public* CMS (book.pastlives.space surface), not the member hub.

---

## 2. All-classes list — dropdown + filter

Currently `templates/classes/public/list.html` (108 lines) shows a sticky horizontal category nav (`#cat-nav`) only. Add **on top of** those buttons:

- A category `<select>` dropdown (mobile-friendly fallback for the chip nav).
- A "Filter" button that opens a small modal/popover with: category multi-select, instructor multi-select, price-range slider, "members-only" toggle, "free" toggle, "has upcoming sessions" toggle.
- Use HTMX to update the grid in place. Persist filter state in the URL querystring so links are shareable.
- Empty-state copy when filters yield zero classes.

---

## 3. Dual approval workflow (admin + guild lead)

### 3a. Data model

Add a new model `classes.ClassApproval`:

```python
class ClassApproval(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        GUILD_LEAD = "guild_lead", "Guild Lead"

    class Decision(models.TextChoices):
        APPROVED = "approved", "Approved"
        CHANGES_REQUESTED = "changes_requested", "Changes Requested"
        DENIED = "denied", "Denied"

    class_offering = FK(ClassOffering, related_name="approvals")
    role = CharField(choices=Role.choices)        # which gate this satisfies
    decision = CharField(choices=Decision.choices)
    decided_by = FK(User)                          # who clicked
    notes = TextField(blank=True)                  # optional reviewer notes
    token = CharField(unique=True)                 # for emailed approve/deny links
    decided_at = DateTimeField(auto_now_add=True)
```

Constraints: unique (`class_offering`, `role`, `decision`) is too tight (a reviewer can change their mind); instead keep only the latest decision per (class, role) by using `created_at`-ordered queries and a model method `latest_for(class, role)`.

### 3b. Lifecycle

Extend `ClassOffering.Status` with one new value:

- `PENDING` stays. We replace the existing "PENDING → PUBLISHED on single admin click" with:
  - `submit_for_review()` → PENDING. Sends emails (§4).
  - `ClassApproval.record(role, decision, user, notes)` writes a row, sends emails (§4), and if BOTH required gates are now `APPROVED`, flips the offering to `PUBLISHED`.
  - Required gates are computed: always `ADMIN`. Add `GUILD_LEAD` if `class.category.guild_id` is not null AND `guild.guild_lead_id` is not null.
- `CHANGES_REQUESTED` decision flips offering status back to `DRAFT` and clears prior approvals so the instructor edits + resubmits.
- `DENIED` decision flips offering to `ARCHIVED` (we keep history; we never hard-delete a denied class).

### 3c. Reviewer UI

- A new page `/classes/admin/review/<class_pk>/` that shows: full class metadata, gallery, sessions, who the assigned reviewers are, and three big buttons — Approve / Request Changes / Deny — with a required notes field on the bottom two.
- Same page reachable via the tokenized URL `/classes/review/<token>/` so emailed reviewers can act without a hub login. Token is single-use per decision but the page itself is idempotent (re-opening shows the current state).
- A "My pending reviews" widget on the CMS admin home and the instructor dashboard for guild leads.

### 3d. Existing data migration

Every currently-PUBLISHED class gets a synthetic `ClassApproval(role=admin, decision=approved, decided_by=approved_by)` row so the audit trail is non-empty. Currently-PENDING classes stay pending; no automatic guild-lead approvals.

---

## 4. Transactional emails

All emails reuse the polished card layout we ship in `templates/classes/emails/` (`confirmation.html` style — dark card, gold accents, big button).

| Trigger | To | Subject | Body |
|---|---|---|---|
| `submit_for_review` | Admins + Guild Lead (if any) | "Review request: <class title>" | Class summary, instructor, category, "Open review page" CTA → tokenized URL, optional notes prompt. |
| `submit_for_review` | Instructor | "Your class is in review — here's what happens next" | Friendly explainer: who reviews, typical timeline, link to the status page, what "Changes Requested" vs "Approved" looks like. |
| Reviewer records APPROVED (not yet fully approved) | Instructor | "<reviewer role> approved your class" | Progress so far, remaining gates. |
| Reviewer records APPROVED (fully approved → PUBLISHED) | Instructor | "Your class is live!" | Public URL, share copy. |
| Reviewer records CHANGES_REQUESTED | Instructor | "Changes requested on your class" | Reviewer name + their notes verbatim, link back to edit page. |
| Reviewer records DENIED | Instructor | "Your class submission was declined" | Reviewer notes, who to contact. |

Implementation:

- New module `classes/emails/approvals.py` (or extend the existing `emails.py`).
- Two new email templates (`templates/classes/emails/review_request.html/.txt`, `templates/classes/emails/review_decision.html/.txt`).
- Send via the existing email infrastructure (`render_to_string` + `EmailMultiAlternatives`, same pattern as `confirmation.html`).
- Hooks fire from `ClassOffering.submit_for_review()` and `ClassApproval.record()`.

---

## 5. YouTube video embeds on class detail

- Add `ClassOffering.video_url = URLField(blank=True, help_text="YouTube link — embedded in the class description.")`.
- Form-level validation: must match a YouTube URL pattern (long `watch?v=`, short `youtu.be/`, shorts, embed).
- New filter `classes_tags.youtube_embed_id` extracts the 11-char ID.
- Template renders a responsive 16:9 iframe inside the description block on `public/detail.html`. No iframe if the field is empty.
- For now, YouTube only. Vimeo can be follow-up.

---

## 6. Class detail page — full width, ≥16px font, "looks full"

`templates/classes/public/detail.html` (110 lines) currently lives in a constrained container. Plan:

### 6a. Full width

- Remove the `.container mx-auto max-w-*` wrappers on the detail page; allow content to span viewport width with internal `padding: clamp(1rem, 4vw, 3rem)`.
- Hero remains edge-to-edge (already the design). Below the hero, the new layout becomes a 2-column grid at ≥1024px: main content (description, video, gallery) left, sticky booking sidebar right.

### 6b. Minimum 16px font

- Set `.cp-page` base `font-size: clamp(16px, 1rem + 0.2vw, 18px)` so 16px is the floor on every viewport.
- Bump description prose to `font-size: 17px; line-height: 1.65;`.
- Bump button labels to `font-size: 16px` minimum, raise touch target to 44px height.
- Audit all the small-print elements (badges, hints, meta lines) and either set to 14–15px deliberately or bump to 16px. No accidental 12–13px copy.

### 6c. "Looks full" sections (each only renders if it has content)

Add these blocks to the detail page so a class with rich metadata fills the screen:

1. Hero with title + instructor + category badge + next-session pill (existing — polish).
2. **What you'll do** — pulled from `description`.
3. **Embedded video** (new — §5).
4. **Gallery** with hover-zoom (existing).
5. **Schedule** — visual session list with date chips.
6. **Pricing** — full price, member price, sliding-scale note, what's included.
7. **What you'll learn** — repurpose `description` second paragraph or a new field if needed (TBD; for now, derive from description).
8. **Materials** — included + bring-your-own.
9. **Prerequisites & safety** — combined.
10. **Your instructor** — bio + photo + "see all classes by …" link.
11. **The guild** — guild card if `category.guild` is set (small "supported by the <name> Guild" panel that links to the guild page).
12. **FAQ** — two seeds: cancellation policy + accessibility. Static content for now, sourced from `ClassSettings`.
13. **Sticky right-rail booking card** — price, spots remaining, big "Register" button, social share.
14. **Below the fold: related classes** — three more from the same category.

The booking right-rail and CTAs become the visual anchor; the supporting sections give the page the dense, professional feel the user asked for.

---

## 7. Per-class discount codes

### 7a. Schema

- Add `DiscountCode.class_offering = FK(ClassOffering, null=True, blank=True, related_name="discount_codes")`.
- A null FK = global code (existing behavior). A populated FK = class-scoped.
- Add `DiscountCode.created_by = FK(User, null=True)` (audit; lets us show "your codes" to instructors and gate edits).

### 7b. Form integration

- On the class create/edit page (`templates/classes/admin/class_form.html` and the instructor version), add a "Discount codes for this class" section listing existing class-scoped codes and a "+ New code" button that opens a modal.
- Instructor-created codes are auto-`is_approved=True` when scoped to that instructor's own class (they already control the class price; per-class codes don't pose an admin-gate risk the global codes pose).
- Global discount codes management stays admin-only on the existing `/classes/admin/discount-codes/` page.

### 7c. Auto-apply at register time

- In `views.register()`, when the user is logged in and we can match them to a Member or registrant:
  - Look up active class-scoped codes for the class.
  - Pick the one that yields the lowest final price.
  - Pre-fill the discount field in the registration form and surface a "Member discount applied" banner (analogous to how the verified-member discount works today).
- When a class-scoped code exists with no usage cap, treat it as a promotional baseline price.

---

## 8. CMS list/detail typography & "feels professional"

(Some overlap with §6.) Project-wide rules for the public CMS surface:

- Body base 16px floor.
- Headings: Lato 700 for h1/h2, Lato 400 for h3. (Already loaded.)
- Body: Inter 400 with 1.6 line-height. (Already loaded.)
- Increase vertical rhythm: 32px between sections, 16px within.
- Buttons: 16px label, 44px height, navy fill + cream text + gold hover ring. Match Squarespace cadence.

---

## 9. Header parity with Squarespace

See §1d. This is the slim navy bar with wordmark + nav. Treat it as the "outer chrome" wrapping `base_public.html`. The hub keeps its sidebar; only the public CMS gets the Squarespace-style header.

---

## 10. Guild icons (replacing text placeholders)

classes.pastlives.space does **not** publish SVG icons — confirmed by inspecting their markup; they use photographic class thumbnails and the wordmark logo. We have to source icons ourselves. Proposal:

1. Add `Category.icon_svg = TextField(blank=True, help_text="Inline SVG markup — uploaded as text.")` (storing SVG markup avoids an asset CDN and lets us tint via `currentColor`).
2. Seed the 14 known guild/category icons from **Phosphor Icons** (open-source, OFL/MIT, tuned for this kind of mixed iconography):
   - Art Framing → `frame-corners`
   - Ceramics → `bowl-food` (close enough; consider custom)
   - Creative Business → `briefcase`
   - Education → `graduation-cap`
   - Food Independence → `plant`
   - Glass → `wine-glass`
   - Jewelry → `diamond`
   - Leather → `scissors`
   - Metalworking → `wrench`
   - Tech → `cpu`
   - Textiles → `needle`
   - Visual Arts → `paint-brush-broad`
   - Woodworking → `hammer`
   - Writing → `pen-nib`
3. Render: where the list/detail pages currently print the category name as a text chip, render `{{ category.icon_svg|safe }}` inside a circular tinted background plus the name beside it.
4. Each category icon is editable from the admin so we can swap to bespoke art later.

**Decision flagged for Jo**: confirm we want the Phosphor seed (vs. you supplying bespoke SVGs). If bespoke, the schema change is the same; only the seed migration differs.

---

## 11. Category↔Guild mapping (data, not code)

The schema is ready; the data isn't. After this PR lands the migration, Jo and I do a short pass through `/admin/classes/category/` to fill in `Category.guild` for any that are still NULL. Categories without a guild assignment skip the guild-lead approval gate — that's the "if a guild & guild lead exist" fallback already baked into §3b.

I'll need the canonical mapping from Jo. From the Drupal list, the 14 categories above all clearly map to Past Lives guilds, but I won't guess across "Visual Arts" vs. a specific guild without confirmation.

---

## 12. Sequencing (the order I'll build in)

Inside this one PR, I'll go in dependency order so the test suite stays green at every step:

1. **Foundations** — theme tokens (`--color-brand-link`), strip backdrop image, move CMS-public CSS into `cms-public.css`, wire dark/light, verify both themes on existing pages. *(Smallest churn; everything else builds on it.)*
2. **Header parity** — new public-CMS slim header.
3. **Class detail full-width + 16px floor + new section blocks** (without video/discounts yet).
4. **YouTube embed field** — model migration, form, template.
5. **Guild icons** — Category.icon_svg field + Phosphor seed + template usage.
6. **All-classes filter** — dropdown + filter modal + HTMX swap.
7. **Per-class discount codes** — schema, form section, auto-apply at register time.
8. **Dual approval — schema** — `ClassApproval` model, data migration for current PUBLISHED classes, manager helpers, `latest_for(class, role)` API.
9. **Dual approval — workflow** — rewrite `submit_for_review`, `approve`, `request_changes`, `deny` to use the new model.
10. **Dual approval — UI** — reviewer page in admin tab + tokenized public page.
11. **Transactional emails** — review_request, review_decision templates; trigger from §9.
12. **Waitlist workflow** — `Registration.waitlist_notified_at` field, sold-out detail-page CTA, waitlist-join flow, auto-promote on cancellation, admin Waitlist card.
13. **Activity feed** — `CmsActivity` model, write-through hooks at every workflow point, backfill data migration, admin Activity tab (with Registrations as a sub-tab), instructor-scoped slice.
14. **Light QA pass** — every modified page in both themes, both desktop and mobile widths.
15. **CHANGELOG + version bump to 2.3.0**.

Each step has its own working tree state that compiles, tests pass, and theme toggles cleanly.

---

## 13. Tests I will add (BDD specs)

- `classes/spec/models/class_approval_spec.py` — record/latest/gates logic.
- `classes/spec/models/class_offering_approval_workflow_spec.py` — submit → admin approves → guild lead approves → published; vs. admin approves alone when no guild assigned; vs. request-changes resetting to draft.
- `classes/spec/emails/review_emails_spec.py` — recipients, subject, link tokens.
- `classes/spec/views/review_view_spec.py` — token URL idempotency, permission gates, notes capture.
- `classes/spec/views/list_filter_spec.py` — HTMX filter responses for each filter dimension.
- `classes/spec/models/discount_code_spec.py` — extend with class-scoped + auto-apply selection.
- `classes/spec/templatetags/youtube_embed_id_spec.py`.
- Template smoke specs that render `detail.html` and `list.html` in both light and dark modes (asserting the `data-theme` attribute, not pixel-perfect rendering).

---

## 14. Decisions (locked)

- **Version.** 2.3.0.
- **Guild icons.** Seed with Phosphor Icons (open-source set, ~9000 icons under MIT, used as inline SVG markup so they tint via `currentColor`). Categories can be swapped to bespoke art later by editing `Category.icon_svg`. No bespoke commission this round.
- **Public CMS default theme.** Light. Member hub keeps its dark default.
- **Guild-lead decline.** Returns the class to DRAFT with a required decline reason. ARCHIVED is reserved for admin-level rejection only.
- **Discount auto-apply.** Mirrors today's verified-member discount logic — also fires for unauthenticated registrants whose email matches a verified Member email.
- **Category→guild mapping.** Schema is already ready (`Category.guild`). Jo will fill it in via `/admin/classes/category/` once this PR lands. Categories without a guild assignment skip the guild-lead gate (already baked into §3b).

---

## 14a. Waitlist (sold-out classes)

`Registration.Status.WAITLISTED` already exists in the schema — the workflow doesn't. Build it out:

### Schema

- No new model needed. Use `Registration` with `status=WAITLISTED` and a brand-new `Registration.waitlist_notified_at = DateTimeField(null=True, blank=True)`.
- Add `Registration.waitlist_position` as a computed property (rank by `registered_at` within WAITLISTED rows for the class) so the UX can show "you're #3 on the waitlist."

### Student-facing flow

- On `public/detail.html`, when `spots_remaining == 0`: the Register CTA flips to **"Join the waitlist"** with one-line copy explaining what that means (you'll get an email if a spot opens; no charge now).
- The waitlist flow reuses the registration form (name, email, optional questions) but skips Stripe entirely and creates the row with `status=WAITLISTED`, `amount_paid_cents=0`.
- Confirmation email: "You're on the waitlist for <class> — we'll email you the moment a spot opens. You're #N in line."
- Self-serve waitlist token-link lets the student check status or leave the waitlist.

### Automatic promotion

- When a confirmed registration is cancelled or refunded *and* `spots_remaining` becomes > 0 *and* a WAITLISTED row exists for that class:
  - Find the lowest-position waitlist row that hasn't been notified.
  - Send a "A spot opened in <class>! You have 24 hours to register" email with a tokenized one-click "claim my spot" link → straight to the register page with the form pre-filled.
  - Stamp `waitlist_notified_at`. Don't auto-confirm — the student still has to register (so they can decline gracefully).
  - If the link isn't used within `ClassSettings.waitlist_claim_window_hours` (new field, default 24), notify the next person in line.

### Admin visibility

- Class detail in the admin tab gets a new **Waitlist** card listing each waitlisted student with name, email, registered_at, position, and notified-at stamp.
- A "Promote to confirmed" admin action exists for the edge case where the class capacity gets bumped or someone is being manually upgraded.

---

## 14b. Activity feed (replacing the Registrations tab — admins only)

Rename the admin **Registrations** tab to **Activity** and turn it into the single pane-of-glass CMS event stream.

### Schema

A new write-through events table makes the UX clean:

```python
class CmsActivity(models.Model):
    class Kind(models.TextChoices):
        CLASS_CREATED = "class_created", "Class created"
        CLASS_SUBMITTED = "class_submitted", "Submitted for review"
        CLASS_APPROVED = "class_approved", "Approved"
        CLASS_CHANGES_REQUESTED = "class_changes_requested", "Changes requested"
        CLASS_DENIED = "class_denied", "Denied"
        CLASS_PUBLISHED = "class_published", "Published"
        CLASS_ARCHIVED = "class_archived", "Archived"
        REGISTRATION_CREATED = "registration_created", "Registered"
        REGISTRATION_CONFIRMED = "registration_confirmed", "Payment confirmed"
        REGISTRATION_CANCELLED = "registration_cancelled", "Cancelled"
        REGISTRATION_REFUNDED = "registration_refunded", "Refunded"
        WAITLIST_JOINED = "waitlist_joined", "Joined waitlist"
        WAITLIST_NOTIFIED = "waitlist_notified", "Notified of open spot"
        WAITLIST_LEFT = "waitlist_left", "Left waitlist"
        DISCOUNT_CODE_CREATED = "discount_code_created", "Discount code created"
        DISCOUNT_CODE_REDEEMED = "discount_code_redeemed", "Discount code redeemed"

    kind = CharField(choices=Kind.choices)
    class_offering = FK(ClassOffering, null=True, related_name="activity")
    registration = FK(Registration, null=True, related_name="activity")
    actor = FK(User, null=True, help_text="Who triggered this (admin, instructor, registrant, or NULL for system).")
    payload = JSONField(default=dict, help_text="Free-form details — discount code, notes excerpt, etc.")
    created_at = DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            Index(fields=["-created_at"]),
            Index(fields=["class_offering", "-created_at"]),
            Index(fields=["kind", "-created_at"]),
        ]
```

### Write points

A small helper `cms_activity.log(kind, **kwargs)` is called from:

- `ClassOffering.save()` (CLASS_CREATED on first create).
- `ClassOffering.submit_for_review()` (CLASS_SUBMITTED).
- `ClassApproval.record()` (CLASS_APPROVED / CHANGES_REQUESTED / DENIED — and CLASS_PUBLISHED when the final gate flips).
- `ClassOffering.archive()` (CLASS_ARCHIVED).
- `Registration.save()` (REGISTRATION_CREATED on create, WAITLIST_JOINED when status=WAITLISTED).
- The Stripe webhook handler (REGISTRATION_CONFIRMED, REGISTRATION_REFUNDED).
- `Registration.cancel()` (REGISTRATION_CANCELLED).
- The waitlist notification job (WAITLIST_NOTIFIED).
- `Registration` self-serve leave-waitlist endpoint (WAITLIST_LEFT).
- `DiscountCode.save()` on first create (DISCOUNT_CODE_CREATED), and at apply time during registration (DISCOUNT_CODE_REDEEMED with the registration FK and code in `payload`).

### Admin UI

The new Activity tab:

- Default view: a reverse-chronological feed across all classes — icon per kind, one-line headline ("Sarah joined the waitlist for **Intro to Welding**" / "Lin requested changes on **Beginner Wheel Throwing**"), with a clickable timestamp to the relevant detail page.
- Filter controls at the top: by kind (multi-select), by class, by date range, by actor.
- Search bar (matches actor name, class title, registration email).
- Pagination (50/page default).
- CSV export of the current filter set so admins can audit/report.
- Existing **Registrations table** does not disappear — it moves under a sub-tab "Registrations" inside the new Activity area so nothing is lost.

### Backfill

A data migration runs once to backfill `CmsActivity` from the existing tables: one CLASS_CREATED per existing class (using `created_at`), one CLASS_PUBLISHED per published (`published_at`), REGISTRATION_CREATED/CONFIRMED/CANCELLED/REFUNDED from existing Registration timestamps, WAITLIST_JOINED for current WAITLISTED rows. Without this the feed starts empty.

### Instructor view (small piece)

Instructors see a scoped slice of the same feed on their dashboard — only activity for classes they teach, only kinds that matter to them (registrations, cancellations, waitlist joins, approval decisions on their own classes). Same shape as the admin feed, no kind-filter widget.

---

## 15. Out of scope (deliberately)

- Vimeo embeds. (YouTube only this round.)
- Member hub theme overhaul. (CMS public + CMS admin only.)
- A separate dark-mode design for the FOG hub. (Already exists; leaving it alone.)
- An `EducationApp` rewrite. (`education/` is the empty placeholder; ignore.)
- Migrating off the Squarespace marketing site. (Header just *matches* it; we're not replacing it.)
