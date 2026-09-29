# #524 Notification settings: Admin / Permissions, per-channel switches, every role email obeys the page

Spec and implementation plan for [#524](https://github.com/Past-Lives-Makerspace/plfog/issues/524). The ticket's
acceptance criteria are the rubric; this file says how the build meets them. It is deleted in the PR's final
commit (`docs/plans/README.md`).

Decided with Felix on 2026-09-29: the Email switch must really stop role emails, so the fixed address lists
go ("no more custom systems"); "app" in the bulk controls means Push, and the bell stays always on. The
mockup gates the PR: it stays a draft until Felix approves the visual.

## Spec

### 1. Page order

1. **Admin / Permissions**, when the viewer has at least one row in it.
2. The member topics in `CATEGORY_ORDER` (`core/events/settings_matrix.py:63`), unknown categories after
   them alphabetically.

There is no tail section any more. `ALWAYS_EMAILED_SECTION`, `_is_always_sent` and the template branch that
renders it are deleted.

### 2. Which section a row lands in

`_section_for(event)` keeps one rule: a recipient in `STAFF_RECIPIENTS` goes to Admin / Permissions,
everything else goes to `event.category`. A forced email no longer moves a row anywhere; it only locks the
Email cell, as it already does.

The 13 rows the Always emailed block held therefore return to their own category:

| Section | Rows |
|---|---|
| Classes | Class cancelled, Refund issued |
| Billing | Tab entry added, Tab approaching limit, Late cancellation fee paid, Late cancellation fee waived |
| Spaces & Equipment | Space agreement ending soon, Reservation confirmed, Reservation cancelled by a manager, Reservation cancelled |
| Membership | You're invited to Past Lives, Sign in to Past Lives for the first time |
| Guilds | Your Past Lives guilds are set up |

`refund_failed` has a forced email and a staff recipient; it stays in Admin / Permissions with a padlocked
Email cell.

### 3. Groups inside Admin / Permissions

Rows are grouped by the permission that brings them. Group order, and how the viewer's standing
(`_StaffProfile`) says they hold it:

| # | Group heading | Held when |
|---|---|---|
| 1 | Admin | `is_admin` |
| 2 | CMS Administrator | `class_approver` capability |
| 3 | Billing Administrator | `billing_approver` |
| 4 | Refunds | `refunds` |
| 5 | Calendar Administrator | `events_approver` |
| 6 | Space & Cubby Administrator | `space_approver` |
| 7 | Discount Code Administrator | `discount_approver` |
| 8 | Equipment Administrator | `equipment` |
| 9 | Guild leadership | leads a guild, holds any guild staff role, or is a guild officer |
| 10 | Equipment manager | `manages_equipment` |

The capability headings come from `AdminCapability.Capability` labels, so the page and the Permissions tab
use the same words. Each recipient lists the groups it can reach through; the row goes under the **first**
of those the viewer holds:

| Recipient | Candidate groups, in order |
|---|---|
| `FOG_ADMINS` | Admin |
| `REFUND_AUTHORITY` | Admin, Refunds |
| `GUILD_LEADERSHIP_OR_ADMINS` | Admin, Guild leadership |
| `WIKI_SCOPE_LEADERSHIP` | Admin, Guild leadership |
| `CLASS_APPROVERS` | CMS Administrator |
| `GUILD_LEADERSHIP_OR_CLASS_APPROVERS` | CMS Administrator, Guild leadership |
| `BILLING_APPROVERS` | Billing Administrator |
| `EVENTS_APPROVERS` | Calendar Administrator |
| `GUILD_LEADERSHIP_OR_EVENTS_APPROVERS` | Calendar Administrator, Guild leadership |
| `SPACE_APPROVERS` | Space & Cubby Administrator |
| `DISCOUNT_APPROVERS` | Discount Code Administrator |
| `EQUIPMENT_MANAGERS` | Equipment Administrator, Guild leadership, Equipment manager |
| `GUILD_ORIENTERS_OR_EQUIPMENT_MANAGERS` | Equipment Administrator, Guild leadership, Equipment manager |
| `GUILD_LEADERSHIP`, `GUILD_LEAD`, `GUILD_ORIENTERS`, `ALL_GUILD_LEADS` | Guild leadership |

Visibility does not change: a row still shows only when `_eligible_for` is true, so page equals delivery. A
row that passes `_eligible_for` but matches no candidate group is a programming error and raises (a spec
walks every staff recipient). A group with no rows does not render. The per-row capability badge
(`_capability_badges`, `Row.badge`) is removed: the group heading now says the same thing once.

The existing section note and "Manage your admin duties" link (`_notification_matrix.html:65`) move to the top
of Admin / Permissions, wording unchanged except the section name.

Preview as Member or Guest (`hub/views.py:3252`) still drops the whole section and still skips its rows on
save.

### 4. The class review row

`core/triggers.py:86`:

- label **Class review request** (matches the email subject "Review request: …")
- description **An instructor submitted a class for review. Guild leadership reviews their own guild's
  classes; CMS Administrators review classes with no guild lead.**

The label also changes on the Emails tab and in the admin copy catalogue, which read the same registry.

### 5. Per-channel bulk controls

Scopes: the whole page, each section, and each group inside Admin / Permissions.

Each scope offers All on / All off (as today) plus on and off for **Email**, **Push** and **Discord**. A
channel control renders only when the scope holds at least one editable cell of that channel: a cell that is
present, not locked (forced or the bell) and available (Discord linked). The bell has no control.

Server side, `build_matrix` computes the editable channels per scope so the template never works it out.
Client side, every checkbox carries `data-channel="<channel value>"`, and `plNotifBulk(scope, on, channel)`
narrows its selector when `channel` is given; the existing `:not(:disabled)` guard keeps locked and
unavailable cells untouched, and the existing bubbling `change` keeps the dirty guard honest. Nothing is
saved until Save.

Each control is a `<button type="button">` whose `aria-label` reads "Turn on Email for Billing", "Turn off
Push for everything", and so on. The visual form (chips per column header, a toolbar per section, or a
sticky page bar) is whatever the approved mockup shows.

