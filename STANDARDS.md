# plfog Standards

How code in this repo is built and tested, and the traps that have cost real time, for every contributor, human or agent. Templates and CSS: `FRONTEND.md`. Domain language: `CONTEXT.md`. Help Center guides: `docs/HELP_AUTHORING.md`. Issues and pull requests: `CONTRIBUTING.md`. Tool settings live in `pyproject.toml` and the pre-push hook enforces ruff and mypy; this file carries what no config says.

## 1. Principles

- **Fat models, skinny views.** Business logic lives in models and managers; views only parse the request, call a model method and respond.
- **Fail loudly.** Raise on unexpected values: `dict[key]`, not `dict.get(key, default)`. Silent fallbacks hide bugs for weeks.
- **Explicit over implicit.** Configuration comes from clearly named environment variables (`plfog/settings.py`), with no magic defaults.
- **Type everything.** Every function is fully annotated, including `-> None`.

## 2. Where Logic Lives

| Concern | Home |
|---|---|
| HTTP in and out | View: parse the request, call a model method, return a response |
| Input validation | Django form (DRF serializers only serialize) |
| One object's behaviour | Model method or property (`membership.renew()`, `membership.is_active`) |
| Filtering, aggregation across rows | Manager or queryset (`Membership.objects.active()`) |
| Orchestration across models | Service module (`services.py`) |

A view that reads `request.data`, checks dates, sets fields and sends mail is wrong; the same flow is a form's `clean_*`, a model's `renew()` and a three-line view. Prefer model methods to signals; use a signal only when the sender must not know about the receiver.

## 3. Models

- `TextChoices` for every choice field, `help_text` on every field, `default=dict` (never `{}`), and a meaningful `__str__`.
- `UniqueConstraint`, not `unique_together`. Partial indexes (`condition=Q(...)`) for filtered queries. Index and constraint names are capped at 30 characters (`models.E034`).
- `@property` for cheap derived data; a method for anything expensive or with side effects.
- Avoid N+1 queries: `select_related` / `prefetch_related` wherever a loop touches a relation.
- Soft delete, where a model has it, is a `deleted_at` field with an `objects` manager that filters it out and an `all_objects` manager that does not.
- **Migrations:** one per logical change; hand-edit one only with its dependency graph in view. A data migration must include a real reverse function; `RunPython.noop` as the reverse needs explicit approval.

## 4. Errors

Re-raise `DoesNotExist` as a domain error in model and service code (`raise ValueError(f"No record for '{email}'")`), and as a 404 in views. Prefer a named domain exception (`class InsufficientStockError(Exception)`) to a generic one.

## 5. Permissions

Access is decided by `Member.fog_role` (the tier, a `FogRole` choice) and `Member.has_admin_capability(...)` (scoped admin grants such as approving classes or issuing refunds, backed by `AdminCapability`). Views enforce them with the decorators in `hub/view_as.py` (`fog_admin_required`, `classes_review_access_required`, `refund_authority_required` and siblings). Every state-changing view carries one, and every access check goes through a decorator or `has_admin_capability`.

## 6. Types and Docstrings

Annotation-only imports go under `if TYPE_CHECKING:`; a runtime import that would be circular goes inside the function. Anything non-obvious gets a Google-style docstring (`Args:`, `Returns:`, `Raises:`).

## 7. Tests

