# 532: Orientation hours choose fixed start times or any time in the window; Availability Blocks retires

Issue: https://github.com/Past-Lives-Makerspace/plfog/issues/532
Branch: fog/orientation-open-windows (part 1; parts 2 and 3 branch from main after each merge)
Brief: one availability model, a per row choice of how members book, the dashboard card removed. Reuse
the window engine; do not rebuild it. Every existing hours row keeps working unchanged.

Mockups (approved by Felix before part 1 opens): `mockups/532-orientation-open-windows.html`
(`?view=schedule|editor|member|picker`, `?theme=light`, `&phone=1`) and `mockups/screenshots/532-open-windows-01.png` to `-09.png`.

Approved by Felix 2026-09-30: the desktop screens as drawn (D1 stands) and D7's fixed with fixed exception. The phone layout (D10, screenshots 08 and 09) was redrawn the same day after his review and approved with one change, more room between the orienter name and the button (1.25rem). Part 1 started 2026-09-30.

## Words

- **hours**: a recurring `OrientationAvailability` row.
- **time**: what a member books.
- **open window**: what an hours row set to "Any time in the window" yields, and a one off of the same
  shape. In code it stays `OrientationAvailabilityBlock`. No member or lead facing copy says "block".
- **How members book**: the row's choice. Values `fixed` ("Fixed start times") and `open` ("Any time in
  the window").

## Decisions (locked unless Felix overrules at the mockup gate)

- D1. The choice lives on the hours row (`booking_style`), not on the orientation type or the guild. One
  guild runs both shapes at once (Woodworking's group orientation; Amber's open studio day).
- D2. `OrientationAvailability.orientation_type` becomes nullable. NULL means any of the guild's active
  orientations and is legal only when `booking_style=open`, `guild` is set and `orienter` is set. A
  `CheckConstraint` says exactly that. Fixed rows keep a required type.
- D3. `OrientationAvailabilityBlock` gains `availability` (FK, null, SET_NULL, `related_name="windows"`)
  with `UniqueConstraint(fields=["availability", "starts_at"], condition=Q(availability__isnull=False))`,
  and `orientation_type` (FK, null, SET_NULL). Set, only that orientation books in. The two production
  blocks (Printmaking, posted 2026-09-30) keep both NULL and become one off open windows.
- D4. One migration, reversible, no data migration. `manage.py check` after (index name cap E034).
- D5. Generation: `generate_slots` keeps its loop; each eligible rule dispatches on style. Open rows
  materialize windows with `get_or_create(availability=rule, starts_at=start, defaults={guild, orienter,
  ends_at, location, orientation_type})` over `_horizon_spans` (an open row has `slot_minutes` NULL, so
  `carve_spans` yields one span per occurrence). `_rule_generates` for a NULL type reads the guild's
  `GuildOrientationSettings.is_accepting` and requires at least one active type.
- D6. Retirement: `_retire_off_grid` dispatches too. For open rows, future generated windows off the grid
  are deleted when `booked_segments()` is empty and cancelled otherwise; `retire_open_slots` gains the
  same branch so pause and delete report `(removed, kept)` as today. A booked segment is its own
  `FROM_BLOCK` slot and booking; never touched.
- D7. One person, one calendar, at save time: `OrientationAvailabilityForm.clean` refuses a row when it
  or another active row of the same orienter on the same weekday is `open` and their times overlap,
  across every guild. Two fixed rows may overlap (Events Guild: Host and Sound over the same hours).
  Message on `start_time`: "These hours overlap your {Weekday} {start}–{end} hours. One person can be
  booked one way at a time." The formset's `clean` runs the same check between rows of one submission.
- D8. One person, one calendar, at booking time: `OrientationAvailabilityBlock._busy_spans` unions the
  orienter's other seat holding slots overlapping the window, any source, any guild
  (`OrientationSlot.objects.filter(orienter=self.orienter_id, is_cancelled=False, starts_at__lt=self.ends_at,
  ends_at__gt=self.starts_at).exclude(block=self)` with a seat holding count over zero). The refusal copy
  stays "That time was just taken. Please pick another time."
