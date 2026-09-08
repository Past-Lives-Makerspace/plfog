# Member Wiki — Core (Spec A) — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build. Phased as **four sequential PRs**, each shipping green.
**Date:** 2026-09-07
**Surface:** FOG hub — a new `/wiki/` section that takes over the existing "Wiki" sidebar slot, plus a
top-level `/m/<code>/` QR route and a printable sticker sheet.
**Related:**
- `2026-09-07-member-wiki-brief.md` — **the shared brief and locked decisions. Binding. Read it first.**
- `2026-09-07-member-wiki-guild-tab.md` (spec B) — guild Wiki tab, Verify, Wanted pages, overdue list,
  failed-search panel, monthly lead digest. Depends on this spec's models.
- `2026-09-07-member-wiki-moderation.md` (spec D) — Report a problem, review queue, official notes,
  archive UI, revert UI, notification triggers. Depends on this spec's models.
- `2026-09-07-governance-doc-mirror.md` (spec E) — read-only mirror of PLM's `org/` markdown.
- `2026-09-03-equipment-reservations.md` — shipped; `Equipment` is the source of the locked Official block
  and the seed input.

---

## 1. Summary

Any active member can write and edit pages in a wiki that lives inside FOG, on a phone, in a noisy shop,
without asking anyone's permission first. They find a machine by scanning the sticker on it, read the four
facts they came for in the first screen, add a photo of the correct blade in thirty seconds, and tap "Still
accurate" when a page matches what is in front of them. Guild staff, orienters, and admins get extra
powers on top of that — verify, archive, revert — never a gate in front of it. Every save is kept, nothing
a member types is ever discarded, and the page always says plainly whether it is official policy or a
member's best understanding.

This spec builds the foundation: the `WikiPage` store and its children, the permission filters, a new
sanitizer profile, the read / write / search-and-browse surfaces, revisions, attachments, drafts,
autosave and draft resume, the Equipment stub seeding that prevents an empty wiki on day one, and the QR
short links.

**Read the brief's §9 first.** It is binding and it moved several objects an earlier draft of this spec
claimed. In short: **A does not ship** the advisory lock, the conflict save, a Safety toggle, a
needs-review banner, `can_moderate_wiki`, `verified_role_label`, or `verified_note`. **A does ship**
`WikiRevision.kind` (including the `CONFLICT_DRAFT` value only D writes), `WikiRevision.author` with
`related_name="wiki_revisions"`, all six wiki `SiteActivity.Kind` values, `can_verify_wiki_page`, a real
search surface with `?guild=` / `?kind=` / `?stale=` / `?source=` and a browse list for an empty query,
`?confirm=1` and `?wanted=<pk>`, and a card partial with a `snippet` parameter and an `actions_partial`
slot. §7's table is the full handoff; every section below says which spec owns what it touches.

### Locked decisions (from the brief — not re-litigated here)

The brief's §4 table and its "Explicitly NO" list are binding. The ones this spec implements directly:

| Decision | Choice |
|---|---|
| Store | A new `WikiPage` model in `membership/models.py`. `WikiArticle` / Help Center untouched. |
| Namespace | One page store, one URL space, one search index. Scope is a `guild` FK, not a separate store. |
| Filing | Exactly two required fields — Scope and Kind — **both pre-filled from context**. No folders, no hierarchy, no tags. |
| Kinds | Six: `MACHINE`, `HOWTO`, `MATERIAL`, `PROJECT`, `GUILD_INFO`, `REFERENCE`. |
| Who may edit | Any active member, live immediately, no approval queue. |
| Who may verify | Guild lead, any guild staff role (orienters included), plus admins/officers. |
| Official pages | Admins/officers only. Members get **no edit affordance at all** — not a disabled button. |
| Editing a verified page | A non-staff edit drops it to Community with a recorded reason. A staff edit keeps verification and resets the clock. |
| Deletion | Members cannot delete. Admins archive (URL keeps working). Hard delete is a shell command only. |
| Revisions | Every save writes a `WikiRevision`. No member-facing diff viewer. |
| Comments | None. |
| Editor | One editor: the existing Quill setup. No markdown-vs-rich-text choice for members. |
| Sanitizer | A **new `wiki` profile**; never loosen `member` / `help` / `sanitize_page_submission`. |
| Search | Extend the existing `icontains` approach. No Postgres FTS. |
| Sidebar | In-app `/wiki/` takes the "Wiki" slot; the external MediaWiki demotes to a link on the wiki home. |
| Attachments | Belong to a page. No standalone file browser. A one-line label is **required**. |
| Public access | None. `@login_required` like the rest of the hub. |

### Decisions this spec makes that the brief left open

Each is called out again at the point it applies.

| # | Question the brief left open | Call, and why |
|---|---|---|
| D1 | Quill emits no heading ids, so `WikiArticle.toc()` is empty for editor bodies — how does the chip-row TOC work? | **Inject heading ids at render, not at save** (`render_wiki_content` slugifies `h2`/`h3` text and dedupes). A saved id goes stale the moment a member renames a heading; computing it in the one function that also produces the body HTML makes anchor drift impossible. §5.6. |
| D2 | How does the seeder detect a member-edited body? | **A nullable `body_edited_at`**, stamped by `apply_edit()` and never by the seeder. A revision count is wrong (the seeder itself writes one, and a revert changes it); an `is_seed_owned` flag answers "who created it", not "has a human touched the body". §5.11. |
| D3 | What shape is `WikiDraft`? | **One row per (page, author) for an edit, one per (author, kind) for a new page**, two partial unique constraints, facts held as JSON. A draft is a scratch buffer, not a queryable store. §4.6. |
| D4 | The editor opens and this member already has a draft — what happens? | **A resume banner, defaulting to the saved page.** "You have unsaved changes from 12 minutes ago. [Use my draft] / [Start from the saved page]". Silently loading the draft would let a stale tab quietly overwrite somebody else's finished edit; silently ignoring it throws away the text the autosave promised to keep. Choosing the saved page **deletes** the draft, so the question is asked once. §5.10. |
| D5 | Who may tap "Still accurate"? | **Any active member.** Freshness is "someone stood here and it still matched reality," which any member can attest; authority is a different claim and stays with staff. The byline names who confirmed. §5.2. |
| D6 | Does a quick tip drop a verified page to Community? | **Yes** — a tip is unreviewed text on the page, and the locked rule admits no exception. A quick **photo** does not (it writes an attachment, not the body). That asymmetry is deliberate: it makes the phone-first affordance the cheap one. §5.3, §6.7, §6.8. |
| D7 | How is reordering done for Quick Answers and attachments with gloves on? | **Reuse the Slideshow Slides editor's delegated reorder** (`templates/hub/admin/site_settings.html`, the `slide-rows` reorder handler): a drag grip on desktop, real ↑/↓ buttons on touch, both rewriting hidden `sort_order` inputs. Already shipped, already glove-safe. §6.6. |
| D8 | Where does the changelog entry go across four PRs? | **A1–A4 bump `VERSION` with no entry.** The wiki round is A + B + D + E and brief §9.1 puts the single curated entry in the **last PR of the whole round**, not the last PR of A. Re-stamping re-posts to Discord, so an entry at A4 would announce the wiki again at every later merge. §8. |
| D9 | What does `/wiki/search/` do when `q` is empty? | **It browses, it does not blank.** An empty `q` with any of `?guild=`, `?kind=`, `?stale=`, `?source=` set renders the filtered list; an empty `q` with no filters renders everything visible, newest first. `none()` on an empty `q` would dead-end three of B's "See all" links and return zero policies for E's Policies search box. §5.7, §6.2. |
| D10 | The seeder runs at A4, but members can write from A3 — what if a member already wrote "Table Saw"? | **The seeder adopts, it never duplicates and never raises.** An existing page whose `equipment` is null and whose slug matches the tool's slugified name is claimed: the seeder sets `equipment` (and `guild` when blank) and leaves every word of content alone. Creating a twin splits the knowledge; raising turns one member's good page into a broken deploy job. §5.11. |
| D11 | Brief §2 says one search box spans all three stores — does A fold in the Help Center? | **Yes, at A2.** `?source=` takes `wiki` / `help` / `policies`, results render in labelled groups with a source chip, and the Help Center group is the existing `WikiArticleQuerySet.search()` behind `help_page_enabled`. E adds the `policies` group with no change to A's view. Two stores in the box on day one is what makes the third free. §5.7. |
| D12 | "Scope" and "Kind" are model field names — are they the visible labels? | **No.** The form labels read "Who is this for?" and "What kind of page is this?", because a member filing their first page is answering a question, not populating a column. The model field names and the `Kind` choice labels are unchanged. §6.5. |

---

## 2. What already exists (reuse, don't reinvent)

**Cited by symbol name, not by line number.** Every citation in this document names a file plus a symbol
you can grep. An earlier draft cited line numbers and roughly a dozen of them had drifted by 2 to 55 lines
inside a single day — a build following a stale number edits the wrong function. Symbols were re-verified
in the working tree on **2026-09-07**.

