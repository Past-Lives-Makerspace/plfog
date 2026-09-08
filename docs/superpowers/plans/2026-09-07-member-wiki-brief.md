# Member Wiki — Shared Brief & Locked Decisions (2026-09-07)

Approved by Josh in a fogstorm session. **Four specs share this brief. Read it before reading any of them.**

- **A. `2026-09-07-member-wiki-core.md`** — the `WikiPage` store, permissions, read/write/search surfaces, revisions,
  attachments, status chips, sidebar, mobile reading layout, Equipment stub seeding, QR short links.
- **B. `2026-09-07-member-wiki-guild-tab.md`** — the Wiki tab on the guild page, guild-scoped views, one-tap Verify,
  Wanted pages, overdue-for-review list, failed-search panel, monthly lead digest.
- **D. `2026-09-07-member-wiki-moderation.md`** — Report a problem, the review queue, official notes,
  archive-with-reason, revert, activity kinds and notification triggers.
- **E. `2026-09-07-governance-doc-mirror.md`** — read-only mirror of PLM's `org/` governance markdown into FOG.

Build order is **A → (B, D in parallel) → E**. Each spec stands alone as a document but must honor the
contracts below.

> Origin: Morlock's email "PLM Knowledge Base source, v1.49.0" (2026-08-19) handing over
> `github.com/Past-Lives-Makerspace/plm-kb-app` at v1.49.0. That app is the reference implementation,
> **not** the thing we are cloning — see §2.

---

## 1. What we are building, in one paragraph

An easy-to-use, member-written wiki inside FOG where any active member can add and edit pages, upload
documents and photos, and build up guild-specific knowledge. It is beautiful in the FOG style, works
one-handed on a phone in a noisy shop, and is legible about what is official and what is a member's
opinion. Guild staff and admins get extra powers on top, never a gate in front.

**The reading half already exists in plfog and the writing half does not.** `WikiArticle` +
`HelpCategory` already give us slugs, a TOC, related articles, prev/next, search with highlighted
snippets, and a dual-mode Markdown/Quill renderer with a hardened sanitizer. What is missing is any
permission below admin, authorship fields, revision history, and an ownership boundary. This project
supplies those, in a new store.

---

## 2. The three content homes (do not merge them)

| Store | Content | Writers | Disposition |
|---|---|---|---|
| **Help Center** — `/help/`, `WikiArticle` + `HelpCategory` | How to use *the app* | Repo-seeded (`membership/help_content.py`), admin-editable | **Unchanged.** It must track the shipped code, so it belongs in the repo. |
| **Wiki** — `/wiki/`, new `WikiPage` | How to use *the space, the machines, the materials, the craft* | Every active member | This project. |
| **Governance** — PLM's `org/` markdown | Policies, bylaws, roles, minutes, registers | Officers, via git | Mirrored read-only into FOG (spec E). Morlock's app keeps the governance *workflow*. |

**Why `WikiPage` is a new model and not a member-editable `WikiArticle`.** `seed_help_center` runs in
`render.yaml`'s `buildCommand` on **every deploy** and `update_or_create`s every slug in
`membership/help_content.ARTICLES`. `docs/HELP_AUTHORING.md` states the consequence outright: edits made
in `/help/edit/` to a seed-owned article are overwritten on the next deploy. Member-authored content in
that table would be silently destroyed on a Tuesday. The two stores stay separate and the seed never
learns about member rows.

**Why governance is a mirror and not an absorption.** Merging the wiki and governance forces one to
inherit the other's process: either the wiki gains an approval gate (which kills participation) or
governance loses its provenance and audit trail. The mirror gives members one login and one search
while leaving the approval ladder, proposed edits, and moderation inbox on Morlock's box.

**The member never sees this taxonomy.** One search box spans all three, with results labeled by source
chip. Cross-link deliberately; never duplicate.

---

## 3. What we take from `plm-kb-app`, and what we deliberately do not

The handoff repo is at `github.com/Past-Lives-Makerspace/plm-kb-app` (private, org access). Django 6,
SQLite, two dependencies, hand-rolled markdown, no JS build step. Its central idea — **content is synced,
not authored** — is the *opposite* of what we are building, so we take patterns, not code.

**Take:**

