# #465 Admins record a completed orientation on a member from Manage Members

Issue: https://github.com/Past-Lives-Makerspace/plfog/issues/465 (enhancement). The issue body is
the spec; this plan pins the decisions and the tree facts the builder needs. Where the two
disagree, the issue's acceptance criteria win.

## What ships

Manage Members > edit member gains an **Orientations** tab (a sibling of Details, Permissions,
Notifications and Emails). It lists every orientation the member has completed and offers one
**Record an orientation** control: a type-ahead over every orientation type, a date (default
today), an optional "oriented by" member and an optional note. Saving makes the member oriented
for that type at once, everywhere the app asks. An admin can remove a recorded entry. Recording
and removal are silent (no email, no Discord) and each logs a `SiteActivity` row.

## Decisions

1. **Option B from the issue: a new model `OrientationRecord`** in `membership/models.py`
   next to `OrientationBooking` (`:10406`): `member` FK (CASCADE, related_name
   `orientation_records`), `orientation_type` FK (PROTECT, related_name `records`),
   `completed_on` DateField, `oriented_by` FK Member (SET_NULL, null, blank),
   `recorded_by` FK User (SET_NULL, null), `note` TextField (blank, default ""),
   `created_at` auto. `UniqueConstraint(fields=["member", "orientation_type"],
   name="uq_orientationrecord_member_type")`. One migration in `membership/` (next after
   `0178`). `OrientationBooking` is untouched.
2. **One truth.** A single resolver `Member.completed_orientation_type_ids(orientation_types=None)`
   returns the set of type pks the member completed, unioning completed bookings
   (`is_completed=True`) with records, optionally narrowed to the given types. Every reader
   goes through it or through the two gates that call it:
   - `Member.is_oriented_for` (`membership/models.py:1622`) becomes: any completed type that
     belongs to the guild (`orientation_type__guild=guild` on both tables).
   - `Member.is_oriented_for_type` (`:1632`) becomes: the type pk is in the resolver's set.
   - `hub/views.py:552` and `:691` (`completed_type_ids`), and
     `hub/equipment_views.py:81` (`_member_access_sets`) call the resolver instead of
     filtering bookings. Keep query counts flat (one query per table, no per-type queries).
   - `OrientationSlot.ensure_bookable_for` (`membership/models.py:10309`),
     `Equipment.access_state` (`:11799`) and `booking_blockers` (`:11826`) already call the
     gates and need no change.
   A spec asserts each gate flips on a record alone, with no booking present.
3. **The picker is a `<datalist>`**, not a JSON endpoint: a text input backed by a datalist of
   every orientation type, active ones first, labelled `"<owner> — <name>"` (the type's
   `__str__`), inactive ones after, labelled `"<owner> — <name> (retired)"`. The form resolves
   the typed label to a type by exact case-insensitive label match and errors with "Pick an
   orientation from the list." otherwise. Two guilds' "Shop Basics" differ by owner in the
   label. The list has a few dozen entries at most, so no endpoint, no JS (YAGNI); the issue's
   Notes offered the endpoint as one option. No checkbox per type anywhere on the page.
4. **The form** `OrientationRecordForm` in `hub/forms.py`: `orientation` (CharField with the
   datalist widget, `list` attr), `completed_on` (DateField, `type="date"`, initial today,
   class `pl-slot-date` and the `showPicker()` onclick as `OrientationBlockForm` does at
   `hub/forms.py:2917`), `oriented_by` (ModelChoiceField over active members, required=False,
   empty label "Not recorded"), `note` (CharField, required=False). `clean` refuses a type the
   member already completed, booked or recorded, with "<Name> already completed this orientation."
   (form-level, readable on the page). No email, no Discord: the save is a plain
   `OrientationRecord.objects.create` plus `SiteActivity.log`.
