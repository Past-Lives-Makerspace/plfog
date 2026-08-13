# membership app

Core domain models for Past Lives Makerspace.

## Models

| Model | Key fields | Notes |
|-------|-----------|-------|
| `MembershipPlan` | name, monthly_price, deposit_required | Tiers (e.g. "Standard $50/mo") |
| `Member` | `_pre_signup_email`, status, member_type, fog_role, membership_plan | Primary actor; links 1:1 to User. Read emails via `member.primary_email` — see the Email Model section below. |
| `MemberEmail` | member FK, email | Pre-signup staging table; migrated to allauth EmailAddress on User link |
| `Guild` | name, is_active, guild_lead FK, about | Interest guild; receives funding votes |
| `VotePreference` | member 1:1, guild_1st/2nd/3rd FK | One per member; auto-syncs to Airtable |
| `FundingSnapshot` | cycle_label, funding_pool, results JSON | Immutable calc; created via `FundingSnapshot.take()` |
| `Space` | space_id, space_type, status, size_sqft | Physical space; read from Airtable |
| `Lease` | GenericFK tenant (Member or Guild), space FK | Active when start_date≤today and end_date null/≥today |

## Email Model — Three Stores (IMPORTANT)

Three places an email can live for a Member. Future agents MUST understand which is authoritative when. See `docs/superpowers/specs/2026-04-07-user-email-aliases-design.md` for the full rationale.

