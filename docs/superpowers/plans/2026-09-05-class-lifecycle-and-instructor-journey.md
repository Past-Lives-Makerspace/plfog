# Class Lifecycle & Instructor Journey — Audit, IA, and Spec & Implementation Plan

**Status:** Spec only — not yet approved to build. Phased as **four sequential PRs** (§9).
**Date:** 2026-09-05
**Surface:** FOG hub — hub sidebar + home page, instructor portal (`/classes/teach/*`), class CMS admin (`/classes/admin/*`), the tokenized guild-lead review page, class emails, one scheduled command
**Related:**
- `2026-06-08-classes-management-redesign-roadmap.md` (+ phases 1, 2, 3, 3b) — shipped. Gave both portals their Overview / Classes nav and the per-class tabbed Workspace. This audit starts from that end state and does not re-house anything.
- `2026-08-10-instructor-orientation-unlock.md` — shipped. The orientation gate and `Member.can_create_classes` are reused as-is.
- `2026-08-26-roster-waitlist-management.md`, `2026-08-27-roster-actions-menu.md` — shipped roster tooling; untouched.
- `2026-06-21-instructor-welcome-email.md`, `2026-07-21-class-sale.md` — shipped; untouched.

---

## 1. Summary

Classes already have a solid engine (sequential guild-lead → admin approval, tokenized review, roster tools, reminders, Stripe). What is missing is **honesty about state and a complete set of doors**: a member cannot find "how do I teach" from the hub, a submitted class looks identical whether it is waiting on a guild lead or an admin, a bounced class looks like a fresh draft, a live class cannot be touched (not even a typo) or cancelled by its instructor, and an admin who "archives" a live class unknowingly emails every registrant a cancellation. This spec adds one real status (**Cancelled**), one derived **lifecycle** shown everywhere, the missing instructor actions (withdraw, light edits, request a change, cancel, run it again), a hub entry point and home card, an honest two-stage admin queue with a publish confirmation, submission readiness checks, a day-before roster email for instructors, a waitlist that actually holds an opened seat for the next person (Claim or Pass, 72 hours by default, adjustable) before moving on, and a **review pipeline diagram** (Submitted → Guild lead → Admin → Live) that shows instructors and reviewers exactly which step a class is on, in the portal and in every review email.

### Locked decisions

