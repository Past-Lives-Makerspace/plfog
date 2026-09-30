"""Sweep ``changelog.d/`` into ``changelog/history.json`` and move ``changelog/base.json``.

The sweep is the housekeeping step ``changelog.d/README.md`` describes: every fragment that
has shipped is frozen into history under the version it shipped as, ``base.json`` moves to
the version the fold reaches today, and the fragments are deleted. Nothing about the version
changes; a sweep is exactly neutral, which is what lets a ``major`` fragment added right after
it fold to an exact ``X.0.0`` rather than ``X.<minors so far>.<patches so far>``.

**Where a swept entry's version comes from.** A fragment carries no version of its own (see
``plfog/changelog.py``), and ``core.release_email.current_release_entries`` treats a history
entry without one as still unswept, so every frozen entry needs the number it shipped under.
The tree does not record merge order, but ``git`` does: the first-parent commit on ``main``
that added the fragment is the push that shipped it, and folding the fragments in that
commit's tree over that commit's ``base.json`` is the version ``release.yml`` tagged. That is
computed here per fragment, so a sweep never guesses a number.

Run it from a checkout whose ``changelog.d/`` matches the ``main`` you are sweeping (rebase
first), and check the plan before writing::

    .venv/bin/python scripts/sweep_changelog.py            # print the plan, touch nothing
    .venv/bin/python scripts/sweep_changelog.py --write    # apply it

Stdlib plus ``plfog.changelog``; no Django.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from typing import Any

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from plfog.changelog import fold_version, load_base, load_fragments, load_history, parse_fragment, parse_version  # noqa: E402
from plfog.version import BASE_PATH, FRAGMENTS_PATH, HISTORY_PATH  # noqa: E402

#: The pathspec every git question is about. A fragment is identified by this relative path
#: whatever directory ``FRAGMENTS_PATH`` resolves to, because it is what ``git log`` prints.
_PATHSPEC = "changelog.d/*.toml"


def _git(*args: str) -> str:
    """Run git in the repo root and return stdout, exiting loudly on any failure.

    A sweep that could not read history would freeze entries under invented numbers, so there
    is no fallback: git declining to answer ends the run.
    """
    completed = subprocess.run(["git", *args], capture_output=True, text=True, check=False, cwd=_REPO_ROOT)
    if completed.returncode:
        sys.exit(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout


def adding_commits(ref: str) -> dict[str, str]:
    """Each fragment's path mapped to the first-parent commit on ``ref`` that added it.

    Walks newest first, so a fragment deleted and added again resolves to its latest arrival,
    which is the push that shipped what the file says now.
    """
    out = _git("log", "--first-parent", "--diff-filter=A", "--name-only", "--format=%H", ref, "--", _PATHSPEC)
    adders: dict[str, str] = {}
    commit = ""
    for line in out.splitlines():
        if not line.strip():
            continue
        if "/" in line:
            adders.setdefault(line, commit)
        else:
            commit = line
    return adders


def version_at(commit: str) -> str:
    """The version the tree at ``commit`` folds to: what that push tagged."""
    base = json.loads(_git("show", f"{commit}:changelog/base.json"))["version"]
    names = [name for name in _git("ls-tree", "--name-only", commit, "changelog.d/").split() if name.endswith(".toml")]
    fragments = [parse_fragment(_git("show", f"{commit}:{name}"), pathlib.Path(name)) for name in names]
    return fold_version(base, fragments)


def plan(ref: str) -> tuple[list[dict[str, Any]], str]:
    """The entries to freeze, newest first, and the version ``base.json`` moves to.

    ``internal`` fragments moved the version and are not news, so they are deleted without an
    entry, exactly as ``compose_changelog`` already leaves them out.
    """
    fragments = load_fragments(FRAGMENTS_PATH)
    if not fragments:
        sys.exit("Nothing to sweep: changelog.d/ holds no fragments.")
    adders = adding_commits(ref)
    entries: list[dict[str, Any]] = []
    for fragment in fragments:
        rel = f"changelog.d/{fragment.path.name}"
        if rel not in adders:
            sys.exit(f"{rel} was never added on {ref}. Rebase onto it, or drop the fragment, before sweeping.")
        if fragment.is_member_facing:
            entries.append({"version": version_at(adders[rel]), **fragment.as_entry()})
    entries.sort(key=lambda entry: (parse_version(entry["version"]), entry["date"]), reverse=True)
    return entries, fold_version(load_base(BASE_PATH), fragments)


def sweep(ref: str, *, write: bool) -> None:
    """Print the plan for ``ref`` and, with ``write``, apply it to the three files."""
    entries, new_base = plan(ref)
    for entry in entries:
        print(f"  v{entry['version']:<10} {entry['date']}  {entry['title']}")
    print(f"{len(entries)} entries to freeze; base.json {load_base(BASE_PATH)} -> {new_base}")
    if not write:
        print("Plan only. Re-run with --write to apply.")
        return
    history = entries + load_history(HISTORY_PATH)
    HISTORY_PATH.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    BASE_PATH.write_text(json.dumps({"version": new_base}, indent=2) + "\n", encoding="utf-8")
    for path in FRAGMENTS_PATH.glob("*.toml"):
        path.unlink()
    print("Swept.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Freeze changelog.d/ into changelog/history.json.")
    parser.add_argument("--ref", default="origin/main", help="The branch whose history says what shipped.")
    parser.add_argument("--write", action="store_true", help="Apply the sweep. Without it, only print the plan.")
    args = parser.parse_args(argv)
    sweep(args.ref, write=args.write)


if __name__ == "__main__":
    main()
