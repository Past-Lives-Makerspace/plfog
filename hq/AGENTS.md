# hq app

The Holiday Bizarre HQ (Track C of the Holiday Bizarre plan). One hub page per event with a hero band, a row of goals and nine door cards, each opening one part of the event. Ships in four parts: part 1 (this) is the feature switch, the HQ page, its cards and the edit pages; C2 adds volunteer shifts behind the `volunteers` card, C3 the wine wall behind `wine_wall`, C4 the seed command and the help article.

## Who

`hq/access.py` holds every rule:

- **The sidebar entry** is the `holiday_bizarre` feature switch (`core/features.py`), seeded Hidden by core migration 0118 so the entry never points at a 404 before the first HQ exists. It is cosmetic, like every switch but My Tab: `/hq/` answers in every state.
- **The open hub** (`EventHub.is_open`, one at a time by a partial unique constraint) is where `/hq/` (`hq:index`) and the sidebar entry land. Its page is public: no login gate, and `hub/base.html` auto-degrades for a signed-out visitor. A closed hub is reachable by admins only (`hub_or_404`), so an HQ can be built before it is shown. With no open hub, `/hq/` sends an admin to `hq:create` and everyone else to the themed 404.
- **A card's audience is the one gate** (`HubCard.is_open_to`): admins and the card's named members always; `public` everyone, signed in or not; `members` an ACTIVE member. A hidden card shows to admins only, with a Hidden chip, and is a 404 at its URL for anyone else (`card_or_404`). A `members` card hit signed out goes through login and back (`card_view`).
- **Rendering reads the effective role** (`request.view_as.is_admin`, `renders_as_admin`), so an admin previewing as a member or a guest sees that viewer's page; `viewer_member` is nobody for a Guest preview. **Action gates read the actual role**: `organizer_required(kind)` admits admins and the card's named members (`HubCard.can_organize`), and the edit pages use `hub.view_as.fog_admin_required`.
- **Guest accounts** (#654) reach `/hq/` on the members surface: `core.middleware.MemberLockoutMiddleware` carves the prefix out before the kiln rule, because the volunteer and wine wall doors are for people with or without an account. What a guest may open is still the card's audience.

## Models

| Model | Notes |
|-------|-------|
| `EventHub` | `slug`, `title`, `eyebrow`, `event` (a `CommunityEvent`, SET_NULL; the page renders without one), `hero_image` (normalized to the hero long edge on save, orphan deleted on replace), `intro`, `footer_note`, `is_open`, `created_at`. `objects.open()` is the one open hub. `countdown_label(now)` is the band's figure by Portland days ("N days to go", "1 day to go", "Today", "Thank you"; blank with no event); `date_line` is the event's `when_display` plus its location. `seed_default_cards()` makes the nine cards of `DEFAULT_CARDS` (Lee's who-for, titles, bullets and buttons), all admins only. `remove_hero()` clears the image. |
| `HubCard` | `kind` (`rsvp`, `volunteers`, `wine_wall`, `placeholder`; one of each live kind per hub), `title`, `who_for`, `bullets` (one per line, `bullet_lines`; the form caps at `MAX_BULLETS`), `button_text`, `sort_order`, `audience` (`admins` default, `members`, `public`), `named_members`, `is_hidden`. `url` is where the button goes: the event page for `rsvp` (blank with no event), `hq:volunteers` and `hq:wine` once C2 and C3 add them (blank until then, resolved inside a try so the card renders as a tile rather than a broken link), blank for a placeholder. `move("up" / "down")` swaps places with a neighbour and renumbers the hub's cards. |
| `HubGoal` | `label`, `kind` (`manual`, `rsvps`, `volunteer_spots`, `bottles`, `bottle_value`), `target`, `current_value` (manual only), `is_money`, `sort_order`. `hq.services.goal_rows(hub)` computes each chip in a fixed number of queries; `rsvp_count` reads the event's RSVP rows (A3b switches it to `rsvp_people_count`), and the C2 and C3 kinds return 0 until those parts land. |

## Views → URLs

| View | URL name | Path |
|------|----------|------|
| `index` | `hq:index` | `/hq/` (302 to the open hub; none → admins to `hq:create`, everyone else 404) |
| `create` | `hq:create` | `/hq/new/` (admin; the hub form, and the nine cards are made with the hub) |
| `hub_page` | `hq:page` | `/hq/<slug>/` (public for an open hub; hero band, goal chips, door cards, footer note; admins see hidden cards, per-card Edit and Edit HQ) |
| `edit` | `hq:edit` | `/hq/<slug>/edit/` (admin; title, eyebrow, event picker over published events, hero via `components/image_field.html`, intro, footer note, goals formset in the Help editor's idiom, Open switch, Save) |
| `hero_delete` | `hq:hero_delete` | `POST /hq/<slug>/hero/delete/` (admin) |
| `card_edit` | `hq:card_edit` | `/hq/<slug>/cards/<pk>/edit/` (admin; words, audience radios, named members as a collapsed pill row that Edit expands into a searchable `.pl-recipient-checklist` of ACTIVE members, Hidden, Move up and Move down) |
| `card_move` | `hq:card_move` | `POST /hq/<slug>/cards/<pk>/move/` with `dir=up` or `down` (admin; lands back on the card's edit page) |

Templates in `templates/hq/`, styles in `static/css/hq.css` (linked from each page's `extra_head`; the HQ page also loads Playfair Display for its display type). Specs in `tests/hq/`; the guest carve-out is specced with the lockout middleware in `tests/plfog/member_lockout_spec.py`.