- D9. A window with an `orientation_type` answers `valid_starts_for(other_type)` with `[]` and
  `ensure_start_valid` raises "That window is for {name} only."
- D10. The member list per orientation is one list, `section["times"]`, of slots (`bookable()` minus
  `source=FROM_BLOCK`) and windows (`upcoming()`, type NULL or this type, `valid_starts_for(type)` non
  empty), sorted by `starts_at`, each tagged `kind` (`slot` / `window`). The template renders one `<ul>`;
  a window row's action is today's Pick a time button and modal. The "Pick a Time" sub heading goes.
  On a phone (640px and under) the same markup lays out as a day header (`.pl-orient-times__day`,
  one per local date via `{% ifchanged %}`) and one card per time (`.pl-orient-time`): the span in
  1.125rem semibold, "with Amber" under it, and after a 1.25rem gap a full width 48px button, Request or Pick a time; a
  full fixed slot is a dimmed card with a dashed Full bar. The pager hides and a full width "Show more
  times" button shows instead; both read the list's Alpine `page` and `size`, and a row's visibility is
  `mobile ? i < (page + 1) * size : (i >= page * size && i < (page + 1) * size)` with `mobile` from
  `window.matchMedia('(max-width: 640px)').matches` in the list's `x-data`. The custom time button and
  the picker's submit are full width 48px on a phone; the picker's select and textarea grow to 48px.
  The CSS is the `.pl-orient-times*` and `.pl-orient-time*` block in the mockup's first `<style>`.
- D11. Upcoming Times (the tab card) lists `upcoming_times_admin`: slots minus `FROM_BLOCK` plus
  `guild.orientation_blocks.upcoming()`, sorted. A window row shows the orienter, "open window",
  recurring or one off, and its `booked_segments()` (time, orientation, member, status) or "nothing booked
  yet". Cancel posts to `hub_orientation_block_cancel`, which gains a `next` redirect back to the tab.
- D12. Add a one off: `OrientationSlotForm` gains `booking_style` (default fixed) and `end_time`;
  `duration_minutes` and `seats` are required only for fixed, `end_time` only for open; open requires an
  orienter ("An open window needs a person."). Open posts an `OrientationAvailabilityBlock` with
  `availability=None`. A one off, either style, is refused when it overlaps the orienter's existing open
  window or, for an open one off, any of the orienter's slots that day (the D7 rule for one offs).
- D13. Removed: `orientation_block_post`, `_orientation_block_guilds`, `OrientationBlockForm`,
  `hub_orientation_block_post`, the dashboard card, the `orientation.availability-blocks` help key. Not
  hidden; gone.
- D14. The legacy shared Any orienter formset in `guild_edit.html` never renders `booking_style`; the
  form treats a missing value as fixed, exactly as `cadence` does.
- D15. Guild owned only. Equipment windows are a follow up (they need the reservation overlap union).
- D16. Copy, exact:
  - Chip labels: "Fixed start times", "Any time in the window". Group label: "How members book".
  - Open row hint: "Members pick an orientation and any 15 minute start that fits inside these hours.
    Each booking takes that time off the window."
  - Overview line, open: "Any orientation · Every Sunday · 11:00 am–6:00 pm · any time in the window";
    with a type: "{Type} · Every Sunday · … · any time in the window". Fixed lines gain "· fixed start
    times" only when the person also has an open row (keeps today's lines quiet for guilds that never
    use windows).
  - Upcoming Times hint: "Everything members can book for the next 8 weeks: fixed times from everyone's
    hours, open windows, and one offs."
  - Any orientation select choice: "Any orientation".

## Changes by file

