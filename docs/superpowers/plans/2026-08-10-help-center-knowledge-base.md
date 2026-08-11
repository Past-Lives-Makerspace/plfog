# Help Center Knowledge Base — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-10
**Surface:** FOG hub `pastlives.test` — `/help/` and everything under it, plus the admin editor at `/help/edit/`.
**Related:** Spec A of the four-spec help-center overhaul. Companions (they depend on this spec's registry contract):
`2026-08-10-info-view-hover-help.md` (B), `2026-08-10-guided-tours.md` (C), `2026-08-10-instructor-orientation-unlock.md` (D).
Shared brief: the fogstorm session's locked decisions of 2026-08-10 (approved 29-article IA, help-key spine, GATED list).

---

## 1. Summary

Today `/help/` is one long page: eight guides stacked under a table of contents, with no per-article URL, no
search, no categories, and no screenshots (the Markdown renderer strips `<img>`). This spec turns it into a real
help center: a landing page of category cards grouped by audience, a page per article at
`/help/<category>/<article>/` with a TOC, screenshots, related guides, and prev/next — plus a plain `?q=` search.
A member, guild lead, instructor, or admin finds their task in two clicks or one search.

It also establishes the **help-key registry** — the in-code spine (`core/help_registry.py`) that Specs B (Info
View hover help), C (guided tours), and D (instructor orientation) all resolve against — and the **screenshot
pipeline** that captures every how-to step from seeded demo data and commits the PNGs to the repo alongside the
copy they illustrate.

### Locked decisions (from the fogstorm brief + this spec's calls)

| Decision | Choice |
|---|---|
| Registry home | **`core/help_registry.py`** (this spec owns the call; B/C/D follow). `core` because the registry is cross-app — hub renders it, core serves it as JSON (Spec B), tours reference it (Spec C) — and `core` is already the home for cross-cutting infrastructure (context processors, `SiteConfiguration`, triggers). |
| Article model | Evolve `WikiArticle` in place (new `category` FK + `related_articles` M2M), keep the `page` FK and `uq_wikiarticle_page_slug` constraint untouched. No new article model — existing data, the formset editor, and slug stability all survive. |
| Category audience | Audience lives on **`HelpCategory`** (`TextChoices`); `WikiArticle.audience` is a delegating property. One source of truth, per the brief ("categories carry the audience"). |
| Audience ≠ access control | Every help page stays **public-read**, exactly like today's `help_page` (org reference content, no PII). Audience is a wayfinding label only. |
| Screenshot storage | **`static/help/<article-slug>/<file>.png`**, committed to the repo, served by WhiteNoise. Versioned with the copy (locked decision), zero R2 dependency, works identically in dev/QA/prod. Manifest-hashing implications in §9. |
| Markdown | New **`profile="help"`** parameter on `render_markdown()` + a `help_markdown` template filter. Allows `img[src|alt|title]` restricted to `/static/help/…`, allows heading `id` anchors, keeps internal links same-tab. The shared member-content profile is byte-for-byte unchanged. |
| Migration | One **schema-only** migration (auto-reversible): `HelpCategory` + two fields on `WikiArticle`. No `RunPython` — category assignment comes from the idempotent seed command, and existing rows stay valid with `category = NULL`. |
| Old `/help/#slug` anchors | **JS anchor mapping on the landing page** (a URL fragment never reaches the server, so a redirect can't see it). A server-rendered map of legacy slug → new article URL; unknown/unshipped targets simply stay on the landing — never a dead end. |
| Search | GET-param `?q=`, server-side: split on whitespace, per-term `icontains` ANDed across title/body/category name — the house grain (member directory, Manage Members). **No HTMX live search** (YAGNI). No pagination (~30 articles). |
| Feature flag | `help_page_enabled` stays the single master switch for everything under `/help/`. No new flag. |
| Sidebar | The existing **Help** link (label unchanged) already points at `hub_help`, gated on `help_page_enabled`. No nav changes. |
| Seed shape | Extend the `seed_wiki_articles` / `seed_floor_geometry` pattern: bodies in a data module, `update_or_create` keyed on slug, `--dry-run`, end-of-run report. `seed_wiki_articles` is retired once `seed_help_center` supersedes it. |

## 2. What already exists (reuse, don't reinvent)

All verified in code 2026-08-10.

| Need | Existing thing | Location |
|---|---|---|
| Article model + slug stability | `WikiArticle` (title, slug auto-filled/stable, body, sort_order, is_published; `uq_wikiarticle_page_slug`) | `membership/models.py:2206` |
| Josh-only page blocks | `OrgInfoPage` singleton (intro, parking, who_to_contact, banner, code of conduct) + `OrgFAQItem`, `OrgLink` | `membership/models.py:2032` |
| Help views + admin gate | `help_page`, `help_edit`, `help_articles_save`, `_org_info_edit_context`, `_require_admin`, `_viewing_as_admin` | `hub/views.py:2296–2427` |
| URLs | `/help/`, `/help/edit/`, `/help/{faq,links,articles}/save/`, `/help/floorplan/delete/` | `hub/urls.py:183–194` |
| Editor formset idiom | Tabbed editor (`vote-tab`), `extra=0` formsets, hidden `<template>` + "+Add" clone, real Delete buttons that `requestSubmit()` | `templates/hub/org_info_edit.html`; forms at `hub/forms.py:1099–1117` |
| Markdown renderer | `render_markdown()` — bleach allowlist (no `img`), `_harden_link` forces `target=_blank nofollow` on **every** link; `guild_markdown` filter | `membership/markdown.py`, `membership/templatetags/membership_md.py` |
| Search component | `components/table_search.html` — GET form, `q` input, Search button, clear link (context vars `q`, `placeholder`) | `templates/components/table_search.html` |
| Search precedent | Plain GET + `icontains` (member directory `?q=`, Manage Members filters) | `hub/views.py` ~240, ~3993 |
| TOC / article CSS | `.pl-wiki-toc`, `.pl-wiki-article` (scroll-margin-top), `.pl-guild-grid`, `.pl-guild-section`, `.pl-md` prose block (no `img` rule — consistent with the sanitizer) | `static/css/hub.css:3210–3281` |
| Screenshot machinery | `describe_cms_screenshots` / `describe_feature_screenshots` — live_server + Playwright, `_seed()` + `_seed_member_hub()`, `login_via_code`, opt-in via `CAPTURE_SCREENSHOTS`, contact-sheet writer | `tests/e2e/screenshots_spec.py`, `tests/e2e/conftest.py:63` |
| Login helper | `login_via_code` fixture — the real allauth login-by-code flow, dismisses the welcome modal | `tests/e2e/conftest.py:63` |
| Seed pattern | Module-level data + idempotent sync keyed on slug + `--dry-run` + report | `membership/management/commands/seed_wiki_articles.py`, `seed_floor_geometry.py` |
| Static serving | WhiteNoise `CompressedManifestStaticFilesStorage`; `STATICFILES_DIRS = [BASE_DIR / "static"]` | `plfog/settings.py:311,412` |
| Feature flag plumbing | `help_page_enabled` on `SiteConfiguration`, exposed by the `feature_flags` context processor | `core/models.py:282`, `core/context_processors.py:56` |
| Existing 8 guides | Seeded bodies (raw material to vet/split/rewrite — Josh judges them lackluster) | `seed_wiki_articles.py` `ARTICLES` |

Genuine gaps (net-new): per-article pages/URLs, `HelpCategory`, related articles, search, the help-key registry,
image-capable help Markdown, and the help-screenshot capture spec.

## 3. Where the code lives

```
core/
    help_registry.py                       # NEW — the help-key spine (dict + helpers)
    spec/help_registry_spec.py             # NEW
membership/
    models.py                              # HelpCategory (new), WikiArticle evolution
    markdown.py                            # render_markdown(source, profile=…)
    templatetags/membership_md.py          # + help_markdown filter
    help_content.py                        # NEW — categories, 29 article bodies, shots, legacy map
    management/commands/seed_help_center.py  # NEW (retires seed_wiki_articles.py)
    migrations/00XX_help_categories.py     # NEW — schema only, auto-reversible
hub/
    views.py                               # help_page rework + help_category/help_article/help_search
                                           #   + help_categories_save; WikiArticleForm gains fields
    forms.py                               # HelpCategoryForm(+Set), WikiArticleForm changes
    urls.py                                # new routes under /help/
tests/
    membership/wiki_article_spec.py        # extend (model logic)
    membership/markdown_spec.py            # extend (+ golden fixtures)
    membership/help_category_spec.py       # NEW
    membership/help_content_spec.py        # NEW (drift guard)
    membership/management/seed_help_center_spec.py  # NEW (beside seed_wiki_articles_spec.py, which retires with its command)
    hub/help_spec.py                       # extend (views)
templates/hub/
    help.html                              # reworked landing
    help_category.html                     # NEW
    help_article.html                      # NEW
    help_search.html                       # NEW
    org_info_edit.html                     # + Categories tab; Articles tab row changes
templates/components/table_search.html     # + optional `action` param (backwards-compatible)
static/css/hub.css                         # .pl-helpcat-*, .pl-help-badge, .pl-md--help img, prev/next, mark
static/help/<article-slug>/*.png           # NEW — committed screenshots
tests/e2e/
    screenshot_seed.py                     # NEW — _seed/_seed_member_hub factored out for reuse
    help_screenshots_spec.py               # NEW — the capture spec (opt-in)
scripts/capture-help-screenshots.sh        # NEW — entry point
```

## 4. Data model

### 4.1 `HelpCategory` (new, `membership/models.py`, beside `WikiArticle`)

| Field | Type | Notes |
|---|---|---|
| `name` | `CharField(100)` | help_text="Category name shown on the Help landing page, e.g. 'Running a guild'." |
| `slug` | `SlugField(120, unique=True, blank=True)` | help_text="URL segment (/help/<slug>/). Auto-filled from the name; stable once set." Auto-slugified + de-duplicated in `save()`, same idiom as `WikiArticle.save()`. |
| `audience` | `CharField(20, choices=Audience.choices, default=Audience.MEMBER)` | help_text="Who this category is for — groups it on the landing page and drives the badge." |
| `description` | `CharField(300, blank, default="")` | help_text="One-liner under the name on the landing card." |
| `sort_order` | `PositiveIntegerField(default=0)` | help_text="Order within its audience group; lower shows first." `save()` auto-assigns `max(existing sort_order) + 10` when a new row is created with the default `0`, so an admin-created category lands at the end of its group instead of jumping to the front (seeded categories pass explicit values). |

```python
class Audience(models.TextChoices):
    MEMBER = "member", "Members"
    GUILD_LEAD = "guild_lead", "Guild leads & staff"
    INSTRUCTOR = "instructor", "Instructors"
    ADMIN = "admin", "Admins"
```

`Meta.ordering = ["sort_order", "pk"]`. `__str__` → `f"{self.name} ({self.get_audience_display()})"`.

Manager: `HelpCategoryQuerySet.with_published_counts()` (annotate `published_count` via filtered `Count`) and
`.nonempty()` (`published_count > 0`) — the landing grid uses both in one query (no N+1).

**Reserved slugs:** `edit`, `search`, `categories`, `articles`, `faq`, `links`, `floorplan`, `more` collide
with fixed `/help/…` routes (this spec adds `/help/categories/save/` itself; `more` is the uncategorized URL
segment). URL registration order makes the fixed routes win regardless; `HelpCategoryForm.clean_slug`
additionally rejects them with *"That name is reserved — pick another."* (validation in the form, per house rules).

### 4.2 `WikiArticle` evolution

Existing fields, the `page` FK, slug auto-fill/stability, and `uq_wikiarticle_page_slug` are **untouched**.
Because `page` is the `OrgInfoPage` singleton, that constraint already makes slugs globally unique — which is
what lets `/help/<category>/<article>/` resolve by article slug alone (§5).

New fields:

| Field | Type | Notes |
|---|---|---|
| `category` | `ForeignKey(HelpCategory, null=True, blank=True, on_delete=SET_NULL, related_name="articles")` | help_text="Where this guide lives in the help center. Uncategorized guides don't appear on the landing grid." Nullable so existing rows stay valid through the migration and a deleted category never deletes guides. |
| `related_articles` | `ManyToManyField("self", symmetrical=False, blank=True, related_name="related_to")` | help_text="Hand-picked guides shown under 'Related guides'. Same-category guides fill any remaining slots automatically." |

New property: `audience` → `self.category.audience if self.category_id else HelpCategory.Audience.MEMBER`
(cheap derived data → property, per house style).

### 4.3 Migration `00XX_help_categories` — path and reverse

Schema-only, one logical change:

1. `CreateModel HelpCategory`
2. `AddField WikiArticle.category`
3. `AddField WikiArticle.related_articles`

Reverse is Django's automatic inverse (drop the M2M table, drop the FK column, drop the model) — no data is
destroyed on rollback beyond the new columns themselves, and the pre-existing article rows/constraint are never
touched in either direction. **No `RunPython`:** category assignment and the new content come from the
idempotent `seed_help_center` command (deploy step: `migrate` → `seed_help_center`), so there is no in-migration
data step to reverse. Between migrate and seed, the 8 live guides are all `category = NULL` — the landing's
permanent **"All guides" fallback list** (§7.1) keeps every one of them reachable, so the deploy window loses
nothing.

## 5. Business logic (fat models)

All on `WikiArticle` / its queryset unless noted; views stay thin.

- `WikiArticleQuerySet.search(q: str)` — splits `q` on whitespace and **ANDs one `icontains` clause per term**,
  each term matching across the three fields: for every term,
  `.filter(Q(title__icontains=term) | Q(body__icontains=term) | Q(category__name__icontains=term))`; chained
  filters AND the terms. (A whole-string match would return zero results for "how do I book an orientation" —
  the headline scenario.) Also excludes `help_content.UNLISTED_SLUGS` (§10.6) and drafts
  (`published()`), with `select_related("category")`. Empty `q` returns `none()` (the view shows the prompt
  state instead).
- `WikiArticle.search_snippet(q: str, radius: int = 90) -> str` — strips the Markdown body to plain text, finds the first case-insensitive hit, returns an HTML-escaped window with the match wrapped in `<mark>` (title-only hits fall back to the lead text). Escaping happens **before** the `<mark>` insertion, so the snippet is safe to `mark_safe` in the template.
- `WikiArticle.lead_text(limit: int = 160) -> str` — first paragraph of the body, Markdown-stripped, truncated on a word boundary. Used by category rows and search fallbacks.
- `WikiArticle.related_for_display(limit: int = 3) -> list[WikiArticle]` — explicit published `related_articles` first (seed order), then same-category published siblings (by `sort_order`, excluding self and already-picked) until `limit` is reached. Fewer than 3 exist → return fewer; zero → the template hides the footer.
- `WikiArticle.next_in_category()` / `previous_in_category()` — published neighbor within the same category ordered by `(sort_order, pk)`; `None` at the ends (template hides the missing side). Uncategorized articles return `None` for both.
- `WikiArticle.url_category_segment` (property) — `self.category.slug` or the reserved fallback `"more"` for uncategorized articles, so every published article always has a canonical URL.
- `WikiArticle.get_absolute_url()` — `reverse("hub_help_article", kwargs={"category_slug": self.url_category_segment, "article_slug": self.slug})`.
- `WikiArticle.toc() -> list[tuple[int, str, str]]` — extracts `(level, anchor_id, text)` for `h2`/`h3` headings **with ids** from the help-rendered body (one regex pass over sanitized output — testable, no client JS). Headings without ids are skipped; an article with none yields `[]` and the TOC block hides.

### 5.1 The help-key registry — `core/help_registry.py` (the contract B/C/D follow)

In-code, module-level, no DB (locked: content is repo-authored; YAGNI on a topics model). **This section is
the canonical contract** — Specs B, C, and D import these names rather than restating them:

- **(a) The dict is named `HELP_KEYS`.** Spec B imports `core.help_registry.HELP_KEYS` by that exact name to
  serve it as JSON; Spec C's tour definitions reference its keys.
- **(b) There is ONE key regex: `KEY_PATTERN`** (single-dot, below). B's and C's drift tests **import
  `KEY_PATTERN`** from this module — they do not restate their own.
- **(c) `article_slug` and `anchor` are OPTIONAL (`None` allowed).** An **annotation-only key** — e.g. Spec C
  stamps tour-only keys like `nav.sidebar` or `guild.edit-tabs` into templates, and Spec B's template-walk
  test requires every `data-help-key` found in a template to exist in the registry — is a registry entry with
  `article_slug=None`. `url_for()` falls back to `/help/` for them.

```python
from typing import TypedDict

class HelpKeyEntry(TypedDict):
    title: str                # short human label, e.g. "Rank your top 3"
    short_text: str           # 1–2 plain sentences for the Info View hover panel (≤ 200 chars)
    article_slug: str | None  # WikiArticle.slug the key deep-links into; None = annotation-only key
    anchor: str | None        # heading id inside that article ([a-z0-9-]+; convention: key with "." → "-");
                              #   None when article_slug is None

HELP_KEYS: dict[str, HelpKeyEntry] = {
    "voting.rank-guilds": {
        "title": "Rank your top 3",
        "short_text": "Pick your 1st, 2nd, and 3rd choice guilds. Your ballot sticks and counts every month until you change it.",
        "article_slug": "guild-voting",
        "anchor": "voting-rank-guilds",
    },
    "orientation.book-slot": {...},
    "teach.create-class": {...},
    "guild.manage-staff": {...},
    "nav.sidebar": {"title": "The menu", "short_text": "…", "article_slug": None, "anchor": None},
    # …grows with the P1 content in §10, and with B/C's annotation-only keys.
}

KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.[a-z0-9]+(?:-[a-z0-9]+)*$")  # THE regex — B/C import it

def entry(key: str) -> HelpKeyEntry: ...   # HELP_KEYS[key] — KeyError propagates (fail loudly)
def url_for(key: str) -> str: ...          # article URL + "#" + anchor; falls back to "/help/" when
                                           #   article_slug is None OR the article isn't seeded/published yet
```

Key format is the brief's contract: `<area>.<action-slug>`, lowercase, dots + hyphens only. Article section
headings carry the anchor via Markdown `attr_list` (bundled in the already-enabled `extra` extension):
`### Rank your top 3 {#voting-rank-guilds}`.

**No build-order deadlock:** registry entries may land **before** their articles ship (Spec B's phase-4
annotations reference `settings.*` / `notifications.*` keys whose articles are P2 — this spec's phase 7).
The §12 drift test therefore checks, only for entries with `article_slug` set **and** whose article is present
in the seed data, that the `anchor` appears as a heading id in that body; entries pointing at a
pending/unpublished article are tolerated, and `url_for()` degrades to `/help/` until the article resolves.
Annotation-only entries (`article_slug=None`) are skipped by the anchor check entirely. Spec B serves
`HELP_KEYS` as JSON; Spec C's tour steps and `data-help-key` DOM attributes reference the same keys; this spec
only defines and validates the module.

### 5.2 Help-profile Markdown — `membership/markdown.py`

`render_markdown(source: str, *, profile: str = "member") -> str`. The default keeps today's exact behavior —
every existing call site and the `guild_markdown` filter are untouched. Unknown profile → `ValueError` (fail
loudly).

`profile="help"` differs in three ways, each scoped to this profile only:

1. **Images:** `img` joins the tag allowlist with attributes `src`/`alt`/`title` — and `src` is validated by a
   bleach attribute callable that only accepts values starting with `/static/help/` (anything else — external
   URLs, `data:`, protocol-relative, `/media/` — drops the attribute, and a follow-up pass removes src-less
   `img` entirely). No tracking pixels, no mixed content, no hotlinking.
2. **Heading anchors:** `id` is allowed on `h2`/`h3`/`h4` when it matches `^[a-z0-9-]{1,80}$` (same callable
   mechanism). This is what carries the help-key anchors and feeds `toc()`.
3. **Links:** internal links (href starting with `/` or `#`) stay **same-tab** with `rel="noopener"`; external
   links keep the full existing hardening (`target=_blank`, `noopener nofollow noreferrer`). Help articles
   constantly deep-link into the app — bouncing a member to a new tab for `/guilds/voting/` is hostile.

New template filter `help_markdown` beside `guild_markdown` in `membership/templatetags/membership_md.py`.

**Security reasoning:** help bodies are repo-seeded or authored by admins in the `_require_admin`-gated editor —
never member-authored. Member-authored surfaces (guild meeting notes, announcements, guild FAQ) all render
through the untouched `member` profile, so nothing is relaxed for them. Even the help profile still strips
scripts, styles, event handlers, and every tag outside the allowlist — the wider allowlist is defense-in-depth
against an admin pasting something dumb, not a trust boundary change. (The more permissive email sanitizer in
`core/html_sanitize.py` was considered and rejected: it targets admin email bodies and allows far more than help
pages need.)

## 6. Views & URLs (skinny)

Routes in `hub/urls.py`, **fixed prefixes registered before the slug catch-alls** (this rule also binds Spec
B: its `/help/topics.json` endpoint must be registered above the `<slug:category_slug>` routes):

```
/help/                                    hub_help            help_page (reworked landing)
/help/edit/ + existing save endpoints                          (unchanged)
/help/categories/save/                    hub_help_categories_save   (new, POST, admin)
/help/search/                             hub_help_search     help_search
/help/<slug:category_slug>/               hub_help_category   help_category
/help/<slug:category_slug>/<slug:article_slug>/   hub_help_article    help_article
```

All GET views: public-read, gated on `SiteConfiguration.load().help_page_enabled` (redirect `hub_home` when
off) — identical to today's `help_page`. Behavior:

- `help_page` — landing context: `categories` = `HelpCategory.objects.with_published_counts().nonempty()`
  ordered by **explicit audience rank then `(sort_order, pk)`** — a `Case/When` annotation over `audience` in
  the display order stated once here: **member ("For every member") → instructor ("Teaching") → guild_lead
  ("Running a guild") → admin ("Admin")**. `{% regroup %}` only groups *adjacent* rows, so the rank ordering
  is what keeps each audience heading contiguous (global `(sort_order, pk)` alone would interleave groups).
  Plus `uncategorized` = published articles with `category=NULL`, excluding `UNLISTED_SLUGS` (§10.6), for the
  fallback "All guides" list (§7.1); plus everything today's view passes (page, faq_items, links, can_edit)
  minus the flat `articles` loop; plus `legacy_anchor_map` (§7.1).
- `help_category` — `get_object_or_404(HelpCategory, slug=…)`; published articles ordered
  `(sort_order, pk)`, excluding `UNLISTED_SLUGS`; admins (`_viewing_as_admin`) additionally see drafts flagged.
- `help_article` — resolve by **article slug alone** (globally unique via the page constraint):
  `WikiArticle.objects.filter(slug=article_slug).first()`. Missing → 404. Unpublished → 404 unless
  `_viewing_as_admin` (admins see it with a Draft banner). If `category_slug` ≠ the article's
  `url_category_segment` → **301 to canonical** (handles recategorization and hand-typed URLs; old article
  links never break when a guide moves categories). Unlisted articles (§10.6) resolve normally here — the URL
  is the only way in, by design.
- `help_search` — `q = request.GET.get("q", "").strip()`; results = `WikiArticle.objects.search(q)` (per-term
  AND, unlisted excluded — §5); the view builds `(article, snippet)` pairs via `search_snippet`.
- `help_categories_save` — `@require_POST`, `_require_admin`, `HelpCategoryFormSet`. Valid →
  `messages.success("Categories saved.")` + redirect `?tab=categories`. **Invalid → no redirect:** re-render
  `org_info_edit.html` with the **bound** formset in context and the Categories tab active (the view passes
  `active_tab="categories"`; the template's Alpine init prefers it over the query param), so field errors
  actually render and the admin's edits survive. This deliberately does NOT mirror `help_articles_save`'s
  current invalid path (`hub/views.py:2417–2427`: `messages.error` → redirect → unbound formsets — field
  errors never render and all edits are discarded). **`help_articles_save` (and the FAQ/Links siblings) get
  the same bound-re-render fix in this build** — same bug, same one-line-per-view cure.

## 7. UI / UX (checklist applied per screen)

Shared for all five screens: theme tokens only, both themes verified before merge; all new classes `pl-`
prefixed in `static/css/hub.css`; spacing on the 8px grid; every page extends
`guilds_page_base|default:"hub/base.html"`; no inline `display` on `x-show` elements; no new date/time inputs
anywhere in this feature.

### 7.1 Landing — `/help/` (`templates/hub/help.html`, reworked)

- **Layout:** keep the optional hero banner block and `components/page_header.html` (title "Help", refreshed
  description; admins keep the "Edit this page" `action_url` → `hub_help_edit`). Then, top to bottom: intro
  card → search → category grid → the retained Josh blocks (Parking & Arrival, Who's Who, FAQ accordion) →
  aside (Resources links, "Go to Spaces" card) — the existing `.pl-guild-grid` main/aside split survives.
- **Search box:** `components/table_search.html` with `placeholder="Search the guides…"` and a new optional
  `action` param (`<form method="get" {% if action %}action="{{ action }}"{% endif %}>` — one-line,
  backwards-compatible component change) pointing at `{% url 'hub_help_search' %}`. Sits in its own slim
  `.hub-card` directly under the intro so it's the first interactive thing on the page.
- **Category grid:** new `.pl-helpcat-grid` (CSS grid, `repeat(auto-fill, minmax(240px, 1fr))`, 1rem gap).
  Grouped under audience headings rendered with `{% regroup %}` over the **audience-ranked queryset from §6**
  (regroup only groups adjacent rows — the rank ordering is load-bearing): "For every member", "Teaching",
  "Running a guild", "Admin" (`hub-detail-label` headings). Each `.pl-helpcat-card` is **one whole-card
  `<a>`** (real tap target) to `hub_help_category`: name, muted description, muted count ("6 guides"), and a
  `.pl-help-badge` audience chip. Card styling on `--hub-card-bg`/`--hub-border` with a
  `--color-tuscan-yellow` hover border — both themes.
- **"All guides" fallback list:** whenever any published **uncategorized** articles exist (excluding unlisted
  ones), an "All guides" section renders under the category grid — a plain link list (title + `lead_text()`)
  to each article's canonical URL. This is permanent behavior, not scaffolding: it keeps the 8 live guides
  reachable through the phase-4 → seed deploy window (making §4.3's "renders like today" claim true), and it
  is where admin-created uncategorized articles surface until they're assigned a category.
- **Retained blocks:** Parking & Arrival, Who's Who, and the FAQ accordion keep their existing markup and
  `guild_markdown` rendering (member profile — these are Josh's blocks, not help articles). Their `#`-anchors
  on this page keep working natively.
- **Legacy anchors:** the view passes `legacy_anchor_map` — built from `help_content.LEGACY_SLUG_MAP` but
  **filtered to targets that exist and are published** (resolved to full article URLs). Template emits it via
  `{{ legacy_anchor_map|json_script:"help-legacy-anchors" }}` plus a ~6-line inline script: on load, if
  `location.hash` matches a map key, `location.replace(map[hash])`. Old `/help/#guild-voting` links land on
  the new article page; unmapped or not-yet-shipped targets (e.g. a P2 article) simply stay on the landing —
  no dead end either way. Fragments for the retained blocks (`#parking` etc.) aren't in the map and behave
  natively.
- **States:** *No categories yet* (post-migrate, pre-seed): grid section absent; the "All guides" fallback
  lists the existing published guides, then intro + Josh blocks — nothing vanishes during the deploy window.
  *No articles at all*: both sections absent, page is intro + Josh blocks. *Empty intro/parking/etc.*: each
  block already hides when blank (existing `{% if %}`s kept). *Feature flag off*: redirect to `hub_home`
  (unchanged).
- **Mobile:** `.pl-guild-grid` already stacks to one column below 1024px (aside after main); the category grid
  collapses to a single column via `auto-fill`. No tables on this page.

### 7.2 Category browse — `/help/<category>/` (`templates/hub/help_category.html`, new)

- **Layout:** breadcrumb line (`.pl-help-breadcrumbs`, muted links: "Help") above
  `page_header` (title = category name, description = category description; admins get an
  "Edit guides" action → `hub_help_edit` + `?tab=articles`). Then a single `.hub-card` list.
- **Article rows:** one link-row per published article, ordered `(sort_order, pk)`: title (gold link),
  `lead_text()` muted below, chevron affordance. Whole row is the `<a>`.
- **Admin extra:** when `_viewing_as_admin`, drafts appear at the bottom with a muted "Draft" `.pl-help-badge`.
- **States:** *empty category* — "No guides here yet." + a "Back to Help" `pl-btn pl-btn--secondary pl-btn--sm`
  (a category can only reach this state for admins previewing, since the landing hides empty categories — but
  the URL is shareable, so it renders friendly). *Unknown slug* — 404.
- **Mobile:** rows are full-width stacked links; nothing to degrade.

### 7.3 Article page — `/help/<category>/<article>/` (`templates/hub/help_article.html`, new)

- **Layout:** breadcrumbs ("Help / {category}") → `page_header` (title = article title; admins get
  "Edit" → editor `?tab=articles`) → `.pl-guild-grid` with:
  - **main:** TOC `nav.pl-wiki-toc` at the top of main ("On this page", built from `article.toc()`, links
    `#anchor`; hidden when `toc()` is empty) — the exact idiom the current help page uses, which also solves
    mobile (TOC precedes the body in DOM order instead of stranding in a stacked-below aside). Then the body:
    `<div class="pl-md pl-md--help">{{ article.body|help_markdown }}</div>`. Then the **related footer**:
    "Related guides" heading + up to 3 compact cards (`related_for_display()`: title + category badge), hidden
    when zero. Then the **prev/next row** (`.pl-help-prevnext`, flex, space-between): two
    `pl-btn pl-btn--secondary pl-btn--sm` links — "← {prev title}" / "{next title} →" — each side hidden at
    the ends of the category.
  - **aside:** an "In this category" `.hub-card` listing the category's other published guides (current one
    highlighted, muted) + a "Can't find it?" card with a link to `/help/search/`.
- **Images:** new scoped rule `.pl-md--help img { max-width:100%; height:auto; border:1px solid
  var(--hub-border); border-radius:8px; margin:0.75rem 0; }` — the base `.pl-md` deliberately keeps no `img`
  rule, so member-content surfaces are unaffected. Captions are the `alt`/`title` text; heading anchors get
  the existing `.pl-wiki-article`-style `scroll-margin-top` applied to `[id]` headings inside `.pl-md--help`
  so anchor jumps clear the topbar.
- **Uncategorized articles (`/help/more/<slug>/`):** the page renders fully without a category — breadcrumbs
  link to **`/help/` only** (no category crumb); the aside collapses to the "Can't find it?" search card (no
  "In this category" list); the audience badge falls back to the default (`member`, via the `audience`
  property); related footer uses explicit `related_articles` only (no same-category fill); prev/next hidden.
  **`/help/more/` itself is never emitted as a link anywhere** (breadcrumbs, cards, related, prev/next all
  point at articles or `/help/`) and has no listing view — it 404s by design; uncategorized articles are
  reached via the landing's "All guides" list, search, or a direct/canonical URL. Stated as the call: no
  pseudo-category listing page.
- **States:** *unpublished* — 404 for members; admins see the page with a "Draft — members can't see this yet"
  banner (muted `.pl-help-badge` strip under the header). *Unknown article* — 404. *Stale category segment* —
  301 to canonical. *No headings* — TOC hidden. *No related and no siblings* — footer + aside list hidden
  (aside collapses to the search card; never an empty card).
- **Mobile:** grid stacks; TOC is at the top of main so it stays useful; prev/next buttons wrap
  (`flex-wrap`); images cap at `max-width:100%`.
- Both themes: body prose is all on `.pl-md` tokens already; the only new color work is the image border,
  badges, and `mark` (§7.4) — tokens only.

### 7.4 Search results — `/help/search/?q=…` (`templates/hub/help_search.html`, new)

- **Layout:** breadcrumbs ("Help") → `page_header` ("Search help") → `table_search.html` (`q=q`,
  `placeholder="Search the guides…"`; no `action` needed — the form GETs the current URL) → results.
- **Results:** count line ("4 guides match "kiln"", muted) then one `.hub-card` per hit: title (gold link
  to the article), a badge row (category name + audience `.pl-help-badge`), and the `<mark>`-highlighted
  snippet. `mark` styled once in `hub.css`: `background: color-mix(in srgb, var(--color-tuscan-yellow) 30%,
  transparent); color: inherit; border-radius: 3px;` — legible on both themes without hardcoded hex pairs.
- **States:** *empty `q`* — prompt state: "Type something to search the guides." + the category cards' link
  list as suggestions. *No results* — "Nothing matched "{q}"." + tip ("Try fewer or different words") + a
  "Browse all guides" `pl-btn pl-btn--secondary` back to `/help/`. Never a bare blank region.
- **No pagination** — 29 articles; `icontains` over that set can't produce an unmanageable page. Revisit only
  if the KB triples (noted in §14).
- **Mobile:** stacked cards; the search form is already flex + `max-width` from the component.

### 7.5 Admin editor — `/help/edit/` (`templates/hub/org_info_edit.html`, evolved)

The existing Alpine tabbed editor gains one tab and two fields; everything follows the editor's own canonical
formset idiom (which is also the checklist's: `extra=0`, template-clone "+Add", real Delete buttons).

- **New "Categories" tab** (a `vote-tab` button between "FAQ & Links" and "Articles"; deep-linkable via
  `?tab=categories` — the existing `URLSearchParams` init handles it). Its own `<form method="post"
  action="{% url 'hub_help_categories_save' %}">` (formsets can't nest in the main form — same reason as
  FAQ/Links):
  - `HelpCategoryFormSet = inlineformset_factory`… no — categories have no parent FK, so it's a plain
    `modelformset_factory(HelpCategory, form=HelpCategoryForm, extra=0, can_delete=True)` with
    `prefix="categories"`.
  - Each row is a `.hub-card`: `form_field.html` for **name**, **slug** (hint: "Optional — the /help/ URL
    segment; auto-filled from the name"), **audience** (a `<select>`, rendered through `form_field.html` so
    it sits in `.hub-form-group` and inherits theme input tokens — rule 13), **description**; `sort_order`
    stays a `HiddenInput`; `{{ f.id }}` emitted.
  - **"+ Add a category"** button (`hub-btn hub-btn--sm`, `margin-top:1rem`) clones the hidden
    `<template id="category-empty-template">` (the formset's `empty_form`), swaps `__prefix__`, bumps
    `id_categories-TOTAL_FORMS` — byte-for-byte the FAQ/Links/Articles wiring already in this file.
  - **Per-row Delete:** saved rows render `{{ f.DELETE }}` hidden behind a real
    `pl-btn pl-btn--danger pl-btn--sm` "Delete this category" button, `style="margin-top:0.75rem;"`, that
    checks DELETE and `this.form.requestSubmit()` — deleting saves the whole page, no lost edits (the
    editor's established destructive idiom, same as "Delete this question"). Unsaved cloned rows get a
    "Remove" button that just drops the DOM node. Consequence is stated in the tab's intro copy: *"Deleting a
    category never deletes its guides — they lose their category and drop off the landing page until you
    re-assign them"* (matches `on_delete=SET_NULL`).
  - **Save:** a "Save Categories" `pl-btn pl-btn--primary` at the card bottom (`margin-top:1rem` — clear of
    the row above, rule 18). Valid POST → `messages.success("Categories saved.")` + redirect back to
    `?tab=categories`. **Invalid POST → bound re-render** (per §6): the editor renders with the bound formset
    and the Categories tab active, so field errors show inline and no edit is lost — *not* the
    redirect-with-unbound-formsets path the current `help_articles_save` takes (which discards edits and never
    shows field errors; that view and the FAQ/Links siblings get the same fix).
  - **Validation** (in `HelpCategoryForm`): `clean_slug` rejects reserved slugs (§4.1) with a visible field
    error; uniqueness violations surface as the model's field error, not a 500.
- **Articles tab, per-row additions** (in `WikiArticleForm`):
  - **`category`** — `ModelChoiceField(queryset=HelpCategory.objects.all(), required=False,
    empty_label="— No category (hidden from the landing grid) —")`, rendered via `form_field.html`.
  - **`related_articles`** — `ModelMultipleChoiceField`, `SelectMultiple(attrs={"size": 6})`, `required=False`,
    hint "Ctrl/Cmd-click to pick a few — shown under 'Related guides'. Same-category guides fill the rest
    automatically." `WikiArticleForm.__init__` excludes `self.instance` from the queryset when editing a saved
    row (an article can't relate to itself). The multi-select sits inside `.hub-form-group` via
    `form_field.html`, so it inherits input tokens; add `select[multiple] option` colors alongside the
    existing rule-13 option styling.
  - **`body`** hint updated: mentions images ("`![caption](/static/help/<guide-slug>/01-step.png)` — only
    `/static/help/` images render") and anchors ("`### Heading {#anchor-id}` makes a linkable section").
  - **Seed-owned marker:** rows whose slug is in the seed set (the view passes
    `seed_slugs = {a["slug"] for a in help_content.ARTICLES}` into the editor context; the row template checks
    `{% if f.instance.slug in seed_slugs %}`) render a muted one-line hint at the **top of the row card**,
    above the title field: *"Seed-owned — edit `help_content.py` instead; changes here are overwritten on
    release."* Read-only text, no behavior change — the honest story for the locked "seeds refresh in place"
    decision, so an admin never loses a body edit *silently*.
  - The row's existing empty-form `<template>` gets the two new fields too, so "+ Add an article" rows carry
    them. Everything else on the tab (Delete buttons, +Add, "Save Articles") is already correct and unchanged.
- **States:** invalid formset saves re-render the editor with the **bound** formset, inline field errors, and
  the right tab active (§6 — a behavior fix over today's redirect-and-discard); the empty state per tab keeps
  its "No … yet. Add your first." line.
- **Mobile / themes:** the editor is admin-only but still hub-standard: stacked cards, form_field everywhere,
  no new inline color styles.

## 8. Search, registry, and flag wiring notes

- `help_page_enabled` remains the single gate; the `feature_flags` context processor needs no change. The
  sidebar "Help" link (both admin and member branches of `base.html`) is untouched.
- No emails, no notifications, no `SiteActivity` kinds in this spec (§9 of the checklist: n/a).

## 9. Screenshot pipeline (mandatory for every how-to step sequence)

**Storage: `static/help/<article-slug>/<NN-step-slug>.png`, committed.** Justification: the locked decision is
repo-versioned screenshots regenerated when the UI changes; `static/` (repo root, in `STATICFILES_DIRS`) means
WhiteNoise serves them on every environment with zero config, `collectstatic` ships them to prod, and the
Markdown `src` restriction (§5.2) gets one simple, auditable prefix. **Manifest-hashing implication:** Markdown
bodies are DB text, not templates — they can't use `{% static %}` and must reference the **unhashed** path
(`/static/help/…`). `CompressedManifestStaticFilesStorage` collects the original-named files alongside the
hashed copies, and WhiteNoise serves unhashed names with a short `max-age` (60s) instead of the far-future
immutable header. That's the behavior we want: regenerated screenshots replace files in place and go fresh
within a minute, no cache-busting rename dance. (Cost: screenshots re-download more often than hashed assets —
acceptable for help pages.)

**The per-article screenshot request list** — the format content authors emit, one entry per how-to step,
living beside the body in `membership/help_content.py`:

```python
class ShotSpec(TypedDict):
    file: str                 # "02-pick-your-guilds.png" → static/help/<article-slug>/02-pick-your-guilds.png
    page: str                 # URL name ("hub_guild_voting") or literal path ("/guilds/voting/")
    selector: str | None      # CSS selector to crop to — e.g. "[data-help-key='voting.rank-guilds']" once
                              #   Spec B lands, plain CSS until then. None = framed viewport shot.
    caption: str              # becomes the image's alt text in the article body
    as_role: str              # "member" | "guild_lead" | "instructor" | "admin" — whose UI to capture
    full_page: NotRequired[bool]   # default False
```

**Capture spec — `tests/e2e/help_screenshots_spec.py`:** borrows the CMS-screenshots harness wholesale.
`describe_help_screenshots` / `it_captures_every_help_step`:

1. Gated exactly like the sibling: `pytestmark = skipif(not os.environ.get("CAPTURE_HELP_SCREENSHOTS"))` so it
   never runs in the ordinary `pytest -m e2e` sweep.
2. Seeds demo data by reusing `_seed()` + `_seed_member_hub()` — factored out of `screenshots_spec.py` into
   `tests/e2e/screenshot_seed.py` (both specs import from it; no behavior change to the existing spec) — plus
   help-specific extras (an orientation slot, a vote, a guild with staff) as the shot lists demand.
3. Seeds **one account per role** (`member@`, `guild-lead@`, `instructor@`, `admin@` — with the matching
   `fog_role`/lead FK/instructor slug) and groups shots by `as_role` so each persona's screenshots show exactly
   what that persona sees — a member's how-to never captures admin-only edit buttons. Logs in with the real
   `login_via_code` fixture per persona (it already dismisses the welcome modal).
4. For each `ShotSpec`: `page.goto(live_server.url + resolved_path)`, settle, then
   `page.locator(selector).screenshot()` for element shots — padded by expanding the locator's
   `bounding_box()` by 16px and clipping the page screenshot to it, so crops keep visual context — or a
   framed `1200×800` viewport / `full_page` shot when `selector` is None. Writes straight into
   `static/help/<article-slug>/<file>`.
5. Writes a review contact sheet to `screenshots/help/index.html` (reuses the `_write_index` shape) and prints
   the captured/total count; asserts at least one capture so a broken harness fails loudly.

**Entry point — `scripts/capture-help-screenshots.sh`:** mirrors `capture-cms-screenshots.sh`
(`CAPTURE_HELP_SCREENSHOTS=1 pytest -m e2e tests/e2e/help_screenshots_spec.py`). Workflow when the UI changes:
run the script, eyeball the contact sheet, commit the changed PNGs with the copy change.

**Drift guard (runs in the normal suite, not e2e):** `tests/membership/help_content_spec.py` asserts, for every
seeded article: every `/static/help/…` image referenced in the body has a matching `ShotSpec` **and** the file
exists on disk; every `ShotSpec.file` is referenced by its body; filenames are `NN-slug.png`. A screenshot can't
silently 404 on a help page, and an orphaned PNG can't linger.

## 10. Content plan — the approved 29-article IA

### 10.1 Categories (seeded)

| slug | name | audience | sort |
|---|---|---|---|
| `getting-started` | Getting started | member | 10 |
| `guilds` | Guilds | member | 20 |
| `classes` | Taking classes | member | 30 |
| `events-community` | Events & community | member | 40 |
| `teaching` | Teaching | instructor | 50 |
| `running-a-guild` | Running a guild | guild_lead | 60 |
| `admin` | Admin | admin | 70 |

### 10.2 Articles (P1 = launch, P2 = fast follow)

| # | slug | category | P | Notes / related (explicit) |
|---|---|---|---|---|
| 1 | `welcome-to-fog` | getting-started | P1 | related: guilds-and-guild-pages, taking-a-class |
| 2 | `set-up-your-profile` | getting-started | P2 | |
| 3 | `notifications` | getting-started | P2 | **no Discord-connect claims** (GATED) |
| 4 | `guilds-and-guild-pages` | guilds | P1 | related: getting-oriented, guild-voting |
| 5 | `getting-oriented` | guilds | P1 | book a slot, custom time, cancel |
| 6 | `guild-voting` | guilds | P1 | **verified voting facts**: rolling ballot, editable anytime, calendar-month cycle, live standings, auto-snapshot on the month's first cron tick then auto-send results |
| 7 | `taking-a-class` | classes | P1 | find/register/pay, discount codes, waitlist, manage via email link |
| 8 | `community-calendar` | events-community | P1 | browse, filter, .ics subscribe, event pages |
| 9 | `propose-an-event` | events-community | P1 | review/approval flow caveat |
| 10 | `announcements` | events-community | P1 | where they appear + proposing (approval caveat) |
| 11 | `member-directory` | events-community | P1 | find people, visibility controls |
| 12 | `become-an-instructor` | teaching | P1 | **written to current behavior**: any active member can draft/submit; the instructor role (admin-granted) creates the public page; every class passes guild-lead/admin review. Flagged for revision by Spec D. |
| 13 | `run-your-class` | teaching | P1 | roster, waitlist, emailing registrants, CSV |
| 14 | `class-emails-and-discounts` | teaching | P2 | automated emails + discount codes (incl. approval step) |
| 15 | `your-instructor-page` | teaching | P2 | |
| 16 | `your-guild-page` | running-a-guild | P1 | overview, banner, gallery, FAQ, links, contact emails |
| 17 | `guild-staff-roles` | running-a-guild | P1 | **the warning**: every staff role = full lead authority (`membership/permissions.py`) |
| 18 | `running-orientations` | running-a-guild | P1 | hours, slots, responding, dashboard, marking complete |
| 19 | `guild-announcements` | running-a-guild | P1 | compose wizard, drafts, reviewing proposals |
| 20 | `guild-events-hours-notes` | running-a-guild | P1 | events, studio hours (never announced), meeting notes |
| 21 | `approving-classes` | running-a-guild | P1 | the guild-lead review queue |
| 22 | `members-and-invites` | admin | P1 | invite/resend/revoke, edit members, email aliases |
| 23 | `reviewing-classes-admin` | admin | P1 | approve, archive, registrations, refunds |
| 24 | `voting-admin` | admin | P1 | snapshots (manual + auto), results emails, settings |
| 25 | `site-settings` | admin | P2 | feature toggles |
| 26 | `notification-copy` | admin | P2 | copy CMS |
| 27 | `billing-admin` | admin | P2 | seeded `is_published=False` until `tab_payments_enabled` ships on (flip in seed data, re-run) |
| 28 | `floor-map-editor` | admin | P2 | |
| 29 | `editing-the-help-center` | admin | P2 | documents this very feature |
| 30 | `instructor-orientation` | *(unlisted — §10.6)* | — | **Reserved for Spec D**: the instructor orientation tutorial's companion article. Seeded by Spec D's build, not this one; the slug is reserved here so nothing else claims it. |

### 10.3 Old guides → new articles (`LEGACY_SLUG_MAP` in `help_content.py`)

**The map covers all 8 legacy slugs** — rewritten-in-place slugs are **identity-mapped** (key = value), so an
old `/help/#guilds-and-guild-pages` link still lands on the article page instead of a vanished landing anchor.

| old slug (raw material) | disposition |
|---|---|
| `guilds-and-guild-pages` | rewritten in place (identity-mapped → `guilds-and-guild-pages`) |
| `orientations` | **split**: member half → `getting-oriented`; lead half → `running-orientations` (map → `getting-oriented`) |
| `guild-voting` | rewritten in place (identity-mapped); admin facts move to `voting-admin` |
| `taking-a-class` | rewritten in place (identity-mapped) |
| `teaching-a-class` | **split**: `become-an-instructor` + `run-your-class` (map → `become-an-instructor`) |
| `the-community-calendar` | → `community-calendar` |
| `connecting-discord` | **retired** (GATED — Discord connect + slash commands undocumented until the prod bot is confirmed); map → `notifications` (P2; until it ships the landing filter drops the entry and the anchor lands on `/help/`, still not a dead end) |
| `notifications-and-your-settings` | → `notifications` (P2; same landing fallback until seeded) |

The seed command **unpublishes** any row whose slug is a retired legacy slug (never deletes — an admin-edited
body is preserved, just hidden). Slugs reused in place keep their rows and history.

### 10.4 Seeding — `seed_help_center`

`membership/management/commands/seed_help_center.py`, data in `membership/help_content.py`
(`CATEGORIES: list[dict]`, `ARTICLES: list[dict]` — one entry per article with
`slug / category / title / sort_order / related: list[slug] / body / screenshots: list[ShotSpec]` —
plus `LEGACY_SLUG_MAP` and `UNLISTED_SLUGS` (§10.6)). Behavior, mirroring `seed_wiki_articles` /
`seed_floor_geometry`:

- Idempotent: categories `update_or_create` on slug; articles `update_or_create` on `(page, slug)` — title,
  body, category, sort_order refreshed **in place** (locked decision: seeds refresh; correcting copy here and
  re-running is the workflow). `related_articles` set via `.set()` after all articles exist (two passes).
- `--dry-run` previews; end-of-run report ("Seeded N categories, M guides: X added, Y refreshed, Z retired.").
- Unpublishes retired legacy slugs (§10.3). Never touches Josh's `OrgInfoPage` blocks.
- `seed_wiki_articles.py` is deleted in the phase that lands this (its bodies were absorbed as raw material);
  release notes for ops: the deploy step becomes `manage.py seed_help_center`.

### 10.5 Content-author working agreement (for the content agent pointed at this later)

1. **Code-verified only.** Every claim traces to a route/model/permission in the inventory's key files
   (`hub/urls.py`, `hub/views.py`, `classes/…`, `membership/permissions.py`). If you can't point at the code,
   the sentence doesn't ship.
2. **ELI14 register.** Concise, plain, short sentences, second person, no filler, zero AI slop. Keep every
   fact and **every permission caveat** (e.g. proposals need approval; "every staff role = full authority";
   orientation requests aren't confirmed until a lead approves).
3. **Screenshots are mandatory for every how-to step sequence.** For each numbered step list, emit the §9
   request list — `(page, selector-or-help-key, caption)` per step — and reference each file from the body
   with the caption as alt text. No step sequence merges without its shots.
4. **GATED list is excluded.** No Tab/payments/storefront (until `tab_payments_enabled`), no Discord connect
   or slash commands (prod bot unconfirmed), no mobile/api surfaces, signage, SSO relay, or copy-review API.
5. **Anchors are help keys.** Section headings that a hover panel or tour will target get
   `{#anchor}` ids matching the registry (`core/help_registry.py`); add the registry entry in the same PR.
6. **External wiki boundary.** wiki.pastlives.space documents the physical space; these articles document the
   app. Link across, never duplicate.
7. **Voting + teaching facts** as pinned in §10.2 (articles 6, 12, 24) — do not soften or "improve" them.

### 10.6 Unlisted articles (`UNLISTED_SLUGS`)

Some articles must exist at a URL without appearing in the browsing surfaces — the first customer is Spec D's
`instructor-orientation` companion article, which the orientation tutorial deep-links into but which has no
home in the browsable IA. Mechanism:

- `help_content.UNLISTED_SLUGS: frozenset[str]` — initially `frozenset({"instructor-orientation"})`.
- Unlisted articles are **excluded from** `search()` (§5), the landing page (category counts, the "All guides"
  fallback), and category pages — but their canonical URL (`help_article`) resolves normally, and registry
  keys / tours may deep-link them.
- The §12 "matches §10" content-integrity assertion is **loosened to tolerate unlisted entries**: an article
  whose slug is in `UNLISTED_SLUGS` need not appear in the §10.2 category/priority tables, and the landing/
  category specs assert its absence rather than its presence.

## 11. Build order (phased; each phase ships green — full suite + ruff + mypy; every PR bumps VERSION per repo convention)

1. **Models + migration + editor.** `HelpCategory`, `WikiArticle` fields, migration; `HelpCategoryForm`/FormSet,
   `WikiArticleForm` field additions; Categories tab + Articles-tab row changes in `org_info_edit.html`;
   `help_categories_save`. `/help/` still renders exactly as today.
2. **Markdown help profile.** *First commit of this phase, before the `profile=` refactor touches
   `markdown.py`:* render a fixture corpus through the **current** `render_markdown` and commit the outputs as
   golden files — otherwise the §12 "member profile byte-identical" test is tautological (it would compare the
   refactored code to itself). Then `render_markdown(…, profile=…)`, `help_markdown` filter,
   `.pl-md--help img`/anchor CSS. No caller uses the help profile yet.
3. **Registry module.** `core/help_registry.py` with the TypedDict, `KEY_PATTERN`, helpers, and the initial
   (small) key set; format specs. Content-resolution specs activate in phase 5 and tolerate pending articles
   (§5.1) — entries for B/C's needs may land here before their articles ship.
4. **Pages + search.** New views/URLs/templates (landing rework, category, article, search), model logic
   (`search`, `snippet`, `related_for_display`, prev/next, `toc`, canonical redirect), legacy-anchor JS,
   `table_search` `action` param, remaining CSS. Ships against the existing 8 articles (uncategorized →
   landing shows the fallback state; the editor from phase 1 can categorize them immediately).
5. **Screenshot pipeline.** `tests/e2e/screenshot_seed.py` refactor, `help_screenshots_spec.py`,
   `capture-help-screenshots.sh`, the drift-guard spec (initially trivially green — no seeded shots yet).
6. **P1 content.** `help_content.py` (P1 bodies + shot lists + registry entries + `LEGACY_SLUG_MAP`),
   `seed_help_center`, captured + committed screenshots, legacy retirement, delete `seed_wiki_articles.py`.
   **This is the launch phase**: bump `plfog/version.py` VERSION and add the one member-facing CHANGELOG entry
   (e.g. "A real Help Center — every guide has its own page now, with pictures, search, and related guides").
7. **P2 content (fast follow).** Articles 2, 3, 14, 15, 25–29 with their shots; legacy map entries for
   `notifications` go live. Per the changelog rules, fold any member-visible polish into the existing entry if
   the release line hasn't shipped, else it's invisible intra-cycle work.

> Spec only — do not build until approved.

## 12. Testing (BDD `*_spec.py`, `describe_*`/`it_*` — remember `context_*` is NOT a collected prefix and would silently skip; coverage gate `fail_under = 98`, branch)

App test trees live under `tests/membership/` and `tests/hub/` (there is no `membership/spec/` or
`hub/spec/` in this repo); `core/spec/` exists and hosts the registry spec.

- `tests/membership/help_category_spec.py` (new) — slug auto-fill/dedupe/stability; audience choices;
  `sort_order` auto-assign (`max+10` on default-0 create; explicit values untouched);
  `with_published_counts` / `nonempty`; `__str__`.
- `tests/membership/wiki_article_spec.py` (extend) — `audience` property (category / uncategorized);
  `search` (**per-term AND**: "book orientation" matches an article containing both words in any field;
  single-field whole-string still matches; term missing everywhere → excluded; drafts + unlisted excluded;
  empty q → none); `search_snippet` (escaping — a body containing `<script>` never reaches the page
  unescaped; `<mark>` placement; title-only fallback); `lead_text`; `related_for_display` (explicit first,
  same-category fill, self-exclusion, <3 available, uncategorized → explicit-only); `next/previous_in_category`
  (ends, drafts skipped, uncategorized → None); `url_category_segment` + `get_absolute_url`; `toc` (levels,
  id-less headings skipped, empty).
- `tests/membership/markdown_spec.py` (extend) — help profile: local `/static/help/` img survives with
  src/alt/title; external / `data:` / `/media/` img dropped; heading `id` kept only when pattern-valid;
  internal link same-tab, external link fully hardened; scripts/styles/handlers still stripped; **member
  profile output byte-identical to the committed golden fixtures** — which are rendered and committed
  *before* the `profile=` refactor (§11 phase 2), so the test compares against pre-refactor truth, not
  itself; unknown profile raises.
- `core/spec/help_registry_spec.py` (new) — every key matches `KEY_PATTERN`; `short_text` ≤ 200 chars;
  `entry()` raises `KeyError` on unknowns; annotation-only entries (`article_slug=None`) are valid and
  `url_for()` returns `/help/` for them; (from phase 6) for entries with `article_slug` set **and** present
  in `help_content.ARTICLES`, the `anchor` appears as `{#anchor}` in that body — entries pointing at
  pending/unseeded articles are tolerated and `url_for()` degrades to `/help/` for them.
- `tests/membership/help_content_spec.py` (new) — seed-data integrity: category refs and related slugs exist;
  audience/category tables match §10 **for listed articles** (slugs in `UNLISTED_SLUGS` are exempt from the
  §10.2 tables and asserted absent from landing/category/search instead); every body image ↔ ShotSpec ↔
  file-on-disk (the §9 drift guard); `LEGACY_SLUG_MAP` has an entry for **all 8** legacy slugs (identity
  entries included) and its values are seeded slugs; no GATED slugs published.
- `tests/membership/management/seed_help_center_spec.py` (new) — creates then refreshes idempotently (counts,
  no duplicates); `--dry-run` writes nothing; retires legacy slugs by unpublishing (body preserved); related
  M2M set correctly across the two passes; report text. (`seed_wiki_articles_spec.py` retires with its
  command in phase 6.)
- `tests/hub/help_spec.py` (extend) — flag-off redirect on all five GET views; landing groups by audience in
  the §6 rank order (an admin-created category never splits a heading group), hides empty categories, shows
  the "All guides" fallback when uncategorized published articles exist and hides unlisted ones; legacy map
  filtered to live targets; category page (ordering, empty state, admin drafts, unlisted excluded, 404);
  article page (published renders with `pl-md--help`, TOC, related, prev/next; uncategorized renders with
  `/help/`-only breadcrumbs and search-card aside; unlisted resolves by URL; draft 404 for member / draft
  banner for admin; canonical 301 on stale segment; 404 unknown; `/help/more/` 404s); search (hits, per-term
  AND behavior end-to-end, snippet marked, empty-q prompt, no-results state); `help_categories_save` (admin
  gate; valid save + redirect `?tab=categories`; **invalid → 200 re-render with the bound formset, inline
  reserved-slug error, Categories tab active, submitted values preserved**); same bound-re-render assertions
  for the fixed `help_articles_save`; `WikiArticleForm` category/related round-trip incl. self-exclusion;
  seed-owned hint renders on seeded rows and not on admin-created rows.
- Template-state assertions ride the view specs (response content checks), per house practice.
- e2e: `help_screenshots_spec.py` is opt-in (`CAPTURE_HELP_SCREENSHOTS`) and never part of the default run;
  `tests/` are outside coverage (`omit`).
- No `skip` / `pragma: no cover` / `pragma: no mutate` without explicit approval.

## 13. Out of scope (the other specs)

- **Info View hover help** — `data-help-key` DOM targets, the `?` toggle, docked panel, registry-as-JSON
  endpoint: `2026-08-10-info-view-hover-help.md` (Spec B, consumes §5.1's contract).
- **Guided tours** — Driver.js vendoring, tour definitions, per-user completion state:
  `2026-08-10-guided-tours.md` (Spec C).
- **Instructor orientation + auto-unlock** — the tutorial, `teaching_member_required` gating change, backfill:
  `2026-08-10-instructor-orientation-unlock.md` (Spec D; article 12 gets revised there).
- Documenting anything on the GATED list (inventory) until its flag/bot ships.

## 14. Open / deferred

- **Search pagination + ranking** — deferred until the KB outgrows a single page of `icontains` results
  (~3× today's 29). The `search()` seam is where Postgres full-text would slot in later.
- **Article reordering UI** — `sort_order` stays hidden/seed-owned; a drag-reorder editor (à la
  `gallery_manager`) only if Josh actually asks.
- **Per-article feedback ("Was this helpful?")** — YAGNI until members exist who'd click it.
- **`selector` migration to `data-help-key`** — shot specs use plain CSS selectors until Spec B lands the
  attributes; swap opportunistically afterwards (pure data change, no machinery change).
- **HTMX live search** — explicitly rejected (locked); the `hx-boost` plumbing exists if this is ever revisited.
