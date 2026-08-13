# Suggested Commands

| Purpose | Command |
|---------|---------|
| Run tests | `pytest` |
| Dev server | `python manage.py runserver` |
| Lint | `ruff check .` |
| Format | `ruff format .` |
| Lint + autofix | `ruff format . && ruff check --fix .` |
| Type check | `mypy .` |
| Install deps | `uv pip install -r requirements.txt` |

Test files: `*_spec.py` inside a `spec/` subdirectory per app. `pytest` collects them automatically per `pyproject.toml` config.
