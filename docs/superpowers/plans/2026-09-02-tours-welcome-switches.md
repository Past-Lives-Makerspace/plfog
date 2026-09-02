# Spec: Site-wide kill switches for Guided Tours and Guild Welcome Emails + rename "Welcome Packet" to "Welcome Email"

## 0. Reading of "Member guide" — conclusion

**Confirmed: "Member guide" = the guided tours feature** (`core/tours.py` TOURS registry, offers/autostarts, Help-page "Guided Tours" card, Settings card, `pl_tour.js` runtime). A full grep for the literal string "Member Guide" finds only: the migration-seeded OrgLink Google-Doc link (`membership/migrations/0068_orginfopage_orglink_orgfaqitem.py:12`, `membership/models.py:2787` help_text example, `tests/membership/org_info_spec.py:119`, a historical comment in `templates/hub/base.html:345`, and old changelog entries in `plfog/version.py:1483,2359`). The OrgLink is admin-deletable DB content and needs no flag. Nothing else answers to "Member Guide", so the flag targets the guided-tour feature.

Also out of scope, deliberately: the **per-class instructor welcome email** (`classes/models.py:562` `welcome_email_enabled`, `templates/classes/_components/welcome_email_form.html`) is a different feature that already says "Welcome Email" and is not renamed or gated.

## 1. New SiteConfiguration fields (core/models.py)

Add two booleans immediately after `wiki_link_enabled` (ends `core/models.py:325`), following the `my_tab_enabled` pattern (`core/models.py:296-303`):

```python
guided_tours_enabled = models.BooleanField(
    default=True,
    verbose_name="Offer guided tours",
    help_text="When off, guided tours are switched off site-wide: no tour offers or autostarts, "
    "the Show me around buttons and the Help page's Guided Tours card are hidden, the Guided "
    "Tours card disappears from member Settings, and a ?tour= link does nothing.",
)
guild_welcome_email_enabled = models.BooleanField(
    default=True,
    verbose_name="Send guild welcome emails",
    help_text="When off, no guild welcome email is sent when a member joins a guild (the Join "
    "button or the Discord /join-guild command), the join popup's email opt-in is hidden, and "
    "the Welcome Email tab is hidden from the guild editor. Per-guild settings are kept and "
    "take effect again when this is turned back on.",
)
```

**Naming caution for the builder:** `Member.guided_tours_enabled` already exists (`membership/models.py:557`) — the per-member preference. The new site flag intentionally reuses the name on a different model; keep reads explicit (`SiteConfiguration.load().guided_tours_enabled` vs `member.guided_tours_enabled`) and never confuse them in tests (`MemberFactory(guided_tours_enabled=...)` is the member field).

## 2. Guided tours — gate points

**Decision: the site flag kills manual starts too.** Unlike the per-member toggle (which only stops auto-offers — `tests/core/tours_spec.py:359` `it_works_with_the_toggle_off`), site-off means the feature does not exist: no offers, no autostarts, no `?tour=` starts, no entry buttons, no cards.

1. **`core/tours.py:696-707` — `tour_offer_context`** (the single choke point; `core/context_processors.py:185-231` `tour_runtime` routes exclusively through it). Insert after the `member is None or not tour.audience(member)` check (line ~703) and **before** the `request.GET.get("tour")` branch (line 707):
   ```python
   from core.models import SiteConfiguration
   if not SiteConfiguration.load().guided_tours_enabled:
       return empty
   ```
   This one gate kills auto-offers, autostarts, manual `?tour=` starts, and the payload on every page a driven tour could land on. Update the docstring guard-order description (lines 681-694).
2. **`templates/hub/base.html:608`** — wrap the runtime include so the JS + pause/resume pill stop shipping (also dead-ends a sessionStorage-paused tour):
   `{% block tour_runtime %}{% if guided_tours_enabled %}{% include "hub/partials/_tour.html" %}{% endif %}{% endblock %}` (context var comes from the `feature_flags` context processor, step 4 below). `_tour.html` is included nowhere else.
3. **Entry buttons** (three, all found by `grep '?tour=' templates`):
   - `templates/hub/home.html:9` — page_header gates its button on a truthy `action_url` (`templates/components/page_header.html:24`), so the one-liner works: `action_url=guided_tours_enabled|yesno:"?tour=member-welcome,"`. Or wrap in `{% if %}` with two includes.
   - `templates/hub/guild_edit.html:9` — wrap the `<a href="?tour=guild-lead">` in `{% if guided_tours_enabled %}`.
   - `templates/classes/teach/overview.html:12` — same wrap.
