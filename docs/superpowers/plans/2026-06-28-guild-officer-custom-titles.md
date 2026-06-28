# Guild officer custom titles

**Date:** 2026-06-28
**Surface:** FOG hub — Guild Page → **Staff** tab (`templates/hub/guild_edit.html`), public guild page (`guild_detail.html`)
**Scope:** Small, additive. One new field on one existing model. No data backfill.

## What the user wants

On a Guild Page's Staff tab, a guild officer (staff/lead/admin) can give a staff member a **made-up title** in
addition to — or instead of — the four preset roles. Example: Sean is **Orienter** *and* **Studio Technician**;
Nicole is **Orienter** *and* **Glaze Technician**. "Studio Technician" / "Glaze Technician" aren't in the preset
list, so they're typed in free-hand.

Two facts that shape the build:

1. **Multiple roles already work.** `GuildStaffMembership` is keyed on `(guild, member, role)`, so a member can
   already hold several roles — you add them one at a time. Nothing to build there.
2. **Titles are cosmetic.** Every guild officer role grants the *same* privilege (guild-edit, like the lead) —
   `Member.can_edit_guild` checks `guild.is_staffed_by(self)`, never the specific role. So a custom title is a
   pure label; **no permission logic changes.**

The feature is therefore exactly: **let a staff entry carry a free-text custom title instead of a preset role.**

## Locked decisions

