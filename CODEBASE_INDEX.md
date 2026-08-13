# plfog Codebase Index

## Apps

| App | Purpose |
|-----|---------|
| `membership/` | Core domain: Member, Guild (+ staff roles, orientations, FAQ/links/announcements), Space, Lease, voting, funding snapshots |
| `classes/` | **Book CMS** — classes/workshops: offerings, sessions, registrations, approvals, discount codes, instructor emails, CSV export (served on the `book.` subdomain) |
| `billing/` | Stripe tab system: Tab, TabEntry, TabCharge, StripeAccount, Product |
| `core/` | Auth + platform infra: Invite, SiteConfiguration, PushSubscription, notifications/triggers, transactional email (`core.email.send`), SiteActivity, scheduled tasks |
| `hub/` | Member-facing views (guild voting, directory, tab, profile, guild pages) |
| `airtable_sync/` | Airtable bidirectional sync for members, spaces, leases, votes |
| `plfog/` | Django project: settings, urls, wsgi, auto_admin, adapters |
| `education/` | Placeholder (empty — migrations only) |
| `outreach/` | Placeholder (empty — migrations only) |
| `tools/` | Placeholder (empty — migrations only) |

## Key Models

### membership
- `Member` — makerspace member; has Status, MemberType, FogRole; linked 1:1 to User via allauth
- `MembershipPlan` — tiered pricing (monthly_price, deposit_required)
- `Guild` — interest guild; members vote on which guild receives funding
- `GuildStaffMembership` — guild staff roles (co-lead/secretary/treasurer/orienter); each grants full lead authority
- `AdminCapability` — scoped admin authority per member (class/space/discount/calendar/billing approver); routes the matching approval/alert notifications AND grants the action, decoupled from the `fog_role=admin` tier
- `GuildMembership` — a member's membership in a guild
- `GuildOrientationSettings` / `GuildFAQItem` / `GuildLink` / `GuildAnnouncement` / `GuildImage` — guild-page content & orientations
- `VotePreference` — persistent 3-choice ranked vote per member (synced to Airtable)
- `FundingSnapshot` — immutable historical funding calc; guild allocations stored in results JSON
- `Space` — physical space (studio/storage/parking/desk); linked to Airtable
- `Lease` — tenant→space via GenericForeignKey (tenant = Member or Guild)
- `MemberEmail` — additional email aliases per member

### billing
- `BillingSettings` — singleton (pk=1); controls charge frequency/day/time, default_tab_limit
- `StripeAccount` — Stripe Connect account per guild (acct_xxx)
- `Product` — purchasable product offered by a guild
- `Tab` — one per member; accumulates entries; holds stripe_customer_id + payment_method
- `TabEntry` — single line item; pending until included in a TabCharge
- `TabCharge` — batched Stripe charge; status: pending→processing→succeeded|failed

### core
- `SiteConfiguration` — singleton (pk=1); controls RegistrationMode (open / invite_only)
- `Invite` — email invite with pre-created Member placeholder; accepted on signup
- `PushSubscription` — Web Push subscription per user
- `Notification` / `NotificationPreference` — per-user in-app notifications + per-trigger channel prefs (see `core/triggers.py` catalogue, `core/notifications.py` dispatch)
- `TransactionalEmailLog` — logs every email sent via `core.email.send()` (sync; console in dev, Resend in prod)
- `SiteActivity` — cross-app activity/audit feed

### classes (book CMS — `book.` subdomain)
- `Category` (+ `verbose_name`) — class grouping; **UI labeled "Guild"** (see relabel plan); optional FK to `membership.Guild`
- `ClassOffering` (+ `ClassOfferingQuerySet`) — a class/workshop; status DRAFT/PENDING/PUBLISHED/ARCHIVED; `public()` / `bookable()`; sequential approval state machine; instructor welcome-email fields
- `ClassSession` — individual dated session (`starts_at` / `ends_at`); a series offering has many
- `ClassApproval` (+ QuerySet) — sequential guild-lead→admin approval rows with tokenized review links
- `Registration` — a signup; status PENDING/CONFIRMED/WAITLISTED/CANCELLED/REFUNDED; `self_serve_token`
- `RegistrationQuestion` / `RegistrationAnswer` — custom per-class signup questions
- `RegistrationReminder` — dedupe audit for scheduled reminder emails
- `DiscountCode` (+ QuerySet) — per-class discount codes
- `ClassImage` (gallery) · `Waiver` · `InstructorMessage` / `InstructorMessageRecipient` (instructor→registrant messaging)
- `CmsActivity` — classes activity feed (mirrors to `core.SiteActivity`)
- `ClassSettings` — singleton (pk=1); reminder timing, email footers, admin-notify emails

## URL Structure

