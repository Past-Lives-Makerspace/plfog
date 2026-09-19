---
name: discord-admin
description: Act as server admin for the Past Lives Makerspace Discord — manage channels, roles, messages, polls, scheduled events, webhooks, and (once granted) member moderation, via the Fog Bot REST API. Use when the user asks to inspect or change anything on the Discord server.
---

# Discord Admin — Past Lives Makerspace

Direct REST administration of the Past Lives Discord server using the Fog Bot token. No MCP server, no gateway — every operation is a curl call.

## Setup (every invocation)

```sh
set -a && source ~/Code/plfog/.env && set +a
GUILD=933589656996565023
API=https://discord.com/api/v10
AUTH="Authorization: Bot $DISCORD_BOT_TOKEN"
```

- Bot: **Fog Bot** (`1520295376727572661`) — the plfog production bot. Its actions are live on the real server; there is no staging Discord.
- If `.env` is missing the token, it also exists in Render env for the `plfog` service.

## Permissions held (verified 2026-07-28)

Fog Bot's role has: create_invite, manage_channels, manage_messages, manage_roles, manage_webhooks, manage_events, send_polls.

**NOT held:** kick, ban, moderate_members (timeout), manage_guild. Those need Jo to toggle them on the Fog Bot role in Server Settings → Roles first — a bot cannot self-grant. If a call returns 403 `Missing Permissions`, report which grant is missing instead of retrying.

Role operations only work on roles BELOW Fog Bot's top role in the hierarchy.

## Ground rules

1. **Read before write.** Fetch the current object (channel, role, member, event) before modifying it, and echo what will change.
2. **Confirm destructive or outward-facing actions with the user first**: deleting channels/roles/messages, bans/kicks, posting messages members will see. Renames and permission tweaks on request are fine to just do.
3. Discord snowflake IDs are strings — never let Python/jq cast them to int (precision loss).
4. On 429, honor `retry_after` from the body, then retry once.
5. Timestamps in/out of the API are UTC. The makerspace runs on America/Los_Angeles — always convert when talking to the user, and remember evening events (5 PM+ PDT) fall on the NEXT calendar day in UTC.

## Common operations

All examples assume the Setup block ran. Add `-H "Content-Type: application/json"` on writes.

### Inspect
- Channels: `curl -s -H "$AUTH" $API/guilds/$GUILD/channels`
- Roles: `curl -s -H "$AUTH" $API/guilds/$GUILD/roles`
- Members (paged): `curl -s -H "$AUTH" "$API/guilds/$GUILD/members?limit=1000"`
- Search member: `curl -s -H "$AUTH" "$API/guilds/$GUILD/members/search?query=NAME"`
- Scheduled events: `curl -s -H "$AUTH" $API/guilds/$GUILD/scheduled-events`
- Audit log (recent admin actions): `curl -s -H "$AUTH" "$API/guilds/$GUILD/audit-logs?limit=25"`

### Channels
- Create: `POST $API/guilds/$GUILD/channels` with `{"name": "...", "type": 0, "parent_id": "CATEGORY_ID", "topic": "..."}` (type 0 text, 2 voice, 4 category, 15 forum)
- Edit: `PATCH $API/channels/CHANNEL_ID` with the changed fields
- Delete: `DELETE $API/channels/CHANNEL_ID` (confirm with user first)
- Per-channel role overwrite: `PUT $API/channels/CHANNEL_ID/permissions/ROLE_ID` with `{"type": 0, "allow": "BITSET", "deny": "BITSET"}`

### Roles & members
- Create role: `POST $API/guilds/$GUILD/roles` with `{"name": "...", "permissions": "0", "color": 5793266, "mentionable": true}`
- Assign/remove role: `PUT` / `DELETE $API/guilds/$GUILD/members/USER_ID/roles/ROLE_ID`
- Nickname: `PATCH $API/guilds/$GUILD/members/USER_ID` with `{"nick": "..."}`
- Timeout (needs moderate_members): same PATCH with `{"communication_disabled_until": "ISO8601"}` (null clears)
- Kick (needs kick): `DELETE $API/guilds/$GUILD/members/USER_ID`
- Ban (needs ban): `PUT $API/guilds/$GUILD/bans/USER_ID` with `{"delete_message_seconds": 0}`

### Messages & polls
- Send: `POST $API/channels/CHANNEL_ID/messages` with `{"content": "..."}`
- Poll: same endpoint with
  ```json
  {"poll": {"question": {"text": "..."}, "answers": [{"poll_media": {"text": "Option A"}}, {"poll_media": {"text": "Option B"}}], "duration": 72, "allow_multiselect": false}}
  ```
  (`duration` is hours, max 768. Polls cannot be edited after posting — get the wording confirmed first.)
- Poll results: `GET $API/channels/CHANNEL_ID/polls/MESSAGE_ID/answers/ANSWER_ID`
- Pin: `PUT $API/channels/CHANNEL_ID/pins/MESSAGE_ID`
- Delete message: `DELETE $API/channels/CHANNEL_ID/messages/MESSAGE_ID`

### Scheduled events
- Create/edit via `POST|PATCH $API/guilds/$GUILD/scheduled-events[/EVENT_ID]`. External (physical) events need `entity_type: 3`, `scheduled_end_time`, and `entity_metadata.location`, with `privacy_level: 2`.
- **Recurrence weekdays are evaluated in UTC** (`by_weekday`: 0=Monday). For an evening Portland event the UTC weekday is one day later than the local one — see the v0.23.45 fix in plfog (`core/integrations/discord_events.py`).
- **plfog owns some events**: guild meetings and general-feed events are pushed by FOG (`CommunityEvent.discord_event_id` / nightly `sync_all_sources`). Prefer fixing those in FOG so the app doesn't overwrite a hand edit; hand-patch Discord only for immediate corrections, and say so.

## What this skill cannot do

- React to messages in real time (needs the gateway or plfog's interactions endpoint — that's app code, not admin calls).
- Server settings behind manage_guild or Administrator (vanity URL, integrations list, etc.) until those are granted.
- Anything on other Discord servers — Fog Bot is only in Past Lives Makerspace.
