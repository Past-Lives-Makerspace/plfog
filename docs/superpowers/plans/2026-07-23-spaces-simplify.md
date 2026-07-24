# Build spec: Spaces simplification (status-click, guild links, drop cubby, lighten requests)

Branch: `feat/interactive-space-map`. Base off whatever `origin/feat/interactive-space-map` is at launch (must be AFTER the Help-rename commit lands). Four changes, decided with Josh. Guiding principle for the request-flow change: **hide and reroute, do not delete** — keep it reversible.

## Environment constraints (unchanged from prior builds)

Fresh worktree off `origin/feat/interactive-space-map`; symlink `.venv` AND `.env`; never touch `/home/josh/Code/plfog`; test/live Postgres is the `spacemap` stack on **host port 5433**; `.env` has live R2 creds (no ImageField saves to real storage); write full pytest output to a log and grep it (never trust a `| tail` exit code). Coverage gate 98%. Only acceptable failures are the three known env-only ones (two `settings_spec` email-backend, one `discord_class_posts` title-length).

## Change 1 — Click a space on the Edit Map to set its status

Admin, on `org_map_edit` (`/spaces/map/edit/`), clicks a space-bound marker and sets **Available / Occupied / Maintenance** inline. No form page.

- **Endpoint**: new admin-only POST, e.g. `path("spaces/map/markers/<int:pk>/status/", views.map_hotspot_status, name="hub_map_hotspot_status")`. Body carries the target status. It sets `hotspot.space.status` (validate against `Space.Status`), saves, and returns the re-rendered marker (so the map recolors) plus enough to refresh the legend counts.
- **Airtable is the source of truth for `Space.status`** (`Space` docstring: "read-only from Airtable's perspective", and `airtable_sync/config.py` maps Status both ways). So after setting status locally you MUST push to Airtable via `airtable_sync.service.sync_space_to_airtable(space)`, or the next `airtable_pull` reverts it. Wrap the push in try/except: on failure, keep the local change, log loudly, and surface a non-fatal warning to the admin ("saved locally; Airtable push failed, it may revert on next sync"). Do NOT let an Airtable outage 500 the request.
- Only markers with a bound `space` (kind studio/cubby) get the control; facility markers have no status. Guard server-side (403/400 if no space) and hide the control client-side for facility markers.
- **UI**: in the edit map, clicking a space marker opens a small inline control (three buttons or a segmented control) reflecting current status; picking one HTMX-posts and swaps the marker. Keep it keyboard-operable. Reuse existing `.pl-map` marker styling and the `--hub-*` status colors already used by the legend.
- Tests: sets each status; pushes to Airtable (mock `sync_space_to_airtable`, assert called); Airtable failure keeps the local change and does not 500; non-admin gets 403; facility marker (no space) is rejected.

## Change 2 — Link a room/space to its guild page

Guild shops like Ceramics and Wood Shop are **label-only facility markers** (`floor_geometry.py`, `kind="facility"`, no space, no guild). Studios can already carry `space.sublet_guild`. Give markers an optional direct guild link and render it.

- **Model**: add `guild = models.ForeignKey("Guild", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", help_text="Optional: link this marker to a guild's page (e.g. a shop that is a guild's home).")` to `MapHotspot`. Migration.
- **Property**: `MapHotspot.linked_guild` → `self.guild or (self.space.sublet_guild if self.space_id else None)`. Single source the template reads.
- **Detail panel** (`templates/hub/partials/_space_detail.html`): where it currently prints `hotspot.space.sublet_guild.name` as plain text, render `linked_guild` as a link to the guild page: `<a href="{% url 'hub_guild_detail' slug=linked_guild.slug %}">{{ linked_guild.name }}</a>`. This must also show for facility markers (the panel already has a "Facility" branch). Confirm the guild has a `slug` (guild detail is `hub_guild_detail` by slug; `hub_guild_detail_by_id` by pk is the fallback). The link must resolve on BOTH the members surface and the guilds surface (`hub_guild_detail` is in `GUILDS_ALLOWED_VIEW_NAMES`? verify; if not, use an absolute member-base URL like the map does, or the by-id redirect).
- **Editor**: in `org_map_edit`, add a guild picker to the marker editor (optional, for any marker but especially facility). Mirror the existing `space` picker wiring in that template/form.
- Tests: `linked_guild` prefers explicit `guild` then falls back to `space.sublet_guild`; the detail panel renders a working guild link for a facility marker with a guild and for a studio with a sublet guild; no link when neither is set.

## Change 3 — Remove "cubby" from the member-facing surface (keep the model)

