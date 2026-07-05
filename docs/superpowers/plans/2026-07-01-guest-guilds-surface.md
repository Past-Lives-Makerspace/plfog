# Guest Guilds Surface (`guilds.pastlives.app`) — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-01
**Surface:** New public host `guilds.pastlives.app` (guest chrome) — a guild **directory** homepage + the existing per-guild page (`/guilds/<slug>/`) rendered without the member sidebar. Also touches the shared `templates/hub/guild_detail.html` and the allauth login templates (guest chrome). Members hub (`pastlives.test`) and book CMS (`book.pastlives.test`) are only lightly touched (four chrome gates in `hub/base.html`, one new `is_featured` field).
**Related:** `2026-06-21-guild-orientations.md` (orientation stack this surface exposes read-only + in-place); the book public surface (`templates/classes/base_public.html`, `static/css/cms-public.css`) is the aesthetic we mirror.

---

## 1. Summary

Anyone — no login, no membership — can visit **guilds.pastlives.app** and browse a beautiful directory of every active guild, then open a guild's page to read about it, see its meetings, classes, FAQ, gallery, and upcoming orientation times. Guests see everything a shareable public page should show; the moment they want to *do* something (join a guild, book an orientation, request a custom time), the page invites them to log in **right there on guilds.pastlives.app** and complete the action in place — they never get bounced to the members site. The chrome is the slim, light-by-default public topbar from the book CMS, fully responsive down to a burger drawer on mobile, and every guild link previews nicely when shared (Open Graph tags per guild).

This is the guild equivalent of what `book.pastlives.space` already does for classes: a public, gorgeous, login-optional front door — but for guilds, and with join/orientation actions that resolve on the same host instead of a redirect handoff.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Gated actions | **Act in place** on `guilds.pastlives.app` after an on-site login. Session is host-scoped: **CONFIRMED this session via the Render API that prod `COOKIE_DOMAIN` is UNSET** (no env group sets it; the `settings.py` comment is aspirational), so prod cookies are already host-only and login-in-place on `.app` works with no plumbing. (`.app` is also a different registrable domain than `.space`, so the `.pastlives.space` cookie would never apply anyway.) **Not** a redirect/handoff to the members host. |
| In-place action scope | Join / Leave + Orientation only (book a slot, request a custom time, cancel own booking). **Buyables / Stripe commerce is out of scope** — a logged-in member sees a "Buy on the members hub" link to `members.pastlives.space`; no cart/Stripe on this surface. |
| Chrome | Public `.cp-topbar` (with account menu + logout when authed), **never** the member sidebar — for anonymous *and* logged-in users on `.app`. |
| Roster | Anonymous guests see the member **count only**, never names/avatars. Once logged in on `.app`, the roster shows names **only if** the guild opted in (`show_members`), identical to the member view. |
| Directory placement | Root `/` on the guilds host **302-redirects to `/guilds/`**; the directory lives at `/guilds/`; guild pages stay at `/guilds/<slug>/` (reuse `guild_detail`). Order: **featured guild(s) first, then alphabetical.** |
| Login | Standard allauth email-code login, rendered in guest (cp-topbar) chrome; `?next=<path>` returns to the guild page after login. |
| Extras included | (1) "Become a member" CTA in the hero **and** guild-page footer; (2) per-guild SEO/Open-Graph tags (title/description/image) so shared links preview; (3) **read-only orientation slots shown to guests** with a "Log in to book" prompt — orientations are *not* hidden until login. |
| Extras excluded | Directory search / filter — out of scope for v1. |

---

## 2. What already exists (reuse, don't reinvent)

The build is ~90% assembly. Confirmed in the codebase (line numbers may drift):

| Need | Existing thing | Location |
|---|---|---|
| Host → surface routing + path gate | `SurfaceMiddleware` (public vs members, root redirect, path-prefix gate) | `core/middleware.py:35` |
| Surface flags for templates | `surface()` context processor (`is_public_surface`, `parent_template`, `MEMBER_HOST`) | `core/context_processors.py:38` |
| Public host / member host settings pattern | `PUBLIC_HOSTS`, `MEMBER_HOST`, `MEMBER_ONLY_PATH_PREFIXES`, `MEMBER_BASE_URL`, `BOOK_BASE_URL` | `plfog/settings.py:56` / `:412` |
| Slim public chrome to mirror | `.cp-topbar` (nav, burger drawer <880px, account dropdown, theme toggle, light default) | `templates/classes/base_public.html:11` |
| Sidebar/topbar are already conditional | member chrome gated on `is_public_surface` | `templates/hub/base.html:49,329,330` |
| The whole book aesthetic (tokens + cards + hero) | `.cp-page` token block, `#hero`, `.cta-primary/secondary`, `.cls-grid`, `.cls-card/.cls-media/.cls-body/.cls-title/.cls-inst` | `static/css/cms-public.css:326,400,439` |
| Guild data + directory queryset base | `Guild` + `GuildManager` (hides soft-deleted), `is_active`, `about`, `banner_image`, `logo_prefix`, `guild_lead`, `next_meeting_at`, `roster_members()`, `show_members`, `featured_class` | `membership/models.py:813,820` |
| The public guild page itself | `guild_detail(request, slug)` — **already public, not `@login_required`**; computes orientation/roster/faq/gallery/classes/calendar/pulse | `hub/views.py:398`; template `templates/hub/guild_detail.html:1` |
| Orientation section (slots list, custom request, cancel) | `templates/hub/partials/guild_orientation.html` — slots table + confirm-modal booking + custom-request form | whole file |
| In-place action endpoints (POST, redirect back by name → relative path) | `guild_join` (`hub/views.py:1490`), `guild_leave` (`:1507`), `orientation_book` (`:807`), `guild_orientation_request_custom` (`:830`), `orientation_cancel_mine` (`:929`) — all `redirect("hub_guild_detail", slug=...)`, which emits a **relative** `/guilds/<slug>/`, so success stays on `.app` |
| Custom-time form (datetime-local widget + future-time validation) | `OrientationCustomRequestForm` (`starts_at` DateTimeInput with `showPicker()`, `note`) | `hub/forms.py:832` |
| Pretty email-code login in public chrome | `templates/account/login.html` — already branches `is_public_surface` for the `.bk-auth` card (uses `.bk-field` scope + `book-account.css`) | `templates/account/login.html:1,11` |
| Components | `confirm_modal.html` (already used by orientation booking/cancel), `form_field.html`, `modal.html`, `toggle.html`, toast (in base), `pl-btn` variants, `hub-card`, `hub-badge`, member-row classes | `templates/components/*`, `static/css/*` |
| Success-toast-from-messages plumbing | full-page POST → Django messages → drained to toasts by base template; `ToastFlashMiddleware` carries them across boosted redirects | `templates/hub/base.html` toast source list; `core/middleware.py:101` |

### Genuine gaps to close (kept minimal)

1. **A third surface, `"guilds"`.** `SurfaceMiddleware` only knows `public`/`members`. Add `GUILDS_HOSTS` and a `"guilds"` branch with an **allowlist gate** (below). `/guilds/` is currently in `MEMBER_ONLY_PATH_PREFIXES` — untouched; it stays 404 on book, allowed on members, and allowed on the guilds host via the new gate.
2. **A guest base template + directory page + directory CSS.** `templates/guilds/base_public.html`, `templates/guilds/directory.html`, `static/css/cms-guilds.css`. The directory page + view (`guild_directory`) is the one genuinely new screen — there is no existing guild list page.
3. **`Guild.is_featured` + `GuildManager.directory()`.** There is no "featured guild" flag today (only `featured_class`, which is a *class*). Add one boolean so "featured first" ordering has a home in the model, not the view.
4. *(Not a gap — resolved.)* Host-scoped session cookie on `.app`: **prod `COOKIE_DOMAIN` is confirmed UNSET** (Render API, this session), so Django already emits host-only cookies and in-place login works with no code. An optional one-line safety net — a middleware response step that strips any `Domain=` from `sessionid`/`csrftoken` on the guilds surface — can be added *only if* a future env group ever sets `COOKIE_DOMAIN=.pastlives.space` (a browser rejects such a cookie coming from a `.app` response). Not load-bearing; do not build it unless that env changes.

---

## 3. Where the code lives

```
core/
  middleware.py            # + "guilds" surface branch + allowlist gate (host-scope-cookie step NOT needed — COOKIE_DOMAIN confirmed unset)
  context_processors.py    # surface(): + is_guilds_surface, is_guest_surface, guilds_page_base; extend parent_template
  spec/
    middleware/surface_middleware_spec.py   # + guilds routing/gate/cookie cases
    context_processors_spec.py               # + guilds flags
plfog/
  settings.py              # + GUILDS_HOSTS, GUILDS_ALLOWED_VIEW_NAMES, GUILDS_BASE_URL
  version.py               # bump VERSION + new CHANGELOG entry (last)
membership/
  models.py                # Guild.is_featured; GuildManager.directory()
  migrations/00XX_guild_is_featured.py       # add field (reverse = remove column)
  spec/models/guild_spec.py                  # + directory() ordering/filtering, is_featured default
hub/
  views.py                 # + guild_directory(); guild_detail: roster gated on member, can_edit forced False on guilds surface
  urls.py                  # + path("guilds/", guild_directory, name="hub_guild_directory")
  spec/views/guild_directory_spec.py         # NEW
  spec/views/guild_detail_spec.py            # + anon-vs-authed rendering, roster/lead/class-link/editor/buyables gating
  spec/views/guest_login_flow_spec.py        # NEW — ?next= end-to-end (MUST-FIX #2)
  spec/views/guild_detail_head_spec.py       # NEW — extra_head block.super survives + OG tags (MUST-FIX #8)
templates/
  guilds/
    base_public.html       # NEW guest base — mirrors classes/base_public.html (cp-topbar, light default; extra_head w/ classes_extra_head sub-block)
    directory.html         # NEW directory page
  hub/
    base.html              # 4 chrome gates: is_public_surface -> is_guest_surface
    guild_detail.html      # extends guilds_page_base; extra_head via block.super; anon Join/Leave/orientation gating; surface-branched class links + member-count chip; Teach/buyables/lead-contact/editor gated; inline OG; member footer CTA
    partials/guild_orientation.html          # anon "Log in to book" branch
  account/
    login.html             # is_public_surface -> is_guest_surface; hidden `next` on request-code form(s); branch off the account:lookup line on guilds
    request_login_code.html / confirm_login_code.html / signup.html   # is_public_surface -> is_guest_surface; hidden `next` on the confirm-code form
static/css/
  cms-guilds.css           # NEW: directory hero/grid/card + guilds-portal scoping + hub-content centering (NO datetime rule — components.css already themes .pl-form-group pickers)
img/
  og-guild-default.png     # NEW asset (~1200x630): OG fallback when a guild has no banner (flag: needs producing)
```