At 390px wide the page must not scroll sideways (`tests/e2e/mobile_no_horizontal_overflow_spec.py`).

### 6. Returned data shape

`build_matrix` returns a list of sections instead of `(category, rows)` tuples:

```python
@dataclass(frozen=True)
class MatrixBlock:
    heading: str                      # "" for a member topic; the group name inside Admin / Permissions
    rows: list[Row]
    editable_channels: list[Channel]  # drives this block's bulk controls


@dataclass(frozen=True)
class MatrixSection:
    title: str                        # "Admin / Permissions" or the category
    slug: str                         # the jump chip anchor
    blocks: list[MatrixBlock]         # one untitled block for a member topic
    editable_channels: list[Channel]  # union of its blocks
    is_admin: bool
```

Names are a recommendation. `RowGroup` (the sibling-family collapse) keeps its name and meaning; blocks are a
different thing and must not reuse it. The three host views pass the result straight through; the page-wide
editable channels come from the union of all sections.

### 7. No custom systems: role and admin emails through the event system

Every conversion keeps the email's subject and body. Delivery moves from a fixed address list to the event's
resolver, so each person's Email switch decides, and the email goes to their notification address
(`notification_email_for`). A member with no login is skipped. One email per person replaces one email with
many addresses in To.

| Email | Today | After |
|---|---|---|
| Review request, guild-led class | `email_to=_guild_leadership_recipients(guild)` (`classes/emails.py:345`, `:427`) | no `email_to`; `class_review_requested`'s resolver (guild leadership) sends it |
| Review request, reminder | same list, `None` when empty (`classes/emails.py:350`) | same emit; returns `None` when `guild.leadership_members()` is empty |
| Orientation request | `email_to` from `_request_audience` (`membership/orientations.py:964`) | no `email_to`; resolver below |
| Admin copy of a class registration | rides `instructor_new_registration` with `email_to=_admin_recipients()` (`classes/emails.py:253`) | new event `class_registration_admin_notice` |
| Duplicate payment | `core_email.send(to=_admin_recipients())` (`classes/emails.py:850`) | new event `classes.duplicate_payment_alert` |
| Payment needs a decision | same (`classes/emails.py:922`) | new event `classes.orphaned_payment_alert` |
| Orphaned late fee payment | `core_email.send` to Billing Administrators (`billing/webhook_handlers.py:159`) | new event `billing.late_fee_orphan_payment` |
| Orphaned orientation payment | same (`membership/webhook_handlers.py:32`) | new event `membership.orientation_orphan_payment` |

The four alert keys reuse their current `trigger_kind` strings, so `TransactionalEmailLog` history reads as
one series.

**New events** (`core/events/registry.py`, `_NEW_EVENTS`):