| Idea | Where it lives there | How it applies here |
|---|---|---|
| **Permissions are filters, not checks** | `kb/permissions.py` | Views ask for `visible_wiki_pages(request)` and render what comes back. A view that forgets to gate shows too little, never too much. This matches `membership/permissions.py`'s existing style. |
| **Gate the real URL, not the index route** | `require_register` decorator | v1.39.0 gated `/register/<key>/` but every register also had its own URL, so a member loading `/finance/` got a 200 and the full financials. Every alternate path to a page needs the same gate. |
| **One card partial + a `.card` property contract** | `_ecard.html` | What makes eight registers feel like one product. Our wiki cards, guild-tab rows, and search results should share one partial with one status pill. |
| **Exactly ONE status pill per card** | `style.css` "no pill salad" | Attribute chips (kind, guild, scope) render as quiet neutral text, never colored pills. Only status carries color. |
| **Stable, never-reused slugs** | `reg_id` + slug-upsert | Fill the slug once, never change it. A deep link must never break. |
| **Archive, never delete; attribute both directions** | `Comment.addressed/addressed_by/addressed_at` | "An archive that can't say who cleared an item can't be audited. Restoring clears the attribution so it never claims a stale actor." |
| **Sync refuses to run on empty input** | `sync_docs` `CommandError` | A bare run once removed 50 documents and reshuffled 27 stable IDs. Spec E's mirror command must refuse an empty source tree. |
| **Written empty states** | "📭 Nothing pending — you're all caught up." | Never a blank region. |
| **Provenance footers** | person/guild/finance pages | Name the upstream source, the sync time, and *how to correct it*. Spec E needs this. |

**Do not take:**

