# plfog — Source Map

Django 5 app for Past Lives Makerspace (Portland, OR). Repo: github.com/Past-Lives-Makerspace/plfog.

## Apps

| App | Role |
|-----|------|
| `core` | SiteConfiguration singleton (`SiteConfiguration.load()`), auth/invite flow, Web Push, event notification infrastructure (`core/events/`) |
| `membership` | All domain models: Member, Guild, Meeting, CommunityEvent, MeetingItemProposal, FundingSnapshot, Space, Lease, VotePreference, etc. |
| `hub` | All hub views and templates: `meeting_views.py`, `views.py`, `notification_views.py`, `forms.py`, calendar/Discord integrations |
| `billing` | Stripe billing |
| `classes` | Class offerings, sessions, orientation |
| `airtable_sync` | Read-only pull from Airtable (Member, Space, Lease) |
| `api` | DRF API endpoints |

## Key reference files

- `CLAUDE.md` — coding standards (fat models, skinny views, type hints, 100% coverage)
- `FRONTEND.md` — component library and design system rules
- `CODEBASE_INDEX.md` — full app/model/URL map

## Environments

- **Production**: Render.com (PostgreSQL via DATABASE_URL)
- **QA/Staging**: Hetzner VPS (`pastlives.plaza.codes`)
- **Local**: WSL2, SQLite by default

## Related memories

- `mem:tech_stack` — language, framework, tooling versions
- `mem:conventions` — code style, patterns, test rules
- `mem:suggested_commands` — dev/test/lint commands
- `mem:task_completion` — definition of done
