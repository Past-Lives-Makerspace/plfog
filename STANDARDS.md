# plfog Standards

How code in this repo is built and tested, and the traps that have cost real time. Written for every contributor, human or agent. `FRONTEND.md` covers templates and CSS, `CONTEXT.md` defines the domain language, and `docs/HELP_AUTHORING.md` covers Help Center guides.

```yaml
LINE_LENGTH: 120
COVERAGE_TARGET: 100  # the CI gate is 98, see section 8
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
- 100% branch coverage on new code (the CI gate is 98% overall)
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
fail_under = 98
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

---

## 10. Testing Traps

Each of these has cost at least one broken merge or a lost afternoon.

- **Judge a test run by pytest's own result.** `pytest ... | tail` returns `tail`'s exit code, so a failing run reads as success. Redirect to a file and check `$?`, or read the `N passed, M failed` summary line. Never chain a commit onto a piped test command.
- **The changelog renders on every page.** `core/context_processors.py` puts the whole changelog into every template context, hub and public classes pages alike. A negative assertion on UI copy (`b"Welcome email" not in content`) fails the day a changelog entry uses that phrase, and a positive one can pass before the feature renders at all. Anchor assertions on structural markup (a class, an id, a URL) or on distinctive factory strings that can never appear in changelog prose.
- **Coverage.** The gate is `fail_under = 98` in `pyproject.toml`; 100% is the target for new code. A full local run can finish with every test passing and still exit 1 on the total (about 93%) because some markers are deselected locally. CI's `test` job is the authority on the total; locally, read the coverage table for the files you changed.
- **The mutation gate can be silently off.** pytest-leela returns early from `pytest_sessionfinish` when the exit status is non-zero, so any failing test or coverage shortfall disables mutation testing without a message, and CI runs that step with `continue-on-error: true`. A run with no mutation report is not a run with no survivors. On a local subset, pass `--cov-fail-under=0` and make every selected spec pass.
- **Browser-writing e2e specs flake on SQLite.** When the browser POSTs while the test reads the database, SQLite's single writer lock raises `database table is locked`, the view returns 500 and the test fails intermittently. CI runs e2e on PostgreSQL (`.github/workflows/playwright.yml`); reproduce there before suspecting the product: `DATABASE_URL=postgres://... pytest -m e2e --no-cov -o addopts="" tests/e2e/<file>.py`.
- **pytest does not run Django system checks; CI does.** Run `python manage.py check` after any model or migration change. Index and constraint names are capped at 30 characters (`models.E034`), and nothing local catches it.

## 11. Pull Requests

- Every PR adds one `changelog.d/` fragment or carries the `no-changelog` label. `changelog.d/README.md` is the contract.
- A PR in a conflicting state gets no CI runs for new pushes, and nothing says so. Resolve the conflict and the next push runs CI.
- After rebasing onto `main`, check migration numbering (`ls <app>/migrations | tail`, `python manage.py makemigrations --check`). Renumber past new arrivals and repoint `dependencies`.

## 12. Deploys

- Production runs on Render from `main`, and the Dockerfile `CMD` is the deploy: `migrate --noinput && seed_notification_templates --quiet && gunicorn`. Migrations apply at container start while the previous release is still serving, so a migration must work with the old code. Add before you remove, and prefer repairing data in a code path over a data migration that assumes the new code.
- `render.yaml` is not linked to the Render service. A variable declared there does not reach production; it has to be set on the service itself.
- Deploys do not run `seed_help_center` or `seed_example_guild`. A change to `membership/help_content.py` or `membership/example_guild.py` reaches production only when someone runs those commands against it after the deploy.

## 13. Domain Traps

- **Class review is sequential.** `ClassOffering.submit_for_review` opens only the first-stage gate: the Guild Lead when the category's guild has a lead, otherwise Admin. A pending guilded class therefore has a `GUILD_LEAD` `ClassApproval` row and no `ADMIN` row until it escalates. Any change to review has to handle rows already in production in that shape; a design that assumes both rows exist strands them in no reviewer's queue.
- **There are two push channels.** Web push (pywebpush and VAPID: `PushSubscription`, `core/push.py`) works in browsers and installed PWAs but never reaches the Android app, because Android System WebView has no Push API. The app uses FCM (`core/fcm.py`, `FcmDevice`, and `static/js/native-push.js`, which registers only when `Capacitor.isNativePlatform()`). `PushAdapter` in `core/events/channels.py` fans out to both. The Capacitor plugin ships inside the app binary, so a plugin change needs a new app release (`mobile/README.md`).
- **Discord API behaviour differs from its docs** (each verified in production):
  - A modal response (type 9) must go through the REST callback, `POST /interactions/{id}/{token}/callback`, not inline from the HTTP interactions endpoint. An inline modal is discarded and the member sees "didn't respond in time". `_route_modal` in `core/events/discord_commands.py` does this, and the callback returns validation errors instead of hiding them.
  - A Label component's (type 18) description is capped at 100 characters, not the documented 200; over the cap the whole modal is rejected. `modal_label()` in `core/events/discord_interactions.py` guards it.
  - Poll messages can never be edited (error 520003), and a type 6 acknowledgement whose original message is a poll makes follow-ups fail silently. Acknowledge poll-adjacent clicks with an ephemeral type 5.
  - Server admins bypass command-permission restrictions, so test a denial as a non-admin.
- **Demo and example content.**
  - The `display_demo_classes` site setting hides classes whose slug starts with `demo-`. It is enforced in two places, `ClassOfferingQuerySet.public()` and `ClassSessionQuerySet.upcoming_public()` (the Discord digest reads the second). A new public surface must go through one of them.
  - `Category.name` is unique, so demo categories are prefixed `[DEMO]` to avoid clashing with real ones.
  - The example guild must never get `CommunityEvent` rows: published events reach the public calendar and Discord whatever the guild's `is_active`. `membership/example_guild.py` documents its safety contract.

## 14. Building Features

- **Parity means the same surfaces.** When a request says "make X work like Y", reuse Y's actual screens, components and data shape by name. Borrowing Y's vocabulary for a new design is not parity. Any deviation from Y is a question for the maintainers, not a decision to make alone.

## 15. Writing for Members

Member-facing copy (help guides, changelog entries, notices, emails) is read by hobbyist makers, not engineers.

- Short sentences and plain words, no lead-ins ("Good to know:"). Prefer a short numbered list to a paragraph.
- Keep every fact and permission caveat ("guild leads only", "not confirmed until a lead approves") even while cutting length.
- Check every sentence against merged code before it ships. Can an ordinary active member do this today? Is there a second condition the sentence leaves out: a flag, a role, data that must exist first, a physical object someone has to make? Code review reads the diff, not the sentences.
- Help Center guides follow `docs/HELP_AUTHORING.md`.
