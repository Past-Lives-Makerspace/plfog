# Staging

`staging.pastlives.space` is a clone of production for practice and QA: the same code, a
copy of production's data, and real outbound email, contained so nothing there reaches a
real member, the real Discord server, Mailchimp or Stripe. It runs on the Hetzner VPS, not
on Render, so Render's crons, previews and release announcement do not apply to it.

## What runs where on the box

| Piece | Where |
|---|---|
| Site user | `staging_pastlives_space` (no sudo beyond the one restart below) |
| Checkout | `/var/www/staging.pastlives.space/app` (this repository, detached at the tracked ref, `.venv` and `.env` inside) |
| Logs and media | `/var/www/staging.pastlives.space/logs`, `/var/www/staging.pastlives.space/media` (local `FileSystemStorage`; the R2 variables are unset) |
| Service | `plfog-staging.service`: gunicorn on a unix socket in the site directory, `EnvironmentFile` = the app `.env` |
| Deploy timer | `plfog-staging-deploy.timer`, every ten minutes, runs `deploy/staging/deploy.sh` as the site user |
| Database | PostgreSQL 18 on localhost only (the same major as Render), database and role `plfog`, owner of its schema |
| Web | nginx vhost for `staging.pastlives.space` and `book.staging.pastlives.space`, Let's Encrypt per host, `/static/` and `/media/` served from the site directory |
| sudo | one rule: the site user may run `systemctl restart plfog-staging.service` and nothing else |

## Environment

The app `.env` is the single place staging is configured; the service and the scripts both
read it. What it sets and why:

| Variable | Value | Why |
|---|---|---|
| `ENVIRONMENT` | `staging` | The one switch behind every containment rule below (`settings.IS_STAGING`). |
| `DJANGO_DEBUG` | `False` | Staging behaves like production, not like a laptop. |
| `DATABASE_URL` | `postgres://plfog:...@localhost:5432/plfog` | Local Postgres; `refresh-db.sh` refuses any other host. |
| `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | both staging hosts | `core.E002` stops a boot whose public host is not allowed. |
| `MEMBER_HOST`, `MEMBER_BASE_URL` | `staging.pastlives.space` | Every member link in emails and cross-surface buttons. |
| `PUBLIC_HOSTS`, `BOOK_BASE_URL` | `book.staging.pastlives.space` | The public catalog surface and the links pointing at it. |
| `COOKIE_DOMAIN` | `.staging.pastlives.space` | One login across the two staging hosts, like production's `.pastlives.space`. |
| `EMAIL_BACKEND`, `RESEND_API_KEY`, `DEFAULT_FROM_EMAIL` | Resend, the production key, `Past Lives STAGING <noreply@pastlives.space>` | Real delivery for the people practising; the display name marks it before the subject does. |
| `EMAIL_DELIVERY_ALLOWLIST` | addresses and domains that may receive mail | Roles cover staff, admins and instructors; this covers everyone else who should, for example a tester without a role. |
| `WEBPUSH_VAPID_*` | a fresh key pair, not production's | Browser push can be tried on staging without a copied subscription ever being reachable. |
| `STRIPE_FIELD_ENCRYPTION_KEY` | a fresh key, not production's | Production's stored Stripe secrets then read back blank, so billing is dark until test keys are entered in the admin. |
| unset on purpose | `DISCORD_*`, `FCM_SERVICE_ACCOUNT_JSON`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_CALENDAR_SYNC_ENABLED`, `AIRTABLE_*`, `R2_*`, `OIDC_RSA_PRIVATE_KEY`, `MAILCHIMP_*` | Each integration is dark when its credential is blank. |

`deploy.env`, one directory above the checkout, is optional and holds one line,
`PLFOG_STAGING_REF=origin/<branch>`, when staging should run a PR branch instead of `main`.

## Containment: what is off and how it is enforced

Blank credentials alone are not enough, because the copied database carries production's
Site Settings, routing rows and guild webhooks. So `ENVIRONMENT=staging` also switches these
off in code, and `scrub.sql` blanks the same rows after every refresh:

| What | Enforced by |
|---|---|
| Email to anyone but staff, admins, instructors and the allowlist | `core/email.py` through `core/email_policy.py`; every dropped address is a `TransactionalEmailLog` row with status `suppressed` |
| The `[STAGING]` subject prefix, the first line of every text body, the banner atop every HTML body | `core/email_policy.py` |
| Discord webhooks: global, per event routes, Site Settings pins, guild channels, the announcement picker | `core/events/discord.py` resolvers answer blank, `post_embed` refuses a URL it is handed, `membership.models.resolve_channel_webhook` answers blank |
| Discord bot calls: DMs, roles, reactions, channel posts, Scheduled Events, the guild sync, interaction callbacks | `core/events/discord_dm.bot_token` answers blank and every bot module asks `bot_disabled` first, so each entry point is a logged no-op before a request is built |
| Mailchimp subscribes | `MailchimpClient.from_site_config` returns a disabled client before reading the database |
| Push to copied browsers and phones | `scrub.sql` deletes the subscription and device rows; staging's VAPID keys differ from production's; no FCM credential |
| Calendar, Airtable, the KB single sign-on, R2 | credentials unset |
| Stripe | production's encrypted secrets are unreadable under staging's key, and `scrub.sql` forces `BillingSettings.test_mode` on so any key entered later lands in the test slot; the test slots are the way to try payments |
| Invite and login-invite links | `scrub.sql` points the sites framework row at `staging.pastlives.space` |
| Hand-typed production links in edited notification copy | `scrub.sql` rewrites the two production hosts in `core_notificationtemplate` |
| Every page | the fixed STAGING ribbon (`components/staging_ribbon.html`) |

Verify containment by behaviour, not by reading this table: send yourself a login code from
staging and check the subject, the banner and that the Email Log shows `suppressed` rows for
a member who is not on the list; publish a class and confirm the real Discord stays quiet.

## Refreshing the data from production

From a shell on the box as the site user, with the production connection string pasted for
that one command and nowhere else:

```
cd /var/www/staging.pastlives.space/app
PROD_DATABASE_URL='postgres://...' PROD_STRIPE_FIELD_ENCRYPTION_KEY='...' zsh deploy/staging/refresh-db.sh
```

It dumps production, drops and recreates the staging schema, restores, applies `scrub.sql`,
runs migrations, imports the Stripe test keys (below) and prints the user and member counts.
The scrub is idempotent, so it is safe to apply again by hand. Refresh on demand only;
nothing schedules it.

### Stripe test keys after a refresh

Production's Payments settings carry Stripe **test-mode** credentials in their test slot. The
two secret ones are Fernet ciphertext under production's `STRIPE_FIELD_ENCRYPTION_KEY`, so
after a refresh staging reads them back blank (its own key cannot decrypt them). The
`staging_import_stripe_test_keys` command fixes that without the production key ever living
on the box: with `PROD_STRIPE_FIELD_ENCRYPTION_KEY` in the operator's shell for one run, it
reads the raw ciphertext of `test_connect_platform_secret_key` and
`test_connect_platform_webhook_secret` through a cursor, decrypts each with production's key,
writes the plaintexts back through the model (re-encrypted under the staging key), blanks the
two live-slot secrets (live credentials never exist on staging), forces `test_mode` on and
prints one masked line per field. It refuses outside `ENVIRONMENT=staging`, refuses without
the key, and writes nothing when a value does not decrypt.

`refresh-db.sh` runs it when the key is in the shell and says so when it is not. By hand:

```
cd /var/www/staging.pastlives.space/app
PROD_STRIPE_FIELD_ENCRYPTION_KEY='...' .venv/bin/python manage.py staging_import_stripe_test_keys
```

**Webhook caveat.** Production has no test-mode webhook secret, so the import leaves that
slot blank and payment confirmations on staging will not arrive until a Stripe test-mode
webhook endpoint exists for `https://staging.pastlives.space/billing/webhooks/stripe/` (route
`billing_stripe_webhook`) and its signing secret is pasted into staging's Payments settings,
test slot. Every refresh re-imports production's blank value, so paste it again after each one.

## Running a PR branch, and going back to main

To put a branch on staging before it merges:

```
print 'PLFOG_STAGING_REF=origin/feat/my-branch' > /var/www/staging.pastlives.space/deploy.env
zsh /var/www/staging.pastlives.space/app/deploy/staging/deploy.sh
```

After the merge, remove `deploy.env` (or set the ref back to `origin/main`) and run the
deploy script once; the timer would pick it up within ten minutes anyway.

## What does not run on staging

- **Scheduled tasks.** There is no `run_scheduled_tasks` timer, so nothing that production's
  15-minute dispatcher does happens on its own: scheduled publishes, class and event
  reminders, hold releases, orientation slot generation, digests. Run the one you need by
  hand, for example `python manage.py publish_due_events` to make a scheduled class go live.
- **The Airtable pull, the calendar sync, the legacy CMS sync.** Credentials are unset and the
  legacy sync toggle is scrubbed off.
- **Render's PR previews and the release announcement.** Those are Render and GitHub Actions;
  staging deploys from the box's own timer.
