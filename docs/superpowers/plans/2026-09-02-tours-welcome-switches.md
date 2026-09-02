# Spec: Site-wide kill switch for Guild Welcome Emails + rename "Welcome Packet" to "Welcome Email"

> **DESCOPE (2026-09-02):** The owner discarded the guided-tours site switch mid-build. `SiteConfiguration.guided_tours_enabled` and every gate, template branch, form field, and spec for it were removed. The guild welcome email switch and the full Welcome Packet → Welcome Email rename (including the guild-lead tour step title in `core/tours.py`) ship as built. Section 2 below is struck; sections 0/1/4/7 are adjusted to the reduced scope.

## 0. Reading of "Member guide" — conclusion

**Confirmed: "Member guide" = the guided tours feature** (`core/tours.py` TOURS registry, offers/autostarts, Help-page "Guided Tours" card, Settings card, `pl_tour.js` runtime). A full grep for the literal string "Member Guide" finds only: the migration-seeded OrgLink Google-Doc link (`membership/migrations/0068_orginfopage_orglink_orgfaqitem.py:12`, `membership/models.py:2787` help_text example, `tests/membership/org_info_spec.py:119`, a historical comment in `templates/hub/base.html:345`, and old changelog entries in `plfog/version.py:1483,2359`). The OrgLink is admin-deletable DB content and needs no flag. Nothing else answers to "Member Guide", so the flag targeted the guided-tour feature. *Descoped 2026-09-02: no tours flag ships; tours remain always-on with only the existing per-member preference.*

Also out of scope, deliberately: the **per-class instructor welcome email** (`classes/models.py:562` `welcome_email_enabled`, `templates/classes/_components/welcome_email_form.html`) is a different feature that already says "Welcome Email" and is not renamed or gated.

## 1. New SiteConfiguration field (core/models.py)

Add one boolean immediately after `wiki_link_enabled` (ends `core/models.py:325`), following the `my_tab_enabled` pattern (`core/models.py:296-303`). *(A second flag, `guided_tours_enabled`, was specced here and descoped 2026-09-02.)*

```python
guild_welcome_email_enabled = models.BooleanField(
    default=True,
    verbose_name="Send guild welcome emails",
    help_text="When off, no guild welcome email is sent when a member joins a guild (the Join "
    "button or the Discord /join-guild command), the join popup's email opt-in is hidden, and "
    "the Welcome Email tab is hidden from the guild editor. Per-guild settings are kept and "
    "take effect again when this is turned back on.",
)
```

## 2. Guided tours — gate points — **STRUCK (descoped 2026-09-02)**

The entire tours kill switch was discarded by the owner before merge. No site-level gate exists in `core/tours.py`, `templates/hub/base.html`, the three entry-button templates, `hub/views.py` (help card, settings card), or `feature_flags`. Tours remain governed solely by the pre-existing per-member `Member.guided_tours_enabled` preference. The `feature_flags` context processor gained only `"guild_welcome_email_enabled"`. The one change from this section that survives is unrelated to gating: the guild-lead tour step rename in §5.

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

1. **`hub/forms.py:749-789` `SiteSettingsForm.Meta.fields`** — add `"guild_welcome_email_enabled"` (after `"wiki_link_enabled"` at line 770). *(Descoped: `guided_tours_enabled` is not added.)*
2. **`templates/hub/admin/site_settings.html:424-441` Features tab** — add one include after line 437 (`wiki_link_enabled`):
   ```
   {% include "components/form_field.html" with field=form.guild_welcome_email_enabled %}
   ```
3. **CRITICAL gotcha — `templates/hub/admin/site_settings.html:159`**: the General tab renders every form field NOT named in that giant `field.name !=` exclusion chain. Add `and field.name != 'guild_welcome_email_enabled'` to the chain or the toggle renders **twice** (General + Features). The existing spec pattern `tests/hub/admin_views_spec.py:910` (`count(b'id="id_my_tab_enabled"') == 1`) exists precisely to catch this — add the same count-==-1 assertion for the new field. *Drive-by observation (do not fix in this PR unless asked): `member_directory_public` is in the Features tab (line 440) but missing from the line-159 exclusion chain, so it likely already renders twice.*

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

