# Copy-review gallery — anonymous comments (TEMPORARY, remove on/after 2026-08-10)

**Status:** spec + implementation plan
**Author:** Josh (via assistant)
**Date:** 2026-07-27
**Lifespan:** ~2 weeks. This is a throwaway review aid, not a permanent feature. See
[Teardown](#10-teardown-remove-onafter-2026-08-10).

---

## 1. What & why

The **CMS copy-review gallery** at <https://copy-review.pastlives.space/> is a static
GitHub Pages site rebuilt on every merge to `main` (workflow
`.github/workflows/copy-review.yml`). It renders one `<section>` per captured page,
grouped under `<h2>` surface headings, from seeded/PII-free screenshots. It is generated
by `_write_index()` in `tests/e2e/screenshots_spec.py`.

We want the copy team to leave **comments on each area (section)** of that gallery so
feedback lives next to the screenshot it's about — with:

- **No login.** Anyone with the (unlisted) URL can read and post.
- **Attribution.** The commenter types the name it's *from* (free-text, remembered
  per browser).
- **Edit / delete.** A commenter can edit or delete their **own** comments.
- **Shared.** Everyone reviewing sees everyone's comments (that's the whole point).

Because the gallery is a **static site with no backend**, shared comments need a store.
We add a **tiny, unauthenticated JSON API to the plfog Django app** (deployed to
production on Render, same pipeline the gallery build tracks) and inject a vanilla-JS
comment widget into the generated gallery HTML that talks to it cross-origin.

This is deliberately temporary. It is **not** a member-facing feature and gets **no
member-facing changelog entry**.

---

## 2. Constraints & decisions

