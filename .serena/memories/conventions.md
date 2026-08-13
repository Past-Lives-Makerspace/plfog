# Conventions

## Architecture
- **Fat models, skinny views**: all business logic in model methods/managers; views only parse request + call model + return response.
- Validation lives in Django Forms, not views or serializers.
- Complex querysets go in Managers; DRF serializers are for API serialization only.
- Cross-model orchestration goes in `services.py`.

## Model patterns
- `help_text` on every field. `TextChoices` for all choice fields.
- `default=dict` (never `default={}`). Meaningful `__str__` on every model.
- `@property` for cheap derived data; methods for side effects or expensive ops.
- Soft delete: `deleted_at` field + `ActiveManager`/`all_objects` pattern.
- Avoid N+1: `select_related` / `prefetch_related` in querysets.

## Type hints
- All functions typed, including `-> None`.
- `from __future__ import annotations` at top of every file.
- Annotation-only imports under `TYPE_CHECKING`.
- Line length: 120.

## Tests (BDD with pytest-describe)
- Files: `*_spec.py` in `spec/` subdirectory per app.
- Structure: `describe_*` blocks nesting `it_*` functions.
- **CRITICAL**: `context_*` is NOT a collected prefix — silently skipped. Use `describe_*` for every nested block including conditional ones.
- factory_boy for all test data; `respx` for HTTP mocking.
- Never mock models or DB; only mock external services.
- 100% branch coverage required; 100% mutation kill rate.
- No `@pytest.mark.skip`, `# pragma: no cover`, or `# pragma: no mutate` without explicit approval.