### Part 1: model, generation, retirement, busy check (branch `fog/orientation-open-windows`)
- `membership/models.py` `OrientationAvailability`: `BookingStyle` TextChoices; `booking_style`
  CharField(16, default fixed); `orientation_type` `null=True, blank=True`; constraint
  `ck_orientavail_any_type_open_only`; `clean()` refuses `open` with `slot_minutes` set or `orienter`
  NULL, and the D7 overlap (so the admin gets it too); `style_display`; `overview_line` (D16);
  `__str__` and `for_equipment` tolerate a NULL type.
- `membership/models.py` `OrientationAvailabilityBlock`: `availability`, `orientation_type` (D3);
  `_busy_spans` D8; `valid_starts_for` and `ensure_start_valid` D9; `booked_segments` selects member.
- `membership/models.py` `OrientationSlotQuerySet.bookable`: unchanged (the member list filters
  `FROM_BLOCK` at the view, D10, because the dashboard and Add member still need those slots).
- `membership/migrations/0179_orientation_open_windows.py`.
- `membership/orientations.py`: `_rule_generates` NULL type branch; `_materialize_windows`;
  `_retire_off_grid_windows`; `retire_open_slots` and `retire_rule` dispatch on style; `generate_slots`
  dispatch. `_carve_block_slot` copies `locked.orientation_type or orientation_type`.
- `tests/membership/factories.py`: `OrientationAvailabilityFactory(booking_style=...)`,
  `OrientationAvailabilityBlockFactory(availability=None, orientation_type=None)`.
- Specs: `tests/membership/orientation_windows_spec.py` (new: generation idempotent, every cadence via
  `occurs_on`, horizon, start day floor, NULL type gate, retirement on pause, delete, re-grid with a
  booked segment kept, D8 busy union across sources and guilds, D9); `orientation_blocks_spec.py`,
  `orientations_service_spec.py`, `orienter_availability_spec.py`, `orientation_models_spec.py`
  fixture updates.

### Part 2: the editor, Upcoming Times, Add a one off, dashboard removal
- `hub/forms.py` `OrientationAvailabilityForm`: `booking_style` ChoiceField (radio, `.pl-chip`),
  "Any orientation" empty choice on `orientation_type` when open, D7 clean with `orienter` passed in;
  formset `clean` for intra submission overlap. `OrientationSlotForm`: D12. Delete `OrientationBlockForm`.
- `hub/views.py` `guild_orientation_hours_save` and `guild_orientation_hours_form`: pass `orienter` to
  the form; `_guild_edit_context`: `upcoming_times_admin` (D11); `guild_orientation_slot_add`: D12
  branch; `orientation_block_cancel`: `next`; delete `orientation_block_post`, `_orientation_block_guilds`;
  `orientations_dashboard` drops `my_blocks` and `block_form`.
- `hub/urls.py`: remove `hub_orientation_block_post`.
- `templates/hub/partials/_orienter_hours_modal_form.html`: chips, Alpine `x-data="{ style: '…' }"` per
  row, `x-show` on the fixed only fields (Seats, Slot length, Break) and the hint, the Any orientation
  option; rows on `.pl-hours-row` classes (no inline styles in rewritten markup).
- `templates/hub/guild_edit.html:570-756`: overview lines via `overview_line`; Upcoming Times card;
  Add a one off with the chips and conditional fields.
