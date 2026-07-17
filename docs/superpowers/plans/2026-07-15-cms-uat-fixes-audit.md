# 2026-07-15 — CMS UAT Fixes: Grounded Audit & Fix Plan

**Status:** Confirmed. Decisions locked via a grilling session on 2026-07-15 (see "Locked decisions" below). Building in progress. Domain model captured in `/CONTEXT.md`; architecture in `docs/adr/0001` and `docs/adr/0002`.
**Scope:** The ~40-item UAT feedback batch for the classes "book" CMS + member hub.
**Method:** Every item was checked against the actual code (7 read-only investigation agents). Each carries a verdict + file:line evidence + a concrete plan.

---

## TL;DR — the big picture

The book CMS already implements a surprising amount of this list. Of ~40 items:

| Disposition | Count | Meaning |
|---|---|---|
| **A — Already done** | ~12 | Shipped and working. No build. Confirm you agree and we drop them. |
| **B — Trivial copy edit** | ~12 | A string in a Django template/form. One batched PR. *(These are code, not runtime toggles — see note.)* |
| **C — Real feature build** | ~11 | Net-new code/models/migrations. Sized below. |
| **D — Needs your decision first** | ~7 | I can't build correctly until you answer a question. |

**On "settings I can do myself":** almost none of these are runtime settings you can flip in the UI. In this Django app, user-facing copy lives in **templates** — changing it is a code change (a PR), not an admin toggle. The **only** genuinely self-serve item is the Model/Photo Release **waiver body text** (editable at `/classes/admin/settings/waivers/`). Everything else in Bucket B is a small code edit I'll batch together.

---

## Locked decisions (grilling, 2026-07-15)

These supersede the open questions in Bucket D and pin the ambiguous builds.

