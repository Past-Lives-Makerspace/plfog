# Voting results go out as an announcement Felix drafts in the composer

Round: 2026-10-02 voting results round, PR 1 of 1.

**User story.** As an admin, when a month's funding snapshot is taken, I would like the Voting page to open a draft announcement of the results in the Announcement Composer, so I can check the numbers, write or adjust the message, and send it to every active member and to Discord with the tool I already use. Currently the Voting page shows a "review & send" banner whose Send results button opens a confirm modal and emails a fixed results email I cannot edit.

**Summary.** The Voting banner's Send results button and modal are replaced by a Draft announcement button that opens a pre-filled results announcement in the composer; sending it reaches all active members and Discord, and marks the snapshot's results as sent.

## Evidence that shaped the design (prod, read only, 2026-10-02)

- Snapshot 10 "September 2026" (taken today) and snapshot 8 "August 2026" are unsent. 298 active members, 295 with an account.
- No site-wide composer announcement has ever been sent in production. The composer sends inline in the web request (`hub_compose_send` calls `AnnouncementDraft.send()`).
- PR #336: an inline fan-out to 279 members took about 62 seconds; gunicorn's 30 second default killed the worker after 201 emails. The composer's site send is the same shape, so sending the results through it inline would fail the same way, and because its `period` carries a timestamp a second click would re-email everyone already reached.
- `SiteConfiguration.discord_general_webhook_url` is set in production, so the composer's `#general-chat` choice posts.

## Design decisions (locked)

1. **The button.** On the Voting Overview banner and on a snapshot's history detail header, Send results / Resend and both confirm modals are replaced by one primary button, **Draft announcement**. It is a POST form (CSRF) to a new `fog_admin_required` + `require_POST` view, `hub_admin_voting_results_draft` at `manage/voting/history/<pk>/draft-announcement/`, which calls a model method and redirects to `hub_compose_resume` for the draft. A GET must never create a row.
2. **One draft per snapshot per author.** The model method (on `FundingSnapshot`, e.g. `draft_results_announcement(author) -> AnnouncementDraft`) returns this author's existing unsent, not queued results draft for the snapshot, or creates one. Clicking twice reopens the same draft.
3. **The link.** `AnnouncementDraft.funding_snapshot = ForeignKey(FundingSnapshot, null=True, blank=True, on_delete=SET_NULL, related_name="announcement_drafts", help_text=...)`. Deleting a snapshot leaves its draft as a plain site announcement.
4. **The pre-filled draft.** `audience=SITE`, `send_email=True`, `push_enabled=True`, `discord_enabled=True`, `discord_channel=GENERAL`, `mention=EVERYONE` (the old results post pinged @everyone), `show_sender=True`, `mark_as_urgent=False`, and:
   - `body` (sanitizer-safe HTML the Quill editor loads; only tags in `core/html_sanitize._ALLOWED_TAGS`):
     ```html
     <p>The votes for {cycle_label} are in. {votes_cast} members voted, and here is how the ${pool} funding pool was split between the guilds:</p>
     <ol><li>{guild_name}: ${funding} ({share_pct}%)</li> ... one per guild, in results order ...</ol>
     <p>See the full breakdown on the <a href="{voting_url}">voting results page</a>.</p>
     <p>Thank you to everyone who voted.</p>
     ```
     Money with thousands separators and two decimals (`$1,000.00`). `votes_cast` is `results["votes_cast"]` (fail loudly). `voting_url` is `_absolute_url("/guilds/voting/history/")`, the link the old email used. Guild names HTML-escaped. No dashes anywhere in this copy.
   - `push_message`: `"{cycle_label} voting results are in. See how the guild funding was split."`
5. **The title.** The composer has no typed subject; the title is the auto category. A draft linked to a snapshot takes `"{cycle_label} Voting Results"` (with the usual `"Urgent: "` lead when urgent) in place of the audience category, on every channel (email subject and heading, push, bell, Discord embed). This must be true in every surface that shows it: the composer page's push preview title (`_render_compose`'s `announcement_category`), the email preview (`hub_compose_preview`), the test email (`hub_compose_test`), the push test, and the real send. The preview endpoints learn the snapshot from the posted `draft_pk`, resolved only to the requesting author's own draft (never a posted snapshot id, which would let a crafted POST stamp any snapshot).
6. **Sending marks the results sent.** When a draft linked to a snapshot finishes sending, the snapshot gets `results_sent_at = now` and `results_send_count += 1`, so the banner moves on and the history page reads "Results sent {date}". A results draft whose snapshot is already marked sent is refused at queue and at send ("These results were already sent."), so a second admin's draft cannot send them twice.
7. **Site-wide composer sends go out in the background.** Guild and class sends stay inline, unchanged.
   - `AnnouncementDraft.send_requested_at = DateTimeField(null=True, blank=True, help_text=...)`.
   - `AnnouncementDraft.queue_send()` runs the same guards as `send()` (already sent, empty body, already-sent results) and stamps `send_requested_at`; calling it on a queued draft is a no-op.
   - `hub_compose_send`, for a SITE audience, saves the draft, queues it and redirects with the toast "Your announcement is sending in the background. It reaches {N} recipient(s) within 15 minutes." The confirm modal's line for a site audience says "within 15 minutes" instead of "right now" (Alpine on `audience`).
   - New command `send_queued_announcements`, registered in `core/scheduled_jobs.py` (`Cadence.ALWAYS`, every 15 min, `toggleable=False`, with the same comment as the job it replaces), sends each queued unsent draft oldest first via `send()`, which stamps `sent_at` and clears `send_requested_at`. A draft whose send raises stays queued, the loop moves on, and the command raises `CommandError` at the end naming the failed drafts, so the run record is red.
   - The site send's `period` becomes stable, `f"announce:{self.pk}"`, so a run killed partway and retried skips every member and the Discord post already delivered. Prove both with a spec (retry after a partial run emails nobody twice and posts to Discord once).
   - A queued draft cannot be resumed or re-sent from the composer (the resume and send lookups exclude `send_requested_at__isnull=False`; a resume URL 404s).