- Specs use pytest-describe: `describe_*` blocks nesting `it_*` functions, in `*_spec.py` files under `tests/<app>/` (older ones sit in an app's `spec/` folder).
- **Only `describe_*` nests.** `context_*` is not a collected prefix, so an `it_*` inside a `context_*` block silently never runs. Use `describe_when_...` for conditions.
- factory-boy for all test data, `respx` for HTTP mocking. Mock only external services; specs run against real models and the database. Shared fixtures go in `conftest.py`, scoped ones in the describe block.
- 100% branch coverage and a 100% mutation kill rate (pytest-leela) on new code. A skip or a `pragma: no cover` / `no mutate` needs a maintainer's explicit approval.

## 8. Testing Traps

- **Judge a run by pytest's own result.** `pytest ... | tail` returns `tail`'s exit code, so a failing run reads as success. Redirect to a file and check `$?`, or read the `N passed, M failed` line, and commit only after that.
- **The changelog renders on every page.** `core/context_processors.py` puts the whole changelog into every template, hub and public classes pages alike. A negative assertion on UI copy fails the day a changelog entry uses that phrase, and a positive one can pass before the feature renders. Anchor assertions on markup (a class, an id, a URL) or on factory strings no changelog could contain.
- **Coverage.** The CI gate is `fail_under = 98`. A full local run can pass every test and still exit 1 on the total (about 93%) because some markers are deselected locally; CI's `test` job is the authority, so locally read the table for the files you changed.
- **The mutation gate can be silently off.** pytest-leela skips mutation when the session exits non-zero, with no message, and CI runs that step with `continue-on-error: true`. No mutation report is not zero survivors. On a local subset, pass `--cov-fail-under=0` and make every selected spec pass.
- **Browser-writing e2e specs flake on SQLite** (`database table is locked`). CI runs e2e on PostgreSQL (`.github/workflows/playwright.yml`); reproduce there before suspecting the product: `DATABASE_URL=postgres://... pytest -m e2e --no-cov -o addopts="" tests/e2e/<file>.py`.
- **pytest does not run Django system checks; CI does.** Run `python manage.py check` after any model or migration change.

## 9. Pull Requests

- Issues and PRs follow `CONTRIBUTING.md`: a 160-character summary, the four-part description, at most 300 words, pictures for visible changes. Every PR adds one `changelog.d/` fragment or carries the `no-changelog` label (`changelog.d/README.md`).
- A PR in a conflicting state gets no CI runs for new pushes, and nothing says so. Resolve the conflict and the next push runs CI.
- After rebasing onto `main`, check migration numbering (`python manage.py makemigrations --check`); renumber past new arrivals and repoint `dependencies`.

## 10. Deploys

- Production runs on Render from `main`, and the Dockerfile `CMD` is the deploy: `migrate --noinput && seed_notification_templates --quiet && gunicorn`. Migrations apply while the previous release is still serving, so a migration must work with the old code: add before you remove, and prefer repairing data in a code path over a data migration that assumes the new code.
- `render.yaml` is not linked to the Render service; a variable declared there never reaches production.
- Deploys do not run `seed_help_center` or `seed_example_guild`. Changes to `membership/help_content.py` or `membership/example_guild.py` reach production only when someone runs those commands against it.

## 11. Domain Traps

- **Class review is sequential.** `ClassOffering.submit_for_review` opens only the first gate: the Guild Lead when the category's guild has a lead, otherwise Admin. A pending guilded class has a `GUILD_LEAD` `ClassApproval` row and no `ADMIN` row until it escalates, and production holds rows in that shape. A design that assumes both rows exist strands them in no reviewer's queue.
- **Two push channels.** Web push (VAPID; `PushSubscription`, `core/push.py`) reaches browsers and PWAs but never the Android app, whose WebView has no Push API. The app uses FCM (`core/fcm.py`, `FcmDevice`, `static/js/native-push.js`). `PushAdapter` in `core/events/channels.py` fans out to both. The Capacitor plugin ships in the app binary, so plugin changes need an app release (`mobile/README.md`).
- **Discord differs from its docs** (each verified in production):
  - A modal (type 9) must go through the REST callback `POST /interactions/{id}/{token}/callback`; an inline one is discarded and the member sees "didn't respond in time" (`_route_modal`, `core/events/discord_commands.py`).
  - A Label (type 18) description is capped at 100 characters, not 200; over it the whole modal is rejected (`modal_label()`, `core/events/discord_interactions.py`).
  - Poll messages can never be edited (520003), and a type 6 ack on a poll makes follow-ups fail silently; ack poll-adjacent clicks with an ephemeral type 5.
  - Server admins bypass command-permission denials; test as a non-admin.
- **Demo and example content.** `display_demo_classes` hides `demo-` slugs through two gates, `ClassOfferingQuerySet.public()` and `ClassSessionQuerySet.upcoming_public()` (the Discord digest reads the second); new public surfaces must use one. Demo categories are prefixed `[DEMO]` because `Category.name` is unique. The example guild stays free of `CommunityEvent` rows, because those reach the public calendar and Discord regardless of `is_active` (`membership/example_guild.py`).

## 12. Building Features

**Parity means the same surfaces.** "Make X work like Y" means reuse Y's screens, components and data shape by name, not a new design in Y's vocabulary. Any deviation from Y is a question for the maintainers.

## 13. Writing for Members

Member-facing copy (help guides, changelog entries, notices, emails) is read by hobbyist makers, not engineers. Short sentences, plain words, no lead-ins; a short numbered list beats a paragraph. Keep every fact and permission caveat ("guild leads only") while cutting. Check every sentence against merged code before it ships: can an ordinary active member do this today, or is there a second condition (a flag, a role, data that must exist, an object someone has to make) the sentence leaves out? Help guides follow `docs/HELP_AUTHORING.md`.
