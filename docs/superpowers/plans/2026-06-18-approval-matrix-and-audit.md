# Transactional Approval Matrix & Activity-Audit Actor Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two things. (1) Turn the class-approval flow into a real **sequential escalation** — an instructor submits a class, the **Guild Lead** is notified first ("Needs Your Attention" dashboard panel + email), and only **after the Guild Lead approves** does the **Admin** get notified for executive validation; the class publishes when the admin approves. (2) Fix the site activity audit so the **acting user** is recorded on the registration-confirmed, registration-refunded, and registration-cancelled events instead of being dropped to `System`.

**Architecture:** Fat models / skinny views. The approval state machine already lives in `ClassOffering` / `ClassApproval` (`classes/models.py`); we tighten it from *parallel* to *sequential* inside `submit_for_review()` and `on_review_decision_recorded()` rather than adding a parallel system. The Guild-Lead queue surfaces on the existing **teaching** dashboard (`/classes/teach/`), which is where a Guild Lead — who is just an active member, not an admin — already lands; the Admin queue stays on the existing admin overview. Notifications go through the existing `core.notifications.dispatch` + `core.triggers` system; reviewer emails go through the existing `classes.emails` senders. The audit fix threads `actor=request.user` from the view layer down into `Registration` model methods.

**Tech Stack:** Django 5 / Python 3.13, existing `classes`/`core` apps, `core.notifications` + `core.triggers`, `classes.emails` (Resend via `core.email.send`), Django templates, pytest + pytest-describe (`*_spec.py`) with factory-boy, ruff (120) + mypy. No new dependencies. One migration is needed only if we add a new `ClassApproval`/`CmsActivity` enum value (see Decisions); no schema/column changes are required for the core flow.

---

## Background / context for the implementer

Read these before writing code. **Re-verify every line number** — the file is under active edit.

### The approval state machine today (PARALLEL — the thing we are changing)
- `classes/models.py:137-141` — `ClassOffering.Status` = `DRAFT / PENDING / PUBLISHED / ARCHIVED`. Instructors create as `DRAFT` (default at `:198-200`); there is no direct DRAFT→PUBLISHED path. Good — keep that.
- `classes/models.py:301-317` — `required_review_roles` returns `[ADMIN]`, plus `GUILD_LEAD` **only** when the class's category links to a guild that has a `guild_lead` set. This is the role set the rest of the flow keys off.
- `classes/models.py:319-340` — `submit_for_review()` moves DRAFT→PENDING and **creates one `ClassApproval` row per required role at once** (`:332`). This is the parallel fan-out: admin and guild lead both get a pending row immediately.
- `classes/models.py:342-360` — `approve(admin_user)` is a convenience that creates/decides the ADMIN row (used by the quick-approve button).
- `classes/models.py:426-498` — `on_review_decision_recorded(row)` is the lifecycle hook. On `APPROVED` it logs `CLASS_APPROVED`, notifies the instructor, then **publishes when `required_review_roles ⊆ approved roles`** (`:454-472`). On `CHANGES_REQUESTED` / `DENIED` it bounces to DRAFT (`:473-498`).
- `classes/models.py:580-673` — `ClassApproval` model. `Role` = `ADMIN / GUILD_LEAD` (`:595-597`); `Decision` = `APPROVED / CHANGES_REQUESTED / DENIED` (`:599-602`); `decide(decision, user, notes)` (`:660-673`) records the verdict and calls back into `on_review_decision_recorded`. Each row has a unique `token` for the emailed review link.

### The review-request emails today (parallel — must become staged)
- `classes/emails.py:116-180` — `send_class_review_requests(offering, approvals)` loops the freshly-created rows and emails **each** reviewer at submission: ADMIN rows → `CLASS_ADMIN_NOTIFY_EMAILS`, GUILD_LEAD rows → `guild.guild_lead.primary_email`. Also emails the instructor an explainer. Templates: `templates/classes/emails/review_request.{txt,html}`, `review_submitted_instructor.{txt,html}`.
- `classes/emails.py:182+` — `send_class_review_decision(offering, row)` emails the instructor the outcome (approved / live / changes / declined). Templates `review_decision.{txt,html}`.
- Helpers: `_admin_review_recipients()` (`classes/emails.py:18`), `_absolute_url()` (`:29`).

