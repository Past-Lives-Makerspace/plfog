# Member Hub Design Spec

## Context

Past Lives Makerspace has an admin dashboard (django-unfold) but no member-facing dashboard. Members currently land on a public home page after login. This spec defines a member hub with sidebar navigation, guild pages, user settings, and dark/light mode theming.

## Architecture

New Django app: `hub`

- Owns all member-facing dashboard views, URLs, and templates
- Separate template layout (`hub/base.html`) from public pages (`base.html`) and admin (unfold)
- Reuses existing models from `membership` app (Guild, Member, VotingSession, GuildVote)
- No new models required (email preferences stored via a simple `EmailPreference` model in hub, or a JSONField on Member)

## Layout

### Sidebar (left, 200px)
- **Always navy (#092E4C)** in both dark and light modes
- Brand logo/name at top: "Past Lives" with camping icon
- Fixed link: **Guild Voting** (checkmark icon, tuscan yellow when active)
- Section header: "GUILDS" (uppercase, muted)
- Dynamic list of all active guilds (`Guild.objects.filter(is_active=True)`), each with a small circle SVG icon
- Active page gets highlighted background (`rgba(255,255,255,0.08)`) and tuscan yellow icon

### Top Bar (right of sidebar, full width)
- Profile avatar in top right: user initials in tuscan yellow circle
- Click avatar opens dropdown menu:
  - User name + email (display only)
  - Profile (link to `/settings/profile/`)
  - Email Preferences (link to `/settings/emails/`)
  - Dark / Light Mode (JS toggle)
  - Log Out (separator above, red text)
- Click outside dropdown closes it

### Main Content Area
- Below top bar, right of sidebar
- Dark mode: #12121f background, navy (#092E4C) cards
- Light mode: #f8f6f0 background, white cards with #e0ddd5 borders

## Pages

### Guild Voting (`/guilds/voting/`)
- Reuses existing vote logic from `membership.vote_views.vote`
- Re-skinned to use hub layout instead of `base.html`
- Shows voting form during open sessions, results link, closed message as appropriate

### Guild Detail (`/guilds/<int:pk>/`)
- Guild name (Playfair Display heading)
- Guild lead name
- Guild notes/description
- Members list: avatar (initials), name, "Lead" badge for guild_lead
- Members are derived from the Member model where role indicates guild membership (or a future M2M — for now, show guild_lead and note that full membership tracking is a future feature)

### Profile Settings (`/settings/profile/`)
- Form to edit: preferred_name, phone
- Display-only: email (managed by allauth)
- Uses hub layout

### Email Preferences (`/settings/emails/`)
- Toggle switches for email notification categories
- Simple model: `EmailPreference(member, category, enabled)` or start with a single "receive voting result emails" toggle
- Uses hub layout

### Home Page (`/`)
- `core.views.home` checks `request.user.is_authenticated`
- Authenticated: redirect to `/guilds/voting/` (hub default page)
- Anonymous: render current `home.html` landing page

## Dark/Light Mode

- Default: dark mode
- Stored in `localStorage` key `theme`
- JS toggles `data-theme="light"` on `<html>` element
- CSS uses custom properties scoped to `[data-theme="light"]`
- Sidebar remains navy in both modes
- On page load, JS reads localStorage and applies theme before render (in `<head>`) to prevent flash

### CSS Custom Properties

```
/* Dark (default) */
--hub-bg: #12121f;
--hub-card-bg: #092E4C;
--hub-card-border: transparent;
--hub-text: #F4EFDD;
--hub-text-muted: #96ACBB;
--hub-topbar-bg: #1a1a2e;
--hub-topbar-border: rgba(255,255,255,0.06);
--hub-dropdown-bg: #222238;
--hub-dropdown-border: rgba(255,255,255,0.1);

/* Light */
[data-theme="light"] {
  --hub-bg: #f8f6f0;
  --hub-card-bg: #ffffff;
  --hub-card-border: #e0ddd5;
  --hub-text: #1D1E1E;
  --hub-text-muted: #666;
  --hub-topbar-bg: #ffffff;
  --hub-topbar-border: #e0ddd5;
  --hub-dropdown-bg: #ffffff;
  --hub-dropdown-border: #e0ddd5;
}
```

## URL Routing

```
# hub/urls.py
/guilds/voting/          → guild_voting
/guilds/<int:pk>/        → guild_detail
/settings/profile/       → profile_settings
/settings/emails/        → email_preferences

# plfog/urls.py — add:
path("", include("hub.urls")),
```

Update `core/views.py` home view to redirect authenticated users.

## Files to Create

- `hub/__init__.py`
- `hub/apps.py`
- `hub/urls.py`
- `hub/views.py`
- `hub/templatetags/__init__.py`
- `hub/templatetags/hub_tags.py` (template tag for active nav highlighting)
- `templates/hub/base.html` (sidebar + topbar layout)
- `templates/hub/guild_voting.html`
- `templates/hub/guild_detail.html`
- `templates/hub/profile_settings.html`
- `templates/hub/email_preferences.html`
- `static/css/hub.css` (hub-specific styles, CSS custom properties)
- `static/js/hub.js` (theme toggle, dropdown menu)

## Files to Modify

- `plfog/settings.py` — add `hub` to INSTALLED_APPS
- `plfog/urls.py` — include hub URLs
- `core/views.py` — redirect authenticated users from home to hub
- `plfog/adapters.py` — update `AdminRedirectAccountAdapter` to send non-staff to hub default page

## Verification

1. Run `python manage.py runserver`, log in as a non-staff user
2. Verify redirect from `/` to `/guilds/voting/`
3. Click each guild in sidebar — verify guild detail page loads with correct data
4. Click profile avatar — verify dropdown opens/closes
5. Toggle dark/light mode — verify theme persists across page loads
6. Navigate to Profile and Email Preferences pages
7. Run `pytest` — all existing tests pass
8. Run `ruff check .` and `mypy .` — no errors
