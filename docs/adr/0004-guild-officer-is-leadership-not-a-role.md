# A guild officer is a guild's lead or staff, not a role: the cross-guild Guild Officer role is retired

**Context.** `Member.fog_role` had three values: admin, guild officer, member. Guild officer was set by hand and was cross-guild: it edited every guild's settings and every class (`is_effective_staff`, `Member.can_edit_guild`, `ClassOffering.objects.editable_by`), carried Django `is_staff`, could change other members' roles, and was a View As rung. Meanwhile a guild's real authority already came from its own rows: the `Guild.guild_lead` FK and `GuildStaffMembership`. In production an admin had given the role to 19 people, nearly every lead and Co-Lead, so a lead could change another guild's settings. A lead noticed and asked whether that was intended. It was not: the role had drifted from "a few trusted people" into "everyone who leads", and it granted far more than leading does.

**Decision.** Felix: "If someone is guild staff or lead or admin, they are a guild officer." The role is retired. `FogRole` is admin or member. Migration `membership 0275_retire_guild_officer_role` moves every holder to member and takes away the `is_staff` the role gave their login (a superuser keeps theirs); the reverse is a no-op, since nothing records who held it. "Guild officer" is now a status derived from leading or staffing any guild. One rule decides every surface the role touched:

- **Own guild = leadership.** What the role gave that a lead should have (the guild-staff What's New tab, the guild lead tour, the notification settings rows, the KB's officer tier, the Guild Lead Meeting audience, the wiki sticker sheet and editing Official pages for their own guilds) now comes from leading or staffing a guild. The KB tier and the sticker sheet are new for staff who never held the role.
- **Every guild = admin.** What the role gave across guilds (editing any guild or class, every guild's lists and queues, changing roles, moderating space-wide wiki pages, Django `is_staff` and the pages it opened: `/admin`, Site Activity's email audit, Invite Member) is admin only. `membership.permissions.is_effective_staff` keeps its name to avoid churn and now means an effective admin.

Official wiki pages were editable by admins and officers. A guild's Official pages are now editable by that guild's lead and staff, its officers, and by admins; a space-wide Official page, and any page marked admins only, stays admin only. Publishing a safety proposal is unchanged: an admin lands it Official, a guild's lead or staff lands it Guild verified.

**Consequences.**
- A lead who held the role keeps everything on their own guild and loses edit rights on the others. Anyone who genuinely needs to act on every guild needs admin.
- The member directory is unchanged: admins, leads and instructors are always listed, and staff choose for themselves. Staff who held the role were force-listed through it and now choose too.
- View As loses its Guild Officer choice. A session still holding the old pick resolves to the viewer's actual roles rather than failing.
- The Discord "Guild Officers" role (`discord_guild_officers_role_id`) was already derived from leads and staff and keeps its name.
- A future cross-guild need gets a scoped grant (an `AdminCapability`) rather than a role, so it routes and grants one thing and cannot drift into general authority.
