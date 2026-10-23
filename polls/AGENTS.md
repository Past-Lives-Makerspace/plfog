# polls app

Removed on 2026-10-23 (#890, Felix: "lets just remove polls for now. if we ever want it back, we can rebuild it."). Polls were the Spotlight's weekly question (#708, #709); announcements took their place in the Spotlight, and the `/polls/` pages, the vote endpoint, Admin Tools > Spotlight's poll posting, Close now, Past polls table and results page, the templates, the CSS and the specs are gone.

What is left is deliberate and temporary:

- `polls` stays in `INSTALLED_APPS`, and `polls/models.py` keeps `Poll`, `PollChoice` and `PollVote` as bare models (fields, `Meta` and constraints exactly as `0001_initial`, no methods), with every migration, so the tables survive while the previous release still reads them during the deploy (STANDARDS.md section 10). Nothing reads them.
- A follow-up PR, after this one is live, deletes the models with a migration that drops the three tables, then removes the app.

Who sees the Spotlight was the app's `can_vote`; that rule now lives in `hub.spotlight.sees_spotlight`, unchanged. Do not add code here.