Home apps: `core` (routing/plumbing), `membership` (model), `hub` (directory view + template). No new app.

---

## 4. Data model

Only one small field is added; everything else is reuse.

### `Guild` (existing — one new field)

| Field | Type | Note |
|---|---|---|
| `is_featured` | `BooleanField(default=False, help_text="Pin this guild to the top of the public guilds directory.")` | Drives "featured first" ordering. Cosmetic; independent of `is_active`. |

### `GuildManager` (existing — one new method)

```python
def directory(self) -> QuerySet[Guild]:
    """Active guilds for the public directory: featured first, then alphabetical."""
    return self.filter(is_active=True).order_by("-is_featured", "name")
```

`get_queryset()` already excludes soft-deleted (`deleted_at__isnull=True`), so `directory()` inherits that. The view calls `Guild.objects.directory()` — no ordering logic in the view.

**Migration:** `00XX_guild_is_featured` — a plain `AddField`. Reverse = Django's automatic `RemoveField` (column drop); no data migration, no custom `RunPython`, so no reverse function to write.

---

## 5. Business logic (fat models)

No new business logic beyond `GuildManager.directory()` (§4). The gated actions already live in the model/service layer and are reused verbatim:

- Join/Leave: `guild_join`/`guild_leave` views delegate to `GuildMembership.objects.get_or_create` / `.delete()` and `orientations.member_joined_guild(...)`.
- Orientation: `orientations.request_orientation(slot, member, note)` raises `OrientationError`; `OrientationSlot.book(...)`, `.upcoming()`, `.bookable()`, `seats_remaining`, `is_full`; `Member.active_orientation_for(guild)` / `.is_oriented_for(guild)`; cancel via `OrientationBooking.cancel(...)`.

The **directory view** and **guild_detail view** stay thin: fetch, pass context, render. `guild_detail` renders for `AnonymousUser` today, but it is **not** fully guest-safe as-is — it needs **two one-line guest-safety tweaks** (thin glue, not business logic), both detailed in §6.2:

1. **Roster** — gate on the viewer, not just the guild: `roster = guild.roster_members() if guild.show_members and member is not None else None` (closes the MUST-FIX #1 privacy leak; today `roster` depends only on `show_members`, so an anon guest on a `show_members=True` guild would render full names).
2. **Editor UI** — `can_edit_this_guild = _can_edit_guild(request, guild) and request.surface != "guilds"`, so a logged-in lead on `.app` sees no editor buttons that would 404.

The only *new* view is `guild_directory`:

```python
def guild_directory(request):
    guilds = Guild.objects.directory().select_related("guild_lead").annotate(
        member_total=Count("memberships")
    )
    ctx = _get_hub_context(request)  # sidebar data is ignored in guest chrome, but keeps parity on members host
    return render(request, "guilds/directory.html", {**ctx, "guilds": guilds})
```

(`member_total` annotation avoids an N+1 across cards; alternatively expose a cheap `member_count` per card.)

---

## 6. UI / UX  ← completeness checklist applied per screen

Light is the **default** theme on this surface (`{% block default_theme %}light{% endblock %}`); the topbar toggle still wins and persists via `localStorage`. Every screen below is verified in **both** themes and reflows on mobile. All colors come from tokens; no inline `background`/`color` on any control.

---

### 6.0 Surface plumbing + guest base + `cms-guilds.css` (the aesthetic)

**Middleware — `core/middleware.py`.** Add a `"guilds"` surface:

```python
guilds_hosts = set(getattr(settings, "GUILDS_HOSTS", []))
if host in guilds_hosts:
    request.surface = "guilds"
    short_circuit = self._handle_guilds_surface(request)   # root -> /guilds/, else allowlist gate
    if short_circuit is not None:
        return short_circuit
    return self.get_response(request)
    # NOTE: no cookie step needed — prod COOKIE_DOMAIN is confirmed unset (§2 #4 / §10 #1),
    # so Django already emits host-only cookies on .app. Add a self._host_scope_cookies(response)
    # step here ONLY if a future env group ever sets COOKIE_DOMAIN=.pastlives.space.
```

`_handle_guilds_surface`:
- `/` → `HttpResponseRedirect("/guilds/")` (relative → stays on host).
- Otherwise resolve the path (`django.urls.resolve`) and 404 unless the matched `url_name` is in **`GUILDS_ALLOWED_VIEW_NAMES`** — a precise allowlist (not a broad `/guilds/` prefix, which would leak the guild *editor* endpoints onto `.app`):
  `{"hub_guild_directory", "hub_guild_detail", "hub_guild_detail_by_id", "hub_guild_join", "hub_guild_leave", "hub_orientation_book", "hub_guild_orientation_request_custom", "hub_orientation_cancel_mine"}` **plus** the allauth `account*` login/logout/signup/verification view names. Static assets bypass this (WhiteNoise middleware runs *before* SurfaceMiddleware). A `Resolver404` → normal 404.

**Context processor — `core/context_processors.py` `surface()`:**

```python
is_guilds = value == "guilds"
is_public = value == "public"
return {
    ...,
    "is_guilds_surface": is_guilds,
    "is_guest_surface": is_public or is_guilds,        # NEW unified "no member chrome" flag
    "guilds_page_base": "guilds/base_public.html" if is_guilds else "hub/base.html",
    "parent_template": (
        "guilds/base_public.html" if is_guilds
        else "classes/base_public.html" if is_public
        else "base.html"
    ),
}
```

**`templates/hub/base.html` — 4 gates** flip `is_public_surface` → `is_guest_surface` so the sidebar + `.pl-topbar` are stripped for anon *and* logged-in users on the guilds host (and the mobile feedback FAB / any other `is_public_surface`-gated member bit — audit and switch consistently). This is the whole reason the sidebar disappears "for free": the guilds surface is now a guest surface.

**`templates/guilds/base_public.html`** — mirror `classes/base_public.html`, changed only where guilds differ:
- `{% block default_theme %}light{% endblock %}`.
- `{% block body_class %}guilds-portal{% endblock %}` (cms-guilds.css scopes to `body.guilds-portal`).
- `{% block public_topbar %}` → a `.cp-topbar` with the **Guilds** nav item marked current (`.cp-topbar__link--current`), the same account dropdown (My account, "FOG" link + "Member" pill when `persona == "member"`, Log out), the theme toggle, the burger + `.cp-topbar__drawer`, and the "Become a member" CTA (`.cp-topbar__cta` → `https://pastlives.space/membership`). Nav links: Home · **Guilds** (current, → `/guilds/`) · Membership · Classes (→ book) · Contact. Anon right side = "Log in" ghost + "Become a member" CTA + theme toggle.
- `{% block extra_head %}` mirrors the book base: meta description, Playfair/Lato fonts, `cms-public.css`, **plus** `cms-guilds.css`, google-analytics include, and `{% block classes_extra_head %}{% endblock %}` (so `login.html` can add `book-account.css`). **The guild page must reach this base `extra_head` via `{{ block.super }}` — see §6.6**, because `guild_detail.html` currently overrides `extra_head` without `block.super` and would otherwise discard the base's fonts/CSS. (No `og_meta` sub-block is needed; OG tags live inline in the guild page's `extra_head`, guarded to the guilds surface — §6.6.)
- **Does not** override `block content` — children provide content and wrap in `.cp-page` themselves (the directory and the login card do this; the guild page keeps its native `hub-card` layout). This keeps `guild_detail.html` usable on both bases without block-name conflicts.

