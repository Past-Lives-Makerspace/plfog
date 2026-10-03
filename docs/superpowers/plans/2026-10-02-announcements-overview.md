# Announcements overview: drafts and sent, in one place

Round: 2026-10-02 announcements round, PR 1 of 1. Builds on #588 (the voting results draft announcement).

**User story.** As an admin, I would like Admin Tools → Announcements to open a page that lists every announcement still in draft and every one already sent, with who, when, where it went and how many it reached, so I can pick a draft back up, delete one I no longer want, and look back at what went out. Today every way in lands on a blank Announcement Composer, a saved draft can't be reached (the drafts list was taken off the composer in July and the Save draft button with it), and this month's results draft exists only if someone presses Draft announcement on the Voting page, and then only for that admin.

**Summary.** A new Announcements page with Drafts and Sent tabs, shared by every admin, scoped by audience for leads, staff and instructors. Save draft comes back to the composer. A read-only view shows what a sent announcement looked like on each channel and how many it reached. Each month's "<cycle> Voting Results" draft is made on its own by the 15 minute snapshot job, once per snapshot.

Felix, verbatim: "I think the Announcements composer needs a CRUD update where I can view past announcements I sent, announcements in draft, stuff like that. Because I just went to look at that draft and it just directs me to create an announcement which isn't what I want. We need a whole overview page where we can a list of drafts/sent with table columns like sent and stuff."

## Evidence that shaped the design (read 2026-10-02)

- `AnnouncementDraft` (`membership/models.py` ~4990) is one row per announcement, kept after sending (`sent_at`). Every composer lookup goes through `AnnouncementDraftManager.for_user`, which filters `author=user`, unsent, not queued. So drafts are private to their author today.
- `AnnouncementDraft.save_from_form` sets `draft.author = <the saving user>` on every save, and `hub_compose_send` calls it before `send()` or `queue_send()`. So the author at send time is always the person who pressed Send. `_sender_line()` (the email "From" line) and `send()`'s `actor=` both read `self.author`. With shared drafts, that already makes the sender the "From" and the actor, with no new field.
- `author` is `on_delete=CASCADE`, NOT NULL. An automatically made draft has nobody to name, and no user id may go in code.
- The composer (`templates/hub/announcement_compose.html`) has two phases, "1. Compose" and "2. Preview & send". It has no Save draft button and no way back anywhere. `_compose_save_result.html` still OOB swaps `_compose_drafts_list.html` into `#compose-drafts`, which no page renders. A successful send redirects to a blank `hub_compose`.
- Entry points to the composer: the Admin Tools card (`templates/hub/admin_tools.html`), the Site Settings Announcements tab ("Compose an announcement", `templates/hub/admin/site_settings.html` ~866), the guild edit Announcements tab ("Compose announcement", `templates/hub/guild_edit.html` ~1094, pre-scoped, unlocked), the guild page "Send Announcement" (`templates/hub/guild_detail.html` ~158, locked), the class screens' "Send Email" (`templates/classes/_components/class_screen_base.html`, `templates/classes/teach/class_registrations.html`, locked), the teach Registrations hand-off (`classes/views.py` ~2678), and the Voting banner's Draft announcement. **There is no sidebar Announcements entry.** The sidebar's Admin Tools link leads to the card. Two of these cards already promise "save a draft to finish later", which is false today.
- Delivery ledger (`core.models.EventDelivery`, unique on `event_key, target_ref, channel, period`, indexed on `event_key, period`). A per-recipient row is deleted again when its send did not land (`_release_delivery`), so a remaining row is a real delivery. A Discord broadcast row is kept even when the webhook post failed. Periods per audience: site `announce:{pk}` or `announce:results:{snapshot_id}` (event `site_announcement`), guild `announcement:{GuildAnnouncement.pk}` (event `guild_announcement`), class `announce:{pk}:{timestamp}` (event `class_announcement`). The draft keeps neither the guild post's pk nor the class timestamp, so **reach for guild and class sends cannot be read back today**. A stored period fixes it with one indexed query.
- `take_cycle_snapshot` runs every 15 minutes but returns early when `auto_snapshot_enabled` is off and on every tick after the month's first (the once-per-cycle ledger slot). Work added after those returns would almost never run.
- Prod: 4 `AnnouncementDraft` rows, all sent guild announcements, no results drafts. Snapshot 10 "September 2026" and snapshot 8 "August 2026" are unsent.
- Reusable pieces: `components/page_header.html` (title, lead, one action button), the Voting tabs (`hub/admin/_voting_tabs.html`: link anchors with `.vote-tab`, `.vote-tab--active`, `aria-current`), the Funding History list (`hub/admin/voting_history.html`: one card, a table with View and Delete per row, per-row `confirm_modal` includes rendered after the table), `.pl-members-table` (`hub.css` ~1611: stacks into labelled cards at 768px and below via `td[data-label]`), `.hub-pill--ok|warn|neutral|danger`, `.hub-badge`, `classes/table.py` `prepare_table` + `components/table_pagination.html`, and the composer's own preview partials.

## Design decisions (locked)

### Naming, URLs, permissions

1. **Names.** The page is **Announcements**. Its tabs are **Drafts** and **Sent**. The editor is still the **Announcement Composer**. No other new terms. Copy in this spec is final; none of it may gain an em dash, en dash or hyphen used as a dash.
2. **URLs** (in `hub/urls.py`, next to the composer routes; existing composer URLs are unchanged):
   - `announcements/` → `announcements_overview`, name `hub_announcements`. Tab by query string: `?tab=drafts` (the default) or `?tab=sent`. Any other value is treated as drafts. Server rendered, so links, Back and pagination work.
   - `announcements/sent/<int:pk>/` → `announcement_sent`, name `hub_announcement_sent`: the read-only view of a sent or sending announcement. Not `announcements/<pk>/`, because `announcements/<pk>/withdraw/` already means a member's proposal and the two pk spaces would read as one.
   - Both views live in `hub/views.py` beside the composer views, because they share its gates.
