# plfog - Past Lives Makerspace

Django app for membership and studio rental management at Past Lives Makerspace (Portland, OR).

Repo: https://github.com/Past-Lives-Makerspace/plfog

> **Quick orientation:** See [CODEBASE_INDEX.md](CODEBASE_INDEX.md) for the full app map, models, URL structure, and integration overview. Each app also has its own `CLAUDE.md` with per-app details.
> **Frontend:** See [FRONTEND.md](FRONTEND.md) for the component library, design system, and rules for building pages.

## Commands

- `pytest` - Run tests
- `python manage.py runserver` - Dev server
- `ruff check .` - Lint
- `ruff format .` - Format
- `mypy .` - Type check

## Testing

BDD/spec style with pytest-describe. Test files named `*_spec.py`. Functions named `it_*` inside `describe_*` blocks.

## Environments

| Environment | Platform | Purpose | DB |
|---|---|---|---|
| **Production** | Render.com | Live app for members | PostgreSQL (via `DATABASE_URL`) |
| **QA / Staging** | Hetzner VPS (`pastlives.plaza.codes`) | Testing before prod | PostgreSQL |
| **Local dev** | WSL2 | Development | SQLite (default) |

Hetzner is **NOT production**. Render is production. Do not confuse these.

## Settings

All configuration via environment variables. See `plfog/settings.py` for available env vars.

## Versioning & Changelog

**A PR adds one file to `changelog.d/` and never touches a version number.** That is the whole
release ritual. `changelog.d/README.md` is the authoring contract; read it before writing one.

```toml
# changelog.d/394-composer-drafts.toml
bump = "minor"          # patch | minor | major
date = "2026-09-13"
title = "The class composer keeps what you typed"
changes = [
  "If the page reloads while writing a class, your next visit offers back what you had typed.",
]
```

`VERSION` and `CHANGELOG` in `plfog/version.py` are **computed at import** from
`changelog/base.json` (the version the fold starts from), `changelog.d/*.toml` (one fragment
per unreleased change) and `changelog/history.json` (258 releases frozen at v1.62.1). The
machinery and the reasoning live in `plfog/changelog.py`.

This replaced a rule where every PR hand-edited the `VERSION` literal at line 5 of a 3,412-line
`plfog/version.py` and inserted an entry at the head of its `CHANGELOG` list. Two PRs open at
once collided at both spots: 15 of the 60 merges before this change — 25% — had to resolve that
file, about 2.4 renumber events a week. **If you find a doc, skill or profile still telling you
to bump `VERSION`, it is stale; fix it.**

- **The version is folded, never written.** Count the bumps in `changelog.d/`, apply them to
  the base. Order-independent by construction, which is what makes a rebase safe — a PR that
  merges late cannot renumber a release that already shipped. Nothing in a PR names a number,
  so nothing in a PR can be stale or collide.
- **One fragment per feature, and edit your own.** A refinement to something still sitting
  unreleased in `changelog.d/` edits that fragment — it is your file, nothing else claims it,
  and the combined result goes out once. A fix to something **already live** is its own
  fragment with `bump = "patch"`: members lived with the bug, so it is news.
- **`audience = "internal"` is the tooling release.** `bump` and nothing else — no title, no
  bullets. It moves the version and announces nothing. This used to be a judgement call
  ("do NOT invent an entry to satisfy the rule") and is now a declaration.
- **Entries are plain, member-friendly language** — no jargon, PR numbers, or commit hashes.
- **Only swept history carries version numbers.** A fragment cannot know its own release
  number without merge order, which the tree does not record, so new entries are identified by
  date and the changelog modal renders the version badge only when there is one. Frozen
  entries keep the numbers they shipped under.
- **Sweeping** moves fragments into `changelog/history.json` and `changelog/base.json` forward.
  It is deliberate housekeeping, usually right after the release email goes out, and it is a
  the thing that keeps a second major's number exact. Nothing breaks if you never do it:
  the fold approximates rather than failing, because it runs at app import.

### Was this really impossible before?

Issue #358 concluded that `VERSION` had to stay a hand-edited literal because **a workflow
cannot push to `main`**. That half is true and still is: the ruleset on the default branch has
an empty bypass list, and both `GITHUB_TOKEN` and `BOT_PAT` are rejected with `GH013`.