### Profile cluster (F1a / F1b / F1c / 5a / 5b / 5c) — see ADR 0001
- **Instructor is a role a Member holds**, never a separate table. No `InstructorProfile` model.
- **Settings = one page with a Member ⇄ Instructor tab.** The Instructor tab renders only if the member holds the instructor role. (F1a is a UI split, not a data split.)
- **Two bios:** member-directory `about_me` (member tab) and a **new `instructor_bio`** (instructor tab, labeled "About me as an instructor", shown on the public instructor page). Migration backfills `instructor_bio` from `about_me` so no live instructor page blanks. (This makes 5b a new field, not a relabel.)
- **One `MemberContact` model** — `{label, value, show_in_directory, show_on_instructor_page, sort_order}` — that **absorbs** `instructor_website`, `instructor_social_handle`, and `other_contact_info` (values migrate into seeded rows). `phone`/`discord` stay first-class. Values **auto-linkify** (no type dropdown). Editable formset mirrors the `GuildLink` pattern. (F1c + 5c.)
- **F1b** (remove instructor directory opt-out): the Instructor tab simply omits the master `show_in_directory` toggle. Already locked-on for instructors at the data layer.
- **5a**: add an "Edit your profile" heading inside the Profile tab (don't rename the page-level `<h1>`).

### Nomenclature
- **"Guild Types"** is the term (not "Class Types"). Copy sweep only: wherever the class `Category` shows as "category" or bare "Guild", it reads "Guild Type(s)." No model/URL rename. The **Guild-Lead approval role is untouched** — a Guild Type may still route approvals to a real Guild's Lead. See `CONTEXT.md`.
- **Titles rule (absorbs 3a):** standardize **hardcoded** template titles/headings only — if already UPPERCASE keep it, otherwise Title Case. **Never** normalize instructor/member-typed titles (class names, guild names). No CSS `text-transform`, no filter.

### Slugs (F4a) — see ADR 0002
- Replace the raw-ID idea with **date-stamped slugs**: `slugify(title)-YYYY-MM-DD` (offering's first session date), `-2` tiebreak for same-day collisions. **New offerings only**; existing slugs untouched (no 404s). Auto-generate on **both** teach and admin forms.

### Discounts (F6a / F6b / F6d)
- **All codes start inactive until approved.** Applies to every code, including an instructor's own-class code.
- **New per-member "can self-approve discounts" permission** (a Manage-Members checkbox, granted like the instructor role) lets trusted instructors approve their own codes; admins approve anyone's.
- **Auto-apply field removed** from the discount form entirely (retires auto-prefill-at-checkout for new codes — intended).
- Force-uppercase inputs client-side (F6a); "uses blank = unlimited" help text (F6c).

### Images (C5 / 6c)
- Submission requires **the class's own hero OR ≥1 gallery image** (the Guild-Type fallback does **not** satisfy it). "3+ gallery" is a **soft nudge**, never a block. Remove "optional" from the gallery field. Enforce in `submit_for_review()`, checking the instance (hero is AJAX-saved).

### Registration/checkout copy
- **4b** newsletter checkbox **pre-checked by default** at checkout (hidden for already-consented users via F5c). Side effect: most first-timers now flow to Mailchimp by default.
- **4c** "Continue to Payment" → **"Next — $X"** (keep the price visible).
- **4d** "Payment processed securely" → **delete** from the reg form (Stripe's hosted page has its own signaling).
- **4e** "You're in!" → **"You're registered"** (contrast bug on that screen fixed in the same pass).
- **4f** leave the real "Cancel my registration" **action** button; email CTA is already "Manage"; only tidy the "(or cancel)" descriptive copy.

### Mailchimp (F5b)
- **Keep the tag `first-time-student` as-is** (renaming would re-point the external Mailchimp automation). Keep the opt-in gate. No change — the pre-checked box (4b) already drives first-timers into the series.

### Dropped / already satisfied
- **F2b** (onboarding prefill): already satisfied — the surviving class-signup flow prefills returning guests. The "Get started" home onboarding checklist is intact and unrelated.
- **3b / 3c** ("Wear" font, "Instructor approval required"): phantom — not app copy; instructor-typed content. **Ignored** per Josh.
- **Already built, no work:** F2a series, F2c waitlist, F3a hero focal-crop, F4b SEO meta, F5a first-time lookup, F5c consent-hide, F5d WYSIWYG welcome email, F6e two-tier Guild-Lead→Admin approval, 2b "Past Lives Members", 1d guest helper text.

### Build order & branch routing
1. **Contrast hot-fix** (live prod readability bug) → hot-fix branch off `main`.
2. **Batched copy PR** (Bucket B + Guild Types + titles) → hot-fix branch off `main`.
3. **Feature builds** (profile/contacts, discounts, slugs, images, date-picker, class flyer) → `v22`.
Commit + push after each unit.

---

## Bucket A — Already done (no work; confirm and drop)

Each verified in code. If you saw these as "broken" it's likely a stale prod deploy, a specific data row, or the light-mode contrast bug (separate section below).

1. **2b — "PL Members" → "Past Lives Members".** Already renamed everywhere it renders (`_list_results.html:100`, `detail.html:371`, `register.html:36`). Old text only survives in the changelog. *Nothing to do.*
2. **1d — Guest search prompts for Last Name + Order Number.** Already does (`account/lookup.html:10–21`). *(Wording says "booking" not "class" — folded into 1b/1c copy sweep.)*
3. **F2a — Single one-off vs multi-day series.** Fully built: `ClassOffering.SchedulingType = SINGLE_SESSION|SERIES_PACKAGE`, `ClassSession` container, "Pick a date" vs "Pick a session set" UI, series badges. Shipped v2.5.9.
4. **F2c — Sold-out swap + waitlist.** Fully built end-to-end: capacity → `spots_remaining` → "Sold out" + "Join the waitlist" CTA, `Registration.Status.WAITLISTED`, auto-promote on cancel, waitlist emails, admin waitlist page.
5. **F3a — Hero image focal adjuster (class + guild).** Fully built: `HeroCropMixin` (focal x/y + crop-box), Cropper.js draw-a-box on edit forms, inline focal slider on the public detail page, `hub_hero_adjust` endpoint. Works for classes, categories, and guilds.
6. **F4b — Dynamic SEO meta tags.** Fully built: `ClassOffering.seo_title` (Class + Date + Instructor, capped ≤60) and `seo_description` (≤160), rendered in `detail.html:4–5`. *(OG/Twitter social-card tags are NOT present — net-new if you want them; see D7.)*
7. **F5a — Identify first-time signups.** Fully built: `derive_tags()` checks prior `CONFIRMED` registrations + `_is_known_member()` across all three email stores.
8. **F5c — Persistent newsletter consent.** Fully built: logged-in + already-consented → checkbox removed (`forms.py:621–623, 664–670`); guest/not-consented → shown.
9. **F5d — WYSIWYG welcome-email editor.** Already a Quill editor: `TeachWelcomeEmailForm` uses `RichTextEditorWidget` + `sanitize_rich_html` + `|rich_email_body`. *(One real bug rides along — see C11.)*
10. **F6e — Multi-tier class approval (Guild Lead → Admin → publish).** Fully built: `submit_for_review()` opens the Guild-Lead gate (or Admin if no guild lead), `ClassApproval` per-role rows with emailed review tokens, escalates to Admin on lead approval, publishes only when all required roles approve. The exact workflow described.
11. **F1b — Instructor directory opt-out.** Already impossible at the data layer: `must_be_listed_in_directory` force-locks instructors ON. Only a (disabled) toggle still *renders* — its removal folds into F1a.
12. **F5b — Conditional first-timer tagging.** Mechanism fully built (returning members are NOT tagged; new emails get the first-timer tag → Mailchimp automation). Two small deltas → see D6.

---

## Bucket B — Trivial copy edits (one batched PR)

All are hardcoded strings in templates/forms. **Several are guarded by tests that will fail unless updated in the same PR** (flagged ⚠). One PR, ~1–2 hrs incl. test updates.

| # | Change | Location | Notes |
|---|---|---|---|
| 1b | "Find your booking." → "Find your class." | `classes/account/lookup.html:9` | ⚠ breaks `lookup_spec.py:33` |
| 1c | "Find my booking" → "Find my class" | `classes/account/lookup.html:25` | (current text is "Find my booking") |
| 2a | Remove "Workshops" from catalog; "Manage Classes & Workshops" → "Manage classes" | `classes/public/list.html:4,8,17` | also detail back-link `:105`, meta desc — decide breadth |
| 3a | Title-case the class-detail section headings | `classes/public/detail.html` (~12 static `<h2>/<h3>`) | e.g. "About this class" → "About This Class" |
| 4a | "How should we call you?" → "What should we call you?" | `hub/forms.py:338` | it's the placeholder on `preferred_name` |
| 4c | "Continue to Payment" → "Next" | `classes/public/register.html:187` | ⚠ breaks `register_spec.py:87`; **drops the "— $X" price suffix** (confirm OK) |
| 4e | "You're in!" → "You're registered" | `classes/public/register_success.html:12` | ⚠ breaks `register_spec.py:273` + `login_and_book_spec.py:68` |
| 4f | "(or cancel)" descriptive copy tidy | `register_success.html:19`, `confirmation.{html:57,txt:18}`, `reminder.{html:52,txt:13}` | email CTA is **already** "Manage Registration" — mostly done; action button → see D4 |
| 6a | Remove "Actor" column | `classes/admin/activity.html:37` (`<th>`) + `70–76` (`<td>`) | keep server-side actor filter |
| 6c | "Hero / banner image" → "Upload image"; drop gallery "Optional." markers | `_components/hero_image_field.html:12`; `teach/class_form.html:52`, `admin/class_form.html:56` | one shared hero component covers both surfaces |
| 1e | "Model Release" → "Photo Release" (labels) | `register.html:171,180`; `forms.py:560,567,708`; enum display `models.py:1244` | internal field names stay; **waiver body text is self-serve, Bucket E** |
| F6c | "uses" help text → "Leaving the 'uses' field blank indicates unlimited uses." | override in `DiscountCodeForm.Meta` (model help_text `models.py:1157`) | |

**Two copy items with a structural caveat (still small, but not a blind find-replace):**

- **5a — "Edit your profile" header.** The page `<h1>` is "User Settings" (`user_settings.html:5`) and labels the *whole* 4-tab page — renaming it mislabels the Emails/Notifications/Guilds tabs. Plan: add a per-tab heading "Edit your profile" **inside** the Profile tab rather than rename the page title. (small)
- **5b — "About me" → "About me as an instructor".** `about_me` is the *shared* member-directory bio, not instructor-only (`membership/models.py:366`). Renaming the label (`user_settings.html:178`) mislabels it for all non-instructor members. Plan: make the label conditional on `member.is_instructor`. (small)

---

## Bucket C — Real feature builds (net-new; sized)

Ordered roughly small → large.

- **C1 · 6a-sort / 6b — "Most Recent" sort header on the activity feed.** *(small)* No sort control exists today; the feed is already hard-ordered newest-first (`views.py:2051`). Reuse the existing `{% sort_header %}` tag on the "When" column + add an oldest-first branch. Near-no-op unless you also want an "Oldest" toggle.
- **C2 · F6b — Remove the "Auto-apply" field from the discount form.** *(small)* Drop `auto_apply` from `DiscountCodeForm.Meta.fields/labels` only. ⚠ **Keep the model field** — `best_auto_apply_for()` + checkout pre-fill read it. New codes default `auto_apply=False`, silently retiring auto-prefill for new codes. Confirm that's intended (vs. retiring the whole mechanism).
- **C3 · F6a — Force-uppercase discount inputs (client-side).** *(small)* Server already uppercases on save + lookup. Add `text-transform:uppercase` + an `oninput` uppercaser to the admin `code` field and the checkout `discount_code` field. Purely cosmetic — no correctness bug.
- **C4 · 4b — Pre-check the newsletter checkbox at checkout.** *(small)* Add `self.fields["wants_newsletter"].initial = True` guarded by `if not self.is_bound` in `RegistrationForm.__init__` (mirror the `create_account` pattern). Note: there is **no** newsletter checkbox on the allauth signup page — the "account creation" opt-in is `create_account`, which is *already* pre-checked.
- **C5 · F3b — Mandatory hero image + 3-photo nudge.** *(small-med)* Enforce in `submit_for_review()` (not on draft save): block submit if no hero on the instance (AJAX-saved — check `offering.image`, not the form field); soft-nudge ≥3 gallery images; remove "optional" from the gallery field. Decide whether a `category.hero_image` fallback counts as "has a hero."
- **C6 · F2d — Simplify the class date picker.** *(small-med)* Replace the Alpine month-grid calendar (`_components/session_calendar.html` + `session_calendar.js`) with an inline date+time+duration + "Add" button that appends to the **already-existing** visible session list. ⚠ Must keep emitting the exact `sessions-<i>-starts_at/ends_at/id/DELETE` inline-formset field names or saving breaks silently. No model change.
- **C7 · F3c — Class flyer/PDF (mirror the working guild flyer).** *(small-med)* Add a `class_flyer` view + `class_flyer.html` + CSS mirroring `guild_flyer` (browser-print → Save as PDF; **no PDF library in the repo**). Both pieces already exist for classes: `ClassOffering.qr_svg()` (encode the pk permalink, slug-proof) + the focal-cropped hero. Handle the `legacy_image_url` fallback.
- **C8 · 1a — "Guilds" → "Class Types" (catalog filter label).** *(small-med)* The catalog "Guild" filter is the `classes.Category` model (`verbose_name="Guild"`), **not** the hub's `membership.Guild`. Relabel ~12 template strings + the model `verbose_name` (display only; no model/field/URL rename). ⚠ Must **exclude** the real `membership.Guild` references: the "Supported by the {guild}" card, the top-bar Guilds link, and the "Guild Lead" approval role. **See D-note below** — the Category still links to a Guild for approval routing, so "Class Types" and "Guild Lead" will coexist.
- **C9 · F1a + F1c + 5c — Instructor/member profile split + multi-contact.** *(medium; the biggest coherent build)* These three are one feature area:
  - Split the combined `ProfileSettingsForm` into a **member settings** form and a dedicated **instructor settings** page (reuse the dead `TeachProfileForm`; `teach_profile` already exists as a redirect stub). Recommend **UI/form split, not a model extraction** — instructor fields already live isolated on `Member`; a full `InstructorProfile` model + data migration is large and unnecessary for the stated goal.
  - Add a **labeled multi-contact** system: a `MemberContact{label, value, sort_order}` child model + editable formset (mirror the `GuildLink` formset pattern per FRONTEND.md). Migrate the existing single `other_contact_info` value in. Decide how per-contact directory visibility works (current `directory_visibility` JSON is keyed by fixed field names).
- **C10 · F6d — Discounts default to inactive-until-approved.** *(small-med)* Full approval infra exists (`is_approved` field, gating, admin approve action, pending-state UI). This is a **policy flip**: change the `is_approved` default to `False` (migration) + remove the two auto-approve shortcuts (admin `form.save()` and the instructor-own-class path). Confirm instructors lose auto-approval of their own class codes.
- **C11 · F5d-bug — Reminder email renders welcome body as plain text.** *(small)* The welcome-email body is rich HTML, but `reminder.html:44` pipes it through `|linebreaks` instead of `|rich_email_body`, so raw tags show. Fix the filter + update the stale "Plain text" help_text on the model field.
- **C12 · Light-mode contrast** — see dedicated section; it's a real live-prod readability bug (hot-fix priority).

---

## Bucket D — Needs your decision before I build

1. **3b — "Wear" heading font + capitalize "Reminders".** The string **"Wear" does not exist anywhere in the codebase.** All class-detail body fields render instructor-typed free-text via `|linebreaks`. This is almost certainly **instructor-entered content on one specific class**, not template copy. → *Send me the exact class URL/screenshot; if it's typed data, it's an edit to that class, not a code fix.*
2. **3c — Remove "Instructor approval required".** Same story: the phrase isn't in any template. Likely instructor-typed data, or you may mean the admin setting `ClassSettings.instructor_approval_required`. → *Need the class URL to confirm.*
3. **4d — Move "Payment is processed securely" to the final payment page.** **There is no in-app payment page** — paid checkout redirects to Stripe-hosted Checkout. Options: **(a)** just delete the line from the registration form (Stripe's page has its own security signaling), or **(b)** inject it onto the Stripe page via Checkout `custom_text`. → *Which?* (I recommend (a).)
4. **4f — The actual "Cancel my registration" button.** On the manage page (`my_registration.html:62`) this is a real cancel **action**, not a link — relabeling it "Manage" would be wrong (the page *is* the manage screen). The email CTA is already "Manage Registration." → *Confirm we leave the action button as "Cancel my registration" and only tidy the surrounding copy.*
5. **F2b — Onboarding auto-prefill.** The forward-cache machinery exists, but the **3-step onboarding wizard it was meant to prefill was deleted** in the #108 booking overhaul. There's no live onboarding form to prefill. → *What is "the onboarding form" now — the account `/profile/` page, a rebuilt wizard, or the hub "Get started" checklist?*
6. **F5b — First-timer tag details.** The shipped tag is `first-time-student`, not "First-Time Class Taker", and first-timers are only pushed to Mailchimp **if they tick the newsletter box.** → *(a) Rename the tag? (this re-points your Mailchimp automation/segment) (b) Push first-timers to Mailchimp even if they didn't opt in?*
7. **F4a — Append Class ID to slugs (`/classes/intro-to-blacksmithing-104`).** This **contradicts a prior locked decision** (the SEO plan explicitly rejected changing existing slugs — it 404s every indexed URL). And **F4b already delivered the real SEO win** (unique per-class titles/descriptions). → *Options: (a) drop it — F4b already fixed indexing; (b) apply to NEW offerings only + add 301 redirects for old ones. I recommend (a).*

**Cross-cutting note on C8 (Guilds→Class Types):** the classes `Category` links to a `membership.Guild` to route the Guild-Lead approval step (F6e). If we relabel the catalog filter to "Class Types," the approval flow will still say "Guild Lead." That's fine and probably correct, but flagging so it's a conscious choice, not a surprise.

---

## Bucket E — Self-serve (you can do this without me)

- **1e (partial) — Model/Photo Release waiver BODY text.** The actual waiver paragraph is editable at **`/classes/admin/settings/waivers/`** (`ClassSettings.model_release_waiver_text`). If you want the body copy to say "Photo Release" too, change it there. I'll handle the surrounding **labels/headings** (Bucket B, 1e), which are template code.

---

## Light-mode contrast fix (your mid-turn report)

Two root causes, both tight and bounded — not a scattered rewrite:

- **Root cause A — the `--cream` token (your exact "You're in!" screen).** `--color-cream` (`#F4EFDD`) is defined only in `:root` and never overridden for light mode, so cream-on-white ≈ **1.1:1** (invisible, reads as a faint gold tint). Hits **2 lines**:
  - `register_success.html:18` — "Your registration is confirmed…" ← **the one you reported**
  - `register_cancelled.html:18` — same pattern
  - **Fix:** replace inline `style="color:var(--cream)…"` with the existing `class="dc-txt"` (themed `--text`). 2-line edit.
- **Root cause B — bright `--gold` used as TEXT.** The system already has the right token: `--gold` = fill color, `--gold-text` = `#7a5d28` (dark gold on light, auto-restored to bright gold in dark mode). ~11 rules paint links/labels/headings with bright `--gold` (≈1.8:1, fails AA) instead of `--gold-text`. **Fix:** swap `color: var(--gold)` → `var(--gold-text)` on those ~11 text rules in `cms-public.css` + `classes-register.css` (price total, waiver heading, prose links, instructor/guild links, waitlist button label, filter legends/reset, results reset). Plus ~6 `--gold-light` hover regressions. **Leave all fills/borders/`accent-color` and gold-on-navy monograms untouched.**

Verify in both Slate (light) and Obsidian (dark). This is a live-prod readability regression → I'd ship it as a **hot-fix** ahead of everything else.

---

## Proposed sequencing (for your confirmation)

1. **Hot-fix now:** light-mode contrast (C12) — real prod bug.
2. **One batched copy PR:** all of Bucket B (+ test updates).
3. **Small builds:** C1–C7, C10, C11 — each its own PR, committed + pushed as it lands.
4. **Medium build:** C9 (profile split + multi-contact) — its own PR.
5. **C8 (Guilds→Class Types)** after you confirm the approval-label coexistence note.
6. **Decisions (Bucket D)** answered → fold into the relevant PR.

Branch routing per the established workflow: live-prod **fixes** (contrast, copy on shipped pages) → a hot-fix line; **net-new features** (C8, C9, flyer, etc.) → the `v22` line. I'll confirm the exact branch with you before pushing.

**Open question for you:** how do these ~40 fixes sequence against the 8 unbuilt `v22` feature specs? My read of your message is *fixes first, then v22 features* — confirm and I'll start with the contrast hot-fix.