| Key | Label | Category | Recipient | Channels |
|---|---|---|---|---|
| `class_registration_admin_notice` | New class registration | Classes | `FOG_ADMINS` | Email on |
| `classes.duplicate_payment_alert` | Duplicate class payment | Classes | `FOG_ADMINS` | Email forced |
| `classes.orphaned_payment_alert` | Class payment needs a decision | Classes | `FOG_ADMINS` | Email forced |
| `billing.late_fee_orphan_payment` | Orphaned late fee payment | Billing | `BILLING_APPROVERS` | Email forced |
| `membership.orientation_orphan_payment` | Orphaned orientation payment | Billing | `BILLING_APPROVERS` | Email forced |

None declares In-app, so `_with_push` adds no Push and they carry no bell, exactly as today. `activity_kind`
is `None` on all five. Each needs its email gallery entry (`tests/e2e/email_gallery/registry.py`) and passes the
copy completeness specs through the uncurated default.

**Dedupe trap.** `emit` claims one ledger slot per `(event, user, channel, period)`. The alerts have no period
today; given `""`, the second duplicate payment of the day would be swallowed as a repeat. Each alert passes
a period keyed on what makes it unique: the Checkout session id for the three webhook alerts and the payment
intent plus registration for the two class alerts. The registration notice keeps `reg:{pk}:admin_notice`.

**Orientation request audience.** The composed resolver
(`resolvers.guild_orienters_or_equipment_managers`, `core/events/resolvers.py:314`) becomes the one audience:
for a guild-owned shared slot it returns the guild's whole leadership (today's email audience) instead of
the lead plus orienters; a personal slot and every equipment case already match `_request_audience` and stay
as they are. `_request_audience` is deleted or becomes the resolver's body; `guild_orienters` itself is not
changed, because other callers use it. Co-leads, secretaries and treasurers gain the bell for shared-slot
requests.

**Deleted:** `classes.emails._admin_recipients`, and `_guild_leadership_recipients` as an email builder.
`classes/views.py:3664` asks `guild.leadership_members()` for its "leadless" flag instead.

**Unchanged:** the instructor's own "your class is in review" explainer and every email to the person who
acted (welcome, resume link, find account, messages). `CLASS_ADMIN_NOTIFY_EMAILS` stays inside the
`fog_admins` resolver, where it reaches only addresses that have an account.

### 8. Copy

Intro paragraph (`templates/hub/_notifications_settings.html:13`):

> Choose how each notice reaches you. The bell always shows everything. A padlock means that notice is always
> emailed, because missing it would cause real problems: sign-in links and invitations, a cancelled class,
> refunds, charges and tab limits, a space agreement ending, equipment reservations, late fees and Discord
> setup.

Help Center (`membership/help_content.py:250`), replacing the Always emailed sentences:

> A few notices are locked on, because missing them would cause real problems: sign-in links and invitations,
> a class being cancelled, refunds, charges and tab limit warnings, a space agreement ending, equipment
> reservations, late fees and Discord setup. Their Email switch shows a padlock. You can still change push and
> Discord for the ones that offer them.
>
> If you hold a role or an admin permission, the notices it brings you sit together in **Admin /
> Permissions** at the top of the page, grouped by the permission. Every section has buttons to turn all
> Email, all Push or all Discord on or off at once. Nothing changes until you press Save.

## Implementation plan

Branch `notif-settings-admin-section`, worktree `~/Code/plfog-wt/notif-settings`. Targeted runs only:
`DATABASE_URL=sqlite:///tmp-check.sqlite3 .venv/bin/pytest <paths> -q --no-cov`, read the pass/fail line,
never a piped exit code.

### Step 1: mockup (runs alongside steps 2 and 3)

A designer produces `mockups/524-notification-settings.html`, a static page that links the real stylesheets
and uses the real classes (`.hub-card`, `.pl-notif-*`, `.pl-toggle`, the padlock), showing:

1. an admin's page top: jump chips, page-wide bulk controls, Admin / Permissions with three groups (Admin, CMS
   Administrator with Class review request, Guild leadership)
2. a member topic with a padlocked row (Billing)
3. a plain member's page top, with no Admin / Permissions
4. the same at 390px wide

Rendered to `mockups/screenshots/524-mockup-*.png` and shown to Felix. Step 4's markup follows the approved
version.

### Step 2: the registry and the send paths

1. `core/triggers.py`: the class review label and description (§4).
2. `core/events/registry.py`: the five new events (§7).
3. `classes/emails.py`: review request and reminder without `email_to`; the admin registration notice on its
   own key; the two class alerts through `emit` with a message override and a period; delete
   `_admin_recipients`.
4. `membership/orientations.py` and `core/events/resolvers.py`: one orientation request audience, no
   `email_to`.