1. **core**: one auto-generated add-field migration (next after `core/migrations/0070_siteconfiguration_display_demo_classes_and_more.py`) adding `guild_welcome_email_enabled` with `default=True`. No data migration — the singleton row picks up the default; existing behavior is unchanged on deploy. *(Descoped: the tours boolean is not added.)*
2. **membership**: one auto-generated `AlterField` migration (next after `membership/migrations/0149_alter_guild_discord_welcome_message.py`) for the `discord_welcome_message` help_text rename. Django detects help_text changes; run `makemigrations` once after all model edits so each app gets exactly one migration.
3. Both are trivially reversible (RemoveField / AlterField back) — no RunPython needed.

## 7. Test plan (BDD pytest-describe, `*_spec.py`)

Extend existing specs *(descoped 2026-09-02: all tours-flag specs removed; only the welcome-email and rename specs remain)*:
- **`tests/core/models_spec.py`** (~line 50, next to `it_defaults_my_tab_enabled_to_true`): `it_defaults_guild_welcome_email_enabled_to_true`.
- **`tests/core/context_processors_spec.py:69-100`**: the `it_reflects_toggled_values` spec asserts **dict equality** of the whole `feature_flags` result (line ~95) — it will fail until the new key is added to both the setup and the expected dict; also extend the defaults spec.
- **`tests/hub/guild_welcome_editor_spec.py`**: rename line 161's assertion; add `it_hides_the_welcome_email_tab_when_the_site_flag_is_off` — assert `b"section === 'welcome_email'"` **not** in content (structural marker; see trap below).
- **`tests/membership/guild_welcome_spec.py`**: `it_sends_nothing_when_the_site_flag_is_off` (flag off + enabled guild settings → `len(mail.outbox) == 0`); existing specs cover flag-on.
- **`tests/hub/guild_join_view_spec.py`**: flag off + `send_welcome=on` → no email, toast is plain `"You joined X!"` (no "Check your inbox"); flag on → toast says "welcome email" (update any existing "welcome packet" toast assertions).
- **`tests/hub/discord_commands_spec.py`**: `/join-guild` with flag off sends no email (exercises the shared gate through the second caller).
- **`tests/hub/admin_views_spec.py`** (~905-975): the new toggle's id appears exactly once on the Site Settings page (the duplicate-render guard, §4.3); the save POST persists `guild_welcome_email_enabled=False` and re-enables (mirror lines 943-972).

Coverage note: every new `if not …enabled` branch needs both directions exercised (100% branch coverage + mutation kill), which the list above does.

## 8. Changelog (`plfog/version.py`)

Bump `VERSION` to 1.22.0 and add ONE member-facing entry (orchestrator-owned; builder does NOT touch version.py).

## 9. Risks and traps (read before building)

1. **The changelog renders into every hub page** (`core/context_processors.py:19-23` + `templates/hub/base.html:525` changelog modal). The new entry will contain the words "Welcome Email", "guided tours", and historic entry 1.21.0 already contains **"Show me around"** (`plfog/version.py:34-36`). Therefore new flag-off specs must NEVER negatively assert those bare phrases against full-page content — `assert b"Show me around" not in response.content` fails on every page today. Use structural markers instead: `b"section === 'welcome_email'"` (tab button), `b"?tour=member-welcome"` (entry href), `b"pl-tour-data"` (payload), `b'value="tours"'` (settings form).
2. **Site-settings duplicate render** — new fields must join the line-159 exclusion chain (§4.3) or they appear twice and the count-==-1 spec fails.
3. **`tests/core/context_processors_spec.py:95` dict-equality** breaks the moment keys are added to `feature_flags` — update in the same commit.
4. **e2e lane** (`pytest tests/e2e`, separate Playwright lane): `tests/e2e/guided_tour_spec.py` asserts tour behavior with defaults (flag True — unaffected); it asserts "Show me around" on the offer card and "Welcome to the Member Portal" (member tour step title — unchanged). No e2e spec asserts "Welcome Packet" except the gallery registry string being renamed (§5). No Playwright spec breaks from the rename because none asserts the guild-editor tab label.
5. ~~**Name collision**: `guided_tours_enabled` on both `Member` and `SiteConfiguration`~~ — moot after the descope; only the `Member` field exists.
6. ~~**Static help articles** still link "Take the tour" when tours are off~~ — moot after the descope; tours are always on.
7. **Deep link `?tab=welcome_email`** on the guild editor with the flag off shows an empty content area (tab strip visible, no panel) — matches the existing my_tab Buyables precedent; acceptable.
8. `hub/forms.py:1660-1667` help_texts override comment is now wrong-way-round; delete the override (§5) rather than leaving a misleading comment for the next agent.