3. **One rule decides who may see and act on a row: the audience.** A request may see, open, save over, send or delete an announcement exactly when the composer would let it address that announcement's audience today: `_compose_audience_forbidden(request, draft.audience_value) is None`. That function is the gate, and it is asked, never re-derived.
   - **Admins** (`_viewing_as_admin`): every row, every audience, every author. Short circuit, no per-row gate. (An effective admin passes the class gate on every class, `classes/access.py` `_admin_access`, so the short circuit agrees with the gate.)
   - **Guild leads and staff**: every row, from any author, whose audience is a guild they may address. Site rows never.
   - **Instructors**, and leads for their guild's classes (#371): every row whose class they may announce to.
   - Why this rule for non-admins: they can already send to these audiences, so letting them see what was already sent to the same members prevents double posts, and a co-lead's draft is theirs to finish. One predicate for list, open, save, send and delete means nothing can be shown that cannot be acted on, and nothing acted on that cannot be shown. The rough edge, named: a lead can edit or delete an admin's draft addressed to the lead's own guild. They could already send that guild anything, so this hands them no new reach.
   - How the non-admin list is built without walking every row: candidates are rows whose guild is in `_compose_editable_guilds(request, member)`, rows whose class is taught by the member (`ClassOffering.objects.for_instructor(member)`) or sits in one of those guilds, and rows the user last saved (`author=request.user`, which catches a target reached through a lock link). Then the gate is asked **once per distinct guild or class** among the candidates, never per row, and the queryset is filtered to the targets that passed. Put this in one helper (`_announcement_rows(request, member) -> QuerySet[AnnouncementDraft]`) used by both views and by the composer lookups in decision 14.
   - A guild officer (site-wide `guild_officer` role) sees rows for their staffed guilds and rows they last saved, not every guild's rows, even though their gate would admit more. The page shows what concerns you, not every guild's mail.