8. **Banner states.** For the most recent pending snapshot (`FundingSnapshot.most_recent_pending`): title "Results are in for {cycle_label}." with the pool, **Review numbers** and **Draft announcement**. When a results draft for it is queued, the button is replaced by the status line "The results announcement is sending. It goes out within 15 minutes." The history detail header shows the same control, or "Results sent {date}" once sent. No em dash in either.
9. **The auto snapshot stops sending.** `take_cycle_snapshot` takes the snapshot and stops; `FundingSnapshot.take` still pings admins (`voting.results_ready`). Production has `auto_snapshot_enabled = True`, so without this the November 1 tick would email the old results and Felix's announcement would be a second one.
10. **Retire the old send path's UI and queue.** Delete `voting_send_results` and its URL, `templates/hub/admin/_results_send_control.html`, `FundingSnapshot.queue_results_send`, the `send_pending_funding_results` command, its `ScheduledJob` entry and their specs; update the `core/spec/scheduled_jobs_spec.py` parity tuples. Before deleting the job key, confirm nothing in the DB or the Scheduled Jobs admin page breaks on a key that is no longer registered (`ScheduledTaskRun` history rows for it stay). **Keep** `FundingSnapshot.send_results`, the `send_funding_results` command and the `voting.results_published` / `voting.results_discord` events as the headless fallback (update their docstrings and the email gallery description, `tests/e2e/email_gallery/registry.py`, to say the composer is now the normal path). **Keep** every `results_send_*` column; dropping columns in the release whose old code still reads them breaks the deploy (STANDARDS.md section 10). `results_send_queued` / `results_pending` may stay as they are.
11. **Help.** Update the admin Voting guide in `membership/help_content.py` (the "review & send" banner paragraph and the "Send Results Emails" section, `{#voting-send-results}`; keep the anchor) to describe Draft announcement, the composer, the 15 minute background send and Discord. Keep the anchor id so existing links work.

## Acceptance criteria

- [ ] Overview banner and history detail render **Draft announcement** as a POST form for a pending snapshot; no `open-confirm` Send/Resend control and no `hub_admin_voting_send_results` URL remain (assert on markup: the form action URL, a `data-` attribute, not copy).
- [ ] POST creates one draft linked to the snapshot with the body, push line, channel, ping and toggles above, and redirects to its resume URL; a second POST returns the same draft; a GET is 405; a non-admin is refused.
- [ ] The body survives `sanitize_rich_html` unchanged in content (guild names, amounts, link); a guild name with `<`/`&` is escaped.
- [ ] Composer page for the draft: push preview title, email preview subject and heading, test email subject, push test title all read "{cycle_label} Voting Results"; a plain site draft still reads "Makerspace Announcement".
- [ ] A crafted `draft_pk` of another author's draft gives that author's snapshot title to nobody (preview falls back to the plain category or 404s).
- [ ] Site send: POST queues (no email, no Discord on the request); `send_queued_announcements` sends it, stamps `sent_at`, clears `send_requested_at`, and stamps the linked snapshot's `results_sent_at` and count; a retry after a partial run reaches only the missed members and posts Discord once; a raising draft stays queued and the command exits with `CommandError`; guild and class sends are still inline.
- [ ] Already-sent results refuse a second results draft at queue and send.
- [ ] Banner shows the sending line while queued and moves on once sent.
- [ ] `take_cycle_snapshot` no longer queues or sends anything (spec updated).
- [ ] Scheduled jobs parity spec passes with the new job and without the old one.
- [ ] Migration is additive only (two nullable fields); `manage.py check` and `makemigrations --check` clean.
- [ ] e2e (`tests/e2e/`, new spec): an admin on the Voting Overview clicks Draft announcement, the composer opens with a guild name and amount visible in the Quill editor, Preview & send shows the email preview titled "{cycle_label} Voting Results". Grep `tests/e2e/` for the old banner copy and buttons and fix any spec that used them.
- [ ] Screenshots under `mockups/screenshots/`: the Overview banner, the composer with the pre-filled draft, the email preview.

## Out of scope (named edges)

- **Each member's own ballot recap** ("You voted: 1st X, 2nd Y") and the **bar chart** from the old email. The announcement is one message to everyone; the composer has no per-member fields or images. The headless `send_funding_results` command still sends the old personalised email.
- **Preference key.** The announcement follows members' "Makerspace-wide announcement" notification settings, not their "Guild funding results" ones.
- A resend control, a Save draft button (the composer's drafts UI is dormant on purpose, commit 32780e05), locking the audience, background sending for guild and class audiences, dropping the retired `results_send_*` columns, and the unsent August 2026 snapshot that will surface on the banner once September is sent.

## Files

`membership/models.py` (`AnnouncementDraft`, `FundingSnapshot`), `membership/migrations/`, a new `core/management/commands/send_queued_announcements.py` (or under `membership/`, next to its model), `core/scheduled_jobs.py`, `core/management/commands/take_cycle_snapshot.py`, `hub/views.py` (compose send, preview, test, push test, `_render_compose`, the new voting view; delete `voting_send_results`), `hub/urls.py`, `templates/hub/admin/_results_review_banner.html`, `templates/hub/admin/voting_history_detail.html`, a new `templates/hub/admin/_results_announcement_control.html`, `templates/hub/announcement_compose.html` (modal line), `membership/help_content.py`, `tests/e2e/email_gallery/registry.py`, specs.
