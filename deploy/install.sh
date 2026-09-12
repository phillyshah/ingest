#!/usr/bin/env bash
# MoveAI ingestion engine — install on srv1373951.
#
# Written against the two server inspections, not against assumptions. That host runs Traefik v3.6 as its public
# front door with eight other sites behind it, so this script is additive only:
#
#   * It never touches /opt/traefik, the `proxy` network, or any other site's files.
#   * It publishes no ports; Traefik routes to the containers over `proxy`.
#   * It writes only under /opt/sites/ingest, plus nothing else on the machine.
#   * `docker compose down` here cannot remove the shared network, which is declared external.
#
# Safe to run again: it updates the checkout, keeps the existing .env unless told otherwise, and rebuilds.
#
# Run:  curl -fsSL https://raw.githubusercontent.com/phillyshah/ingest/main/deploy/install.sh | sudo bash

set -euo pipefail

REPO="${REPO:-https://github.com/phillyshah/ingest.git}"
BRANCH="${BRANCH:-main}"
DIR="${DIR:-/opt/sites/ingest}"
HOSTNAME_DEFAULT="${INGEST_HOST:-ingest.phillyshah.com}"
COMPOSE="docker compose -f infra/docker-compose.prod.yml"

# Prompts must read from the terminal: this script is normally run through a pipe, so stdin is the script itself.
TTY=/dev/tty
ask() { # ask VAR "prompt" ["default"]
  local __var=$1 __prompt=$2 __default=${3:-} __reply=""
  if [ -n "$__default" ]; then printf '%s [%s]: ' "$__prompt" "$__default" >"$TTY"; else printf '%s: ' "$__prompt" >"$TTY"; fi
  IFS= read -r __reply <"$TTY" || true
  [ -z "$__reply" ] && __reply=$__default
  printf -v "$__var" '%s' "$__reply"
}
ask_secret() { # never echoed, never logged
  local __var=$1 __prompt=$2 __reply=""
  printf '%s: ' "$__prompt" >"$TTY"
  stty -echo <"$TTY" 2>/dev/null || true
  IFS= read -r __reply <"$TTY" || true
  stty echo <"$TTY" 2>/dev/null || true
  printf '\n' >"$TTY"
  printf -v "$__var" '%s' "$__reply"
}
die()  { printf '\nSTOPPED: %s\n' "$*" >&2; exit 1; }
step() { printf '\n== %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || die "run this with sudo."

step "Checking what this machine already has"
command -v docker >/dev/null || die "docker is not installed."
docker compose version >/dev/null 2>&1 || die "the docker compose plugin is not available."
docker network inspect proxy >/dev/null 2>&1 \
  || die "the docker network 'proxy' does not exist. That network belongs to Traefik in /opt/traefik; this script will not create it. Check that Traefik is running."
docker ps --format '{{.Names}}' | grep -qi traefik \
  || die "Traefik is not running. Start it before deploying, or certificates cannot be issued."
echo "  docker, compose, traefik and the proxy network are all present."

step "Where to publish"
ask INGEST_HOST "Hostname for this application" "$HOSTNAME_DEFAULT"
[ -n "$INGEST_HOST" ] || die "a hostname is required."

# Certificates here are issued by HTTP challenge, which means Let's Encrypt must reach this machine on port 80 at
# the hostname. If DNS is not in place yet, starting now burns a failed challenge and serves a browser warning, so
# check first and say exactly what to do.
this_v4=$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)
res_v4=$(getent ahostsv4 "$INGEST_HOST" 2>/dev/null | awk '{print $1}' | head -1 || true)
if [ -z "$res_v4" ]; then
  die "$INGEST_HOST does not resolve yet.
  Add a DNS A record for it pointing at this server, wait a few minutes, then run this again.
  Certificates are issued by HTTP challenge, so the name must resolve before the container starts."
