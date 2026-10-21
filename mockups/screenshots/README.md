# Screenshots

Every pull request that changes what members see (anything under `templates/`, `static/css/` or `static/js/`) shows at least one screenshot in its description. CI checks this; see `CONTRIBUTING.md`.

Images are not committed. Save them here while you work (this folder is gitignored apart from this README), then upload them:

    .venv/bin/python scripts/upload_screenshot.py mockups/screenshots/412-review-reason-01.png

It puts each file on the R2 media bucket under `pr-screenshots/<date>/` and prints the Markdown to paste into the description. It never overwrites, so a name clash means a new file name. Without the R2 keys (an outside contributor), drag the image into the PR description instead and GitHub hosts it.

- Name files `<issue-number>-<short-slug>-<nn>.png`, for example `412-review-reason-01.png`.
- A mockup is fine before the screen exists; a screenshot of the real page is better once it does.
- R2 deletes everything under `pr-screenshots/` 30 days after upload, so a PR's images stop showing a month later. Nothing in the app or the docs may link to them.
