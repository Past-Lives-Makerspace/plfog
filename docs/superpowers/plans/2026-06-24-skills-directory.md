# Skills Directory & "Open for Commissions" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let members tag curated skills (with optional years of experience) and a "Open for commissions!" flag on their directory profile, and make the member directory searchable/filterable by skill and commission availability.

**Architecture:** A curated, categorized `Skill` vocabulary (`SkillCategory` → `Skill`) with a member→skill join (`MemberSkill`, optional years), mirroring the existing `GuildMembership` pattern. Two flat fields on `Member` drive commissions. Directory filtering extends the existing `member_directory` view/queryset and never bypasses the existing directory-visibility floor. Editing happens through small HTMX endpoints that re-render a chip partial and fire a toast — the established `guild_staff_add`/`guild_product_*` pattern.

**Tech Stack:** Django 5 (Python 3.13), pytest + pytest-describe (BDD `describe_`/`it_`), factory-boy, Alpine.js 3.x + HTMX (no build step), custom CSS with `pl-` prefix.

**Spec:** `docs/superpowers/specs/2026-06-24-skills-directory-design.md`

## Global Constraints

Every task implicitly includes these (values copied verbatim from the spec and project standards):

- **Line length:** 120. Run `ruff format . && ruff check --fix .` before every commit; `mypy .` must pass.
- **Type everything**, including `-> None`. Use `from __future__ import annotations`.
- **Fat models, skinny views.** Validation in forms, querysets in the manager, business logic in models. No logic in views.
- **`help_text` on every model field. `TextChoices` for choices. Meaningful `__str__`. `default=dict` never `default={}`.**
- **Fail loudly:** `dict[key]` not `dict.get(key, default)` when the key must exist.
- **Testing:** BDD spec files named `*_spec.py` under `tests/<app>/`, functions `it_*` inside `describe_*`. 100% branch coverage, factory-boy for all data, never mock the DB/models. `@pytest.mark.django_db` or the `db` fixture.
- **Data migrations MUST include a reverse function** — never `RunPython.noop` as reverse without approval.
- **FRONTEND.md is binding for all UI.** Specifically: use `components/form_field.html` for fields, `components/toggle.html` for booleans, `trigger_toast()` after mutating HTMX actions (not Django messages), `hub-card` section wrappers, `pl-` class prefix, **no inline `background`/`color` on `<select>`/`<input>`/`<textarea>`** (use `--hub-input-bg`/`--hub-input-border`/`--text` via a CSS class, and style `select option`), no `display` in inline `style` on `x-show` elements, test dark **and** light themes. New reusable component CSS → `static/css/components.css`; hub page CSS → `static/css/hub.css`.
- **Versioning:** every PR bumps `plfog/version.py` and adds a member-friendly `CHANGELOG` entry (no jargon/PR numbers). Current version on this branch: `0.19.0`.
- **Skill cap per member:** 15 (constant `Member.MAX_SKILLS = 15`), enforced in the add form.
- **`commission_note` max length:** 280.

---

### Task 1: Skill vocabulary models (`SkillCategory`, `Skill`, `MemberSkill`)

**Files:**
- Modify: `membership/models.py` (add three models near `GuildMembership`, ~`:995`)
- Create migration: `membership/migrations/0050_skills_directory.py` (generated)
- Modify: `tests/membership/factories.py` (add factories)
- Test: `tests/membership/skills_models_spec.py`

**Interfaces:**
- Produces:
  - `SkillCategory(name: str, slug: str, sort_order: int)` — `__str__ -> name`
  - `Skill(name: str, slug: str, category: FK, status: Skill.Status, suggested_by: FK|None, created_at)`; `Skill.Status.APPROVED = "approved"`, `Skill.Status.PENDING = "pending"`; `__str__ -> name`
  - `MemberSkill(member: FK, skill: FK, years_experience: int|None, created_at)`; unique `(member, skill)`; `__str__ -> "<member> — <skill>[ (Ny)]"`; reverse accessor `member.skills`, `skill.member_links`
  - Factories: `SkillCategoryFactory`, `SkillFactory`, `MemberSkillFactory`

- [ ] **Step 1: Write the failing tests**

Create `tests/membership/skills_models_spec.py`:

```python
from __future__ import annotations

import pytest
from django.db import IntegrityError

from membership.models import Skill
from tests.membership.factories import MemberFactory, MemberSkillFactory, SkillCategoryFactory, SkillFactory


def describe_SkillCategory():
    def it_str_is_the_name(db):
        assert str(SkillCategoryFactory(name="Woodworking")) == "Woodworking"


def describe_Skill():
    def it_str_is_the_name(db):
        assert str(SkillFactory(name="Welding")) == "Welding"

    def it_defaults_to_approved(db):
        assert SkillFactory().status == Skill.Status.APPROVED

    def it_can_be_pending(db):
        member = MemberFactory()
        skill = SkillFactory(name="Glassblowing", status=Skill.Status.PENDING, suggested_by=member)
        assert skill.status == Skill.Status.PENDING
        assert skill.suggested_by == member


def describe_MemberSkill():
    def it_str_shows_member_and_skill(db):
        ms = MemberSkillFactory(skill__name="Coding")
        assert "Coding" in str(ms)

    def it_str_includes_years_when_set(db):
        ms = MemberSkillFactory(skill__name="Coding", years_experience=10)
        assert "(10y)" in str(ms)

    def it_str_omits_years_when_unset(db):
        ms = MemberSkillFactory(skill__name="Coding", years_experience=None)
        assert "y)" not in str(ms)

    def it_rejects_duplicate_member_skill(db):
        member = MemberFactory()
        skill = SkillFactory()
        MemberSkillFactory(member=member, skill=skill)
        with pytest.raises(IntegrityError):
            MemberSkillFactory(member=member, skill=skill)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/membership/skills_models_spec.py -v`
Expected: FAIL — `ImportError` / `cannot import name 'MemberSkillFactory'`.

- [ ] **Step 3: Add the models**

In `membership/models.py`, add near the other guild-affiliation models (after `GuildMembership`):

```python
class SkillCategory(models.Model):
    """A grouping of related skills shown in the skills picker and directory filter."""

    name = models.CharField(max_length=100, unique=True, help_text="Display name of the category.")
    slug = models.SlugField(max_length=120, unique=True, help_text="URL-safe identifier.")
    sort_order = models.PositiveSmallIntegerField(default=0, help_text="Lower numbers sort first in pickers.")

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "skill category"
        verbose_name_plural = "skill categories"

    def __str__(self) -> str:
        return self.name


class Skill(models.Model):
    """A single skill members can list, drawn from a curated vocabulary."""

    class Status(models.TextChoices):
        APPROVED = "approved", "Approved"
        PENDING = "pending", "Pending review"

    name = models.CharField(max_length=80, unique=True, help_text="Canonical skill name shown everywhere.")
    slug = models.SlugField(max_length=100, unique=True, help_text="URL-safe identifier used for filtering.")
    category = models.ForeignKey(
        SkillCategory,
        on_delete=models.PROTECT,
        related_name="skills",
        help_text="The category this skill belongs to.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.APPROVED,
        help_text="Approved skills appear publicly; pending skills are member suggestions awaiting review.",
    )
    suggested_by = models.ForeignKey(
        "Member",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="suggested_skills",
        help_text="Member who proposed this skill, if it came from a suggestion.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the skill entered the vocabulary.")

    class Meta:
        ordering = ["category__sort_order", "name"]
        indexes = [
            models.Index(fields=["status", "name"], name="idx_skill_status_name"),
        ]

    def __str__(self) -> str:
        return self.name


class MemberSkill(models.Model):
    """A skill claimed by a member, with optional years of experience."""

    member = models.ForeignKey(
        "Member", on_delete=models.CASCADE, related_name="skills", help_text="The member who listed this skill."
    )
    skill = models.ForeignKey(
        Skill, on_delete=models.CASCADE, related_name="member_links", help_text="The skill being claimed."
    )
    years_experience = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Optional years of experience, shown beside the skill when set.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the member added this skill.")

    class Meta:
        ordering = ["skill__category__sort_order", "skill__name"]
        constraints = [
            models.UniqueConstraint(fields=["member", "skill"], name="uq_memberskill_member_skill"),
        ]

    def __str__(self) -> str:
        years = f" ({self.years_experience}y)" if self.years_experience is not None else ""
        return f"{self.member.display_name} — {self.skill.name}{years}"
```

