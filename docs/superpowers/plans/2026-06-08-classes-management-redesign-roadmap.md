# Classes Management Redesign — Roadmap

A role-clear, reuse-first redesign of the classes/workshops **management** surfaces (admin + instructor portals). The public CMS catalog (`book.*`) keeps its look. The existing plumbing — role routing, `view_as`, the approval workflow, every existing form/table/email/preview view — is reused; the new work is information architecture.

Design was brainstormed visually; mockups persist in `.superpowers/brainstorm/`.

## Target end-state

- **Admin nav:** `Overview · Classes · Settings` + a "View live catalog ↗" action.
- **Instructor nav:** `Overview · Classes` + the catalog action (Profile moves to the avatar menu).
- **Overview** (role-filtered): admin sees the approvals queue, money, recent registrations, activity, and graphs; instructors see *their* review status / sign-ups / waitlist (no money, no approvals) and a "Create your first class" empty state.
- **Per-class Workspace** (shared, permission-scoped): one class managed via tabs **Edit · Registrations (x) · Waitlist (x) · Discount Codes · Email**, with Preview/Duplicate/Archive as actions. Create-a-class = the Edit tab on a blank class (the existing form, reused — not a wizard).
- **Settings** (admin only): Categories · Discount Codes · Questions · Waivers & reminders.

## Phases (each independently shippable)

### Phase 1 — Admin Overview + slim nav + Settings hub  ← detailed plan written
`2026-06-08-classes-admin-overview-phase1.md`. New `admin_overview` dashboard at `/classes/admin/`; nav collapses to Overview/Classes/Settings; config pages grouped behind a Settings hub; Activity + Registrations demoted to "view all" links; live-catalog action. Reuses all existing views.

### Phase 2 — Instructor Overview + slim instructor nav + empty state
To be detailed after Phase 1 lands. New `instructor_overview` (their attention items, their counts, no money/approvals); instructor nav → `Overview · Classes`; "Create your first class" empty state routing to the existing instructor create form; Profile relocates to the avatar/account menu; live-catalog action on the instructor base. Reuses `instructor_dashboard`, `instructor_registrations`, the instructor form.

### Phase 3 — Per-class Workspace (tabbed shell, both portals)
To be detailed after Phase 2 lands. Re-house the existing per-class screens (class detail/edit, per-class registrations table + bulk email, waitlist section, per-class discount codes, preview/hero/gallery) into one tabbed Workspace: `Edit · Registrations (x) · Waitlist (x) · Discount Codes · Email`. Shared by instructor (own classes) and admin (any class) via the existing permission checks. This is the largest re-housing; little net-new logic.

## Coordination note

`plfog/version.py` is at 2.4.0 in this worktree while the in-flight 2.5.0 release is built on `release-2.5.0` by another agent. This redesign targets **2.6.0**. `version.py` is the likely merge-conflict point between the branches — resolve at integration time with a human in the loop.
