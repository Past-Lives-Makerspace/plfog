# polls app

Polls for the Spotlight (#708): an admin asks one question, members pick one answer, and it closes on a date. Posted and closed from Admin Tools > Spotlight (`hub/spotlight_views.py`); members vote and browse past polls in part 2 of #708; the member Spotlight is #709.

## Models

| Model | Key fields | Notes |
|-------|-----------|-------|
| `Poll` | question, opens_at, closes_at, created_by | Open exactly while `opens_at <= now < closes_at` (`PollQuerySet.open_at` / `closed_at`), so it closes on its own and nothing runs for it; `close(now)` is Close now. `Poll.post(...)` is the one way to open a poll: it checks 2 to 6 answers and 1 to 60 days, serializes on the `SiteConfiguration` row, and refuses with `PollAlreadyOpenError` while one is open unless asked to close it first. One open poll is kept by that code, not a constraint (a time range cannot be a unique index). `with_totals()` annotates `total_votes` and `top_answer` (first listed wins a tie, None with no votes). |
| `PollChoice` | poll, text, position | Answers in the admin's order; `results()` / `tally()` give `ChoiceResult(pk, text, votes, percent)`. |
| `PollVote` | poll, choice, member | One per member per poll (`pollvote_one_per_member`). Counted only: no page shows who voted for what. |

`open_poll_with_results(now)` reads the open poll and its tally in one query from the answer side, because the Spotlight renders on every hub page.
