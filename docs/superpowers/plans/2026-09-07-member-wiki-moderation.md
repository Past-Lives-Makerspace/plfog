# Member Wiki — Moderation, Reports & Revert (Spec D) — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-09-07 · **Revised 2026-09-07** after the cross-spec reconciliation round (brief §9).
**Surface:** FOG hub `pastlives.test` — every wiki page (`/wiki/p/<slug>/`), the new review queue
(`/wiki/review/`, including its `?archived=1` view), the page history (`/wiki/p/<slug>/history/`), the
conflict-save screen, the wiki editor, and `components/confirm_modal.html` (a shared component, fixed here).
**Related:** `2026-09-07-member-wiki-brief.md` (**binding** — read it first; **§9 supersedes anything in this
document that contradicts it**), `2026-09-07-member-wiki-core.md` (A, the store this consumes),
`2026-09-07-member-wiki-guild-tab.md` (B, emits two of the activity kinds this spec owns and calls one of its
events), `2026-09-07-governance-doc-mirror.md` (E, unaffected).

---

## 1. Summary

Any member can write on the wiki, so any member needs a way to say "this is wrong" without waiting for
permission, and staff need a way to answer that is smaller than deletion. This spec adds **Report a Problem**
(two taps from any page, phone included), which puts an amber *Needs review* banner on the page quoting the
reporter's words **while the page stays fully readable**, and routes the report to the people who actually know
the shop. It adds a **review queue** at `/wiki/review/` scoped to what each person may moderate, and three
graduated staff actions in the page header — **Edit** the sentence, **Add an Official Note** (a locked callout
members cannot touch), or **Archive** (the URL keeps working and explains itself, and the author hears why, by
name). It adds staff-only **Revert** on the page history, a **soft advisory lock** so two people rarely collide,
and a **conflict save that never discards a word** of the loser's text. Finally it adds the safety gate: a member
proposing safety content lands a draft in a lead's queue instead of publishing live.

The whole design is one bet: **the alternative to graduated actions is over-deletion, and a member whose page
silently disappears does not write a second one.**

It also fixes one bug that is live in shipped code today: `components/confirm_modal.html` styles its note input
and its typed-confirmation input with `.pl-input`, **a class defined in no CSS file in this repo**, so both
render as browser-default white boxes with near-invisible text on the dark theme. D's Archive screen is the one
place in this round that uses a typed confirmation, so D owns the one-line component fix (§6.13) and the three
existing callers (`equipment_manage.html`, `user_settings.html`, `classes/partials/roster_modals.html`) are
fixed for free.

### What this spec no longer owns (brief §9.1, binding)

The reconciliation round moved six objects out of this spec. Each is listed here so a reader of D alone does not
go looking for them, and each is repeated inline where it used to live.

| Object | Now owned by | Note |
|---|---|---|
| `archived_at` / `archived_by` / `archive_reason` | **A**, `CharField(300)` reason, A1 migration | D adds only `archive_redirect` and inherits A's 300-char reason. |
| The soft-delete manager pair and `base_manager_name` | **Nobody — struck entirely** | Archived-exclusion lives in A's `visible_for()` / `not_archived()`; A's tombstone needs plain `objects` to reach an archived row. `base_manager_name` appears nowhere in this repo. |
| The six `SiteActivity.Kind` wiki values and their migration | **A**, in the A1 migration | Enum values are inert; shipping all six up front removes the ordering hazard between three parallel PRs. D keeps the canonical `log()` call shapes (§4.4) and ships no `core/` migration. |
| `WikiRevision.kind` (with `CONFLICT_DRAFT`) and `WikiRevision.author`'s `related_name="wiki_revisions"` | **A** | D's `AlterField` goes away; D states the dependency instead (§2). |
| The zero-result search screen, the `?guild=` / `?kind=` / `?stale=` search filters, `_wiki_card.html` | **A** and **B** | D touches no read or search surface. |
| `verified_role_label` / `verified_note` | **B**'s migration | D only consumes the label, as `verifier_role` in the `wiki.page_verified` context (§7.2). |

**Field and method names are A's throughout this document**: `is_published` (never `published`),
`create_page()` and `apply_edit()` (never `publish_new()` / `save_edit()` / `apply_conflict_draft()`).

### Where this spec won, and what the other specs strike

Also binding, from the same section — recorded here because D's screens depend on there being exactly one of each:

| Object | D owns | What the losing spec removes |
|---|---|---|
| **The Safety gate** | The queue, the authority-aware publish, and the determination rule (§5.6) | **A strikes its create-form Safety toggle and its `needs_review_*` overload.** A no longer has a competing path: there is one gate, in one method, and the edit form cannot bypass it. |
| **The advisory edit lock** | `WikiEditLock` (§4.2, §5.5) | A strikes its `WikiDraft`-derived lock. **A's `hub_wiki_autosave` view calls `WikiEditLock.refresh()`**, so that file is in D's §3 map — D's "autosave refreshes the lock" claim is only true of *this* lock. |
| **The conflict save** | The dedicated `/wiki/p/<slug>/conflict/<pk>/` screen (§5.5, §6.10) | A strikes its inline "Save mine anyway" re-post. |
| **`can_moderate_wiki_page(request, page)`** | Effective staff **or** `can_edit_guild` on the page's guild (§5.3) | A drops its request-only `can_moderate_wiki`. A guild lead must be able to archive a bad page in their own guild without pulling in an officer. |
| **`wiki.page_verified`** | The `Trigger`, the resolver, the copy, and the `period` (§7.2) | **B deletes its `Recipients.SINGLE_USER` fallback** and calls it in D's shape, passing its `verified_role_label` as `verifier_role`. |
| **The amber "Needs review" banner** | Rendered from `WikiReport` (§6.3) | A deletes its duplicate banner partial and provides the always-present container. **D's `file()` / `resolve()` must keep A's denormalized `needs_review_since` / `needs_review_reason` in step** (§5.1, §5.2). |

### Locked decisions

Inherited from the brief (§4, §5.8, "Explicitly NO") and not re-litigated here: no page comments of any kind, no
member-facing diff viewer, hard delete only as a shell command, archive-never-delete with the author addressed by
name, approve-after except the one safety exception, one editor. **CSS namespace: A owns bare `pl-wp-*`; every
class this spec adds is `pl-wp-mod__*`** (brief §9.1 — B takes `pl-wp-tab__*`, and three parallel PRs claiming
`.pl-wp-empty` would be a silent collision).

Decided **by this spec**:

| # | Decision | Choice |
|---|---|---|
| D1 | The official note's shape | **Three fields on `WikiPage`** (`official_note`, `official_note_by`, `official_note_at`), not a model. Exactly one note per page — a stack of official notes is a comment thread wearing a hat, and comments are out. Fields on the page are also **revert-immune**: if the note lived in the body, a revert would silently destroy staff copy, and if it lived in a child model it would invite N. |
| D2 | Archive's shape | **A owns `archived_at` / `archived_by` / `archive_reason`** (`CharField(300)`, A1 migration). D adds exactly one field, `archive_redirect`, and one method set (§5.4). **No manager swap and no `base_manager_name`**: archived-exclusion already lives in A's `visible_for()` / `not_archived()`, A's reading view resolves through plain `WikiPage.objects.all()` so the tombstone is reachable, and `report.page` / `revision.page` therefore never break. Swapping the default manager would have deleted `published()` / `visible_for()` / `for_guild()` / `space_wide()` / `search()` / `needs_review()` / `with_fact_prefetch()` — every method A, B and E call — and 404'd the tombstone this spec exists to protect. |
| D3 | Report routing | **Composition, not union.** A scoped page routes to that guild's leadership; a space-wide page (or a guild with no lead and no staff) routes to admins. A union would email six admins about a Woodworking typo and train them to ignore the event. Admins still see **every** report in the queue, which is the "officers second" leg. |
| D4 | Escalation | None on a timer. No cron, no "unreviewed for 7 days" nag. The queue plus the notification is the mechanism; a dormant queue is a staffing problem, not a scheduler problem. Deferred in §10. |
| D5 | Advisory lock storage | A small **`WikiEditLock` model**, not the Django cache. the `CACHES` setting in `plfog/settings.py` is `DatabaseCache` in prod and `LocMemCache` under `_IS_PYTEST`; a cache is *allowed* to evict, and one `cache.clear()` from any admin path would silently drop every lock — a lock that vanishes is precisely the failure it exists to prevent. A row is also directly assertable in a spec without wall-clock games. |
| D6 | Lock TTL and heartbeat | **10 minutes** from `refreshed_at`, and **no new heartbeat endpoint**: spec A's `wiki/p/<slug>/autosave/` POST refreshes the lock as a side effect. A real editing session never expires; a closed tab stops warning people within ten minutes. |
| D7 | Conflict save | The loser's text is written as a `WikiRevision` with `kind=CONFLICT_DRAFT` (never applied), and the saver lands on a reconcile screen showing both versions. Nothing is ever discarded, including when they choose "Keep Their Version" — the draft row is permanent. |
| D8 | How "safety" is determined | **Zero new fields.** Safety content *is* Official content (brief §5.2: "Policy, safety, membership terms"). The Safety starter card sets `status=OFFICIAL`; a non-staff author may not publish an Official page, so it saves as a draft. See §5.6. |
| D9 | Who publishes a safety draft, and at what status | Whoever reviews it, at **their own** authority: a guild lead publishing lands it **Guild verified**; an admin publishing lands it **Official**. One button, honest outcome, no escalation ladder to build. |
| D10 | `WIKI_MODERATOR` `AdminCapability` | **No** — every moderation action already belongs to an existing authority (guild lead/staff for a scope, admin site-wide), and at 200 members there is nobody who should moderate the wiki without being one of those; adding it would also swing `wiki.page_reported` away from the people who know the machine and toward whoever holds a badge. |
| D11 | Report lifecycle | Reports are **resolved, never deleted**, with `resolved_by` + `resolved_at` + an optional note. Two ways in: a moderator's "Mark Reviewed", and the reporter's own **Withdraw** (§5.2a), which is the same resolve with `resolved_by = reporter` and `resolution = "Withdrawn by the reporter."`. No fixed/dismissed taxonomy and no delete — the real outcome is visible in the page's own history, and a withdrawn misfire still shows that somebody looked. |
| D12 | Duplicate reports | A partial unique constraint: one **open** report per (page, reporter). A second attempt gets a friendly "You already reported this page." rather than a 500 or a flood. Different members may each file one. |
| D13 | Banner content with several open reports | Quotes the **oldest** open report (matching the queue's ordering) and appends "and 2 more". |
| D14 | Report reason rendering | Plain text, template-escaped. It is **never** run through `render_markdown` or the wiki sanitizer — it is member free text shown to every reader, and a renderer buys nothing but an XSS surface. |
| D15 | Discord | Neither wiki event declares the `DISCORD` broadcast channel. A report names a member's mistake; broadcasting it to a guild channel is a punishment nobody asked for. |
| D16 | Queue scoping | Uses `editable_meeting_scopes(request)`'s **guild list** for the scoped leg, but the space-wide leg uses `is_effective_staff(request)`, **not** that helper's council boolean — the council boolean deliberately grants any lead the council scope (`membership/permissions.py`), which is right for meetings and wrong for site-wide wiki pages. |
| D17 | Closing the banner where the work actually happens | **"Mark Reviewed" lives on the amber banner itself**, for `can_moderate_wiki_page`, not only in the queue. The path a lead actually walks is *banner → Edit → fix the sentence*, and nothing on that path resolved anything, so the page kept quoting a fixed complaint until somebody separately remembered the queue. Same modal as §6.5, same HTMX post, OOB-swapping the banner away. A staff save on a page with open reports additionally returns a toast carrying that action (§6.3). |
| D18 | The reporter can take it back | When the viewer already has an open report on this page, the Report control is replaced by a quiet **"You reported this"** state with a **Withdraw** button. Two problems, one fix: a misfire was previously permanent, and a member got no signal they had already filed until after they had typed a second report and submitted it. |
| D19 | Archived pages are findable | Restore lived only on the tombstone, which is reachable only by someone who already knows the slug — so an over-archive was unfixable in practice. `/wiki/review/` gains an **`?archived=1`** view for moderators listing archived pages in their scopes, with its own written empty state (§6.4). |
| D20 | Archive when the page has no author | Every seeded Equipment stub has `created_by=None` (`SET_NULL`), so the notice is **conditional, not unconditional**. Null author: different confirm copy, no email, and the activity payload records `author: ""`. Other revision authors are **not** emailed either — see §7.3 for why that is a deliberate under-reach and what would change it. |
| D21 | A declined safety draft is not a dead end | Declining names a real channel — a `core.email.send` transactional note to the author, the same shape as the archive notice — **and** carves out an edit permission: **the author of an unpublished proposal may edit their own draft**, even though its `status` is `OFFICIAL`. Without that carve-out `can_edit_wiki_page` locks the author out of the very draft they were asked to change (§5.6). |
| D22 | The `.pl-input` fix is D's | It is a one-line change to `components/confirm_modal.html` (or one rule in `components.css`), it is a live bug in shipped code, and D's Archive screen is the round's only typed confirmation. D also adds `confirm_note_required` to the same component, so a forgotten archive reason cannot cost a full-page POST and a retyped ARCHIVE. Two small, additive, backward-compatible changes to a component D is already touching (§6.13). |

## 2. What already exists (reuse, don't reinvent)

All locations verified against the current tree on 2026-09-07. **The symbol name is the reference; a line number
beside it is a convenience that will drift** — grep for the symbol, not the line. Three that have already moved
since these specs were drafted are given by symbol only: `SiteActivity.log()`, the `Trigger` dataclass's
positional-argument comment, and the `CACHES` block.

| Need | Existing thing | Location |
|---|---|---|
| Append-only audit rows | `SiteActivity.log(kind, *, actor=, target=, email_log=, payload=)` | `SiteActivity.log` classmethod in `core/models.py` |
| Notification spine | `emit(event_key, *, actor=, target=, context=, title=, body=, url=, period=, …)` | `core/events/emit.py:44` |
| Where emit writes the activity row | `if event.activity_kind is not None: SiteActivity.log(kind, actor=, target=)` — **no payload** | `core/events/emit.py:165-167` |
| Event catalogue + channel defaults | `EventType`, `Channel`, `ChannelDefault`, `ChannelSpec`, `_seed_from_triggers`, `_TRIGGER_RESOLVERS`, `_TRIGGER_ACTIVITY_KINDS` | `core/events/registry.py:129, 28, 49, 117, 384, 285, 334` |
| Legacy trigger catalogue that seeds the registry | `Trigger` dataclass + `TRIGGERS` + `CATEGORY_ORDER` | `core/triggers.py:23, 39, 172` |
| **The positional-argument trap** | The comment inside the `Trigger` dataclass (`core/triggers.py`, just under the `audience` field): several calls pass `audience` positionally at index 4, so any new field sits past it — **pass every flag by keyword** | `Trigger` in `core/triggers.py` |
| Composition-style resolver to copy | `guild_leadership_or_class_approvers` — guild present → its leadership; guild `None` → the fallback audience | `core/events/resolvers.py:176` |
| Guild leadership fan-out (lead + every staff role, incl. orienters) | `Guild.leadership_members()` | `membership/models.py:2237` |
| Staff-section grouping on the settings matrix | `STAFF_RECIPIENTS` frozenset + `_section_for` | `core/events/settings_matrix.py:92, 158` |
| Settings-matrix category order | `settings_matrix.CATEGORY_ORDER` | `core/events/settings_matrix.py:63` |
| Absolute member-hub URL for an email | `hub_url(viewname, *args)` (reverse + `MEMBER_BASE_URL`) | `core/events/discord_replies.py:41` |
| Branded email shell for spine copy | `wrap_email_html` + `notification_shell_light.html`, `_style_copy_fragment` | `core/events/templates.py:79-110` |
| Seedable per-event copy (subject / text / html / placeholders / sample) | `EventCopy` + `ChannelCopy` in `_CURATED` | `core/events/copy.py:52, 43` |
| Guild-edit permission, `view_as`-aware (covers lead, co-lead, secretary, treasurer, **orienter**) | `can_edit_guild(request, guild)` | `membership/permissions.py` |
| "Which guilds may I edit", 2 queries not N | `editable_meeting_scopes(request)` | `membership/permissions.py` |
| Effective-staff test (admin or guild officer, `view_as`-aware) | `is_effective_staff(request)` | `membership/permissions.py` |
| Admin gate in a hub view | `_require_admin(request)` / `_viewing_as_admin(request)` | `hub/views.py:769, 775` |
| Scoped admin duties + their one-line descriptions | `AdminCapability.Capability` + `AdminCapability.DESCRIPTIONS` | `membership/models.py:2416` |
| Guild staff roles | `GuildStaffMembership` | `membership/models.py:2340` |
| Confirm modal — plain POST, **note input**, **typed confirmation**, JS mode | `confirm_note_name` / `confirm_note_label` / `confirm_note_hint`, `confirm_typed_value` | `templates/components/confirm_modal.html` |
| Modal shell + HTMX body load | `components/modal.html` | `templates/components/` |
| **Closing a modal from a view** | `components/modal.html` listens for **`close-modal`** and compares `$event.detail` to the modal id — so it is `trigger_client_event(response, "close-modal", "wiki-report")`, **never** `trigger_client_event(response, "close-modal-wiki-report")`, which fires an event nothing listens for and leaves the modal open. **Ordering:** `trigger_toast()` *overwrites* `HX-Trigger` while `trigger_client_event()` *merges* into it, so the toast is always set first or it is silently dropped | `templates/components/modal.html`, `hub/toast.py` |
| Transactional email | `core.email.send(*, to, subject, trigger_kind, text_body, html_body=None, best_effort=False, …)` — **keyword-only**, `trigger_kind` **required**, and it takes rendered **strings**, not template paths. Both bodies are `render_to_string`'d by the caller | `core/email.py` |
| **The `.pl-input` bug** | `confirm_modal.html` renders its note input and its typed-confirmation input with `class="pl-input"`, **which is defined in no CSS file in this repo** and sits outside any `.hub-form-group` scope. Live in shipped code today for `equipment_manage.html`, `user_settings.html` and `classes/partials/roster_modals.html`. **D fixes it** (§6.13) | `templates/components/confirm_modal.html` (the two inputs), `static/css/components.css` (`.pl-form-label` / `.pl-field-hint` live here), `static/css/hub.css` (`.hub-form-group` input rules) |
| Overflow ("kebab") menu — already built | `components/row_actions.html` (`menu_include` + `menu_label`, fixed positioning, Escape/Tab/scroll dismiss, `role="menu"`) | `templates/components/row_actions.html` |
| Page header with a right-aligned action | `components/page_header.html` (`title` / `description` / `action_url` / `action_label`) | `templates/components/` |
| Pagination | `components/table_pagination.html` (takes `page`, optional `base_params`) | `templates/components/` |
| Toasts from a view | `trigger_toast(response, msg, type)` / `trigger_client_event` | `hub/toast.py` |
| Fields + toggles | `components/form_field.html`, `components/toggle.html` | `templates/components/` |
| Warn-banner styling to mirror | `.pl-equip-banner--warn` (left rule in `--color-tuscan-yellow`). **`--hub-warn` is not a token in this repo** — zero occurrences in `static/css/`; the amber pair to copy is `.pl-confirm-warn`'s `rgba(251,191,36,0.12)` / `#fbbf24` with its `[data-theme="light"]` override | `.pl-equip-banner` in `static/css/hub.css`; `.pl-confirm-warn` in `static/css/components.css` |
| Admin page shell convention | `hub-page-title` + `hub-card` + `vote-tab` tabs | `templates/hub/admin/activity.html` |
| Member display name | `Member.display_name` (preferred name, else legal name) | `membership/models.py:612` |
| Factories | `MemberFactory`, `GuildFactory`, `GuildStaffMembershipFactory`, `UserFactory` | `tests/membership/factories.py:75, 119, 397, 255` |

### What spec A must provide (the contract this spec consumes)

Spec D touches no read/edit/search surface. It needs exactly these from A, and nothing else. **Every row below is
a hard dependency: if A ships without it, D's phase 2 does not build.**

| From A | Why D needs it |
|---|---|
| `WikiPage` with `slug`, `title`, `guild` (nullable FK), `kind`, `status` (incl. `OFFICIAL`), **`is_published`** (not `published`), `created_by`, `updated_at`, and `WikiPageQuerySet` on `objects` with `published()` / `not_archived()` / `visible_for()` / `for_guild()` / `space_wide()` / `search()` / `needs_review()` / `with_fact_prefetch()` | Every screen here hangs off a page, and D8 reads `status`. **D does not replace this manager** (D2) — those eight methods are called by A, B and E. |
| `archived_at`, `archived_by`, **`archive_reason` as `CharField(300)`** | A's A1 migration ships all three. D adds only `archive_redirect` (§4.3), and every "one sentence" cap in this document is the 300-char field, not 200. |
| `needs_review_since` (`DateTimeField`) and `needs_review_reason` (`CharField(300)`) | **D writes both** (§5.1, §5.2). A's status-pill precedence, A's `needs_review()` queryset, and the brief's *Needs review* chip **in search results** all read them. If D renders the banner from `WikiReport` and leaves these null, the chip never fires anywhere outside the page itself. |
| `WikiPage.create_page(...)` and `WikiPage.apply_edit(*, editor, editor_may_verify, title, body, note="")` | D's safety gate hooks `create_page` (§5.6); D's conflict promote and D's revert both go through `apply_edit`. **These are the names — not `publish_new()` / `save_edit()` / `apply_conflict_draft()`.** |
| `WikiRevision` with `page` FK (`related_name="revisions"`), `created_at`, and the full snapshot: `title`, `body`, **`facts`** (JSON), **`status`**, `note` | History, Revert, and the conflict draft all restore or store a whole revision. A snapshots `facts` and `status` explicitly so D's revert can restore them rather than guess — restoring the prose but not the Quick Answers resurrects a half-old page (§5.4). **There is no `body_format` column**: A stores the body dual-mode and sniffs it with `looks_like_html`, so D's conflict draft and D's revert store and restore the body verbatim and let the renderer decide. |
| **`WikiRevision.kind`** as a `TextChoices` including `SAVE`, `REVERT` and **`CONFLICT_DRAFT`** | A ships all three (brief §9.1). **D's `AlterField` is gone.** The three values distinguish "a person typed this", "a person rewound to this", and "this was never applied"; a boolean would need a second boolean within a month. |
| **`WikiRevision.author` FK → `Member` with `related_name="wiki_revisions"`** (not `"+"`) | `wiki_page_contributors` (§7.2) is `Member.objects.filter(wiki_revisions__page=page)`. With `related_name="+"` there is no reverse accessor and the resolver is impossible — and that notification is the round's stated retention mechanism. A changed the `related_name` for exactly this. |
| The editor template exposing a block or include point in its footer, and the phone's sticky action bar exposing a third slot **plus a single overflow menu** | The lock warning (§6.9) and the Report button (§6.1) live there. D's staff actions merge into A's existing "⋯" rather than adding a second one (§6.7). |
| `wiki/p/<slug>/autosave/` POST **that calls `WikiEditLock.refresh(page, member)`** | The heartbeat with no new endpoint (D6). A's own draft-derived lock is struck, so this is the only thing keeping the lock alive; `hub/wiki_views.py`'s autosave view is therefore listed in D's §3 file map. |
| A page-detail view that resolves through plain **`WikiPage.objects.all()`** and an always-present `<div id="wiki-review-banner">` at composition slot 1 | So an archived page's URL keeps working (the tombstone), and so D's OOB banner swap always has a target. A's template currently names a `pl-wp-reviewbanner` class and no id — **the id is the contract**, and A deletes its own banner partial in favour of D's (brief §9.1). |
| `can_edit_wiki_page(request, page)` with the **unpublished-proposal carve-out** | The author of their own unpublished Safety proposal may edit it (D21). Without the carve-out an `OFFICIAL`-status draft is uneditable by the person who was just asked to change it. |

**Genuine gaps this spec closes:** two new models (`WikiReport`, `WikiEditLock`), **four** fields on `WikiPage`
(three official-note fields plus `archive_redirect`), two event types + two recipient resolvers, one permission
helper pair, one review queue, one history/revert page, one conflict screen, two additive fixes to
`components/confirm_modal.html`, and the `pl-wp-mod__` moderation CSS. **No `core/` migration and no
`SiteActivity.Kind` change** — A ships all six wiki kinds in A1.

## 3. Where the code lives

Template paths follow A and B: **full pages are `templates/hub/wiki_*.html`, partials are
`templates/hub/partials/_wiki_*.html`.** D no longer uses a `templates/hub/wiki/` directory of its own (brief
§9.1) — one wiki, one template neighbourhood.

```
core/
  triggers.py                  ~ two Trigger rows (keyword args only) + "Wiki" in CATEGORY_ORDER
  events/
    registry.py                ~ Recipients.WIKI_SCOPE_LEADERSHIP / WIKI_PAGE_CONTRIBUTORS;
                                 _TRIGGER_RESOLVERS + _TRIGGER_ACTIVITY_KINDS entries (both None)
    resolvers.py               + wiki_scope_leadership(), wiki_page_contributors(), _RESOLVERS entries
    copy.py                    + two _CURATED EventCopy blocks (subject/text/html + placeholders + sample)
    settings_matrix.py         ~ "Wiki" in CATEGORY_ORDER; WIKI_SCOPE_LEADERSHIP into STAFF_RECIPIENTS
  models.py                    UNCHANGED — A ships all six SiteActivity.Kind wiki values in A1 (§4.4)
membership/
  models.py                    + WikiReport, WikiEditLock; four WikiPage fields (§4.3).
                                 NO manager swap, NO base_manager_name, NO WikiRevision.Kind change.
  permissions.py               + can_moderate_wiki_page(), moderatable_wiki_scopes()
                                 (A adopts can_moderate_wiki_page and drops its request-only version)
  migrations/0167_wiki_moderation.py                NEW  (§4.5 — renumber if B lands first)
hub/
  wiki_views.py                + report, withdraw, review queue, resolve, official note, archive, restore,
                                 set-redirect, history, revert, conflict  (A creates this module)
                               ~ hub_wiki_autosave — A's view, D adds the WikiEditLock.refresh() call (D6)
                               ~ hub_wiki_edit — D adds the base_revision conflict check around apply_edit
  forms.py                     + WikiReportForm, WikiOfficialNoteForm, WikiArchiveForm, WikiDeclineForm
  urls.py                      + eleven routes (nine under wiki/p/<slug>/, plus review + its ?archived view)
templates/components/
  confirm_modal.html           ~ THE .pl-input FIX (§6.13) + the new confirm_note_required flag (D22)
templates/hub/
  wiki_page.html               ~ A's page; D supplies the partials it includes (banner, note, tombstone,
                                 report control, moderation actions) — A owns the include points
  wiki_review.html             NEW   the queue (reports + safety proposals + the ?archived=1 view)
  wiki_history.html            NEW
  wiki_conflict.html           NEW
templates/hub/partials/
  _wiki_report_button.html     NEW   the one Report control, used by header AND mobile bar
  _wiki_report_modal.html      NEW
  _wiki_review_banner.html     NEW   the amber banner (A deletes its duplicate)
  _wiki_official_note.html     NEW   rendered note
  _wiki_official_note_form.html NEW  HTMX modal body
  _wiki_archived_banner.html   NEW   the tombstone
  _wiki_moderation_actions.html NEW  Edit / Official Note / Archive, desktop header row
  _wiki_moderation_menu.html   NEW   the same actions as menu items inside A's single phone overflow
  _wiki_review_report_card.html NEW
  _wiki_review_safety_card.html NEW
  _wiki_review_archived_card.html NEW
  _wiki_history_row.html       NEW
  _wiki_lock_warning.html      NEW
templates/hub/emails/
  wiki_page_archived.html      NEW   the author's archive notice (branded shell)
  wiki_page_archived.txt       NEW   its .txt twin, same sentences in the same order
  wiki_proposal_declined.html  NEW   the declined-safety-draft note (D21)
  wiki_proposal_declined.txt   NEW
static/css/components.css      + the .pl-input rule (§6.13) — one component fix, three callers fixed
static/css/hub.css             + one pl-wp-mod__ moderation block (§6.12)
tests/hub/                     wiki_report_spec.py, wiki_review_queue_spec.py, wiki_official_note_spec.py,
                               wiki_archive_spec.py, wiki_history_revert_spec.py, wiki_edit_lock_spec.py,
                               wiki_conflict_save_spec.py, wiki_safety_gate_spec.py
tests/hub/confirm_modal_spec.py  ~ or the nearest existing component spec: the .pl-input fix and
                                   confirm_note_required, asserted against all three existing callers
tests/core/                    wiki_events_spec.py (registry/resolver/copy)
tests/membership/factories.py  + WikiReportFactory, WikiEditLockFactory
```

Home apps: `membership` (models + permissions), `core` (events), `hub` (views + templates) — all inside the
existing coverage / mypy scope. Spec A creates `hub/wiki_views.py`; D appends to it and modifies two of its views.

## 4. Data model

### 4.1 `WikiReport` (new, `membership/models.py`)

```python
class WikiReport(models.Model):
    """One member's "this page is wrong" on a wiki page — the pressure valve that makes
    open editing safe without deletion. Filing one raises an amber banner on the page
    (the page stays readable) and notifies whoever moderates that page's scope."""
```

| Field | Type | Note |
|---|---|---|
| `page` | FK → `WikiPage`, `CASCADE`, `related_name="reports"` | help_text: "The page being reported." |
| `reporter` | FK → `Member`, `SET_NULL`, `null=True`, `related_name="wiki_reports"` | Nulled if the account is deleted; the banner then reads "a member". Mirrors `SiteActivity.actor` / `AdminCapability.granted_by`. |
| `reason` | `TextField` | "What the member says is wrong. Shown on the page, in their own words." Form-bounded 10–500 chars. |
| `created_at` | `DateTimeField(auto_now_add=True, db_index=True)` | |
| `resolved_at` | `DateTimeField(null=True, blank=True)` | "Set when a lead or admin marked it reviewed. Null means open." |
| `resolved_by` | FK → `Member`, `SET_NULL`, `null=True`, `related_name="wiki_reports_resolved"` | |
| `resolution` | `TextField(blank=True, default="")` | "Optional note from the reviewer, for the audit trail." |

```python
class Meta:
    ordering = ["created_at"]           # oldest first — the queue's order, and the banner's quote
    indexes = [
        models.Index(fields=["resolved_at", "created_at"], name="idx_wikireport_open_age"),
        models.Index(fields=["page", "resolved_at"], name="idx_wikireport_page_open"),
    ]
    constraints = [
        models.UniqueConstraint(
            fields=["page", "reporter"],
            condition=models.Q(resolved_at__isnull=True),
            name="uq_wikireport_open_reporter",     # 27 chars — under the 30-char cap
        ),
    ]

def __str__(self) -> str:
    who = self.reporter.display_name if self.reporter else "a member"
    state = "resolved" if self.resolved_at else "open"
    return f"Report on {self.page.slug} by {who} ({state})"
```

> Index/constraint names are all ≤ 30 characters. `manage.py check` enforces E034 and the cap has bitten this
> repo before (PR #205) — run it after the migration.

**Manager:** `WikiReportQuerySet.open()` (`resolved_at__isnull=True`), `.resolved()`,
`.for_scopes(guild_ids, *, include_space_wide)` — the single query the queue uses (§5.3) — and
`.for_page(page)`, which the reading view uses twice: once for the banner's oldest open report and its
"and N more" count, and once to decide whether this viewer already has one open (the Withdraw state, D18).
Both come off one `open().for_page(page).select_related("reporter")` evaluation, not two queries.

### 4.2 `WikiEditLock` (new, `membership/models.py`)

```python
class WikiEditLock(models.Model):
    """A soft advisory lock: who opened this page's editor most recently, and when.

    Advisory ONLY — it never blocks a save. It exists so the second person sees
    "Dana started editing this 3 minutes ago" before they spend twenty minutes on
    text that will collide. Combined with the non-destructive conflict save (§5.5),
    this covers essentially every real collision at 200 members for roughly 2% of
    the cost of real-time collaborative editing."""

    TTL = timedelta(minutes=10)
```

| Field | Type | Note |
|---|---|---|
| `page` | `OneToOneField` → `WikiPage`, `CASCADE`, `related_name="edit_lock"` | One holder at a time; claiming overwrites a stale row. |
| `holder` | FK → `Member`, `CASCADE`, `related_name="wiki_edit_locks"` | |
| `started_at` | `DateTimeField(default=timezone.now)` | What the warning's "3 minutes ago" is measured from. **`default=timezone.now`, deliberately not `auto_now_add`.** `auto_now_add` is write-once-on-insert, and `claim()` re-uses the row via `update_or_create` when the lock is stale — so an `auto_now_add` row would keep the *previous* holder's start time under the *new* holder's name, and the warning would read "Dana started editing this 3 hours ago" about someone who opened the editor a minute ago. `claim()` therefore passes `started_at=timezone.now()` in `defaults` on every re-claim, and a plain default is the only field type that honours it. §9 pins this with a test. |
| `refreshed_at` | `DateTimeField(auto_now=True)` | Bumped by the autosave POST; expiry is measured from here. |

`__str__`: `f"{self.holder.display_name} editing {self.page.slug}"`.
No index beyond the implicit unique on `page` — the table holds at most one row per page currently being edited.

### 4.3 `WikiPage` additions (fields added by **this** spec's migration)

| Field | Type | Note |
|---|---|---|
| `official_note` | `TextField(blank=True, default="")` | "A locked staff note pinned above member content. Members cannot edit or remove it." |
| `official_note_by` | FK → `Member`, `SET_NULL`, `null=True`, `related_name="+"` | |
| `official_note_at` | `DateTimeField(null=True, blank=True)` | Drives the note's byline date. |
| `archive_redirect` | FK → `"self"`, `SET_NULL`, `null=True`, `blank=True`, `related_name="+"` | "The page readers should go to instead, if there is one." |

**Four fields, not seven.** `archived_at`, `archived_by` and `archive_reason` are **A's**, shipped in the A1
migration with the reason at `CharField(300)` (brief §9.1). Adding them here a second time at 200 characters was
the single worst seam in the round: two migrations declaring the same three column names with conflicting
lengths, so D's phase 2 fails on `makemigrations`. D reads all three and writes all three (§5.4); it declares
none of them.

**No manager swap, and no `base_manager_name`.** The earlier draft replaced `objects` with a plain
`WikiPageManager` filtered on `archived_at__isnull=True`. That would have deleted `published()`,
`not_archived()`, `visible_for()`, `for_guild()`, `space_wide()`, `search()`, `needs_review()` and
`with_fact_prefetch()` — every queryset method A, B and E call — and, worse, made A's reading view 404 an
archived page, which kills the tombstone this spec exists to protect. `base_manager_name` fell away with it: the
argument for it was technically correct but it only existed *because of* the swap, and the attribute appears
nowhere else in this repo.

Archived-exclusion instead lives where A already put it: `WikiPageQuerySet.not_archived()`, and
`visible_for(request)` which every list and search surface calls. That still satisfies the brief's rule — *a view
that forgets to gate shows too little, never too much* — because the gate is the filter every listing already
asks for, and the three surfaces that deliberately want an archived row (the tombstone, the history page, the
`?archived=1` queue) reach for `WikiPage.objects.all()` explicitly. Related traversals (`report.page`,
`revision.page`) go through the unfiltered default manager and simply work.

### 4.4 `SiteActivity.Kind` — A ships the values, D owns the call shapes

**D ships no `core/` migration.** All six values go into the existing `Kind` `TextChoices` in **A's A1
migration** (brief §9.1). Enum values are inert until something writes them, so shipping all six up front removes
the ordering hazard between three PRs building in parallel — D and B would otherwise each need a migration that
depends on the other's. What D keeps is the canonical *list* and the exact `log()` call shapes below, which the
reviewers rated correct and which A and B build against. The longest value is 18 characters, well under the
field's `max_length=50`.

```python
WIKI_PAGE_CREATED = "wiki_page_created", "Wiki page created"
WIKI_PAGE_EDITED = "wiki_page_edited", "Wiki page edited"
WIKI_PAGE_VERIFIED = "wiki_page_verified", "Wiki page verified"
WIKI_PAGE_REPORTED = "wiki_page_reported", "Wiki page reported"
WIKI_PAGE_ARCHIVED = "wiki_page_archived", "Wiki page archived"
WIKI_PAGE_REVERTED = "wiki_page_reverted", "Wiki page reverted"
```

**Every one of the six is written by a model method, never by `emit()`.** `emit()` writes its activity row with
`actor` and `target` only and *no payload* (`core/events/emit.py:167`), and the payload is the useful half here
(the reason, the reporter, the slug). So both new `EventType`s declare `activity_kind=None`, exactly as
`tab_entry_added` and the classes events do in `_TRIGGER_ACTIVITY_KINDS` — one source per row, no duplicates.

**Exact `SiteActivity.log()` call shapes.** `actor` is a `User` (or `None` for a system write); `target` is the
`WikiPage`. Specs A and B call the first three; D calls the last three.

```python
# A — on WikiPage.objects.create_page() / first save by a member
SiteActivity.log(
    SiteActivity.Kind.WIKI_PAGE_CREATED, actor=user, target=page,
    payload={"slug": page.slug, "title": page.title, "page_kind": page.kind,
             "guild": page.guild.name if page.guild_id else "", "status": page.status},
)

# A — on every member/staff save that writes a WikiRevision
SiteActivity.log(
    SiteActivity.Kind.WIKI_PAGE_EDITED, actor=user, target=page,
    payload={"slug": page.slug, "revision_id": revision.pk,
             "dropped_verification": dropped, "status": page.status},
)

# B — on one-tap Verify
SiteActivity.log(
    SiteActivity.Kind.WIKI_PAGE_VERIFIED, actor=user, target=page,
    payload={"slug": page.slug, "verified_by": member.display_name,
             "role": role_label, "guild": page.guild.name if page.guild_id else ""},
)

# D — WikiReport.file()
SiteActivity.log(
    SiteActivity.Kind.WIKI_PAGE_REPORTED, actor=user, target=page,
    payload={"slug": page.slug, "report_id": report.pk, "reason": report.reason,
             "reporter": member.display_name},
)

# D — WikiPage.archive()
SiteActivity.log(
    SiteActivity.Kind.WIKI_PAGE_ARCHIVED, actor=user, target=page,
    payload={"slug": page.slug, "reason": self.archive_reason,
             "author": author_name, "redirect_slug": redirect_slug},
)

# D — WikiPage.revert_to()
SiteActivity.log(
    SiteActivity.Kind.WIKI_PAGE_REVERTED, actor=user, target=page,
    payload={"slug": page.slug, "reverted_to_revision": target_revision.pk,
             "reverted_to_author": target_author_name, "new_revision": new_revision.pk},
)
```

There is no `WIKI_PAGE_RESTORED` kind: restoring writes `WIKI_PAGE_EDITED` with
`payload={"restored_from_archive": True}`. Six kinds is the brief's list and a seventh for a rare inverse of an
existing one is clutter in the `/manage/activity/` kind filter. **The trade is explicit and worth naming: the
`/manage/activity/` kind filter reads `SiteActivity.Kind.choices` only, so `restored_from_archive` is invisible
to it — a restore is findable as "Wiki page edited" and then only by reading the row's payload.** That is
accepted: restores are rare, and an admin hunting one has the page's own history in front of them. If restores
ever need to be filterable, the fix is a seventh kind, not a filter that reads payloads.

### 4.5 Migrations

* **No `core/` migration.** A's A1 migration carries the six `SiteActivity.Kind` values and
  `WikiRevision.kind` / `WikiRevision.author`'s `related_name`; D declares none of them.
* `membership/migrations/0167_wiki_moderation.py` — `CreateModel` × 2 (`WikiReport`, `WikiEditLock`) and
  `AddField` × 4 on `WikiPage` (`official_note`, `official_note_by`, `official_note_at`, `archive_redirect`).
  No `AlterModelManagers`, no `AlterModelOptions`, no `AlterField`. All additive and nullable-or-defaulted, so it
  is auto-reversible with no `RunPython`; nothing needs a reverse function.
* Depends on spec A's `WikiPage` migration. **B and D build in parallel and B's is currently numbered
  `0168_wiki_guild_tab`** — whichever lands second renumbers and re-points its `dependencies`, which is a
  mechanical fix but must not be discovered at merge time.
* Run `ruff format`, `mypy .`, **and `manage.py check`** after — CI runs system checks that local pytest skips,
  and the 30-character index-name cap (E034) has bitten this repo before (PR #205).

## 5. Business logic (fat models)

Views parse the request, call one model method, and return a response. All validation is in `hub/forms.py`.

### 5.1 `WikiReport.file(page, reporter, reason) -> WikiReport` (classmethod)

Guards: the page is not archived (an archived page's Report control is not rendered and the view 404s on it);
`reason` already validated by `WikiReportForm`; the (page, reporter) open-report constraint is caught and
re-raised as `DuplicateWikiReport(ValueError)` so the view can answer with the friendly message rather than a
500. Side effects, in order: create the row → **`page.mark_needs_review()`** → `SiteActivity.log(
WIKI_PAGE_REPORTED, …)` (§4.4) → `emit("wiki.page_reported", …)` (§7.1). Returns the report so the view can
OOB-swap the banner.

**`WikiPage.mark_needs_review()` is the denormalization seam, and it is not optional.** D renders the amber
banner from `WikiReport` (brief §9.1), but A's status-pill precedence, A's `WikiPageQuerySet.needs_review()`, and
the brief's *Needs review* chip **in search results** all read A's two denormalized columns. So `file()` sets
them and `resolve()` clears or re-points them:

```python
def mark_needs_review(self) -> None:
    """Keep A's denormalized review columns in step with this page's open reports.

    The banner reads WikiReport; the status pill, the needs_review() queryset and the
    search-result chip read these two columns. Called by WikiReport.file(), .resolve()
    and .withdraw() so the two can never disagree — without it the chip never fires
    anywhere except the page itself, which is the one place the banner already covers.
    """
    oldest = self.reports.filter(resolved_at__isnull=True).order_by("created_at").first()
    if oldest is None:
        self.needs_review_since = None
        self.needs_review_reason = ""
    else:
        self.needs_review_since = oldest.created_at
        self.needs_review_reason = oldest.reason[:300]      # A's column is CharField(300)
    self.save(update_fields=["needs_review_since", "needs_review_reason"])
```

It writes two fields with `update_fields`, so it never trips `updated_at` semantics or the freshness clock, and
`WikiReportForm` caps `reason` at 500 characters while the column holds 300 — the truncation is deliberate and
one-directional: the banner and the queue quote the report row in full, and only the denormalized copy is
clipped.

### 5.2 `WikiReport.resolve(by, note="") -> None`

Guard: `resolved_at is None` (a second resolve raises `AlreadyResolved(ValueError)`; the queue's HTMX row swap
means a stale tab is a real possibility). Sets `resolved_at`, `resolved_by`, `resolution`, then calls
`page.mark_needs_review()` — which clears both columns when that was the last open report, and re-points them at
the next-oldest when it was not. No activity row and no notification: the reporter is not told "your report was
reviewed" — the page itself is the answer, and a "reviewed" ping with nothing visibly changed reads worse than
silence. Deferred consideration in §10.

### 5.2a `WikiReport.withdraw(by) -> None` (D18)

Guard: `by == self.reporter` (anyone else raises `PermissionDenied`; a moderator who wants it gone uses Mark
Reviewed, which is the honest label for what they are doing) and `resolved_at is None`. It is the same resolve —
`resolved_at = now()`, `resolved_by = self.reporter`, `resolution = "Withdrawn by the reporter."` — followed by
`page.mark_needs_review()`, so the banner comes down immediately if this was the only open report. **Nothing is
deleted** (D11), the partial unique constraint frees up so the same member may file again about something else,
and the audit trail still records that somebody looked and changed their mind.

### 5.3 `moderatable_wiki_scopes(request) -> tuple[list[Guild], bool]` (`membership/permissions.py`)

```python
def moderatable_wiki_scopes(request):
    """The guilds whose wiki pages this request may moderate, plus space-wide access.

    Reuses editable_meeting_scopes()'s cheap two-query guild list, but deliberately
    REPLACES its council boolean: that helper grants the council scope to anyone with
    lead/staff authority in ANY guild (right for meetings, §5.1 of the meetings spec),
    which would hand every guild lead the site-wide wiki. Space-wide pages are
    effective-staff only.
    """
    guilds, _council = editable_meeting_scopes(request)
    return guilds, is_effective_staff(request)
```

And the per-page twin — **D owns this, and A drops its request-only `can_moderate_wiki`** (brief §9.1). It is
used by every template affordance and every view gate in both specs, so the two can never drift. The reason it is
page-scoped rather than request-scoped is the whole point: a guild lead must be able to archive a bad page in
their own guild without pulling in an officer, and `is_effective_staff` alone cannot express that.

```python
def can_moderate_wiki_page(request, page) -> bool:
    """Admin/officer anywhere; a scoped page's guild lead or any guild staff role
    (co-lead, secretary, treasurer, ORIENTER — can_edit_guild already covers all four)."""
    if is_effective_staff(request):
        return True
    return page.guild is not None and can_edit_guild(request, page.guild)
```

The queue's single query:

```python
guilds, space_wide = moderatable_wiki_scopes(request)
reports = WikiReport.objects.open().for_scopes([g.pk for g in guilds], include_space_wide=space_wide) \
                    .select_related("page", "page__guild", "reporter")
```

`for_scopes` is `Q(page__guild_id__in=ids) | Q(page__guild__isnull=True)` when `include_space_wide`, else the
first clause alone — one query, no N-query loop, and an admin's `editable_meeting_scopes` short-circuit already
returns every guild.

### 5.4 `WikiPage` moderation methods

| Method | Guards | Side effects |
|---|---|---|
| `set_official_note(text, by)` | text ≤ 1000 chars (form). Caller already gated by `can_moderate_wiki_page`. | Sets the three note fields; **writes no `WikiRevision`** — the note is not member content and must survive every revert. `WIKI_PAGE_EDITED` activity with `payload={"official_note": "set"}`. |
| `clear_official_note()` | note is non-empty | Blanks all three fields; `WIKI_PAGE_EDITED` with `payload={"official_note": "cleared"}`. |
| `archive(by, reason, redirect=None)` | not already archived (`AlreadyArchived`); `reason` non-blank (`ValueError`); `reason` fits A's `CharField(300)`; `redirect` is not `self` and is not itself archived | Sets A's `archived_at` / `archived_by` / `archive_reason` plus D's `archive_redirect` → `WIKI_PAGE_ARCHIVED` activity → **notifies the author by name, when there is an author** (§7.3, D20). Resolves nothing: open reports on an archived page stay open until a human marks them reviewed, because archiving is an answer to *some* reports and not others. |
| `restore(by)` | is archived | Clears all four archive fields — "restoring clears the attribution so it never claims a stale actor" (brief §3) — then `WIKI_PAGE_EDITED` with `payload={"restored_from_archive": True}`. |
| `set_archive_redirect(target)` | page is archived; target is live and not self | Sets `archive_redirect`; no activity row (a redirect is a signpost, not an event). |
| `revert_to(revision, by)` | `revision.page_id == self.pk`; revision is not the current head (`NothingToRevert`); revision `kind != CONFLICT_DRAFT` — a draft was never applied and is used from the conflict screen, not reverted to from the history list | Restores the revision's **whole** snapshot — `title`, `body`, **and `facts`** — then writes a **new** `WikiRevision(kind=REVERT, author=by, note="Reverted to 12 Mar")` (history is append-only; a revert never rewinds the list) → `WIKI_PAGE_REVERTED` activity. See the two paragraphs below on `facts` and on `status`. |

**Revert restores the facts, not just the prose.** A snapshots `WikiRevision.facts` explicitly so that this
method can restore them rather than guess, and warns why: a revert that put back the body but left the Quick
Answers alone resurrects a half-old page whose two-column summary contradicts the text underneath it — which is
worse than either version on its own. `revert_to` therefore replaces the page's `WikiPageFact` rows wholesale
from the snapshot (delete then bulk-create in `sort_order` order, inside the same transaction as the page save),
and rebuilds `search_text` after, since the facts feed it.

**Revert does not restore `status`, and that is settled here once.** A snapshots `status` on every revision, so
the data is there; D deliberately does not use it. Reverting text is not a verification decision: rewinding a
page that was Guild verified last March must not silently re-apply a green check that a human took off, and
rewinding a page that has since been verified must not strip it. A's rule — a non-staff edit drops verification,
a staff edit keeps it — owns `status` end to end, and only staff can revert anyway, so the staff-edit branch is
the one that applies. The snapshot column stays because it is the honest record of what the page looked like and
because a future "restore this page exactly" would need it; nothing in this round reads it.

### 5.5 The advisory lock and the conflict save

**Claim.** The editor GET calls `WikiEditLock.claim(page, member)`:

```python
@classmethod
def claim(cls, page, member):
    """Take or refresh the lock. Returns the PREVIOUS live holder's lock (or None) so the
    view can warn. Never blocks: a lock is a message, not a gate.

    On a re-claim of a stale row, started_at is reset to now. It is the field the
    warning's "started editing 3 minutes ago" is measured from, so leaving the
    previous holder's timestamp on the row would attribute their start time to the
    new holder and print a warning that is simply false.
    """
    now = timezone.now()
    existing = cls.objects.filter(page=page).select_related("holder").first()
    if existing and existing.holder_id != member.pk and existing.refreshed_at > now - cls.TTL:
        return existing                      # live, someone else's: warn and leave it alone
    cls.objects.update_or_create(
        page=page,
        defaults={"holder": member, "started_at": now, "refreshed_at": now},
    )
    return None
```

It reads the existing row first; if it exists, is not stale (`refreshed_at > now - TTL`), and belongs to someone
else, that row is returned for the warning **and left alone** — the second editor does not steal the lock, so a
third person still sees the original warning about the person who is most likely still typing. If it is stale or
absent or already theirs, `update_or_create` claims it and `None` is returned. `started_at` is in `defaults` and
the field is `default=timezone.now`, not `auto_now_add`, for exactly the reason in §4.2; §9 pins it with a test
that re-claims a stale row and asserts the elapsed time restarts.

**Refresh.** Spec A's `wiki/p/<slug>/autosave/` POST calls `WikiEditLock.refresh(page, member)`
(`save(update_fields=["refreshed_at"])` on the caller's own row, a no-op if they do not hold it). No new
endpoint, no polling timer, no JS. **A's autosave view is a file D edits** (§3): A's own `WikiDraft`-derived lock
is struck per brief §9.1, so "autosave refreshes the lock" is true only once D has added this call.

**Release.** A successful save deletes the row. Cancel does not — a member who backs out with the browser button
never fires anything, so relying on an explicit release would leave stale locks anyway. Expiry is the real
release; ten minutes is short enough that an abandoned tab stops warning people quickly and long enough that a
person actually typing (autosaving) never loses it.

**What a `WikiRevision` means, pinned in one sentence.** A's `apply_edit()` writes the **pre-edit** snapshot: a
revision row holds what the page looked like *before* the save that created it, and `page.revisions.first()` is
therefore "the previous body", not the current one. Two consequences run through the rest of this spec, and both
are stated here so nothing downstream re-derives them differently:

* The history list (§6.8) labels each row **"what this page looked like before <person>'s edit on <date>"** — it
  does **not** claim the row is what that person wrote, because it is not.
* The conflict screen's right-hand card (§6.10) renders **`page.body`**, the live page, and never
  `page.revisions.first()`. Rendering the head revision there would show the reader the version *before* the
  edit they are being asked to reconcile against — the one thing on that screen that must be current.

The one exception is a `CONFLICT_DRAFT` row, which is a **post**-edit snapshot by construction: it is text that
was submitted and never applied. The history list gives it its own chip and its own label for exactly that
reason.

**Conflict detection.** The edit form carries a hidden `base_revision` — the pk of the page's newest revision at
load. **The check lives in `hub_wiki_edit`, around A's `apply_edit()`, not inside a new page method** — A owns
the save path and D wraps it, so there is one writer of page content in the round. On POST the view compares the
submitted `base_revision` to the current newest revision id. Equal → call `apply_edit()` normally. Different →
**conflict**, and before anything else:

```python
draft = WikiRevision.objects.create(
    page=page, author=member, kind=WikiRevision.Kind.CONFLICT_DRAFT,
    title=submitted_title, body=submitted_body, facts=submitted_facts,
    status=page.status, note="Unmerged: someone else saved first",
)
raise WikiSaveConflict(draft=draft, theirs=page)
```

There is no `body_format` argument: A stores the body dual-mode and sniffs it with `looks_like_html` at render
time, so the draft stores the submitted body verbatim and the renderer decides. The page is **not** modified. The
view catches `WikiSaveConflict` and redirects to `/wiki/p/<slug>/conflict/<draft.pk>/`. Nothing the member typed
exists anywhere but in a durable database row before they see a single pixel of the conflict screen — that
ordering is the entire feature.

From the conflict screen, **"Keep My Version" calls A's `apply_edit()`** with the draft's `title` / `body` /
`facts` and `note="Resolved an edit conflict"`. There is no `apply_conflict_draft()`: promoting a draft is an
ordinary edit whose text happens to come from a stored row, and routing it through the one save method keeps the
verified-drop rule, the search-text rebuild and the activity row identical to every other save. The
`CONFLICT_DRAFT` row stays in place forever as the record of what happened.

### 5.6 The safety gate (D8, D9)

Determination, with **no new field**: a page whose `status == WikiPage.Status.OFFICIAL`. The brief already
defines Official as "Policy, safety, membership terms" and already forbids members from editing Official pages,
so safety flavour and Official status are the same fact. The starter chooser (spec A) gains a **Safety & Rules**
card that sets `status=OFFICIAL` alongside the picked `kind`; every other starter leaves `status=COMMUNITY`.

**D owns this gate outright, and A no longer has a competing path** (brief §9.1). A's create-form
"This page tells someone how to stay safe" toggle is struck, and so is A's overload of `needs_review_since` /
`needs_review_reason` to mean "waiting for a lead" — those two columns now mean exactly one thing, *somebody
reported a problem* (§5.1), which is what A's status pill and search chip read them as. The failure the old
arrangement invited is worth naming because it is why there is only one gate now: with the toggle on the create
form only, a member could create a Safety page, open Edit, untick the box, and publish straight past the single
gate this round has. There is no box to untick.

The gate lives in one place, `WikiPage.objects.create_page(...)`:

```python
if status == WikiPage.Status.OFFICIAL and not can_moderate_wiki_page(request, page):
    page.is_published = False       # a draft, not a rejection
    page.save(update_fields=["is_published"])
```

Precisely: a non-moderator saving an Official-status page saves it **unpublished** (`is_published=False` — A's
field name). It then appears in the review queue's second section (§6.4) for that page's scope — derived from
`page.guild`, exactly like a report, so there is no `assigned_to` field to add, no assignment to keep in sync,
and no orphan when a lead steps down. The author gets a full-page message: *"Thanks. Safety pages get a second
read before they go live. The Woodworking leads have it, and you will hear back."*

Publishing is `WikiPage.publish_proposal(by)`, and the resulting status follows the publisher's authority (D9):
`is_effective_staff` → `OFFICIAL`; a guild lead/staff → `GUILD_VERIFIED`. Both write `WIKI_PAGE_CREATED` /
`WIKI_PAGE_EDITED` as appropriate.

**Declining is a real loop, not a shrug (D21).** `WikiPage.decline_proposal(by, note)` sets nothing on the page
except leaving `is_published=False`, records the note on the page's own history as a `WikiRevision` with
`kind=SAVE`, `author=by` and `note="Sent back: <the reviewer's note>"` — so the reason is in the one place both
people already look — and then **emails the author**, through `core.email.send` with
`trigger_kind="wiki.proposal_declined"` and `best_effort=True`, templates
`templates/hub/emails/wiki_proposal_declined.{html,txt}` (§7.3's shape, same branded shell, same absolute-URL
rule). Not an event type: like the archive notice it is a per-person transactional message with no preference to
hold and no bell row worth adding. Nothing is deleted, ever.

**And the author can act on it.** An unpublished proposal carries `status=OFFICIAL`, and A's
`can_edit_wiki_page` refuses Official pages to non-staff — so without a carve-out the author is locked out of the
draft they were just asked to change, which is a dead end wearing a friendly note. A's helper therefore carries
one extra clause, stated here because D's loop is what needs it:

> **An unpublished page may always be edited by its own `created_by`,** whatever its status. Publishing is still
> gated on `can_moderate_wiki_page`; only editing is opened. The moment it is published the ordinary Official
> rule applies again and the author sees no edit affordance at all.

Every other kind of page is unaffected: approve-after, live immediately, no queue.

### 5.7 Domain exceptions

`DuplicateWikiReport`, `AlreadyResolved`, `AlreadyArchived`, `NothingToRevert`, `WikiSaveConflict` — all in
`membership/models.py` beside the models, all subclassing `ValueError` except `WikiSaveConflict`, which carries
`draft` and `theirs` and subclasses `Exception` because it is control flow, not a bad input. Withdrawing
someone else's report raises Django's own `PermissionDenied` rather than a domain exception: it is an
authorization failure with an existing 403 path, not a domain state the caller can recover from.

## 6. UI / UX

Design language: hub shell, theme tokens only, new classes under **`pl-wp-mod__`** in `hub.css`.

**CSS grep performed before naming anything** (brief §5.7): `pl-wp-` returned **0 hits** in both
`static/css/hub.css` and `static/css/components.css` when these specs were written. `pl-wiki-toc` and
`pl-wiki-article` are taken by the Help Center (8 hits). Three near-misses are already claimed by unrelated
features and must not be reused: `.pl-note` / `.pl-notes` / `.pl-note--unread` (notifications),
`.pl-review-details` / `.pl-review-note` (class review), `.pl-lock-disposition`.

**But `pl-wp-` is no longer free.** Spec A occupies bare `pl-wp-*` heavily (`pl-wp-body`, `pl-wp-toc`,
`pl-wp-facts`, `pl-wp-actionbar`, `pl-wp-photos`, `pl-wp-details`, …) and B is being namespaced to
`pl-wp-tab__*`. Per brief §9.1, **every class this spec adds is `pl-wp-mod__*`** — three parallel PRs each
inventing `.pl-wp-empty` or `.pl-wp-row` is a silent collision that no test catches and both themes hide.
**Re-grep `static/css/*.css` against A's merged branch before writing a line of CSS.** Verify both themes on
every screen.

### 6.1 The Report control — `templates/hub/partials/_wiki_report_button.html`

One partial, two placements, so the label and the modal id can never drift.

* **Desktop**, in the page header's action row, after the graduated staff actions and set apart from them:
  `<button class="pl-btn pl-btn--sm pl-btn--ghost pl-wp-mod__report-btn" @click="$dispatch('open-modal', 'wiki-report')">`
  with a small flag SVG (`aria-hidden`) and the label **"Report a Problem"**. Quiet by design — it is a safety
  valve, not a call to action.
* **Mobile**, as the third slot in spec A's sticky bottom bar (Add Photo / Add Tip / **Report**), full 48px tall,
  label **"Report"**, same `@click`. The bar is A's; this partial is what A includes in slot three.

Not rendered at all when the page is archived, or for a viewer without an active membership. Two taps end to
end: tap Report, type, tap Send.

**The already-reported state (D18).** When the viewer has an open report on this page, the partial renders a
different thing entirely — not a disabled button, and not the same button leading to a duplicate error the member
only meets *after* typing a second report:

```
🏳 You reported this · 2 hours ago            [ Withdraw ]
```

* A quiet line in `--hub-text-muted` at the same place the button was (`pl-wp-mod__reported`), with the report's
  `timesince` so it is obvious which one they mean. No colour, no pill — a person's own pending report is
  information, not a status.
* **Withdraw** is `pl-btn pl-btn--danger pl-btn--sm` and goes through `confirm_modal.html`
  (`confirm_id="wiki-report-withdraw"`, `confirm_title="Withdraw your report?"`,
  `confirm_message="The note comes off the page. Nothing you wrote is deleted, and you can report the page again
  later if you need to."`, `confirm_button_text="Withdraw"`), plain POST to `hub_wiki_report_withdraw`.
* **On success:** redirect back to the page with the Django message *"Report withdrawn."*; the amber banner is
  gone if this was the only open report, and still there quoting the next-oldest if it was not (`resolve()` and
  `withdraw()` share `mark_needs_review()`, §5.2).
* **On the phone**, slot three shows the same two states: **Report**, or a 48px **Withdraw** with the muted line
  above it inside the bar's third cell.
* A moderator viewing their own open report sees both this control and the banner's **Mark Reviewed** (§6.3) —
  two honest labels for two different acts, and the spec does not try to merge them.

### 6.2 The Report modal — `templates/hub/partials/_wiki_report_modal.html`

* **Container:** `components/modal.html` with `modal_id="wiki-report"`, `modal_title="Report a Problem"`,
  `modal_size="md"`. One field, so a modal + toast is exactly what the FRONTEND.md interaction table prescribes.
  The component renders the body inside `<div class="pl-modal__body" id="wiki-report-body">`, which is the id the
  form posts back into.
* **Body:** an intro line — *"Tell us what is wrong. The page stays up and readable while a guild lead takes a
  look."* — then `{% include "components/form_field.html" with field=form.reason %}` (a `Textarea`, 4 rows,
  inside `.hub-form-group` so it inherits `--hub-input-bg` and never renders as a browser-default white box,
  Rule 13), with `field_hint="What is wrong, and what should it say instead if you know?"`.
* **Controls:** **Send Report** (`pl-btn pl-btn--primary`, `hx-post` to `hub_wiki_report`,
  **`hx-target="#wiki-report-body" hx-swap="innerHTML"`**, `hx-disabled-elt="this"`) and **Cancel**
  (`pl-btn pl-btn--secondary`, `@click="$dispatch('close-modal', 'wiki-report')"`).
  The submit is the last element in the form (Rule 21); the action-named button matches the interaction table's
  own modal examples ("Add to Tab").
  **The explicit target is load-bearing:** without it the error response has nowhere to land, and a member who
  typed three sentences and tripped the 10-character floor loses them. With it, the view re-renders this same
  partial bound to the invalid form and the text is still in the box.
* **States.**
  * *Empty / initial:* an empty textarea, Send enabled (the server owns validation).
  * *Loading:* `hx-disabled-elt="this"` greys Send; the global `loading_bar.html` covers the request.
  * *Error — too short / blank:* the response re-renders the modal body with the form's error under the field
    ("Please say a little more so a lead knows what to look at."), modal stays open, text preserved.
  * *Error — duplicate (D12):* HTTP 200 with a toast, `"You already reported this page. A guild lead has it."`,
    and the modal closes. Not an error page; they did nothing wrong.
  * *Success:* HTTP 200 whose body is the `_wiki_review_banner.html` partial with `hx-swap-oob="true"` targeting
    **`#wiki-review-banner`** — the always-present empty `<div>` A's `wiki_page.html` renders at composition slot
    1 (§2; A's template currently names a `pl-wp-reviewbanner` class and no id, and the id is the contract). The
    reporter sees the banner appear with their own words in it, on the page they were reading. **That is the
    "what happens next" answer** — the toast says who was told, the banner shows the effect. The response also
    OOB-swaps the Report control into its **"You reported this / Withdraw"** state (§6.1), so the affordance and
    the banner update together.
  * *The two header calls, in this exact order:*

    ```python
    trigger_toast(response, "Thanks. A guild lead has been notified.", "success")
    trigger_client_event(response, "close-modal", "wiki-report")
    ```

    Both details matter and both were wrong in the previous draft.
    **The event name:** `components/modal.html` listens for `close-modal` and compares `$event.detail` to the
    modal id, so `trigger_client_event(response, "close-modal-wiki-report")` fires an event nothing is listening
    for and the modal simply stays open over the freshly swapped banner.
    **The order:** `trigger_toast()` *overwrites* the `HX-Trigger` header while `trigger_client_event()` *merges*
    into it (`hub/toast.py`), so the toast must be set first or it is silently dropped. Every success path in
    this spec that does both follows this order.
* **Dark + light:** the modal is `components/modal.html` (already theme-correct); the textarea inherits the
  `.hub-form-group` field scope in both.
* **Mobile 390px:** modal is full-width with 1rem gutters (existing `--sm/--md` behavior); the textarea is 100%
  wide; Send and Cancel stack full-width at 48px.

### 6.3 The amber banner — `templates/hub/partials/_wiki_review_banner.html`

**D owns this partial and A deletes its duplicate** (brief §9.1). A's `wiki_page.html` renders
`<div id="wiki-review-banner">` at composition slot 1, always present and empty when there is nothing to show, so
the OOB swap always has a target, and includes this partial inside it.

The banner reads `WikiReport`. **`WikiReport.file()` / `.resolve()` / `.withdraw()` additionally maintain A's
`needs_review_since` / `needs_review_reason`** through `mark_needs_review()` (§5.1) — the banner is not the only
consumer, and the other three (A's status-pill precedence, A's `needs_review()` queryset, and the brief's
*Needs review* chip **in search results**) read the columns, not the rows.

```
┌ ▌ Needs review                                                            ┐
│   "The blade guard step is backwards. You lower it AFTER the fence."      │
│   Reported by Dana Kim, 2 hours ago       [ Mark Reviewed ]  [ Review ]   │
└──────────────────────────────────────────────────────────────────────────┘
```

* Classes `pl-wp-mod__banner pl-wp-mod__banner--review`, mirroring `.pl-equip-banner--warn`'s shape: 4px left
  rule in `var(--color-tuscan-yellow)`, `background: var(--hub-surface)`, `padding: 1rem 1.25rem`,
  `margin-bottom: 1.25rem`. (`--hub-warn` is **not** a token in this repo — zero occurrences in `static/css/` —
  so nothing here reaches for it; brief §9.2.)
* Heading "Needs review" in `--hub-text` at 600 weight (**not** gold text — gold on `--hub-surface` fails
  contrast on the light theme; the rule carries the colour, the text carries the meaning).
* The quote is `<blockquote class="pl-wp-mod__quote">`, escaped plain text (D14), clamped to 3 lines with
  `-webkit-line-clamp` and a "Show more" `x-show` toggle past that.
* Byline in `--hub-text-muted`: `Reported by {{ report.reporter.display_name|default:"a member" }},
  {{ report.created_at|timesince }} ago`. With more than one open report, appends
  `and {{ others }} more`, linking to the queue.
* **[ Mark Reviewed ]** (`pl-btn pl-btn--sm pl-btn--primary`), rendered **only** for
  `can_moderate_wiki_page` — **this is D17, and it is the fix for the round's worst half-built loop.** The path a
  lead actually walks is *see the banner → click Edit → fix the sentence → save*, and nothing on that path
  resolved anything, so the page went on quoting a complaint about a sentence that no longer existed until
  somebody separately remembered to visit `/wiki/review/`. The button opens **the same modal as §6.5**
  (`$dispatch('open-modal', 'resolve-<pk>')`, one modal template, one view, one resolution field), posts by HTMX,
  and on success **OOB-swaps `#wiki-review-banner` to empty** — or to the next-oldest open report's banner if
  there are more — plus `trigger_toast(…, "Marked reviewed.")` then
  `trigger_client_event(response, "close-modal", "resolve-<pk>")`, in that order (§6.2).
* **[ Review ]** (`pl-btn pl-btn--sm pl-btn--ghost`) → `/wiki/review/#report-<pk>`, also moderators only, for the
  case where the lead wants the queue's context rather than to act now.
* **A plain member sees the banner with neither button**, which is correct: the banner is information for readers
  ("someone has flagged this, read with care"), not an assignment.
* **The second half of D17, for the lead who fixes it in the editor anyway.** A staff save (`apply_edit` by
  someone for whom `can_moderate_wiki_page` is true) on a page that still has open reports returns a toast
  carrying the action rather than a bare success: *"Saved. This page still has a report open. **Mark it
  reviewed?**"* — the bolded words are a link to the page anchored at `#wiki-review-banner`, where the button now
  is. Deliberately a prompt and not an automatic resolve: a staff edit is often *unrelated* to the report, and
  silently closing someone's report because a typo got fixed is exactly the "nobody looked at this" failure the
  queue exists to prevent.
* **The page below is untouched.** No blur, no collapse, no interstitial. This is the design's whole thesis.
* **Dark + light:** both use `--hub-surface` + `--hub-text` + the gold rule; the gold token is identical in both
  themes per the design-system table, so only the surface changes.
* **Mobile:** stacks — heading, quote, byline, then Mark Reviewed and Review full-width at 48px underneath, in
  that order.

### 6.4 The review queue — `templates/hub/wiki_review.html` at `/wiki/review/`

* **Gate:** `moderatable_wiki_scopes(request)` returning `([], False)` → 403 (an ordinary member has no queue).
* **Header:** `components/page_header.html` with
  `title="Wiki Review"`,
  `description="Pages members have flagged, and safety pages waiting on a second read. Oldest first."`
* **Two sections, as `hub-card`s** with Title Case headings (Rule 22): **"Reported Pages"** and
  **"Safety Pages Awaiting Review"**. The second renders only when the viewer has at least one in scope — an
  empty second card would be noise on a screen whose job is "is there anything for me".
* **A third view for archived pages, at `?archived=1`** — see the block after the states.
* **Card list, not a table.** A report is a short paragraph plus two names, not tabular data; cards reflow to
  390px for free where a five-column table would need a stacking rule. Each `_wiki_review_report_card.html`:

  | Row | Content |
  |---|---|
  | 1 | `<a>` the page **Title** (to `/wiki/p/<slug>/`) · a quiet neutral scope chip — "Woodworking" or "Space-wide" (**attribute chips are quiet neutral text, never coloured pills** — brief §3's "no pill salad") |
  | 2 | the reason, quoted, escaped, 3-line clamp |
  | 3 | `Reported by Dana Kim · 2 days ago` in `--hub-text-muted` |
  | 4 | actions: **Open the Page** (`pl-btn pl-btn--sm`) · **Mark Reviewed** (`pl-btn pl-btn--sm pl-btn--ghost`) |

* **Ordering:** `WikiReport.Meta.ordering = ["created_at"]` — oldest first, so the thing rotting longest is at
  the top and the list does not reshuffle under a reviewer working down it.
* **Scoping, visibly:** a guild lead's page says *"Showing reports for Woodworking and Metals."* under the
  section heading; an admin's says *"Showing every scope."* A queue that silently filters is a queue people stop
  trusting.
* **Pagination: reports only, 25 per page.** `components/table_pagination.html` with `page` and `base_params`;
  it renders the `.admin-table-footer` shell below a card list perfectly well. **Only one list on this screen is
  paginated**, because the component hard-codes `?page=` in every link it builds, so two paginators on one screen
  would drive each other: clicking page 2 of the reports would also jump the safety list to page 2 (or to an
  empty page, since the two lists are different lengths). Reports are the list that can actually grow; safety
  proposals are a handful at a time by construction (§10 has the escape hatch if that stops being true — give
  the second list its own `?safety_page=` param and a copy of the component that reads it, which is a fork of a
  shared component and therefore not worth doing on speculation).
* **States.**
  * *Empty (both sections):* the whole page renders one centred `pl-wp-mod__empty` block — **"Nothing to review.
    You are all caught up."** and, for a lead, a second muted line naming their scopes: *"Reports from Woodworking
    and Metals will land here."* Never a blank region (brief §3).
  * *Empty (reports only, safety pending):* the Reported Pages card carries the same written empty line inside
    it; the safety card renders normally.
  * *Loading:* full-page navigation (hx-boosted); the global loading bar covers it. Row actions are HTMX with
    `hx-disabled-elt="this"`.
  * *Error:* a resolve on an already-resolved report (stale tab) returns the row partial plus an info toast,
    "Someone already reviewed this one." — never a 500.
  * *Success:* the card is swapped out (`hx-swap="outerHTML"` on `#report-<pk>`) with a fade, plus
    `trigger_toast(…, "Marked reviewed.")`. When the last card in a section goes, the swap returns the written
    empty state rather than an empty card.
* **Dark + light:** `hub-card` + `--hub-text-muted` + `--hub-border` only; the scope chip is
  `color: var(--hub-text-muted); border: 1px solid var(--hub-border)` in both.
* **Mobile 390px:** cards are already single-column; the two action buttons go full-width and stack at 48px each
  under 480px. No hover-only affordance anywhere on this screen — the actions are always-visible buttons,
  because **a moderation action a lead can only reach on a laptop will not happen.**

**Finding an archived page — `/wiki/review/?archived=1` (D19).** Restore lives on the tombstone, and the
tombstone is reachable only by someone who already knows the slug — so an over-archive, the exact failure the
brief names as ending a contributor's participation, was in practice unfixable by anyone who had not bookmarked
the URL. The queue is where a moderator already goes, so the list goes there.

* A third tab-style control in the header row beside the two sections: **Open** (default) and **Archived**, as
  the `.vote-tab` idiom the admin shell already uses, deep-linked by `?archived=1` so a link to it is shareable.
* The list is `WikiPage.objects.all().filter(archived_at__isnull=False)` narrowed to
  `moderatable_wiki_scopes(request)` — the same scoping as the reports leg, so a Woodworking lead sees
  Woodworking's archived pages and not Metals'. Ordered `-archived_at`, newest removal first, and paginated with
  the same component and the same `?page=` param (the two tabs are separate screens, so they never share one).
* Each `_wiki_review_archived_card.html`: the page **Title** linking to its still-working URL · the quiet neutral
  scope chip · `Removed by Kate Mizuno · 3 September 2026` · the reason quoted and clamped to two lines · one
  action, **Open the Page** (`pl-btn pl-btn--sm`), because Restore belongs on the tombstone where the reader can
  see what they are restoring. A one-click Restore from a list is how you restore the wrong page.
* **Written empty state:** *"Nothing has been archived in your scopes. Pages you remove show up here so you can
  find them again."* — and for an admin, *"Nothing has been archived."* Never a blank region.
* **Mobile:** the two tabs are 48px tap targets side by side; cards are single-column as above.

### 6.5 Marking a report reviewed

* **Trigger:** the **Mark Reviewed** button — on the queue card, and on the amber banner itself (D17, §6.3).
  Both dispatch `@click="$dispatch('open-modal', 'resolve-<pk>')"` at the same modal, so there is one template,
  one form and one view for both entry points; only the success swap differs (a card row in the queue, the
  banner container on the page).
* **Container:** `components/modal.html`, `modal_title="Mark This Report Reviewed"`, one field, so modal + toast.
  Not a confirm modal — resolving is not destructive, and FRONTEND.md reserves `confirm_modal.html` for actions
  with a consequence to name.
* **Body:** the quoted reason again (so the reviewer confirms which one), then
  `form_field.html` with `field.resolution` — a `TextInput`, `field_label="What you did (optional)"`,
  `field_hint="Recorded for the audit trail. The reporter is not emailed."`
* **Controls:** **Mark Reviewed** (`pl-btn pl-btn--primary`, `hx-post`) and **Cancel**.
* **States:** loading via `hx-disabled-elt`; error → the stale-tab info toast above; success → card swap + toast.

### 6.6 The official note

**Rendered — `templates/hub/partials/_wiki_official_note.html`.** Sits in the page composition immediately below the
title/status line and **above** all member content, one slot below the equipment Official block so the two never
argue about which comes first.

```
┌ ▌ [Official]                                                              ┐
│   The 12in blade is out of service until the arbor is replaced.           │
│   Do not fit a replacement blade.                                         │
│   Added by Kate Mizuno, Woodworking lead, 3 Sep                           │
└──────────────────────────────────────────────────────────────────────────┘
```

* `pl-wp-mod__note` deliberately reuses the visual language the brief already specifies for the equipment Official
  block (§5.4): tinted `--hub-surface` background, 4px left rule in `var(--hub-blue)`, an **Official** chip, no
  edit affordance for members — *not a disabled button, no button*. One visual grammar for "locked staff
  content" on a page, learned once.
* Note body renders through the existing `render_markdown(..., profile="wiki")` (A's new profile) so a link or
  a bold word works, with no new renderer.
* Staff see one extra control beside the chip: **Edit Note** (`pl-btn pl-btn--sm pl-btn--ghost`).

**The editor — `templates/hub/partials/_wiki_official_note_form.html` in a modal.** One field → modal + toast.

* `components/modal.html`, `modal_id="wiki-official-note"`, `modal_title="Official Note"`, `modal_size="md"`.
  The button `hx-get`s the form body into `#wiki-official-note-body` before opening (the documented HTMX +
  modal pattern).
* **Layout obeys Rule 21 exactly.** When a note already exists, the **immediate-effect control comes first** and
  the save form comes last, so the Save button is the final element with nothing beneath it:
  1. a **Remove Note** button (`pl-btn pl-btn--danger pl-btn--sm`, `margin-bottom:1rem`) that opens
     `confirm_modal.html` (`confirm_id="wiki-note-remove"`,
     `confirm_title="Remove this official note?"`,
     `confirm_message="Members will stop seeing it immediately. The page's own text is not changed."`,
     `confirm_button_text="Remove Note"`);
  2. a divider;
  3. the form: `form_field.html` with the note `Textarea` (5 rows, `.hub-form-group` scope), a hint —
     *"Members cannot edit or remove this. Keep it to what is true right now."* — and **Save** last,
     labelled exactly **"Save"**.
* **States:** empty (no note yet → no Remove button, placeholder text in the textarea, Save disabled until
  something is typed via a tiny `x-model` length check); loading (`hx-disabled-elt`); error (over 1000 chars →
  inline field error, modal stays open, text preserved); success (modal closes, the rendered note region
  OOB-swaps in, toast "Official note saved.").
* **Dark + light:** textarea in `.hub-form-group`; the rendered note uses `--hub-surface` / `--hub-blue` /
  `--hub-text` in both.
* **Mobile:** modal full width; the note renders at the same 17px body size as the rest of the page.

### 6.7 Archive, the tombstone, and Restore

**The three graduated actions in the page header — `templates/hub/partials/_wiki_moderation_actions.html`.**
Rendered only when `can_moderate_wiki_page`, in escalation order left to right, so the cheapest fix is the
closest to hand:

| Order | Control | Class | Why it is first / last |
|---|---|---|---|
| 1 | **Edit** | `pl-btn pl-btn--sm` | Most cases end here. It is the same Edit spec A ships; for staff it is simply always present. |
| 2 | **Add an Official Note** (or **Edit Note**) | `pl-btn pl-btn--sm pl-btn--ghost` | The middle rung: the sentence stays, the correction is stated with authority. |
| 3 | **Archive** | `pl-btn pl-btn--danger pl-btn--sm` | Last, red, and behind a typed confirmation. |

Below them, quiet text links: **History** and (when archived) **Restore**.

**On phones there is exactly one overflow menu, and it is A's.** A's sticky bottom bar already ends in a "⋯"
holding **Edit** and **Still accurate**; D's staff actions are **appended into that same menu**, not given a
second kebab in the page header. Two overflow menus on a 390px screen, both containing Edit, is the kind of thing
that reads as a bug even when both work. Concretely: A's bar keeps its three primary slots (Add Photo / Add Tip /
**Report**), and its single "⋯" renders `components/row_actions.html` with
`menu_include="hub/partials/_wiki_moderation_menu.html"` and
`menu_label="More actions for this page"`, whose items are Edit · Still accurate · **Official Note** ·
**History** · **Archive** · (when archived) **Restore** — the last three only when `can_moderate_wiki_page`. The
component is already built, already keyboard-accessible, and already dismisses on scroll. **D's header row
(`pl-wp-mod__actions`) is desktop-only**, hidden by the same breakpoint that shows A's bar.

**The Archive confirmation.** `confirm_modal.html`, plain-POST mode, with both opt-in modes turned on:

```django
{% include "components/confirm_modal.html" with
   confirm_id="wiki-archive"
   confirm_title="Archive this page?"
   confirm_message=archive_confirm_message
   confirm_action_url=archive_url
   confirm_note_name="reason"
   confirm_note_required=1
   confirm_note_label="Why is this page being removed?"
   confirm_note_hint="One sentence. The author reads this. Be specific and be kind."
   confirm_typed_value="ARCHIVE"
   confirm_typed_placeholder="Type ARCHIVE to confirm"
   confirm_button_text="Archive Page" %}
```

The typed confirmation is warranted here and nowhere else in this spec: archive is the only action that takes a
page away from every member at once, and it is the action the brief names as the one that ends a contributor's
participation when it goes wrong.

**`archive_confirm_message` is computed by the view, because a page may have no author (D20).** Every seeded
Equipment stub is created by the seeder with `created_by=None`, and the FK is `SET_NULL`, so a member's account
being deleted produces the same state. The old copy hard-coded *"Rowan Ellis wrote this page and will be emailed
your reason, by name"* and §7.3 called the notice unconditional — on a stub, that sentence is a lie told at the
moment a moderator is about to do the most consequential thing in the spec.

| Case | Message |
|---|---|
| `page.created_by` is set | "The link keeps working, and anyone who opens it sees when it was removed and why. Members stop finding it in search. **Rowan Ellis** wrote this page and will be emailed your reason, by name." |
| `page.created_by` is null | "The link keeps working, and anyone who opens it sees when it was removed and why. Members stop finding it in search. **Nobody is listed as the author of this page, so no one will be emailed** — the reason still shows on the page." |

In the null case `archive()` skips the send entirely (no `core.email.send` call, no
`TransactionalEmailLog` row) and the activity payload records `"author": ""`. §7.3 states what happens to the
page's *other* revision authors, which is the harder half of the same question.

**`confirm_note_required` is a new flag on `confirm_modal.html`, added by this spec (D22).** Today Confirm is
gated on the typed value only (`:disabled="typed !== '…'"`), so a moderator who types ARCHIVE and forgets the
reason gets a full-page POST, a rejection, and has to retype ARCHIVE from scratch. The flag makes the disabled
expression `typed !== '…' || note.trim() === ''` — two tokens in a component D is already touching, opt-in, and
backward-compatible with all three existing callers, none of which pass it. Server-side validation stays exactly
as it is: the flag is a courtesy, not the gate.

The redirect target is deliberately **not** in this modal. Two reasons: `confirm_modal.html`'s note mode carries
exactly one field (its rich-body include renders outside the `<form>`, so extra inputs there would not post), and
more importantly you usually do not know the right replacement page at the instant you archive. So the redirect
is set afterwards, on the tombstone itself (below).

* *Error:* a blank reason still POSTs if JS is off. The view rejects it, does not archive, and returns to the
  page with an error toast: *"Add a reason. The author will read it."*
* *Success:* redirect to the page, Django message *"Page archived. Rowan Ellis has been emailed."* — or, with no
  author, *"Page archived."*

**The tombstone — `templates/hub/partials/_wiki_archived_banner.html`.** The URL keeps working; A's reading view
resolves through `WikiPage.objects.all()` (no `all_objects` — D2).

```
┌ ▌ Removed by Kate Mizuno on 3 September 2026                             ┐
│   Reason: The information here was replaced by the Bandsaw Safety page.   │
│   → Go to Bandsaw Safety                                                  │
└──────────────────────────────────────────────────────────────────────────┘
```

* `pl-wp-mod__banner pl-wp-mod__banner--archived`: left rule in `var(--hub-border-strong)`, not red. A tombstone
  should be calm; red says "danger" when the message is "moved on".
* **Line one names the person, not just the date.** The brief requires that the author learn why *and from whom*;
  a tombstone that says only "This page was removed on 3 September" makes the removal look like weather. When
  `archived_by` is null (the archiver's account was later deleted), it degrades to *"Removed on 3 September
  2026"* rather than inventing an actor — the same rule the brief states for restore clearing attribution.
* Date rendered in the project timezone (`TIME_ZONE = "America/Los_Angeles"` in `plfog/settings.py`), long form.
* The redirect link, when set, is the one prominent control.
* **A member sees the banner and nothing else** — no body, no attachments, no history link. **Staff see the
  banner and then the full page below it**, because the only way to fix an over-archive is to read what was
  archived.
* Staff also get, inside the banner: **Restore** (`pl-btn pl-btn--sm`) and a one-field inline form
  *"Point readers at another page"* — a `<select>` of live pages in the same scope inside `.hub-form-group`
  (with `select option { background; color }` styled per Rule 13, since native option popups do not inherit) and
  a **Save** button. Setting a redirect on an already-archived page is the common case and it must not require
  un-archiving first.
* **Restore** goes through `confirm_modal.html` with `confirm_button_style="primary"` (restoring is not
  destructive) and the message *"The page becomes visible to every member again, and this removal note
  disappears."*
* **States:** archived-with-redirect / archived-without-redirect (no link row) / just-restored (toast *"Page
  restored."*, banner gone, full page renders).
* **Dark + light:** `--hub-surface` + `--hub-border-strong` + `--hub-text-muted` in both.
* **Mobile:** the banner is the whole screen for a member; Restore and the redirect select stack full-width.

### 6.8 History and Revert — `templates/hub/wiki_history.html`

* **Route:** `/wiki/p/<slug>/history/`. Readable by any member (seeing that a page has been worked on is part of
  trusting it); **Revert is staff-only**.
* **Header:** `page_header.html`, `title="History"`,
  `description="Every version of this page is saved. Nothing here can be lost."` — the same sentence the brief
  puts in the editor footer, in the place a nervous contributor goes looking for proof.
* **The list** (`_wiki_history_row.html`), newest first, one row each:

  | Column | Content |
  |---|---|
  | When | `3 Sep 2026, 2:14 PM` (project timezone) + `2 days ago` muted |
  | Who | `Member.display_name`, or "a former member" when the author FK is null |
  | What | a quiet chip: **Before this edit** / **Before this revert** / **Unmerged draft** (from `WikiRevision.kind`) |
  | | **Revert to This** (`pl-btn pl-btn--sm pl-btn--ghost`), staff only |

* **What a row means, said plainly.** A's `apply_edit()` stores the **pre-edit** snapshot (§5.5), so a row is
  *what the page looked like before that person's save*, not what that person wrote. The old chip labels ("Edit",
  "Reverted") quietly claimed the opposite, and a moderator reverting on that reading restores one version
  further back than they intend — the single most expensive misreading available on this screen. Hence the
  labels above, and one muted line under the header: *"Each row is what the page looked like just before that
  change."* The `CONFLICT_DRAFT` row is the one exception and reads **Unmerged draft** — it is a post-edit
  snapshot of text that was submitted and never applied.
* **Pagination: `components/table_pagination.html`, 50 rows per page**, ordered by A's
  `WikiRevision.Meta.ordering = ["-created_at", "-pk"]`. A page that gets a quick tip and a photo from every
  member who touches it accumulates revisions faster than anything else in the round, and an unbounded list is a
  slow query and a mile of scroll on a phone. It is the only list on this screen, so it owns `?page=` outright.
* **No diff viewer, deliberately.** The brief rejects word-level HTML diffs outright: 253 lines of vanilla JS in
  the reference app for a need members do not have. Staff need *revert*, not a diff — the failure they are fixing
  is "someone broke this", and the fix is "put back the version that worked", not "study 40 highlighted words".
  A read-only view of a single old revision is offered instead (clicking the timestamp opens
  `?revision=<pk>` rendering that snapshot with a "You are looking at an old version" bar), which answers
  "what did it used to say" at a hundredth of the cost. Recorded as scoped-out in §10.
  **`?revision=<pk>` works for a `CONFLICT_DRAFT` row too**, which is what makes an unmerged draft more than an
  inert line in a list: its bar reads *"This version was written by Dana Kim and never applied — someone else
  saved first."* and, for anyone who could edit the page, carries one action, **Use This Version**, posting to
  the same view "Keep My Version" uses on the conflict screen (§6.10) with the same confirm copy. Without it a
  draft row is a dead end, and the conflict screen's reassurance that "your version stays in this page's
  history" is technically true and practically useless — it stays there and can never be reached.
* **Revert goes through `confirm_modal.html`** — never a bare button:
  `confirm_title="Revert to this version?"`,
  `confirm_message="The page goes back to Rowan Ellis's version from 3 September. Nothing is deleted: this creates a new version on top, and every later version stays in the history."`,
  `confirm_button_text="Revert"`. Plain POST to `hub_wiki_revert`.
* **States.**
  * *Empty:* impossible for a live page (the first save writes revision one), but the template still carries
    *"No history yet."* rather than an empty `<tbody>` — an archived-and-restored edge case should not render a
    blank box.
  * *Loading:* page navigation, global bar.
  * *Error — reverting to the current head:* `NothingToRevert` → error toast *"That is already the current
    version."*, no write. The head row's Revert button is not rendered in the first place; this catches a stale
    tab.
  * *Success:* redirect to the page with the Django message *"Reverted to the 3 September version."*
* **Dark + light:** `admin-table` inside `admin-table-wrap` (existing, theme-correct).
* **Mobile 390px:** below 640px the table stacks into `pl-wp-mod__history-card` blocks — When on line one, Who
  and the chip on line two, the Revert button full-width at 48px on line three. No horizontal scroll.

### 6.9 The advisory-lock warning — `templates/hub/partials/_wiki_lock_warning.html`

Rendered at the **top** of the editor, before the title field, when `WikiEditLock.claim()` returned a live
holder:

```
┌ ▌ Dana Kim started editing this 3 minutes ago                            ┐
│   You can still edit. If you both save, nothing is lost: we keep both     │
│   versions and show you them side by side.                                │
└──────────────────────────────────────────────────────────────────────────┘
```

* `pl-wp-mod__banner pl-wp-mod__banner--lock`, left rule in `var(--hub-blue)` — informational, not a warning. It
  is not a gate and must not read as one.
* Relative time from `started_at`, not `refreshed_at` ("started editing" is the honest verb) — and `started_at`
  is reset on every re-claim (§4.2, §5.5), so the sentence is true about the person it names rather than about
  whoever last held the row.
* It does not live-update and there is no polling: a static line at open is what changes the reader's behavior,
  and a countdown would just add JS to a page that already has a rich editor on it.
* **States:** absent (no live holder, the overwhelmingly common case) / present / present-and-mine (never — a
  holder is never warned about themselves).
* **Mobile:** full width above the title field, no action, nothing to tap.

### 6.10 The conflict-save screen — `templates/hub/wiki_conflict.html`

Reached only by redirect from a conflicting save, at `/wiki/p/<slug>/conflict/<draft_pk>/`. Gated to the draft's
own author plus moderators.

* **Header:** `page_header.html`,
  `title="Two People Edited This Page"`,
  `description="Your text is saved. Nothing you wrote has been lost. Pick what should be on the page."`
  That first sentence is the entire job of this screen and it is the first thing read.
* **Body:** two `hub-card`s side by side above 900px, stacked below:

  | | Left card | Right card |
  |---|---|---|
  | Heading | **Your Version** | **The Page Right Now** |
  | Sub | `Saved just now` | `Saved by Dana Kim, 4 minutes ago` |
  | Body | the draft snapshot (`draft.body`), rendered read-only through the wiki renderer, in a `max-height: 420px; overflow-y: auto` region | **`page.body` — the live page**, same treatment |

  **The right card renders `page.body`, never `page.revisions.first()`.** A stores the *pre-edit* snapshot
  (§5.5), so the newest revision row is what the page looked like *before* Dana's save — the exact version this
  screen exists to reconcile *away from*. Showing it would put the wrong text under the heading "The Page Right
  Now" and quietly invite the member to re-apply a change that was already superseded. The sub-line's name and
  time come from the revision row (`theirs.author`, `theirs.created_at`) because that is the honest record of
  *who* saved last; only the body comes from the page.

* **Three actions, in a footer row below both cards** (not inside either, so neither reads as pre-selected):

  | Button | Class | Does |
  |---|---|---|
  | **Keep My Version** | `pl-btn pl-btn--primary` | Calls A's `apply_edit()` with the draft's `title` / `body` / `facts` and `note="Resolved an edit conflict"` (§5.5) — an ordinary save whose text came from a stored row. Behind `confirm_modal.html`: *"Dana Kim's version is replaced. It stays in the history and can be reverted to."* |
  | **Open the Editor With Both** | `pl-btn` | Opens the editor on the **current page body** with the draft's text appended below a `--- your version ---` rule, so a human merges the two in the one tool that can. This is the reconcile path and the one most people take. |
  | **Keep Their Version** | `pl-btn pl-btn--secondary` | Navigates back to the page. **Writes nothing and deletes nothing** — the `CONFLICT_DRAFT` row is permanent, and a footnote under the button says so, with a link that makes it true: *"Your version stays in this page's history, and you can [open it](/wiki/p/<slug>/history/?revision=<pk>) any time."* Without that link the sentence is a promise the UI does not keep, which is why `?revision=` accepts draft pks (§6.8). |

  There is no "Save" here and Rule 21 does not apply: this screen holds no fields, only three exits. The primary
  is first because a conflict is most often a stale tab, not a genuine disagreement.
* **States:** normal / draft already applied (someone acted in another tab → the page renders a single line,
  *"This has already been sorted out."*, with a link to the page) / draft belongs to someone else and the viewer
  is not a moderator → 403.
* **Dark + light:** two `hub-card`s and existing button classes; the `--- your version ---` marker in the editor
  is plain text, not styled markup.
* **Mobile 390px:** cards stack, "Your Version" first; each body region gets `max-height: 40vh`; the three
  buttons stack full-width at 48px in the same order.

### 6.11 Safety proposals

* **At creation:** the starter chooser (spec A) gains a **Safety & Rules** card with the sub-line *"Rules,
  hazards, required gear. A guild lead reads these before they go live."* — expectations set before a word is
  typed, not after Publish.
* **On Publish, for a non-moderator:** a full-page confirmation, not a toast (it is a state change they did not
  expect): *"Thanks. Safety pages get a second read before they go live. The Woodworking leads have it, and you
  will hear back."* with **View My Draft** (→ `/wiki/drafts/`) and **Back to the Wiki**.
* **In the queue (`_wiki_review_safety_card.html`):** page title, scope chip, `Proposed by Rowan Ellis, 2 days
  ago`, the first ~200 characters of the body, and three controls — **Read the Full Draft** (opens the page in
  draft preview), **Publish**, **Decline**.
  * **Publish** goes through `confirm_modal.html` with authority-aware copy so the outcome is never a surprise:
    a lead sees *"Publishing as the Woodworking lead marks this Guild Verified. An officer can make it
    Official."*; an admin sees *"This publishes as an Official page. Members will not be able to edit it."*
  * **Decline is its own modal, not `confirm_modal.html` in note mode.** `components/modal.html` with
    `modal_id="wiki-decline-<pk>"`, `modal_title="Send This Back"`, one `form_field.html` bound to
    `WikiDeclineForm.note` — a **`Textarea`, 4 rows**, `field_label="What should change?"`,
    `field_hint="The author gets this by email. Say what would make it publishable."` — and **Send Back**
    (`pl-btn pl-btn--primary`) last (Rule 21). The confirm modal's note mode is a single-line `TextInput`, which
    is fine for "why is this page being removed" and thin for the one message in this round whose entire job is
    to explain what a member should do next. Required, minimum 10 characters, same floor as a report reason.
  * **The note reaches the author** through the channel named in §5.6 / D21: a `WikiRevision` on the page's own
    history (`note="Sent back: …"`) plus a `core.email.send` transactional note, and the author can edit their
    own unpublished proposal to act on it. The page stays a draft. Nothing is deleted, ever.
* **Empty state:** the card is not rendered at all when the viewer has none in scope (§6.4).

### 6.12 CSS inventory, themes, and mobile

One block appended to `static/css/hub.css`, all classes `pl-wp-mod__*` (namespace rationale in the §6 preamble):

| Class | Purpose |
|---|---|
| `.pl-wp-mod__banner` | shared banner shell: `padding:1rem 1.25rem; margin-bottom:1.25rem; border-left:4px solid var(--hub-border); background:var(--hub-surface); border-radius:6px` |
| `.pl-wp-mod__banner--review` / `--archived` / `--lock` | left-rule colour only: `--color-tuscan-yellow` / `--hub-border-strong` / `--hub-blue` |
| `.pl-wp-mod__quote` | 3-line clamp, `border-left:2px solid var(--hub-border)`, `padding-left:0.75rem`, italic |
| `.pl-wp-mod__note` | official note: `background:var(--hub-surface); border-left:4px solid var(--hub-blue); padding:1rem 1.25rem` |
| `.pl-wp-mod__note-chip` | the Official chip — the page's **one** coloured pill |
| `.pl-wp-mod__chip` | quiet neutral attribute chip (scope, revision kind): `--hub-text-muted` text, `--hub-border` outline, **no fill** |
| `.pl-wp-mod__review-card` | queue card: `hub-card` padding plus a `border-bottom` between cards |
| `.pl-wp-mod__empty` | written empty state: centred, `2.5rem 1rem`, `--hub-text-muted`, `max-width:44ch` |
| `.pl-wp-mod__conflict-grid` | `display:grid; grid-template-columns:1fr 1fr; gap:1.25rem` → one column under 900px |
| `.pl-wp-mod__conflict-body` | `max-height:420px; overflow-y:auto` (40vh under 640px) |
| `.pl-wp-mod__history-card` | the ≤640px stacked history row |
| `.pl-wp-mod__actions` | the desktop header action row: `display:flex; gap:0.5rem; flex-wrap:wrap` |
| `.pl-wp-mod__report-btn` / `.pl-wp-mod__reported` | the Report control and its already-reported state (§6.1) |

Rules honoured explicitly: **no hardcoded colours** (every value above is a token; `--surface` is not a token
and is not used, and neither is `--hub-warn`, which does not exist in this repo — brief §9.2); **`display` never
in an inline style on an `x-show` element** — the conflict grid, the "Show more" quote expansion, and the modal
bodies all get their display from a class (Rule 12); **buttons get ≥1.5rem clearance from the next section**
(Rule 18) via `.pl-wp-mod__actions { margin-bottom: 1.5rem }`; **8px grid** throughout; **no multi-line `{# #}`
comments** — every explanatory comment in these templates is `{% comment %}…{% endcomment %}` (Rule 17, and
`tests/template_comment_lint_spec.py` runs after every template change).

### 6.13 The `confirm_modal.html` fixes (D22)

Two small, additive, backward-compatible changes to a component this spec already uses on five screens.

**1. `.pl-input` is defined in no CSS file in this repo.** Verified 2026-09-07: `grep -rn "pl-input"
static/css/` returns nothing, and the only two occurrences anywhere are `components/confirm_modal.html:77` (the
note input) and `:87` (the typed-confirmation input). Both sit outside any `.hub-form-group` scope, so they get
no `--hub-input-bg`, no `--hub-input-border`, no focus ring, and no `color` — a browser-default white box with
near-black text, which on the Obsidian theme reads as an unlabelled white slab and, in the light theme, as an
input with no border. **This is live in shipped code today** for all three existing callers
(`equipment_manage.html`, `user_settings.html`, `classes/partials/roster_modals.html`), and D's Archive screen —
the single most consequential action in this round, the one whose confirmation exists to slow a moderator
down — is the round's only typed confirmation. So D fixes it.

**The fix, and why this one:** add a `.pl-input` rule to `static/css/components.css`, immediately beside the
existing `.pl-form-label` and `.pl-field-hint` rules the same markup already uses, mirroring
`.hub-form-group input[type="text"]` in `hub.css` — `width:100%`, the `--hub-input-bg` / `--hub-input-border`
pair, `color: var(--hub-text)`, `border-radius:6px`, the `0.625rem 0.875rem` padding, and the same
`--color-tuscan-yellow` focus ring. Both themes come for free, because both tokens are already theme-aware.

The alternative — wrapping each input in `<div class="hub-form-group">` — also works and is what Rule 13
prescribes for new markup, but it drags `.hub-form-group label` (specificity 0,1,1) over the existing
`.pl-form-label` (0,1,0) and silently restyles the labels of three shipped screens. Defining the class the markup
already claims changes exactly the thing that is broken and nothing else. Either way this is one line of work and
three callers are fixed for free; the spec picks the CSS rule and says so rather than leaving it to the builder.

**2. `confirm_note_required`** — a new opt-in flag (D22). When set, the Confirm button's disabled expression
becomes `typed !== '<value>' || note.trim() === ''` instead of the typed check alone, and the note label drops
its `(optional)` default. Two tokens in the template, no change to any caller that does not pass it, and no
change to server-side validation, which stays the real gate. Without it, the Archive flow's only failure mode is
also its most annoying one: type the reason, forget nothing, click — versus forget the reason, click, eat a
full-page POST and retype ARCHIVE.

**Both changes are tested against all three existing callers**, not only against D's screens (§9).

**Mobile at 390px, across every screen above:** every tappable control is ≥48px tall; **nothing is
hover-dependent, swipe-revealed, or long-pressed** — the kebab in §6.7 is a tap-to-open `<button>` with a
`role="menu"` list of tap targets; tables stack into cards rather than scrolling sideways; the page body never
scrolls horizontally; body copy stays ≥17px with 1.6 line-height per brief §5.6.

## 7. Notifications / emails / activity

### 7.1 `wiki.page_reported`

**`core/triggers.py`** — appended to `TRIGGERS`. Note the comment inside the `Trigger` dataclass, just under its
`audience` field: several existing calls pass `audience` positionally at index 4, so **every flag below is passed
by keyword**, never positionally.

```python
Trigger(
    "wiki.page_reported",
    "Wiki page reported",
    "A member flagged a problem on a wiki page in a guild you lead.",
    "Wiki",
    audience=Audience.STAFF_ONLY,
    email_default=True,
),
```

`email_default=True` because this is the one moderation signal, it is low volume (a handful a month at 200
members), and a lead who only sees it on the bell will see it next Thursday. `push_default` stays False (the key
is not added to `_PUSH_ON_BY_DEFAULT`) — the Push toggle is still offered by `_with_push`, defaulted off. Add
`"Wiki"` to `triggers.CATEGORY_ORDER` (after `"Guilds"`) so `by_category` does not silently drop the row.

**`core/events/registry.py`:**

* `Recipients.WIKI_SCOPE_LEADERSHIP = "wiki_scope_leadership"`.
* `_TRIGGER_RESOLVERS["wiki.page_reported"] = Recipients.WIKI_SCOPE_LEADERSHIP`.
* `_TRIGGER_ACTIVITY_KINDS["wiki.page_reported"] = None`, with the comment stating why: `WikiReport.file()`
  writes the row itself so the payload carries the reason and the reporter, exactly as `tab_entry_added` does.
* No `_NEW_EVENTS` entry is needed — `_seed_from_triggers` builds the `EventType` from the trigger, and
  `_channels_from_trigger` yields IN_APP on, EMAIL on, PUSH off, DISCORD_DM off. **No `DISCORD` broadcast
  channel** (D15).

**`core/events/resolvers.py`** — composition, modelled line for line on `guild_leadership_or_class_approvers`:

```python
def wiki_scope_leadership(context):
    """COMPOSITION — the page's guild leadership, else (or if that guild has none) admins.

    A guild-scoped report reaches the people who know the machine; a space-wide page, or a
    dormant guild with no lead and no staff rows, escalates to the FOG admins rather than
    landing nowhere. Deliberately NOT the guild_leadership_or_admins union: a Woodworking
    typo must not email every admin, or the event trains them to ignore it.
    """
    guild = _require(context, "guild")          # fail loudly on a missing key
    if guild is not None:
        recipients = guild_leadership(context)
        if recipients:
            return recipients
    return fog_admins(context)
```

Registered in `_RESOLVERS`, and added to `settings_matrix.STAFF_RECIPIENTS` so the row groups under
**"Staff & leadership"** and never appears on a plain member's settings page.

**The emit call**, from `WikiReport.file()`:

```python
emit(
    "wiki.page_reported",
    actor=reporter.user,
    target=page,
    context={
        "guild": page.guild,                      # None for a space-wide page
        "member_name": "there",
        "page_title": page.title,
        "page_url": hub_url("hub_wiki_page", page.slug),
        "reason": report.reason,
        "reporter_name": reporter.display_name,
        "scope_label": page.guild.name if page.guild_id else "Space-wide",
        "review_url": hub_url("hub_wiki_review"),
    },
    period=f"wiki_report:{report.pk}",            # unique per report, so every report delivers
)
```

**Copy** (`core/events/copy.py` `_CURATED`), placeholders
`("page_title", "page_url", "reason", "reporter_name", "scope_label", "review_url")`, with a sample context for
the admin preview. Email subject: `Someone flagged "{{ page_title }}" on the wiki`. Body, per FRONTEND.md's
*Email Templates* rules:

* the **subject noun is a link** — `{{ page_title }}` is an `<a href="{{ page_url }}">`, never dead text;
* the reporter's own words are surfaced, guarded and quoted, not summarised away;
* **one primary CTA** (`Read the page`) plus the useful secondary (`Open the review queue`) — no dead end;
* **absolute URLs only**, built with `hub_url()` (in `core/events/discord_replies.py`), never a bare path;
* branded shell — the event's `email_shell` default `"light"` routes it through
  `notification_shell_light.html`, and the copy fragment is styled centrally by `_style_copy_fragment`, so it
  renders as dark-slate-on-white with navy links rather than black-on-dark;
* the `.txt` and `.html` bodies are the same sentences in the same order, with the two links written out in the
  text version;
* one timezone — no time appears in this email at all, which is the cheapest way to satisfy the rule.

In-app copy is the short greeting-free form: `{{ reporter_name }} flagged {{ page_title }}: "{{ reason }}"`.

### 7.2 `wiki.page_verified` — the retention mechanism

**This is the load-bearing notification of the whole wiki round.** The message that a lead read your page and
stands behind it is the single strongest reason a member writes a second one. It is written and channelled
accordingly: warm, specific, naming the human who verified, and defaulting to email as well as the bell.

**D owns all four pieces of it — the `Trigger`, the resolver, the copy, and the `period`** (brief §9.1); A
disclaims it, and **B deletes its `Recipients.SINGLE_USER` fallback over `page.created_by`**. B and D build in
parallel, so two registrations of the same key is a guaranteed collision, and the fallback was also the wrong
audience: a page's most valuable contributor is often not the person who created the row. B *calls* it, in the
shape fixed below, passing its own `WikiPage.verified_role_label` (B's migration, B's `_role_label()`, e.g.
"Woodworking orienter") as **`verifier_role`**. B has no decisions to make here.

```python
Trigger(
    "wiki.page_verified",
    "Your wiki page was verified",
    "A guild lead or orienter read a page you wrote and marked it verified.",
    "Wiki",
    email_default=True,
),
```

Member audience (no `Audience.STAFF_ONLY`), so it renders under a member-facing **Wiki** category — add `"Wiki"`
to `settings_matrix.CATEGORY_ORDER` after `"Guilds"`, and **do not** add
`Recipients.WIKI_PAGE_CONTRIBUTORS` to `STAFF_RECIPIENTS`, or it would be hidden from the very people it exists
for. `_TRIGGER_ACTIVITY_KINDS["wiki.page_verified"] = None` (spec B logs `WIKI_PAGE_VERIFIED` itself, with the
verifier's name in the payload).

**Resolver** (`core/events/resolvers.py`):

```python
def wiki_page_contributors(context):
    """Every member who has authored a revision of this page, minus the actor.

    The people who wrote it hear that it was verified. The verifier does not get a
    notification about their own tap, and a page whose only author is the verifier
    resolves to nobody (emit fans out to zero recipients, which is correct, not a bug).
    """
```

Implemented as one query — `Member.objects.filter(wiki_revisions__page=page).exclude(pk=actor_member_pk).distinct()`
— then `_members_to_recipients(members, "wiki_contributor")`. Revisions with `kind=CONFLICT_DRAFT` are included:
a person whose text lost a race still wrote for this page.

**This query depends on A shipping `WikiRevision.author` with `related_name="wiki_revisions"`, not `"+"`**
(brief §9.1). With `"+"` there is no reverse accessor, the filter above does not resolve, and the round's stated
retention mechanism has no audience — so A changed it for exactly this. Stated as a hard dependency in §2.

**The emit call** (spec B makes it; the shape is fixed here so B has no decisions to make):

```python
emit(
    "wiki.page_verified",
    actor=verifier.user,
    target=page,
    context={
        "page": page,
        "actor_member_pk": verifier.pk,
        "member_name": "there",
        "page_title": page.title,
        "page_url": hub_url("hub_wiki_page", page.slug),
        "verifier_name": verifier.display_name,
        "verifier_role": role_label,               # "Woodworking orienter"
        "guild_name": page.guild.name if page.guild_id else "the makerspace",
    },
    period=f"wiki_verified:{page.pk}:{page.verified_at:%Y%m%d%H%M%S}",
)
```

The timestamp in `period` is deliberate: a page edited and re-verified months later **delivers again**, because
that second message is worth as much as the first. A page-only period would silence it forever after the first
verification.

Email subject: `{{ verifier_name }} verified your page "{{ page_title }}"`. Body: the title links to the page
(subject noun), one primary CTA (`See your page`), one secondary (`Write another page for {{ guild_name }}` →
`/wiki/new/`, which is exactly the action the notification exists to provoke), absolute URLs, branded shell,
`.txt` and `.html` in sync.

### 7.3 The archive notice to the author

The brief is emphatic that silent removal ends a contributor's participation permanently, so this is
transactional and **conditional on there being an author to tell** (D20). It reuses the existing
**`member.login_invite` pattern** — a single addressed member, no in-app row needed since the point is that they
may not come back on their own — but as a plain `core.email.send` call from `WikiPage.archive()` rather than a
third event type:

* **Why not an event:** a per-person notice with no preference to hold (nobody may opt out of hearing that their
  work was taken down), no bell row, no Discord, no digest. That is a transactional email, and the spine's
  `email_to` path exists precisely for sends that address one person's address. A fourth registry key would add
  a settings-matrix row that must then be hidden.
* **The exact call.** `core.email.send` is **keyword-only**, takes **rendered strings** rather than template
  paths, and **requires `trigger_kind`**:

  ```python
  core.email.send(
      to=author.email,
      subject=f'Your wiki page "{self.title}" was archived',
      trigger_kind="wiki.page_archived",
      text_body=render_to_string("hub/emails/wiki_page_archived.txt", ctx),
      html_body=render_to_string("hub/emails/wiki_page_archived.html", ctx),
      best_effort=True,
  )
  ```

  Both bodies are `render_to_string`'d by the caller; the function never sees a template name. `best_effort=True`
  means a mail failure is logged in `TransactionalEmailLog` and swallowed rather than re-raised, so a bounced
  address can never roll back the archive. `trigger_kind` is the workflow identifier the audit log groups on, and
  it is required — omitting it is a `TypeError`, not a default.
* **When there is no author, there is no send.** `page.created_by` is null for every seeded Equipment stub and
  for any page whose author's account was deleted. `archive()` skips the call entirely (no log row, no partial
  send), the confirm copy says so before the click (§6.7), and the success message drops its second sentence.
* **The page's other revision authors are deliberately not emailed either**, and this is the one place the spec
  knowingly under-reaches. A page's main writer is frequently not its creator — that is the whole premise of
  seeding stubs so every contribution is an *edit* — so a strict reading of "tell the author" would fan out to
  `Member.objects.filter(wiki_revisions__page=self)`, exactly like §7.2's resolver. It does not, for now, because
  archiving a page with six contributors would send six people an email about someone else's page and the brief's
  own rule is to nudge leads, never authors. **The rule is: the person whose name is on the page gets told.** If
  a real over-archive ever hits a multi-contributor page and the writers find out from the tombstone, the fix is
  one line — swap the recipient for that queryset — and §10 records it as the deferred option rather than
  pretending the question does not exist.
* **Contents:** the page title (**linked to its still-working URL**, which is the whole point — they can read it),
  the reason verbatim and attributed by name (*"Kate Mizuno removed this page and wrote: …"*), the redirect page
  when one is set, one primary CTA (`Read what was there`), one secondary (`Reply to Kate` as a `mailto:`), the
  branded shell, absolute URLs, and both `.txt` and `.html`. Templates:
  `templates/hub/emails/wiki_page_archived.{html,txt}` on `membership/emails/_base.html`.
* **The `mailto:` publishes the archiver's own email address to the author, and that is intentional.** The brief
  requires the author learn why and from whom; a "reply to the person who did this" that routes nowhere is the
  polite version of no answer at all. Archivers are guild leads and admins, whose addresses members already have
  from guild pages and orientation emails, so this discloses nothing new. It is called out here because it is the
  one place in the round where one member's address is put in front of another automatically — if that is ever
  unwanted, the replacement is a link to the guild's contact route, not the removal of the reply path.

### 7.4 Activity rows

Six kinds, six model-method call sites, no `emit()`-written rows (§4.4), and **no migration of D's own** — A
ships the enum values in A1. All six appear in `/manage/activity/`'s kind filter automatically (the activity view
passes `SiteActivity.Kind.choices` straight to the template) — no view change needed there.

One consequence to state rather than discover: **`payload={"restored_from_archive": True}` is invisible to that
filter.** The filter reads `Kind.choices` only and never looks inside a payload, so a restore is findable as
"Wiki page edited" and then only by reading the row. That is the accepted cost of not minting a seventh kind for
the rare inverse of an existing one (§4.4); an admin hunting a specific restore has the page's own history in
front of them, which is a better tool for the job anyway.

## 8. Build order (phases; each phase ships green: full suite + `ruff check` + `ruff format` + `mypy .` + `manage.py check`)

Depends on spec A landing first (models, page, editor, autosave, the six `SiteActivity.Kind` values,
`WikiRevision.kind`, and `WikiRevision.author`'s `related_name`).

1. **The component fixes.** `components/confirm_modal.html` — the `.pl-input` rule in `components.css` and the
   `confirm_note_required` flag (§6.13) — with specs asserting both against all three existing callers. It goes
   first because it is standalone, it fixes a live bug independently of the wiki, and every later phase's confirm
   modals inherit it. **No `SiteActivity.Kind` phase any more:** A ships all six in A1.
2. **Models + permissions.** `WikiReport`, `WikiEditLock`, the **four** `WikiPage` fields,
   `membership/migrations/0167`, `can_moderate_wiki_page`, `moderatable_wiki_scopes`, `mark_needs_review()`, the
   domain exceptions, and every model method in §5. Factories. No UI, **no manager swap, no `AlterField` on
   `WikiRevision`**. This is the phase where `manage.py check` matters most (index names).
3. **Events.** Both `Trigger` rows, both `Recipients` + resolvers, the `_TRIGGER_*` map entries, the
   `settings_matrix` category + `STAFF_RECIPIENTS` entry, both `_CURATED` copy blocks. Wire `emit()` into
   `WikiReport.file()`. Verify the email renders slate-on-white with navy links in the admin preview, not
   black-on-dark. Coordinate with B here: B deletes its `SINGLE_USER` fallback in the same window.
4. **Report end to end.** `_wiki_report_button.html` (both states, including Withdraw), `_wiki_report_modal.html`,
   `_wiki_review_banner.html`, the `hub_wiki_report` / `hub_wiki_report_withdraw` views + form, the OOB banner
   swap, the `pl-wp-mod__banner` CSS. A member can report, withdraw, and every reader sees the banner. First
   user-visible slice.
5. **The review queue.** `/wiki/review/`, the card partials, resolve (from the queue **and** from the banner),
   scoping, single-list pagination, the `?archived=1` view, the written empty states. A lead can now close the
   loop from wherever they are standing.
6. **Graduated actions.** `_wiki_moderation_actions.html` + the merge into A's single phone overflow; the
   official note (render, modal, remove); archive + tombstone + restore + set-redirect; the author email,
   including the null-author skip. `pl-wp-mod__note` CSS.
7. **History and revert.** `wiki_history.html`, pagination at 50, the pre-edit row labels, the single-revision
   read-only view including drafts, revert through `confirm_modal` restoring title + body + facts, the ≤640px
   stacked cards.
8. **Lock and conflict.** `WikiEditLock.claim/refresh` (with the `started_at` reset), the hook into A's autosave
   view, `_wiki_lock_warning.html`, the base-revision check around A's `apply_edit` in `hub_wiki_edit`,
   `wiki_conflict.html`, and "Keep My Version" routed through `apply_edit`.
9. **The safety gate.** The Safety starter's `status=OFFICIAL`, the `create_page` gate, the queue's second
   section, publish with authority-aware copy, decline with its own textarea modal and its email, and A's
   unpublished-proposal edit carve-out.
10. **Housekeeping.** `tests/template_comment_lint_spec.py`, a both-themes pass at 390px and 1440px on every
    screen in §6, and `plfog/version.py` VERSION bump.

> **Changelog rule, changed by brief §9.1 and superseding brief §5.9:** every PR in the wiki round bumps
> `VERSION` with **no changelog entry at all**, except the **last PR of the whole round**, which adds the single
> curated entry. Re-stamping an existing entry re-posts it to Discord on every merge, so the earlier
> "first PR adds the entry, everyone else re-stamps it" would have announced the wiki a dozen times. **D adds no
> `CHANGELOG` entry** unless D happens to be the round's final PR. No em dashes or standalone hyphen-dashes in
> that entry when it is written.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py` under `tests/hub/` and `tests/core/`, `describe_*` / `it_*` only (**never `context_*`** — it is
not a collected prefix and every test inside one is silently skipped), factory-boy for all data, `respx` if any
HTTP appears (none should), 100% branch coverage. New factories: `WikiReportFactory`, `WikiEditLockFactory` in
`tests/membership/factories.py`, alongside A's `WikiPageFactory` / `WikiRevisionFactory`.

**`tests/hub/confirm_modal_spec.py`** (or the nearest existing component spec) — **the shipped-bug fix, §6.13**
* rendering `confirm_modal.html` with `confirm_note_name` and with `confirm_typed_value` produces inputs that
  resolve to the theme tokens: assert `.pl-input` exists in `static/css/components.css` and that its rule names
  `--hub-input-bg` and `--hub-input-border`. A rendering test alone cannot see CSS, so the assertion is on the
  stylesheet, which is what was actually missing.
* **all three existing callers still render**: `equipment_manage.html`, `user_settings.html` and
  `classes/partials/roster_modals.html` are exercised through their own views and their confirm inputs carry the
  class. This is the regression guard for a shared-component change.
* `confirm_note_required` set → the Confirm button's `:disabled` expression includes the note check; **not** set →
  the expression is byte-identical to today's, so no existing caller changes behavior.
* server-side validation is unchanged: a blank note POSTed directly (JS off) is still rejected by the view.

**No `tests/core/wiki_activity_kinds_spec.py`.** The six `SiteActivity.Kind` values ship in A1 and A's specs
assert them; D asserts only that its own `log()` calls carry the payload keys in §4.4, inside the feature specs
below.

**`tests/core/wiki_events_spec.py`**
* `describe_wiki_scope_leadership`: a guild page resolves to lead + every staff role including **orienter** (via
  `GuildStaffMembershipFactory(role=ORIENTER)`); a space-wide page (`guild=None`) resolves to fog admins **only**
  and to no guild lead; a guild with no lead and no staff falls back to admins; a missing `guild` key raises
  (fail-loudly, `_require`).
* `describe_wiki_page_contributors`: every distinct revision author is resolved; the verifier is excluded; a page
  whose only author is the verifier resolves to `[]`; a `CONFLICT_DRAFT` author is included; a deleted-account
  author (null FK) does not crash the resolver.
* `describe_registry`: both keys exist via `get_event`; **neither declares `Channel.DISCORD`**; both declare
  `activity_kind=None` (the double-row guard); `wiki.page_reported` is in `STAFF_RECIPIENTS`' section and
  `wiki.page_verified` is **not**; both appear on the settings matrix under a **Wiki** category for the right
  viewer and are absent for the wrong one.
* `describe_copy`: both `_CURATED` entries render with no `[missing: …]` marker against their sample context; the
  email HTML contains `page_url` as an `href` on the title (subject-noun-is-a-link); the `.txt` body contains the
  same two URLs as the `.html`.

**`tests/hub/wiki_report_spec.py`**
* a member posts a report → row created, banner renders **for every reader** including one with no membership in
  the page's guild, the toast fires, and the OOB target id is present.
* **routing:** a report on a Woodworking page notifies the Woodworking lead + staff and **no admin who is not
  one**; a report on a space-wide page notifies admins and no guild lead; the `SiteActivity` row carries the
  reason and reporter in its payload; the `emit` period is unique per report so two reports both deliver.
* duplicate open report → friendly message, no second row, no second notification.
* a report on an archived page → 404 and no row.
* the reason is escaped, not rendered: a report containing `<script>` renders as text in the banner.
* the banner quotes the **oldest** open report and says "and N more" with several.
* **the denormalized columns are maintained** (§5.1): filing sets `needs_review_since` and
  `needs_review_reason`; the page then appears in A's `WikiPageQuerySet.needs_review()` and its search result
  carries the *Needs review* chip. Resolving the only open report clears **both** columns; resolving one of two
  re-points them at the next-oldest report's `created_at` and `reason`. A reason longer than 300 characters is
  truncated in the column and **not** in the banner.
* **the modal actually closes**: the success response's `HX-Trigger` header contains **both** `showToast` and
  `close-modal` with detail `"wiki-report"` — the ordering assertion, since setting the toast second would drop
  it (`hub/toast.py`).
* the error response targets `#wiki-report-body` and re-renders the bound form with the submitted text intact.
* **withdraw (D18):** with an open report, the reader sees the "You reported this" state and no Report button;
  Withdraw resolves the row with `resolved_by == reporter` and the fixed resolution string, **deletes nothing**,
  brings the banner down when it was the only open report, and leaves it quoting the next when it was not; a
  member cannot withdraw someone else's report; the same member may file again afterwards.

**`tests/hub/wiki_review_queue_spec.py`**
* **scoping:** a Woodworking lead sees only Woodworking reports; a lead of two guilds sees both; an admin sees
  every report **including space-wide**; a plain member gets 403. Explicitly: a guild lead does **not** see
  space-wide reports (the D16 council-boolean divergence — this is the test that pins it).
* query count: the queue runs a bounded number of queries with 30 reports across 10 guilds (no N+1) — asserted
  with `django_assert_num_queries`.
* ordering is oldest first; pagination at 26 rows.
* empty state renders the written sentence, not a blank region; resolving the last report returns the empty
  state.
* resolve sets `resolved_at`/`resolved_by`/`resolution`; a second resolve returns the info toast and does not
  overwrite the first reviewer.
* **resolve from the banner (D17):** posting the same modal from a page view swaps `#wiki-review-banner` to empty
  and toasts, without a queue round-trip; a staff save on a page with an open report returns the
  "Mark it reviewed?" toast; **a staff save does not auto-resolve anything**.
* **`?archived=1` (D19):** a lead sees archived pages in their guilds and not in others'; an admin sees all; the
  list is ordered newest removal first; the written empty state renders when there are none; a plain member still
  gets 403 on the whole route.
* **only one paginator** on the open view: `?page=2` pages the reports and the safety card is unaffected.

**`tests/hub/wiki_official_note_spec.py`**
* a moderator sets, edits, and clears the note; a plain member gets 403 on POST and **no Edit Note control
  appears at all** in their rendered page (not a disabled one).
* setting a note writes **no `WikiRevision`**, and a subsequent revert leaves the note intact — the D1 argument,
  asserted.
* the note renders above member content and carries the byline and date.

**`tests/hub/wiki_archive_spec.py`**
* archive with a reason: the page's **URL still returns 200**; a member sees the tombstone with the date and
  reason and none of the body; a moderator sees the tombstone **and** the body.
* **the tombstone's first line names the archiver** — "Removed by Kate Mizuno on 3 September 2026" — and degrades
  to "Removed on 3 September 2026" when `archived_by` is null.
* the page disappears from every list and search built on A's `visible_for()` / `not_archived()`, while
  `WikiPage.objects.all()` still finds it and `revision.page` / `report.page` still resolve. **No manager swap
  is asserted, because there is none** (D2) — the assertion is on the querysets, which is where the rule lives.
* **the author is emailed, by name, with the reason** — assert the recipient, the reason string, and the
  absolute page URL in both the `.txt` and `.html` bodies, and that the call passed `trigger_kind` and
  `best_effort=True`.
* **null author (D20):** archiving a page with `created_by=None` sends **no** email and writes **no**
  `TransactionalEmailLog` row, the confirm copy renders the no-author sentence, and the activity payload carries
  `"author": ""`. A page with several revision authors and a null creator also sends nothing — the deliberate
  under-reach in §7.3, pinned so a later change to it is a decision and not a drift.
* archive with a blank reason is rejected and does not archive; a reason longer than A's `CharField(300)` is
  rejected by the form rather than silently truncated.
* a redirect set afterwards renders the link on the tombstone; a redirect to self or to an archived page is
  rejected.
* restore clears **all four** archive fields (the stale-attribution rule) and the page returns to `objects`.
* archiving does not resolve open reports.

**`tests/hub/wiki_history_revert_spec.py`**
* history lists every revision newest first with author and kind chip; a null author renders "a former member";
  the chips read **Before this edit** / **Before this revert** / **Unmerged draft**, and the muted explainer line
  is present (the pre-edit-snapshot semantics, §5.5).
* **pagination at 50**: a page with 51 revisions renders a second page and the first page holds exactly 50.
* **revert restores exactly**: title, body **and `facts`** equal the target revision's snapshot — assert the fact
  labels and values and their `sort_order`, not only the body — and `search_text` is rebuilt so the restored
  facts are searchable again. A **new** revision is appended (`kind=REVERT`) and the intervening revisions are
  still present.
* revert does **not** change `status`, in both directions: reverting past a verification does not un-verify, and
  reverting to a revision snapshotted while the page was `GUILD_VERIFIED` does not re-verify (§5.4).
* revert is staff-only: a member sees no Revert control and gets 403 on POST.
* reverting to the current head raises `NothingToRevert` → toast, no write.
* a `CONFLICT_DRAFT` row is not offered a Revert button, **but `?revision=<its pk>` renders it** with the
  "never applied" bar and, for someone who may edit the page, a working **Use This Version** action.

**`tests/hub/wiki_edit_lock_spec.py`**
* opening the editor claims the lock; a second person sees the warning naming the first and the elapsed time;
  the first person never sees a warning about themselves.
* **expiry:** a lock whose `refreshed_at` is older than `TTL` is stale — the next opener claims it and sees no
  warning. Asserted by writing `refreshed_at` directly (`queryset.update`, bypassing `auto_now`), never by
  sleeping.
* **`started_at` restarts on a re-claim (§4.2).** Dana claims the lock, the row is aged past `TTL` with
  `queryset.update(started_at=…, refreshed_at=…)` three hours back, Sam opens the editor and claims it, then a
  third person opens it and sees **"Sam started editing this less than a minute ago"** — not "three hours ago".
  This is the test that would have caught the `auto_now_add` bug, and it fails on the old field definition.
* the autosave POST refreshes the holder's own lock and is a no-op for a non-holder. **The call lives in A's
  `hub_wiki_autosave`**, so this spec exercises the real route rather than the model method alone.
* a successful save deletes the lock.
* the lock never blocks: a second person can open the editor and save.

**`tests/hub/wiki_conflict_save_spec.py`**
* **nothing is lost:** with a stale `base_revision`, the save writes a `CONFLICT_DRAFT` revision containing the
  submitted text **verbatim**, leaves the page unchanged, and redirects to the conflict screen.
* the conflict screen's right card renders **`page.body`**, the live page — asserted against a case where the
  head revision's body differs from it, which is every case, since A snapshots the *pre-edit* state (§5.5). A
  test that only checks "two bodies render" passes on the wrong one.
* "Keep My Version" goes through A's `apply_edit`, writing a `SAVE` revision and leaving the `CONFLICT_DRAFT` row
  in place; "Keep Their Version" writes nothing and **deletes nothing** (the draft row survives, appears in
  history, and its `?revision=` link resolves); "Open the Editor With Both" prefills `page.body` plus the draft
  text under the marker rule.
* a draft already applied renders the "already sorted out" state.
* a non-author, non-moderator gets 403.

**`tests/hub/wiki_safety_gate_spec.py`**
* a member publishing a Safety starter lands an **unpublished** `status=OFFICIAL` page, sees the explanation
  screen, and the page is invisible to other members.
* it appears in the scope's review queue and in no other scope's.
* a **lead** publishing it lands `GUILD_VERIFIED`; an **admin** publishing it lands `OFFICIAL` (the D9 pair).
* **declining is a closed loop (D21):** it keeps the draft unpublished, writes a `WikiRevision` whose `note`
  carries the reviewer's words, **emails the author** (assert recipient, the note text, and the draft's absolute
  URL in both bodies), and deletes nothing. The decline form rejects a note under 10 characters.
* **the author can then act on it:** the author of an unpublished `status=OFFICIAL` page gets 200 and a real
  editor on `/edit/`, and can save; a different non-staff member gets 403 on the same URL; once the page is
  published, the author gets **no edit affordance at all** and 403 on POST — the ordinary Official rule resumes.
* **there is no second path past the gate:** the edit form carries no Safety toggle and no way to set `status`,
  so a member cannot create a Safety page, open Edit, and publish it themselves. (A's toggle is struck; this is
  the test that pins its absence.)
* a moderator creating a Safety page publishes immediately (no gate for staff).
* every non-Safety starter publishes live for a member (the approve-after rule is not regressed).

**Cross-cutting**
* `tests/template_comment_lint_spec.py` after all template work.
* Local full-suite coverage exits non-zero at the repo-wide gate even when green; check per-file coverage for the
  new modules and treat CI as the authority.
* Manual pass, both themes, at 390px and 1440px: the report modal, the already-reported state, the banner with
  and without Mark Reviewed, the queue empty and populated, the `?archived=1` view, the note modal (Remove above,
  Save last), **the archive typed confirmation with the `.pl-input` fix in place** (both inputs must read as real
  fields on Obsidian, which is the visual proof of §6.13), the tombstone for a member and for staff, the history
  stack, the lock warning, the decline modal's textarea, and the conflict screen's stacked cards.
* Also manual, once, on the three pre-existing callers of `confirm_modal.html` — a shared-component change is not
  finished until somebody has looked at the screens it was not written for.

## 10. Open / deferred

* **No member-facing diff viewer** (brief-locked, restated here as an owned decision). Staff need revert, not a
  diff; the reference app spent 253 lines of vanilla JS on word-level HTML diffs for a need members do not have.
  The read-only single-revision view (§6.8) answers "what did it used to say" at a fraction of the cost. Revisit
  only if a lead actually asks, twice.
* **No time-based escalation** of an unreviewed report (D4). No cron, no seven-day nag. If the queue turns out
  to stagnate, the cheapest fix is a count in spec B's monthly lead digest, not a new scheduled job.
* **The reporter is not notified when their report is resolved** (§5.2). A "reviewed" ping with nothing visibly
  changed reads worse than silence, and the page itself is the answer. Reconsider if members start filing
  duplicate reports because they cannot tell whether anyone looked. Partly mitigated by D18: a reporter who
  revisits the page sees their own "You reported this" state while it is open, and its absence once it is closed.
* **Only the page's named author is told when it is archived** (§7.3, D20), not everyone who wrote a revision.
  The one-line change if that proves wrong is to swap the recipient for
  `Member.objects.filter(wiki_revisions__page=self).exclude(pk=archiver.pk)`, which is §7.2's resolver query. Not
  done now because archiving a six-contributor page would email five people about a page they do not think of as
  theirs, and the brief's rule is to nudge leads, never authors.
* **The archive email's `mailto:` publishes the archiver's address to the author, deliberately** (§7.3). If that
  ever needs to change, the replacement is a guild contact route, not the removal of the reply path.
* **`payload={"restored_from_archive": True}` is invisible in `/manage/activity/`'s kind filter** (§4.4, §7.4).
  Accepted; the fix if restores ever need filtering is a seventh kind, not a payload-aware filter.
* **The review queue has one paginator** (§6.4). If safety proposals ever outgrow a single screen, the second
  list needs its own `?safety_page=` param and a variant of `table_pagination.html` that reads it — a fork of a
  shared component, and not worth doing before the need exists.
* **No Admin Tools card for the review queue.** Entry points are the wiki home, the banner's Review button, and
  the notification. A card is a one-line follow-up if admins say they never find it.
* **No `WIKI_MODERATOR` capability** (D10). If the space ever grows a wiki librarian who is not a lead and not an
  admin, it is one migration plus one `DESCRIPTIONS` entry plus swapping `wiki_scope_leadership`'s fallback leg —
  deliberately cheap to add later, deliberately absent now.
* **No page-level "watch this page" subscription** — brief-locked (roughly zero subscribers per page at 200
  members); watch the guild instead (spec B).
* **Bulk moderation** (select several reports, resolve together) is out. At the expected volume the queue is
  three cards long.
* **Hard delete** stays a shell command only, per the brief. This spec does not build one and does not expose
  one.
* **Attachment-level moderation** (removing one bad photo without archiving the page) is out of scope: an
  attachment is deleted from the page's own editor by anyone who can edit the page, which is the same open-edit
  bet the rest of the wiki makes.
* **A "restored" activity kind** was considered and rejected in favour of `WIKI_PAGE_EDITED` with a payload flag
  (§4.4), to keep the `/manage/activity/` kind filter readable.