4. **`core/context_processors.py:47-59` `feature_flags`** — add `"guided_tours_enabled": config.guided_tours_enabled` and `"guild_welcome_email_enabled": config.guild_welcome_email_enabled`.
5. **Help-page card** — `hub/views.py:3610`: `tour_rows = help_card_rows(member) if member is not None and SiteConfiguration.load().guided_tours_enabled else None` (`SiteConfiguration` is already used at line 3591; `templates/hub/help.html:197` hides the whole card when `tour_rows is None`).
6. **Settings card** — `hub/views.py:2058-2077` `_handle_tours_form`: early-return `(None, None)` when the site flag is off (both GET and POST branches); `templates/hub/_tour_settings.html:7` already hides the card when `tours_form` is falsy.
7. **Left alone, deliberately:** the `hub_tour_state` POST endpoint (state writes are harmless and unreachable with no runtime payload); `Member.guided_tours_enabled` and its form (`hub/forms.py:3270` `TourSettingsForm`) — the member preference persists under the site flag; the static help-article copy that mentions tours (`membership/help_content.py:765` and `:1230`) — code-authored markdown that cannot conditionally render; the dangling "Take the tour" link degrades gracefully (ignored `?tour=` param). Note this as an accepted limitation.

## 3. Guild welcome email — gate points

**Decision: the send path MUST be server-gated, not just UI-hidden.** Two send triggers exist that no template gate reaches: the Discord `/join-guild` command (`hub/discord_commands.py:200`) and any direct POST to `hub_guild_join`. Per the `my_tab_enabled` precedent (gated in `billing/models.py:575`, `billing/views.py:72`, `bill_tabs.py:68` — not just templates), the gate goes at the model/service layer.

1. **Server gate (the choke point): `membership/orientations.py:999` `send_guild_welcome`** — first line of the body (before the settings-row lookup):
   ```python
   from core.models import SiteConfiguration
   if not SiteConfiguration.load().guild_welcome_email_enabled:
       return
   ```
   This covers both `Member.send_guild_welcome` callers: `hub/views.py:2593` (web join) and `hub/discord_commands.py:200` (`/join-guild`). Update the docstring (line 1000-1011) and `Member.send_guild_welcome`'s docstring (`membership/models.py:743-751`).
2. **Honest toast — `hub/views.py:2591`**: `welcomed` currently claims "Check your inbox for your welcome packet" (line 2600) even when nothing sends. Change to:
   `welcomed = bool(joined and request.POST.get("send_welcome") and SiteConfiguration.load().guild_welcome_email_enabled)` — and rename the toast copy to `"You joined {guild.name}! Check your inbox for your welcome email."` (line 2600) plus the docstring at 2579.
3. **Guild editor tab — `templates/hub/guild_edit.html`**: wrap the tab button (line 21) and the whole `welcome_email` section (lines 626-652, which contains the Preview/Send-test card, the save form, and the preview modal) in `{% if guild_welcome_email_enabled %}`. A stale `?tab=welcome_email` deep link with the flag off simply shows no panel — same behavior as the my_tab-gated Buyables tab (`templates/hub/guild_detail.html:126`).
4. **Join modal opt-in — `templates/hub/guild_detail.html:804`**: wrap the `send_welcome` toggle include in `{% if guild_welcome_email_enabled %}` (the Discord-announce toggle at 806-809 stays).
5. **Left alone, deliberately:** `send_guild_welcome_test` (`membership/orientations.py:1031`) and the preview endpoint (`hub/views.py:2632,2655`) — lead-only proofing tools whose buttons live inside the hidden tab; a direct POST only emails the lead themselves. `hub_guild_emails_save` `form_id=welcome_email` (`hub/views.py:3390`) — saving copy while off is harmless and preserves drafts. `/join-guild`'s Discord channel/DM welcome messages (`discord_welcome_message`) — those are Discord posts, not the email; unaffected. The e2e email-gallery mirror (`tests/e2e/email_gallery/context.py:535`) builds context directly, unaffected.

## 4. Site Settings wiring (admin)

1. **`hub/forms.py:749-789` `SiteSettingsForm.Meta.fields`** — add `"guided_tours_enabled"` and `"guild_welcome_email_enabled"` (after `"wiki_link_enabled"` at line 770).
2. **`templates/hub/admin/site_settings.html:424-441` Features tab** — add two includes after line 437 (`wiki_link_enabled`):
   ```
   {% include "components/form_field.html" with field=form.guided_tours_enabled %}
   {% include "components/form_field.html" with field=form.guild_welcome_email_enabled %}
   ```
