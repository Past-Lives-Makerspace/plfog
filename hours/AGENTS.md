# hours app

One set of hours per person for every kind of thing they give (#1300): the time is kept once, and each kind (tours, orientations) ticks it with its own settings. The member-facing name stays **My Hours**. #1300 ships in five parts; parts 1 to 3 are built: the tables underneath, filled and kept in step (part 1), one rule for what blocks a person's time, read by tours and orientations alike (part 2), and the My Hours page, read only (part 3, below).

## Models (`hours/models.py`)

| Model | Notes |
|---|---|
| `Hours` | member, date (blank repeats), weekday, cadence / anchor_date / last_of_month (the orientation cadences, `OrientationAvailability.Cadence`), start_time, end_time, created_at. Checks: `ck_hours_end_after_start`, `ck_hours_date_weekday` (a date's weekday is stored), `ck_hours_dated_no_cadence` (a dated set is weekly with no anchor). `key` is its `TimeKey`; `Hours.key_of(row)` is a kind row's. |
| `DayOff` | member, starts_on, ends_on, note, created_at; `ck_dayoff_ends_after_start`. One Days Off for every kind. |

The kind rows point here: `tours.TourHours.hours` and `membership.OrientationAvailability.hours` (personal rows only; the shared row with no orientor keeps none). Both keys are nullable and `on_delete=RESTRICT`: a bare `Hours.delete()` raises while a row ticks the set, but deleting the person (which cascades to their rows too) still works. The plan said PROTECT; PROTECT would have refused every member deletion with hours.

## Keeping in step (part 1)

`hours/follow.py` mixes `FollowsHours` into `TourHours` and `OrientationAvailability`, and `FollowsDaysOff` into `TourDayOff` and `OrientationDayOff`. Every `save()` and `delete()` then runs `Hours.objects.regroup(member_id)` or `DayOff.objects.recopy(member_id)` for that person, under `FOR NO KEY UPDATE` on their member row (the lock `tours.models.lock_guide_hours` takes). `TourDatesForm.save` bulk creates, so it regroups itself.

- `regroup` groups the person's rows by identical `TimeKey` (date or weekday, cadence, start day, last of the month, From, Until). A set already holding a group's time stays that group's; else a set the group's rows hold takes the new time; else a new set is made. Sets left holding nothing are deleted. Only the rows' `hours` key is written, by `update()`, so no row's time changes and no slot, window or tour is made, moved or retired.
- `recopy` makes the person's `DayOff` rows match: each `TourDayOff` as it is, each `OrientationDayOff` as a one day range unless their own tour range covers it. Rows that still match are kept.
- A queryset `update()`/`delete()` or a cascade (a guild deleted) skips both; the person's next save puts them right.

Migrations: `0001` the tables, `tours.0013` and `membership.0292` the keys, `0002_group_hours` the grouping backfill, `0003_copy_days_off` the days off copy. Both backfills have a real reverse (let go of the keys and delete the sets; delete the copies). Specs in `tests/hours/`.

**Deploy gap.** Migrations run while the previous release still serves, so a kind row or day off written by the old code in that minute is not grouped or copied until that person's next save. Part 2's `0004_regroup_everyone` lets go of every set and copy and makes them again (0002 and 0003's functions; its reverse is the same function).

## Never double booked (part 2)

`hours/busy.py` is the one rule for what holds a person's time: a tour they guide holding seats (booked, attended, requested), an orientation slot of theirs holding seats (checkout hold, requested, confirmed), a session of a class they teach unless the class is Draft or Cancelled, their Google busy time (`tours.CalendarBusySpan`, rows the busy read writes beside the JSON cache, read only while connected, read and Google is set up), and a `DayOff` (whole local days). A tour hour or a fixed slot nobody booked blocks nothing.

- `busy_holds(member_ids, first, before, *, ...)` reads every thing holding the time as a `Hold` (member, start, end, `source` of `TOUR`, `ORIENTATION`, `CLASS`, `GOOGLE`, `DAY_OFF`, and that row's pk), five queries at most; `busy_spans(member_ids, first, before, *, tours=True, google_busy=True, except_slot=None)` merges them into each person's spans. `is_busy(...)` asks one time. `busy_q(member, starts_at, ends_at, *, except_slot=None)` is the SQL twin over an outer row's fields; change one, change the other.
- **Tours** (`tours.availability.open_tours`) drop a tour over the guide's busy time with `tours=False` (a guide's own tour keeps selling seats; other guides' claims are #1312's rule) and, for staff, `google_busy=False`. Days off come from `DayOff`, so an orientation day off closes tours too (the Calendar tab's coverage reads it as well).
- **Orientations.** `OrientationSlot.objects.bookable()` filters `~busy_q(...)` (the slot's own seats never hide it), `ensure_bookable_for` refuses busy time with `BUSY_MESSAGE` for members and staff alike, open windows subtract busy spans from their free time (`_busy_spans`, `free_intervals_by_block`), a move refuses a busy time (the slot it leaves never counts), and generation skips `DayOff` days. A custom request has no orientor until someone confirms it, so it checks nobody's time.
- **Days off cancel nothing** (Felix, 2026-11-23, replacing #767's cancel): a day off hides the unbooked times; every booking stays, counted in the Days Off modal (`orientations.booked_on_day_off`). Generation then retires the day's unbooked generated times and keeps booked ones, as for any off grid time.
- **The person lock.** Every booking path takes `lock_person` before it checks: `tour_at`/`open_tour_at(lock=True)` (after `lock_bookings`; `tour_at` locks the guide it picks and reads again until the guide is one it holds), `request_orientation` (members and staff add), the checkout hold, both open window paths and `_move_locked`, before the equipment and window locks. `tests/hours/person_lock_spec.py` checks the order and races a tour against an orientation on PostgreSQL.

## My Hours, the page (part 3)

`/hours/` (`hours.views.my_hours`, `templates/hours/my_hours.html`, `static/css/my-hours.css`, lifted from `mockups/1300-my-hours.html`), read only. No sidebar link and no other door yet (Felix, 2026-11-23): part 5 sends the places hours are set today here. Gated by `hub.view_as.hours_access_required`: actual admins, and whoever `hours.page.hours_people_q` names (`tours.models.guide_filter`, so Tour Guides and admins, or an active member who leads a guild, holds a guild staff role or is on a tool's staff: the people `Guild.orientor_ids` and `Equipment.orientor_ids` count). Anyone else gets a 403. An admin picks whose hours with `?member=<pk>` (everyone with a page or any `Hours`; 404 otherwise); anyone else naming another person gets a 403. `?week_offset=<n>` moves the week (`hub.calendar_pages.calendar_nav_params`).

`hours.page.MyHours.for_member(member, week_offset=)` builds it:

- **Kinds**: Tours (gold) when they give tours, have tour hours or a booked tour that week, then each guild or tool (`guild-<pk>`, `equipment-<pk>`) in the order their hours name them, then any a booked orientation that week belongs to, each taking the next of `KIND_CLASSES`.
- **The week**: a day off (a `DAY_OFF` hold) is striped and shows no open hours; every other day gets a dashed chip per `Hours` set offering something that day (`TourHours.applies_on`, active `OrientationAvailability.occurs_on`) dotted with its kinds; then every other hold as a chip, clipped to each day it touches: tours together per start (`Tour · 3 visitors`), an orientation slot (`<owner> · Sam K.`, `requested`, or `2 booked`), a class (`Class: <title>`), Google (`Busy in Google`). The blocks are `busy_holds`, never a second rule. `week_grid` places those chips on the hour grid (#1332, `components/hour_grid_week.html` with `hours/partials/_hour_grid_day.html`): a booking inside open hours sits inside that block, one no open hours hold stands alone, and overlapping blocks share the column side by side (`hub.hour_grid.lay_lanes`).
- **Every Week / Available On**: the sets with no date, and with a date from today on, each `<day> · <From> to <Until> · <Hours.repeat_label>` with a chip per tour row (`TourHours.offer_summary`) and active orientation row.
- **Days Off** (`DayOff`, not ended yet, `DayOff.label`), **Google Calendar** (the `TourCalendarConnection`, state only) and, for a guide, **Booking Notice** (`TourGuideSettings.notice_minutes_by_guide`). The mockup's Confirming Tour Bookings card went with #1309; Booking Notice (#1312) is the tours only setting in its place.

Editing (Open To, Add Day Off, Connect) is part 4. Specs: `tests/hours/my_hours_spec.py`, `tests/e2e/my_hours_spec.py` (`CAPTURE_1300_DIR` for pictures).