```
/admin/                         Django admin
/admin/membership/member/invite/ Custom invite action
/admin/take-snapshot/           Trigger funding snapshot
/accounts/                      allauth (login, signup, email verification)
/billing/payment-method/...     Stripe setup, confirm, remove
/billing/api/setup-intent/      AJAX — create Stripe SetupIntent
/billing/webhooks/stripe/       Stripe webhook receiver
/billing/admin/dashboard/       Billing admin dashboard
/billing/admin/add-entry/       Admin: add tab entry for any member
/billing/connect/initiate/<id>/ Initiate Stripe Connect for a guild
/billing/connect/callback/      Stripe Connect OAuth callback
/guilds/voting/                 Guild voting page
/guilds/voting/history/         Snapshot history
/guilds/voting/history/<pk>/    Snapshot detail
/guilds/<pk>/                   Guild detail with products
/members/                       Member directory
/settings/profile/              Profile settings
/settings/emails/               Email preferences
/feedback/                      Beta feedback form
/tab/                           My Tab (current balance + add entry)
/tab/history/                   Past billing charges
/                               Home / redirects (core.views)

# --- classes / book CMS (mounted at /classes/, served on book. subdomain) ---
/classes/                       Public class catalog
/classes/<slug>/                Public class detail
/classes/<slug>/register        Register for a class
/classes/category/<slug>/       Browse a category (UI: "guild")
/classes/instructor/<slug>/     Public instructor page
/classes/my/<token>/            Registrant self-serve (manage / cancel)
/classes/review/<token>/        Tokenized class review (guild lead / admin)
/classes/teach/                 Instructor dashboard + class management
/classes/admin/                 CMS admin (overview, classes, registrations, categories, discount codes, settings)
/classes/admin/registrations/export/  Registrations CSV download
/account/                       Book CMS account area (classes.account)
```

## Test Structure

BDD/spec style (pytest-describe, `*_spec.py`, `describe_*` / `it_*`). **Two locations coexist** — newer apps keep specs beside the code in a per-app `spec/` dir; older suites live under the root `tests/` tree.

Per-app `spec/` dirs (newer):
```
classes/spec/    models/, views/, exports_spec.py, emails_*_spec.py, ...   (+ classes/factories.py)
billing/spec/    ...
core/spec/       ...
```

Root `tests/` tree (mirrors app names):
```
tests/
  membership/    models, admin, forms, guild, signals, vote_calculator, ...
  hub/           views, guild_voting, tab_views, guild_pages, templatetags, ...
  airtable_sync/ client, service, config, airtable_pull, integration, ...
  auth/          allauth_spec.py        admin/  admin_login_spec.py
  plfog/         adapters, auto_admin, dashboard, settings, wsgi
  e2e/           Playwright browser tests (login→book flow, axe a11y ratchet)
```

Factories: `classes/factories.py`, `tests/membership/factories.py`, `tests/billing/factories.py`.

> `context_*` blocks are **not** collected — use `describe_*` for every nested block (see CLAUDE.md §7).

Root `conftest.py` provides:
- `_disable_airtable_sync` (autouse) — sets `AIRTABLE_SYNC_ENABLED=False`
- `_fake_stripe_keys` (autouse) — uses test-safe fake Stripe keys

## External Integrations

| Integration | App | Config |
|-------------|-----|--------|
| Stripe (Connect + PaymentIntents) | `billing/` | `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_CONNECT_CLIENT_ID` |
| Airtable | `airtable_sync/` | `AIRTABLE_API_KEY`, `AIRTABLE_BASE_ID`, `AIRTABLE_SYNC_ENABLED` |
| allauth (email auth) | `plfog/` | `ACCOUNT_*` settings in `plfog/settings.py` |
| Web Push | `core/` | `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_ADMIN_EMAIL` |
| Discord webhook | GitHub Actions only | `DISCORD_WEBHOOK_URL` secret in repo |

## Important Patterns

- **Airtable-managed records**: `Member`, `Space`, `Lease` are read-only from Django's perspective — pulled via `airtable_pull` command. No save/delete sync overrides on those models.
- **VotePreference + FundingSnapshot** sync TO Airtable on save (outbound only).
- **Tab.add_entry()** uses `select_for_update()` + `transaction.atomic()` to prevent race conditions on balance checks.
- **Tab charges to guilds** use Stripe Connect destination charges (`create_destination_payment_intent`); charges without a guild use standard PaymentIntents.
- **Fog roles** map to Django permissions: admin→is_superuser+is_staff, guild_officer→is_staff, member→neither.
- **Class approval** is a *sequential* state machine in `ClassOffering` / `ClassApproval`: submit creates only the first gate (guild lead if the category's guild has a lead, else admin); guild-lead approval escalates and creates the admin gate; publish requires all required roles approved.
- **All transactional email** goes through the single choke-point `core.email.send()` (logs to `TransactionalEmailLog`). Senders live in `classes/emails.py`. Event/notification routing is `core/triggers.py` + `core/notifications.py` (in-app always; email/push on per-user opt-in).
- **Scheduled jobs** run via `core/management/commands/run_scheduled_tasks.py` (Render `cron` every 15 min, `render.yaml`). NB: `classes` reminder/follow-up emails are *not yet* wired into this dispatcher (see the event-driven-notifications spike).

## Version & Changelog

`plfog/version.py` contains `VERSION` and `CHANGELOG`. Must be bumped on every PR. Discord workflow reads CHANGELOG on merge to main.

## Deployment

- **Production**: Render.com (`DATABASE_URL` points to PostgreSQL)
- **QA/Staging**: Hetzner VPS at `pastlives.plaza.codes`
- **Local**: SQLite (default when `DATABASE_URL` unset)
- See memory file `deployment.md` for Hetzner deploy commands
