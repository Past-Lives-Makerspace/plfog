# hub app

Member-facing views. All views are `@login_required`. No models — reads from `membership` and `billing`.

## Views → URLs

| View | URL name | Path |
|------|----------|------|
| `guild_voting` | `hub_guild_voting` | `/guilds/voting/` |
| `snapshot_history` | `hub_snapshot_history` | `/guilds/voting/history/` |
| `snapshot_detail` | `hub_snapshot_detail` | `/guilds/voting/history/<pk>/` |
| `guild_detail` | `hub_guild_detail` | `/guilds/<slug>/` (old `/guilds/<pk>/` 301-redirects via `hub_guild_detail_by_id`) |
| `member_directory` | `hub_member_directory` | `/members/` |
| `profile_settings` | `hub_profile_settings` | `/settings/profile/` |
| `email_preferences` | `hub_email_preferences` | `/settings/emails/` |
| `beta_feedback` | `hub_beta_feedback` | `/feedback/` |
| `tab_detail` | `hub_tab_detail` | `/tab/` |
| `tab_history` | `hub_tab_history` | `/tab/history/` |
| `announcements_overview` | `hub_announcements` | `/announcements/` (Drafts and Sent tabs, `?tab=drafts\|sent`) |
| `announcement_sent` | `hub_announcement_sent` | `/announcements/sent/<pk>/` (read-only record of a sent or sending announcement) |

## Announcements

The composer (`hub_compose*`) and the Announcements page share one visibility rule, `_announcement_rows(request, member)` in `hub/views.py`: a row is visible and actionable exactly when `_compose_audience_forbidden(request, draft.audience_value)` is `None` (admins short-circuit to every row). Every composer lookup that takes a `draft_pk` goes through `_handled_draft`, which applies it to resumable rows only. Drafts are shared; `author` is whoever saved last, and the sender once sent.

## Common Pattern

All views call `_get_hub_context(request)` for sidebar data (guild list, user initials) and `_get_member(request)` to get the logged-in `Member`. Views gracefully handle `member is None` (unlinked account).

## Forms (hub/forms.py)

- `VotePreferenceForm` — 3 guild FK selects (guild_1st, guild_2nd, guild_3rd)
- `ProfileSettingsForm` — edits Member fields (pronouns, about_me, discord_handle, etc.)
- `EmailPreferencesForm` — email notification toggles
- `BetaFeedbackForm` — bug/feature/general feedback; calls `form.send(user=user)`
- Self-service tab-entry form: `billing.forms.TabItemForm` with `context="member_tab_page"` (replaces the old `AddTabEntryForm` removed in v1.5.0)

## Templates

`templates/hub/` — one template per view. Layout uses shared `templates/base.html`.

## Template Tags

`hub/templatetags/hub_tags.py` — filters/tags used in hub templates.
