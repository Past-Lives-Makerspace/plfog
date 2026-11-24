# hours app

One set of hours per person for every kind of thing they give (#1300): the time is kept once, and each kind (tours, orientations) ticks it with its own settings. The member-facing name stays **My Hours**. #1300 ships in five parts; only part 1 is built: the tables underneath, filled and kept in step, with no visible change and nothing reading them yet.

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

**Deploy gap.** Migrations run while the previous release still serves, so a kind row or day off written by the old code in that minute is not grouped or copied until that person's next save. Part 2 regroups and recopies everyone before it switches the readers.