- [ ] **Step 4: Add factories**

In `tests/membership/factories.py`, add to the model import block `Skill, SkillCategory, MemberSkill`, then append:

```python
class SkillCategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SkillCategory
        django_get_or_create = ("name",)

    name = factory.Sequence(lambda n: f"Category {n}")
    slug = factory.Sequence(lambda n: f"category-{n}")


class SkillFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Skill
        django_get_or_create = ("name",)

    name = factory.Sequence(lambda n: f"Skill {n}")
    slug = factory.Sequence(lambda n: f"skill-{n}")
    category = factory.SubFactory(SkillCategoryFactory)
    status = Skill.Status.APPROVED


class MemberSkillFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MemberSkill

    member = factory.SubFactory(MemberFactory)
    skill = factory.SubFactory(SkillFactory)
```

- [ ] **Step 5: Generate the migration**

Run: `python manage.py makemigrations membership`
Expected: creates `membership/migrations/0050_skills_directory.py` adding three models. Confirm filename/number (next after `0049`).

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/membership/skills_models_spec.py -v`
Expected: PASS (all 7).

- [ ] **Step 7: Commit**

```bash
ruff format . && ruff check --fix .
git add membership/models.py membership/migrations/0050_skills_directory.py tests/membership/factories.py tests/membership/skills_models_spec.py
git commit -m "Add Skill, SkillCategory, MemberSkill models"
```

---

### Task 2: Member commission fields + skills visibility toggle

**Files:**
- Modify: `membership/models.py` (`Member` fields ~`:254-265`, `DIRECTORY_TOGGLEABLE_FIELDS` `:297`, add `MAX_SKILLS`)
- Create migration: `membership/migrations/0051_member_commissions.py` (generated)
- Test: `tests/membership/skills_models_spec.py` (extend)

**Interfaces:**
- Produces: `Member.open_for_commissions: bool`, `Member.commission_note: str`, `Member.MAX_SKILLS = 15`, `"skills"` in `Member.DIRECTORY_TOGGLEABLE_FIELDS` (so `member.is_public("skills")` and a `show_skills` form field exist automatically).

- [ ] **Step 1: Write the failing tests**

Append to `tests/membership/skills_models_spec.py`:

```python
from membership.models import Member  # add to imports at top


def describe_Member_commissions():
    def it_defaults_open_for_commissions_false(db):
        assert MemberFactory().open_for_commissions is False

    def it_includes_skills_in_toggleable_fields(db):
        assert "skills" in Member.DIRECTORY_TOGGLEABLE_FIELDS

    def it_skills_default_public(db):
        # is_public defaults missing keys to True
        assert MemberFactory().is_public("skills") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/membership/skills_models_spec.py::describe_Member_commissions -v`
Expected: FAIL — `AttributeError: ... 'open_for_commissions'` / `"skills"` not in tuple.

- [ ] **Step 3: Add the fields and constant**

In `membership/models.py`, after `directory_visibility` (`:265`) add:

```python
    open_for_commissions = models.BooleanField(
        default=False,
        help_text="When on, the member shows an 'Open for commissions!' badge and appears in that filter.",
    )
    commission_note = models.CharField(
        max_length=280,
        blank=True,
        help_text="Short note on the kind of paid or commissioned work the member welcomes.",
    )
```

Extend `DIRECTORY_TOGGLEABLE_FIELDS` (`:297`) by appending `"skills"`:

```python
    DIRECTORY_TOGGLEABLE_FIELDS: tuple[str, ...] = (
        "pronouns",
        "phone",
        "email",
        "discord_handle",
        "other_contact_info",
        "about_me",
        "profile_photo",
        "skills",
    )

    MAX_SKILLS = 15
```

- [ ] **Step 4: Generate the migration**

Run: `python manage.py makemigrations membership`
Expected: `0051_member_commissions.py` adding the two fields only (the tuple/constant are code, not schema).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/membership/skills_models_spec.py::describe_Member_commissions -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
ruff format . && ruff check --fix .
git add membership/models.py membership/migrations/0051_member_commissions.py tests/membership/skills_models_spec.py
git commit -m "Add open_for_commissions and skills visibility to Member"
```

---

### Task 3: Member skill accessors + queryset filters

**Files:**
- Modify: `membership/models.py` (`MemberQuerySet` — find via `class MemberQuerySet`; `Member.approved_skills` property)
- Test: `tests/membership/skills_models_spec.py` (extend)

**Interfaces:**
- Produces:
  - `Member.approved_skills` (property) → queryset of this member's `MemberSkill` whose skill is approved, `select_related("skill__category")`, ordered by category sort then skill name.
  - `MemberQuerySet.with_skill(slug: str)` → members having an **approved** skill with that slug.
  - `MemberQuerySet.open_for_commissions()` → `filter(open_for_commissions=True)`.
  - `MemberQuerySet.search_skills(text: str)` → members whose display name OR an approved skill name contains `text` (case-insensitive), `.distinct()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/membership/skills_models_spec.py`:

```python
def describe_member_skill_queries():
    def it_approved_skills_excludes_pending(db):
        member = MemberFactory()
        approved = SkillFactory(name="Coding", status=Skill.Status.APPROVED)
        pending = SkillFactory(name="Mind reading", status=Skill.Status.PENDING)
        MemberSkillFactory(member=member, skill=approved)
        MemberSkillFactory(member=member, skill=pending)
        names = [ms.skill.name for ms in member.approved_skills]
        assert names == ["Coding"]

    def it_with_skill_filters_by_slug(db):
        wanted = SkillFactory(name="Welding", slug="welding")
        other = SkillFactory(name="Sewing", slug="sewing")
        welder = MemberFactory()
        sewer = MemberFactory()
        MemberSkillFactory(member=welder, skill=wanted)
        MemberSkillFactory(member=sewer, skill=other)
        result = Member.objects.with_skill("welding")
        assert list(result) == [welder]

    def it_with_skill_ignores_pending(db):
        pending = SkillFactory(name="Alchemy", slug="alchemy", status=Skill.Status.PENDING)
        member = MemberFactory()
        MemberSkillFactory(member=member, skill=pending)
        assert list(Member.objects.with_skill("alchemy")) == []

    def it_open_for_commissions_filters(db):
        open_member = MemberFactory(open_for_commissions=True)
        MemberFactory(open_for_commissions=False)
        assert list(Member.objects.open_for_commissions()) == [open_member]

    def it_search_skills_matches_skill_name(db):
        member = MemberFactory()
        MemberSkillFactory(member=member, skill=SkillFactory(name="Music production"))
        assert member in Member.objects.search_skills("music")

    def it_search_skills_matches_display_name(db):
        member = MemberFactory(full_legal_name="Ada Lovelace")
        assert member in Member.objects.search_skills("lovelace")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/membership/skills_models_spec.py::describe_member_skill_queries -v`
