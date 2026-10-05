# Bookings tab on Orientations and Reservations

Date: 2026-10-05. Design spec, no code yet. Two features, two PRs: **A (Orientations) ships first**, B (Reservations) second and reuses A's pieces.

## 1. Problem

Felix: "Add a tab to Orientations and Reservations that shows a history/list admin view of recent Orientations/Reservations so we can look at these as a whole, like we can for Classes. Give an actions button (...) that can do things like refund users, mark users as oriented, and other stuff that makes sense." Then: "It actually looks like it exists already for Orientations, so just move it to a Tab. Members can view, but only guild officer/staff/admins can do things like refunds and extra stuff. Then do the same for Reservations."

What is there today:

- **Orientations** has a staff dashboard at `/orientations/manage/` (`hub/views.py:2658` `orientations_dashboard`, `templates/hub/orientations_dashboard.html`). It is a separate page reached from the page header's "Manage orientations" button (`templates/hub/orientations.html:11-16`) and the Admin Tools card (`templates/hub/admin_tools.html:72`). Its only row action is a Mark done button (`orientations_dashboard.html:110`); confirm, decline, cancel and refunds live one click away on the respond page (`hub/views.py:2107`, `templates/hub/orientation_respond.html`). Members have no list of their own bookings at all; they see only per card state on the List pane.
- **Reservations** has no cross equipment list. Each piece of equipment's manage page has an Upcoming Reservations list with a reason required Cancel (`templates/hub/equipment_manage.html:473-490`, `hub/equipment_views.py:934`), and a Late Cancellation Fees card with Waive (`equipment_manage.html:493-518`). A member sees their own reservations only on each equipment's schedule (`hub/equipment_views.py:264-277`).

Two defects in the current dashboard the move must fix, not carry over:

1. **It is not scoped.** The gate is "any guild lead or staff" (`hub/views.py:2609-2615`), but the table's base queryset is every booking (`hub/views.py:2667-2683`), the Upcoming panel is every booking (`hub/views.py:2711-2718`), the recorded list is every record (`hub/views.py:2690-2701`) and the CSV export is every booking (`hub/views.py:2775-2784`). A lead of Ceramics sees and exports Woodshop's bookings, with member names. "Mine" is only an opt-in filter (`hub/views.py:2638-2639`).
2. **Sort is unguarded.** `prepare_table` is called without `sortable` (`hub/views.py:2677-2683`), and `prepare_table` passes an unknown `?sort=` straight to `order_by`, which raises (`classes/table.py:30-46`). A crafted URL is a 500.

## 2. Locked decisions (from the brief, not reopened)

