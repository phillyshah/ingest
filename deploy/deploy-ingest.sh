#!/usr/bin/env bash
# deploy-ingest — pull the latest code and restart the MoveAI ingestion engine on this server.
#
# Installed to /usr/local/bin/deploy-ingest by deploy/install.sh. Run it after merging a change:
#
#     sudo deploy-ingest              # deploy the latest main
#     sudo deploy-ingest some-branch  # deploy a specific branch
#     sudo deploy-ingest --logs       # deploy, then tail the logs
#
# Scope: this touches /opt/sites/ingest and its four containers, nothing else. Traefik, the shared `proxy`
# network and the other eight sites on this machine are never restarted, reconfigured, or stopped.
#
# Configuration (infra/.env) is never overwritten: credentials survive every deploy. Use install.sh if you need
# to change them.

set -euo pipefail

DIR="${DIR:-/opt/sites/ingest}"
COMPOSE="docker compose -f infra/docker-compose.prod.yml"
BRANCH="main"
TAIL_LOGS=0

for arg in "$@"; do
  case "$arg" in
    --logs) TAIL_LOGS=1 ;;
    --help|-h) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) BRANCH="$arg" ;;
  esac
done

die()  { printf '\nDEPLOY FAILED: %s\n' "$*" >&2; exit 1; }
step() { printf '\n== %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || die "run this with sudo."
[ -d "$DIR/.git" ] || die "$DIR is not set up yet. Run deploy/install.sh first."
[ -f "$DIR/infra/.env" ] || die "$DIR/infra/.env is missing. Run deploy/install.sh first."
cd "$DIR"

# Record where we are, so a failed deploy can be rolled back to a commit known to have run.
PREVIOUS=$(git rev-parse HEAD)

step "Fetching $BRANCH"
git fetch --depth 1 origin "$BRANCH" || die "could not reach GitHub."
# A deploy checkout is disposable and must match the remote exactly; any local edit here is a mistake, not work.
git checkout -q -B "$BRANCH" "origin/$BRANCH"
NEW=$(git rev-parse HEAD)

if [ "$PREVIOUS" = "$NEW" ]; then
  echo "  already at $(git rev-parse --short HEAD) — rebuilding anyway in case the configuration changed."
else
  echo "  $(git rev-parse --short "$PREVIOUS") -> $(git rev-parse --short "$NEW")"
  git --no-pager log --oneline "$PREVIOUS..$NEW" | sed 's/^/    /' || true
fi

step "Building"
GIT_SHA=$(git rev-parse --short HEAD) BUILT_AT=$(date -u '+%Y-%m-%dT%H:%M:%SZ') $COMPOSE build || die "the build failed. Nothing was restarted; the previous version is still serving."

# The database and the application are deployed separately, and nothing used to compare them. Deploying code
# whose migrations had not been applied took the site down with 500s that surfaced in the browser as an unrelated
# JSON parse error. Check before restarting: a failure here leaves the running version serving.
step "Checking the database has the migrations this build needs"
$COMPOSE run --rm --no-deps -T api /app/.venv/bin/python scripts/check_migrations.py </dev/null \
  || die "the database is behind this build. Nothing was restarted; the previous version is still serving."

step "Restarting"
# --force-recreate because `up -d` does not reliably recreate a container when only the contents of the env file
# changed. Editing infra/.env and redeploying is the normal way to change configuration, and silently keeping the
# old values is worse than the second of rebuild time this costs.
$COMPOSE up -d --remove-orphans --force-recreate

step "Checking health"
ok=0
for _ in $(seq 1 24); do
  # `</dev/null`: `exec -T` otherwise inherits this script's stdin and consumes it. Harmless when run from a file,
  # fatal when piped, and there is no reason to leave the difference lying around.
  if $COMPOSE exec -T api python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/v1/healthz')" </dev/null >/dev/null 2>&1; then
    ok=1; break
  fi
  sleep 5
done

if [ "$ok" -ne 1 ]; then
  echo
  echo "  The API did not come up healthy. Last 40 lines:"
  $COMPOSE logs --tail 40 api | sed 's/^/    /'
  echo
  echo "  To go back to the version that was running before this deploy:"
  echo "    cd $DIR && sudo git checkout -q $PREVIOUS && sudo $COMPOSE up -d --build"
  die "deploy finished but the API is unhealthy."
fi
echo "  API healthy."

step "Status"
$COMPOSE ps

# Images accumulate fast with a rebuild every deploy, and this host has 58G free but no swap and other sites to
# protect. Only dangling layers are removed: never a named image another site might be using.
step "Tidying unused image layers"
docker image prune -f >/dev/null 2>&1 || true

HOST=$(grep -E '^INGEST_HOST=' infra/.env | cut -d= -f2- || true)
printf '\nDeployed %s at %s\n' "$(git rev-parse --short HEAD)" "${HOST:-the configured hostname}"

if [ "$TAIL_LOGS" -eq 1 ]; then
  step "Logs (ctrl-c to stop)"
  $COMPOSE logs -f --tail 50
fi
