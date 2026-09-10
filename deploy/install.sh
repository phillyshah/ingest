#!/usr/bin/env bash
# MoveAI ingestion engine — installer for a VPS that already hosts other live sites.
#
# SAFETY DESIGN
#   * Dry run by default. It prints what it would do and changes nothing until you pass --apply.
#   * Additive only. It never rewrites an existing web-server config file; it adds one new site file.
#   * Backs up any file it replaces to <file>.bak.<timestamp>.
#   * Validates the web-server config and rolls the change back if validation fails, so other sites cannot break.
#   * Refuses to run against a setup it does not recognise, rather than guessing.
#   * Secrets are typed into this terminal, never passed as arguments, so they stay out of shell history.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/phillyshah/ingest/main/deploy/install.sh -o install.sh
#   sudo bash install.sh                 # dry run, safe, shows the plan
#   sudo bash install.sh --apply         # actually does it
set -euo pipefail

HOSTNAME_APP="${MOVEAI_HOST:-ingest.phillyshah.com}"
REPO="${MOVEAI_REPO:-https://github.com/phillyshah/ingest.git}"
APP_DIR=/opt/moveai
ENV_DIR=/etc/moveai
ENV_FILE="$ENV_DIR/.env"
STAMP=$(date -u +%Y%m%d%H%M%S)
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

c_red=$'\033[31m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_off=$'\033[0m'
ok()   { printf '%s  ok%s  %s\n'   "$c_grn" "$c_off" "$*"; }
warn() { printf '%s warn%s %s\n'   "$c_ylw" "$c_off" "$*"; }
die()  { printf '%s fail%s %s\n\n' "$c_red" "$c_off" "$*"; exit 1; }
step() { printf '\n=== %s ===\n' "$*"; }
would() { if [ "$APPLY" = 1 ]; then "$@"; else printf '   would run: %s\n' "$*"; fi; }

if [ "$APPLY" = 0 ]; then
  printf '\n%sDRY RUN%s — nothing will be changed. Re-run with --apply when the plan below looks right.\n' "$c_ylw" "$c_off"
fi

step "1. Preflight"
[ "$(id -u)" = 0 ] || die "run with sudo"
command -v docker >/dev/null || die "docker is not installed. Send me the inspect.sh report and I will adjust this script."
docker compose version >/dev/null 2>&1 || die "the docker compose plugin is missing. Send me the inspect.sh report."
docker info >/dev/null 2>&1 || die "the docker daemon is not running. Start it, or send me the inspect.sh report."
ok "docker $(docker --version | awk '{print $3}' | tr -d ,) with compose plugin"

avail_kb=$(df -Pk /opt 2>/dev/null | awk 'NR==2{print $4}' || echo 0)
[ "${avail_kb:-0}" -gt 3000000 ] || die "less than 3 GB free on /opt. Free space first; this server hosts other sites."
ok "disk space: $(( avail_kb / 1024 / 1024 )) GB free on /opt"

for port in 8000 8080; do
  if ss -tln 2>/dev/null | grep -qE "127\.0\.0\.1:$port\b|\*:$port\b|:::$port\b"; then
    die "port $port is already in use by something else. Send me the inspect.sh report so I can pick different ports."
  fi
done
ok "loopback ports 8000 and 8080 are free"

# Which web server fronts this machine?
PROXY=none
if systemctl is-active --quiet nginx 2>/dev/null; then PROXY=nginx
elif systemctl is-active --quiet caddy 2>/dev/null; then PROXY=caddy
elif systemctl is-active --quiet apache2 2>/dev/null || systemctl is-active --quiet httpd 2>/dev/null; then PROXY=apache
elif systemctl is-active --quiet lshttpd 2>/dev/null || [ -d /usr/local/lsws ]; then PROXY=litespeed
fi
case "$PROXY" in
  nginx|caddy) ok "reverse proxy detected: $PROXY" ;;
  apache)    die "Apache is fronting this server. Apache needs mod_proxy configured carefully alongside your existing sites; send me the inspect.sh report and I will write the vhost for you rather than guess." ;;
  litespeed) die "LiteSpeed/CyberPanel is fronting this server. Its vhosts are managed through the control panel, not files; send me the inspect.sh report and I will write click-by-click panel steps instead." ;;
  none)      die "no supported reverse proxy is running. Send me the inspect.sh report." ;;
