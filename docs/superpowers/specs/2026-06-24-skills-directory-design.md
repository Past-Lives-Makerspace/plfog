# Skills Directory & "Open for Commissions" — Design

**Date:** 2026-06-24
**Status:** Draft for review
**Author:** Spec drafted with Claude; design owner Jo (Covo)
**App(s) touched:** `membership` (models, migrations, admin), `hub` (views, forms, templates, template tags), `tests/`

---

## 1. Summary

Members can tag their **skills** on their directory profile, optionally noting **years of experience** per skill, and flag themselves as **open for commissions** (contract work, custom jobs, consulting) with a short note about what they take on. The member directory becomes a **searchable, filterable skills directory**: members can find each other by skill and surface everyone currently open for paid/commissioned work.

Concrete example (Jo's own profile): *Coding*, *Small woodworking projects*, *Music production*, *Website design & consulting*, *AI development & consulting* — flagged **Open for commissions!** with a note welcoming people to reach out.

This builds directly on the existing member directory (`hub.views.member_directory`, `templates/hub/member_directory.html`) and the existing profile-settings + directory-visibility machinery (`hub.forms.ProfileSettingsForm`, `Member.directory_visibility`, `Member.DIRECTORY_TOGGLEABLE_FIELDS`). It mirrors the established many-to-many affiliation pattern used for guilds (`GuildMembership` at `membership/models.py:995`).

---

## 2. Goals / Non-Goals

### Goals
- Members add skills to their profile from a **shared, curated vocabulary** so filtering actually groups people (no "Woodworking" vs "woodworking" vs "wood working" fragmentation).
- Optional **years of experience** per skill, member's choice to show or omit.
- A clear, attractive **"Open for commissions!"** badge and a short member-written note describing the work they take on.
- Directory **search by skill name** and **filter by skill** + **filter to "open for commissions only"**, composable with the existing guild filter.
- Skills respect the existing **directory privacy** model: a member who isn't listed in the directory isn't surfaced by skill search.
- The skill vocabulary can **grow organically** without a developer: admins curate, members can suggest new skills.

### Non-Goals (YAGNI — explicitly out of scope for v1)
- No ratings, endorsements, or peer verification of skills.
- No in-app messaging, booking, quoting, or payments for commissions. Contact happens through the member's existing directory contact fields (email/phone/Discord/other). The makerspace is not brokering the work.
- No per-skill portfolios, images, or rich text. (A future "instructor profile"-style page could host this; not now.)
- No skill hierarchy/synonyms/aliasing engine. Flat categories + curated names is enough at makerspace scale.

---

## 3. Key Design Decisions (with rationale)

### 3.1 Curated, categorized vocabulary — not free text
**Decision:** A `Skill` model (admin-curated, grouped by `SkillCategory`), seeded with a large starter list. Members pick skills from this list; they don't type arbitrary strings into the directory.

**Why:** The whole point is *search and filter*. Free-text tags fragment instantly (case, plurals, phrasing) and make filtering useless. A controlled vocabulary keeps "Woodworking" a single filterable thing that 30 members can share.

**Escape hatch so it doesn't become a bottleneck:** members can **suggest a new skill** inline. A suggested skill is created with `status = PENDING` and is visible **only on the suggester's own profile** until an admin approves it (`status = APPROVED`), at which point it joins the public vocabulary and filters. This gives organic growth without a developer in the loop and without polluting the public directory. Admins approve/merge from Django admin.

**Alternative considered (rejected):** fully free-form member tags with a normalization pass. Rejected: normalization is never good enough, and it pushes cleanup cost onto admins forever. The suggest-then-approve flow front-loads the one decision that matters (is this a real, distinct skill?).

### 3.2 Join model with optional metadata — `MemberSkill`
**Decision:** `MemberSkill(member FK, skill FK, years_experience nullable, created_at)` with a uniqueness constraint on `(member, skill)`. This mirrors `GuildMembership` (`membership/models.py:995`) and `GuildStaffMembership` (`:848`) exactly, so it slots into existing conventions.

`years_experience` is a nullable `PositiveSmallIntegerField` — entirely optional, omitted by default, shown next to the skill only when set.

### 3.3 "Open for commissions" lives on `Member`
**Decision:** Two new fields on `Member`:
- `open_for_commissions: BooleanField(default=False)` — drives the badge and the directory filter.
- `commission_note: CharField(max_length=280, blank=True)` — short, member-written ("Small custom woodworking, website builds, AI consulting — happy to chat!"). 280 chars keeps it a tagline, not an essay.

Contact details reuse the existing directory contact fields. No new contact plumbing.

**Why on `Member` and not a separate model:** it's 1:1 with the member and queried on every directory render; a flat boolean filters cheaply and indexes well.

### 3.4 Privacy follows the existing pattern
**Decision:** Add `"skills"` to `Member.DIRECTORY_TOGGLEABLE_FIELDS` so the skills section gets the same per-member show/hide toggle every other directory field already has (`Member.is_public("skills")`, the `show_skills` dynamic form field in `ProfileSettingsForm`). The commission badge is governed by the same `"skills"` toggle (skills + commissions are one logical "what I make / will I make it for you" section).

Hard rule, enforced in the view: a member who is **not** surfaced in the directory (`show_in_directory=False` and not force-listed) is **never** returned by a skill search, even with a direct `?skill=` URL. Skill filtering composes with — never bypasses — the existing visibility filter at `hub/views.py:250-252`.

### 3.5 Filtering reuses the guild-filter pattern, progressively enhanced
**Decision:** Extend the existing GET-param filter form on the directory (`?guild=…`) with:
- `?skill=<id>` — filter to members who have that skill (public/approved skills only in the dropdown).
- `?commissions=1` — filter to `open_for_commissions=True`.
- `?q=<text>` — case-insensitive contains match against skill names (and the member display name), so typing "weld" finds welders.

All compose with each other and with `?guild=`. Baseline works with plain form `onchange` submit (matching today's directory). Optional progressive enhancement: HTMX swap of the results grid for a live feel (`FRONTEND.md` conventions). The filter UI is the only genuinely new piece of front-end.

---

## 4. Data Model

New models in `membership/models.py`:

```python
class SkillCategory(models.Model):
    """A grouping of related skills (e.g. Woodworking, Software & Tech)."""
    name = models.CharField(max_length=100, unique=True, help_text="Display name of the category.")
    slug = models.SlugField(max_length=120, unique=True, help_text="URL-safe identifier.")
    sort_order = models.PositiveSmallIntegerField(default=0, help_text="Lower sorts first in pickers.")

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "skill categories"

    def __str__(self) -> str:
        return self.name


class Skill(models.Model):
    """A single skill members can list, drawn from a curated vocabulary."""
    class Status(models.TextChoices):
        APPROVED = "approved", "Approved"
        PENDING = "pending", "Pending review"

    name = models.CharField(max_length=80, unique=True, help_text="Canonical skill name shown everywhere.")
    slug = models.SlugField(max_length=100, unique=True, help_text="URL-safe identifier for filtering.")
    category = models.ForeignKey(
        SkillCategory, on_delete=models.PROTECT, related_name="skills",
        help_text="The category this skill belongs to.",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.APPROVED,
        help_text="Approved skills appear publicly; pending skills are member suggestions awaiting review.",
    )
    suggested_by = models.ForeignKey(
        "Member", null=True, blank=True, on_delete=models.SET_NULL, related_name="suggested_skills",
        help_text="Member who proposed this skill, if it came from a suggestion.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the skill entered the vocabulary.")

    class Meta:
        ordering = ["category__sort_order", "name"]
        indexes = [
            models.Index(fields=["status", "name"], name="idx_%(class)s_status_name"),
        ]

    def __str__(self) -> str:
        return self.name


class MemberSkill(models.Model):
    """A skill claimed by a member, with optional years of experience."""
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="skills")
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name="member_links")
    years_experience = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Optional years of experience, shown beside the skill when set.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the member added this skill.")

    class Meta:
        ordering = ["skill__category__sort_order", "skill__name"]
        constraints = [
            models.UniqueConstraint(fields=["member", "skill"], name="uq_%(class)s_member_skill"),
        ]

    def __str__(self) -> str:
        years = f" ({self.years_experience}y)" if self.years_experience is not None else ""
        return f"{self.member.display_name} — {self.skill.name}{years}"
```

New fields on `Member`:

```python
open_for_commissions = models.BooleanField(
    default=False,
    help_text="When on, the member shows an 'Open for commissions!' badge and appears in that filter.",
)
commission_note = models.CharField(
    max_length=280, blank=True,
    help_text="Short note on the kind of paid/commissioned work the member welcomes.",
)
```

Add `"skills"` to `Member.DIRECTORY_TOGGLEABLE_FIELDS` (currently at `membership/models.py:297`).

### Model helpers (fat model)
- `Member.public_skills` (property): approved skills for this member **plus** the member's own pending suggestions — but the directory/listing path uses `approved_skills` only. Two clearly named accessors so the "show my pending suggestion to me, not to others" rule lives in the model, not a view.
- `Member.approved_skills` (property): `self.skills.filter(skill__status=Skill.Status.APPROVED).select_related("skill__category")`.
- `MemberManager.with_skill(slug)` / `.open_for_commissions()` / `.search_skills(text)` (manager methods): the queryset-building logic for the directory filter, per the fat-model / skinny-view standard. These compose onto the visibility-filtered base queryset.

### Migrations
1. `005X_skills_directory.py` — create `SkillCategory`, `Skill`, `MemberSkill`; add `open_for_commissions`, `commission_note` to `Member`; extend `DIRECTORY_TOGGLEABLE_FIELDS` is code-only (no migration).
2. `005X+1_seed_skills.py` — **data migration** that populates categories and the starter skill list (§7). Includes a reverse function that deletes only the seeded rows (by slug), per the project rule that data migrations must reverse — never `RunPython.noop`.

---

## 5. Forms & Editing UX (`hub/forms.py`, profile settings)

Extend the **Profile** tab of user settings with a **"Skills & Commissions"** section, below the existing fields.

- **Skill picker:** an Alpine.js-powered multi-select / autocomplete over the approved vocabulary, grouped by category. Selecting a skill adds a chip; each chip has an optional small "years" input. Removing a chip removes the `MemberSkill`. No new JS framework — Alpine `x-data` over a JSON list of skills rendered server-side, matching `FRONTEND.md`.
- **Suggest a skill:** a small "Can't find it? Suggest a skill" affordance. Submitting creates a `Skill(status=PENDING, suggested_by=member)` and an attached `MemberSkill`. The member sees it immediately (labeled "pending review"); it's invisible to others and to filters until approved.
- **Open for commissions:** a `components/toggle.html` boolean. When on, it reveals the `commission_note` text field (`components/form_field.html`). When off, the note is retained but hidden.
- **Visibility:** the existing dynamic `show_skills` toggle (auto-generated from `DIRECTORY_TOGGLEABLE_FIELDS`) governs whether the section appears in the directory.

Persistence:
- `ProfileSettingsForm` gains `open_for_commissions` and `commission_note` in its `Meta.fields` (`hub/forms.py:202`).
- Skill add/remove/suggest and per-skill years are handled by **dedicated small endpoints** (HTMX-friendly), not crammed into the monolithic profile form — keeps each unit focused and testable. New hub views: `skill_add`, `skill_remove`, `skill_suggest`, returning a toast via `trigger_toast()` (`FRONTEND.md` rule 6). Validation (e.g. years in a sane range, duplicate guard, suggestion not duplicating an existing approved skill case-insensitively) lives in a `MemberSkillForm` / `SkillSuggestionForm` in `hub/forms.py`, never in the view.

---

## 6. Directory View & Template

### View (`hub/views.py:member_directory`)
Extend the existing view. The visibility-filtered base queryset (`hub/views.py:250-252`) is the *floor*; skills filtering only ever narrows it.

```
member_qs = <existing visibility-filtered ACTIVE queryset>
if guild_filter: ...                                  # unchanged
if skill_slug:   member_qs = member_qs.with_skill(skill_slug)
if commissions:  member_qs = member_qs.open_for_commissions()
if q:            member_qs = member_qs.search_skills(q)   # name + skill-name contains
member_qs = member_qs.prefetch_related("skills__skill__category")  # avoid N+1 on cards
```

Context additions: `skills` (approved skills for the filter dropdown, grouped by category), `selected_skill`, `commissions_only`, `query`.

### Template (`templates/hub/member_directory.html`)
- **Filter bar:** extend the existing guild-filter form (`:10-18`) with a skill `<select>` (grouped `<optgroup>` by category), an "Open for commissions only" checkbox, and a search text input. Same `onchange`/submit pattern; HTMX swap of `#directory-grid` as progressive enhancement.
- **Card additions** (mirroring the guild-badge block at `:127-136`):
  - A **skills row** of chips, each `Skill.name` with `(Ny)` appended when years are set. Only shown when `member.is_public("skills")` and the member has approved skills.
  - An **"Open for commissions!"** badge (accent-colored, using `--color-tuscan-yellow`) when `member.open_for_commissions` and the section is public, with the `commission_note` beneath it.
- New template tag if needed in `hub/templatetags/hub_tags.py`; reuse the existing `is_public` filter for the section gate.

---

## 7. Starter Skill Vocabulary (seed data)

The data migration seeds categories and a broad starter list (admins extend later; members suggest). Representative, not exhaustive — the migration will carry the full set:

- **Woodworking:** Furniture making, Small woodworking projects, Cabinetry, Wood turning, Carving, Joinery, CNC routing, Finishing & refinishing
- **Metal & Jewelry:** Welding (MIG/TIG), Blacksmithing, Silversmithing, Lost-wax casting, Machining, Sheet metal, Engraving, Stone setting
- **Textiles & Fiber:** Sewing, Garment making, Quilting, Weaving, Knitting, Crochet, Embroidery, Screen printing, Dyeing
- **Leather:** Leatherworking, Bag & wallet making, Tooling & carving, Bookbinding
- **Ceramics & Glass:** Wheel throwing, Hand-building, Glazing, Kiln firing, Stained glass, Lampworking, Glassblowing
- **Paper & Print:** Letterpress, Printmaking, Linocut, Risograph, Zine making, Calligraphy
- **Electronics & Fab:** Electronics & soldering, Microcontrollers (Arduino/Pi), Robotics, 3D modeling (CAD), 3D printing, Laser cutting, PCB design
- **Software & Tech:** Coding, Web development, Website design & consulting, AI development & consulting, Mobile apps, Game development, Data & automation, IT & networking
- **Music & Audio:** Music production, Audio engineering, Mixing & mastering, Songwriting, Instrument repair, DJing, Live sound
- **Photo & Video:** Photography, Videography, Photo editing, Video editing, Motion graphics, Lighting
- **Art & Design:** Illustration, Painting, Graphic design, UX/UI design, Branding & logos, Murals, Sculpture, Animation
- **Writing & Media:** Copywriting, Editing, Technical writing, Grant writing, Social media
- **Trades & Misc:** Carpentry, Electrical, Plumbing, Upholstery, Sign making, Prop & set building, Teaching & workshops, Consulting

(Jo's profile, as the worked example, maps to: *Coding*, *Small woodworking projects*, *Music production*, *Website design & consulting*, *AI development & consulting* + Open for commissions.)

---

## 8. Admin

- Register `SkillCategory`, `Skill`, `MemberSkill` (auto-registration already covers new models; add a custom `SkillAdmin` for the approval workflow).
- `SkillAdmin`: `list_display = (name, category, status, suggested_by)`, `list_filter = (status, category)`, a **"Approve selected skills"** admin action, and a guard/merge path for "this suggestion duplicates an approved skill" (admin reassigns the member's `MemberSkill` to the canonical skill, deletes the dup).
- `MemberSkill` inline on the `Member` admin for support/debugging.

---

## 9. Testing (BDD specs, 100% coverage target)

New spec files following the `describe_`/`it_` style:
- `tests/membership/skills_models_spec.py` — `Skill`/`MemberSkill`/`SkillCategory` `__str__`, uniqueness constraint, `approved_skills` vs pending-visibility, manager methods (`with_skill`, `open_for_commissions`, `search_skills`).
- `tests/hub/member_directory_skills_spec.py` — directory shows skill chips & years; respects `is_public("skills")`; **hidden members never surface via `?skill=`**; `?commissions=1` filters; `?q=` search; filters compose with `?guild=`.
- `tests/hub/profile_skills_spec.py` — add/remove skill, set years, toggle commissions + note, suggest-a-skill creates `PENDING` + visible only to suggester; validation (years range, duplicate guard, case-insensitive suggestion collision).
- `tests/membership/skills_admin_spec.py` — approve action flips status and makes the skill public/filterable.
- New factories in `tests/membership/factories.py`: `SkillCategoryFactory`, `SkillFactory`, `MemberSkillFactory`.

---

## 10. Versioning & Changelog

Bump `plfog/version.py` (current `0.19.0`) and add a **member-friendly** changelog entry, e.g.:

> **Show off your skills — and let people know you're open for commissions**
> Your directory profile can now list the things you make and do, from woodworking and welding to music production and web design, with optional years of experience. Flip on "Open for commissions!" and add a short note if you're happy to take on custom work, contract jobs, or consulting. And you can now search and filter the member directory by skill to find the right person for a project.

---

## 11. Build Order (for the implementation plan)

1. Models + migration + seed data migration (with reverse). Admin registration + approval action.
2. Member model helpers/managers + `open_for_commissions`/`commission_note` fields + `DIRECTORY_TOGGLEABLE_FIELDS` update.
3. Directory view filtering + template filter bar + card chips/badge.
4. Profile settings editing (picker, years, commissions toggle, suggest-a-skill) + small HTMX endpoints + forms/validation.
5. Tests to 100% coverage at each step (TDD).
6. Version bump + changelog.

---

## 12. Open Questions (decide before / during implementation)

1. **Skill cap per member?** Suggest a soft cap (e.g. 15) to keep cards readable. Default: cap at 15, configurable constant.
2. **Suggestion moderation load:** is admin approval acceptable, or should trusted roles (guild officers) also approve? Default v1: admins only.
3. **Commission note length:** 280 chars proposed — confirm it's enough for a tagline without becoming an essay.
4. **Search scope:** should `?q=` also match the `commission_note` text? Default: no (skill names + display name only) to keep results predictable.