### Call sites that submit / decide (must keep working after the refactor)
- `classes/views.py:902-903` — `teach_class_submit`: `approvals = offering.submit_for_review(); send_class_review_requests(offering, approvals)`. **This is the only path that currently emails reviewers.**
- `classes/views.py:848` (`teach_class_create`, "Save & submit") and `:881` (`teach_class_edit`, "submit" action) — these call `offering.submit_for_review()` but **do NOT call `send_class_review_requests`** — i.e. submitting via create/edit moves to PENDING and creates the approval row(s) but **silently skips the reviewer emails** today. This is a pre-existing inconsistency. Decide: the cleanest fix is to dispatch the reviewer notification/email from inside the **model** (`submit_for_review`), so every submit path notifies regardless of which view triggered it (matches fat-models and how `on_review_decision_recorded` already dispatches). If you keep email in the view, you must add the sender to all three call sites. Prefer moving it into the model and reducing the view to just `submit_for_review()`.
- `classes/views.py:1614-1638` — `admin_class_approve` quick-approve: `row = offering.approve(request.user); send_class_review_decision(offering, row)`.
- `classes/views.py:1754-1799` — `_class_review_view` (shared by `/classes/admin/<pk>/review/` and tokenized `/classes/review/<token>/`): `approval.decide(decision, user=request.user if authenticated else None, notes=...)` then `send_class_review_decision`.

### Dashboards / surfaces
- **Admin queue exists:** `templates/classes/admin/overview.html:7-24` "Needs your attention" panel; built by `admin_overview` view (`classes/views.py:1249-1345`) from `ClassOffering.objects.pending_review()` (`classes/models.py:113-114`).
- **No Guild-Lead queue exists.** Guild Leads are NOT admins — `classes_admin_access_required` (`classes/views.py:700-717`) gates the admin area on the *actual* admin role, which a Guild Lead does not have. They reach `/classes/teach/` via `teaching_member_required` (`classes/views.py:682-697`, any active member). So the Guild-Lead queue belongs on the **teaching** dashboard.
- **Teaching dashboard:** `teach_overview` view (`classes/views.py:721-768`) renders `templates/classes/teach/overview.html`; its "Needs you" panel (`:18-36`) is the model to mirror. The view has `request.teaching_member` (a `Member`).
- `membership/models.py:368-371` — `Member.is_guild_lead` → leads ≥1 guild. `membership/models.py:535-541` — `Guild.guild_lead` FK, `related_name="led_guilds"`, so `member.led_guilds.all()` gives the guilds this member leads. `Category.guild` (`classes/models.py:67`) links a category to a guild; pending classes "for a Guild Lead" = classes whose `category.guild` is a guild this member leads.

### Notifications & triggers
- `core/notifications.py:18-63` — `dispatch(trigger_key, users, *, title, body, url, ...)`: always writes in-app rows; push/email only on opt-in (or `force_email`). `active_member_users()` (`:66-70`).
- `core/triggers.py:34-98` — the trigger catalogue. Existing instructor triggers: `instructor_class_approved`, `instructor_changes_requested` (`:47-60`). **There is NO trigger for "a class needs your review" (guild lead) or "a class needs executive validation" (admin).** Audience enum has `STAFF_ONLY` (`:19`) and `INSTRUCTORS_ONLY` (`:18`). New triggers must be added here, with a sensible category (use `"Teaching"` for the guild-lead one; `"Teaching"` or a new `"Approvals"` for the admin one — see Decisions). NOTE: `class_submitted`/`class_approved` are NOT triggers today, only `CmsActivity` kinds.

### The audit actor gap (the required fix)
- `classes/activity.py:48-66` — `log(kind, *, class_offering, registration, actor, payload)` writes one `CmsActivity` row and mirrors a subset to `core.models.SiteActivity` (`core/models.py:472-560`, `SiteActivity.log(kind, actor=, target=, payload=)`). `actor` is stored only when truthy with a pk (`:64`).
- `classes/models.py:1315-1322` — `CmsActivity.actor` FK already exists.
- **Fixed in 2.5.8:** `REGISTRATION_CREATED` and `WAITLIST_JOINED` already log `actor=user` (`classes/models.py:1020`, `:1035`), where `user = self.member.user` (`:1016`).
- **STILL BROKEN — these log with NO actor:**
  - `REGISTRATION_CONFIRMED` — `classes/models.py:1039-1041` (inside `_dispatch_status_notification`, the status-transition branch).
  - `REGISTRATION_REFUNDED` — `classes/models.py:1051-1053` (same method).
  - `REGISTRATION_CANCELLED` / `WAITLIST_LEFT` — `Registration.cancel()` at `classes/models.py:1094-1108`. `cancel()` currently takes only `reason` (`:1094`).
