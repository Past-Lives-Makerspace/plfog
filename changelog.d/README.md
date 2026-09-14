# changelog.d — one fragment per change

**Every PR adds exactly one file here.** That is the whole release ritual. No PR edits
`plfog/version.py`, and no PR names a version number, so two PRs open at once cannot collide
and a rebase cannot leave a stale number behind.

CI fails a PR that adds no fragment. If a change genuinely has no release in it (a README
typo, a comment), add the `no-changelog` label instead.

## The file

Name it `<pr-number>-<short-slug>.toml`. The PR number makes the filename unique without
anyone having to think about it. If you do not have the number yet, use the branch slug and
rename later — nothing reads the filename except a same-day tie-break in display order.

```toml
# changelog.d/394-composer-drafts.toml
bump = "minor"
date = "2026-09-13"
title = "The class composer keeps what you typed"
changes = [
  "If the page reloads or you close the tab while writing a class, your next visit offers back what you had typed.",
  "Once a class saves, the kept copy is cleared.",
]
```

| key | required | meaning |
|---|---|---|
| `bump` | yes | `patch`, `minor` or `major`. See below. |
| `audience` | no (`members`) | `members` or `internal`. |
| `date` | for `members` | `YYYY-MM-DD`. Today's date when you write it; a day's drift on a slow PR is invisible to members and nothing computes off it but sort order. |
| `title` | for `members` | The Discord headline and the changelog heading. |
| `changes` | for `members` | The bullets. Plain member-facing language: no jargon, no PR numbers, no commit hashes. |
| `screenshot` | no | A feature-shot slug for the release email's card. |

## Picking a bump

- **`patch`** — a fix to something already live on production. Members lived with the bug, so
  it is member-facing and it gets bullets.
- **`minor`** — anything net-new members can see or do.
- **`major`** — a break in how the portal works. Twice in this repo's life. Sweep
  `changelog.d/` first (see below), so the number stays exact — two unswept majors are
  approximated, not rejected, because the fold runs at app import and refusing there
  would mean the app does not boot.

The version is the fold of every fragment here over `changelog/base.json`, computed in
`plfog/changelog.py`. Nothing writes it down, which is why nothing can get it wrong.

## `audience = "internal"`

A tooling or test PR still moves the version, and members should hear nothing about it:

```toml
bump = "patch"
audience = "internal"
```

That is the whole file. It needs no title and no bullets, because inventing prose for a
release that gets published nowhere is how entries end up stamped at versions Discord never
posts.

## Refining a fragment

**A fragment is announced the moment its own PR merges.** That is the fact everything below
hangs on: `release.yml` announces whatever a push *added*, so the window in which editing a
fragment changes what members hear is the window before your PR merges — not "before the next
sweep".

**Inside your own PR, edit the fragment rather than adding a second one.** A reviewer asks for
another bullet, you fix something on the branch: update the fragment that is already there and
the combined result goes out once, when the PR merges.

**After your PR merges, that fragment is spent.** Editing it corrects the in-app changelog and
announces nothing at all — not the edit, not the original again. So a polish fix to a feature
that shipped last week needs its own fragment if members should hear about it, and needs
nothing if they should not. Do not fold a real new bullet into a merged fragment expecting
members to see it; they will not.

A fix to something already live on production is its own `bump = "patch"` fragment — members
lived with the bug, so it is news.

## Sweeping

`changelog.d/` grows until someone sweeps it: move the entries into `changelog/history.json`,
set `changelog/base.json` to the current `VERSION`, and delete the fragments. That is the new
"start a fresh release line", and it is a deliberate housekeeping PR — usually right after the
release email goes out, since the email offers exactly the unswept fragments as its cards.

Nothing breaks if you never sweep. It is tidiness, plus what keeps a second major's number
exact.
