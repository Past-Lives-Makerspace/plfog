#!/usr/bin/env zsh
# Deploy the staging checkout to the ref it tracks: origin/main unless deploy.env says
# otherwise. Runs as the site user from inside the checkout. A systemd timer calls it every
# ten minutes and it exits at once when the tracked ref has not moved, so the timer costs a
# git fetch and nothing else. What it runs after the checkout mirrors the production deploy,
# which is the Dockerfile CMD: migrate, seed_notification_templates, serve.
#
#   zsh deploy/staging/deploy.sh            deploy when the tracked ref moved
#   zsh deploy/staging/deploy.sh --force    redeploy the same ref (after editing .env, say)
#
# Layout it assumes (deploy/staging/README.md):
#   $SITE_DIR/app          this checkout, with .env and .venv inside it
#   $SITE_DIR/deploy.env   optional, one line: PLFOG_STAGING_REF=origin/<branch>
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
APP_DIR="${SCRIPT_DIR:h:h}"
SITE_DIR="${APP_DIR:h}"
DEPLOY_ENV="$SITE_DIR/deploy.env"
PLFOG_STAGING_REF="origin/main"
FORCE=0

log() { print -r -- "$(date -u +%Y-%m-%dT%H:%M:%SZ) deploy: $*" }

if [[ "${1:-}" == "--force" ]]; then
  FORCE=1
fi
if [[ -f "$DEPLOY_ENV" ]]; then
  source "$DEPLOY_ENV"
fi

cd "$APP_DIR"
# manage.py needs the app environment (DATABASE_URL and friends). The service reads the
# same file through EnvironmentFile, so there is exactly one place staging is configured.
set -a
source "$APP_DIR/.env"
set +a

git fetch --quiet origin
target="$(git rev-parse --verify "${PLFOG_STAGING_REF}^{commit}")"
current="$(git rev-parse HEAD)"
if [[ "$target" == "$current" && $FORCE -eq 0 ]]; then
  exit 0
fi

log "checking out $PLFOG_STAGING_REF at ${target[1,12]} (was ${current[1,12]})"
git checkout --quiet --detach "$target"
log "installing requirements"
.venv/bin/pip install --quiet --requirement requirements.txt
log "migrating"
.venv/bin/python manage.py migrate --noinput
log "collecting static files"
.venv/bin/python manage.py collectstatic --noinput --verbosity 0
log "seeding notification templates"
.venv/bin/python manage.py seed_notification_templates --quiet
log "restarting plfog-staging.service"
sudo -n systemctl restart plfog-staging.service
log "deployed ${target[1,12]}"
