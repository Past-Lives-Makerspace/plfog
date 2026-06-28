# Guild staff: one person, all their titles as badges

**Commit 4 of release 0.20.x.** Surface: public guild page (`templates/hub/guild_detail.html`) and the Staff tab
(`templates/hub/guild_edit.html`). Model helper in `membership/models.py`.

## The ask

A staff member can already hold several titles (each title is its own `GuildStaffMembership` row — preset role or custom
title, shipped in 0.19.17). But the page currently **groups by title**, so one person appears once per title under
separate headings. Show each **person once, with all their titles as badges** — e.g. *Sean — `Orienter` `Glaze
Technician` `Treasurer`*. "Might need a UX update" → yes, a regroup.

## Current state (verified)

- `Guild.staff_by_role()` (`membership/models.py:1055-1078`) groups `GuildStaffMembership` rows **by `display_title`**,
  returning `[(title_label, [memberships])]`.
- `GuildStaffMembership.display_title` (custom_title or preset label) already exists.
- Public page `guild_detail.html:189-212` loops those groups; each row renders the member name + a single `hub-badge`
  for that title.
- Staff tab `guild_edit.html:298-319` loops the same groups; each row shows the name + a per-row **Remove** button.
- `.hub-badge` exists (`hub.css:1008-1017`); `.hub-member-row` / `.hub-member-info` layout exists.

## Design

Add a member-grouped helper and switch both displays to it.

```python
def staff_by_member(self) -> list[tuple[Member, list[GuildStaffMembership]]]:
    """Each staff member once, with all their title rows, for badge display.

    Members sorted by name; each member's rows ordered presets-first (role-declaration
    order) then custom titles alphabetically, mirroring staff_by_role's stable ordering.
    """
```

- Build it from `self.staff_memberships.select_related("member")` (no N+1). Group rows by `member_id`; sort members by
  `full_legal_name`/`display_name` (match the existing case-insensitive sort); within a member, order rows presets-first
  then custom alphabetically (reuse the ordering logic from `staff_by_role`). Keep `staff_by_role()` too if anything
  else uses it (search usages; if only these two templates use it, this can replace it — but additive is safer).
- **Public page** (`guild_detail.html`): the Guild Lead row stays. Replace the per-title loop with a per-member loop:
  one `.hub-member-row` per staff member, name once, then a horizontal group of `.hub-badge`s (one per title). Use a flex
  wrap with small gap so multiple badges sit inline and wrap on narrow screens.
- **Staff tab** (`guild_edit.html`): same per-member grouping. Name once, all title badges inline. The **Remove** control
  must stay **per-title** (removing one title, not the whole person) — render a small remove affordance per badge
  (e.g. a badge followed by a `hub-btn hub-btn--sm hub-btn--danger` "Remove" that targets that `sm.pk` via the existing
  confirm modal `del-staff-{{ sm.pk }}`), or a compact "× " on each badge wired to the same per-row remove. Keep the
  add-staff form below unchanged (member + preset role OR custom title + "Add staff member").

## UI / UX completeness

- One row per person; titles render as multiple `.hub-badge`s, wrapping on mobile (no horizontal scroll).
- Per-title Remove preserved on the edit tab (confirm modal, `hub-btn--sm hub-btn--danger` — never a raw full-size
  Delete), so a person with three titles can lose one and keep the rest.
- Empty state unchanged ("No staff yet…"); lead still shown with the "Lead" badge.
- Dark/light: `.hub-badge` already theme-tokened; no inline `background`/`color`. Badge group uses a flex container with
  gap, not inline styles that break theming.
- Avatar/initial + name layout reuses `.hub-member-row`/`.hub-member-avatar`/`.hub-member-info`.

## Tests

- Model: `staff_by_member()` returns each member once with all rows; ordering stable (presets first, then custom alpha);
  members sorted by name; no extra queries beyond the single `select_related` (assert with `django_assert_num_queries`).
- Template/view: a member with a preset role + a custom title shows once with two badges on both the public page and the
  Staff tab; per-title Remove targets the correct `sm.pk`.
- BDD `*_spec.py`, `describe_`/`it_`, factory-boy (the `custom` trait from 0.19.17).

## Out of scope

- Changing how titles are added/removed (the 0.19.17 add form + per-row remove stay).
- Reordering/prioritizing titles per person beyond the stable presets-then-custom ordering (no drag-sort — YAGNI).