The conclusion drawn from it was wrong. The version does not have to live on the branch — and
`release.yml` had been pushing a **tag** on every single merge, with plain `GITHUB_TOKEN`, the
entire time. Here the version lives on no ref at all: it is a pure function of files on disk.
Issue #365 (the fragments child) declined itself over a cost that only exists if the literal
stays — "a PR would name the version number twice... the fragment's stamp can silently go
stale on a rebase" — so it declined a crippled design rather than this one. **Retest the
premise before you inherit a conclusion from it.**

## Automated PR review

Every pull request marked **Ready for Review** is reviewed by PastLivesReviewBot
automatically, and approved if it has no blockers. Nobody has to ask for it.

- The workflow is `.github/workflows/bot-review.yml`; the rubric it reviews
  against is `.github/bot-review-prompt.md`. **The rubric is the single source of
  truth for what counts as a blocker** — the manual `/pl-bot-review-pr` command
  reads the same file, so change the rubric, not one of the two callers.
- The model writes a verdict file and nothing else;
  `.github/scripts/bot_review_post.py` is what actually posts the review, and it
  is the only thing holding `BOT_PAT`. Its fail-closed behaviour is specced in
  `tests/scripts/bot_review_post_spec.py` — change one, change the other.
- It runs on `pull_request_target` so that PRs from forks are reviewed too. That
  trigger holds secrets, so the contributor's code is never checked out and
  never executed — the change is reviewed as diff text. The header comment in
  the workflow explains the four rules that keep this safe. Read it before
  editing that file.
- **The approval is not a merge.** `main`'s ruleset requires one approving
  review, so the bot's approval is what unblocks the merge, but a human still
  performs it. That gate is deliberate: a push to main deploys to Render and
  fires the Discord announcement.
- Blockers are posted as a **comment**, not a `REQUEST_CHANGES` review, so a PR
  is never stranded behind a blocking review only the bot can dismiss.
  Withholding the approval is already the block.
- It reviews once per PR, on open / reopen / ready-for-review — not on every
  push. To get a **re-review** after fixing something, add the `bot-review`
  label. To opt a PR out entirely, add `no-bot-review`.
- It needs two repo secrets: `CLAUDE_CODE_OAUTH_TOKEN` (from `claude
  setup-token`) and `BOT_PAT`. If a review ever fails to produce a verdict, the
  bot says so on the PR and approves nothing — it fails closed.

## Discord Notifications

`.github/workflows/release.yml` tags the release and posts the announcement, on one trigger,
from one plan. It **announces the fragments the push added** (`git diff --diff-filter=A`
against the tip of main before the push), not the entries matching a version string.

That difference closes the last way a release could go quiet. The old workflow filtered
`CHANGELOG` for entries stamped at the exact current `VERSION`, and an entry stamped at the
wrong number deployed, announced nothing, and went green — indistinguishable from a tooling
release that deliberately carried no entry. The planner now knows which it is, because the PR
said so in its fragment.

Consequences worth knowing:

- **Editing a fragment that already shipped re-announces nothing.** It is not an added file.
  This used to be a rule a maintainer had to hold in their head; it is now a property of the
  diff.
- **A release carrying only `audience = "internal"` fragments tags and announces nothing**,
  without anyone deciding to withhold an entry.
- **A push that adds no fragment moves no version**, so the tag already exists and the run is a
  no-op with a `::warning`. Not a red X: a PR carrying the `no-changelog` label legitimately
  adds none.
- The post is chunked under Discord's 4096-char embed limit and **fails loudly** on a rejected
  post.

### The check that replaced the release guard

`.github/scripts/release_guard.py` is gone. It caught exactly one shape — a push that edited
`plfog/version.py` and left the literal alone — and its own docstring admitted the shape it
could not reach: "a merge that never touches `plfog/version.py` at all does not fail anything...
that is the ordinary forgot-to-bump mistake."

**`.github/scripts/check_changelog_fragment.py` now fails the pull request** that adds no
fragment and carries no `no-changelog` label, and rejects a malformed one. It runs in `ci.yml`
on `pull_request`. That is both earlier and wider than the guard: it catches the mistake the
guard could not see, on a branch nobody has deployed, instead of on main after Render has.

The `#348` shape it replaced is no longer expressible. There is no literal to leave alone.

### Recovering a missed announcement

The release is live and members heard nothing. The lever is `gh workflow run release.yml`,
which re-announces the **newest member-facing fragment** in `changelog.d/`.

**Look before you pull it.** It re-announces whatever is newest, which is right when a post
failed to send and wrong when the post landed and something else went quiet. Re-posting one
members have seen announces the wrong release, and a Discord post cannot be unsent. Nothing may
be owed at all. That judgment is a human's, and there is deliberately no rule here that makes
it for you — three attempts at writing one each produced a different way to double-post.

