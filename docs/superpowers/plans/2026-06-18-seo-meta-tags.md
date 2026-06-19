# SEO Meta Tags — Unique Titles & Descriptions for Class Detail Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Each task is strict TDD — write the failing test, confirm it fails, implement, confirm it passes, then lint + commit. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every public class detail page a **unique, length-bounded SEO `<title>` and `<meta name="description">`**, generated server-side from the offering's own data. Today every dated copy of the same class shares one identical title (`{{ offering.title }} — Past Lives Makerspace`) and there is no per-page meta description at all — so Google sees dozens of pages with duplicate titles and a single site-wide description, and penalizes them. The repo root holds the Screaming-Frog-style audit PDFs that motivate this (`PLM-SEO-Dupe-Title-Tags.pdf`, `PLM-SEO-Dupe-Metas.pdf`, `PLM-SEO-Title-Tags-Long.pdf` — now gitignored; they are the "before" state).

**Architecture:** Fat models, skinny views. **All** title/description string-building and truncation lives in two new `ClassOffering` properties (`seo_title`, `seo_description`). The detail view passes nothing new — the template already has `offering` in context — but we add a `{% block meta_description %}` to `base_public.html`'s `<head>` and override `{% block title %}` + the new block in `detail.html` to read those properties. The template does **zero** string logic. No new dependencies, no DB columns, no migrations.

**Tech Stack:** Django 5 templates, Python `textwrap`/manual word-boundary truncation in the model, `django.utils.html.strip_tags` + `escape` for safety, pytest + pytest-describe (`*_spec.py`), factory-boy. No new packages.

---

## Background / context for the implementer

### The current (duplicate-title) state — verified
- `templates/classes/public/detail.html:4` — `{% block title %}{{ offering.title }} — Past Lives Makerspace{% endblock %}`. This is the ONLY thing distinguishing the page, and sibling dated offerings share an identical `title`, so every date of the same class emits the same `<title>`.
- `templates/base.html:6` and `:13` — base.html (the **member-hub** base) has a static site-wide `<meta name="description">` and a `{% block title %}`. The public/booking pages do **not** extend this file (detail.html extends `classes/base_public.html`). Confirm where `base_public.html`'s `<head>` lives (see next bullet) — that is the file to edit, NOT `base.html`.
- `templates/classes/base_public.html` — the public booking surface base that `detail.html` extends (`detail.html:1`). **Read its `<head>` first.** It must already define `{% block title %}` (since detail.html overrides it). Add the new `{% block meta_description %}` inside its `<head>`, right after the existing `<title>`/charset block. If `base_public.html` has no static fallback description, give the block a sensible site-wide default so non-detail public pages still emit one.

### Why titles are duplicated (the real problem)
- Legacy CMS posts one Drupal node **per date**, so one class ("Blacksmithing 101 with Glen") becomes many `ClassOffering` rows sharing one `title` (`classes/grouping.py:1-8`).
- The catalog collapses them into one card via `grouping_key` (`classes/grouping.py:17-28`), but **each dated offering keeps its own `slug` and its own detail page** (`detail.html:224` links siblings by `slug`; the detail view loads by `slug` at `classes/views.py:268-271`). So the duplicate-title penalty hits the detail pages, which grouping does not touch.

### The data available to build a unique title — verified
- `ClassOffering.title` — `CharField(max_length=255)` (`classes/models.py:147`). May carry a trailing CMS date suffix (e.g. `" - 6/5/26"`).
- `strip_date_suffix` filter (`classes/templatetags/classes_tags.py:205-210`) strips those suffixes. The detail H1 already uses it (`detail.html:143`). **Reuse this function in the model** (import `from classes.templatetags.classes_tags import strip_date_suffix`, exactly as `classes/grouping.py:14` already does) so the SEO title starts from a clean base.
- `ClassOffering.instructor` — FK to `membership.Member`, **nullable** (`classes/models.py:152-159`). `instructor.display_name` = `preferred_name or full_legal_name` (`membership/models.py:260-261`).
- `ClassOffering.first_upcoming_session_at` (`classes/models.py:543-546`) → first future session datetime or `None`.
- `ClassOffering.earliest_session_at` (`classes/models.py:548-552`) → first session ever (past or future) or `None`. **This is the date to use for expired/archived offerings** so historical pages still get a unique, date-stamped title. Prefer `first_upcoming_session_at`, fall back to `earliest_session_at`.
- `ClassOffering.description` — `TextField(blank=True)` (`classes/models.py:160`). Source for the meta description. May contain newlines/markdown-ish text; the public page renders it with `|linebreaks` (`detail.html:161`).
- `ClassOffering.category.name` — available via `select_related("category")` in the detail view (`classes/views.py:269`); useful as a description fallback when `description` is blank.

