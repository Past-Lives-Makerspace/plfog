# Screenshots and mockups

Every pull request that changes what members see (anything under `templates/`, `static/css/` or `static/js/`) adds at least one image here and shows it in its description. CI checks this; see `CONTRIBUTING.md`.

- Name files `<issue-number>-<short-slug>-<nn>.png`, for example `412-review-reason-01.png`. `.png`, `.jpg`, `.gif`, `.webp` and `.svg` all count.
- A mockup is fine before the screen exists; a screenshot of the real page is better once it does.
- Embed it in the PR description by its raw URL at the commit that added it (`git rev-parse HEAD` after you push): `![Review reason](https://raw.githubusercontent.com/Past-Lives-Makerspace/plfog/<commit>/mockups/screenshots/412-review-reason-01.png)`. Not the branch name, which is deleted on merge, and not `main`, because this folder is pruned monthly; a commit link outlives both. A relative link such as `mockups/screenshots/412-review-reason-01.png` works in the repo's own Markdown but shows a broken image in a PR description, and `pr-description.yml` fails it.

Once a month `prune-screenshots.yml` opens a PR that removes images untouched for 30 days, so nothing may link here from the app or the docs. It is not shipped in the Docker image.
