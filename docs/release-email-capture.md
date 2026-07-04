# Release-email feature screenshots — capture runbook

The redesigned release-update email (Site Settings → Announcements → **Draft from
latest release**) shows one **feature card** per changelog entry, each with an
optional **screenshot** of that feature. Screenshots are captured from seeded
(PII-free) data and hosted on R2 at a stable key, so the composer assembles them
automatically. This note is the go-live / refresh procedure.

## How a card gets its screenshot

1. **Registry** — `core/release_email.py::FEATURE_PAGES` lists the member-hub pages
   we screenshot (slug → label → URL). It drives *both* the capture and the
   composer's screenshot `<select>`.
2. **Changelog** — a `CHANGELOG` entry in `plfog/version.py` may carry an optional
   `"screenshot": "<slug>"` key naming its default shot (absent → the card renders
   text-only). Valid slugs are the registry slugs.
3. **Storage** — each shot lives at `email/features/<slug>.png` in `default_storage`
   (R2 in prod, the local media dir in dev).
4. **Guard** — `resolve_feature_shot_url(slug)` returns a URL **only if the object
   exists**, and the composer only offers slugs that are actually captured. So no
   broken image can ever ship, even before the first capture runs.

## Running the capture

The capture reuses the Playwright harness (like the copy-review screenshots). It is
opt-in (`CAPTURE_SCREENSHOTS`) and e2e-marked, so it never runs in the normal suite.

```bash
CAPTURE_SCREENSHOTS=1 pytest -m e2e \
  tests/e2e/screenshots_spec.py::describe_feature_screenshots
```

It seeds member-hub data (`_seed_member_hub`), signs in as the seeded admin, shoots
each `FEATURE_PAGES` page **framed** (a fixed 1200×800 viewport — not a full-page
scroll-shot), and uploads each to `email/features/<slug>.png` via `save_feature_shot`
(overwrite-in-place, so the latest state always sits at one URL).

- **In CI / prod:** set the R2 env vars (`R2_ACCOUNT_ID`, `R2_BUCKET_NAME`,
  `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_PUBLIC_URL`) so `default_storage`
  is R2. This is the one go-live prerequisite. It can run in the existing
  `copy-review` CI job or as a Render one-off job.
- **In local dev (no R2 vars):** the shots write to the local media dir — useful for
  eyeballing, never seen by members.

## Adding a new feature page

1. Add a `FeaturePage(slug, label, url_name)` to `FEATURE_PAGES` (kwargless URL; add a
   `query="?tab=…"` if the feature lives on a tab).
2. Seed its state in `tests/e2e/screenshots_spec.py::_seed_member_hub` so the shot
   shows the feature populated.
3. Re-run the capture. Then add `"screenshot": "<slug>"` to that feature's `CHANGELOG`
   entry so the card defaults to the shot (the composer can still swap or clear it).

## Verify before sending

Always **Send test to me** from the composer and open it in a real inbox — it is the
one step that catches how the dark hero band renders after a mail client's dark-mode
inversion (the light body survives fine; the hero is the one dark element).