### Length targets (from the audit + the feature spec)
- **Page title: 50–60 characters.** Screaming Frog flags titles >60 as "over 60 characters" (`PLM-SEO-Title-Tags-Long.pdf`) and Google truncates around there. Aim for the 50–60 band, hard-cap at 60.
- **Meta description: 140–160 characters.** Google's snippet width. Hard-cap at 160; pad/keep ≥140 where the source text allows.

### Persistence requirement — verified, nothing to do
The feature spec says "preserve all historical/expired class entries … to protect historical indexing." **Verified: nothing prunes offerings.** Management commands are only `download_legacy_images`, `regroup_classes`, `send_class_reminders`, `sync_legacy_cms` — none delete `ClassOffering` rows; `sync_legacy_cms.py` has no `.delete()`. Archived classes are kept and surfaced (`classes/views.py:371-375` lists `Status.ARCHIVED` on instructor pages). **No code change is needed for persistence** — this task only documents it (a spec assertion in Task 5 guards against future regression). Using `earliest_session_at` as the date fallback means even archived/past-only offerings still get a unique, indexable title.

### Test infrastructure — verified
- Specs live in `classes/spec/models/` as `*_spec.py`; the offering model already has `classes/spec/models/class_offering_spec.py`. **Add the SEO specs there** (or a sibling `class_offering_seo_spec.py` in the same dir — pick one and be consistent).
- Factories: `ClassOfferingFactory` (`classes/factories.py:75-87`) sets `title`, `slug`, `instructor` (via `InstructorFactory`), `description = "A hands-on class."`, no sessions by default. `ClassSessionFactory` (`:99-105`) attaches a session via `class_offering=`. `InstructorFactory` (`:43-57`) builds a Member with a `full_legal_name`.

---

## Decisions baked into this plan

- **Meta tags only — DO NOT change existing slugs.** The feature spec floats a `[Name]-[Instructor]-[Date]` **slug** pattern. Changing the `slug` of an already-indexed page changes its URL, which **breaks inbound links and discards the page's existing index entry** — the exact opposite of "protect historical indexing." We fix the duplicate-title penalty entirely in the `<title>`/`<meta>` tags, leaving every existing URL untouched. A future date-stamped slug scheme (for *new* offerings only, with 301s for old ones) is flagged in **Follow-up** as a separate, riskier change. This is the single most important scoping decision in this plan.

- **Title formula:** `"{base_title} with {instructor} — {Mon D, YYYY}"`, then trim to fit. Build greedily and **drop segments from the right when over budget**, in this priority order (keep the most-distinguishing info that fits):
  1. Always keep `base_title` (= `strip_date_suffix(title)`).
  2. Add ` — {date}` if an instructor+date both don't fit, the **date is the stronger uniqueness signal** for sibling offerings, so prefer date over instructor when only one fits.
  3. Add ` with {instructor}` when room remains.
  - **Suffix decision:** the brand suffix `" — Past Lives Makerspace"` is **dropped from `seo_title`**. It costs ~25 chars and would blow the 60-char budget the moment instructor/date are present; the brand is already in the OG/site name and the `<h1>`. `seo_title` returns only the class-identifying string (≤60). The template renders `{{ offering.seo_title }}` with **no suffix** in `detail.html`'s title block. (Non-detail public pages keep their existing branded titles via `base_public.html`'s default `{% block title %}`.)