Expected: FAIL — `AttributeError: 'MemberQuerySet' object has no attribute 'with_skill'`.

- [ ] **Step 3: Implement accessors and queryset methods**

Add the property to `Member` (near other properties, after `is_public`):

```python
    @property
    def approved_skills(self) -> models.QuerySet[MemberSkill]:
        """This member's skills whose vocabulary entry is approved, ready for display."""
        return self.skills.filter(skill__status=Skill.Status.APPROVED).select_related("skill__category")
```

Add to `MemberQuerySet` (find `class MemberQuerySet(models.QuerySet)` in the same file; add alongside `active`/`paying`):

```python
    def with_skill(self, slug: str) -> MemberQuerySet:
        """Members who list an approved skill with the given slug."""
        return self.filter(skills__skill__slug=slug, skills__skill__status=Skill.Status.APPROVED)

    def open_for_commissions(self) -> MemberQuerySet:
        """Members who have flagged themselves open for commissions."""
        return self.filter(open_for_commissions=True)

    def search_skills(self, text: str) -> MemberQuerySet:
        """Members whose display name or an approved skill name contains ``text`` (case-insensitive)."""
        approved = models.Q(skills__skill__status=Skill.Status.APPROVED)
        return self.filter(
            models.Q(preferred_name__icontains=text)
            | models.Q(full_legal_name__icontains=text)
            | (approved & models.Q(skills__skill__name__icontains=text))
        ).distinct()
```

> Note: `MemberSkill` is defined after `Member` in the file; the `approved_skills` return annotation is a string under `from __future__ import annotations`, so forward reference is fine.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/membership/skills_models_spec.py::describe_member_skill_queries -v`
Expected: PASS (all 6).

- [ ] **Step 5: Commit**

```bash
ruff format . && ruff check --fix .
git add membership/models.py tests/membership/skills_models_spec.py
git commit -m "Add member skill accessors and directory queryset filters"
```

---

### Task 4: Seed the starter skill vocabulary (reversible data migration)

**Files:**
- Create: `membership/migrations/0052_seed_skills.py`
- Test: `tests/membership/skills_seed_spec.py`

**Interfaces:**
- Consumes: models from Task 1.
- Produces: ~13 `SkillCategory` rows and the full starter `Skill` list from the spec (§7), all `status=APPROVED`, `suggested_by=None`. Reverse deletes exactly those seeded rows by slug.

- [ ] **Step 1: Write the failing test**

Create `tests/membership/skills_seed_spec.py`:

```python
from __future__ import annotations

from membership.models import Skill, SkillCategory


def describe_skill_seed():
    def it_loads_categories_and_skills(db):
        # Migrations run for the test DB, so seeded rows exist.
        assert SkillCategory.objects.filter(slug="software-tech").exists()
        assert Skill.objects.filter(slug="ai-development-consulting").exists()
        assert Skill.objects.filter(slug="small-woodworking-projects").exists()

    def it_seeds_only_approved_skills(db):
        assert not Skill.objects.exclude(status=Skill.Status.APPROVED).exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/membership/skills_seed_spec.py -v`
Expected: FAIL — seeded rows don't exist yet.

- [ ] **Step 3: Write the data migration**

Create `membership/migrations/0052_seed_skills.py`. Build categories then skills from a single nested dict so forward/reverse share one source of truth. Use `django.utils.text.slugify` for slugs.

```python
from __future__ import annotations

from django.db import migrations
from django.utils.text import slugify

# category name -> list of skill names. Ordered; category index drives sort_order.
SEED: dict[str, list[str]] = {
    "Woodworking": [
        "Furniture making", "Small woodworking projects", "Cabinetry", "Wood turning",
        "Carving", "Joinery", "CNC routing", "Finishing & refinishing",
    ],
    "Metal & Jewelry": [
        "Welding (MIG/TIG)", "Blacksmithing", "Silversmithing", "Lost-wax casting",
        "Machining", "Sheet metal", "Engraving", "Stone setting",
    ],
    "Textiles & Fiber": [
        "Sewing", "Garment making", "Quilting", "Weaving", "Knitting", "Crochet",
        "Embroidery", "Screen printing", "Dyeing",
    ],
    "Leather": ["Leatherworking", "Bag & wallet making", "Tooling & carving", "Bookbinding"],
    "Ceramics & Glass": [
        "Wheel throwing", "Hand-building", "Glazing", "Kiln firing", "Stained glass",
        "Lampworking", "Glassblowing",
    ],
    "Paper & Print": ["Letterpress", "Printmaking", "Linocut", "Risograph", "Zine making", "Calligraphy"],
    "Electronics & Fab": [
        "Electronics & soldering", "Microcontrollers (Arduino/Pi)", "Robotics",
        "3D modeling (CAD)", "3D printing", "Laser cutting", "PCB design",
    ],
    "Software & Tech": [
        "Coding", "Web development", "Website design & consulting", "AI development & consulting",
        "Mobile apps", "Game development", "Data & automation", "IT & networking",
    ],
    "Music & Audio": [
        "Music production", "Audio engineering", "Mixing & mastering", "Songwriting",
        "Instrument repair", "DJing", "Live sound",
    ],
    "Photo & Video": [
        "Photography", "Videography", "Photo editing", "Video editing", "Motion graphics", "Lighting",
    ],
    "Art & Design": [
        "Illustration", "Painting", "Graphic design", "UX/UI design", "Branding & logos",
        "Murals", "Sculpture", "Animation",
    ],
    "Writing & Media": ["Copywriting", "Editing", "Technical writing", "Grant writing", "Social media"],
    "Trades & Misc": [
        "Carpentry", "Electrical", "Plumbing", "Upholstery", "Sign making",
        "Prop & set building", "Teaching & workshops", "Consulting",
    ],
}


def _all_slugs() -> tuple[set[str], set[str]]:
    cat_slugs = {slugify(name) for name in SEED}
    skill_slugs = {slugify(s) for skills in SEED.values() for s in skills}
    return cat_slugs, skill_slugs


def seed(apps, schema_editor) -> None:
    SkillCategory = apps.get_model("membership", "SkillCategory")
    Skill = apps.get_model("membership", "Skill")
    for order, (cat_name, skills) in enumerate(SEED.items()):
        category, _ = SkillCategory.objects.get_or_create(
            slug=slugify(cat_name), defaults={"name": cat_name, "sort_order": order}
        )
        for skill_name in skills:
            Skill.objects.get_or_create(
                slug=slugify(skill_name),
                defaults={"name": skill_name, "category": category, "status": "approved"},
            )


def unseed(apps, schema_editor) -> None:
    SkillCategory = apps.get_model("membership", "SkillCategory")
    Skill = apps.get_model("membership", "Skill")
    cat_slugs, skill_slugs = _all_slugs()
    Skill.objects.filter(slug__in=skill_slugs).delete()
    SkillCategory.objects.filter(slug__in=cat_slugs).delete()


