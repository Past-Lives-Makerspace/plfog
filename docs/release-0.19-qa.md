# Release 0.19.x — Local QA Checklist

Everything new since production (prod runs **0.18.11**; this branch ships **0.19.0 → 0.19.21**).
Work top to bottom: do **Part 0** once, then tick off each feature.

Surfaces:
- **FOG hub** (members/admin): `http://pastlives.test:8000`
- **Book / CMS** (public catalog): `http://book.pastlives.test:8000`
- **Mailpit** (all outgoing email): `http://localhost:8025`

> Never use `localhost:8000` for the app itself — it's out of `ALLOWED_HOSTS` and the cookie domain breaks auth. Use the `.test` hostnames.

---

## Part 0 — One-time setup

1. **Hosts file** — make sure these resolve (WSL `/etc/hosts` and/or Windows hosts):
   ```
   127.0.0.1 pastlives.test book.pastlives.test
   ```

2. **Start the stack** (from the repo root so the container serves THIS checkout):
   ```bash
   cd /home/josh/Code/plfog
   docker compose up -d
   docker compose ps          # wait for web + db + mailpit healthy
   ```
   The override mounts `/home/josh/Code/plfog` → `/app`, so the running container is release-0.19.x. (If a feature looks missing, confirm the volume in `docker-compose.override.yml` still points here and not the `plfog-series` worktree.)

3. **Seed demo data** (members, guilds, a "Lamp Working" demo studio, classes, orientations, a MembershipPlan):
   ```bash
   docker compose exec web python manage.py demo_data
   docker compose exec web python manage.py demo_data --status   # verify
   ```

4. **Make yourself admin** (use your real email so you get the toast/mail):
   ```bash
   docker compose exec web python manage.py create_admin_user you@example.com
   ```

5. **Make a guild lead** (most guild features are gated on this):
   ```bash
   docker compose exec web python manage.py set_guild_lead --guild "Ceramics Guild" --member you@example.com
   ```
   (Run `set_guild_lead` with no args, or check the guild list in-app, to see exact guild names.)

6. **Log in** — go to `http://pastlives.test:8000/accounts/login/code/`, enter your email.
   - In DEBUG the 6-digit code shows as an on-screen **toast** (only for emails already in the DB).
   - It's also in **Mailpit**. Enter the code to land in the hub.

7. **(Important) Tame external side effects before testing announcements/events:**
   - **Discord:** `.env` may hold the **real** Past Lives webhook (`DISCORD_NOTIFY_WEBHOOK_URL`). Anything you "announce" or "post" below will hit the real server. Swap in a throwaway webhook (make a private test server) or blank it, then reload:
     ```bash
     docker compose up -d --force-recreate --no-deps web
     ```
   - **Mailchimp:** if real creds are set, newsletter opt-ins tag real contacts — blank the Mailchimp settings in Site Settings for testing, or use a test audience.
   - **Stripe:** paid-class booking needs test keys in Billing settings; use Stripe **test cards** (`4242 4242 4242 4242`, any future expiry/CVC). Free classes need none.

**Role cheat-sheet:** admin = full access (set via `create_admin_user`). Guild lead/staff = guild edit, classes, orientations, events. Plain member = profile, directory, booking. Use the **"Viewing as"** dropdown in the topbar to preview lower roles without logging out.