5. **Views** in `hub/views.py` beside `admin_member_teaching_set` (`:6806`), both
   `@fog_admin_required` + `@require_POST`, full-page POST + Django message + redirect to
   `?tab=orientations`, matching the page's sibling actions:
   - `admin_member_orientation_record` at `manage/members/<int:pk>/orientations/record/`
     (name `hub_admin_member_orientation_record`). On an invalid form, re-render
     `admin_member_edit` with the bound form so the error shows beside the field (factor the
     page's context build so both views can render it).
   - `admin_member_orientation_record_remove` at
     `manage/members/<int:pk>/orientations/<int:record_pk>/remove/`
     (name `hub_admin_member_orientation_record_remove`), through
     `components/confirm_modal.html` naming the consequence ("They will need the orientation
     again before booking anything it gates."). Deleting a record never touches a booking.
   `admin_member_edit` (`:6702`) adds to its context the completed list and the form.
6. **The tab** in `templates/hub/admin/member_edit.html`: a `vote-tab` button "Orientations"
   after Notifications (inside the `is_member` block), and a section `x-show="section ===
   'orientations'"`. The list is rows shaped like the Emails tab's `pl-email-row`: type name,
   owner, date (`completed_on` for a record, the slot's date for a booking), oriented by, and
   a pill: `hub-pill--ok` "Booked" for a booking, `hub-pill--neutral` "Recorded" for a record.
   A record row carries a Remove button (`hub-btn hub-btn--sm hub-btn--danger`) and the
   confirm modal; a booking row has none (the dashboard's toggle owns those). Empty state:
   "No orientations completed yet." The Record form sits under the list, fields through
   `components/form_field.html`, submit "Record orientation". New classes prefixed `pl-` go in
   `static/css/member-edit.css`. No inline styles.
7. **Activity.** `SiteActivity.Kind` (`core/models.py:1482` onward) gains
   `ORIENTATION_RECORDED = "orientation_recorded", "Orientation recorded"` and
   `ORIENTATION_RECORD_REMOVED = "orientation_record_removed", "Orientation record removed"`,
   with a choices-only migration in `core/` (next after `0096`). Log with `actor=request.user`,
   `target=member`, `payload={"orientation_type": type.name, "owner": type.owner_name}`. The feed
   at `/manage/activity/` (`core/views.py:657`, `templates/hub/admin/_activity_feed.html`)
   renders the new kinds with no template change beyond what its generic row needs.
8. **Other surfaces.** The orientations dashboard (`hub/views.py:2173` filters,
   `templates/hub/orientations_dashboard.html:84` table) keeps its bookings table as is; when
   the Completed filter is "yes" it renders a second table "Recorded by an admin" below it with
   the same guild filter applied (records whose type belongs to that guild), columns date,
   member (linking to the member edit page's Orientations tab), owner, type, oriented by,
   recorded by, and a "Recorded" pill; no Mark done control. The guild page and the equipment
   page (`hub/views.py:524-576` `_orientation_sections`, `templates/hub/partials/guild_orientation.html:46`,
   `equipment_orientation.html:31`) show the existing "✓ You've completed this orientation."
   for a record too, followed by a muted "Recorded on <date>." when it came from a record and
   not a booking. The equipment detail banner already reads the gate.
9. **Retired types are recordable** (a retired orientation someone took last year is still
   history); they sit after the active ones in the datalist, labelled.
10. **Audience:** admins only (`fog_admin_required`). Guild leads and staff get nothing here.

## Acceptance criteria (from the issue)

- On Manage Members > edit member, an Orientations tab lists each completed orientation with
  type, owner (guild or equipment), date, who ran it, and booked vs recorded.
- An admin can record an orientation by searching orientation types by name with a type-ahead
  across all active guild-owned and equipment-owned types, each result showing its owner, then
  picking one and saving with a date (default today), optional "oriented by" and optional note.
- There is no checkbox per orientation type anywhere on the page.
- After recording, `Member.is_oriented_for_type` is true for that type: the equipment detail
  page for a tool requiring it shows "You're all set" and a reservation succeeds.
- After recording a guild-owned type, `Member.is_oriented_for` is true for that guild and guild
  join gating passes (`membership/discord_commands.py:425` reads it).
- Recording a type the member has already completed (booked or recorded) is refused with a
  readable message.
- An admin can remove a recorded orientation; both gates return to false. Booked-and-completed
  orientations are not removable from this tab.
- Recording sends no email and emits no Discord event; specs assert the outbox and the emitter
  stay empty.
- A `SiteActivity` row is logged for each record and each removal, with the acting admin as
  actor, and appears on the activity feed.
- Every surface that already lists completed orientations (the orientations dashboard's
  completed filter, the member's guild page, the equipment page requirements banner) renders a
  recorded orientation without error and labels it "Recorded".
- Specs cover: record, duplicate refused, removal, both gates open and close, the picker
  search, no email or Discord, activity rows.
- The PR shows a screenshot of the tab and the picker mid-search.

## Out of scope (from the issue)

Bulk backfill or CSV import; leads or staff recording from the dashboard; a per-member "still
needed" checklist; member-facing history beyond the guild and equipment pages; expiry or
revoking a booked orientation; changing how booked orientations complete; the CSV export
(`membership/orientation_exports.py`) stays bookings-only.

## Tree facts (verified 2026-09-25 on main at ed7e69d4; re-verify, #466 merged since)

| Thing | Where |
|---|---|
| `Member.is_oriented_for`, `is_oriented_for_type` | `membership/models.py:1622`, `:1632` |
| `OrientationBooking`, `mark_completed`, `uncomplete` | `membership/models.py:10406`, `:10550`, `:10562` |
| `OrientationBookingQuerySet.completed()` | `membership/models.py:10402` |
| `OrientationSlot.ensure_bookable_for` already-oriented refusal | `membership/models.py:10309` |
| `Equipment.access_state`, `booking_blockers` | `membership/models.py:11777`, `:11811` |
| `_orientation_sections` and the guild page context | `hub/views.py:524-576`, `:676-735` |
| Equipment index access sets | `hub/equipment_views.py:72-84` |
| Dashboard filters, view, template | `hub/views.py:2173`, `:2199`, `templates/hub/orientations_dashboard.html` |
| `admin_member_edit`, `admin_member_teaching_set` | `hub/views.py:6702`, `:6806` |
| Member routes | `hub/urls.py:604-635` |
| Member edit template and CSS | `templates/hub/admin/member_edit.html`, `static/css/member-edit.css` |
| `SiteActivity.Kind`, `log()` | `core/models.py:1458`, `:1555` |
| Activity feed | `core/views.py:657`, `templates/hub/admin/_activity_feed.html` |
| `complete_orientation` (what a live completion fires and recording must not) | `membership/orientations.py:1061` |
| Discord join gate | `membership/discord_commands.py:425` |
| Factories | `tests/membership/factories.py` (`OrientationTypeFactory:491`, `OrientationBookingFactory:618`, `EquipmentFactory:751`) |
| Sibling specs to mirror | `tests/hub/admin_member_teaching_spec.py`, `tests/hub/member_admin_capabilities_spec.py`, `tests/membership/orientation_models_spec.py:302` (`describe_is_oriented_for`) |

`tests/e2e/manage_members_mobile_spec.py` drives the member edit page; grep it for tab labels
before renaming anything. No e2e clicks a tab this plan adds.

## Screenshots

`mockups/screenshots/465-member-orientations-tab.png` (the tab with one booked and one recorded
row) and `mockups/screenshots/465-member-orientations-picker.png` (the picker with a few letters
typed and the datalist open, if the browser renders it; otherwise the form with a resolved value).
Throwaway spec under `tests/e2e/` with `live_server` + `login_via_code` as an admin, deleted after.
