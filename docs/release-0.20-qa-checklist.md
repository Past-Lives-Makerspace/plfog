# QA Checklist — Release 0.20.x (PR #118)

Step-by-step test script for everything new since **0.19 shipped**. This covers **release-0.20.x only** — the
0.21 Propose-an-Event / Google-Calendar sync work is a **separate branch (PR #119)** and is **not** tested here.

Work top-to-bottom. Each test is **numbered clicks → one "✅ Pass if" line → a checkbox.** You sign in as three
different people in turn (member → guild lead → admin), so the doc is grouped by who you're logged in as.

---

## Part 0 — Setup (do this once)

1. Point a browser at a **release-0.20.x build**:
   - **Local:** run the app on the `release-0.20.x` branch and open **http://pastlives.test:8000/** (the FOG hub).
     Never use `localhost` — it's removed from `ALLOWED_HOSTS` and breaks auth.
   - **or QA/staging:** **https://pastlives.plaza.codes/** once release-0.20.x is deployed there. (Hetzner is *not*
     production — it's safe to poke.)
2. Open a **second tab** on the mail catcher so you can watch emails without leaving the app:
   - **Local:** **http://localhost:8025** (Mailpit).
   - **QA:** emails are real-ish — flag anything you can't verify rather than sending to real members.
3. **Login is passwordless.** Type a known member's email, submit, and on a dev/QA build the **6-digit code pops
   up on-screen as a toast** (it also lands in Mailpit). Paste it back. If no toast appears, the email isn't a
   known account — pick another.
4. **Accounts you'll need** (local build runs against a copy of prod data, so these exist locally):
   - a plain **member** (example: `bronehammer@gmail.com`),
   - a **guild lead** — someone who leads at least one guild (example: `caitvonderwin@gmail.com`); an **admin using
     the "Viewing as" dropdown** to impersonate a lead also works,
   - an **admin** (example: `testadmin@x.com`).
5. **What is safe:** no real email / Discord / Stripe goes out on a dev build (mail → Mailpit). On QA, Discord only
   fires if a webhook is actually configured — skip and note those checks if it isn't.
6. **Guest-surface note:** the public **guilds** directory (Part D) needs the guest host wired. Locally that's
   **http://guilds.pastlives.test:8000/** with the host added to your build's guilds-host list + `/etc/hosts`. If it
   doesn't resolve, that's an **infra-not-wired** flag, not a bug — note it and move on.

---

## Part A — Signed in as a plain MEMBER

*Log in as the member account.*

### A1 — Home page on sign-in

1. Sign in.
2. Look at where you land.

✅ **Pass if** you land on a real **home page** — an "upcoming for you" area (your orientations, your guilds'
meetings/events, classes at the space), a "latest from your guilds" block, and quick links — **not** the guild
directory or a bare class list.

- [ ] A1 passes

### A2 — First-login welcome + profile nudge

1. *(If this is a brand-new account's first-ever sign-in)* note the **welcome** that appears pointing you to set up
   your profile.
2. On the home page, look for a **"finish setting up your profile"** nudge (shows when your profile is incomplete).
3. Complete the profile, return home.

✅ **Pass if** the first-login welcome points at profile setup, the nudge is present while incomplete, and it
**disappears** once the profile is complete.

- [ ] A2 passes

### A3 — Space & Org Info page

1. In the sidebar, click **Space & Org Info** (`/info/`).

✅ **Pass if** the page opens with a **map / floorplan** area, **parking**, a **who-to-contact** section, and the
**Member Guide + Code of Conduct inline** (not links out to Google Docs). *(Floorplan image and who's-who text are
content that gets entered post-deploy — if a block is empty, note "awaiting content," not a bug.)*

- [ ] A3 passes

### A4 — Profile: safe edit + oversized photo

1. Go to **edit profile**. Change pronouns / Discord handle / about-you and flip a visibility toggle.
2. In the **same save**, upload a deliberately **too-large photo**.
3. Save.

✅ **Pass if** the oversized photo is **rejected but your other edits are kept** (pronouns/Discord/about-you persist
— they are NOT wiped by the photo error).

- [ ] A4 passes

### A5 — Listed by default + hide controls

1. Open the **member directory**; find your card.
2. In **Settings**, toggle **hide me** (or hide a single field); reload the directory.

✅ **Pass if** you appear in the directory **by default**, and each hide toggle visibly takes effect on your card.

- [ ] A5 passes

### A6 — Connect Discord autofill

1. In Settings, use **Connect Discord account**.

✅ **Pass if** your **Discord handle is filled in automatically** after connecting. *(Needs Discord OAuth configured
on the build — skip + note if not wired.)*

- [ ] A6 passes

### A7 — Suggest an announcement (member-submitted announcements) 🆕

*This is the headline new feature (v0.20.2). You must be a **member of the guild** but **not** its lead.*

1. Open a guild page for a guild you're **in** (not one you lead).
2. Click **"+ Suggest an announcement"** (in the announcements area / Get Involved).
3. Fill in title + body (+ optional expiry), submit.
4. Look at the guild page's public announcements, and at **your proposals** list.

✅ **Pass if** submitting shows it as **Pending / awaiting review**, it does **NOT** appear on the public guild page
or your home feed yet, and it shows in **your proposals** with a pending status.

- [ ] A7 passes

### A8 — Withdraw / edit-and-resubmit

1. In **your proposals**, **withdraw** the pending proposal → confirm it disappears.
2. Submit another; later (after Part B requests changes on it) come back and **edit & resubmit**.

✅ **Pass if** withdraw removes it cleanly, and a **changes-requested** proposal can be **edited and resubmitted**
(goes back to pending).

- [ ] A8 passes

---

## Part B — Signed in as a GUILD LEAD (a guild you lead)

*Log out, log in as the guild-lead account — or, as an admin, use the **"Viewing as"** dropdown to act as the lead.*

### B1 — Guild page: "View public page" button 🆕

1. Open a guild page **you can edit** whose public page is **on** (`is_public`).
2. Find the **"View public page"** button next to **Edit**.
3. Click it.

✅ **Pass if** the button is present (only when the guild is public) and opens **that guild's guest/public page in a
new tab**. On a **private** guild, the button is **absent**.

- [ ] B1 passes

### B2 — Editor tabs (Orientations / Meeting Notes / Events)

1. Open the guild **edit** page.
2. Switch between **Orientations**, **Meeting Notes**, and **Events**.

✅ **Pass if** those are **tabs inside the editor** (not separate pages) and switching keeps you on the same page.

- [ ] B2 passes

### B3 — Post an announcement directly (email + Discord options)

1. On the edit page's **Announcements/Emails** tab, use **Post an Announcement**.
2. Note the two separate options: **"Also send email"** and **"Also post to your guild's Discord channel"**.
3. Post once with **email ON**. Check Mailpit.
4. Post once with **Discord ON** *(webhook configured)*; and once with Discord **OFF**.

✅ **Pass if** a lead's own post goes **live immediately (no review)**; with email ON, **joined members** get the
email and a **non-member does not**; with Discord ON it hits the guild's channel, with Discord OFF it doesn't.

> **Note (scope):** this is a plain **on/off** Discord toggle. Choosing between **#general-chat / #leadership /
> guild channel** (the "channel picker") is a **separate, un-built spec** — do **not** expect a channel dropdown here.

- [ ] B3 passes

### B4 — Review queue: approve a member's proposal 🆕

1. On the edit page, find the **"Review proposals"** card / link (shows a **count** when submissions are waiting).
2. Open the **review queue**; find the member's proposal from A7.
3. Click **Approve**. In the approve modal, note the **"Also send email"** and **"Also post to Discord"** checkboxes
   (both default **on**); optionally edit the wording first.
4. Approve. Then check: the public guild page, Mailpit, and (if wired) Discord.

✅ **Pass if** approving posts it **live via the normal fan-out** — it now shows on the guild page, joined members
get the email (when the box is checked), Discord fires (when checked) — and the **proposer gets a notification** that
it's live.

- [ ] B4 passes

### B5 — Review queue: request changes / decline (note required) 🆕

1. Back in the queue, on another proposal click **Request changes** — try to submit with a **blank note**.
2. Add a note, submit.
3. On a third proposal click **Decline** — again a note is **required**.

✅ **Pass if** request-changes and decline **both require a note** (blank is rejected with a friendly message), the
proposer is **notified either way**, and a declined proposal **never posts** to the guild page.

- [ ] B5 passes

### B6 — Empty queue state

1. Clear the queue (approve/decline everything) and reopen the review card.

✅ **Pass if** the empty queue shows a sensible **"nothing waiting"** message, not a blank or broken card.

- [ ] B6 passes

### B7 — Staff badges (dedup) + FAQ label & rich answers

1. On the guild page, find someone who holds **two titles** (e.g. Orienter **and** a technician role).
2. On the edit page, **rename the FAQ section** (e.g. "Ceramics Info") and give an FAQ answer a **clickable link +
   simple formatting**. Save; view the public guild page.

✅ **Pass if** a two-title person shows **once with both badges** (not listed twice), the FAQ section rename
**persists**, and the FAQ answer renders the **link/formatting** (not raw text). Every editor form has a **visible,
working Save** that survives reload.

- [ ] B7 passes

---

## Part C — Signed in as an ADMIN

*Log out, log in as the admin account.*

### C1 — Purpose headers

1. Visit pages that gained a **purpose header** — e.g. **Orientations** and the **Member Directory**.

✅ **Pass if** each shows a short **purpose blurb** at the top explaining what the page is for.

- [ ] C1 passes

### C2 — Calendar ↔ catalog alignment

1. Open the **Community Calendar** and the **Class Catalog** side by side.

✅ **Pass if** the calendar **no longer lists a class that has already started** — it matches the catalog (no
unbookable classes).

- [ ] C2 passes

### C3 — Admin can act on any guild's proposal

1. As an admin who is **not** a given guild's lead, open that guild's edit page → review queue.

✅ **Pass if** you can still **approve/decline** its proposals (the edit gate admits admins), even though the queue
is primarily the guild's own leadership worklist.

- [ ] C3 passes

---

## Part D — Public / guest surfaces (logged OUT) — needs guest host wired

*Log out entirely. These need the guilds guest host (Part 0 step 6). If it doesn't resolve, mark the whole section
**infra-not-wired** and skip.*

### D1 — Guest guild directory

1. Open the guest guilds directory (**guilds.pastlives.test:8000** locally / the wired domain on QA).

✅ **Pass if** you see a **public directory of guilds** and can open an individual **guest guild page** — with no
member chrome — for a guild that is **public**.

- [ ] D1 passes

### D2 — Private guild is hidden

1. Confirm a guild whose **"make page private"** toggle is **on** is **not** in the directory and its guest page is
   not reachable, while members still see it inside the hub.

✅ **Pass if** private guilds are absent from the public directory/guest page but remain visible to members on the hub.

- [ ] D2 passes

### D3 — Vanity URL + QR flyer

1. Hit a guild **vanity URL** (`/g/<slug>`) → confirm it **redirects** to the guest guild page.
2. From the guild's flyer/QR feature, generate the **printable flyer** and scan its **QR code**.

✅ **Pass if** the vanity URL 301-redirects to the right guest page, and the flyer renders with a **scannable QR** that
lands on that page. *(`essential_rules` flyer text is post-deploy content — empty = "awaiting content," not a bug.)*

- [ ] D3 passes

---

## Part E — Cross-cutting (do across the whole pass)

### E1 — Theme persistence

1. Set **dark** (or light). Navigate across several hub pages, then to a **book/classes** page and back.

✅ **Pass if** the theme **sticks** and never flips back mid-navigation, across both the hub and book surfaces.

- [ ] E1 passes

### E2 — Light + dark + mobile

1. Re-open a few of the new screens (home, Space & Org Info, guild edit review queue, propose modal) in **light**,
   **dark**, and at **mobile width**.

✅ **Pass if** every new screen reads correctly in **both themes** and **reflows on mobile** — no horizontal scroll,
tap targets usable, no clashing margins.

- [ ] E2 passes

---

## NOT in this build — do not test (spec-only)

These were **written as specs but never built**; skip any checklist item that mentions them:

- **Discord announcement *channel picker*** (#general-chat / #leadership / guild channel). Only the plain on/off
  Discord toggle exists (B3/B4). Spec: `docs/superpowers/plans/2026-07-03-guild-announcement-discord-channel-picker.md`.
- **Official guild membership — "My Guilds" Settings tab** (self-serve join/leave toggles + membership-drive email).
  Not built. Members still get a guild's emails only if an admin/lead added them to the guild. Spec:
  `docs/superpowers/plans/2026-07-03-official-guild-membership.md`.
- **0.21 Propose-an-Event / Google-Calendar sync** — separate branch (PR #119), tested on its own.

---

## Sign-off

- [ ] All checks pass on **light** and **dark**.
- [ ] All checks pass at **mobile** width (no horizontal scroll, tap targets usable).
- [ ] Any skipped items (Discord webhook, Google Calendar, guest-surface host, post-deploy content) are noted with
      the reason.