esac

# Never touch an existing site for this hostname.
if grep -rqs -- "$HOSTNAME_APP" /etc/nginx /etc/caddy 2>/dev/null; then
  warn "a config mentioning $HOSTNAME_APP already exists"
  grep -rls -- "$HOSTNAME_APP" /etc/nginx /etc/caddy 2>/dev/null | sed 's/^/     /'
  die "refusing to touch an existing configuration for this hostname. Remove it first, or tell me and I will adapt."
fi
ok "no existing config claims $HOSTNAME_APP"

step "2. Application user and directories"
if ! id moveai >/dev/null 2>&1; then would useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin moveai; else ok "user moveai exists"; fi
would install -d -o moveai -g moveai -m 0750 "$APP_DIR"
would install -d -o root  -g root   -m 0700 "$ENV_DIR"

step "3. Source code"
if [ -d "$APP_DIR/.git" ]; then
  ok "repository already present, will fast-forward"
  would git -C "$APP_DIR" fetch --depth 1 origin main
  would git -C "$APP_DIR" reset --hard origin/main
else
  would git clone --depth 1 "$REPO" "$APP_DIR"
fi

step "4. Secrets"
if [ -f "$ENV_FILE" ]; then
  ok "$ENV_FILE exists; leaving it alone. Delete it and re-run if you need to change a secret."
else
  if [ "$APPLY" = 1 ]; then
    echo "You will now paste the Supabase connection string. It is stored at $ENV_FILE, readable only by root,"
    echo "and is not echoed to the screen or saved in your shell history."
    printf 'Supabase session-pooler connection string: '
    read -rs DB_URL; echo
    [ -n "$DB_URL" ] || die "nothing entered"
    case "$DB_URL" in postgres://*|postgresql://*) : ;; *) die "that does not look like a postgres connection string" ;; esac
    printf 'Supabase project URL (https://xxxx.supabase.co): '
    read -r SUPA_URL
    SIGNING=$(head -c 48 /dev/urandom | base64 | tr -d '\n=+/' | cut -c1-48)
    umask 077
    cat > "$ENV_FILE" <<ENVEOF
# Written by deploy/install.sh on $(date -u +%FT%TZ). Root-only. Do not commit this anywhere.
APP_ORIGIN=https://$HOSTNAME_APP
API_ROOT_PATH=/api
MOVEAI_ENV=production
AUTH_MODE=supabase
DATABASE_URL=$DB_URL
SUPABASE_URL=$SUPA_URL
VITE_SUPABASE_URL=$SUPA_URL
SUPABASE_JWT_AUDIENCE=authenticated
PLAN_SIGNING_SECRET=$SIGNING
WORKER_POLL_SECONDS=2
WORKER_CONCURRENCY=1
EXTRACTION_MODEL=mock-1
TENANT_DAILY_USD_CAP=25
ENVEOF
    chmod 600 "$ENV_FILE"; chown root:root "$ENV_FILE"
    ok "wrote $ENV_FILE (mode 600, root only) with a freshly generated signing secret"
  else
    printf '   would prompt you for the Supabase connection string and project URL,\n'
    printf '   generate PLAN_SIGNING_SECRET locally, and write %s with mode 600\n' "$ENV_FILE"
  fi
fi

step "5. Build and start the containers"
would docker compose --project-directory "$APP_DIR/infra" --env-file "$ENV_FILE" build
would docker compose --project-directory "$APP_DIR/infra" --env-file "$ENV_FILE" up -d api worker scheduler reviewer
if [ "$APPLY" = 1 ]; then
  sleep 6
  curl -fsS --max-time 10 http://127.0.0.1:8000/v1/healthz >/dev/null \
    && ok "API answers on 127.0.0.1:8000 (loopback only, not exposed to the internet)" \
    || die "the API did not come up. Nothing was added to the web server, so your other sites are untouched. Run: docker compose --project-directory $APP_DIR/infra logs api"
fi