| Need | Existing thing (grep this symbol) | File |
|---|---|---|
| Slug idiom (fill once, dedupe, never change) | `WikiArticle.save()` / `HelpCategory.save()` | `membership/models.py` |
| Reserved-slug guard on a form | `RESERVED_HELP_SLUGS` + `HelpCategoryForm.clean_slug` | `hub/forms.py` |
| Search queryset to copy (terms AND'd, `icontains`) | `WikiArticleQuerySet.search()` | `membership/models.py` |
| Highlighted search snippet, escape-before-mark | `WikiArticle.search_snippet()` | `membership/models.py` |
| Flatten a dual-mode body to plain text | `_source_to_text()` / `_markdown_to_text()` | `membership/models.py` |
| TOC from rendered body | `WikiArticle.toc()` + `_HELP_TOC_HEADING_RE` | `membership/models.py` |
| Related-pages picker with automatic fill | `WikiArticle.related_for_display()` | `membership/models.py` |
| Markdown → safe HTML, profile seam | `render_markdown(source, *, profile=…)` | `membership/markdown.py` |
| Dual-mode render + form-save seam | `render_page_content` / `looks_like_html` / `sanitize_page_submission` | `membership/markdown.py` |
| Quill widget with a per-field markdown profile | `PageContentEditorWidget(markdown_profile=…)` | `core/widgets.py` |
| Quill toolbar variants + clone-safe delegated init | `TOOLBARS`, `window.plRteInitAll()` | `static/js/rich-editor-init.js` |
| Editor asset include | `_components/rich_editor_assets.html` | `templates/_components/` |
| Permission filters, `view_as`-aware | `can_edit_guild`, `is_effective_staff`, `_editing_member`, `editable_meeting_scopes` | `membership/permissions.py` |
| Admin gate in a view | `_require_admin` / `_viewing_as_admin` | `hub/views.py` |
| Guild staff roles (co-lead / secretary / treasurer / **orienter**) | `GuildStaffMembership.Role` | `membership/models.py` |
| Lead + staff fan-out | `Guild.leadership_members()`, `Guild.is_staffed_by()` | `membership/models.py` |
| Equipment (slug, guild, photo, required orientation, access state) | `Equipment`, `Equipment.AccessState`, `Equipment.access_state()`, `Equipment.is_run_by()` | `membership/models.py` |
| File **XOR** link attachment + CheckConstraint | `MeetingAttachment` + `ck_meetingattachment_file_xor_url` | `membership/models.py` |
| Document upload allowlist + size cap | `validate_document`, `ALLOWED_DOCUMENT_EXTENSIONS`, `validate_image_size` | `core/validators.py` |
| Image normalize / downscale on save | `normalize_field_if_uploaded(instance, field_name, max_long_edge)` | `core/images.py` |
| Orphan cleanup when a file is replaced | `delete_orphan_on_replace(instance, field_name)` | `core/files.py` |
| AJAX instant-save image upload (the contract to copy) | `guild_image_upload` + its three siblings | `hub/views.py` |
| Drop zone markup + `drag-hover` state | `.cls-image-upload-zone` / `.cls-image-upload-label` / `.cls-image-upload-hint` | `static/css/components.css` |
| **Delegated** drop-zone + reorder handlers, clone-safe | the Slideshow Slides editor's `slide-rows` handlers | `templates/hub/admin/site_settings.html` |
| List-editor pattern (`extra=0` + `<template>` clone + real Delete) | the FAQ and Links editors | `templates/hub/guild_edit.html` |
| Field-at-a-time autosave: 204 / 400 / 422, savestate pill, debounce | `_autosave()`; the workspace's `save()` + `onResponseError()` | `hub/meeting_views.py`; `templates/hub/meeting_workspace.html` |
| Draft model idiom (per-author, resumable) | `AnnouncementDraft` + `AnnouncementDraftManager.for_user` | `membership/models.py` |
| QR SVG/PNG rendering (segno, scalable) | `qr_svg()` / `qr_png_bytes()` | `membership/qr.py` |
| Reusable Share & Print QR card (`qr_svg`, `share_url`, `svg_url`, `png_url`, `title`, `hint`) | `components/qr_share_card.html` | `templates/components/` |
| QR download view idiom (`?fmt=svg|png`, editor-gated) | `guild_qr_download` | `hub/views.py` |
| Short vanity redirect that a printed code encodes | `guild_vanity_redirect` at `/g/<slug>/` | `core/views.py`, `core/urls.py` |
| Print-optimized standalone sheet + Print button | `guild_flyer.html` + `guild-flyer.css`'s `@page` rule | `templates/hub/`, `static/css/` |
| In-page print stylesheet (hides sidebar/topbar) | the meeting-workspace `@media print` block | `static/css/hub.css` |
| Feature flag → sidebar + view gate | `SiteConfiguration.help_page_enabled` / `equipment_page_enabled`; `equipment_feature_required` | `core/models.py`; `hub/equipment_views.py` |
| Flags exposed to every template | the `feature_flags` context processor | `core/context_processors.py` (registered in `plfog/settings.py`) |
| Singleton loader | `SiteConfiguration.load()` | `core/models.py` |
| Audit feed | `SiteActivity.log(kind, *, actor=, target=, email_log=, payload=)` | `core/models.py` |
| Notification spine (B/D use it; A does not) | `emit()` + `EventType` registry | `core/events/emit.py`, `core/events/registry.py` |
| Toast + extra HX-Trigger events, merge-safe | `trigger_toast(response, message, toast_type)`, `trigger_client_event(response, event_name, payload)` | `hub/toast.py` |
| Row overflow ("kebab") menu — `menu_include` + `menu_label` | `components/row_actions.html` | `templates/components/` |
| Tab strip | `.pl-tabs` + `.vote-tab` / `.vote-tab--active` + Alpine `x-data="{ section: … }"` | `static/css/hub.css`; `templates/hub/guild_edit.html` |
| Pill family (documented in-family extension rule) | `.hub-pill` + `--ok/--warn/--primary/--neutral/--danger` | `static/css/hub.css` |
| The shipped amber pair (background + text, with a light override) | `.pl-confirm-warn` | `static/css/components.css` |
| Rendered-body typography base | `.pl-md` | `static/css/hub.css` |
| Two-column page + aside layout | `.pl-guild-grid` (1 col → `1fr 340px` at 1024px) | `static/css/hub.css` |
| Page header with an action button | `components/page_header.html` | `templates/components/` |
| Search box / pagination | `components/table_search.html`, `components/table_pagination.html`, `prepare_table()` | `templates/components/`, `classes/table.py` |
| Modals / confirms / fields / toggles | `components/modal.html`, `confirm_modal.html`, `form_field.html`, `toggle.html` | `templates/components/` |
| Seed command shape (module data, `update_or_create`, `--dry-run`, report, `CommandError`) | `seed_help_center` | `membership/management/commands/seed_help_center.py` |
| Seed content module shape | `CATEGORIES` / `ARTICLES` | `membership/help_content.py` |
| New views module precedent | — | `hub/meeting_views.py`, `hub/equipment_views.py` |
| Body boosted, Alpine re-init on settle | `hx-boost="true"` + `Alpine.initTree` on `htmx:afterSettle` | `templates/hub/base.html` |

### Corrections to the brief's §6 reuse map, and to claims an earlier draft of this spec made

All verified in the working tree on 2026-09-07. The build must not trust the originals.

1. **`seed_wiki_articles` does not exist.** It was deleted when the Help Center landed; `seed_help_center`
   replaced it and its docstring records the replacement. The pattern to copy is `seed_help_center` only.
2. **`MeetingAttachment` has no `uploaded_by` field**, and its `label` is `blank=True` (optional). Wiki
   attachments need both an uploader (the brief's §5.5 attachment card shows one) and a **required** label
   (the locked rule). Both are net-new on `WikiAttachment`.
3. **The `MeetingAttachment` editor is not a formset list editor** — it is a read-only row list plus a
   modal add form plus an HTMX per-row delete. The formset list editor to copy is the FAQ/Links pair in
   `guild_edit.html` (and `GuildMeetingNoteAttachmentFormSet` for a file-bearing example).
4. **`WikiArticle.toc()` returns `[]` for every rich-editor body.** `_HELP_TOC_HEADING_RE` only matches
   headings that already carry an `id`, and the Quill page profile emits none. A wiki whose members write
   in Quill therefore has no TOC at all unless ids are generated — see D1 / §5.6.
5. **`WikiArticleQuerySet.search()` matches raw `body`.** For a Markdown body that is fine; for a Quill
   HTML body an `icontains` search for "strong" or "href" matches markup. The wiki stores a flattened
   `search_text` column instead — see §4.1.
6. **`--hub-warn` is not a token.** Zero occurrences in `static/css/`. The `.hub-pill--warn` *modifier*
   exists, but there is no amber variable. Amber in this codebase is a literal hex pair plus a light-theme
   override, and the shipped one to copy is `.pl-confirm-warn`: `background: rgba(251, 191, 36, 0.12)` /
   `color: #fbbf24`, with `[data-theme="light"]` → `rgba(180, 120, 10, 0.1)` / `#8a5b06`. Any
   `background: var(--hub-warn)` renders transparent in both themes. **This is why this spec does not carry
   a blanket "never a hardcoded hex" rule** — the amber banners are literal hex by design, in both themes.
7. **`PageContentEditorWidget` hardcodes its toolbar.** `get_context` sets
   `context["widget"]["toolbar"] = "page"` unconditionally, independent of `markdown_profile`. And
   `rich-editor-init.js` builds Quill as `modules: { toolbar: TOOLBARS[...] }` — a bare format array with
   **no `handlers` seam**. A `wiki` toolbar carrying an image button therefore needs an edit to **both**
   files, and both appear in §3's file map (§5.6).
8. **`components/form_field.html` emits `.pl-form-group`, not `.hub-form-group`.** Both are defined and
   both style `input` / `select` / `textarea` from the theme's input tokens, so both satisfy FRONTEND.md
   Rule 13 — but a template that writes `.hub-form-group` around a `form_field.html` include is describing
   markup that does not exist. This spec says `.pl-form-group` for anything rendered through the component
   and `.hub-form-group` only for a control wrapped by hand.
9. **`components/confirm_modal.html`'s default is a plain full-page POST.** Its HTMX mode is opt-in via
   `confirm_hx_post` (+ optional `confirm_hx_target`, `confirm_hx_vals`); `hx-swap` is hardcoded to
   `outerHTML` when a target is given. A 204 therefore cannot power "Still accurate" — see §6.9.
10. **`get_object_or_404` renders the site-wide `templates/404.html`**, whose body reads "We couldn't find
    that page." and offers "Browse Past Lives classes". No view in this repo renders its own 404 body
    today. Every wiki dead end in this spec is `render(..., status=404)` against a wiki template (§6.3,
    §6.12).
11. **There is no `unreferenced_files` management command.** `core/files.delete_if_unreferenced` exists and
    runs on replace only; nothing sweeps orphans. An earlier draft of this spec claimed a sweeper — see §10.

### Genuine gaps to build

Revision history (no history library, and no content model in the repo carries `created_by` /
`updated_by`), member-level write permissions, an attachment model with a required label and an uploader,
a per-author draft/autosave store for a body, a short-code QR redirect, a page-level print sheet, a
multi-store search surface, and the `wiki` sanitizer profile. Everything else in this spec is assembly.

The brief notes a breadcrumb / tabs / empty-state component extraction as "a reasonable side-quest for
spec A." **Declined.** Confirmed today: there is no `.pl-breadcrumb`, no tab component, and no empty-state
class; extracting three cross-cutting components while landing a store this size is how a spec balloons.
The wiki uses the established inline idioms (`.pl-help-breadcrumbs`'s shape under a `pl-wp-` name,
`.pl-tabs` + `.vote-tab`, `hub-text-muted` + a scoped `__empty` modifier) and the extraction stays on
`DEFERRED.md`.

---

## 3. Where the code lives (and the URL map)

Same homes as Equipment and Meetings, inside the existing coverage/mypy source set.

```
membership/models.py                      # + WikiPage, WikiPageQuerySet, WikiPageFact, WikiRevision,
                                          #   WikiAttachment, WikiDraft, WikiError            (A1)
membership/wiki_starters.py               # NEW module data: the six starter templates + fact prompts (A1)
membership/markdown.py                    # + the `wiki` profile, sanitize_wiki_html,
                                          #   render_wiki_content, sanitize_wiki_submission   (A1)
membership/templatetags/membership_md.py  # + the `wiki_content` filter                       (A1)
membership/permissions.py                 # + can_edit_wiki_page, can_verify_wiki_page,
                                          #   visible_wiki_pages, editable_wiki_scopes        (A1)
                                          #   NOTE: can_moderate_wiki_page is spec D's (§5.5)
membership/qr.py                          # unchanged — reused as-is
membership/migrations/                    # A1: the five models + the SiteConfiguration flag
membership/management/commands/seed_wiki_machine_pages.py                                   # (A4)
core/models.py                            # + SiteConfiguration.wiki_enabled; wiki_link_enabled
                                          #   help_text repurposed; + ALL SIX SiteActivity.Kind
                                          #   wiki values (brief §9.1)                        (A1)
core/context_processors.py                # + wiki_enabled in feature_flags                   (A1)
core/validators.py                        # + validate_wiki_upload                            (A1)
core/widgets.py                           # MUST BE EDITED: get_context hardcodes
                                          #   context["widget"]["toolbar"] = "page"           (A3)
static/js/rich-editor-init.js             # MUST BE EDITED: + a `wiki` TOOLBARS entry AND a
                                          #   modules.toolbar {container, handlers} seam — the
                                          #   file has none today                             (A3)
templates/components/confirm_modal.html   # unchanged — its confirm_hx_post mode is already
                                          #   there; A only passes the params (§6.9)
hub/wiki_views.py                         # NEW module (precedent: hub/equipment_views.py)     (A2/A3/A4)
hub/forms.py                              # + RESERVED_WIKI_SLUGS, WikiPageCreateForm, WikiPageForm,
                                          #   WikiPageFactFormSet, WikiAttachmentFormSet,
                                          #   WikiQuickPhotoForm, WikiQuickTipForm             (A3)
                                          #   + wiki_enabled in SiteSettingsForm.Meta.fields   (A1)
hub/urls.py                               # /wiki/… routes + the top-level m/<code>/           (A2/A4)
plfog/settings.py                         # + "/wiki/" and "/m/" in MEMBER_ONLY_PATH_PREFIXES  (A2/A4)
templates/hub/base.html                   # the Wiki sidebar entry, BOTH nav branches          (A2)
templates/hub/wiki_home.html                                                                   (A2)
templates/hub/wiki_page.html                                                                   (A2)
templates/hub/wiki_search.html                                                                 (A2)
templates/hub/wiki_not_found.html         # bad slug — render(..., status=404), never
                                          #   get_object_or_404 (§2 correction 10)            (A2)
templates/hub/wiki_new.html               # starter chooser                                    (A3)
templates/hub/wiki_edit.html              # create + edit (one template, two modes)            (A3)
templates/hub/wiki_drafts.html                                                                 (A3)
templates/hub/wiki_sticker_sheet.html     # standalone print doc                               (A4)
templates/hub/wiki_qr_missing.html        # the scan dead-end catcher                          (A4)
templates/hub/partials/_wiki_card.html    # ONE card partial — home, search results, B's tab.
                                          #   Params: page, snippet (optional, replaces the
                                          #   lead line), source_chip (optional),
                                          #   actions_partial (optional slot)                  (A2)
templates/hub/partials/_wiki_facts.html   # Quick answers block (read)                         (A2)
templates/hub/partials/_wiki_official.html# the locked Equipment block                         (A2)
templates/hub/partials/_wiki_attachments.html                                                  (A2)
templates/hub/partials/_wiki_qr_share.html# "Share This Page" card wrapping qr_share_card       (A4)
templates/hub/partials/_wiki_fact_rows.html    # the Quick answers list editor                 (A3)
templates/hub/partials/_wiki_attach_rows.html  # the attachments list editor                   (A3)
templates/hub/partials/_wiki_status_oob.html   # the OOB fragment "Still accurate" returns     (A3)
static/css/hub.css                        # all pl-wp-* classes + the wiki @media print block
static/css/wiki-stickers.css              # NEW standalone print sheet (the guild-flyer idiom)  (A4)
tests/membership/  tests/hub/  tests/e2e/ # *_spec.py (root tests tree — membership/spec/ does NOT exist)
tests/membership/factories.py             # + WikiPageFactory, WikiPageFactFactory,
                                          #   WikiAttachmentFactory, WikiRevisionFactory, WikiDraftFactory
```

**Files this spec does NOT create, because another spec owns them** (listed so a builder reading A alone
does not invent a second one):

| Not A's | Owner | Why |
|---|---|---|
| `templates/hub/partials/_wiki_search_empty.html` | **B** | A's search view *includes* it for the zero-result screen; B writes its content. A ships no inline zero-result markup at all. |
| `_needs_review_banner.html` and everything that renders a `WikiReport` | **D** | A ships the `needs_review_since` / `needs_review_reason` columns and reads them; D renders the banner and maintains the columns. |
| `WikiEditLock`, `_lock_warning.html`, `conflict.html`, `apply_conflict_draft` | **D** | A's editor exposes the two include points D fills (§6.6); A's autosave calls `WikiEditLock.refresh(page, member)`. |
| `can_moderate_wiki_page(request, page)`, `moderatable_wiki_scopes(request)` | **D** | A consumes both (§5.5). |
| `verified_role_label`, `verified_note` | **B** | B's migration adds both. A's byline mock prints the role label; A must not add the column. |
| `_wiki_verify_control.html` | **B** | It is what A's `actions_partial` slot renders, with `compact=True` in a card row. |
| `WikiWantedPage` and `fulfil()` | **B** | A's create view calls `fulfil()` when `?wanted=<pk>` rode in (§5.7). |

### URL map (`hub/urls.py`)

Every route is `@login_required` **except `hub_wiki_qr`**, and every route is wrapped in
`wiki_feature_required` (the `equipment_feature_required` idiom — 404 while the flag is off, so a
disabled feature is fully dark and a crafted request learns nothing).

| Path | Name | Method / gate | PR |
|---|---|---|---|
| `wiki/` | `hub_wiki_home` | GET | A2 |
| `wiki/search/` | `hub_wiki_search` | GET · `q`, `guild`, `kind`, `stale`, `source`, `scope`, `page` | A2 |
| `wiki/p/<slug:slug>/` | `hub_wiki_page` | GET · reads `?confirm=1` | A2 |
| `wiki/new/` | `hub_wiki_new` | GET — starter chooser · carries `?guild=`, `?title=`, `?wanted=` | A3 |
| `wiki/new/<slug:kind>/` | `hub_wiki_create` | GET/POST · active member · same three params | A3 |
| `wiki/p/<slug:slug>/edit/` | `hub_wiki_edit` | GET/POST · `can_edit_wiki_page` · `?draft=use｜fresh` | A3 |
| `wiki/p/<slug:slug>/autosave/` | `hub_wiki_autosave` | POST · 204/400/422 · calls `WikiEditLock.refresh` | A3 |
| `wiki/p/<slug:slug>/confirm/` | `hub_wiki_confirm` | POST · any active member · **200 + OOB + toast** | A3 |
| `wiki/p/<slug:slug>/photo/` | `hub_wiki_quick_photo` | POST · **`can_edit_wiki_page`** | A3 |
| `wiki/p/<slug:slug>/tip/` | `hub_wiki_quick_tip` | POST · **`can_edit_wiki_page`** | A3 |
| `wiki/p/<slug:slug>/image/` | `hub_wiki_image_upload` | POST, AJAX · **`can_edit_wiki_page`** | A3 |
| `wiki/p/<slug:slug>/qr/` | `hub_wiki_qr_download` | GET · `?fmt=svg｜png` · `can_edit_wiki_page` | A4 |
| `wiki/drafts/` | `hub_wiki_drafts` | GET · own drafts only | A3 |
| `wiki/drafts/<int:pk>/discard/` | `hub_wiki_draft_discard` | POST · own draft only | A3 |
| `wiki/stickers/` | `hub_wiki_stickers` | GET · `can_moderate_wiki_page`-equivalent: `is_effective_staff` | A4 |
| `m/<str:code>/` | `hub_wiki_qr` | GET · **no login** | A4 |
| `wiki/p/<slug>/verify/` · `wanted/` · `review/` · `report/` · `history/` · `revert/<pk>/` · `conflict/<pk>/` | — | **spec B / D** | — |

The three write routes changed from "active member" to `can_edit_wiki_page` per brief §9.3: the tip route
writes the body through `apply_edit`, so gating it on membership alone puts two write affordances at the
bottom of an Official page, against the locked rule that Official pages give members no edit affordance at
all. `hub_wiki_stickers` is a staff-wide printable sheet with no page in scope, so it takes
`is_effective_staff` directly rather than `can_moderate_wiki_page`, which needs a page argument.

Two ordering notes the build must honor:

- `hub.urls` is included at `""` **before** `core.urls`, and `core.urls` ends in a `<slug:zone_slug>/`
  signage catch-all. `m/<str:code>/` is two segments so it could not collide anyway, but the include order
  is what guarantees it.
- The fixed `p/` segment means the wiki has **no catch-all routes at all** — the Help Center's
  "the slug catch-alls MUST stay below every fixed route" comment in `hub/urls.py` has no analogue here,
  which is exactly why the brief chose `p/`.

`/wiki/` and `/m/` join `MEMBER_ONLY_PATH_PREFIXES` in `plfog/settings.py` alongside `/guilds/`, so both
404 on the public book surface.

---

## 4. Data model

All in `membership/models.py`. Every field carries `help_text`; every model has a meaningful `__str__`;
`disallow_untyped_defs` is on for `membership.*`, so every method is fully annotated. Every index and
constraint name is **≤ 30 characters** (the E034 cap that bit PR #205) — run `manage.py check` after the
migration.

### 4.1 `WikiPage`

| Field | Type | Notes |
|---|---|---|
| `title` | `CharField(200)` | `help_text="What the page is called, e.g. 'SawStop Table Saw'."` |
| `slug` | `SlugField(220, unique=True, blank=True)` | **Fill once, never change.** Globally unique — one URL space (brief §4). Guarded against `RESERVED_WIKI_SLUGS`. |
| `kind` | `CharField(20, choices=Kind.choices)` | Six values. Drives the starter template and the review interval. |
| `guild` | `FK(Guild, null=True, blank=True, on_delete=SET_NULL, related_name="wiki_pages")` | The scope. Null = space-wide. `SET_NULL` not `PROTECT`: guilds soft-delete, so a hard delete is a deliberate act and a page losing its guild should become space-wide, not block the delete or vanish with it. |
| `equipment` | `FK(Equipment, null=True, blank=True, on_delete=SET_NULL, related_name="wiki_pages")` | Drives the locked Official block (§5.8) and the seeder. `SET_NULL`: retiring a tool must not take its accumulated member knowledge with it. |
| `status` | `CharField(20, choices=Status.choices, default=COMMUNITY)` | `COMMUNITY` / `GUILD_VERIFIED` / `OFFICIAL`. |
| `body` | `TextField(blank=True, default="")` | Dual-mode: Quill HTML or legacy Markdown, same sniff as the help columns. |
| `created_by` | `FK(Member, null=True, on_delete=SET_NULL, related_name="+")` | |
| `updated_by` | `FK(Member, null=True, on_delete=SET_NULL, related_name="+")` | |
| `created_at` / `updated_at` | `DateTimeField(auto_now_add=…)` / `(auto_now=…)` | |
| `body_edited_at` | `DateTimeField(null=True, blank=True)` | Stamped by `apply_edit()`. Null = the body is still exactly what the seeder wrote. **The seed-clobber guard (D2).** |
| `last_checked_at` | `DateTimeField(null=True, blank=True)` | The freshness clock. Written by "Still accurate", by a staff edit, and by verification. |
| `last_checked_by` | `FK(Member, null=True, on_delete=SET_NULL, related_name="+")` | Named in the byline. |
| `verified_by` | `FK(Member, null=True, blank=True, on_delete=SET_NULL, related_name="+")` | |
| `verified_at` | `DateTimeField(null=True, blank=True)` | |
| `unverified_reason` | `CharField(200, blank=True, default="")` | "Edited since it was verified." The locked rule says the drop is *recorded*; this is the column the page reads it from — **and it is rendered**, beside the status pill (§6.3). A written-but-never-shown column is a lie told to the next reader. |
| `is_published` | `BooleanField(default=True)` | False = a page held for a second read. **Spec D's safety gate is what writes it** (`status == OFFICIAL` saved by a non-moderator). A ships the column, the `published()` queryset, and the `/wiki/drafts/` row that explains it; A does **not** ship a Safety toggle. |
| `needs_review_since` | `DateTimeField(null=True, blank=True)` | **Denormalized. Spec D maintains it** from `WikiReport.file()` / `.resolve()` (brief §9.1). A reads it in three places that must be SQL-cheap: the status-pill precedence (§6), `needs_review()` (§4.2), and the *Needs review* chip **in search results** the brief demands. A never writes it. |
| `needs_review_reason` | `CharField(300, blank=True, default="")` | Same owner, same reason. A shows it beside the pill on the reading page as a one-line summary; D renders the full amber banner from `WikiReport`. **Escaped on render — it is member-supplied text.** |
| `archived_at` | `DateTimeField(null=True, blank=True)` | **A's column** (brief §9.1: D drops its own three and adds only `archive_redirect`). |
| `archived_by` | `FK(Member, null=True, on_delete=SET_NULL, related_name="+")` | Same. |
| `archive_reason` | `CharField(300, blank=True, default="")` | Same. Shown to the author by name on the archived page. |
| `search_text` | `TextField(blank=True, default="")` | Denormalized flattened plain text: title + body + every fact label/value + every attachment label. Rebuilt by `rebuild_search_text()`. **Why:** `icontains` over a Quill body matches `strong` and `href`; and search must reach the Quick answers, which is where the useful nouns live. |
| `qr_code` | `CharField(10, unique=True, blank=True, default="")` | The `/m/<code>/` short code. Fill-once, like the slug. §5.9. |

```python
class Kind(models.TextChoices):
    MACHINE    = "machine",    "Machine or tool"
    HOWTO      = "howto",      "How to do something"
    MATERIAL   = "material",   "Material"
    PROJECT    = "project",    "Project write-up"
    GUILD_INFO = "guild_info", "How this guild works"
    REFERENCE  = "reference",  "Reference table or chart"

class Status(models.TextChoices):
    COMMUNITY      = "community",      "Community"
    GUILD_VERIFIED = "guild_verified", "Guild verified"
    OFFICIAL       = "official",       "Official"
```

`__str__`: `f"{self.title} ({self.get_kind_display()})"`.

**Meta**

```python
ordering = ["title"]
indexes = [
    models.Index(fields=["guild", "kind"],   name="idx_wikipage_guild_kind"),
    models.Index(fields=["status", "kind"],  name="idx_wikipage_status_kind"),
    models.Index(fields=["-updated_at"],     name="idx_wikipage_updated"),
]
constraints = [
    # One seeded machine page per tool — the seeder must never be able to make a second.
    models.UniqueConstraint(
        fields=["equipment"],
        condition=Q(kind="machine", equipment__isnull=False),
        name="uq_wikipage_machine_equip",
    ),
]
```

### 4.2 `WikiPageQuerySet`

| Method | Behavior |
|---|---|
| `published()` | `filter(is_published=True)` |
| `not_archived()` | `filter(archived_at__isnull=True)` |
| `visible_for(request)` | The listing/search filter (§5.5). `is_effective_staff` gets everything; everyone else gets `published().not_archived()` plus their own unpublished pages plus unpublished pages in the guilds `editable_wiki_scopes(request)` returned. Those last two legs are `can_moderate_wiki_page`'s two legs expressed in bulk — when D lands `moderatable_wiki_scopes(request)` this method points at it instead, with no behavior change. |
| `for_guild(guild)` | `filter(guild=guild)` — B's tab is a filtered view of this store. |
| `space_wide()` | `filter(guild__isnull=True)` |
| `filtered(*, guild=None, kind="", stale=False)` | The browse/facet filter shared by the home page, the search page, and B's "See all" links. Each argument is applied only when truthy; `stale=True` narrows to `needs_review()`. **This is what makes an empty `q` a browse rather than a blank (D9)** — the search view chains `.search(q)` on top only when `q` is non-empty. |
| `search(q)` | Terms split on whitespace and AND'd, each `icontains` over `search_text`, `title`, `guild__name`, `equipment__name`. Empty `q` → `none()`, copying `WikiArticleQuerySet.search`'s shape exactly. **The view, not the queryset, is what turns an empty `q` into a browse** — keeping `none()` here means the two callers that genuinely want "no query, no results" still get it. Ordered by a `Case/When` status rank (Official → Guild verified → Community) then `-updated_at`, so the authoritative answer leads. |
| `needs_review()` | Pages past their review interval **or** carrying `needs_review_since`. Built as an OR of per-kind cutoff `Q()`s from `REVIEW_INTERVALS` — SQL, not Python, so B's overdue list and `?stale=1` both paginate. |
| `with_fact_prefetch()` | `prefetch_related("facts", "attachments").select_related("guild", "equipment", "verified_by", "updated_by")` — the N+1 guard every list surface uses. |

Attached as `objects = WikiPageQuerySet.as_manager()`. **No soft-delete manager override and no
`base_manager_name`** (brief §9.1 struck both): archiving is a visible state a member must be able to land
on, unlike `Guild.deleted_at` which hides a row everywhere, and A's tombstone needs `objects` to reach an
archived row. `base_manager_name` appears nowhere in this repo.

### 4.3 `WikiPageFact` — the Quick Answers rows

| Field | Type | Notes |
|---|---|---|
| `page` | `FK(WikiPage, on_delete=CASCADE, related_name="facts")` | |
| `label` | `CharField(60)` | Required. `help_text="The question, in two or three words. 'Blade' or 'Max width'."` |
| `value` | `CharField(200)` | Required. `help_text="The answer. Short enough to read at a glance."` |
| `sort_order` | `PositiveIntegerField(default=0)` | `HiddenInput` in the form; rewritten to the visual index by the reorder handler. |

`Meta.ordering = ["sort_order", "pk"]`. `__str__`: `f"{self.label}: {self.value}"`. No unique constraint on
label — the formset's `clean()` rejects duplicates within one save with a friendly message instead, which
gives a better error than an `IntegrityError` and lets a legitimate repeat exist if one ever appears.
The formset also caps the block at **eight rows** ("Quick answers work best short. Keep it to eight.") —
the brief's 4–8 target enforced where the user can see it.

### 4.4 `WikiRevision`

One row per save. Staff-only revert and the history list are spec D's UI; this spec owns the model, the
write, and the two things D cannot build without: **`kind`** and a real **reverse accessor on `Member`**.

| Field | Type | Notes |
|---|---|---|
| `page` | `FK(WikiPage, on_delete=CASCADE, related_name="revisions")` | |
| `title` | `CharField(200)` | Snapshot. |
| `body` | `TextField(blank=True, default="")` | Snapshot. **No `body_format` column:** the body is dual-mode and `looks_like_html()` sniffs it at render, exactly as the help columns do, so a stored format would be a second source of truth that can disagree with the text. D's snapshot restore reads `title` / `body` / `facts` / `status`. |
| `facts` | `JSONField(default=list, blank=True)` | Snapshot: `[{"label": …, "value": …}, …]`. **Included deliberately** — a revert that restored the prose but not the Quick answers would resurrect a half-old page. |
| `status` | `CharField(20, choices=WikiPage.Status.choices)` | The status at save time, so D's revert can restore it rather than guess. |
| `kind` | `CharField(20, choices=Kind.choices, default=Kind.SAVE)` | **A ships all three values**, including the one only D writes. Enum values are inert; shipping them here removes the A/D ordering hazard the way the six `SiteActivity.Kind` values do (brief §9.1). |
| `author` | `FK(Member, null=True, on_delete=SET_NULL, related_name="wiki_revisions")` | **`related_name="wiki_revisions"`, never `"+"`.** D's `wiki_page_contributors` resolver is one query — `Member.objects.filter(wiki_revisions__page=page).exclude(pk=actor_pk).distinct()` — and it is impossible without the reverse accessor. That notification (Kate verified your page) is the round's retention mechanism, so this is the single most load-bearing character in this table. Null for the seeder. |
| `note` | `CharField(200, blank=True, default="")` | "Seeded from the equipment register", "Reverted to 12 Mar", "Photo added". |
| `created_at` | `DateTimeField(auto_now_add=True)` | |

```python
class Kind(models.TextChoices):
    SAVE           = "save",           "Edit"
    REVERT         = "revert",         "Reverted"
    CONFLICT_DRAFT = "conflict_draft", "Unmerged draft"
```

`SAVE` is everything A writes. `REVERT` and `CONFLICT_DRAFT` are written only by D — a `CONFLICT_DRAFT`
row was never applied to the page, which is why D's revert refuses one and why D's history list labels it
differently. A's own code never filters on `kind`; it only has to exist and default correctly.

`Meta.ordering = ["-created_at", "-pk"]`, `indexes = [Index(fields=["page", "-created_at"], name="idx_wikirevision_page")]`.
`__str__`: `f"{self.page.title} @ {self.created_at:%Y-%m-%d %H:%M}"`.

### 4.5 `WikiAttachment`

The `MeetingAttachment` shape, with the two additions the brief requires.

| Field | Type | Notes |
|---|---|---|
| `page` | `FK(WikiPage, on_delete=CASCADE, related_name="attachments")` | |
| `label` | `CharField(200)` | **Required** (`blank=False`) — the locked rule; the label is the entire value of the upload. Diverges from `MeetingAttachment.label`, which is optional. |
| `file` | `FileField(upload_to="wiki/attachments/", blank=True, validators=[validate_wiki_upload])` | |
| `url` | `URLField(blank=True, default="")` | |
| `sort_order` | `PositiveIntegerField(default=0)` | `HiddenInput`; reorder rewrites it. |
| `uploaded_by` | `FK(Member, null=True, on_delete=SET_NULL, related_name="+")` | Net-new vs `MeetingAttachment`; the brief's attachment card shows the uploader. |
| `created_at` | `DateTimeField(auto_now_add=True)` | |

```python
class Meta:
    ordering = ["sort_order", "created_at"]
    constraints = [
        models.CheckConstraint(
            condition=((Q(file="") & ~Q(url="")) | (~Q(file="") & Q(url=""))),
            name="ck_wikiattach_file_xor_url",
        ),
    ]
```

`save()` calls `delete_orphan_on_replace(self, "file")`, then `normalize_field_if_uploaded(self, "file",
settings.IMAGE_MAX_LONG_EDGE_GALLERY)` **only when the upload is an image** (the helper already no-ops on
a non-fresh field; the image check keeps it from trying to open a PDF), then `super().save()`.

`validate_wiki_upload` is a new validator in `core/validators.py` — `ALLOWED_DOCUMENT_EXTENSIONS` plus
`{"jpg", "jpeg", "png", "webp", "heic"}`, sized against `MAX_UPLOAD_DOCUMENT_BYTES` for documents and
`MAX_UPLOAD_IMAGE_BYTES` for images. Why a new validator rather than `validate_document`: the quick-photo
flow writes an attachment, and `ALLOWED_DOCUMENT_EXTENSIONS` has no image extensions, so `validate_document`
would reject every photo a member takes.

Properties: `is_file`, `is_link`, `is_image` (extension test — drives the photo grid vs the card),
`display_name` (label, else file basename, else url), `size_label` (`"2.4 MB"`, blank for links).

### 4.6 `WikiDraft` (D3)

| Field | Type | Notes |
|---|---|---|
| `page` | `FK(WikiPage, null=True, blank=True, on_delete=CASCADE, related_name="drafts")` | Null = a new page not yet created. |
| `author` | `FK(Member, on_delete=CASCADE, related_name="wiki_drafts")` | |
| `kind` | `CharField(20, choices=WikiPage.Kind.choices)` | Meaningful for a new-page draft; mirrors the page's kind otherwise. |
| `guild` | `FK(Guild, null=True, blank=True, on_delete=CASCADE, related_name="+")` | Pre-filled scope for a new-page draft. |
| `title` | `CharField(200, blank=True, default="")` | |
| `body` | `TextField(blank=True, default="")` | Sanitized on every autosave — a draft is never a hole in the sanitizer. |
| `facts` | `JSONField(default=list, blank=True)` | `[{"label": …, "value": …}, …]`. Held as JSON because a draft is a keystroke-debounced scratch buffer, not a queryable store; normalizing it would double the write cost of every autosave for no query benefit. |
| `base_revision` | `FK(WikiRevision, null=True, blank=True, on_delete=SET_NULL, related_name="+")` | The page's newest revision when this draft was started. A never compares it; it is what the edit form re-populates its hidden `base_revision` from on `?draft=use`, so **spec D's** conflict check measures against the ancestor the member actually typed over rather than against "now" (§5.10). |
| `updated_at` | `DateTimeField(auto_now=True)` | Compared against `page.updated_at` to decide whether the resume banner offers this draft (D4). **Not a lock signal** — the advisory lock is D's `WikiEditLock.refreshed_at`. |

```python
class Meta:
    ordering = ["-updated_at"]
    constraints = [
        models.UniqueConstraint(fields=["page", "author"],
                                condition=Q(page__isnull=False), name="uq_wikidraft_page_author"),
        models.UniqueConstraint(fields=["author", "kind"],
                                condition=Q(page__isnull=True),  name="uq_wikidraft_new_author_kind"),
    ]
```

Manager: `WikiDraftManager.for_member(member)` — the resume list, newest first. **There is no
`active_editors` method and no `ADVISORY_LOCK_WINDOW`.** The soft advisory lock was struck from this spec
by brief §9.1; **spec D owns it**, as a `WikiEditLock` model with its own `TTL`, and A's autosave view
calls `WikiEditLock.refresh(page, member)` (§5.10).

A draft is **deleted** on a successful apply, and also when its author chooses "Start from the saved page"
in the resume banner (§5.10) — it is a buffer, not a record; the `WikiRevision` is the record. (This is the
one place the wiki departs from `AnnouncementDraft`'s mark-sent idiom, and it departs because an
announcement draft has no revision table behind it.)

### 4.7 Config and vocabulary

- **`SiteConfiguration.wiki_enabled`** — `BooleanField(default=False)`, `verbose_name="Member wiki"`,
  `help_text="Show the Wiki in the sidebar and let members read and write wiki pages. When off, every
  /wiki/ page and the QR sticker links answer 404."` Added to the `feature_flags` context processor and to
  `SiteSettingsForm.Meta.fields` beside `wiki_link_enabled`.
  **`default=False`, unlike the three precedent flags.** `help_page_enabled`, `equipment_page_enabled` and
  `wiki_link_enabled` all default `True` because each shipped in the same PR as the feature it gates. This
  one ships in A1 and the feature is not finished until A4, with B and D behind that — a `True` default
  would open a half-built wiki on the next deploy, on every environment at once, including production.
  Turning it on is one Site Settings tick at the end of the round, and it is also the flip that makes the
  round's single changelog entry true.
- **`SiteConfiguration.wiki_link_enabled` is kept, not deleted.** Its `help_text` changes to
  `"Show a link to the old MediaWiki at the bottom of the Wiki home. Turn this off once the old wiki is
  retired."` — an `AlterField` migration, no data change. Repurposing rather than dropping means nobody
  loses the ability to hide the old link, and the migration stays reversible with a plain field swap.
- **`SiteActivity.Kind` gains all six wiki values in the A1 migration** (brief §9.1), not the two A
  happens to write:

  ```python
  WIKI_PAGE_CREATED  = "wiki_page_created",  "Wiki page created"
  WIKI_PAGE_EDITED   = "wiki_page_edited",   "Wiki page edited"
  WIKI_PAGE_VERIFIED = "wiki_page_verified", "Wiki page verified"
  WIKI_PAGE_REPORTED = "wiki_page_reported", "Wiki page reported"
  WIKI_PAGE_ARCHIVED = "wiki_page_archived", "Wiki page archived"
  WIKI_PAGE_REVERTED = "wiki_page_reverted", "Wiki page reverted"
  ```

  Enum values are inert — a name nothing writes costs a row in a choices tuple. Shipping all six here
  removes the ordering hazard entirely: B and D build in parallel off A's merged branch, and neither has
  to add an enum member in its own migration, which is how two parallel migrations collide. A writes the
  first two (§7); B writes `WIKI_PAGE_VERIFIED`; D writes the last three.

### 4.8 Migrations

One migration in A1 creating all five models plus the `SiteConfiguration` changes and the six
`SiteActivity.Kind` values. Purely additive; the reverse is `DeleteModel` / `RemoveField` / an
`AlterField` back to the old help text and the old choices tuple. No data migration, no Airtable-synced
table touched. **Run `manage.py check` after it** — CI runs system checks that local pytest skips, and
every index/constraint name above was counted against the 30-character cap.

---

## 5. Business logic (fat models, skinny views)

### 5.1 Slug and reserved slugs

`WikiPage.save()` copies `WikiArticle.save()`'s idiom verbatim, with two changes: uniqueness is global
(not per-parent), and the reserved set is checked at the model too, not only in the form.

```python
def save(self, *args: Any, **kwargs: Any) -> None:
    """Fill the slug and QR code once, then persist.

    The slug stays stable once set — a deep link and a printed sticker must never break —
    so it is only filled when blank, and a slugified title that lands on a reserved
    segment or an existing page is suffixed (-2, -3, …) rather than replacing it.
    """
```

```python
RESERVED_WIKI_SLUGS = frozenset({
    "new", "search", "drafts", "review", "wanted", "p", "m", "edit", "history",
    "stickers", "verify", "report", "revert", "autosave", "confirm", "photo", "tip", "image",
    "qr", "conflict",
})
```

The brief's list plus every fixed segment this spec actually adds. The `WikiPageCreateForm` /
`WikiPageForm` carry a `clean_slug` that raises `"That name is reserved. Pick another title."`, mirroring
`HelpCategoryForm.clean_slug` in `hub/forms.py`. The model's dedupe loop treats a reserved value as
taken, so even a programmatic create cannot land on one.

### 5.2 Freshness (D5)

```python
REVIEW_INTERVALS: dict[str, int | None] = {   # months; None = never goes stale
    Kind.MACHINE: 12, Kind.HOWTO: 24, Kind.MATERIAL: 24,
    Kind.GUILD_INFO: 12, Kind.REFERENCE: 12, Kind.PROJECT: None,
}
VERIFIED_AGES_AFTER_MONTHS = 12
```

- `freshness_at` → `last_checked_at or verified_at or created_at`. **Deliberately not `updated_at`:** a
  member fixing a typo does not make a stale machine page accurate, so the clock only moves on an explicit
  write to `last_checked_at`. Every reset in this spec is such a write, which makes the rule one line to
  test.
- `review_due_at` → `freshness_at + interval`, or `None` for `PROJECT` and for an Official page whose kind
  is `PROJECT`.
- `is_out_of_date` → `review_due_at is not None and review_due_at < timezone.now()`.
- `verification_is_aged` → `verified_at is not None and verified_at < now - 12 months`. The green chip
  goes grey and reads "Verified Mar 2026"; the page is **never silently un-verified**.
- `confirm_still_accurate(member)` — sets `last_checked_at = now()`, `last_checked_by = member`, saves
  those two fields, and returns nothing. **Any active member may call it** (D5). It never sets
  `verified_by`, never changes `status`, and never writes a revision — confirming is not editing, which
  is the whole point of it being one tap.

### 5.3 The status ladder and the verified-drop rule

```python
def apply_edit(
    self,
    *,
    editor: Member,
    editor_may_verify: bool,
    title: str,
    body: str,
    note: str = "",
) -> WikiRevision:
    """Save one edit, snapshot a revision, and settle the verification.

    Writes the *pre-edit* state to a WikiRevision first, so the revision table always
    holds a restorable "before" for every change (D's revert reads it).

    A staff edit (``editor_may_verify``) keeps GUILD_VERIFIED and stamps
    ``last_checked_at`` — the locked "keeps the verification and resets the clock" rule.
    A non-staff edit of a GUILD_VERIFIED page drops it to COMMUNITY, clears
    ``verified_by``/``verified_at``, and records
    ``unverified_reason = "Edited since it was verified."`` — a green check must never
    ride on unreviewed text.

    An OFFICIAL page never reaches here from a member: ``can_edit_wiki_page`` refuses,
    and the reading page renders no edit affordance at all.
    """
```

It also stamps `updated_by`, `body_edited_at = now()`, rebuilds `search_text`, and logs
`SiteActivity.WIKI_PAGE_EDITED`. Creation goes through `WikiPage.objects.create_page(...)`, which writes
the page, its starter facts, an initial revision with `note="Created"`, and
`SiteActivity.WIKI_PAGE_CREATED`.

**The Safety gate is spec D's, not A's.** An earlier draft of this spec put a "This page tells someone
how to stay safe" toggle on the create form and overloaded `needs_review_since` / `needs_review_reason` to
hold the held-back state. Brief §9.1 struck both. **D owns the safety gate entirely**: safety-ness *is*
`status == OFFICIAL`, D adds a **Safety & Rules** card to A's starter chooser (which is why the chooser
renders from `membership/wiki_starters.py` data rather than hardcoded markup, and why `create_page` takes
an optional `status`), and D's gate saves an Official-status page `is_published=False` when the author is
not `can_moderate_wiki_page`. A ships three things the gate needs and nothing else: the `is_published`
column, the `published()` queryset, and the `/wiki/drafts/` row that tells the author where their page went
(§6.10).

A's create form therefore has **no Safety toggle**, and `apply_edit` **never writes `is_published`** —
which also closes brief §9.3's third hole, where a member created a Safety page, opened Edit, and
unticked the box to publish past the one gate the round has.

### 5.4 Revisions

`apply_edit` writes the **pre-edit** snapshot. `WikiPage.revisions.first()` is therefore "what it looked
like before the most recent change", which is exactly what a revert wants. The very first revision (written
by `create_page` / the seeder) is the empty-or-seeded starting point. `WikiRevision` rows are never
deleted except by the page's `CASCADE`; `WikiPage.revision_count` is a cheap `.count()` used by the byline
("12 versions saved").

### 5.5 Permissions (`membership/permissions.py`)

Written in the existing style: request-level, `view_as`-aware, filters not checks, no `is_staff` /
`fog_role` / `member_type` gate anywhere.

```python
def can_edit_wiki_page(request: HttpRequest, page: WikiPage) -> bool:
    """True when this request may edit the page's title, body, facts, and attachments.

    OFFICIAL pages: effective staff only (admins/officers) — a member sees no edit
    affordance at all, not a disabled one. An archived page: ``can_moderate_wiki_page``
    only. Everything else: any ACTIVE member, live, with no approval queue.

    This is also the gate on the three micro-contribution routes — quick tip, quick
    photo, and the editor's image upload (brief §9.3). The tip route writes the body
    through ``apply_edit``, so gating it on membership alone would put two write
    affordances at the bottom of an Official page.
    """

def can_verify_wiki_page(request: HttpRequest, page: WikiPage) -> bool:
    """True when this request may stand behind the page with a green check.

    **An OFFICIAL page is never verifiable, by anyone** — the guard is first, before any
    authority test. Official already outranks Guild verified in the pill precedence, so a
    Verify button there either does nothing visible or quietly demotes the page; B renders
    no control at all instead.

    Otherwise: a guild-scoped page defers to ``can_edit_guild`` — which is already lead +
    every staff role, orienters included (the orienter M2M folded into the staff role in
    migration 0049), so the brief's "lead, any guild staff role, and orienters" is exactly
    that one call. A space-wide page takes ``is_effective_staff``. A page linked to
    Equipment additionally admits that tool's own orienters via ``Equipment.is_run_by`` —
    the people who teach the machine, even when the tool belongs to no guild.

    A ships this; B and D consume it (brief §9.1).
    """

def visible_wiki_pages(request: HttpRequest) -> WikiPageQuerySet:
    """The pages this request may see in listings and search — a FILTER, not a check.

    Views ask for this and render what comes back, so a view that forgets to gate shows
    too little, never too much. ``WikiPage.objects.visible_for(request)`` — see §4.2.
    """

def editable_wiki_scopes(request: HttpRequest) -> tuple[list[Guild], bool]:
    """(guilds this request may scope a page to, may-create-space-wide) in two queries.

    The bulk companion for the New-page scope picker, mirroring
    ``editable_meeting_scopes``. Every active member may create a space-wide page and a
    page in any guild they have joined; staff get every guild.
    """
```

**`can_moderate_wiki` is struck. A adopts spec D's page-scoped `can_moderate_wiki_page(request, page)`**
(brief §9.1) — `is_effective_staff(request)` **or** `can_edit_guild(request, page.guild)`. An earlier draft
of this spec had a request-only version equal to `is_effective_staff`, which meant a guild lead could not
archive a bad page in their own guild without finding an officer. D also ships the bulk companion
`moderatable_wiki_scopes(request)`.

Because D lands after A, A's own A2–A4 code refers to `can_moderate_wiki_page` in exactly two places — the
archived-page edit leg in `can_edit_wiki_page`, and the archived tombstone's "Restore" affordance, which
is D's button on D's timeline. A2 ships a two-line private `_can_moderate_wiki_page(request, page)` in
`membership/permissions.py` with D's exact signature and body, and **D deletes it and lands the public
one in the same PR**. Naming it privately means the two cannot both be public at once and `git grep` finds
the collision instantly. `hub_wiki_stickers` takes `is_effective_staff` directly, because a printable
staff-wide sheet has no page in scope.

The reading view does **not** use `visible_wiki_pages` — an archived page must still resolve so it can
explain itself. `hub_wiki_page` resolves through `WikiPage.objects.all()` and renders the archived notice,
which is a different question from "what belongs in a list". This split is stated in both docstrings so a
later reader does not "fix" it.

### 5.6 The `wiki` sanitizer profile (`membership/markdown.py`) — D1

**The constraint.** `tests/membership/markdown_spec.py`'s golden-fixture spec pins `render_markdown(source)` byte-for-byte
against eight committed fixtures rendered *before* the `profile=` refactor. Those fixtures are pre-refactor
truth, not the code compared against itself. The `member` and `help` code paths must not move.

**How that is guaranteed.** The change to existing code is exactly two lines:

```python
-    if profile not in ("member", "help"):
+    if profile not in ("member", "help", "wiki"):
```

plus one `elif profile == "wiki":` branch inserted **after** the `member` branch and **before** the `help`
fall-through. `_ALLOWED_TAGS`, `_ALLOWED_ATTRS`, `_MEMBER_EXTENSIONS`, `_harden_link`, `_HELP_*`, and
`sanitize_page_html` / `sanitize_page_submission` / `render_page_content` are **not touched at all**. The
new profile is a parallel chain of new module-level names:

| New symbol | Purpose |
|---|---|
| `_WIKI_TAGS = [*_ALLOWED_TAGS, "img"]` | Member markdown plus images. **No `iframe`, no `div`** — an iframe is a full browsing context and stays the admin-authored help profile's privilege. |
| `_WIKI_ATTRS` | `a: [href, title]`, `th/td: [align]`, `img: _allow_wiki_img_attr`, `h2/h3/h4: _allow_help_heading_attr` (reused as-is — the pattern guard is identical). |
| `_allow_wiki_img_attr(tag, name, value)` | `alt`/`title` pass; `src` must start with one of `wiki_image_src_prefixes()`. |
| `wiki_image_src_prefixes() -> tuple[str, ...]` | Computed per call from settings, not a module constant, so `override_settings` in tests works and dev/prod differ correctly: `(f"{settings.MEDIA_URL}wiki/",)` plus `(f"{settings.R2_PUBLIC_URL}/wiki/",)` when R2 is configured. Every other host, every `data:` URI, and every protocol-relative URL is refused. |
| `_WIKI_EXTENSIONS = _MEMBER_EXTENSIONS` | No `admonition` — members have no syntax for it and the Quill toolbar cannot emit it. |
| `_WIKI_TAGS_HTML` / `_WIKI_ATTRS_HTML` | The Quill side: `_PAGE_TAGS + ["img", "h4", "table", "thead", "tbody", "tr", "th", "td", "code", "pre", "hr"]`. A member pasting a table from a supplier's page should keep it. |
| `sanitize_wiki_html(raw)` | The Quill path: `_normalize_quill_lists` → `bleach.clean` with the wiki HTML allowlist → drop src-less `img` (`_SRCLESS_IMG_RE`, reused) → `bleach.linkify(callbacks=[_harden_link_help])` → `""` when the result flattens to nothing. |
| `_inject_heading_ids(html)` | **D1.** One regex pass over `h2`/`h3`: a heading with no `id` gets `id="<slugified text>"`, deduped `-2`, `-3`; a heading that already has a pattern-valid id keeps it. |
| `render_wiki_content(source)` | The dual-mode entry point: `looks_like_html` → `sanitize_wiki_html`, else `render_markdown(profile="wiki")`; then `_inject_heading_ids` over the result either way. |
| `sanitize_wiki_submission(value)` | The form-save seam: HTML gets `sanitize_wiki_html`; anything else passes through unchanged (a no-JS textarea fallback, or a legacy Markdown value). Mirrors `sanitize_page_submission` exactly, and does **not** call it. |
| `wiki_content` filter | `membership/templatetags/membership_md.py` — `mark_safe(render_wiki_content(value or ""))`. |

**Why ids at render, not at save (D1).** A member renames a heading; a stored id points at text that no
longer exists, the TOC chip scrolls nowhere, and nobody notices for a month. Computing the id in the same
function that produces the body HTML makes the TOC and the anchors provably the same pass:
`WikiPage.toc()` is `_HELP_TOC_HEADING_RE.findall(render_wiki_content(self.body))`, reusing the existing
regex verbatim. It also keeps the stored body exactly what Quill produced, which keeps revisions honest.

**The editor's image button — two shipped files must change, and neither has a seam today.**

1. **`core/widgets.py`.** `PageContentEditorWidget.get_context` sets
   `context["widget"]["toolbar"] = "page"` **unconditionally**, ignoring `markdown_profile` entirely. So
   `PageContentEditorWidget(markdown_profile="wiki")` alone renders `data-rte-toolbar="page"` and the wiki
   toolbar never loads. The fix is a `toolbar` class attribute defaulting to `"page"`, settable from
   `__init__`, with `get_context` reading `self.toolbar`. Two lines, no behavior change for the two
   existing callers.
2. **`static/js/rich-editor-init.js`.** It gains a third `TOOLBARS` entry, `wiki` — the `page` set plus
   `["image"]` — **and a `handlers` seam it does not have.** Quill is constructed today as
   `modules: { toolbar: TOOLBARS[mount.dataset.rteToolbar] || TOOLBARS.default }`, a bare format array
   with nowhere to attach a custom handler. It becomes
   `modules: { toolbar: { container: TOOLBARS[…] || TOOLBARS.default, handlers: HANDLERS[…] || {} } }`,
   with `HANDLERS.wiki.image` doing the upload. Quill accepts both shapes, so the two existing toolbars
   are unaffected — but the array form must be moved into `container` or every existing editor breaks, so
   this is one change with a blast radius of three editors and needs a spec on each.

An earlier draft of this spec listed `core/widgets.py` as "unchanged" and described the JS change as
adding a toolbar entry. Both were wrong; §3's file map now names both files as edits.

The upload itself follows the `guild_image_upload` contract: `FormData` field `image`, `X-CSRFToken`
header, `{"error": …}` at 400 or `{"url": …}` at 200. The returned URL is under `wiki/body/` and therefore
passes the sanitizer. Base64 data-URI insertion — Quill's default — is refused by the sanitizer, so a
paste-in image is dropped rather than bloating the column; the editor hint says "Use the image button to
add a photo." `hub_wiki_image_upload` is gated on `can_edit_wiki_page` like the other two write routes
(brief §9.3).

### 5.7 Search (D9, D11)

Brief §9.1 makes this a **real, named expansion of A's scope**, not a nine-line view. As an earlier draft
specified it — one `q`, one flat list, `none()` when `q` is empty — three of B's "See all" links dead-ended
on a blank page and E's Policies search box returned zero policies. Brief §2's "one search box spans all
three stores, with results labeled by source chip" is a requirement, not an aspiration.

**Parameters `hub_wiki_search` accepts.** Every one is optional; unknown values are ignored, never an
error, because these arrive from printed links, emails, and other people's templates.

| Param | Values | Effect |
|---|---|---|
| `q` | free text | Terms AND'd, `icontains`. **Empty is legal** and means "browse", not "no results". |
| `guild` | a `Guild.slug` | Narrows to that guild. An unknown slug narrows to nothing and says so, rather than silently searching everything. |
| `kind` | a `WikiPage.Kind` value | Narrows to one kind. Drives B's "See all 19 →" from a grouped kind list. |
| `stale` | `1` | Narrows to `needs_review()`. Drives B's overdue panel "See all 14 →". |
| `source` | `wiki` · `help` · `policies` | Shows one store's group alone. Absent = all available groups. |
| `scope` | `all` | Drops `guild=` even when it is also present. B's "Everything" chip is a real submit inside the guild-scoped form, so it needs a way to override a hidden input. |
| `page` | int | `components/table_pagination.html`, 20 per group. |

**The view, in shape:**

```python
base = visible_wiki_pages(request).filtered(guild=guild, kind=kind, stale=stale)
wiki_results = base.search(q) if q else base.order_by("-updated_at")
```

`WikiPageQuerySet.search()` keeps its `none()`-on-empty behavior (§4.2) — the *view* is what decides that
an empty `q` browses. That keeps the queryset honest for any caller that really does want "no query, no
results", and keeps the two branches one line apart.

**Grouped results and the source chip (D11).** The view builds a list of groups, each
`{source_label, source_slug, results}`, in a fixed order:

| `source_slug` | `source_label` (the chip) | Query | Gate |
|---|---|---|---|
| `wiki` | **Wiki** | the two lines above | always |
| `help` | **Help** | `WikiArticle.objects.search(q)` — the shipped queryset, unchanged | `help_page_enabled`; skipped entirely when `q` is empty, because the Help Center has no browse surface here |
| `policies` | **Policies** | spec E's `visible_governance_documents(request).search(q)` | E's own flag; **the group is absent until E ships**, and A's template loops over whatever groups the view built |

E's §2.1 asks A for exactly this, plus "a shared search-result partial parameterised on `title`, `url`,
`snippet`, `source_chip`, and at most one `status_pill`". **That partial is `_wiki_card.html`** (brief
§9.1), not a second `_search_result.html` — see §6.1. E includes A's card and passes `source_chip`.