class Migration(migrations.Migration):
    dependencies = [("membership", "0051_member_commissions")]
    operations = [migrations.RunPython(seed, unseed)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/membership/skills_seed_spec.py -v`
Expected: PASS.

- [ ] **Step 5: Verify the reverse works**

Run: `python manage.py migrate membership 0051 && python manage.py migrate membership`
Expected: both directions succeed with no error.

- [ ] **Step 6: Commit**

```bash
ruff format . && ruff check --fix .
git add membership/migrations/0052_seed_skills.py tests/membership/skills_seed_spec.py
git commit -m "Seed starter skill vocabulary (reversible)"
```

---

### Task 5: Admin — approval workflow for suggested skills

**Files:**
- Modify: `membership/admin.py` (add `SkillAdmin`, register `SkillCategory`, `MemberSkill` inline on `Member`)
- Test: `tests/membership/skills_admin_spec.py`

**Interfaces:**
- Consumes: models from Tasks 1-2.
- Produces: `SkillAdmin` with an `approve_skills` action that sets `status=APPROVED`.

- [ ] **Step 1: Write the failing test**

Create `tests/membership/skills_admin_spec.py`:

```python
from __future__ import annotations

from django.contrib.admin.sites import site

from membership.admin import SkillAdmin
from membership.models import Skill
from tests.membership.factories import SkillFactory


def describe_SkillAdmin():
    def it_approve_action_marks_pending_skills_approved(db, rf):
        pending = SkillFactory(name="Glassblowing", status=Skill.Status.PENDING)
        admin = SkillAdmin(Skill, site)
        admin.approve_skills(rf.post("/"), Skill.objects.filter(pk=pending.pk))
        pending.refresh_from_db()
        assert pending.status == Skill.Status.APPROVED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/membership/skills_admin_spec.py -v`
Expected: FAIL — `cannot import name 'SkillAdmin'`.

- [ ] **Step 3: Implement the admin**

In `membership/admin.py` (custom admins are registered before the auto-register loop — follow the existing file's pattern):

```python
from membership.models import MemberSkill, Skill, SkillCategory


class MemberSkillInline(admin.TabularInline):
    model = MemberSkill
    extra = 0
    autocomplete_fields = ["skill"]


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "status", "suggested_by")
    list_filter = ("status", "category")
    search_fields = ("name",)
    actions = ["approve_skills"]

    @admin.action(description="Approve selected skills")
    def approve_skills(self, request, queryset) -> None:
        queryset.update(status=Skill.Status.APPROVED)


admin.site.register(SkillCategory)
```

Add `MemberSkillInline` to the existing `MemberAdmin.inlines` list (find `class MemberAdmin` in the file and append to its `inlines`).

> If `Member` uses auto-registration rather than a custom `MemberAdmin`, skip the inline line and note it; the `SkillAdmin` + `approve_skills` action is the required deliverable. `search_fields` on `SkillAdmin` is required so the inline's `autocomplete_fields` works.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/membership/skills_admin_spec.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
ruff format . && ruff check --fix .
git add membership/admin.py tests/membership/skills_admin_spec.py
git commit -m "Add Skill admin with approval action"
```

---

### Task 6: Directory view — skill/commission/search filtering

**Files:**
- Modify: `hub/views.py` (`member_directory`, `:232-279`)
- Test: `tests/hub/member_directory_skills_spec.py`

**Interfaces:**
- Consumes: `Member.objects.with_skill/open_for_commissions/search_skills` (Task 3).
- Produces: context keys `skill_categories` (approved skills grouped for the dropdown), `selected_skill` (slug str), `commissions_only` (bool), `query` (str). The visibility floor at `:250-252` is applied **before** any skill filter.

- [ ] **Step 1: Write the failing tests**

Create `tests/hub/member_directory_skills_spec.py`:

```python
from __future__ import annotations

from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import Member
from tests.membership.factories import MemberFactory, MemberSkillFactory, SkillFactory


def _login(client: Client) -> Member:
    member = MemberFactory(show_in_directory=True)
    user = User.objects.create_user(username="viewer", password="pw")
    member.user = user
    member.save(update_fields=["user"])
    client.login(username="viewer", password="pw")
    return member


def describe_member_directory_skills():
    def it_filters_by_skill_slug(client):
        _login(client)
        welder = MemberFactory(show_in_directory=True, full_legal_name="Wendy Welder")
        MemberFactory(show_in_directory=True, full_legal_name="Sandy Sewer")
        MemberSkillFactory(member=welder, skill=SkillFactory(name="Welding", slug="welding"))
        resp = client.get(reverse("hub_member_directory"), {"skill": "welding"})
        assert b"Wendy Welder" in resp.content
        assert b"Sandy Sewer" not in resp.content

    def it_filters_open_for_commissions(client):
        _login(client)
        MemberFactory(show_in_directory=True, full_legal_name="Carla Commission", open_for_commissions=True)
        MemberFactory(show_in_directory=True, full_legal_name="Nina None", open_for_commissions=False)
        resp = client.get(reverse("hub_member_directory"), {"commissions": "1"})
        assert b"Carla Commission" in resp.content
        assert b"Nina None" not in resp.content

    def it_searches_skill_names(client):
        _login(client)
        producer = MemberFactory(show_in_directory=True, full_legal_name="Polly Producer")
        MemberSkillFactory(member=producer, skill=SkillFactory(name="Music production", slug="music-production"))
        resp = client.get(reverse("hub_member_directory"), {"q": "music"})
        assert b"Polly Producer" in resp.content

    def it_never_surfaces_hidden_members_via_skill(client):
        _login(client)
        hidden = MemberFactory(show_in_directory=False, full_legal_name="Henry Hidden")
        MemberSkillFactory(member=hidden, skill=SkillFactory(name="Welding", slug="welding"))
        resp = client.get(reverse("hub_member_directory"), {"skill": "welding"})
        assert b"Henry Hidden" not in resp.content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/hub/member_directory_skills_spec.py -v`
Expected: FAIL — unfiltered results (e.g. "Sandy Sewer" present) / "Henry Hidden" present.

- [ ] **Step 3: Extend the view**

In `hub/views.py`, after the existing `guild_filter` block (`:253-255`) and before building `members`, add:

```python
    skill_slug = request.GET.get("skill", "")
    if skill_slug:
        member_qs = member_qs.with_skill(skill_slug)
    if request.GET.get("commissions") == "1":
        member_qs = member_qs.open_for_commissions()
    query = request.GET.get("q", "").strip()
    if query:
        member_qs = member_qs.search_skills(query)
```

Add `skills__skill__category` to the existing `prefetch_related` (`:258-265`) so card chips don't N+1:

```python
            "skills__skill__category",
```

Add to the render context (`:271-278`):

```python
            "skill_categories": SkillCategory.objects.prefetch_related(
                Prefetch("skills", queryset=Skill.objects.filter(status=Skill.Status.APPROVED).order_by("name"))
            ),
            "selected_skill": skill_slug,
            "commissions_only": request.GET.get("commissions") == "1",
            "query": query,
```

Add the imports at the top of `hub/views.py`: `from membership.models import Skill, SkillCategory` (extend the existing membership import).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/hub/member_directory_skills_spec.py -v`
Expected: PASS (all 4).

- [ ] **Step 5: Commit**

```bash
ruff format . && ruff check --fix .
git add hub/views.py tests/hub/member_directory_skills_spec.py
git commit -m "Filter member directory by skill, commissions, and search"
```

---

### Task 7: Directory template — filter bar, skill chips, commission badge (FRONTEND.md)

**Files:**
- Modify: `templates/hub/member_directory.html`
- Modify: `static/css/hub.css` (filter bar + chip/badge styles)
- Test: `tests/hub/member_directory_skills_spec.py` (extend)

**Interfaces:**
- Consumes: context from Task 6; `member.approved_skills`, `member.is_public("skills")`, `member.open_for_commissions`, `member.commission_note`.

**FRONTEND.md constraints for this task (binding):**
- The filter `<select>`/`<input>` must NOT carry inline `background`/`color`. Wrap them in a `.pl-directory-filters` block whose CSS uses `--hub-input-bg`/`--hub-input-border`/`--text`, and style `select option { background; color }`.
- Chips/badge use the `pl-` prefix; the commission badge accent is `--color-tuscan-yellow`.
- The skill section renders only when `member.is_public("skills")`.
- Verify in **both** dark and light themes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/hub/member_directory_skills_spec.py`:

```python
def describe_member_directory_skill_display():
    def it_shows_skill_chips_with_years(client):
        _login(client)
        m = MemberFactory(show_in_directory=True, full_legal_name="Carl Coder")
        MemberSkillFactory(member=m, skill=SkillFactory(name="Coding", slug="coding"), years_experience=10)
        resp = client.get(reverse("hub_member_directory"))
        assert b"Coding" in resp.content
        assert b"10y" in resp.content

    def it_hides_skills_when_section_private(client):
        _login(client)
        m = MemberFactory(
            show_in_directory=True,
            full_legal_name="Pat Private",
            directory_visibility={"skills": False},
        )
        MemberSkillFactory(member=m, skill=SkillFactory(name="Welding", slug="welding"))
        resp = client.get(reverse("hub_member_directory"))
        assert b"Welding" not in resp.content

    def it_shows_open_for_commissions_badge(client):
        _login(client)
        MemberFactory(
            show_in_directory=True,
            full_legal_name="Carla Commission",
            open_for_commissions=True,
            commission_note="Custom woodworking welcome!",
        )
        resp = client.get(reverse("hub_member_directory"))
        assert b"Open for commissions" in resp.content
        assert b"Custom woodworking welcome!" in resp.content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/hub/member_directory_skills_spec.py::describe_member_directory_skill_display -v`
Expected: FAIL — chips/badge not rendered.

- [ ] **Step 3: Add the filter bar**

In `templates/hub/member_directory.html`, replace the guild-only filter form (`:9-19`) with a combined filter bar (single GET form so filters compose). Note: NO inline `background`/`color` on the controls — they get `.pl-filter-control`:

```html
<form method="get" class="pl-directory-filters">
    {% if guilds %}
    <label class="pl-filter-field">
        <span class="pl-filter-label">Guild</span>
        <select name="guild" class="pl-filter-control" onchange="this.form.submit()">
            <option value="">All guilds</option>
            {% for g in guilds %}
            <option value="{{ g.pk }}"{% if g.pk|stringformat:'s' == guild_filter %} selected{% endif %}>{{ g.name }}</option>
            {% endfor %}
        </select>
    </label>
    {% endif %}
    <label class="pl-filter-field">
        <span class="pl-filter-label">Skill</span>
        <select name="skill" class="pl-filter-control" onchange="this.form.submit()">
            <option value="">All skills</option>
            {% for category in skill_categories %}
            {% if category.skills.all %}
            <optgroup label="{{ category.name }}">
                {% for skill in category.skills.all %}
                <option value="{{ skill.slug }}"{% if skill.slug == selected_skill %} selected{% endif %}>{{ skill.name }}</option>
                {% endfor %}
            </optgroup>
            {% endif %}
            {% endfor %}
        </select>
    </label>
    <label class="pl-filter-field">
        <span class="pl-filter-label">Search</span>
        <input type="search" name="q" value="{{ query }}" placeholder="name or skill" class="pl-filter-control">
    </label>
    <label class="pl-filter-checkbox">
        <input type="checkbox" name="commissions" value="1"{% if commissions_only %} checked{% endif %} onchange="this.form.submit()">
        Open for commissions
    </label>
    <button type="submit" class="hub-btn hub-btn--sm">Apply</button>
</form>
```

- [ ] **Step 4: Add chips + badge to the card**

In `templates/hub/member_directory.html`, after the guilds block (`:127-136`, before the card closing `</div>` at `:138`), add:

```html
        {% if member|is_public:"skills" %}
        {% with member_skills=member.approved_skills %}
        {% if member_skills %}
        <div class="pl-skill-chips">
            {% for ms in member_skills %}
            <span class="pl-skill-chip">{{ ms.skill.name }}{% if ms.years_experience is not None %}<span class="pl-skill-chip__years">{{ ms.years_experience }}y</span>{% endif %}</span>
            {% endfor %}
        </div>
        {% endif %}
        {% if member.open_for_commissions %}
        <div class="pl-commission">
            <span class="pl-commission__badge">Open for commissions!</span>
            {% if member.commission_note %}<p class="pl-commission__note">{{ member.commission_note }}</p>{% endif %}
        </div>
        {% endif %}
        {% endwith %}
        {% endif %}
```

- [ ] **Step 5: Add CSS (dark + light)**

Append to `static/css/hub.css`:

```css
/* Member directory filters */
.pl-directory-filters {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    gap: 0.75rem;
    margin-bottom: 1.5rem;
}
.pl-filter-field { display: flex; flex-direction: column; gap: 0.25rem; }
.pl-filter-label { font-size: 0.75rem; color: var(--hub-text-muted); }
.pl-filter-control {
    background: var(--hub-input-bg);
    color: var(--text);
    border: 1px solid var(--hub-input-border);
    border-radius: 6px;
    padding: 0.45rem 0.75rem;
    font-size: 0.875rem;
}
.pl-filter-control option { background: var(--hub-elevated); color: var(--text); }
.pl-filter-checkbox { display: inline-flex; align-items: center; gap: 0.4rem; font-size: 0.875rem; color: var(--hub-text); }

/* Skill chips */
.pl-skill-chips { display: flex; flex-wrap: wrap; gap: 0.35rem; margin-top: 0.6rem; }
.pl-skill-chip {
    display: inline-flex; align-items: center; gap: 0.35rem;
    background: var(--hub-surface); color: var(--hub-text);
    border: 1px solid var(--hub-input-border);
    border-radius: 12px; padding: 2px 10px; font-size: 0.8125rem;
}
.pl-skill-chip__years { color: var(--hub-text-muted); font-size: 0.75rem; }

/* Open for commissions */
.pl-commission { margin-top: 0.75rem; }
.pl-commission__badge {
    display: inline-flex; align-items: center;
    background: rgba(238, 180, 75, 0.14); color: var(--color-tuscan-yellow);
    border: 1px solid rgba(238, 180, 75, 0.35);
    border-radius: 12px; padding: 2px 10px; font-size: 0.8125rem; font-weight: 700;
}
.pl-commission__note { margin: 0.4rem 0 0; font-size: 0.85rem; color: var(--hub-text-muted); }
```

> `--hub-input-bg`, `--hub-input-border`, `--text`, `--hub-elevated` are theme-aware tokens (FRONTEND.md), so light mode is handled by the existing `[data-theme="light"]` overrides — no extra light-mode CSS needed. The badge's `rgba(238,180,75,…)` matches the accent already used for the admin banner in this template.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/hub/member_directory_skills_spec.py -v`
Expected: PASS (all 7 in the file).

- [ ] **Step 7: Visually verify both themes**

Run `python manage.py runserver`, open `/members/`, toggle the theme. Confirm: filter controls are not white boxes in dark mode, chips/badge legible in both themes, optgroups grouped by category.

- [ ] **Step 8: Commit**

```bash
ruff format . && ruff check --fix .
git add templates/hub/member_directory.html static/css/hub.css tests/hub/member_directory_skills_spec.py
git commit -m "Show skill chips, commission badge, and skill filters in directory"
```

---

### Task 8: Skill-editor forms (add / suggest validation)

**Files:**
- Modify: `hub/forms.py` (add `MemberSkillForm`, `SkillSuggestionForm`)
- Test: `tests/hub/skill_forms_spec.py`

**Interfaces:**
- Produces:
  - `MemberSkillForm(member=...)` — fields `skill` (`ModelChoiceField` over approved skills), `years_experience` (optional int 0-99). `clean()` rejects: duplicate skill for the member, exceeding `Member.MAX_SKILLS`. `save()` creates the `MemberSkill`.
  - `SkillSuggestionForm(member=...)` — field `name`. `clean_name()` rejects a name that case-insensitively matches an existing skill (raise pointing at the canonical one). `save()` creates `Skill(status=PENDING, suggested_by=member)` + a `MemberSkill` linking it, returns the `MemberSkill`.

