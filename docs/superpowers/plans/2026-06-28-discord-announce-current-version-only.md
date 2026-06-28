# Discord release announce: post only the shipped version

**Commit 5 of release 0.20.x.** Tooling/infra change — **not member-facing**, so no CHANGELOG bullet. Touches
`.github/scripts/discord_release_notify.py`, `.github/workflows/discord-notify.yml`, `CLAUDE.md`, and two memory files.

## The problem

`discord_release_notify.py._release_entries()` returns **every** CHANGELOG entry sharing the current `MAJOR.MINOR`:

```python
def _release_entries() -> list[dict[str, object]]:
    minor = ".".join(VERSION.split(".")[:2])
    entries = [e for e in CHANGELOG if ".".join(str(e["version"]).split(".")[:2]) == minor]
    return entries or [CHANGELOG[0]]
```

So announcing re-posts the whole release line. With features now shipping incrementally (one merge → one deploy → one
announce, e.g. via `/fog-quick-feature`), members get spammed with everything in `0.19.x` / `0.20.x` every time.

## The fix

Post only the entry/entries stamped at the exact current `VERSION`:

```python
def _release_entries() -> list[dict[str, object]]:
    """Only the CHANGELOG entry/entries for the version being announced (what just went live)."""
    entries = [e for e in CHANGELOG if str(e["version"]) == VERSION]
    return entries or [CHANGELOG[0]]
```

Everything downstream (title from `entries[0]`, bullet flattening, chunking, fail-loud `_post`) is unchanged. Returning a
*list* preserves the "two features at the same VERSION both post" case. This is also why the 0.20.x batch keeps all its
member-facing bullets under the single `0.20.1` entry (overview doc) — they announce together, once.

## Docs that must move in lockstep (or they contradict the code)

- `discord_release_notify.py` module docstring + the function docstring (drop "aggregates every entry in the release
  line"; describe "only the current VERSION").
- `.github/workflows/discord-notify.yml:26-27` comment ("Aggregates every changelog entry in the current release line…")
  → "Posts only the current VERSION's changelog entry."
- Root `CLAUDE.md` — the *Versioning & Changelog* and *Discord Notifications* sections that state it aggregates the whole
  `MAJOR.MINOR`. Update to: posts only `version == VERSION`; the curation rule ("group by feature, fold refinements")
  still holds and now also means *stamp everything you want announced together at the same VERSION*.
- Memories `feedback_version_bump` and `project_release_branch_versioning` (record the new announce behavior). *(These are
  in `~/.claude/projects/.../memory/` — update them as part of this commit's "docs" step; they're not in the repo, so they
  don't affect lint/tests but keep them honest.)*

## Tests

- `discord_release_notify` already has tests (find `tests/**/discord*`); update/add: with a CHANGELOG containing multiple
  versions, `_release_entries()` returns only those whose `version == VERSION`; the multi-entry-same-version case returns
  all of them; the empty fallback still returns `[CHANGELOG[0]]`. Keep the existing chunking / fail-loud tests green.
- BDD `*_spec.py`, `describe_`/`it_`.

## Out of scope

- Adding a `workflow_dispatch` "announce a specific past version" input (possible later enhancement; not now — YAGNI).
- Re-announcing anything already posted; this only changes future runs.
