# A guild officer is a guild's lead or staff, not a role: the cross-guild Guild Officer role is retired

**Context.** `Member.fog_role` had three values: admin, guild officer, member. Guild officer was set by hand and was cross-guild: it edited every guild's settings and every class (`is_effective_staff`, `Member.can_edit_guild`, `ClassOffering.objects.editable_by`), carried Django `is_staff`, could change other members' roles, and was a View As rung. Meanwhile a guild's real authority already came from its own rows: the `Guild.guild_lead` FK and `GuildStaffMembership`. In production an admin had given the role to 19 people, nearly every lead and Co-Lead, so a lead could change another guild's settings. A lead noticed and asked whether that was intended. It was not: the role had drifted from "a few trusted people" into "everyone who leads", and it granted far more than leading does.

**Decision.** Felix: "If someone is guild staff or lead or admin, they are a guild officer." The role is retired. `FogRole` is admin or member. Migration `membership 0275_retire_guild_officer_role` moves every holder to member and takes away the `is_staff` the role gave their login (a superuser keeps theirs); the reverse is a no-op, since nothing records who held it. "Guild officer" is now a status derived from leading or staffing any guild. One rule decides every surface the role touched:

- **Own guild = leadership.** What the role gave that a lead should have (the directory force-listing, the guild-staff What's New tab, the guild lead tour, the notification settings rows, the KB's officer tier, the Guild Lead Meeting audience) now comes from leading or staffing a guild.
- **Every guild = admin.** What the role gave across guilds (editing any guild or class, every guild's lists and queues, changing roles, Django `is_staff`) is admin only. `membership.permissions.is_effective_staff` keeps its name to avoid churn and now means an effective admin.

Official wiki pages were editable by admins and officers. They are admin only now; letting every guild staff role edit them would undo the deliberate protection that keeps a treasurer or orientor from rewriting a safety policy (`hub/wiki_views.py`). Widening that to a page's own lead and staff is a separate decision.

**Consequences.**
- A lead who held the role keeps everything on their own guild and loses edit rights on the others. Anyone who genuinely needs to act on every guild needs admin.
- View As loses its Guild Officer choice. A session still holding the old pick resolves to the viewer's actual roles rather than failing.
- The Discord "Guild Officers" role (`discord_guild_officers_role_id`) was already derived from leads and staff and keeps its name.
- A future cross-guild need gets a scoped grant (an `AdminCapability`) rather than a role, so it routes and grants one thing and cannot drift into general authority.
