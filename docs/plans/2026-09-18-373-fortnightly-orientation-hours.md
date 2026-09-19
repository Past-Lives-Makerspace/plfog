# 373: Orientation hours can recur on a cadence

Issue: https://github.com/Past-Lives-Makerspace/plfog/issues/373
Branch: fog/fortnightly-orientation-hours
Brief for this round: surgical, pragmatic, YAGNI.

## Decisions (locked, amended 2026-09-18 on Jo's instruction: ALL cadences, not one)
- D1. The full set of cadences ships in this PR. The ticket's reference vocabulary
  (`CommunityEvent.Recurrence`) minus its semi-monthly trap: Every week, Every other week,
  Every month, Every 2 months, Every 3 months, Every 6 months, Every year. Fortnightly alone was
  a wrong reading of the ticket and is superseded.
- D2. Two fields on `OrientationAvailability`: `cadence` (CharField, TextChoices `weekly`,
  `fortnightly`, `monthly`, `every_2_months`, `every_3_months`, `every_6_months`, `yearly`,
  default `weekly`) and `anchor_date` (DateField, null/blank, label "Starting on", the first
  day the rule runs). Weekly rows ignore the anchor; every other cadence requires it (form AND
  model `clean()`). No data migration; every existing row is weekly. The unshipped migration
  0174 is regenerated for this shape while the PR is a draft.
- D3. Week arithmetic on Mondays for fortnightly: a rule occurs on `day` when
  `day.weekday() == weekday`, `day >= anchor_date`, and
  `((monday(day) - monday(anchor_date)).days // 7) % 2 == 0`.
- D3b. Month arithmetic for the month-based cadences: N = the anchor's weekday ordinal within
  its month (`(anchor.day - 1) // 7 + 1`, so the 2nd Tuesday). A rule occurs on `day` when the
  weekday matches, `day >= anchor_date`, `(day.day - 1) // 7 + 1 == N`, and the whole months
  between the anchor's month and the day's month are divisible by the cadence's month count
  (1, 2, 3, 6, 12). A month with no 5th such weekday gets nothing that month. No "last
  Tuesday" shape; not asked for.
- D4. The legacy shared "Any orienter" / "Any manager" rows stay weekly. They are
  keep/edit/retire only, the fields are not rendered there, and the form treats a missing
  `cadence` as weekly.
- D5. `cadence_display` in plain words: "Every Tuesday", "Every other Tuesday", "Every month on
  the 2nd Tuesday", "Every 3 months on the 2nd Tuesday", "Every year on the 2nd Tuesday of
  September". Ordinals 1st to 5th.
- D6. A rule edited to a sparser cadence retires its already generated off-grid OPEN slots on
  the next generation run (booked ones stay, capped): the equipment-only gate on
  `_retire_off_grid` is lifted so guild rules get the same cleanup.

## Changes
- `membership/models.py` `OrientationAvailability`: `Cadence` TextChoices and the `cadence` +
  `anchor_date` fields (D2); `MONTHS_BY_CADENCE` (the month count per month-based cadence);
  `occurs_on(day) -> bool` (D3, D3b), raising `ValueError` when a non weekly rule has no
  anchor; `cadence_display` (D5), falling back to the bare cadence label when a month-based
  rule has no anchor; `__str__` uses it; `clean()` refuses a non weekly rule with no anchor
  ("Pick the day these hours start."); docstring stops calling the rule weekly.
- Migration `0174_orientation_availability_cadence`: two AddFields, `ruff format`ed. The
  earlier `0174_orientation_availability_interval` is deleted (one migration ships).
- `membership/orientations.py` `_horizon_spans`: `if not rule.occurs_on(day): continue`
  replaces the bare weekday check. `generate_slots` runs `_retire_off_grid` for every rule,
  not only equipment ones (D6).
- `hub/forms.py` `OrientationAvailabilityForm`: `cadence` as `ChoiceField(required=False,
  label="Repeats")` whose blank cleans to weekly (D4); `anchor_date` (`required=False`, label
  "Starting on", `DateInput(type="date")` with the hub's `pl-slot-date` + `showPicker`
  treatment). `clean()`: any cadence but weekly with no anchor adds "Pick the day these
  hours start." on `anchor_date`.
- `templates/hub/partials/_orienter_hours_modal_form.html`: both per-person row layouts gain
  a Repeats + Starting on row via `components/form_field.html`. The legacy block in
  `guild_edit.html` is untouched (D4).
- Overview rows: `{{ r.get_weekday_display }}` becomes `{{ r.cadence_display }}` in
  `templates/hub/guild_edit.html` (3 places) and `templates/hub/equipment_manage.html`
  (3 places). That is the plain-words acceptance.

## Acceptance (specs)
- `_horizon_spans(window_weeks=14, today=date(2026, 9, 21))` for an every-other-week Tuesday
  rule anchored 2026-09-22 yields exactly Sep 22, Oct 6, Oct 20, Nov 3, Nov 17, Dec 1, Dec 15;
  the same rule anchored 2026-09-29 yields Sep 29, Oct 13, Oct 27, Nov 10, Nov 24, Dec 8,
  Dec 22; a weekly rule yields all 14 Tuesdays; an anchor after `today` yields nothing before
  it. The DST fall-back on 2026-11-01 inside that window leaves every start at the same local
  time.
- `_horizon_spans(today=date(2026, 9, 7))` for a monthly rule anchored 2026-09-08 (the 2nd
  Tuesday) yields Sep 8, Oct 13, Nov 10, Dec 8, Jan 12, Feb 9 over 26 weeks (the window ends
  Mar 7) and adds Mar 9 over 27.
- `occurs_on`: an anchor on a Friday counts the Tuesdays of its own week parity from the next
  one on (the Tuesday before it is before the anchor, so it is False); a day before the anchor
  is False; a weekly rule ignores the anchor. Monthly anchored 2026-09-08: Oct 13 True, Oct 6
  False, Nov 10 True. A 5th Tuesday anchor (2026-09-29): nothing in October, Dec 29 True.
  Every 3 months anchored 2026-09-08: Dec 8 True, Nov 10 False, 2027-03-09 True. Yearly:
  2027-09-14 True, 2027-03-09 False. Every 2 months and every 6 months one case each. A non
  weekly rule with no anchor raises `ValueError`.
- `cadence_display` for each of the seven values, ordinals 1st to 5th.
- A rule edited from weekly to every other week retires its already generated off-week OPEN
  slots on the next generation run (booked ones stay, capped), the same off-grid cleanup
  equipment rules already do. A non weekly rule saved without an anchor is refused by the
  model's `clean()` too, so the admin cannot create a row that aborts generation.
- Form: the dropdown offers all seven cadences; any cadence but weekly without a date errors
  on `anchor_date`; a POST with no `cadence` stays weekly (modal and legacy shared-rows form);
  an existing weekly row round-trips; a monthly rule saves.
- `tests/hub/orienter_hours_editor_spec.py` and `equipment_orientation_manage_spec.py`: the
  modal renders both fields; overview rows read "Every other Tuesday" and "Every month on the
  2nd Tuesday".
- `manage.py check` clean.

## Out of scope
`OrientationAvailabilityBlock`, `CommunityEvent.Recurrence`, a "last Tuesday" shape, kiosk,
Host a Workshop, fees, duration and seat caps.