- [ ] **Step 1: Write the failing tests**

Create `tests/hub/skill_forms_spec.py`:

```python
from __future__ import annotations

from django.core.exceptions import ValidationError

from hub.forms import MemberSkillForm, SkillSuggestionForm
from membership.models import Member, MemberSkill, Skill
from tests.membership.factories import MemberFactory, MemberSkillFactory, SkillFactory


def describe_MemberSkillForm():
    def it_adds_a_skill(db):
        member = MemberFactory()
        skill = SkillFactory()
        form = MemberSkillForm(member=member, data={"skill": skill.pk, "years_experience": 5})
        assert form.is_valid(), form.errors
        ms = form.save()
        assert ms.member == member and ms.years_experience == 5

    def it_rejects_duplicate(db):
        member = MemberFactory()
        skill = SkillFactory()
        MemberSkillFactory(member=member, skill=skill)
        form = MemberSkillForm(member=member, data={"skill": skill.pk})
        assert not form.is_valid()

    def it_rejects_when_at_cap(db):
        member = MemberFactory()
        for _ in range(Member.MAX_SKILLS):
            MemberSkillFactory(member=member)
        form = MemberSkillForm(member=member, data={"skill": SkillFactory().pk})
        assert not form.is_valid()


def describe_SkillSuggestionForm():
    def it_creates_pending_skill_and_link(db):
        member = MemberFactory()
        form = SkillSuggestionForm(member=member, data={"name": "Underwater basket weaving"})
        assert form.is_valid(), form.errors
        ms = form.save()
        assert ms.skill.status == Skill.Status.PENDING
        assert ms.skill.suggested_by == member
        assert ms.member == member

    def it_rejects_existing_skill_name_case_insensitive(db):
        SkillFactory(name="Welding")
        member = MemberFactory()
        form = SkillSuggestionForm(member=member, data={"name": "welding"})
        assert not form.is_valid()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/hub/skill_forms_spec.py -v`
Expected: FAIL — `cannot import name 'MemberSkillForm'`.

- [ ] **Step 3: Implement the forms**

In `hub/forms.py` add (import `slugify` from `django.utils.text`, and the models):

```python
class MemberSkillForm(forms.Form):
    """Add a single skill to a member's profile, with optional years of experience."""

    skill = forms.ModelChoiceField(queryset=Skill.objects.none())
    years_experience = forms.IntegerField(required=False, min_value=0, max_value=99)

    def __init__(self, *args: Any, member: Member, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.member = member
        self.fields["skill"].queryset = Skill.objects.filter(status=Skill.Status.APPROVED)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        skill = cleaned.get("skill")
        if skill and self.member.skills.filter(skill=skill).exists():
            raise ValidationError("You've already listed that skill.")
        if self.member.skills.count() >= Member.MAX_SKILLS:
            raise ValidationError(f"You can list up to {Member.MAX_SKILLS} skills.")
        return cleaned

    def save(self) -> MemberSkill:
        return MemberSkill.objects.create(
            member=self.member,
            skill=self.cleaned_data["skill"],
            years_experience=self.cleaned_data.get("years_experience"),
        )


class SkillSuggestionForm(forms.Form):
    """Suggest a new skill not yet in the vocabulary; created pending admin approval."""

    name = forms.CharField(max_length=80)

    def __init__(self, *args: Any, member: Member, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.member = member

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        if Skill.objects.filter(name__iexact=name).exists():
            raise ValidationError("That skill already exists — pick it from the list instead.")
        return name

    def save(self) -> MemberSkill:
        name = self.cleaned_data["name"]
        category, _ = SkillCategory.objects.get_or_create(
            slug="suggested", defaults={"name": "Suggested", "sort_order": 999}
        )
        skill = Skill.objects.create(
            name=name,
            slug=slugify(name),
            category=category,
            status=Skill.Status.PENDING,
            suggested_by=self.member,
        )
        return MemberSkill.objects.create(member=self.member, skill=skill)
```

Extend the model import at the top of `hub/forms.py` to include `MemberSkill, Skill, SkillCategory`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/hub/skill_forms_spec.py -v`
Expected: PASS (all 5).

- [ ] **Step 5: Commit**

```bash
ruff format . && ruff check --fix .
git add hub/forms.py tests/hub/skill_forms_spec.py
git commit -m "Add member skill add/suggest forms with validation"
```

---

### Task 9: Skill-editor HTMX endpoints + chip partial

**Files:**
- Modify: `hub/views.py` (add `skill_add`, `skill_remove`, `skill_suggest`)
- Modify: `hub/urls.py` (3 routes)
- Create: `templates/hub/partials/profile_skills.html`
- Test: `tests/hub/skill_endpoints_spec.py`

**Interfaces:**
- Consumes: `MemberSkillForm`, `SkillSuggestionForm` (Task 8).
- Produces (all `@login_required`, POST):
  - `skill_add(request)` — name `hub_skill_add`, path `settings/skills/add/`
  - `skill_remove(request, skill_pk)` — name `hub_skill_remove`, path `settings/skills/<int:skill_pk>/remove/`
  - `skill_suggest(request)` — name `hub_skill_suggest`, path `settings/skills/suggest/`
  - Each re-renders `templates/hub/partials/profile_skills.html` (the member's current skills + add controls) and attaches a toast via `trigger_toast`. Invalid input re-renders the partial with an error toast.

**FRONTEND.md constraints:** mutating actions return a `trigger_toast()` partial, not a redirect with Django messages. Remove control is a real button. Inputs/selects use field-group classes, never inline `background`/`color`.

- [ ] **Step 1: Write the failing tests**

Create `tests/hub/skill_endpoints_spec.py`:

```python
from __future__ import annotations

from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import Member, MemberSkill, Skill
from tests.membership.factories import MemberFactory, MemberSkillFactory, SkillFactory


def _login(client: Client) -> Member:
    member = MemberFactory()
    user = User.objects.create_user(username="editor", password="pw")
    member.user = user
    member.save(update_fields=["user"])
    client.login(username="editor", password="pw")
    return member


