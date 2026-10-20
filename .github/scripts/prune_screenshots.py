"""Remove PR screenshots from ``main`` once nobody has touched them for a month.

``mockups/screenshots/`` grew about 90 MB in its first month (2026-09-19 to 2026-10-20), and
every checkout and worktree carries all of it. A screenshot's job ends when its PR merges: the
description embeds it by commit (CONTRIBUTING.md), and that link keeps working after the file
leaves ``main``, so pruning breaks nothing. It does not shrink the repo's history; only a
rewrite would, and a rewrite would break those commit links.

Age is the last commit that touched the file, not the filesystem mtime, which a fresh checkout
resets. The README stays.

``prune-screenshots.yml`` runs this monthly with ``--apply`` and opens the result as a PR.
Stdlib only, like the other scripts here.
"""

from __future__ import annotations

import argparse
import datetime
import os
import pathlib
import subprocess
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FOLDER = "mockups/screenshots"
_KEEP = {f"{_FOLDER}/README.md"}
_DAY = 86400


def _git(*args: str) -> str:
    completed = subprocess.run(["git", *args], capture_output=True, text=True, check=False, cwd=_REPO_ROOT)
    if completed.returncode:
        sys.exit(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout


def last_touched(log: str) -> dict[str, int]:
    """Each path's newest commit time, from ``git log --format=%x00%ct --name-only`` output.

    The log runs newest first, so the first time a path appears is its last touch.
    """
    touched: dict[str, int] = {}
    when = 0
    for line in log.splitlines():
        if line.startswith("\0"):
            when = int(line[1:])
        elif line.strip() and line not in touched:
            touched[line] = when
    return touched


def stale(tracked: list[str], touched: dict[str, int], now: int, days: int) -> list[str]:
    """Tracked screenshots last touched more than ``days`` ago, the README excepted."""
    cutoff = now - days * _DAY
    return sorted(path for path in tracked if path not in _KEEP and touched.get(path, now) < cutoff)


def _size(paths: list[str]) -> int:
    return sum((_REPO_ROOT / path).stat().st_size for path in paths)


def pr_body(count: int, megabytes: float, days: int) -> str:
    return f"""**Summary:** Monthly cleanup: removes {count} PR screenshots older than {days} days from main ({megabytes:.1f} MB).

**Area:** ops

### Problem
PR screenshots pile up in every checkout; each one is done once its PR merges.

### Solution
- Removes screenshots under `{_FOLDER}/` whose last commit is more than {days} days old.
- PR descriptions link screenshots by commit, so those images keep showing.

### Impact / Risks
None. Nothing in the app or docs reads this folder, and git history keeps every file.

### Verification
`python .github/scripts/prune_screenshots.py --days {days}` lists the same {count} files.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--apply", action="store_true", help="git rm the files and write the fragment")
    args = parser.parse_args(argv)

    tracked = _git("ls-files", "--", _FOLDER).split("\n")
    touched = last_touched(_git("log", "--format=%x00%ct", "--name-only", "--", _FOLDER))
    now = int(datetime.datetime.now(datetime.UTC).timestamp())
    paths = stale([path for path in tracked if path], touched, now, args.days)
    megabytes = _size(paths) / 1048576
    print(f"{len(paths)} screenshots older than {args.days} days, {megabytes:.1f} MB")

    if args.apply and paths:
        _git("rm", "--quiet", "--", *paths)
        today = datetime.date.today().isoformat()
        (_REPO_ROOT / "changelog.d" / f"prune-screenshots-{today}.toml").write_text(
            'bump = "patch"\naudience = "internal"\n'
        )
        body_file = os.environ.get("PR_BODY_FILE")
        if body_file:
            pathlib.Path(body_file).write_text(pr_body(len(paths), megabytes, args.days))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as handle:
            handle.write(f"count={len(paths)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
