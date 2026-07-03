# Org-Info Page ("Space & Org Info") — Spec & Implementation Plan

**Status: Spec only — not yet approved to build.**
**Date:** 2026-07-03
**Release line:** `release-0.20.x` (PR #118 is the UAT batch). See §11 for a recommendation that this ships as **its own PR/release**, not folded into #118.
**Source:** FOG UAT feedback items **#15** (org-info page: map, who's-who, who-to-contact, code of conduct; replace the "Member Guild" link) and **#14** (physical map: guilds, restrooms, exits — v1 = annotated floorplan image), per `docs/superpowers/plans/2026-07-03-qa-uat-response.md` (Spec B).
**Related:** `2026-06-08-guild-pages-redesign.md` (the content machinery this reuses), `2026-07-01-guest-guilds-surface.md` (the public front-door surface this page should also be exposable on — designed here so that is not precluded).

---

## 1. Summary

Members told us in UAT that there is no single, obvious place to learn how the *space and the organization* work: where each guild lives physically, where the restrooms and emergency exits are, where to park, who runs what, who to email about X, and the code of conduct. Today that information is scattered across two external Google Docs buried in the sidebar footer ("Member Guide" and "Code of Conduct" — `templates/hub/base.html:262-292`) plus a prod `Guild` row informally used as "Member Guild." QA wants those replaced by **one prominent, in-app organizational-info page** — a "guild page for the whole org."

This spec builds a single **Space & Org Info** page: a prominent nav item that opens a page shaped like a guild page (hero, rich-text sections, FAQ, links) but centered on org-wide content, headlined by an **annotated floorplan image** (the "map" — guild locations, restrooms, emergency exits) with click-to-zoom. It consolidates the two Google-Doc links into native page content and gives the whole thing an intuitive home in the member nav.

The page's ingredients already exist and are proven on the guild detail page — this build is largely **assembly + one small floorplan field + a lean content model**, not net-new UI invention.

### Locked design decisions (this spec's recommendations)

| Decision | Choice | Rationale |
|---|---|---|
| **Model approach** | **(b) Dedicated `OrgInfoPage` singleton + purpose-built view/template** — **NOT** an `is_org_info` flag on `Guild`. | Reuse the *ingredients* (components, sub-model patterns) without reusing the *entity*. A magic guild-that-isn't-a-guild pollutes voting/funding/directory/roster/sidebar querysets (correctness hazard, §4.1). |
| **Physical map v1** | A single uploaded **annotated floorplan image** (`floorplan_image`) shown prominently, **click-to-zoom** reusing the existing gallery lightbox. | "Keep it simple." No interactive/clickable-hotspot map in v1 (§4.4, §10). |
| **Content authoring** | Reuse `about`-style **rich-text (Markdown) sections** + the **FAQ** and **Links** editor patterns from `guild_edit.html`. No bespoke forms. | FRONTEND.md and the QA brief both say reuse over bespoke. |
| **Who's-who / who-to-contact** | Authored as a Markdown **contact section** + **FAQ items** in v1; a structured `OrgContact` child model is an **optional** fast-follow (§4.3). | Minimizes new bespoke forms per the brief; markdown covers v1. |
| **Nav** | A prominent top-level sidebar item **"Space & Org Info"** placed high in the member nav (below Community Calendar), and the two Google-Doc footer links **removed/folded in**. | QA: "somewhere more prominent so members intuitively find this info," replacing the Member Guild link. |
| **Access** | Page is **public-read** (view, not `@login_required`, mirroring `guild_detail`); **editing is admin-only** (`_require_admin`, `view_as`-aware). | Enables the guest-surface synergy (§8) and matches the org-wide (not guild-scoped) nature of the content. |
| **"Replace Member Guild" mechanics** | **OPEN QUESTION for Josh** (§7.3) — depends on whether prod "Member Guild" is a real `Guild` row (it is, per QA). |  |

---

## 2. What already exists (reuse, don't reinvent)

Confirmed in the tree this session (line numbers may drift):

| Need | Existing thing | Location |
|---|---|---|
| The whole "guild page" content pattern to mirror | `guild_detail` view + template — hero, rich-text `about`, FAQ, links, gallery, meeting notes, video embeds, staff cards | `hub/views.py:398-506`; `templates/hub/guild_detail.html` |
| **Hero banner + crop/adjust** mixin | `HeroCropMixin` on `Guild`; `get_hero_image_field_name()`; the `heroPlacement` Alpine adjuster + `hub_hero_adjust` endpoint | `membership/models.py:820,852`; `guild_detail.html:15-92` |
| **Gallery grid + click-to-zoom lightbox** (the map's zoom) | `_guild_gallery.html` (`x-data="{ open, src }"` lightbox) + `.pl-guild-gallery` / `.pl-guild-lightbox` CSS | `templates/hub/_guild_gallery.html`; `static/css/hub.css:2869,2872-2873` |
| **FAQ model w/ rich answer + YouTube + PDF/doc-or-link** | `GuildFAQItem` (`answer`, `video_url`, `document` XOR `document_url`, `has_document`/`document_href`/`document_display_name`, check-constraint) | `membership/models.py:1228-1288` |
| **Links model** | `GuildLink` (`label`, `url`, `sort_order`) | `membership/models.py:1291-1303` |
| **Gallery-image model + save patterns** | `GuildImage` (`validate_image_size`, `normalize_field_if_uploaded`, `delete_orphan_on_replace`, `sort_order`) | `membership/models.py:1205-1225` |
| **Markdown render + template filter** | `render_markdown()`; `{{ x|guild_markdown }}` filter; `.pl-md` render class | `membership/markdown.py:50`; `membership/templatetags/membership_md.py:13`; used at `guild_detail.html:350` |
| **YouTube embed id filter** | `youtube_embed_id` | `classes/templatetags/classes_tags.py` (used `guild_detail.html:171,320`) |
| **FAQ/Links editor idiom** (`extra=0` + "+ Add", real Delete buttons, own save endpoint outside the main form) | `guild_edit.html` "FAQ & Links" tab + `guild_faq_save` / `guild_links_save` views + `GuildFAQItemFormSet`/`GuildLinkFormSet` | `guild_edit.html:305-435`; `hub/views.py:1701,1724`; `hub/forms.py:527,573` |
| **Image-delete endpoint pattern** | `guild_image_delete` (editor-gated, orphan-cleanup) | `hub/views.py:1521` |
| **Singleton model pattern** (`load()`, forced `pk=1`) | `SiteConfiguration` | `core/models.py:100,212-221` |
| **Admin-only gate** (`view_as`-aware) | `_require_admin` / `_viewing_as_admin` | `hub/views.py:528-542` |
| **Sidebar nav slot pattern + resources footer to replace** | member nav links (Class Catalog … Member Directory … My Tab) + the two Google-Doc footer links | `base.html:171-229` (nav); `base.html:262-292` (Member Guide + Code of Conduct) |
| **Validators + image-size settings** | `validate_image_size`, `validate_document`, `IMAGE_MAX_LONG_EDGE_HERO/GALLERY` | `core/validators.py`; `plfog/settings.py:242-243` |
| **Guest-surface plumbing** (for §8) | `SurfaceMiddleware`, `guilds_page_base`, `GUILDS_ALLOWED_VIEW_NAMES` allowlist | `core/middleware.py`; `2026-07-01-guest-guilds-surface.md` §6.0 |

### Genuine gaps to close

1. **No org-wide content container.** Every content model FKs to `Guild`; there is no home for org-wide info. → new singleton `OrgInfoPage` + two child models that *mirror* (do not reuse) `GuildFAQItem`/`GuildLink`.
2. **No floorplan/map asset or field anywhere in the repo** (confirmed — `grep` for floorplan/map returns nothing). → new `floorplan_image` field + a client-supplied annotated floorplan image (content, not code).
3. **No org-info nav slot; org info lives in two external Google Docs.** → new prominent sidebar item + fold the two footer links into native content.
4. **No page_header/intro component yet on org pages.** QA **Build C4** (same batch) introduces a reusable `page_header` intro-blurb component; this page should adopt it once it lands (§6.1). If C4 hasn't merged, use a plain intro card and swap later.

---

## 3. Where the code lives

```
membership/
  models.py                # + OrgInfoPage (singleton), OrgFAQItem, OrgLink  (+ optional OrgContact)
  migrations/00NN_org_info_page.py     # AddModel x3 (reverse = auto DeleteModel; no data migration)
  spec/models/org_info_spec.py         # (or tests/membership/org_info_models_spec.py per repo convention)
tests/membership/
  factories.py             # + OrgInfoPageFactory, OrgFAQItemFactory, OrgLinkFactory
hub/
  views.py                 # + org_info (public read) ; org_info_edit (admin) ;
                           #   org_info_faq_save ; org_info_links_save ; org_info_floorplan_delete
  urls.py                  # + /info/ routes (see §7.1)
  forms.py                 # + OrgInfoPageForm ; OrgFAQItemFormSet ; OrgLinkFormSet  (mirror guild forms)
templates/hub/
  org_info.html            # NEW read page (surface-aware base — §8)
  org_info_edit.html       # NEW admin editor (tabbed, mirrors guild_edit.html)
  _org_floorplan.html      # NEW map + click-to-zoom partial (mirrors _guild_gallery.html lightbox)
  base.html                # nav slot added; the two Google-Doc footer links removed/folded
static/css/hub.css         # + .pl-org-* (map figure, contact matrix); reuse .pl-guild-* + lightbox
plfog/version.py           # bump VERSION + one member-facing CHANGELOG entry (last)
```

Home apps: **`membership`** (models), **`hub`** (views/templates/nav). No new app.

---

## 4. Data model

### 4.1 Why NOT option (a) — the `is_org_info` flag on `Guild`

Option (a) is genuinely tempting: a Guild row already renders every ingredient, `guild_edit.html` already authors them, and QA's "Member Guild" is *already* a Guild row — flip a flag and hide the guild-only blocks. But the cost is not one template `{% if %}`; it is **query pollution across the app**, and that is a correctness hazard, not cosmetics. A `Guild` is enumerated in many places that would each need `.exclude(is_org_info=True)` — miss one and the org page becomes a real, mislabeled participant:

- **Funding vote / snapshot** — `Guild.objects.filter(is_active=True)` feeds voting (`hub/views.py` voting flows) and `FundingSnapshot`/`calculate_results`. An org pseudo-guild silently entering a funding calculation is a serious bug.
- **Sidebar guild list** — `_get_hub_context` uses `Guild.objects.order_by("name")` (`hub/views.py:55-67`).
- **Guest directory** — `GuildManager.directory()` (guest-guilds spec) and member-directory guild filters.
- **Roster** — `roster_members()`, member counts, `GuildMembership`.

The guild-only *blocks to hide on the detail template alone* are also non-trivial: stat chips + member link (`guild_detail.html:94-99`), Join/Leave (`:232-237`), the orientation prompt + whole orientation section (`:238-243, :386`), Buyables tab + cart + EYOP + product modals (`:106, :389-492, :555-670`), Teach-a-Class (`:244-247`), featured/upcoming classes (`:116-169`), voting. Threading `{% if not is_org_info %}` through a 795-line detail template **and** the ~500-line editor, **plus** exclusions across every Guild queryset, is *more* cruft than a clean dedicated model — and the risky kind.

**Fail loudly / explicit over implicit** (CLAUDE.md §1) argues decisively: an entity whose flag secretly means "this isn't a guild" is exactly the implicit magic the standards warn against. Recommendation: **option (b)**, below — reuse the *patterns and components*, isolate the *data*.

> The one thing (a) buys that (b) must re-solve is the FAQ/Links **models** (they FK to `Guild`). (b) mirrors them as `OrgFAQItem`/`OrgLink` (~40 lines total, copy-paste of a proven shape). That is the whole net "duplication" cost, and it is bounded and safe.

### 4.2 `OrgInfoPage` (new singleton — `HeroCropMixin`, `load()` like `SiteConfiguration`)

```python
class OrgInfoPage(HeroCropMixin, models.Model):
    """Singleton (pk=1) org-wide info page: map, parking, who-to-contact, code of conduct.

    Reuses the guild page's content shapes (hero, markdown sections, FAQ, links) but is
    org-scoped and never participates in guild voting/funding/directory. Load via load().
    """
    banner_image = models.ImageField(upload_to="org/banner/", blank=True, validators=[validate_image_size],
        help_text="Optional hero banner at the top of the Space & Org Info page.")
    intro = models.TextField(blank=True, default="",
        help_text="Welcome / overview blurb. Supports Markdown.")
    floorplan_image = models.ImageField(upload_to="org/floorplan/", blank=True, validators=[validate_image_size],
        help_text="Annotated floor plan — guild locations, restrooms, emergency exits. Click-to-zoom on the page.")
    floorplan_caption = models.CharField(max_length=300, blank=True, default="",
        help_text="Caption under the map, e.g. 'Guild locations, restrooms, and emergency exits.'")
    parking = models.TextField(blank=True, default="", help_text="Parking & arrival info. Supports Markdown.")
    who_to_contact = models.TextField(blank=True, default="",
        help_text="Org structure / who's-who / who to contact for what. Supports Markdown (a table or list).")
    code_of_conduct = models.TextField(blank=True, default="",
        help_text="Code of conduct body. Supports Markdown. Leave blank to link out instead.")
    code_of_conduct_url = models.URLField(blank=True, default="",
        help_text="Optional external Code of Conduct link (used if the body above is blank).")
    updated_at = models.DateTimeField(auto_now=True)

    def get_hero_image_field_name(self) -> str:
        return "banner_image"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.pk = 1
        delete_orphan_on_replace(self, "banner_image")
        delete_orphan_on_replace(self, "floorplan_image")
        from django.conf import settings
        normalize_field_if_uploaded(self, "floorplan_image", settings.IMAGE_MAX_LONG_EDGE_HERO)  # keep detail legible
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "OrgInfoPage":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self) -> str:
        return "Space & Org Info"
```

Notes: floorplan normalized at the **HERO** long-edge (2400px, `settings.py:242`) rather than gallery (1600px) so annotations stay readable when zoomed. `code_of_conduct` body takes precedence; `code_of_conduct_url` is the fallback link (mirrors the FAQ upload-XOR-link idea without a check constraint, since these aren't mutually exclusive — body wins, url is fallback).

### 4.3 `OrgFAQItem` + `OrgLink` (mirror the guild sub-models)

Copy `GuildFAQItem` (`membership/models.py:1228`) and `GuildLink` (`:1291`) verbatim in shape, swapping the FK from `Guild` to `OrgInfoPage` (`related_name="faq_items"` / `"links"`). `OrgFAQItem` keeps `answer` (markdown), `video_url`, `document`/`document_url` (+ the `ck_orgfaqitem_doc_not_both` check constraint), `sort_order`, and the `has_document`/`document_href`/`document_display_name` properties + `save()` orphan cleanup. This is the who-to-contact-for-X workhorse ("Who do I ask about billing?" → rich answer, optional attached policy PDF).

**Optional fast-follow — `OrgContact`** (NOT v1): a structured who's-who row (`role_label`, `name`, `email`, nullable `member` FK for avatar/profile link, `note`, `sort_order`) rendered as a contact matrix. Deferred because the brief says "prefer reusing … over new bespoke forms," and `who_to_contact` markdown + FAQ cover v1. Design `who_to_contact` as a section so `OrgContact` can later replace/augment it without a page redesign.

### 4.4 Physical map v1

`floorplan_image` (above) is the map. Rendered prominently in its own "Map & Facilities" section via `_org_floorplan.html`, which reuses the `_guild_gallery.html` lightbox mechanics (`x-data="{ open, src }"`, `.pl-guild-lightbox`) so a click zooms the annotated plan full-screen. **Explicitly out of scope for v1:** an interactive/clickable SVG map with per-guild hotspots (§10). One image keeps v1 shippable; multi-floor support (a small `OrgFloorplan` child with `sort_order` + `caption`) is a trivial later extension if the space has multiple floors.

**Migration:** `00NN_org_info_page` — plain `AddModel` × (3 or 4). Reverse = Django's automatic `DeleteModel`; no data migration, no `RunPython`, so no reverse function to author (CLAUDE.md §3). Next `membership` migration number — verify with `ls membership/migrations/` at build time (the UAT-response doc notes `0065` was next as of that writing; other batch builds may have consumed it).

---

## 5. Business logic (fat models, skinny views)

Almost none — this is content display. What little exists lives on the model:

- `OrgInfoPage.load()` — singleton accessor (only "logic").
- `OrgInfoPage.has_code_of_conduct` (property) → `bool(self.code_of_conduct or self.code_of_conduct_url)`; `code_of_conduct_href` → body-anchor vs external url; matches the FAQ `document_href` idiom so the template stays dumb.
- Markdown rendering stays in the template filter (`{{ page.intro|guild_markdown }}`) — no view logic.

Views are thin: `org_info` fetches `OrgInfoPage.load()` + `page.faq_items.all()` + `page.links.all()`, calls `_get_hub_context`, renders. Editor views validate a form/formset and save, exactly like the guild endpoints they mirror.

---

## 6. UI / UX (completeness checklist applied)

All colors via tokens; no inline `background`/`color` on controls (FRONTEND.md Rule 13). Verified in **both** themes and on mobile. Reuses `.pl-guild-*` layout classes plus a few new `.pl-org-*`.

### 6.1 Read page — `templates/hub/org_info.html`

Extends `{{ guilds_page_base|default:'hub/base.html' }}` (surface-aware; on the member host that's `hub/base.html` — §8). Structure mirrors the guild Overview but org-flavored:

- **Header / intro:** once QA **Build C4**'s `page_header` component exists, use it for the title "Space & Org Info" + one-line purpose blurb; until then a plain `hub-card` intro. Below it, `{{ page.intro|guild_markdown }}` in a `.pl-md` block. Optional `banner_image` hero (reuse `.pl-guild-hero` markup + the admin `heroPlacement` adjuster, gated on `can_edit`).
- **Map & Facilities (prominent, near top):** `{% include "hub/_org_floorplan.html" %}` — the annotated floorplan as a large figure with `floorplan_caption`, click-to-zoom lightbox. Empty state (no image yet): a muted "The facility map is coming soon." card (never a blank region); for admins, a "Upload the floor plan →" link to the editor's Map tab.
- **Parking & Arrival:** `{{ page.parking|guild_markdown }}` section (hidden when blank).
- **Who's Who / Who to Contact:** `{{ page.who_to_contact|guild_markdown }}` section (a markdown table renders as the contact matrix). Hidden when blank.
- **FAQ:** reuse the exact accordion from `guild_detail.html:311-334` (rich answer, YouTube embed via `youtube_embed_id`, PDF/link via `has_document`/`document_href`). Only shown when `faq_items` exist.
- **Code of Conduct:** `{{ page.code_of_conduct|guild_markdown }}` in a `.pl-md` block, or — if body blank and `code_of_conduct_url` set — a prominent `pl-btn pl-btn--secondary` "Read the Code of Conduct →" link. Given prominence (QA called it out by name).
- **Links / Resources aside:** reuse the `.pl-guild-links` card (`guild_detail.html:292-299`) listing `page.links` — the natural home for any remaining external resources (and the transition landing spot for the two Google-Doc links if kept as links; §7.2).
- **Admin affordance:** when `can_edit` (viewing-as-admin), an "Edit this page" `pl-btn pl-btn--secondary` in the header → `org_info_edit`.

**States:** empty sections self-hide (except Map, which shows a friendly placeholder). No dead ends — sidebar + intro always present. **Dark+light + mobile:** inherits `--hub-*` tokens and `.pl-guild-grid` responsive stacking; verify the map figure and lightbox in both themes.

### 6.2 Admin editor — `templates/hub/org_info_edit.html`

Mirrors `guild_edit.html` structure (Alpine `x-data="{ section }"` tabs, `?tab=` deep-link) but only the tabs org info needs:

- **Content** (main form → `OrgInfoPageForm`): `intro`, `parking`, `who_to_contact`, `code_of_conduct` + `code_of_conduct_url`, `floorplan_caption`, `banner_image`. All text areas wrapped by `components/form_field.html` (auto `.pl-form-group`). Save = single POST to `org_info_edit`.
- **Map** (within the main form or its own instant-upload endpoint): `floorplan_image` upload + a Delete button posting to `org_info_floorplan_delete` (mirrors `guild_image_delete`, `hub/views.py:1521`). Follow FRONTEND.md "Delete is a button, never a toggle."
- **FAQ & Links** (each its own `<form>` outside the main form — can't nest forms): reuse the guild idiom verbatim — `OrgFAQItemFormSet`/`OrgLinkFormSet` with `extra=0`, a `<template>`-cloned "+ Add" row, hidden `DELETE` behind a real `pl-btn--danger pl-btn--sm` Delete button, own save endpoints `org_info_faq_save` / `org_info_links_save` (mirror `guild_faq_save`/`guild_links_save`, `hub/views.py:1701,1724`). This is the canonical editable-list pattern FRONTEND.md points at.

Gate the whole editor on `_require_admin` (403 otherwise). Reuse `components/form_field.html`, `components/toggle.html` (none needed here beyond booleans if added), `components/confirm_modal.html` for the floorplan delete. No bespoke widgets.

### 6.3 New CSS (`static/css/hub.css`, `.pl-org-*`)

Only what `.pl-guild-*` doesn't already cover: `.pl-org-map` figure (full-width image, `cursor: zoom-in`, rounded, caption styling) and `.pl-org-contact` table striping for a markdown contact matrix. Reuse `.pl-guild-lightbox`, `.pl-guild-gallery`, `.pl-guild-section`, `.pl-md`, `.hub-card` as-is. Test both themes (Rule 7).

---

## 7. Nav & "replace Member Guild"

### 7.1 Routes (`hub/urls.py`)

```python
path("info/", views.org_info, name="hub_org_info"),
path("info/edit/", views.org_info_edit, name="hub_org_info_edit"),
path("info/floorplan/delete/", views.org_info_floorplan_delete, name="hub_org_info_floorplan_delete"),
path("info/faq/save/", views.org_info_faq_save, name="hub_org_info_faq_save"),
path("info/links/save/", views.org_info_links_save, name="hub_org_info_links_save"),
```

`/info/` is short, memorable, and clean for the guest surface (`guilds.pastlives.app/info/`). (Alternatives: `/space/`, `/about/` — Josh's call.)

### 7.2 Prominent nav slot + fold in the Google-Doc links

- **Add a top-level sidebar item** "Space & Org Info" in `base.html` in the member nav block, placed high for intuitive discovery — recommended **directly under "Community Calendar"** (`base.html:187`) and above "Guild Voting"/"Member Directory", with a map/info icon. It should show for all personas (it's org-wide reference).
- **Remove the two external Google-Doc footer links** (`base.html:262-292`: "Member Guide" 262-277, "Code of Conduct" 278-292). Their content moves into the page: **Code of Conduct** → the native `code_of_conduct` section (or, transitionally, `code_of_conduct_url` pointed at the existing Doc); **Member Guide** → the `intro` + FAQ + Links. During the content-entry runway, both Docs can be seeded as `OrgLink` rows so nothing is lost before native content is authored.
- The mobile feedback FAB and other footer items stay unchanged.

### 7.3 OPEN QUESTION FOR JOSH — the "replace Member Guild" mechanics

> **QA #15 says "replace the current link to the Member Guild." There is no "Member Guild" in the codebase — it is a prod `Guild` data row.** The replacement mechanics depend on what that row is and whether links to it are already circulating:
>
> 1. **Is prod "Member Guild" a real `Guild` row?** (QA says yes.) If so:
>    - **Convert & redirect:** soft-delete/hide the row and **301 its slug URL → `/info/`** (add a tiny redirect view keyed on that slug), so any bookmarked/Discord-shared `/guilds/<member-guild-slug>/` link keeps working. Its `about`/FAQ/links content can be migrated into `OrgInfoPage` first.
>    - **Risk to confirm:** if that row has `is_active=True`, it may currently appear in **guild voting/funding** — converting it out is a *correctness improvement*, but confirm no in-flight `FundingSnapshot`/`VotePreference` depends on it.
>    - Confirm it has no `GuildMembership` rows, products, or orientation slots that need re-homing.
> 2. **Or a fresh page** (ignore the old row): simpler, but stale links to the old guild page dangle. Only acceptable if the old row was never shared.
>
> **Recommended default:** convert-and-redirect (option 1), but **do not build the redirect until Josh confirms the exact slug and that the row is safe to retire.** This is the one thing that genuinely blocks the "replace" half of the feature.

---

## 8. Synergy — expose on the guest guilds surface (design so it's not precluded)

`2026-07-01-guest-guilds-surface.md` builds a public `guilds.pastlives.app` front door. An org-info page is a natural **public front door for visitors** ("here's how our space works, come by"). To keep that open without building it now:

- **Make `org_info` public-read** (no `@login_required`), exactly like `guild_detail` (`hub/views.py:398`). Editing stays admin-only.
- **Extend `{{ guilds_page_base|default:'hub/base.html' }}`** in `org_info.html` (the guest spec's surface-aware base var), so the page renders in guest chrome on `.app` and member chrome on the hub with zero rework.
- **No member-only data leaks:** the page has none by design (org-wide reference), so it is guest-safe out of the box — no roster names, no contact PII beyond what admins deliberately publish.
- **Absolute URLs** for any cross-host links (reuse `MEMBER_BASE_URL`/`BOOK_BASE_URL` conventions) so a guest never dead-ends.
- **When the guest surface ships:** add `hub_org_info` to `GUILDS_ALLOWED_VIEW_NAMES` (guest spec §6.0) and it's live at `guilds.pastlives.app/info/`. That is out of scope here; this spec only avoids precluding it.

---

## 9. Notifications / emails / activity

**None.** This is static reference content; no triggers, no emails, no activity feed entries. (No changes to `core/triggers.py` or the notification spine.)

---

## 10. Build order (phased; each phase ships green)

Each phase: full suite + `ruff format . && ruff check .` + `mypy` green before the next. Stage only this feature's files (never `git add -A`), sequential commits, do not touch `plfog/version.py` until the last phase (CLAUDE.md).

1. **Models + migration + factories.** `OrgInfoPage` (singleton, `load()`, hero + floorplan + markdown fields), `OrgFAQItem`, `OrgLink` (mirror guild sub-models). `makemigrations membership` (`ruff format` the migration, add it with the model change). Factories in `tests/membership/factories.py`. Specs: `load()` idempotence/pk=1, floorplan normalization, FAQ doc XOR constraint, orphan cleanup on replace.
2. **Read view + template + partial + CSS.** Public `org_info` view; `org_info.html`; `_org_floorplan.html` (lightbox); `.pl-org-*` CSS. Renders for a bare page (all-empty → map placeholder + hidden sections) and a fully-populated page. Specs: 200 (anon + member), sections render/hide by content, floorplan figure + lightbox markup present, FAQ rich-answer/YouTube/doc rendering, code-of-conduct body-vs-url branch.
3. **Admin editor + save endpoints.** `OrgInfoPageForm`, `OrgFAQItemFormSet`, `OrgLinkFormSet`; `org_info_edit` (admin-gated), `org_info_faq_save`, `org_info_links_save`, `org_info_floorplan_delete`. `org_info_edit.html` tabbed (Content / Map / FAQ & Links) reusing the guild editor idiom. Specs: 403 for non-admin on every editor endpoint; main-form save; FAQ/Links formset save (`extra=0`, add/delete); floorplan upload + delete.
4. **Nav + replace Member Guild.** Add the sidebar slot; remove the two Google-Doc footer links (fold Code of Conduct + Member Guide into page content / seed as `OrgLink`s). **Gate the old-guild redirect on Josh's §7.3 answer** — build it only once the slug is confirmed. Specs: nav link present + points at `hub_org_info`; the two Doc `<a href="https://docs.google.com/...">` links absent from rendered `base.html`.
5. **(Deferred / not this build) Guest-surface exposure** — add `hub_org_info` to the allowlist when the guest surface ships (§8). No work here beyond the public-view + surface-aware-base design already in phase 2.
6. **Housekeeping (last).** Bump `plfog/version.py` VERSION + one member-facing CHANGELOG entry (below), grouped as a net-new feature.

> Spec only — do not build until approved.

**Changelog entry (net-new member-facing feature; stamp at the bumped VERSION):**
> **Title:** "One place for how our space works"
> - New **Space & Org Info** page in the sidebar: a map of the space (guild locations, restrooms, and emergency exits), parking info, who to contact for what, and our code of conduct — all in one spot.
> - The old Member Guide and Code of Conduct links now live right inside this page, so you don't have to hunt through Google Docs.

---

## 11. PR fit — recommend its own release, not PR #118

The UAT-response doc (`2026-07-03-qa-uat-response.md`) files this as **Spec B**, landing in PR #118 "unless flagged own release." **Flagging it: this wants its own PR** (branched off `release-0.20.x` or a follow-up), **not** folded into #118. Rationale:

- PR #118 is already large: 5 done features + the sequential C1–C5 UAT builds. This adds **3–4 new models + a migration + 5 endpoints + 2 templates + a nav replacement** — an independently shippable surface, not a small fix.
- The **"replace Member Guild"** nav change is user-visible and depends on an **unresolved product decision** (§7.3) plus **real content entry** (a floorplan image, code-of-conduct text, contact list) before the page is worth surfacing. That runway shouldn't gate the rest of #118.
- It is cleanly separable — nothing in C1–C5 depends on it, and it depends on nothing in #118 except the (optional) `page_header` component from C4, which it degrades gracefully without.

**If Josh prefers to keep the whole UAT batch in #118:** it can ride along, but sequence it **last** (after C1–C5) and keep the nav/redirect change behind the §7.3 confirmation. Either way, **one PR = one version** (VERSION bump in the final phase only).

---

## 12. Open / deferred

1. **§7.3 "Member Guild" mechanics — BLOCKS the "replace" half.** Confirm with Josh whether the prod row is convert-and-redirect vs. retire; get the exact slug; confirm no voting/funding/membership dependence.
2. **Floorplan asset is content, not code.** Someone must produce the annotated floor plan (guild locations, restrooms, exits) at reasonable resolution (long edge ≥ ~2000px so annotations stay legible zoomed). No repo asset exists.
3. **Interactive/clickable map — out of scope v1.** A future SVG-hotspot map (click a guild → its page) is a separate effort; the `floorplan_image` field does not preclude it.
4. **`OrgContact` structured who's-who — deferred.** v1 uses `who_to_contact` markdown + FAQ; promote to a structured model only if markdown proves insufficient.
5. **Route name `/info/`** — final URL word is Josh's call (`/info/` vs `/space/` vs `/about/`).
6. **`page_header` dependency (Build C4).** Adopt when available; degrade to a plain intro card otherwise.
7. **Guest-surface exposure (§8)** — intentionally not built here; the public-view + surface-aware-base design keeps it a one-line allowlist add later.
8. **Content authoring model editing.** `OrgInfoPage`/`OrgFAQItem`/`OrgLink` will auto-register in Django admin (CLAUDE.md admin auto-registration) as a backstop, but the in-app admin editor (§6.2) is the intended authoring path.

---

### Critical Files for Implementation

- `membership/models.py` — add `OrgInfoPage` singleton + `OrgFAQItem` + `OrgLink`, mirroring `GuildImage` (`:1205`), `GuildFAQItem` (`:1228`), `GuildLink` (`:1291`) and the `SiteConfiguration.load()` pattern.
- `hub/views.py` — add `org_info` (public, model on `guild_detail` `:398-506`), `org_info_edit` (admin, `_require_admin` `:534`), and the FAQ/Links/floorplan-delete save endpoints (mirror `:1521,1701,1724`).
- `templates/hub/guild_detail.html` + `templates/hub/_guild_gallery.html` — the read-page template and the lightbox partial to clone into `org_info.html` / `_org_floorplan.html`.
- `templates/hub/guild_edit.html` — the tabbed editor + FAQ/Links formset idiom to clone into `org_info_edit.html`; and `hub/forms.py` (`GuildFAQItemFormSet`/`GuildLinkFormSet` at `:527,573`) to mirror.
- `templates/hub/base.html` — add the prominent nav slot and remove/fold the two Google-Doc footer links (`:262-292`).
