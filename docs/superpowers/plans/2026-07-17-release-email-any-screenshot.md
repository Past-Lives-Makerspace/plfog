# Release-email composer: attach any captured screenshot (PR-free) — Spec & Implementation Plan

**Status:** Spec only — approved to build (v23).
**Date:** 2026-07-17
**Surface:** FOG hub admin — Site Settings → Announcements → "Draft from latest release" composer (`templates/hub/admin/_release_announcement.html`).
**Related:** `2026-07-04-release-email-redesign.md`; memory `project_release_email_redesign`, `project_release_email_v20v21_send`.

---

## 1. Summary

Today the release-email composer's per-card **screenshot** dropdown only offers the seven curated `FEATURE_PAGES` slugs, so attaching a **new** release screenshot (e.g. `guild-pages`, `qr-codes`, `profile-settings`) requires a code change + PR to register it. This change makes the dropdown offer **every screenshot actually captured to R2** under `email/features/*.png`. Once a screenshot is captured (by the existing capture harness or a one-off job), an admin can attach it from the composer with **no code and no PR** — which is the stated goal: release-email sends become fully PR-free going forward.

### Locked decisions (from user)

| Decision | Choice |
|---|---|
| Source of the dropdown options | **Whatever is actually in R2** — enumerate `default_storage.listdir("email/features")`, not the static registry. |
| Broken-image guard | **Kept** — only real, existing assets are offered (listing R2 *is* the existence check). |
| Ordering & labels | Curated `FEATURE_PAGES` first (registry order, friendly labels), then any other captured slug alphabetically, labelled from a humanized slug. |
| Curated registry | **Stays** — still drives the changelog `screenshot` defaults and the card **title link** (`feature_page_url`). Uncurated slugs render image-only (no title link), which is already how bespoke shots (`guild-pages`, `qr-codes`) render. |

## 2. What already exists (reuse, don't reinvent)

Confirmed on `origin/main` (v23 base).

| Need | Existing thing | Location |
|---|---|---|
| The function to change (registry-only today) | `feature_shot_choices()` | `core/release_email.py:136-148` |
| Storage key + prefix | `feature_shot_key()`, `_FEATURE_SHOT_PREFIX = "email/features"` (no trailing slash) | `core/release_email.py:40,111-113` |
| Existence-guarded URL (render-time guard, unchanged) | `resolve_feature_shot_url()` | `core/release_email.py:116-127` |
| Registry (kept — labels + title-link source) | `FEATURE_PAGES`, `_PAGES_BY_SLUG`, `feature_page_url()` | `core/release_email.py:70-92,130-133` |
| Sole caller — builds the per-card `<select>` choices once in `__init__` | `ReleaseAnnouncementForm` | `hub/forms.py:1837,1846,1861-1864` |
| Composer template rendering the select | `{% include "components/form_field.html" with field=row.screenshot %}` | `templates/hub/admin/_release_announcement.html:50` |
| Read path (changelog slug → card `screenshot_url`), unchanged | `build_release_cards()` | `core/release_email.py:195-221` |
| Storage backend: S3 (R2) in prod, FileSystem in dev/CI — both implement `listdir → (dirs, files)` with **basename** files | `STORAGES` | `plfog/settings.py:343-369` |

### Genuine gaps to close

1. Rewrite `feature_shot_choices()` to enumerate R2 instead of the registry.
2. Give the **two test storage doubles** a `listdir()` method (neither has one today).

## 3. Where the code lives

```
core/release_email.py                       # feature_shot_choices() rewrite (+ a small _humanize helper)
tests/core/release_email_spec.py            # _FakeStorage gains listdir(); update describe_feature_shot_choices
tests/hub/release_announcement_spec.py      # _Storage gains listdir(); verify the two choice assertions
```

No form, template, model, or migration changes — the form already calls `feature_shot_choices()` and renders the resulting `ChoiceField`.

## 4. Data model

None. This is a pure read-side change over object storage.

## 5. Business logic

Rewrite `feature_shot_choices()` (`core/release_email.py`):