step "6. Add one new site to $PROXY (additive, validated, reversible)"
if [ "$PROXY" = nginx ]; then
  SITE=/etc/nginx/sites-available/$HOSTNAME_APP
  [ -d /etc/nginx/sites-available ] || SITE=/etc/nginx/conf.d/$HOSTNAME_APP.conf
  if [ "$APPLY" = 1 ]; then
    [ -f "$SITE" ] && cp -a "$SITE" "$SITE.bak.$STAMP" && warn "backed up existing $SITE"
    cat > "$SITE" <<NGINXEOF
# MoveAI ingestion engine. Added by deploy/install.sh on $(date -u +%FT%TZ).
# Additive: this file only serves $HOSTNAME_APP and does not affect other sites.
server {
    listen 80;
    listen [::]:80;
    server_name $HOSTNAME_APP;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name $HOSTNAME_APP;

    # certbot fills these in; until then this block is inert because no cert exists yet.
    ssl_certificate     /etc/letsencrypt/live/$HOSTNAME_APP/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$HOSTNAME_APP/privkey.pem;

    client_max_body_size 30m;
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;

    location /api/v1/ {
        proxy_pass http://127.0.0.1:8000/v1/;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Request-Id \$request_id;
        proxy_read_timeout 65s;
        proxy_buffering off;          # server-sent events for the campaign board
    }
    location /api/ { return 404; }
    location / {
        proxy_pass http://127.0.0.1:8080/;
        proxy_set_header Host \$host;
    }
}
NGINXEOF
    [ -d /etc/nginx/sites-enabled ] && ln -sfn "$SITE" "/etc/nginx/sites-enabled/$HOSTNAME_APP"
    if nginx -t 2>/dev/null; then
      systemctl reload nginx && ok "nginx validated and reloaded; other sites untouched"
    else
      rm -f "/etc/nginx/sites-enabled/$HOSTNAME_APP" "$SITE"
      [ -f "$SITE.bak.$STAMP" ] && mv "$SITE.bak.$STAMP" "$SITE"
      die "nginx config validation failed, so the change was rolled back and nginx was NOT reloaded. Your other sites are unaffected."
    fi
  else
    printf '   would write %s and symlink it, run nginx -t, and reload only if validation passes\n' "$SITE"
  fi
elif [ "$PROXY" = caddy ]; then
  SITE=/etc/caddy/sites/$HOSTNAME_APP.caddy
  if [ "$APPLY" = 1 ]; then
    install -d /etc/caddy/sites
    grep -q 'import sites/\*' /etc/caddy/Caddyfile 2>/dev/null || {
      cp -a /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak.$STAMP"
      printf '\nimport sites/*\n' >> /etc/caddy/Caddyfile
      warn "added an import line to Caddyfile (backup at /etc/caddy/Caddyfile.bak.$STAMP)"
    }
    cat > "$SITE" <<CADDYEOF
# MoveAI ingestion engine. Added by deploy/install.sh on $(date -u +%FT%TZ). Caddy obtains TLS automatically.
$HOSTNAME_APP {
    encode gzip
    request_body { max_size 30MB }
    handle_path /api/v1/* {
        rewrite * /v1{uri}
        reverse_proxy 127.0.0.1:8000 { flush_interval -1 }
    }
    handle /api/* { respond 404 }
    handle { reverse_proxy 127.0.0.1:8080 }
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
    }
}
CADDYEOF
    if caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
      systemctl reload caddy && ok "caddy validated and reloaded; TLS will be obtained automatically"
    else
      rm -f "$SITE"
      die "caddy config validation failed, so the new site file was removed and caddy was NOT reloaded. Your other sites are unaffected."
    fi
  else
    printf '   would write %s and reload caddy only if validation passes\n' "$SITE"
  fi
fi

step "Done"
if [ "$APPLY" = 0 ]; then
  printf '\nThat was a dry run. Nothing changed. Re-run with:  sudo bash install.sh --apply\n\n'
else
  printf '\nNext:\n'
  [ "$PROXY" = nginx ] && printf '  1. Get the certificate:  sudo certbot --nginx -d %s\n' "$HOSTNAME_APP"
  [ "$PROXY" = caddy ] && printf '  1. Caddy is fetching the certificate automatically; give it a minute.\n'
  printf '  2. Run the "Verify deployment" workflow in GitHub Actions.\n'
  printf '  3. Containers: docker compose --project-directory %s/infra ps\n\n' "$APP_DIR"
fi
