# Notifications Page (replace the bell dropdown) — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-10
**Surface:** FOG hub `pastlives.test` — the topbar notification bell + a new `/notifications/` page. Members-only (not a guest surface).
**Related:** Notification spine (`docs/` notification-redesign notes); reuses the existing `Notification` model + bell endpoints.

---

## 1. Summary

Today the topbar bell opens a 340px absolutely-positioned dropdown popover (`static/css/hub.css:2882+`, `.pl-bell__panel`). It's fine on a wide desktop and unusable on a phone — it overflows, can't scroll comfortably, and the "Mark all read" affordance is cramped. This change turns the bell into a **plain link** (keeping its unread-count badge) that navigates to a dedicated, fully responsive **Notifications page** at `/notifications/`. The page lists every one of the member's notifications, newest-first, with unread rows visually emphasized; each row links through to whatever it's about (marking itself read on the way), and a prominent **"Mark all as read"** button clears the badge. The dropdown is removed entirely.

### Locked decisions

| Decision | Choice |
|---|---|
| Bell behavior on every screen | The bell becomes a simple `<a href="{% url 'notification_list' %}">` with its unread badge, on **desktop and mobile**. No dropdown, no Alpine `x-data`, no `@click.away`, no `hx-get` feed load. |
| Who owns `/notifications/` | The route (today `notification_feed`, returning the dropdown partial) is repurposed to serve the **full page**. New view + template extending `hub/base.html`. |
| URL name | Rename to **`notification_list`** (path unchanged: `/notifications/`). Self-documenting now that it's a page, not a feed partial. The only reference is the bell (being rewritten) + the spec — all enumerated below. |
| Read semantics | **Option (b), accurate badge.** Visiting the page does **not** auto-mark-all-read — members should *see* what's new highlighted. Each row marks only itself read on click-through; the "Mark all as read" button is an explicit action. |
| "Mark all as read" mechanism | A normal full-page **POST** to the existing `notification_read_all`, repointed to **redirect back to the page** (not a 204). The topbar re-renders on the redirect (`hx-boost`) and the badge clears. |
| Badge freshness | Comes from the `notification_badge()` context processor, recomputed on every full/boosted render. `<body hx-boost="true">` means navigating to the page (and the mark-all redirect) re-renders the topbar. **No new polling.** |
| Pagination | Notifications accumulate, so paginate: **20 per page**, newest-first, with prev / "Page X of Y" / next controls that reflow on mobile. |
| Empty state | `"You're all caught up."` in `hub-text-muted` — never a blank card. |

## 2. What already exists (reuse, don't reinvent)

| Need | Existing thing | Location |
|---|---|---|
| Notification records (user, title, body, url, read_at, created_at) | `Notification` model; `Meta.ordering=["-created_at"]`; indexes on `(user, read_at)` and `(user, -created_at)`; `is_unread` property; `mark_read()` method | `core/models.py:818-846` |
| The route to repurpose | `notification_feed` GET `/notifications/` → renders `hub/_notification_feed.html` (15 most recent) | `core/views.py:307-314`, `core/urls.py:32` |
| Per-item mark-read | `notification_read` POST `/notifications/<pk>/read/` → `mark_read()` → `redirect(note.url or "home")` — **keep as-is** | `core/views.py:327-338`, `core/urls.py:34` |
| Mark-all bulk update | `notification_read_all` POST `/notifications/read-all/` → bulk `.update(read_at=now())`, returns 204 — **repoint to redirect back to the page** | `core/views.py:341-351`, `core/urls.py:35` |
| Unread badge count | `notification_badge()` context processor → `unread_notification_count`; recomputed every render | `core/context_processors.py:94-102` |
| Bell include site + members-only gate | `{% include "hub/_notification_bell.html" %}` inside `{% if user.is_authenticated and not request.GET.public and not is_guest_surface %}` | `templates/hub/base.html:401` (gate opens `:363`) |
| Page skeleton to mirror | `tab_history.html` — `{% extends "hub/base.html" %}`, `.tab-page-header` + `.hub-page-title` + back link, `<div class="hub-card">`, `{% if rows %}…{% else %}` empty state | `templates/hub/tab_history.html` |
| Row markup + CSS to reuse | `<form action=notification_read>` + submit button per row; `.pl-note`, `.pl-note__btn`, `.pl-note--unread`, `.pl-note__title/__body/__time` | `templates/hub/_notification_feed.html`, `static/css/hub.css:2889-2896` |
| Paginator pattern (queryset) | `Paginator(qs, N)` + `get_page(request.GET.get("page", 1))`, page passed as `page` | member directory `hub/views.py:3549-3551`; simple prev/next markup `templates/hub/admin/_activity_pagination.html` |
| Page-size constant style | `_CALENDAR_PAGE_SIZE = 10` module constant | `hub/views.py:2843` |
| Boosted loading feedback | Global top progress bar, already included once in base — no per-page work | `templates/components/loading_bar.html` (base `:73`) |
| Django-message → toast bridge | Hub base renders `{% if messages %}` as toasts (`data-toast-message`) | `templates/hub/base.html:471-477` |
| Button styles | `.hub-btn` / `.hub-btn--sm` / `.hub-btn--ghost` | `static/css/hub.css:952-1005` |

