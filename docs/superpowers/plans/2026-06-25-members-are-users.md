# Members are Users — unify the person model + reshape the member admin

**Status:** spec (not built). Build after review.
**Surface:** FOG hub admin — Manage Members list + member edit page; membership/core models, signals, an admin email-management UX.
**Version at build:** bump `plfog/version.py` + member-friendly changelog (one entry, at build time — not now).

## Why

Today a `Member` may have **no linked `User`** (Airtable-imported people who never signed in). That seam leaks into the admin as *"No linked user yet — emails can be managed once this member signs up,"* a dead end. The owner's model is simpler: **every member is a person with an account** — some just haven't logged in yet — and a **User without a Member** is a valid "Non-member user." This spec makes the data match that model and reshapes the member admin around it.

## Locked decisions

| # | Decision |
|---|---|
| 1 | **Every Member has a User (invariant).** Backfill existing members; auto-provision going forward. |
| 2 | Backfill is an **idempotent management command** (`provision_member_users`, `--dry-run`), NOT a data migration — observable, re-runnable, batched, and **silent** (sends zero email). |
| 3 | Provisioned users get a **verified primary `EmailAddress`** from `_pre_signup_email` (login-by-code works immediately). Staged `MemberEmail` rows are promoted too. |
| 4 | **Manage Members lists everyone** — members (with/without sign-in) AND Users with no Member, the latter badged **"Non-member user."** |
| 5 | Member edit page becomes **tabbed: Details \| Emails.** Details shows the **single** primary email; Emails tab manages all addresses. |
| 6 | Status badges: **"Signed in"** (has logged in), **"Hasn't signed in yet"** (user exists, `last_login is None`), **"Non-member user"** (User, no Member). |
| 7 | **"Send login invite"** button on the edit page for not-signed-in people (new path; does not reuse the "already a member" guard). |

## Definitions (badge logic)

For a person row:
- **Non-member user** — a `User` with no `Member`. (Only possible direction now, since every Member has a User.)
- **Hasn't signed in yet** — `member.user.last_login is None`. (Account exists, never authenticated.)
- **Signed in** — `member.user.last_login is not None`.

`member.has_signed_in` → `bool(self.user_id and self.user.last_login)` (property; prefetch `user` to avoid N+1).

## What already exists (reuse — file:line)

| Thing | Location |
|---|---|
| Member↔User OneToOne (nullable today) | `membership/models.py:186-191` |
| `_pre_signup_email` (db col `email`) | `membership/models.py:194-204` |
| `Member.primary_email` (resolves the single email) | `membership/models.py:323-356` |
| Email helpers (add/remove/set_primary/toggle) | `membership/email_aliases.py:28-103` |
| Staging → allauth promotion on link | `membership/managers.py` `MemberEmail.objects.migrate_to_user()` |
| User-created → links/creates Member + promotes emails | `membership/signals.py:20-101` (`ensure_user_has_member`) |
| Login-code auto-create-user form | `plfog/adapters.py` `AutoCreateUserLoginCodeForm` |
| Invite create + branded email | `core/models.py:243-382`; spine email branded in v0.19.11 |
| Manage Members list view/template | `hub/views.py:2032-2078`; `templates/hub/admin/members.html` |
| Member edit view/template | `hub/views.py:2082-2138`; `templates/hub/admin/member_edit.html` |
| Per-member email POST endpoints | `hub/urls.py:156-175`; `hub/views.py:2140-2210` |
| Member email-status buckets (v0.19.12) | `membership/models.py` `MemberQuerySet.with_email_status()` |

## 1. The invariant + going-forward provisioning

### 1a. Provisioning helper (single source of truth)
Add `membership/services/provisioning.py` (cross-model orchestration → service per CLAUDE.md):

```python
def provision_user_for_member(member: Member, *, verified: bool = True) -> User:
    """Idempotently ensure `member` has a linked User with a primary EmailAddress.
    SILENT: sends no email, fires no notification. Returns the linked user."""
```