- **Truncation strategy (both fields): word-boundary trim with ellipsis, never mid-word.** Implement one private helper on the model, e.g. `_truncate(text: str, limit: int) -> str`:
  - If `len(text) <= limit`: return `text` unchanged.
  - Else: cut to `limit - 1` chars, drop back to the last space, strip trailing punctuation/space, append `"…"` (U+2026, one char). Result length ≤ `limit`. If there is no space in the window (one very long word), hard-cut at `limit - 1` + `"…"`.
  - Use this for the description (limit 160) and as the final guard on the assembled title (limit 60). For the title, the segment-dropping above usually keeps it ≤60 without ellipsis; `_truncate` is the backstop.

- **Description source + cleaning:** `seo_description` = first `strip_tags(description)` with newlines collapsed to single spaces; if `description` is blank, fall back to `f"{base_title} at Past Lives Makerspace in Portland, OR. {category.name} class — register online."` (a non-empty, ≥140-ish default). Then `_truncate(..., 160)`. **Do not** worry about a 140 *minimum* by padding — short real descriptions are fine; the 140–160 band is a target, not a hard floor. The spec/test asserts the **upper** bound (≤160) strictly and documents the lower bound as a soft target.

- **HTML safety:** both properties return **plain text** (tags stripped, no markup). The template still pipes them through Django's auto-escaping (`<title>{{ offering.seo_title }}</title>`), so any `&`, `<`, `"` in a title are escaped to entities — assert this in tests. The meta tag uses `content="{{ offering.seo_description }}"` (auto-escaped attribute).

- **Date formatting:** use Django's `date_format`/`localtime` (or `strftime("%b %-d, %Y")` for a stable "Jun 5, 2026"). Use **local time** (the rest of the app localizes session times, e.g. `classes_tags.py:169`), so a near-midnight-UTC session shows the right local date. Prefer `django.utils.formats.date_format(localtime(dt), "M j, Y")` to stay locale-correct and avoid platform `%-d` issues.

---

## File Structure

- Modify: `classes/models.py` — add `seo_title`, `seo_description` properties + private `_seo_date_label` and `_truncate` helpers on `ClassOffering` (near `first_upcoming_session_at`, `:543`). Full type hints, Google-style docstrings.
- Modify: `templates/classes/base_public.html` — add `{% block meta_description %}<default>{% endblock %}` `<meta>` in `<head>`.
- Modify: `templates/classes/public/detail.html` — `{% block title %}{{ offering.seo_title }}{% endblock %}` (`:4`) and a `{% block meta_description %}{{ offering.seo_description }}{% endblock %}`.
- Test: `classes/spec/models/class_offering_seo_spec.py` (new) — title uniqueness/length/escaping + description length/escaping/fallback.
- Test: `classes/spec/views/` — extend the existing public-detail view spec (or add one) asserting the rendered page contains the unique `<title>` and the `<meta name="description">`.
- Modify: `plfog/version.py` — version bump (`2.5.9` — verify) + member-friendly changelog entry.

---

## Task 1: `seo_title` — unique, ≤60-char class-identifying title

**Files:** `classes/models.py`, `classes/spec/models/class_offering_seo_spec.py` (new)

