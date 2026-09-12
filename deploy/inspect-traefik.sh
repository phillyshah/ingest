#!/usr/bin/env bash
# MoveAI ingestion engine — Traefik inspection. READ-ONLY.
#
# The first inspection found Traefik, not nginx, holding ports 80 and 443, with every existing site routed through
# it. Adding a site therefore means Traefik router labels on a container, not a web-server vhost. This script
# reads the details needed to write those labels correctly: entrypoint names, the ACME certificate resolver's
# name, the docker network Traefik watches, and the label conventions the existing sites already use.
#
# Guessing any of those would produce a site that answers on HTTP and fails on HTTPS, so they get read, not
# assumed.
#
# This script changes nothing. It installs nothing, starts nothing, stops nothing, and writes no file.
#
# Secrets: it never prints acme.json (that file holds the private keys for every certificate on this server), and
# it redacts anything that looks like a token, key, password or email from the configuration it does print. Read
# the output before you send it back; if anything still looks sensitive, say so instead of pasting it.
#
# Run:  curl -fsSL https://raw.githubusercontent.com/phillyshah/ingest/main/deploy/inspect-traefik.sh | sudo bash

set -u
say() { printf '%s\n' "$*"; }

# Redact anything shaped like a credential. Deliberately blunt: over-redacting costs a follow-up question,
# under-redacting leaks a secret into a chat transcript.
redact() {
  sed -E \
    -e 's/([Tt][Oo][Kk][Ee][Nn][^=:]*[=:][[:space:]]*)[^[:space:],"]*/\1REDACTED/g' \
    -e 's/([Kk][Ee][Yy][^=:]*[=:][[:space:]]*)[^[:space:],"]*/\1REDACTED/g' \
    -e 's/([Pp][Aa][Ss][Ss][^=:]*[=:][[:space:]]*)[^[:space:],"]*/\1REDACTED/g' \
    -e 's/([Ss][Ee][Cc][Rr][Ee][Tt][^=:]*[=:][[:space:]]*)[^[:space:],"]*/\1REDACTED/g' \
    -e 's/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/EMAIL-REDACTED/g'
}

say "===== BEGIN MOVEAI TRAEFIK REPORT ====="
say "generated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
say "report-version: 1"
say ""

if ! command -v docker >/dev/null 2>&1; then
  say "docker is not available; nothing further can be read."
  say "===== END MOVEAI TRAEFIK REPORT ====="
  exit 0
fi

TRAEFIK=$(docker ps --filter "ancestor=traefik" --format '{{.Names}}' 2>/dev/null | head -1)
[ -z "$TRAEFIK" ] && TRAEFIK=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -i traefik | head -1)

if [ -z "$TRAEFIK" ]; then
  say "No running Traefik container found. Containers present:"
  docker ps --format '  {{.Names}}  {{.Image}}' 2>/dev/null
  say "===== END MOVEAI TRAEFIK REPORT ====="
  exit 0
fi

say "## traefik container"
say "name:  $TRAEFIK"
say "image: $(docker inspect -f '{{.Config.Image}}' "$TRAEFIK" 2>/dev/null)"
say ""

# The static configuration usually lives either in the command line (flags) or in a mounted traefik.yml.
say "## command-line flags"
docker inspect -f '{{range .Config.Cmd}}  {{.}}{{"\n"}}{{end}}' "$TRAEFIK" 2>/dev/null | redact
docker inspect -f '{{range .Config.Entrypoint}}  {{.}}{{"\n"}}{{end}}' "$TRAEFIK" 2>/dev/null | redact
say ""

say "## environment variable NAMES only (values withheld)"
docker inspect -f '{{range .Config.Env}}{{.}}{{"\n"}}{{end}}' "$TRAEFIK" 2>/dev/null | cut -d= -f1 | sed 's/^/  /'
say ""

say "## networks traefik is attached to"
docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}  {{$k}}{{"\n"}}{{end}}' "$TRAEFIK" 2>/dev/null
say ""