Folding the Help Center in at A2 costs one extra queryset call and one group, and it is what makes the
`?source=` chip row meaningful on day one instead of a filter with one value. A guild page and a help
guide answering the same question is the exact case the brief's single box exists for.

**Filter chips above the results** are the same `.pl-wp-chip` links as the home page (§6.1): one row for
source, one for kind, and the guild chip when `?guild=` is set. Each chip is an `<a>` carrying the current
query string with one key changed, so a member can widen a search without retyping it.

**`search_snippet`** copies `WikiArticle.search_snippet` verbatim except that it reads the already-flattened
`search_text`, so no re-flattening happens per result. **Escaping happens before the `<mark>` insertion**,
which is why the template may `|safe` it — the same reason as the original, restated in the docstring so
nobody removes it. On a browse (no `q`) there is no snippet and the card falls back to its lead line.

`rebuild_search_text()` runs in `WikiPage.save()` and is called again by the fact and attachment save
views after their formsets commit (children are saved after the parent, so the page's own save cannot see
them). A model method, not a signal — CLAUDE.md prefers the explicit call, and there are exactly three
call sites.

Zero-result logging is spec B's (it owns both the model and the panel). The hook point is named here so B
does not have to hunt for it: **immediately after the `wiki` group's `results` is built in
`hub_wiki_search`, and only when `q` is non-empty** — a browse with no filters matching nothing is not a
failed search and must not pollute B's panel.