**Gaps to close (small):**
- A view + template for the full page (replacing the partial view + `_notification_feed.html`).
- Rewrite the bell include to a plain link.
- Repoint `notification_read_all` to redirect (was 204).
- A pagination control block (dedicated `.pl-notes__pager` CSS class, no inline styles).
- Optional but recommended: a `NotificationQuerySet` helper so the view isn't inlining filters (fat model).
- Delete the now-dead dropdown CSS (`.pl-bell__panel/__header/__mark`) and the `.pl-note__empty` rule (empty state now uses `hub-text-muted`).

## 3. Where the code lives

```
core/
  models.py                 # + NotificationQuerySet (for_user / unread); objects = …as_manager()  [recommended]
  views.py                  # notification_feed  → RENAME to notification_list, returns the full PAGE
                            # notification_read_all → redirect back to the page (was 204) + success message
                            # notification_unread_count → LEFT UNUSED (dead; removal noted, out of scope)
  urls.py                   # path("notifications/", views.notification_list, name="notification_list")
  spec/…  (see §9)          # existing tests currently live at tests/core/notification_views_spec.py — rewritten there

templates/hub/
  notifications.html        # NEW — the page. extends hub/base.html. header + rows + mark-all + pager + empty state
  _notification_bell.html   # REWRITTEN — plain <a> + badge, no Alpine/HTMX/dropdown
  _notification_feed.html   # DELETED — dropdown partial, no other caller

static/css/
  hub.css                   # keep .pl-bell__btn (+position:relative) & .pl-bell__badge; DELETE .pl-bell__panel/__header/__mark
                            # restyle .pl-note* full-width already OK; + .pl-notes / .pl-notes__pager; light-theme unread + hover overrides
```

Home app: `core` (view/url/model/context-processor) + `templates/hub` (page/bell) + `static/css/hub.css`. All within existing coverage/mypy scope.

## 4. Data model

**No schema change. No migration.** The `Notification` model already has every field and index this feature needs (`core/models.py:818-834`).

