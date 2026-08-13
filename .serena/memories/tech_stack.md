# Tech Stack

- **Python**: 3.13
- **Framework**: Django 5.x + Django REST Framework
- **Package manager**: uv (`uv pip install -r requirements.txt`)
- **Testing**: pytest + pytest-describe (BDD), factory_boy (test data), respx (HTTP mocking), pytest-leela (mutation testing)
- **Linting/formatting**: ruff (`ruff check .` / `ruff format .`), line length 120
- **Type checking**: mypy
- **DB**: SQLite (local/dev), PostgreSQL (Render prod and Hetzner staging)
- **Frontend**: HTMX + Alpine.js; see `mem:core` for FRONTEND.md reference
- **Discord**: webhook-based notifications via `core/events/discord.py`
- **Google Calendar**: sync via `hub/calendar_service.py`
- **Airtable**: read-only pull, `airtable_sync` app
