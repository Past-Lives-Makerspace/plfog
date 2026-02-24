# plfog - Past Lives Makerspace

Django app for membership and studio rental management at Past Lives Makerspace (Portland, OR).

## Commands

- `pytest` - Run tests
- `python manage.py runserver` - Dev server
- `ruff check .` - Lint
- `ruff format .` - Format
- `mypy .` - Type check

## Testing

BDD/spec style with pytest-describe. Test files named `*_spec.py`. Functions named `it_*` inside `describe_*` blocks.

## Settings

All configuration via environment variables. See `plfog/settings.py` for available env vars.