Recommended (fat-model, still no DB change — `as_manager()` doesn't alter the table):

```python
class NotificationQuerySet(models.QuerySet):
    def for_user(self, user):        # relies on Meta.ordering = ["-created_at"]
        return self.filter(user=user)
    def unread(self):
        return self.filter(read_at__isnull=True)

class Notification(models.Model):
    ...
    objects = NotificationQuerySet.as_manager()
```

Call sites today inline `Notification.objects.filter(user=…, read_at__isnull=True)` (context processor, `notification_read_all`, the dead `unread_count`). The **new page view** and the **repointed mark-all** use `Notification.objects.for_user(user)` / `.unread()`; migrating the other inliners is optional polish, out of scope for this change.

## 5. Business logic (thin view, fat-ish model)

- **`notification_list(request)`** (`@login_required`): `qs = Notification.objects.for_user(request.user)`; `Paginator(qs, _NOTIFICATIONS_PAGE_SIZE)` with `_NOTIFICATIONS_PAGE_SIZE = 20`; `page = paginator.get_page(request.GET.get("page", 1))`. Pass `page` and `unread_count = qs.filter(read_at__isnull=True).count()` (drives whether the "Mark all as read" button shows) to `hub/notifications.html`. No business logic beyond querying — read semantics live in `mark_read()`.
- **`notification_read(pk)`** — unchanged: `mark_read()` (guarded: only sets `read_at` if currently `None`) then `redirect(note.url or "home")`. A row whose `url` is blank lands the user on home; that's the existing, intended behavior. (Watch-item, not a change here: if any **live** notification type routinely ships a blank `url`, prefer falling back to the list — `notification_list` — over `home`, so the member returns to context instead of the dashboard. No action unless such a type exists.)
- **`notification_read_all`** — change the tail: after the bulk `.update(read_at=now())` (only `.unread()` rows), **`messages.success(request, "You're all caught up.")` then `redirect("notification_list")`** instead of `HttpResponse(status=204)`. The button that posts here is the page's primary action, so it renders `hub-btn hub-btn--sm hub-btn--primary` (base `.hub-btn` carries *no* background/color — all visible styling comes from the modifier; `hub-btn hub-btn--sm` alone is transparent + inherited text + no border = invisible). The hub base converts the Django message into a toast; the redirect re-renders the topbar so the badge clears. (Bulk `.update()` is deliberate — no per-row `save()`, no N queries.) Note (intentional): from page 2+, `redirect("notification_list")` drops `?page` and lands on page 1 — harmless, since nothing is unread after the action.

## 6. UI / UX  ← completeness checklist applied

### Screen A — Notifications page (`templates/hub/notifications.html`)

- **Layout & container:** dedicated page, `{% extends "hub/base.html" %}`, `{% block content %}`. Header mirrors `tab_history.html`'s structure but **does not reuse `.tab-page-header`** — that class (`hub.css:1510`) is `display:flex; justify-content:space-between; gap:1rem` with **no `flex-wrap`**, so on a phone the title would compete with the action cluster in one row. Instead wrap the header in a new **`.pl-notes__header`** (`display:flex; justify-content:space-between; align-items:center; gap:1rem; flex-wrap:wrap`) so the whole right-hand cluster drops below the title on narrow screens. Inside: `<h1 class="hub-page-title">Notifications</h1>` on the left, and a right-hand action cluster (`.pl-notes__actions`) holding the "Mark all as read" button and a **"Back to Home"** back link (`{% url 'home' %}`, styled like tab_history's back link). Rows live in a single `<div class="hub-card">` (FRONTEND Rule 8).
- **Components used:** the page is a list, not a form-set — no `form_field`/`modal`/`toggle` needed. Pagination via a dedicated `.pl-notes__pager` block (mirrors `_activity_pagination.html` structure with a real CSS class instead of inline styles). Loading via the global boosted loading bar (already in base — nothing to add).
- **The controls, named explicitly:**
  - **Each notification row** = the existing pattern, restyled full-width: `<form method="post" action="{% url 'notification_read' n.pk %}" class="pl-note{% if n.is_unread %} pl-note--unread{% endif %}">` + `{% csrf_token %}` + `<button type="submit" class="pl-note__btn">` wrapping `.pl-note__title` (n.title), `.pl-note__body` (n.body), `.pl-note__time` (`{{ n.created_at|timesince }} ago`). Submitting POSTs `notification_read` → marks itself read → redirects to `n.url` (or home). These are **top-level forms** inside the hub-card — never nested — so no orphaned-submit bug (per the nested-form lesson).
  - **"Mark all as read"** = a real button, not a link, and it's the page's **primary action** so it takes the emphasis modifier: `<form method="post" action="{% url 'notification_read_all' %}">{% csrf_token %}<button type="submit" class="hub-btn hub-btn--sm hub-btn--primary">Mark all as read</button></form>`, wrapped in `{% if unread_count %}…{% endif %}` so it is **hidden when there are zero unread** (no dead button). **`hub-btn hub-btn--sm` alone renders invisible** — base `.hub-btn` (`hub.css:952`) sets padding/`border:none`/radius but no background or color, so the fill and text come entirely from `--primary` (yellow on navy). Sits in the header action cluster with the back link; the `.pl-notes__actions` gap + `flex-wrap` keeps it clear of the title and stacks on mobile.
  - **Pagination** = `{% if page.has_other_pages %}<nav class="pl-notes__pager" aria-label="Notifications pages">` with `hub-btn hub-btn--sm hub-btn--ghost` prev/next anchors (`?page={{ page.previous_page_number }}` / `next_page_number`) flanking a `hub-text-muted` "Page {{ page.number }} of {{ page.paginator.num_pages }}". `margin-top` clears the last row; `flex-wrap` + `justify-content:center` reflows on mobile. (Inherited debt, out of scope: `hub-btn--ghost:hover` at `hub.css:1001` is `rgba(255,255,255,0.06)` — a no-op on the light theme; it affects every ghost button site-wide, so leave it alone here.)