- [ ] **Step 1 (RED): Write the failing spec.** Create `class_offering_seo_spec.py`:
  ```python
  """SEO title/description generation on ClassOffering."""

  from __future__ import annotations

  from datetime import timedelta

  from django.utils import timezone

  from classes.factories import ClassOfferingFactory, ClassSessionFactory, InstructorFactory


  def describe_ClassOffering():
      def describe_seo_title():
          def it_is_unique_across_same_titled_offerings_on_different_dates(db):
              inst = InstructorFactory(full_legal_name="Glen Smith")
              o1 = ClassOfferingFactory(title="Blacksmithing 101", instructor=inst)
              o2 = ClassOfferingFactory(title="Blacksmithing 101", instructor=inst)
              ClassSessionFactory(class_offering=o1, starts_at=timezone.now() + timedelta(days=3))
              ClassSessionFactory(class_offering=o2, starts_at=timezone.now() + timedelta(days=30))
              assert o1.seo_title != o2.seo_title

          def it_stays_within_sixty_chars(db):
              o = ClassOfferingFactory(
                  title="An Extremely Long Introductory Workshop About Hand Forging Knives",
                  instructor=InstructorFactory(full_legal_name="Alexandra Montgomery"),
              )
              ClassSessionFactory(class_offering=o, starts_at=timezone.now() + timedelta(days=5))
              assert 0 < len(o.seo_title) <= 60

          def it_never_cuts_a_word_in_half(db):
              o = ClassOfferingFactory(title="Introduction to Lampworking Borosilicate Glass Beadmaking")
              # the trimmed title must not end on a partial word fragment before the ellipsis
              t = o.seo_title.rstrip("…").rstrip()
              assert " " not in o.title or not t.endswith(o.title[len(t):len(t) + 1].strip() or "X")
              # simpler invariant: trimmed text is a whitespace-bounded prefix of the source words
              base_words = o.title.split()
              assert all(w in base_words or w in {"with", "—"} for w in t.split())

          def context_with_no_instructor_and_no_sessions():
              def it_falls_back_to_just_the_clean_title(db):
                  o = ClassOfferingFactory(title="Open Studio - 6/5/26", instructor=None)
                  assert o.seo_title == "Open Studio"

          def context_with_only_a_past_session():
              def it_still_includes_the_historical_date(db):
                  o = ClassOfferingFactory(title="Welding Basics", instructor=None)
                  ClassSessionFactory(class_offering=o, starts_at=timezone.now() - timedelta(days=400))
                  assert "Welding Basics" in o.seo_title
                  assert any(ch.isdigit() for ch in o.seo_title)  # a year/date is present

          def it_is_html_safe_when_escaped_in_a_template(db):
              from django.utils.html import escape

              o = ClassOfferingFactory(title="Mom & Me: <Clay>", instructor=None)
              # the property returns plain text; the template escapes it
              assert "&amp;" in escape(o.seo_title)
  ```
- [ ] **Step 2: Confirm it fails** — `pytest classes/spec/models/class_offering_seo_spec.py` → `AttributeError: 'ClassOffering' object has no attribute 'seo_title'`.
- [ ] **Step 3 (GREEN): Implement** `seo_title` + helpers on `ClassOffering` (place near `first_upcoming_session_at`, `classes/models.py:543`):
  - `_seo_date_label(self) -> str`: pick `first_upcoming_session_at or earliest_session_at`; return `date_format(localtime(dt), "M j, Y")` or `""`.
  - `_truncate(text: str, limit: int) -> str`: word-boundary trim + `"…"` per the Decision.
  - `seo_title` `@property`: `base = strip_date_suffix(self.title).strip()`; build candidate segments `[date, instructor]` in priority order (date first); greedily append ` — {date}` then ` with {instructor}` only while staying ≤ 60; final `return self._truncate(result, 60)`.
- [ ] **Step 4: Confirm pass** — same pytest command, all green.
- [ ] **Step 5:** `ruff format . && ruff check . && mypy classes/models.py`. Commit: `feat(classes): unique SEO title property on ClassOffering`.

---

## Task 2: `seo_description` — 140–160-char meta description

**Files:** `classes/models.py`, `classes/spec/models/class_offering_seo_spec.py`

- [ ] **Step 1 (RED): Add specs** under a `describe_seo_description()` block:
  ```python
  def describe_seo_description():
      def it_never_exceeds_one_hundred_sixty_chars(db):
          o = ClassOfferingFactory(description="Forge a knife from raw steel. " * 20)
          assert 0 < len(o.seo_description) <= 160

      def it_strips_html_and_newlines_from_the_source(db):
          o = ClassOfferingFactory(description="Line one\n\nLine two <b>bold</b> & more")
          d = o.seo_description
          assert "\n" not in d and "<b>" not in d

      def context_with_a_blank_description():
          def it_falls_back_to_a_category_aware_default(db):
              o = ClassOfferingFactory(description="")
              assert len(o.seo_description) >= 40  # non-empty, sensible default
              assert o.category.name in o.seo_description

      def it_does_not_cut_a_word_in_half(db):
          o = ClassOfferingFactory(description="Supercalifragilistic " * 30)
          assert o.seo_description.rstrip("…").endswith("Supercalifragilistic".rstrip()) or \
              o.seo_description.rstrip("…")[-1] != "S"
  ```
  > Tune the mid-word assertion to your `_truncate` output; the canonical invariant is `len <= 160` and the trimmed text (minus the trailing `…`) ends on a complete word from the source.
