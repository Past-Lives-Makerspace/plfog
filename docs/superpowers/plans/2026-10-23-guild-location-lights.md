# Guild page: a setting to hide the location lights, and the lights share a row

Issue: #892

Source: Felix, 2026-10-23, with a screenshot of four full width rows (Audio Desk, Miniatures Desk, Resin Printing Room, Tech Area, each "Free now"): "put a settings in Guild Settings where we don't have to show these. Also, if they are more than 1, these should be taking up the same row instead of being a new row to save space."

### User story

As a guild lead, I want to turn off the location lights on my guild page, and when they show, I want them compact, so the page is not a stack of near empty rows.

### Summary

Guild Settings gets a toggle to hide the location lights, and the lights sit side by side in one row, wrapping only when they run out of room.

### Goal

Guild pages spend less space on location lights, and a guild can drop them.

### Current behavior

`templates/hub/guild_detail.html` includes `hub/partials/_guild_location_status.html` (#616), fed by `membership.services.location_status.guild_location_statuses` from `hub/views.py` (around line 933). Each location is its own full width row (`static/css/locations.css`, `.pl-area-status__list` is a flex column). There is no way to hide them.

### Expected behavior

A toggle in Guild Settings, "Show location lights on the guild page", on by default (today's behavior). Off: the section is gone from the guild page and its status query is not run. On: the lights are compact pills laid out in rows that fill the width and wrap.

### Acceptance criteria

1. **Field** `Guild.show_location_status`, `BooleanField(default=True, db_default=True)`, help text "Show whether each of the guild's locations is free, starting soon or in use on the guild page." One migration in `membership`, numbered after main's latest; `db_default` so the previous release's INSERTs still work during deploy (STANDARDS.md section 10).
2. **Setting** in Guild Settings (`templates/hub/guild_edit.html`), using `components/toggle.html`, in the section that already holds the guild page display options (beside `show_members`; pick the right form in `hub/forms.py` and say which in the PR). Label "Show location lights on the guild page", description "Free now, starting soon or in use, for each of this guild's locations." Saves like its neighbours. Same people who can edit Guild Settings today; nobody new.
3. **Guild page:** off, the section does not render and `guild_location_statuses` is not called (assert the query count or patch it). On, as today in content.
4. **Layout:** the list becomes a wrapping row: each light a compact pill (dot, name, message), sized to its content, several per row, wrapping to the next row when the width runs out (`display: flex; flex-wrap: wrap` or a grid with `auto-fill`; pick whichever keeps long "In use" messages readable and say which). One light: a single pill, not stretched full width. 375px phone: pills wrap, no horizontal scroll, a long message wraps inside its pill. Light and dark both read; the dot is still never the only signal. Keep the in use and soon border colours.
5. **Public guilds site:** the guild page also renders on guilds.pastlives.space; the toggle governs both.
6. **Tests:** model default; the form saves on and off; guild page renders with the toggle on and omits the section and skips the status query with it off; permission unchanged (a member who cannot edit the guild cannot flip it with a crafted POST). Screenshots for the PR: the guild page with four lights at 1440px and 375px, light and dark, and the Guild Settings toggle.
7. No dashes in copy. Docs: the `locations.css` row in `FRONTEND.md`, the guild page and Guild Settings rows in `hub/AGENTS.md`, `membership/AGENTS.md` if it lists Guild display fields.

### Area

hub (guild page, Guild Settings), membership

### Constraints

- Default on, so no guild page changes content on deploy, only layout.
- No change to how a status is worked out (`location_status.py` logic).

### Out of scope

- Hiding single locations, or the lights anywhere other than the guild page.
