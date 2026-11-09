# polls app

Polls (#708) were removed in #890 and came back in #925: site wide on Home, and a guild's on its guild page's Polls tab; never in the Spotlight. Restored from git before `ff485764` rather than rewritten.

## Models (`polls/models.py`)

| Model | Notes |
|---|---|
| `Poll` | question, opens_at, closes_at, created_by, created_at, guild (nullable, 0002, `CASCADE`, `related_name="polls"`; blank is site wide), allows_write_in (0003, off by default, set when posted). Open exactly while `opens_at <= now < closes_at`, so it closes on its own; `close(now)` is Close now. `Poll.post(question, choices, days, by, now, guild=None)` validates (question 1 to 200 characters, 2 to 6 answers of at most 120, 1 to 60 days), serializes with `select_for_update` on the `SiteConfiguration` row (site wide) or the guild's row, and raises `PollAlreadyOpenError` (its message is the open poll's question) while a poll is open in that scope. One open poll site wide and one per guild; a guild's never counts against the site wide one or another guild's. There is no "close it and post" path: Close now first. `results()` is the tally, its percents over every vote including written ones. `written_answers(viewer)` lists the written answers as `WrittenAnswer` (text, author, is_mine), none and no query without the toggle. `vote(member, now, choice_pk=None, write_in="", anonymous=True)` is the only way a vote is cast: exactly one of a picked answer and a written one (only where `allows_write_in`, at most 120 characters). It raises the `VoteRefusedError` family (`NotAVoterError`, `PollClosedError`, `InvalidAnswerError`, `AlreadyVotedError`); the one-vote constraint's race becomes `AlreadyVotedError`. |
| `PollChoice` | poll, text, position; unique per poll and position. |
| `PollVote` | poll, choice (nullable since 0003), write_in, anonymous, member, created_at; one per member and poll (`pollvote_one_per_member`), and exactly one of choice and write_in (`pollvote_choice_xor_write_in`). `save` takes the poll from the choice and refuses a choice from another poll. `objects.written()` is the written answers, oldest first. |

`PollQuerySet`: `site_wide()`, `for_guild(guild)`, `open_at(now)`, `closed_at(now)`, `open_poll(now)` (the one open poll or None, one query), `with_totals()` (`total_votes`, `top_answer`, a tie going to the answer listed first).