- **Per-section comments.** Built in v1.47.0, **reverted entirely in v1.49.0** two days later ("the shape
  isn't right"). The need was real; the shape was wrong. We ship no page comments at launch at all.
- **The `ApprovalRequiredMiddleware`.** It gates site-wide; in FOG that would gate all of FOG.
- **The hand-rolled markdown converter.** plfog already has `membership/markdown.py` (markdown + bleach)
  with golden-file tests. Never introduce a second renderer.
- **Word-level HTML diffs and the changelog hover card.** Beautiful, and 253 lines of vanilla JS for a
  need members do not have. Staff need *revert*, not a diff viewer. Deferred.
- **The approval ladder / tracker / inbox.** That is governance workflow and it stays on Morlock's box.

---

## 4. Locked decisions

| Decision | Choice |
|---|---|
| **Store** | A new `WikiPage` model in `membership/models.py`. `WikiArticle` and the Help Center are untouched. |
| **Namespace** | One page store, one URL space, one search index. **Scope is a field**, not a separate store. |
| **Scope** | `guild` FK, nullable. Null = space-wide. The guild Wiki tab is a *filtered view* of the same store, never a separate store. Content does not respect guild boundaries (finishing walnut matters to Woodworking and to Print), and guilds go dormant. |
| **Filing** | Exactly two required fields, **both pre-filled from context**: Scope (from where they clicked New) and Kind (from which starter card they picked). **No folders, no parent pages, no hierarchy, no tag taxonomy.** Hierarchies need a librarian we do not have and end with a "Misc" node holding most of the content. |
| **Kinds** | Six: `MACHINE`, `HOWTO`, `MATERIAL`, `PROJECT`, `GUILD_INFO`, `REFERENCE`. Kind drives the starter template and the review interval. |
| **Who may edit** | **Any active member may create pages and edit any Community or Guild-verified page, live immediately, with no approval queue.** A first contribution that sits invisible for four days is the last contribution. |
| **Who may verify** | The guild's lead, any guild staff role, **and orienters** (they teach the machine, they know what is true), plus admins. |
| **Official pages** | Admins/officers only. Members get **no edit affordance at all** — not a disabled button, no button. |
| **Editing a verified page** | A non-staff edit drops the page back to Community and records why ("edited since it was verified"). A green check must never ride on unreviewed text. A staff edit keeps the verification and resets the clock. |
| **Approval** | **Approve-after, never approve-before.** One exception: a member proposing a page of the Safety flavour lands a draft assigned to the guild lead. |
| **Deletion** | Members cannot delete. Admins archive (URL keeps working, page explains itself to the author **by name**). Hard delete exists only as a shell command, never in the UI. |
| **Revisions** | Every save writes a `WikiRevision`. Staff can revert. **No member-facing diff viewer** at launch. |
| **Comments** | **None.** Discord owns conversation; comments become where the correct answer hides while the page stays wrong. The pressure valve is *Report a problem*, not a comment thread. |
| **Editor** | One editor for members: the existing Quill setup (`core/widgets.py` `PageContentEditorWidget` + `templates/_components/rich_editor_assets.html`). **No markdown-vs-rich-text choice** — a choice between two editors is a decision a member has no basis for making. Raw markdown stays available to admins via the existing dual-mode storage. |
| **Sanitizer** | A **new `wiki` profile** in `membership/markdown.py` and a parallel `sanitize_wiki_submission`. Never loosen the shared `member` / help profiles or `sanitize_page_submission`. The wiki profile allows `img` from the wiki media prefix only; **no `iframe`** (that is the admin-authored help profile's privilege). |
| **Search** | Extend the existing `icontains` approach (`WikiArticleQuerySet.search` is the model to copy). **Do not introduce Postgres FTS** — nothing in the repo depends on it and local dev is SQLite, so an FTS path would behave differently in dev than in CI/prod. |
| **Sidebar** | The in-app `/wiki/` **takes the existing "Wiki" sidebar slot**. The external MediaWiki (`MAKERSPACE_WIKI_URL`, `SiteConfiguration.wiki_link_enabled`) is demoted to a link on the wiki home during migration, then retired. |
| **MediaWiki migration** | **Hand-migrate the real content, no importer.** An importer yields formatting debris that makes the new wiki look abandoned on day one. Freeze MediaWiki read-only for 90 days behind a banner pointing at the new home, then redirect the domain. This is an ops task, not a spec. |
| **Governance docs** | Read-only mirror (spec E). Depends on read access to PLM's private `org/` repo — **Josh to ask Morlock.** |
| **Attachments** | Belong to a page. **There is no standalone file browser and no `/wiki/files/` view.** A file library guarantees 200 PDFs named `scan_0034.pdf` that nobody reads. A one-line label is **required** on every attachment; the label is the entire value of the upload. |
| **Empty wiki** | Prevented at launch by seeding a stub page for every `Equipment` row (spec A). Every member contribution is then an *edit*, not a *creation*, which is psychologically an order of magnitude cheaper. |
| **Public access** | None. Wiki pages are `@login_required` like the rest of the hub. Opening member-written machine instructions to the public changes the liability calculus entirely. |

### Explicitly NO (each was considered and rejected)

Free-form hierarchy or nested folders · wikitext/markdown syntax for members · real-time collaborative
editing (a soft advisory lock plus a non-destructive conflict save covers it for ~2% of the cost) ·
per-page custom permissions · threaded comments · reactions, karma, leaderboards, edit-count badges ·
a standalone file or document browser · public or anonymous read/edit · MediaWiki-style talk pages ·
a transclusion/template engine · a second editor mode for members · per-page email subscriptions
(roughly zero subscribers per page at 200 members; watch the *guild* instead) · nudging individual
authors about their stale pages (it reads as assigned homework and drives off the people you cannot
afford to lose — nudge leads, never authors) · merging the Help Center or governance into the wiki.

---

## 5. Shared contracts every spec must honor

### 5.1 URL space

`/wiki/p/<slug>/` for pages. The `p/` segment is deliberate: the Help Center's catch-all
`help/<category>/<article>/` routes forced a reserved `more` segment and a comment warning that the
catch-alls must stay last. A fixed `p/` segment means fixed routes and page routes can never collide.

```
wiki/                          hub_wiki_home
wiki/new/                      hub_wiki_new            starter chooser
wiki/new/<kind>/               hub_wiki_create
wiki/search/                   hub_wiki_search
wiki/drafts/                   hub_wiki_drafts
wiki/review/                   hub_wiki_review          (D)
wiki/wanted/                   hub_wiki_wanted          (B)
wiki/p/<slug>/                 hub_wiki_page
wiki/p/<slug>/edit/            hub_wiki_edit
wiki/p/<slug>/autosave/        hub_wiki_autosave        POST, JSON
wiki/p/<slug>/confirm/         hub_wiki_confirm         POST, "Still accurate"
wiki/p/<slug>/photo/           hub_wiki_quick_photo     POST
wiki/p/<slug>/tip/             hub_wiki_quick_tip       POST
wiki/p/<slug>/verify/          hub_wiki_verify          POST (B)
wiki/p/<slug>/report/          hub_wiki_report          POST (D)
wiki/p/<slug>/history/         hub_wiki_history         (D)
wiki/p/<slug>/revert/<pk>/     hub_wiki_revert          POST (D)
m/<code>/                      hub_wiki_qr              top-level, NOT under /wiki/ (A)
guilds/<slug>/?tab=wiki        existing Alpine tab on hub_guild_detail (B)
```

Reserved slugs (a page may never claim one): `new`, `search`, `drafts`, `review`, `wanted`, `p`, `m`,
`edit`, `history`. Follow the `RESERVED_HELP_SLUGS` precedent in `hub/forms.py:1446`.

### 5.2 Status chips — exactly four states plus two derived

| Chip | Who sets it | Meaning | Volume target |
|---|---|---|---|
| **Official** | Admins/officers only | Policy, safety, membership terms. Members cannot edit. | Under 40 pages, ever |
| **Guild verified** | Guild lead, staff, or **orienter** for that guild | A member wrote it; someone with authority read it and stands behind it. Shows "Verified by Kate (Woodworking orienter), 3 Mar". | 30–60 |
| **Community** | Default for everything | "Written by members. Helpful, not official." Neutral, **not** a warning. The normal state. | The bulk |
| **Needs review** | Anyone, via Report a problem (D) | Amber banner quoting the reporter's reason. **Page stays readable.** | Transient |
| *Out of date* | Derived from the review clock | Appears **in search results**, not only on the page | |
| *Verified (aged)* | Derived at 12 months | Green fades to grey, "Verified Mar 2026". Never silently un-verify; never let a green check ride a three-year-old page. | |

If Official is applied liberally it means nothing. Hold the line at policy, safety, and money.

### 5.3 Review intervals by kind

`MACHINE` 12 months · `HOWTO` 24 · `MATERIAL` 24 · `GUILD_INFO` 12 · `REFERENCE` 12 · `PROJECT` never.
An Official page inherits the interval of its kind. Past the interval, the *Out of date* chip appears.
**Confirming must be cheaper than editing** or nothing gets confirmed — hence one-tap "Still accurate".

### 5.4 The official block on a machine page

Rendered **from the `Equipment` register, never from wiki prose**, so it cannot drift per-page and no
member can edit it. `Equipment` (shipped) already carries `slug`, `guild`, `required_orientation` →
`OrientationType`, and an `AccessState` enum (`OK` / `NEEDS_ORIENTATION` / `NEEDS_GUILD` /
`INACTIVE_MEMBER`). The block sits in its own container (tinted background, left rule, an "Official"
chip), **above** member content, with no edit affordance.

### 5.5 Page composition, top to bottom

1. Title, status chip, live equipment status line
2. **Quick answers** — a two-column key/value block of the 4–8 facts people came for. This is the most
   useful component on the page and the starter template must prompt for it first.
3. The locked **Official** block (machine pages)
4. Member content: photos, steps, what goes wrong, tips
5. Attachments (cards with icon, label, size, uploader)
6. Related pages
7. Byline and metadata (moved to a "Details" disclosure on mobile)

### 5.6 Mobile is the primary target

Assume 390px, one thumb, gloves, dust, bad light, and eight seconds of attention.

- **On a phone the primary contribution affordance is a photo, not an edit.** A member in the shop has a
  camera and thirty seconds, not a keyboard and twenty minutes. A photo of the correct blade or the
  broken part beats three paragraphs and costs a hundredth of the effort.
- TOC is a **horizontal chip row** pinned under the header (44px tall, scrollspy) — not a sidebar, not
  an accordion.
- Sticky bottom bar: **Add photo / Add tip / Report**. Full Edit is secondary, in an overflow.
- 48px minimum tap targets. **No hover-dependent affordances, no swipe-to-reveal, no long-press menus,
  no drag-to-reorder** on the phone layout — all of them fail with gloves.
- Body text 17px minimum, line-height 1.6, high contrast. Tables scroll inside their own container; the
  page never scrolls horizontally.

### 5.7 Naming and CSS

`pl-wiki-toc` and `pl-wiki-article` are **already taken** by the Help Center in `hub.css`. New wiki
classes use the **`pl-wp-`** prefix (wiki page). Grep `static/css/hub.css` before naming anything.
All new wiki CSS goes in `hub.css`; shared component styles go in `components.css`.

### 5.8 Activity and notification vocabulary

`SiteActivity.Kind` additions (spec D owns the list, A and B emit them):
`WIKI_PAGE_CREATED`, `WIKI_PAGE_EDITED`, `WIKI_PAGE_VERIFIED`, `WIKI_PAGE_REPORTED`,
`WIKI_PAGE_ARCHIVED`, `WIKI_PAGE_REVERTED`.

Event keys (`core/triggers.py` + `core/events/registry.py`):
`wiki.page_reported` → guild leads for the page's scope, then officers ·
`wiki.page_verified` → the page's contributors (**this is the retention mechanism — the notification
that Kate verified their page is why someone writes a second one**) ·
`wiki.guild_digest_monthly` → guild leads (B).

### 5.9 House rules that bite here specifically

- **FRONTEND.md is law.** Rule 3 (booleans are toggles), Rule 11 (`extra=0` + "+ Add" + real Delete
  buttons — the Quick Answers rows and attachments are both list editors and **will** be reviewed for
  this), Rule 13 (never inline-style a form control; wrap in `.hub-form-group`), Rule 17 (`{# #}` is
  single-line only), Rule 18 (buttons never touch an adjacent section), Rule 21 (Save is last and says
  just "Save"), Rule 22 (Title Case headings).
- **Image upload fields use the draggable zone**, never a bare `<input type="file">` (Rule 16), and in a
  cloned formset row the drag handlers must be **delegated** from the rows container, because cloned
  `innerHTML` never executes its scripts.
- **`hub/base.html` boosts the whole body** (`hx-boost` + head-support) and re-runs `Alpine.initTree` on
  `htmx:afterSettle`. Anything new must survive body swaps.
- **Every PR bumps `plfog/version.py` VERSION.** Per `CLAUDE.md`, the whole wiki round is ONE
  member-facing feature: the first PR adds the changelog entry, every later PR in the round **edits and
  re-stamps that same entry** rather than adding a second one.
- **No em dashes or standalone hyphen-dashes in changelog entries or commit messages** (Josh pastes
  those verbatim). Prose inside these plan documents is fine.
- Run `manage.py check` after model/migration changes — CI runs system checks local pytest skips, and
  the index-name 30-character cap has bitten before.

---

## 6. Verified reuse map (confirmed in the codebase 2026-09-07)

| Need | Existing thing | Location |
|---|---|---|
| Guild-edit permission, `view_as`-aware | `can_edit_guild(request, guild)` | `membership/permissions.py` |
| "Which guilds may I edit", 2 queries not N | `editable_meeting_scopes(request)` | `membership/permissions.py` |
| Admin gate in a view | `_require_admin(request)` / `_viewing_as_admin` | `hub/views.py:769,775` |
| Guild staff roles (co-lead/secretary/treasurer/**orienter**) | `GuildStaffMembership` | `membership/models.py:2340` |
| Scoped admin duty, `view_as`-independent | `AdminCapability` + `DESCRIPTIONS` | `membership/models.py:2416` |
| Markdown → safe HTML, profiles | `render_markdown(source, profile=…)` | `membership/markdown.py:217` |
| Rich-editor HTML sanitize on save | `sanitize_page_submission` | `membership/markdown.py:329` |
| Dual-mode render (HTML or Markdown) | `render_page_content` / `looks_like_html` | `membership/markdown.py:306,271` |
| Quill editor widget + assets | `PageContentEditorWidget`, `_components/rich_editor_assets.html` | `core/widgets.py:30` |
| Search queryset to copy | `WikiArticleQuerySet.search()` + `search_snippet` | `membership/models.py:3087,3112` — **note:** it matches `body` raw, so a Quill body's HTML markup is searchable text. That is why `WikiPage` carries its own `search_text` column instead. |
| TOC from rendered body | `WikiArticle.toc()` + `_HELP_TOC_HEADING_RE` | `membership/models.py` |
| Slug idiom (fill once, dedupe, never change) | `HelpCategory.save` / `WikiArticle.save` | `membership/models.py:3020` |
| Reserved-slug guard | `RESERVED_HELP_SLUGS` + `clean_slug` | `hub/forms.py:1446` |
| File **XOR** link attachment with label | `MeetingAttachment` (+ `ck_*_file_xor_url`) | `membership/models.py:7061` — **take the XOR constraint, `delete_orphan_on_replace`, and `is_file`/`is_link`; do not take its shape wholesale.** Its `label` is `blank=True` (ours is **required** per §4) and it has no `uploaded_by` (we need one for the byline). Its editor is a modal + HTMX delete, **not** a formset. |
| Image upload, AJAX instant-save, reorder, alt text | `guild_image_upload` + `components/gallery_manager.html` | `hub/views.py:3207` |
| Single image field with drop zone + delete | `components/image_field.html` | `templates/components/` |
| R2 storage, size caps, downscale, orphan cleanup | `MAX_UPLOAD_*`, `normalize_field_if_uploaded`, `delete_orphan_on_replace` | `plfog/settings.py:319-420`, `core/images.py`, `core/files.py` |
| Audit feed | `SiteActivity.log(kind, actor=, target=, payload=)` | `core/models.py:1298` |
| Notifications spine | `emit()` + `EventType` registry | `core/events/emit.py:44`, `core/events/registry.py` |
| Transactional email choke-point | `core.email.send` | `core/email.py:61` |
| Tabs (Alpine, `?tab=` deep link) | `.pl-tabs` / `.vote-tab` + `x-data="{section:…}"` | `templates/hub/guild_detail.html:121`, `hub.css:21` |
| List-editor pattern to copy verbatim | FAQ / Links / recurring-hours editors | `templates/hub/guild_edit.html` |
| Pagination | `components/table_pagination.html` | |
| Search box | `components/table_search.html` | |
| Toasts / modals / confirm / toggles / fields | `components/*.html` | see FRONTEND.md |
| Equipment (slug, guild, required orientation, access state) | `Equipment` + `AccessState` | `membership/models.py:10700`, `hub/equipment_views.py` |
| Soft delete idiom | nullable `deleted_at` + manager override | `Guild` `membership/models.py:1930,1683` |
| Feature flag in the sidebar | `SiteConfiguration.help_page_enabled` / `wiki_link_enabled` | `core/models.py:624,629` |

> **Corrections applied 2026-09-07 after the specs verified the map.** `seed_wiki_articles` no longer
> exists — `seed_help_center` replaced it and is the only idempotent-seed pattern to copy. The
> `MeetingAttachment` and `WikiArticleQuerySet.search()` rows above carry their corrections inline.

**Genuine gaps to build:** revision history (no history library and no `created_by`/`updated_at` on any
content model), authorship on pages, member-level write permissions, a breadcrumb component (currently
hand-rolled per page), a tab component (currently copy-pasted), and an empty-state convention (there is
none — no `.hub-empty` class, no mention in FRONTEND.md). Extracting breadcrumb / tab / empty-state as
real components is a reasonable side-quest for spec A but must not balloon it.

---

## 7. The failure modes this design is defending against

Stated so a reviewer can check the design against them rather than against a feature list.

| Failure | Mechanism that prevents it |
|---|---|
| Empty wiki, nobody writes | Equipment stubs seeded before launch, so every contribution is an *edit* |
| Nobody knows where to put a page | Two fields, both pre-filled from context. No tree, no folders. |
| Stale content | Freshness as a first-class field; *Out of date* chip **in search results**; one-tap "Still accurate"; leads nudged, never authors |
| One person writes everything | Micro-contributions (photo / tip / report) that bypass the editor · Wanted pages · multi-contributor bylines with faces · no approval gate |
| Fear of breaking things | "Every version is saved. Nothing here can be lost." **in the editor footer**, where the fear is · members cannot delete · Publish helper text reads "Rough is fine. Someone will tidy it." |
| Edit conflicts | Soft advisory lock ("Dana started editing this 3 minutes ago") + a conflict save that **never discards text** — the loser's version becomes a draft revision |
| "Is this official or someone's opinion?" | The four status chips; official content locked, above member content, on the same page |
| Search fails so people ask in Discord | Log zero-result queries; show leads their top failed searches (free content roadmap); the zero-result screen offers "Ask in #guild" and "Request this page" |
| Document dump | No file browser; attachments belong to pages; label required |
| Two wikis therefore zero wikis | MediaWiki retired on a announced date |
| Admin over-deletes, author never returns | Archive with a reason, addressed to the author by name; hard delete is not in the UI |

---

## 8. Open questions (not blockers for drafting)

1. **`org/` repo access** for spec E — Josh to ask Morlock. E can be specced without it; it cannot be
   built without it.
2. **MediaWiki content audit** — how many pages actually carry real content. Ops task, informs the
   migration weekend, does not block any spec.
3. **QR sticker production** — size, lamination, who applies them. Spec A owns the `/m/<code>/` route
   and the printable sheet; the physical rollout is an ops task.

---

## 9. Ownership reconciliation (added 2026-09-07 after the adversarial review round)

Four independent reviewers read the four specs against the UX-completeness rubric and against each
other. **The individual UX design passed** — every spec names its Save, its "+ Add", its real
danger-button Delete, its empty/loading/error/success states, both themes, and its 390px reflow. The
famous failure this process exists to prevent is absent.

**What failed was the seam.** A, B and D each specified the same six objects independently and
incompatibly; built as written, D's migration phase fails on `makemigrations`. This section is
**binding and supersedes any contradicting text in A, B, D or E.** One owner per object, no exceptions.

### 9.1 Contested objects — the ruling

| Object | Owner | The losing spec must |
|---|---|---|
| `archived_at` / `archived_by` / `archive_reason` | **A** (`CharField(300)`, A1 migration) | D drops all three; D adds only `archive_redirect` |
| Soft-delete manager / `base_manager_name` | **Neither — struck entirely** | D drops the manager swap. Archived-exclusion already lives in A's `visible_for()` / `not_archived()`, and A's tombstone requires `objects` to reach archived rows. `base_manager_name` appears nowhere in this repo. |
| The "Needs review" amber banner | **D** renders it from `WikiReport` | D's `file()` / `resolve()` **must maintain A's denormalized `needs_review_since` / `needs_review_reason`**, because A's status-pill precedence, `needs_review()` queryset, and the brief's *Out of date / Needs review* chip **in search results** all read them. A deletes its own duplicate banner partial. |
| The Safety gate | **D** (it owns the queue and the authority-aware publish) | A strikes its create-form Safety toggle and its `needs_review_*` overload. B states "D owns this, not B". |
| The advisory edit lock | **D**'s `WikiEditLock` model | A strikes its `WikiDraft`-derived lock. **A's autosave view calls `WikiEditLock.refresh()`**, and D lists that file in its §3. |
| The conflict save | **D**'s dedicated `/wiki/p/<slug>/conflict/<pk>/` screen | A strikes its inline "Save mine anyway" version. |
| `WikiRevision.kind`, `WikiRevision.author` reverse | **A** ships `kind` **and** `related_name="wiki_revisions"` (not `"+"`) | D's `wiki_page_contributors` resolver is impossible without the reverse accessor, and that notification is the round's retention mechanism. |
| `can_moderate_wiki` | **D**'s page-scoped `can_moderate_wiki_page(request, page)` = effective staff **or** `can_edit_guild(request, page.guild)` | A adopts it and drops its request-only version. A guild lead must be able to archive a bad page in their own guild without an officer. |
| `can_verify_wiki_page` | **A** ships it; B and D consume | Definition: `can_edit_guild` for a guild-scoped page, `is_effective_staff` for a space-wide one, **plus B's guard that an Official page is never verifiable**. Equipment orienters who are not guild staff stay deferred (already recorded in B §10). |
| `wiki.page_verified` event | **D** owns the `Trigger`, the resolver, the copy, and the `period` | A disclaims it (correct). **B deletes its `Recipients.SINGLE_USER` fallback** and calls it in D's shape, passing its role label as `verifier_role`. B and D build in parallel, so a fallback registration is a guaranteed collision. |
| `verified_role_label`, `verified_note` | **B**'s migration | A does not ship them; B must add `verified_note` (it currently assumes A provides it). |
| Field names | **A's names win** | B renames `last_confirmed_at` → `last_checked_at` (and sets `last_checked_by`), `objects.visible()` → `visible_for()`, `out_of_date()` → the `REVIEW_INTERVALS` / `is_out_of_date` / `review_due_at` symbols. `NEEDS_REVIEW` is not a `Status` value. D renames `published` → `is_published`, `publish_new()`/`save_edit()` → A's `create_page()`/`apply_edit()`. |
| `SiteActivity.Kind` — all six wiki values | **A**, in the A1 migration | Enum values are inert; shipping all six up front removes the ordering hazard between A, B and D entirely. D drops its own migration and keeps its canonical `log()` call shapes. |
| Search: `?guild=`, `?kind=`, `?stale=`, `?source=`, grouped results, source chip, and a **browse list for an empty `q`** | **A**, as a real named change to its own scope | This is not optional. As specified, three of B's "See all" links dead-end on an empty page, and E's Policies search box returns zero policies. It is also what brief §2's "one search box spans all three" requires. |
| `?confirm=1` (cue Still accurate) and `?wanted=<pk>` (fulfil on create) | **A** carries both | B's digest CTA and B's whole wanted-page fulfilment loop depend on them; A currently knows about neither. |
| The zero-result search screen | **B**'s `_wiki_search_empty.html` | A includes it and deletes its own inline version. |
| `_wiki_card.html` | **A**, with an optional `snippet` parameter and an `actions_partial` slot | Without the slot B's compact Verify button has nowhere to live; without `snippet` search results fork into a second card shape, breaking the one-card contract in §3. |
| CSS namespaces | A owns bare `pl-wp-*`; **B namespaces `pl-wp-tab__*`**, **D namespaces `pl-wp-mod__*`**, E keeps `pl-gov-*` | Three parallel PRs claiming `.pl-wp-empty` / `.pl-wp-row` / `.pl-wp-panel` is a silent collision. Re-grep against A's merged branch before writing CSS. |
| Template directory | `templates/hub/partials/_wiki_*.html` | D moves off `templates/hub/wiki/`. |
| `VERSION` and the changelog | Every PR in the round bumps `VERSION` with **no** changelog entry except the **last PR of the whole round**, which adds the single entry | Re-stamping re-posts to Discord (see `project_plfog_discord_autoannounce`), so the brief's earlier "first PR adds the entry" would announce the wiki a dozen times. This supersedes §5.9. |

### 9.2 Verified-false claims that must be corrected wherever they appear

Each was checked against the working tree on 2026-09-07.

- **`--hub-warn` is not a token.** Zero occurrences in `static/css/`. Amber in this codebase is literal hex plus a light override — copy `.pl-confirm-warn` (`components.css:1372`): `rgba(251,191,36,0.12)` / `#fbbf24`, with `[data-theme="light"]` → `rgba(180,120,10,0.1)` / `#8a5b06`. Any `background: var(--hub-warn)` renders transparent in both themes.
- **`.pl-input` is defined in no CSS file in this repo**, yet `components/confirm_modal.html:77,87` renders both the note input and the typed-confirmation input with it, outside any field scope. **This is a live bug in shipped code today** — those inputs are browser-default white boxes with near-invisible text on the dark theme, for all three existing callers. D fixes it once (wrap in `.hub-form-group`, or define `.pl-input` from `--hub-input-bg` / `--hub-input-border`) and three existing callers are fixed for free.
- **A 204 cannot carry an `hx-swap-oob` swap.** Every "204 + toast + OOB" response in B (Verify, Still accurate) would toast success and leave the screen stale. Return **200 with the OOB fragment plus `trigger_toast()`**, per FRONTEND.md's OOB pattern. Keep 204 only where nothing on screen changes.
- **`trigger_client_event(response, "close-modal-wiki-report")` does not close anything.** `components/modal.html:26` listens for `close-modal` and compares `$event.detail` to the modal id. Use `trigger_client_event(response, "close-modal", "wiki-report")`. Order matters: `trigger_toast()` **overwrites** `HX-Trigger` while `trigger_client_event()` merges, so set the toast first.
- **`extra=0` cannot render pre-seeded starter prompts.** A `ModelFormSet` renders `initial_form_count() + extra`, which is 0 for a new page. Create mode uses `extra=len(prompts)` with `initial=[...]`; edit mode uses `extra=0`. And the fact form's `clean()` must treat a label-with-no-value row as empty, or an untouched prompt row blocks Save — the exact Rule 11 bug.
- **`PageContentEditorWidget` hardcodes `context["widget"]["toolbar"] = "page"`** (`core/widgets.py`), and `rich-editor-init.js` builds Quill with no `handlers` seam. A `wiki` toolbar with an image button requires editing both; A must list them in its §3.
- **A form containing file inputs needs `enctype="multipart/form-data"`**, and every ModelFormSet row needs `{{ f.id }}`. Both are in the canonical `guild_edit.html` editor; both are missing from A's descriptions. Without them uploads silently vanish and rows cannot match their instances.
- **`call_command("sync_governance_docs --if-configured")` raises `CommandError: Unknown command`.** `ScheduledJob` has no args field and both dispatch sites pass the string as a command name. E must make skip-when-unconfigured the default behavior, or add an `args` field and thread it through both call sites. Separately, an unconfigured run that exits 0 shows a **green** nightly job that has never synced anything — the exact "exits 0 either way" failure E quotes as its motivation.
- **`get_object_or_404` renders the site-wide `templates/404.html`** ("Browse Past Lives classes"), not E's carefully written governance copy. E must name a real delivery mechanism, and its test must assert the literal sentence rather than only that two responses match — as written that test passes on the generic page while the screen is wrong.
- **The governance profile emits no heading ids, so `toc()` returns empty for every document.** `_MEMBER_EXTENSIONS` contains no `toc` extension; help articles only have anchors because a human hand-writes `{#slug}`. Adding `toc` then collides with `_HEADING_ID_PATTERN` (`^[a-z0-9-]{1,80}$`), because python-markdown dedupes repeated headings with an **underscore** — and bylaws repeat "Purpose" and "Definitions" constantly, so those headings would silently vanish from the TOC. E needs the extension *and* an id filter accepting `_`, plus a duplicate-heading test.

### 9.3 Permission holes to close (A)

- `hub_wiki_quick_tip`, `hub_wiki_quick_photo` and `hub_wiki_image_upload` are gated on "active member", but the tip route writes the body through `apply_edit`. **Gate all three on `can_edit_wiki_page(request, page)`**, or a member gets two write affordances at the bottom of an Official page — against the locked rule that Official pages give members no edit affordance at all.
- The action bar renders only when `can_edit_wiki_page` is true (except "Still accurate" and D's Report, which any active member gets), and is hidden entirely on an archived page.
- The Safety toggle is on the **create** form only; the edit form omits it and `apply_edit` never writes `is_published`. Otherwise a member creates a Safety page, opens Edit, unticks the box, and publishes past the one gate the round has.

### 9.4 Half-built loops to close

- **B:** `fulfil()` has exactly one caller and that caller does not exist, so a lead can add a wanted page and never mark it done except by deleting it — which discards the credit and leaves the "Already Written" section permanently empty. Add a per-row **Mark As Written** for leads, and render **Release** for leads on someone else's stale claim (the model method exists; the button is lead-invisible).
- **D:** the amber banner never comes down on the path a lead actually walks (see banner → Edit → fix). Put **Mark Reviewed** on the banner itself for `can_moderate_wiki_page`. A reporter cannot withdraw a misfire. An archived page is unreachable from anywhere — add an `?archived=1` filter to the review queue.
- **A:** a draft can be saved, listed and discarded, but nothing says what happens when the editor opens and a draft exists. Add a resume banner: "You have unsaved changes from 12 minutes ago. **[Use my draft]** / **[Start from the saved page]**", defaulting to the saved page.

> These four documents are **specs only — do not build until approved.** Every PR in the round bumps
> `plfog/version.py` VERSION; only the final PR of the round adds the single curated changelog entry.