**Trigger emails on demand** (scheduled jobs don't fire on their own locally):
```bash
docker compose exec web python manage.py send_class_reminders
docker compose exec web python manage.py send_voting_reminders
```

---

## Part 1 — Classes & booking

- [ ] **Catalog counts bookable sessions (0.19.2)** — open Classes & Workshops (`book.pastlives.test:8000` or the hub Classes page). The headline count should reflect **sessions you can book** (a workshop on 6 dates counts as 6), not the number of class types.

- [ ] **"Guilds" not "Categories" (0.19.3)** — in the catalog filters and the class manager → Settings, groupings are labelled **Guild(s)**. The demo example studio reads **"Lamp Working."**

- [ ] **Dark-mode review note box (0.19.1)** — as admin/guild lead, review a submitted class (Classes admin → a pending class). The instructor-note box matches the dark theme (not a white box), with a hint that a note is optional on approve, required on request-changes/decline.

- [ ] **Guild calendar shows classes (0.19.6)** — open a guild's public page → **Guild Calendar** tab. Its published classes appear alongside orientations and guild events; Overview still lists the next few bookable ones.

- [ ] **Book a class → free account (0.19.7)** — in a private/incognito window (logged out), register for a **free** class on `book.pastlives.test:8000`. Leave the pre-ticked **"Create a Past Lives account"** box checked. Expect: registration confirms, and Mailpit shows (a) a "sign in to your new account" code email and (b) the registration confirmation. Re-register the **same email** for another class → it links to the existing account, no duplicate.
  - Paid variant: account is created only **after** Stripe payment succeeds.

- [ ] **Per-question Mailchimp tags (0.19.7)** — in class Settings → Registration Questions, set a custom **Mailchimp tag** on a yes/no or pick-one question. Register with the newsletter box ticked → that answer is sent to Mailchimp as a tag (free-text answers are never sent). *(Only fully observable with Mailchimp creds; otherwise verify the field saves.)*

- [ ] **Auto class reminders, no doubles (0.19.8 / 0.19.9)** — set a class session ~tomorrow and bump **Class Settings → reminder hours** so the session falls in the window. Confirm a registrant, then:
  ```bash
  docker compose exec web python manage.py send_class_reminders
  ```
  Expect **exactly one** reminder email in Mailpit + a bell notification. Run it again → **no second email** (deduped).

---

## Part 2 — Guild pages & staff

Open a guild you lead: public page `/guilds/<id>/`, editor via the **Edit** button → `/guilds/<id>/edit/`.

- [ ] **Staff roles (0.19.0)** — editor → **Staff** tab. Add a member as Co-Guild Lead / Secretary / Treasurer / Orienter. They get the same access as you and are CC'd on class-approval & orientation emails. The public guild page lists the whole leadership team under **Guild Lead** with role labels. (Orienter selection now lives here, not on the orientation settings page.)

- [ ] **Per-section Save + Gallery tab + editable announcements (0.19.4)** — in the editor:
  - **FAQ & Links** tab: each section has its **own Save** button (no scroll-to-bottom).
  - **Images/Gallery** tab: gallery has its own tab; uploads are instant; public page shows a **Gallery** tab.
  - **Announcements** tab: post one, then **Edit** it (fix a typo) without deleting — public page shows the update.

- [ ] **Meeting notes (0.19.5)** — editor → **Meeting Notes** link → add a note (date, write-up, file/link attachments). Members read them newest-first on the guild's **Meeting Notes** tab; attachments are one-tap download/link.

---

## Part 3 — Community calendar & FOG-native events

- [ ] **Guild lead posts a guild event (0.19.19)** — guild editor → **Events** → Add. Set title/time/location, choose **Repeats monthly**. On save a Discord heads-up + in-app notification fire (watch your test webhook). The event shows on the **Community Calendar** *and* the guild's own calendar.

- [ ] **Admin posts a community event / Guild Lead Meeting (0.19.19)** — Community Calendar (`/calendar/`) → **Events** tab (admin only) → Add. Event type can be Community event, Guild Lead Meeting, or a guild event. Confirm it lands on the calendar.

- [ ] **Monthly recurrence (0.19.19)** — make a monthly event on, e.g., the 2nd Saturday. Page forward month-by-month: it recurs on the 2nd Saturday each month (one DB row, expanded virtually).

- [ ] **Export / subscribe (0.19.19)** — on `/calendar/`, use Export/Subscribe (`.ics`). The downloaded calendar includes community + guild events, with an RRULE for monthly ones.

- [ ] **Edit doesn't re-announce** — edit an existing event's title → it updates on calendars but does **not** re-post to Discord.

- [ ] **Bigger calendar text (0.19.21)** — on the Community Calendar and a guild's calendar, event names, times, and day numbers are noticeably larger / less cramped than before.

---

## Part 4 — Notifications spine

- [ ] **Unified notification settings grid (0.19.10)** — as a member, **Settings → Notifications**. One grid: per notification type pick **in-app bell / email / push**. The old separate email-preferences page is gone. New opt-ins present: guild announcements, makerspace announcements, voting-closing-soon, voting results.
  - Admin view of the catalogue: `/manage/notifications/` (every event, its channels, and editable copy with live preview).

- [ ] **No double-sends / prefs respected (0.19.10)** — trigger any emailing event (e.g. the class reminder from Part 1); turn email **off** for that type in the grid and re-trigger → no email, but the bell still updates.

- [ ] **Branded emails (0.19.11)** — open any 0.19 notification email in Mailpit: dark card, Past Lives logo, a real button, footer tagline — not plain text. *(If copy/previews look empty, seed templates: `docker compose exec web python manage.py seed_notification_templates`.)*

- [ ] **Site-wide announcement actually fans out (0.19.15)** — Django admin → site announcement page (`/admin/announcement/`), post one. Expect: an email to every active member (Mailpit), a bell notification, a Discord post (test webhook), and a Site Activity log row. Post a **second** one → it also goes out (the old "only the first ever sent" bug is fixed).

---

## Part 5 — Member admin & invites

Admin → **Manage Members** (`/manage/members/`).

- [ ] **Email integrity (0.19.12)** — each member shows their real email; use the **Email: missing** filter to find accounts genuinely lacking one (flagged with a warning).

- [ ] **Tabbed member admin + non-members (0.19.14)** — open a member → **Details** and **Emails** tabs. The Emails tab manages addresses (add / remove / set primary / mark verified). Class-only people are marked **non-member**; each row shows whether they've signed in.

- [ ] **One-tap login invite (0.19.14)** — on a member who's never signed in, click **Send sign-in code** → Mailpit shows the invite.

- [ ] **Invite workflow (0.19.13)** — from Manage Members, see who's been invited and whether they've joined; **Resend** a pending invite and **Revoke** one. Confirm the toast + the list updates.

---

## Part 6 — Voting

Admin → **Voting** (`/manage/voting/`).

- [ ] **Tabbed admin + snapshots (0.19.16)** — tabs: **Overview**, **Funding History**, **Snapshots**. Preview the month's tally and filter the breakdown by member type / role before locking. Open a past result to review it in detail.

- [ ] **Voting Settings (0.19.17)** — **Voting → Settings**: set reminder lead-days and the funding-pool floor; edit the wording of each voting email. Toggles for closing-soon / vote-soon reminders.

- [ ] **Personalized member emails (0.19.17)** — set lead-days so a reminder is due (e.g. `reminder_lead_days` small) and run:
  ```bash
  docker compose exec web python manage.py send_voting_reminders
  ```
  Mailpit shows a "polls closing soon" email (showing what each member currently votes for) to voters, and a "you haven't voted" nudge to signed-in non-voters.

- [ ] **Results wait for a human (0.19.17)** — take a snapshot (Snapshots tab). Results are **not** auto-emailed; an organizer presses **Send results** to fan out the personalized funding-breakdown email per voter. Verify in Mailpit.

- [ ] **Snapshot field dark-mode fix (0.19.21)** — on the voting Snapshots page, the **"Minimum pool $"** box matches the app theme (not a plain white field).

---

## Part 7 — Discord routing

- [ ] **One channel for everything (0.19.18)** — with the main webhook set to your test channel, confirm releases, announcements, voting reminders, and newly published classes all land in that **one** channel.

- [ ] **Per-guild webhook (0.19.18)** — guild editor → Discord section: paste a **second** test webhook and tick **"Also post to our Discord."** Post a guild announcement → it appears in **both** the main channel and the guild's channel. With the toggle off, only the main channel gets it.

---

## Part 8 — Skills directory & open for commissions (0.19.20)

- [ ] **Add skills (0.19.20)** — as a member, **Settings → Profile → My skills**. Add a skill from the picker (102 seeded across 13 categories), optionally with **years**. Chip shows e.g. "Welding (MIG/TIG) 5y." Duplicate → "already listed"; 16th → "up to 15 skills."

- [ ] **Suggest a skill (0.19.20)** — use "Suggest a skill" with a novel name → it appears on your profile with a **pending** badge. Suggesting an existing name (any case) is rejected with a pointer to the list.

- [ ] **Admin review (0.19.20)** — Django admin → `/admin/membership/skill/`, filter **Status = pending**, select the suggestion → **Approve selected skills**. It then appears in everyone's picker and becomes searchable in the directory.

- [ ] **Open for commissions (0.19.20)** — Settings → Profile → toggle **Open for commissions!**, add a short note, Save. A badge + note appear on your directory card.

- [ ] **Directory search & filters (0.19.20)** — Member Directory (`/members/`): filter by **skill**, tick **Open for commissions**, and combine both. Search by name or skill name. Confirm only matching members show, and that a member who hid skills doesn't expose them.

---

## Smoke pass before go-live

- [ ] Logged-out booking of a free class works end-to-end on `book.pastlives.test:8000`.
- [ ] A paid class checkout reaches Stripe (test card) and confirms.
- [ ] No email in Mailpit is plain/unstyled or has a broken/`/`-only link.
- [ ] No Discord post went to the **real** Past Lives server during testing.
- [ ] `docker compose exec web python manage.py check` is clean and migrations are applied.