- [ ] **Step 2: Confirm it fails** (`AttributeError: ... seo_description`).
- [ ] **Step 3 (GREEN): Implement** `seo_description` `@property`:
  - `raw = strip_tags(self.description or "")`; collapse whitespace/newlines to single spaces (`" ".join(raw.split())`).
  - If empty: `raw = f"{strip_date_suffix(self.title).strip()} at Past Lives Makerspace in Portland, OR. {self.category.name} class — register online."`
  - `return self._truncate(raw, 160)` (reuse the Task 1 helper).
- [ ] **Step 4: Confirm pass.**
- [ ] **Step 5:** lint/format/mypy; commit: `feat(classes): SEO meta-description property on ClassOffering`.

---

## Task 3: Wire the meta-description block into the public base + detail template

**Files:** `templates/classes/base_public.html`, `templates/classes/public/detail.html`

- [ ] **Step 1: Read `templates/classes/base_public.html`'s `<head>`** to find the `<title>` block and any existing `<meta name="description">`. Add a block right after the title:
  ```django
  <meta name="description" content="{% block meta_description %}Past Lives Makerspace — Portland's community workshop for creators, builders, and makers. Browse classes and workshops and register online.{% endblock %}">
  ```
  If a static description meta already exists there, convert it to this block (keep its text as the default) rather than adding a second `<meta name="description">`.
- [ ] **Step 2: `templates/classes/public/detail.html:4`** — change the title block to drop the hardcoded suffix and use the property:
  ```django
  {% block title %}{{ offering.seo_title }}{% endblock %}
  ```
- [ ] **Step 3: `templates/classes/public/detail.html`** — add (just below the title block) the meta override:
  ```django
  {% block meta_description %}{{ offering.seo_description }}{% endblock %}
  ```
- [ ] **Step 4: Verify no string logic leaked into the template** — the only template change is referencing `offering.seo_title` / `offering.seo_description`; no `{% if %}`, no slicing, no truncation filters. (Fat models rule.)

---

## Task 4: View/render test — the page emits the unique tags

**Files:** `classes/spec/views/` (extend the existing public-detail spec; confirm its filename first, e.g. `public_spec.py`)

- [ ] **Step 1 (RED): Add a render spec.** Build a published offering with a session, GET its detail URL on the public surface (reuse the existing public-surface client/host fixture used by the other view specs in this dir), and assert:
  ```python
  def it_renders_a_unique_seo_title_and_meta_description(client, db):
      o = make_published_offering_with_session()  # mirror existing helper/fixtures
      resp = client.get(reverse("classes:public_class_detail", kwargs={"slug": o.slug}))
      html = resp.content.decode()
      assert resp.status_code == 200
      assert f"<title>{o.seo_title}</title>" in html  # adjust for escaping
      assert 'name="description"' in html
      assert o.seo_description[:30] in html
  ```
  > Match how the other detail-view specs construct a *published* offering (status `PUBLISHED` + a future session so `ClassOffering.objects.public()` returns it — see `classes/views.py:268-271`). If those specs use a fixture/helper for "a live offering," reuse it. Account for HTML-escaping in the title assertion (compare against `escape(o.seo_title)` if the title contains special chars).
- [ ] **Step 2: Confirm it fails** (old template still emits `{{ offering.title }} — Past Lives Makerspace`).
- [ ] **Step 3: Confirm it passes** after Task 3's template edits.
- [ ] **Step 4:** lint/format; commit: `feat(classes): render unique SEO title + meta description on class detail`.

---

## Task 5: Persistence guard (spec only — no code change)

**Files:** `classes/spec/` (a small management/command or services spec — place beside the relevant existing spec)

