Spin up plfog locally the ONE canonical way, so we never do the improvise-and-fail dance again. Optionally previews a branch.

Usage: `/spin-up` (current branch) or `/spin-up feat/some-branch` (check that branch out first).

## Rules — do NOT deviate

- Run from the **primary checkout** `/home/josh/Code/plfog`. NEVER stand up a separate worktree compose stack — a fresh worktree gets an empty DB (no members, invite-only), so login breaks and no code emails send. That is the exact failure to avoid.
- Serve at **http://pastlives.test:8000** (it is in `/etc/hosts` → 127.0.0.1). Do NOT add a `localhost` override or edit `.env` — `DJANGO_ALLOWED_HOSTS` deliberately omits localhost.
- Use the persistent **`plfog_pgdata`** volume (real data, ~273 users / 613 members). Never `docker compose down -v` — that wipes it. `make db-pull-prod` refreshes from prod (heavy — only if Jo asks).
- `DEBUG` is True by default (`DJANGO_DEBUG` unset). Do not set it.

## Steps

1. Select the branch in the primary checkout. If `$ARGUMENTS` names a branch, stash any uncommitted files first, then check it out; otherwise stay put:
   ```
   cd /home/josh/Code/plfog
   git stash push -u -m "spin-up: parked before previewing $ARGUMENTS"   # only if switching and there are changes
   git checkout $ARGUMENTS                                                # only if a branch was given
   ```
2. Bring up the canonical stack (db + mailpit + web; web bind-mounts the checkout, so it serves whatever branch is checked out):
   ```
   docker compose up -d
   ```
3. Wait for the dev server, then migrate the persistent DB:
   ```
   timeout 120 bash -c 'until docker compose logs web 2>/dev/null | grep -q "Watching for file changes\|Starting development server"; do sleep 2; done'
   docker compose exec -T web python manage.py migrate --noinput
   ```
4. VERIFY before claiming it is up — all three must pass, or fix and re-check:
   ```
   curl -s -o /dev/null -w "web %{http_code}\n" http://pastlives.test:8000/                                  # 200
   docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('DEBUG', settings.DEBUG)"   # True
   curl -s -o /dev/null -w "mailpit %{http_code}\n" http://localhost:8025/                                    # 200
   ```
   If previewing a specific page, also render-check it for the logged-in member (force_login + `SERVER_NAME="pastlives.test"`) so you confirm the actual change shows, not just that the server booted.

5. Report the login steps to Jo:
   - Open **http://pastlives.test:8000/**
   - Sign in as a real member — Jo's own account is **plazajosue2@gmail.com**
   - Grab the login code from **http://localhost:8025** (mailpit)
   - Then open the page in question, e.g. `http://pastlives.test:8000/settings/?tab=notifications`

## Teardown

`docker compose down` (keeps the data volume). Never add `-v`.