| Decision | Choice | Why |
|---|---|---|
| Title model | Each staff entry (row) is **either** one preset role **or** one custom title | Matches "X in addition to Orienter" = two independent rows, each removable. Stays additive — no backfill. |
| Add UX | Mirror the **member-skills** pattern: a preset role `<select>` **plus** a separate "or type a custom title" text input | User asked to copy the skills directory's preset-plus-free-text shape. |
| Approval / moderation | **None** — custom titles take effect immediately | Staff are trusted; user asked for "simple". (Unlike skills' pending-admin-approval — deliberately dropped.) |
| Permissions | Unchanged | Titles are cosmetic; `is_staffed_by` already governs edit rights. |
| Transport | Keep the existing full-page POST + redirect on the Staff tab (not HTMX) | The Staff tab isn't HTMX today; converting it is out of scope. We copy the skills *input shape*, not its transport. |
| Title length | 60 chars max, trimmed | Fits a badge; matches skills' tight cap. |

## What already exists (reuse, don't reinvent)

| Piece | Location |
|---|---|
| Model | `membership/models.py` → `GuildStaffMembership` (role `TextChoices`: CO_LEAD/SECRETARY/TREASURER/ORIENTER) |
| Grouping for display | `Guild.staff_by_role()` in `membership/models.py` → returns `[(label, [memberships])]` |
| Add form | `hub/forms.py` → `GuildStaffAddForm` (member `ModelChoiceField` + role `ChoiceField`) |
| Add/remove views | `hub/views.py` → `guild_staff_add`, `guild_staff_remove` |
| Edit template | `templates/hub/guild_edit.html` (Staff tab, ~line 288) |
| Public template | `templates/hub/guild_detail.html` (~line 189, staff badges) |
| Tests | `tests/hub/guild_staff_spec.py`, `tests/membership/guild_staff_model_spec.py` |
| Factory | `tests/membership/factories.py` → `GuildStaffMembershipFactory` |
| Free-text-beside-preset reference | member skills: `templates/hub/partials/profile_skills.html`, `hub/forms.py` `SkillSuggestionForm` |

## Data model

Add one field and tighten constraints on `GuildStaffMembership` — all additive, existing rows stay valid:

```python
role = models.CharField(
    max_length=20, choices=Role.choices, blank=True, default="",
    help_text="A preset officer role. Leave blank when this entry uses a custom title instead.",
)
custom_title = models.CharField(
    max_length=60, blank=True, default="",
    help_text="A free-text officer title (e.g. 'Studio Technician') used instead of a preset role.",
)
```

```python
class Meta:
    constraints = [
        # Exactly one of role / custom_title is set — fail loudly on a blank-or-both row.
        models.CheckConstraint(
            name="ck_guildstaff_role_xor_custom_title",
            condition=(
                (models.Q(role="") & ~models.Q(custom_title=""))
                | (~models.Q(role="") & models.Q(custom_title=""))
            ),
        ),
        # No duplicate preset role per member per guild (replaces the old uq on (guild, member, role)).
        models.UniqueConstraint(
            fields=["guild", "member", "role"], condition=~models.Q(role=""),
            name="uq_guildstaff_member_role",
        ),
        # No duplicate custom title per member per guild.
        models.UniqueConstraint(
            fields=["guild", "member", "custom_title"], condition=~models.Q(custom_title=""),
            name="uq_guildstaff_member_custom_title",
        ),
    ]
```

Add a display helper and route every label through it:

```python
@property
def display_title(self) -> str:
    """The label to show for this staff entry — the custom title if set, else the preset role's label."""
    return self.custom_title or self.get_role_display()
```

- `__str__` uses `display_title`.
- `Guild.staff_by_role()` groups by `display_title` (so each custom title becomes its own heading, exactly like
  a preset). Keep the existing preset ordering first, then custom titles alphabetically, so the list is stable.

**Migration:** one migration — `AlterField(role, blank=True)`, `AddField(custom_title)`, remove the old
`uq_guildstaff_guild_member_role`, add the three constraints above. Existing rows all have a preset `role` and
blank `custom_title`, so they satisfy the check and the role uniqueness constraint untouched. **No `RunPython`,
no data backfill.** (Constraint-only migrations don't need a reverse beyond Django's auto-generated one.)

## Form & view

`GuildStaffAddForm` (`hub/forms.py`):

- `role` becomes `required=False` (still a `ChoiceField` populated from `Role.choices`, with a blank "Choose a
  role…" option).
- Add `custom_title = forms.CharField(max_length=60, required=False)`.
- `clean()`:
  - Trim `custom_title`.
  - **Exactly one** of `role` / `custom_title` must be provided → raise `ValidationError("Pick a preset role or
    type a custom title — not both.")` if both, and `"Pick a role or type a custom title."` if neither.
  - Duplicate guard: if the member already holds that preset role *or* that custom title (case-insensitive) on
    this guild, raise a friendly `ValidationError`.

`guild_staff_add` view: read `role`/`custom_title` from `form.cleaned_data`; create the
`GuildStaffMembership` with whichever is set (the other stays `""`). Success message uses `display_title`.
`guild_staff_remove` is unchanged (it already removes a single row by pk and shows `get_role_display()` — switch
that to `display_title`).

## UI / UX (completeness checklist — this is the point)

Staff tab (`guild_edit.html`), inside the existing `hub-card`:

- **Existing staff display:** keep the grouped-by-title list. Each row shows the member's `display_name` and a
  margin-spaced **Remove** button — already `hub-btn hub-btn--sm hub-btn--danger` firing the existing confirm
  modal (`$dispatch('open-confirm', …)`). Custom-title groups render identically to preset groups. ✔ delete
  control present, ✔ confirm modal.
- **Add form** (mirrors member-skills' preset-plus-free-text shape), all fields via `components/form_field.html`:
  1. **Member** dropdown (existing, active members minus the lead).
  2. **Preset role** `<select>` with a blank "Choose a role…" first option.
  3. **Custom title** text input, labelled *"…or type a custom title"*, `maxlength=60`,
     placeholder `e.g. Studio Technician`.
  4. A visible **"Add staff member"** submit button wired to this form (`hub-btn hub-btn--sm hub-btn--primary`).
  - Helper line under the two role inputs: *"Pick a preset role or write your own title."* so the either/or is
    obvious before submit.
- **States:**
  - *Empty:* when the guild has no staff, show the existing "No staff yet" style muted line above the add form.
  - *Error:* form validation errors surface via the existing Django `messages` flash on redirect (both-fields
    and neither-field cases have plain-language messages above).
  - *Success:* `messages.success` "{name} is now {title} of {guild}." on redirect to `?tab=staff`.
  - *Duplicate:* friendly `messages` info, no crash.
- **Margins:** the custom-title input and Add button clear the elements above them on the 8px grid (match the
  existing field spacing in the card; the Add button keeps its current top margin).
- **Mobile:** the add row already wraps (`flex-wrap`); the new text input sits on its own line on narrow screens
  — no horizontal scroll, tap targets stay full-width.
- **Dark + light:** use `form_field.html` / existing `.hub-` inputs only — **no inline `background`/`color`** on
  the new input, so both themes inherit `--hub-*` tokens. (This is the recurring dark-mode form pitfall.)

Public guild page (`guild_detail.html`): the staff role badges already render a label per membership — switch
that label to `display_title` so custom titles appear as badges alongside presets. No layout change.

## Tests (BDD `*_spec.py`, `describe_`/`it_`, factory-boy)

- **Model** (`guild_staff_model_spec.py`): `display_title` returns custom title when set, else preset label;
  `__str__` uses it; `staff_by_role()` groups custom titles as their own headings; the check constraint rejects
  a row with neither / both set; the two partial unique constraints reject duplicate preset role and duplicate
  custom title but **allow** the same member holding a preset role *and* a custom title.
- **Form** (`guild_staff_spec.py` or a forms spec): adds with a preset role; adds with a custom title; rejects
  both-set; rejects neither-set; rejects duplicate custom title (case-insensitive); trims whitespace.
- **View** (`guild_staff_spec.py`): POSTing a custom title creates a custom-title row and it shows on the tab;
  POSTing a preset role still works; permission gate unchanged.
- **Factory:** extend `GuildStaffMembershipFactory` so a `custom_title` trait is easy (e.g. a
  `factory.Trait` or a second factory) without breaking existing callers (default stays a preset role).

## Out of scope

- No new vocabulary tables, no categories, no admin-approval/pending workflow (deliberately simpler than skills).
- No HTMX conversion of the Staff tab.
- No permission/role-power changes — every officer title is functionally identical, as today.
- No editing a title in place (remove + re-add covers it; YAGNI).

## Done when

- A lead can add a member with a typed custom title on the Staff tab and it appears (edit + public page).
- A member can hold a preset role and a custom title simultaneously; duplicates of either are blocked with a
  friendly message.
- Both themes and mobile render the new input cleanly; tests cover model/form/view at the repo coverage gate;
  `VERSION` bumped to `0.19.17` with one member-friendly CHANGELOG entry.
