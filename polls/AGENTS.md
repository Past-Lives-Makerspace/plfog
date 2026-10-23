# polls app

Polls, built for the Spotlight (#708): an admin asks one question, members pick one answer, and it closes on a date. Since #890 the poll is not in the Spotlight (announcements took its place) and Admin Tools > Spotlight no longer posts or closes one; it keeps the Past polls table and one poll's totals (`hub_admin_spotlight_poll`). The app, its data and `/polls/` stay as history, and no page opens a new poll today (`Poll.post` and `close` have no caller).

## Models

| Model | Key fields | Notes |
|-------|-----------|-------|
| `Poll` | question, opens_at, closes_at, created_by | Open exactly while `opens_at <= now < closes_at` (`PollQuerySet.open_at` / `closed_at`), so it closes on its own and nothing runs for it; `close(now)` is Close now. `Poll.post(...)` is the one way to open a poll: it checks 2 to 6 answers and 1 to 60 days, serializes on the `SiteConfiguration` row, and refuses with `PollAlreadyOpenError` while one is open unless asked to close it first. One open poll is kept by that code, not a constraint (a time range cannot be a unique index). `with_totals()` annotates `total_votes` and `top_answer` (first listed wins a tie, None with no votes). |
| `PollChoice` | poll, text, position | Answers in the admin's order; `results()` / `tally()` give `ChoiceResult(pk, text, votes, percent)`. |
| `PollVote` | poll, choice, member | One per member per poll (`pollvote_one_per_member`). Counted only: no page shows who voted for what. |

The Spotlight read the open poll in its own query from #709 until #890; it reads nothing from this app now except `can_vote`, which still decides who sees it.

## Voting and /polls/ (part 2)

- `Poll.vote(member=, choice_pk=, now=)` is the only way a vote is cast. It refuses with a `VoteRefusedError` subclass whose message is written for the member: `NotAVoterError` (no member, a guest #691 or a former member; `can_vote`), `PollClosedError`, `AlreadyVotedError` (the one-vote constraint's IntegrityError, so a double tap or a race never 500s). It saves through `PollVote.save`, never a bulk insert, so the cross-poll guard runs.
- `PollCard` is one poll as one member sees it: the tally, their own answer and `shows_choices`. `PollCard.for_polls(polls, member, now)` builds a page of them in two queries.
- `polls.views.polls_index` (`polls:index`, `/polls/`): every poll newest first, ten a page, each through `templates/polls/partials/_poll_card.html`; members only (`can_vote`), and `/polls/` is in `MEMBER_ONLY_PATH_PREFIXES`.
- `polls.views.poll_vote` (`polls:vote`, `POST /polls/<pk>/vote/`): an `HX-Request` gets the card back to swap in place with a toast; a plain post redirects to a same-site `next`, else `/polls/`. The card takes a `variant`: "" on /polls/ (the only copy with an `id`), "spotlight" (compact) and "panel", which the Spotlight used until #890 and nothing renders now; the vote posts it back so the swapped card matches.