5. `billing/webhook_handlers.py`, `membership/webhook_handlers.py`: the two orphan alerts through `emit`.
6. `classes/views.py:3664`: the leadless flag.
7. `tests/e2e/email_gallery/registry.py`: gallery entries for the five keys.

Specs, next to the existing ones for each sender (`classes/spec/emails_review_spec.py` and siblings,
`tests/core/events/resolvers_spec.py`, the billing and membership webhook specs):

- a guild lead with Email off for `class_review_requested` gets no review email, gets the bell; with it on,
  gets the email at their notification address
- the same for a CMS Administrator on a lead-less class, and for the reminder
- orientation request, guild shared slot: every leadership member gets the bell, and the email unless they
  switched it off; personal slot and equipment audiences unchanged
- an Admin with Email off for `class_registration_admin_notice` gets no admin copy; the instructor's own
  notice is unaffected
- each alert reaches Admins or Billing Administrators even with every switch off (forced), and two alerts for
  different sessions both send
- the five events are registered with the audience and channels in §7

### Step 3: the settings matrix service

`core/events/settings_matrix.py`:

1. delete `ALWAYS_EMAILED_SECTION` and `_is_always_sent`; `_section_for` keeps the staff rule only
2. `STAFF_SECTION` becomes `ADMIN_SECTION = "Admin / Permissions"`, ordered first
3. group table and first-held-group rule (§3), computed from one `_StaffProfile` per render
4. `MatrixSection` / `MatrixBlock` with editable channels (§6); drop `_capability_badges` and `Row.badge`
5. `save_matrix` unchanged in behavior: every rendered cell is still posted, and the preview-as-Member flag
   still skips the admin rows

`tests/core/events/settings_matrix_spec.py`: rewrite the Always emailed and Staff & leadership pins as:

- the 13 former Always emailed rows land in the sections in §2, Email cell locked
- every staff-recipient event lands in Admin / Permissions and nowhere else; no member event lands there
- group order; first-held-group dedupe (a lead who is also a CMS Administrator sees Class review request once,
  under CMS Administrator); no empty group; a plain member gets no admin section
- every staff recipient has a candidate group (walks `STAFF_RECIPIENTS`)
- editable channels: a scope whose Email cells are all padlocked offers no Email control; an unlinked member's
  scopes offer no Discord control
- saving the page leaves Push and Discord choices on padlocked rows unchanged

### Step 4: the page

1. `templates/hub/partials/_notification_matrix.html`: sections and blocks, bulk controls per the approved
   mockup, `data-channel` on every checkbox, `plNotifBulk(scope, on, channel)`; the Always emailed markup and
   its comment go.
2. `templates/hub/_notifications_settings.html`: intro copy (§8); jump chips from `section.slug`.
3. The CSS the mockup needs, in the stylesheet that already holds `.pl-notif-*`; tokens only.
4. `membership/help_content.py:250`: the Help Center copy (§8).
5. The admin member-edit Permissions tab and the token page pick up the partial unchanged; check both render.

Specs:

- `tests/hub/notification_settings_spec.py`: the three surfaces render Admin / Permissions for an admin, not
  for a member; no "Always emailed" text anywhere; each bulk button's `aria-label`
- a new e2e spec: an admin presses "Turn off Email for everything", saves, and every unlocked Email preference
  is off while padlocked cells, Push and Discord are untouched; the same for one section; the unsaved-changes
  guard fires
- `tests/e2e/mobile_no_horizontal_overflow_spec.py`: drop the Always emailed step, keep the 390px check over
  the new page

### Step 5: handover

1. One fragment in `changelog.d/` per `changelog.d/README.md`, member facing.
2. Screenshots under `mockups/screenshots/524-*.png` from a throwaway e2e spec on the test database (the dev
   database is behind main), embedded with branch-pinned URLs.
3. Push, open the PR as a **draft** with `Closes #524`, body from `.github/pull_request_template.md`, checked with
   `check_pr_description` before creating.
4. Delete this plan in the final commit.
5. Stop. The PR is marked ready only after Felix approves the visual.

## Risks

- **People stop getting emails they got before.** Anyone with Email off on `class_review_requested` or
  `orientation_requested` (including everyone who ever pressed All off), and guild leadership members with no
  login. The production counts are named on #524; take them before merge.
- **`CLASS_ADMIN_NOTIFY_EMAILS` addresses without an account** stop getting the five admin emails. Read the
  Render value before merge.
- **Save wipe.** Any section that renders collapsed or hidden must still submit its inputs.
- **Alert dedupe.** A missing or shared period silently drops the second alert (§7).