- **States:**
  - *Empty:* `{% if page.object_list %}…{% else %}<p class="hub-text-muted">You're all caught up.</p>{% endif %}` inside the hub-card. Never a bare card.
  - *Loading:* internal navigation + the mark-all/mark-one POSTs are boosted, so `components/loading_bar.html` shows the top progress bar automatically. No spinner markup needed.
  - *Error:* the row/mark-all POSTs are simple and `@login_required`; a logged-out POST bounces to login (allauth), not a 500. A stale `pk` in `notification_read` already falls back to `redirect("home")`.
  - *Success:* per-item — the redirect to `n.url` is the confirmation (you land on the thing). Mark-all — the `messages.success` toast plus the visibly-cleared highlights/badge and the now-hidden button.
- **New CSS (layout-only; color comes from the reused `hub-btn--ghost` + `hub-text-muted`, no new tokens; all on the 8px grid):**
  - `.pl-notes__header { display:flex; justify-content:space-between; align-items:center; gap:1rem; flex-wrap:wrap; }`
  - `.pl-notes__actions { display:flex; flex-wrap:wrap; gap:0.5rem; align-items:center; }`
  - `.pl-notes__pager { display:flex; flex-wrap:wrap; gap:0.75rem; justify-content:center; align-items:center; margin-top:1.5rem; }`
- **Dark + light (verify both — Rule 7):** theme tokens only (`--hub-card-bg`, `--hub-text`, `--hub-text-muted`, `--hub-border`, `--color-tuscan-yellow`). The current `.pl-note--unread .pl-note__btn { background: rgba(238,180,75,0.08); }` and `.pl-note__btn:hover { background: rgba(255,255,255,0.04); }` are tuned for **dark**; on the light theme the yellow tint reads faint and the white hover is a no-op. **Add `[data-theme="light"]` overrides:** a slightly stronger unread tint (or a `border-left` accent in `--color-tuscan-yellow`) and a dark-ink hover (`rgba(0,0,0,0.04)`). No form controls on this page, so no input-token/`select option`/date-picker concerns.
- **Mobile (Rule 6):** no fixed widths — the 340px panel is gone; rows are full-width flex columns (`.pl-note__btn` is already `width:100%`). The panel used to clip long text; now that rows are full-width, a long unbroken token (a class name or a URL in the title/body) would push past the viewport, since `.pl-note__title`/`.pl-note__body` (`hub.css:2893`) don't wrap and `.pl-note__btn` is a flex column with default `min-width:auto`. **Add `overflow-wrap:anywhere` to `.pl-note__title` and `.pl-note__body`, and `min-width:0` to `.pl-note__btn`** so long tokens wrap instead of scrolling. `.pl-notes__header`, `.pl-notes__actions`, and `.pl-notes__pager` all `flex-wrap`. Tap targets are the full-width row buttons and real `hub-btn`s (not tiny icons). Spacing on the 8px grid (existing `.pl-note` padding `0.75rem 1rem`). No horizontal scroll at any width.