4. **Who can open the page:** `_can_compose(request, member)` (the composer's own entry test), or anyone whose `_announcement_rows` is not empty. The second clause covers an instructor who reaches the composer only through a class page's locked link. Wrap it as `_can_open_announcements(request, member)`. Anyone else is refused the way the composer refuses: `messages.error(request, _compose_refusal_message(request))` and a redirect to `hub_guild_announcement_propose`. View as is honoured throughout (an admin previewing as a member is refused like a member).

### Where every entry point lands

5. | Entry point | Lands on |
   |---|---|
   | Admin Tools card "Announcements" | **The overview** (Drafts tab). Card description becomes "Write, save, and look back on announcements to a class, a guild, or the whole makerspace." |
   | Site Settings Announcements tab, "Compose an announcement" | **The overview.** Button text becomes "Open Announcements". Rewrite the paragraph above it without dashes: "Announcements has its own page now. Write one in the Announcement Composer, save a draft to finish later, and see everything already sent." |
   | Guild edit Announcements tab, "Compose announcement" | Unchanged: the composer pre-scoped to the guild. Add a second, ghost button beside it, "Drafts and sent", to the overview. |
   | Guild page "Send Announcement", class "Send Email", teach Registrations hand-off | Unchanged: the composer locked to that guild or class. They start a new announcement aimed at one audience, which is what those buttons promise. |
   | Voting banner and history detail "Draft announcement" | The shared results draft for that snapshot in the composer (decision 19). |
   | Composer "Back to Announcements" (new, decision 13) | The overview, Drafts tab. |
   | Composer Save draft | Stays in the composer (decision 13). |
   | Composer send, guild or class (sent in the request) | **The overview, Sent tab**, with the existing "Announcement sent to N recipient(s)." message. |
   | Composer send, site (queued) | **The overview, Sent tab**, with the existing "sending in the background" message; the row shows Sending. |
   | Composer send refused by the model (`_compose_send_refused`) | A results draft: its snapshot page (unchanged). A draft still resumable: its resume URL, so the work typed is kept. Otherwise: the overview, Sent tab. |
   | Delete from the overview | The overview, Drafts tab, toast "Draft deleted." |
   | No sidebar entry is added. | The sidebar is already long, and Admin Tools is the door Felix uses. |

   The admin tour (`core/tours.py`, "Site Wide Announcements") keeps navigating to the composer; its copy stays true.

### The list

6. **Layout.** `hub/announcements.html`, extending `hub/base.html`:
   - `components/page_header.html` with `title="Announcements"`, `description="Announcements waiting to go out and the ones already sent. Open a draft to finish it in the Announcement Composer."`, `action_url={% url 'hub_compose' %}`, `action_label="New announcement"`, `action_class="hub-btn hub-btn--primary"`, `action_help_key="announcements.new"`. This is the one primary action on the page.
   - Tabs: copy the Voting tabs pattern (`<nav class="pl-vote-tabs">` of `<a class="vote-tab">` links with `vote-tab--active` and `aria-current="page"`; `.pl-vote-tabs` lives in `voting-admin.css`, so copy its few rules into the new `announcements.css` as `.pl-announcements-tabs` rather than loading the voting sheet). Labels "Drafts" and "Sent", each followed by its count in a `<span class="hub-badge">`. Each anchor carries `data-announcements-tab="drafts|sent"`.
   - One `<div class="hub-card">` holding a `<table class="pl-members-table pl-announcements-table">` (the members table's card stacking, plus page-specific rules in `announcements.css`), then `components/table_pagination.html`, then the per-row delete `confirm_modal` includes (rendered after the table, as on Funding History).
7. **Which rows are in which tab.** Drafts: `sent_at IS NULL AND send_requested_at IS NULL` (this includes a site send the queue gave up on). Sent: `sent_at IS NOT NULL OR send_requested_at IS NOT NULL` (sent, plus queued and sending). Add manager methods `AnnouncementDraftManager.resumable()` (the Drafts filter; it replaces the state half of `for_user`) and `.sent_or_sending()`.
8. **Order and paging.** `prepare_table(request, qs, search_fields=[], default_sort=..., default_dir="desc", per_page=25, sortable=frozenset({default_sort}))`. Drafts sort on `updated_at`. Sent sorts on an annotation `activity_at = Coalesce("sent_at", "send_requested_at")`, so a queued row sits at the top by the time it was queued. No sortable headers and no search box (out of scope). `prepare_table` already carries `tab` through `base_params`, so pagination stays on its tab. Querysets `select_related("guild", "class_offering", "author", "funding_snapshot")`.
9. **Columns, with exact header text.** Every `<td>` carries `data-label` with its header text, which is what the members table uses to stack. Each `<tr>` carries `data-announcement-row="{pk}"` and `data-announcement-state="{state}"`.

   **Drafts tab:** Announcement | Audience | Channels | Status | Last edited | Edited by | (actions)

   **Sent tab:** Announcement | Audience | Channels | Status | Sent | Sent by | Reached | (actions)

   The actions header is an empty `<th>` with a `sr-only` "Actions" label.
   - **Announcement:** the title (the stored auto category, e.g. "Makerspace Announcement", "Woodshop Guild Announcement", "Class Announcement", "September 2026 Voting Results") as a link: Drafts to `hub_compose_resume`, Sent to `hub_announcement_sent`. Under it, one muted line: the flattened message (`rich_html_to_text(body)`) truncated to 90 characters, or "No message yet." when empty. The excerpt matters because most titles are the same few category words, so it is what tells rows apart. **No separate results badge:** the title already reads "<cycle> Voting Results", and the composer locks that draft's audience.
   - **Audience:** site → `AnnouncementDraft.Audience.SITE.label` ("Everyone (site-wide)", the composer's own locked label); guild → the guild's name; class → the class title.
   - **Channels:** the words for what this send uses, joined with " · ": "Email" when `send_email`, "Push" when `push_enabled`, "Discord" when `discord_enabled` and `discord_channel != none` and the audience is not a class (a class send never posts). When none apply: "App only". The header carries a `.pl-help` bubble: "Everyone it goes to also gets it in their notification bell." Model property `AnnouncementDraft.channel_labels -> list[str]`.
   - **Status:** decision 10.
   - **Last edited** (Drafts): `updated_at|date:"M j, g:i a"` (the format the Voting control uses: "Oct 2, 3:12 p.m."). **Sent** (Sent tab): `sent_at` in the same format; a queued row shows "Queued " plus `send_requested_at` in the same format.
   - **Edited by / Sent by:** `AnnouncementDraft.author_label`: the author's `get_full_name()` or username (what `_sender_line` uses); "Automatic" when `author` is blank on an unsent row (the results draft the job made, decision 18); "Unknown" when `author` is blank on a sent row (an account deleted later). "Automatic" is the word the Voting history already uses for a snapshot the system took.
   - **Reached** (Sent): people reached, decision 11. A queued row reads "Not yet". A row with no recorded period reads "Not recorded". The cell carries `data-reach`.
10. **Status set.** A derived, unstored `AnnouncementDraft.state` (a `TextChoices` named `DraftState`, values and labels below), shown as a `hub-pill` with the modifier given:
    | State | When | Pill | Line under the pill (muted) |
    |---|---|---|---|
    | `draft` "Draft" | unsent, not queued, no `send_error` | `hub-pill--neutral` | none |
    | `could_not_send` "Could not send" | `send_given_up` | `hub-pill--danger` | `send_error`, truncated to 80 characters |
    | `sending` "Sending" | queued | `hub-pill--warn` | "Goes out within 15 minutes." If `send_error` is set (a try failed and the queue will try again): "The last try failed. Trying again." |
    | `sent` "Sent" | `sent_at` set | `hub-pill--ok` | none |

    Only site sends are ever queued, so Sending and Could not send only appear on site rows. A row that could not be sent sits on the Drafts tab, where it can be fixed and sent again.
11. **Reach, one query per page.** `AnnouncementDraftManager.reach_for(rows) -> dict[int, int]`: one `EventDelivery` query over the page's `(event_key, delivery_period)` pairs, `status=SENT`, excluding `target_ref` starting with `broadcast`, grouped by `(event_key, period)` with `Count("target_ref", distinct=True)`. People reached = distinct members and email-only addresses who got it on at least one channel; Discord is not people. The event key is the draft's `_trigger_kind()` (it equals the emit key for all three audiences; expose it as a public `event_key` property). Rows with a blank `delivery_period` are skipped and read "Not recorded" (the 4 prod rows sent before this lands). The Drafts tab runs no reach query.

### Row actions

12. **Row actions.**
    - **Drafts row:** "Edit" (`pl-btn pl-btn--secondary pl-btn--sm`, a link to `hub_compose_resume`) and "Delete" (`pl-btn pl-btn--danger pl-btn--sm`, `@click="$dispatch('open-confirm', 'del-draft-{pk}')"`), side by side in `.pl-announcements-table__actions` (flex, `gap: 0.5rem`, right aligned on desktop, full width on a card).
    - **Delete** goes through `components/confirm_modal.html` in plain POST mode: `confirm_id="del-draft-{pk}"`, `confirm_action_url={% url 'hub_compose_delete_draft' pk %}`, `confirm_title="Delete this draft?"`, `confirm_button_text="Delete draft"`, and
      - `confirm_message`, plain draft: "It has not been sent to anyone. Deleting it cannot be undone."
      - `confirm_message`, results draft: "It has not been sent to anyone. It will not be made again on its own; you can start a new one from the Voting page."
    - `hub_compose_delete_draft` becomes a full-page POST (`@require_POST`): it looks the pk up among `resumable()` rows the request may handle (decision 14), deletes it, and redirects to `hub_announcements` with `messages.success(request, "Draft deleted.")` (ToastFlashMiddleware turns it into a toast across the boosted redirect). Any miss (sent, queued, someone else's audience, gone) is a 404 and nothing is deleted. Deleting a draft deletes only that row: a results draft's snapshot keeps its numbers and its "made" stamp (decision 18); nothing else references a draft.
    - **Sent row** (sent or sending): "View" (`pl-btn pl-btn--ghost pl-btn--sm`) to `hub_announcement_sent`. No Edit, no Delete: a sent announcement is a record. A queued row is out of the composer's hands (the queue may already be sending it), so it allows View only. See out of scope for cancelling.

### The composer

13. **Composer changes** (`templates/hub/announcement_compose.html`, `hub/views.py`):
    - **Back link.** Above the `<h1>`, for anyone `_can_open_announcements`: `<a href="{% url 'hub_announcements' %}" class="hub-btn hub-btn--sm hub-btn--ghost" data-compose-back>&larr; Back to Announcements</a>` (the hub's back link pattern, as on `space_request_review_queue.html`).
    - **Save draft returns.** A `type="button"` "Save draft" (`hub-btn hub-btn--ghost`, `data-compose-save-draft`, `hx-post="{% url 'hub_compose_save_draft' %}" hx-include="closest form" hx-swap="none"`, a `pl-spin` `htmx-indicator`, `:disabled="!hasBody"`) in both action rows, always before the primary: phase 1 "Save draft", "Preview & send →"; phase 2 "← Back", "Save draft", "Send announcement". Disabled with no message because an empty draft has nothing to resume and only clutters the list.
    - **The save response.** Valid: 200 carrying only the OOB `#compose-draft-pk` input (drop the `_compose_drafts_list.html` include from `_compose_save_result.html`), an `HX-Replace-Url` header set to the draft's resume URL (so a reload, or Back after leaving, reopens the draft instead of a blank composer), and the toast "Draft saved." Invalid: unchanged (204 with the first error as an error toast, no row). A draft that can no longer be saved (sent, queued, deleted, or not the requester's to handle): 404 with the error toast "This draft can no longer be edited. It may have been sent or deleted." in `HX-Trigger` (the `_compose_refused` idiom; htmx swaps no 4xx, but reads the header). No new row is created in that case.
    - **Delete the dead drafts list.** Remove `templates/hub/partials/_compose_drafts_list.html`, the `.pl-drafts*` and `.pl-draft-row*` rules in `hub.css` (~5822 to 5846), and the unused `"drafts"` context in `_render_compose` and in the save view. The overview replaces them.
    - **A line about the draft you opened.** For a resumed draft, under the lead paragraph, one muted line (`<p class="pl-compose-draft-meta" data-compose-draft-meta>`, styled in `announcement-compose.css`):
      - author set: "Draft last saved by {name}, {updated_at|date:"M j, g:i a"}."
      - author blank: "Draft made automatically {updated_at|date:"M j, g:i a"} from the {cycle_label} voting results."
      - and, when `send_given_up`: a second line, "This announcement could not be sent: {send_error}. Check it and send it again."
    - **Redirects after send:** decision 5's table. The success messages are unchanged.
    - Update the `hub_compose` docstring ("GET renders all three steps + the drafts list" is stale).

14. **Shared lookups replace `for_user`.** Every composer path that takes a `draft_pk` (resume GET, Save draft, Send, Delete, and the preview, test email and push test resolving a results snapshot) resolves it through one helper, `_handled_draft(request, raw_pk) -> AnnouncementDraft | None`: the pk among `AnnouncementDraft.objects.resumable()`, and only when the request may handle **the draft's stored audience** (decision 3). Save draft and Send additionally keep today's check on the **posted** audience. Both checks are needed: without the first, a lead could post an admin's site draft pk with their own guild as the audience and take the row over. Resume and Delete answer a miss with 404; Save draft as in decision 13; Send with `messages.error(request, "This draft can no longer be edited. It may have been sent or deleted.")` and a redirect to the overview. `AnnouncementDraftManager.results_snapshot_of(user, draft_pk)` is rewritten on top of `_handled_draft` (or removed in its favour), keeping its promise: a crafted `draft_pk` gives the results title to nobody who may not handle that draft. Remove `for_user` once nothing calls it.

15. **Two admins on one draft: last save wins, and the page says who saved last.** `save_from_form` keeps setting `author` to whoever saves, so `author` reads as "last saved by" while unsent and "sent by" once sent, and the email's From line and the send actor stay the sender with no new field. The list's Edited by column and the composer's "Draft last saved by" line make a co-editor visible. **No lock and no stale-save guard**: a handful of admins rarely open the same draft at once, the confirm and the previews show exactly what goes out, and a version check would add a hidden field, a refusal path and an OOB refresh after every save for a collision that has not happened. The edge, named: if two people edit at once, the second save replaces the first person's edits. Out of scope below.

### Seeing a sent announcement

16. **The sent view** (`hub/announcement_sent.html`, view `announcement_sent`). The pk must be in `_announcement_rows` (else 404). A pk that is still a draft redirects to its resume URL, so an old link never dead ends. Loads `announcement-compose.css` (preview cards) and `announcements.css`.
    - Top: the back link `&larr; Back to Announcements` to `?tab=sent`, then `<h1 class="hub-page-title">{title}</h1>` with the status pill, then one muted line:
      - sent: "Sent {sent_at|date:"M j, Y, g:i a"} by {author_label}."
      - sending: "Queued {send_requested_at|date:"M j, Y, g:i a"} by {author_label}. It goes out within 15 minutes." plus "The last try failed: {send_error}. It tries again on the next run." when set.
    - Card **"Who It Reached"**, a two column definition table (`<dl class="pl-announcement-facts">` in `announcements.css`):
      - Audience: as in the list, plus " (chosen recipients)" when `recipient_selection` is not empty, plus " and the waitlist" for a class send with `include_waitlist`.
      - Reached: "{n} people", or "Not yet" while sending.
      - In the app: "{n} members".
      - Email: "{n}", or "Off" when `send_email` was off.
      - Push: "{n} members", or "Off".
      - Discord (not for a class): "Sent to {channel}" plus " with {mention}" when a ping was chosen, where channel is the `discord_channel` label (for the guild choice: "the {guild name} channel") and mention the `mention` label; "Off" when off; "Not posted" when it was on but the ledger holds no Discord row (no webhook was set).
      - When `delivery_period` is blank: every number reads "Not recorded" and the card adds "How many people this reached was not recorded for announcements sent before this page existed."
      Counts come from `AnnouncementDraft.reach() -> AnnouncementReach | None` (a frozen dataclass with `people`, `in_app`, `email`, `push`, `discord_posted`): one `EventDelivery` query grouped by channel on `(event_key, delivery_period, status=SENT)`, plus the distinct people count. The rough edge, named: the ledger keeps a Discord row even when the webhook post failed (`_release_delivery`'s docstring), so "Sent to #general-chat" means it was sent, not that Discord accepted it.
    - Card **"What Went Out"**, one section per channel that was on, each with a `pl-compose-channel__title` heading:
      - Push Notification: a static copy of the composer's push preview card (`pl-push-preview` markup, no ids), title and line from `build_push_message`.
      - Email: the inbox row and iframe from the composer's email preview. Split them out of `_compose_email_preview.html` into a new `hub/partials/_email_preview_frame.html` that both the composer partial and this page include. Do not include `_compose_email_preview.html` itself here: it carries the Discord card with `hx-swap-oob="true"`, and a boosted arrival would try to swap it out of band.
      - Discord: `hub/partials/_compose_discord_preview.html` with `oob=False`.
      Build all three with the same calls `hub_compose_preview` makes (factor one helper both use, so the composer preview and this page cannot drift): `build_email_message(site_url)`, `build_embed_payload(build_discord_message(site_url))` with `discord_markdown_html`, `build_push_message(site_url)`. The edge, named: the page is rebuilt from the saved row, not a stored copy. The message, title and results chart are what went out (the chart reads the snapshot's frozen numbers); a class renamed since, or a sender who changed their name, shows the new name.
    - No actions beyond Back. Nothing on this page sends, edits or deletes.

17. **Recording reach: `AnnouncementDraft.delivery_period`.** `CharField(max_length=120, blank=True, default="", db_default="", help_text="The delivery ledger period this announcement's send used, stamped when it is sent, so its reach can be read back from EventDelivery. Blank for announcements sent before it was recorded.")`. `send()` stamps it in its final transaction, beside `sent_at`: site `self._site_delivery_period()`; class the exact timestamped period it emitted with (hoist it into a local before `emit`); guild the `GuildAnnouncement`'s period (add `GuildAnnouncement.delivery_period` returning `f"announcement:{self.pk}"` and use it in `notify_members` too, so the literal lives once). The 4 existing prod rows stay blank. No backfill: matching guild posts back to drafts by title and time would be a guess.

### The results draft, made on its own

18. **Once per snapshot, by the 15 minute job, for the newest snapshot only.**
    - **Where:** the existing `take_cycle_snapshot` command, as a second phase. Not `FundingSnapshot.take()`: `take()` cannot reach September, which already exists, and go live needs it. Not a data migration: the draft's body is built by live model methods (`results_announcement_body`). Not `send_queued_announcements`: a send job that also creates drafts would surprise the next reader. Restructure `handle()` into `_take_snapshot()` (today's body; its early returns now return from the helper) and then `_make_results_draft()`, which runs on **every** tick, whether `auto_snapshot_enabled` is on or off and whether the month's slot is claimed, because a snapshot an admin took by hand deserves its draft too. Phase order means the auto take on the first of the month and its draft happen in the same tick. Update the `ScheduledJob` description: "Records each month's vote tallies so results are preserved, and makes that month's voting results draft." If an admin pauses this job in Scheduled Jobs, drafts stop appearing too; the Voting page's Draft announcement still works.
    - **The model:** `FundingSnapshot.make_results_draft() -> AnnouncementDraft | None` and `FundingSnapshot.make_newest_results_draft()` (classmethod: the newest snapshot by `snapshot_at`, then `make_results_draft()`). Inside `transaction.atomic()` with the snapshot row `select_for_update`:
      1. `results_draft_created_at` already set → `None` (made before; deleted or not, it is never made again).
      2. Results sent, or a results announcement for it queued → `None`, no stamp.
      3. No per-guild results (`not self.allocation_summary()`) → `None`, no stamp.
      4. An unsent, unqueued results draft for this snapshot already exists (an admin pressed Draft announcement first) → stamp `results_draft_created_at = now`, return `None`.
      5. Otherwise create the draft with `author=None` and stamp.
      The draft is built by one shared builder, `FundingSnapshot._new_results_draft(author: User | None)`, which `draft_results_announcement` also uses, so a made draft and a clicked one are identical: audience site, the prose body, the push line, email, push and Discord on, `#general-chat`, `@everyone`, sender shown, not urgent, title "<cycle> Voting Results".
    - **New field:** `FundingSnapshot.results_draft_created_at = DateTimeField(null=True, blank=True, help_text="When this snapshot's results draft was made automatically, or found already open. Set once, so a draft an admin deletes is never made again on its own.")`.
    - **Only the newest snapshot.** August (snapshot 8) does not get one, now or after September is sent. A results announcement is news; August's numbers announced in October with an @everyone ping would be noise, and a second "Voting Results" draft beside September's invites sending the wrong one. Anyone who wants August announced can still press Draft announcement on its snapshot page, which works as today.
    - **The author of a made draft is blank**, shown as "Automatic" in the list and "Draft made automatically" in the composer. Nothing about who sends it changes: the composer's preview builds its From line from the viewer (`_compose_preview_draft` uses `request.user`), and Save draft or Send sets `author` to that person (decision 15), so the email's From line, the SiteActivity actor and the Sent by column all name whoever pressed Send.
    - **Fail loudly:** `send()` raises `ValueError("An announcement needs a sender before it goes out.")` if `author` is blank (no path reaches it, because the composer saves before it sends or queues). `_sender_line()` returns `""` when `author` is blank, so a sent row whose account was deleted still renders.
    - **Concurrency:** `draft_results_announcement` takes the same snapshot row lock before it looks for an open draft, so a click and the job in the same instant make one draft, not two.

19. **The Voting banner opens the shared draft.** `FundingSnapshot.draft_results_announcement(author)` returns the snapshot's open (unsent, unqueued) results draft **from any author**, newest `updated_at` first, else creates one with `author` set to the clicker. Its guards are unchanged (already sent, sending, nothing to announce). `voting_results_draft`'s docstring and `results_announcement_failure`'s ("reopens it for its author") are updated. The button and its copy stay "Draft announcement": it now opens the draft the job already made.

### Data, help and housekeeping

20. **One migration, schema only.** `AnnouncementDraft.author` becomes `null=True, blank=True, on_delete=SET_NULL` (a sent announcement is a record and should not vanish with an account), help text "Who last saved this announcement, and the sender once it is sent (the send actor and the email's From line). Blank on a results draft the system made, until someone saves or sends it." Add `AnnouncementDraft.delivery_period` and `FundingSnapshot.results_draft_created_at`. All three are additive or relax a constraint, so the release still serving during `migrate` keeps working: old code never writes a blank author, and its `for_user` never returns a row with one. No data migration. `manage.py check` and `makemigrations --check` clean.
21. **Help.** In `membership/help_content.py`, guide `announcement-composer`: change "Ways in" so **Admin Tools → Announcements** opens the Announcements page, and add a section (copy final):

    > ### Drafts and Sent Announcements {#composer-drafts}
    >
    > **Admin Tools → Announcements** opens the Announcements page. It has two tabs.
    >
    > - **Drafts** lists announcements that have not gone out yet. Press **Save draft** in the composer to keep one here. Use **Edit** to pick it up again, or **Delete** to throw it away.
    > - **Sent** lists what already went out: when, who sent it, and how many people it reached. **View** shows what went out on each channel.
    >
    > Drafts are shared. Admins see every draft. Guild leads and staff see the drafts and sent announcements for the guilds they help run, and instructors see the ones for their classes. Whoever saved a draft last shows as its editor. Whoever sends it is the sender, and their name is on the email's From line when **Show who it's from** is on.
    >
    > Once a month's voting results are in, a draft called "September 2026 Voting Results" (with that month's name) appears on the Drafts tab on its own, ready for an admin to check and send. If you delete it, it does not come back. You can start a new one from the Voting page with **Draft announcement**.
    >
    > An announcement to everyone shows as **Sending** until it goes out, within 15 minutes. If it could not be sent, it moves back to Drafts marked **Could not send**, with the reason.

    Add a screenshot entry for the overview (`page: "hub_announcements"`, `as_role: "admin"`). Deploys do not run `seed_help_center`; the PR says it must be run against production after the merge (STANDARDS section 10).
22. **Docs.** `FRONTEND.md` CSS table gains `announcements.css` (loaded by `hub/announcements.html` and `hub/announcement_sent.html`). `hub/AGENTS.md` or `CODEBASE_INDEX.md`, whichever lists hub views, gains the two views.
23. **Changelog fragment** (`changelog.d/`, bump per its README), wording to start from:
    - title: "Announcements has its own page, with drafts and sent announcements"
    - "Admin Tools → Announcements now opens a page listing drafts and the announcements already sent, with who sent each one, when, and how many people it reached."
    - "The composer has a Save draft button again. A saved draft waits on the Drafts tab until someone sends it."
    - "Each month's voting results draft appears on the Drafts tab on its own once the results are in."

### Components, CSS, states (the UI checklist, answered)

24. **Components, CSS and states.**
    - Reused, by name: `components/page_header.html`, `components/confirm_modal.html` (plain POST), `components/table_pagination.html`, `prepare_table`, `.pl-members-table` stacking, `.vote-tab`, `.hub-pill--*`, `.hub-badge`, `.pl-help`, `hub/partials/_compose_discord_preview.html`, the push preview card markup, the new shared `_email_preview_frame.html`. Toasts via Django messages on full page POSTs and `trigger_toast` on the HTMX Save draft. No new modal, toggle or form field components; the page has no form fields.
    - New CSS only in `static/css/announcements.css` (`pl-` prefix): `.pl-announcements-tabs`, `.pl-announcements-table` (excerpt line, status sub line, actions cell), `.pl-announcements-table__actions`, `.pl-announcement-facts`, plus light theme pill colours for this table matching the existing overrides (`--ok` #1f7a52, `--neutral` #5b6675, `--danger` #b03030; give `--warn` a darker amber in the same spirit). `.pl-compose-draft-meta` goes in `announcement-compose.css`. Theme tokens only, no inline styles, no `<style>` in `extra_head`, 8px grid spacing, and the last button on each card clears the next section by at least 1.5rem (FRONTEND rule 18).
    - **Empty states**, one muted paragraph in the card in place of the table:
      - Drafts: "No drafts right now. Press Save draft in the Announcement Composer and the draft waits here until it is sent."
      - Sent: "Nothing sent yet. Each announcement sent is listed here with when it went out and how many people it reached."
    - **Loading:** tabs, pagination, Edit, View and Delete are full, boosted navigations or POSTs, so the hub's loading bar covers them; Save draft shows its `pl-spin` indicator.
    - **Errors:** every miss is a 404 or a toast, never a 500 (decisions 13, 14).
    - **Success:** "Draft saved." (toast), "Draft deleted." (toast via messages), the existing send messages on the Sent tab.
    - **Mobile** (768px and below): the members table stacking turns each row into a bordered card: the title and excerpt lead with no label, then Audience, Channels, Status, the date, the person, Reached, each under its small uppercase label, then the action buttons side by side at full card width (real buttons with text, never icons). No horizontal scroll at 375px. The sent view's facts list stacks label over value; the email iframe keeps the composer's responsive frame.
    - **Verify both themes**, desktop and 375px, on the overview (both tabs, with rows and empty), the sent view, and the composer with the back link, the meta line and Save draft.

## Wireframes

Desktop, Drafts tab (admin):

```
Announcements                                                        [ New announcement ]
Announcements waiting to go out and the ones already sent. Open a draft to finish it in
the Announcement Composer.

 Drafts (2)    Sent (5)
 ━━━━━━━━━━
┌───────────────────────────────────────────────────────────────────────────────────────────────┐
│ Announcement                    Audience        Channels (?)      Status          Last edited      Edited by             │
├───────────────────────────────────────────────────────────────────────────────────────────────┤
│ September 2026 Voting Results   Everyone        Email · Push ·    [Draft]         Oct 2, 12:15     Automatic  [Edit][Delete] │
│ The votes for September 2026    (site-wide)     Discord                           a.m.                                   │
│ are in. 211 members voted on…                                                                                           │
├───────────────────────────────────────────────────────────────────────────────────────────────┤
│ Makerspace Announcement         Everyone        Email · Push      [Could not send] Oct 1, 4:02     Felix Plaza [Edit][Delete]│
│ The shop closes at 6 p.m. on…   (site-wide)                       Email provider  p.m.                                  │
│                                                                   timed out                                              │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

Desktop, Sent tab (admin):

```
 Drafts (2)    Sent (5)
               ━━━━━━━━
┌───────────────────────────────────────────────────────────────────────────────────────────────────┐
│ Announcement                Audience          Channels         Status       Sent             Sent by      Reached        │
├───────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Makerspace Announcement     Everyone          Email · Push ·   [Sending]    Queued Oct 2,    Felix Plaza  Not yet [View] │
│ Open house this Saturday…   (site-wide)       Discord          Goes out     3:40 p.m.                                    │
│                                                                within 15 minutes.                                        │
├───────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Woodshop Guild Announcement Woodshop Guild    Email · Discord  [Sent]       Oct 1, 9:12 a.m. Mira Lee     41      [View] │
│ The table saw is back from…                                                                                              │
├───────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Class Announcement          Intro to          Email · Push     [Sent]       Sep 28, 6:30     Ana Ruiz     12      [View] │
│ Bring gloves on Saturday.   Blacksmithing                                   p.m.                                         │
├───────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Ceramics Guild Announcement Ceramics Guild    Email · Push     [Sent]       Sep 2, 10:05     Jo Park   Not recorded [View]│
└───────────────────────────────────────────────────────────────────────────────────────────────────┘
                                         Page 1 of 1 · 5 total
```

Mobile, 375px, one card per row:

```
┌─────────────────────────────────┐
│ September 2026 Voting Results   │
│ The votes for September 2026…   │
│                                 │
│ AUDIENCE                        │
│ Everyone (site-wide)            │
│ CHANNELS                        │
│ Email · Push · Discord          │
│ STATUS                          │
│ [Draft]                         │
│ LAST EDITED                     │
│ Oct 2, 12:15 a.m.               │
│ EDITED BY                       │
│ Automatic                       │
│                                 │
│ [     Edit     ] [   Delete   ] │
└─────────────────────────────────┘
```

Sent view:

```
[← Back to Announcements]

Makerspace Announcement   [Sent]
Sent Oct 2, 2026, 3:52 p.m. by Felix Plaza.

┌ Who It Reached ─────────────────────────────────────────┐
│ Audience      Everyone (site-wide)                      │
│ Reached       297 people                                │
│ In the app    295 members                               │
│ Email         294                                       │
│ Push          131 members                               │
│ Discord       Sent to #general-chat with @everyone      │
└─────────────────────────────────────────────────────────┘

┌ What Went Out ──────────────────────────────────────────┐
│ Push Notification                                       │
│  ┌ 🔔 Past Lives Makerspace ──────────────────────────┐  │
│  │ Makerspace Announcement                           │  │
│  │ Open house this Saturday from noon. Bring a…      │  │
│  └───────────────────────────────────────────────────┘  │
│ Email                                                   │
│  Past Lives   Makerspace Announcement                   │
│  ┌ (the branded email, in the preview iframe) ───────┐  │
│  └───────────────────────────────────────────────────┘  │
│ Discord                                                 │
│  ┌ 💬 Past Lives Makerspace ─────────────────────────┐  │
│  │ Makerspace Announcement                           │  │
│  │ Open house this Saturday from noon…               │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

Composer, top and phase 1 actions (resumed results draft):

```
[← Back to Announcements]
Compose announcement
Write your message and pick who gets it, then preview each channel before it goes out. …
Draft made automatically Oct 2, 12:15 a.m. from the September 2026 voting results.
┌──────────────────────────────────────────────────────────┐
│ 1. Compose   2. Preview & send                           │
│ Sending to: Everyone (site-wide)                         │
│ …message, delivery…                                      │
│                              [Save draft] [Preview & send →] │
└──────────────────────────────────────────────────────────┘
```

## Acceptance criteria

Assert on markup (URLs, `data-` attributes, ids) or factory strings, never on copy a changelog entry could contain (STANDARDS section 8). 100% branch coverage and mutation kill on new code.

**Page and tabs**
- [ ] `GET /announcements/` as an admin renders `hub/announcements.html` with both `data-announcements-tab` anchors, the Drafts tab active by default and for an unknown `tab` value, `?tab=sent` active for Sent, and a header link to `hub_compose`.
- [ ] Tab counts equal the number of rows the viewer may see in each tab.
- [ ] Drafts holds exactly the unsent, unqueued rows (including a given-up site send); Sent holds the sent and queued rows. Drafts are ordered newest `updated_at` first; Sent by `Coalesce(sent_at, send_requested_at)` newest first, so a queued row heads the list.
- [ ] 26 rows paginate to 25 plus 1, and page 2 keeps `tab=sent`.
- [ ] Each row carries `data-announcement-row` and the right `data-announcement-state` for draft, could not send (with `send_error` shown), sending, and sent.
- [ ] Channels lists Email, Push, Discord per the flags; a class row never lists Discord; all off reads App only.
- [ ] Edited by / Sent by shows the author's name, "Automatic" for a blank author on an unsent row, "Unknown" on a sent one.
- [ ] Reached equals the distinct non-broadcast `target_ref` count in `EventDelivery` for the row's `(event_key, delivery_period)`; a queued row and a blank-period row have no number. The Sent tab's query count is the same with 2 rows and with 12 (`django_assert_num_queries` on both), for an admin.
- [ ] Each tab shows its empty state paragraph and no table when it has no rows.

**Permissions**
- [ ] An admin sees site, guild and class rows from every author on both tabs, and can open, save, send and delete another admin's draft.
- [ ] A lead of guild A sees guild A rows from any author, and never guild B rows or site rows: they are absent from the list, `GET` of guild B's resume URL is 404, `POST` delete of guild B's draft is 404 and the row survives, and Save draft or Send with guild B's `draft_pk` is refused with the row unchanged.
- [ ] A lead posting an admin's **site** draft pk with `audience=guild:<their guild>` to Save draft or Send is refused and the site draft is unchanged (the stored audience check, decision 14).
- [ ] An instructor sees rows for classes they may announce to and not another instructor's class rows; a lock-only instructor (no general compose rights) with a class row can open the page; one with no rows is refused.
- [ ] A member with no compose rights and no rows is redirected to `hub_guild_announcement_propose` with the refusal message; so is an admin previewing as a member.
- [ ] `POST` to `hub_compose_delete_draft` for a **sent** row, and for a **queued** row, returns 404 and the row still exists; `GET` to it returns 405.
- [ ] `hub_announcement_sent` 404s a row the viewer may not see, and redirects a still-draft pk to its resume URL.
- [ ] Preview, test email and push test with a crafted `draft_pk` of a draft the requester may not handle give the plain category title, never "<cycle> Voting Results".

**Composer**
- [ ] The composer renders the `data-compose-back` link to `hub_announcements` for an admin, and both Save draft buttons (`data-compose-save-draft`, `hx-post` to `hub_compose_save_draft`). Replaces `it_hides_the_drafts_panel_and_the_save_draft_button`.
- [ ] First Save draft creates one row, returns the OOB `#compose-draft-pk` with its pk, an `HX-Replace-Url` of its resume URL and a success toast; a second save updates the same row; the response contains no `compose-drafts`. `_compose_drafts_list.html` no longer exists and no template or view references it.
- [ ] Save draft on a draft sent, queued or deleted since it was opened returns 404 with an error toast and creates no row.
- [ ] Admin B saving admin A's draft changes `author` to B, and the overview's Edited by shows B. Replaces `it_404s_resuming_another_users_draft` and `it_404s_deleting_another_users_draft` with the shared and the audience scoped behaviour.
- [ ] A resumed draft shows `data-compose-draft-meta` with the last saver, the automatic wording for a blank author, and the could not send reason when given up.
- [ ] A guild send and a class send redirect to `hub_announcements?tab=sent`; a site send queues and redirects there too, and the row shows `data-announcement-state="sending"`.
- [ ] A refused send of a non-results draft that is still resumable redirects to its resume URL.

**Reach and the sent view**
- [ ] `send()` stamps `delivery_period`: `announce:{pk}` for a plain site send, `announce:results:{snapshot_id}` for a results send, `announcement:{guild_announcement.pk}` for a guild send, and the exact period the class emit used (an `EventDelivery` row exists under it).
- [ ] The sent view's In the app, Email and Push numbers equal the ledger rows per channel for that period; Discord reads sent, off or not posted correctly; Off shows for a channel that was switched off; a blank period shows "Not recorded".
- [ ] The sent view's email iframe `srcdoc` equals `build_email_message(site_url).html_body` (escaped), and its Discord card shows the same title and description `hub_compose_preview` would; a class send has no Discord section. The page contains no `hx-swap-oob`.
- [ ] A sent row whose author was deleted still renders on both pages (`_sender_line` returns blank, "Unknown" shows).

**The results draft**
- [ ] `make_results_draft()` on the newest pending snapshot creates one draft, `author` blank, audience site, linked to the snapshot, with the same body, push line, channel, ping and toggles `draft_results_announcement` uses, and stamps `results_draft_created_at`.
- [ ] Called again, it creates nothing. After that draft is deleted, it creates nothing.
- [ ] With an open results draft an admin made first, it creates nothing and stamps the field.
- [ ] It creates nothing for a snapshot whose results were sent, whose announcement is queued, or which has no per-guild results.
- [ ] With August (older) and September (newest) both pending, only September gets a draft; after September is sent, a further tick still makes none for August.
- [ ] `take_cycle_snapshot` makes the draft on a tick where the month's slot is already claimed, and with `auto_snapshot_enabled` off; on the first tick of a month it takes the snapshot and makes its draft in the same run. Its run fails red if making the draft raises.
- [ ] `draft_results_announcement` by a second admin returns the job's draft (same pk). Replaces `it_gives_another_admin_their_own_draft`; the banner's Draft announcement POST opens that pk in the composer.
- [ ] Sending the made draft from the composer sets `author` to the sender before `queue_send`, and after the queue sends it the email's From line and the `SiteActivity` actor are that sender. `send()` on a blank author draft raises `ValueError`.

**Migration, checks, e2e, pictures**
- [ ] One migration: `AlterField` on `AnnouncementDraft.author` (nullable, `SET_NULL`), `AddField` `AnnouncementDraft.delivery_period`, `AddField` `FundingSnapshot.results_draft_created_at`. No `RunPython`. `manage.py check` and `makemigrations --check` clean.
- [ ] Scheduled jobs parity spec passes with the updated description.
- [ ] e2e, new `tests/e2e/announcements_overview_spec.py`, on PostgreSQL: an admin goes Admin Tools → Announcements, sees a results draft made by `make_newest_results_draft()` on the Drafts tab marked Automatic, presses Edit and sees the prose in the Quill editor and the meta line; starts a new guild announcement, presses Save draft, returns via Back to Announcements and finds it; deletes it through the confirm modal and sees it gone; sends a guild announcement, lands on the Sent tab with the row sent and a reach number, and opens View to see the facts and the email iframe. Wait on the observable (the row, the card), never only on the URL (boosted arrivals). At 375px, `document.documentElement.scrollWidth <= window.innerWidth` on both tabs. Grep `tests/e2e/` for the composer's old post-send landing on `hub_compose` and fix any spec that relied on it.
- [ ] Screenshots under `mockups/screenshots/announcements-overview-*.png`: Drafts tab (dark and light), Sent tab, the 375px cards, the sent view, the composer with Back, the meta line and Save draft.

**Go live (verify by data after the deploy, not by the job's green run)**
- [ ] Within one `take_cycle_snapshot` tick of the deploy, a read of production shows one unsent `AnnouncementDraft` linked to snapshot 10 with a blank author and title "September 2026 Voting Results", snapshot 10's `results_draft_created_at` set, and no draft linked to snapshot 8.
- [ ] Felix, or any admin, opening Admin Tools → Announcements sees that row on the Drafts tab marked Automatic, and the Voting banner's Draft announcement opens the same pk.
- [ ] `seed_help_center` has been run against production so the guide carries the new section.

## Out of scope (named edges)

- **Search, filters and sortable columns.** Production holds 4 rows; a search box earns its place when the list is long enough to lose things in. `prepare_table` makes it a small follow up.
- **Duplicate an announcement, scheduling a send for later, open and click rates.** Not asked for. The ledger records delivery, not opens.
- **Cancelling a queued send.** The queue job may already be sending it; an honest cancel needs a lock shared with the job. The confirm before sending is the guard today.
- **Editing or deleting a sent announcement.** Sent rows are a record. A guild post's text on the guild page is still edited from the guild edit Announcements tab, as today.
- **A lock or stale-save check for two editors** (decision 15): last save wins.
- **Member proposals** (`/announcements/review/`) stay on their own page; they are not composer drafts.
- **A sidebar Announcements entry** (decision 5).
- **August 2026's results.** No draft is made for it on its own (decision 18). Once September is sent, the Voting banner will offer August, as #588 already said; whether to announce it or leave it is a separate call.
- **Keeping the lock on a resumed guild or class draft.** A draft saved from a locked compose resumes with the audience picker, which offers only audiences the user may address. Unchanged from today.
- **Reach for the 4 announcements sent before this lands**, and live "reached so far" counts while a send is still going.
- **Moving the composer views out of `hub/views.py`.**

## Files

`membership/models.py` (`AnnouncementDraft`: `author`, `delivery_period`, `DraftState`, `state`, `channel_labels`, `author_label`, `audience_value`, `event_key`, `reach()`, `send()`, `_sender_line()`; `AnnouncementDraftManager`: `resumable()`, `sent_or_sending()`, `reach_for()`, rewritten `results_snapshot_of`, `for_user` removed; `GuildAnnouncement.delivery_period`; `FundingSnapshot`: `results_draft_created_at`, `make_results_draft()`, `make_newest_results_draft()`, `_new_results_draft()`, `draft_results_announcement()`), `membership/migrations/` (one schema migration), `core/management/commands/take_cycle_snapshot.py`, `core/scheduled_jobs.py`, `hub/views.py` (`announcements_overview`, `announcement_sent`, `_announcement_rows`, `_can_open_announcements`, `_handled_draft`, the composer save, send, delete, preview, test and push test lookups, `_render_compose`, `_compose_send_refused`, `hub_compose`, `hub_admin_tools` unchanged flags, `voting_results_draft` docstring), `hub/urls.py`, new `templates/hub/announcements.html`, new `templates/hub/announcement_sent.html`, new `templates/hub/partials/_email_preview_frame.html`, `templates/hub/partials/_compose_email_preview.html`, `templates/hub/partials/_compose_save_result.html`, deleted `templates/hub/partials/_compose_drafts_list.html`, `templates/hub/announcement_compose.html`, `templates/hub/admin_tools.html`, `templates/hub/admin/site_settings.html`, `templates/hub/guild_edit.html`, new `static/css/announcements.css`, `static/css/announcement-compose.css`, `static/css/hub.css` (remove `.pl-drafts*`, `.pl-draft-row*`), `membership/help_content.py`, `FRONTEND.md`, `changelog.d/`, specs (`tests/hub/announcement_compose_spec.py`, `tests/hub/voting_results_announcement_spec.py`, `tests/membership/announcement_draft_spec.py`, `tests/membership/results_announcement_spec.py`, the `take_cycle_snapshot` spec, a new `tests/hub/announcements_overview_spec.py`, the new e2e spec).
