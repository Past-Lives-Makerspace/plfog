# 373: Orientation hours can recur every other week

Issue: https://github.com/Past-Lives-Makerspace/plfog/issues/373
Branch: fog/fortnightly-orientation-hours
Brief for this round: surgical, pragmatic, YAGNI.

## Decisions (locked)
- D1. Fortnightly only. No monthly. The issue's out-of-scope list stands.
- D2. Two fields on `OrientationAvailability`: `interval_weeks`
  (PositiveSmallIntegerField, choices 1 "Every week" / 2 "Every other week", default 1) and
  `anchor_date` (DateField, null/blank, label "Starting the week of"). Weekly rows ignore
  `anchor_date`. No data migration; every existing row is `interval_weeks=1`.
- D3. Week arithmetic on Mondays. A rule occurs on `day` when `day.weekday() == weekday` and
  (`interval_weeks == 1` or (`day >= anchor_date` and
  `((monday(day) - monday(anchor_date)).days // 7) % interval_weeks == 0`)). The anchor's own
  weekday does not matter; "the week of" is what the label promises.
- D4. The legacy shared "Any orienter" / "Any manager" rows stay weekly. They are
  keep/edit/retire only, the fields are not rendered there, and the form treats a missing
  `interval_weeks` as 1.

## Changes
- `membership/models.py` `OrientationAvailability`: the two fields (D2); `occurs_on(day) -> bool`
  (D3); `cadence_display` property ("Every Tuesday" / "Every other Tuesday"); `__str__` uses
  it; docstring stops calling the rule weekly by construction.
- Migration: two AddFields, `ruff format`ed.
- `membership/orientations.py` `_horizon_spans`: `if not rule.occurs_on(day): continue`
  replaces the bare weekday check. Nothing else in generation changes.
- `hub/forms.py` `OrientationAvailabilityForm`: add both to `Meta.fields`. `interval_weeks` as
  `TypedChoiceField(coerce=int, required=False, empty_value=1, choices=...)` (D4).
  `anchor_date` widget `DateInput(attrs={"type": "date"})`, `required=False`. `clean()`:
  interval 2 with no anchor adds the error "Pick the week these hours start." on `anchor_date`.
  Copy the dark-mode date input treatment the hub already uses (grep `type="date"` and
  `showPicker` under templates/hub and the hub css) rather than inventing one.
- `templates/hub/partials/_orienter_hours_modal_form.html`: both per-person row layouts
  (guild ~37-48, equipment ~77+) gain the two fields, via `components/form_field.html`. The
  legacy block in `guild_edit.html` ~640 is untouched (D4).
- Overview rows: `{{ r.get_weekday_display }}` becomes `{{ r.cadence_display }}` in
  `templates/hub/guild_edit.html` (3 places) and `templates/hub/equipment_manage.html`
  (3 places). That is the plain-words acceptance.

## Acceptance (specs)
- `_horizon_spans(window_weeks=14, today=date(2026, 9, 21))` for an every-other-week Tuesday
  rule anchored 2026-09-22 yields exactly Sep 22, Oct 6, Oct 20, Nov 3, Nov 17, Dec 1, Dec 15;
  the same rule anchored 2026-09-29 yields Sep 29, Oct 13, Oct 27, Nov 10, Nov 24, Dec 8,
  Dec 22; a weekly rule yields all 14 Tuesdays; an anchor after `today` yields nothing before
  it. The DST fall-back on 2026-11-01 inside that window leaves every start at the same local
  time (extend the existing DST pattern in `tests/membership/orienter_availability_spec.py`).
- `occurs_on`: an anchor on a Friday still counts the Tuesday of that same week; a day before
  the anchor is False; a weekly rule ignores the anchor.
- Form: interval 2 without a date errors on `anchor_date`; a POST with no `interval_weeks`
  keeps 1; an existing weekly row round-trips.
- `tests/hub/orienter_hours_editor_spec.py` and `equipment_orientation_manage_spec.py`: the
  modal renders both fields; an overview row reads "Every other Tuesday".
- `manage.py check` clean.

## Out of scope
Monthly, `OrientationAvailabilityBlock`, `CommunityEvent.Recurrence`, kiosk, Host a Workshop,
fees, duration and seat caps.