`python manage.py announce_release` is **not** a companion to the manual run. It sends the
release **email**, but `release.published` is registered on the in-app, email *and* Discord
channels (`core/events/registry.py:638`), so it posts to Discord too — run both and members get
the same release announced twice.

---

# PLFOG Django Coding Standards

You are an AI coding assistant working on the PLFOG Django project. Follow these standards exactly.

```yaml
LINE_LENGTH: 120
COVERAGE_TARGET: 100
PYTHON_VERSION: "3.13"
MUTATION_TESTING: true
DJANGO_SETTINGS_MODULE: "plfog.settings"
```

## 1. General Principles

- **Fat models, skinny views** — all business logic lives in models and managers, never in views.
- **Fail loudly** — raise exceptions on unexpected values. `dict[key]` not `dict.get(key, default)`.
- **Explicit over implicit** — env vars with clear names, no magic defaults.
- **Type everything** — all functions have full type annotations, including `-> None`.
- **Test everything** — 100% coverage, BDD-style tests, mutation testing.

---

## 2. Architecture — Fat Models, Skinny Views

| Layer | Responsibility |
|-------|----------------|
| **Views/ViewSets** | HTTP request/response only — parse request, call model method, return response |
| **Forms** | Validation — all input validation lives in Django forms, not views |
| **Models** | All business logic — calculations, data transformations, state changes |
| **Managers** | Complex querysets — filtering, annotations, aggregations across records |

**WRONG — logic in views:**
```python
class MembershipView(APIView):
    def post(self, request):
        member = Member.objects.get(pk=request.data["member_id"])
        if member.membership_end and member.membership_end > timezone.now():
            return Response({"error": "Already active"}, status=400)
        member.membership_start = timezone.now()
        member.membership_end = timezone.now() + timedelta(days=365)
        member.status = "active"
        member.save()
        send_mail("Welcome!", "Your membership is active.", None, [member.email])
        return Response({"status": "activated"})
```

**RIGHT — logic in models, validation in forms:**
```python
# models.py
class Membership(models.Model):
    member = models.ForeignKey(Member, on_delete=models.CASCADE, help_text="The member this belongs to.")
    starts_at = models.DateTimeField(help_text="When the membership period begins.")
    ends_at = models.DateTimeField(help_text="When the membership period expires.")

    @property
    def is_active(self) -> bool:
        return self.starts_at <= timezone.now() < self.ends_at

    def renew(self, duration_days: int = 365) -> None:
        """Extend or create a new membership period."""
        self.starts_at = max(self.ends_at, timezone.now())
        self.ends_at = self.starts_at + timedelta(days=duration_days)
        self.save()
        self.member.send_membership_confirmation()

# forms.py — validation lives here, not in views
class MembershipRenewalForm(forms.Form):
    member = forms.ModelChoiceField(queryset=Member.objects.all())

    def clean_member(self):
        member = self.cleaned_data["member"]
        active = member.membership_set.filter(ends_at__gt=timezone.now()).exists()
        if active:
            raise ValidationError("Member already has an active membership.")
        return member

# views.py (minimal — no business logic, no validation)
class MembershipViewSet(viewsets.ModelViewSet):
    def create(self, request):
        form = MembershipRenewalForm(data=request.data)
        if not form.is_valid():
            return Response({"errors": form.errors}, status=400)
        membership = Membership.objects.create(member=form.cleaned_data["member"])
        membership.renew()
        return Response(self.get_serializer(membership).data, status=201)
```

| Use Case | Location |
|----------|----------|
| Single object operations | Model methods (`membership.renew()`, `member.deactivate()`) |
| Input validation | Forms (`MembershipRenewalForm.clean_member()`) |
| Querying/filtering | Manager (`Membership.objects.active()`) |
| Aggregations across records | Manager |
| Object-specific calculations | Model properties (`membership.is_active`) |
| Cross-model orchestration | Service module (`services.py`) |

**Signals:** Prefer model methods over signals. Use signals only for cross-app decoupling where the sender should not know about the receiver.

---

## 3. Model Patterns

### TextChoices, help_text, and \_\_str\_\_

```python
class MyModel(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=255, help_text="Display name shown to the customer.")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT,
        help_text="Current lifecycle status.",
    )
    metadata = models.JSONField(default=dict, blank=True, help_text="Arbitrary key-value data.")

    def __str__(self) -> str:
        return f"{self.name} ({self.get_status_display()})"
```