- A member who is not a lead, staff or admin sees **only their own** bookings in the tab, past and upcoming, with the self service actions that already exist. Never another member's name or row.
- Guild leads and guild staff see bookings in their scope (their guilds' orientations; equipment they manage); admins see everything. They get the "..." row actions.
- Refunds stay gated by `hub.view_as.has_refund_authority` (`hub/view_as.py:318`). No permission is widened.

## 3. The tab

### Name: **Bookings**

| Candidate | Why not |
|---|---|
| History | Reads as past only. The tab's default is upcoming, and staff use it to act on upcoming requests. |
| Manage | Wrong for members, who see the same tab with their own rows. |
| Mine / All | Two names for one component; the locked decision is one tab whose rows depend on who looks. |
| Reservations (on the Reservations page) | Repeats the page title. |
| **Bookings** | The app already says "book" on both pages: the Reservations header reads "Book a room, a space, or a tool" (`templates/hub/equipment_index.html:12`), the orientation model is `OrientationBooking`, and member copy says "when you book" (`templates/hub/orientation_info.html:16`). A member reads it as "my bookings", a lead as "the bookings". Same word on both pages, so one engineer builds one pattern twice. |

No new domain term is coined: "Bookings" is a label over existing `OrientationBooking` and `EquipmentReservation` rows.

### Position and URL

`List | Calendar | Bookings`, third in the existing `pl-tabs` bar (`templates/hub/orientations.html:24-27`, `templates/hub/equipment_index.html:18-21`), same `vote-tab` buttons. `?view=bookings` opens it, as `?view=calendar` opens the calendar today. List stays the default pane for everyone.

### One component, scope decides the rows

The pane is one template partial per page. The view computes a **viewer scope** once and the partial branches only on `is_staff_view` (columns and filters) and per row `row.can_manage` (which menu items show):

- `is_staff_view` is true when the viewer manages anything in this feature's scope (Orientations: any guild via `can_manage_orientations`, or any equipment via `manageable_equipment_ids`; Reservations: any equipment via `manageable_equipment_ids`).
- Staff rows = **managed scope OR the viewer's own bookings**. The union matters: a lead who also books orientations elsewhere should not lose their own rows by becoming a lead. Own rows on unmanaged items get the member menu, not the staff menu.
- Member rows = the viewer's own bookings only.

### Lazy load

The Calendar pane is built only when opened, so the List view costs no calendar queries (`static/js/list_calendar.js:1-15`, `hub/orientations_views.py:139-144`). The Bookings pane follows the same rule:

- Generalise `plListCalendar` (`static/js/list_calendar.js:37-52`) so `setPane(pane)` writes `?view=<pane>` for any non list pane and lazy loads any pane placeholder carrying `data-pane-src`, not only `calendarLazy`. Keep the component name; the docstring gains the third pane.
- New GET partial per page returning just the pane, honoring the same query string: `hub_orientations_bookings` at `/orientations/bookings/` and `hub_equipment_bookings` at `/equipment/bookings/` (above `<slug>/` in `hub/urls.py:367-374`, like `add/`).
- When the page loads with `?view=bookings` the view renders the pane inline (no extra request), as `pane == "calendar"` does today.
- Filters, chips, sort headers and pagination inside the pane are ordinary links and GET forms to the **page** URL with `view=bookings` kept in `base_params`, so boosted navigation re-renders the page with the pane open and the URL is shareable.
- The pane wrapper carries `hx-get="<partial url>?<current query>" hx-trigger="refund-done from:body"` so a refund refreshes the rows, the exact pattern of `templates/classes/teach/class_registrations.html:6-10`.

## 4. Feature A: Orientations > Bookings

### 4.1 Acceptance criteria

1. `/orientations/` shows a third tab, Bookings, for every logged in member; `?view=bookings` opens it.
2. A member who manages nothing sees only their own `OrientationBooking` rows and their own `OrientationRecord` rows, past and upcoming. No member column, no other member's name anywhere in the pane, the export link absent.
3. A guild lead or guild staff member sees bookings for their guilds' orientation types, bookings for equipment owned types they manage, and their own bookings. Nothing else, including in the CSV export and the recorded list.
4. An admin or guild officer sees every guild owned booking (officers already pass `can_manage_orientations` for every guild, `membership/permissions.py:55-70`); an admin also sees every equipment owned booking.
5. Each row has a "..." menu (`components/row_actions.html`) whose items follow section 4.4 exactly; an item never shows when its endpoint would refuse the viewer.
6. Refund and Retry Refund appear only when `has_refund_authority` is true, the booking was paid, and there is something to refund or retry.
7. `/orientations/manage/` redirects (302) to `/orientations/?view=bookings`, carrying its query string. The Admin Tools card and the help tour point at the new URL.
8. Mark Oriented, Undo, Confirm, Decline, Cancel, Waive and Add Member all land back on the Bookings tab with the filters the viewer had, not on the respond page or the old dashboard.
9. A guild X lead POSTing to any booking action for a guild Y booking gets 403 (already true via `_require_can_manage_booking`, `hub/views.py:896-909`; pinned by a test).
10. An unknown `?sort=` falls back to the default, never a 500.
11. At 375px wide the table stacks into labelled rows, the "..." stays reachable, and the page has no sideways scroll.
12. The List view still runs no Bookings queries.

### 4.2 Scope helper (one place, tested for parity)

New `membership/permissions.py` function, next to `manageable_equipment_ids` (`membership/permissions.py:134`):

```python
def manageable_orientation_bookings(request, queryset) -> QuerySet:
    """queryset narrowed to bookings this request may run: the queryset form of _require_can_manage_booking."""
```

- `is_effective_staff(request)` (admin or officer, view_as aware, `membership/permissions.py:30-37`): every guild owned booking.
- Admin (view_as) or the EQUIPMENT capability holder (actual member): every equipment owned booking, mirroring `manageable_equipment_ids` (`membership/permissions.py:156-161`).
- Otherwise, for the editing member `m`: `Q(guild__guild_lead=m) | Q(guild__staff_memberships__member=m) | Q(orientation_type__equipment__guild__guild_lead=m) | Q(orientation_type__equipment__guild__staff_memberships__member=m) | Q(orientation_type__equipment__staff_memberships__member=m)`, then `.distinct()`.

A parity spec builds bookings across two guilds and two pieces of equipment and asserts, for each viewer role, that a booking is in `manageable_orientation_bookings` **if and only if** `_require_can_manage_booking` returns None. This is the guard against list scope and action scope drifting apart. The same helper scopes the table, the upcoming default, the export, and (by guild) the recorded list.

`_can_access_orientations` (`hub/views.py:2609`) is retired in favour of "the scope is non empty" (`is_staff_view`). Note this lets a pure equipment manager into the staff view for their equipment's orientations, which they can already act on through the respond page; it widens no action.

### 4.3 Table

Reuse `classes.table.prepare_table` (`classes/table.py:22`), the `sort_header` tag and `components/table_pagination.html` as the dashboard does (`orientations_dashboard.html:74-118`), now with `sortable=` set. Table markup uses `.pl-members-table` so it stacks below 768px (`static/css/hub.css:1613-1660`), each cell with a `data-label`, as Announcements does (`templates/hub/announcements.html:54-63`). New rules go in `static/css/orientations.css` (FRONTEND.md CSS table), no inline styles (FRONTEND.md rule 9).

**Staff columns**

| Column | Content | Sort key |
|---|---|---|
| When | `slot.starts_at` as "Tue Oct 7, 6:00 PM" | `slot__starts_at` |
| Member | display name; own row reads "You" | `member__full_legal_name` |
| Orientation | type name, owner name muted under it | `orientation_type__name` |
| Run By | `slot.orienter.display_name`, else "Any orienter" (the respond page's words, `orientation_respond.html:17`) | none |
| Status | `get_status_display` pill, plus an "Oriented" pill when `is_completed`, plus a fee pill when the booking's late fee is unpaid | `status` |
| Paid | amount, "Refunded", or a red "Refund failed" (the dashboard's cell, `orientations_dashboard.html:94-104`) | none |
| (menu) | `components/row_actions.html` | none |

`Oriented by` drops from its own column into the View Request page and the CSV; it is a detail, not a scanning column.

**Member columns:** When, Orientation, Status (includes Paid / Refunded inline), menu. No Member, Run By keeps only on the respond page.

**Default sort and chips.** A chip row above the table, using the page's existing chip style (`pl-equip-chip`, `templates/hub/orientations.html:32-39`):

- **Upcoming** (default): live statuses, `slot__starts_at >= now`, ascending. This absorbs the dashboard's Upcoming panel (`orientations_dashboard.html:23-43`), which duplicated the table's first page.
- **Needs a Reply (n)**: staff only, `status=requested`, ascending, with the count. Hidden when n is 0.
- **Past**: `slot__starts_at < now`, every status, descending. This is the "recent history" Felix asked for.
- **All**: descending.

**Filters (staff).** The dashboard's filters, kept and cleaned (`hub/views.py:2632-2655`): Guild (only guilds in scope, not every active guild as today, `hub/views.py:2757`), Status, Oriented (Any / Yes / No, the old "Completed"), From and To dates. The "Scope: Mine" select goes: scope is now the default. Controls sit in `.hub-form-group` so they take theme tokens (FRONTEND.md rule 13), not the inline `style=` the dashboard uses today (`orientations_dashboard.html:45-69`). On phones the filters fold into a `pl-disclosure` titled "Filters" (FRONTEND.md, Disclosure); search and chips stay visible.

**Search.** "Search member or orientation", over `member__full_legal_name`, `member__preferred_name`, `orientation_type__name`, `guild__name`. Members get no search (their list is short).

**Pagination.** 25 per page (`classes/table.py:9`).

**Empty states.**
- Member, Upcoming: "You have no orientations coming up. Pick one under List to get started." with List as a link that calls `setPane('list')`.
- Member, Past: "Nothing here yet. Orientations you have been to show up here."
- Staff, filtered: "Nothing matches those filters. Clear filters" (the page's own line, `templates/hub/orientations.html:52`).
- Staff, Upcoming, unfiltered: "No orientations are booked yet."

**Recorded orientations** (`OrientationRecord`, #465): staff see the existing "Recorded by an Admin" table under the bookings table when Oriented = Yes or the Past chip is on (today: Completed = Yes only, `hub/views.py:2690`), now scoped to guilds in scope. Members see their own records as a short "Recorded by an Admin" list under their table when any exist. No menu on these rows: there is nothing to act on.

### 4.4 Row menu: Orientations

Rendered with `{% include "components/row_actions.html" with menu_include="hub/partials/orientation_booking_row_menu.html" menu_label="Actions for "|add:name %}`, items built like `templates/classes/partials/registration_row_menu.html`: `pl-row-menu__item`, `--danger` for destructive, dividers only between non empty groups, `closeAndRefocus()` on click. Destructive and consequential items open the house `components/confirm_modal.html` (`$dispatch('open-confirm', id)`); no browser `confirm()`. Every POST carries `next` = the current page path with query, so it returns to the tab. Confirm modals render once per row, outside the table, as the manage page does (`equipment_manage.html:519-525`).

`can_manage` per row = booking in the viewer's managed scope (precomputed id set, no per row query). `Name` below is the member's display name.

**Staff menu (row.can_manage)**

| Group | Item | Shows when | Calls | Confirm copy |
|---|---|---|---|---|
| Look | View Request | always | GET `hub_orientation_respond` | none |
| Look | Email Member | admin, or the member shows email in the directory (`member\|is_public:"email"`, `hub/templatetags/hub_tags.py:82`) | `mailto:` `member.primary_email` | none |
| Look | View Member | admin only | GET `hub_admin_member_edit` `?tab=orientations` | none |
| Reply | Confirm Request | `status == requested` | POST `hub_orientation_respond` `action=confirm` | Title "Confirm This Orientation?" Body "We'll email {Name} that their time is set." Button "Confirm" (primary style) |
| Reply | Decline Request | `status == requested` | POST `hub_orientation_respond` `action=decline`, optional `note` via `confirm_note_name="note"` | Title "Decline This Request?" Body "We'll email {Name} and include your note." Paid: add "Their {amount} is refunded automatically." Note label "Note (optional)", placeholder "Suggest another time" |
| Oriented | Mark Oriented | `status == confirmed` and not `is_completed` | POST `hub_orientation_toggle_completed` `completed=1` | Title "Mark {Name} as Oriented?" Body "This counts as their orientation, so they can book what it unlocks." Button "Mark Oriented" (primary) |
| Oriented | Undo Oriented | `is_completed` | POST same, `completed=0` | Title "Undo Oriented?" Body "{Name} will need this orientation again before they can book what it unlocks." Button "Undo" |
| Money | Refund | `has_refund_authority` and `amount_paid_cents` and `refundable_cents > 0` and `refund_state != failed` | GET `billing_orientation_refund_form` into `#refund-modal-body`, then the form's own POST `billing_orientation_refund` (`billing/views.py:488-536`) | The existing refund modal (amount and reason) |
| Money | Retry Refund | `has_refund_authority` and `refund_state == failed` | same form, which renders its Retry state (`billing/views.py:469-485`) | existing |
| Money | Waive Late Fee | `booking.late_fee` unpaid and `late_fees.can_waive` (`billing/late_fees.py:316`) | opens `waive-fee-{pk}` modal, POST `hub_late_fee_waive` with `next` | the existing waive form, reason required (`hub/partials/_late_fee_waive_form.html`) |
| Money | Refund Late Fee | `has_refund_authority` and `booking.late_fee` paid with something refundable | GET `billing_late_fee_refund_form` into `#refund-modal-body` | existing |
| Stop | Cancel Orientation | `status == confirmed` and `slot.starts_at >= now` | POST `hub_orientation_lead_cancel` | Title "Cancel This Orientation?" Body "We'll email {Name} to let them know." Paid: "Their {amount} is refunded automatically. We'll email {Name} to let them know." Button "Cancel Orientation" (danger). Same as `orientation_respond.html:76-88`, minus the dash. |

`pending_payment` rows (a member mid Checkout) get View Request only: `confirm`, `decline` and `cancel` refuse a hold (`OrientationBooking._refuse_checkout_hold`, `membership/models.py` near 12973).

Why these and no more: Confirm and Decline earn a place because the dashboard's Upcoming panel existed mostly to send leads to Respond (`orientations_dashboard.html:38`); now they are one click. Waive Late Fee because the respond page already offers it for orientation fees (`orientation_respond.html:38-56`). "Move to another time" and "Resend email" were considered and dropped: no backend exists for either and nobody asked.

**Member menu (own row, not managed)**

| Item | Shows when | Calls | Confirm copy |
|---|---|---|---|
| View Orientation | always | GET `orientation_type.owner_page_path` with the card anchor | none |
| Finish Paying | `status == pending_payment` | POST `hub_orientation_checkout_resume` | none (goes to Stripe; form `hx-boost="false"`) |
| Release This Spot | `status == pending_payment` | POST `hub_orientation_checkout_cancel_hold` | Title "Release This Spot?" Body "You haven't paid yet, so nothing is charged. You can book again any time." |
| Cancel Orientation | `status in (requested, confirmed)` and upcoming | POST `hub_orientation_cancel_mine` with `next` (already honoured, `hub/views.py:878-893`), `confirm_no_boost=True` because a late cancel redirects to Stripe | The card's existing copy and late line (`templates/hub/partials/_orientation_type_state.html:58`): "You'll get an automatic full refund. You can request a new orientation any time." plus `late_cancel_warning` when it applies |
| Pay Late Fee | `booking.late_fee` unpaid | GET `hub_late_fee_detail` | none |

The late warning per row reuses the rule in `hub/views.py:596-604` (`policy_for_type` then `cancel_sentence` while `policy.is_late`), moved into a small helper both callers share.

### 4.5 Endpoint changes (Feature A)

| Endpoint | Today | Change |
|---|---|---|
| `hub_orientations` (`hub/orientations_views.py:132`) | List and Calendar | Accept `view=bookings`; build the pane only then. Drop the header's "Manage orientations" action (`orientations.html:11-16`): the tab replaces it. |
| new `hub_orientations_bookings` | none | GET partial: the pane, for lazy load and the `refund-done` refresh. |
| `orientations_dashboard` (`hub/views.py:2658`) | the dashboard | `redirect(f"{reverse('hub_orientations')}?view=bookings&{request.GET.urlencode()}")`, 302 not 301 so a cached redirect never outlives a later move. URL name kept so stray `reverse()` calls still resolve. |
| `orientations_export` (`hub/views.py:2775`) | unscoped | Scoped by `manageable_orientation_bookings`, same filters and chip as the pane; 403 when the scope is empty. Path unchanged. |
| `orientation_add_member` (`hub/views.py:2788`) | redirects to dashboard | Redirects to `next` (safe local path) else the tab. Path unchanged, so the help tour's selector `form[action='/orientations/manage/add-member/']` still matches (`membership/help_content.py:1561`). |
| `orientation_toggle_completed` (`hub/views.py:2812`) | flips the flag, redirects to dashboard | Takes `completed=1|0` and sets that state (idempotent: two staff clicking Mark Oriented at once no longer cancel each other out). Redirects to safe `next` else the tab. Gate unchanged. |
| `orientation_respond` POST (`hub/views.py:2122-2136`) | redirects to respond page | Honour safe `next`; default unchanged. |
| `orientation_lead_cancel` (`hub/views.py:2176`) | redirects to respond page | Honour safe `next`; default unchanged. |

Safe `next` means `url_has_allowed_host_and_scheme`, the rule `_orientation_return` already uses (`hub/views.py:888-890`); factor it to one `_safe_next(request, default)` helper. Mutations here are full page POSTs with Django messages, matching the endpoints they reuse; no new HTMX endpoints. The refund modal is the exception and already returns a toast plus `refund-done` (`billing/views.py:530-536`).

### 4.6 Where the dashboard's extras land

| Extra | New home |
|---|---|
| Upcoming panel (`orientations_dashboard.html:23-43`) | The default Upcoming chip, plus the Needs a Reply chip. Removed as a separate panel. |
| Filters, search, sort, pagination | The pane's filter bar (section 4.3). |
| Export CSV (`orientations_dashboard.html:70`) | Staff only, right end of the filter bar, `hx-boost="false" data-pl-download` kept (FRONTEND.md rule 24). Exports the filtered, scoped rows. |
| Add a member to a slot (`orientations_dashboard.html:164-180`) | A "+ Add Member" button at the top right of the staff pane opens a `components/modal.html` holding the same form (two fields: FRONTEND.md says quick forms go in a modal). The paid guild note (`paid_slot_prices_json`) moves with it. |
| Hours nudge (`orientations_dashboard.html:10-21`) | A slim card at the top of the staff pane, unchanged copy and rule (`hub/views.py:2733-2741`). |
| Recorded orientations | Section 4.3. |
| `data-help-key="orientation.dashboard"` | On the pane wrapper. Update the two help screenshots that open `hub_orientations_dashboard` (`membership/help_content.py:1553-1561`) to `/orientations/?view=bookings`, and the Admin Tools card link (`templates/hub/admin_tools.html:72`). Help is seeded by hand after release. |
| `templates/hub/orientations_dashboard.html` | Deleted once the redirect lands. |

### 4.7 Wireframes: Orientations

Desktop, staff (lead of Woodshop):

```
Orientations                                                  
Get trained on a guild or a tool before you book it.          
                                                              
[ List ] [ Calendar ] [*Bookings*]                            
                                                              
+------------------------------------------------------------+
| You have not posted any orientation hours yet.             |
| Post your orientation hours                                |
+------------------------------------------------------------+
                                              [+ Add Member]  
(Upcoming) (Needs a Reply 3) (Past) (All)                     
[Search member or orientation    ] [Guild v] [Status v]       
[Oriented v] [From   ] [To   ] [Apply]          [Export CSV]  
                                                              
When v           Member      Orientation       Run By      Status                Paid    
Tue Oct 7 6 PM   Ana Ruiz    Shop Basics       Bob P.      (Requested)           $25     [...]
                             Woodshop                                                     
Tue Oct 7 6 PM   You         Lathe             Any orien.  (Confirmed)           -       [...]
                             Woodshop                                                     
Thu Oct 9 1 PM   Kai Moss    Shop Basics       Bob P.      (Confirmed)(Oriented) $25     [...]
                                                                                          
                                   < 1 2 3 >                                              
```

The open menu on Ana's row (requested, paid, viewer is a lead without refund authority):

```
                                    +----------------------+
                                    | View Request         |
                                    | Email Member         |
                                    |----------------------|
                                    | Confirm Request      |
                                    | Decline Request      |  <- red
                                    +----------------------+
```

Kai's row, viewer is an admin:

```
| View Request | Email Member | View Member |
|-------------------------------------------|
| Undo Oriented                             |
|-------------------------------------------|
| Refund                                    |  <- red
|-------------------------------------------|
| Cancel Orientation                        |  <- red
```

Desktop, member:

```
[ List ] [ Calendar ] [*Bookings*]                            
                                                              
(Upcoming) (Past) (All)                                       
                                                              
When v            Orientation             Status                   
Tue Oct 7 6 PM    Shop Basics · Woodshop  (Confirmed) Paid $25    [...]
Sat Oct 18 10 AM  Laser · Laser Cutter    (Pending payment)       [...]
                                                              
Recorded by an Admin                                          
Jun 2  Ceramics Basics · Ceramics                             
```

Mobile 375px, staff (rows stack via `.pl-members-table`; filters fold):

```
+-----------------------------------+
| Orientations                      |
| [List] [Calendar] [*Bookings*]    |
| [+ Add Member]                    |
| (Upcoming)(Needs a Reply 3)       |
| (Past)(All)                       |
| [Search member or orientation  ]  |
| > Filters                         |
|-----------------------------------|
| Tue Oct 7, 6:00 PM          [...] |
| MEMBER       Ana Ruiz             |
| ORIENTATION  Shop Basics          |
|              Woodshop             |
| RUN BY       Bob P.               |
| STATUS       (Requested)          |
| PAID         $25                  |
|-----------------------------------|
| Thu Oct 9, 1:00 PM          [...] |
| ...                               |
|            < 1 2 3 >              |
+-----------------------------------+
```

Mobile 375px, member:

```
+-----------------------------------+
| [List] [Calendar] [*Bookings*]    |
| (Upcoming)(Past)(All)             |
|-----------------------------------|
| Tue Oct 7, 6:00 PM          [...] |
| Shop Basics · Woodshop            |
| (Confirmed) Paid $25              |
|-----------------------------------|
| Sat Oct 18, 10:00 AM        [...] |
| Laser · Laser Cutter              |
| (Pending payment)                 |
+-----------------------------------+
```

The "..." trigger sits on the row's first line at the right on phones; the menu is `position: fixed` and flips up near the bottom (`components/row_actions.html:10-14`), so it escapes the stacked row.

## 5. Feature B: Reservations > Bookings

Same tab, same pane mechanics, same menu component. Built after A merges so it reuses `_safe_next`, the generalised `plListCalendar`, and the pane CSS.

### 5.1 Acceptance criteria

1. `/equipment/` (the Reservations page) shows Bookings as its third tab; `?view=bookings` opens it.
2. A member who manages no equipment sees only their own `EquipmentReservation` rows, past and upcoming, including ones they cancelled. No other member's name.
3. A viewer who manages equipment (admin, the EQUIPMENT capability, the owning guild's lead or staff, or an equipment staff row: `Member.led_or_staffed_equipment_ids`, `membership/models.py:1418-1444`) sees reservations on that equipment plus their own. Guild officers get no blanket grant here, matching `manageable_equipment_ids` (`membership/permissions.py:139-140`).
4. Staff can cancel a confirmed, not yet ended reservation from the menu with a required reason the member sees; the member is emailed (`EquipmentReservation.cancel`, `membership/models.py:14987`).
5. A member can cancel their own upcoming reservation from the menu; a late cancel still creates the fee and sends them to pay it.
6. Waive Late Fee and Refund Late Fee appear only where `can_waive` and `has_refund_authority` respectively allow.
7. A manager of equipment X POSTing a cancel for equipment Y's reservation gets 403 (already true, `hub/equipment_views.py:718-719`; pinned by a test).
8. 375px: stacked rows, no sideways scroll.
9. The List view runs no Bookings queries.

### 5.2 Scope helper

`manageable_reservations(request, queryset)` in `membership/permissions.py`: admin (view_as) or EQUIPMENT capability (actual member) gets all; else `Q(equipment__guild__guild_lead=m) | Q(equipment__guild__staff_memberships__member=m) | Q(equipment__staff_memberships__member=m)`, distinct. Parity spec against `can_manage_equipment` per reservation, as in 4.2.

### 5.3 Table

**Staff columns:** When ("Tue Oct 7, 6:00 PM to 8:00 PM", the manage page's format, `equipment_manage.html:480`; sort `starts_at`), Member ("You" for own rows; sort `member__full_legal_name`), Equipment (name, guild or "Standalone" muted under; sort `equipment__name`), Purpose (muted, truncated), Status (Confirmed / Cancelled; "Cancelled by a manager" when `is_cancelled_by_manager`; a fee pill when `late_fee` exists, read through `select_related("late_fee")`; sort `status`), menu.

**Member columns:** When, Equipment, Status, menu. Purpose shows under Equipment.

**Chips:** Upcoming (default: confirmed, `ends_at > now`, ascending, the queryset's own `upcoming()`, `membership/models.py:14900`), Past (`ends_at <= now` or cancelled, descending), All (descending).

**Filters (staff):** Equipment (only items in scope), Status (Confirmed / Cancelled), Late Fee (Any / Unpaid / Paid / Waived), From and To. Search "Search member or equipment" over member names and `equipment__name`. 25 per page. The page's own top filter chips (guild, kind, `equipment_index.html:26-50`) belong to the List pane and stay there.

**Empty states.**
- Member, Upcoming: "You have nothing booked. Pick something under List to book a time."
- Member, Past: "Nothing here yet. Times you have booked show up here."
- Staff, filtered: "Nothing matches those filters. Clear filters"
- Staff, unfiltered: "Nothing is booked yet."

No CSV export in B (section 6).

### 5.4 Row menu: Reservations

**Staff menu (row.can_manage)**

| Group | Item | Shows when | Calls | Confirm copy |
|---|---|---|---|---|
| Look | View Equipment | always | GET `hub_equipment_detail` | none |
| Look | Manage Equipment | always | GET `hub_equipment_manage` `?tab=reservations` | none |
| Look | Email Member | admin, or `member\|is_public:"email"`; not on own rows | `mailto:` | none |
| Look | View Member | admin only | GET `hub_admin_member_edit` | none |
| Money | Waive Late Fee | `late_fee` unpaid and `can_waive` | `waive-fee-{pk}` modal, POST `hub_late_fee_waive` with `next` | existing waive form |
| Money | Refund Late Fee | `has_refund_authority` and fee paid with something refundable | GET `billing_late_fee_refund_form` into `#refund-modal-body` | existing |
| Stop | Cancel Reservation | `status == confirmed` and `ends_at > now` | POST `hub_equipment_reservation_cancel` with `reason` and `next` | Title "Cancel This Reservation?" Body "{Name} has this time booked. Cancelling frees it and tells them why." Reason required, label "Reason", hint "{Name} will see this." Button "Cancel Reservation". The manage page's modal (`equipment_manage.html:519-525`) with the name filled in. |

Reservations are free, so the only money on a row is the late fee; Refund Late Fee is what "refund users" means here. "Edit time" was considered and dropped: no endpoint, and a manager can cancel with a reason.

**Member menu (own row, not managed)**

| Item | Shows when | Calls | Confirm copy |
|---|---|---|---|
| View Equipment | always | GET `hub_equipment_detail` | none |
| Cancel Reservation | `status == confirmed` and `starts_at > now` (a self cancel refuses once started, `membership/models.py` `_ensure_cancel_allowed`) | POST `hub_equipment_reservation_cancel` with `next`, `confirm_no_boost=True` | Title "Cancel This Reservation?" Body "This frees the time for someone else." plus `late_cancel_warning` when a cancel now would be late (rule at `hub/equipment_views.py:272-277`) |
| Pay Late Fee | `late_fee` unpaid | GET `hub_late_fee_detail` | none |

A manager on their own row of equipment they manage gets the staff menu: the endpoint already treats a posted `reason` as the manager route for their own row (`hub/equipment_views.py:686-688`).

### 5.5 Endpoint changes (Feature B)

| Endpoint | Change |
|---|---|
| `hub_equipment_index` (`hub/equipment_views.py:380`) | Accept `view=bookings`; build the pane only then. |
| new `hub_equipment_bookings` at `/equipment/bookings/` | GET partial for lazy load and the `refund-done` refresh. |
| `hub_equipment_reservation_cancel` (`hub/equipment_views.py:674`) | Self route: when a safe `next` is posted, answer as a full page (message plus redirect to `next`; a late fee redirects to Checkout, which is why the form is unboosted) instead of re-rendering the schedule partial (`hub/equipment_views.py:689-716`). Manager route: redirect to safe `next` else the manage tab as today (`hub/equipment_views.py:720-731`). Gates unchanged. |

### 5.6 Wireframes: Reservations

Desktop, staff:

```
Reservations                                         [+ Add]  
Book a room, a space, or a tool you are trained on.           
[ List ] [ Calendar ] [*Bookings*]                            
                                                              
(Upcoming) (Past) (All)                                       
[Search member or equipment ] [Equipment v] [Status v]        
[Late Fee v] [From ] [To ] [Apply]                            
                                                              
When v                      Member     Equipment      Status               
Tue Oct 7, 6 PM to 8 PM     Ana Ruiz   Laser Cutter   (Confirmed)           [...]
                                       Laser Guild                          
Wed Oct 8, 9 AM to 10 AM    You        Big Room       (Confirmed)           [...]
                                       Standalone                           
Mon Oct 6, 1 PM to 3 PM     Kai Moss   Laser Cutter   (Cancelled)(Fee unpaid) [...]
```

Kai's menu, viewer manages the Laser Cutter and holds refund authority (fee unpaid, so no refund yet):

```
| View Equipment   |
| Manage Equipment |
| Email Member     |
|------------------|
| Waive Late Fee   |
```

Desktop, member:

```
(Upcoming) (Past) (All)                                       
When v                      Equipment                 Status        
Tue Oct 7, 6 PM to 8 PM     Laser Cutter              (Confirmed)   [...]
                            Cutting signs                            
```

Mobile 375px, staff:

```
+-----------------------------------+
| [List] [Calendar] [*Bookings*]    |
| (Upcoming)(Past)(All)             |
| [Search member or equipment    ]  |
| > Filters                         |
|-----------------------------------|
| Tue Oct 7, 6 PM to 8 PM     [...] |
| MEMBER     Ana Ruiz               |
| EQUIPMENT  Laser Cutter           |
|            Laser Guild            |
| STATUS     (Confirmed)            |
|-----------------------------------|
```

Mobile 375px, member: as Orientations' member layout, with "Laser Cutter" and the purpose line.

## 6. Out of scope (both PRs)

- Bulk actions (select several rows). The Classes analog has none either.
- CSV export for reservations.
- Moving a booking to another time; editing a reservation's time.
- Equipment owned slots in Add Member (`_manageable_slots` stays guild only, `hub/views.py:2618-2629`).
- Any change to who may refund, waive, cancel or confirm.
- Creating or editing `OrientationRecord` rows (that stays on the member edit page).
- Retiring the equipment manage page's Reservations tab; it stays as the per item view.
- Notifications, emails and their copy.

## 7. Copy rules

- No em dashes or hyphen dashes in anything a member reads: time ranges use "to", owners join with " · " (the page's own separator), and the respond page's "Orientation cancelled — the member has been notified." style messages that the touched endpoints emit are rewritten as two sentences when the endpoint is touched ("Orientation cancelled. We let the member know.").
- Plain words a 14 year old follows: "Mark Oriented", "Release This Spot", "Pay Late Fee". No "booking lifecycle", "scope", "hold".
- Menu items and modal titles in Title Case (FRONTEND.md rule 22, and the registration menu's own labels). Body copy in sentence case.
- Name the member in confirms ("We'll email Ana ...") so the viewer knows which row they opened.
- Status pills use the model's own display labels (`OrientationBooking.Status`, `EquipmentReservation.Status`) plus "Oriented"; no new status words.

## 8. Test plan

Specs follow STANDARDS.md (branch coverage is the working gate; e2e on PostgreSQL).

**Feature A**

- `membership/spec/manageable_orientation_bookings_spec.py`: parity with `_require_can_manage_booking` for member, guild lead, guild staff, equipment staff, EQUIPMENT holder, officer, admin, and admin previewing as member.
- `tests/hub/orientations_bookings_tab_spec.py` (replaces `tests/hub/orientations_dashboard_spec.py`):
  - A member's pane contains their own rows only; a second member's name never appears in the response body (assert on the name string, past and upcoming, records included).
  - A guild X lead sees X's bookings and their own, not guild Y's; same for the export CSV body and the recorded list.
  - Officer sees all guild bookings; admin sees all.
  - Menu items per role and state match section 4.4 (Refund absent without refund authority; Confirm only on requested; nothing but View Request on pending payment).
  - `?sort=nonsense` renders with the default sort.
  - `view=list` makes no Bookings queries (`django_assert_num_queries` on the list render is unchanged); the pane's query count is constant across 1 and 25 rows.
  - `/orientations/manage/?status=requested` redirects 302 to `/orientations/?view=bookings&status=requested`.
- Crafted POST probes, guild X lead against a guild Y booking: `hub_orientation_toggle_completed`, `hub_orientation_respond` (confirm and decline), `hub_orientation_lead_cancel` all 403 and the booking is unchanged. A plain member against someone else's booking on `hub_orientation_cancel_mine`: 403. A lead without refund authority on `billing_orientation_refund`: refused by `refund_authority_required`.
- `toggle_completed` with `completed=1` twice leaves it completed; `next` off site is ignored.
- `tests/e2e/orientations_bookings_menu_spec.py`: open the tab from List (lazy load), open a row's "..." menu, keyboard down and Escape, Mark Oriented through the confirm modal, land back on the tab with the filter kept and the Oriented pill showing. A 375px viewport run: menu opens, no horizontal scroll (`document.documentElement.scrollWidth <= innerWidth`). Wait on the observable, never snapshot after it (the boosted arrival trap).

**Feature B**

- `membership/spec/manageable_reservations_spec.py`: parity with `can_manage_equipment`.
- `tests/hub/reservations_bookings_tab_spec.py`: member sees own rows only (names asserted absent), manager of X sees X and own, admin all, officer without equipment roles sees only own; menu items per role and state; late fee items gated by `can_waive` and refund authority.
- Crafted POST: manager of X cancelling Y's reservation with a `reason` gets 403 and the row stays confirmed; a member cancelling someone else's reservation without `reason` gets 403; self cancel with `next` redirects to `next`, and a late one redirects to Checkout with the fee created once.
- `tests/e2e/reservations_bookings_menu_spec.py`: manager cancels from the menu, the Confirm stays disabled until a reason is typed, the row shows Cancelled after return.

## 9. Defaults taken (no question needed)

- Tab named Bookings on both pages; Upcoming is the default chip.
- Officers see every guild owned orientation booking because they already pass the action gate for every guild; a pure equipment manager reaches the staff view for their equipment's orientations for the same reason.
- Staff views include the viewer's own bookings.
- Email Member honours the member's directory email setting for non admins, so a lead never sees an address the member hid.
- `/orientations/manage/` is a 302, not a 301.
- Add Member moves into a modal.
