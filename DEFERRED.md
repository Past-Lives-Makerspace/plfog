# Deferred: guild + site announcement notifications

The guild-pages redesign (Plan 3 of the notifications/guilds/activity spec) was
built on top of `release-2.5.0`, which does **not** contain Plan 2's notification
engine. Three notification touchpoints were therefore deferred. Everything else
in the redesign shipped.

**Wire these once Plan 2 has merged** — i.e. once both of these exist:

- `core/notifications.py` with `dispatch(trigger, users, *, title, body, url)` and `active_member_users()`
- `core/models.py` with the `Notification` model (so `Notification.objects.filter(trigger=...)` works)

After that, this becomes a small, self-contained follow-up PR.

---

## 1. `GuildAnnouncement.publish` — fires the `guild_announcement` trigger

**File:** `membership/models.py` (the `GuildAnnouncement` model).

Add the `publish` classmethod (the model and its `__str__`/ordering already exist):

```python
@classmethod
def publish(cls, *, guild: Guild, author: Any | None, title: str, body: str) -> GuildAnnouncement:
    """Create an announcement and notify the guild's members."""
    from django.contrib.auth.models import User

    from core import notifications

    announcement = cls.objects.create(guild=guild, author=author, title=title, body=body)
    member_user_ids = guild.memberships.filter(member__user__isnull=False).values_list(
        "member__user_id", flat=True
    )
    users = User.objects.filter(pk__in=list(member_user_ids))
    notifications.dispatch(
        "guild_announcement", users,
        title=f"{guild.name}: {title}", body=body[:200], url=f"/guilds/{guild.pk}/",
    )
    return announcement
```

**Test (re-enable):** `tests/membership/guild_content_models_spec.py` — the
`describe_GuildAnnouncement` block has a NOTE marking the dropped
`it_notifies_guild_members_when_published_via_publish` test. Restore it.

## 2. `guild_announcement_create` view — the in-app "Post Announcement" endpoint

**Files:** `hub/views.py`, `hub/urls.py`, `templates/hub/guild_edit.html`,
`tests/hub/guild_announcements_spec.py`.

- Add the `guild_announcement_create` view (editor-only, `@require_POST`) that calls
  `GuildAnnouncement.publish(guild=guild, author=request.user, title=..., body=...)`
  using `hub.forms.GuildAnnouncementForm` (the form already exists).
- Add the route `guilds/<int:pk>/announcements/` → `name="hub_guild_announcement_create"`.
- In `templates/hub/guild_edit.html`, replace the "Posting announcements … arrives with
  the notifications system." note with a real Post-Announcement form
  (`{% include "components/form_field.html" %}` for `announcement_form.title`/`.body`,
  posting to `hub_guild_announcement_create`). Pass a `GuildAnnouncementForm()` into the
  `guild_edit` context.
- Add `describe_announcement_create` test (the spec file already documents this in its
  module docstring). The `guild_announcement_delete` endpoint already ships.

## 3. Site-wide announcement broadcast — fires the `site_announcement` trigger (Task 11)

**Files:** `hub/views.py` (`admin_site_settings`),
`templates/hub/admin/site_settings.html`, `tests/hub/site_announcement_spec.py` (new).

- In `admin_site_settings`, add a `request.POST.get("form_id") == "site_announcement"`
  branch using `hub.forms.SiteAnnouncementForm` (already exists) that calls
  `notifications.dispatch("site_announcement", notifications.active_member_users(),
  title=..., body=..., url="/")`.
- Pass a `SiteAnnouncementForm()` into the settings template context and add the
  broadcast card to `site_settings.html`.
- Add `tests/hub/site_announcement_spec.py` asserting a `Notification` row with
  `trigger="site_announcement"` is created for each active member user.

---

After all three are wired, every key in `core/triggers.py` has a caller, completing
the spec at `docs/superpowers/specs/2026-06-08-notifications-guilds-activity-design.md`.
