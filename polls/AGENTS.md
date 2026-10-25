# polls app

Polls (#708) were removed in #890 and came back in #925, on Home and (part 2) on guild pages, never in the Spotlight. Restored from git before `ff485764` rather than rewritten.

## Models (`polls/models.py`)

| Model | Notes |
|---|---|
| `Poll` | question, opens_at, closes_at, created_by, created_at. Open exactly while `opens_at <= now < closes_at`, so it closes on its own; `close(now)` is Close now. `Poll.post(question, choices, days, by, now)` validates (question 1 to 200 characters, 2 to 6 answers of at most 120, 1 to 60 days), serializes on the `SiteConfiguration` row with `select_for_update`, and raises `PollAlreadyOpenError` (its message is the open poll's question) while any poll is open. There is no "close it and post" path: Close now first. `results()` is the tally in one query. `vote(member, choice_pk, now)` is the only way a vote is cast and raises the `VoteRefusedError` family (`NotAVoterError`, `PollClosedError`, `AlreadyVotedError`); the one-vote constraint's race becomes `AlreadyVotedError`. |
| `PollChoice` | poll, text, position; unique per poll and position. |
| `PollVote` | poll, choice, member, created_at; one per member and poll (`pollvote_one_per_member`). `save` takes the poll from the choice and refuses a choice from another poll. |

`PollQuerySet`: `open_at(now)`, `closed_at(now)`, `open_poll(now)` (the one open poll or None, one query), `with_totals()` (`total_votes`, `top_answer`, a tie going to the answer listed first).

`can_vote(member)` is `hub.spotlight.sees_spotlight`: a member that is not a guest (#691) or former member. The lockout middleware keeps guests, former and suspended members off the hub, so the read only card for them is proven on the partial (`tests/polls/voting_spec.py`).

`PollCard` is one poll as one member sees it (tally, their own answer, `shows_choices`). `for_polls` builds a page in two queries; `open_for(member, now)` is Home's card, one query with no poll open and three with one.

## Views (`polls/views.py`, `polls/urls.py`, mounted at `/polls/`, members host only via `MEMBER_ONLY_PATH_PREFIXES`)

| URL name | Path | What |
|---|---|---|
| `polls:index` | `/polls/` | Past Polls: every closed poll, newest first, ten a page, with counts and the member's own answer. Exists whether or not a poll is open; 403 for an account that cannot vote. |
| `polls:vote` | `/polls/<pk>/vote/` | POST only. Over HTMX it answers the card (`polls/partials/_poll_card.html`, `variant` "" or "home") with a toast; a plain post redirects to `next` (ours only), else Home. |
| `polls:admin` | `/polls/manage/` | Admin Tools > Polls (`@fog_admin_required`): the open poll with Close now, the New Poll form, the Past Polls table (`prepare_table`, sortable). |
| `polls:admin_new` | `/polls/manage/new/` | Post a poll from the New Poll composer (`polls.forms.NewPollForm`, `polls/partials/_poll_form.html`, the approved `mockups/925-poll-composer.html`: the composer is the poll card, Runs for is one of the five `RunLength` chips, its Closes line uses `closes_label`, the same words the server writes). Invalid or refused posts re-render the page at 200 with what was typed, because a boosted 4xx becomes a fresh GET. |
| `polls:admin_close` | `/polls/manage/<pk>/close/` | Close now. |
| `polls:admin_poll` | `/polls/manage/<pk>/` | One poll's results, counts only. |

## Where polls show

- Home (`hub.home.build_home_context` puts `home_poll` in the context): a Poll block only while a poll is open, with no heading or empty state otherwise. The Past Polls quick link is always there, and the block links there too.
- Admin Tools: the Polls card in the Tools section (`tool_polls`, admins).
- Never the Spotlight (`tests/hub/spotlight_member_spec.py`, `tests/hub/home_poll_spec.py`).

Templates: `templates/polls/` (`index.html`, `admin.html`, `admin_poll.html`, `partials/_poll_card.html`, `_poll_results.html`, `_poll_form.html`). Styles: `static/css/polls.css`.

Nothing here sends an email, push, Discord post or bell, and no page shows who voted for what.
