# kiln app

Kiln tickets for the Ceramics Guild (#691). A maker files one ticket per piece (or set of identical pieces) with photos and the guild's questions; the crew loads, unloads and tells them it is ready. Ships in three parts: part 1 (this) is tickets for makers and the crew's clay and glaze lists; part 2 adds loading, crew flags and replies; part 3 adds unloading, pickup notices and the kiln log.

## Who

`kiln/access.py` holds every rule:

- The kiln guild is found by `settings.KILN_GUILD_SLUG` (`ceramics-guild`, guild 8 in production). No pk is hard coded.
- **Crew** is that guild's `guild_lead` plus its `GuildStaffMembership` rows (`is_crew`, `crew_required`). No admin override, no new role.
- **Makers** are any member the portal admits plus any **guest** account (#654). A guest reaches `/kiln/` and nothing else on the members surface: `core.middleware.MemberLockoutMiddleware` sends every other members page to `kiln:mine`, and `AdminRedirectAccountAdapter.pre_login` lets a guest sign in. Former and suspended members stay locked out. A guest's sidebar is Kiln Tickets and Sign out (`KilnNav.guest_only` in `templates/hub/base.html`).
- **Launch switch:** `SiteConfiguration.kiln_tickets_open` (Site Settings, General tab), off by default. Off: only the crew reach `/kiln/` (`kiln_open_required` answers everyone else 404), only the crew see the sidebar entry and the guild page link, and guests are locked out exactly as before #691 (the middleware and `pre_login` read it through `guest_may_use_kiln`). On: everything below.
- The sidebar entry shows for the guild's members, lead and staff (`shows_kiln_nav`, one query with the switch folded in, evaluated only when the sidebar renders). Everyone else reaches the pages from the Ceramics Guild page's Get Involved card.

## Models

| Model | Notes |
|-------|-------|
| `ClayOption`, `GlazeOption` | The guild's lists. Archived (`archived_at`, `archived_by`), never deleted; a partial unique constraint keeps active names unique. Seeded by migration 0002. |
| `KilnTicket` | `status` already holds the whole lifecycle (draft, submitted "In the queue", loaded "In the kiln", fired "Ready for pickup"); part 1 uses draft and submitted. Name and contact are read from `maker`; `maker_type` is a snapshot taken on every save. `missing_for_submit` is the one list of what Submit refuses for; `automatic_flag_kinds` / `sync_automatic_flags` are the flag rules. |
| `KilnTicketPhoto` | `image` (gallery size) and `tile` (480px) from one upload; a partial unique constraint allows one cover, and `add_photo` / `remove_photo` / `set_cover` keep exactly one while photos exist. |
| `KilnFlag` | Automatic flags have no `added_by`. `MANUAL`, `note`, `cleared_at` and `cleared_by` are there for part 2. One automatic flag per kind per ticket. |

## Rules worth knowing

- Submit is refused only for: a photo, a firing type, a clay (an Other clay needs its name), the Cone 6 confirmation that applies, and the follow up text of a ticked glaze kind. Every other answer only flags.
- Saving drops the answers of the branch the maker left (`drop_inapplicable_answers`), so a glaze answer never flags a bisque piece.
- A ticket is editable until loaded. Any save of a ticket already in the queue is checked like a Submit and re-runs the automatic flags; a flag the answers still raise keeps its cleared state.
- "Make another like this" is `kiln:new?from=<pk>`: every answer but the photos and the Cone 6 confirmations (`copy_initial`). Nothing is written until the maker saves.
- The ticket form is one form with several submit buttons (`action` = draft, submit, `cover:<pk>`, `remove:<pk>`); the photo buttons save the whole page first. It is not boosted (file uploads). `static/js/kiln_ticket_form.js` previews picks and keeps earlier picks when the maker adds more.

## Views → URLs

| View | URL name | Path |
|------|----------|------|
| `my_tickets` | `kiln:mine` | `/kiln/` |
| `ticket_new` | `kiln:new` | `/kiln/new/` (`?from=<pk>` copies) |
| `ticket_detail` | `kiln:detail` | `/kiln/<pk>/` (the maker's own) |
| `ticket_edit` | `kiln:edit` | `/kiln/<pk>/edit/` |
| `lists` and `list_add` / `list_rename` / `list_archive` / `list_restore` | `kiln:lists`, `kiln:list_*` | `/kiln/lists/...` (crew only; htmx swaps the card) |

Templates in `templates/kiln/`, styles in `static/css/kiln-tickets.css` (lifted from `mockups/kiln-tickets.html`). Specs in `tests/kiln/`, the browser flow in `tests/e2e/kiln_tickets_spec.py`.
