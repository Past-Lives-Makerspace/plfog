---
name: new-guild
description: >-
  Create one or more new plfog guilds end to end: a private Discord channel in the Guilds category, a
  "<Guild> Lead" role that lets the guild's staff in, a webhook so announcements post there, and the
  guild row in production, hidden so only admins and guild staff can reach it. Use when the user types
  /new-guild, or asks to add, create or start a new guild.
---

# /new-guild: a private guild in Discord and the portal

    /new-guild Music Guild
    /new-guild "Music Guild" "ERMA Guild"

No portal page creates a guild (Guild left the Django admin in v1.6.0), so a new guild is a Discord
step the bot can do and a production write Felix approves. A new guild starts **private**:

- **Portal:** `is_active=False`, the existing Hidden switch. It is left out of the sidebar, the
  directory, the calendar, voting and the Discord guild commands. Admins and the guild's lead and
  staff reach its page and settings; making it public is the Visible switch on the guild editor's
  Basic tab (admins only). The guild page itself still opens for anyone who has the exact link; that
  is how Hidden works today.
- **Discord:** a channel in Guilds with @everyone denied and no Member grant. Admins see it through
  Administrator; the guild's staff see it once someone gives them the `<Guild> Lead` role.

## 1. Settle the names

Use Felix's words for the guild name and end it in "Guild" like the others ("Music Guild"). The slug
comes from the name and never changes on a rename, so get it right now. Channel names follow the
server: one emoji, a dash, lowercase words (`🎵-music-guild`). Any long form of the name goes in
`about`, not the name. Read the existing names first:

    zsh ~/Code/brain/bin/prod-read -C ~/Code/plfog -f <probe printing Guild.all_objects names>

A name that already exists (including a soft-deleted one) is reused by step 3, not duplicated.

## 2. Discord

Write a spec to the scratchpad, a list of `{"name", "channel", "topic", "about"}`, then:

    set -a; source ~/Code/plfog/.env; set +a
    python3 .claude/skills/new-guild/discord_setup.py <scratch>/spec.json <scratch>/out

It makes the lead role, the private channel and its webhook, reusing any that already exist by name,
and writes `out/create_guilds.py` with the channel id, name, link and webhook filled in. Read the
channels back and check the overwrites: @everyone denies 3072, the lead role and the bot are the only
allows.

## 3. Production row

`create_guilds.py` runs on the Render web instance. The auto mode classifier denies this write
(Remote Shell Writes), so ask Felix to switch auto mode off, then run, bare:

    scp -o BatchMode=yes <scratch>/out/create_guilds.py srv-d6j2331drdic73ak0s30@ssh.oregon.render.com:/tmp/
    ssh -o BatchMode=yes srv-d6j2331drdic73ak0s30@ssh.oregon.render.com 'cd /app && python /tmp/create_guilds.py > /tmp/cg.out 2>&1; echo exit=$?; tail -5 /tmp/cg.out'

It prints the prod host, then `created <pk> <name> <slug> hidden` per guild. It contains the webhook
token, so it never goes in the repo or a PR.

## 4. Verify and report

Read the rows back with `prod-read`: `is_active` False, the Discord fields set. Report in two
sentences: the guilds and their pages (`https://members.pastlives.space/guilds/<slug>/`), the
channels, and that staff get in through the `<Guild> Lead` role once a lead is named.

## Later, not now

When Felix names a lead: set it on the guild (`manage.py set_guild_lead`) and give that person the
lead role in Discord. When the guild goes public: flip Visible on the Basic tab, add a member role and
a `Member` allow on the channel, and put the member role id in `discord_role_ids`.
