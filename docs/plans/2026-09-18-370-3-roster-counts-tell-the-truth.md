# Roster counts tell the truth (#370 item 3)

## The problem, in one line

The Registrations tab shows everyone who ever touched the class, and the class
lists count them all against capacity, so a class can read `15/12` while it
genuinely has room.

## Why now

Items 1 and 2 shipped (#419, #434). Both of them deliberately **cancel rather
than delete**: the dedup migration cancelled 17 duplicate rows on production,
and the abandoned-hold sweep cancels every unfinished checkout from here on.
That was the right call for the audit trail, and it means cancelled rows are no
longer rare. Every surface that counts `registrations` without a status filter
now over-reports by design, and gets worse every day the sweep runs.

## What is actually wrong (verified against the tree at `a181f1b6`)

### 1. The roster query has no status filter

`_roster_registrations` (`classes/views.py:2684`) returns `offering.registrations`
whole. CONFIRMED, PENDING, WAITLISTED, CANCELLED and REFUNDED rows all land in
the Registrations tab together, separated only by `style="opacity:0.5;"` on the
shared row partial (`templates/classes/partials/registration_row.html:11`). A
dimmed row is invisible in a printed or screenshotted roster.

Waitlisted rows are a separate problem in the same query: `_waitlist_context`
(`classes/views.py:2764`) already gives them their own tab with their own query,
so today a waitlisted person appears on **both** tabs.

### 2. Three unfiltered counts render against capacity

| Site | Renders as |
|---|---|
| `classes/views.py:1707` | `{{ registration_count }}/{{ capacity }}` in the teach class list |
| `classes/views.py:2264` | the `teach_registrations` group headers |
| `classes/views.py:3356` | `{{ registration_count }}/{{ capacity }}` in the admin class list |
| `classes/views.py:3687` | the per-class Overview |

`ClassOffering.active_registration_count` (`classes/models.py:1586`) exists and
is not used by any of them.

### 3. The tab badge counts pending under a confirmed name

`_class_workspace_counts` (`classes/views.py:3667`) computes
`confirmed_registration_count` from an inline `[CONFIRMED, PENDING]` list. The
number is defensible; the name is not, and the inline list is a fourth copy of a
set that now has a module constant.

## The decisions I am making, and why

**Cancelled rows hide by default, with a Show cancelled toggle.** The issue left
this open between a toggle and a separate section. A toggle, because instructors
do need the history (who dropped, and when) and a roster that silently omits
people is its own support ticket. Hidden by default because the complaint that
opened #370 was an instructor reading a cancelled row as a live student.

**The counts use `CAPACITY_CONSUMING_REGISTRATION_STATUSES`, not
`active_registration_count`.** This contradicts the issue's own proposed fix, and
the issue is wrong here. `active_registration_count` is `seat_holding()`, which
includes WAITLISTED. These numbers render as `N/capacity`, and `spots_remaining`
(`classes/models.py:1799`) computes the capacity side from
`CAPACITY_CONSUMING_REGISTRATION_STATUSES`. A count that uses a wider set than
its own denominator makes the class list and the register page disagree about
whether a class is full, which is the bug in a new costume. Waitlisted people do
not occupy a seat and must not be counted against capacity.

**The Registrations tab stops showing waitlisted rows.** They have their own tab
with its own ordering and its own actions. This is a visible change; it is the
point.

## The trap that must not be sprung

`_registration_row_response` (`classes/views.py:4645`) re-renders a single roster
row after an htmx action by calling
`_roster_registrations(offering).get(pk=registration.pk)`.

**Putting the status filter inside `_roster_registrations` turns every cancel
and every refund into a 500**, because the action's own response re-fetches the
row it just took out of the filtered set, and `.get()` raises `DoesNotExist`.
That is the most-used action on the surface being changed.

So the filtering and the annotating must be two separate things:

- keep an **unfiltered annotated base** (the `Exists()` subquery for the
  promoted-email chip, the `select_related`/`prefetch_related`) for anything
  fetching one known row,
- apply the **status filter in the roster context builder**, where the toggle
  lives.

A spec must prove this directly: cancel a registration through its real endpoint
and assert the response is 200 and renders that row.

## Build steps

1. Split `_roster_registrations` into the annotated base and a filtered roster
   query. `_registration_row_response` uses the base.
2. `_teach_registrations_context` filters to
   `CAPACITY_CONSUMING_REGISTRATION_STATUSES`, widening to include CANCELLED and
   REFUNDED when the request asks for them. Follow the existing facet/param
   convention in this module rather than inventing a new one; read
   `resolve_facet` and the `?facet=` handling at `classes/views.py:1708` first
   and match whichever shape fits a boolean toggle.
3. Add the toggle control to the Registrations tab template for both the teach
   and admin roster, matching the component library in `FRONTEND.md`. It must
   state the count it is hiding, e.g. `Show 3 cancelled`, so a roster never
   silently omits people.
4. Replace the four `Count("registrations")` annotations with a filtered
   `Count(..., filter=Q(registrations__status__in=CAPACITY_CONSUMING_REGISTRATION_STATUSES), distinct=True)`.
   Keep `distinct=True` wherever a sessions join is present (`:1707`, `:3356`)
   and check whether the other two need it.
5. Rename `confirmed_registration_count` to say what it counts, and source it
   from the constant. Update every template reading it.
6. Check whether `ClassOffering.active_registration_count` still has callers
   after this. If it does, leave it. If it does not, say so in your report
   rather than deleting it.

## Acceptance criteria

1. A class with 12 capacity, 10 confirmed, 2 cancelled and 3 waitlisted reads
   `10/12` in both class lists, and its Registrations tab shows 10 rows.
2. Toggling Show cancelled on that class shows 12 rows, and the two cancelled
   ones are still visually marked.
3. Cancelling a registration from the roster returns 200 and renders the
   cancelled row. Same for a refund.
4. The tab badge and the number of rows on the tab agree.
5. `spots_remaining` and the class list count agree on the same class.

## Out of scope

- Grouping roster rows by email (issue item 3 fix 4). The unique constraint from
  #419 means one person cannot hold two live seats, so the grouping this asked
  for no longer has rows to group.
- Any change to `registration_row.html`'s status cell, the opacity cue, or
  `can_receive_class_announcement`. Commit `15fe9037` settled the email
  reachability rule and it is not in question here.
- The waitlist tab's own query and ordering.
