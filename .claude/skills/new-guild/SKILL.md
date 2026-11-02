---
name: new-guild
description: >-
  Create one or more new plfog guilds end to end: a private Discord channel with a lead role and
  webhook, the guild row in production (hidden, so only admins and that guild's staff reach it), a
  color and B/W logo, and a banner photo, then check it shows for admins and staff. Use when the user
  types /new-guild, or asks to add, create or start a new guild.
---

# /new-guild: a private guild, fully dressed

    /new-guild Music Guild
    /new-guild "Music Guild" "ERMA Guild"

A guild is not done when its row exists. Done means: the Discord channel, the hidden row, a logo, a
banner, and the guild in the sidebar for admins and its staff. Do every step, then show Felix the
logo and banner mockup before anything visual merges. Starting the Music and ERMA guilds
(2026-11-01) shipped without logos or banners and with the guild missing from admins' sidebars; that
is the failure this list exists to stop.

**Private** means `is_active=False` (Hidden). Since the hidden-guild sidebar change, admins and the
guild's lead and staff see it in the sidebar and can open its page; everyone else gets a 404. Making
it public is the Visible switch on the guild editor's Basic tab (admins only). In Discord, @everyone
is denied and only admins (Administrator) and the `<Guild> Lead` role see the channel.

Production writes in steps 3 and 5 run over SSH on the Render web instance. The auto mode classifier
denies them (Remote Shell Writes), so prepare everything first and ask Felix once to switch auto mode
off for both.

## 1. Settle the names

Use Felix's words, ending in "Guild" ("Music Guild"); a long form goes in `about`. The slug comes from
the name and never changes on a rename. Channel names follow the server: one emoji, a dash, lowercase
words (`🎵-music-guild`). Read existing names first with `prod-read` (`Guild.all_objects`); a name
that exists, even soft-deleted, is reused, never duplicated.

## 2. Discord

Write a spec to the scratchpad, a list of `{"name", "channel", "topic", "about"}`, then:

    set -a; source ~/Code/plfog/.env; set +a
    python3 .claude/skills/new-guild/discord_setup.py <scratch>/spec.json <scratch>/out

It makes the `<Guild> Lead` role, the private channel in Guilds and its webhook (reusing any that
exist by name) and writes `out/create_guilds.py`. Read the channels back: @everyone denies 3072, the
lead role and the bot are the only allows.

## 3. Production row

    scp -o BatchMode=yes <scratch>/out/create_guilds.py srv-d6j2331drdic73ak0s30@ssh.oregon.render.com:/tmp/
    ssh -o BatchMode=yes srv-d6j2331drdic73ak0s30@ssh.oregon.render.com 'cd /app && python /tmp/create_guilds.py > /tmp/cg.out 2>&1; echo exit=$?; tail -5 /tmp/cg.out; rm -f /tmp/create_guilds.py /tmp/cg.out'

It prints the prod host, then `created <pk> <name> <slug> hidden`. It holds the webhook token: never
in the repo or a PR, and removed from the instance after.

## 4. Logo (a code PR)

Logos are static files, `static/img/guild_logos/<prefix>_color.svg` and `<prefix>_bw.svg`, mapped from
the name in `membership/logos.py`. Match the set: `viewBox="0 0 50 50"`, flat fills, `#231f20`
outline at 3px with round caps and joins; the B/W file is the same drawing filled white. A thin dark
line vanishes on the navy sidebar, so draw lines as outlined tubes (a 5.5px dark stroke under a 2.5px
colored or white core), as `printmaking_*.svg` and `music_*.svg` do.

The map matches substrings, and class types use it too (the class type is the name without "Guild").
A short name that a longer word contains ("ERMA" in "Permaculture") goes in `_WORD_TO_PREFIX` as a
whole word. Add a spec for the new names and for the word it must not catch.

Render a mockup from the committed SVGs at 120, 32 and 16 px on white and the hub navy `#092e4c`, in a
mock sidebar beside existing logos, plus each banner from step 5 as the guild hero (cover crop, the
navy gradient, the B/W logo beside the name). Playwright from `.venv` renders it; upload with
`scripts/upload_screenshot.py`. Open the PR as a **draft** with the mockup and an **internal**
fragment (a members fragment announces the private guild on Discord at merge). Send Felix the image
with SendUserFile and the link in the same message, and wait for his yes before marking it ready.

## 5. Banner

Every guild has a photo banner; there is no default. Search free photos with
`~/Code/asset-cli/assets search "<query>" --landscape` (Pixabay, Unsplash), keep only `photo` rows
(the illustration and vector rows look like clip art), and download two or three per guild into the
scratchpad. Prefer gear, hands or a space over identifiable faces, and skip any photo whose tags name
people's ethnicity or nationality. Put the picks in the step 4 mockup and get Felix's choice with it.

Then fill `set_banners.py.tmpl` (`BANNERS = [(pk, "<slug>-banner.jpg"), ...]`) into the scratchpad
and, with the step 3 approval:

    scp -o BatchMode=yes <banners> <scratch>/set_banners.py srv-d6j2331drdic73ak0s30@ssh.oregon.render.com:/tmp/
    ssh -o BatchMode=yes srv-d6j2331drdic73ak0s30@ssh.oregon.render.com 'cd /app && python /tmp/set_banners.py; rm -f /tmp/set_banners.py /tmp/*-banner.jpg'

Check each printed URL answers 200 with `image/jpeg`.

## 6. Verify and report

`prod-read` the rows: `is_active` False, Discord fields and `banner_image` set. Confirm the guild is
in the sidebar for an admin (after the logo PR merges, with its logo). Report in two sentences: the
guild pages (`https://members.pastlives.space/guilds/<slug>/`), the channels, the open logo PR, and
that staff get in through the `<Guild> Lead` role once a lead is named.

## Later, not now

When Felix names a lead: `manage.py set_guild_lead --guild <pk> --member <email>` and give that
person the lead role in Discord. When the guild goes public: flip Visible on the Basic tab, add a
member role and a `Member` allow on the channel, and put the member role id in `discord_role_ids`.