| # | Decision | Choice and why |
|---|---|---|
| 1 | Status vs lifecycle | **One new stored status, `CANCELLED`.** Everything else the brief lists as "missing states" (changes requested, review stage, completed) is **derived** from data that already exists (approval rows, sessions) and exposed as `ClassOffering.lifecycle`, a single property + queryset annotations both portals render through one badge component. No status explosion, no migration of history. |
| 2 | Cancel ≠ Archive | `cancel(actor, reason)` is the member-facing event (notifies registrants and members, reason required). `archive()` becomes **quiet housekeeping** (no notices) and is refused for an upcoming class with live registrations. Today `archive()` emits `class_cancelled` (`classes/models.py:887`), so "archive" already IS cancel without saying so. |
| 3 | Restore | `restore()` takes ARCHIVED → DRAFT (approvals cleared, must be re-submitted). The archive confirm modal already promises "you can re-open it later" but no route exists (`classes/urls.py`, verified). |
| 4 | Instructor cancel | Instructors may cancel their own published class with a reason. Refunds stay an admin action through the existing per-registration refund panel; when paid registrations exist, admins get an in-app + email notice naming the count. Money never moves on an instructor click. |
| 5 | Post-publish edits | Instructors get **light edits** on a live class (description, prerequisites, materials, safety, guardian note, flexible note, video, FAQ, gallery, welcome email) without re-review. **Structural** fields (title, guild type, price, sale, capacity, dates, scheduling model) stay admin-only after publish, with a one-field **Request a change** modal that notifies admins. Registrants booked on facts; facts change through a human. |
| 6 | Withdraw | An instructor may take back a PENDING submission (→ DRAFT). Reviewers' open links already render "not awaiting review" for non-PENDING classes (`_class_review_view:2658`). |
| 7 | Readiness | `submit_for_review` checks five things (hero, gallery, description, dates or flexible note, capacity) and the edit page shows the same checklist before the button. Today only images are checked (`:817`), so an undated class can be published into the catalog. |
| 8 | Publish is confirmed | Approve now opens a confirm modal naming the blast radius (catalog, every member's bell, Discord). Publishing is the most irreversible action in the CMS and today it is a bare one-click button on the overview row (`admin/overview.html:13`). |
| 9 | Queue stage | The admin overview splits "Waiting on You" from "With Guild Leads" (with a **Remind lead** action). `pending_review()` is every PENDING class (`classes/models.py:225`), so today an admin's queue shows classes the guild lead has not seen yet, with a one-click Approve that closes the lead's gate. |
| 10 | Hub entry | A **Teach** sidebar entry for every active member (label and destination flip on `can_create_classes`: "Teach a Class" → orientation; "Teaching" → portal) and a conditional **Teaching** home card. No hidden dead ends: locked members land on the explainer, never a 403. |
| 11 | Public instructor page | `instructor_slug` is **minted automatically when an instructor's first class publishes**, only while that member still holds `can_create_classes` (a revoked instructor never gets a public page re-minted behind an admin's back). Today only the admin Permissions tab's Instructor toggle mints it (`Member.grant_instructor`, `membership/models.py:1244`, behind `hub/views.py:6310`; the older `apply_admin_role` loop still exists but the Role dropdown no longer offers Instructor, `hub/forms.py:767`), and the instructor Profile page exists but no nav links to it (`teach_profile`, zero template references). One `ensure_instructor_slug()` helper replaces both copies of the loop. The teaching unlock is not touched. |
| 12 | Roster email | Instructors get a day-before email per session with the roster, from the existing `send_class_reminders` command (no new scheduled job, so the cron parity spec stays untouched). |
| 13 | Scope guard | No wizard, no re-housing, no attendance/check-in, no payouts, no analytics (§11). The public catalog keeps its look. |
| 15 | Review pipeline diagram | One `ClassOffering.review_pipeline()` model method feeds one page component and one email-safe component. Steps: Submitted, Guild lead (only when `required_review_roles` includes it), Admin, Live. States per step: done (green check), current (gold ring, the step being waited on), ahead (empty), changes requested (blue mark with the reviewer's note). The connector fills up to the current step. Shown on the instructor edit page and workspace, the admin class detail and review pages, and inside every review email (HTML as an inline-styled table, text as a bracketed line). Ships in PR 1 with the lifecycle states. |
| 14 | Waitlist claim window | When a seat opens, the first eligible waitlister gets it **held**: `Registration.offer_expires_at` is stamped, the held seat counts against `spots_remaining` so the public cannot take it, and the offer email carries **Claim my spot** and **Pass** links onto the token self-serve page. Claim = the existing staff promote path run by the registrant (CONFIRMED, `payment_due_cents` stamped, pay link for paid classes). Pass, or silence past the deadline, removes them from the waitlist and offers the seat to the next person automatically. The window is `ClassSettings.waitlist_claim_window_hours` (exists, default 24, not editable today, never enforced): default becomes **72**, exposed on the Waivers & Reminders settings page. Expiry runs on a new 15 minute job. Today the claim email sends people back to the public register form, notified people are skipped forever, and nothing holds the seat. |

---

## 2. What already exists (reuse, don't reinvent)

All locations re-verified in the current tree, 2026-09-05 (`VERSION` 1.34.2).

| Need | Existing thing | Location |
|---|---|---|
| Statuses and the approval state machine | `ClassOffering.Status` (DRAFT / PENDING / PUBLISHED / ARCHIVED), `submit_for_review:800`, `on_review_decision_recorded:990`, `archive:887`, `required_review_roles:782`, `approve:867` | `classes/models.py` |
| Approval rows with decisions + tokens | `ClassApproval` (`Role`, `Decision` APPROVED / CHANGES_REQUESTED / DENIED, `decide:1557`) | `classes/models.py:1477` |
| Querysets | `pending_review:225`, `awaiting_guild_lead(member):228`, `awaiting_admin_validation:242`, `for_instructor:258`, `hosted_by:261`, `editable_by:271`, `public:138`, `upcoming:201` | `classes/models.py` |
| Sessions | `ClassSession` (`starts_at` / `ends_at`) | `classes/models.py:1741` |
| Teaching gate + portal views (21) | `teaching_member_required:984`, `teach_overview:1167`, `teach_dashboard:1278`, `teach_class_create:1345`, `teach_class_edit:1383`, `teach_class_submit:1438`, `teach_class_detail:1635`, `teach_profile`, workspace tabs | `classes/views.py` |
| Admin views | `admin_overview:2006`, `admin_classes:2107`, `admin_class_detail:2381`, `admin_class_approve:2505`, `_class_review_view:2658`, `admin_class_archive:2726`, duplicate / duplicate_run / delete | `classes/views.py` |
| Access decorators | `classes_admin_access_required:1031` (full admin), `classes_review_access_required` (admin or `CLASS_APPROVER` capability) | `classes/views.py`, `hub/view_as.py` |
| Instructor form + published-field split source | `TeachClassOfferingForm` (26 fields) | `classes/forms.py:370` |
| Portal templates | `teach/base.html` (nav), `teach/overview.html`, `teach/classes_list.html`, `teach/class_form.html`, `teach/class_detail_base.html`, `teach/class_overview.html`, `teach/profile.html` | `templates/classes/teach/` |
| Admin templates | `admin/base.html`, `admin/overview.html`, `admin/classes_list.html`, `admin/class_detail_base.html`, `admin/class_detail.html`, `admin/class_review.html`, `admin/settings_hub.html` | `templates/classes/admin/` |
| Review decision + explainer emails | `send_class_review_decision:459`, `_emit_instructor_review_explainer:338`, `send_guild_lead_review_request`, `send_admin_review_request`, `send_admin_validation_request` | `classes/emails.py` |
| Registrant reminders (the command the roster email rides) | `send_class_reminders` job (`Cadence.ALWAYS`); `classes/tasks.py` fires at `ClassSettings.reminder_hours_before or 24` (`:31`) and `class_reminder_occurrences` (`:36-46`) yields only sessions paired with CONFIRMED registrations; `build_class_reminder_occurrence`, `send_reminder_email` | `core/scheduled_jobs.py:100`, `classes/tasks.py`, `classes/emails.py` |
| Admin direct publish (no approval rows) | `admin_class_create` sets `status = PUBLISHED` outright | `classes/views.py:2237` |
| Refund authority (the audience for "refunds needed") | `refund_authority_required` (REFUNDS capability) | `hub/view_as.py`, `classes/views.py:3114` |
| Event registry rows | `class_published` (ALL_ACTIVE_MEMBERS + Discord), `class_cancelled` (ALL_ACTIVE_MEMBERS in-app, `email_to` registrants), `instructor_class_approved`, `instructor_changes_requested`, `instructor_new_registration`, `class_review_requested`, `class_validation_requested`; resolvers `INSTRUCTOR`, `CLASS_APPROVERS` | `core/events/registry.py:284-303` |
| Activity log | `CmsActivity.Kind` (`classes/models.py:2758`), `classes.activity.log`, mirror to `SiteActivity` | `classes/` |
| Duplicate / run again | `duplicate_as_new_run:1447`, `teach_class_duplicate_run`, `admin_class_duplicate_run` | `classes/models.py`, `classes/views.py` |
| Compose email to a class | `hub_compose?audience=class:<pk>` | `hub/` |
| Instructor slug minting loop (two copies today) | `Member.grant_instructor` (the live path, Permissions tab toggle) and the older `apply_admin_role` branch | `membership/models.py:1244`, `:1358` |
| Teaching unlock | `Member.can_create_classes:1172`, `instructor_oriented_at:568` | `membership/models.py` |
| Hub sidebar + home cards | `templates/hub/base.html:108` / `:229` (Class Catalog, both nav variants), `hub/home.py::build_home_context:61`, `templates/hub/home.html` | hub |
| Help | Instructor Quickstart guide (`/help/teaching/instructor-quickstart/`), help keys `teach.*` | `core/help_registry.py` |
| Existing entry points | book catalog hero "Manage My Classes" (`classes/public/list.html:19`), guild page "Teach a Class" (`hub/guild_detail.html:311`), book-account instructor banner, Admin Tools "Instructor Quickstart" card | templates |
| UI components | `confirm_modal.html`, `modal.html`, `form_field.html`, `toggle.html`, `table_pagination.html`, `.pl-help`, `trigger_toast` | `templates/components/` |
| Waitlist auto-notify (one person per opened seat, skips anyone already notified, holds nothing) | `ClassOffering.promote_next_from_waitlist` (callers: `Registration.cancel:2411`, `mark_refunded:2437`, `move_to:2542`) | `classes/models.py:947` |
| Seat math (CONFIRMED + PENDING only) | `ClassOffering.spots_remaining` | `classes/models.py:1119` |
| Staff promote (CONFIRMED, `payment_due_cents` stamped, row lock) | `Registration.promote_from_waitlist(actor)`, `registration_promote` view + follow-up modal | `classes/models.py:2286`, `classes/views.py:3238` |
| Claim email + stale-claim guard (sends people to the public register form) | `send_waitlist_spot_opened` (`?waitlist_token=` link), `_stale_claim_link_redirect` | `classes/emails.py:581`, `classes/views.py:562` |
| Promoted emails (plain "You're in" / pay link) | `send_waitlist_promoted`, `send_payment_link_email` | `classes/emails.py:643` |
| The window setting (default 24, absent from the form, unenforced) | `ClassSettings.waitlist_claim_window_hours`; `ClassSettingsForm` fields | `classes/models.py:2846`, `classes/forms.py:974` |
| Token self-serve page + pay page | `my_registration`, `my_registration_pay` | `classes/views.py:808`, `:856` |
| Waitlist tab row + menu | `templates/classes/partials/waitlist_row.html`, `waitlist_row_menu.html` | templates |
| Scheduled job registry + the cron parity spec that must list any new job | `SCHEDULED_JOBS`, `_DISPATCHER_ALWAYS` | `core/scheduled_jobs.py`, `core/spec/scheduled_jobs_spec.py` |

**Genuinely net-new:** the `CANCELLED` status + three columns, `lifecycle` + six queryset methods, `readiness()`, `cancel` / `withdraw_submission` / `restore` / `request_change`, `publish(actor)`, `ensure_instructor_slug`, the published-class edit form, one badge component, one facet helper, three events + one email template, `instructor_roster_occurrences()`, `send_guild_lead_review_reminder`, the `refund_authority` resolver, the registrant self-serve cancelled state, the sidebar entry, the home card, the Remind lead action, and the reviewer readiness block. Everything else is re-labeling and re-wiring what exists.

---

## 3. Journey maps — today vs target

### 3.1 Prospective instructor (member)

| # | Step | Today (verified) | Friction | Target |
|---|---|---|---|---|
| 1 | Discover "how do I teach?" | No hub sidebar or home entry. Entry points: catalog hero on the book site, a "Teach a Class" button on guild pages, the book-account banner (only once you already teach), the Admin Tools quickstart card. | **F1** A member on the hub home has no path. | **Teach** in the sidebar for every active member; Help quickstart linked from the orientation page. |
| 2 | Onboard | Orientation page → acknowledge → Unlock (shipped, solid). | — | Unchanged. Sidebar label flips to **Teaching** after unlock. |
| 3 | Set up my public page | Only an admin can mint `instructor_slug` (the Permissions tab's Instructor toggle, `grant_instructor`). Profile page exists at `/classes/teach/profile/` but nothing links to it. | **F2** Dead route; hidden dependency on an admin. | **Profile** tab in the portal; slug minted on first publish; profile page states when the public page goes live. |
| 4 | Draft a class | One long form (26 fields + sessions calendar + gallery + FAQ + discount codes). Save Draft / Save & Submit / Cancel. | **F3** Submission requirements (hero + gallery) surface only as an error at submit; success message says "admin review" even when the guild lead is first. | Readiness checklist above the buttons; honest submit message naming the first reviewer. |
| 5 | Submit and wait | Overview "Needs your attention": every PENDING class reads **Awaiting admin review**; Classes list shows raw status. | **F4** Stage invisible. | Badge reads **With guild lead (Woodshop)** or **Awaiting admin**. |
| 6 | Get feedback | Changes requested / declined → status flips to DRAFT; notes arrive by email only. | **F5** In the portal the bounced class looks like a fresh draft; the reviewer's notes are nowhere on the page. | **Changes requested** badge + notes banner on the overview row and edit page, "Fix and resubmit" CTA. |
| 7 | Change my mind | No withdraw. Editing while PENDING is allowed silently. | **F7** | **Withdraw submission** on the workspace overview; the pending banner names the stage. |
| 8 | Class goes live | Email "Your class is live!" + bell. | — | Unchanged, plus public page now exists (step 3). |
| 9 | Fix a typo / add a photo / adjust materials | `teach_class_edit` redirects with "Published and archived classes can only be edited by an admin." | **F6** Full lockout for content that does not affect registrants. | Light edits allowed; structural fields read-only with **Request a change**. |
| 10 | Plans fall through | No instructor cancel. | **F7** | **Cancel class** with a reason; registrants and admins told. |
| 11 | Run the class again | "+ Offer on another set of dates" lives only on the edit form, which published classes cannot open. | **F6b** Unreachable for exactly the classes worth re-running. | **Run it again** on the workspace overview for published, completed, and cancelled classes. |
| 12 | Day of | Registrants get a reminder; the instructor gets nothing. Roster tabs exist. | **F12** | Day-before roster email with names, what they said, and links. |
| 13 | Afterwards | Class stays "Published" forever; no completed view. | **F11** | **Completed** facet in Classes; badge reads Completed. |

### 3.2 Admin / staff

| # | Step | Today (verified) | Friction | Target |
|---|---|---|---|---|
| 1 | See what needs me | Overview "Needs your attention" = every PENDING class (`pending_review()`), including ones the guild lead has not decided; row actions **Review** + one-click **Approve**. | **F9** Stage mixed; Approve publishes and auto-closes the lead's open gate with no confirmation. | Two lists: **Waiting on You** and **With Guild Leads** (lead name, days waiting, **Remind lead**). Approve opens a confirm modal. |
| 2 | Review | Review page: decision radios, notes, live student preview iframe, progress, details, history. Tokenized twin for leads. | **F10** A dated class with zero sessions can be approved (only images are checked at submit). | Readiness block on the review page; the submit guard makes an undated dated-class unsubmittable. |
| 3 | Approve / publish | Publishes, notifies all members (bell) and Discord. | **F9** No confirm. | Confirm modal names the blast radius. |
| 4 | Monitor | Overview stats by range, registrations, waitlists, activity. Classes list: status pills, instructor filter, My Classes. | **F11** No upcoming / completed split; a two-year-old class is "Published". | Lifecycle facets with counts; badge everywhere. |
| 5 | Change a live class | Admin edit (any field). | — | Unchanged. Change requests from instructors arrive as notices with a deep link. |
| 6 | Cancel a live class | Only **Archive**: confirm copy says "hidden… registrations preserved… re-open later via the Archived filter"; the model emits `class_cancelled` (bell to all members, email to registrants). No restore route. | **F8** Archive is a cancel in disguise; the promised re-open does not exist. | **Cancel class** (reason modal, notices) for live classes; **Archive** quiet and guarded; **Restore** to draft. |
| 7 | Delete | Allowed with zero registrations (confirm). | — | Unchanged. |
| 8 | CMS Administrator (capability, not full admin) | Sees Registrations tab + class detail with Approve / Review with notes. | **F14** One-click Approve applies here too. | Same confirm modal. |

### 3.3 Waitlisted registrant (PR 4)

| # | Step | Today (verified) | Friction | Target |
|---|---|---|---|---|
| 1 | Join the waitlist | Register form creates a WAITLISTED row; "Added to the waitlist" email. | — | Unchanged. |
| 2 | A seat opens | The oldest un-notified waitlister gets "you have 24 hours" and a link to the **public register form**; the seat is not held, so anyone browsing the catalog can take it first. Notified people are never offered again. | **F15** The promise in the email is not kept by the system. | The seat is held for that person; the email says until when and carries Claim and Pass links to their own page. |
| 3 | Decide | Re-register through the form, or ignore it. | No pass; ignoring leaves the seat open to the public and the next waitlister uninformed. | **Claim my spot** (one click; paid classes get a payment link right after) or **Pass** (seat goes to the next person). |
| 4 | Miss the window | Nothing happens. | Seat sits open; the waitlist stalls. | Offer expires, they get a courtesy email with a rejoin link, and the next person is offered automatically. |

---

## 4. Findings (numbered, with evidence)

Severity: **H** breaks trust or causes an unintended member-facing event; **M** makes a common task hard or misleading; **L** polish.

| # | Sev | Finding | Evidence |
|---|---|---|---|
| F1 | H | No hub entry to teaching. | `templates/hub/base.html:108,229` (Class Catalog only); grep for `teach_overview` outside `templates/classes/` hits only `guild_detail.html:311` and the book catalog hero. |
| F2 | L | Instructor Profile route unlinked; public page needs an admin. | `teach_profile` in `classes/urls.py:77`, zero template references; only `grant_instructor` (and the older `apply_admin_role` copy) mint the slug. |
| F3 | M | Submit requirements found at error time; wrong reviewer named. | `submit_for_review` checks only `has_submittable_image` (`classes/models.py:817`); `teach_class_create:1368` and `teach_class_edit:1409` say "for admin review" unconditionally while `teach_class_submit:1450` names the real first gate. |
| F4 | M | Review stage invisible to instructors. | `teach/overview.html` pill "Awaiting admin review" for every PENDING; `teach/classes_list.html` shows `get_status_display`. |
| F5 | H | Changes requested / declined is indistinguishable from a fresh draft in the portal; notes only in email. | `on_review_decision_recorded` sets DRAFT (`:1071,1082`); nothing reads `ClassApproval.notes` in `teach/*` templates. |
| F6 | M | Published classes fully locked for instructors; run-again unreachable. | `teach_class_edit:1390-1392` redirect; duplicate-run form only in `teach/class_form.html:76-82`. |
| F7 | M | No withdraw, no instructor cancel. | `classes/urls.py:17-77` has neither route. |
| F8 | H | Archive emits cancellation notices and promises a re-open that does not exist. | `archive()` emits `class_cancelled` with `email_to=self.registrant_notice_emails` (`classes/models.py:900-913`); `admin/class_detail.html` archive modal copy; no unarchive route. |
| F9 | H | Admin queue mixes stages; one-click publish without confirm; admin approval silently closes the lead's open gate. | `pending_review()` (`:225`), `admin/overview.html:13-16`, `on_review_decision_recorded:1023-1030`. |
| F10 | H | Undated classes can be submitted and published. | `submit_for_review` guard; review page renders "No upcoming sessions scheduled yet." as an aside (`admin/class_review.html:117`). |
| F11 | M | No completed / past facet; published forever. | `admin_classes` status filter = raw `Status` values (`:2109`); instructor list has no filter. |
| F12 | M | Instructors get no day-of roster. | `send_class_reminders` targets registrants only (`classes/emails.py::send_reminder_email`). |
| F13 | L | Editing while PENDING is silent. | `teach/class_form.html:5-9` banner ("pending admin review") is stage-blind. |
| F14 | M | CMS Administrator gets the same unconfirmed Approve. | `admin/class_detail.html` action row. |
| F15 | H | The waitlist claim window is advertised but not enforced, and the opened seat is not held. | `promote_next_from_waitlist` stamps `waitlist_notified_at` only (`classes/models.py:947`); `spots_remaining` ignores notified rows (`:1119`); `waitlist_claim_window_hours` is not in `ClassSettingsForm` (`classes/forms.py:974`) and no code reads it except the email copy (`classes/emails.py:606`); the claim link is the public register form (`:597`). |

---

## 5. State model

### 5.1 Stored status (one addition)

`ClassOffering.Status`: `DRAFT`, `PENDING`, `PUBLISHED`, **`CANCELLED = "cancelled", "Cancelled"`** (new), `ARCHIVED`.

New columns on `ClassOffering` (one migration, auto-reversible): `cancelled_at` (`DateTimeField(null=True, blank=True)`), `cancelled_by` (`FK(Member, null=True, blank=True, on_delete=SET_NULL, related_name="+")`), `cancellation_reason` (`CharField(300, blank=True)`). Every field gets `help_text`.

`public()` / `bookable()` already filter `status="published"`, so CANCELLED classes drop out of the catalog, calendar, and Discord automatically. `upcoming()` (staff pickers) keeps them out via its callers' status filters; the move-student picker excludes CANCELLED explicitly.

### 5.2 Derived lifecycle (one property, one badge)

```python
class Lifecycle(models.TextChoices):
    DRAFT = "draft", "Draft"
    CHANGES_REQUESTED = "changes_requested", "Changes requested"
    AWAITING_GUILD_LEAD = "awaiting_guild_lead", "With guild lead"
    AWAITING_ADMIN = "awaiting_admin", "Awaiting admin"
    UPCOMING = "upcoming", "Upcoming"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    ARCHIVED = "archived", "Archived"

@property
def lifecycle(self) -> Lifecycle: ...
```

Resolution order: ARCHIVED → CANCELLED → PENDING with an undecided `GUILD_LEAD` row → AWAITING_GUILD_LEAD; other PENDING (an open `ADMIN` row, or no rows at all) → AWAITING_ADMIN; DRAFT with **a row whose decision is `CHANGES_REQUESTED` or `DENIED`** → CHANGES_REQUESTED (an APPROVED guild-lead row left behind by an admin bounce never counts, withdraw deletes every row, and `submit_for_review` deletes every row on resubmit); other DRAFT → DRAFT; PUBLISHED, dated model, last session ended → COMPLETED; other PUBLISHED → UPCOMING. Two consequences stated plainly: a **flexible** published class never reaches COMPLETED (it stays Upcoming until cancelled or archived), and a dated PUBLISHED class with **zero** sessions (legacy data only, once the readiness guard lands) reads Upcoming with the note "No dates yet" while `bookable()` keeps it out of the catalog. `lifecycle_note` returns the stage detail: the guild name, the deciding role + `notes` excerpt from the **latest** bouncing row by `decided_at`, the completion date, or the no-dates note.

Queryset `with_lifecycle_inputs()` annotates `last_session_at`, `open_guild_gate` (Exists undecided GUILD_LEAD row), `bounced` (Exists row with decision in CHANGES_REQUESTED / DENIED) so lists resolve the badge with no per-row queries. Manager methods (fat models): `awaiting_admin()` = PENDING and no open GUILD_LEAD row (so a PENDING class with zero rows still lands in a queue), `awaiting_guild_lead_any()`, `changes_requested()`, `upcoming_published()`, `completed()`, `cancelled()`.

**Badge component** `templates/classes/_components/lifecycle_badge.html` (`{% include ... with offering=c %}`): `pl-lifecycle-badge pl-lifecycle-badge--<value>` on theme tokens (muted for draft/archived, gold for review stages, blue for changes requested, green for published, muted-green for completed, red for cancelled), `title`-free (a `.pl-help` bubble carries the note where space allows). Replaces every raw `get_status_display` in `teach/classes_list.html`, `teach/class_detail_base.html`, `admin/classes_list.html`, `admin/class_detail_base.html`, and the overview rows.

### 5.3 Transitions

| Transition | Who | Method | Guards | Side effects |
|---|---|---|---|---|
| DRAFT → PENDING | instructor, admin | `submit_for_review` | `is_ready` (§5.4) | as today + honest message |
| PENDING → DRAFT (withdraw) | instructor | `withdraw_submission()` *(new)* | status PENDING | delete **all** approval rows (an APPROVED guild-lead row must not survive to masquerade as a bounce); `CmsActivity.CLASS_WITHDRAWN` |
| PENDING → DRAFT (bounce) | reviewer | `decide(CHANGES_REQUESTED / DENIED)` | as today | as today; now **visible** as CHANGES_REQUESTED |
| PENDING → PUBLISHED | admin | `approve` / `decide(APPROVED)` → `publish(actor)` *(new, model)* | as today + `is_ready` (raises `ValidationError` naming the failing items) | `publish(actor)` is the single place that sets status / `approved_by` / `published_at`, logs `CLASS_PUBLISHED`, and emits `class_published` (PR 1); PR 2 adds the one call that mints the slug when `instructor.can_create_classes` |
| DRAFT → PUBLISHED (admin direct) | admin | `admin_class_create` → `publish(actor)` (the only direct path: `admin_class_edit` never sets a status and `ClassOfferingForm` has no `status` field, `classes/views.py:2321`, `classes/forms.py:314`) | `is_ready`; a failing check surfaces as a form error on the admin create form | same as above (today `admin_class_create` sets PUBLISHED with no rows, `classes/views.py:2237`, so this path skipped every guard). **Ordering in the view:** save as DRAFT, save sessions and gallery, then call `publish(actor)`; on `ValidationError` roll the half-created class back the way the gallery path already does (`classes/views.py:2242`), because `is_ready` needs images and sessions that do not exist at `form.save()` time. |
| PUBLISHED → CANCELLED | instructor (own), admin | `cancel(actor, reason)` *(new)* | status PUBLISHED; reason non-blank (`ValueError`) | set the three fields; `CLASS_CANCELLED` activity; emit `class_cancelled` (existing event, context gains `reason` and copy shows it); when `actor` is the instructor and paid registrations exist → emit `class_cancelled_admin_notice` (new, §8) |
| DRAFT / PENDING / PUBLISHED (completed, or upcoming with no active registrations) / CANCELLED → ARCHIVED | admin | `archive()` | **refuses** PUBLISHED with a future session and active registrations: "Cancel this class instead. It has upcoming dates and N active registrations." | `CLASS_ARCHIVED` activity only. **No `class_cancelled` emit** (moved to `cancel`). Classes archived before this change already sent their notices; nothing re-fires. |
| ARCHIVED → DRAFT | admin | `restore()` *(new)* | status ARCHIVED | delete approval rows; `CLASS_RESTORED` activity |
| PUBLISHED → (content edit) | instructor | `TeachPublishedClassForm` save | light fields only | `CLASS_UPDATED`? no: existing edit path logs nothing; keep quiet |
| PUBLISHED → change request | instructor | `request_change(instructor, note)` *(new)* | status PUBLISHED; note non-blank | `CLASS_CHANGE_REQUESTED` activity; emit `class_change_requested` (new, §8) |
| any → new DRAFT copy | instructor, admin | `duplicate_as_new_run` / `duplicate` (exist) | — | as today, **plus** both clear `cancelled_at` / `cancelled_by` / `cancellation_reason` (they clone by `pk = None` and only reset status / `published_at` / `approved_by` / `legacy_cms_id`, `classes/models.py:1437-1467`, so the new columns would ride along) |

`CmsActivity.Kind` additions: `CLASS_CANCELLED`, `CLASS_WITHDRAWN`, `CLASS_RESTORED`, `CLASS_CHANGE_REQUESTED`.

### 5.4 Readiness (`ClassOffering.readiness()` + `is_ready`)

Returns an ordered list of `ReadinessItem(ok: bool, label: str, hint: str, anchor: str)`:

1. Hero photo (`has_submittable_image` half) — "Add a hero photo."
2. At least one gallery photo — "Add one gallery photo."
3. A real description (≥ 40 characters) — "Write a short description."
4. Dates: dated model → at least one **future** session; flexible model → `flexible_note` set — "Add at least one date." / "Say how students pick a time."
5. Capacity ≥ 1 — "Set how many can attend."

`submit_for_review` raises `ValidationError` listing every failing label ("Not ready to submit: Add at least one date. Write a short description.") — the existing images message folds into this. `teach_class_submit` / create / edit already surface `exc.messages[0]` as a Django error message.

### 5.4a Review pipeline (PR 1)

`ClassOffering.review_pipeline() -> ReviewPipeline` (a small dataclass: `steps`, `headline`, `note`, `is_live`, `is_bounced`), computed from `status`, `required_review_roles`, and this cycle's `ClassApproval` rows (`submit_for_review` deletes rows on resubmit, so the rows always describe the current cycle):

| Step | Present when | done | current | changes_requested |
|---|---|---|---|---|
| Submitted | always | status is PENDING or PUBLISHED, or a decided bouncing row exists | status DRAFT with no rows (the step the instructor is on) | never |
| Guild lead | `GUILD_LEAD` in `required_review_roles` | its row is APPROVED | its row is open (undecided) and the class is PENDING | its row is CHANGES_REQUESTED or DENIED (class back in DRAFT) |
| Admin | always | its row is APPROVED (or the class is PUBLISHED) | its row is open, or PENDING with no admin row yet and no open guild row | its row is CHANGES_REQUESTED or DENIED |
| Live | always | status PUBLISHED | never | never |

`headline`: "Waiting on the guild lead ({guild name})", "Waiting on an admin", "Changes requested by the guild lead" / "…by an admin", "Live since {date}", "Not submitted yet". `note`: the bouncing row's notes when bounced, else empty. Cancelled and archived classes render the last known strip with a muted "Cancelled" / "Archived" headline (the component never errors on any status). A step's `done` timestamp and decider name ride along for tooltips ("Approved by Sam, Sep 3").

**Components.** `templates/classes/_components/review_pipeline.html` (page): a `pl-pipeline` flex strip of circles and labels with the fill line, stacking vertically under 480px, `--color-success` / `--color-tuscan-yellow` / `--hub-blue` / `--hub-text-muted` tokens only, an `aria-label` sentence equal to the headline. `templates/classes/emails/_review_pipeline.html` (email): an inline-styled single-row table, one cell per step with a colored circle character (✓ done, ○ ahead, ● current, ↩ changes requested) and the label beneath, headline line above it, no SVG, no external CSS, cream on dark per the shell rules. `templates/classes/emails/_review_pipeline.txt`: one line, e.g. `[✓] Submitted  [✓] Guild lead  [ ] Admin  [ ] Live` followed by the headline. Both email variants are rendered through one template tag (`{% review_pipeline offering %}` / `{% review_pipeline_text offering %}`) so the page and email can never disagree.

### 5.5 Waitlist offers (PR 4)

**Data.** `Registration.offer_expires_at` (`DateTimeField(null=True, blank=True, help_text="While set on a WAITLISTED row, this person holds an offered seat until this moment.")`). A **held offer** = status WAITLISTED with `offer_expires_at` set; it stays held until the expiry job cancels it, so the seat never reopens to the public in the gap between the deadline and the next 15 minute tick (accepted: a walk-in cannot jump the waitlist, and the extra hold is at most 15 minutes). A **claimable offer** additionally has `offer_expires_at > now` on a PUBLISHED class that has not started. `ClassSettings.waitlist_claim_window_hours` default 24 → **72**: a schema migration for the default plus a data migration that bumps the singleton from 24 to 72 only when it still reads 24 (reverse: 72 → 24 under the same guard), written as `apps.get_model("classes", "ClassSettings").objects.filter(waitlist_claim_window_hours=24).update(waitlist_claim_window_hours=72)`, never `ClassSettings.load()` (which would create the singleton with the live model inside a migration). `CmsActivity.Kind` gains `WAITLIST_OFFERED`, `WAITLIST_OFFER_PASSED`, `WAITLIST_OFFER_EXPIRED`.

**Seat math.** `spots_remaining` counts CONFIRMED + PENDING **+ held offers** as used, so a held seat reads as taken in the catalog, on the register form, and in every roster count. The same rule goes into **`spots_remaining_map`** (`classes/models.py:287`), the separate SQL count the catalog run and date pickers read (`classes/views.py:118`, `:279`, `:386`); the two must agree, and a test asserts it on a held seat. `Registration.cancel()`'s "held a spot" test includes a held offer, so passing or expiring an offer re-offers the seat exactly like a cancel does. Two other readers adjust: `_claim_email_will_fire` (`classes/views.py:1682`, the remove modal's "the next person will be offered" computation) counts held offers as held and treats only rows with `offer_expires_at` empty as eligible; and the Add to Class confirm body (`promote_confirm_body.html:19`) excludes the row's own held offer from its "already full" warning.

**Offering.** `ClassOffering.offer_open_spots()` replaces `promote_next_from_waitlist()` at its three call sites and is also called when an admin edit raises `capacity`. It runs inside `transaction.atomic()` holding `select_for_update()` on the **ClassOffering row**, so two cancellations, a cancel racing the expiry job, or a cancel racing a capacity raise serialize per class and read `spots_remaining` under the lock (today's `promote_next_from_waitlist` has this race). Loop while the class is PUBLISHED, not started, and `spots_remaining > 0`: pick the oldest WAITLISTED row with `offer_expires_at` empty; stamp `offer_expires_at = min(now + window, first upcoming session start)` (an offer never outlives the class start; a class already started or a dated class with no future session offers nothing); stamp `waitlist_notified_at`; log `WAITLIST_OFFERED`; queue the offer email with `transaction.on_commit`. The emit period is keyed on the offer itself, `reg:{pk}:waitlist_offer:{offer_expires_at:%Y%m%d%H%M}`, **not** the shipped `reg:{pk}:waitlist_spot_opened`: every waitlister notified under the old system already holds a delivery for that old period, and reusing it would hold a seat for three days for someone the dedupe silently never emailed. One offer per open seat, so two cancellations produce two offers.

**Claim.** `Registration.claim_offer(actor)`: under `select_for_update`, require a claimable offer (else `RegistrationStateError("This offer has expired or was already used.")`; a class that started or is no longer PUBLISHED raises "This class is no longer taking registrations."), clear `offer_expires_at`, then run the existing `promote_from_waitlist(actor)` (CONFIRMED, `payment_due_cents` stamped, `WAITLIST_PROMOTED` logged with `payload={"via": "claim"}`). Then: due 0 → `send_waitlist_promoted`; due > 0 → `send_payment_link_email` (the persistent "Send payment link" roster button and Mark paid keep working for the unpaid seat, as the roster spec designed, and the pay page's Stripe session lands in the existing `_handle_class_payment_link` webhook branch, `classes/webhook_handlers.py:112`, which already handles CONFIRMED-unpaid rows). Instructor and admin new-registration notices fire (`emit_instructor_new_registration`, `send_admin_registration_notification`) so the instructor hears about the new seat holder. The claim is the commitment; payment collection stays the shipped staff tooling (§11 lists the "hold as pending until paid" alternative).

**Pass.** `Registration.pass_offer(actor)`: require a claimable offer (anything past the deadline belongs to the expiry job, and the Pass button only renders on a claimable offer); `cancel(reason="Passed on the offered spot", actor=…)` logging `WAITLIST_OFFER_PASSED` instead of `WAITLIST_LEFT`; the cancel's held-a-spot branch calls `offer_open_spots()`.

**Expiry.** `Registration.objects.expire_offers(now)`: every WAITLISTED row with `offer_expires_at <= now` on a PUBLISHED class → `cancel(reason="Offer expired")` logging `WAITLIST_OFFER_EXPIRED`; the cancel's held-a-spot branch is what re-offers the seat (no second `offer_open_spots()` call), and the `waitlist_offer_expired` courtesy email (rejoin link) is queued with `transaction.on_commit` like the offer email, since the cancel and the next offer sit in one transaction. The job walks candidates one at a time inside `transaction.atomic()` with `select_for_update(skip_locked=True)` and **re-checks `status == WAITLISTED and offer_expires_at <= now` on the locked row** before cancelling: `cancel()` has no status guard (`classes/models.py:2386`), so without the re-check a claim landing between the job's select and its cancel would confirm the person and then cancel them. Runs from a new management command `expire_waitlist_offers` registered in `SCHEDULED_JOBS` with `Cadence.ALWAYS` (every 15 minutes) **and added to `_DISPATCHER_ALWAYS` in `core/spec/scheduled_jobs_spec.py`** (the parity spec that goes red every time a job is added). Offers on a class that was cancelled or archived are left alone by the job; the claim endpoint refuses them with "This class is no longer taking registrations."

**Staff actions on an offered row.** Add to Class still works (a promote on a held offer clears `offer_expires_at` first; the follow-up modal is unchanged). Remove from Waitlist cancels the offer and re-offers the seat. A new **Resend offer email** re-emits the offer with the offer period plus a resend suffix (`…:resend:{n}`). Staff never need to run the expiry by hand.

**Class edits while an offer is out.** `admin_class_edit` re-caps every held offer's `offer_expires_at` to the new first session start when that moves earlier and **re-sends the offer email** (the new stamp is a fresh period, so it delivers), because the deadline in the person's inbox must not be later than the real one. Lowering capacity does not revoke held offers (staff know the room; the over-capacity warning covers it). Cancelling or archiving the class leaves offers alone; the class_cancelled email already reaches WAITLISTED rows (`registrant_notice_emails`, `classes/models.py:929`), and the claim endpoint refuses.

**Stale links.** `_stale_claim_link_redirect` also redirects a WAITLISTED row with a held offer to its self-serve page; the offer email no longer links to the register form at all.

---

## 6. IA recommendations

| Surface | Today | Target |
|---|---|---|
| Hub sidebar (both variants, `base.html:108` / `:229`) | Class Catalog | Class Catalog · **Teach** (label "Teach a Class" → `classes:teach_orientation` when locked; "Teaching" → `classes:teach_overview` when `can_create_classes`) · **Manage Classes** (admin variant only, → `classes:admin_overview`; verify no existing entry in the topbar/avatar menu, none found in `templates/hub/`). Active state by path prefix: Teach on `/classes/teach/`, Manage Classes on `/classes/admin/`, Class Catalog on `/classes/` **excluding** those two prefixes. `can_create_classes` exposed by `hub/context_processors.py` (the hub context already loads the member). |
| Hub home (`hub/home.py`) | No teaching content | **Teaching** card when the member can create classes AND has anything to show: up to three rows (Changes requested → "Fix and resubmit"; Drafts → "Finish"; In review → stage; This week → session time) + "Open teaching" link. Absent otherwise (quiet home). |
| Instructor portal nav (`teach/base.html`) | Overview · Classes · Registrations · Discount Codes | + **Profile** (last). Header keeps "View public profile ↗" (now appears once the slug exists) and "View live catalog ↗". |
| Instructor Overview | Needs your attention (drafts + pending) · This week · At a glance · Waitlists · Recent sign-ups · guild-lead queue | Same sections; rows carry the lifecycle badge and note; Changes requested rows first with "Fix and resubmit"; pending rows show stage. |
| Instructor Classes list | Flat table, raw status | Facet chips: All · Needs attention · In review · Upcoming · Completed · Cancelled (counts); badge column (the Upcoming badge and the Upcoming chip share one label); row actions by state (Edit / Submit / Withdraw / Open). |
| Instructor Workspace overview action row | Edit (draft/pending), Submit (draft), Preview | By state: DRAFT → Edit · Submit; CHANGES_REQUESTED → notes banner + Edit + Submit; PENDING → Edit · **Withdraw**; UPCOMING → **Edit details** (light) · **Request a change** · **Cancel class** · **Run it again**; COMPLETED / CANCELLED → **Run it again**. Preview always. (No instructor delete; admins delete, as today.) |
| Admin Overview | Needs your attention (all PENDING) with Review + Approve | **Waiting on You** (awaiting admin) with Review + Approve (confirm) · **With Guild Leads** (lead, days waiting, Remind lead, Review) · the rest unchanged. |
| Admin Classes list | Status pills (raw statuses) | Facet chips from the lifecycle (All · Needs review · With guild lead · Awaiting admin · Drafts · Changes requested · Upcoming · Completed · Cancelled · Archived) with counts; badge column; sort header on `lifecycle` order. Instructor filter + My Classes unchanged. |
| Admin Workspace overview action row | Edit · Approve · Review with notes · Duplicate · Archive · Delete | By state: PENDING → Approve (confirm) · Review with notes · Archive; UPCOMING → Edit · **Cancel class** · Duplicate · Run it again · Archive (only when no active registrations, mirroring the model guard); COMPLETED → Edit · Run it again · Archive; CANCELLED → Run it again · Archive; ARCHIVED → **Restore to draft**; DRAFT → Edit · Archive · Delete (no registrations). CMS Administrators keep Approve / Review only. |
| Review page (both) | Decision form · preview · progress · details · history | The plain "Approval progress" list becomes the **review pipeline** strip (§5.4a), then the **Readiness** block above the decision (the §5.4 list, read-only), so a reviewer sees "No dates yet" as a red item, not an aside. |
| Instructor edit page and workspace overview | Stage-blind banner | The **review pipeline** card at the top while the class is draft, in review, or bounced (§7.3, §7.4): the strip, the headline, and the reviewer's note when bounced. |
| Admin class detail | Status badge only | The **review pipeline** strip under the header for PENDING and bounced classes. |
| Settings hub | Guild Types · Discount Codes · Questions · Waivers & Reminders | Unchanged, except the Waivers & Reminders page gains the **Waitlist claim window (hours)** field (PR 4). |
| Waitlist tab (both portals) | Position · Name · Email · Joined · Notified · Actions | The Notified column becomes **Offer**: "Held until Sep 8, 3:00 PM" (gold) / "Not yet" (muted); passed and expired rows render the existing removed stub with the reason. Row menu gains **Resend offer email** for a held offer. |
| Registrant self-serve page | Status line, sessions, Cancel registration | + the **offer card** (PR 4): deadline, Claim my spot, Pass. |

---

## 7. UI / UX — interaction patterns per screen (rubric applied)

Member copy: plain ELI14, short sentences, **no dashes in any copy string**. Theme tokens only; new classes `pl-` prefixed in `hub.css` (both portals extend `hub/base.html`). Verify **both themes** on every screen. Textareas live under `.hub-form-group` (Rule 13). Every Save button is the last thing in its form and says **Save** (Rule 21).

### 7.1 Sidebar entry + home card (PR 2)

- Sidebar item markup copies the Class Catalog block (icon SVG from the set), `--hub-sidebar-*` tokens only. Tap target is the full row. Locked members: "Teach a Class" → orientation page (its explainer banner is the landing state). No new dead end.
- Home card: `hub-card` titled **Teaching** placed with the other conditional cards; rows are single lines that wrap on mobile; the card is omitted entirely when empty. No loading state (server-rendered).

### 7.2 Instructor Classes list + Overview (PR 1)

- Facet chips: `hub-btn hub-btn--sm` pills in the existing `admin-filters` row shape, horizontal scroll in a contained region on phones, active pill `hub-btn--primary`. Empty facet: "No classes here yet." with a link back to All. Table degrades to the existing stacked-row behavior (`admin-table-wrap`).
- Overview "Needs your attention": CHANGES_REQUESTED rows render the badge, a one-line note excerpt (`lifecycle_note`, 120 chars), and **Fix and resubmit** (`hub-btn hub-btn--sm hub-btn--primary` → edit page). Pending rows: badge "With guild lead (Woodshop)" / "Awaiting admin" in place of the stage-blind pill. Empty state unchanged.

### 7.3 Edit page (draft / bounced / pending) (PR 1)

- **Review pipeline card** at the top (draft, in review, or bounced): heading "Where Your Class Is", the §5.4a strip, the headline ("Waiting on the guild lead (Woodshop)"), and, when bounced, the reviewer's full note in a `pl-review-note` block (gold border token) with "Fix the notes below and submit again." This card replaces the separate notes banner and stage banner: one place, one truth. Declined uses the same card with "Declined" wording. On a plain draft it reads "Not submitted yet" with the Submitted step current, so a brand new instructor sees the road ahead before they start.
- **Ready to Submit?** card directly above the button row: the five readiness items as a check list (✓ / ○, label, hint linking to the field anchor). Buttons unchanged (Save Draft / Save & Submit for Review / Cancel); an unready submit re-renders with the Django error listing the failing items and the card highlighting them. Success messages: "Submitted “{title}” for review by {first gate label}." (all three paths).
- **The "+ Offer on another set of dates" form leaves both edit pages in PR 2**, in the same PR that places Run it again on both workspaces (§7.4), never earlier (each PR ships to production alone, and a gap between removal and replacement would leave nobody able to re-run a class). It sat below the Save buttons (Rule 21 violation) at `teach/class_form.html:86` and `admin/class_form.html:76`.
- Dark + light: the checklist uses `--color-success` / `--hub-text-muted`; no new inputs.

### 7.4 Workspace overview action row (both portals) (PR 1 + PR 2)

**Routes, gates, and scoping (so nobody guesses):** instructor actions `classes:teach_class_withdraw`, `classes:teach_class_cancel`, `classes:teach_class_request_change` are `@teaching_member_required` + `require_POST` and load the class through `_teach_class_or_404` (`classes/views.py:1630`, instructor only); the published light edit reuses `classes:teach_class_edit` (same URL, branches on status) and keeps that view's `editable_by` scope, so guild leads and staff who can edit a draft today can make light edits too. Admin actions `classes:admin_class_cancel`, `classes:admin_class_restore`, `classes:admin_class_remind_lead` are `@classes_admin_access_required` (full admin), like `admin_class_archive`; Approve stays `@classes_review_access_required`. Every modal form is server-rendered inline in the workspace page (no HTMX GET for the body): a full-page POST that fails validation re-renders the workspace with the modal open and the bound errors inside it. `components/modal.html` owns its own `open` state and opens only on the `open-modal` window event (`templates/components/modal.html:19`), so the reopen is an `x-init` on the page root that dispatches `open-modal` with the modal id when the form is bound (`{% if cancel_form.is_bound %}x-init="$dispatch('open-modal', 'cancel-class')"{% endif %}`); no template does this today, so it is new but two lines.

- **Withdraw submission** (PR 2, instructor, PENDING): `pl-btn pl-btn--danger pl-btn--sm` → `confirm_modal` "Take back this submission? It goes back to draft and reviewers stop seeing it. You can submit again any time." → POST `classes:teach_class_withdraw` → Django message "Submission withdrawn." + redirect to the workspace.
- **Cancel class** (PR 1 admin, PR 2 instructor; UPCOMING): `pl-btn pl-btn--danger pl-btn--sm` opens `components/modal.html` (2 fields → modal): required **Reason** textarea (`.hub-form-group`; error "Please tell people why."; hint under it: "Your reason is emailed to everyone registered.") + a read-only line "{n} registered, {m} paid" + **Cancel class** danger submit + Cancel. POST `classes:teach_class_cancel` / `classes:admin_class_cancel` → message "Class cancelled. Everyone registered has been told." (+ for instructors with paid registrations: "An admin will handle refunds.") → redirect to the workspace, badge now Cancelled. Invalid → the workspace re-renders with the modal open and the error inside it (see the routes note above).
- **Request a change** (PR 2, instructor, UPCOMING): `hub-btn hub-btn--sm` opens a modal with one textarea "What needs to change?" + **Send** → POST `classes:teach_class_request_change` (full-page) → redirect to the workspace + Django message "Sent to the admins." (a message, not a toast: this is a full-page POST, Rule 6). Rate: none; dedupe by period per request.
- **Run it again** (PR 2, UPCOMING / COMPLETED / CANCELLED): `hub-btn hub-btn--sm` → existing duplicate-run POST behind a `confirm_modal` (primary): "Run this class again? We make a draft copy with no dates. Add the new dates, then submit it for review." Lands on the new draft's edit page (existing behavior).
- **Archive** (PR 1, admin): quiet; guarded by `archive()`; modal copy becomes honest: "Archive this class? It leaves every list and the catalog. Nobody is notified. Registrations stay on record. You can restore it to a draft later." When the guard refuses (upcoming with registrations) the button is not rendered; Cancel class is offered instead.
- **Restore to draft** (PR 1, admin, ARCHIVED): `hub-btn hub-btn--sm` → confirm "Restore to draft? It will need review again before it goes live." → POST `classes:admin_class_restore` → message.
- **Approve** (PR 1, both admin surfaces): `confirm_modal` with `confirm_button_style="primary"`: title "Publish this class?", message "“{title}” goes live in the catalog, every active member gets a new class notice, and it posts to Discord. Use Review with notes to ask for changes instead." → existing POST.
- Mobile: the action row already wraps; buttons keep full tap targets.

### 7.4a Review pipeline everywhere it appears (PR 1)

- **Instructor workspace overview** (`teach/class_overview.html`): the same card as the edit page under the header while draft, in review, or bounced; replaced by the lifecycle badge once live.
- **Admin class detail** (`admin/class_detail.html`): the strip under the header for PENDING and bounced classes, no card chrome.
- **Review page** (`admin/class_review.html`, both the admin and tokenized routes): the strip replaces the "Approval progress" `<ul>`; the review history list stays.
- **States:** one gate (no guild lead) renders three steps, two gates render four; cancelled and archived render muted; a legacy class with rows from an old cycle still renders (the method reads only what exists). Both themes verified; the strip stacks vertically on phones with the fill line running down the left.

### 7.5 Published class edit page (PR 2) — `templates/classes/teach/class_form_published.html`

- Same shell as the edit form. Top banner: "This class is live. You can update the description, photos, and what to bring any time. To change the title, dates, price, or capacity, ask an admin." + **Request a change** (opens the §7.4 modal).
- **Locked summary card** (read-only): Title · Guild Type · Price (+ member discount, sale) · Capacity · Dates list. No inputs.
- **Editable**: `TeachPublishedClassForm` (`description`, `prerequisites`, `materials_included`, `materials_to_bring`, `safety_requirements`, `age_guardian_note`, `flexible_note`, `video_url`) via `form_field.html` / the collapsible component, the FAQ formset (existing `build_class_faq_formset`), the gallery formset (existing, saves instantly), the welcome email lives on its Emails tab already. One **Save** at the bottom → message "Class updated." No submit button (nothing to review).
- States: validation errors inline; success message; Cancel link back to the workspace.

### 7.6 Admin Overview queue (PR 1)

- **Waiting on You** card first: rows = title (→ detail), instructor, guild type, days waiting (muted), actions **Review** (ghost) + **Approve** (success, opens the publish confirm). Empty: "Nothing waiting on you."
- **With Guild Leads** card second: rows = title, lead name (→ member directory), days waiting, **Remind lead** (`hub-btn hub-btn--sm hub-btn--ghost`, POST `classes:admin_class_remind_lead`), **Review** (ghost; admins may still step in). Empty: "Nothing with guild leads."
  - Mechanics: a new `send_guild_lead_review_reminder(row)` in `classes/emails.py` re-emits the lead request with `period=f"approval:{row.pk}:reminder:{today}"` and **without** the instructor explainer (`send_guild_lead_review_request` hard-codes `period=f"approval:{row.pk}:request"` and fires the explainer too, so it is not reused as is). The button is `hx-post` with `hx-swap="none"` (the FRONTEND toast pattern), and the view answers 204 + `trigger_toast` from the returned `EmitResult`: delivered → "Reminder sent to {lead}."; `skipped_duplicates` → "Already reminded today."
  - **Leadless guild:** when the open guild-lead row's guild has no lead or staff any more (`_guild_leadership_recipients` returns nobody), the row shows a muted "This guild has no lead. Review it yourself." in place of Remind lead, and Review is the primary action.
- Counts in the headings; "At a Glance" `pending` stat splits into "awaiting you" and "with leads".

### 7.7 Review page (PR 1)

- **Readiness** block (read-only list, same markup as §7.3) between the header and the decision form. A red item does not disable the form (an admin may still request changes with notes that name it), but the form's Approve option shows a hint "This class is not ready to publish yet." when any item fails, and `decide(APPROVED)` on an unready class raises with the same message (defense in depth; the tokenized page shows it as a form error).

### 7.8 Profile tab (PR 2)

- `teach/profile.html` gains a status line above the form: "Your public instructor page is live: {absolute URL}" (linked) or "Your public instructor page goes live with your first published class." Save button reads **Save**.

### 7.9 Roster email (PR 3) — `templates/classes/emails/instructor_roster.{html,txt}`

- Source: a new `instructor_roster_occurrences()` in `classes/tasks.py`, one occurrence per **session** of a PUBLISHED class whose instructor has an email, starting 24 hours out (fixed, independent of the admin's `reminder_hours_before` registrant setting) and regardless of roster size (`class_reminder_occurrences` yields only sessions paired with CONFIRMED registrations, so an empty class would never reach the instructor). Fed into the same `run_due` loop.
- Subject: "Tomorrow: {title} at {time} ({n} registered)". Body: linked class title (subject noun → the instructor workspace), session date/time (project timezone), roster table (name, paid / free / pending payment, waitlist count line, each registrant's custom answers when any) **or**, with nobody registered, "Nobody has signed up yet. Share the class page or cancel it if you need to.", the class's own "what to bring" note if set, **primary CTA** "Open the roster", secondary "Message your students" (compose link) and "See the class page". Branded shell, absolute URLs, `.txt` and `.html` in lockstep, no dashes.

### 7.10 Registrant self-serve page after a cancel (PR 1) — `my_registration`

- When the registration's class is CANCELLED, the page shows one state card: "This class was cancelled. {reason}. If you paid, a refund is on its way from our staff." with the class title linked to the catalog; the Cancel registration and Pay buttons are hidden (today the page would still offer both for a class that 404s publicly). Registrations keep their own status so the admin refund panel works unchanged.

### 7.11 Waitlist claim window (PR 4)

- **Offer email** (`templates/classes/emails/waitlist_spot_opened.{html,txt}`, rewritten copy on the existing `waitlist_spot_available` event): subject "A spot opened in {title}. It is yours until {deadline}". Body: linked class title, the session dates, "We are holding this seat for you until {deadline}. Claim it, or pass and the seat goes to the next person." (no hours figure: the deadline is capped at the class start, so a fixed number would sometimes be wrong). **Claim my spot** (primary, → the self-serve page) and **Pass** (secondary, → the self-serve page with `?pass=1`, which an `x-init` on the offer card turns into `$dispatch('open-confirm', 'pass-offer')` so the confirm is already open). Paid classes add one line: "Claiming confirms your seat. You get a payment link right after." Both links are the token self-serve URL, never the register form.
- **Self-serve page** (`classes/public/my_registration.html`, styled by `cms-public.css` (`cp-` / `dc-` classes; it extends `hub/base.html`, so `pl-btn`, `confirm_modal`, Alpine, HTMX, and toasts are available): with a claimable offer, an **offer card** replaces the status line: "A spot is yours until {deadline}" with a countdown phrase ("2 days left"), **Claim my spot** (`pl-btn pl-btn--primary`, POST `classes:my_registration_claim`) and **Pass** (`pl-btn pl-btn--danger pl-btn--sm`, opens `confirm_modal`: "Pass on this spot? It goes to the next person and you leave the waitlist. You can rejoin from the class page." → POST `classes:my_registration_pass`). Success: Django message "You're in! Check your email." (free) or "You're in! Check your email for the payment link." (paid) and the page re-renders in its confirmed state, which gains a **Pay now** link to `my_registration_pay` while `is_unpaid` (verified missing today: the only route to the pay page is the emailed link, `templates/classes/public/my_registration.html` has no reference to it). Pass: message "You've passed. The seat is free for the next person." and the page shows the removed state with a rejoin link. Expired or already-used offer: message "This offer has expired or was already used." Cancelled class: "This class is no longer taking registrations." Both endpoints are POST-only, token-authenticated like the existing self-cancel, no login required (guests keep working).
- **Expiry email** (`templates/classes/emails/waitlist_offer_expired.{html,txt}`, new `waitlist_offer_expired` event): subject "Your held spot in {title} has been released". Body: linked class title, "We did not hear back by {deadline}, so the seat is no longer held for you and you are off the waitlist. Want back in? Rejoin the waitlist here." (neutral on purpose: the waitlist may be empty, the class may have started, or the seat may simply reopen). CTA → the public class page. Branded shell, absolute URLs, both files in lockstep, no dashes.
- **Waitlist tab** (`waitlist_row.html`): the Offer cell per §6; stubs for passed and expired rows read "Passed on the offered spot." / "Offer expired." The remove modal copy (roster spec §6.7) now says "the next person will be offered the seat and has until its deadline to claim it." (no hours figure, same reason as the offer email). **Resend offer email** in `waitlist_row_menu.html` → POST `classes:registration_resend_offer` (HTMX, `hx-swap="none"`, toast "Offer email sent again to {email}."), only rendered for a held offer.
- **Settings** (`admin/settings.html`, Waivers & Reminders): `waitlist_claim_window_hours` via `form_field.html` with hint "How many hours a waitlisted person has to claim an opened seat before it goes to the next person." Min 1, max 336 (two weeks) in the form. Save button unchanged (already last).
- **States:** no eligible waitlister → the seat simply returns to the public (`spots_remaining` > 0 and the catalog shows it). Offer window capped at class start shows "until 2:00 PM today". Dark + light: the offer card uses the page's `cms-public.css` tokens; verify both themes. Mobile: buttons stack full-width under 480px.

### 7.12 Checklist walk

- **§1 list editors:** none new (the FAQ and gallery formsets are reused unchanged).
- **§2 forms:** every new form names its submit and container (reason modal, change-request modal, published edit page with Save last); validation in forms (`ClassCancelForm.clean_reason`, `ClassChangeRequestForm`), messages stated.
- **§3 destructive:** cancel / withdraw / archive / restore / delete / publish all behind `confirm_modal` or a modal with the consequence named; danger styling on the destructive ones.
- **§4 states:** every list has an empty state; modals re-render errors; successes are messages (full-page) or toasts (HTMX); no dead ends (locked members → explainer; refused archive → cancel offered; unready submit → checklist).
- **§5 themes:** badge and note card on tokens; textareas scoped.
- **§6 mobile:** facet chips scroll contained; action rows wrap; tables degrade as today.
- **§8 components:** all named above; nothing reinvented.
- **§9 emails:** roster email and the two new events follow the email rules; every placeholder supplied by its emit context (tested).
- **§10 user lens:** every state has a next step on screen; nothing you can create but not withdraw, cancel, restore, or run again.

---

## 8. Notifications / emails / activity

| Event key | When | Recipients | Channels | PR |
|---|---|---|---|---|
| `class_cancelled` (exists) | `cancel()` (no longer `archive()`) | ALL_ACTIVE_MEMBERS in-app (as today), `email_to` registrants | the **registrant email** gains "Reason: {reason}"; the in-app broadcast copy is unchanged (a reason written for registrants is not for every member's bell); keeps the "find another class" CTA | 1 |
| `class_cancelled_admin_notice` *(new)* | instructor cancels a class with paid registrations | new composed resolver `refund_authority` = fog-admins **or** REFUNDS capability holders, exactly the set `refund_authority_required` admits (`hub/view_as.py:291`; the 0118 backfill only seeded admins of that day, so "admins hold every capability" is not reliable). This deliberately revises the `AdminCapability` docstring's "REFUNDS routes no notifications" contract (`membership/models.py:2405`): update the docstring in the same PR. CLASS_APPROVERS is the wrong audience because the CTA is a refund. | in-app ON, email ON. Copy: "{instructor} cancelled {title}. {n} paid registrations need refunds." CTA → the admin class Registrations tab. Grouped under Staff & leadership. | 2 |
| `class_change_requested` *(new)* | `request_change()` | `CLASS_APPROVERS` | in-app ON, email ON. Copy: "{instructor} asked for a change to {title}: {note}". CTA → admin edit page. | 2 |
| `instructor_class_tomorrow` *(new)* | `instructor_roster_occurrences()` finds a session starting within a fixed 24 hours (§7.9) | `INSTRUCTOR` | email ON, in-app ON, push OFF (opt-out allowed; operational but not critical). Period `session:{pk}:instructor_roster`. | 3 |
| review request, explainer, decision, and changes emails (exist) | every send | as today | each HTML body gains the `_review_pipeline.html` table above the CTA and each `.txt` body the bracketed line, rendered from the same `review_pipeline()` the pages use: the guild lead's request shows their step current; the admin validation request shows the guild step done and the admin step current; the "approved, waiting on the next step" email shows it half full; "your class is live" shows both checks and Live green; changes requested shows the reviewer's step marked and the note | 1 |
| `waitlist_spot_available` (exists) | `offer_open_spots()` | registrant (`email_to`) + the `next_waitlisted` in-app row | copy rewritten around the held seat, deadline, Claim and Pass links; period keyed on the offer stamp (`reg:{pk}:waitlist_offer:{offer_expires_at:%Y%m%d%H%M}`, never the legacy `waitlist_spot_opened` period); Resend appends `:resend:{n}` | 4 |
| `waitlist_offer_expired` *(new)* | `expire_offers()` | registrant (`email_to`) | email FORCED (operational), in-app row when a member. CTA → class page to rejoin. | 4 |
| review request reminder | Remind lead | the open `GUILD_LEAD` row's audience via the new `send_guild_lead_review_reminder(row)` (§7.6) | as the original lead request, no instructor explainer, period `approval:{pk}:reminder:{date}` | 1 |

Rules for the builder: declare every placeholder in `core/events/copy.py`; broadcast channels never greet a person (none of these declare Discord); spine copy verified cream-on-dark; `.txt` + `.html` parity; subject/body in one timezone.

Activity: the four new `CmsActivity` kinds mirror to `SiteActivity` through the existing `classes.activity.log` path.

---

## 9. Build order — three sequential PRs, each ships green

Each PR: targeted suites + `ruff format/check` + `manage.py check` (the pre-push hook runs real mypy), `template_comment_lint_spec`, VERSION bump (next free minor at merge time), one curated no-dash changelog entry. **E2e lane:** `tests/e2e/instructor_orientation_spec.py` asserts only the post-unlock landing URL and the message "Teaching unlocked — welcome, instructor." (both untouched here); `tests/e2e/login_and_book_spec.py` and the screenshot/help specs snapshot class surfaces. Before pushing each PR, grep `tests/e2e/` for every UI string the PR relabels or removes (none of this spec's targets have hits today) and run the affected specs on Postgres 5433.

### PR 1 — Honest class states and review queue

1. Migration: `CANCELLED` + the three columns. `Lifecycle`, `lifecycle`, `lifecycle_note`, `with_lifecycle_inputs`, the six queryset methods, `readiness()` / `is_ready`, **`review_pipeline()` + the page and email components + the two template tags (§5.4a)**, the `submit_for_review` guard, **`publish(actor)`** (called by `on_review_decision_recorded` and by `admin_class_create`, no slug minting yet; the admin create form shows a failing readiness item as a form error; `admin_class_approve` gains `except ValidationError` → `messages.error` + redirect, and `_class_review_view` catches it into a form error, since today the quick-approve catches only `ValueError` (`classes/views.py:2517`) and the review view wraps `decide()` in nothing (`:2695`)), `cancel()`, `archive()` guard + no emit, `restore()`, `duplicate` / `duplicate_as_new_run` clearing the cancel fields, activity kinds. The registrant self-serve cancelled state (§7.10).
2. Badge component + facet helper; instructor Classes list + Overview; admin Classes list + Overview split queue + Remind lead (`send_guild_lead_review_reminder`, leadless state); admin workspace action row by state (Cancel modal, quiet Archive, Restore, publish confirm); review page pipeline strip + readiness block; edit-page pipeline card (with the bounce note) and readiness card; workspace overview and admin detail pipeline; the pipeline table and text line in the five review emails (`.txt` and `.html` in lockstep); honest submit messages.
3. `class_cancelled` reason copy.
4. Changelog:
   > *"Clearer class status: Every class now shows exactly where it is on a simple review pipeline, Submitted, Guild lead, Admin, Live, with a check for each step that is done. You see it on your class page and in every review email. If a reviewer asks for changes, their notes show right on your class with a Fix and resubmit button. A class needs photos, a description, and dates before it can be submitted. Admins now cancel a live class with a reason and everyone registered is told, while archiving is quiet housekeeping that can be undone."*

### PR 2 — Find teaching, run your class

1. Sidebar Teach + Manage Classes entries (both nav variants, context processor), home Teaching card.
2. Profile tab; `Member.ensure_instructor_slug()` extracted from `grant_instructor` (and used by the `apply_admin_role` copy too, so one loop remains), called from `publish(actor)` when `instructor.can_create_classes`; profile status line.
3. Instructor actions: withdraw, cancel (+ admin notice event), request a change (+ event), light-edit published form + template, Run it again placement on both workspaces **and, in the same PR, the duplicate-run form removed from both edit pages**.
4. Changelog:
   > *"Teaching is easier to find and run: Teach now lives in the sidebar for every member. Instructors can update a live class's description and photos, ask an admin for bigger changes, cancel a class if plans fall through, take back a submission, and run a finished class again with one click. Your public instructor page goes live with your first published class."*

### PR 3 — Roster in your inbox

1. `instructor_class_tomorrow` event, copy, templates; `instructor_roster_occurrences()` in `classes/tasks.py` (one per session, fixed 24 hours, empty rosters included) fed into `send_class_reminders`, per-session dedupe.
2. Changelog:
   > *"Roster in your inbox: Instructors get an email the day before each class with who is coming and what they said they need, plus quick links to the roster and to message the group."*

### PR 4 — Waitlist spots are held for you

1. Migration: `Registration.offer_expires_at`; `ClassSettings.waitlist_claim_window_hours` default 72 + the guarded data bump (`apps.get_model`, never `load()`); activity kinds. `spots_remaining` **and `spots_remaining_map`** count held offers; `cancel()` treats a held offer as a held spot.
2. `offer_open_spots()` under the ClassOffering row lock with `on_commit` emails and the offer-keyed period (replacing the three `promote_next_from_waitlist` call sites, plus the capacity-raise call and the deadline re-cap in `admin_class_edit`), `claim_offer` (claimable + not started), `pass_offer`, `expire_offers` (per-row lock + re-check); the `expire_waitlist_offers` command + `SCHEDULED_JOBS` entry + **the `_DISPATCHER_ALWAYS` parity tuple**.
3. Self-serve offer card + claim/pass/resend endpoints; offer email rewrite; expiry email + event; waitlist tab Offer column, stubs, Resend; `_claim_email_will_fire` and the promote confirm warning adjusted for held offers; settings field; `_stale_claim_link_redirect` extension.
4. Changelog:
   > *"Waitlist spots are held for you: When a seat opens up, the first person on the waitlist gets it held for three days with a Claim or Pass link in their email. Pass, or let the time run out, and the seat moves to the next person automatically. Admins can change the hold time in class settings."*

> Spec only — do not build until approved.

---

## 10. Testing

BDD `*_spec.py`, `describe_*` / `it_*` ONLY (`context_*` is silently skipped), factory-boy (`classes/factories.py`), 100% branch gate, fast-merge policy. Homes: `classes/spec/models/`, `classes/spec/views/`, `tests/hub/` (sidebar, home), `tests/core/events/`. Session fixtures at `now ± timedelta(days=2)`.

**PR 1**
- `lifecycle`: every branch (archived, cancelled, pending with open guild row, pending with an open admin row, pending with zero rows, draft with a CHANGES_REQUESTED row, draft with a DENIED row, **draft with only an APPROVED guild-lead row reads plain Draft**, plain draft, published dated future, published dated past, published flexible stays Upcoming, published dated with zero sessions reads Upcoming with the no-dates note); `lifecycle_note` quotes the latest bouncing row when two decided rows exist; annotations match the property for a mixed fixture set (no per-row queries — `assertNumQueries`).
- Queryset methods return exactly the right rows; facets map to them; counts on the chips.
- `readiness()`: each item ok/fail; `is_ready`; `submit_for_review` error lists every failing label; the old images-only message is gone; flexible model passes with a note and fails without.
- `review_pipeline()`: three steps without a guild lead, four with; every cell of the §5.4a table (plain draft → Submitted current; PENDING with open guild row → guild current; guild APPROVED + admin open → guild done, admin current, fill half; PUBLISHED → all done, Live green, headline "Live since"; guild CHANGES_REQUESTED → guild step marked with the note, class DRAFT; admin DENIED likewise; resubmit after a bounce → strip reset with Submitted done and guild current); cancelled and archived render muted without error; legacy rows from an old cycle do not crash; the page component, the email table, and the text line all render from one call and agree on every step state; the five review emails carry the table and the line in both `.txt` and `.html`; the `aria-label` equals the headline.
- `cancel()`: PUBLISHED only; blank reason raises; fields set; activity; `class_cancelled` emitted once, the reason in the registrant email only; catalog / calendar / Discord posts exclude it.
- `archive()`: refuses upcoming-with-registrations with the message; archives a completed / draft / cancelled class quietly (no emit — assert zero `class_cancelled` rows); `restore()` → DRAFT with approvals cleared + activity.
- `publish(actor)`: `decide(APPROVED)` on an unready class raises; ready class publishes as today (regression block); `admin_class_create` saving PUBLISHED on an unready class shows the form error and saves nothing (rolled back); on a ready one it publishes through `publish()` (activity + `class_published` fire exactly once); **view level:** quick-approve on an unready class → `messages.error` + redirect, no 500; the admin review page and the tokenized review page render the readiness message as a form error, no 500.
- Duplicate paths: `duplicate` and `duplicate_as_new_run` of a CANCELLED class yield a DRAFT with `cancelled_at` / `cancelled_by` / `cancellation_reason` empty.
- Registrant self-serve page on a cancelled class: state card with the reason, no Cancel / Pay controls; unchanged for other statuses.
- `class_cancelled`: the registrant email carries the reason; the in-app broadcast row does not.
- Admin overview: waiting-on-you vs with-guild-leads membership (a PENDING class with zero rows lands in Waiting on You); Remind lead sends once per day (`skipped_duplicates` → the "Already reminded today" toast), never fires the instructor explainer, and a leadless guild renders the review-it-yourself line instead of the button; publish confirm markup present on both surfaces; CMS Administrator sees the confirm.
- Instructor overview / list / edit page: badge per state, notes banner text, stage banner text, readiness card items, honest submit message in create / edit / submit views.
- Permission edges (crafted POSTs): plain member 403 on admin cancel / restore / remind; instructor cannot cancel another's class; CMS Administrator cannot restore.
- Changelog-renders-everywhere check.

**PR 2**
- Sidebar: label + href flip on `can_create_classes`; admin variant shows Manage Classes; Class Catalog not active on `/classes/teach/`.
- Home card: absent when nothing to show or locked; rows and cap of three.
- `ensure_instructor_slug`: minted on first publish only when `can_create_classes`, unique suffixing, idempotent, never touches `instructor_oriented_at`; a revoked instructor whose class an admin publishes gets no slug; `grant_instructor` and `apply_admin_role` still mint (and unlock) exactly as today through the shared helper (regression).
- Withdraw: PENDING → DRAFT, **every** approval row deleted (including an APPROVED guild-lead row, so the draft reads plain Draft, not Changes requested), activity; tokenized reviewer page renders "not awaiting review"; non-owner 403; guild staff who can `editable_by` a draft still cannot withdraw it.
- Instructor cancel: allowed on own PUBLISHED; admin notice emitted only when paid registrations exist, with the count; no notice for a free class.
- Request a change: event emitted with the note; blank note error in the modal.
- Published edit: only light fields save; a crafted POST with `price_cents` / `capacity` / sessions is ignored (not saved); locked summary renders; draft/pending still use the full form.
- Run it again: button by state on both workspaces; POST creates the undated draft copy (existing behavior); the duplicate-run form is gone from both edit pages and nothing sits under Save.
- Request a change: full-page POST → redirect + Django message; Remind lead: `hx-post` → 204 + toast.

**PR 3**
- Command emits one `instructor_class_tomorrow` per session starting within 24 hours, **including a session with zero registrations** (empty-roster copy renders), independent of `ClassSettings.reminder_hours_before`; dedupes on re-run; skips cancelled / archived / draft classes and instructors without an email; template renders every placeholder (no `[missing:]`), `.txt` / `.html` parity, subject in project timezone; the registrant reminder path is byte-identical (regression block); `core/spec/scheduled_jobs_spec.py` parity untouched (no new job).

**PR 4**
- `spots_remaining` and `spots_remaining_map` agree: a held offer counts as used in both, including one past its deadline that the job has not cancelled yet; a cancelled (passed / expired) row does not; the public register form, the catalog cards, and the date pickers all read the held seat as full.
- Legacy rows: a waitlister who already holds an old `waitlist_spot_opened` delivery receives the new offer email (the offer-keyed period is fresh) and gets a held seat only together with that email.
- `offer_open_spots`: one offer per open seat (two cancels → two offers to the two oldest eligible rows, never the same row twice, serialized by the ClassOffering lock); skips rows with any stamped offer; caps the deadline at the first session start; offers nothing on a started, cancelled, archived, or draft class; stamps `waitlist_notified_at` and logs `WAITLIST_OFFERED`; the email is sent after commit (assert with `django_capture_on_commit_callbacks(execute=True)`; the house precedent is the deferred refund receipt in `billing/refunds.py:281` and its capture in `tests/billing/refunds_service_spec.py`); the capacity-raise path in `admin_class_edit` offers the new seats, and moving the first session earlier re-caps every held offer's deadline and re-sends the offer email.
- `claim_offer`: claimable offer → CONFIRMED with `payment_due_cents`, `offer_expires_at` cleared, `WAITLIST_PROMOTED` via claim, plain email for due 0 and the pay-link email for due > 0 (exactly one email either way), instructor + admin notices fire; expired, already-claimed, and concurrent double claims raise the stated error under the row lock; a cancelled, archived, or started class refuses with the stated message.
- Expiry vs claim interleaving: a claim that lands after the job selected the row but before it cancels is honored (the job's locked re-check sees CONFIRMED and skips); the reverse order leaves the row cancelled and the claim raising. Note for the builder: SQLite ignores `select_for_update`, and `skip_locked` has no precedent in this repo, so the real lock behavior is exercised only on Postgres 5433 (the re-check still runs logically on SQLite); the expiry email is asserted through the on-commit capture above.
- `_claim_email_will_fire` counts a held offer as held and only an un-offered row as eligible; the Add to Class confirm body does not warn "full" when the only overage is the row's own held offer.
- `pass_offer`: cancels with `WAITLIST_OFFER_PASSED`, the seat is re-offered to the next person; a pass on an expired offer raises.
- `expire_offers`: cancels only WAITLISTED rows with a past deadline on PUBLISHED classes, sends the expiry email once (period), re-offers the seat; a second run sends nothing; the settings window changes the stamped deadline on the next offer, not on ones already held.
- Data migration: singleton at 24 becomes 72; a singleton already at 48 stays 48; reverse only touches 72.
- Parity: `core/spec/scheduled_jobs_spec.py` lists `expire_waitlist_offers` in the always tuple and the command is dispatched by `run_scheduled_tasks`.
- Views/templates: the self-serve page renders the offer card only for a claimable offer; claim and pass endpoints are POST-only and token-gated (a wrong token 404s); the confirmed-unpaid state shows Pay now; the waitlist tab shows "Held until", the passed and expired stubs, and Resend only on a held offer; Resend re-emits with a new period; the settings form accepts 1 to 336 and rejects 0; both email templates render every placeholder with no dashes; `_stale_claim_link_redirect` sends a held-offer row to its self-serve page.
- Changelog-renders-everywhere check.
---

## 11. Open / deferred

- **Attendance / check-in** — a `Registration.attended_at` toggle in the roster row menu is cheap, but nobody has asked; add on demand.
- **Instructor payouts, revenue per class for instructors** — policy first ("instructors don't see money" is the standing rule).
- **Series "In progress"** — folded into Published; revisit if multi-session series need it.
- **Threaded change requests** — one notice per request is enough; no ticket model.
- **SLA timers / auto-escalation for stale guild-lead gates** — Remind lead covers it; automate only if leads go quiet routinely.
- **Wizard / step form for class creation** — the roadmap chose "the existing form, not a wizard"; the readiness card is the lighter fix.
- **Re-orientation, instructor tiers, co-instructors** — out of scope.
- **Waitlist offer variations** — moving a silent waitlister to the back instead of removing them (chosen: remove, with a rejoin link, so the list never loops); a per-class claim window (site-wide only for now); holding a claimed paid seat as PENDING until payment lands (chosen: claim confirms, staff collect, matching the roster spec's promote); a manual "Offer the seat" staff action (Add to Class covers it).