| Decision | Choice | Rationale |
|---|---|---|
| Backend location | plfog Django API on **production** (`book.pastlives.space`) | Tracks the same `main` pipeline as the gallery build; the live migration and the gallery JS stay in sync. Anonymous rows are guarded and removed via reverse migration in ~2 weeks. |
| Auth model | **None** (public). Edit/delete gated by a per-comment secret `edit_token`. | "No login." The token (returned once on create, stored in the browser's `localStorage`) lets a browser edit/delete only the comments it created, without accounts. |
| Framework | Plain Django `JsonResponse` function views, `@csrf_exempt`, manual CORS. | plfog has **no DRF and no django-cors-headers**. The existing push-subscription endpoints in `core/views.py` are the pattern to follow. Do not add dependencies for a throwaway. |
| Section identity | `section_key = slug(surface + "--" + label)`, authored by Python at generation time and embedded in `data-section-key`. | Stable across rebuilds as long as a page keeps its label (better than the counter-based filename, which shifts when pages are added/removed). A renamed page orphans its thread — acceptable for 2 weeks. |
| Storage on delete | **Soft delete** (`deleted_at` + `ActiveManager`), per the repo's soft-delete standard. | Matches CLAUDE.md; keeps a record for the throwaway window; trivially purged at teardown. |

---

## 3. Backend — model

New model in `core/models.py` (or `core/copy_review.py` imported by `core/models.py` —
follow whatever the app already does for cohesion; a single model in `core/models.py` is
fine). Follow **all** PLFOG standards: `help_text` on every field, `__str__`, type hints,
manager for querysets, business logic on the model/manager (not the view).

```python
class CopyReviewComment(models.Model):
    """A public, unauthenticated comment on one section of the copy-review gallery.

    TEMPORARY — the whole copy-review comments feature is a ~2-week review aid.
    Remove on/after 2026-08-10 (see docs/superpowers/plans/2026-07-27-copy-review-comments.md).
    """

    section_key = models.CharField(
        max_length=200, db_index=True,
        help_text="Stable slug of the gallery section this comment belongs to (surface--label).",
    )
    author_name = models.CharField(
        max_length=80,
        help_text="Free-text name the commenter attributed the comment to. Not a user account.",
    )
    body = models.TextField(help_text="The comment text (max 2000 chars, enforced by the form).")
    edit_token = models.CharField(
        max_length=64,
        help_text="Secret returned once on create; required to edit or delete this comment.",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the comment was posted.")
    updated_at = models.DateTimeField(auto_now=True, help_text="When the comment was last edited.")
    deleted_at = models.DateTimeField(
        null=True, blank=True, help_text="Set when the commenter soft-deletes the comment.",
    )

    objects = CopyReviewCommentManager()      # active only (deleted_at IS NULL)
    all_objects = models.Manager()            # includes soft-deleted

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.author_name} on {self.section_key} ({self.created_at:%Y-%m-%d})"

    def owned_by(self, token: str) -> bool:
        """Constant-time check that `token` matches this comment's edit_token."""
        return bool(token) and secrets.compare_digest(self.edit_token, token)

    def apply_edit(self, author_name: str, body: str) -> None:
        self.author_name = author_name
        self.body = body
        self.save(update_fields=["author_name", "body", "updated_at"])

    def soft_delete(self) -> None:
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])
```

Manager:

```python
class CopyReviewCommentManager(models.Manager):
    def get_queryset(self) -> models.QuerySet:
        return super().get_queryset().filter(deleted_at__isnull=True)

    def post(self, *, section_key: str, author_name: str, body: str) -> "CopyReviewComment":
        """Create a comment with a freshly generated edit_token."""
        return self.create(
            section_key=section_key, author_name=author_name, body=body,
            edit_token=secrets.token_hex(16),
        )

    def grouped(self) -> dict[str, list["CopyReviewComment"]]:
        """All active comments grouped by section_key, each list oldest-first."""
        grouped: dict[str, list[CopyReviewComment]] = {}
        for comment in self.get_queryset():
            grouped.setdefault(comment.section_key, []).append(comment)
        return grouped
```

- `secrets.token_hex(16)` → 32 hex chars; use `secrets.compare_digest` for the check.
- Register in `core/admin.py` so Josh can moderate/purge from Django admin.

**Migration:** standard `makemigrations`. It's an additive create — no data migration, so
no reverse `RunPython` needed; the auto migration reverses cleanly.

---

## 4. Backend — validation (form)

Validation lives in a Django **Form**, never the view (fat models / skinny views).

```python
class CopyReviewCommentForm(forms.Form):
    section = forms.CharField(max_length=200)
    author_name = forms.CharField(max_length=80)
    body = forms.CharField(max_length=2000)

    def clean_section(self) -> str: ...      # strip; reject blank
    def clean_author_name(self) -> str: ...  # strip; reject blank
    def clean_body(self) -> str: ...         # strip; reject blank
```

- Every field required and non-blank after strip → else the view returns `400` with the
  form errors as JSON.
- `max_length` on `body` is the abuse cap (2000). `author_name` cap 80.

---

## 5. Backend — endpoints

Plain Django function views in `core/views.py` (or a small `core/copy_review_views.py`),
all `@csrf_exempt`, all wrapped by a **CORS helper** that (a) answers `OPTIONS` preflight
with `204` + CORS headers and (b) stamps CORS headers on every response.

**CORS** (manual — no library):
- Allowed origins constant: `COPY_REVIEW_ALLOWED_ORIGINS = {"https://copy-review.pastlives.space"}`.
  Reflect the request `Origin` back in `Access-Control-Allow-Origin` **only if** it's in
  the set (else omit the header). Add `http://localhost:*`/`http://127.0.0.1:*` handling
  only if trivial; not required.
- `Access-Control-Allow-Methods: GET, POST, OPTIONS`
- `Access-Control-Allow-Headers: Content-Type`
- `Vary: Origin`

**Routes** (add under `core/urls.py`, reachable unauthenticated on the public surface —
verify `SurfaceMiddleware` does not gate `/copy-review/`):

| Method | Path | Body (JSON) | Success | Errors |
|---|---|---|---|---|
| `GET` | `/copy-review/comments/` | — | `200 {"sections": {"<key>": [ {id, author_name, body, created_at, updated_at}, ... ]}}` | — |
| `POST` | `/copy-review/comments/` | `{section, author_name, body, website}` | `201 {"comment": {...}, "edit_token": "<hex>"}` | `400` invalid JSON / form errors; honeypot `website` non-empty → `200 {"ok": true}` no-op |
| `POST` | `/copy-review/comments/<int:pk>/edit/` | `{edit_token, author_name, body}` | `200 {"comment": {...}}` | `404` missing; `403` token mismatch; `400` form errors |
| `POST` | `/copy-review/comments/<int:pk>/delete/` | `{edit_token}` | `200 {"ok": true}` | `404` missing; `403` token mismatch |
| `OPTIONS` | any of the above | — | `204` + CORS headers | — |

- **Never** return `edit_token` in the GET/list payload — only in the create response.
- Serialize timestamps as ISO 8601 (`.isoformat()`).
- Invalid JSON body → `400 {"error": "Invalid JSON"}` (mirror the push endpoints).
- `POST` uses JSON content-type → triggers a CORS preflight, hence the `OPTIONS` handler.
- **Honeypot:** the create form includes a hidden `website` field; if a bot fills it,
  return a success shape without persisting.
- **Best-effort throttle (optional, keep simple):** per-IP cap (e.g. 30 posts / 10 min)
  via `django.core.cache` → `429` when exceeded. If it adds meaningful complexity, skip
  it — the unlisted URL + length caps + short lifetime are the primary guard. Do **not**
  block on getting a perfect rate limiter.

Keep views skinny: parse JSON → run `CopyReviewCommentForm` → call the manager/model
method → serialize. No business logic in the view.

---

## 6. Frontend — inject the widget into the gallery

Modify `_write_index()` in `tests/e2e/screenshots_spec.py` (this is test-harness/
generator code, outside the coverage-measured packages — no unit tests required for the
JS, but keep it ruff-clean Python and valid HTML/JS):

1. **Section keys.** For each `<section>`, compute
   `key = slugify(f"{surface}--{label}")` (lowercase, non-alphanumeric → `-`, collapse
   repeats, strip) and emit `<section data-section-key="{key}" id="{key}">`. Add a small
   Python slug helper matching the JS-side expectation (the server just stores the string).

2. **Inject CSS + one `<script>`** before `</body>` with a self-contained vanilla-JS
   widget (no external libraries — the gallery is standalone). Behavior:
   - `const API_BASE = "https://book.pastlives.space";` (allow `?api=` query-param
     override for local testing — nice-to-have).
   - On load: `GET {API_BASE}/copy-review/comments/` **once**, group by `section_key`,
     render each section's thread beneath its screenshot: each comment shows
     `author_name`, `body`, a humanized timestamp (`new Date(...).toLocaleString()`),
     and **Edit/Delete** buttons **only** when this browser owns that comment's token.
   - **Add-comment form** under every section: a name input (pre-filled from the
     remembered name), a body textarea, and a Post button. On success, store
     `{[comment.id]: edit_token}` in `localStorage` (key e.g. `crc_tokens`) and the last
     used name (`crc_name`), then re-render that section.
   - **Edit** inline (swap the comment body for a textarea + Save/Cancel), `POST .../edit/`
     with the stored token.
   - **Delete** with an **inline** confirm (a small "Delete? yes / no", not a native
     `window.confirm()` blocking dialog), `POST .../delete/` with the stored token, then
     remove from the DOM.
   - **Graceful degradation:** if the API is unreachable (e.g. viewing the gallery
     locally or the bundled offline file), show a subtle "comments unavailable" note and
     never break the gallery. Wrap fetches in try/catch.
   - Style to match the existing gallery CSS (light, system-ui, simple borders). Show a
     per-section comment count.

3. **Bundled offline file** (`scripts/bundle_screenshots.py`) needs **no change** — it
   inlines images for emailing; the comment widget simply won't reach the API there and
   degrades to "unavailable." (Confirm the injected script doesn't throw during bundling.)

---

## 7. Testing (100% coverage, BDD spec style)

New code in `core/` is coverage-measured → **100% branch coverage required**. Use
`pytest-describe` (`describe_*` / `it_*`), factory-boy, `*_spec.py` under `core/spec/`.

- **Model/manager** `core/spec/models/copy_review_comment_spec.py`:
  - `objects.post()` generates a token and persists; `__str__`; `owned_by` true/false
    (incl. empty token); `apply_edit` updates fields + `updated_at`; `soft_delete` hides
    from `objects` but not `all_objects`; `grouped()` groups active comments oldest-first
    and excludes soft-deleted.
- **Form** `core/spec/forms/copy_review_comment_form_spec.py`: valid; blank/whitespace
  each field → invalid; `body` over 2000 → invalid; `author_name` over 80 → invalid.
- **Views** `core/spec/views/copy_review_comments_spec.py`:
  - GET groups by section, excludes soft-deleted, **never** leaks `edit_token`.
  - POST create → `201`, returns token, row persists; invalid JSON → `400`; form errors →
    `400`; honeypot filled → no row created.
  - Edit: correct token → `200` + updated; wrong token → `403`; missing pk → `404`.
  - Delete: correct token → soft-deleted + `200`; wrong token → `403`; missing → `404`.
  - CORS: allowed `Origin` reflected on GET/POST; `OPTIONS` preflight → `204` + headers;
    disallowed origin → no ACAO header.
  - (If the throttle is implemented, one test that it trips.)
- **factory** `CopyReviewCommentFactory` in `core/factories.py` (or the app's factories
  module).

Run `ruff format . && ruff check --fix .`, `pytest` (the e2e screenshot test stays
opt-in and won't run in the normal suite), and confirm coverage stays at 100 for `core`.

---

## 8. Files touched

- `core/models.py` (+ manager) — new `CopyReviewComment`, `CopyReviewCommentManager`.
- `core/forms.py` — `CopyReviewCommentForm`.
- `core/views.py` (or `core/copy_review_views.py`) — 4 endpoints + CORS helper.
- `core/urls.py` — `/copy-review/comments/...` routes.
- `core/admin.py` — register `CopyReviewComment` for moderation.
- `core/migrations/000X_copyreviewcomment.py` — auto migration.
- `core/factories.py` + `core/spec/...` — factory + specs.
- `tests/e2e/screenshots_spec.py` — `_write_index()` section keys + injected widget.
- `plfog/version.py` — bump `VERSION` (no member-facing changelog entry; see §9).
- `docs/superpowers/plans/2026-07-27-copy-review-comments.md` — this spec (in the PR).

---

## 9. Versioning / changelog

- **Bump `VERSION`** in `plfog/version.py` (every PR bumps version) — base off the current
  value on `origin/main`, patch bump.
- **No member-facing `CHANGELOG` entry.** This is an internal, temporary copy-team tool,
  invisible to members (CLAUDE.md: intra-cycle/internal changes get no changelog entry).
  Do **not** trigger the Discord announce workflow for this.

---

## 10. Teardown (remove on/after 2026-08-10)

Removal is one revert because it's one PR:

1. Revert the feature PR (drops the endpoints, form, admin, URL routes, injected widget).
2. The model removal includes a reverse migration (auto migration reverses cleanly) —
   `makemigrations` after deleting the model, or `git revert` the add-migration and run
   `migrate` down.
3. Purge rows: `CopyReviewComment.all_objects.all().delete()` (or drop via the reverse
   migration).
4. Merge to `main` → Render redeploys, next gallery build ships without the widget.

Leave the `# TEMPORARY — remove on/after 2026-08-10` marker on the model, views, and the
injected block so a `grep` finds every piece.