Rules: `TextChoices` for all choice fields. `help_text` on every field. `default=dict` never `default={}`. Meaningful `__str__` on every model.

### Indexes and Constraints

```python
class Meta:
    indexes = [
        models.Index(
            fields=["status", "created_at"],
            name="idx_%(class)s_active_created",
            condition=models.Q(status="active"),
        ),
    ]
    constraints = [
        models.UniqueConstraint(fields=["account", "email"], name="uq_%(class)s_account_email"),
    ]
```

Use partial indexes for filtered queries. `UniqueConstraint` over deprecated `unique_together`.

**Migrations:** One migration per logical change. Squash old migrations when the chain gets long. Never hand-edit migration files without understanding the dependency graph. Data migrations MUST include a reverse function — never use `migrations.RunPython.noop` as the reverse without explicit approval.

### Properties vs Methods

`@property` for cheap derived data. Methods for expensive operations or side effects:

```python
@property
def is_overdue(self) -> bool:
    return self.due_at is not None and self.due_at < timezone.now() and self.status != self.Status.COMPLETED

def send_reminder(self) -> None:
    """Has side effects — not a property."""
    ...
```

### Soft Delete

```python
class ActiveManager(models.Manager):
    def get_queryset(self) -> models.QuerySet:
        return super().get_queryset().filter(deleted_at__isnull=True)

class MyModel(models.Model):
    deleted_at = models.DateTimeField(null=True, blank=True, help_text="Set when soft-deleted.")
    objects = ActiveManager()
    all_objects = models.Manager()

    def soft_delete(self) -> None:
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])
```

### Avoid N+1 Queries

```python
# WRONG: N+1 — each iteration hits the DB
for order in Order.objects.all():
    print(order.customer.name)

# RIGHT: prefetch in one query
for order in Order.objects.select_related("customer"):
    print(order.customer.name)
```

---

## 4. Error Handling

When a key must exist, use `dict[key]` not `dict.get(key, fallback)`. Silent fallbacks hide bugs for weeks.

`DoesNotExist` should be re-raised as a domain-appropriate exception, not swallowed:

```python
# In service/model code: re-raise as ValueError
try:
    return self.get(email=email)
except self.model.DoesNotExist:
    raise ValueError(f"No record found for email '{email}'")

# In views: return a proper HTTP error
try:
    obj = MyModel.objects.get(pk=pk)
except MyModel.DoesNotExist:
    return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
```

**Domain exceptions** over generic ones:

```python
class InsufficientStockError(Exception):
    """Raised when an order exceeds available inventory."""
```

---

## 5. Permissions

Use **django-guardian** for object-level permissions. Define permissions on the model, assign them per-object via guardian.

```python
class Project(models.Model):
    name = models.CharField(max_length=200, help_text="Project name")
    owner = models.ForeignKey(User, on_delete=models.CASCADE, help_text="Project owner")

    class Meta:
        permissions = [
            ("view_project", "Can view this project"),
            ("manage_members", "Can manage project members"),
        ]
```

Assign and check per-object:

```python
from guardian.shortcuts import assign_perm, get_objects_for_user

# Assign
assign_perm("view_project", user, project)
assign_perm("manage_members", admin_user, project)

# Check
user.has_perm("myapp.view_project", project)

# Filter querysets to permitted objects
visible = get_objects_for_user(user, "myapp.view_project", Project)
```

Rules:
- Define permissions in `Meta.permissions`, not ad-hoc strings
- Check permissions in views via `has_perm()` or DRF's `DjangoObjectPermissions`
- Never hardcode role checks (`if user.role == "admin"`) — use permissions
- Assign permissions at the point of creation (e.g., owner gets all perms when creating an object)

---

## 6. Type Hints

All functions typed including `-> None`. Use `TYPE_CHECKING` for annotation-only imports. Use lazy imports inside methods for runtime imports that would cause circular dependencies. Google-style docstrings for anything non-obvious:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from myapp.models import RelatedModel

def calculate_discount(self, subtotal: Decimal, code: str) -> Decimal:
    """Apply a discount code to the subtotal.

    Args:
        subtotal: The pre-discount order total.
        code: The discount code to apply.

    Returns:
        The discount amount in the same currency as the subtotal.

    Raises:
        InvalidCodeError: If the code is expired or does not exist.
    """
