# Member Wiki — The Guild Wiki Tab (Spec B) — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-09-07
**Surface:** FOG hub — the guild detail page (`/guilds/<slug>/?tab=wiki`), the wiki page header on
`/wiki/p/<slug>/`, the wanted-pages page (`/wiki/wanted/`), the zero-result block on `/wiki/search/`, and one
monthly email to guild leadership.
**Related:**

- `2026-09-07-member-wiki-brief.md` — **the shared brief. Binding.** Its Locked decisions and Explicitly NO
  tables are not re-litigated here. **§9 (Ownership reconciliation) supersedes anything in this document that
  contradicts it**, including brief §5.9's own changelog rule. This spec was revised against §9 on 2026-09-07;
  the reconciled contract is §2.2 and §2.3.
- `2026-09-07-member-wiki-core.md` (**spec A**) — the `WikiPage` store, read/edit pages, search, revisions,
  attachments, sidebar, Equipment stubs, QR. **B depends on A** and consumes the contracts listed in §2.2.
- `2026-09-07-member-wiki-moderation.md` (**spec D**) — Report a problem, review queue, archive, revert,
  moderation notifications. Runs in parallel with B. Shared surface: `core/events/registry.py` (B adds
  `wiki.guild_digest_monthly`, D adds `wiki.page_reported` **and `wiki.page_verified` — D owns the latter and
  B only calls it**); `SiteActivity.Kind` (**A** ships all six values, D keeps the canonical `log()` shapes, B
  adds none); the `needs_review_since` / `needs_review_reason` pair (D writes it from reports, **B's `verify()`
  clears it**); and the Safety-draft publish surface, which is **D's, not B's**, despite A §7's
  mis-attribution.
