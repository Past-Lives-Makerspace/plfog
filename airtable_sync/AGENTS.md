# airtable_sync app

Bidirectional Airtable sync. Pulls Member/Space/Lease records INTO Django; pushes VotePreference/FundingSnapshot OUT to Airtable.

## Direction Summary

| Data | Direction | Trigger |
|------|-----------|---------|
| Member, Space, Lease | Airtable → Django | `airtable_pull` management command |
| VotePreference | Django → Airtable | `VotePreference.save()` / `VotePreference.delete()` |
| FundingSnapshot | Django → Airtable | `FundingSnapshot.save()` |

## Service (airtable_sync/service.py)

- `sync_vote_to_airtable(vote_preference)` — upsert VotePreference row in Airtable
- `delete_vote_from_airtable(record_id)` — delete VotePreference row from Airtable
- `sync_snapshot_to_airtable(snapshot)` — upsert FundingSnapshot row in Airtable

## Client (airtable_sync/client.py)

Thin wrapper around pyairtable: `get_api()` and `get_table(table_id)`, configured from settings. Both take `timeout` (connect, read seconds) and `retry`; the nightly pull keeps pyairtable's defaults (no timeout, retries), while a page an admin waits on passes a short timeout and no retries (the New Members lookup, `core/new_members.py`, #1115). Field mappings are in `airtable_sync/config.py`.

## Management Commands

- `airtable_pull` — pulls all records from Airtable and upserts into Django (Members, Spaces, Leases). A member it turns Former is queued for removal from Discord by `Member.save` (#898; `membership/AGENTS.md`).
- `airtable_backfill` — one-time backfill for existing records
- `backfill_old_votes` — one-time migration to sync old vote data

## Test Isolation

Root `conftest.py` has `_disable_airtable_sync` autouse fixture — sets `AIRTABLE_SYNC_ENABLED=False` in all tests. Models check `getattr(self, '_skip_airtable_sync', False)` to skip sync in tests.

## Config

`AIRTABLE_API_KEY`, `AIRTABLE_BASE_ID` env vars. `AIRTABLE_SYNC_ENABLED=False` disables all outbound syncs (used in tests and local dev).