- **Call sites of `cancel()`:** `classes/views.py:641` (`my_registration_cancel`, self-serve — the actor is the registrant's `request.user` when authenticated, else `None`), and `classes/views.py:2001-2006` (`admin_registration_cancel` — has `request.user`, an admin, but does NOT pass it).
- **Who confirms/refunds:** CONFIRMED transitions happen in the Stripe webhook (`classes/webhook_handlers.py:62`, no human user — actor stays `None`/system, which is correct) and on the free-class path in `register` (`classes/views.py:483-486`, the registrant). REFUNDED has **no programmatic view call site today** — it is currently only reachable via the Django admin changing `status`. So the refund actor cannot be threaded from a view yet; design the model to accept an actor and record it from the only path that exists (see Decisions / Task 6).

### Project conventions
- Fat models/skinny views; signals only for cross-app decoupling — do all of this in model methods, not signals.
- BDD pytest-describe in `classes/spec/...` (e.g. existing `classes/spec/models/class_approval_spec.py`, `classes/spec/views/admin_review_spec.py`, `classes/spec/views/teach_overview_spec.py`, `classes/spec/models/cms_activity_spec.py`). factory-boy. Mock email; never mock the DB.
- 100% branch coverage, ruff (120), mypy, full type hints incl. `-> None`.
- Every PR bumps `plfog/version.py` (`VERSION` + member-friendly `CHANGELOG`). Latest is **2.5.8 (PR #108, OPEN/in flight)** — verified via `gh pr list`; next patch is **2.5.9** (re-verify before committing).

---

## Decisions baked into this plan

### DECISION 1 — Sequential vs. parallel approval: **Option A (true sequential). RECOMMENDED.**

The spec is explicit and transactional: Stage 1 is the Guild Lead, Stage 2 ("when the Guild Lead approves") is the Admin. Option B (keep parallel, only bolt on the missing panel + a second email) would leave the admin able to publish before the Guild Lead has weighed in, which directly contradicts "when the Guild Lead approves, a SECOND event notifies Admin." The refactor is **contained** — it touches three methods in `classes/models.py` (`submit_for_review`, a small helper, `on_review_decision_recorded`) and one email function (`send_class_review_requests`), all of which already exist. We are changing *when* rows/emails are created, not the row model or the decision plumbing.

**Trade-off accepted:** Slightly more churn in `on_review_decision_recorded` (it now creates the admin row + fires the stage-2 escalation on guild-lead approval) versus Option B's lower churn. We take the churn because correctness-to-spec is the whole point of the feature, and the blast radius is one file plus its specs.

**Sequential rules (locked):**
1. **Submit (Stage 1):** `submit_for_review()` moves DRAFT→PENDING and creates **only** the first-stage gate:
   - If the class has a Guild Lead (`required_review_roles` contains `GUILD_LEAD`) → create **just the `GUILD_LEAD` row**. The admin row is NOT created yet.
   - If the class has **no** Guild Lead → create **just the `ADMIN` row** (guild-lead stage is skipped; admin is Stage 1 by default). This preserves today's behavior for categories with no guild lead.
2. **Guild-Lead APPROVED (escalate to Stage 2):** in `on_review_decision_recorded`, when an approved row's `role == GUILD_LEAD`, **create the `ADMIN` row** (if absent) and fire the **stage-2 admin escalation** (notification + email). Do NOT publish yet.
3. **Admin APPROVED (Stage 2):** publishing remains "every required role approved." Because the admin row now only exists once the guild-lead gate is passed (or when there was no guild lead), `required_review_roles ⊆ approved` becomes true exactly when the admin signs off last. **Keep the existing `required.issubset(approved)` publish check** (`classes/models.py:454-456`) — it stays correct under the staged creation, which is the elegant part.
4. **CHANGES_REQUESTED / DENIED at either stage:** unchanged — bounce to DRAFT (`:473-498`). Resubmission re-runs Stage 1.

**`required_review_roles` stays the source of truth** for "what must approve" (so the publish check is unchanged); the *sequencing* is enforced purely by *when rows are created*. Do not weaken `required_review_roles`.

### DECISION 2 — Where the Guild-Lead queue lives: **the teaching dashboard (`/classes/teach/`).**
Guild Leads are active members with teaching access but not admin access. `teach_overview` already runs for them. Add a Guild-Lead "Needs Your Attention" panel there, shown only when `request.teaching_member.is_guild_lead`. Do **not** try to give Guild Leads the admin overview — that would require widening `classes_admin_access_required`, which is out of scope and risky.

### DECISION 3 — New notification triggers (added to `core/triggers.py`):
- `class_review_requested` — "Class needs your review" — category `"Teaching"`, audience `ALL_MEMBERS` (guild leads are members; we cannot scope to a guild-lead audience and it is harmless). Body per spec: *"[Instructor] in the [Guild] Guild requests approval for their upcoming class dates."*
- `class_validation_requested` — "Class needs executive validation" — category `"Teaching"`, audience `STAFF_ONLY`. Body per spec: *"[Guild Lead] and [Instructor] request executive validation to publish this class."*
Both default `email_default=False`, `push_default=False` (in-app always; reviewers also get the dedicated reviewer email below, so we do not double-email via the trigger). Adding triggers is data-only — **no migration** (triggers are a Python catalogue, not DB rows).

### DECISION 4 — Reviewer emails: split `send_class_review_requests` into two staged senders.
- `send_guild_lead_review_request(offering, approval)` — Stage 1, sent from `submit_for_review` path. Reuses `review_request.{txt,html}` with `role_label="Guild Lead"`. Still sends the instructor the existing explainer.
- `send_admin_validation_request(offering, approval)` — Stage 2, sent from the guild-lead-approved branch of `on_review_decision_recorded` (via a thin emails call; keep the model lazy-importing `classes.emails` as it already does for other senders). New template `templates/classes/emails/admin_validation_request.{txt,html}` worded per spec ("[Guild Lead] and [Instructor] request executive validation…"), or reuse `review_request.*` with `role_label="Admin"` plus a stage-2 lead-in — prefer a small dedicated template for the correct wording.
- When there is **no** guild lead, Stage 1 == admin; reuse the admin sender (or the existing admin branch) so the admin still gets an email at submission. Keep `_admin_review_recipients()` for admin addresses.

### DECISION 5 — Audit actor on the model: add an optional `actor` to the registration lifecycle.
- Add `actor=None` param to `Registration.cancel(self, reason="", actor=None)` and pass it into `activity.log(...)`.
- For CONFIRMED/REFUNDED, `_dispatch_status_notification` runs inside `save()` and has no request context. Add a transient, non-persisted attribute the caller sets before `save()` (e.g. `registration._acting_user`) which `_dispatch_status_notification` reads and passes as `actor`, defaulting to the registrant `user`. This keeps the model fat and the view thin: views set `registration._acting_user = request.user` before the status-changing `save()`. The free-class confirm path (`classes/views.py:483-486`) sets it to the registrant; the webhook (`classes/webhook_handlers.py:62`) leaves it unset → actor stays `None` (system), which is correct for an automated Stripe event. Document `_acting_user` with a comment; default it to `None` in `__init__`-free fashion via `getattr(self, "_acting_user", None)`.
  - *Alternative considered:* a dedicated `confirm(actor=...)` / `mark_refunded(actor=...)` method. Cleaner in theory, but CONFIRMED is set inline in two places and REFUNDED only via Django admin; the transient-attribute approach threads the actor through the existing single `_dispatch_status_notification` choke point without rewriting those call sites' field assignments. If the implementer prefers explicit methods, that is acceptable provided every confirm/refund path routes through them and tests cover the actor — but the transient attribute is the lower-churn default.

### DECISION 6 — No DB migration unless an enum changes.
The core flow adds no fields. If, during implementation, you decide to also record `CmsActivity` kinds for the two escalations (optional, not required by spec), adding a `Kind` enum value needs a migration with a reverse. Default: **do not** add new `CmsActivity` kinds — the existing `CLASS_SUBMITTED` / `CLASS_APPROVED` rows already capture the audit trail; the escalations are surfaced via notifications + dashboard, not the activity feed. Keep it migration-free.

---

## File Structure

- Modify: `classes/models.py` — `submit_for_review()` (stage-1-only row creation), `on_review_decision_recorded()` (guild-lead-approved → create admin row + escalate), `Registration.cancel()` (+`actor`), `Registration._dispatch_status_notification()` (thread actor into CONFIRMED/REFUNDED).
- Modify: `classes/emails.py` — split into `send_guild_lead_review_request()` + `send_admin_validation_request()`; keep/adjust the instructor explainer.
- Add: `templates/classes/emails/admin_validation_request.{txt,html}` — stage-2 admin email (spec wording).
- Modify: `core/triggers.py` — add `class_review_requested`, `class_validation_requested`.
- Modify: `classes/views.py` — `teach_overview` (build the guild-lead queue when `is_guild_lead`); `admin_registration_cancel` (pass `actor=request.user`); `my_registration_cancel` (pass authenticated user as actor); free-class confirm in `register` (set `_acting_user`); update the submit call site if the email-sender signature changes.
- Modify: `templates/classes/teach/overview.html` — add the Guild-Lead "Needs Your Attention" panel (guarded by a context flag).
- Add tests:
  - `classes/spec/models/approval_sequencing_spec.py` (new) — the staged state machine.
  - `classes/spec/emails_review_spec.py` (extend) — stage-1 vs stage-2 senders + wording.
  - `classes/spec/views/teach_overview_spec.py` (extend) — guild-lead panel visibility/content.
  - `classes/spec/models/registration_spec.py` and/or `cms_activity_spec.py` (extend) — actor recorded on confirmed/refunded/cancelled.
  - `classes/spec/views/admin_registrations_spec.py` / `register_spec.py` (extend) — view threads `request.user` as actor.
- Modify: `plfog/version.py` — bump to 2.5.9 (verify) + changelog.

---

## Task 1: Sequential submit — Stage 1 creates only the first gate

**Files:** `classes/models.py`, `classes/spec/models/approval_sequencing_spec.py` (new)

- [ ] **Step 1 (failing test):** In `approval_sequencing_spec.py`, `describe_submit_for_review` →
  - `context_with_a_guild_lead`: build a `ClassOffering` whose category links a guild with a `guild_lead`; call `submit_for_review()`; assert status is `PENDING`, exactly **one** `ClassApproval` exists, and its `role == GUILD_LEAD` (no ADMIN row yet).
  - `context_without_a_guild_lead`: category guild has no lead (or no guild); assert exactly one approval row with `role == ADMIN`.
  Use the existing `classes` factories (mirror `classes/spec/models/class_approval_spec.py`). Mock no email here.
- [ ] **Step 2:** Run `pytest classes/spec/models/approval_sequencing_spec.py -k submit` — **confirm it FAILS** (today two rows are created when a lead exists).
- [ ] **Step 3 (implement):** In `submit_for_review()` (`classes/models.py:319-340`), replace the "one row per required role" creation (`:332`) with first-stage-only logic: compute `roles = self.required_review_roles`; if `GUILD_LEAD in roles`, create only the `GUILD_LEAD` row; else create the `ADMIN` row. Keep the DRAFT-guard, the `approvals.all().delete()` reset, the `CLASS_SUBMITTED` activity log, and the return value (return the list of rows actually created so the caller can email them). Add a private helper `_create_first_stage_approval() -> ClassApproval` if it reads cleaner.
- [ ] **Step 4:** Run the spec — **confirm PASS**.
- [ ] **Step 5:** `ruff format . && ruff check . --fix` then commit ("Sequential approval: submit creates only the first-stage gate").

---

## Task 2: Guild-Lead approval escalates to the Admin gate

**Files:** `classes/models.py`, `classes/spec/models/approval_sequencing_spec.py`

- [ ] **Step 1 (failing test):** `describe_on_review_decision_recorded` →
  - `context_guild_lead_approves`: submit (lead exists), then `gl_row.decide(APPROVED, user=lead_user)`. Assert: status is **still PENDING** (not published), an `ADMIN` `ClassApproval` row now exists and is undecided, and the guild-lead row is APPROVED. (Mock `classes.emails` to assert the stage-2 sender is called — see Task 4; here just assert the row/state.)
  - `context_admin_approves_after_lead`: continue the above, `admin_row.decide(APPROVED, user=admin_user)`. Assert status is `PUBLISHED`, `approved_by == admin_user`, `published_at` set.
  - `context_no_guild_lead_admin_approves`: no-lead path → submit makes the ADMIN row; admin approves → PUBLISHED directly. (Stage 1 == admin.)
  - `context_guild_lead_requests_changes`: `gl_row.decide(CHANGES_REQUESTED)` → status DRAFT, no ADMIN row created.
- [ ] **Step 2:** Run `pytest classes/spec/models/approval_sequencing_spec.py` — **confirm the escalation cases FAIL** (today a guild-lead approval does not create an admin row; nothing escalates).
- [ ] **Step 3 (implement):** In `on_review_decision_recorded()` (`classes/models.py:426-498`), inside the `APPROVED` branch, **after** the instructor notification and **before/around** the publish check: if `row.role == ClassApproval.Role.GUILD_LEAD` **and** `ADMIN in self.required_review_roles` **and** no ADMIN row exists yet → create the undecided ADMIN row and fire the stage-2 escalation (Task 3 notification + Task 4 email). Leave the existing publish check (`required.issubset(approved)`) exactly as-is — it now naturally fires only when the admin (created here) later approves. Guard against double-creating the admin row on re-entry.
- [ ] **Step 4:** Run the spec — **confirm PASS**.
- [ ] **Step 5:** Verify the existing approval/publish specs still pass: `pytest classes/spec/models/class_approval_spec.py classes/spec/views/admin_review_spec.py classes/spec/views/class_review_spec.py`. Fix any that asserted the old parallel behavior (update them to the sequential expectation; do not weaken coverage). Commit ("Sequential approval: guild-lead approval escalates to the admin gate").

---

## Task 3: Stage-1 and Stage-2 notifications

**Files:** `core/triggers.py`, `classes/models.py`, plus spec extensions

- [ ] **Step 1 (failing test):** In `approval_sequencing_spec.py` (or a `notifications`-focused describe), assert:
  - On submit with a guild lead: the guild lead's `User` receives a `class_review_requested` `Notification` row whose `body` contains the instructor name and the guild name (spec wording).
  - On guild-lead approval: staff users receive a `class_validation_requested` `Notification` whose `body` mentions the guild lead and instructor.
  Query `core.models.Notification` directly. Determine the staff audience the same way `dispatch` + `triggers.STAFF_ONLY` does (look at how other `STAFF_ONLY` triggers pick recipients, e.g. `new_member_joined`; recipients are the `User`s of admin/staff members — reuse whatever helper exists, or filter members by `is_fog_admin`/`is_guild_officer` and map to users).
- [ ] **Step 2:** Run — **confirm FAIL** (triggers/dispatch calls don't exist yet).
- [ ] **Step 3 (implement):**
  - Add the two `Trigger`s to `core/triggers.py:34-98` per Decision 3.
  - In `submit_for_review()` (or the view that calls it — keep notification dispatch in the model to honor fat-models, matching how `on_review_decision_recorded` already dispatches), when a guild-lead row is created, `notifications.dispatch("class_review_requested", [guild_lead_user], title="A class needs your review", body=f"{instructor_name} in the {guild_name} Guild requests approval for their upcoming class dates.", url="/classes/teach/")`. Resolve `guild_lead_user = self.category.guild.guild_lead.user` (guard for `None`).
  - In the guild-lead-approved escalation branch (Task 2), `notifications.dispatch("class_validation_requested", staff_users, title="A class needs executive validation", body=f"{guild_lead_name} and {instructor_name} request executive validation to publish this class.", url="/classes/admin/")`.
- [ ] **Step 4:** Run — **confirm PASS**. Also run `core` notification/trigger specs to ensure the catalogue change didn't break the settings UI tests.
- [ ] **Step 5:** Commit ("Approval notifications: guild-lead review request + admin validation escalation").

---

## Task 4: Staged reviewer emails

**Files:** `classes/emails.py`, `templates/classes/emails/admin_validation_request.{txt,html}` (new), `classes/spec/emails_review_spec.py`

- [ ] **Step 1 (failing test):** In `emails_review_spec.py`, mock the mail sender (the project mocks email — follow the existing pattern in that file / `emails_spec.py`; use the `core.email.send` seam). Assert:
  - `send_guild_lead_review_request(offering, gl_approval)` sends one email to `guild.guild_lead.primary_email` containing the tokenized `/classes/review/<token>/` URL and "Guild Lead" framing, **and** still emails the instructor the explainer.
  - `send_admin_validation_request(offering, admin_approval)` sends to `_admin_review_recipients()` with the stage-2 wording ("executive validation") and the admin row's token link.
- [ ] **Step 2:** Run — **confirm FAIL**.
- [ ] **Step 3 (implement):**
  - Split `send_class_review_requests` (`classes/emails.py:116-180`) into `send_guild_lead_review_request(offering, approval)` and `send_admin_validation_request(offering, approval)` per Decision 4. Keep `review_request.{txt,html}` for the guild-lead/admin-stage-1 email; add `admin_validation_request.{txt,html}` for stage 2 (copy `review_request.*` and reword the lead-in to the spec sentence). Keep the instructor explainer in the guild-lead/stage-1 sender only.
  - Wire the senders — **dispatch from the model so every submit path notifies** (Background flagged that `teach_class_create`/`teach_class_edit` skip emails today). In `submit_for_review()`, after creating the first-stage row, call the matching stage-1 sender (`send_guild_lead_review_request` when the row is GUILD_LEAD; the admin stage-1 email when it's ADMIN) via lazy `import classes.emails`. In the **guild-lead-approved escalation** branch of `on_review_decision_recorded`, call `send_admin_validation_request` (lazy import, matching existing model→emails lazy imports).
  - Then **simplify the views**: `teach_class_submit` (`classes/views.py:902-903`) drops its own `send_class_review_requests` call (the model now sends); `:848`/`:881` get the notification for free. Keep the message strings. Update any spec that asserted the view calls the email function to assert the model/sender behavior instead.
- [ ] **Step 4:** Run — **confirm PASS**.
- [ ] **Step 5:** Commit ("Staged reviewer emails: guild-lead request + admin validation request").

---

## Task 5: Guild-Lead "Needs Your Attention" dashboard panel

**Files:** `classes/views.py`, `templates/classes/teach/overview.html`, `classes/spec/views/teach_overview_spec.py`

- [ ] **Step 1 (failing test):** In `teach_overview_spec.py`:
  - `context_when_member_is_a_guild_lead`: member leads a guild; a class in that guild's category is PENDING with an **undecided GUILD_LEAD** approval. GET `/classes/teach/`; assert 200 and the response contains the class title under a guild-lead review panel, plus a link to the tokenized/admin review page (use `classes:class_review` token or a teach-accessible review entry — see Step 3).
  - `context_when_member_is_not_a_guild_lead`: ordinary teaching member; assert the panel is **absent**.
  - Edge: a PENDING class in a guild this member does **not** lead must not appear.
- [ ] **Step 2:** Run — **confirm FAIL**.
- [ ] **Step 3 (implement):**
  - In `teach_overview` (`classes/views.py:721-768`), when `teaching_member.is_guild_lead`, build `guild_lead_pending`: classes that are PENDING with an undecided `ClassApproval(role=GUILD_LEAD)` whose `category.guild` is in `teaching_member.led_guilds.all()`. Prefer a manager method on `ClassOfferingQuerySet` (e.g. `awaiting_guild_lead(member)`) to keep the view thin and reuse the queryset; add it next to `pending_review()` (`classes/models.py:113-114`). Pass `guild_lead_pending` and a boolean `is_guild_lead` into the context.
  - **Review link for guild leads:** they have no admin access, so the panel must link to the **tokenized** review page (`classes:class_review` with the GUILD_LEAD row's `token`) — fetch each pending class's undecided guild-lead approval and expose its token, or add a small teach-scoped review route. The tokenized page (`_class_review_view`) already records `request.user` when authenticated (`classes/views.py:1775`), so the guild lead's decision is attributed correctly. Confirm the tokenized page renders for a logged-in non-admin.
  - In `templates/classes/teach/overview.html`, add a panel (mirror admin overview `:7-24` and the existing "Needs you" panel) shown only when `is_guild_lead`, titled "Needs your attention", listing each pending class with a "Review" link to the token URL. Empty state when none.
- [ ] **Step 4:** Run — **confirm PASS**.
- [ ] **Step 5:** Commit ("Guild-lead review queue on the teaching dashboard").

---

## Task 6: Audit fix — thread the acting user into cancel / confirm / refund

**Files:** `classes/models.py`, `classes/views.py`, plus spec extensions

- [ ] **Step 1 (failing tests):**
  - `registration_spec.py` (or `cms_activity_spec.py`): `describe_cancel` → `it_records_the_acting_user`: call `registration.cancel(reason="x", actor=some_user)`; assert the latest `CmsActivity` row (`REGISTRATION_CANCELLED` or `WAITLIST_LEFT`) has `actor == some_user`. Also `it_defaults_actor_to_none` when omitted.
  - CONFIRMED: set `registration._acting_user = some_user`, flip status to CONFIRMED and `save()`; assert the `REGISTRATION_CONFIRMED` activity row has `actor == some_user`. And without setting it (e.g. webhook path), actor is `None`.
  - REFUNDED: same pattern for `REGISTRATION_REFUNDED`.
  - View-level: in `admin_registrations_spec.py`, POST `admin_registration_cancel` as an admin user; assert the cancellation activity row's `actor` is that admin. In `register_spec.py`, free-class registration's CONFIRMED row has `actor` == the registrant user (when the registrant is a linked member with a user).
- [ ] **Step 2:** Run — **confirm FAIL** (actor is dropped today on these three kinds).
- [ ] **Step 3 (implement):**
  - `Registration.cancel(self, reason: str = "", actor=None) -> None` (`classes/models.py:1094`): pass `actor=actor` into the `activity.log(...)` call (`:1103-1108`).
  - `_dispatch_status_notification` (`classes/models.py:1011-1061`): compute `acting = getattr(self, "_acting_user", None)` and pass `actor=acting` into the `REGISTRATION_CONFIRMED` (`:1039-1041`) and `REGISTRATION_REFUNDED` (`:1051-1053`) `activity.log` calls. (Leave CREATED/WAITLIST_JOINED as-is — they already log `actor=user`.) Add a short comment documenting the `_acting_user` transient.
  - Views:
    - `admin_registration_cancel` (`classes/views.py:2001-2006`): `registration.cancel(reason=request.POST.get("reason", ""), actor=request.user)`.
    - `my_registration_cancel` (`classes/views.py:629-643`): `registration.cancel(reason="self-serve", actor=request.user if request.user.is_authenticated else None)`.
    - Free-class confirm in `register` (`classes/views.py:483-486`): before the `save()`, set `registration._acting_user = request.user if request.user.is_authenticated else (registration.member.user if registration.member and registration.member.user else None)`.
    - Webhook (`classes/webhook_handlers.py:62`): leave `_acting_user` unset → actor stays `None` (correct: automated Stripe event). Add a one-line comment so a future reader doesn't "fix" it.
  - REFUNDED note: there is no view that sets REFUNDED today (Django-admin only). The model now records `_acting_user` if set; document that the admin-refund UI (when built) should set it. Do not invent a refund view in this plan.
- [ ] **Step 4:** Run — **confirm PASS**. Type-check: `mypy classes/` (export DATABASE_URL first per memory).
- [ ] **Step 5:** Commit ("Audit: record acting user on registration confirm/refund/cancel").

---

## Task 7: Lint / format / type-check / full suite

- [ ] **Step 1:** `ruff format . && ruff check .` — clean.
- [ ] **Step 2:** `export $(grep '^DATABASE_URL=' .env | xargs)` then `mypy .` — clean (the new `actor`/optional params and queryset method must be fully typed).
- [ ] **Step 3:** `pytest` — all pass, 100% coverage. Pay attention to coverage on the new branches: stage-1 with/without lead, escalation branch, no-lead direct-admin publish, guild-lead changes-requested, and the actor default vs. set paths.

---

## Task 8: Version bump + changelog

**Files:** `plfog/version.py`

- [ ] **Step 1:** Set `VERSION` to the next patch after the released version. At time of writing the latest is **2.5.8 (PR #108, OPEN)** — verify the merged version with `gh pr list` / `git log main` and use the next one (**2.5.9** expected; do not assume).
- [ ] **Step 2:** Prepend a member-friendly `CHANGELOG` entry (plain language — posts to Discord):
  ```python
  {
      "version": "2.5.9",  # verify
      "date": "2026-06-18",  # set to merge date
      "title": "Step-by-step class approvals and a clearer activity log",
      "changes": [
          "Submitting a class now goes to the right people in order: the category's Guild Lead is asked first, and only once they approve does it go to an admin for final sign-off before it publishes. Guild Leads now see classes waiting on them right on their Teaching dashboard.",
          "The site activity log now records exactly who confirmed, refunded, or cancelled a registration, instead of showing 'System' for staff actions.",
      ],
  }
  ```
- [ ] **Step 3:** Commit.

---

## Final verification

- [ ] `pytest` — all pass, 100% coverage.
- [ ] `ruff format . && ruff check . && mypy .` — clean.
- [ ] Manual sequencing pass (project `run` skill): create a class in a category with a Guild Lead → submit → Guild Lead sees it on `/classes/teach/` and gets the Stage-1 email; admin sees **nothing yet**. Guild Lead approves → admin now sees it on `/classes/admin/` + gets the Stage-2 "executive validation" email. Admin approves → class publishes. Repeat with a category that has no Guild Lead → goes straight to admin at submission.
- [ ] Manual audit pass: cancel a registration as an admin and confirm the Activity feed shows the admin's name (not "System"); confirm a free-class signup attributes the registrant.

---

## Follow-up (out of scope for this plan)

- **Admin refund UI:** there is no view that sets a registration to REFUNDED today (Django-admin only). When a refund action is built, it must set `registration._acting_user = request.user` before saving so the refund actor is captured. File a separate plan.
- **Guild-Officer escalation nuance:** the spec says Stage 2 is "Staff/Admin." This plan dispatches `class_validation_requested` to `STAFF_ONLY` (admins + guild officers via existing audience handling) and emails `CLASS_ADMIN_NOTIFY_EMAILS`. If product wants guild officers excluded from the validation step, tighten the audience — flagged, not done.
- **Decline at Stage 2 returns to DRAFT, re-running Stage 1.** If product wants an admin decline to skip the guild-lead re-review on resubmit, that is a separate state-machine decision; not handled here.