def describe_skill_endpoints():
    def it_adds_a_skill(client):
        member = _login(client)
        skill = SkillFactory(name="Coding")
        resp = client.post(reverse("hub_skill_add"), {"skill": skill.pk, "years_experience": 8})
        assert member.skills.filter(skill=skill, years_experience=8).exists()
        assert b"Coding" in resp.content

    def it_removes_a_skill(client):
        member = _login(client)
        ms = MemberSkillFactory(member=member, skill=SkillFactory(name="Sewing"))
        resp = client.post(reverse("hub_skill_remove", args=[ms.pk]))
        assert not MemberSkill.objects.filter(pk=ms.pk).exists()
        assert b"Sewing" not in resp.content

    def it_does_not_remove_another_members_skill(client):
        _login(client)
        other = MemberSkillFactory()
        client.post(reverse("hub_skill_remove", args=[other.pk]))
        assert MemberSkill.objects.filter(pk=other.pk).exists()

    def it_suggests_a_skill(client):
        member = _login(client)
        resp = client.post(reverse("hub_skill_suggest"), {"name": "Kintsugi"})
        assert member.skills.filter(skill__name="Kintsugi", skill__status=Skill.Status.PENDING).exists()
        assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/hub/skill_endpoints_spec.py -v`
Expected: FAIL — `NoReverseMatch: 'hub_skill_add'`.

- [ ] **Step 3: Add the partial template**

Create `templates/hub/partials/profile_skills.html`:

```html
{% load hub_tags %}
<div id="profile-skills" class="pl-skill-editor">
    {% if member.skills.all %}
    <div class="pl-skill-chips">
        {% for ms in member.skills.all %}
        <span class="pl-skill-chip">
            {{ ms.skill.name }}{% if ms.years_experience is not None %}<span class="pl-skill-chip__years">{{ ms.years_experience }}y</span>{% endif %}
            {% if ms.skill.status == "pending" %}<span class="pl-skill-chip__pending" title="Awaiting admin approval">pending</span>{% endif %}
            <button type="button" class="pl-btn pl-btn--danger pl-btn--sm pl-skill-chip__remove"
                    hx-post="{% url 'hub_skill_remove' ms.pk %}" hx-target="#profile-skills" hx-swap="outerHTML">
                Remove
            </button>
        </span>
        {% endfor %}
    </div>
    {% else %}
    <p class="hub-text-muted">No skills yet — add some below.</p>
    {% endif %}

    <div class="hub-form-group pl-skill-add">
        <select name="skill" form="skill-add-form" class="pl-filter-control">
            <option value="">Choose a skill…</option>
            {% for category in skill_categories %}
            <optgroup label="{{ category.name }}">
                {% for skill in category.skills.all %}<option value="{{ skill.pk }}">{{ skill.name }}</option>{% endfor %}
            </optgroup>
            {% endfor %}
        </select>
        <input type="number" name="years_experience" form="skill-add-form" min="0" max="99" placeholder="years (optional)" class="pl-filter-control">
        <form id="skill-add-form" hx-post="{% url 'hub_skill_add' %}" hx-target="#profile-skills" hx-swap="outerHTML">
            {% csrf_token %}
            <button type="submit" class="hub-btn hub-btn--sm">Add skill</button>
        </form>
    </div>

    <form class="pl-skill-suggest" hx-post="{% url 'hub_skill_suggest' %}" hx-target="#profile-skills" hx-swap="outerHTML">
        {% csrf_token %}
        <input type="text" name="name" maxlength="80" placeholder="Can't find it? Suggest a skill" class="pl-filter-control">
        <button type="submit" class="hub-btn hub-btn--sm hub-btn--ghost">Suggest</button>
    </form>
</div>
```

> The bare `<textarea>`/`<input>` warning in FRONTEND.md rule 13 is satisfied: every control here carries `.pl-filter-control` (defined in Task 7) which uses the theme input tokens, and the add controls sit inside `.hub-form-group`. Add a small `.pl-skill-chip__pending` style to `hub.css` (muted pill) and `.pl-skill-editor`/`.pl-skill-add`/`.pl-skill-suggest` flex layout — no inline `display` on any `x-show` element.

- [ ] **Step 4: Add the views**

In `hub/views.py` add a small shared renderer + three views:

```python
def _render_profile_skills(request: HttpRequest, member: Member, message: str, level: str) -> HttpResponse:
    response = render(
        request,
        "hub/partials/profile_skills.html",
        {
            "member": member,
            "skill_categories": SkillCategory.objects.prefetch_related(
                Prefetch("skills", queryset=Skill.objects.filter(status=Skill.Status.APPROVED).order_by("name"))
            ),
        },
    )
    trigger_toast(response, message, level)
    return response


@login_required
def skill_add(request: HttpRequest) -> HttpResponse:
    member = _get_member(request)
    form = MemberSkillForm(member=member, data=request.POST)
    if form.is_valid():
        form.save()
        return _render_profile_skills(request, member, "Skill added.", "success")
    return _render_profile_skills(request, member, form.errors_as_text(), "error")


@login_required
def skill_remove(request: HttpRequest, skill_pk: int) -> HttpResponse:
    member = _get_member(request)
    member.skills.filter(pk=skill_pk).delete()
    return _render_profile_skills(request, member, "Skill removed.", "success")


@login_required
def skill_suggest(request: HttpRequest) -> HttpResponse:
    member = _get_member(request)
    form = SkillSuggestionForm(member=member, data=request.POST)
    if form.is_valid():
        form.save()
        return _render_profile_skills(request, member, "Thanks! Your skill is pending review.", "success")
    return _render_profile_skills(request, member, form.errors_as_text(), "error")
```

Add imports: `from hub.forms import MemberSkillForm, SkillSuggestionForm` (extend existing) and ensure `trigger_toast` is imported (`from hub.toast import trigger_toast`).

> `form.errors_as_text()` is a built-in Django method — no custom error formatting needed. `skill_remove` scoping by `member.skills.filter(pk=...)` is what makes the "can't remove another member's skill" test pass — it's a `MemberSkill` pk, owned via the reverse FK.

- [ ] **Step 5: Add the URLs**

In `hub/urls.py`, after the `settings/profile-photo/delete/` route, add:

```python
    path("settings/skills/add/", views.skill_add, name="hub_skill_add"),
    path("settings/skills/<int:skill_pk>/remove/", views.skill_remove, name="hub_skill_remove"),
    path("settings/skills/suggest/", views.skill_suggest, name="hub_skill_suggest"),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/hub/skill_endpoints_spec.py -v`
Expected: PASS (all 4).

- [ ] **Step 7: Commit**

```bash
ruff format . && ruff check --fix .
git add hub/views.py hub/urls.py templates/hub/partials/profile_skills.html static/css/hub.css tests/hub/skill_endpoints_spec.py
git commit -m "Add HTMX skill add/remove/suggest endpoints"
```

---

### Task 10: Profile settings — commissions fields + skills section

**Files:**
- Modify: `hub/forms.py` (`ProfileSettingsForm.Meta.fields`, labels, widgets, help_texts)
- Modify: `hub/views.py` (`user_settings` — pass `skill_categories` + member skills to the template context for the profile tab)
- Modify: `templates/hub/user_settings.html` (render the Skills & Commissions section in the profile tab)
- Test: `tests/hub/profile_skills_spec.py`

**Interfaces:**
- Consumes: Tasks 2, 8, 9; the `show_skills` visibility field auto-generated from `DIRECTORY_TOGGLEABLE_FIELDS`.
- Produces: `open_for_commissions` + `commission_note` editable on the profile form; the profile tab includes `{% include "hub/partials/profile_skills.html" %}` and the commissions toggle/note.

**FRONTEND.md constraints:** `commission_note` field via `components/form_field.html`; `open_for_commissions` via `components/toggle.html`; reveal the note with `x-show` but keep `display` in a CSS class (rule 12); 4+ field form stays inline on the dedicated settings page (not a modal).

- [ ] **Step 1: Write the failing tests**

Create `tests/hub/profile_skills_spec.py`:

```python
from __future__ import annotations

from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import Member
from tests.membership.factories import MemberFactory


def _login(client: Client) -> Member:
    member = MemberFactory()
    user = User.objects.create_user(username="ed", password="pw")
    member.user = user
    member.save(update_fields=["user"])
    client.login(username="ed", password="pw")
    return member


def describe_profile_commissions():
    def it_saves_open_for_commissions_and_note(client):
        member = _login(client)
        data = {
            "form_id": "profile",
            "preferred_name": "Jo",
            "open_for_commissions": "on",
            "commission_note": "Custom woodworking welcome!",
            "show_in_directory": "on",
        }
        client.post(reverse("hub_user_settings"), data)
        member.refresh_from_db()
        assert member.open_for_commissions is True
        assert member.commission_note == "Custom woodworking welcome!"

    def it_renders_skills_section_on_profile_tab(client):
        _login(client)
        resp = client.get(reverse("hub_user_settings"))
        assert b"profile-skills" in resp.content