3. **CRITICAL gotcha — `templates/hub/admin/site_settings.html:159`**: the General tab renders every form field NOT named in that giant `field.name !=` exclusion chain. Add `and field.name != 'guided_tours_enabled' and field.name != 'guild_welcome_email_enabled'` to the chain or both toggles render **twice** (General + Features). The existing spec pattern `tests/hub/admin_views_spec.py:910` (`count(b'id="id_my_tab_enabled"') == 1`) exists precisely to catch this — add the same count-==-1 assertion for both new fields. *Drive-by observation (do not fix in this PR unless asked): `member_directory_public` is in the Features tab (line 440) but missing from the line-159 exclusion chain, so it likely already renders twice.*

## 5. Rename inventory — "Welcome Packet" to "Welcome Email" (every occurrence, from a full case-insensitive grep)

Member/staff-facing copy (must change):
- `templates/hub/guild_edit.html:21` — tab label `Welcome Packet` → `Welcome Email`
- `templates/hub/guild_edit.html:628` — tab heading → `Welcome Email`
- `templates/hub/guild_edit.html:651` — modal title `Welcome Packet Preview` → `Welcome Email Preview`
- `templates/hub/guild_detail.html:804` — toggle_label `"Email me the X welcome packet"` → `"Email me the X welcome email"`
- `hub/views.py:2600` — join toast `"…Check your inbox for your welcome packet."` → `"…welcome email."`
- `hub/views.py:3395` — success message `"Welcome packet saved."` → `"Welcome email saved."`
- `hub/forms.py:1656` — label → `"Send a welcome email when a member joins this guild"`
- `hub/forms.py:1660-1667` — help_text override: the stale comment at 1661-1662 inverts — with the model help_text (`membership/models.py:8258-8264`) already saying "welcome email", the whole `help_texts` override can now simply be **deleted** (model help_text is correct as-is).
- `hub/forms.py:1693` — `GuildJoinForm.send_welcome` label → `"Email me the guild's welcome email"`
- `core/tours.py:334-335` — guild-lead tour step: title `"The Welcome Packet"` → `"The Welcome Email"`, body `"This is the welcome packet…"` → `"This is the welcome email new members get when they join your guild. Make it warm."`
- `core/help_registry.py:674-677` — Info View annotation `guild.welcome-email`: title `"The welcome packet"` → `"The welcome email"`, short_text likewise
- `membership/help_content.py:1302` — the tab list in the "your guild page" article: `**Welcome Packet**` → `**Welcome Email**`
- `membership/models.py:1816-1822` — `Guild.discord_welcome_message` help_text: `"…separate from your guild Welcome Packet…"` → `"…separate from your guild Welcome Email…"` (**generates a membership migration** — see §6)
- `hub/views.py:2579` — docstring `"send welcome packet" box` → `"send welcome email" box`

Tests / e2e:
- `tests/hub/guild_welcome_editor_spec.py:161` — `assert b"Welcome Packet" in response.content` → `b"Welcome Email"`
- `tests/e2e/email_gallery/registry.py:498` — edit_pointer `"guild editor → Welcome Packet tab"` → `"Welcome Email tab"` (copy-review metadata; no spec asserts the old string)

Internal comments/docstrings (rename for hygiene, zero runtime effect):
- `membership/guild_welcome_copy.py:1-16` — module docstring ("welcome packet" x3)
- `membership/orientations.py:966` — `_guild_welcome_context` docstring
- `hub/forms.py:1636` docstring already says "Welcome Email tab" — fine.

No occurrences exist in `static/js/`, CSS, or email templates (`templates/membership/emails/guild_welcome.*` never says "packet").

## 6. Migration plan

1. **core**: one auto-generated add-fields migration (next after `core/migrations/0070_siteconfiguration_display_demo_classes_and_more.py`) adding both booleans with `default=True`. No data migration — the singleton row picks up defaults; existing behavior is unchanged on deploy.
2. **membership**: one auto-generated `AlterField` migration (next after `membership/migrations/0149_alter_guild_discord_welcome_message.py`) for the `discord_welcome_message` help_text rename. Django detects help_text changes; run `makemigrations` once after all model edits so each app gets exactly one migration.
3. Both are trivially reversible (RemoveField / AlterField back) — no RunPython needed.

## 7. Test plan (BDD pytest-describe, `*_spec.py`)