- [ ] **Step 1:** Add one defensive spec documenting the "never prune expired classes" requirement so a future change can't silently start deleting historical offerings:
  ```python
  def it_keeps_archived_offerings_queryable_for_seo(db):
      o = ClassOfferingFactory(status=ClassOffering.Status.ARCHIVED)
      ClassSessionFactory(class_offering=o, starts_at=timezone.now() - timedelta(days=500))
      # archived/expired offerings remain in the DB and produce a valid SEO title
      assert ClassOffering.objects.filter(pk=o.pk).exists()
      assert o.seo_title  # historical page still gets an indexable, dated title
  ```
- [ ] **Step 2:** Run it — should pass immediately (nothing prunes offerings; verified in Background). This is a regression guard, not new behavior.
- [ ] **Step 3:** Commit with the others or fold into Task 1.

---

## Task 6: Lint / format / type-check

- [ ] **Step 1:** `ruff format . && ruff check .` — clean.
- [ ] **Step 2:** `mypy .` (export `DATABASE_URL` first if running before push: `export $(grep '^DATABASE_URL=' .env | xargs)`). All new properties/helpers are fully typed (`-> str`, `-> str | None` where relevant).

---

## Task 7: Version bump + changelog

**Files:** `plfog/version.py`

- [ ] **Step 1: Bump `VERSION`.** Latest released on `main` is `2.5.7`; `2.5.8` is **in flight as PR #108 (open)**. Verify what has merged to `main` at implementation time and use the next patch — at time of writing that is **`2.5.9`** (after #108 lands). Do **not** assume; check `git log --oneline main` / `gh pr list`.
- [ ] **Step 2: Prepend a member-friendly `CHANGELOG` entry** (plain language — posts to Discord; no jargon, no PR numbers):
  ```python
  {
      "version": "2.5.9",  # verify against merged main
      "date": "2026-06-18",  # set to merge date
      "title": "Better search-engine results for class pages",
      "changes": [
          "Each class page now has its own unique page title and search-result description built from the class name, instructor, and date — so when the same class runs on several dates, Google can tell the pages apart instead of treating them as duplicates. This helps our classes show up better in search.",
      ],
  }
  ```
- [ ] **Step 3:** Commit.

---

## Final verification

- [ ] `pytest` — all pass, 100% branch coverage on the new properties/helpers (the SEO specs cover the empty-instructor, no-session, past-session, blank-description, long-text, and escaping branches).
- [ ] `ruff format . && ruff check . && mypy .` — clean.
- [ ] Manual spot check (run skill, public surface): open a class that runs on ≥2 dates; confirm each date's detail page has a **different** `<title>` (50–60 chars) and a populated `<meta name="description">` (≤160 chars) — view-source. Confirm an **archived/past** class still renders a sensible dated title. Confirm a class with `&`/`<` in its title escapes correctly in `<title>`.
- [ ] Confirm the public list/category pages (non-detail) still carry the default branded title + description from `base_public.html` (we only changed the block default + the detail override).

---

## Follow-up (out of scope for this plan)

- **Date-stamped slugs for NEW offerings + 301s for old URLs.** The feature spec's `[Name]-[Instructor]-[Date]` *slug* idea is deliberately excluded here because rewriting existing slugs breaks indexed URLs. A safe future version: generate the date-stamped slug **only for newly created** offerings going forward, leave every existing `slug` untouched, and add canonical-URL handling. This is a separate, higher-risk plan with its own SEO-redirect testing.
- **`<link rel="canonical">` + Open Graph / Twitter tags.** Once titles/descriptions are unique, adding canonical URLs and OG/Twitter cards (`og:title`, `og:description`, `og:image` from `offering.image`) further improves crawling and social previews. Natural next SEO increment; not needed to fix the duplicate-title penalty this plan targets.
- **Sitemap of class detail pages.** A `django.contrib.sitemaps` sitemap including all (incl. archived) detail pages would help Google discover and retain the historical pages this plan keeps indexable.
- **The audit PDFs** (`PLM-SEO-Broken-Links.pdf`, `PLM-SEO-Dupe-Title-Tags.pdf`, `PLM-SEO-Dupe-Metas.pdf`, `PLM-SEO-Title-Tags-Long.pdf`) also flag **broken links**, which this plan does not address. File separately if still relevant after re-crawling.