fi
if [ -n "$this_v4" ] && [ "$res_v4" != "$this_v4" ]; then
  echo "  WARNING: $INGEST_HOST resolves to $res_v4 but this server appears to be $this_v4."
  echo "  If that is a proxy in front of the server this is fine; otherwise the certificate will fail."
  ask CONT "Continue anyway? (yes/no)" "no"
  [ "$CONT" = "yes" ] || die "stopped at your request; nothing was changed."
else
  echo "  $INGEST_HOST -> $res_v4 (matches this server)."
fi

step "Fetching the application to $DIR"
mkdir -p "$(dirname "$DIR")"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" remote set-url origin "$REPO"
  git -C "$DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$DIR" checkout -q -B "$BRANCH" "origin/$BRANCH"
  echo "  updated existing checkout to $(git -C "$DIR" rev-parse --short HEAD)"
else
  git clone --depth 1 --branch "$BRANCH" "$REPO" "$DIR"
  echo "  cloned at $(git -C "$DIR" rev-parse --short HEAD)"
fi
cd "$DIR"

ENVFILE="$DIR/infra/.env"   # beside the compose file: that is where `env_file: .env` resolves
step "Configuration"
if [ -f "$ENVFILE" ]; then
  echo "  $ENVFILE already exists and will be kept."
  ask REDO "Replace it and re-enter everything? (yes/no)" "no"
else
  REDO=yes
fi

if [ "$REDO" = "yes" ]; then
  echo
  echo "  Two things are needed. Neither is stored anywhere but this server."
  echo
  echo "  1. The Supabase connection string — Supabase dashboard, Project Settings, Database,"
  echo "     Connection string, Session pooler. It starts with postgresql:// and contains your password."
  ask_secret DB_URL "     Paste it here (it will not be shown as you type)"
  [ -n "$DB_URL" ] || die "the connection string is required."
  case "$DB_URL" in postgresql://*|postgres://*) ;; *) die "that does not look like a connection string; it should start with postgresql://" ;; esac

  echo
  echo "  2. A username and password to open the site. The whole site sits behind this one prompt,"
  echo "     because the application does not have its own sign-in yet."
  ask BA_USER "     Username" "moveai"
  ask_secret BA_PASS "     Password (not shown as you type)"
  [ -n "$BA_PASS" ] || die "a password is required; the site must not be left open."

  # Hash locally. The plaintext never reaches a file, the environment, or a docker label.
  if command -v htpasswd >/dev/null 2>&1; then
    BA_LINE=$(htpasswd -nbB "$BA_USER" "$BA_PASS")
  elif command -v openssl >/dev/null 2>&1; then
    BA_LINE="$BA_USER:$(openssl passwd -apr1 "$BA_PASS")"
  else
    die "neither htpasswd nor openssl is available to hash the password."
  fi
  unset BA_PASS
  # Compose interpolates env-file values, so every '$' in the hash must be doubled or the hash is silently
  # mangled into a blank string and the login prompt breaks. Caught by `docker compose config`, not by reading.
  BA_LINE_ESCAPED=${BA_LINE//$/$$}

  SIGNING=$(openssl rand -hex 32)

  umask 077
  cat > "$ENVFILE" <<EOF
# Written by deploy/install.sh on $(date -u '+%Y-%m-%d %H:%M:%S UTC'). Mode 600, root-owned. Never commit this.
INGEST_HOST=$INGEST_HOST
APP_ORIGIN=https://$INGEST_HOST
API_ROOT_PATH=/api

# Declared staging, not production, and that is accurate: the reviewer UI still uses the development header
# shim, so there is no per-person identity. The API refuses the shim outright when MOVEAI_ENV=production.
MOVEAI_ENV=staging
AUTH_MODE=shim

DATABASE_URL=$DB_URL
PLAN_SIGNING_SECRET=$SIGNING

# Traefik basic-auth credential (bcrypt or apr1 hash; the plaintext was never written down).
BASIC_AUTH_USERS=$BA_LINE_ESCAPED

# Extraction runs the deterministic mock until a key is set here. With the mock, ingesting any document returns
# fixture output rather than anything read from that document — see the runbook before reading results as real.
EXTRACTION_MODEL=mock-1
ANTHROPIC_API_KEY=

WORKER_POLL_SECONDS=2
WORKER_CONCURRENCY=1
MAX_DOCUMENT_BYTES=26214400
MAX_GRAPHIC_BYTES=2097152
ALLOWED_FETCH_DOMAINS=
ALLOWED_FILE_ROOTS=/srv/moveai/uploads
TENANT_DAILY_USD_CAP=25
TENANT_DAILY_FETCH_CAP=500
EOF
  chmod 600 "$ENVFILE"
  unset DB_URL
  echo "  wrote $ENVFILE (mode 600)"
else
  grep -q '^INGEST_HOST=' "$ENVFILE" || printf 'INGEST_HOST=%s\n' "$INGEST_HOST" >> "$ENVFILE"
fi

step "Building (a few minutes on first run)"
$COMPOSE build

step "Starting"
$COMPOSE up -d

step "Installing the deploy-ingest command"
install -m 0755 "$DIR/deploy/deploy-ingest.sh" /usr/local/bin/deploy-ingest
echo "  from now on, 'sudo deploy-ingest' pulls the latest code and restarts this app."

step "Checking it came up"
sleep 5
$COMPOSE ps
echo
for i in $(seq 1 24); do
  if $COMPOSE exec -T api python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/v1/healthz')" >/dev/null 2>&1; then
    echo "  the API is healthy inside its container."
    break
  fi
  [ "$i" = 24 ] && echo "  WARNING: the API did not become healthy. Run: cd $DIR && $COMPOSE logs api"
  sleep 5
done

step "Checking the site is protected"
# A bcrypt hash contains '$', which compose will silently eat if it is not escaped. Rather than trust that,
# ask Traefik: an unauthenticated request must come back 401 with a basic-auth challenge.
probe=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 --resolve "$INGEST_HOST:443:127.0.0.1" -k "https://$INGEST_HOST/" 2>/dev/null || true)
challenge=$(curl -s -I --max-time 15 --resolve "$INGEST_HOST:443:127.0.0.1" -k "https://$INGEST_HOST/" 2>/dev/null | grep -ci '^www-authenticate' || true)
if [ "$probe" = "401" ] && [ "$challenge" -ge 1 ]; then
  echo "  the site answers 401 with a password prompt: the door is locked."