Extend existing specs:
- **`tests/core/models_spec.py`** (~line 50, next to `it_defaults_my_tab_enabled_to_true`): `it_defaults_guided_tours_enabled_to_true`, `it_defaults_guild_welcome_email_enabled_to_true`.
- **`tests/core/context_processors_spec.py:69-100`**: the `it_reflects_toggled_values` spec asserts **dict equality** of the whole `feature_flags` result (line ~95) — it will fail until the two new keys are added to both the setup and the expected dict; also extend the defaults spec.
- **`tests/core/tours_spec.py`** (mirror the member-toggle guards at 327-331 and 359-362): new `describe_when_the_site_flag_is_off` block — `it_suppresses_the_offer` (no offer, no TourState row) and `it_blocks_even_a_manual_start` (`?tour=member-welcome` → `tour_autostart is False`, `tour_json is None`) — the deliberate contrast with `it_works_with_the_toggle_off` (member toggle). Flag-on default behavior is already covered by every existing spec.
- **`tests/hub/tours_spec.py`**: flag-off specs — home page GET has no offer and no `pl-tour-data` payload and no `?tour=member-welcome` href; user_settings GET has no Guided Tours card (assert structural markers, see the negative-assertion trap below); a `form_id=tours` POST with the flag off does not crash. Help page: `tour_rows` card absent (assert on a structural marker like `b"?tour="`).
- **`tests/hub/guild_welcome_editor_spec.py`**: rename line 161's assertion; add `it_hides_the_welcome_email_tab_when_the_site_flag_is_off` — assert `b"section === 'welcome_email'"` **not** in content (structural marker; see trap below).
- **`tests/membership/guild_welcome_spec.py`**: `it_sends_nothing_when_the_site_flag_is_off` (flag off + enabled guild settings → `len(mail.outbox) == 0`); existing specs cover flag-on.
- **`tests/hub/guild_join_view_spec.py`**: flag off + `send_welcome=on` → no email, toast is plain `"You joined X!"` (no "Check your inbox"); flag on → toast says "welcome email" (update any existing "welcome packet" toast assertions).
- **`tests/hub/discord_commands_spec.py`**: `/join-guild` with flag off sends no email (exercises the shared gate through the second caller).
- **`tests/hub/admin_views_spec.py`** (~905-975): each new toggle's id appears exactly once on the Site Settings page (the duplicate-render guard, §4.3); the save POST persists `guided_tours_enabled=False` / `guild_welcome_email_enabled=False` and re-enables (mirror lines 943-972).
- **Base template**: one spec asserting a hub page with flag off does not reference `pl_tour.js` (the `base.html:608` gate; can live in `tests/hub/tours_spec.py`).

Coverage note: every new `if not …enabled` branch needs both directions exercised (100% branch coverage + mutation kill), which the list above does.

## 8. Changelog (`plfog/version.py`)

Bump `VERSION` to 1.22.0 and add ONE member-facing entry (orchestrator-owned; builder does NOT touch version.py).

## 9. Risks and traps (read before building)

1. **The changelog renders into every hub page** (`core/context_processors.py:19-23` + `templates/hub/base.html:525` changelog modal). The new entry will contain the words "Welcome Email", "guided tours", and historic entry 1.21.0 already contains **"Show me around"** (`plfog/version.py:34-36`). Therefore new flag-off specs must NEVER negatively assert those bare phrases against full-page content — `assert b"Show me around" not in response.content` fails on every page today. Use structural markers instead: `b"section === 'welcome_email'"` (tab button), `b"?tour=member-welcome"` (entry href), `b"pl-tour-data"` (payload), `b'value="tours"'` (settings form).
2. **Site-settings duplicate render** — new fields must join the line-159 exclusion chain (§4.3) or they appear twice and the count-==-1 spec fails.
3. **`tests/core/context_processors_spec.py:95` dict-equality** breaks the moment keys are added to `feature_flags` — update in the same commit.
4. **e2e lane** (`pytest tests/e2e`, separate Playwright lane): `tests/e2e/guided_tour_spec.py` asserts tour behavior with defaults (flag True — unaffected); it asserts "Show me around" on the offer card and "Welcome to the Member Portal" (member tour step title — unchanged). No e2e spec asserts "Welcome Packet" except the gallery registry string being renamed (§5). No Playwright spec breaks from the rename because none asserts the guild-editor tab label.
5. **Name collision**: `guided_tours_enabled` exists on both `Member` and (now) `SiteConfiguration` — semantics differ (member = auto-offers only; site = feature dead including manual starts). Keep docstrings explicit at both gate sites.
6. **Static help articles** (`membership/help_content.py:765,1230`) still link "Take the tour" when tours are off — accepted limitation (ignored `?tour=` param, no error); note it in the PR.
7. **Deep link `?tab=welcome_email`** on the guild editor with the flag off shows an empty content area (tab strip visible, no panel) — matches the existing my_tab Buyables precedent; acceptable.
8. `hub/forms.py:1660-1667` help_texts override comment is now wrong-way-round; delete the override (§5) rather than leaving a misleading comment for the next agent.
