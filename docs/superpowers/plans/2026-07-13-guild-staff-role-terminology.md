# Guild Staff Role Terminology — "Guild Lead" & "Orientator" — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-13
**Surface:** FOG hub `pastlives.test` — the guild-edit **Staff** tab (`/guilds/<pk>/edit/`, `?tab=staff`) and the public guild page (`/guilds/<slug>/`).
**Related:** `2026-06-21-guild-orientations.md` (folded orienters into staff roles), `2026-04-16-guild-leads-m2m-design.md`.

---

## 1. Summary

Two guild-staff role names read awkwardly on the Staff tab. A lead adding a staff member can pick "Co-Guild Lead" — but these people are co-equal leads, so the dropdown should say **"Guild Lead"**. And **"Orienter"** should read **"Orientator"**. This is a pure display-label rename: the words a member sees on the role dropdown, the staff-tab badges, and the public guild page. Nothing about who can do what changes.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| What changes | **Labels only.** The stored `TextChoices` **values** (`co_lead`, `orienter`) do **not** change. |
| Why values stay | Renaming the value `orienter` would break the `guild_orienters` resolver (`core/events/resolvers.py:200`) and the reverse of migration `0049` — both filter on `role="orienter"`. Same reasoning protects `co_lead`. |
| Data migration | **None.** No rows are read or written; values are untouched. (See §4 for the one *schema-state* migration Django still generates.) |
| Multiple "Guild Lead" badges | Intended. The Staff tab now assigns a co-equal **"Guild Lead"** title, distinct from the single admin-set `Guild.guild_lead` FK — so a guild page may show several "Guild Lead" badges. Accepted. |

## 2. What already exists (reuse, don't reinvent)

The rename is **one edit** to the `TextChoices`; every render site reads the label through `get_role_display()` and picks it up automatically.

| Need | Existing thing | Location |
|---|---|---|
| The role labels (the only source edit) | `GuildStaffMembership.Role` — `CO_LEAD = "co_lead", "Co-Guild Lead"` / `ORIENTER = "orienter", "Orienter"` | `membership/models.py:1444-1448` |
| Label resolution | `GuildStaffMembership.display_title` → `custom_title or self.get_role_display()` | `membership/models.py:1501-1504` |
| Role dropdown on the add form | `GuildStaffAddForm` builds choices from `GuildStaffMembership.Role.choices` | `hub/forms.py:1373-1376` |
| Staff-tab badges + add form | `{{ sm.display_title }}`, `staff_add_form.role` field | `templates/hub/guild_edit.html:557, 577` |
| Public guild page badges | `{{ sm.display_title }}` in the Staff section | `templates/hub/guild_detail.html:234` |
| Preset-name collision guard | `clean()` rejects a `custom_title` matching any preset label, casefolded, from `Role.choices` | `hub/forms.py:1393-1395` |

**No new plumbing.** Because the badge, dropdown, `__str__`, and collision guard all derive from `Role.choices` / `get_role_display()`, they inherit the new labels the moment the `TextChoices` change lands.

**Collision-guard note (verify, no change expected):** after the rename, "Guild Lead" and "Orientator" become the preset labels the guard blocks. So a member typing "Guild Lead" (or "Orientator") into the *custom title* box is correctly told "That title is already a preset role — pick it from the dropdown instead." That is the intended behavior and needs no code change — the guard reads `Role.choices` live. The only real-world edge is a pre-existing `custom_title` row literally equal to "Guild Lead"/"Orientator" created before this change; those rows are untouched (the guard only runs on *new* adds) and remain valid.

## 3. Where the code lives

Single source edit:

```
membership/models.py:1445   CO_LEAD  = "co_lead",  "Co-Guild Lead"   →  "co_lead",  "Guild Lead"
membership/models.py:1448   ORIENTER = "orienter", "Orienter"        →  "orienter", "Orientator"
```

Plus one auto-generated migration (§4) and the test-string updates (§9). No view, form, template, resolver, or URL edits are required for the labels themselves.