**`static/css/cms-guilds.css`** — new, `pl-`-prefixed, tokens only:
- `body.guilds-portal { background: var(--hub-bg); }` and centering for the guild page's `<main class="hub-content">` (max-width ~1100px, `margin:0 auto`, 8px-grid padding) so the reused guild page reads centered like book, without touching `guild_detail`'s markup.
- Directory-specific bits that aren't already in `.cls-*`: a member-count chip on the card, the lead line, and the empty state — all as `.pl-guild-dir__*` classes using `var(--text2)`, `var(--gold-text)`, `var(--border)` (defined under `.cp-page`).
- **No new datetime CSS is needed** (corrected from an earlier draft). `form_field.html` wraps fields in **`.pl-form-group`** (not `.hub-form-group`), and `components.css` **already** themes native date/time/datetime-local pickers for both modes via `color-scheme`: `.pl-form-group input[type="datetime-local"] { color-scheme: dark; }` (`components.css:491`) and `[data-theme="light"] .pl-form-group input[type="datetime-local"] { color-scheme: light; }` (`components.css:948`). Because the light-default theme sets `data-theme="light"` on `<html>` (base.html's theme script), the light rule applies on this surface — the custom-time field renders correctly in both themes with zero additions. **Do not** add a `filter: invert` rule (it fights `color-scheme`) and **do not** scope anything to `.hub-form-group` (wrong class). §6.5 just requires *verifying* both themes.

**States:** n/a for plumbing itself. **Dark+light:** the guest base and directory verified in both; the guild page inherits hub tokens (already dual-theme). **Mobile:** `.cp-topbar` collapses to burger at <880px (existing CSS reused verbatim).

---

### 6.1 Guild **Directory** homepage — `templates/guilds/directory.html`

The stunning front door. Extends `{{ guilds_page_base }}`; on the guilds host that's `guilds/base_public.html`. Fills `{% block content %}` with a single `<div class="cp-page">` wrapper so it inherits every cms-public token and can reuse the exact book components.

- **Layout & container:** dedicated page. Inside `.cp-page`:
  - **Hero** — reuse `#hero`: `<h1>` "Find your <span>guild</span>." (gold span), a one-line `<p>` blurb, `#hero-stats` (guild count · total members · classes running — cheap aggregates), and `#hero-cta` with **`.cta-primary` "Become a member"** (→ `pastlives.space/membership`) + **`.cta-secondary` "Browse classes"** (→ book). This is the hero-woven Become-a-member CTA (locked extra #1).
  - **Grid** — reuse `.cls-grid` (`repeat(auto-fill, minmax(260px,1fr))`). Each card is built on the book card structure so it is visually identical to the class catalog:
    - `.cls-media` → the guild banner (`guild.banner_image.url`) if set; else a `.cls-img-ph` placeholder tinted navy→navy-light with the guild's B/W logo (`img/guild_logos/<logo_prefix>_bw.svg`) or its initial, matching the class-card placeholder treatment. The whole media is the card link (`<a href="{% url 'hub_guild_detail' guild.slug %}">`).
    - `.cls-body` → `.cls-title` (guild name, links to page), a `.cls-inst`-style line "Led by {{ guild.guild_lead.display_name }}" (omitted if no lead), a one-line blurb from `about` (`{{ guild.about|striptags|truncatewords:18 }}`), and a `.pl-guild-dir__meta` row of `hub-badge`-style chips: **member count** (count only — never names), and "Next meeting {{ guild.next_meeting_at|date:'M j' }}" when set.
  - A featured guild renders first (from `directory()`); optionally flagged with a small `.pl-guild-dir__star` badge. No search/filter (excluded).
- **Components used:** reused `.cls-grid/.cls-card/.cls-media/.cls-body/.cls-title`, `#hero`, `.cta-primary/secondary`; new `.pl-guild-dir__*` only for the meta chips + empty state.
- **Controls:** the directory has **no forms** — it's browse-only. The only actions are the hero CTAs and the card links (all `<a>`). Nothing to Save. (Checklist §1/§2 N/A — confirmed no list-editor here.)
- **States:**
  - *Empty* (no active guilds): a centered `.pl-guild-dir__empty` card inside `.cp-page` — "No guilds are listed yet. Check back soon — or become a member to help start one." with the Become-a-member CTA. Not a blank region.
  - *Loading:* plain server-rendered page; boosted navigations show the global `loading_bar.html` progress bar.
  - *Error:* an unknown path 404s (guest-chrome 404 — see §10 #3); the directory itself has no failure mode.
  - *Success:* n/a (no mutation).
  - *No dead ends:* every card links to a guild page; the topbar returns Home/Classes.
- **Dark + light:** all via `.cp-page` tokens; verify the placeholder gradient and gold text in both. **Mobile:** grid reflows to one column via `auto-fill`; hero stats wrap; topbar → burger. Tap targets are full cards + real buttons. 8px-grid spacing.

---

### 6.2 Guest **Guild page** — `templates/hub/guild_detail.html` (shared, in guest chrome)

Same template members already use, now surface-aware and guest-safe.

- **Base:** line 1 changes `{% extends "hub/base.html" %}` → `{% extends guilds_page_base %}`. On members it resolves to `hub/base.html` (unchanged); on `.app` to `guilds/base_public.html` (cp-topbar, no sidebar).
- **Two guest-safety view tweaks in `guild_detail` (thin glue, not business logic):**
  1. **Roster:** change `roster = guild.roster_members() if guild.show_members else None` (`hub/views.py:433`) → `... if guild.show_members and member is not None else None`. `member` is `None` for anon, so the roster query never even runs for guests. (Belt-and-suspenders: the template block also gains `{% if user.is_authenticated %}` — see the table.) **This closes the MUST-FIX #1 privacy leak**: today the roster depends only on `show_members`, so an anon guest on a `show_members=True` guild would see full names/avatars.
  2. **Editor UI:** force `can_edit_this_guild = _can_edit_guild(request, guild) and request.surface != "guilds"`. `can_edit_this_guild` is permission-based and surface-agnostic, so a logged-in **lead** on `.app` would otherwise get `True` → Edit / Adjust-placement / Manage-meeting-notes / product-admin buttons all render, then **404** when clicked (the editor endpoints aren't in the guilds allowlist). Forcing it `False` on the guilds surface (one AND clause) hides every editor affordance in one place. Leads still edit on FOG.
- **Layout & container:** the existing tabbed page (Overview · Guild Calendar · Buyables · FAQ · Meeting Notes · Gallery) inside `hub-card`s. On `.app`, cms-guilds.css centers the `hub-content` column. No structural rewrite.

**Per section — guest vs logged-in member (on `.app`):**

| Section | Anonymous guest | Logged-in member |
|---|---|---|
| Hero / banner | Banner, logo, name, meeting chips, **member-count** chip. The chip currently links to `hub_member_directory` (a members-host route → dead end on `.app`). Fix: `{% if is_guilds_surface %}` render the count as a **plain `hub-badge` span** (no link); else keep the existing link. | Same (link on members host, plain span on `.app`) |
| Get Involved | See **Join gating** below | Join / Leave + orientation actions live |
| Roster ("Members") | **Hidden for anon** — view sets `roster = None` when `member is None` (tweak above) **and** the template block is wrapped `{% if user.is_authenticated and guild.show_members and roster %}`. Count-only lives in the hero chip. | Names/avatars **only if** `show_members`, exactly as today |
| Featured class (`guild_detail.html:118`) / Upcoming classes (`:159`) | Shown, but the hrefs use `{% url 'classes:register' … %}` → a **relative `/classes/…`** not in the guilds allowlist → **404 on `.app`**. Fix: on `is_guilds_surface`, render the href as **absolute `{{ BOOK_BASE_URL }}{% url 'classes:register' … %}`** (with `hx-boost="false"`) so it opens on the book host. | Same absolute link on `.app` |
| "Teach a Class" button (`:244`) | Currently rendered **unconditionally** (`{% url 'classes:teach_class_create' %}`, a members/book route) → nonsensical for a guest + guaranteed 404. Fix: wrap in `{% if not is_guilds_surface %}` so it never shows on `.app`. | **Hidden on `.app`** (teaching lives on FOG/book) |
| Guild Lead card + profile modal (`:194`, `:497`–`:555`) | Name / title / avatar visible. The row is currently `@click`-openable and the modal exposes **email, phone, Discord handle, and bio** to the public. Fix: gate the clickable modal + all contact-detail rows behind `{% if user.is_authenticated %}`; for anon show only the static name/title/avatar row (no `@click`, no modal). | Full contact modal as today |
| FAQ / Gallery / Meeting Notes | Read-only. "Manage meeting notes" (`:342`) and every other `can_edit_this_guild` affordance are gone because `can_edit_this_guild` is forced `False` on `.app` (view tweak above). | Read-only here too (edit on FOG) |
| Guild Calendar | Shown (public read-only calendar) | Same |
| Orientation | **Read-only slots + "Log in to book"** (§6.3) | Live booking (§6.5) |
| Links / Discord / Website / Email lead | Shown — `mailto:` and external links work for everyone | Same |
| Buyables / cart / EYOP | **Fully suppressed on `.app`, not a body swap** (MUST-FIX #9). The `guildCart` Alpine component (page wrapper `guild_detail.html:12`), the Buyables tab button (`:106`), the cart bar (`:471`), the Add-to-Cart / EYOP / product-admin modals (`:497`–`:670`), **and** the `extra_js` that `fetch`es `/guilds/<pk>/cart/confirm/` (`:752`) are each wrapped in `{% if not is_guilds_surface %}`; the page wrapper's `x-data="guildCart(…)"` becomes a no-op `x-data="{}"` on `.app`. None of `hub_guild_product_*` / `cart/confirm` is allowlisted, so nothing may call them. In the Overview, a single **"Buy this guild's items on the members hub"** `pl-btn--secondary` link → `{{ MEMBER_BASE_URL }}{% url 'hub_guild_detail' guild.slug %}` (anon: "Log in on the members hub to shop"). | Same members-hub link (no in-place cart) |

**Join gating (Get Involved panel).** Today: `{% if member and not is_member_of_guild %}` → Join form; there is no Leave button (a stale "leaving disabled" comment). Change to a three-way, per the locked Join/Leave scope:

```django
{% if not user.is_authenticated %}
  <a href="{% url 'account_login' %}?next={{ request.path|urlencode }}" class="pl-btn pl-btn--primary" style="width:100%;">Log in to join</a>
{% elif member and not is_member_of_guild %}
  <form method="post" action="{% url 'hub_guild_join' guild.pk %}">{% csrf_token %}
    <button type="submit" class="pl-btn pl-btn--primary" style="width:100%;">Join This Guild</button></form>
{% elif member and is_member_of_guild and is_guilds_surface %}
  <button type="button" class="pl-btn pl-btn--danger pl-btn--sm" style="width:100%;" @click="$dispatch('open-confirm','leave-guild')">Leave this guild</button>
  {% url 'hub_guild_leave' guild.pk as leave_url %}
  {% include "components/confirm_modal.html" with confirm_id="leave-guild" confirm_title="Leave "|add:guild.name|add:"?" confirm_message="You can rejoin any time. Your orientation history stays." confirm_action_url=leave_url confirm_button_text="Leave guild" %}
{% endif %}
```

Leave is destructive → routed through **`confirm_modal.html`** with a `pl-btn--danger pl-btn--sm` trigger (checklist §3). **The Leave branch is gated `and is_guilds_surface`** so this feature does **not** silently add a Leave button to the members hub (per coordinator decision — see §10). "Log in to join" is an anchor carrying `?next={{ request.path|urlencode }}` — i.e. the **GET guild page**, never a POST-only action endpoint (a stale session hitting a POST-only URL as `next` would 405; the anchors always bounce through the GET page). Post-login the member lands here and the live Join/Leave button renders.

**Become-a-member footer CTA (locked extra #1).** After the tabs, a `hub-card` band: "Not a member yet? Join Past Lives to teach, book studio time, and take part in your guilds." + a `pl-btn--primary` "Become a member" (→ `pastlives.space/membership`). Shown to everyone (harmless for members; primarily for guests).

- **Controls / Save:** the guest page's only writing controls are Join (form → full-page POST → Django message → toast), Leave (confirm modal), and the orientation actions (§6.5). Each is a real button with a wired action and stated feedback.
- **States:** *Empty* — sections self-hide when empty (existing); orientation empty handled in §6.3. *Loading* — global loading bar on boosted POST/redirect. *Error* — bad slug → guest-chrome 404; orientation errors → error toast (§6.5). *Success* — "You joined {guild}." / "You left {guild}." toast on redirect back. *No dead ends* — topbar + directory link always present.
- **Dark + light:** page uses `--hub-*` tokens (dual-theme already); verify the new "Log in to…" buttons and footer band in both. **Mobile:** the `pl-guild-grid` already stacks; Get-Involved buttons are full-width; tabs wrap.

---

### 6.3 Guest orientation (read-only slots + "Log in to book") — `templates/hub/partials/guild_orientation.html`

Locked extra #3: guests **see** upcoming orientation times but can't book without logging in. The view already populates `orientation_slots` for anon (its condition doesn't require a member), and `show_orientation` is true when enabled — so the slots table renders for guests today, but its "Request" buttons open a confirm modal that POSTs to a `@login_required` endpoint (a bounce). Fix by branching on auth **inside the partial**:

- The slots table stays (Date / Time columns, pager). For each bookable slot:
  - `{% if user.is_authenticated %}` → the existing "Request" button + `confirm_modal.html` (unchanged member path).
  - `{% else %}` → a `pl-btn pl-btn--sm pl-btn--primary` **anchor** "Log in to book" → `{% url 'account_login' %}?next={{ request.path|urlencode }}`. Full slot (full → "Full" muted, same as now).
- The custom-request block (`orientation.allow_custom_requests`): for anon, replace the reveal-form with a single "Log in to request a custom time" anchor (same `?next=`). For members, the existing toggle-revealed form (§6.5).
- **Empty:** existing copy — "No orientation times posted yet — check back soon." *Paused:* existing `orientation.is_closed` message. Both show for guests.
- **Dark + light / mobile:** partial already uses `pl-orient-slots__*` + hub tokens and a responsive pager; verify the new anchors in both themes.

---

### 6.4 **Login** on `.app` — `templates/account/login.html` (+ signup / code-verification templates)

Standard allauth email-code flow, rendered in guest chrome, resolving on `.app`.

- **Base:** `{% extends parent_template %}` already; parent_template now resolves to `guilds/base_public.html` on the guilds host (§6.0) → cp-topbar chrome, light default.
- **Pretty card:** the login template gates its beautiful `.bk-auth` card on `{% if is_public_surface %}`. Change that condition (here and in `request_login_code.html`, `confirm_login_code.html`, and `signup.html`) to **`{% if is_guest_surface %}`** so the same `.bk-auth`/`.bk-field` card (with `book-account.css`) renders on both book and guilds. Inputs stay wrapped in `.bk-field` (Rule 13 — book-account input tokens, no inline color).
- **`?next=` handoff — MUST wire it (MUST-FIX #2), it does NOT "just work".** Today `templates/account/login.html`'s forms (the `.bk-auth` card ~`:20`–`:28` and the plain branch ~`:50`–`:59`) POST to `account_request_login_code` with **no `next`** field, and `confirm_login_code.html` has none either — so the "stay on the same page" promise is currently broken. Fix: add a hidden field to **every** step's form — the request-code form(s) in `login.html`, **and** the code-confirmation form in `confirm_login_code.html`:
  ```django
  <input type="hidden" name="{{ redirect_field_name|default:'next' }}" value="{{ redirect_field_value }}">
  ```
  allauth's login-by-code is a two-request flow (`account_request_login_code` → `account_confirm_login_code`); confirm at build time that allauth carries `next` across both (modern allauth stashes the pending redirect in the login stage/session, but the hidden field on *both* forms is the belt-and-suspenders that guarantees it). §9 adds the end-to-end test.
- **Book-specific footer link (MUST-FIX #6):** `login.html:32` links `{% url 'account:lookup' %}` ("Booked as a guest? Look up your class.") — a **namespaced** view (not an allauth `account_*` name), so it's **not in the guilds allowlist → 404 on `.app`**, and the copy is book-only. Fix: branch the footer links on surface — `{% if not is_guilds_surface %}` keep the lookup line; on the guilds surface drop it (keep "New to Past Lives? Create an account" → `account_signup`, which *is* allowlisted). Recommend branching the copy over widening the allowlist with off-topic routes.
- **Flow:** email → `account_request_login_code` (with hidden `next`) → code emailed → `confirm_login_code` (hidden `next`) → authenticated on `guilds.pastlives.app` → redirect to `next` = the exact guild page. All these `account*` view names are in the guilds allowlist (§6.0).
- **Controls / Save:** "Email me a login code" and "Verify" buttons (`bk-btn-primary`), each an obvious primary submit; success = redirect to `next` (full page).
- **States:** *Empty* — the form. *Loading* — submit button; boosted nav shows the loading bar. *Error* — allauth renders invalid-email / wrong-or-expired-code inline in the `.bk-auth` card (friendly, not a 500). *Success* — lands back on the guild page, now able to act; a "Welcome back" message toast. *No dead ends* — "Create an account" + "Become a member" links present.
- **Session:** host-only cookie issued on `.app` (§10 #1). **Dark + light / mobile:** `.bk-auth` is already responsive and dual-theme; verify with the guilds cp-topbar above it.

---

### 6.5 In-place actions once logged in

All four are **full-page POST → `redirect("hub_guild_detail", slug=…)`**, i.e. a relative `/guilds/<slug>/` that stays on `.app`. Per the FRONTEND.md interaction table, full-page posts use **Django messages** (drained into toasts by the base template) — *not* HTMX 204 + `trigger_toast`. `ToastFlashMiddleware` carries the message across the boosted redirect so the toast fires exactly once.

| Action | Trigger | Feedback | Empty/Loading/Error/Success |
|---|---|---|---|
| **Join** | `pl-btn--primary` form submit (§6.2) | success toast "You joined {guild}." | Loading: global bar. Error: none expected (idempotent `get_or_create`). Success: page re-renders with Leave button + orientation prompt. |
| **Leave** | `pl-btn--danger pl-btn--sm` → **`confirm_modal.html`** (§6.2) | success toast "You left {guild}." | Success: page shows "Log in to join" replaced by Join. |
| **Book orientation** | per-slot "Request" → **`confirm_modal.html`** (existing) → POST `hub_orientation_book` | success toast "Orientation requested! Check your email…"; on `OrientationError` → **error toast** with the domain message (e.g., slot full / already have a live booking) | Empty: "No orientation times posted." Loading: bar. Error: error toast, page unchanged. Success: partial now shows "Your orientation … Requested — awaiting confirmation," with a **Cancel** button. |
| **Request custom time** | toggle-revealed form (`x-show`, closed by default — a secondary/optional form per the interaction table) with `form_field.html` `starts_at` (datetime-local) + `note` → POST `hub_guild_orientation_request_custom` | success toast "Your orientation request was sent…"; invalid/past time → error toast "Pick a valid future time…" | Loading: bar. Error: error toast (form re-validates server-side via `clean_starts_at`). Success: partial flips to the "awaiting confirmation" state. |
| **Cancel own booking** | `hub-btn--sm hub-btn--danger` → **`confirm_modal.html`** (existing) → POST `hub_orientation_cancel_mine` | success toast; frees the seat | Success: partial returns to the slots list. |

- **Date/time picker (Rule 14) — verify only, no new CSS (corrected):** the `starts_at` widget already sets `onclick="this.showPicker?.()"`, and the field renders inside **`.pl-form-group`** (via `form_field.html`, `:28`). `components.css` already themes the native picker for both modes via `color-scheme` — `dark` by default (`:491`) and `light` under `[data-theme="light"]` (`:948`). Because the light-default theme on this surface sets `data-theme="light"` on `<html>`, the light rule applies and the field is legible on the light page; a guest who toggles to dark gets the dark rule. **Do not add a `filter: invert` rule or scope anything to `.hub-form-group`** (that was the earlier draft's bug: wrong wrapper class + a mechanism that fights `color-scheme`). §9 verifies the custom-time field in **both** themes on the light-default surface. The `x-show` reveal keeps `display` in a CSS class, never inline (Rule 12) — reuse the existing `pl-orient-slots`/`hub-form` structure which already obeys this.
- **Toggles / lists:** none of these actions is a boolean toggle or an editable list, so §1/§3-toggle rules don't add controls here (the only destructive controls — Leave, cancel — correctly use confirm modals with `pl-btn--danger pl-btn--sm`, checklist §3). Confirmed: no `+Add`/per-row Delete formset is introduced on this surface.
- **Dark + light / mobile:** all reuse hub components already dual-theme + responsive; verify the toasts and confirm modals over the light default.

---

### 6.6 Per-guild SEO / Open Graph — inline in `guild_detail.html`'s `extra_head`

So a shared `guilds.pastlives.app/guilds/<slug>/` link previews with the guild's identity.

- **`extra_head` block collision (MUST-FIX #8):** `guild_detail.html:7` currently overrides `{% block extra_head %}` with only `<link … calendar.css>` and **no `{{ block.super }}`**. On `hub/base.html` that's harmless (its `extra_head` is empty), but on `guilds/base_public.html` the base `extra_head` carries the fonts + `cms-public.css` + `cms-guilds.css` — a child override without `super` **discards all of it**, breaking the aesthetic. **Fix:** make it pull the parent in and append:
  ```django
  {% block extra_head %}{{ block.super }}
  <link rel="stylesheet" href="{% static 'css/calendar.css' %}">
  {% if is_guilds_surface %}{# ── OG / SEO — only where links are shared publicly ── #}
    <link rel="canonical" href="{{ GUILDS_BASE_URL }}{% url 'hub_guild_detail' guild.slug %}">
    <meta property="og:type" content="website">
    <meta property="og:title" content="{{ guild.name }} — Past Lives Guilds">
    <meta property="og:description" content="{{ guild.about|striptags|truncatewords:30|default:'A guild at Past Lives Makerspace in Portland, OR.' }}">
    <meta property="og:url" content="{{ GUILDS_BASE_URL }}{% url 'hub_guild_detail' guild.slug %}">
    <meta property="og:image" content="{% if guild.banner_image %}{{ guild.banner_image.url }}{% else %}{{ GUILDS_BASE_URL }}{% static 'img/og-guild-default.png' %}{% endif %}">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="description" content="{{ guild.about|striptags|truncatewords:30|default:'A guild at Past Lives Makerspace in Portland, OR.' }}">
  {% endif %}{% endblock %}
  ```
  This keeps the calendar CSS on **both** surfaces (`block.super` is empty on members, the fonts/CSS on guilds), and emits OG tags **only** on the guilds surface (members pages are login-gated, so scrapers never hit them). No separate `og_meta` sub-block is needed, which also sidesteps the nested-block-through-`block.super` subtlety.
- **Absolute URLs:** `og:url`/`canonical`/the fallback `og:image` are built from a new **`GUILDS_BASE_URL`** setting (mirrors `MEMBER_BASE_URL`/`BOOK_BASE_URL`) so they always canonicalize to `https://guilds.pastlives.app/...` regardless of which host rendered them. `banner_image.url` from R2/media storage is already absolute.
- **States:** every field is guarded (`|default:` copy for a blank `about`; banner-vs-fallback image) so a bare guild still emits a valid, non-empty preview. `og:image` fallback = `img/og-guild-default.png` (flag: asset to produce; do **not** use the `.svg` logo — social scrapers handle SVG poorly). No user-facing UI; verified via the rendered `<head>` in a spec (§9) and a scraper preview at QA.

---

## 7. Notifications / emails / activity

**No new emails or notifications.** The reused orientation actions already send their own emails through the existing orientation flow (`membership/orientations.py`), unchanged. The only email a guest triggers is the **allauth login code**, which sends a numeric code to type (host-agnostic — no host-specific link to get wrong). Verify at QA that the login-code email contains no hard-coded members/book-host link that would pull the guest off `.app`.

---

## 8. Build order (phased; each phase ships green)

Each phase is independently shippable (full suite + `ruff` + `mypy`).

1. **Plumbing + model.** `GUILDS_HOSTS`/`GUILDS_ALLOWED_VIEW_NAMES`/`GUILDS_BASE_URL` settings; `SurfaceMiddleware` guilds branch + allowlist gate (no cookie step — prod `COOKIE_DOMAIN` confirmed unset); `surface()` new flags + `parent_template`; `hub/base.html` four gates → `is_guest_surface`; `Guild.is_featured` + migration + `GuildManager.directory()`. Specs: middleware routing/gate, context flags, `directory()` ordering/filtering. (No visible feature yet, but green.)
2. **Directory.** `guild_directory` view + `/guilds/` route + `templates/guilds/base_public.html` + `templates/guilds/directory.html` + `cms-guilds.css` (hero/grid/card/empty). Renders for anon in guest chrome. Spec: anon 200, featured-first + alpha, excludes inactive/soft-deleted, count-only (no roster names in HTML), empty state.
3. **Guest guild page + gating.** `guild_detail` extends `guilds_page_base` and uses `{{ block.super }}` in `extra_head`; the two guest-safety view tweaks (roster gated on `member`; `can_edit_this_guild` forced `False` on the guilds surface); anon Join / surface-gated Leave / orientation branches; `guild_orientation.html` "Log in to book" branch; class links → absolute `BOOK_BASE_URL`; "Teach a Class" and the entire buyables/cart/EYOP block + JS gated out; lead-contact modal behind auth; member-count chip → plain span; Become-a-member footer. Spec: MUST-FIX #1/#3/#4/#7/#9 rendering assertions + chrome/gating (see §9); POST actions as anon → login redirect with GET-path `next`, as member → perform + relative redirect.
4. **Login-on-`.app` + in-place actions.** `is_public_surface` → `is_guest_surface` in `login.html`/`request_login_code.html`/`confirm_login_code.html`/`signup.html`; **wire hidden `next` on both the request-code and confirm-code forms** and branch off the book-only `account:lookup` link; verify email-code login lands back on the exact guild page on `.app` (cookie is already host-only — no work); exercise Join/Leave/book/custom/cancel end-to-end. Spec: `guest_login_flow_spec` proves `?next=` survives both steps; login-code flow renders the pretty card on the guilds surface; action success/error toasts.
5. **SEO / OG.** Inline OG/canonical tags in the guild page's `extra_head` (guarded `is_guilds_surface`) + `GUILDS_BASE_URL` absolute URLs + fallback OG image asset. Spec (`guild_detail_head_spec`): `<head>` retains base CSS via `block.super` and emits guarded, absolute OG tags for a full guild and a bare guild.
6. **Housekeeping (last):** bump `plfog/version.py` `VERSION` and add the member-facing CHANGELOG entry (below). Infra ticket (separate, §10): Render custom domain + DNS + env.

> Spec only — do not build until approved.

**Changelog entry (net-new member-facing feature → new grouped entry at the top, stamped at the bumped VERSION, e.g. `0.20.2`):**

> **Title:** "A public home for our guilds"
> - Anyone can now browse every Past Lives guild at **guilds.pastlives.app** — no login needed. See what each guild is about, when it meets, its classes, photos, and FAQs.
> - Ready to jump in? Log in right on the guilds site to join a guild or book an orientation — you stay on the same page the whole time.
> - Guest visitors see how many members a guild has (never names), and can view upcoming orientation times before deciding to sign in.
> - Share a guild's link anywhere and it now shows a proper preview with the guild's banner and description.

(If the team treats a new subdomain as a minor release, bump to `0.21.0` and start the matching release branch instead — flagged in §10.)

---

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*` (never `context_*` — not collected), factory-boy, run in the `plfog-web` Docker image, ≥98% coverage gate (`--no-cov` for subsets while iterating).

Every MUST/SHOULD-FIX below has a named test so the fix is *proven*, not asserted in prose. Exercise the guilds surface with `HTTP_HOST=guilds.pastlives.app` + a `GUILDS_HOSTS` override so the middleware branch actually runs; assert against **rendered HTML** for gating/privacy (per the nested-form lesson — template-structure bugs escape context-only assertions).

- **`core/spec/middleware/surface_middleware_spec.py`:**
  - `describe_guilds_surface`: host in `GUILDS_HOSTS` → `request.surface == "guilds"`; `/` → 302 `/guilds/`; allowlisted view names (directory, guild detail, join/leave, orientation book/custom/cancel, account login/request-code/confirm-code/signup) pass; **denied** names 404 (`/settings/...`, `/billing/...`, `/classes/admin/...`, `/classes/register/...` **and** — importantly — the guild *editor* `hub_guild_edit`, the product/cart endpoints `hub_guild_product_create`/`hub_guild_cart_confirm`, and the book-only `account:lookup`); `Resolver404` path → 404.
  - `describe_host_scope_cookies` *(optional guard only)*: since prod `COOKIE_DOMAIN` is confirmed unset, this covers the belt-and-suspenders step **only if it's built** — with `SESSION_COOKIE_DOMAIN` set in the test, a guilds-surface response has `Domain` stripped from `sessionid`/`csrftoken`; other surfaces untouched. Skip entirely if the safety-net step isn't implemented.
- **`core/spec/context_processors_spec.py`:** `surface()` on a guilds request → `is_guilds_surface` True, `is_guest_surface` True, `guilds_page_base == "guilds/base_public.html"`, `parent_template == "guilds/base_public.html"`; public request → `is_guest_surface` True but `is_guilds_surface` False; members request → both False, `guilds_page_base == "hub/base.html"`.
- **`membership/spec/models/guild_spec.py`:** `describe_directory`: featured guild(s) sort before non-featured; within a group alphabetical; excludes `is_active=False` and soft-deleted; `is_featured` defaults False.
- **`hub/spec/views/guild_directory_spec.py`:** anon GET 200; lists active guilds; ordering featured-first/alpha; soft-deleted/inactive absent; **no roster member names in the response body** for anon (assert count chip present, individual `display_name`s absent); empty-state copy when zero guilds.
- **`hub/spec/views/guild_detail_spec.py`** — anon GET 200 on the guilds host, then:
  - **MUST-FIX #1 (roster privacy):** build a guild with `show_members=True` and several members, GET as an **anonymous** client on the guilds host → assert **none** of the members' `display_name`s appear in the body (only the numeric count chip). Repeat as a logged-in member → names *do* appear. (This is the leak the reviewer caught: today `roster` ignores the viewer.)
  - **MUST-FIX #3 (class links + Teach):** featured/upcoming class links in the body are **absolute** `BOOK_BASE_URL`-prefixed (not a bare `/classes/…`); the "Teach a Class" button is **absent** on the guilds surface (present on the members render).
  - **MUST-FIX #4 (editor buttons):** log in a user who is this guild's **lead**, GET on the guilds host → assert **no** editor affordances in the body ("Edit Guild Page", "Manage meeting notes", "Adjust placement", product add/edit/delete); the same lead on the members host still sees them.
  - **SHOULD-FIX #7 (lead contact privacy):** anon render → the lead's `phone`/`discord_handle`/`about_me`/email are **absent** and the row has no `open-modal` hook; logged-in member render → the contact modal + details are present.
  - **MUST-FIX #9 (buyables suppressed):** anon and member renders on the guilds host contain **no** `guildCart(`, no Add-to-Cart/EYOP modal markup, and **no** `cart/confirm` string; instead the "Buy … on the members hub" link (absolute `MEMBER_BASE_URL`) is present.
  - **Minor (member-count chip):** on the guilds surface the count chip is a plain `hub-badge` span with **no** `href` to `hub_member_directory`.
  - **Chrome + gating:** no `hub-sidebar` markup; orientation "Request" replaced by a `?next=`-carrying "Log in to book" link; "Log in to join" link present, no raw Join form; a member sees Join form / Leave confirm-modal trigger (and a **member on the members host does NOT** get a Leave button — proves the `is_guilds_surface` gate); action gating: anon POST to `hub_guild_join`/`hub_guild_leave`/`hub_orientation_book`/`hub_guild_orientation_request_custom`/`hub_orientation_cancel_mine` → 302 to `account_login` with `next`; member POST → performs and 302s to the **relative** `/guilds/<slug>/` (assert `Location` is relative → stays on `.app`).
- **MUST-FIX #2 (`?next=` end-to-end), `hub/spec/views/guest_login_flow_spec.py`:** seed a `MembershipPlan` + a member; simulate the guest flow on the guilds host — GET the guild page, follow the "Log in to join" link (assert its `?next=` == the guild path), POST the email to `account_request_login_code` **carrying the hidden `next`**, POST the code to `account_confirm_login_code` **carrying `next`**, and assert the final redirect `Location` is the **exact guild URL** on `.app`. Also assert the rendered login + confirm templates both contain the hidden `next` input (proves the wiring exists, not just that allauth happened to carry it).
- **MUST-FIX #8 (extra_head survives) + OG, `hub/spec/views/guild_detail_head_spec.py`:** render the guild page on the guilds host and assert the `<head>` still contains the base's `cms-public.css`/`cms-guilds.css`/fonts **and** `calendar.css` (proves `block.super` is used, not a discarding override); assert `og:title/og:description/og:image/og:url` + `canonical` present and **absolute** (`startswith(GUILDS_BASE_URL)`); a guild with blank `about`/no banner still emits non-empty tags (fallback copy + `og-guild-default.png`); on the **members** render (login-gated) the OG block is absent (guarded by `is_guilds_surface`).
- **MUST-FIX #5 (datetime theme) — manual/QA note in the spec, plus a light assertion:** the custom-time field is `.pl-form-group`-wrapped and relies on the existing `components.css` `color-scheme` rules; a spec asserts the field renders inside `.pl-form-group` (so the themed rules apply) — the visual legibility in light **and** dark is verified in a browser at QA (light is the default here).
- **Gotchas:** member-gated login tests must seed a `MembershipPlan` before login (else no `Member` is created); orientation future-time validation is tz-sensitive (`clean_starts_at` compares to `timezone.now()`); always pass `HTTP_HOST=guilds.pastlives.app` + a `GUILDS_HOSTS`/`GUILDS_BASE_URL`/`BOOK_BASE_URL`/`MEMBER_BASE_URL` settings override so hosts and absolute links resolve deterministically.

---

## 10. Open / deferred

1. **`COOKIE_DOMAIN` — RESOLVED, not blocking.** Confirmed via the Render API this session that prod `COOKIE_DOMAIN` is **unset** (no env group sets it; the `settings.py` comment is aspirational). Prod cookies are therefore already host-only, and in-place login on `.app` works with **no plumbing**. The `Domain`-strip middleware step is downgraded to an **optional** one-line safety net to build *only if* a future env group ever sets `COOKIE_DOMAIN=.pastlives.space` (§2 #4, §6.0). Nothing to confirm before build.
2. **Infra (handled separately, but stated here):** `guilds.pastlives.app` needs a **Render custom domain** + a **DNS CNAME → `plfog.onrender.com`**, and env changes: add the host to **`DJANGO_ALLOWED_HOSTS`** and to **`CSRF_TRUSTED_ORIGINS`** (`https://guilds.pastlives.app` — forms POST on this host), and set the new **`GUILDS_HOSTS=guilds.pastlives.app`** (and `GUILDS_BASE_URL=https://guilds.pastlives.app`). **Do not** add it to `PUBLIC_HOSTS` — that would serve the class catalog and redirect `/` to `/classes/`, not the guild directory.
3. **Guest-chrome error pages (nice-to-have).** A 404 for a bad slug / denied path on `.app` should ideally render in guest chrome; at minimum the standard error page is acceptable. A branded `guilds/404.html` is deferred unless quick.
4. **Leave stays gated to the guilds surface (coordinator decision).** Per the review, the Leave button is gated `and is_guilds_surface` (§6.2) so this feature does **not** silently add a Leave button to the members hub. **Open item:** Josh may later choose to surface Leave on the members hub too — if so, drop the `is_guilds_surface` clause; until then it's guilds-only.
5. **`Guild.is_featured` — keep it.** It's the one genuine model gap (there is no "featured guild" flag today, only `featured_class` which is a *class*), and it's the right home for the directory's "featured first" ordering. Confirmed to keep.
6. **Release numbering (decide at build time).** Spec assumes a patch bump on `release-0.20.x` (`0.20.2`). If a new public subdomain is treated as a minor feature, use `0.21.0` and the matching release branch.
7. **OG fallback image asset (decide/produce at build time):** `img/og-guild-default.png` needs to exist for guilds without a banner; dimensions ~1200×630 for `summary_large_image`.
8. **Explicitly out of scope (v1):** directory search/filter; any Stripe/buyables commerce on `.app` (members-hub link only); guild-editor/admin surfaces on `.app` (allowlist excludes them); notification/email changes.

---

# ============================================================
# SPEC EXTENSION v2 — Shareable guild URL, QR code & printable flyer
# (layers on top of the guest guild page defined above; ships with/after it)
# ============================================================

**Status:** Spec only — not yet approved to build. *(unchanged)*
**Added:** 2026-07-03 — validated Guild-Leads pain point: every guild needs an easy, live, shareable guest URL plus a QR code and a printable one-page flyer, so leads can hang "here is our guild's digital home page (rules, tutorials, announcements, meetings)" signage in the physical space. Multiple leads have already asked for a QR code.

> **Dependency on the guest surface (read first).** Everything here points at the **public guest guild page** defined in §1–§6 (`guilds.pastlives.app/guilds/<slug>/`). The vanity URL 301s to it, the QR encodes the vanity URL, and the flyer prints the vanity URL + QR. Therefore this feature **ships with, or after, the guest surface — never before.** Concretely it needs Phases 1–3 of §8 (the guilds surface + the public guest guild page rendering) already in place; it becomes the new **Phase 7**.

## 11. Locked decisions (v2 — present as decided)

| # | Decision | Choice |
|---|---|---|
| V1 | **Per-guild public guest URL** | Already the core of this spec: `guilds.pastlives.app/guilds/<slug>/`, public, no login (§1, §6.2). This is the canonical destination everything below resolves to. Confirmed, not re-opened. |
| V2 | **Short vanity URL** | A human-typable **`pastlives.app/g/<slug>`** on the **member host** that **301-redirects** to the guest page. This is what prints on the flyer and what the QR encodes (short, readable aloud, typable, memorable). New URLconf entry + a tiny public redirect view. Unknown/soft-deleted slug → 404. Reachable **pre-login** (no `@login_required`). |
| V3 | **QR code** | Generated from the **vanity URL**, downloadable as **SVG (default) and PNG** from the guild editor's "Flyer & QR" control. Generator = **`segno`** (pure-Python, no system deps, native SVG *and* PNG writers). |
| V4 | **Printable flyer** | A **print-optimized standalone HTML page** per guild (US-Letter print CSS, forced light), rendered from existing guild data + one new field. Leads open it and use the browser's **Print → Save as PDF**. **No server-side PDF library.** Standardized across all guilds (a mini, on-brand version of the guild page). |
| V5 | **New model field** | `Guild.essential_rules` — a short optional "essential/safety rules" text field (the full `about` is too long to print). Additive migration. Surfaced in `GuildEditForm` + the editor UI; consumed by the flyer. |
| V6 | **Distribution scope (v1)** | **Lead-only** — the Flyer & QR controls live on the guild editor (permission = same as guild edit). An admin "print all guild flyers" bulk convenience is **deferred to v2** (§14). |

**Why encode the *vanity* URL in the QR, not the guest URL directly (durability):** the QR resolves through `pastlives.app/g/<slug>` → guest page. If the guest host ever moves (or the guest URL shape changes), we repoint one redirect view and **every already-printed flyer/QR keeps working** — no reprints. This is the deciding reason the QR targets the vanity route.

---

## 12. Vanity URL — `pastlives.app/g/<slug>` (member host, public 301)

**Home:** `core` (routing/plumbing — consistent with §3's "core = routing/plumbing"). **Not** `hub` (every hub view is `@login_required` by convention — `hub/CLAUDE.md`; a public view there would violate it).

**URLconf — `core/urls.py`** (mounted at `""` in `plfog/urls.py`, so it's root-level on the member host):
```python
path("g/<slug:slug>/", views.guild_vanity_redirect, name="guild_vanity"),
```

**View — `core/views.py`** (no decorator → public/pre-login):
```python
def guild_vanity_redirect(request: HttpRequest, slug: str) -> HttpResponse:
    """Public, human-typable pastlives.app/g/<slug> → 301 to the guest guild page.

    The default Guild manager hides soft-deleted guilds, so an unknown OR
    soft-deleted slug 404s. Permanent (301) because the vanity ↔ guild mapping is
    stable; the QR/flyer encode THIS route so the guest host can move without reprints.
    """
    guild = get_object_or_404(Guild, slug=slug)
    target = f"{settings.GUILDS_BASE_URL}{reverse('hub_guild_detail', args=[guild.slug])}"
    return HttpResponsePermanentRedirect(target)
```

**Fat-model helper — `Guild.vanity_url` (`membership/models.py`)** — one source of truth for the string that the QR encodes and the flyer prints:
```python
@property
def vanity_url(self) -> str:
    """Absolute human-typable share URL, e.g. https://pastlives.app/g/ceramics/."""
    from django.urls import reverse
    return f"{settings.MEMBER_BASE_URL}{reverse('guild_vanity', args=[self.slug])}"
```

**Reachability notes:**
- **Member host, pre-login:** works with no plumbing — there is no `LoginRequiredMiddleware` (`plfog/settings.py:116`); login is per-view. The members-surface handler (`core/middleware.py:79`) only bounces `PUBLIC_ONLY_PATH_PREFIXES` (`/account/`), so `/g/…` is untouched. **Do not** add `/g/` to `MEMBER_ONLY_PATH_PREFIXES` (that would 404 it on book and defeats the "public, reachable anywhere" goal).
- **Vanity host = `MEMBER_BASE_URL`** (prod env sets this to `https://pastlives.app`). No new setting.
- **On the guilds host:** the `guild_vanity` view name is **not** in `GUILDS_ALLOWED_VIEW_NAMES`, so `guilds.pastlives.app/g/<slug>` 404s — fine, because the QR/flyer always point at the member host. *(Optional nicety, default off: add `guild_vanity` to the allowlist so the short URL also works if typed on the guilds host. Listed as an open question, §16.)*

---

## 13. QR code — `segno`, downloadable SVG/PNG from the editor

**Dependency (state clearly, safe for Render):** add **`segno`** to `requirements.txt` (pin a floor, e.g. `segno>=1.6`). It is a **single pure-Python wheel with zero system dependencies** — it ships its own PNG and SVG encoders, so it needs **no Pillow, no cairo/pango, no native build step**. This is the deciding contrast: `qrcode` needs Pillow to emit PNG, and `WeasyPrint`/`reportlab` need native libs — both are avoided. `segno` cannot break the Render build.

**Fat-model QR methods — `Guild` (`membership/models.py`)** encode `self.vanity_url`:
```python
def qr_svg(self) -> str:
    """Inline SVG markup of this guild's vanity-URL QR (crisp at any print size)."""
    import io, segno
    buf = io.StringIO()
    segno.make(self.vanity_url, error="m").save(buf, kind="svg", scale=1, xmldecl=False, svgns=True)
    return buf.getvalue()

def qr_png_bytes(self) -> bytes:
    """PNG bytes of the same QR (segno's native writer — no Pillow)."""
    import io, segno
    buf = io.BytesIO()
    segno.make(self.vanity_url, error="m").save(buf, kind="png", scale=10, border=2)
    return buf.getvalue()
```

**Download view — `hub/views.py`** (editor permission, member host):
```python
@login_required
def guild_qr_download(request: HttpRequest, pk: int, fmt: str) -> HttpResponse:
    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)   # reuse §6 permission helper
    if forbidden is not None:
        return forbidden
    if fmt == "svg":
        resp = HttpResponse(guild.qr_svg(), content_type="image/svg+xml")
    elif fmt == "png":
        resp = HttpResponse(guild.qr_png_bytes(), content_type="image/png")
    else:
        raise Http404
    resp["Content-Disposition"] = f'attachment; filename="{guild.slug}-qr.{fmt}"'
    return resp
```

**URLconf — `hub/urls.py`:**
```python
path("guilds/<int:pk>/qr.<str:fmt>/", views.guild_qr_download, name="hub_guild_qr"),
```

**Default:** **SVG** (vector — perfect at any flyer size, tiny file). **PNG** offered alongside (segno-native) for leads pasting into Canva/Docs. *(SVG-vs-PNG default is a stated open question — §16.)*

**Note:** the flyer embeds the QR **inline** (`guild.qr_svg()` rendered directly into the print template) so printing fetches no second asset and the code scales exactly to the print box. The download endpoint is only for leads who want a standalone image file.

---

## 14. Printable flyer — standalone print page + `Guild.essential_rules`

### 14.1 New model field — `Guild.essential_rules` (additive)

| Field | Type | Note |
|---|---|---|
| `essential_rules` | `TextField(blank=True, default="", help_text="Short essential/safety rules shown on your printable flyer. Keep it brief — your full About is too long to print.")` | The full `about` is too long for a flyer; this is the short, printable safety/essentials blurb. Optional. |

**Migration:** `membership/migrations/00XX_guild_essential_rules` — a plain `AddField` (reverse = automatic `RemoveField`; no data migration). Use the next available number (`ls membership/migrations/`; the UAT batch noted `0065` as next — verify at build time). If built in the same commit as any other additive guild field, they may share one migration; otherwise its own.

**Form — `GuildEditForm` (`hub/forms.py`):** add `"essential_rules"` to `Meta.fields`; `widgets["essential_rules"] = forms.Textarea(attrs={"rows": 3, "placeholder": "e.g. Closed-toe shoes in the shop. No solo use of the kiln. Sign in at the front desk."})`; `labels["essential_rules"] = "Essential / safety rules (for the flyer)"`; `help_texts["essential_rules"] = "Shown on your printable flyer. Keep it to a few short lines."`. Rendered via `components/form_field.html` (wraps in `.pl-form-group`, so it themes correctly — FRONTEND.md Rule 13). Place it on the **Basic Information** tab, directly under `about`.

### 14.2 Flyer view + URL (lead-only, member host)

**View — `hub/views.py`:**
```python
@login_required
def guild_flyer(request: HttpRequest, pk: int) -> HttpResponse:
    """Print-optimized one-page flyer for a guild (leads → Print → Save as PDF)."""
    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    return render(request, "hub/guild_flyer.html", {"guild": guild, "qr_svg": guild.qr_svg()})
```
**URLconf — `hub/urls.py`:** `path("guilds/<int:pk>/flyer/", views.guild_flyer, name="hub_guild_flyer")`.

Lead-only via `_require_can_edit_guild` (admins/officers/lead/staff — the §6 permission helper). Keyed on `pk` to match every other guild-editor endpoint.

### 14.3 Print template — `templates/hub/guild_flyer.html` (standalone, no member chrome)

- **Does NOT extend `hub/base.html`** — it's its own minimal `<!doctype html>` document with **no sidebar, no topbar**, only the flyer + a "Print / Save as PDF" button that's hidden in print. This is the FRONTEND.md-allowed "minimal standalone template" for print.
- Loads **`static/css/guild-flyer.css`** (new) + Playfair/Lato fonts (mirrors the book aesthetic). **Forced light, print-safe** — dark/light is irrelevant (print is always light); the CSS hardcodes the on-brand light palette (white bg, navy `#092E4C` headings, gold `#EEB44B` accent) rather than reading theme tokens, so it's identical no matter the lead's theme.
- **`@page { size: letter; margin: 0.5in; }`** + an `@media print { .no-print { display:none } }` for the on-screen Print button. Standardized layout — **no per-guild layout knobs**; the same frame renders every guild, only the data changes.
- **Content (essential only, auto-composed):**
  1. **Header band:** color logo (`img/guild_logos/<logo_prefix>_color.svg`, or banner thumbnail, or initial) + **guild name** (Playfair).
  2. **One-line about:** `{{ guild.about|striptags|truncatewords:20 }}`.
  3. **Contact block:** "Led by {{ guild.guild_lead.display_name }}"; contact email (`guild.contact_email` else the lead's `primary_email`); Discord (`guild.discord_url`).
  4. **Next meeting:** `{{ guild.next_meeting_at|date:'l, M j' }}` + `guild.meeting_location` / `guild.meeting_schedule` (or "TBA").
  5. **Essential / safety rules:** `{{ guild.essential_rules|linebreaksbr }}` (section hidden if blank).
  6. **QR + call-to-action panel:** the inline `{{ qr_svg }}`, the caption **"Scan for our digital home page — rules, tutorials, announcements & meetings,"** and the **vanity URL in plain text** (`{{ guild.vanity_url }}`) beneath it so it works even if the camera fails.
- **Empty-field resilience:** every optional block guards itself (`{% if %}`), so a sparse guild still yields a clean, non-broken one-pager (name + QR + vanity URL are always present).

### 14.4 Editor UI — the "Flyer & QR" control

On `templates/hub/guild_edit.html`, add a **"Share & Print" `hub-card`** (on the Basic Information tab, below the fields — or its own small tab; recommend the card to avoid tab sprawl) containing:
- The **vanity URL** shown as selectable/copyable text (`{{ guild.vanity_url }}`) with a one-line hint "Print this on signage or read it aloud — it opens your public guild page."
- **"Open printable flyer"** → `{% url 'hub_guild_flyer' guild.pk %}` (`target="_blank"`, `pl-btn pl-btn--primary`) — opens the flyer; the lead hits Ctrl/Cmd-P → Save as PDF.
- **"Download QR (SVG)"** → `{% url 'hub_guild_qr' guild.pk 'svg' %}` and **"Download QR (PNG)"** → `…'png'` (`pl-btn pl-btn--secondary`).
- A small inline preview of the QR (`{{ guild.qr_svg }}`) so the lead sees it without downloading. *(Optional; nice.)*

All buttons are `pl-btn` variants (FRONTEND.md). Because this card lives on the editor (already `_require_can_edit_guild`-gated), it's lead-only for free.

---

## 15. Distribution scope — lead-only (v1), admin bulk deferred

**Recommendation: lead-only for v1** (Decision V6). Each lead prints their own flyer / grabs their own QR from their editor. This is the whole validated pain point and needs no central surface.

**Admin "print all guild flyers" (deferred to v2):** a single admin page (`/manage/guild-flyers/`, admin-gated) that renders **every active guild's flyer stacked with `page-break-after: always`** between them, so one Print → Save as PDF yields a booklet. It reuses `guild_flyer.html` as an include and the same `guild-flyer.css`. Low effort, but not needed for v1 — leads asked for *their* QR, not a batch. Left as an **open question** for Josh (§16).

---

## Edits to fold into the existing spec sections

### → §2 reuse table (add rows)
| Need | Existing thing | Location |
|---|---|---|
| Unknown / soft-deleted slug → 404 for the vanity redirect | `get_object_or_404` + default `Guild` manager (`deleted_at__isnull=True`) | `membership/models.py:952` |
| Absolute guest/member base URLs for the redirect target & vanity string | `GUILDS_BASE_URL` (this spec, §6.6) / `MEMBER_BASE_URL` | `plfog/settings.py:64` |
| Public-view-with-no-login is the default on the member host | no `LoginRequiredMiddleware`; login is per-view `@login_required` | `plfog/settings.py:116` |
| Editor permission gate for QR/flyer endpoints | `_require_can_edit_guild(request, guild)` | `hub/views.py:509` |
| Guild-edit form to extend for the new field | `GuildEditForm` (`Meta.fields`/`widgets`/`labels`/`help_texts`) | `hub/forms.py:52` |
| On-brand print assets | color logos `img/guild_logos/<prefix>_color.svg`; Playfair/Lato + book palette | `static/img/guild_logos/`, `static/css/cms-public.css` |

### → §2 "Genuine gaps" (append)
5. **`segno` dependency** — new pure-Python QR generator (no Pillow/native libs; safe for Render). Add to `requirements.txt`.
6. **`Guild.essential_rules`** text field + additive migration (the short printable rules blurb).
7. **`Guild.vanity_url` property + `qr_svg()` / `qr_png_bytes()` methods** — fat-model helpers the redirect/QR/flyer all reuse.
8. **Vanity redirect** (`core` view + `/g/<slug>/` URL, public 301).
9. **QR download** (`hub` view + `guilds/<pk>/qr.<fmt>/` URL, editor-gated).
10. **Flyer** (`hub` view + `guilds/<pk>/flyer/` URL + `templates/hub/guild_flyer.html` + `static/css/guild-flyer.css`, editor-gated, standalone print doc).

### → §3 "Where the code lives" (append)
```
requirements.txt           # + segno>=1.6  (pure-Python QR; no Pillow/native libs)
core/
  urls.py                  # + path("g/<slug>/", guild_vanity_redirect, name="guild_vanity")
  views.py                 # + guild_vanity_redirect (public, 301, no @login_required)
  spec/views/guild_vanity_spec.py            # NEW
membership/
  models.py                # Guild.vanity_url property; Guild.qr_svg()/qr_png_bytes(); essential_rules field
  migrations/00XX_guild_essential_rules.py   # additive AddField
  spec/models/guild_spec.py                  # + vanity_url, qr_*, essential_rules default
hub/
  views.py                 # + guild_qr_download(pk, fmt); guild_flyer(pk)  (both editor-gated)
  urls.py                  # + hub_guild_qr, hub_guild_flyer
  forms.py                 # GuildEditForm: + "essential_rules" (Meta.fields/widgets/labels/help_texts)
  spec/views/guild_qr_spec.py                # NEW
  spec/views/guild_flyer_spec.py             # NEW
templates/hub/
  guild_flyer.html         # NEW standalone print doc (no sidebar chrome; @page letter; inline QR)
  guild_edit.html          # + "Share & Print" hub-card (vanity URL, Open flyer, Download QR svg/png); essential_rules field on Basic tab
static/css/
  guild-flyer.css          # NEW print CSS (@page letter, forced light, on-brand)
```

### → §8 build order (add Phase 7; keep the "spec only" gate)
7. **Vanity URL + QR + flyer (depends on Phases 1–3 — the guest guild page must exist).**
   - 7a. `segno` dep; `Guild.essential_rules` + migration; `Guild.vanity_url` / `qr_svg` / `qr_png_bytes`; `GuildEditForm` field.
   - 7b. `core` vanity redirect view + `/g/<slug>/` URL (public 301; 404 on unknown/soft-deleted).
   - 7c. `hub` QR download view + URL (editor-gated, svg/png).
   - 7d. `hub` flyer view + URL + `guild_flyer.html` + `guild-flyer.css`; the "Share & Print" card on the editor.
   - Each sub-step ships green (full suite + `ruff` + `mypy`). Housekeeping/version bump stays the last thing (§8.6).

### → §9 testing (add specs)
- **`core/spec/views/guild_vanity_spec.py`:** `/g/<slug>/` as **anonymous** → 301 with `Location == GUILDS_BASE_URL + /guilds/<slug>/`; unknown slug → 404; **soft-deleted** guild's slug → 404; assert the view carries **no** `@login_required` (anon reaches it). Also assert it resolves on the member host (not gated by `PUBLIC_ONLY_PATH_PREFIXES`).
- **`membership/spec/models/guild_spec.py`:** `vanity_url` == `MEMBER_BASE_URL + /g/<slug>/`; `qr_svg()` returns non-empty `<svg …>` markup that **contains/encodes the vanity URL**; `qr_png_bytes()` returns bytes with the PNG magic header (`\x89PNG`); `essential_rules` defaults to `""`.
- **`hub/spec/views/guild_qr_spec.py`:** a **lead** GET `…/qr.svg/` → 200, `Content-Type image/svg+xml`, `Content-Disposition: attachment`; `…/qr.png/` → 200, `image/png`; a **non-editor** member → 403; anon → login redirect; a bad `fmt` → 404.
- **`hub/spec/views/guild_flyer_spec.py`:** a **lead** GET `…/flyer/` → 200; body contains the guild name, the **vanity URL text**, an inline `<svg` QR, and `essential_rules` when set (absent when blank); the response has **no `hub-sidebar`** markup (standalone chrome); a **non-editor** → 403; anon → login redirect.
- **Gate the whole feature behind the guest page in CI order:** the vanity spec asserts the redirect *target* is the guest URL, so it implicitly documents the dependency.

### → §10 open / deferred (append)
9. **Vanity path shape** — `/g/<slug>/` (recommended: shortest) vs `/guild/<slug>/`. Recommend `/g/`; flip in one URL line if Josh prefers `/guild/`.
10. **QR default format** — SVG (recommended, vector) vs PNG. Both are offered; only the "primary" button label/order is the decision.
11. **Admin bulk "print all flyers"** — recommend **defer to v2** (§15); trivial to add later by stacking `guild_flyer.html` with page breaks.
12. **Vanity on the guilds host too** — optionally add `guild_vanity` to `GUILDS_ALLOWED_VIEW_NAMES` so `guilds.pastlives.app/g/<slug>` also redirects. Default off (QR/flyer use the member host).
13. **`essential_rules` beyond the flyer** — v1 shows it only on the flyer. Josh may later want it on the guest guild page hero too; additive, out of scope now.

## 16. Open questions for Josh
1. **Vanity path:** `pastlives.app/g/<slug>` (recommended) or `/guild/<slug>`?
2. **QR default download:** SVG (recommended) or PNG as the primary button?
3. **Admin bulk print:** ship v1 lead-only (recommended), or include a "print all guild flyers" admin page now?
4. **Flyer field name:** `essential_rules` (recommended) vs `safety_rules` — pick the label leads will recognize.
5. **Flyer reach:** keep lead-only behind the editor (recommended), or also expose a public `guilds.pastlives.app/guilds/<slug>/flyer/` so anyone can print it? (Low effort either way.)