### 5.8 The locked Official block

`_wiki_official.html` renders **from `Equipment`, never from wiki prose** (brief §5.4). Given
`page.equipment`, the block shows the tool's name, its guild, `required_orientation` and the viewer's
`access_state(member)` line, and `location_note` — all read-only, in its own tinted container with a left
rule and an "Official" chip, **above** member content, with no edit affordance for anyone. There is no
per-page copy of any of it, so it cannot drift, and a member cannot edit it because there is nothing on
the page to edit.

`WikiPage.official_block_context(member)` returns the small dict the partial renders, computed with the
bulk-set arguments `access_state` already accepts (`oriented_type_ids`, `member_guild_ids`) so a list of
machine cards costs two queries, not two per card.

### 5.9 QR short codes

```python
_QR_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford-ish: no 0/O/1/I/L/U
_QR_CODE_LENGTH = 6                               # ~7.3e8 codes
```

Filled once in `WikiPage.save()` alongside the slug, retrying on the unique constraint. Codes are stored
uppercase and the view uppercases before lookup, so a phone camera that lower-cases the path still
resolves. **The code lives on `WikiPage`, not on `Equipment`,** because a sticker points at *the page you
want someone to read* and not everything worth stickering is a tool — a material shelf, a door, a dust
collector. `Equipment` reaches its page through the reverse `wiki_pages` relation, so nothing is lost.

`hub_wiki_qr` is the only wiki view with **no `@login_required`**:

```python
def hub_wiki_qr(request: HttpRequest, code: str) -> HttpResponse:
    """/m/<code>/ — the sticker route. A scan must never dead-end.

    Signed in: 302 to the page. Signed out: 302 to the login page carrying
    ?next=<the page>, via ``redirect_to_login``, so finishing the login lands on the
    machine the member is standing in front of rather than the home page. An unknown or
    retired code renders a friendly 404 page with a search box, not a bare 404 — a
    printed sticker outlives the page it was made for.
    """
```

It still respects the feature flag (404 when off) and lives behind `MEMBER_ONLY_PATH_PREFIXES` so it does
not resolve on the public book surface.

### 5.10 Draft resume; and where the lock and the conflict save went (D4)

**The advisory lock is struck from this spec, and so is the inline conflict save.** Brief §9.1 gives both
to spec D. An earlier draft of A derived a lock from `WikiDraft.updated_at` and re-rendered the edit screen
with a "Save mine anyway" banner; neither exists here now.

| Struck from A | Now owned by | Shape |
|---|---|---|
| The soft advisory lock | **D** | A `WikiEditLock` model (`page` OneToOne, `holder`, `started_at`, `refreshed_at`, `TTL = 10 min`). D's editor GET calls `WikiEditLock.claim(page, member)` and renders `_lock_warning.html` from the previous live holder it returns. |
| The conflict save | **D** | A hidden `base_revision` on the form, a `WikiRevision(kind=CONFLICT_DRAFT)` written **before** the response, `WikiSaveConflict`, and a dedicated `/wiki/p/<slug>/conflict/<pk>/` screen. |

**What A still has to build for them**, and must not forget:

1. **`hub_wiki_autosave` calls `WikiEditLock.refresh(page, member)`** on every accepted POST — a
   `save(update_fields=["refreshed_at"])` on the caller's own row, a no-op when they do not hold it. This
   is the entire reason D needs no polling timer, no second endpoint, and no JS. D lists this file in its
   own §3 for the same reason. Until D merges, the call site is a one-line lazy import guarded by
   `try: from membership.models import WikiEditLock / except ImportError: pass`; **D removes the guard**.
2. **The edit form carries a hidden `base_revision`** — the pk of the page's newest revision at load, and
   on a resumed draft the pk the draft was started from (`WikiDraft.base_revision`). A does not compare it;
   D does. A ships the field so D's check has something to read and so a resumed draft compares against the
   right ancestor rather than against "now".
3. **`wiki_edit.html` exposes two named include points** (D's §2 requires them): a
   `{% block wiki_editor_banners %}` immediately inside the `<form>`, above the title field, where D's lock
   warning and conflict notice land; and a `{% block wiki_editor_footer %}` under the body field, beside
   A's "Every version is saved" line. `wiki_page.html`'s action row exposes a third slot,
   `{% block wiki_page_actions_extra %}`, for D's **Report** button.

**Draft resume — the loop this spec has to close itself (D4, brief §9.4).** A draft could be saved, listed
and discarded, but nothing said what happens when the editor opens and one exists. It does now.

`hub_wiki_edit` GET looks for this member's draft for this page. When one exists **and**
`draft.updated_at > page.updated_at`, the editor renders in **resume-offer** state:

- A neutral (not amber — nothing is wrong) `pl-wp-resume` card at the top of the form, inside the
  `wiki_editor_banners` block: **"You have unsaved changes from 12 minutes ago."** with the age from
  `timesince`, and two real buttons: **[Use My Draft]** (`pl-btn pl-btn--sm`) → `?draft=use`, and
  **[Start From The Saved Page]** (`pl-btn pl-btn--primary pl-btn--sm`) → `?draft=fresh`.
- **The saved page is the default.** The form below the card is populated from the page, not the draft, so
  a member who ignores the card entirely edits the live page — the safe outcome. A stale tab from three
  days ago must never silently overwrite somebody else's finished work.
- **`?draft=fresh` deletes the draft** and reloads clean, so the question is asked once and the drafts list
  does not keep offering a draft its author already declined. It is behind
  `components/confirm_modal.html` (`confirm_title="Throw away your unsaved changes?"`,
  `confirm_message="The text you had here is not saved anywhere else."`,
  `confirm_button_text="Throw It Away"`), because it is the one destructive button on the screen.
- **`?draft=use`** loads title, body, and facts from the draft and keeps `base_revision` at the draft's,
  so D's conflict check compares against what the member actually started from.
- When `draft.updated_at <= page.updated_at` the draft is **stale by definition** — the page moved on after
  it was written. No card renders, and the draft is deleted on the next successful save like any other.
- **States.** *No draft:* nothing renders, no extra query beyond the one `.filter(...).first()`.
  *Draft with an empty title and body:* treated as no draft (autosave fires on focus-out, so an opened and
  abandoned editor leaves an empty row).

New-page drafts resume the same way, keyed on (author, kind) instead of (page, author); `/wiki/new/<kind>/`
renders the same card.

### 5.11 Equipment stub seeding (D2)

`manage.py seed_wiki_machine_pages [--dry-run]`, following `seed_help_center` exactly: module-level data
in `membership/wiki_starters.py`, `update_or_create`-shaped syncs, a plain-text report, no
`self.style.SUCCESS`, no transaction wrapper.

**It refuses to run on an empty register.** The brief's §3 lesson (`sync_docs` once removed 50 documents
on a bare run):

```python
if not Equipment.objects.exists():
    raise CommandError(
        "No equipment rows found. Refusing to run: an empty register would create nothing "
        "and hide a broken import behind a clean exit."
    )
```

**Per active `Equipment` row** (`Equipment.objects.active()`), in this order:

1. **Find the page.** By `equipment=<tool>` first. If none, **look for an unlinked page to adopt**: a page
   whose `equipment` is null and whose `slug` equals `slugify(tool.name)` (or whose `title` matches
   case-insensitively). If none, create one.