```python
def _humanize_slug(slug: str) -> str:
    """A readable label for a captured slug with no registry entry ('guild-pages' -> 'Guild pages')."""
    return slug.replace("-", " ").replace("_", " ").strip().capitalize()


def captured_feature_slugs() -> list[str]:
    """Every screenshot slug present in storage under email/features/ (one LIST call).

    Returns the '<slug>' of each '<slug>.png'. Empty when nothing has been captured yet —
    FileSystemStorage raises FileNotFoundError on a missing dir (S3 does not), so we guard it.
    """
    try:
        _dirs, files = default_storage.listdir(_FEATURE_SHOT_PREFIX)
    except FileNotFoundError:
        return []
    # Take the basename before stripping .png — FileSystemStorage returns basenames, and this
    # stays correct even if a backend ever hands back full keys ("email/features/home.png").
    names = (name.rsplit("/", 1)[-1] for name in files)
    return sorted(name[:-4] for name in names if name.endswith(".png"))


def feature_shot_choices() -> list[tuple[str, str]]:
    """(value, label) options for the composer's per-card screenshot <select>.

    Offers EVERY screenshot actually captured to storage (listing R2 is the broken-image
    guard), plus a leading 'No screenshot'. Curated FEATURE_PAGES come first in registry
    order with their friendly labels; any other captured slug follows, alphabetically,
    labelled from its slug. A brand-new screenshot needs no code change to appear here.
    """
    captured = set(captured_feature_slugs())
    choices: list[tuple[str, str]] = [("", "No screenshot")]
    seen: set[str] = set()
    for page in FEATURE_PAGES:               # curated first, registry order + labels
        if page.slug in captured:
            choices.append((page.slug, page.label))
            seen.add(page.slug)
    for slug in sorted(captured - seen):     # bespoke/new shots, alphabetical
        choices.append((slug, _humanize_slug(slug)))
    return choices
```

- `resolve_feature_shot_url()`, `feature_page_url()`, `build_release_cards()` are **unchanged**. The card render still guards the image via `resolve_feature_shot_url()`, and uncurated slugs still resolve `feature_page_url("") == ""` → image-only card (already handled by `_feature_card.html`/`_screenshot.html`).
- Every changelog `screenshot` default slug that has an asset is, by construction, in the listdir set — so the form's `initial_slug` preselection stays a valid `ChoiceField` choice.

## 6. UI / UX

No new screen; the existing composer's per-card **screenshot `<select>`** simply lists more options.

- **Screen:** `templates/hub/admin/_release_announcement.html:38-54` (per-card row), select at line 50 via `form_field.html`.
- **Behavior change:** the dropdown now shows every captured screenshot (curated pages by friendly name; bespoke shots like "Guild pages", "Qr codes" by humanized slug), plus "No screenshot" at top and the changelog default preselected when it has an asset.
- **Save/feedback:** unchanged — the composer's existing **Preview** (assembled-email iframe, `:97-101`) and **Send test to myself** / **Send** actions. Note (existing behavior, not a regression): the swapped screenshot shows in the **Preview iframe**, not live in the select — call this out so no one specs a live thumbnail.
- **States:**
  - *Empty (nothing captured):* dropdown shows only "No screenshot" (the `FileNotFoundError`/empty-list guard prevents a 500 on an unseeded env). Cards render text-only.
  - *Error / success:* unchanged from the existing composer.
- **Dark + light / mobile:** unchanged — same `form_field.html` select in the same admin composer; no new markup or styles.

## 7. Notifications / emails

None new. This only changes which screenshots an admin can attach; the release email itself, its `emit()` send, and its `.txt`/`.html` rendering are unchanged.

## 8. Build order

1. Rewrite `feature_shot_choices()` + add `captured_feature_slugs()` / `_humanize_slug()`; update the two test storage doubles with `listdir()`; update `describe_feature_shot_choices`. Green (full suite + lint + mypy).
2. **No version bump / no changelog entry of its own** — this is admin-tooling plumbing (invisible to members); it folds into the v23 PR whose member-facing entry is `/join-guild`. (Per CLAUDE.md: internal/admin-workflow changes get no changelog entry.)

## 9. Testing

- **`captured_feature_slugs()`:** returns sorted slugs of `*.png` under the prefix; ignores non-`.png`; returns `[]` when the prefix is missing (`FileNotFoundError` guard) — assert no raise.
- **`feature_shot_choices()`:**
  - Registry slug present in storage → offered with its **friendly label**, before extras.
  - A **bespoke** captured slug not in the registry (e.g. `guild-pages`) → offered with a **humanized** label, after the curated ones.
  - A registry slug with **no** asset → **not** offered (guard holds).
  - Always leads with `("", "No screenshot")`; no duplicates when a slug is both registry and captured.
- **Test doubles:** `_FakeStorage` (`tests/core/release_email_spec.py:51-73`) and `_Storage` (`tests/hub/release_announcement_spec.py:37-47`) each gain `listdir(prefix)` returning `(dirs, files)` where **`files` are basenames** (`home.png`, not `email/features/home.png`) — mirroring the real S3/FileSystem backends' contract — derived from their existing key set. Re-verify `release_announcement_spec.py:82-92` (the "only captured screenshots" + default-selected assertions) still hold.

## 10. Open / deferred

- **A composer "capture now" button** (trigger the screenshot harness from the UI) — deferred; capture stays a harness/one-off-job step. This spec only removes the *registry* gate, which is the PR trigger.
- **Title links for bespoke slugs** — deferred; uncurated shots stay image-only (consistent with today). Adding a slug to `FEATURE_PAGES` later gives it a linked title, but is never required to attach the image.

> Spec only — build under the v23 branch, same PR as `/join-guild`.
