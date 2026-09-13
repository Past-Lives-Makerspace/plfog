"""App version and changelog.

Both names are computed at import from files outside this module, so that a PR never has to
edit a shared file to ship a release:

- ``changelog/base.json`` — the version the fold starts from. A sweep moves it; a feature PR
  never touches it.
- ``changelog.d/*.toml`` — one fragment per unreleased change, each declaring ``patch``,
  ``minor`` or ``major``. A PR adds exactly one, under a filename nothing else claims.
- ``changelog/history.json`` — the 258 releases frozen at v1.62.1, read-only.

``plfog.changelog`` holds the machinery and the reasoning, including why the version is folded
rather than written and why new entries carry no version number of their own. Read that before
changing anything here.

Everything imports ``VERSION`` and ``CHANGELOG`` from this module, and both keep exactly the
shapes they had when they were literals: a ``MAJOR.MINOR.PATCH`` string and a newest-first
list of ``{version?, date, title, changes, screenshot?}`` dicts.
"""

from __future__ import annotations

import pathlib
from typing import Any

from plfog.changelog import compose_changelog, fold_version, load_base, load_fragments, load_history

#: Repository root — ``plfog/version.py`` sits one directory down from it. Resolved from
#: ``__file__`` rather than the working directory so a management command, pytest and the
#: gunicorn worker all read the same files no matter where they were invoked from.
_ROOT = pathlib.Path(__file__).resolve().parents[1]

BASE_PATH = _ROOT / "changelog" / "base.json"
HISTORY_PATH = _ROOT / "changelog" / "history.json"
FRAGMENTS_PATH = _ROOT / "changelog.d"

_FRAGMENTS = load_fragments(FRAGMENTS_PATH)

#: The version the current batch folds forward from — the last sweep. Everything shipped since
#: is a fragment, which is what ``core.release_email.current_release_entries`` offers as the
#: release email's scope, so this is the number that labels it.
BASE_VERSION: str = load_base(BASE_PATH)

VERSION: str = fold_version(BASE_VERSION, _FRAGMENTS)

CHANGELOG: list[dict[str, Any]] = compose_changelog(load_history(HISTORY_PATH), _FRAGMENTS)