2. **Adopt rather than duplicate (D10).** This spec's phasing makes the collision certain, not
   hypothetical: the wiki is writable from **A3** and the seeder does not run until **A4**, so by launch
   day a member may well have written "Table Saw" by hand. The seeder sets `equipment` (and `guild`, when
   the page's is blank) on that existing page, writes a `WikiRevision` with
   `note="Linked to the equipment register"`, counts it as **adopted**, and **changes not one word of
   content** — `title`, `body`, and every fact are left exactly as the member wrote them, whatever
   `body_edited_at` says. Creating a twin splits the knowledge across two URLs and makes the member's page
   look like the wrong one; raising on the clash turns one member's good page into a red deploy job.
3. **The `uq_wikipage_machine_equip` constraint is what makes this safe** — if adoption ever picked the
   wrong page, a second machine page for the same tool cannot exist, so the failure is loud and immediate
   rather than a slow duplicate.
4. **Moderators can fix a mis-link by hand.** The edit form carries an **Equipment** select for
   `can_moderate_wiki_page` holders only (§6.6), with a blank "Not about a specific tool" option, so an
   adopted-in-error page can be detached and an orphan page attached without a shell. Without it, the only
   remedy for a mis-seed is a Render one-off job, which is not a remedy anyone will use.

Then, keyed on that page:

| The seeder always writes | The seeder writes only when `body_edited_at is None` **and** the page was not adopted |
|---|---|
| `kind = MACHINE`, `equipment`, `guild = equipment.guild` (when the page's is blank), `qr_code` (fill-once) | `title` (the tool name), `body` (the MACHINE starter), the starter `WikiPageFact` rows |

That split is the whole guard: the left column is structural wiring no member can edit on the page, so
refreshing it is always safe; the right column is content, and once a human has touched it the seeder
never writes it again. `body_edited_at` is chosen over the alternatives because a revision count is
changed by the seeder's own initial revision and by any revert, and an `is_seed_owned` flag answers
"who created this row" rather than "has a person written here" — which is the question that matters, and
the timestamp additionally tells an operator *when* the page left seed ownership.

The seeder never sets `status` (stubs are Community, like everything else), never touches `is_published`,
and never deletes a page for a retired tool — an inactive `Equipment` keeps its page, which is the whole
"archive, never delete" posture.

Report: `"Seeded 34 machine pages: 6 added, 24 refreshed, 4 skipped (edited by members)."`
`--dry-run` reports the same counts and writes nothing.

It is **not** added to `render.yaml`'s `buildCommand`. `seed_help_center` runs on every deploy because it
owns its content; this seeder hands its rows to members on the first run and must never race a deploy
against a member's edit. It is a one-off Render job before launch, re-run by hand when new equipment is
added, and §10 notes the alternative if that ever becomes a chore.

### 5.12 Domain exception

`class WikiError(Exception)` — the sibling of `EquipmentError` / `OrientationError`, always carrying
member-facing copy. Raised by `apply_edit` on an archived page, by `create_page` on a duplicate title in
the same scope, and by the quick-tip path when the body would exceed its cap. Views map it to a friendly
toast; nothing else catches it.

---

## 6. UI / UX

Member-facing copy throughout is plain, short-sentence ELI14, **no dashes in any copy string**. Every
screen is verified in **both themes**. Every form control goes through `components/form_field.html`, which
emits **`.pl-form-group`** — a control wrapped by hand instead gets `.hub-form-group`; both are defined,
both style `input` / `select` / `textarea` from the theme's input tokens, and both satisfy FRONTEND.md
Rule 13. **Never `var(--surface, …)`**, which is not a token and silently falls back to white. All new
classes use the **`pl-wp-`** prefix.

**On colors.** Most colors here are `--hub-*` tokens. **Amber is the exception and is literal hex**, in
both themes, because there is no amber token: `--hub-warn` does not exist anywhere in `static/css/`, and a
`background: var(--hub-warn)` renders transparent. Every amber surface in this spec copies the shipped
`.pl-confirm-warn` pair from `components.css`: `background: rgba(251, 191, 36, 0.12)` / `color: #fbbf24`,
with `[data-theme="light"]` → `rgba(180, 120, 10, 0.1)` / `#8a5b06`. The `.hub-pill--warn` *modifier* does
exist and is what the pills use.

**Headings are Title Case** (Rule 22), including generated ones. The headings this spec names, spelled the
way they must ship: **"Files And Photos"**, **"Search The Wiki"**, **"Start A Page"**, **"Your Guilds"**,
**"Recently Updated"**, **"Machines"**, **"Your Drafts"**, **"The Old Wiki"**, **"Quick Answers"**,
**"Related Pages"**, **"Tips From Members"**, **"Share This Page"**, **"Add A Photo"**, **"Add A Tip"**,
**"Wiki"**. Body copy, hints, and sentence-style descriptions stay sentence case.

**CSS names checked before choosing the prefix** (grepped across `static/css/*.css` on 2026-09-07):
`pl-wiki` (two classes exist, `.pl-wiki-toc` and `.pl-wiki-article`, both owned by the Help Center —
**not reused**), `pl-help` (52 classes: the tooltip component `.pl-help*` and the whole `pl-help-*`
help-center namespace — **not extended**), `pl-wp` (**zero matches, unclaimed**), `hub-empty` / `pl-empty`
/ `empty-state` (**none exist** — the convention is `hub-text-muted` plus a feature-scoped `__empty`
modifier, used 51 times in `templates/hub/`), `pl-breadcrumb` (**does not exist**), `pl-pill` (**does not
exist**; the family is `.hub-pill` + modifiers, and `hub.css` documents adding new modifiers in-family
rather than a new prefix), `pl-badge` (only `--version`; the base is `.hub-badge`), `pl-tabs` / `vote-tab`
(exist — reused verbatim), `pl-chip` (exists as a checkbox chip — reused for filters), `@media print`
(exists in `hub.css` scoped to the meeting workspace, and in `guild-flyer.css` / `class-flyer.css` with
`@page`), `pl-form-group` / `pl-row-menu` (exist in `components.css` — reused verbatim).

**Namespace discipline across three parallel PRs.** A owns bare `pl-wp-*`; **B namespaces `pl-wp-tab__*`**,
**D namespaces `pl-wp-mod__*`**, E keeps `pl-gov-*` (brief §9.1). Re-grep `hub.css` against A's merged
branch before writing CSS in B or D.

**One status pill per surface.** `WikiPage.status_pill` returns `(modifier, label, tooltip)` and is the
single source for the page header, the card partial, and the search result, so the three can never drift.
Precedence, top wins:

| Condition | Pill | Copy |
|---|---|---|
| `needs_review_since` set | `hub-pill hub-pill--warn` | Needs review |
| `is_out_of_date` | `hub-pill hub-pill--warn` | Out of date |
| `status == OFFICIAL` | `hub-pill hub-pill--primary` | Official |
| `status == GUILD_VERIFIED` and not aged | `hub-pill hub-pill--ok` | Guild verified |
| `status == GUILD_VERIFIED` and aged | `hub-pill hub-pill--neutral` | Verified Mar 2026 |
| otherwise | `hub-pill hub-pill--neutral` | Community |

The first two rows are why A keeps `needs_review_since` and `needs_review_reason` as columns even though
**D writes them** (§4.1): a pill in a paginated list cannot join a `WikiReport` per row, and the brief
requires the *Needs review* and *Out of date* chips to appear **in search results**. Do not delete these
columns when reading D and finding `WikiReport`.

Kind, guild, and equipment render as **quiet muted text**, never colored pills — the brief's "no pill
salad" rule. The Official *block* keeps its own chip inside its container, which is how an out-of-date
official page still reads as official where it matters.

**Beside the pill, one line of plain-language state** — the column an earlier draft wrote and never showed:

| When | Line, `hub-text-muted pl-wp-statusnote` |
|---|---|
| `unverified_reason` is set | "Edited since it was verified. Waiting for someone to check it again." |
| `needs_review_since` is set | "Someone reported a problem with this page." (D's banner carries the reporter's words in full.) |
| `is_out_of_date`, nothing else | "Nobody has checked this since March 2025." |

It renders under the `<h1>` on the reading page and is omitted from cards, where the pill alone is the
budget.

---

### 6.0 Sidebar — `templates/hub/base.html` (A2)

**The nav block is duplicated across two role branches and both must change.** `templates/hub/base.html`'s
`<nav>` splits on `{% if request.view_as.is_admin %}` / `{% else %}` / `{% endif %}`, and the block
`{% if wiki_link_enabled and makerspace_wiki_url %}` appears **once inside each branch**. They are
byte-identical today, and a change to one that misses the other silently leaves half the members on the
old external link — grep for the `{% if wiki_link_enabled %}` opener and expect exactly two hits. Each
becomes:

```html
{% if wiki_enabled %}
<a href="{% url 'hub_wiki_home' %}" class="hub-sidebar__link {% active_nav 'hub_wiki_home' %}"
   data-help-key="nav.wiki">
    <svg …the existing globe icon, unchanged…></svg>
    Wiki
</a>
{% endif %}
```

Three changes from what is there: the `href` becomes an internal `{% url %}`; `target="_blank"
rel="noopener noreferrer"` is dropped (it is no longer an external link); and `{% active_nav %}` is added,
which the external link could not have. The gate moves from `wiki_link_enabled and makerspace_wiki_url` to
`wiki_enabled`. Both entries stay under the same `<hr class="hub-sidebar__divider">`, directly below Help.
Sidebar colors come from `--hub-sidebar-*` tokens, never `--color-navy`.

`makerspace_wiki_url` and `wiki_link_enabled` are **still used** — by the "Old wiki" link on the wiki home
(§6.1). Neither the context processor nor the setting is removed.

---

### 6.1 Wiki home — `/wiki/` · `templates/hub/wiki_home.html` (A2)

- **Layout:** `components/page_header.html` — title "Wiki", description "How the space, the machines, and
  the materials actually work. Written by members." Header action: **"+ New Page"**
  (`pl-btn pl-btn--primary`) → `/wiki/new/`, shown to any active member.
- **Search first.** Directly under the header, a full-width `components/table_search.html` with
  `action="{% url 'hub_wiki_search' %}"`, `placeholder="Search the wiki"`, in a `hub-card`. Search is the
  primary way in, so it sits above every list.
- **Filter chips:** two `.pl-tabs` rows — scope (All / one per guild with pages / Space-wide) and kind
  (All / Machines / How-tos / Materials / Projects / Guild info / Reference). These are **GET-param
  navigation, not a form**, so each chip is an `<a>` carrying `.pl-wp-chip` / `.pl-wp-chip--active`
  rather than the existing `.pl-chip`, which wraps a checkbox and gets its selected state from
  `:has(input:checked)`. `.pl-wp-chip` copies `.pl-chip`'s pill geometry and its active colors so the two
  look identical side by side. On a phone the rows scroll horizontally inside their own container
  (`.pl-tabs` already does this); the page never scrolls horizontally.
- **Sections**, each a `hub-card` of `_wiki_card.html` rows:
  - **"Your Guilds"** — pages scoped to guilds the member has joined, newest-updated first, capped at 8
    with a **"See all"** link to `/wiki/search/?guild=<slug>`. Hidden entirely when the member has joined
    no guilds.
  - **"Recently Updated"** — 10 most recent across everything visible, "See all" → `/wiki/search/`.
  - **"Machines"** — the `kind=MACHINE` grid, `repeat(auto-fill, minmax(16rem, 1fr))`, one column on
    phones, "See all" → `/wiki/search/?kind=machine`. This is the section the stub seeding fills, so the
    wiki is never empty on day one.
  - Every one of those links resolves to a real list because the search view browses on an empty `q`
    (§5.7). Under the old `none()` behavior all three were dead ends.
- **Aside** (`.pl-guild-grid`, collapses under the main column below 1024px):
  - **"Start A Page"** card — the six starter cards in miniature, each linking to `/wiki/new/<kind>/`
    and carrying any `?guild=` / `?title=` / `?wanted=` through (§6.4).
  - **"Your Drafts"** card — up to three, link to `/wiki/drafts/`. Hidden when there are none.
  - **"The Old Wiki" card** — rendered only when `wiki_link_enabled and makerspace_wiki_url`: "We are
    moving everything here. The old wiki is read only and will be switched off." plus an outbound link
    (`target="_blank" rel="noopener noreferrer"`). This is the brief's demotion, and the existing Site
    Settings toggle is what retires it.
- **States.** *Empty wiki* (before seeding): "Nothing here yet. Start with the machine you know best."
  plus the New Page button — `<p class="hub-text-muted pl-wp-home__empty">`. *Empty filter result:*
  "Nothing matches those filters." plus a "Clear filters" link. No loading state (a full-page GET).
- **The card partial — one shape, everywhere.** `templates/hub/partials/_wiki_card.html` is the only card
  in the wiki: home sections, search results, B's guild-tab rows, B's wanted list, and E's governance
  results all render through it (brief §9.1). It takes:

  | Parameter | Required | Effect |
  |---|---|---|
  | `page` | yes | The object. A duck-typed stand-in works: E passes a `GovernanceDocument` and only touches `title`, `get_absolute_url`, and `status_pill`. |
  | `snippet` | no | Pre-escaped HTML with `<mark>` already inserted. **When present it replaces the lead line** and is rendered `|safe`. This is the whole reason search results do not need a second card shape. |
  | `source_chip` | no | A quiet neutral label — "Wiki", "Help", "Policies". Not a pill, not colored. Omitted when the surface has only one source. |
  | `actions_partial` | no | A template path rendered in `.pl-wp-card__actions` at the right of the row. **This is where B's `_wiki_verify_control.html` with `compact=True` lives.** Without the slot B has nowhere to put a Verify button but a second card. |

  Body, in order: title (link), the one status pill, muted attribute text ("Machine · Woodworking"), then
  `snippet` **or** the lead line (first 160 characters of `search_text`), then a muted footer
  "Updated 3 Mar by Sam R.", then `actions_partial`. The whole card is the tap target, minimum 48px tall;
  when `actions_partial` is present the action sits outside the link so it is not swallowed by it.
- **Dark + light:** cards are `hub-card`; chips are `.pl-wp-chip`, whose active state is a class, not a
  `:has()` selector, so it works for a link. **Pills need one scoped light-theme override.**
  `.hub-pill--ok`'s dark-theme green (`#6ee7b7`) is too pale to read on the light card, and the codebase
  has already worked around this four separate times — `[data-theme="light"]` scoped under
  `.pl-invite-list`, `.pl-automation-card`, `.pl-map-list` and `.pl-map-detail`, all setting
  `color: #1f7a52` in `hub.css`. Add the same scoped rule under `.pl-wp-card` and the page header rather
  than changing the shared modifier, which would move four existing surfaces.

---

### 6.2 Search and browse — `/wiki/search/` · `templates/hub/wiki_search.html` (A2)

This screen is both the search results and the browse list; §5.7 has the parameters and the groups.

- Breadcrumb `<nav class="pl-wp-breadcrumbs" aria-label="Breadcrumb"><a href="{% url 'hub_wiki_home' %}">Wiki</a></nav>`
  (the `.pl-help-breadcrumbs` shape under our own prefix; there is no breadcrumb component).
- `page_header.html` **"Search The Wiki"**, then `components/table_search.html` in a `hub-card`. The form
  re-submits every active filter as hidden inputs, so searching inside a filtered browse keeps the filter.
- **Filter chip rows** under the search box, `.pl-wp-chip` links carrying the current query string with one
  key changed: **source** (All / Wiki / Help / Policies — only the sources that produced a group, and the
  row is hidden entirely when there is one source), **kind** (All / the six), and a single removable
  **guild** chip when `?guild=` is set, whose "×" is the `scope=all` link. Each chip is 48px tall and the
  row scrolls horizontally inside its own container on a phone.
- **Result count line:** `<p class="hub-text-muted pl-wp-searchcount">7 pages match "table saw".</p>`
  On a browse it reads "34 pages in Woodworking." or "Everything in the wiki, newest first."
- **Groups.** Each source renders as a `hub-card` with an `<h2>` naming it ("Wiki", "Help", "Policies") and
  its rows as `_wiki_card.html` with `snippet` and `source_chip` passed. A group with no results is not
  rendered at all — never a section reading "0". With one group the `<h2>` is dropped and the rows sit
  directly under the count line, so a plain wiki search does not grow a redundant header.
- **Out of date shows here.** The brief is explicit that the derived chip must appear *in search results*,
  not only on the page — the pill precedence in §6 does that for free, from the denormalized columns.
- **States.**
  - *Browse, no query, no filters:* every visible page, newest first, paginated. **Not** a prompt screen —
    this is the URL B's "See all" links and E's search box land on (D9).
  - *Query typed, results found:* the groups above.
  - *Zero results:* `{% include "hub/partials/_wiki_search_empty.html" %}` — **spec B's partial,
    included verbatim.** A ships no inline zero-result markup at all (brief §9.1). It carries the
    "Search Everything" escape, "Ask In #woodworking", "Request This Page", and the "write it yourself"
    link. Until B merges, A2 renders a two-line placeholder in the same slot: "Nothing matched *table saw*."
    plus a **"+ Start This Page"** link to `/wiki/new/?title=<query>` — **and A2's own spec asserts on the
    include, not on that copy**, so B swapping the file in does not turn a test red.
  - *Filters that match nothing:* "Nothing matches those filters." plus a "Clear filters" link back to
    `/wiki/search/`. This is a different state from a failed search and must not log a search miss.
  - *Loading:* none (full-page GET).
- **"Ask in Discord" — how the URL is built.** `https://discord.com/channels/<server>/<channel>` from
  `SiteConfiguration.discord_server_id` and `Guild.discord_channel_id`, `target="_blank" rel="noopener"`,
  `hx-boost="false"`. **There is no general-channel field** — `discord_general_webhook_url` is a webhook,
  not a channel, and cannot be linked to. So: guild scoped and both ids set → the guild channel; server id
  set but no guild channel (including an unscoped search) → `SiteConfiguration.discord_info_channel_id`
  when it is set, else a plain link to the server; neither configured → **the button is omitted entirely**
  rather than rendering a dead link. B owns this markup; the construction is written down here because A's
  placeholder has to match it and because the "general channel" an earlier draft assumed does not exist.
- **Pagination:** `components/table_pagination.html` past 20 rows per group, with `base_params` carrying
  every active parameter, not just `q`.
- **Mobile:** single column; the snippet clamps to three lines; the pill wraps under the title rather than
  squeezing it; the chip rows scroll, the page does not.

---

### 6.3 Reading a page — `/wiki/p/<slug>/` · `templates/hub/wiki_page.html` (A2)

The composition is brief §5.5, top to bottom, identical order on desktop and phone.

**0. Breadcrumb + header.** `Wiki / Woodworking`. Then `<h1>` title, the one status pill, the one-line
status note (§6), and — for a machine page — the live equipment status line from `access_state` ("You are
set up for this tool." / "Orientation needed before you use this.").

**0b. The action row — the desktop twin of the phone action bar.** One block, one gate, so the two widths
cannot drift apart. Rendered right-aligned in the page header, in this order:

| Action | Control | Shown to |
|---|---|---|
| **Edit** | `pl-btn pl-btn--secondary pl-btn--sm` | `can_edit_wiki_page` |
| **+ Add A Photo** | `hub-btn hub-btn--sm` (opens the §6.7 modal) | `can_edit_wiki_page` |
| **+ Add A Tip** | `hub-btn hub-btn--sm` (opens the §6.8 modal) | `can_edit_wiki_page` — **this is the named desktop trigger** an earlier draft was missing |
| **Still accurate** | `pl-btn pl-btn--sm` (§6.9) | any active member |
| *Report* | — | **spec D**, into `{% block wiki_page_actions_extra %}` |
| **Print / Save PDF** | `hub-btn hub-btn--sm hub-btn--ghost`, `onclick="window.print()"` | everyone |

Three rules the build must hold (brief §9.3):

1. **Everything except "Still accurate", Report, and Print is inside one
   `{% if can_edit_wiki_page %}`.** An Official page shows a member no edit affordance at all — not a
   disabled button, not a greyed one, no button. The same gate is on the three write endpoints (§3), so
   the screen and the server agree.
2. **On an archived page the whole action row is hidden**, including "Still accurate": there is nothing to
   keep accurate. The archived notice and a link back to `/wiki/` are the only affordances. `hub_wiki_confirm`
   answers a crafted POST on an archived page with **409 and an error toast** ("This page is archived.
   There is nothing to confirm."), never a silent success.
3. On a phone the same list becomes the sticky bar in item 10; the template renders one action list and
   two layouts.

**0c. The digest cue — `?confirm=1`.** B's monthly lead digest links overdue pages as
`/wiki/p/<slug>/?confirm=1`. "Cued" means, concretely:

- A neutral `pl-wp-confirmcue` card renders directly under the breadcrumb, above the `<h1>`:
  **"Does this page still match what is in the space today?"** with the same **Still accurate** primary
  button the header carries (the same `confirm_id="wiki-confirm"` modal, so there is one confirm flow) and
  a quiet secondary link, "Not quite. Edit it," into the editor.
- It renders **only** for an active member on a non-archived page. Any other viewer, or any other value of
  the parameter, and the card is simply absent — a link from a two-week-old email must never error.
- No modal opens on load, no scroll is hijacked, and nothing flashes. A card that is already the first
  thing under the breadcrumb does not need to steal focus.
- After a successful confirm the OOB response replaces both the pill and this card (§6.9), so the cue
  disappears and the page says "Checked today."

**1. Amber review banner — spec D renders this, A does not.** When a page has an open `WikiReport`, D's
`_needs_review_banner.html` quotes the oldest report's reason and reporter and carries D's own
**Mark Reviewed** button. **A ships no banner partial of its own** (brief §9.1 struck it). What A ships is
the pair of denormalized columns D maintains, the pill precedence that reads them, and the one-line status
note under the title (§6). **The page stays fully readable underneath**, which is the locked rule.

**2. Archived notice** — when `archived_at` is set: `pl-wp-archived`, "This page was archived by Kate on
3 Mar. Reason: superseded by the SawStop page." plus a link to the replacement when the reason contains
one, plus a link back to `/wiki/`. The URL keeps working, which is the locked rule. No edit affordance.

**3. Quick Answers** — `_wiki_facts.html`, a two-column key/value block in its own `hub-card` headed
**"Quick Answers"**, first
thing under the title because it is what people came for. `dl` markup (`pl-wp-facts` / `__label` /
`__value`); on a phone the two columns stack into label-above-value pairs with the value at 17px.
Hidden entirely when the page has no facts, with an inline nudge for an editor: "No quick answers yet.
[Add the first one]" linking into the edit page anchored at the facts block.

**4. The locked Official block** — `_wiki_official.html`, §5.8. Tinted background, 3px left rule in
`--hub-blue`, an "Official" `hub-pill hub-pill--primary` in its corner, and a muted footer line:
"From the equipment register. Members cannot edit this." Only on pages with `equipment` set.

**5. TOC — a horizontal chip row.** `pl-wp-toc`, pinned under the header at 44px tall, horizontally
scrollable in its own container, one `pl-wp-toc__chip` per `h2`/`h3` from `toc()` (which now returns real
entries thanks to D1). Scrollspy adds `--active` on the chip for the section in view. Not a sidebar, not
an accordion. Hidden when the body has fewer than two headings. `position: sticky` under the topbar on
desktop as well — one behavior, two widths.

**6. Body** — `<div class="pl-md pl-wp-body">{{ page.body|wiki_content }}</div>`. `.pl-md` is the existing
rendered-body typography base; `pl-wp-body` adds the brief's §5.6 reading rules: `font-size: 17px`,
`line-height: 1.6`, `img { max-width: 100%; height: auto; border-radius: 8px }`, and
`table { display: block; overflow-x: auto }` so a wide table scrolls **inside its own container** and the
page never scrolls horizontally.

**7. Photos and attachments** — `_wiki_attachments.html`, one `hub-card` titled **"Files And Photos"**.
Image attachments render first as a `pl-wp-photos` grid (`minmax(9rem, 1fr)`), each with its label as a
caption; documents and links render below as `pl-wp-attach__card` rows — icon by extension, label,
`size_label`, and "Added by Sam R." Every file link is `target="_blank" rel="noopener"`. The card header
carries the **"+ Add A Photo"** button for `can_edit_wiki_page`. Empty state: "Nothing attached yet." plus,
for an editor, "[Add a photo]".

**8. Related Pages** — up to three, same kind or same guild, ordered by recency, as `_wiki_card.html`
rows under an `<h2>` reading **"Related Pages"**. Section hidden when empty.

**8b. Share This Page** — a `hub-card` titled **"Share This Page"** holding
`templates/hub/partials/_wiki_qr_share.html`, which wraps `components/qr_share_card.html` with
`qr_svg`, `share_url` (`/m/<code>/`), `svg_url` / `png_url` (`hub_wiki_qr_download?fmt=…`),
`title="Share This Page"` and a hint reading "Print it and tape it to the machine." Rendered for
`can_edit_wiki_page`.

**This card has a real desktop home**, which an earlier draft did not give it: it sat inside the phone-only
Details disclosure, so on a desktop — the only place anyone actually prints a sticker — it did not exist.
On desktop it is its own card between Related Pages and the byline. On a phone it is the last block inside
the Details disclosure, because a member standing at the machine does not need a QR code for the page they
are already reading. The component is already form-safe (readonly input with no `name`, every control
`type="button"`), so nesting it costs nothing.

**9. Byline / Details.** Desktop: a muted footer line — "Started by Dana R. 12 Jan. Last edited by Sam R.
3 Mar. Last checked by Kate O. 3 Mar. 12 versions saved." When verified, it additionally reads "Verified
by Kate O. (Woodworking orienter), 3 Mar" — the brief's exact shape. **The "(Woodworking orienter)" part
reads `verified_role_label`, which is spec B's column, not A's** (brief §9.1). A's byline template prints
it with an `{% if %}` guard and reads nothing until B's migration lands; A must not add the field. On a
phone the whole byline collapses into a `<details class="pl-wp-details"><summary>Details</summary>`
disclosure, closed by default.

**10. Sticky bottom bar (phone only)** — `pl-wp-actionbar`, `position: fixed`, the same actions as the
desktop row under the same gate: **Add Photo**, **Add Tip**, and an overflow holding **Edit** and
**Still accurate**. Spec D inserts **Report** as the third primary button and moves Still accurate under
the overflow.

- **The overflow is `components/row_actions.html`**, not a new menu: `menu_include` points at a small
  `_wiki_page_menu.html` of `role="menuitem"` links and `menu_label="More actions for this page"`. It is
  Alpine, `position: fixed`, keyboard-navigable, and flips upward near the viewport bottom — which is
  exactly where a bottom bar puts it. Building a second overflow menu here would be a second thing to get
  wrong.
- **The bar is hidden entirely when `can_edit_wiki_page` is false and on an archived page.** A member on
  an Official page gets the reading column and nothing fixed to the bottom of it.
- **`body` gets `padding-bottom: 5.5rem` while the bar is rendered**, applied by a `pl-wp-has-actionbar`
  class on the page wrapper inside the same `@media (max-width: 768px)`. Without it the bar covers the
  last two lines of the byline and the final attachment row, and a `position: fixed` element gives no
  warning that it is doing so.
- No hover-dependent affordances, no swipe-to-reveal, no long-press, no drag — every one of those fails
  with gloves. The bar is `display: none` above 768px, set in a CSS class (never an inline `display`,
  which Alpine's `x-show` strips).

**Print (A2).** A new `@media print` block in `hub.css`, next to the meeting-workspace block and reusing
its global `.hub-sidebar` / `.hub-topbar` hides. It additionally hides `.pl-wp-actionbar`, `.pl-wp-toc`,
`.pl-wp-related`, `.pl-wp-qrshare`, and every `.pl-btn`; forces `.pl-wp-details` open
(`details { display: block }` and `summary { display: none }`); forces black-on-white; and **reveals
`.pl-wp-printhead`**, an element that is `display: none` on screen and carries the page's absolute URL and
"Last checked 3 Mar 2026" so a sheet taped to a machine says where it came from and how old it is. The
"Print / Save PDF" button is the `pl-flyer-print-btn` idiom from `guild_flyer.html` — a real visible
action, not a hidden keyboard shortcut.

**States.**

- *No body yet* (a fresh stub): the body area shows "Nobody has written this one yet. You probably know
  something." plus an **"+ Add What You Know"** primary button into the editor — the brief's "every
  contribution is an edit" mechanic made literal.
- *Not found (a bad slug):* `templates/hub/wiki_not_found.html`, returned as
  `render(request, ..., status=404)`. **Not `get_object_or_404`**, which renders the site-wide
  `templates/404.html` — a page reading "We couldn't find that page." and offering "Browse Past Lives
  classes", which is the wrong answer for a member standing in a shop. The wiki's version says
  "There is no page at that address." and carries a `table_search.html` box aimed at `/wiki/search/` plus
  a link to `/wiki/`. It is the sibling of `wiki_qr_missing.html` (§6.12), which says something different
  because a printed sticker is a different failure.
- *No permission to edit:* no button, no message (an Official page simply has no edit affordance).
- *Archived:* item 2's notice, no action row, no bar.

**Dark + light.** The Official block's tint is `color-mix(in srgb, var(--hub-blue) 8%, var(--hub-card-bg))`
so it reads as a tint in both themes. **The amber surfaces are literal hex** — the `.pl-confirm-warn` pair
and its `[data-theme="light"]` override, copied per §6's colors note; `--hub-warn` does not exist. Every
pill is a `.hub-pill` modifier. Verify both themes.

---

### 6.4 Starter chooser — `/wiki/new/` · `templates/hub/wiki_new.html` (A3)

- `page_header.html` **"Start A Page"**, description "Pick what kind of thing it is. You can change your
  mind later."
- **Six cards** in a `pl-wp-starter-grid` (`repeat(auto-fill, minmax(15rem, 1fr))`, one column on phones):
  icon, title ("Machine or tool"), and one line of plain copy ("A saw, a kiln, a press. What it does and
  how not to break it."). Each card is a 48px+ tap target linking to `/wiki/new/<kind>/`.
- **The grid renders from `membership/wiki_starters.py` data, never from hardcoded markup.** That is the
  seam **spec D** extends with its seventh **Safety & Rules** card (which sets `status=OFFICIAL` alongside
  the picked kind); a template full of six hand-written `<a>` blocks would force D to edit A's markup.
- **Every card carries the incoming query string through**, unchanged: `?guild=<slug>`, `?title=<text>`,
  and **`?wanted=<pk>`**. Every "Start this page" link spec B renders — from the wanted list, the guild
  tab, the failed-search panel, and B's zero-result partial — arrives here, and the parameters have to
  survive the one hop to `/wiki/new/<kind>/` or B's whole wanted-page loop silently never completes.
- Below the grid, a muted line: "Not sure? Pick How to do something. Nothing here is hard to move." — a
  chooser with no escape hatch is a chooser people abandon.
- **Cancel:** "Back to the wiki" link. No dead end.
- **States.** *A `?wanted=<pk>` that is already fulfilled, deleted, or not visible:* the parameter is
  dropped silently and the chooser renders normally — a stale link from a month-old digest must never be
  an error screen. *Not an active member:* a `hub-card` instead of the grid — "Your membership needs to be
  active to write here." with a link to Settings.

---

### 6.5 Create a page — `/wiki/new/<kind>/` · `templates/hub/wiki_edit.html` in create mode (A3)

Dedicated page (well past the 4+ field threshold; never a modal). The `<form>` carries
**`enctype="multipart/form-data"`** — the attachment rows hold file inputs, and without it every upload
silently vanishes with no error anywhere. It also carries `{{ facts_formset.management_form }}`,
`{{ attachments_formset.management_form }}`, and a hidden `base_revision` (§5.10).

- **Fields, every one through `components/form_field.html`** (which wraps each in `.pl-form-group`):
  - **Title** — required. Hint: "What would you type into search to find this?"
  - **"Who is this for?"** — the Scope `<select>`, built from `editable_wiki_scopes(request)`,
    **pre-filled** from `?guild=` or from the single guild the member belongs to, with
    "Everyone (space wide)" as the blank label. One of the two required fields the brief allows.
  - **"What kind of page is this?"** — the Kind `<select>`, **pre-filled** from the URL segment. The other
    required field. Visible and changeable, because a pre-filled field the user cannot see is a field they
    cannot fix.
  - **The two labels are deliberately not the field names (D12).** "Scope" and "Kind" are what the columns
    are called; a member filing their first page is answering a question, not populating a column. The
    `Kind` choice *labels* already read this way ("Machine or tool", "How to do something") and the field
    label should match their register. The model, the queryset methods, and every internal reference keep
    `guild` and `kind`.
  - **Quick Answers** — the fact list editor (§6.6), pre-seeded with that kind's prompt labels and blank
    values, because the brief says the starter template must prompt for facts *first*.
  - **Body** — the Quill editor (`PageContentEditorWidget(markdown_profile="wiki")`,
    `data-rte-toolbar="wiki"`), pre-filled with the kind's starter headings.
    `{% include "_components/rich_editor_assets.html" %}` sits at the top of the template.
  - **No Safety toggle.** Struck by brief §9.1 — **spec D owns the safety gate**, via its Safety & Rules
    starter card and `status == OFFICIAL` (§5.3). A create form that offered a "this is a safety page"
    tickbox and an edit form that did not is exactly the hole D's gate exists to close.
- **The starter prompts need `extra=len(prompts)`, not `extra=0`.** A `ModelFormSet` renders
  `initial_form_count() + extra` rows, which is `0 + 0` for a brand-new page, so `extra=0` in create mode
  renders **no** prompt rows at all and the "prompts for facts first" mechanic silently does not exist.
  Create mode builds the fact formset with `extra=len(prompts)` and
  `initial=[{"label": p, "value": ""} for p in prompts]`; **edit mode keeps `extra=0`** per Rule 11. And
  `WikiPageFactForm.clean()` must treat **a row with a label but no value as empty** (return `{}` /
  `cleaned_data["DELETE"] = True`), or an untouched prompt row fails its required `value` and blocks Save —
  the exact Rule 11 bug that rule exists to prevent, arriving through the back door.
- **Editor footer copy, immediately under the body field, where the fear is:**
  "Every version is saved. Nothing here can be lost." **And the second line, "Rough is fine. Someone will
  tidy it.", sits immediately ABOVE the Save button, not under it** — Rule 21 says nothing goes below Save,
  and Rule 18 wants the button clear of what precedes it, so both hint lines go above with ≥1.5rem between
  the last of them and the button. These two strings are the brief's §7 anti-fear mechanism and are not
  decoration.
- **Save:** one primary button, labeled exactly **"Save"**, the **last** element in the form with nothing
  beneath it. Posts the whole form — title, body, facts, and attachments together.
  Success → redirect to the new page + a Django message "Page created. Thanks for writing it."
- **`?wanted=<pk>` closes B's loop.** When the create POST succeeds and a valid `wanted` pk rode in,
  the view calls `WikiWantedPage.fulfil(page)` (spec B's method, one line, lazily imported until B lands)
  and the success message becomes "Page created. That was on the Wanted list. Thanks for writing it."
  Without this call a lead can add a wanted page and never mark it done except by deleting it, which
  discards the credit and leaves B's "Already Written" section permanently empty (brief §9.4).
  A fulfilled, deleted, or invisible pk is ignored silently — the page is still created.
- **Cancel:** a ghost "Cancel" link back to `/wiki/new/`.
- **Autosave** runs here too, into a `page=NULL` draft keyed on (author, kind), so a member who closes the
  tab mid-sentence finds it in `/wiki/drafts/`, and the resume banner (§5.10) offers it back.
- **States.** *Validation error:* inline via `form_field.html`, nothing lost, the draft still holds the
  text. *Duplicate title in the same scope:* a form error, "Woodworking already has a page called
  'Table Saw'. [Open it] or pick another name." — never a silent second page. *Reserved slug:*
  "That name is reserved. Pick another title." *Not an active member:* the create view 403s and the
  starter cards were never shown. *A draft already exists for this (author, kind):* the resume card
  (§5.10).
- **Dark + light:** every control inside `.pl-form-group`; `select option { background; color }` styled
  (native option popups do not inherit); the Quill surface uses the existing `rich-editor.css` theming.
- **Mobile:** single column, full-width controls, the Save button full-width and above the sticky
  keyboard.

---

### 6.6 Edit a page — `/wiki/p/<slug>/edit/` · `templates/hub/wiki_edit.html` (A3)

Same template, edit mode. Everything from §6.5 plus:

- **`{% block wiki_editor_banners %}`**, the first thing inside the `<form>`, above the title field. A's
  resume card (§5.10) renders here. **Spec D's lock warning and conflict notice render here too** — A
  ships the block, D fills it. There is no `pl-wp-lockbanner` and no `pl-wp-conflict` in this spec; brief
  §9.1 gave the advisory lock and the conflict save to D, including its own
  `/wiki/p/<slug>/conflict/<pk>/` screen. A's contribution is the hidden `base_revision` field D reads and
  the `WikiEditLock.refresh(page, member)` call in the autosave view.
- **`{% block wiki_editor_footer %}`** under the body field, beside A's "Every version is saved" line, for
  anything D needs to say to an editor.
- **Draft resume card** (§5.10) — "You have unsaved changes from 12 minutes ago." with
  **[Use My Draft]** and **[Start From The Saved Page]**, the saved page as the default.
- **Equipment** — a `<select>` rendered **only for `can_moderate_wiki_page`**, listing active `Equipment`
  with a blank "Not about a specific tool" option. Hint: "Links this page to the tool's official block and
  to its QR sticker." This is the hand-fix for a mis-seed (§5.11): without it, detaching a wrongly adopted
  page or attaching an orphan needs a Render shell, which means it never happens. A member never sees this
  field, and the seeder's unique constraint still refuses a second machine page for one tool, so a
  moderator gets a clear form error rather than a 500.
- **Verification warning**, when the page is `GUILD_VERIFIED` and the editor cannot verify:
  a muted line **above** Save — "This page is guild verified. Saving moves it back to Community until
  someone checks it again." Honest, and it stops the drop from being a surprise. (Above, not below:
  Rule 21.)
- **Autosave savestate pill** — `pl-wp-savepill`, copied from the meeting workspace's Alpine savestate
  component: "Saving…" / "Saved ✓ just now" / "Saved ✓ a moment ago" / "Couldn't save. Check your
  connection." Fields carry `data-autosave data-saved="…"` and post through `htmx.ajax` with a 700ms
  debounce; the endpoint answers 204 with `HX-Trigger: {"wiki-saved": {}}`, 400 for an unlisted field,
  422 with an error toast for an invalid value, and the client reverts a 422'd field to its `data-saved`
  value. This is the shipped contract, not a new one.
- **What autosaves, exactly.** The allowlist is **`title` and `body`, and nothing else.** Fact rows and
  attachment rows do **not** autosave: a formset row is only meaningful as part of a complete `TOTAL_FORMS`
  submission, a half-typed row would need its own validity story, and a file input cannot be debounce-posted
  at all. Every unlisted field name is the 400 branch, which is what makes the allowlist testable.
  Consequently the drafts screen (§6.10) says **"Your unsaved title and writing are kept here"**, not
  "unsaved changes" — a member who added three quick answers and closed the tab must not be told they were
  saved. The editor's savestate pill carries the same qualifier on hover: "Title and writing are saved as
  you type. Quick answers and files save when you press Save."
- **The whole form still saves at once.** One "Save" button at the bottom commits title, body, facts, and
  attachments together; autosave is a crash net, not a second save path.

#### The Quick Answers list editor — `templates/hub/partials/_wiki_fact_rows.html`

The FAQ and Links editors in `templates/hub/guild_edit.html` copied verbatim, with the Slideshow reorder
bolted on.

- Container `<div id="wp-fact-rows">`, one `<div class="hub-card pl-wp-factrow">` per row containing
  `label` and `value` through `form_field.html`, `{{ f.sort_order }}` hidden, and **`{{ f.id }}`**.
  The `id` field is not optional decoration: without it a `ModelFormSet` cannot match a submitted row to
  its instance and the save silently creates duplicates instead of updating. It is present in the
  canonical `guild_edit.html` editors and was missing from an earlier draft of this section.
- **"+ Add A Quick Answer"** — `hub-btn hub-btn--sm`, `margin-top:1rem`, the canonical inline `onclick`
  that clones `#wp-fact-empty-template`, replaces `__prefix__` with the new index, appends to
  `#wp-fact-rows`, and bumps `id_facts-TOTAL_FORMS`.
- **`extra` differs by mode** (§6.5): **`extra=0` in edit mode** so no perpetual blank row can block Save
  (Rule 11), and **`extra=len(prompts)` with `initial=` in create mode**, because a `ModelFormSet` renders
  `initial_form_count() + extra` and `0 + 0` on a new page means the starter prompts do not render at all.
  Paired with that, `WikiPageFactForm.clean()` treats a row with a label and no value as empty, so an
  untouched prompt never blocks Save.
- **Per-row Delete** for a saved row (`{% if f.instance.pk %}`): `{{ f.DELETE }}` rendered inside
  `<div style="display:none;">` and driven by a real
  `<button type="button" class="pl-btn pl-btn--danger pl-btn--sm" style="margin-top:0.75rem;"
  onclick="document.getElementById('…').checked = true; this.form.requestSubmit();">Delete</button>` —
  which submits the whole form, so every other edit on the page is preserved. Never a toggle.
- **Per-row Remove** for a cloned, unsaved row: same classes and margin, `onclick="this.closest('.hub-card').remove();"`
  — no save needed, and an abandoned half-filled row can never block Save.
- **Reorder (D7):** a `⠿` drag grip on desktop and real **↑ / ↓** buttons (44px) that are the only visible
  control under `@media (hover: none)`, both rewriting every row's hidden `sort_order` to its visual
  index — the Slideshow Slides reorder handler in `templates/hub/admin/site_settings.html`, delegated on
  `#wp-fact-rows` so cloned rows work.
- **Validation:** duplicate label in one save → "You have two answers called 'Blade'. Rename one."
  More than eight rows → "Quick answers work best short. Keep it to eight."
- **Empty state:** `{% empty %}<p class="hub-text-muted pl-wp-factrow__empty">No quick answers yet. These
  are the four or five things people actually came for.</p>`
- **Save:** the page's single "Save" button at the very bottom saves facts, attachments, title, and body
  together. There is no separate per-block save, because a member editing on a phone should press Save
  once.

#### The Attachments list editor — `templates/hub/partials/_wiki_attach_rows.html`

Identical structure on `#wp-attach-rows` / `#wp-attach-empty-template` / `id_attachments-TOTAL_FORMS`,
`extra=0` in both modes (attachments have no starter prompts), and **`{{ f.id }}` on every row**. The
enclosing `<form>` carries **`enctype="multipart/form-data"`** (§6.5) — without it these file inputs post
nothing at all and the failure is silent.

- Row fields: **Label** (required, hint "One line. 'Blade change steps' beats 'scan_0034'."),
  **File**, **Link**, hidden `sort_order`.
- The file input is a **drop zone**, never a bare `<input type="file">` (Rule 16): the
  `.cls-image-upload-zone` markup with `.cls-image-upload-label` / `.cls-image-upload-hint`, plus a
  `.pl-help` "?" bubble giving the caps ("Photos or documents, up to 10 MB for a photo and 25 MB for a
  file."). **`components/image_field.html` is deliberately not used here** — it binds a per-field
  `modal_id` and a `id="image-preview-{{ field.auto_id }}"` that collide on clone, and its inline script
  never executes inside cloned `innerHTML`. Drag, drop, and preview come from **one delegated handler on
  `#wp-attach-rows`**, the Slideshow Slides drop-zone pattern in `templates/hub/admin/site_settings.html`,
  including its `hasFiles()` guard so a row-reorder drag does not light up the drop zone.
- **"+ Add A File Or Link"**, per-row **Delete** / **Remove**, and the same ↑/↓ reorder, all exactly as
  above.
- **Validation:** both file and link → "Pick a file or paste a link, not both." Neither → "Add a file or
  a link." Blank label → "Give it a one line name so people know what it is." Bad extension or oversize →
  the validator's message, inline.
- **Empty state:** "Nothing attached yet. A photo of the setup helps more than a paragraph."

---

### 6.7 Quick photo — modal on the reading page (A3)

Two fields, so a modal plus a toast (the interaction table). **Gated on `can_edit_wiki_page`** — the
trigger, the modal, and `hub_wiki_quick_photo` itself (brief §9.3).

- **Trigger:** "Add Photo" in the sticky bottom bar on a phone, and the **"+ Add A Photo"** button in the
  desktop action row (§6.3 item 0b) and in the "Files And Photos" card header. Opens
  `components/modal.html` with `modal_id="wiki-photo"`, `modal_title="Add A Photo"`, `modal_size="sm"`.
- **Fields, both through `form_field.html`:** the photo (a `.cls-image-upload-zone` drop zone with
  `accept="image/*"` and, on a phone, `capture="environment"` so the camera opens directly) and a required
  one-line **Caption** ("What are we looking at?"). The form carries
  `enctype="multipart/form-data"`.
- **Submit:** **"Add Photo"** (`pl-btn pl-btn--primary`), posting to `hub_wiki_quick_photo`. Success →
  the attachments card re-swaps via HTMX and a toast fires: "Photo added. Thanks."
- **It does not touch the body, so it does not drop verification** (D6). This asymmetry is intentional:
  the cheapest contribution should also be the one with no consequences, because on a phone in a shop
  that is the only contribution most people will ever make.
- **States.** *Uploading:* the submit button goes `hx-disabled-elt="this"` and the zone gets the existing
  `.uploading` class. *Too large:* the client-side size guard rejects before the round trip with an inline
  `pl-field-error`, and the server validator repeats it. *Wrong type:* "That is not an image. Use the file
  list below for documents." *No permission:* the button is not rendered and the endpoint 403s.
  *Failure:* an error toast, the modal stays open, nothing is lost.

---

### 6.8 Quick tip — modal on the reading page (A3)

**Gated on `can_edit_wiki_page`**, trigger and endpoint both (brief §9.3) — the tip route writes the body
through `apply_edit`, so it is an edit wearing a smaller coat.

- **Trigger:** on a phone, "Add Tip" in the sticky bottom bar. **On desktop, the "+ Add A Tip" button in
  the action row** (§6.3 item 0b) — an earlier draft said "the card header" but tips have no card, so
  desktop had no trigger at all. `components/modal.html`, `modal_id="wiki-tip"`,
  `modal_title="Add A Tip"`, `modal_size="sm"`.
- **One field, through `components/form_field.html`:** a `forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))`
  on `WikiQuickTipForm`, rendered `{% include "components/form_field.html" with field=form.tip %}`.
  **Not a hand-rolled `<textarea>`** — Rule 1 says every field goes through the component, and the
  component is also what supplies the `.pl-form-group` wrapper that keeps the control from rendering as a
  browser-default white box on the dark theme. Hint: "One or two sentences. What do you wish someone had
  told you?"
- **Submit:** **"Add Tip"**. The server appends the sanitized text as a `<p>` under a stable
  `<h2>Tips From Members</h2>` section (created if absent), through `apply_edit`, so it writes a revision
  like any other edit.
- **The verification warning is shown before the member commits.** When the page is `GUILD_VERIFIED` and
  the member cannot verify, the modal body carries a muted line: "This page is guild verified. Adding a
  tip moves it back to Community until someone checks it." — because a tip *is* unreviewed text on the
  page, and the locked rule admits no exception (D6). Saying so up front is the difference between a rule
  and a trap.
- **Success:** toast "Tip added. Thanks." and the body re-swaps.
- **States.** *Empty:* "Write a sentence first." *Body at its cap:* the `WikiError` message, "This page is
  full. Try editing it instead," with an Edit link. *No permission:* no button, endpoint 403s.
  *Failure:* error toast, modal stays open.

---

### 6.9 "Still accurate" — confirm on the reading page (A3)

- **Trigger:** a `pl-btn pl-btn--sm` in the action row reading **"Still accurate"**, plus an entry in the
  phone overflow (`components/row_actions.html`). Shown to **any active member** (D5) — this is the one
  action that does not need `can_edit_wiki_page`. Hidden on an archived page (§6.3 item 0b).
- **Guard:** `components/confirm_modal.html` with
  `confirm_id="wiki-confirm"`,
  `confirm_title="Still accurate?"`,
  `confirm_message="You are saying this page matches what is actually in the space today. Your name goes on it."`,
  `confirm_button_text="Yes, it is accurate"`,
  `confirm_button_style="primary"`,
  **`confirm_hx_post="{% url 'hub_wiki_confirm' page.slug %}"`**, and
  **`confirm_hx_target="#wiki-status"`**.
  One tap plus one confirm — confirming must stay cheaper than editing or nothing gets confirmed.
- **Those two HTMX parameters are load-bearing.** `confirm_modal.html`'s **default is a plain full-page
  POST form**; it only switches to `hx-post` when `confirm_hx_post` is passed. An earlier draft specified a
  toast plus an OOB swap without naming them, which would have full-page-posted and reloaded instead.
  Note also that the component hardcodes `hx-swap="outerHTML"` whenever a target is given — there is no
  `hx_swap` parameter to pass.
- **The response is 200, not 204.** `hub_wiki_confirm` returns
  `render(request, "hub/partials/_wiki_status_oob.html", …)` — the status block with `hx-swap-oob="true"`
  on `#wiki-status` (pill + status note) and on `#wiki-confirm-cue` (the `?confirm=1` card, §6.3) — then
  `trigger_toast(response, "Thanks. Marked as checked today.")`. **A 204 carries no body, so it cannot
  carry an OOB swap**: the toast would fire and the out-of-date pill would sit there unchanged until the
  next reload, which is precisely the "did that work?" failure the one-tap action exists to avoid
  (brief §9.2). Order matters if D or B ever adds a client event here: `trigger_toast()` **overwrites**
  `HX-Trigger` while `trigger_client_event()` merges, so set the toast first.
- **Success:** the pill drops from "Out of date" to "Community", the status note disappears, the cue card
  disappears, and the toast fires. No reload.
- **States.** *Already checked today:* the control renders as muted text "Checked today", not a button.
  *Not an active member:* not rendered. *Archived page:* not rendered; a crafted POST answers **409** with
  an error toast, "This page is archived. There is nothing to confirm." *Failure:* error toast, the modal
  closes, nothing changed.

---

### 6.10 Drafts — `/wiki/drafts/` · `templates/hub/wiki_drafts.html` (A3)

- `page_header.html` "Your Drafts", description "Things you started. Only you can see these."
- One `hub-card` per draft: the title (or "Untitled machine page"), the scope and kind as muted text,
  "Last saved 12 minutes ago", a primary **"Keep Writing"** button into the editor, and a
  **"Discard"** `pl-btn pl-btn--danger pl-btn--sm` (`margin-top:0.75rem`) behind
  `components/confirm_modal.html`: "Discard this draft? The text is not saved anywhere else." →
  toast "Draft discarded."
- Unpublished pages held by **spec D's safety gate** appear here too, with the muted line "Waiting for a
  guild lead to publish it," so a member whose page went to the queue is never left wondering where it
  went. A ships this row and the `published()` filter behind it; D owns the gate that sets the flag.
- **Empty state:** "No drafts. Your title and writing are saved here automatically as you type."
- **The copy says what actually autosaves.** "Last saved 12 minutes ago" refers to the **title and the
  writing only** (§6.6's allowlist); each card carries a muted second line, "Quick answers and files are
  saved when you press Save." A drafts screen that says "unsaved changes" flatly, when three quick-answer
  rows were never in the draft, is a promise the store does not keep.
- **Mobile:** cards stack; both buttons are full-width and 48px.

---

### 6.11 Sticker sheet — `/wiki/stickers/` · `templates/hub/wiki_sticker_sheet.html` (A4)

A standalone print document, not a hub page — the `guild_flyer.html` idiom, with its own
`static/css/wiki-stickers.css` carrying `@page { size: letter; margin: 0.5in }`.

- **Gate:** `is_effective_staff` — a printable staff-wide sheet has no page in scope, so it cannot use
  `can_moderate_wiki_page(request, page)`. Filters: `?guild=<slug>` and `?kind=machine` (default), so a
  lead prints one shop at a time.
- **Toolbar** (`no-print`): a **"Print / Save as PDF"** button (`onclick="window.print()"`) and the hint
  "Print on label sheets or plain paper and tape them on. Use your browser's Print dialog and choose Save
  as PDF."
- **The sheet:** a 3-across grid of `pl-wp-sticker` cells, each containing the QR SVG (from the existing
  `qr_svg()` helper, scalable because it carries a `viewBox`), the **machine name in large text
  underneath**, and the short URL in small type — `members.pastlives.space/m/K7R2QW`. The name and the
  typed URL are there so a member with a dead phone battery or a scratched code can still find the page,
  which is the whole reason the code is short and the alphabet has no `O` or `I`.
- **States.** *No pages match:* "No machine pages in that guild yet. Run the equipment seed first."
  *Feature flag off:* 404, like every other wiki route.
- **Not theme-aware on purpose:** it is a print document and is black on white in both themes, exactly
  like `guild-flyer.css`.

---

### 6.12 QR landing and its dead end — `/m/<code>/` (A4)

- **Signed in:** an immediate 302 to the page. No interstitial — a member holding a phone next to a
  running machine gets the page, not a welcome screen.
- **Signed out:** 302 to the login page with `?next=<the page URL>`, so finishing the emailed-code login
  lands on the machine they are standing in front of. This is the single most important detail of the QR
  feature; without it every first scan lands on the home page and the sticker teaches people it does not
  work.
- **Unknown or retired code:** `templates/hub/wiki_qr_missing.html`, returned as
  `render(request, ..., status=404)` — a friendly page inside the hub shell: "That sticker points at a
  page that has moved." plus a `table_search.html` box aimed at `/wiki/search/` and a link to `/wiki/`.
  **Never `get_object_or_404`**, which would render the site-wide `templates/404.html` and offer to
  "Browse Past Lives classes" (§2 correction 10). Its sibling for a bad slug is `wiki_not_found.html`
  (§6.3); the copy differs because a printed sticker and a bad link are different failures.
- **The per-page share card has a real desktop home** — its own **"Share This Page"** `hub-card` on the
  reading page for `can_edit_wiki_page` (§6.3 item 8b), folded into the phone Details disclosure only at
  the narrow width. An earlier draft put it in the phone-only disclosure, which meant it did not exist on
  the one device anybody prints a sticker from.
- **`hub_wiki_qr_download`** is a real named route in §3's URL map — `wiki/p/<slug:slug>/qr/?fmt=svg|png`,
  gated on `can_edit_wiki_page`, in the `guild_qr_download` shape. It was missing from an earlier draft's
  map while the share card referenced it, which is a `NoReverseMatch` on the reading page.
  `components/qr_share_card.html` is already form-safe (readonly input with no `name`, every control
  `type="button"`), so it can sit inside the page without riding along on any submit.

---

## 7. Activity, notifications, and what this spec hands to B, D and E

**This spec sends no email and emits no spine event.** The wiki's notifications — `wiki.page_reported`
(D), `wiki.page_verified` (**D owns the `Trigger`, the resolver, the copy and the `period`**, per brief
§9.1; it is the round's retention mechanism), `wiki.guild_digest_monthly` (B) — all belong to specs that
own both the trigger and the audience. Adding an `EventType` here with no sender would be a dead registry
entry.

**Activity rows this spec writes**, through `SiteActivity.log(kind, *, actor=, target=, payload=)`:

| Kind | When | Payload |
|---|---|---|
| `WIKI_PAGE_CREATED` | `create_page` | `{"slug", "kind", "guild": <name or null>, "seeded": bool, "adopted": bool}` |
| `WIKI_PAGE_EDITED` | `apply_edit` | `{"slug", "dropped_verification": bool, "via": "editor"｜"tip"｜"photo"}` |

A ships **all six** wiki `SiteActivity.Kind` values in the A1 migration (§4.7) and writes these two; the
other four are inert until B and D write them.

**What this spec hands to the other specs.** Nothing below needs a migration of its own on `WikiPage`.

| Consumer | What it gets from A |
|---|---|
| **B** — guild Wiki tab | `WikiPageQuerySet.for_guild(guild)`, `filtered(...)`, `with_fact_prefetch()`, and `_wiki_card.html` with its `snippet` / `source_chip` / **`actions_partial`** parameters — the tab is a filtered view of this store and B's compact Verify control renders into the slot. |
| **B** — Verify button | `status`, `verified_by`, `verified_at`, `unverified_reason`, `last_checked_at`, `last_checked_by`, and **`can_verify_wiki_page`** (A ships it, including the guard that an Official page is never verifiable). Verifying is: set the three, clear `unverified_reason`, stamp `last_checked_at`, write a revision with `note="Verified"`. |
| **B** — overdue list and its "See all" | `needs_review()`, `review_due_at`, and `/wiki/search/?guild=<slug>&stale=1`. |
| **B** — grouped-kind "See all" | `/wiki/search/?guild=<slug>&kind=<kind>`, and `?scope=all` to drop the guild. |
| **B** — the wanted-page loop | `hub_wiki_new` and `hub_wiki_create` carry `?guild=`, `?title=`, **`?wanted=<pk>`**, and the create view calls **`WikiWantedPage.fulfil(page)`** on success (§6.5). |
| **B** — the digest CTA | `/wiki/p/<slug>/?confirm=1` renders the confirm cue card (§6.3 item 0c). |
| **B** — the zero-result screen | A's search **includes B's `_wiki_search_empty.html`**; A ships no zero-result copy of its own. |
| **B** — failed-search panel | The named hook point in `hub_wiki_search`, immediately after the `wiki` group's `results` is built, and only when `q` is non-empty. |
| **D** — the review banner | `needs_review_since` + `needs_review_reason` as **denormalized columns D maintains**, which A's pill precedence, `needs_review()`, and the search-result chip all read. D renders the banner from `WikiReport`; A renders no banner. |
| **D** — archive UI | `archived_at`, `archived_by`, `archive_reason` (all three are A's per brief §9.1; D adds only `archive_redirect`), plus A's archived notice on the reading page. |
| **D** — revert and history | `WikiRevision` with `title`, `body`, `facts`, `status`, **`kind`** (`SAVE` / `REVERT` / `CONFLICT_DRAFT`, all three shipped by A), and `note`. No `body_format` column — the body is dual-mode and sniffed. |
| **D** — the contributor notification | **`WikiRevision.author` with `related_name="wiki_revisions"`.** `Member.objects.filter(wiki_revisions__page=page)` is D's whole resolver. |
| **D** — the advisory lock | `hub_wiki_autosave` calls `WikiEditLock.refresh(page, member)`; the edit form carries a hidden `base_revision`; `wiki_edit.html` exposes `{% block wiki_editor_banners %}` and `{% block wiki_editor_footer %}`; `wiki_page.html` exposes `{% block wiki_page_actions_extra %}` for Report. |
| **D** — the safety gate | `is_published`, `published()`, `create_page(..., status=…)`, the data-driven starter grid D adds its Safety & Rules card to, and the `/wiki/drafts/` row that explains a held page. **A ships no Safety toggle.** |
| **D** — moderation permission | A **consumes** `can_moderate_wiki_page(request, page)`; A2 carries a private `_can_moderate_wiki_page` with D's exact body, which D deletes when it lands the public one (§5.5). |
| **D** — activity vocabulary | All six `SiteActivity.Kind` values, in A1. D drops its own migration for them. |
| **E** — governance mirror | `hub_wiki_search`'s **grouped results** (`{source_label, source_slug, results}`) and its **`?source=`** chip row; `_wiki_card.html` as the shared result partial (E's `_search_result.html` ask — one partial, A's name); and A's `pl-wp-` search chrome. E adds the `policies` group and nothing else. |

**Fields A explicitly does NOT ship**, so nobody adds them twice: `verified_role_label` and
`verified_note` (**B's migration**, brief §9.1 — A's byline prints the role label behind an `{% if %}`),
`archive_redirect`, `official_note`, `WikiReport`, `WikiEditLock`, and any `Status.NEEDS_REVIEW` value
(there is none; "needs review" is a chip derived from `needs_review_since`, not a status).

---

## 8. Build order — four sequential PRs, each ships green

A is the first spec of the round and **B and D branch off A's merged main**, so every PR here must leave
main green and must land the seams B and D read before they start. The round order is
**A → (B, D in parallel) → E** (brief §5).

Each PR: targeted suite plus `ruff check . && ruff format --check .`, `mypy .`, and `manage.py check`
green; `VERSION` bumped in `plfog/version.py`. Coverage gates at `fail_under = 98` with branch coverage on
by default, so every new branch needs a spec in the same PR.

### PR A1 — The store (invisible)

1. `WikiPage` + queryset (including `filtered()` and `visible_for()`), `WikiPageFact`, `WikiRevision`
   (**with `Kind` and `author` `related_name="wiki_revisions"`**), `WikiAttachment`, `WikiDraft`,
   `WikiError`; one additive migration; `manage.py check`.
2. `membership/wiki_starters.py` — the six kinds' starter bodies and fact prompts, as **module data the
   chooser renders from**, so D can add its seventh card without editing A's template.
3. The `wiki` sanitizer profile, `sanitize_wiki_html`, `_inject_heading_ids`, `render_wiki_content`,
   `sanitize_wiki_submission`, the `wiki_content` filter; the two-line widening of `render_markdown`'s
   profile guard and the two ValueError specs that pin it.
4. `validate_wiki_upload` in `core/validators.py`.
5. The four permission helpers — `can_edit_wiki_page`, `can_verify_wiki_page`, `visible_wiki_pages`,
   `editable_wiki_scopes` — plus the private `_can_moderate_wiki_page` D replaces (§5.5). **No
   `can_moderate_wiki`.**
6. `SiteConfiguration.wiki_enabled` (`default=False`) + `feature_flags` + `SiteSettingsForm.Meta.fields`;
   the `wiki_link_enabled` help-text repurpose; **all six `SiteActivity.Kind` wiki values**.
7. Factories for all five models.
8. **No UI, no URLs.** Nothing member-facing → **no changelog entry** (D8).

**This is the PR B and D wait for.** Before merging it, check the four seam items by name: `WikiRevision.kind`
including `CONFLICT_DRAFT`, `related_name="wiki_revisions"`, the six activity kinds, and
`can_verify_wiki_page` with its Official guard. Every one of them is cheap here and a cross-spec migration
conflict later.

### PR A2 — Reading, search, and browse

1. `hub/wiki_views.py` with `wiki_feature_required`; `hub_wiki_home`, `hub_wiki_page`, `hub_wiki_search`;
   `/wiki/` in `MEMBER_ONLY_PATH_PREFIXES`.
2. **The full search surface** (§5.7): `q`, `guild`, `kind`, `stale`, `source`, `scope`, `page`; the
   browse branch for an empty `q`; grouped results with the source chip; the Help Center group.
3. `wiki_home.html`, `wiki_page.html`, `wiki_search.html`, `wiki_not_found.html`, and the read partials —
   including `_wiki_card.html` **with `snippet`, `source_chip` and `actions_partial`**, and the
   `{% block wiki_page_actions_extra %}` slot on the reading page.
4. The placeholder include point for B's `_wiki_search_empty.html`, asserted on the include.
5. All `pl-wp-` read CSS in `hub.css` plus the wiki `@media print` block, `.pl-wp-printhead`, and the
   phone action-bar body padding.
6. The sidebar swap in **both** nav branches of `base.html`.
7. Still no writing. **No changelog entry.**

### PR A3 — Writing

1. `hub_wiki_new`, `hub_wiki_create`, `hub_wiki_edit`, `hub_wiki_autosave`, `hub_wiki_confirm`,
   `hub_wiki_quick_photo`, `hub_wiki_quick_tip`, `hub_wiki_image_upload`, `hub_wiki_drafts`,
   `hub_wiki_draft_discard` — the three write routes gated on `can_edit_wiki_page`.
2. `RESERVED_WIKI_SLUGS` and the forms/formsets (`extra=len(prompts)` in create mode, `extra=0` in edit
   mode, `{{ f.id }}` on every row, `enctype="multipart/form-data"` on both forms); `wiki_new.html`,
   `wiki_edit.html` **with its two named blocks**, `wiki_drafts.html`, the two list-editor partials, the
   two modals, `_wiki_status_oob.html`.
3. `apply_edit` / `create_page` / `confirm_still_accurate`; the autosave endpoint, its `title`/`body`
   allowlist, its savestate pill, and its **`WikiEditLock.refresh` call behind the import guard**; the
   draft resume banner. **No lock model, no conflict screen** — both are D's.
4. `?wanted=<pk>` carried through the chooser and `fulfil()` called on create (guarded until B lands).
5. The `wiki` toolbar entry in `rich-editor-init.js`, **the `modules.toolbar` `{container, handlers}`
   rework**, and the `toolbar` seam in `core/widgets.py`.
6. **No changelog entry.**

### PR A4 — Seeding, QR, and the switch-on

1. `seed_wiki_machine_pages` with `--dry-run`, the refuse-empty guard, and **the adoption rule** (§5.11).
2. `m/<code>/` + `hub_wiki_qr` + `wiki_qr_missing.html`; `/m/` in `MEMBER_ONLY_PATH_PREFIXES`;
   `hub_wiki_qr_download`; the "Share This Page" card on the reading page.
3. `hub_wiki_stickers` + `wiki_sticker_sheet.html` + `static/css/wiki-stickers.css`.
4. E2e coverage (§9) against Postgres on 5433, like CI.
5. Run the seeder as a Render one-off job.
6. **No changelog entry, and `wiki_enabled` stays off.**

### Where the flag flips and the changelog entry goes (D8)

**Not here.** Brief §9.1 is binding: *every* PR in the round bumps `VERSION` with **no** changelog entry
except **the last PR of the whole round** — which is E's, not A4's. Re-stamping an entry re-posts it to
Discord, so an entry at A4 would announce the wiki again on every later merge in the round.

So A4 leaves `wiki_enabled` **off** and the wiki dark. The last PR of the round flips it in Site Settings
and adds the single entry. A drafts it here so the copy is not invented at the end of a long round, in the
required plain, no-dash voice:

> **A wiki written by members.** There is now a Wiki in the sidebar, and you can write in it. Every
> machine already has a page waiting for what you know. Add a photo from your phone in about thirty
> seconds, drop in a tip, or write the whole thing. Scan the sticker on a machine to jump straight to
> its page. Every version is saved, so nothing you write can be lost.

A bump with no entry announces nothing, which is the correct outcome per CLAUDE.md and is why
`wiki_enabled` defaults to `False` (§4.7): the flag and the announcement turn on together, once.

> Spec only — do not build until approved.

---

## 9. Testing

BDD `*_spec.py` with **`describe_*` / `it_*` only** — `context_*` is not a collected prefix, so a
`context_*` block is silently skipped and everything inside it never runs. Homes: `tests/membership/`
(models, permissions, markdown, the seed command under `tests/membership/management/`) and `tests/hub/`
(views, forms, template states) — the root `tests/` tree; `membership/spec/` and `hub/spec/` do **not**
exist. factory-boy factories go in `tests/membership/factories.py` beside the existing
`WikiArticleFactory`. `respx` for any HTTP mocking. 100% branch coverage on new code; the suite gate is
`fail_under = 98` and coverage runs by default.

**Models and freshness**
- Slug: filled from the title once; **never changes on rename**; deduped `-2`, `-3`; a reserved value is
  treated as taken; a title that slugifies to nothing falls back sensibly.
- `qr_code`: filled once, uppercase, from the ambiguity-free alphabet, unique, never changes.
- `is_out_of_date` per kind at the interval boundary, one day before and one day after; `PROJECT` never
  goes stale; the clock base is `last_checked_at or verified_at or created_at` and a plain `updated_at`
  bump does **not** reset it.
- `verification_is_aged` at 11 and 13 months; an aged page is still `GUILD_VERIFIED` (never silently
  un-verified).
- `confirm_still_accurate` writes both fields, changes no status, and writes no revision.
- `rebuild_search_text` includes fact labels/values and attachment labels and excludes HTML tag names
  (search "strong" on a bolded body returns nothing).

**The verified-drop rule** (the locked behavior, tested from both sides)
- A non-staff edit of a `GUILD_VERIFIED` page → `COMMUNITY`, `verified_by`/`verified_at` cleared,
  `unverified_reason` recorded.
- A staff edit of the same page → still `GUILD_VERIFIED`, `verified_at` unchanged, `last_checked_at`
  stamped.
- A non-staff edit of a `COMMUNITY` page changes no status and records no reason.
- A quick **tip** by a non-staff member drops verification; a quick **photo** by the same member does not.
- Every path writes exactly one `WikiRevision`, and the revision holds the **pre-edit** title, body,
  facts, and status.

**Sanitizer profile isolation** (the golden-file constraint)
- `render_markdown(source)` still matches every fixture in `tests/membership/fixtures/markdown_golden/`
  byte for byte — the existing test is untouched and must stay green.
- The two `ValueError` specs are updated for the widened tuple and a fresh one asserts
  `render_markdown("x", profile="bogus")` still raises.
- `img` from `MEDIA_URL + "wiki/"` survives the wiki profile; `img` from any other host, from an absolute
  external URL, from a protocol-relative host, and from a `data:` URI is dropped, and a src-less `img` is
  removed entirely rather than left as a useless tag.
- `iframe` is stripped by the wiki profile (mirroring the help profile's `it_still_strips_X_in_the_member_profile`
  guard idiom): a Loom embed that survives `profile="help"` does **not** survive `profile="wiki"`.
- `script`, inline `style=`, `onclick`, and unknown tags are stripped in the wiki profile.
- `sanitize_wiki_submission` sanitizes HTML and passes non-HTML through unchanged; it never calls
  `sanitize_page_submission`, and `sanitize_page_submission`'s own specs still pass unchanged.
- `_inject_heading_ids`: ids are slugified from the heading text, deduped `-2`, an existing valid id is
  preserved, and `WikiPage.toc()` returns non-empty for a **Quill** body — the bug this fixes.
- `render_wiki_content` on the same body twice produces the same ids (deterministic anchors).

**Permissions** (each helper, each leg, true and false, with `view_as` respected)
- `can_edit_wiki_page`: active member yes; inactive member no; `OFFICIAL` page → staff only; archived →
  `can_moderate_wiki_page` only; an admin previewing as a member gets the member's answer.
- `can_verify_wiki_page`: **an `OFFICIAL` page is never verifiable, not even by an admin** (the guard runs
  first); guild lead yes; each `GuildStaffMembership.Role` yes, **orienter included**; a member of the
  guild who holds no role no; an `Equipment.is_run_by` orienter on a guild-less machine page yes; a
  space-wide page takes `is_effective_staff`; a plain member no.
- **No `can_moderate_wiki` spec** — the helper does not exist. `_can_moderate_wiki_page` gets one spec
  proving a guild lead is true for a page in their own guild and false for another guild's, so D's
  replacement is a drop-in.
- `visible_wiki_pages` is a filter: it excludes unpublished and archived pages for a plain member,
  includes the member's own unpublished page, includes an unpublished page in a guild the member can edit,
  and includes everything for effective staff.
- Endpoint gating: `hub_wiki_edit` on an Official page 403s for a member; **`hub_wiki_quick_tip`,
  `hub_wiki_quick_photo` and `hub_wiki_image_upload` each 403 for an active member on an Official page**
  (brief §9.3 — three separate specs, because three separate routes); `hub_wiki_stickers` 403s for a
  guild lead; every route 404s while `wiki_enabled` is off, including the POSTs.

**Seeding**
- Refuses an empty `Equipment` table with a `CommandError` and writes nothing.
- Creates one page per active tool with the machine starter, the guild, the equipment link, and a QR code.
- **Idempotent:** a second run adds nothing and changes nothing.
- **Never clobbers an edit:** after `apply_edit`, a re-run leaves `title`, `body`, and facts untouched and
  reports the page as skipped, while still refreshing `guild` when the tool moved guilds.
- **Adoption (D10):** a member-written page whose slug matches the tool and whose `equipment` is null is
  **adopted** — `equipment` is set, `guild` filled only when blank, `title` / `body` / facts unchanged
  byte for byte, a revision written with the linking note, and the run reports it as adopted, **not**
  created. Assert no second page exists for that tool. A page that already has a *different* `equipment`
  is left alone entirely. Adoption never raises.
- `--dry-run` reports the same counts (added / adopted / refreshed / skipped) and writes zero rows.
- The `uq_wikipage_machine_equip` constraint refuses a second machine page for one tool.

**Views, forms, and template states**
- Home: the three sections, the empty-wiki state, the empty-filter state, the Old Wiki card appearing and
  disappearing with `wiki_link_enabled`.
- **Search and browse:** multi-term AND; snippet escaping (a body containing `<script>` renders escaped,
  once); the out-of-date pill present in a result row; **an empty `q` with no filters returns the browse
  list rather than nothing**; `?guild=` + `?kind=` (B's "See all") returns the right rows with no `q`;
  `?stale=1` returns only overdue pages; `?scope=all` drops a `?guild=` even when both are present; an
  unknown `guild` slug returns the "nothing matches those filters" state, not a 500; `?source=help`
  returns only the Help group; `?source=` is absent from the chip row when only one group exists; the
  Help group is skipped when `help_page_enabled` is off and when `q` is empty; the zero-result branch
  **renders the `_wiki_search_empty.html` include** (assert on the include, not on placeholder copy);
  a zero-result *browse* does **not** log a search miss.
- Reading page: quick answers present and absent; the Official block rendered from `Equipment` and
  **absent** when `equipment` is null; the TOC chip row present with two headings and hidden with one;
  the archived notice; the byline strings; the print head element present in the DOM; **no edit affordance
  at all** in the HTML of an Official page for a member — assert on the absence of the Edit, Add Photo and
  Add Tip markup, not just on a 403.
- **The status note:** `unverified_reason` set renders "Edited since it was verified. Waiting for someone
  to check it again."; `needs_review_since` set renders its line; a plain Community page renders neither.
- **`?confirm=1`:** the cue card renders for an active member on a live page; it does **not** render on an
  archived page, and an unknown parameter value renders the page normally with no error.
- **The action row and bar:** hidden entirely on an archived page (including "Still accurate"); the
  overflow renders through `components/row_actions.html`; `pl-wp-has-actionbar` present exactly when the
  bar is.
- Create/edit: reserved slug rejected with the friendly message; duplicate title in a scope rejected;
  **the create form contains no Safety toggle** (assert the absence — this is the seam with D);
  the verification warning string shown only to a non-verifier on a verified page; **both forms carry
  `enctype="multipart/form-data"`**; the Equipment select renders for a moderator and is absent for a
  member, and setting it links the page.
- **`?wanted=<pk>`:** carried from `/wiki/new/` through to `/wiki/new/<kind>/` and into the POST; a
  successful create calls `fulfil()` (assert on the wanted row, not on a mock); a stale or invisible pk
  creates the page and does not raise.
- **Fact formset:** **create mode renders one row per starter prompt** (`extra=len(prompts)`) and edit mode
  renders none beyond the saved rows (`extra=0`); **an untouched prompt row with a label and no value does
  not block Save** and is not persisted; every row carries `{{ f.id }}`; the per-row Delete flips `DELETE`
  and **preserves the other rows' edits** in the same submit; duplicate labels rejected; the nine-row cap
  rejected; `sort_order` persisted from the submitted order.
- Attachment formset: file XOR url both ways; blank label rejected; a disallowed extension rejected; an
  oversize file rejected; an image attachment normalized on save and a document left alone.
- **Autosave:** the allowlist is exactly `title` and `body` — a `facts-0-label` POST is a **400**; an
  invalid value → 422 with the error toast header; a valid value → 204 with `HX-Trigger: wiki-saved`; the
  draft upsert respects both unique constraints; a second draft for the same (page, author) is impossible;
  **the view calls `WikiEditLock.refresh` when the model is importable and does not raise when it is not**
  (the A3-before-D guard).
- **Draft resume:** a draft newer than `page.updated_at` renders the card and the form is still populated
  from the **page**; `?draft=use` populates from the draft and carries its `base_revision`; `?draft=fresh`
  deletes the draft; a draft older than `page.updated_at` renders no card; an empty draft renders no card.
- **"Still accurate":** the confirm modal is passed `confirm_hx_post` (assert the rendered `hx-post`
  attribute — a plain-POST form here is the bug); the response is **200 with a body**, carries
  `hx-swap-oob` on both `#wiki-status` and `#wiki-confirm-cue`, and sets the toast header; a second tap
  the same day renders "Checked today" and no button; a POST on an archived page is **409**.
- **No conflict or lock specs live in A** — they belong to D with `WikiEditLock` and the conflict screen.
- Quick photo / quick tip: happy paths, the verification consequences from both sides, the
  `can_edit_wiki_page` gate on each, the tip field rendering through `form_field.html` inside
  `.pl-form-group`, and the failure toasts.

**QR**
- `/m/<code>/` signed in → 302 to `/wiki/p/<slug>/`.
- `/m/<code>/` **signed out → 302 to the login URL carrying `?next=/wiki/p/<slug>/`** — asserted on the
  exact query string, because this is the detail that decides whether a scan works.
- Lowercase code resolves; unknown code renders the friendly page with a 404 status; a code on an archived
  page still resolves (the page explains itself).
- `/m/…` 404s while the flag is off, and 404s on the public book surface.
- Sticker sheet: renders one cell per machine page with the name and typed URL in the markup; the
  `?guild=` filter narrows it; a moderator-only gate.

**E2e** (`tests/e2e/`, run against Postgres on 5433 like CI — browser-writing specs flake on local SQLite)
- The full loop: seed a machine page → scan-equivalent `/m/<code>/` while signed out → login → land on the
  page → add a photo → add a tip → confirm the tip dropped the verified chip to Community → a lead
  re-verifies (B's button, so this leg lands with B) → "Still accurate" clears the out-of-date pill.

**Gotchas to design the tests around**
- Time fixtures use `now + timedelta(...)` and freshness boundaries are asserted in **local (Portland)**
  time, not UTC.
- `conftest.py` forces `STORAGES["default"]` to `FileSystemStorage`, so upload specs write to a temp media
  root and must not assume an R2 URL.
- `wiki_image_src_prefixes()` reads settings per call, so sanitizer specs can `override_settings` both
  `MEDIA_URL` and `R2_PUBLIC_URL` to prove both prefixes.
- Every UI copy string added here lands in the changelog-renders-everywhere blast radius: the `CHANGELOG`
  text is in every hub page's context, so assert on specific elements, not on `"Wiki" in response.content`.
- Run `tests/template_comment_lint_spec.py` before committing any template — a wrapped `{# … #}` renders
  as visible text (Rule 17). Every multi-line comment in the new templates uses `{% comment %}`.

---

## 10. Open / deferred

- **A scoped `AdminCapability.Capability.WIKI`.** Moderation permission is **spec D's**
  `can_moderate_wiki_page` — effective staff or the page's guild lead. If council later wants a Wiki
  Administrator who is neither, that is one enum value plus one leg in D's function, and it belongs in D.
  This spec no longer proposes a request-only `can_moderate_wiki`; brief §9.1 struck it.
- **Member-facing diff viewer.** The brief rejected it outright (253 lines of vanilla JS in the reference
  app for a need members do not have). Staff get revert in spec D; nobody gets a diff.
- **Adding the seeder to `render.yaml`'s `buildCommand`.** Left out on purpose (§5.11): it hands rows to
  members and must never race a deploy against an edit. If re-running it by hand after every equipment
  addition becomes a chore, the right fix is a hook on `Equipment` creation, not a deploy step.
- **Breadcrumb / tabs / empty-state component extraction.** Declined for this spec (§2); the note stays on
  `DEFERRED.md`.
- **Orphaned `wiki/body/` uploads are not swept, and this spec does not build a sweeper.** An earlier
  draft claimed "the existing `unreferenced_files` command" cleans them up. **That command does not
  exist.** What exists is `core/files.delete_if_unreferenced`, called by `delete_orphan_on_replace` when a
  field is *replaced* — it never walks storage looking for unreferenced blobs. An image inserted into a
  body and then deleted from the body stays in R2. At the wiki's expected volume that is a few megabytes a
  year and the honest answer is to leave it; a real sweeper is a separate, carefully-tested command
  (deleting files from storage on a heuristic is how a media library gets emptied) and goes on
  `DEFERRED.md`.
- **Body images beyond the editor's image button.** No gallery manager, no reorder, no alt-text editor for
  inline body images — the attachment list covers the labeled case and Quill covers the inline case.
- **Per-page email subscriptions**, reactions, karma, edit-count badges, threaded comments, a standalone
  file browser, a transclusion engine, real-time collaborative editing, and nudging individual authors
  about their stale pages — all rejected in the brief's "Explicitly NO" list and not revisited here.
- **MediaWiki migration** is an ops task, not a spec: hand-migrate the real content, freeze the old wiki
  read-only for 90 days behind a banner, then redirect the domain. This spec ships the "Old Wiki" card
  and the existing `wiki_link_enabled` toggle that retires it.
- **QR sticker production** — size, material, lamination, who applies them. This spec owns the route and
  the printable sheet; the physical rollout is a shop task.

## 11. Done criteria

- A member scans a sticker on a cold phone, signs in, and lands on **that machine's page**, not the home
  page.
- A brand-new member opens a machine page, taps Add Photo, and is done in under thirty seconds without a
  keyboard.
- No page in the wiki is empty on launch day, because every `Equipment` row has a seeded stub, and
  re-running the seeder after a member has written on a stub changes nothing they wrote — including a page
  the member wrote *before* the seeder ever ran, which is adopted rather than duplicated.
- A member's edit of a guild-verified page drops it to Community with a recorded reason **that is visible
  on the page**, and a staff edit of the same page keeps the check and resets the clock — both provable
  from the model, both visible on the page.
- **`/wiki/search/` answers usefully with no `q`**, so every "See all" link B renders and E's Policies
  search box land on real content.
- **A tap on "Still accurate" changes the pill without a reload**, because the response is a 200 carrying
  an OOB fragment and a toast.
- **Opening the editor with an unsaved draft asks one clear question and defaults to the safe answer.**
- `render_markdown(source)` still matches all eight golden fixtures byte for byte, and
  `sanitize_page_submission` is unchanged.
- **B and D can branch off A's merged main and build in parallel without touching A's migration**: `kind`,
  `related_name="wiki_revisions"`, the six activity kinds, `can_verify_wiki_page`, the card slot, the two
  editor blocks and the action slot are all already there.
- Every screen in §6 has its named Save / "+ Add" / Delete / Cancel controls, its empty, loading, error,
  and success states, both themes, and its mobile reflow, exactly as written — and no list editor
  anywhere renders a delete as a toggle.