## 4. Migration

There is **no data migration** — but `choices` is part of Django's migration state (see the existing `AlterField` in `membership/migrations/0063_…:26-41`), so `manage.py makemigrations membership` **will** emit one `AlterField` on `role` carrying the new choice labels. This is expected and required:

- It is a **no-op at the database level** — PostgreSQL does not enforce `choices`, so no rows change and no column is rewritten.
- It **must still be generated, `ruff format`ed, `git add`ed, and committed** so the model state matches (repo convention; a stray uncommitted migration or a local `makemigrations --check` would flag drift). Pair the format + `git add` in one step (migrations are easy to leave unformatted).
- Expected filename: the next sequential membership migration, **~`0084_alter_guildstaffmembership_role.py`** (latest on disk is `0083`). Confirm the number at build time.
- Its reverse is the standard auto-generated `AlterField` back to the old labels — no custom reverse function needed.

## 5. Business logic

None. No model methods, managers, guards, or side effects change. Permissions, `leadership_members()`, `staff_by_role()`, and the `guild_orienters` resolver all key off `role` **values**, which are unchanged.

## 6. UI / UX

No layout, component, or markup changes — only the text inside existing badges and the dropdown flips. Both surfaces re-render the new label with zero template edits.

- **Screen — Guild edit → Staff tab** (`templates/hub/guild_edit.html:540-585`):
  - The **role `<select>`** (`staff_add_form.role`, rendered via `components/form_field.html`) now lists: *Choose a role…*, **Guild Lead**, Secretary, Treasurer, **Orientator**. Ordering (declaration order) is unchanged.
  - Existing staff **badges** (`.hub-badge` inside `.pl-staff-chip`) show **Guild Lead** / **Orientator** where they previously showed the old text. Each keeps its `.hub-btn--sm .hub-btn--danger` **Remove** button + `confirm_modal.html` — unchanged.
  - Save/add flow, empty state ("No staff yet — only the guild lead and admins manage this guild."), and the add form's helper text are untouched.
- **Screen — Public guild page** (`templates/hub/guild_detail.html:228-238`): staff **badges** in the Staff section render the new labels via `{{ sm.display_title }}`. Multiple co-equal **Guild Lead** badges may now appear — intended (see §1).
- **States:** unaffected. Empty/populated/error/success all behave exactly as today; no new HTMX swap, no new toast, no new form.
- **Dark + light:** no new CSS, no new form control, no color or token added — the `.hub-badge` / `form_field.html` `<select>` already carry theme-correct tokens on both themes. **Still verify both themes** on the Staff tab and public page after the rename, per the checklist — a label swap should not, and will not, change rendering, but confirm the dropdown text is legible on Obsidian.
- **Mobile:** unchanged; badge group and add-form already reflow.

**UX-checklist lens (applied lightly — this is a label change):** the one real risk is a **hardcoded old label string** living in a template or test instead of coming from `get_role_display()`. §2 confirms every render site is dynamic (no hardcoded "Co-Guild Lead"/"Orienter" badge text). The remaining hardcoded occurrences are (a) descriptive **prose** in the Staff-tab copy and (b) **test assertions** — handled in §9 and §10.

## 7. Notifications / emails / activity

None. No email, notification, `SiteActivity`, or Discord message references these display labels. (`classes/emails.py:46` and `core/events/copy.py:91` mention "orienters" only as descriptive prose about *recipients*, keyed off the role **value** — see §10 for whether to touch that copy.)

## 8. Build order (single phase; ships green)

1. Edit the two labels in `membership/models.py:1445,1448`.
2. `manage.py makemigrations membership` → the no-op `AlterField` (§4); `ruff format .` it and `git add` alongside the model change.
3. Update the label assertions in the four/six test spots (§9).
4. Optionally update descriptive prose for consistency (§10) — decide before building.
5. `ruff format . && ruff check .`; run the affected specs (§9) plus the guild-staff suite in the `plfog-web` Docker image.
6. Bump `plfog/version.py` `VERSION` and add the CHANGELOG entry (§10).

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, run in the `plfog-web` Docker image (`--no-cov` for a subset). No new tests are needed — this is a rename — but existing assertions on the **old label text** must flip. Confirmed occurrences (a `grep` for `"Co-Guild Lead"` and `"Orienter"` as exact display strings):