- `templates/hub/orientations_dashboard.html:181-224`: removed.
- `static/css/hub.css`: `.pl-booking-style`, `.pl-hours-row*`, `.pl-orient-style`,
  `.pl-slot-admin__segments`, `.pl-oneoff*` (from the mockup's first `<style>` block).
- `core/help_registry.py`: `orientation.how-members-book` replaces `orientation.availability-blocks`;
  the modal's chip group carries `data-help-key`.
- Specs: `orienter_hours_editor_spec.py` (chips, hidden fields per style, D7 refusal and the fixed
  with fixed exception, legacy rows without the control), `orientation_settings_spec.py` (Upcoming
  Times rows and segments, window cancel redirect), a new `orientation_oneoff_spec.py` (both styles,
  staff lock, open needs a person, one off overlap), `orientation_block_views_spec.py` (post specs
  removed, dashboard card absent, URL gone), `tests/help_registry_spec.py` or its equivalent for the key.

### Part 3: the member list, help, glossary (closes #532)
- `hub/views.py:726-762`: `section["times"]` (D10); `_orientation_sections` unchanged (equipment
  shares it).
- `templates/hub/partials/guild_orientation.html:103-192`: one list, kind branches, Pick a time modal
  kept once per type.
- `membership/help_content.py:1478-1490`: Recurring Hours describes both choices; Availability Blocks
  section removed; `static/help/running-orientations/02-recurring-hours.png` retaken with
  `scripts/capture-help-screenshots.sh`.
- `CONTEXT.md`: **Open window** entry; Avoid: availability block.
- Specs: `orienter_member_surface_spec.py` (merged order, no `FROM_BLOCK` row, Any orientation window
  under each type with room, typed window under its type only), `orientation_external_signup_spec.py`
  fixture updates, help content spec.

## Acceptance (specs)

Each ticket criterion maps to at least one `it_` in the files above. The ones that pin behaviour:

- Generation: a weekly open row Sundays 11:00–18:00 with `window_weeks=8` and `today=2026-10-01` yields
  windows on Oct 4, 11, 18, 25, Nov 1, 8, 15, 22 (the DST day Nov 1 at the same local time); a second
  run creates 0; a fortnightly row anchored 2026-10-04 yields Oct 4, 18, Nov 1, 15; a monthly row
  anchored 2026-10-11 (2nd Sunday) yields Oct 11 and Nov 8 (the Nov 15 window does not exist).
- Retirement: pausing the weekly row deletes the 8 windows when none has a segment; with one segment
  booked on Oct 11, seven are deleted, Oct 11 is cancelled, the segment's slot and booking are unchanged,
  and the return value is `(7, 1)`.
- D7: a fixed Monday 11:00–18:00 row and a new open Monday 12:00–15:00 row for the same orienter fail
  with the overlap message on `start_time`; the same two rows both fixed save; an open Monday row and
  an open Tuesday row save; an open row against a fixed row of the same orienter in another guild fails.
- D8: a window 11:00–18:00 whose orienter has a REQUESTED booking on a fixed slot 12:00–13:00 offers
  no start in [11:15, 13:00) for a 60 minute type; a CANCELLED booking there frees them.
- D9: a window typed Print Studio answers `[]` for Cyanoprinting and raises on `ensure_start_valid`.
- Member list: with a fixed slot Oct 5 11:00 and an Any orientation window Oct 4, both types show the
  window and only Print Studio shows the slot; booking 12:15 in the window adds no row to any list.
- Dashboard: GET `/orientations/` contains neither "Availability Blocks" nor the post URL; `reverse`
  of `hub_orientation_block_post` raises `NoReverseMatch`.
- One off: an open one off posts a window with `availability=None`; a plain staff member's post lands
  with themselves as orienter whatever the POST carried; Any orienter with open is refused.

## Retirement of the old way

- The dashboard card, its form, view and URL, and the help key go in part 2.
- The two existing production windows stay; they list on Printmaking's Upcoming Times with Cancel.
  Amber decides whether the Oct 5 one stays over her Monday hours; nothing migrates it.
- `CONTEXT.md` records "availability block" under Avoid in part 3.
- Fragments: parts 1 and 2 carry internal fragments; the member facing fragment ships with part 3
  ("Book any time inside an orienter's open hours: pick the orientation, then any 15 minute start that
  fits, and that time is yours.").

## Neighbours

- #503 owns the tab's card order and setup line and leaves the Edit Hours modal untouched. Whichever
  lands second rebases; the Upcoming Times card keeps its position in #503's order (third).
- #502's member Orientations page reuses `section["times"]` from part 3.
- #430 (more recurrence shapes) is untouched; windows inherit whatever `occurs_on` learns.