Behavior:
- If `member.user_id` is set → return it (idempotent no-op).
- Else resolve the email = `member._pre_signup_email` (lowercased). If blank → **do not provision** (can't make a passwordless account with no email); leave user None and let the caller/report flag it (this is the genuinely-emailless case from v0.19.12). Return None.
- Create `User(username=email, email=email)` via `create_user` with an **unusable password** (passwordless; login is by code).
- Create primary `EmailAddress(user, email, verified=verified, primary=True)` (or reuse if exists).
- Promote `MemberEmail` staging rows via `migrate_to_user`.
- Link `member.user = user; member.save(update_fields=["user"])`.
- **Suppress the email-sending side of any signal** — see Risk R1.

### 1b. Going-forward auto-provision
`post_save` on `Member` (created and `user_id is None`): call `provision_user_for_member(member)`.
- **Recursion guard (Risk R2):** creating the User triggers `ensure_user_has_member` (User post_save). That signal must (a) find the existing member by email and link it (NOT create a second member), and (b) be safe to run while we're mid-provision. Implementation: have `provision_user_for_member` set `member.user` **before** returning and make `ensure_user_has_member` a no-op when a member is already linked to the new user / when the email already maps to a member it just links. Add a re-entrancy guard (e.g. a thread-local or an explicit `_provisioning` flag) so the two hooks don't double-promote emails. Tests must assert exactly one User and one Member result and one set of EmailAddress rows.
- **No-plan case:** the user signal currently early-returns if no MembershipPlan exists *when creating* a member. Here the member already exists, so linking must work regardless of plans. Verify/adjust.

### 1c. Airtable import
`airtable_pull` creates members via `Member.objects.create(...)`. With 1b's signal, each newly-imported member auto-provisions a user. Confirm the pull stays **silent** (no emails) and idempotent on re-run (existing members already have users). The pull still writes `_pre_signup_email` (Airtable remains source of truth for that field per membership/CLAUDE.md).

### 1d. Backfill command
`membership/management/commands/provision_member_users.py`:
- Iterate `Member.objects.filter(user__isnull=True)` in batches.
- For each: `provision_user_for_member(member)`; count provisioned / skipped-blank-email.
- `--dry-run` prints the plan (who would get a user) without writing.
- **Silent** — Risk R1 guard wraps the whole run.
- Print a summary: `Provisioned N users, skipped M (no email on file).`
- Idempotent: safe to re-run; already-linked members are no-ops.
- Run at deploy (like `seed_notification_templates`). Document in the build's done-list.

## 2. Manage Members lists everyone (members + non-member users)

The list becomes **person-centric**. Two sources, unioned and paginated:
- All `Member` rows (every one now has a user) — existing columns/filters apply.
- All `User` rows with **no** Member (`Member` reverse is null) — badged "Non-member user."

Implementation:
- Add `MemberQuerySet`/manager support OR a small assembler in the view that yields a uniform `PersonRow` (name, email, badges, edit-url, is_member, status/role/type-or-None). Keep assembly logic in a manager/service per fat-models; the view stays thin.
- **Filters:** status/role/type/email filters apply to members; when any member-only filter is active, **non-member users are excluded** (they have no such fields) — state this in the UI (the filter description). The default (no filter) shows everyone.
- **Pagination** over the combined, ordered sequence (order: members by name, then non-member users by email — or interleave by name; pick one and state it). Keep 50/page.
- **Email column:** `primary_email` for members (v0.19.12), `user.email`→ via a `primary_email`-equivalent for non-member users (read their primary EmailAddress; never the mirror).
- **Badges in the list:** "Hasn't signed in yet" (muted) and "Non-member user" (distinct token). Reuse the v0.19.13 `.hub-pill--*` tokens (neutral/danger) — don't invent colors.
- Non-member user rows get an **edit link** too (see §3 — the edit page must handle a user with no member: show Details as read-only identity + Emails tab + a "Create membership record" affordance is **out of scope**; for now Details shows the user's identity + "Non-member user" and the Emails tab works).

## 3. Member edit page — tabbed redesign

Replace the current single-scroll page (`member_edit.html`) with a **tabbed** layout. Use the established hub tab pattern (find an existing tabbed admin page — e.g. the guild edit page's Staff tab — and match its markup/Alpine, FRONTEND.md compliant; do NOT invent a new tab system).

### Header (above tabs)
- Name + **single** primary email (`member.primary_email`), never the mirror, never "no linked user."
- **Status badge:** "Signed in" / "Hasn't signed in yet" / "Non-member user."
- "← Back to Members".

### Tab: Details
- The existing `MemberAdminEditForm` (unchanged) with its visible **Save member** button.
- A compact read-only line: **Email:** `{{ member.primary_email }}` with a "Manage in Emails tab →" link (the single email lives here; editing/adding happens in the Emails tab).
- **If not signed in:** a **"Send login invite"** button (see §4) with helper text ("Emails them a link to sign in for the first time").
- Extract the page's inline `<style>` into `static/css/member-edit.css`; **remove `templates/hub/admin/member_edit.html` from the lint baseline** in `scripts/check_no_inline_style_in_extra_head.py` (it's grandfathered today).

### Tab: Emails
- **Never** show "No linked user yet." Because every member now has a user, the existing EmailAddress UI applies to **all** members. (For the rare race where a member truly has no user yet — blank email — show "Add an email to enable sign-in" with the add form writing `_pre_signup_email` + provisioning on first non-blank save. Edge, not the main path.)
- Single primary email shown first (★ primary), then any additional, each with verified/primary state.
- **"+ Add email"** (visible button, wired) → existing `hub_admin_member_email_add`.
- Per-row **Set primary** / **Mark|Unmark verified** / **Remove** (existing endpoints). Remove is destructive → `hub-btn--sm hub-btn--danger` + `confirm_modal.html` (today it's a raw danger button with no confirm — fix to the repo standard). Guard: can't remove the only/primary email (helper already in `email_aliases.remove_alias`).
- All controls theme-tokened (dark+light), 8px grid, mobile reflow (rows stack, no horizontal scroll).

## 4. "Send login invite" for existing, not-signed-in people

The existing `Invite.create_and_send` guards against "already a member" — every member now trips that. Add a **distinct** action:
- `hub/views.py` `admin_member_send_login_invite(request, pk)` (POST, `@fog_admin_required`).
- Emails the member a branded "sign in for the first time" message via the spine (`emit(...)` with the branded shell from v0.19.11) containing a link to the login-code request page with their email prefilled (e.g. `/accounts/login/code/?email=...` — confirm the actual allauth request-code URL).
- Since their email is verified (§1a), the link lets them request a code and log in. No new Invite row needed; optionally stamp `member`/activity for audit.
- Button lives on the Details tab (not-signed-in only). Success/loading/error states + toast. Reuse the v0.19.13 toast pattern.
- Decide copy: a new spine event `member.login_invite` (curated copy in `core/events/copy.py`, seeded), or reuse `member.invited` copy with a login URL. Prefer a small new event for clarity; it inherits the branded shell automatically.

## UI/UX completeness (apply the checklist)
- Tabs: keyboard-reachable, default to Details; the active tab is visually clear in both themes.
- Every form has a visible, wired submit (Save member; Add email; Send login invite).
- Email list: real **+ Add** and per-row **Remove** *buttons* (not toggles); Remove confirms via modal; margins on the 8px grid.
- Badges: theme tokens only; legible dark + light.
- Empty/loading/error/success states for: empty Emails (shouldn't happen post-provision, but handle), add-email error (dupe/invalid), send-invite success/failure, non-member-user edit page.
- Mobile: tabs and email rows reflow; tap targets ≥ the existing button size; no horizontal scroll on the list table (reuse v0.19.12 `.pl-members-table`).

## Tests (BDD pytest-describe; `it_*` in `describe_*`; `context_*` NOT collected)
- `provision_user_for_member`: creates user+verified primary email; idempotent; blank-email → no user; promotes staged MemberEmail; **sends no email** (assert `len(mail.outbox)==0`).
- Going-forward signal: creating a Member yields exactly one User + one Member + correct EmailAddress rows; **no recursion / no duplicate member**; works with and without a MembershipPlan; no email sent.
- Airtable import path: a pulled member ends up provisioned + silent (respx/mock the table per repo convention).
- Backfill command: provisions all userless members, `--dry-run` writes nothing, idempotent on re-run, summary counts correct, silent.
- List: shows members + non-member users; non-member rows badged; member-only filters exclude non-member users; pagination over the union; email column never reads the mirror.
- Edit page: Details + Emails tabs render; header shows primary_email + correct status badge; "No linked user yet" string is **gone**; not-signed-in shows Send-login-invite; Emails tab Add/Remove/Set-primary work; Remove confirms via modal.
- Send login invite: emits the branded email to the member's address; not-signed-in only.
- Lint guard: `member_edit.html` removed from baseline and has no inline `<style>`.

## Risks & mitigations (review these hardest)
- **R1 — email blast on backfill.** Creating User/EmailAddress or linking members could fire signals that send welcome/notification emails to hundreds of real addresses (this DB holds pulled prod data). Mitigation: the provisioning helper + command run under an explicit silent guard; audit every `post_save`/allauth signal in the chain (`user_signed_up`, `email_confirmed`, `ensure_user_has_member`, any membership welcome) and confirm none send mail on this path. Test asserts `mail.outbox == []`. **This is the #1 thing to verify.**
- **R2 — signal recursion / duplicate members.** Member→User hook vs User→Member signal. Mitigation: re-entrancy guard + make `ensure_user_has_member` link-only when the email already maps to a member. Tests assert single user/member/email-set.
- **R3 — invite guard.** Don't route existing members through `create_and_send`. Separate `send_login_invite` path. Test both.
- **R4 — list performance.** Union + per-row primary email could N+1. Prefetch primary EmailAddress for both sources; assert query budget.
- **R5 — mass-verified emails.** Provisioned emails are `verified=True` → anyone with a member's on-file email can log in by code. This is the owner's explicit choice (option B); note it in the changelog/PR so it's a conscious deploy decision.
- **R6 — local deploy.** After build, run the backfill command against the local prod-data DB to demonstrate; it must be silent and idempotent.

## Review fixes — MUST apply (from adversarial review + DB ground-truth)

DB ground truth (local prod-data copy, 2026-06-25): 610 members, **573 with no user** (Airtable imports — the backfill target); 38 users, **1 genuine non-member user** (`student@pastlives.demo`, a `book.*` class registrant — non-staff, never logged in). Non-member users are real and are NOT just admins — the reviewer's generalization was wrong. Keep decision #4.

1. **Status preservation (BLOCKER).** `ensure_user_has_member` force-sets `status=ACTIVE` on link (`membership/signals.py:57,72`). The backfill must NOT resurrect cancelled/suspended people or bypass invite acceptance. Backfill scope = `Member.objects.filter(user__isnull=True, status=Member.Status.ACTIVE)` only. **Skip `INVITED`** placeholders entirely (provisioning them would bypass `mark_accepted`). Provisioning must preserve the member's existing `status` (do not let the link path flip it) — verify the link path and add a guard so provisioning never changes status.
2. **Duplicate `_pre_signup_email` (BLOCKER).** No unique constraint exists (`membership/models.py:342-348`); `ensure_user_has_member`'s `.get(_pre_signup_email__iexact=..., user__isnull=True)` (`signals.py:53`) raises `MultipleObjectsReturned`. The command must pre-flight detect duplicate emails, report them as skips (don't crash mid-batch), and `--dry-run` must list them. Provisioning helper handles the multi-match case gracefully (skip + log).
3. **Re-entrancy = FULL suppression (BLOCKER-adjacent).** During `provision_user_for_member`, `ensure_user_has_member` must be a **complete no-op** (thread-local/contextvar guard), with the helper owning: create user → create verified primary EmailAddress → `migrate_to_user` → link. Do NOT rely on "link-only" behavior (a case/whitespace lookup miss falls through to the create branch `signals.py:80-106` → second Member → OneToOne collision `models.py:239`).
4. **Reuse the existing idempotent creator.** Use `AutoCreateUserLoginCodeForm._create_user_idempotent` logic (`plfog/adapters.py:303-317`): `create_user(username=email, email=email)` with the `IntegrityError` swallow. Handle `username` length (email can exceed 150) — truncate/hash or catch; don't let one bad row kill the batch.
5. **Non-member-user EDIT route (BLOCKER).** The current route keys on Member pk (`hub/urls.py:165`, `admin_member_edit` → `get_object_or_404(Member, pk)`; email endpoints via `_email_member_or_redirect` `views.py:2150`). Non-member users have no Member pk. Add a **separate user-keyed route** `manage/users/<int:user_pk>/edit/` → a view that renders the same tabbed page in a "non-member user" mode: Details shows identity + "Non-member user" badge (no MemberAdminEditForm), Emails tab manages their `EmailAddress` rows via **user-keyed** email endpoints (parallel to the member ones, or generalize the existing ones to accept a user). The list links member rows to the member-edit route and non-member rows to the user-edit route.
6. **"Send login invite" link (MAJOR).** allauth's `RequestLoginCodeView` (`account_request_login_code`, `accounts/login/code/`) has **no `get_initial`** — `?email=` is ignored. Add a thin view override (mirror `HubEmailView` at `plfog/urls.py:76`) that seeds the email into the form/session, OR accept a blank field and say so in the email copy. Prefer the override for a seamless first-login.
7. **List = mostly Airtable imports + the few non-member users.** Union: members (every one provisioned → has user) + Users with `member__isnull=True`. Do NOT exclude non-member users, but DO decide on staff/superuser: badge system/admin accounts distinctly or exclude `is_superuser` from the "Non-member user" rows so the owner's admin login isn't listed as a class-registrant-style row (the 1 real non-member user is non-staff; superusers are noise here). Recommend: exclude `is_superuser=True` from the non-member-user rows.
8. **Light-theme pills (MINOR).** `.hub-pill--neutral/--danger` light overrides are scoped to `.pl-invite-list` (`hub.css:452-454`). Extend the light override to the members-table context so badges are legible in light theme.
9. **Backfill is MANUAL at deploy** (not in `render.yaml` buildCommand `render.yaml:5-8`) — like `seed_notification_templates`. Document in the done-list; note that running it against prod mints ~hundreds of verified login-capable accounts at that instant (R5 — the owner's explicit choice).
10. **R1 silence is belt-and-suspenders, not load-bearing** — the existing chain (`create_user`→`ensure_user_has_member`→`migrate_to_user`) sends no mail (verified). Keep the `assert mail.outbox == []` tests so a future change can't silently introduce a blast.

## Out of scope
- Creating Member records from non-member Users (the reverse direction) — non-member users are listed/badged/edit-emails only.
- Changing Airtable's ownership of `_pre_signup_email`.
- Bulk "invite all not-signed-in" (could be a follow-up).
- Self-serve member email UI at `/accounts/email/` (unchanged).
