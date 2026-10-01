# Screenshots and mockups

Every pull request that changes what members see (anything under `templates/`, `static/css/` or `static/js/`) adds at least one image here and shows it in its description. CI checks this; see `CONTRIBUTING.md`.

- Name files `<issue-number>-<short-slug>-<nn>.png`, for example `412-review-reason-01.png`. `.png`, `.jpg`, `.gif`, `.webp` and `.svg` all count.
- A mockup is fine before the screen exists; a screenshot of the real page is better once it does.
- Embed it in the PR description by its raw URL on the branch: `![Review reason](https://raw.githubusercontent.com/Past-Lives-Makerspace/plfog/<branch>/mockups/screenshots/412-review-reason-01.png)`. A relative link such as `mockups/screenshots/412-review-reason-01.png` works in the repo's own Markdown but shows a broken image in a PR description, and `pr-description.yml` fails it.

This folder is cleaned out from time to time, so nothing may link here from the app or the docs. It is not shipped in the Docker image.