| File:line | Current assertion | Update to |
|---|---|---|
| `tests/membership/guild_staff_model_spec.py:38` | `sm.display_title == "Orienter"` | `"Orientator"` |
| `tests/membership/guild_staff_model_spec.py:88` | `labels == ["Co-Guild Lead", "Secretary", "Orienter"]` | `["Guild Lead", "Secretary", "Orientator"]` |
| `tests/membership/guild_staff_model_spec.py:130` | `titles == ["Orienter", "Glaze Technician"]` | `["Orientator", "Glaze Technician"]` |
| `tests/membership/guild_staff_model_spec.py:140` | `titles == ["Co-Guild Lead", "Treasurer", "Glaze Technician", "Studio Technician"]` | `["Guild Lead", "Treasurer", "Glaze Technician", "Studio Technician"]` |
| `tests/hub/guild_staff_spec.py:286` | `assert "Orienter" in content` (public page badge) | `assert "Orientator" in content` |
| `tests/hub/guild_staff_spec.py:304` | `assert "Orienter" in content` (staff-tab badge) | `assert "Orientator" in content` |

Notes / gotchas:
- These specs use `Role.CO_LEAD` / `Role.ORIENTER` **enums** to *create* rows (e.g. `guild_staff_model_spec.py:47,86,138`; `guild_staff_spec.py:280`) — those stay as-is; only the **string assertions on the label** change.
- `tests/hub/guild_staff_spec.py:304` is a **weak** assertion today: the Staff-tab page also contains the prose word "Orienters"/"orienters" (template lines 270, 544), so `"Orienter" in content` passes even from prose. Switching it to `"Orientator"` — which is **not** a substring of "Orienters" — makes it genuinely assert the *badge* label. If §10's prose update is **not** done, this is the correct, tighter assertion; if the prose is updated, it still holds (the badge is the source).
- `tests/core/events/resolvers_spec.py` (`guild_orienters`) and `tests/membership/permissions_spec.py` / `tests/hub/orienters_spec.py` assert on **behavior/values**, not the display label — no change.

## 10. Open / deferred

- **Descriptive prose using the old names (recommend updating for consistency; flag before build).** These are hardcoded member-facing strings that *describe* the roles but aren't the `TextChoices` label, so they're outside the strict "labels only" decision — call it explicitly:
  - `templates/hub/guild_edit.html:270` — "Orienters are now part of your guild staff…"
  - `templates/hub/guild_edit.html:544` — "Co-leads, secretaries, treasurers, and orienters. Every staff member…"
  - Recommendation: update "orienters" → "orientators"; leave "Co-leads" wording to the reviewer (arguably still accurate as description, but "Guild Leads" would match the new dropdown). Low-risk either way; harmless to defer, but a keen user will notice the dropdown says "Orientator" while the helper text says "orienters."
  - Explicitly **out of scope:** the *recipient-description* prose in `classes/emails.py:46` and `core/events/copy.py:91` ("orienters") — internal/adjacent copy, not the guild-staff UI; leave unless the user asks.
- **Changelog:** this is a small, member-visible wording polish on a feature **already live** (guild staff shipped earlier). Per the versioning rules it earns a short, plain entry — e.g. *"The guild Staff tab now calls co-equal leads 'Guild Lead' and renames 'Orienter' to 'Orientator.'"* — stamped at the new `VERSION`. If a same-line unreleased guild-staff entry exists at build time, fold it in and re-stamp instead of adding a second entry.
- **No back-fill / no announcement of badge changes** — existing rows keep their values and simply display the new words on next render.