### Screen B — the bell (`templates/hub/_notification_bell.html`, rewritten)

- **Now a plain link**, desktop and mobile, keeping the badge:
  ```html
  <a href="{% url 'notification_list' %}" class="pl-bell__btn" aria-label="Notifications">
    <svg …bell icon (unchanged paths)…></svg>
    {% if unread_notification_count %}<span class="pl-bell__badge">{{ unread_notification_count }}</span>{% endif %}
  </a>
  ```
- **Removed:** the `.pl-bell` `x-data="{ open }"`, `@keydown.escape`, the `@click` toggle, `hx-get`/`hx-target`/`hx-trigger`, the entire `.pl-bell__panel` (header, `.pl-bell__mark`, `#pl-bell-feed`, `Loading…`).
- **CSS:** keep `.pl-bell__btn` and `.pl-bell__badge`. `.pl-bell__btn` **already has `position:relative`** (`hub.css:2883`), so the badge stays anchored to the anchor with **no change** once the `.pl-bell` wrapper is gone. Delete `.pl-bell` (or reduce to nothing), `.pl-bell__panel`, `.pl-bell__header`, `.pl-bell__mark`, and `.pl-note__empty`. Two edits to `.pl-bell__btn`: (1) it's an `<a>` now, so **add `text-decoration:none`** (the old `<button>` didn't underline; an anchor does by default); (2) its `:hover` is `background:rgba(255,255,255,0.06)` — invisible on the light theme, the same no-op you guard for the rows — so **add `[data-theme="light"] .pl-bell__btn:hover { background:rgba(0,0,0,0.06); }`**. `color: var(--hub-text)` already renders theme-correct on both themes.
- The include stays exactly where it is (`base.html:401`), still inside the members-only gate — no surface/`GUILDS_ALLOWED_VIEW_NAMES` change needed (the page is members-only, not a guest surface).

## 7. Notifications / emails / activity

**None.** This is a pure UI relocation of existing in-app notifications — it sends no email, emits no new trigger, writes no `SiteActivity`. The `Notification` records it displays are still created by their existing upstream triggers.

## 8. Build order (each phase ships green)

