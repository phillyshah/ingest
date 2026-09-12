#!/usr/bin/env bash
# MoveAI ingestion engine — server inspection. READ-ONLY.
#
# This script changes nothing. It installs nothing, starts nothing, stops nothing, and writes no file outside
# /tmp. It prints a report so the deployment can be tailored to this machine instead of guessing, which matters
# because other live sites are on it.
#
# Run:  curl -fsSL https://raw.githubusercontent.com/phillyshah/ingest/main/deploy/inspect.sh | sudo bash
#
# Then copy everything between the BEGIN and END markers and paste it back.

set -u
say()  { printf '%s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }
head_of() { [ -r "$1" ] && sed -n "1,${2:-20}p" "$1" 2>/dev/null; }

say "===== BEGIN MOVEAI SERVER REPORT ====="
say "generated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
say "report-version: 1"
say ""

say "## identity"
say "hostname: $(hostname -f 2>/dev/null || hostname)"
say "kernel:   $(uname -srm)"
if [ -r /etc/os-release ]; then . /etc/os-release; say "os:       ${PRETTY_NAME:-unknown}"; else say "os:       unknown"; fi
say "uptime:   $(uptime -p 2>/dev/null || true)"
say "user:     $(id -un) (uid $(id -u))"
say "virt:     $(systemd-detect-virt 2>/dev/null || echo unknown)"
say ""

say "## capacity"
say "cpu cores: $(nproc 2>/dev/null || echo '?')"
if have free; then say "memory:"; free -h 2>/dev/null | sed 's/^/  /'; fi
say "disk:"; df -h / /var /opt 2>/dev/null | sed 's/^/  /' | sort -u
say "load:      $(cat /proc/loadavg 2>/dev/null | cut -d' ' -f1-3)"
say ""

say "## container tooling"
for c in docker podman; do
  if have "$c"; then say "$c: $($c --version 2>&1 | head -1)"; else say "$c: not installed"; fi
done
if have docker; then
  say "docker compose: $(docker compose version 2>&1 | head -1 || echo 'plugin not present')"
  say "docker daemon: $(docker info --format '{{.ServerVersion}}' 2>/dev/null || echo 'not running or no permission')"
  say "running containers:"
  docker ps --format '  {{.Names}}  {{.Image}}  {{.Status}}  {{.Ports}}' 2>/dev/null | head -30 || say "  (cannot list)"
fi
have docker-compose && say "docker-compose (v1): $(docker-compose --version 2>&1 | head -1)"
say ""

say "## web servers and control panels"
for s in nginx apache2 httpd caddy lshttpd litespeed openlitespeed traefik haproxy; do
  if have "$s"; then say "$s binary: present ($($s -v 2>&1 | head -1 | tr -d '\n'))"; fi
done
for p in /usr/local/lsws /usr/local/CyberCP /usr/local/psa /home/cloudpanel /usr/local/cpanel /opt/plesk; do
  [ -e "$p" ] && say "control panel path present: $p"
done
if have systemctl; then
  say "active web-ish services:"
  systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null \
    | grep -iE 'nginx|apache|httpd|caddy|lsws|litespeed|traefik|haproxy|docker|cyberpanel|plesk|cloudpanel' \
    | sed 's/^/  /' || say "  (none matched)"
fi
say ""

say "## listening ports"
if have ss; then ss -tlnp 2>/dev/null | sed 's/^/  /' | head -40
elif have netstat; then netstat -tlnp 2>/dev/null | sed 's/^/  /' | head -40
else say "  (no ss or netstat)"; fi
say ""

say "## existing virtual hosts (names only, no file contents)"
for d in /etc/nginx/sites-enabled /etc/nginx/conf.d /etc/apache2/sites-enabled /etc/httpd/conf.d \
         /usr/local/lsws/conf/vhosts /etc/caddy; do
  if [ -d "$d" ]; then
    say "$d:"
    ls -1 "$d" 2>/dev/null | sed 's/^/  /' | head -30
  fi
done
[ -r /etc/caddy/Caddyfile ] && say "Caddyfile site blocks:" && grep -oE '^[a-z0-9.*-]+\.[a-z]{2,}' /etc/caddy/Caddyfile 2>/dev/null | sed 's/^/  /' | head -20
say "server_name entries found in nginx config:"
grep -rhoE 'server_name[[:space:]]+[^;]+;' /etc/nginx 2>/dev/null | sed 's/^/  /' | sort -u | head -20
say "ServerName entries found in apache config:"
grep -rhoE 'ServerName[[:space:]]+[^[:space:]]+' /etc/apache2 /etc/httpd 2>/dev/null | sed 's/^/  /' | sort -u | head -20
say ""

say "## TLS / certificates"
have certbot && say "certbot: $(certbot --version 2>&1 | head -1)" || say "certbot: not installed"
[ -d /etc/letsencrypt/live ] && say "letsencrypt certs:" && ls -1 /etc/letsencrypt/live 2>/dev/null | sed 's/^/  /' | head -20
have acme.sh && say "acme.sh: present"
say "renewal timers:"
systemctl list-timers --no-pager 2>/dev/null | grep -iE 'certbot|acme|renew' | sed 's/^/  /' || say "  (none found)"
say ""

say "## firewall"
have ufw && say "ufw: $(ufw status 2>/dev/null | head -5 | tr '\n' ' ')"
have firewall-cmd && say "firewalld: $(firewall-cmd --state 2>/dev/null)"
have iptables && say "iptables rules: $(iptables -S 2>/dev/null | wc -l) lines"
say ""

say "## languages and package managers"
for b in python3 node npm pnpm git make curl; do
  if have "$b"; then say "$b: $($b --version 2>&1 | head -1)"; else say "$b: not installed"; fi
done
say ""

say "## anything already deployed for this project"
for p in /opt/moveai /etc/moveai /srv/moveai; do
  [ -e "$p" ] && say "exists: $p" && ls -la "$p" 2>/dev/null | sed 's/^/  /' | head -10
done
say ""
say "===== END MOVEAI SERVER REPORT ====="
say ""
say "Nothing was changed. Copy everything between the BEGIN and END markers above and send it back."
