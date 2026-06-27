# Release 0.19.x — Go-Live Runbook

Everything prod needs beyond "deploy the code." Prod = **Render**. Work top to bottom.
Env vars live in the **Render dashboard** (not `.env.prod`, which is only DB + MediaWiki creds), so the env section is "verify each is set," not a diff.

---

## 0. TL;DR — what you must not skip
- **`provision_member_users`** — run **manually, once** (`--dry-run` first). It **mints login accounts** for existing members; the 0.19.14 login/invite/one-tap features need it. Deliberately not automated.
- **`DISCORD_NOTIFY_WEBHOOK_URL`** set in Render — or zero Discord announcements post. Not in `.env.example`, easy to miss.
- **Discord DM channel (v0.19.25)** — set `DISCORD_BOT_TOKEN` / `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` with **fresh prod creds** (rotate, don't reuse dev), and **register the prod OAuth redirect URI** in the Discord developer portal.
- `seed_notification_templates` is now **automatic** — wired into the Render build (§1), so it's no longer a manual step.

---

## 1. Deploy (automatic — Render build)
The web service build now runs: `pip install` → `collectstatic` → `migrate --run-syncdb` → **`seed_notification_templates --quiet`**. So **migrations apply automatically** (including the data migrations that seed the default MembershipPlan and the ~102 directory skills), **and notification copy is refreshed on every deploy** (idempotent, preserves admin-edited rows). Confirm both steps succeeded in the Render build logs.

---

## 2. One-time commands to run after deploy (Render Shell)
`seed_notification_templates` now runs **automatically** in the build (§1), so it's off this list. What remains is manual on purpose:

```bash
# a. Give every ACTIVE member a login account (0.19.14). PREVIEW FIRST — it mints accounts.
python manage.py provision_member_users --dry-run
python manage.py provision_member_users

# b. Only if passwordless login is failing for some members (unverified email rows):
python manage.py fix_unverified_emails --dry-run
python manage.py fix_unverified_emails
```

- `provision_member_users` — **required once** for the member-login/invite features; its own docstring says run manually, not in the build (it mints accounts). Verified, silent (sends no email), idempotent — so it only needs the single go-live run; new members get accounts via normal signup afterward.
- `fix_unverified_emails` — conditional; run if members report login codes not working.

---

## 3. Env vars to verify in the Render dashboard
New or load-bearing for 0.19.x. Set/confirm each:

| Var | Feature | If missing |
|---|---|---|
| `DISCORD_NOTIFY_WEBHOOK_URL` | Discord routing (0.19.18) — the one announcements channel | No Discord posts at all (silent no-op). **Not in `.env.example` — easy to forget.** |
| `DISCORD_BOT_TOKEN` / `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` | Per-member Discord **DM channel + account linking** (v0.19.25, OAuth) | Linking/DMs silently disabled. **Use fresh prod creds (rotate, don't reuse dev), and register the prod OAuth redirect URI in the Discord developer portal** (the `members.pastlives.space` callback). |
| `WEBPUSH_VAPID_PUBLIC_KEY` / `WEBPUSH_VAPID_PRIVATE_KEY` / `WEBPUSH_VAPID_ADMIN_EMAIL` | Browser push channel in the notification grid (0.19.10) | Push silently fails; bell + email still work |
| `MEMBER_BASE_URL` (and `MEMBER_HOST`) | Absolute links in in-app/notification URLs; member surface routing | Links in notifications can dead-end. Email footers are hardcoded to `members.pastlives.space`, so confirm prod's member host **is** that |
| `BOOK_BASE_URL` | Absolute links in class emails (confirmation, reminder, etc.) | Class links break |
| `R2_ACCOUNT_ID` / `R2_BUCKET_NAME` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_PUBLIC_URL` | Media + **new document uploads**: guild meeting-note attachments (0.19.5) and FAQ docs (0.19.24), 25 MB cap | Uploads fail |
| `RESEND_API_KEY` (+ `EMAIL_*`) | All outbound email | The spine sends **much more** email now (auto class reminders, voting emails, announcements) — confirm Resend is set **and has quota headroom** |
| `STRIPE_FIELD_ENCRYPTION_KEY` | Billing creds decryption | Existing; confirm unchanged (losing it bricks stored Stripe keys) |

Have defaults, usually fine as-is: `INVITE_EXPIRY_DAYS` (14), `CLASS_ADMIN_NOTIFY_EMAILS` (optional), `LOGIN_CODE_*` caps, `MAX_UPLOAD_DOCUMENT_BYTES` (25 MB).

---

## 4. Config that lives in the DB / Site Settings (not env)
- **Mailchimp** (newsletter tagging, 0.19.7) — API key + audience ID are in **Site Settings**, not env. Confirm they're set if newsletter sign-up/tagging should work.
- **Per-guild Discord webhooks** (0.19.18) — guild leads self-serve from each guild's settings page. No prod action; just know it's member-configured.
- **Voting settings** (0.19.17) — reminder lead-days, funding-pool floor, email wording live in the Voting → Settings page (DB). Defaults are sane; review once.

---

## 5. Scheduled jobs — verify, nothing new to add
The existing `run-scheduled-tasks` cron (every 15 min, already in `render.yaml`) now also fires: auto class reminders (0.19.8), the per-member voting emails (0.19.17), and month-end funding snapshots. **No new cron service is needed** — just confirm the `run-scheduled-tasks` service is green in Render. (`airtable-pull` nightly is unchanged.)

---

## 6. Post-deploy smoke checks
- [ ] Render deploy succeeded; `migrate` step clean in logs.
- [ ] `seed_notification_templates` run; an admin opens `/manage/notifications/` and a sample email preview looks branded (not "[missing: …]").
- [ ] `provision_member_users` run; spot-check a previously-userless member now has an account, and the Manage Members page shows sign-in status.
- [ ] Post a test site-wide announcement → it lands in the Discord channel + members' bells + email.
- [ ] A test class reminder / voting reminder email arrives branded, with the gold "Past Lives Federation of Guilds" footer + working unsubscribe link.
- [ ] The Discord release-announcement workflow fired on merge with the **consolidated** changelog (see the changelog grouping — pending).

---

## 7. Still open (tracked elsewhere)
- The **changelog consolidation** (9 grouped entries) is staged, waiting on the parallel session to stop bumping `version.py`.
- Decision: wire `seed_notification_templates` into the Render deploy (recommended) vs. keep manual.