elif [ "$probe" = "200" ]; then
  die "the site answered 200 WITHOUT asking for a password. It is open to the internet.
  Stop it now:  cd $DIR && $COMPOSE down
  Then report this — the basic-auth middleware did not load."
else
  echo "  WARNING: expected 401, got '${probe:-no response}'. Traefik may still be starting."
  echo "  Re-check in a minute:  curl -I https://$INGEST_HOST/"
  echo "  It MUST return 401. If it returns 200, run '$COMPOSE down' and report it."
fi

cat <<EOF

================================================================
Done. Nothing outside $DIR was modified.

  Site:      https://$INGEST_HOST
  Username:  ${BA_USER:-the one you set earlier}

The first visit may take up to a minute while Traefik obtains the certificate.
If the browser warns about the certificate, wait a minute and reload.

To deploy a new version later, from anywhere:
  sudo deploy-ingest          # pull latest main, rebuild, restart
  sudo deploy-ingest --logs   # ...and tail the logs afterwards

Useful later, all from $DIR:
  $COMPOSE logs -f api        # what the API is doing
  $COMPOSE logs -f worker     # ingestion jobs
  $COMPOSE restart            # restart this app only
  $COMPOSE down               # stop this app only; the other sites are untouched

This is a staging deployment: one shared password, no per-person identity, and every
content pack is unsigned placeholder, so no plan can be prescribed.
================================================================
EOF