- `2026-09-07-governance-doc-mirror.md` (spec E) — no overlap.
- `2026-09-05-equipment-orientations-guild-pattern.md` — the house shape for a guild-surface spec; its
  "mirror the named pattern" discipline applies here (the tab is the guild page's tab pattern, verbatim).
- `2026-08-27-guild-orientation-announcement-polish.md` — the precedent for adding a panel to the guild page
  without disturbing the surrounding tabs.

---

## 1. Summary

A guild page gets a **Wiki** tab: everything the guild knows, in one place, on the page members already open.
It groups the guild's wiki pages by kind (Machines, How-To, Materials, Guild Info, Reference, Projects), shows
what changed recently and who changed it, and puts a search box at the top that looks inside this guild first
with a one-tap escape to search everything. Anyone on the guild's leadership — the lead, any staff role,
**and orienters**, who teach the machine and know what is true — can mark a page **Guild verified** with a
single tap, from the page itself or straight from a row in this tab. Leadership also gets three small panels
that turn the wiki from a hope into a to-do list they can finish on a Sunday afternoon: pages **overdue for
review** with a one-tap "Still accurate" that resets the clock without opening an editor, a **Wanted pages**
list they seed and any member can claim, and the **top searches that found nothing** in the last 30 days, which is a
free content roadmap. Once a month one email lands in leadership's inbox carrying exactly those three things.

Everything in this spec is a **filtered view of the one global `WikiPage` store**. There is no guild-local wiki,
no per-guild namespace, and no page that lives "in" a tab. Every row links to `/wiki/p/<slug>/`; a page whose
scope changes simply stops appearing here.

### Locked decisions (this spec, 2026-09-07)

| # | Decision | Choice |
|---|---|---|
| 1 | Tab position | **Immediately after Orientations** (after Guild Calendar when the guild has no orientations), before Buyables. Overview / Calendar / Orientations answer "what is happening and how do I get access"; Wiki answers "how do I do the thing" and belongs with them, above the commerce and archive tabs. Placing it before every *conditional* tab (FAQ, Wishlist, Gallery) keeps its position stable across guilds. |
| 2 | Tab visibility | `wiki_tab_enabled` = the wiki feature flag is on **AND** `request.surface != "guilds"` **AND** the viewer has a linked Member. The wiki is `@login_required` (brief §4, Public access), the guilds guest surface does not resolve wiki URLs, and an anonymous visitor on the members surface can already open a guild page — so a tab of links they cannot follow is a dead end, not a teaser. |
| 3 | Verify is one tap, full stop | The primary control POSTs immediately. **No modal, no form, no queue.** The optional note is a second, quieter control ("with a note…", 1 field, modal + toast per the FRONTEND.md interaction table) so the note never taxes the common path. |
| 4 | Verification can be removed | A verifier who taps by mistake, or who no longer stands behind a page, gets **Remove verification** behind `confirm_modal.html`. A feature you can turn on but not off is half-built (checklist §10). It posts to the same endpoint with `remove=1` and drops the page to Community. |
| 5 | Who may verify | `can_verify_wiki_page(request, page)` = `can_edit_guild(request, page.guild)` for a guild-scoped page, `is_effective_staff(request)` for a space-wide one. **No new orienter branch is needed**: `GuildStaffMembership.Role.ORIENTER` is a staff role and `can_edit_guild` already admits every staff row via `Guild.is_staffed_by` (`membership/permissions.py`, `membership/models.py:2149`). The brief's "and orienters" is already true; we verified it rather than adding a second code path. |
| 6 | Verify is hidden on Official pages | Official is set by admins/officers and outranks Guild verified (brief §5.2). Rendering a Verify button that would downgrade the chip is a trap. The control is absent, not disabled. |
| 7 | The verification credit is denormalized | `WikiPage.verified_role_label` is written at verify time ("Woodworking orienter", "Woodworking lead", "Admin"). "Verified by Kate (Woodworking orienter), 3 Mar" is a **historical statement**; it must not silently rewrite itself when Kate leaves the guild. **B's migration owns this field** — and, per brief §9.1, **also `verified_note`**, which B originally assumed A shipped. A ships neither. Both are added by B's migration even though the model is A's, so the two specs can land independently. |
| 8 | Lead panels are lead-only | Overdue, Wanted (the editor), and Failed searches render for `can_verify_wiki_page` holders only. A work queue shown to people who cannot work it is noise. Members still see the *Out of date* chip on the page itself and can still edit, and they see the Wanted list read-plus-claim. |
| 9 | The Wanted list editor lives on `/wiki/wanted/` | A dedicated page (a formset is not a modal), scoped by `?guild=<slug>`. One page, two roles: every member sees the list and can Claim or Start; leadership additionally sees the `extra=0` editor with "+ Add a wanted page", per-row Delete, and Save. The guild tab shows a read-plus-claim card that links into it. |
| 10 | Wanted rows are chronological | No `sort_order` field and no drag-to-reorder (drag fails with gloves — brief §5.6). Newest request first; a wanted list that needs manual ordering is too long to be useful. |
| 11 | A repeat request bumps a counter — **but only when a member is the one asking** | "Request this page" from a zero-result search finds an open row with the same normalized title and increments `request_count` instead of creating a twin. "4 people asked for this" is the single most useful number on the panel and costs one integer. **`request(...)` therefore takes `bump: bool = True`**: the failed-search panel's lead-facing "Add To Wanted" passes `bump=False`, because that number counts *people who asked*, and a lead filing the row is not a fourth person asking. Without this, a lead who taps the same row twice manufactures the panel's headline number. |
| 12 | Search misses are per person, per query, per day | Dedupe on `(member, query_normalized, that calendar day)`. The panel then counts **people**, not keystrokes, so one frustrated member retyping six times cannot fake a content roadmap. |
| 13 | Miss retention: 90 days, purged by the digest command | The panel window is a rolling 30 days (decision 19's neighbour, §6.5) and the digest reads last calendar month, so 90 days is generous. The purge rides the daily digest command — no second job row, no unbounded table. |
| 14 | The digest is one email per guild, monthly, on the 1st | `wiki.guild_digest_monthly`, recipient `GUILD_LEADERSHIP` (lead + every staff role, orienters included) with `context={"guild": guild}`, `period="wiki-digest:<guild.pk>:<YYYY-MM>"`. A guild with nothing to report gets **no email**. |
| 15 | The digest's confirm links land on the page, not on a mutating GET | Each overdue page links to `/wiki/p/<slug>/?confirm=1`, which lands the reader on the page with the "Still accurate" button cued. A tokenized one-click GET (the orientation confirm/decline pattern) exists because those recipients act from a phone with no session; a guild lead reading a monthly digest is already a logged-in member, and a mutating GET in an email is a mail-client-prefetch hazard. One extra tap buys correctness. |
| 16 | New CSS namespace | **`pl-wp-tab__`**, in `hub.css`, per brief §9.1. `pl-wp-` returns zero matches in the tree *today*, but A occupies bare `pl-wp-*` heavily (`-card`, `-chip`, `-body`, `-toc`, `-facts`, `-result`, `-actionbar`, `-breadcrumbs`, …) and D adds its own (`pl-wp-empty`, `pl-wp-chip`, `pl-wp-actions`, `pl-wp-banner*`). B's originally-generic `.pl-wp-empty` / `.pl-wp-row` / `.pl-wp-panel` / `.pl-wp-grid` / `.pl-wp-search` would have collided with D's `pl-wp-empty` and A's `pl-wp-chip` across three parallel PRs. **A owns bare `pl-wp-*`; B owns `pl-wp-tab__*` and nothing else.** Status-chip classes are **A's** (one card partial, one status pill, brief §3) — B defines none. |
| 17 | A wanted row can be closed without deleting it | `fulfil()` shipped with no caller B could reach: the `?wanted=<pk>` create path only closes a row when the writer starts from *that row's* button, and the common case (written from `/wiki/new/`, from a search, or already existing) left the row open forever. That made the collapsed "Already Written" section permanently empty and left Delete — which discards the credit — as a lead's only control. Leads get a per-row **Mark As Written** on the read section: one field, a page picker, modal + toast, calling `fulfil()` (§6.3). |
| 18 | A lead can release someone else's stale claim | `release()` already says "the claimer (or a lead)" and `is_claim_stale` flips at 30 days, but Release was rendered only for the claimer, so the staleness hint was a label with no lever. Release renders for `can_verify_wiki_page` holders on any claimed row, behind `confirm_modal.html`. Nothing is deleted; the row goes back to open. |
| 19 | "Add To Wanted" is not repeatable | An actioned failed-search row that still shows a live button is a control that lies. Rows carrying an open wanted match are annotated in the panel query and render **"On the wanted list →"** (a link to the row) instead of the button. Combined with decision 11's `bump=False`, a lead can neither double-file nor inflate the count. |

---

## 2. What already exists (reuse, don't reinvent)

### 2.1 In the current tree (confirmed 2026-09-07 on `fog/delivery-reliability`)

| Need | Existing thing | Location |
|---|---|---|
| The tab strip and its Alpine state | `x-data="{ section: 'overview' }"` + `.pl-tabs` / `.vote-tab` / `.vote-tab--active` | `templates/hub/guild_detail.html:121-132`; CSS `static/css/hub.css:20-40` |
| `?tab=` deep-link mapping (known values only, so a garbage `?tab` cannot blank every pane) | the `x-init` on the same element | `templates/hub/guild_detail.html:122` |
| A tab rendered from its own partial (the model to copy) | `{% include "hub/partials/_guild_meetings_tab.html" %}` inside `<div x-show="section === 'meetings'" x-cloak>` | `templates/hub/guild_detail.html:400-402` |
| Guild-edit permission, `view_as`-aware, already includes every staff role incl. **orienter** | `can_edit_guild(request, guild)` → `is_effective_staff` ∪ `guild_lead_id` ∪ `Guild.is_staffed_by` | `membership/permissions.py`; `membership/models.py:2149` |
| "Which guilds may I edit", **2 queries not N** | `editable_meeting_scopes(request) -> (list[Guild], bool)` | `membership/permissions.py` |
| Effective-staff check (admin / guild officer, preview-aware) | `is_effective_staff(request)` | `membership/permissions.py` |
| The staff-role label for the credit string | `GuildStaffMembership.display_title` (custom title or preset role), `Role.ORIENTER` | `membership/models.py:2340-2415` |
| 403 helper for an editor-only POST | `_require_can_edit_guild(request, guild)` | `hub/views.py:729` |
| Guild-detail context assembly + the `can_edit_this_guild` / guest-surface guard | `guild_detail` | `hub/views.py:527-726` |
| Canonical list editor: `extra=0`, `<template>` clone of `empty_form`, `TOTAL_FORMS` bump, per-row `pl-btn pl-btn--danger pl-btn--sm` Delete at `margin-top:0.75rem` that flips hidden `DELETE` and `requestSubmit()`s, Save last | the **Links** editor and the **FAQ** editor | `templates/hub/guild_edit.html:719-851` |
| The matching save view (validate → `messages` → redirect back to the tab) | `guild_links_save` / `guild_faq_save` | `hub/views.py:4004-4040` |
| Guild Discord channel deep link | `Guild.discord_channel_id`, `Guild.discord_channel_name`, `Guild.announcement_channel_label`, `SiteConfiguration.discord_server_id`; URL shape `https://discord.com/channels/<server>/<channel>` | `membership/models.py:1872-1884, 2320`; `hub/discord_calendar_posts.py:339-342` |
| Notification spine | `emit()` / `emit_with_email_shell()` + the `EventType` registry, `Recipients.GUILD_LEADERSHIP` (lead + all staff, deduped) | `core/events/emit.py:44`, `core/events/senders.py:75`, `core/events/registry.py`, `core/events/resolvers.py:125` |
| Email choke point + branded shell | `core.email.send`; `templates/membership/emails/_base.html` (+ `_footer.txt`) | `core/email.py:61` |
| Scheduled-job registry the dispatcher, the crons, and the Automations dashboard all read | `SCHEDULED_JOBS` / `ScheduledJob` / `Cadence.DAILY` (runs when UTC hour == 13, ~6 AM PT) | `core/scheduled_jobs.py` |
| Audit feed | `SiteActivity.log(kind, actor=, target=, payload=)` | `core/models.py:1298-1340` |
| HTMX success feedback | `trigger_toast(response, msg, type)` | `hub/toast.py` |
| Components | `form_field.html`, `toggle.html`, `modal.html`, `confirm_modal.html`, `table_pagination.html`, `table_search.html` | `templates/components/` |

### 2.2 What B consumes from spec A (the contract)

B does **not** define any of these. If A ships without one, that is a blocking bug in A, not a licence for B to
invent a parallel version.

> **This table was reconciled against A's actual §4 and §5 on 2026-09-07, after the adversarial review round.**
> Roughly half of what B originally listed here did not exist in A under those names, or did not exist at all.
> The names below are **A's real symbols**; brief §9.1 rules that A's names win. The rows marked
> **REQUIRED CHANGE TO A** are things A does not do today and must be told to do — they are not existing
> behaviour B can assume.

**Things A already ships that B reads.**

| From A | Shape B relies on |
|---|---|
| `WikiPage` model | `slug`, `title`, `kind` (the six `Kind` TextChoices), `guild` (nullable FK), `status` (**exactly three** values: `COMMUNITY` / `GUILD_VERIFIED` / `OFFICIAL`), `created_by`, `updated_by`, `created_at`, `updated_at`, **`last_checked_at`**, **`last_checked_by`**, `verified_by`, `verified_at`, `unverified_reason`, `is_published`, `needs_review_since`, `needs_review_reason`, `archived_at` |
| **Not** on `WikiPage` from A | `verified_note` and `verified_role_label` — **B's migration adds both** (§4.3, decision 7). `last_confirmed_at`, `review_interval_months`, `deleted_at`, and a `Status.NEEDS_REVIEW` member **do not exist anywhere**; earlier drafts of B invented all four. |
| Freshness | Module-level **`REVIEW_INTERVALS: dict[str, int \| None]`** (months by kind, `PROJECT` → `None`) and `VERIFIED_AGES_AFTER_MONTHS = 12`; the properties **`freshness_at`** (`last_checked_at or verified_at or created_at`), **`review_due_at`**, **`is_out_of_date`**, and `verification_is_aged`. **Do not derive freshness from `updated_at`** — A rejects that explicitly ("a member fixing a typo does not make a stale machine page accurate") and tests against it. |
| The overdue queryset | **`WikiPageQuerySet.needs_review()`** — SQL, so B's list paginates. It is a **superset** of "overdue": it ORs the per-kind interval cutoffs together **with `needs_review_since__isnull=False`**, which is D's reported-page state. B's Overdue panel and the digest therefore filter **`.needs_review().filter(needs_review_since__isnull=True)`**, or the panel leaks D's report queue into a list whose only control is "Still accurate" — a control that answers the wrong question about a reported page and duplicates D's `/wiki/review/`. |
| Visibility filter (permissions are filters, not checks) | **`WikiPageQuerySet.visible_for(request)`** — or equivalently the module function **`visible_wiki_pages(request)`** in `membership/permissions.py` — plus `.for_guild(guild)` and `.with_fact_prefetch()`. There is no `objects.visible()`. |
| Status pill | `WikiPage.status_pill` → `(modifier, label, tooltip)`, one source for header, card, and search result. **Its precedence puts needs-review *above* Guild verified** — which is why `verify()` must clear the needs-review pair (§5.2). |
| Confirm endpoint | `hub_wiki_confirm` — `POST /wiki/p/<slug>/confirm/`, sets `last_checked_at` + `last_checked_by`, writes no revision, changes no status. **Open to any active member** (A §5.2: "Any active member may call it"). **A owns the endpoint; B owns the surfaces that call it and the list that feeds them.** |
| Permission helper | **`can_verify_wiki_page(request, page)`** — brief §9.1 gives the helper to **A**; B and D consume it. Its definition is B's (§5.1 / decision 5) and brief §9.1 ratifies exactly that: `can_edit_guild` for a guild-scoped page, `is_effective_staff` for a space-wide one, plus B's guard that an Official page is never verifiable. A's draft docstring additionally admits a tool's own orienters via `Equipment.is_run_by` and admits *any* guild's staff to a space-wide page; both are superseded by §9.1 (the Equipment-orienter case stays deferred — B §10). |
| Card partial + status chip | `templates/hub/partials/_wiki_card.html` — one shared card/row partial with exactly one status pill (brief §3). See the include contract below; B adds no chip classes of its own. |
| Reserved slugs | `wanted` is already reserved (brief §5.1) — B adds nothing |
| Sidebar flag | **`SiteConfiguration.wiki_enabled`** (A §4.7), exposed through the `feature_flags` context processor. `wiki_link_enabled` is *kept but repurposed* by A for the old MediaWiki link and is **not** the flag B reads. B reads `wiki_enabled`, never redefines it. |
| Activity vocabulary | Per brief §9.1, **A ships all six `SiteActivity.Kind` wiki values in its A1 migration**, `WIKI_PAGE_VERIFIED` included (enum values are inert; shipping them together removes the A/B/D ordering hazard). A's own §4.7 says it ships only two — that is superseded. **B adds no enum member**; it only calls `log()`. |

**Required changes to A (brief §9.1) — A does not do these today.**

| REQUIRED CHANGE TO A | Why B cannot proceed without it |
|---|---|
| `hub_wiki_search` honors **`?guild=<slug>`** | Every scoped search in B — the tab's search box, its "This guild" scope chip, the digest — is this parameter. A's §6.2 search reads only `q`. |
| `hub_wiki_search` honors **`?kind=<kind>`** | The grouped list's per-group "See all 19 →" link is `?guild=…&kind=…`. |
| `hub_wiki_search` honors **`?stale=1`** | The Overdue panel's "See all 14 →" link. |
| `hub_wiki_search` renders a **browse list for an empty `q`** | Without it, all three links above land on A's "Type what you are looking for" screen — three of B's own "See all" affordances dead-end on an empty page. It is also what brief §2's "one search box spans all three stores" requires. |
| `hub_wiki_page` honors **`?confirm=1`** | The digest's per-page confirm link (decision 15) lands the reader on the page with "Still accurate" cued. A knows nothing about the parameter. |
| `hub_wiki_new` / `hub_wiki_create` carry **`?guild=`, `?title=`, `?wanted=<pk>`** through the starter chooser to the create view, and the create view calls `WikiWantedPage.fulfil(page)` when `wanted` is present | The whole wanted-page fulfilment loop. Every "Start This Page" link B renders goes to the chooser, because the kind is the member's pick from a starter card (brief: Filing) and B has no business guessing it. Note this closes only the case where the writer started from the row's own button — decision 17's Mark As Written closes the rest. |
| `_wiki_card.html` gains an optional **`snippet`** parameter and an **`actions_partial`** slot | Named include contract, below. |
| A's `/wiki/search/` **includes B's `_wiki_search_empty.html`** and deletes its own inline zero-result copy | The screen is specified twice today (A §6.2 "Nothing matched *table saw*" vs B §6.6). Brief §9.1: **B's partial wins.** |
| The named hook point in `hub_wiki_search`, immediately after `results` is built | Where B's one-line `WikiSearchMiss.objects.record(...)` call goes (A §5.7 already names it). |

**The card partial include contract.** B does not "reuse the card partial verbatim" — it reuses it through a
named contract, because a tab row needs a Verify button and a search result needs a snippet:

```
{% include "hub/partials/_wiki_card.html" with page=page snippet=snippet actions_partial="hub/partials/_wiki_verify_control.html" compact=True %}
```

- `page` (required) — the `WikiPage`.
- `snippet` (optional, default unset) — pre-escaped HTML with `<mark>` already inserted; rendered `|safe` by A.
  Without it, search results fork into a second card shape and break the one-card contract in brief §3.
- `actions_partial` (optional, default unset) — a template path A `{% include %}`s in the card's action slot,
  with `page` and `compact` in context. Without the slot, B's compact Verify button has nowhere to live.
- `compact` (optional, default `False`) — passed through to the actions partial.

### 2.3 Coordination with A and D

Brief §9.1 is binding on every line here. Where an earlier draft of B claimed ownership that §9.1 assigns
elsewhere, the §9.1 ruling is stated and B's claim is withdrawn.

- **`can_verify_wiki_page`** — **A owns the helper**; B consumes it. The *definition* is B's (§5.1, decision 5)
  and §9.1 ratifies it. If A ships without the helper, B adds it to `membership/permissions.py` with the body
  in §5.1. Either way it is one function in one file, and the orienter case is served by `can_edit_guild` with
  no extra branch.
- **`WikiPage.verified_role_label` and `WikiPage.verified_note`** — **B's migration adds both** (decision 7,
  §9.1). A ships neither. B's earlier claim that A provides `verified_note` was wrong.
- **`SiteActivity.Kind`** — **A** ships all six wiki values in its A1 migration (§9.1); D keeps the canonical
  `log()` call shapes. **B adds no enum member.** B calls
  `SiteActivity.log(SiteActivity.Kind.WIKI_PAGE_VERIFIED, …)` and nothing more. (Brief §5.8's "D owns the list"
  is about the *vocabulary*; §9.1 moved the *migration* to A to kill the ordering hazard.)
- **`wiki.page_verified` — D owns it entirely** (§9.1): the `Trigger`, the `wiki_page_contributors` resolver,
  the copy, the email templates, and the `period`. **B deletes its `Recipients.SINGLE_USER` fallback
  registration.** B and D build in parallel, so a fallback registration is a guaranteed collision in
  `core/events/registry.py`, not a safety net. B *calls* the event, in D's fixed shape (§5.2), passing B's
  `_role_label(...)` output as D's `verifier_role` context key. B's earlier claim that **A** owns this event was
  also wrong — A §7 explicitly disclaims every spine event ("This spec sends no email and emits no spine
  event").
- **`core/events/registry.py`** — B adds **exactly one** `EventType` (`wiki.guild_digest_monthly`). D adds
  `wiki.page_reported` and `wiki.page_verified`. Three additive edits to one list; resolve the merge by keeping
  all three.
- **The Safety-flavour draft publish surface is D's, not B's.** A's §7 handoff lists "B — publishing a Safety
  draft" against `is_published` / `needs_review_since` / `needs_review_reason`. That is a mis-attribution:
  §9.1 gives **the Safety gate to D**, which owns the queue and the authority-aware publish. B's Verify button
  does not publish an unpublished page and B ships no publish control. If a Safety draft is also unverified,
  a lead publishes it from D's surface and may then verify it from B's.
- **Verification clears the needs-review pair.** A's status-pill precedence puts **Needs review above Guild
  verified**, so a verified page carrying `needs_review_since` renders an amber "Needs review" pill and no
  green check — the verify button would appear to do nothing. `verify()` therefore clears
  `needs_review_since` and `needs_review_reason` (§5.2). This is the right semantics, not a workaround: a lead
  reading a page and standing behind it *is* the resolution D's queue is asking for. **Coordinated with D** —
  D's `resolve()` maintains the same two columns, and D's open `WikiReport` rows on that page stay open until a
  human marks them reviewed, exactly as D's `archive()` already behaves.
- **Verifying writes no `WikiRevision` — B is right and A is wrong.** A's §7 handoff says verifying is "set the
  three, clear `unverified_reason`, stamp `last_checked_at`, **write a revision with `note="Verified"`**".
  **Do not follow A here.** D's `wiki_page_contributors` resolver is
  `Member.objects.filter(wiki_revisions__page=page).exclude(pk=actor_member_pk).distinct()` — so a verification
  revision would enrol every verifier as a "contributor" of every page they verify, and they would then receive
  other people's verification emails forever. That notification is the round's retention mechanism; polluting
  its audience breaks the one thing the round is built around. Secondary reason: a history full of "verified"
  rows makes D's revert list harder to read. **The builder must not reconcile this by adding the revision.**
- **`unverified_reason` is B's to clear.** A assigns the column to B ("clear `unverified_reason`") and B never
  did. Without it, a page that was edited (dropping to Community with "Edited since it was verified.") and then
  re-verified renders a green check above a line saying it was edited since it was verified. `verify()` blanks
  it (§5.2).
- **CSS namespace** — A owns bare `pl-wp-*`, **B owns `pl-wp-tab__*`**, D owns `pl-wp-mod__*`, E keeps
  `pl-gov-*` (§9.1, decision 16). **Re-run the grep against A's merged branch before writing any CSS** — the
  "zero matches" evidence in §6.9 was gathered against `main`, before A existed.

### 2.4 Genuine gaps B closes

Two models (`WikiWantedPage`, `WikiSearchMiss`), one permission helper (if A did not), **two** denormalized
fields (`verified_role_label`, `verified_note`), one tab partial, one dedicated page, one zero-result partial,
one event, one management command, one job row, and one pair of email templates. Nothing else.

---

## 3. Where the code lives

```
membership/
  models.py                       + WikiWantedPage, WikiSearchMiss (+ their querysets/managers)
  permissions.py                  + can_verify_wiki_page (only if A has not added it)
  wiki_guild.py                   NEW — the guild-tab context builder, the verify/claim logic,
                                  the digest payload builder. Keeps guild_detail thin.
  migrations/0168_wiki_guild_tab.py   NEW (0167 is A's; renumber if A lands at a different index)
  management/commands/send_wiki_guild_digest.py   NEW

hub/
  views.py                        + wiki_page_verify, wiki_wanted (GET list + POST editor save),
                                    wiki_wanted_claim, wiki_wanted_request, wiki_wanted_fulfil;
                                    guild_detail gains the wiki-tab context block
  urls.py                         + 5 routes (§6.0 table)
  forms.py                        + WikiWantedPageForm, WikiWantedPageFormSet (extra=0),
                                    WikiVerifyNoteForm, WikiWantedFulfilForm

core/
  events/registry.py              + EventType("wiki.guild_digest_monthly")
  scheduled_jobs.py               + ScheduledJob("send_wiki_guild_digest", Cadence.DAILY)

templates/hub/
  guild_detail.html               + 1 tab button, 1 x-show pane, 1 x-init mapping
  partials/_guild_wiki_tab.html   NEW — the whole tab
  partials/_wiki_verify_control.html   NEW — shared by the page header and the tab rows
  partials/_wiki_search_empty.html     NEW — the zero-result block A's search page includes
  wiki_wanted.html                NEW — the Wanted pages page
templates/membership/emails/
  wiki_guild_digest.html / .txt   NEW

static/css/hub.css                + the pl-wp-tab__* block (§6.9)

tests/hub/ , tests/membership/    NEW *_spec.py files (§9)
```

Home app is `membership` for models and logic (fat models), `hub` for views/templates (skinny views), matching
every other guild-surface feature. This keeps the new code inside the existing coverage and mypy scope.

---

## 4. Data model

Two new models in `membership/models.py`, one migration (`0168_wiki_guild_tab`), fully reversible (it only adds
tables and two blank-defaulted columns; the reverse drops them).

### 4.1 `WikiWantedPage`

A specific ask beats an open invitation. This is the single best answer to "one person writes everything".

| Field | Type | Notes |
|---|---|---|
| `guild` | `FK(Guild, null=True, blank=True, on_delete=CASCADE, related_name="wanted_wiki_pages")` | Null = space-wide, matching `WikiPage.guild`. |
| `title` | `CharField(max_length=200)` | The ask, in the requester's own words. From a failed search this is the **exact query the member typed** (locked item 7 of the task brief). |
| `title_normalized` | `CharField(max_length=200, db_index=True)` | `title.strip().casefold()` with runs of whitespace collapsed. Set in `save()`. Powers the duplicate-bump (decision 11). |
| `note` | `TextField(blank=True, default="")` | Optional: what the page should cover, or who to ask. |
| `created_by` | `FK(Member, null=True, on_delete=SET_NULL, related_name="wiki_pages_wanted")` | Null survives a member deletion; the row is still useful. |
| `claimed_by` | `FK(Member, null=True, blank=True, on_delete=SET_NULL, related_name="wiki_pages_claimed")` | Set by Claim. |
| `claimed_at` | `DateTimeField(null=True, blank=True)` | Drives the "claimed 5 weeks ago" staleness hint. |
| `fulfilled_page` | `FK(WikiPage, null=True, blank=True, on_delete=SET_NULL, related_name="fulfilled_wants")` | Set when the page is created from the row. Non-null = done. |
| `request_count` | `PositiveIntegerField(default=1)` | Bumped when the same ask arrives again (decision 11). |
| `created_at` | `DateTimeField(auto_now_add=True)` | Ordering key. |

```python
class Meta:
    ordering = ["-created_at"]
    constraints = [
        models.UniqueConstraint(
            fields=["guild", "title_normalized"],
            condition=models.Q(fulfilled_page__isnull=True),
            name="uq_wikiwanted_guild_title",   # 25 chars, clear of the 30-char cap
        ),
    ]
    indexes = [models.Index(fields=["guild", "fulfilled_page"], name="idx_wikiwanted_guild_open")]
```

`__str__` → `f"{self.title} ({self.guild.name if self.guild else 'Space-wide'})"`.

Derived state (a property, not a stored field — nothing writes it):

```python
@property
def state(self) -> str:
    """'done' | 'claimed' | 'open'."""
```

`is_claim_stale` → `claimed_at` older than 30 days with no `fulfilled_page`. **No job clears claims**; a
background process that silently un-assigns a member's work is worse than a stale label. But it is not
display-only either: the row shows "claimed by Sam 5 weeks ago", stays claimable and startable by anyone, and
**a lead gets a Release control on it** (decision 18, §6.3). A staleness label with no lever is half a feature;
the model method already exists and said "the claimer (or a lead)" from the first draft.

> Note on the Postgres NULL caveat: the unique constraint does not dedupe space-wide rows (`guild IS NULL`),
> because NULLs never collide. `WikiWantedPage.objects.request(...)` (§5.3) therefore does its own lookup
> before creating, which is the path every automated request takes anyway.

Manager: `WikiWantedPageQuerySet.open()` (`fulfilled_page__isnull=True`), `.for_guild(guild)`,
`.unclaimed()`, and `.with_people()` (`select_related("created_by", "claimed_by", "fulfilled_page", "guild")`).

### 4.2 `WikiSearchMiss`

| Field | Type | Notes |
|---|---|---|
| `query` | `CharField(max_length=200)` | Exactly what they typed, kept for display. |
| `query_normalized` | `CharField(max_length=200)` | Grouping key, same normalization as above. |
| `guild` | `FK(Guild, null=True, blank=True, on_delete=CASCADE, related_name="wiki_search_misses")` | The scope the search ran in, null for "everything". |
| `member` | `FK(Member, null=True, on_delete=SET_NULL, related_name="wiki_search_misses")` | Null after a member is deleted; the miss still counts. |
| `created_at` | `DateTimeField(auto_now_add=True, db_index=True)` | Window + purge key. |

```python
class Meta:
    ordering = ["-created_at"]
    indexes = [
        models.Index(fields=["guild", "created_at"], name="idx_wikimiss_guild_created"),   # 26 chars
        models.Index(fields=["query_normalized"], name="idx_wikimiss_norm"),
    ]
```

`__str__` → `f"'{self.query}' found nothing ({self.created_at:%b %-d})"`.

**Write rules** (all in `WikiSearchMiss.objects.record(...)`, §5.4) — this is the "does not grow without
bound" policy the task asked for, stated once:

1. Only on a **submitted search that returned zero results**. Never on an empty query, never on a typeahead.
2. Only for a signed-in member (the wiki is login-required; `member is None` is a no-op).
3. Length gate: `3 <= len(normalized) <= 120`. Two characters is a typo; 120+ is a paste.
4. Per-day dedupe on `(member, query_normalized, created_at__date)` via one indexed `.exists()`. One person
   retyping counts once, so the panel counts **people**.
5. **Retention 90 days.** `send_wiki_guild_digest` deletes `created_at__lt=now - 90 days` on **every** run
   (daily), not only on digest day. Bounded at roughly (members × distinct failed queries × 90) rows, which
   at 200 members is a table measured in thousands.

Aggregation for the panel and the digest:

```python
WikiSearchMiss.objects.top_for_guild(guild, since) -> list[dict]
# [{"query": "epoxy cure time", "people": 4, "last_seen": datetime}, ...] max 10, people desc, last_seen desc
```
It groups on `query_normalized`, counts rows (already one per person per day), and takes `Max("query")` as the
display spelling. One aggregate query.

### 4.3 Two added columns on A's model

Brief §9.1 assigns **both** to B's migration. A ships neither; B's earlier draft assumed A provided
`verified_note` and it does not exist in A's §4.1 field table.

```python
verified_role_label = models.CharField(
    max_length=80, blank=True, default="",
    help_text="The verifier's role at the moment they verified, e.g. 'Woodworking orienter'. "
              "Frozen on purpose so the credit cannot rewrite itself later.",
)
verified_note = models.CharField(
    max_length=280, blank=True, default="",
    help_text="An optional line the verifier left for the next reader. Shown under the credit.",
)
```

`verified_note` is a `CharField(280)`, not a `TextField`: it is one line under a credit, and the verify
endpoint truncates to `[:280]` anyway (§5.2). See decision 7. Same migration.

**Reverse:** `0168` is additive only; `migrate membership 0167` drops the two tables and the two columns with
no data transformation. No `RunPython` and therefore no reverse function needed.

Run `manage.py check` after this migration — the index-name 30-character cap has bitten this repo before
(E034); every name above is under it and was counted.

---

## 5. Business logic (fat models)

Views stay skinny; everything below lives in `membership/wiki_guild.py` or on the models.

### 5.1 `can_verify_wiki_page(request, page) -> bool`

```python
def can_verify_wiki_page(request: HttpRequest, page: WikiPage) -> bool:
    """True when this request may set or clear a page's Guild verified chip.

    A guild-scoped page defers to :func:`can_edit_guild`, which already admits the lead
    and EVERY GuildStaffMembership role — co-lead, secretary, treasurer, and orienter —
    plus effective admins/officers. The brief's "and orienters" needs no extra branch:
    orienters are guild staff. A space-wide page (guild is None) is effective staff only,
    because there is no guild whose authority could stand behind it.
    """
    if page.status == WikiPage.Status.OFFICIAL:
        return False                      # Official outranks verified; the control is absent (decision 6)
    if page.guild is None:
        return is_effective_staff(request)
    return can_edit_guild(request, page.guild)
```

**Bulk form for list surfaces.** The guild tab lists many rows that all share one guild, so it calls the helper
once. `/wiki/wanted/` and the "Recently updated" column can span guilds; those build one set from
`editable_meeting_scopes(request)` and test membership per row — **2 queries, not N**:

```python
guilds, _council = editable_meeting_scopes(request)
verifiable_guild_ids = {g.pk for g in guilds}
```

### 5.2 Verify / unverify — `WikiPage.verify(...)` and `.unverify(...)`

Both are model methods (fat models), called by one thin view.

```python
def verify(self, by: Member, *, note: str = "") -> None:
    """Mark this page Guild verified, crediting `by` with their role at this moment."""
```

Guards, in order — each raises a domain exception, never a generic one:

| Guard | Exception |
|---|---|
| Page is Official | `WikiVerificationError("Official pages are set by admins, not verified.")` |
| Page is archived (`archived_at` set — A has no soft-delete manager; brief §9.1 struck it) | `WikiVerificationError("This page is archived.")` |
| Already Guild verified by the same member within the last 60 seconds | no-op, returns quietly (double-tap on a phone must not write two revisions) |

Side effects:

1. `status = GUILD_VERIFIED`; `verified_by = by`; `verified_at = now`; `verified_note = note[:280]`;
   `verified_role_label = _role_label(by, self.guild)`.
2. **`last_checked_at = now`** and **`last_checked_by = by`** — verifying **is** a confirmation, so it resets
   the review clock too; a lead should never have to tap both. A ships `last_checked_by` and the byline renders
   "Last checked by Kate O." from it, so writing only the timestamp would leave a stale name beside a fresh
   date. (A's names, per §9.1: there is no `last_confirmed_at`.)
3. **`unverified_reason = ""`** — A assigns this column to B and B never cleared it. Left set, a page that was
   edited down to Community ("Edited since it was verified.") and then re-verified renders a green check
   directly above a line contradicting it.
4. **`needs_review_since = None`, `needs_review_reason = ""`** — A's pill precedence puts Needs review *above*
   Guild verified, so without this the button appears to do nothing: the page keeps its amber pill. Clearing
   them is also the correct semantics, not a workaround — a lead reading the page and standing behind it is the
   resolution D's queue wants. Coordinated with D (§2.3); D's open `WikiReport` rows are **not** auto-resolved,
   matching how D's own `archive()` leaves them for a human.
5. `SiteActivity.log(SiteActivity.Kind.WIKI_PAGE_VERIFIED, actor=by.user, target=self,
   payload={"guild": self.guild_id, "note": bool(note)})` — the enum member is **A's** (§9.1); B adds none.
6. `emit("wiki.page_verified", ...)` — **D owns this event** (§9.1): the `Trigger`, the
   `wiki_page_contributors` resolver, the copy, and the `period`. B **calls** it in D's fixed shape and
   registers nothing. There is **no `Recipients.SINGLE_USER` fallback** — B and D build in parallel, so a
   fallback registration collides in `core/events/registry.py` rather than covering for a late D. If D has not
   landed, B's verify call is the last thing wired up, not a second registration.

   ```python
   emit(
       "wiki.page_verified",
       actor=by.user,
       target=self,
       context={
           "page": self,
           "actor_member_pk": by.pk,
           "member_name": "there",
           "page_title": self.title,
           "page_url": hub_url("hub_wiki_page", self.slug),
           "verifier_name": by.display_name,
           "verifier_role": self.verified_role_label,   # B's _role_label output = D's verifier_role
           "guild_name": self.guild.name if self.guild_id else "the makerspace",
       },
       period=f"wiki_verified:{self.pk}:{self.verified_at:%Y%m%d%H%M%S}",
   )
   ```

   The timestamped `period` is D's deliberate choice: a page re-verified months later delivers again.
7. **No `WikiRevision` row.** **A's §7 handoff says to write one (`note="Verified"`). Do not.** D's
   contributor resolver is `Member.objects.filter(wiki_revisions__page=page)…`, so a verification revision
   would make every verifier a permanent "contributor" of every page they verify, and they would start
   receiving other people's verification emails — breaking the exact notification this round is built around.
   Secondary reason: a history full of "verified" rows makes D's revert list harder to read. This conflict is
   named here so the builder does not "reconcile" it in A's favour.

`_role_label(member, guild)` — the frozen credit string:

```
guild.guild_lead_id == member.pk               -> f"{guild.name} lead"
a GuildStaffMembership row on this guild       -> f"{guild.name} {row.display_title.lower()}"   # "Woodworking orienter"
otherwise (admin / officer reaching in)        -> "Admin"
space-wide page                                -> "Admin"
```
One query (`guild.staff_memberships.filter(member=member).first()`), executed once per verify.

```python
def unverify(self, by: Member, *, reason: str = "") -> None:
    """Drop back to Community and clear the credit. The review clock is NOT reset."""
```
Sets `status = COMMUNITY`, blanks `verified_by` / `verified_at` / `verified_note` / `verified_role_label`,
logs `WIKI_PAGE_VERIFIED` with `payload={"removed": True}`, emits nothing (nobody needs a notification that
their green check went away; that is a conversation, not an alert). It does **not** re-set
`needs_review_since` — removing a verification is not a report, and re-raising D's amber banner from B's
control would put a page into D's queue with no `WikiReport` behind it.

> The brief's rule that **a non-staff edit drops a verified page back to Community** lives in A's save path,
> not here. B only owns the deliberate button.

### 5.3 Wanted pages

```python
WikiWantedPage.objects.request(*, title, guild, member, bump: bool = True) -> tuple[WikiWantedPage, bool]
```
Normalizes the title; looks for an **open** row with that `(guild, title_normalized)`; if found, applies
`F("request_count") + 1` **only when `bump` is true** and returns `(row, False)`; else creates with
`request_count=1` and returns `(row, True)`. This is the one entry point used by "Request this page"
(zero-result screen, `bump=True` — a member is asking) and by "Add To Wanted" (failed-search panel,
**`bump=False`** — a lead is filing, not asking), so the dedupe can never be bypassed and the panel's headline
number keeps meaning *people* (decisions 11 and 12). A lead tapping "Add To Wanted" twice is therefore a
no-op, and decision 19 stops them seeing the button a second time at all.

```python
WikiWantedPage.claim(self, member) -> None      # sets claimed_by/claimed_at; ValueError if already fulfilled
WikiWantedPage.release(self, member) -> None    # the claimer, or a lead, hands it back
WikiWantedPage.fulfil(self, page) -> None       # "this row is done, here is the page"
```

`release` clears `claimed_by` / `claimed_at`; it raises `ValueError` on a fulfilled row. **Authorization is the
caller's** — the view admits the claimer, or a `can_verify_wiki_page` holder for the row's scope (decision 18).

`fulfil` sets `fulfilled_page` and, when `claimed_by` is empty, credits `page.created_by` as the claimer, so
the list reads "written by Sam" without anyone having pressed Claim first. It raises `ValueError` when the row
is already fulfilled or when `page` is archived. **It has two callers, and both must exist:**

1. **A's create view**, when `?wanted=<pk>` is present (a required change to A, §2.2). This covers only the
   writer who started from that row's own "Start This Page" button.
2. **B's `wiki_wanted_fulfil` view** (decision 17, §6.3), for every other path — the page written from
   `/wiki/new/`, written from a search result, or already existing. Without it the row stays open forever, the
   collapsed "Already Written" section is permanently empty, and a lead's only way to close a row is Delete,
   which throws away the credit the section exists to show.

### 5.4 Recording a miss

```python
WikiSearchMiss.objects.record(*, query, guild, member) -> WikiSearchMiss | None
```
Applies the five write rules from §4.2 and returns `None` when it declines. **A's search view calls it** on the
zero-result branch; that one call site is the whole integration (`if not results: WikiSearchMiss.objects.record(...)`).

### 5.5 The tab payload

```python
def guild_wiki_tab_context(request, guild) -> dict[str, Any]
```
Called from `guild_detail` only when `wiki_tab_enabled`. Query budget: **2 for a member, 4 for a lead.**

1. One page query: `visible_wiki_pages(request).for_guild(guild).with_fact_prefetch().order_by("title")` —
   A's real symbols (§2.2); there is no `objects.visible()`. Everything below is computed in Python off that
   one list: `groups` (ordered `Machines, How-To, Materials, Guild Info, Reference, Projects`, empty groups
   omitted, **max 12 rows per group** plus a "See all N" link into
   `/wiki/search/?guild=<slug>&kind=<kind>`), `recent` (`sorted by updated_at desc[:5]`), and — lead only —
   `overdue`: `[p for p in pages if p.is_out_of_date and p.needs_review_since is None]`, oldest `freshness_at`
   first.

   **Why the `needs_review_since is None` filter.** A's `needs_review()` queryset is a *superset*: it ORs the
   per-kind interval cutoffs with `needs_review_since__isnull=False`, which is D's reported-page state. An
   Overdue panel that included reported pages would offer "Still accurate" as the only control on a page
   somebody has just reported as wrong, and would duplicate D's `/wiki/review/` queue with a worse verb. The
   in-Python filter here and the queryset filter in §5.6 are the same rule stated twice, once per surface.
2. One wanted query, **annotated for decision 19**:
   `WikiWantedPage.objects.for_guild(guild).open().with_people()[:8]`, plus the same manager's
   `open_titles_for_guild(guild) -> set[str]` (one `values_list("title_normalized", flat=True)` over open rows
   for this guild) which the failed-search panel tests each miss's normalized query against. Both come off one
   round trip in practice because the panel is lead-only and the list is short; count them as one for the
   budget. A count of open rows rides along for the "3 open" secondary link.
3. **No tab badge.** An earlier draft budgeted a count "for the tab badge", but §6.1 renders no badge and no
   other tab on the guild page carries one. Dropped rather than half-specified: a number on the tab strip is a
   new pattern for the whole guild page, and it would need its own truncation, its own empty case, and its own
   mobile behaviour on a strip that already scrolls.
4. Lead only: `WikiSearchMiss.objects.top_for_guild(guild, since=now - 30 days)` — a **rolling 30-day**
   window, not the calendar month (§6.5).

A guild with more than 200 pages falls back to "See all" links per group rather than rendering everything —
the tab is a directory, not an index.

**Mobile ordering (≤900px).** When the viewer can verify, the lead panels are ordered **above** the grouped
list; for everyone else they do not exist. On a phone the one-column stack otherwise puts Overdue, Wanted and
Failed searches below as many as **72 page rows** (six kinds × 12), and the guild lead with forty minutes on a
Sunday afternoon is the persona this whole feature is for. The context builder sets a boolean
`lead_panels_first = can_verify` and the template puts `pl-wp-tab__grid--lead` on the grid; the ordering is
`order:` on the two columns inside that class at the ≤900px breakpoint. **Never an inline `style` on the
`x-show` element** (Rule 12 — the bug that collapsed the orientation slot table). If the reordering proves
awkward against A's card partial, the acceptable alternative is a **"Lead Tools" jump chip** in the search row
that anchors to the panels; what is not acceptable is leaving the panels at the bottom of 72 rows.

If any of it raises (A not deployed, flag off mid-request), `guild_detail` must still render: the context
builder is called inside the `wiki_tab_enabled` guard and nowhere else, so a missing wiki cannot 500 a guild
page.

### 5.6 The digest payload

```python
def guild_digest_payload(guild, *, month_start, month_end) -> dict | None
```
Returns `None` — meaning **do not send** — when all three lists are empty. Otherwise:

| Key | Content |
|---|---|
| `new_pages` | pages in this guild created within the window, newest first, max 10, each with author + kind |
| `overdue` | `visible_wiki_pages_for_leads.for_guild(guild).needs_review().filter(needs_review_since__isnull=True)`, oldest **`last_checked_at`** first, max 10, each with its `?confirm=1` URL. A's real symbols: there is no `out_of_date()` queryset and no `last_confirmed_at` column, and the `needs_review_since` filter keeps D's reported pages out of a list whose only verb is "still accurate" (§5.5). |
| `misses` | `top_for_guild(guild, since=month_start)` max 5, each with `people`. The **digest** genuinely wants the calendar month (it is a monthly report of a month); only the on-tab panel switches to a rolling window. |
| `wanted_open_count` | open wanted rows, for the secondary link |
| absolute URLs | guild page, `?tab=wiki`, `/wiki/wanted/?guild=<slug>`, `/wiki/new/?guild=<slug>` |

---

## 6. UI / UX

Every screen below is walked with its real path, its components, its named controls, and its empty / loading /
error / success states. Theme tokens only; **verify both dark and light**. Headings are Title Case (Rule 22).
Save is last and says "Save" (Rule 21). Buttons clear the section above them (Rule 18). No `{# #}` comment
wraps to a second line (Rule 17) — run `tests/template_comment_lint_spec.py`.

### 6.0 Routes B adds

| URL | Name | Method | Purpose |
|---|---|---|---|
| `wiki/p/<slug>/verify/` | `hub_wiki_verify` | POST | Verify, verify-with-note (`note=`), or remove (`remove=1`) |
| `wiki/wanted/` | `hub_wiki_wanted` | GET, POST | The list + the lead editor's Save (`?guild=<slug>` scopes both) |
| `wiki/wanted/<int:pk>/claim/` | `hub_wiki_wanted_claim` | POST | Claim / release (`release=1`) |
| `wiki/wanted/<int:pk>/fulfil/` | `hub_wiki_wanted_fulfil` | POST | **Mark As Written** — links the row to a page (decision 17) |
| `wiki/wanted/request/` | `hub_wiki_wanted_request` | POST | "Request this page" and "Add To Wanted" |

All five are `@login_required`; the four mutating ones are `@require_POST`.

**Response shape — read this before writing any view here.** Brief §9.2 is explicit: **a 204 carries no body,
so it cannot perform an `hx-swap-oob` swap.** Every "204 + toast + OOB" in B's earlier draft would have toasted
success and left the chip, the row, or the panel showing the old state until the member reloaded — on *every*
Verify and *every* Still accurate, which is every mutating control on the surface. The rule:

- **A control that changes something on screen returns `200` with the OOB fragment as its body, then
  `trigger_toast(response, …)` on top** — FRONTEND.md's documented OOB pattern (`render(...)` the partial,
  then `trigger_toast`, then return it).
- **`204` is kept only where nothing on screen changes.**
- Order matters (brief §9.2): `trigger_toast()` **overwrites** `HX-Trigger` while `trigger_client_event()`
  merges, so set the toast first if a view needs both.

So `hub_wiki_verify` returns **200 with the re-rendered `_wiki_verify_control.html` (page header) or the
re-rendered row (tab)** carrying `hx-swap-oob`, plus the toast — or a redirect back to the referring surface
with a Django message for a plain form post (the tab rows post with HTMX; the page header degrades to a plain
form so it works with JS off). `hub_wiki_wanted_claim`, `hub_wiki_wanted_fulfil` and `hub_wiki_wanted_request`
follow the same rule; see their state tables in §6.2–§6.5.

### 6.1 The Wiki tab — `templates/hub/partials/_guild_wiki_tab.html`

**Wiring in `templates/hub/guild_detail.html`** (three one-line edits, mirroring the Meetings tab exactly):

```html
{# tab button — after the Orientations button, line ~126 #}
{% if wiki_tab_enabled %}<button type="button" class="vote-tab"
    :class="section === 'wiki' ? 'vote-tab--active' : ''" @click="section = 'wiki'">Wiki</button>{% endif %}

{# pane — beside the other panes #}
{% if wiki_tab_enabled %}
<div x-show="section === 'wiki'" x-cloak>{% include "hub/partials/_guild_wiki_tab.html" %}</div>
{% endif %}
```

and inside the existing `x-init`, **guarded by the same flag** so a `?tab=wiki` on a guild without the tab
cannot blank every pane (the exact failure the existing comment warns about):

```
{% if wiki_tab_enabled %} if (t === 'wiki') section = 'wiki';{% endif %}
```

**Layout & container:** inline in the pane, no modal. Two columns on desktop
(`.pl-wp-tab__grid`, `grid-template-columns: minmax(0,2fr) minmax(0,1fr)`): the grouped page list on the left,
"Recently Updated" plus the lead panels on the right. One column below 900px.

**Top of the tab — guild-scoped search** (`.pl-wp-tab__search`):

- A `<form method="get" action="{% url 'hub_wiki_search' %}">` with a hidden `guild={{ guild.slug }}`, one
  text input (`name="q"`, `placeholder="Search the {{ guild.name }} wiki"`, wrapped in `.hub-form-group` so it
  inherits the theme's input tokens — Rule 13; never inline `background`/`color` on an input), and a submit
  button labelled **Search**.
- Under it, a two-chip scope row (`.pl-wp-tab__scope`): **This guild** (active, `aria-pressed="true"`) and
  **Everything**. "Everything" is a real submit button (`name="scope" value="all"`) that drops the guild
  param — one tap, not a dropdown, not a hover menu.
- Right of the search box: **+ Start A Page** → `{% url 'hub_wiki_new' %}?guild={{ guild.slug }}`
  (`pl-btn pl-btn--primary pl-btn--sm`). This is the tab's primary action and it is present in every state.

**Left column — the grouped page list** (`.pl-wp-tab__group`):

- One `<h3 class="pl-wp-tab__group-title">` per non-empty kind, in the fixed order Machines / How-To / Materials /
  Guild Info / Reference / Projects, with a muted count.
- Each row uses **A's shared card partial** (brief §3: one card partial, exactly one status pill): title link
  to `/wiki/p/<slug>/`, a quiet neutral meta line ("Machine · updated 12 Mar by Sam"), and the single status
  chip. Attribute chips are neutral text, never coloured pills.
- Rows are full-width anchors, minimum 48px tall.
- A group with more than 12 rows ends with "See all 19 →" linking to the scoped search.

**Right column:**

- **Recently Updated** (`.pl-wp-tab__recent`) — 5 rows, "title · Sam · 2d ago", each linking to the page. Faces and
  recency, because seeing that a person (not a system) touched this last week is what makes the next person
  edit.
- Then the three lead panels (§6.4, §6.5, §6.3), in that order: Overdue first because it is the only one with
  a deadline.

**States:**

| State | What renders |
|---|---|
| **Empty** (no pages) | `.pl-wp-tab__empty` card: "Nothing here yet." / "Start the first page for {{ guild.name }}, or ask someone to write one." + **+ Start A Page** and (leads) **+ Add A Wanted Page**. Never a blank region. In practice the Equipment stubs A seeds mean only a guild with no equipment lands here. |
| **Empty for one group** | the group is omitted entirely — no "0 pages" headings. |
| **Loading** | the tab is server-rendered with the page; nothing streams. The inline Verify / Still accurate buttons are the only HTMX in the tab (see their states below). |
| **Error** | if the wiki flag is off or A's queryset raises, the tab is not rendered at all (the `{% if wiki_tab_enabled %}` guard) — the guild page is unaffected. A failed inline POST toasts the error and leaves the row untouched. |
| **Success** | per-control toasts, below. |

**Mobile (390px):** the tab strip already scrolls horizontally (`.pl-tabs { overflow-x: auto }`), so "Wiki"
is reachable by swipe; nothing new is needed there. `.pl-wp-tab__grid` collapses to one column at ≤900px.

**The one-column order is not the DOM order for a lead.** Stacking the right column below the left puts the
Overdue, Wanted and Failed-search panels underneath as many as **72 page rows** (six kinds × 12), and the guild
lead with forty minutes on a Sunday is the persona this feature exists for — asking them to thumb past the
whole directory to reach their to-do list is the wrong trade for a member's slightly shorter scroll. So at
≤900px, when `can_verify_wiki_page` is true, `.pl-wp-tab__grid--lead` sets `order:` on the two columns to put
**the lead panels first**; for a member nothing changes, because the panels do not render at all. The
ordering lives entirely in that CSS class — **never an inline `display`/`order` on the `x-show` element**
(Rule 12, the bug that collapsed the orientation slot table). Acceptable alternative if the reorder fights A's
card partial: a **"Lead Tools" jump chip** in the search row anchoring to the panels. Leaving the panels at the
bottom is not acceptable.

Page rows become full-width cards with the title on line one and the
meta on line two; the status chip sits inline after the title and wraps with it. All controls are ≥48px.
**No hover-only affordances anywhere in this tab** — every action is a visible button or link. The display
property for `.pl-wp-tab__grid` lives in the CSS class, never in an inline `style` on the `x-show` element
(Rule 12 — this is exactly the bug that collapsed the orientation slot table).

### 6.2 The Verify control — `templates/hub/partials/_wiki_verify_control.html`

One partial, two homes. It takes `page` and `compact` (true in a tab row).

**Home 1 — the page header on `/wiki/p/<slug>/`** (A's template includes it beside the status chip):

- **Not yet verified, viewer may verify:**
  - Primary: `<button class="pl-btn pl-btn--primary pl-btn--sm">Verify</button>` inside a
    `<form method="post" action="{% url 'hub_wiki_verify' page.slug %}" hx-post="…" hx-swap="none"
    hx-disabled-elt="this">`. **One tap. No modal.** (decision 3)
  - Secondary: a quiet text button **with a note…** → `$dispatch('open-modal', 'wiki-verify-note')`. The modal
    (`components/modal.html`, `modal_size="sm"`) holds one field — `WikiVerifyNoteForm.note`, rendered through
    `components/form_field.html`, hint "Anything the next reader should know. Optional." — and a **Save**
    button. 1 field → modal + toast, per the FRONTEND.md interaction table.
  - Helper line under the pair: "Verifying says you read it and stand behind it. It resets the review clock too."
- **Already verified:**
  - The credit line: **"Verified by Kate (Woodworking orienter), 3 Mar"**, built from `verified_by.display_name`,
    `verified_role_label`, and `verified_at|date:"j M"`. If `verified_note` is set it renders underneath in
    muted type, quoted.
  - At 12 months the chip greys and the line reads "Verified Mar 2026" (brief §5.2, *Verified (aged)*) — the
    styling is A's chip; B only supplies the credit text.
  - For a viewer who may verify: **Re-verify** (same one-tap POST, refreshes the date) and, in a quiet
    overflow, **Remove verification** → `components/confirm_modal.html`
    (`confirm_id="wiki-unverify"`, title "Remove this verification?", message "The page drops back to Community.
    Anyone can still read and edit it. Nothing is deleted.", button text "Remove verification",
    `confirm_button_style="danger"`).
- **Official page, or viewer may not verify:** the partial renders **nothing at all** — no disabled button, no
  greyed control (decision 6 and the brief's Official-pages rule: members get no edit affordance, not a dead one).

**Home 2 — inline in a tab row** (`compact=True`): the one-tap **Verify** button only, `pl-btn pl-btn--sm`,
sitting in `.pl-wp-tab__row-actions` at the right of the row (below the meta line on mobile, with
`margin-top:0.5rem` so it clears the text). No note, no re-verify, no remove — those live on the page, where
there is room to explain them. An already-verified row shows the chip and no button.

**States for both homes:**

| State | Behaviour |
|---|---|
| **Loading** | `hx-disabled-elt="this"` disables the button for the flight; `.pl-wp-tab__busy` (opacity .55, `pointer-events:none`) is applied via `htmx-request`. A double-tap on a phone therefore cannot double-post, and the 60-second same-member guard in `verify()` is the server-side backstop. |
| **Success** | **HTTP 200** whose body *is* the re-rendered fragment carrying `hx-swap-oob` — the chip + credit line (page header) or the whole row (tab) — with `trigger_toast(response, "Verified. Thanks for reading it.", "success")` set on it. **Not 204:** a 204 has no body, so it cannot perform the OOB swap, and the earlier "204 + toast + OOB" spec would have toasted success while leaving the chip stale until reload (brief §9.2). No page reload, no scroll jump. |
| **Error (403)** | toast "You can't verify pages for this guild." — the button is only rendered for people who can, so this is the stale-tab case. |
| **Error (domain)** | `WikiVerificationError` → 400 + toast carrying the exception's message ("Official pages are set by admins, not verified."). |
| **JS off** | the plain `<form method="post">` still posts; the view redirects back with a Django message. |

### 6.3 Wanted Pages

**On the guild tab** — the `.pl-wp-tab__wanted` card, visible to **everyone**:

- Heading "Wanted Pages", hint "Pages this guild wants written. Claim one, or just start it."
- Up to 8 open rows, newest first. Each row: the title, a muted second line ("asked by Dana, 4 Mar" and, when
  `request_count > 1`, "**4 people asked for this**"), the note when set, and its actions:
  - **Claim** (`pl-btn pl-btn--sm`, HTMX POST → toast "Claimed. It's yours.") when unclaimed.
  - **Start This Page** (`pl-btn pl-btn--primary pl-btn--sm`) → `hub_wiki_new?guild=<slug>&title=<title>&wanted=<pk>`
    — always present, claimed or not, because the point is the page, not the claim.
  - When claimed by someone else: muted "Claimed by Sam, 5 weeks ago" plus Start This Page (still allowed —
    a claim is a signal, not a lock).
  - When claimed by **you**: **Release** (`pl-btn pl-btn--sm`) beside Start This Page.
  - When claimed by someone else **and the viewer holds `can_verify_wiki_page`**: **Release** as well
    (decision 18). `release()` always said "the claimer (or a lead)" and `is_claim_stale` flips at 30 days, but
    the control was rendered only for the claimer — so the staleness hint was a label with no lever and a
    claim that went quiet blocked nothing but looked like it did. It goes behind
    `components/confirm_modal.html` (`confirm_id="wiki-wanted-release"`, title "Release this claim?", message
    "Sam claimed this 5 weeks ago. Releasing it lets someone else pick it up. Nothing is deleted.",
    button text "Release the claim", `confirm_button_style="danger"`) — the claimer's own Release stays a
    one-tap button, because releasing your own claim needs no ceremony.
- **On a space-wide (null-guild) row:** Claim and Start This Page render for **any active member**, exactly as
  on a guild row — a space-wide wanted page is the makerspace asking, and anyone may answer. Only the *editor*
  and the lead-only controls (Release-someone-else's, Mark As Written, Delete) narrow, to
  `is_effective_staff` for the space-wide scope. Stated because the row otherwise reads ambiguously: the
  wanted list is the one surface in B where a member has a real action.
- Footer link for leads: **Manage the list →** `{% url 'hub_wiki_wanted' %}?guild={{ guild.slug }}`.
- **Empty state:** "No requests yet." plus, for leads, "Ask for the pages your guild actually needs — a
  specific ask gets written; an open invitation doesn't." and a **+ Add A Wanted Page** button into the editor.

**The editor page — `templates/hub/wiki_wanted.html`** (route `hub_wiki_wanted`). This is a **list editor and
will be reviewed against checklist §1**, so every control is named here.

- **Container:** dedicated page (a formset is never a modal), `hub-card`, with `components/page_header.html`
  ("Wanted Pages", subtitle naming the scope: "for Woodworking" when `?guild=` is set, "Space-wide" otherwise).
  A guild switcher is its own tiny `<form method="get">`: a plain `<select name="guild">` of
  `editable_meeting_scopes(request)[0]` inside `.hub-form-group`, with `select option { background; color }`
  styled (checklist §5), **plus a real `Go` submit button**. It navigates on change via a one-line
  `@change="$el.form.requestSubmit()"`, and the Go button is what makes it work with JS off — a bare
  navigate-on-change `<select>` is a control that silently does nothing for a keyboard or no-JS user. It is a
  separate form and is never part of the save form.
- **Read section (every member):** the same rows as the tab card, unlimited, with Claim / Release / Start This
  Page. Uses `components/table_pagination.html` past 25 rows. Fulfilled rows move to a collapsed
  "Already Written" section (`x-show`, closed by default) showing "→ linked page".
- **Mark As Written (leads and admins only, on each open row of the read section)** — decision 17, the control
  that makes `fulfil()` reachable and the "Already Written" section non-empty. Without it a lead can add a
  wanted page and never close it except by Delete, which discards the credit; and even once A's `?wanted=<pk>`
  create path lands, it only closes the row for a writer who started from *that row's own button* — the common
  cases (written from `/wiki/new/`, written from a search result, or the page already existed) all leave the
  row open forever.
  - Trigger: **Mark As Written** (`pl-btn pl-btn--sm`) → `$dispatch('open-modal', 'wiki-wanted-fulfil-{{ row.pk }}')`.
  - The modal (`components/modal.html`, `modal_size="sm"`) holds **one field** — `WikiWantedFulfilForm.page`, a
    `ModelChoiceField` over `visible_wiki_pages(request).for_guild(row.guild)` ordered by title, rendered
    through `components/form_field.html`, label "Which page?", hint "The page that answers this request." A
    guild with more than 100 pages degrades the widget to a slug text input with the same validation, so the
    control never becomes a thousand-option `<select>` on a phone. 1 field → modal + toast, per the
    FRONTEND.md interaction table.
  - **Save** is the last element and says exactly "Save".
  - POST → `hub_wiki_wanted_fulfil` → `row.fulfil(page)` → **200 with the re-rendered row (OOB) moving it into
    "Already Written"** + toast "Marked as written. Nice." (not 204 — §6.0).
  - Errors: an already-fulfilled row (stale tab) → 400 + toast "That request was already closed."; a page from
    another guild or an archived page → form error rendered in the modal, which stays open.
- **Editor section (leads and admins only)** — a single `<form method="post" class="hub-form">`:
  - `WikiWantedPageFormSet` built with **`extra=0`** (an `extra=1` blank row with a required `title` blocks
    Save — the exact bug Rule 11 exists to prevent), `can_delete=True`, prefix `wanted`.
  - `{{ wanted_formset.management_form }}` then `<div id="wanted-rows">` with one `hub-card` per row holding
    `form_field.html` for **Title** (required, hint "What should the page be called? Plain words are fine.")
    and **Note** (optional, hint "Anything the writer should cover."), the hidden `{{ f.id }}`, and:
    ```html
    {% if f.instance.pk %}
      <div style="display:none;">{{ f.DELETE }}</div>
      <button type="button" class="pl-btn pl-btn--danger pl-btn--sm" style="margin-top:0.75rem;"
              onclick="document.getElementById('{{ f.DELETE.id_for_label }}').checked = true; this.form.requestSubmit();">
        Delete this request
      </button>
    {% endif %}
    ```
    — a **real Delete button**, never a toggle, `margin-top:0.75rem` so it clears the field above, and it
    submits the whole form so no other edit on the page is lost.
  - `{% empty %}` → `<p class="hub-text-muted">No requests yet. Add your first.</p>`
  - A hidden `<template id="wanted-empty-template">` of `wanted_formset.empty_form` whose cloned row carries a
    **Remove** button (`onclick="this.closest('.hub-card').remove();"`) — an abandoned half-filled clone must
    never block Save.
  - **`+ Add A Wanted Page`** (`hub-btn hub-btn--sm`, `margin-top:1rem`) cloning that template, replacing
    `__prefix__` with the new index and bumping `id_wanted-TOTAL_FORMS`. Copied verbatim from the Links editor
    at `templates/hub/guild_edit.html:832-844`.
  - **`Save`** — `pl-btn pl-btn--primary`, the **last** element in the form, inside
    `<div style="margin-top:1.5rem;">` so it clears the rows above (Rule 18) and nothing sits under it
    (Rule 21). Label is exactly "Save".
- **Booleans:** there are none on this form. If one is ever added it goes through `components/toggle.html`; the
  formset's `DELETE` is the documented exception and stays hidden behind the button above.
- **Validation** (in `WikiWantedPageForm.clean_title`, not the view): title required, trimmed, 3–200 chars;
  a duplicate open title in the same scope errors with "You already have a request called 'Sharpening jigs'."
  Non-form errors render above the rows via `{{ wanted_formset.non_form_errors }}`.
- **States:** empty (above); **loading** — none, it is a plain form post; **error** — invalid formset
  re-renders the bound form in place with `messages.error(request, "Couldn't save — check the highlighted
  fields.")` so typed text is preserved (the `guild_mailing_list_save` pattern, not the `guild_links_save`
  redirect-and-lose pattern); **success** — `messages.success(request, "Wanted pages saved.")` and a redirect
  back to `?guild=<slug>`.
- **Permission:** GET is any member (they can claim); POST to the editor runs `_require_can_edit_guild` for the
  scoped guild, or `_require_admin` for the space-wide scope. Claim, self-Release, and Start POSTs need only an
  active member. **Release of someone else's claim** and **Mark As Written** run `can_verify_wiki_page`'s scope
  test for the row's guild (`can_edit_guild` for a guild row, `is_effective_staff` for a space-wide one) — the
  same test the lead panels use, so a lead never sees a control the view then refuses.

### 6.4 Overdue For Review (lead panel, on the tab)

- `.pl-wp-tab__panel`, heading **"Overdue For Review"**, hint "Past their check-up date. One tap says it's still
  right." with a `.pl-help` bubble spelling out the intervals ("Machines every 12 months, how-tos and materials
  every 24, guild info and reference every 12. Project pages never expire.").
- **What is in the list:** `is_out_of_date` pages in this guild **excluding any with `needs_review_since` set**
  (§5.5). A's `needs_review()` queryset is a superset that ORs in D's reported-page state; a reported page in
  this panel would offer "Still accurate" as its only verb, which is the wrong answer to "someone says this is
  wrong", and would duplicate D's `/wiki/review/` queue. Reported pages belong to D's surface.
- Up to 8 rows, oldest **`freshness_at`** first: title (link), kind, "last checked 14 Mar 2025" (from
  `last_checked_at`, with "never checked" when it is null), and **Still Accurate**
  (`pl-btn pl-btn--sm`, HTMX POST → **A's** `hub_wiki_confirm`, `hx-swap="outerHTML"` on the row).
  **This never opens an editor** — confirming must be cheaper than editing or nothing gets confirmed (brief §5.3).
- Beside it, a quiet **Edit** link for the case where it is *not* still accurate, so the panel is not a dead end.
- More than 8 → "See all 14 →" into `/wiki/search/?guild=<slug>&stale=1` (a required change to A, §2.2).
- **The panel is lead-only for *visibility*; A's endpoint is open to everyone.** `hub_wiki_confirm` admits any
  active member by design (A §5.2 — one-tap confirmation is the brief's answer to stale content and gating it
  to leads would kill it). The panel is behind `can_verify_wiki_page` because a work queue shown to people who
  cannot work it is noise, **not** because the action is privileged. Those are different questions and this
  spec previously conflated them.
- **Empty:** "Nothing overdue. Everything here has been checked recently." with a check glyph — a written
  empty state, never a blank region (brief §3).
- **Loading:** the tapped button disables (`hx-disabled-elt="this"`) and dims via `.pl-wp-tab__busy`.
- **Success:** **200** whose body is the replacement row carrying `hx-swap-oob` — a one-line
  "✓ Confirmed just now" that fades to nothing on the next page load — plus a toast "Thanks. Clock reset for
  12 months." (**Not 204**: a 204 has no body and cannot swap the row, so the row would sit there looking
  unconfirmed. Brief §9.2.)
- **Error:** the page vanished or was archived between render and tap → 404/400 + toast "That page is gone."
  and the row is removed. **There is no 403 state here** — an earlier draft specced one, and the test asserting
  it would have failed on day one: A's confirm endpoint is open to any active member, and every viewer of this
  panel is at least that.

### 6.5 Searches That Found Nothing (lead panel, on the tab)

- `.pl-wp-tab__misses`, heading **"Searches That Found Nothing"**, hint "What members looked for in
  {{ guild.name }} in the last 30 days and didn't find. This is your writing list."
- **Window: a rolling 30 days, not the calendar month.** "This month" means that on the 1st the panel shows a
  false all-clear — the lead most likely to open it is the one who just got the digest, on the exact day the
  panel is guaranteed empty. A rolling window is one argument's difference (`since=now - 30 days`) and never
  lies. The **digest** keeps the calendar month, because a monthly report of a month is what it is.
- Up to 5 rows: the query **in the member's own words**, a muted "4 people", and its actions:
  - **Start This Page** → `hub_wiki_new?guild=<slug>&title=<query>`
  - **Add To Wanted** (`pl-btn pl-btn--sm`, HTMX POST → `hub_wiki_wanted_request`, `bump=False` per
    decision 11) → toast "Added to Wanted pages." and an OOB refresh of the Wanted card so the lead sees it
    land.
  - **When this query already matches an open wanted row**, the button is replaced by the quiet link
    **"On the wanted list →"** pointing at `/wiki/wanted/?guild=<slug>` (decision 19). The rows are annotated
    against `open_titles_for_guild(guild)` in the context builder (§5.5), so this costs no extra query.
    Without it the row never changes after the lead acts: the button stays live, a second tap re-posts, and
    (before `bump=False`) each tap inflated "4 people asked for this" — the one number on this panel that is
    supposed to count *people*, per decision 12's whole point.
- **Empty:** "No failed searches in the last 30 days. Either everything is findable, or nobody looked." —
  honest, and it stops the panel reading as a broken feature on a quiet month.
- **Loading / success / error:** same button semantics as §6.4 — **200 + OOB fragment + toast**, never 204.
- Rows age out of the rolling window on their own; there is no Dismiss control, because a dismiss list is state
  nobody maintains.

### 6.6 The zero-result search screen — `templates/hub/partials/_wiki_search_empty.html`

A's `/wiki/search/` includes this partial when the result set is empty. B owns its content.

> **The screen is currently specified twice.** A §6.2 also describes a zero-result card ("Nothing matched
> *table saw*", an Ask-in-Discord button, a "+ Start This Page" button). Brief §9.1 rules that **B's partial
> wins**: A `{% include %}`s `hub/partials/_wiki_search_empty.html` and **deletes its own inline copy**. Two
> zero-result screens on one route is the sort of thing that ships as both, one behind a stale `{% if %}`.

- Headline: **"Nothing found for 'epoxy cure time'."** (the query, escaped, quoted).
- Line two, when the search was guild-scoped: "You searched **Woodworking** only." plus a real button
  **Search Everything** (a GET submit that drops `guild=`) — the one-tap escape.
- Then the two actions the brief names, side by side (stacked on mobile), both ≥48px:
  1. **Ask In #woodworking** — `pl-btn pl-btn--sm`, `href="https://discord.com/channels/<server_id>/<guild.discord_channel_id>"`,
     `target="_blank" rel="noopener"`, `hx-boost="false"`. Rendered **only** when both
     `SiteConfiguration.discord_server_id` and `guild.discord_channel_id` are set; when the guild has no channel
     id but the server id exists, it falls back to "Ask On Discord" pointing at the server; when neither is
     configured, the button is omitted entirely rather than rendering a broken link. The label uses
     `guild.announcement_channel_label`, which already falls back to "your guild's channel".
  2. **Request This Page** — `pl-btn pl-btn--primary pl-btn--sm`, HTMX POST to `hub_wiki_wanted_request` with
     the **exact query text as the title** and the scoped guild. Success toast: "Added to Wanted pages. Your
     guild's leads will see it." When `request(...)` bumped an existing row instead of creating one:
     "Already on the list — you're the 4th person to ask." (Telling someone they are the fourth is far better
     than a silent duplicate.)
- Third, quieter line: "Or **write it yourself** — rough is fine, someone will tidy it." linking to
  `hub_wiki_new?guild=<slug>&title=<query>`.
- **States:** this *is* an empty state; its own error case is the failed POST (toast, button re-enabled) and its
  success case is the toast above. The panel never disappears, so a member can take the second action too.

### 6.7 The monthly digest email

Templates `templates/membership/emails/wiki_guild_digest.html` and `.txt`, both extending the branded shell
(`membership/emails/_base.html` / `_footer.txt`). Inline styles are expected here (Rule 15's exception).

- **Subject — the rule, not an example.** The subject names the guild and the **single largest actionable
  count**, in this precedence: *overdue*, then *searches that found nothing*, then *new pages*. Exactly:

  | Condition | Subject |
  |---|---|
  | `overdue` non-empty | `"{guild} wiki: {n} page needs a look"` / `"{n} pages need a look"` |
  | else `misses` non-empty | `"{guild} wiki: {n} search found nothing"` / `"{n} searches found nothing"` |
  | else (`new_pages` only) | `"{guild} wiki: {n} new page"` / `"{n} new pages"` |

  Singular and plural are both written out; a subject reading "1 pages" is the kind of thing members notice and
  leads mention. There is no all-empty case — `guild_digest_payload` returns `None` and no email is sent. No
  time of day appears, so there is no subject/body timezone to disagree. Dates in the body render in the
  project timezone.
- **The subject noun is a link:** the guild's name in the opening line links to the guild page
  (`{% url 'hub_guild_detail' guild.slug %}`, absolutised). Never dead text.
- **One obvious primary CTA:** **Open the {{ guild.name }} wiki** → `/guilds/<slug>/?tab=wiki`.
- **Three sections, each skipped when empty** (never a section reading "0"):
  - *New this month* — up to 10, each title linking to its page, "by Sam · Machine".
  - *Overdue for review* — up to 10, each linking to `/wiki/p/<slug>/?confirm=1`, with the line
    "One tap on the page says it's still right." (decision 15). `?confirm=1` is a **required change to A**
    (§2.2); A knows nothing about the parameter today.
  - *Searches that found nothing* — up to 5, "epoxy cure time — 4 people", each with a
    "Start this page" link carrying the query as the title. This is the human-written content in the email:
    it is members' own words, surfaced verbatim.
- **Secondary links:** "Wanted pages (3 open)" → `/wiki/wanted/?guild=<slug>`, and "Start a page" →
  `/wiki/new/?guild=<slug>`. No dead ends.
- **Absolute URLs** everywhere, never a bare path. **`_absolute_url` is not "the spine's resolver"** — there is
  no shared resolver. It is a private four-line helper that exists **twice**, in `membership/orientations.py:52`
  and `membership/equipment.py:27`, and every other caller in the repo lazily imports the orientations one
  (`membership/models.py`, `membership/voting.py`, `hub/` — a dozen sites, all
  `from membership.orientations import _absolute_url`). **Use that exact import** in
  `membership/wiki_guild.py`:

  ```python
  from membership.orientations import _absolute_url   # the one every other caller uses
  ```

  Named precisely so nobody writes a third copy. Do not "tidy" the duplication as part of this spec.
- **`.txt` mirrors the `.html` section for section**, same links, same counts — change one, change the other.
- No BETA badge; the shared shell already handles branding.

### 6.8 Dark and light

Every new colour is a token: `--hub-card-bg`, `--hub-surface`, `--hub-text`, `--hub-text-muted`, `--hub-border`,
`--color-tuscan-yellow` for the primary accent, `--hub-blue` for focus rings. **`--surface` is not a token** and
appears nowhere. The search input lives inside `.hub-form-group`, which already supplies theme-correct
`--hub-input-bg` / `--hub-input-border`; the guild `<select>` on `/wiki/wanted/` additionally styles
`select option { background; color }` because native option popups do not inherit. There are no date or time
inputs in this spec, so Rules 14 and 20 do not arise. Panel backgrounds use `--hub-surface` (one step down the
elevation ladder from `--hub-card-bg`) so a panel inside a card reads as inset in both themes rather than as a
white box on dark. **Verify both themes** on: the tab's grouped list, the verified credit line, all three lead
panels, the wanted editor rows, and the zero-result block.

**The "Guild verified" pill needs scoped light-theme overrides in B's containers too.** `.hub-pill--ok` is
`color: #6ee7b7` (`hub.css:448`), a dark-theme green that is too pale to read on a light card — the repo has
already worked around it four times (`[data-theme="light"] .pl-invite-list .hub-pill--ok` at `hub.css:584`,
`.pl-automation-card` at `:688`, `.pl-map-list` / `.pl-map-detail` at `:5901`/`:5904`, and
`member-edit.css:204`), all setting `color: #1f7a52`. **A documents this and scopes its fix to A's own
containers** (`.pl-wp-card`, the page header). B renders the same pill in three containers A's rule does not
reach — `.pl-wp-tab__row`, `.pl-wp-tab__wanted-row`, and `.pl-wp-tab__panel` — so without B's own rule the tab
ships a light-theme pill nobody can read. B adds the same scoped override for its three (the exact CSS is in
§6.9). **Do not change the shared `.hub-pill--ok` modifier** — that would move five existing surfaces.

### 6.9 New CSS (all `pl-wp-tab__`, all in `static/css/hub.css`)

**Namespace, per brief §9.1 and decision 16.** B's first draft claimed generic bare names — `.pl-wp-empty`,
`.pl-wp-row`, `.pl-wp-panel`, `.pl-wp-grid`, `.pl-wp-search`. That was safe against `main` and unsafe against
the round: **A occupies bare `pl-wp-*` heavily** (`pl-wp-card`, `pl-wp-chip`, `pl-wp-body`, `pl-wp-toc`,
`pl-wp-facts`, `pl-wp-result`, `pl-wp-actionbar`, `pl-wp-breadcrumbs`, `pl-wp-related`, …) and **D adds
`pl-wp-empty`, `pl-wp-chip`, `pl-wp-actions`, `pl-wp-banner*`** — a direct collision on `.pl-wp-empty` between
B and D, and on `.pl-wp-chip` between A and D, across three PRs that merge within days of each other. A owns
bare `pl-wp-*`; **B owns `pl-wp-tab__*` and nothing else**; D owns `pl-wp-mod__*`; E keeps `pl-gov-*`.

**Grep evidence and its shelf life.** `grep -rn "pl-wp-" static/ templates/` → no matches, and
`grep -n "pl-wiki" static/css/hub.css` → only `.pl-wiki-toc` / `.pl-wiki-article` at lines 3435–3442 (Help
Center). Both were run against `main` on 2026-09-07, **before A existed**. **Re-run both against A's merged
branch immediately before writing this CSS block**, and treat any hit inside `pl-wp-tab__` as a blocker. B adds
no `pl-wiki-*` class and no status-chip class (A's card partial owns the pill).

```
.pl-wp-tab__grid  .pl-wp-tab__grid--lead
.pl-wp-tab__search  .pl-wp-tab__scope  .pl-wp-tab__scope-chip  .pl-wp-tab__scope-chip--active
.pl-wp-tab__group  .pl-wp-tab__group-title  .pl-wp-tab__group-more
.pl-wp-tab__row  .pl-wp-tab__row-title  .pl-wp-tab__row-meta  .pl-wp-tab__row-actions
.pl-wp-tab__recent  .pl-wp-tab__recent-row
.pl-wp-tab__panel  .pl-wp-tab__panel-title  .pl-wp-tab__panel-empty  .pl-wp-tab__panel-more
.pl-wp-tab__wanted  .pl-wp-tab__wanted-row  .pl-wp-tab__wanted-count
.pl-wp-tab__misses  .pl-wp-tab__miss  .pl-wp-tab__miss-people
.pl-wp-tab__verify  .pl-wp-tab__verify-credit  .pl-wp-tab__verify-note
.pl-wp-tab__empty  .pl-wp-tab__busy
```

**Two scoped light-theme overrides are required** (see §6.8): `.hub-pill--ok`'s dark green `#6ee7b7`
(`hub.css:448`) is unreadable on a light card, and A only scopes its fix to A's own containers. B renders the
same pill in three of its own.

```css
[data-theme="light"] .pl-wp-tab__row .hub-pill--ok,
[data-theme="light"] .pl-wp-tab__wanted-row .hub-pill--ok,
[data-theme="light"] .pl-wp-tab__panel .hub-pill--ok { color: #1f7a52; }
```

Spacing on the 8px grid throughout (`0.5 / 0.75 / 1 / 1.5rem`). `display` for `.pl-wp-tab__grid`, the ≤900px
column `order:` under `.pl-wp-tab__grid--lead` (§5.5), and every flex row live in these classes, never in an
inline `style` on an `x-show` element (Rule 12).

### 6.10 Surviving body swaps

`hub/base.html` boosts the whole body and re-runs `Alpine.initTree` on `htmx:afterSettle`. Every Alpine root in
B's templates is declared with `x-data` on the element that needs it (the collapsed "Already Written" section,
the verify-note modal trigger); no global JS state, no `DOMContentLoaded` handler, no per-field inline
`<script>` inside a cloned formset row (Rule 16's delegation warning — B's wanted rows carry no file inputs, so
the delegated-drop-zone machinery is not needed, and the "+ Add" clone is pure `innerHTML` with no scripts).

---

## 7. Notifications / emails / activity

| Item | Detail |
|---|---|
| **New event** | `wiki.guild_digest_monthly` in `core/events/registry.py`: label "Monthly guild wiki digest", description "A monthly summary of your guild's wiki: new pages, pages due a check, and what members searched for and didn't find.", `category="Guilds"`, `recipient=Recipients.GUILD_LEADERSHIP`, `channels=(_IN_APP_ON, _EMAIL_ON)`, `activity_kind=None`, `email_shell="light"`. Push is offered but defaults OFF (it is not in `_PUSH_ON_BY_DEFAULT` and a monthly digest should not buzz a phone). A lead can opt the email off in settings; it is not forced (it is a summary, not a transaction). |
| **Where it actually renders in settings** | **Not under "Guilds", whatever `category` says.** `Recipients.GUILD_LEADERSHIP` is a member of `settings_matrix.STAFF_RECIPIENTS` (`core/events/settings_matrix.py:92`), and `_section_for()` (`:158`) returns `STAFF_SECTION` for every event whose recipient is in that set — the `category` is used only for the email's `X-Category` header. So the row lands in the **staff section**, gated behind `_is_staff_or_leadership`, alongside every other leadership alert. That is the correct home for it (only leadership receives it), so nothing changes except this spec's claim: **do not "fix" the section by moving `category`, and do not add `GUILD_LEADERSHIP` to or from `STAFF_RECIPIENTS`.** Keep `category="Guilds"` for the header, and expect the checkbox under the staff heading. |
| **In-app copy** | `in_app_title = f"{guild.name} wiki digest"`. `in_app_body` mirrors the subject's precedence (§6.7) in one plain sentence: `"3 pages need a look, 2 searches found nothing, and 1 new page went up."` — sections with a zero count are **omitted from the sentence**, not rendered as "0", and the sentence never ends up empty because an all-empty payload sends nothing at all. Singular and plural are written out. `url = f"/guilds/{guild.slug}/?tab=wiki"`, the same destination as the email's primary CTA, so the bell and the inbox agree. |
| **Audience correctness** | `GUILD_LEADERSHIP` resolves `guild.leadership_members()` = lead + every staff row, orienters included, deduped — exactly the people who can act on all three sections. It is **emitted once per guild** with `context={"guild": guild}`; the site-wide `ALL_GUILD_LEADS` resolver would be wrong here, since each lead needs their own guild's numbers. |
| **Dedupe** | `period=f"wiki-digest:{guild.pk}:{now:%Y-%m}"`. `EventDelivery` then makes a re-run, a manual "Run now", and a retry all idempotent — the second run delivers nothing. |
| **Send path** | `emit_with_email_shell("wiki.guild_digest_monthly", context={"guild": guild}, subject=…, text_template="membership/emails/wiki_guild_digest.txt", html_template="membership/emails/wiki_guild_digest.html", template_context=payload, in_app_title=…, in_app_body=…, url=f"/guilds/{slug}/?tab=wiki", period=…)`. |
| **Existing event B calls** | `wiki.page_verified` on every successful verify — the retention mechanism (brief §5.8). **D owns it**, not A (§9.1; A §7 disclaims all spine events): D registers the `Trigger`, writes the `wiki_page_contributors` resolver and the copy, and fixes the `period`. B calls `emit()` in D's shape (§5.2) and passes `_role_label(...)`'s output as `verifier_role`. **B registers no fallback `EventType`** — the `Recipients.SINGLE_USER` fallback in B's earlier draft is deleted, because B and D land within days of each other and two registrations of one key is a collision, not a safety net. |
| **Activity** | `SiteActivity.Kind.WIKI_PAGE_VERIFIED` on verify and on remove (`payload={"removed": True}`). **The enum member ships in A's A1 migration** (§9.1) — B adds none. No activity row for a claim, a wanted-page fulfilment, a wanted-page edit, or a search miss — those are not audit events and would drown the feed. |
| **The job** | New `ScheduledJob(key="send_wiki_guild_digest", name="Guild wiki digest", description="Emails each guild's leadership a monthly summary of their wiki, and prunes old search-miss rows.", command="send_wiki_guild_digest", schedule_label="Daily (1st of the month)", cadence=Cadence.DAILY)` in `core/scheduled_jobs.py`. `Cadence.DAILY` means the existing `run_scheduled_tasks` dispatcher fires it once a day when UTC hour == 13 (~6 AM PT) — **no new cron service, no Render change.** The command itself gates the digest on `timezone.localdate().day == 1` and runs the 90-day miss purge on **every** invocation, so a mid-month manual run is always safe and never sends anything. A `--force` flag exists for a shell run against a specific month; the dashboard's "Run now" never passes it. |

---

## 8. Build order (phased; each phase ships green: full suite + `ruff check .` + `ruff format .` + `mypy .` + `manage.py check`)

**Depends on A.** Phase 1 can be written against A's merged models the day A lands; nothing here should start
before `WikiPage` exists.

1. **Data and logic, no UI.** `WikiWantedPage` + `WikiSearchMiss` + their querysets; the
   `WikiPage.verified_role_label` column; migration `0168_wiki_guild_tab` (additive, reversible);
   `can_verify_wiki_page` (if A did not ship it); `WikiPage.verify` / `.unverify` / `_role_label`;
   `WikiWantedPage.objects.request` / `.claim` / `.release` / `.fulfil`;
   `WikiSearchMiss.objects.record` / `.top_for_guild`; `SiteActivity.Kind.WIKI_PAGE_VERIFIED` if D has not
   added it. Full model + permission specs. **`manage.py check` here** — the index names are new.
2. **The Wiki tab and the Verify control.** `guild_wiki_tab_context`; the three edits to `guild_detail.html`;
   `_guild_wiki_tab.html` (search, scope chips, grouped list, Recently Updated, empty state);
   `_wiki_verify_control.html` in both homes; `hub_wiki_verify` + its URL; the `pl-wp-tab__` CSS. Ships a usable,
   member-facing tab with working one-tap verification.
3. **Wanted pages and the zero-result screen.** `WikiWantedPageForm` / `FormSet` / `WikiWantedFulfilForm`;
   `hub_wiki_wanted` (+ editor save), `hub_wiki_wanted_claim`, `hub_wiki_wanted_fulfil`,
   `hub_wiki_wanted_request` + URLs; `wiki_wanted.html`; the tab's Wanted card; **Mark As Written**
   (decision 17) and the lead's **Release** on a stale claim (decision 18); `_wiki_search_empty.html`; the
   one-line `record(...)` call in A's search view. **The list-editor phase** — review it against checklist §1
   before opening the PR. This phase is also where the wanted loop actually closes: do not ship it with
   `fulfil()` still callerless.
4. **The lead panels.** Overdue For Review (calling A's `hub_wiki_confirm`) and Searches That Found Nothing,
   both on the tab, both behind `can_verify_wiki_page`.
5. **The monthly digest.** The `EventType`; `guild_digest_payload`; `send_wiki_guild_digest` (digest on the 1st,
   purge every run, `--force`); the `ScheduledJob` row; `wiki_guild_digest.html` / `.txt`.
6. **Housekeeping.** Bump `plfog/version.py` `VERSION` — **every PR in the round bumps `VERSION` and adds
   NO changelog entry, except the single last PR of the whole round, which adds the one entry.** This is
   brief §9.1's ruling and it **supersedes brief §5.9 and CLAUDE.md's "first PR adds the entry, later PRs
   re-stamp it"**. Re-stamping is exactly what re-posts to Discord (`project_plfog_discord_autoannounce`: the
   workflow fires automatically on a push to main that changes `VERSION`, and posts every entry stamped at the
   new `VERSION`), so the old rule would have announced the wiki six times — once per PR in this spec alone,
   and a dozen times across A, B, D and E. **B's PRs 1 through 5 add nothing to `CHANGELOG`.** A bump with no
   entry announces nothing, which is correct.

   Whether B's last PR is the round's last PR depends on merge order with D and E; the round's final merger
   writes the entry. Bullets to fold into it when that PR is B's, with no dashes anywhere:

   > **The makerspace wiki**
   > - Every guild page now has a Wiki tab with that guild's pages, grouped and searchable.
   > - Guild leads, guild staff, and orienters can mark a page verified in one tap, so you can tell what someone with authority has read.
   > - Pages that are due a check show up in a list for your guild's leads, who can confirm a page is still right without opening it.
   > - Guilds can post a list of pages they want written. Claim one, or just start it.
   > - Searched for something and found nothing? You can ask in your guild's Discord channel or request the page right there.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py` with `describe_*` / `it_*` and factory-boy, in `tests/hub/` and `tests/membership/` (the house
location; `describe_*` for every nested block — `context_*` is **not** a collected prefix and silently skips).
New factories: `WikiWantedPageFactory`, `WikiSearchMissFactory` (plus A's `WikiPageFactory`). Coverage gate is
the repo's; run `manage.py check` after phase 1. E2E stays on Postgres 5433, not local SQLite.

**`tests/membership/wiki_verify_spec.py`** — the permission matrix, one `it_` per row:

| Actor | Guild-scoped page | Space-wide page |
|---|---|---|
| The guild's `guild_lead` | may verify | no |
| `GuildStaffMembership(role=CO_LEAD)` | may verify | no |
| **`GuildStaffMembership(role=ORIENTER)`** | **may verify** (the brief's headline case) | no |
| `GuildStaffMembership(custom_title="Studio Technician")` | may verify | no |
| Lead of a *different* guild | no | no |
| Plain active member | no | no |
| FOG admin | yes | yes |
| Guild officer | yes | yes |
| Admin with `view_as` = member | **no** (preview honored) | no |
| Anyone, page is `OFFICIAL` | **no** (control absent, POST 403) | no |

Plus, one `it_` each, all against **A's real field names**:

- verify sets `status` / `verified_by` / `verified_at` / `verified_note` / `verified_role_label`;
- verify **also stamps `last_checked_at` AND `last_checked_by`** (assert both — writing only the timestamp
  leaves A's byline reading "Last checked by <the previous person>" beside a fresh date);
- verify **clears `unverified_reason`** — build a page that was edited down to Community with
  "Edited since it was verified.", verify it, assert the string is gone (otherwise the page renders a green
  check above a line contradicting it);
- verify **clears `needs_review_since` and `needs_review_reason`**, and therefore `status_pill` returns the
  Guild-verified pill and not the amber Needs-review one (A's precedence puts needs-review on top, so this
  test is the difference between the button working and appearing to do nothing);
- verify **writes no `WikiRevision`** — assert `page.revisions.count()` is unchanged. **This is the one place
  B deliberately contradicts A's §7 handoff**, and the test is the guard: a verification revision would enrol
  every verifier in D's `wiki_page_contributors` audience forever;
- `verified_role_label` reads "Woodworking orienter" for an orienter, "Woodworking lead" for the lead, "Admin"
  for an admin; the label **does not change** after the verifier's staff row is deleted (the whole point of
  denormalizing);
- a second verify within 60 seconds by the same member is a no-op;
- `unverify` clears the four verification fields and drops to Community **without resetting the clock and
  without setting `needs_review_since`**;
- verify emits `wiki.page_verified` exactly once, with `verifier_role` equal to `verified_role_label`, and logs
  `WIKI_PAGE_VERIFIED`; unverify logs with `removed=True` and emits nothing.

**`tests/hub/guild_wiki_tab_spec.py`** — the tab renders for a plain member of the guild (grouped list, no lead
panels); renders for a lead (all three panels present); the tab button and pane are **absent** when the flag is
off, when `request.surface == "guilds"`, and for an anonymous visitor; `?tab=wiki` is mapped in `x-init` only
when the tab exists (assert the string is absent from the rendered HTML when the flag is off — a stray mapping
would blank every pane); groups appear in the fixed kind order with empty groups omitted; a group over 12 rows
renders "See all"; the empty state renders both its buttons; Recently Updated shows 5 rows newest first;
**a page carrying `needs_review_since` is absent from the Overdue panel even when it is past its interval**
(the D-queue leak); **for a lead at ≤900px the lead panels precede the grouped list in the DOM/CSS order and
the ordering comes from a class, not an inline `style`** (assert `pl-wp-tab__grid--lead` is present and that
no `style="` containing `order` or `display` appears on the pane element — Rule 12);
`assertNumQueries` pins the budget at 2 for a member and 4 for a lead (the N+1 guard — the tab must not fire
one query per row or per guild).

**`tests/hub/wiki_verify_view_spec.py`** — one-tap POST returns **200 whose body carries `hx-swap-oob`** plus
the toast header for HTMX (assert the body is non-empty and contains `hx-swap-oob` — a 204 would pass a
naive "did it toast" assertion while leaving the chip stale, which is the exact bug brief §9.2 names), and
redirects with a message for a plain post; the note modal's POST stores the note; `remove=1` unverifies; a
non-verifier gets 403; the compact tab-row control renders the button only for verifiers and only on
unverified, non-Official pages.

**`tests/hub/wiki_overdue_spec.py`** — a page past its kind's interval appears in the panel and one past the
interval for a *different* kind does not; `PROJECT` pages never appear; a page with `needs_review_since` set
**never appears**, however stale (it belongs to D's queue); **"Still accurate" resets the clock**
(assert **`last_checked_at`** moves — there is no `last_confirmed_at` — and the row leaves the list on reload)
**without creating a revision and without opening an editor** (assert the response is **200 + OOB**, not a
redirect to `/edit/`); the empty state renders its written copy. **No 403 test** — A's `hub_wiki_confirm` is
open to any active member by design, so an "a non-verifier gets 403" test would fail on day one; assert
instead that **a plain member POSTing to the confirm endpoint succeeds**, and that the *panel* is absent from
their rendered tab.

**`tests/membership/wiki_wanted_spec.py`** — `request(...)` creates once and **bumps `request_count` on the
second identical ask** rather than creating a twin; **`request(..., bump=False)` does not bump** (the lead
path); normalization matches across case and extra whitespace; a
*fulfilled* row with the same title does not block a new request (the partial unique constraint); `claim` sets
`claimed_by`/`claimed_at`; claiming a fulfilled row raises; `release` clears both; `fulfil` links the page and
credits the author as claimer when nobody had claimed it, and raises on an already-fulfilled row and on an
archived page; `is_claim_stale` flips at 30 days.

**`tests/hub/wiki_wanted_view_spec.py`** — the editor renders `extra=0` (no blank row), the "+ Add a wanted
page" template and its `TOTAL_FORMS` id, and a per-row Delete **button** (assert `pl-btn--danger` and
`margin-top:0.75rem`, and assert the DELETE input is **hidden**, not a toggle — the famous failure); Save
persists and redirects with a success message; an invalid row re-renders bound with the typed text intact; a
plain member gets the read list with Claim but **no editor form**; a lead of another guild gets 403 on POST;
Claim/Release round-trip via HTMX returns a toast; the guild switcher renders a real **Go** submit button
(assert a `type="submit"` inside the switcher form, so the control still works with JS off).

**`tests/hub/wiki_wanted_fulfil_spec.py`** (decision 17, the loop that was open) — **Mark As Written renders
for a lead and not for a plain member**; its POST calls `fulfil()`, returns **200 + OOB + toast**, and the row
moves into the "Already Written" section on the next render (assert the section is non-empty — as previously
specced it could never be, because `fulfil()` had no reachable caller); a second POST on the closed row
answers 400 with the "already closed" toast, not a 500; a page from another guild is a form error and the row
stays open; **a plain member POSTing directly gets 403**.

**`tests/hub/wiki_wanted_release_spec.py`** (decision 18) — a row claimed by Sam renders **Release for a guild
lead** behind `confirm_modal.html` (assert the confirm id and the "Nothing is deleted." sentence) and renders
it **without** the confirm for the claimer themselves; a lead's Release clears `claimed_by`/`claimed_at`; a
different plain member gets 403; a stale claim (over 30 days) renders the "claimed 5 weeks ago" line beside
the control, so the hint and the lever are on the same row.

**`tests/membership/wiki_search_miss_spec.py`** — a miss is written **only** on a zero-result search (assert
nothing is written when results exist); not for a 2-character query, not for a 200-character one, not for an
anonymous request; **the same member searching the same thing twice in a day writes one row**, and a different
member writes a second; `top_for_guild` groups case-insensitively, counts people, returns at most 10 ordered by
count; the 90-day purge deletes old rows and keeps recent ones. Plus the panel's window: **on the 1st of a
month, a miss recorded three days earlier still appears** (the rolling-30-day rule — the calendar-month version
showed a false all-clear on exactly the day the digest sends).

**`tests/hub/wiki_misses_panel_spec.py`** (decision 19) — a miss whose normalized query matches an **open**
wanted row for that guild renders **"On the wanted list →"** and **no** Add To Wanted button; a miss with no
match renders the button; a miss matching a **fulfilled** row renders the button again (the ask is live once
more); the lead's Add To Wanted POST **does not increment `request_count`** on an existing row (assert the
integer before and after — this is the number the panel calls "4 people asked for this").

**`tests/hub/wiki_search_empty_spec.py`** — the zero-result partial shows "Search everything" only when the
search was scoped; the Discord button renders with the real channel deep link when both ids are set, falls back
to the server link with only a server id, and is **absent** with neither; "Request this page" posts the exact
typed query as the title and toasts the "you're the 4th person" variant on a bump.

**`tests/membership/wiki_digest_spec.py`** — the payload is `None` (and therefore no email) when all three
sections are empty; a guild with only overdue pages still sends; **the digest emits once per period** (run the
command twice for the same month and assert exactly one delivery per lead, via `EventDelivery`); every guild
gets its **own** numbers (two guilds, disjoint content, no cross-contamination); recipients are the lead **and
every staff role including an orienter**; the command is a no-op on the 2nd of the month but still purges;
`--force` sends off-cycle; the `.txt` and `.html` bodies both contain the guild page link, the `?tab=wiki` CTA,
and every overdue page's `?confirm=1` URL, all absolute (assert `https://` and no bare `/wiki/`). Plus:
**a page carrying `needs_review_since` is excluded from the `overdue` section** (same rule as the panel);
the **subject follows §6.7's precedence** — overdue over misses over new pages — and reads "1 page needs a
look", not "1 pages"; and `_section_for(EVENTS["wiki.guild_digest_monthly"])` returns **`STAFF_SECTION`**, not
`"Guilds"`, because `GUILD_LEADERSHIP` is in `STAFF_RECIPIENTS` — assert the real behaviour so nobody later
"fixes" the category to move a row that is already in the right place.

**Template hygiene:** `tests/template_comment_lint_spec.py` must pass (no multi-line `{# #}` in the new
templates).

**Timezone gotcha:** the digest's month window is computed in the project timezone
(`timezone.localdate()`), not UTC. A run at 13:00 UTC on the 1st is 06:00 PT on the 1st, so the window is
"the previous calendar month, Portland time". The specs freeze time with the project TZ active and include a
case at 23:30 PT on the last day of a month to prove the boundary does not slip a day.

## 10. Open / deferred

- **A "verified by" filter on search.** Obvious and cheap once A's search takes facets, but it is A's surface
  and nobody has asked. Deferred.
- **Per-guild wiki digest cadence (weekly / off).** One `SiteConfiguration`-style toggle per guild would be
  easy; monthly-for-everyone with a per-lead settings opt-out is enough until a lead complains. Deferred.
- **Auto-releasing stale claims.** Decided against (decision 10's rationale): a background job that
  un-assigns a member's work reads as a rebuke. Revisit only if the wanted list actually clogs.
- **Nudging individual authors about their stale pages.** Explicitly forbidden by the brief ("nudge leads,
  never authors"). Not deferred — rejected.
- **A cross-guild "everything overdue" admin view.** Plausibly useful for officers; not in this round.
- **Counting *views* per page** to rank what matters. A different feature (analytics), and the failed-search
  panel already answers the question that has an action attached to it.
- **Equipment-owned orienters** (`EquipmentStaffMembership`) who are *not* guild staff currently cannot verify
  a machine page. This is deliberate for now: the brief's verify tier is the guild's, and every tool's page is
  scoped to a guild. If it bites, the fix is one `or can_manage_equipment(request, page.equipment)` clause in
  `can_verify_wiki_page` once A ships the page↔equipment link — noted here so the next person does not have to
  rediscover it.
