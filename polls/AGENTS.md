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

## Views (`polls/views.py`, `polls/urls.py`, mounted at `/polls/`, members host only via `MEMBER_ONLY_PATH_PREFIXES`)

| URL name | Path | What |
|---|---|---|
| `polls:index` | `/polls/` | Past Polls: every closed poll, site wide and guild (each guild's labelled with a pill, `select_related("guild")`), newest first, ten a page, with counts and the member's own answer. Exists whether or not a poll is open; 403 for an account that cannot vote. |
| `polls:vote` | `/polls/<pk>/vote/` | POST only. Over HTMX it answers the card (`polls/partials/_poll_card.html`, `variant` "", "home" or "guild") with a toast; a plain post redirects to `next` (ours only), else Home. |
| `polls:admin` | `/polls/manage/` | Admin Tools > Polls (`@fog_admin_required`): the open site wide poll with Close now, the New Poll form, the past site wide polls (`prepare_table`, sortable). Guild polls are managed on their guild pages. |
| `polls:admin_new` | `/polls/manage/new/` | Post a poll (`polls.forms.NewPollForm`). Invalid or refused posts re-render the page at 200 with what was typed, because a boosted 4xx becomes a fresh GET. |
| `polls:admin_close` | `/polls/manage/<pk>/close/` | Close now. |
| `polls:admin_poll` | `/polls/manage/<pk>/` | One poll's results, counts only. |
| `polls:guild_new` | `/polls/guild/<slug>/new/` | Post a guild poll. `membership.permissions.can_edit_guild` (the guild's lead and staff, guild officers, admins) or 403. A refusal (bad form, a poll already open in the guild) comes back on the guild's Polls tab as an error message. |
| `polls:guild_close` | `/polls/guild/<slug>/<pk>/close/` | Close now on that guild's poll; same gate; another guild's poll is a 404. |

## Where polls show

- Home (`hub.home.build_home_context` puts `home_poll` in the context): a Poll block only while a poll is open, with no heading or empty state otherwise. The Past Polls quick link is always there, and the block links there too.
- Admin Tools: the Polls card in the Tools section (`tool_polls`, admins).
- A guild's page (`hub.views.guild_detail`, `guild_polls` and `show_polls_tab`): a Polls tab (`?tab=polls`) when the guild has a poll or the viewer may post one, members surface only (the guilds surface allowlists no poll view). `polls/partials/_guild_polls.html`: Ask the Guild with New Poll behind a button for those who may post, the open poll with Close Now, the past polls, and a link to Past Polls.
- Never the Spotlight (`tests/hub/spotlight_member_spec.py`, `tests/hub/home_poll_spec.py`).

Templates: `templates/polls/` (`index.html`, `admin.html`, `admin_poll.html`, `partials/_poll_card.html`, `_poll_results.html`, `_poll_written.html`, `_poll_form.html`, `_guild_polls.html`). Styles: `static/css/polls.css`.

Nothing here sends an email, push, Discord post or bell, and no page shows who picked what. A written answer shows its author only when they chose to be named.
