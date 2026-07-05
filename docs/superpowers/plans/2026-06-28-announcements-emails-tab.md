# Announcements/Emails tab: move the emails in, add the note + opt-out toggles

**Commit 3 of release 0.20.x.** Surface: the Announcements section of `guild_edit.html` (in-page tab). Touches
`hub/forms.py`, `hub/views.py`, `membership/models.py` (`GuildAnnouncement`, `GuildOrientationSettings`).

## Three changes

### A. Rename the tab "Announcements/Emails" and move the two email editors into it

The **Thank-you email** and **Welcome email** editors currently live on the Orientations page as fields of
`GuildOrientationSettingsForm` (`thankyou_email_*`, `join_email_*` on the `GuildOrientationSettings` model;
rendered `orientation_settings.html:93-106`). Move their UI to the Announcements/Emails tab. **No data migration** —
the fields stay on `GuildOrientationSettings`; only the editing UI moves.

- Tab label → **"Announcements/Emails"** (`section === 'announcements'`, key unchanged).
- New `GuildGuildEmailsForm` (ModelForm on `GuildOrientationSettings`, fields = the six email fields) carrying the
  enable-requires-subject+body `clean` and the `*_updated_at` stamping that currently lives in
  `GuildOrientationSettingsForm` (move that logic over; drop those fields + their clean/save bits from the orientation
  settings form).
- New save view `guild_emails_save(request, pk)` (editor-only) + URL `hub_guild_emails_save`; its own `<form
  action=…>` in the Announcements/Emails tab with a primary "Save emails" button; redirect to `?tab=announcements`.
- Render two `hub-card`s ("Thank-you email", "Welcome email") in the tab, below the announcement composer — same
  markup that was on the orientations page (RichText body widget preserved). The orientations settings section loses
  these two cards (commit 2 already reorganized that section).

### B. Note on "Post an Announcement": Joined members get it by email

Add a muted line in the composer card: **"Everyone who has *Joined* this guild will receive this announcement by email
(when 'Also send email' is on), plus an in-app notification."** Grounding: posting calls
`GuildAnnouncement.notify_members()` → `emit("guild_announcement", …)`, which resolves recipients to the guild's active
members (the `GuildMembership` joiners) — in-app (always), email (opt-out), Discord broadcast. The note makes the
email audience explicit.

### C. Two opt-out toggles on the composer (default ON)

Add two booleans to `GuildAnnouncement` (additive migration, no backfill):

```python
send_email = models.BooleanField(default=True, help_text="Also email this announcement to members who joined the guild.")
post_to_discord = models.BooleanField(default=True, help_text="Also post this announcement to the guild's own Discord channel.")
```

- `GuildAnnouncementForm` gains both as toggle fields (render via `components/toggle.html` / `form_field.html` so they're
  switches, not raw checkboxes), labelled "Also send email" and "Also post to {{ guild.name }}'s Discord channel",
  both checked by default. The Discord toggle is shown always, but a muted hint notes it only does something if the guild
  has a Discord webhook configured (Meetings tab) — it's a no-op otherwise (the spine already treats a blank/disabled
  webhook as "no guild post", so an unconfigured guild simply posts nothing).
- `notify_members()` honors the flags: when `send_email` is False, suppress the email channel; when `post_to_discord`
  is False, suppress the guild Discord broadcast. **Implementation note for the builder:** inspect the `emit()` signature
  (`core/events/emit.py`) for the existing channel/broadcast suppression hooks (memory: `emit(..., suppress_broadcast=…)`
  exists for site-wide sends) and the per-guild `_guild_broadcast`. Pass through the per-announcement choices; keep the
  in-app bell always on. Preserve the unique `period=f"announcement:{self.pk}"` (dedup) — see the emit-period rule.

**OPEN QUESTION for Josh (answer at plan review):** announcements *also* post to the **makerspace-wide** Discord channel
today (the global webhook), independently of the guild's own channel. Your requested toggle names the *guild's* channel.
Should "Also post to Discord" govern **only the guild channel** (global keeps posting as today — my default), or should
it gate the **makerspace-wide** post too? I'll wire whichever you pick; default is guild-channel-only unless you say
otherwise.

## UI / UX completeness

- Composer keeps its visible "Post Announcement" submit; the two toggles sit above it with the audience note; both
  default ON. "Recent Announcements" list unchanged (edit-after-post already exists).
- Email editors: each has a visible "Save emails" submit, enable-requires-subject+body validation surfaces inline, and
  the RichText body editor renders in both themes (existing `RichTextEditorWidget`).
- Toggles are switches (not raw checkboxes); margins clear the field above; mobile reflows; no inline `background`/`color`.
- Empty/disabled states: Discord toggle hint when no webhook; email toggle off → composer still posts to the page +
  in-app bell (note copy reflects the conditional).

## Tests

- Model: `GuildAnnouncement.send_email` / `post_to_discord` default True; `notify_members` suppresses email when
  `send_email=False` and suppresses the guild Discord post when `post_to_discord=False`, while still emitting the in-app
  bell and (per the open question) the global post. Use the emit spine's test seams; mock the Discord channel, never the DB.
- Form: toggles present and default-checked; posting with a toggle off persists False and the view passes it through.
- `guild_emails_save`: saves the six fields, stamps `*_updated_at`, enable-requires-subject+body enforced, redirects to
  `?tab=announcements`; permission gate enforced. Orientation settings save no longer touches the email fields.
- BDD `*_spec.py`, `describe_`/`it_`, factory-boy, `respx` for any HTTP.

## Out of scope

- Moving the email *data* off `GuildOrientationSettings` (stays there; only the editor UI relocates).
- Reworking recipient resolution or the global Discord routing beyond honoring the new per-announcement flags.
