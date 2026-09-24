#!/usr/bin/env zsh
# Replace the staging database with a fresh copy of production, then scrub it (scrub.sql) so
# nothing in the copy can reach a real member, the real Discord server or Mailchimp.
#
#   PROD_DATABASE_URL='postgres://...' PROD_STRIPE_FIELD_ENCRYPTION_KEY='...' zsh deploy/staging/refresh-db.sh
#
# PROD_DATABASE_URL (and, optionally, PROD_STRIPE_FIELD_ENCRYPTION_KEY, which lets the
# Stripe test keys come across) come from the caller's shell for the one run and are never
# written to the box. DATABASE_URL (the staging target) is read from the app .env. The script refuses to run
# unless that target is on localhost and the app is configured as staging, so it cannot be
# pointed at production by accident. It needs pg_dump, pg_restore and psql of the same major
# version as production (18) on the PATH.
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
APP_DIR="${SCRIPT_DIR:h:h}"
ENV_FILE="$APP_DIR/.env"

log() { print -r -- "$(date -u +%Y-%m-%dT%H:%M:%SZ) refresh-db: $*" }
die() { print -r -- "refresh-db: $*" >&2; exit 1 }

[[ -n "${PROD_DATABASE_URL:-}" ]] || die "PROD_DATABASE_URL is not set in this shell."
[[ -f "$ENV_FILE" ]] || die "no app .env at $ENV_FILE."
grep -q '^ENVIRONMENT=staging$' "$ENV_FILE" || die "$ENV_FILE does not set ENVIRONMENT=staging; refusing."
if grep -q '^PROD_DATABASE_URL=' "$ENV_FILE"; then
  die "$ENV_FILE stores PROD_DATABASE_URL; it must never live on the box."
fi
if grep -q '^PROD_STRIPE_FIELD_ENCRYPTION_KEY=' "$ENV_FILE"; then
  die "$ENV_FILE stores PROD_STRIPE_FIELD_ENCRYPTION_KEY; it must never live on the box."
fi

set -a
source "$ENV_FILE"
set +a
[[ -n "${DATABASE_URL:-}" ]] || die "DATABASE_URL is not set in $ENV_FILE."
if [[ "$DATABASE_URL" != *@localhost[:/]* && "$DATABASE_URL" != *@127.0.0.1[:/]* ]]; then
  die "DATABASE_URL does not point at localhost; refusing."
fi
[[ "$PROD_DATABASE_URL" != "$DATABASE_URL" ]] || die "PROD_DATABASE_URL equals DATABASE_URL; refusing."

dump="$(mktemp --tmpdir plfog-prod.XXXXXX)"
trap 'rm -f "$dump"' EXIT

log "dumping production"
pg_dump --no-owner --no-acl --format=custom --file="$dump" "$PROD_DATABASE_URL"
log "resetting the staging schema"
psql "$DATABASE_URL" --quiet --set=ON_ERROR_STOP=1 --command='DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
log "restoring"
pg_restore --no-owner --no-acl --exit-on-error --dbname="$DATABASE_URL" "$dump"
log "scrubbing"
psql "$DATABASE_URL" --quiet --set=ON_ERROR_STOP=1 --file="$SCRIPT_DIR/scrub.sql"
log "migrating"
(cd "$APP_DIR" && .venv/bin/python manage.py migrate --noinput)
# Production's Stripe TEST keys are ciphertext this box's key cannot read. With production's
# Fernet key in the operator's shell for this one run, the command re-encrypts them under the
# staging key; without it, staging's Payments settings start with blank test slots.
if [[ -n "${PROD_STRIPE_FIELD_ENCRYPTION_KEY:-}" ]]; then
  log "importing production's Stripe test keys"
  (cd "$APP_DIR" && .venv/bin/python manage.py staging_import_stripe_test_keys)
else
  log "Stripe test keys not imported: PROD_STRIPE_FIELD_ENCRYPTION_KEY is not set in this shell"
fi
log "done"
psql "$DATABASE_URL" --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  --command="SELECT 'users: ' || count(*) FROM auth_user UNION ALL SELECT 'members: ' || count(*) FROM membership_member;"