```

> The exact `form_id`/POST keys must match `user_settings`' existing profile-form handling (`hub/views.py:1225-1236`). Read that block first and mirror its required fields so the POST validates.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/hub/profile_skills_spec.py -v`
Expected: FAIL — `open_for_commissions` not saved / `profile-skills` not in page.

- [ ] **Step 3: Add fields to the form**

In `hub/forms.py`, `ProfileSettingsForm.Meta.fields` (`:202-213`) append `"open_for_commissions"` and `"commission_note"`. Add to `widgets`:

```python
            "commission_note": forms.Textarea(
                attrs={"rows": 2, "placeholder": "e.g. Small custom woodworking, websites, AI consulting — happy to chat!"}
            ),
```

Add to `labels`:

```python
            "open_for_commissions": "Open for commissions",
            "commission_note": "What kind of work do you welcome?",
```

- [ ] **Step 4: Pass skills context to the template**

In `hub/views.py` `user_settings`, add to the render context (alongside `profile_form`):

```python
        "skill_categories": SkillCategory.objects.prefetch_related(
            Prefetch("skills", queryset=Skill.objects.filter(status=Skill.Status.APPROVED).order_by("name"))
        ),
```

(`member` is already in context for the profile tab; the partial uses `member.skills.all`.)

- [ ] **Step 5: Render the section in the profile tab**

In `templates/hub/user_settings.html`, inside the profile-tab form (after the existing fields, before the submit button), add the commissions block and include the skills partial. Wrap in a `hub-card`-style section, use the toggle component and reveal note via class-based `x-show`:

```html
<div class="pl-profile-section" x-data="{ commissions: {% if profile_form.open_for_commissions.value %}true{% else %}false{% endif %} }">
    <h2 class="pl-profile-section__title">Skills &amp; Commissions</h2>

    {% include "components/toggle.html" with field=profile_form.open_for_commissions toggle_label="Open for commissions!" toggle_description="Show a badge letting members know you welcome contract work, custom jobs, or consulting." %}

    <div class="pl-commission-note" x-show="commissions" x-cloak
         @toggle-changed.window="commissions = $event.detail">
        {% include "components/form_field.html" with field=profile_form.commission_note %}
    </div>
</div>

<div class="pl-profile-section">
    <h2 class="pl-profile-section__title">My skills</h2>
    <p class="hub-text-muted">List what you make and do — members can search the directory by skill.</p>
    {% include "hub/partials/profile_skills.html" %}
</div>
```

Add to `hub.css`: `.pl-commission-note { display: block; }` (so `x-show` only toggles `display:none`, per rule 12 — never inline `display`).

> If `components/toggle.html` doesn't emit a `toggle-changed` event, bind the reveal directly to the checkbox instead: `<input ... @change="commissions = $event.target.checked">` already inside the toggle, or drive `commissions` off the field's checkbox id via `x-model`. Read `components/toggle.html` first and wire whichever the component supports; the requirement is: note hidden until the toggle is on, no inline `display`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/hub/profile_skills_spec.py -v`
Expected: PASS (both).

- [ ] **Step 7: Visually verify**

`runserver` → `/settings/` profile tab: toggle commissions reveals note; add/remove/suggest skills works without a page reload and fires toasts; both themes legible.

- [ ] **Step 8: Commit**

```bash
ruff format . && ruff check --fix .
git add hub/forms.py hub/views.py templates/hub/user_settings.html static/css/hub.css tests/hub/profile_skills_spec.py
git commit -m "Add skills and commissions to profile settings"
```

---

### Task 11: Version bump + changelog + full suite

**Files:**
- Modify: `plfog/version.py`
- Test: full suite + coverage

- [ ] **Step 1: Bump version and add changelog**

In `plfog/version.py`, set `VERSION = "0.20.0"` and prepend to `CHANGELOG`:

```python
    {
        "version": "0.20.0",
        "date": "2026-06-24",
        "title": "Show off your skills and open up for commissions",
        "changes": [
            "Your directory profile can now list the things you make and do — woodworking, welding, "
            "music production, web design, and lots more — with optional years of experience next to each one.",
            "Flip on 'Open for commissions!' and add a short note if you're happy to take on custom work, "
            "contract jobs, or consulting, so other members know they can reach out.",
            "The member directory is now searchable and filterable by skill, and you can show just the "
            "members who are open for commissions — handy when you're looking for the right person for a project.",
            "Can't find your skill in the list? Suggest it, and it'll show on your profile while an admin reviews it.",
        ],
    },
```

- [ ] **Step 2: Run the full suite with coverage**

Run: `pytest --cov --cov-report=term-missing -q`
Expected: PASS, 100% coverage on new code (`membership/models.py` additions, `hub/forms.py`, `hub/views.py` new views, admin). Add tests for any uncovered branch (e.g. `MemberSkillForm` cap path, suggest dup path) until green.

- [ ] **Step 3: Lint, format, type-check**

Run: `ruff format . && ruff check . && mypy .`
Expected: all clean.

- [ ] **Step 4: Commit**

```bash
git add plfog/version.py
git commit -m "Bump to 0.20.0 — skills directory and commissions"
```

- [ ] **Step 5: Push**

```bash
git push origin release-0.19.x
```

---

## Self-Review

**Spec coverage:**
- Curated, categorized vocabulary → Tasks 1, 4. ✓
- Suggest-then-approve (pending visible only to suggester; approved joins public/filter) → Tasks 1 (status), 3 (approved-only queries), 5 (admin approve), 8 (suggest form), 9 (endpoint). ✓ Pending skills appear on the member's own editor (`member.skills.all` in the partial) but never in `approved_skills`/`with_skill`/`search_skills` used by the public directory. ✓
- `MemberSkill` with optional years → Tasks 1, 7 (display), 8/9 (edit). ✓
- `open_for_commissions` + `commission_note` (280) on `Member` → Tasks 2, 7 (badge), 10 (edit). ✓
- Privacy: `"skills"` toggle + hidden members never surfaced by skill → Tasks 2, 6 (`it_never_surfaces_hidden_members_via_skill`), 7 (`is_public("skills")` gate). ✓
- Filtering composes (skill + commissions + q + guild) → Task 6 (all applied to the same visibility-floored `member_qs`). ✓
- Skill cap (15) → Tasks 2 (constant), 8 (enforced + tested). ✓
- FRONTEND.md standards → Tasks 7, 9, 10 carry explicit binding constraints (components, toast, no inline form-control colors, `pl-` prefix, `x-show` display rule, both themes). ✓
- Version + changelog → Task 11. ✓

**Open questions from the spec (defaults chosen, noted for the reviewer):**
- Skill cap 15 (configurable constant). ✓ implemented.
- Suggestions: admins only approve (Task 5). ✓
- `commission_note` 280 chars. ✓
- Search scope = skill names + display name only (not the commission note) — Task 3 `search_skills`. ✓

**Placeholder scan:** none — every code step carries complete code. The two "read the existing block first" notes (Task 5 `MemberAdmin` shape, Task 10 `form_id` POST keys, toggle event name) are deliberate integration checks against existing code, each with a concrete fallback, not deferred work.

**Type consistency:** `with_skill`/`open_for_commissions`/`search_skills` (Task 3) are the exact names called in Task 6. `approved_skills` (Task 3) is the exact name used in Task 7. `MemberSkillForm`/`SkillSuggestionForm` (Task 8) match Task 9 imports. `Member.MAX_SKILLS` (Task 2) matches Tasks 8/9. `_render_profile_skills` is internal to Task 9. Partial id `profile-skills` matches the HTMX `hx-target` in every endpoint and the Task 10 assertion. ✓
