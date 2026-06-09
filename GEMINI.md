# PLFOG Project Instructions (Gemini)

This file contains team-shared conventions, architecture, and repo guidance for the PLFOG project.

## Core Mandates

- **Fat Models, Skinny Views**: All business logic lives in models and managers. Views should only handle HTTP request/response.
- **Fail Loudly**: Raise exceptions on unexpected values.
- **Explicit over Implicit**: Use environment variables with clear names; no magic defaults.
- **Type Everything**: All functions must have full type annotations, including `-> None`.
- **Test Everything**: 100% coverage target. Use BDD-style tests with `pytest-describe`.
- **Versioning**: Bump version in `plfog/version.py` and add a member-friendly changelog entry for every PR.

## Architecture

| Layer | Responsibility |
|-------|----------------|
| **Views/ViewSets** | HTTP request/response only. |
| **Forms** | Input validation and cleaning. |
| **Models** | Business logic, calculations, state changes. |
| **Managers** | Complex querysets, filtering, aggregations. |
| **Services** | Cross-model orchestration (use sparingly). |

## Coding Standards

- **Line Length**: 120
- **Python Version**: 3.13
- **Ruff**: Use for formatting and linting (`ruff format .`, `ruff check --fix .`).
- **MyPy**: Use for type checking (`mypy .`).
- **Models**:
    - Use `TextChoices` for choice fields.
    - `help_text` on every field.
    - `default=dict` for JSONFields, never `default={}`.
    - Meaningful `__str__` on every model.
    - Use `UniqueConstraint` over `unique_together`.
- **Soft Delete**: Implement using `deleted_at` timestamp and custom managers.
- **N+1 Queries**: Always use `select_related` and `prefetch_related`.

## Testing Convention

- **Framework**: `pytest` with `pytest-describe`.
- **Naming**: Test files named `*_spec.py` inside `spec/` directories.
- **Structure**: Use `describe_*`, `context_*`, and `it_*` blocks.
- **Data**: Use `factory-boy` factories (usually in `factories.py`).
- **Mocks**: Use `respx` for HTTP mocking. Never mock models or the database.

## External Integrations

- **Stripe**: Billing and payments (Connect + PaymentIntents).
- **Airtable**: Bidirectional sync for members, spaces, leases, and votes.
- **Allauth**: Authentication infrastructure.
- **Web Push**: Notifications via VAPID.

## Release & Deployment

- **Production**: Render.com (PostgreSQL).
- **QA/Staging**: Hetzner VPS (PostgreSQL).
- **Local**: SQLite (default) or PostgreSQL.
- **Changelog**: Written in plain, member-friendly language. Discord notifications sent automatically on merge to `main`.