```

---

## 7. Testing

### BDD-Style with pytest-describe

Tests use `pytest-describe` with `describe_*` blocks nesting `it_*` tests. Test files are `*_spec.py` in a `spec/` subdirectory per app. The `spec/` directory signals BDD-style organization.

> **Only `describe_*` nests.** `context_*` is NOT a collected prefix here (see the pytest config below), so a `context_*` block is silently skipped and every `it_*` inside it never runs. Use `describe_*` for every nested block, including conditional ones (`describe_when_…`).

```
myapp/
    spec/
        conftest.py            # Shared fixtures
        models/
            my_model_spec.py
        views/
            my_view_spec.py
    factories.py               # factory-boy factories
```

### Canonical Test Example

```python
import pytest
from decimal import Decimal
from django.core.exceptions import ValidationError
from myapp.factories import MyModelFactory, RelatedModelFactory

def describe_MyModel():
    def describe_calculate_total():
        def it_sums_line_items(db):
            record = MyModelFactory(items=[RelatedModelFactory(price=10, quantity=2)])
            assert record.calculate_total() == Decimal("20.00")

        def describe_with_no_items():
            def it_returns_zero(db):
                record = MyModelFactory(items=[])
                assert record.calculate_total() == Decimal("0.00")

    def describe_place():
        def it_sets_status_to_placed(db):
            record = MyModelFactory()
            record.place()
            record.refresh_from_db()
            assert record.status == "placed"

        def describe_with_insufficient_stock():
            def it_raises_validation_error(db):
                record = MyModelFactory(items=[RelatedModelFactory(quantity=100)])
                with pytest.raises(ValidationError):
                    record.place()
```

### factory-boy for Test Data

```python
class MyModelFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MyModel
    name = factory.Sequence(lambda n: f"Record {n}")
    status = MyModel.Status.DRAFT
    email = factory.LazyAttribute(lambda o: f"{o.name.lower().replace(' ', '.')}@example.com")
```

### Test Rules

- `conftest.py` for shared fixtures across an app's specs
- `@pytest.fixture` inside describe blocks for scoped fixtures
- `factory-boy` for all test data
- `respx` for HTTP mocking (not `responses` or `httpretty`)
- Mock external services, never mock models or the database
- 100% branch coverage
- 100% mutation kill rate (pytest-leela)
- No `@pytest.mark.skip`, `# pragma: no cover`, or `# pragma: no mutate` without explicit approval

### pytest Config

```toml
[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "plfog.settings"
python_files = ["*_spec.py", "test_*.py"]
python_classes = ["Describe*"]
# Only these prefixes are collected. `context_*` is deliberately absent — a
# context_* block is silently skipped and the it_* tests inside it never run.
python_functions = ["it_*", "test_*", "describe_*"]
addopts = "--strict-markers --tb=short -q"
```

---

## 8. Code Style

### Ruff

```toml
[tool.ruff]
line-length = 120
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "B", "SIM", "C4", "DJ", "RUF"]
# E/F: pyflakes+pycodestyle, I: isort, N: naming, UP: pyupgrade,
# B: bugbear, SIM: simplify, C4: comprehensions, DJ: django, RUF: ruff-specific

[tool.ruff.lint.mccabe]
max-complexity = 10
```

Run before every commit: `ruff format . && ruff check --fix .`

### Coverage

```toml
[tool.coverage.run]
source = ["plfog", "core", "membership"]
branch = true
omit = ["*/migrations/*", "*/spec/*", "manage.py"]

[tool.coverage.report]
fail_under = 100
show_missing = true
exclude_lines = ["pragma: no cover", "if TYPE_CHECKING:", "pass"]
```

---

## 9. Views & Admin

### Thin Views

```python
class MyModelViewSet(viewsets.ModelViewSet):
    queryset = MyModel.objects.all()
    serializer_class = MyModelSerializer

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        instance = self.get_object()
        instance.activate()
        return Response(self.get_serializer(instance).data)
```

### Validation in Forms, Serialization in DRF

Django forms own validation. DRF serializers are for API serialization only:

```python
class MyModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = MyModel
        fields = ["id", "name", "status", "email", "created_at"]
        read_only_fields = ["id", "status", "created_at"]
```

### Admin Auto-Registration

```python
from django.contrib import admin
from django.apps import apps

# Register custom admins first, then auto-register the rest
for model in apps.get_app_config("plfog").get_models():
    try:
        admin.site.register(model)
    except admin.sites.AlreadyRegistered:
        pass
```