1. **Add the page, keep the dropdown.** Add `NotificationQuerySet` (no migration). Rename `notification_feed` → `notification_list` in `core/views.py` + `core/urls.py`; make it render the new `templates/hub/notifications.html` (paginated, 20/page, rows + mark-all + pager + empty state). Add `.pl-notes`/`.pl-notes__actions`/`.pl-notes__pager` CSS. **Temporarily point the still-present bell dropdown's `hx-get` at the old behavior is not possible after the rename** — so in this phase also update the bell's `{% url %}` to `notification_list` but leave the dropdown markup; the dropdown's feed load is acceptable to drop here (it can 200 the full page into the panel harmlessly, or simply switch the bell in phase 2). *Simplest green:* do the bell swap in phase 2 and in phase 1 just add the page + repoint the URL name, updating the one `{% url 'notification_feed' %}` in the bell to `notification_list` so nothing 500s. Suite + lint + mypy green.
2. **Switch the bell to a link; remove the dropdown; repoint mark-all.** Rewrite `_notification_bell.html` to the plain `<a>` + badge. Delete `templates/hub/_notification_feed.html`. Delete the dead dropdown CSS (`.pl-bell__panel/__header/__mark`, `.pl-note__empty`) and add `position:relative` to `.pl-bell__btn` + the `[data-theme="light"]` unread/hover overrides. Change `notification_read_all` to `messages.success(...)` + `redirect("notification_list")`. Suite + lint + mypy green.
3. **Tests + housekeeping.** Rewrite `tests/core/notification_views_spec.py` (see §9). Note (do not act) that `notification_unread_count` is now fully unused and could be deleted in a later cleanup. Bump `plfog/version.py` VERSION and add/fold the member-facing CHANGELOG entry (draft in §10) — decide the release number at build time.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*` (never `context_*`), in `tests/core/notification_views_spec.py` (where the current bell-feed specs live). Run in the `plfog-web` Docker image; assert against **rendered HTML** (not just status) per the nested-form lesson. Cases:

- `describe_notification_list`
  - `it_renders_the_members_own_notifications` — a `Notification` for the user appears (title in `resp.content`), `200`.
  - `it_does_not_show_other_users_notifications` — another user's notification title is **not** in the body.
  - `it_emphasizes_unread_rows` — an unread notification's row carries `pl-note--unread` in the HTML; a read one does not.
  - `it_paginates_at_20` — create 25; page 1 shows 20 rows and a "Page 1 of 2" pager (`page.has_other_pages`); `?page=2` shows the remaining 5.
  - `it_shows_the_empty_state_when_none` — zero notifications → `You're all caught up.` present, no `pl-note` rows.
  - `it_shows_mark_all_only_when_unread_exists` — with an unread one, the `notification_read_all` form/button renders; with everything read, it does **not**.
  - `it_requires_login` — anonymous GET redirects to login.
- `describe_notification_read` (keep)
  - `it_marks_one_read_and_redirects_to_its_url` — POST sets `read_at`, `302` → the note's `url`.
- `describe_notification_read_all` (adjust expectation)
  - `it_marks_all_read_and_redirects_to_the_page` — POST zeroes the unread count **and** returns `302` to `reverse("notification_list")` (was 204).
- `describe_notification_bell` (new, template-level)
  - `it_renders_as_a_link_with_the_badge` — render a hub page while logged in with an unread notification; assert the bell is an `<a href="/notifications/">` carrying `pl-bell__badge`, and that **no dropdown markup** remains (`pl-bell__panel` absent from the HTML).

Gotchas: no tz/date-window logic here. Existing `describe_unread_count` test can stay (endpoint still resolves) or be dropped with the endpoint later — leave it for now so the suite stays green without touching unrelated code.

## 10. Open / deferred

- **Rejected: keep a desktop dropdown.** Considered a responsive split (dropdown ≥768px, link below). Rejected — two code paths for one affordance, and the page is a strictly better read on desktop too. One behavior everywhere.
- **Deferred: delete the dead `notification_unread_count` endpoint.** It's unused after this change (the badge comes from the context processor, no polling). Removing it (view + url + its spec) is a trivial follow-up cleanup, out of scope here.
- **Deferred: filters / "unread only" toggle.** Newest-first with unread emphasis is enough for launch. A filter chip can come later if members ask.
- **Deferred: migrate the remaining inline `Notification.objects.filter(...)` call sites** (context processor, unread-count) to the new queryset helper — pure tidy-up, no behavior change.
- **Release number:** decided at build time (current `release-0.20.x` patch line).

**Draft CHANGELOG entry (add/fold at build time — do NOT bump `version.py` now):**

> **Notifications now have their own page**
> - The bell in the top bar opens a full Notifications page instead of a small pop-up — easy to read on your phone.
> - Unread notifications are highlighted; tap one to jump straight to what it's about.
> - "Mark all as read" clears your unread count in one tap.

> At build time: if the notification bell has **not** yet shipped to production, fold these bullets into that feature's existing unreleased-line entry and re-stamp it to your VERSION rather than adding a second entry (per CLAUDE.md → *Versioning & Changelog*). If the bell is already live on prod, this is its own member-facing entry.

> Spec only — do not build until approved.