| Store | Role |
|---|---|
| `Member._pre_signup_email` (DB column `email`, accessed via `db_column="email"`) | Source of truth ONLY when `Member.user` is None (Airtable-imported members who haven't signed up yet). |
| `allauth.account.EmailAddress` | Source of truth for any Member with a linked User. Owns login, verification, and the primary flag. |
| `User.email` | Mirror kept in sync by allauth. Never read or write directly from app code. |

### Reading "the" email
- Use `member.primary_email` (property). It returns the live value: primary `EmailAddress.email` for linked members, `_pre_signup_email` otherwise, with a final fallback to `user.email`.
- **Exception:** `airtable_sync/` reads `_pre_signup_email` directly because Airtable is the external source of truth for unlinked members and we don't want sync to re-enter allauth.

### Writing (user-facing)
- Members manage their own emails at `/accounts/email/` (themed `templates/account/email.html` over allauth's built-in `account_email` view).
- Admin: the `MemberEmailInline` is **only** shown for unlinked members. Once linked, it's hidden because allauth.EmailAddress is now the truth.

### Login
- Allauth login-by-code looks up any verified `EmailAddress` row, so any verified alias works automatically.
- Pre-signup aliases imported into `MemberEmail` are promoted to `EmailAddress` when the user signs up — handled by `MemberEmail.objects.migrate_to_user(user)`, called from the `ensure_user_has_member` signal.

## Airtable Sync

`Member`, `Space`, and `Lease` are **read from Airtable** via `airtable_pull` management command. They do NOT sync back — treat as read-only from Django's perspective. `airtable_record_id` fields link to Airtable rows.

`VotePreference` and `FundingSnapshot` sync **to Airtable** on save (outbound only).

## Fog Roles → Django Permissions

`Member.set_fog_role()` calls `sync_user_permissions()` which sets:
- `admin` → `is_superuser=True`, `is_staff=True`
- `guild_officer` → `is_superuser=False`, `is_staff=True`
- `member` → both False

Never set `is_staff`/`is_superuser` directly — always go through `set_fog_role()`.

## Guild Leads — edit authority

A guild's lead is **only** the `Guild.guild_lead` FK (→ Member). That FK alone grants edit rights — no FOG role, Django group, or `is_staff` flag required. `fog_role` (admin / guild_officer) is a *separate*, cross-guild staff tier and keeps working independently.

- **One source of truth for "who may edit what":** `membership/permissions.py` — `can_edit_guild(request, guild)`, `can_edit_class(request, offering)`, `can_edit_category(request, category)` (all `view_as`-aware). For role-based checks (commands, model logic, tests) use `Member.can_edit_guild` / `Member.can_edit_class` and `ClassOffering.objects.editable_by(member)`. Don't reimplement these inline in views.
- Never gate a guild-lead surface on `is_staff`, `fog_role`, or `member_type` — that reintroduces the drift that left real leads unable to save. `MemberType.GUILD_LEAD` is an Airtable label only and grants nothing.
- Guild was removed from the Django admin (v1.6.0). Assign a lead with `manage.py set_guild_lead --guild <name|id> --member <email>` (it warns if that member has no linked user). Detect drift with `manage.py audit_guild_leads` (leads with no login, inactive leads, guilds with no lead).

### Guild Staff (co-leads, secretaries, treasurers, orienters)

Beyond the single `guild_lead` FK, a guild has `GuildStaffMembership` rows (`role` ∈ co_lead / secretary / treasurer / orienter), managed by leads/staff on the **Staff tab** of the guild edit page. **Every staff role grants the same authority as the lead** — `can_edit_guild`/`can_edit_class`/`can_manage_orientations` all treat staff like the lead, and `editable_by`/`awaiting_guild_lead` include staffed guilds. Lead-facing emails and notifications (class-review requests, orientation requests) fan out to `Guild.leadership_members()` (lead + all staff, deduped). The former orientation-only `Guild.orienters` M2M was folded into the `orienter` staff role (migration `0049`); orienters now get full lead permissions. Use `Member.is_guild_staff` / `Member.staffed_guilds` and `Guild.is_staffed_by` / `staff_by_role` / `leadership_members`.

## Admin Capabilities

`AdminCapability` grants a member a **scoped admin duty** without promoting them to the full `fog_role=admin` tier. Each capability does two things at once: it **routes** the matching approval/alert notifications to the holder, and it **grants the action** (the holder can actually approve/decline that object type).

| Capability (`AdminCapability.Capability`) | Routes these notifications | Grants this action |
|---|---|---|
| `CLASS_APPROVER` (Class Administrator) | `class_review_requested` (lead-less categories), `class_validation_requested` | approve/validate classes |
| `SPACE_APPROVER` (Space & Cubby Administrator) | `space.lease_requested`, `space.cubby_requested` | review space requests (`hub._map_reviewer_scope` grants admin-level review) |
| `DISCOUNT_APPROVER` (Discount Code Administrator) | `discount_code.requested` | approve any discount code (`DiscountCode.approver_for` → `approves_any`) |
| `EVENTS_APPROVER` (Calendar Administrator) | `event.submitted`, `meeting.item_proposed` (site-wide/council) | review calendar proposals (`hub._reviewer_guild_scope` grants admin-level review) |
| `BILLING_APPROVER` (Billing Administrator) | `billing.charge_failed_admin` | (alert-only — no approve action; the billing dashboard stays `fog_admin`-gated) |

Helpers on `Member`: `has_admin_capability(cap)` (the authorization gate) and `sync_admin_capabilities([...], granted_by=…)` (reconcile-to-set, used by the admin member-edit form). Assign/revoke on the **Details tab** of the hub Member edit page (`MemberAdminEditForm.capabilities`, admin-only, same surface as `can_self_approve_discounts`).

**Routing mechanics (`core/events/resolvers.py`):** a capability resolver (e.g. `class_approvers`) returns ONLY the capability holders, tagged `capability:<name>`. The capability is the master switch: a `fog_role=admin` member who does not hold it receives nothing (and does not see the row on their settings page — see below); they can self-grant it to opt in. The `guild_leadership_or_{class,events}_approvers` resolvers **compose** (guild present → that guild's leadership ONLY; guild absent → the capability holders) — never a union. Migration `0118` backfills every existing admin with every capability so nobody loses a blanket notification on rollout.

**Settings-page grouping (`core/events/settings_matrix.py`):** every staff/leadership/admin event is collected into one **"Staff & leadership"** section (rendered first), out of the member-facing categories. Each row is shown per-recipient: a capability row only to a holder of that capability, a role row only to that role (`_eligible_for`) — so what the page shows equals what the send path delivers. Plain members never see the section; full admins get a "Manage your admin duties" link to their own capability checkboxes.

**Meeting-item proposal decide is NOT capability-gated:** `hub_meeting_proposal_decide` runs through `can_edit_meeting` (full meeting-workspace edit), so an `EVENTS_APPROVER` who isn't also a guild lead/staff/admin is notified but can't act there — left as-is because widening `can_edit_meeting` would over-grant guild-page/class authority.

## Key QuerySet Methods

- `Member.objects.active()` — status=ACTIVE
- `Member.objects.paying()` — member_type=STANDARD
- `Member.objects.with_lease_totals()` — annotates active_lease_count, total_monthly_rent
- `Space.objects.available()` — status=AVAILABLE
- `Space.objects.with_revenue()` — annotates active_lease_rent_total
- `Lease.objects.active(as_of=date)` — start_date≤date and (end_date null or ≥date)

## Vote Calculator

`membership/vote_calculator.py` — `calculate_results(votes, paying_voter_count, pool_override)` returns a dict with per-guild allocations. Called by `FundingSnapshot.take()`.

`membership/cycle.py` — `get_cycle_context()` returns current cycle label/dates for template display.

## Signals

`membership/signals.py` — post-save signal on User to sync permissions when user is updated outside of fog_role flow. Prefer calling `member.sync_user_permissions()` directly.

## Factories

`tests/membership/factories.py` — `MemberFactory`, `GuildFactory`, `LeaseFactory`, `MembershipPlanFactory`, etc.
