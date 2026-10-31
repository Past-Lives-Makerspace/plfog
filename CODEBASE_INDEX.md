# plfog Codebase Index

## Apps

| App | Purpose |
|-----|---------|
| `membership/` | Core domain: Member, Guild (+ staff roles, orientations, FAQ/links/announcements), Space, Lease, voting, funding snapshots |
| `classes/` | **Book CMS** — classes: offerings, sessions, registrations, approvals, discount codes, instructor emails, CSV export (served on the `book.` subdomain) |
| `billing/` | Stripe tab system: Tab, TabEntry, TabCharge, StripeAccount, Product |
| `core/` | Auth + platform infra: Invite, SiteConfiguration, PushSubscription, notifications/triggers, transactional email (`core.email.send`), SiteActivity, scheduled tasks |
| `hub/` | Member-facing views (guild voting, directory, tab, profile, guild pages) |
| `airtable_sync/` | Airtable bidirectional sync for members, spaces, leases, votes |
| `kiln/` | Ceramics Guild kiln tickets (#691), a tab on that guild's page: tickets with photos and flags, loading, unloading with ready for pickup notices, the kiln log, the clay and glaze lists; see `kiln/AGENTS.md` |
| `polls/` | Polls (#708, back in #925): one site-wide poll on Home while open, one per guild on its page's Polls tab, Past Polls at `/polls/`, Admin Tools > Polls at `/polls/manage/`; never in the Spotlight. See `polls/AGENTS.md` |
| `hq/` | The Holiday Bizarre HQ (Track C): one hub page per event with a hero band, goal chips and nine door cards an admin opens to the public, to members or to named people; behind the `holiday_bizarre` switch; `manage.py seed_holiday_bizarre --event <pk>` writes Lee's hub, doors, goals and shifts from `hq/holiday_bizarre.py`; see `hq/AGENTS.md` |
| `tours/` | Facility tours (#947; not the in-app guided tours in `core/tours.py`): Tour Settings, each Tour Guide's weekly tour hours and days off, bookings, and open tour times computed live (`tours/availability.py`); the visitor booking site on tours.pastlives.space with its emails and reminder (#948, `tours/booking.py`, `tours/notify.py`); the staff's Upcoming and Past tabs with reschedule, cancel and attendance (#949); the front desk kiosk on cairn, check in or schedule a tour (#950, `tours/kiosk.py`, `tours/kiosk_views.py`); each guide's Google Calendar, tours written to it and busy times hiding tour times (#951, `tours/google.py`, `tours/calendar_sync.py`, the `sync_tour_calendars` job); see `tours/AGENTS.md` |
| `plfog/` | Django project: settings, urls, wsgi, auto_admin, adapters |

Those ten plus `plfog/` are the whole of `INSTALLED_APPS` for this project. Other top-level directories are not Django apps:

| Directory | What it is |
|-----------|-----------|
| `api/` | DRF permission classes (`IsFogAdmin`, `IsFogAdminOrReadOnly`) used by `membership/api_views.py`. Not an installed app. |
| `mobile/` | The Capacitor shell that wraps the live site for the App Store and Google Play. Nothing in it renders UI; see `mobile/README.md`. |
| `assets/` | One showcase hero image that `demo_data` seeds. Not served as static. |
| `scripts/` | Developer and CI helpers: the pre-push hook source, screenshot capture, the email gallery build, demo seeding. |
| `changelog/`, `changelog.d/` | The folded version and its unreleased fragments; see `AGENTS.md` § Versioning. |

## Key Models

### membership
- `Member` — makerspace member; has Status, MemberType, FogRole; linked 1:1 to User via allauth
- `MembershipPlan` — tiered pricing (monthly_price, deposit_required)
- `Guild` — interest guild; members vote on which guild receives funding
- `GuildStaffMembership` — guild staff roles (co-lead/secretary/treasurer/orienter); each grants full lead authority
- `AdminCapability` — scoped admin authority per member (class/space/discount/calendar/billing approver, Tour Guide); routes the matching approval/alert notifications AND grants the action, decoupled from the `fog_role=admin` tier
- `GuildMembership` — a member's membership in a guild
- `GuildOrientationSettings` / `GuildFAQItem` / `GuildLink` / `GuildAnnouncement` / `GuildImage` — guild-page content & orientations
- `OrientationType` — one kind of orientation a guild offers (Shop Basics, Lathe, …) with its own duration/price/seats/location; `OrientationAvailability`/`OrientationSlot`/`OrientationBooking` each FK a type; "oriented for the guild" = any completed type, one live booking per (type, member)
- `VotePreference` — persistent 3-choice ranked vote per member (synced to Airtable)
- `FundingSnapshot` — immutable historical funding calc; guild allocations stored in results JSON
- `Space` — physical space (studio/storage/parking/desk); linked to Airtable
- `Location` — a named area of the building, optionally a guild's (#616); classes, events, orientation types and equipment point at it through a nullable `area` FK, and `membership/services/location_status.py` lights it on the guild page
- `Lease` — tenant→space via GenericForeignKey (tenant = Member or Guild)
- `ShelfUnit` / `Cubby` — a guild's own rentable cubbies on shelf units drawn as grids (#977); rules and read models in `membership/cubbies.py`, the staff manager in `hub/cubby_views.py` and `hub/cubby_forms.py`. `CubbyWaitlistEntry` (#980) is a member waiting for any cubby in a full guild, `membership/cubby_waitlist.py`. Not the Past Lives house cubbies, which are Airtable `Space` rows
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
- `core/threads.py` — the shared conversation thread: abstract `ThreadMessage` / `ThreadPhoto` bases each domain builds its own tables on (`FeedbackComment`, `OrientationBookingMessage`), with one rule for who hears a message
- `Notification` / `NotificationPreference` — per-user in-app notifications + per-trigger channel prefs (see `core/triggers.py` catalogue, `core/notifications.py` dispatch)
- `TransactionalEmailLog` — logs every email sent via `core.email.send()` (sync; console in dev, Resend in prod)
- `SiteActivity` — cross-app activity/audit feed

### polls
- `Poll` / `PollChoice` / `PollVote` — a question, its 2 to 6 answers and one anonymous vote per member; `Poll.guild` (blank is site wide); `Poll.post` refuses while one is open in that scope, `PollCard` is one poll as one member sees it, `GuildPolls` a guild's Polls tab; written answers (`allows_write_in`, `PollVote.write_in`, named or Anonymous) where the poll takes them
### hq
- `EventHub` / `HubCard` / `HubGoal` — the Holiday Bizarre HQ: the one open hub, its door cards (kind, audience, Can open and Team, hidden) and its goal chips; `hq.services.goal_rows` counts the live kinds
### classes (book CMS — `book.` subdomain)
- `Category` (+ `verbose_name`) — class grouping; **UI labeled "Guild"** (see relabel plan); optional FK to `membership.Guild`
- `ClassOffering` (+ `ClassOfferingQuerySet`) — a class; status DRAFT/PENDING/PUBLISHED/ARCHIVED; `public()` / `bookable()`; sequential approval state machine; instructor welcome-email fields
- `ClassSession` — individual dated session (`starts_at` / `ends_at`); a series offering has many
- `ClassApproval` (+ QuerySet) — sequential guild-lead→admin approval rows with tokenized review links
- `ClassMessage` / `ClassMessagePhoto` — a class's messages between its instructors and its reviewers (the shared thread, `core/threads.py`, #975). `ClassOffering.post_message` always tells the other side minus the author (`class.message_to_reviewers` to the first gate's audience plus reviewer side writers who still review, never all admins; `class.message_to_instructors` to every instructor); `write_message` tells nobody: a decision's note (`ClassApproval.decide`, the note stays on the row too) and a live class's change request ride on their own notice. `message_side(user)` decides who reads and writes (instructors; the guild's lead and Class Reviewers, CMS Administrators, admins). The thread hangs off the class, so a resubmit that deletes a bounced row keeps it; `0096` carried every earlier note in
- `Registration` — a signup; status PENDING/CONFIRMED/WAITLISTED/CANCELLED/REFUNDED; `self_serve_token`
- `RegistrationQuestion` / `RegistrationAnswer` — custom per-class signup questions
- `RegistrationReminder` — dedupe audit for scheduled reminder emails
- `DiscountCode` (+ QuerySet) — discount codes that work everywhere, on one class or on every class one instructor teaches (`honoured_on`)
- `DiscountCodeRequest` (+ QuerySet) — an instructor's ask for a class code, approved or declined by an admin; approval creates the `DiscountCode`
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
/feedback/                      Feedback form
/orientation/bookings/<pk>/     A member's orientation booking and its messages (the shared thread)
/equipment/reservations/<pk>/messages/  A reservation's messages for the Messages modal (the shared thread)
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
/classes/review/<token>/messages/  Post a class message from a review link (the token is the credential)
/classes/teach/classes/<pk>/messages/  A class's messages: GET lands each reader on their page, POST posts one
/classes/teach/                 Instructor dashboard + class management
/classes/teach/classes/<pk>/    The ONE per-class screen (Overview / Registrations / Waitlist /
                                Discount Codes / Emails). Admin, CLASS_APPROVER reviewer,
                                instructor and guild lead all land here; classes/access.py
                                decides which tabs and actions each of them gets. The old
                                /classes/admin/<pk>/... paths 302 onto it.
/classes/admin/                 CMS admin (overview, classes, registrations, categories, discount codes, settings)
/classes/admin/registrations/export/  Registrations CSV download (admins only)
/account/                       Book CMS account area (classes.account)
/hq/                            The Holiday Bizarre HQ: 302 to the open hub's page (/hq/<slug>/, public); /hq/new/, /hq/<slug>/edit/ and /hq/<slug>/cards/<pk>/edit/ for admins
/facility-tours/                The Tours page, opened from the Tools page's Tours card (Tour Guides and admins): Upcoming (here), Past at /facility-tours/past/, My Hours (weekly tour hours, days off and the guide's Google Calendar, whose sign in returns to /facility-tours/google/callback/) at /facility-tours/hours/ and, for admins, Settings at /facility-tours/settings/
/tour/                          The visitor's tour pages, ONLY on tours.pastlives.space (TOURS_HOSTS), whose root is the booking page; /tour/b/<token>/ (and reschedule/, cancel/) from the emails
/kiln/                          Kiln tickets (a guest's home; members land on the Ceramics Guild page's ?tab=kiln; /kiln/load/, /kiln/unload/, /kiln/log/, /kiln/lists/ for the crew and admins)
/guilds/<pk>/cubbies/           A guild's Cubbies manager (#977): Layout, a cubby's page at /cubbies/<pk>/, Pricing at /guilds/<pk>/cubbies/pricing/; /cubbies/ opens the first guild the viewer runs
/tools/                         Admin Tools for admins, Guild Tools for guild officers, leads and staff (#912, #921; hub/guild_tools.py)
/manage/spotlight/              Admin Tools > Spotlight (#708): the Spotlight messages that crawl across the top of Home (#935)
/manage/site-settings/          Site Settings: Brand, General, Calendar, Emails, Discord
/manage/member-agreement/, /manage/features/, /manage/cms/, /manage/automations/
                                Member Agreement, Manage Features, Manage CMS, Automations (#922; Site Settings tabs until then)
/manage/bookings/               Manage Bookings (#768): the one home for everyone who runs orientations or equipment; /orientations/manage/ 302s here
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

> `context_*` blocks are **not** collected — use `describe_*` for every nested block (see STANDARDS.md §7).

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
| Discord (bot, slash commands, event mirroring, notification webhooks) | `core/events/discord.py`, `core/integrations/discord_events.py`, `hub/discord_commands.py` | `DISCORD_BOT_TOKEN`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_INTERACTIONS_PUBLIC_KEY`, `DISCORD_NOTIFY_WEBHOOK_URL` |
| Release announcements to Discord | `.github/workflows/release.yml` | `DISCORD_WEBHOOK_URL` repo secret |

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

`VERSION` and `CHANGELOG` are **computed at import** in `plfog/version.py`, folded by `plfog/changelog.py` from `changelog/base.json` + `changelog.d/*.toml` + `changelog/history.json`. Nothing hand-edits a version number.

Every PR adds one fragment to `changelog.d/` declaring `bump = "patch" | "minor" | "major"` (`changelog.d/README.md` is the contract); CI fails a PR without one. `release.yml` folds the version, pushes the tag, and announces the fragments that push **added**.

A member-facing fragment also names its `audience` list, its `feature` and its `guidance` level (#1007). `core/guidance.py` is the registry of feature keys (title, where it lives, steps, tour, help key), beside `core/help_registry.py` (Info View keys) and `core/tours.py` (Guided Tours); `docs/GUIDANCE.md` is the contract for the levels and the PR's `**Guidance:**` line. `core/whats_new.py` reads the composed changelog for one viewer (#1007, part 2): the audience keys `request.view_as` puts them in (one query), every entry by tab and by feature 20 a page, the last 30 days as what can be unseen, the seen state on `Member.whats_new_seen` (the badges) and the opened state on `Member.whats_new_opened` (the buttons: one open quiets them until the next release); `hub/whats_new_views.py` serves it as the What's New modal (`hub/AGENTS.md`). `core/templatetags/guidance.py` (part 3) is `{% new_badge "<key>" %}` and `{% whats_new_button "<key>" %}`, on the guild page's tabs and statbar, the tool cards and the tools page title, Home's quick links and the sidebar, and the button in the header of every page a feature key lives on (#1030; `components/page_header.html` takes `new_key=`, the classes shells a `portal_whats_new` block, and `tests/hub/whats_new_on_feature_pages_spec.py` walks the registry); `{% tab_picker %}` carries the badge into the phone picker and `static/js/guidance.js` marks a key seen on the click (`FRONTEND.md`, New! badge and What's New button).

## Deployment

- **Production**: Render.com (`DATABASE_URL` points to PostgreSQL)
- **Staging**: Hetzner VPS at `staging.pastlives.space` and `classes.staging.pastlives.space`, a contained clone of production that tracks `main` (`deploy/staging/README.md`)
- **Local**: SQLite (default when `DATABASE_URL` unset); the canonical dev stack is `docker compose up -d` from the primary checkout (see `/spin-up`)