Reversible: leave the `MapHotspot.Kind.CUBBY` and `SpaceRequest.RequestKind.CUBBY` enums in the model so nothing is destroyed, but stop showing the word "cubby" to members and stop using it to route.

- Copy: replace member-visible "cubby" wording. `cta_label` map ("Request this cubby" → "Request this space"), `org_map_edit.html` hints ("studio, cubby, and facility" → "studio and facility" / "space"), the `Kind.CUBBY` display label if it surfaces anywhere member-facing. Search text (`MapHotspot.search_text`) should not emit "cubby".
- Do NOT drop the enum values or their DB rows. A follow-up can fully retire them; for now they are dormant.
- Grep the repo for member-facing "cubby" (templates + any string rendered to users) and neutralize each; leave code comments/docstrings/enum identifiers alone.

## Change 4 — Lighten the request flow: notify admins, hide the review workflow

Decided: "Notify admins, keep it reversible." A request emails + notifies the **admins** (only), and the in-app review/approve/deny queue is removed from view. Keep records, model, and the review view/URL in code (dormant) so it can be restored.

- **Routing**: change `SpaceRequest._notify_submitted` so every request notifies the makerspace admins (email + in-app), regardless of studio vs cubby. Stop routing cubby requests to guild leadership. Check `core/events/registry.py` for how `space.lease_requested` / `space.cubby_requested` map to recipients; the simplest reversible move is to route both event keys to admins (or collapse to a single `space.requested` event to admins — but keep the old keys defined so history/tests survive). Keep the email + notification content ("a member requested space X"). The member still gets their own confirmation path unchanged.
- **Remove the review queue from view**: drop the "Space requests (N)" entry point on the Spaces page (`templates/hub/spaces.html`, the `map_can_review` block) and any nav/link to `hub_space_request_review_queue`. Leave the URL, view, and template in the codebase (dormant, reversible) — do not delete them. If a spec asserts the entry point renders, update it to assert it is gone.
- **Keep** the member side: "Your space requests" on the Spaces page and the detail-panel pending state + **Withdraw** button stay exactly as they are.
- **Approve/deny UI** becomes unreachable (its page is unlinked). Keep the decision view/endpoint in code. Do not delete.
- Tests: a submitted request emits an admin-targeted notification and email (assert recipients are admins, not guild leads); the Spaces page no longer shows the review-queue entry point; the member still sees their pending request and can withdraw; the review view still exists (a direct GET by an admin still 200s — it is dormant, not removed).

## Airtable note to surface to Josh in the report

`Space.status` is bidirectionally synced and Airtable is currently the system of record. The status-click pushes changes back, but if the same space is edited in Airtable, Airtable wins on the next pull. Flag this so Josh decides later whether the app should become authoritative for status.

## Version + changelog

Bump `VERSION` (next patch in the 0.23 line after whatever Help shipped — read the current value, add 1). Same unreleased feature line, so **edit the existing 0.23 map/Spaces changelog entry in place** (re-stamp version + date); add a short member-friendly bullet only if there is something members see (e.g. "spaces now link to their guild's page"). The status-click and request-flow changes are admin/internal; do not over-announce. No second entry. No em/standalone dashes.

## Verify before commit (check, report real numbers)

- ruff format + check clean; mypy (`plfog/ core/ membership/ hub/`) clean; `makemigrations --check` clean EXCEPT the one new migration you added (Change 2) — that migration must be committed and `--check` clean after it exists.
- Full suite to a log, 98% coverage, only the 3 known env failures.
- Deploy to the live `spacemap` stack (cp changed files into `../spacemap/`, run migrations against the 5433 DB). In a browser: on `/spaces/map/edit/` click a space and flip its status, confirm the marker recolors and the value persists; on `/spaces/` open Ceramics or Wood Shop (once a guild is linked via the editor) and confirm the guild link works; confirm no "cubby" text is visible; confirm the review-queue entry point is gone but "Your space requests"/Withdraw still work. Check light and dark. Clean up any Playwright screenshots dropped in the repo.

## Commit + push

Copy this spec into the worktree's `docs/superpowers/plans/2026-07-23-spaces-simplify.md`. `git add -A ':!.venv' ':!.env' ':!media'`; commit (message ending exactly `Claude-Session: https://claude.ai/code/session_01J2kYTLwGEHBqs1ihuAkS57`); `git push origin HEAD:feat/interactive-space-map`.

## Report back

True pytest numbers (and only the 3 known failures); commit SHA; confirmation that (1) status-click works and pushes to Airtable, (2) guild links render for a facility shop and a sublet studio, (3) no member-facing "cubby" remains while the enums are intact, (4) requests notify admins and the review queue is unlinked-but-not-deleted with member withdraw intact; the Airtable-authority note; and any deviations. Do not overstate.
