# #466 Equipment can require its own orientation at creation

Issue: https://github.com/Past-Lives-Makerspace/plfog/issues/466 (bug). The issue body is the
spec; this plan pins the decisions and the tree facts the builder needs. Where the two
disagree, the issue's acceptance criteria win.

## What ships

The "during" shape from the issue. The Required orientation control on the equipment form
(the add page and Manage > Details, both rendered by
`templates/hub/partials/_equipment_form_fields.html`) gains a third choice beside
"No orientation needed" and the existing types: **"New orientation for this equipment"**.
Choosing it reveals the new type's Name plus Length (minutes), Seats per slot, Price and
Location, prefilled with the model defaults. One Save creates the equipment, the type
(owned by the equipment, guild empty) and the requirement in one transaction, and lands on
the equipment page with the gate already closed.

## Decisions

1. **One form, one save.** `EquipmentForm` (`hub/forms.py:4194`) keeps the field name
   `required_orientation` so every existing POST and spec keeps working, but the field
   becomes a `ChoiceField` whose choices are `""` (No orientation needed), `"new"`
   (New orientation for this equipment) and one entry per type the current
   `ModelChoiceField` queryset would offer, labelled by the type's `__str__`, in the same
   order. `clean_required_orientation` maps a pk back to the `OrientationType` instance and
   `"new"` to `None` plus a flag; `cleaned_data["required_orientation"]` stays an instance or
   `None` so `clean()`'s guild match rule and `is_own_type` allowance are untouched.
2. **The nested type form is `OrientationTypeForm`** (`hub/forms.py:2105`), built in
   `EquipmentForm.__init__` with `prefix="new_type"` and bound to the same POST data only
   when the posted choice is `"new"`. Do not fork it and do not subclass it to drop fields:
   render only name, duration_minutes, default_seats, price and default_location; the other
   fields (description, sort_order, is_active, external_signup_url) are not rendered and
   take their model defaults (`is_active` must land True: pass `initial`/handle the
   unchecked checkbox so a type created here is active).
3. **Errors land beside the field.** `EquipmentForm.is_valid()`/`clean()` also validates the
   nested form when `"new"` is chosen. A blank name is the nested form's own required error.
   A duplicate name is checked in `EquipmentForm.clean` against
   `self.instance.owned_orientation_types` (casefold, only when the instance has a pk; a new
   equipment has no types yet), with the message beside the nested name field, because
   `uq_orienttype_equip_name` is conditional and Django's form-level unique check skips it
   (see the comment at `hub/forms.py:2203`).
4. **Atomic save.** `EquipmentForm.save()` wraps in `transaction.atomic()`: save the
   equipment, then when `"new"` was chosen create the type with `equipment=equipment`,
   `guild=None`, set `equipment.required_orientation` and save again. The two views
   (`hub_equipment_add` `hub/equipment_views.py:371`, `hub_equipment_details_save` `:806`)
   keep calling `form.save()`; they need no new branches.
5. **Template.** The Alpine state in `_equipment_form_fields.html` gains the selected
   orientation choice (read from the select on `@change`, initial from the bound value). The
   nested fields render through `components/form_field.html` inside a container with a
   `pl-` class (new rule in `static/css/hub.css`, no inline styles, no inline `display` on
   an `x-show` element: FRONTEND rules 9 and 12). Closed by default; open when the bound
   form posted `"new"` and failed, so the errors are visible. The existing "Create a new
   orientation type" link under the picker stays; it is for guild-owned types.
6. **No migration.** `ck_orienttype_one_owner` and `uq_orienttype_equip_name` stand.
7. **Audience unchanged.** `can_create_equipment` for the add page, `_require_can_manage` for
   Details.

## Acceptance criteria (from the issue, verbatim targets)

- On Add Equipment, an admin can create the equipment, a new orientation type owned by it,
  and set that type as the requirement, in one Save.
- Immediately after that Save, the equipment detail page shows "Orientation needed" to a
  member who has not completed the type, and `reserve()` refuses them; the new type appears
  on Manage > Orientation as an active type with the equipment as its owner.
- The save is atomic: a blank or duplicate type name re-renders the form with the error
  beside the field and creates no equipment row and no type row.
- "No orientation needed" and picking an existing guild-owned type behave exactly as
  before, including the existing guild-match rule in `EquipmentForm.clean`.
- Manage > Details offers the same control, and saving it on existing equipment creates the
  type and sets the requirement in one Save.
- The new type obeys `uq_orienttype_equip_name` and `ck_orienttype_one_owner`.
- Specs cover: create with a new type; rollback on an invalid type; both existing paths
  unchanged; the gate closed on the first page load after creation; the Details tab path.
- The PR shows a screenshot of the add page with the new control revealed.

## Tree facts (verified 2026-09-25 on main at ed7e69d4)

| Thing | Where |
|---|---|
| `EquipmentForm`, its `__init__` queryset and `clean` | `hub/forms.py:4194-4290` |
| `OrientationTypeForm` (price in dollars to `price_cents`) | `hub/forms.py:2105-2172` |
| `BaseOrientationTypeFormSet` duplicate-name comment | `hub/forms.py:2203` |
| `OrientationType` constraints | `membership/models.py:9355-9372` |
| `OrientationType.equipment` FK, `related_name="owned_orientation_types"` | `membership/models.py:9303` |
| `Equipment.required_orientation` (PROTECT, `related_name="gated_equipment"`) | `membership/models.py:11635` |
| `Equipment.access_state`, `booking_blockers` | `membership/models.py:11777`, `:11811` |
| `hub_equipment_add`, `hub_equipment_details_save` | `hub/equipment_views.py:371`, `:806` |
| Shared fields partial | `templates/hub/partials/_equipment_form_fields.html` |
| Add page | `templates/hub/equipment_add.html` |
| Existing specs (`describe_equipment_add` at :201, Details saves at :489 onward) | `tests/hub/equipment_views_spec.py` |
| Reserve service that must refuse an unoriented member | `membership/equipment.py:91-127` |

No e2e spec touches the add page or the Details tab (checked `tests/e2e/`).

## Screenshot

`mockups/screenshots/466-equipment-add-new-orientation.png`: the add page with "New
orientation for this equipment" chosen and the nested fields revealed. Take it with a
throwaway spec under `tests/e2e/` that drives `live_server` with `login_via_code`
(`tests/e2e/conftest.py:67`) as an admin, then delete the spec. Run it on Postgres:
`DATABASE_URL="postgres://plfog:plfog@127.0.0.1:5433/plfog" .venv/bin/pytest tests/e2e/<file> -m e2e --no-cov -o addopts="" -q`.