say "## mounts"
docker inspect -f '{{range .Mounts}}  {{.Source}} -> {{.Destination}} (rw={{.RW}}){{"\n"}}{{end}}' "$TRAEFIK" 2>/dev/null
say ""

say "## traefik labels on the traefik container itself"
docker inspect -f '{{range $k, $v := .Config.Labels}}{{if or (eq $k "traefik.enable") (gt (len $k) 8)}}  {{$k}}={{$v}}{{"\n"}}{{end}}{{end}}' "$TRAEFIK" 2>/dev/null \
  | grep -i '^  traefik\.' | redact
say ""

# The static config file, if one is mounted. Only the lines that name entrypoints and certificate resolvers are
# printed; the rest is skipped rather than dumped, and what is printed goes through redact().
say "## static configuration file (entrypoint and certresolver lines only, redacted)"
found_cfg=""
for src in $(docker inspect -f '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' "$TRAEFIK" 2>/dev/null); do
  for cand in "$src" "$src/traefik.yml" "$src/traefik.yaml" "$src/traefik.toml"; do
    case "$cand" in *.yml|*.yaml|*.toml) ;; *) continue ;; esac
    [ -r "$cand" ] || continue
    found_cfg="yes"
    say "  --- $cand"
    grep -n -i -E 'entryPoint|certificatesResolver|acme|httpChallenge|tlsChallenge|dnsChallenge|provider|exposedByDefault|network' "$cand" 2>/dev/null \
      | sed 's/^/    /' | redact
  done
done
[ -z "$found_cfg" ] && say "  (no readable yml/toml config mounted — configuration is most likely all in the flags above)"
say ""

say "## acme storage"
for src in $(docker inspect -f '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' "$TRAEFIK" 2>/dev/null); do
  for cand in "$src" "$src/acme.json"; do
    case "$cand" in *acme*) ;; *) continue ;; esac
    if [ -e "$cand" ]; then
      # Contents are never printed: this file holds the private key of every certificate on this server.
      say "  present: $cand  ($(stat -c '%s bytes, mode %a' "$cand" 2>/dev/null))"
      say "  certificate hostnames currently stored:"
      grep -o '"main":"[^"]*"' "$cand" 2>/dev/null | cut -d'"' -f4 | sort -u | sed 's/^/    /'
    fi
  done
done
say ""

# The most useful thing of all: a working example. Copying the conventions of a site that already serves HTTPS
# here beats inferring them from documentation.
say "## traefik labels on existing routed containers (this is the pattern to copy)"
for c in $(docker ps --format '{{.Names}}' 2>/dev/null); do
  [ "$c" = "$TRAEFIK" ] && continue
  labels=$(docker inspect -f '{{range $k, $v := .Config.Labels}}{{$k}}={{$v}}{{"\n"}}{{end}}' "$c" 2>/dev/null | grep -i '^traefik\.' | sort)
  [ -z "$labels" ] && continue
  say "  --- $c"
  printf '%s\n' "$labels" | sed 's/^/    /' | redact
  say "    networks: $(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$c" 2>/dev/null)"
  say ""
done

say "## containers with no traefik labels (routed some other way, or not routed)"
for c in $(docker ps --format '{{.Names}}' 2>/dev/null); do
  [ "$c" = "$TRAEFIK" ] && continue
  if ! docker inspect -f '{{range $k, $v := .Config.Labels}}{{$k}}{{"\n"}}{{end}}' "$c" 2>/dev/null | grep -qi '^traefik\.'; then
    say "  $c"
  fi
done
say ""

say "## compose projects on this host"
docker ps -a --format '{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null \
  | grep -v '^\s*$' | sort -u | sed 's/^/  /'
say ""

say "## docker networks"
docker network ls --format '  {{.Name}}  ({{.Driver}})' 2>/dev/null
say ""

say "===== END MOVEAI TRAEFIK REPORT ====="
say ""
say "Nothing was changed. acme.json contents were NOT printed."
say "Read the output, then copy everything between the BEGIN and END markers and send it back."
