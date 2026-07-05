# Release 0.19.x — Step-by-Step QA Script

Follow this **top to bottom**. It's grouped into 4 parts by who you're logged in as, so you only sign in a
few times. Each **Test** is a numbered list of clicks ending in one **✅ Pass if** line — tick the box when
it matches, jot a note if it doesn't.

> Everything here is your **local** machine on a **copy** of production data. All email goes to **Mailpit**,
> never real inboxes. No Discord posts, no card charges, no member is affected. Click freely.

---

## Part 0 — One-time setup (do this once, ~1 minute)

1. Open two browser tabs:
   - **Tab 1 — the app:** http://pastlives.test:8000/
   - **Tab 2 — the email inbox (Mailpit):** http://localhost:8025
2. Keep Tab 2 open the whole time — every "email" the app sends shows up there instantly.

**⚠️ Always use `pastlives.test:8000`. Never `localhost:8000`** — login breaks on localhost.

### How to log in (you'll repeat this at the start of each Part)
1. In Tab 1, go to http://pastlives.test:8000/
2. Type the email for the Part you're on → click **Send me a code**.
3. A **6-digit code pops up on screen** (yellow toast). *(If you miss it, it's also in Mailpit, Tab 2.)*
4. Type the code → you're in.
5. To switch accounts later: top-right menu → **Log Out**, then repeat.

| Part | Log in as | Email to use |
|---|---|---|
| **A** | Plain member | `bronehammer@gmail.com` |
| **B** | Guild lead (Glass Guild) | `caitvonderwin@gmail.com` |
| **C** | Admin | `testadmin@x.com` |

---

## Part A — As a plain member  (`bronehammer@gmail.com`)

Log in as `bronehammer@gmail.com` first (see above).

### Test A1 — You already have an account (no signup wall)
1. You just logged in with a code.
2. Look at where you landed.
- [ ] **✅ Pass if:** you're on the member hub immediately — you were **never** asked to "create an account" or set a password.

### Test A2 — The notification preferences grid
1. Top-right menu → **Settings** (or go to http://pastlives.test:8000/settings/).
2. Click the **Notifications** (or **Emails**) tab.
3. Look at the grid of update types (class reminders, approvals, waitlist, billing, announcements, voting) across the columns **Bell / Email / Push**.
4. Click an **All on** (or **All off**) button for one section.
5. Click **Save**.
- [ ] **✅ Pass if:** each cell is a **toggle switch** (not a bare checkbox), the All-on/off button flips that whole row of toggles, and Save shows a confirmation.

### Test A3 — Add skills to your profile
1. Still in **Settings**, go to the **Profile** tab.
2. Find the **Skills** section → click **+ Add** (or **Add skill**).
3. Type a skill (e.g. `Woodworking`) and, if offered, a number of years.
4. Add a second skill the same way.
5. Click **Save**.
- [ ] **✅ Pass if:** there's a working **+ Add** button AND a **Save** button, each row has its own remove control, and after saving the skills stick.

### Test A4 — Open for commissions
1. Same Profile tab, find **Open for commissions**.
2. Turn the toggle **on**.
3. A short note field appears — type something like `Available for custom woodwork`.
4. Click **Save**.
- [ ] **✅ Pass if:** it's a **toggle switch**, the note box appears when it's on, and it saves.

### Test A5 — Search the member directory by skill
1. Left sidebar → **Member Directory** (or http://pastlives.test:8000/members/).
2. Use the **search / filter** to search the skill you just added.
3. Then apply the filter for **open for commissions only**.
- [ ] **✅ Pass if:** results narrow to matching members, and each card shows their skills (and guild icons).

### Test A6 — Cast a guild-funding vote
1. Left sidebar → **Guild Voting** (or http://pastlives.test:8000/guilds/voting/).
2. Rank the guilds using the controls shown.
3. Submit your vote.
- [ ] **✅ Pass if:** the vote saves and the page confirms it's recorded (and notes it rolls over each cycle).

### Test A7 — Log-in alert doesn't cry wolf
1. Check Mailpit (Tab 2) — note how many "new sign-in from a new device" emails exist.
2. In Tab 1, top-right → **Log Out**, then log back in as `bronehammer@gmail.com` (same browser).
3. Refresh Mailpit.
- [ ] **✅ Pass if:** you do **not** get a new "new device" security email for this same browser you've already used.

---

## Part B — As a guild lead  (`caitvonderwin@gmail.com` — Glass Guild)

Log out, then log in as `caitvonderwin@gmail.com`.

### Test B1 — Open your guild page
1. Left sidebar → **Guilds** section → click **Glass Guild** (or open it from the directory).
2. Note the **Edit** button near the guild title (top of the page).
- [ ] **✅ Pass if:** the **Edit** button is visible to you on your own guild page.

### Test B2 — Post a guild event (shows on the calendar)
1. On the Glass Guild page, open the **Events** tab/section → click **Add event** (Add).
2. Fill in a title (`QA Test Event`), a date, a time, and a short description.
3. Click **Save**.
4. Now open the sidebar → **Community Calendar**.
- [ ] **✅ Pass if:** the event saved, and it shows on **both** the Glass Guild's own calendar **and** the main Community Calendar.

### Test B3 — Make the event repeat
1. Add another event (`QA Recurring`) the same way.
2. In the recurrence option, choose **monthly** on a weekday-of-month (e.g. **2nd Saturday**).
3. Save, then look at the Community Calendar across this month and next month.
- [ ] **✅ Pass if:** the event appears on the correct weekday-of-month in future months, not just once.

### Test B4 — Build your leadership team
1. Click **Edit** on the Glass Guild page.
2. Find the **Leadership / Staff** section → click its **Add** button.
3. Pick a member and a role (co-lead / secretary / treasurer / orienter) → **Save**.
4. Go back and view the public guild page.
- [ ] **✅ Pass if:** there's an **Add** button and a per-row **remove**, it saves, and the staff member appears on the guild page.

### Test B5 — Post a meeting note with an attachment
1. On the guild page, open the **Meeting Notes** tab → **Add** a note.
2. Give it a title, write-up, and attach a file **or** paste a link.
3. **Save**.
- [ ] **✅ Pass if:** the note has its own **Save**, saves cleanly, and the attachment/link is downloadable afterward.

### Test B6 — Edit the FAQ (watch for the missing-button trap)
1. Click **Edit** → find the **FAQ** section.
2. Click **+ Add question**, type a question and answer.
3. Try attaching a video link or a document to an answer if offered.
4. Click **Save**.
- [ ] **✅ Pass if:** BOTH a **+ Add question** button AND a **Save** button exist and work, and each question row has a delete. *(This exact combo is the #1 thing to verify.)*

### Test B7 — Each section saves on its own
1. On the **Edit** page, change just the **About** text.
2. Save that section only.
- [ ] **✅ Pass if:** the About section saves by itself without forcing you to re-save the whole page.

### Test B8 — Post and then edit an announcement
1. On the guild page, post a guild **announcement** (title + body).
2. After it posts, click **edit** on it and change the text → save.
- [ ] **✅ Pass if:** the announcement posts, pins to the top, and you can **edit it in place** afterward.

---

## Part C — As an admin  (`testadmin@x.com`)

Log out, then log in as `testadmin@x.com`. You now have the full admin sidebar.

### Test C1 — The member roster is tidy
1. Sidebar → **Manage Members**.
2. Scan the list.
- [ ] **✅ Pass if:** each row shows the person's **email** and whether they've **signed in**; class-only people are marked **non-member**.

### Test C2 — Send a login invite
1. In **Manage Members**, open a member who has **never signed in**.
2. Click **Send login invite** (one-tap email invite).
3. Check Mailpit (Tab 2).
- [ ] **✅ Pass if:** a confirmation shows, and the invite email lands in Mailpit with a single set-up link.

### Test C3 — Invite tracking (resend / cancel)
1. Go to **Manage Members → Invite** (http://pastlives.test:8000/manage/members/invite/).
2. Look at the list of who's been invited and whether they've joined.
3. On a pending invite, try **Resend**, then **Cancel**.
- [ ] **✅ Pass if:** you can see invite status and both **Resend** and **Cancel** work.

### Test C4 — Voting tools are in one tabbed place
1. Sidebar → **Voting** (http://pastlives.test:8000/manage/voting/).
2. Click across the tabs: **Overview**, **Funding History**, **Snapshots**.
3. Find the link that opens the **member** voting page from here.
- [ ] **✅ Pass if:** all three tabs load, and you can view the member voting page without leaving the admin area.

### Test C5 — Voting settings
1. Go to **Voting → Settings** (http://pastlives.test:8000/manage/voting/settings/).
2. Change the reminder lead time and the funding-pool floor.
3. Reword one of the voting emails.
4. **Save**.
- [ ] **✅ Pass if:** the settings save, and there's an option to **hold the results email** until an organizer sends it.

### Test C6 — Feature kill-switches
1. Sidebar → **Site Settings** → **Features** tab.
2. Confirm two toggles, both **on**: *Enable My Tab & Payments* and *Allow class registration*.
3. Turn **My Tab & Payments off** → **Save**.
4. Top bar → **View as** → **Member**, and look at the sidebar.
5. Switch **View as** back to Admin, turn the toggle **back on**, Save.
- [ ] **✅ Pass if:** they're **toggle switches**, and turning My Tab off removes the My Tab nav + balance pill from the member view.

### Test C7 — Send an announcement blast (to Mailpit)
1. Sidebar → **Site Settings** → **Announcements** tab.
2. Click **Draft from latest release** (or compose one) → use the formatting toolbar → **Preview**.
3. Make sure **Also post to Discord** is **OFF**.
4. Click **Send**.
5. Check Mailpit (Tab 2).
- [ ] **✅ Pass if:** the preview renders, there's a rich-text toolbar (bold/italic/headings/lists), and the blast lands in Mailpit as a branded email.

### Test C8 — Add a calendar feed
1. **Site Settings** → **Calendar** tab.
2. Click **+ Add calendar**, give it a name and any iCal URL → Save.
3. Also click **Sync Now** and confirm it runs.
- [ ] **✅ Pass if:** the add works, per-row remove works, and **Sync Now** submits on its own (its own button — not stuck/orphaned).

### Test C9 — Guild soft-delete
1. Open any guild page (ideally a throwaway one).
2. Click **Edit** → find **Delete**.
3. Confirm through the modal.
- [ ] **✅ Pass if:** Delete is a **small red danger button with a confirm modal** (not a big raw Delete), and the guild disappears from listings afterward.

---

## Part D — Cross-cutting (do these as you go, in any Part)

### Test D1 — Dark and light both work
1. Top bar → theme toggle (sun/moon). Flip to the **other** theme.
2. Visit: a guild **Edit** page, the notification grid, and any form with a **dropdown**.
- [ ] **✅ Pass if:** no white boxes on dark, all text readable, dropdowns match the theme in **both** modes.

### Test D2 — Mobile reflow
1. Narrow the browser window (or use device emulation / your phone).
2. Open the Community Calendar, a guild page, and the Member Directory.
- [ ] **✅ Pass if:** no sideways scrolling, buttons are tappable, tables/calendars reflow instead of overflowing.

### Test D3 — Emails look right
1. In Mailpit (Tab 2), open 3–4 different emails you generated above.
- [ ] **✅ Pass if:** each has the branded layout, a manage-preferences/unsubscribe footer, and **full clickable links** (not bare `/paths`).

---

## Notes

- **Data is a copy of production** (610 members, 21 guilds) — your edits only touch the local copy.
- **`guilds.pastlives.test` won't load** — the public guest-guilds surface is a **0.20.x** feature, not part of this 0.19.x pass.
- **If a page hangs**, restart the app: in a terminal, `cd /home/josh/Code/plfog && docker compose up -d --force-recreate --no-deps web`, wait ~5s, reload.