`can_vote(member)` is `hub.spotlight.sees_spotlight`: a member that is not a guest (#691) or former member. The lockout middleware keeps guests, former and suspended members off the hub, so the read only card for them is proven on the partial (`tests/polls/voting_spec.py`).

Written answers (#925 part 3): `WrittenAnswer.from_vote` is the only place an author is named, and it reads the member's name only when the vote is not anonymous, so an anonymous author never reaches a template, attribute or response. Picked answers stay counts only. `polls.forms.WriteInForm` (prefixed `poll-<pk>`, rendered through `polls_tags.write_in_form`) carries a hidden `shown` marker: a post without it counts as anonymous, so a name shows only when the member turned Post anonymously off on the rendered form. 0003's reverse deletes written answers before `choice` is required again.

`PollCard` is one poll as one member sees it (tally, written answers, their own answer, `has_voted`, `shows_choices`, `offers_write_in`). `for_polls` builds a page in two queries, three when any poll takes written answers; `open_for(member, now)` is Home's card (site wide only), one query with no poll open and three with one. `GuildPolls.load(guild, member, now)` is a guild's Polls tab: `open` (a card or None) and `past` (closed cards, newest first), one query with no polls and three with some; falsy when the guild has none.

## Email polls (#1142, part 1 of 4: models and rules only, no page or email yet)

A poll of up to ten questions sent from Email Members. It rides on an `membership.AnnouncementDraft` whose `kind` is `Poll` (`AnnouncementDraft.Kind`, default `Email`): the draft holds the title, audience, selection, sender, `send_email` (Email when it goes live) and the site queue. `AnnouncementDraft.poll_recipient_ids()` is who a poll to that audience goes to: the accounts the same email reaches, selection and added people included, email-only addresses left out. Separate from `Poll` above, which it never touches.

| Model | Notes |
|---|---|
| `EmailPoll` | draft (one-to-one, `related_name="poll"`, deleted with it), intro (500), run_days (`RunLength`), remind_days_before (`ReminderLead` 1 to 3, blank for none; `emailpoll_remind_in_run` keeps it under run_days), audience_only, opens_at / closes_at / remind_at (blank while a draft, set by `open(now)`, which also records the recipients from `draft.poll_recipient_ids()` in the same transaction, so an audience only poll admits them before a queued email goes out, and keeps the first window and recipients on a repeat), reminder_sent_at. `replace_questions(specs)` (draft only, `validate_questions`; blank option rows are dropped, not counted: `QuestionSpec.filled_options`), `record_recipients(user_ids)` (idempotent), `answer_refusal(member, now)` (`POLL_CLOSED`, `DIFFERENT_GROUP`), `answer(member, answers, now)` (the one writer: replaces the member's answers under a row lock; every pick question answered, Pick one exactly one, open-ended may be blank and stores nothing, but a submission that answers nothing is refused with `NO_ANSWERS`, so every response holds an answer; a pick arrives as an int, a string of digits or a list of either, anything else is `AnswerRefusedError`, never a 500), `may_see_results(member)` (sender, admin, or answered), `results()`. Queryset: `sent()`, `open_at(now)`, `sent_to(member)`, `reminders_due(now)`. |
| `EmailPollQuestion` | poll, position, text (200), kind (`QuestionKind`: Open-ended, Pick one, Pick any). |
| `EmailPollOption` | question, position, text (120); 2 to 4 per pick question. |
| `EmailPollRecipient` | poll, member, sent_at; one per pair. Who it was sent to, recorded at send. |
| `EmailPollResponse` | poll, member, created_at, updated_at; one per pair. |
| `EmailPollAnswer` | response, question, option (nullable), text; exactly one of option and text, one row per picked option, one text row per question. |

The email link's token: `make_answer_token(member)` signs `{"poll", "member"}` with its own salt; `EmailPoll.read_answer_token(token, now)` raises `BadPollLinkError` (`BAD_LINK` or `POLL_CLOSED`) for a tampered token, a member the poll was not sent to, or a closed poll. It names who answers; it never signs anyone in.

`EmailPollResults.load(poll)`: per question `OptionResult`s (count and percent of everyone who answered, so Pick any can add past 100) and `WrittenReply`s (name, text), then one `ResponseRow` per person (name, one cell per question). Four queries however many answered.

## Views (`polls/views.py`, `polls/urls.py`, mounted at `/polls/`, members host only via `MEMBER_ONLY_PATH_PREFIXES`)

| URL name | Path | What |
|---|---|---|
| `polls:index` | `/polls/` | Past Polls: every closed poll, site wide and guild (each guild's labelled with a pill, `select_related("guild")`), newest first, ten a page, with counts and the member's own answer. Exists whether or not a poll is open; 403 for an account that cannot vote. |
| `polls:vote` | `/polls/<pk>/vote/` | POST only. Over HTMX it answers the card (`polls/partials/_poll_card.html`, `variant` "", "home" or "guild") with a toast; a plain post redirects to `next` (ours only), else Home. |
| `polls:admin` | `/polls/manage/` | Admin Tools > Polls (`@fog_admin_required`): the open site wide poll with Close now, the New Poll composer, the past site wide polls (`prepare_table`, sortable). Guild polls are managed on their guild pages. |
| `polls:admin_new` | `/polls/manage/new/` | Post a poll from the New Poll composer (`polls.forms.NewPollForm`, `polls/partials/_poll_form.html`, the approved `mockups/925-poll-composer.html`: the composer is the poll card, Runs for is one of the five `RunLength` chips, its Closes line uses `closes_label`, the same words the server writes). Invalid or refused posts re-render the page at 200 with what was typed, because a boosted 4xx becomes a fresh GET. |
| `polls:admin_close` | `/polls/manage/<pk>/close/` | Close now. |
| `polls:admin_poll` | `/polls/manage/<pk>/` | One poll's results, counts only. |
| `polls:guild_new` | `/polls/guild/<slug>/new/` | Post a guild poll. `membership.permissions.can_edit_guild` (the guild's lead and staff, guild officers, admins) or 403. A refusal (bad form, a poll already open in the guild) comes back on the guild's Polls tab as an error message. |
| `polls:guild_close` | `/polls/guild/<slug>/<pk>/close/` | Close now on that guild's poll; same gate; another guild's poll is a 404. |

## Where polls show

- Home (`hub.home.build_home_context` puts `home_poll` in the context): a Poll block only while a poll is open, last above Get the App, with no heading or empty state otherwise. It folds to one line with Hide (an answered poll starts folded; `hub/AGENTS.md`), and its See Past Polls link is Home's only way to Past Polls (#936 removed the quick link).
- Admin Tools: the Polls card in the Tools section (`tool_polls`, admins).
- A guild's page (`hub.views.guild_detail`, `guild_polls` and `show_polls_tab`): a Polls tab (`?tab=polls`) when the guild has a poll or the viewer may post one, members surface only (the guilds surface allowlists no poll view). `polls/partials/_guild_polls.html`: Ask the Guild with New Poll behind a button for those who may post, the open poll with Close Now, the past polls, and a link to Past Polls.
- Never the Spotlight (`tests/hub/spotlight_member_spec.py`, `tests/hub/home_poll_spec.py`).

Templates: `templates/polls/` (`index.html`, `admin.html`, `admin_poll.html`, `partials/_poll_card.html`, `_poll_results.html`, `_poll_written.html`, `_poll_form.html`, `_guild_polls.html`). Styles: `static/css/polls.css`.

Nothing here sends an email, push, Discord post or bell, and no page shows who picked what. A written answer shows its author only when they chose to be named.
