#!/usr/bin/env bash
# Docker smoke test (F1, F3): build the image and exercise it against a throwaway PostgreSQL.
#
#   bash scripts/docker_smoke.sh [image-tag]
#
# Everything runs on a private Docker network; the only published port is bound to 127.0.0.1,
# so nothing is reachable from the LAN. On every exit (success, failure, Ctrl-C) the run's own
# containers with their anonymous volumes, its network, its Compose project and its temporary
# directory (HTTP headers, session cookie, CSRF token) are removed; nothing else is touched.
# Secrets are never printed. SMOKE_FAIL_AT=<stage> forces a failure at a stage (image, db, app,
# restart) so CI can prove the cleanup also runs when the script fails half-way.
# This checks the image and the development Compose file. It does NOT validate the production
# topology (TLS proxy, managed PostgreSQL, backups), which is task P0-17.
set -euo pipefail

IMAGE="${1:-smart-home-future:smoke}"
ID="shf-smoke-$$"
NET="$ID-net"
DB="$ID-db"
APP="$ID-app"
PORT="${SMOKE_PORT:-18080}"
BASE="http://127.0.0.1:$PORT"
ORIGIN="http://127.0.0.1:$PORT"
DSN="postgresql://postgres:smoke-only@$DB:5432/postgres"
PASSWORD="smoke-test-passphrase-$$"  # throwaway, only inside this run
WORK=$(mktemp -d "${TMPDIR:-/tmp}/shf-smoke.XXXXXX")  # headers, cookie, CSRF token
HEADERS="$WORK/headers"
BODY="$WORK/body"
COMPOSE_PROJECT="$ID-compose"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python

pass() { printf '  PASS  %s\n' "$1"; }
checkpoint() {
  if [ "${SMOKE_FAIL_AT:-}" = "$1" ]; then printf '  FAIL  forced failure at %s\n' "$1"; exit 1; fi
}
fail() { printf '  FAIL  %s\n' "$1"; exit 1; }
json() { "$PY" -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

cleanup() {
  rc=$?
  # -v also removes the anonymous volumes of these containers (postgres declares one).
  docker rm -fv "$APP" "$DB" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  docker compose -p "$COMPOSE_PROJECT" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$WORK"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
echo "workdir: $WORK"

echo "== build"
docker build -q -t "$IMAGE" . >/dev/null && pass "docker build $IMAGE"

echo "== image"
env_value=$(docker run --rm --entrypoint python "$IMAGE" -c \
  "import os; print(os.environ.get('SMARTHOME_ENVIRONMENT', ''))")
[ "$env_value" = "production" ] && pass "SMARTHOME_ENVIRONMENT=production in the image" \
  || fail "SMARTHOME_ENVIRONMENT is '$env_value'"
uid=$(docker run --rm --entrypoint id "$IMAGE" -u)
[ "$uid" = "10001" ] && pass "runs as non-root uid 10001" || fail "uid is $uid"
pyver=$(docker run --rm --entrypoint python "$IMAGE" -c "import sys; print(sys.version_info[:2])")
[ "$pyver" = "(3, 12)" ] && pass "Python 3.12" || fail "Python $pyver"
leaked=$(docker run --rm --entrypoint sh "$IMAGE" -c \
  "ls -a /app; test -e /app/.env && echo ENV_FILE_PRESENT; env | grep -i -E 'password|secret|token|database_url' || true")
case "$leaked" in
  *ENV_FILE_PRESENT*|*PASSWORD*|*SECRET*|*TOKEN*|*DATABASE_URL*) fail "image carries secrets: $leaked" ;;
  *) pass "no .env and no secret-looking variables in the image" ;;
esac

echo "== fail-closed defaults"
if docker run --rm "$IMAGE" >/dev/null 2>&1; then fail "production image started without a database"; fi
pass "production image refuses to start without PostgreSQL"

checkpoint image
echo "== PostgreSQL"
docker network create "$NET" >/dev/null
docker run -d --name "$DB" --network "$NET" -e POSTGRES_PASSWORD=smoke-only postgres:16 >/dev/null
for _ in $(seq 1 60); do
  docker exec "$DB" pg_isready -U postgres >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$DB" pg_isready -U postgres >/dev/null && pass "postgres:16 ready"

tool() { docker run --rm -i --network "$NET" -e SMARTHOME_DATABASE_URL="$DSN" \
  -e SMARTHOME_ENVIRONMENT=local --entrypoint python "$IMAGE" "$@"; }

tool -m smarthome.persistence upgrade | grep -q "applied=\[1, 2, 3\]" && pass "migrations 0001-0003 applied"
tool -m smarthome.persistence status | grep -q "current=3 latest=3" && pass "schema at version 3"
tool -m smarthome.persistence seed-demo | grep -q "created" && pass "seed-demo created the demo home"
printf '%s\n' "$PASSWORD" | tool -m smarthome.auth create-owner --home home-demo \
  --email owner@smoke.test --name Owner --password-stdin | grep -q "created owner" \
  && pass "create-owner via --password-stdin"
if docker run --rm --network "$NET" -e SMARTHOME_DATABASE_URL="$DSN" "$IMAGE" >/dev/null 2>&1; then
  fail "production started without SMARTHOME_ALLOWED_ORIGINS"
fi
pass "production refuses to start without SMARTHOME_ALLOWED_ORIGINS"

start_app() {
  docker run -d --name "$APP" --network "$NET" -p "127.0.0.1:$PORT:8000" \
    -e SMARTHOME_DATABASE_URL="$DSN" -e SMARTHOME_ALLOWED_ORIGINS="$ORIGIN" "$IMAGE" >/dev/null
  for _ in $(seq 1 60); do
    curl -fsS "$BASE/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  docker logs "$APP"; fail "app did not answer /health"
}

login() {
  curl -sS -D "$HEADERS" -o "$BODY" -H "Origin: $ORIGIN" -H "Content-Type: application/json" \
    -d "{\"email\": \"owner@smoke.test\", \"password\": \"$PASSWORD\"}" "$BASE/api/v1/auth/login" \
    -w '%{http_code}'
}

checkpoint db
echo "== application (production mode, real login)"
start_app && pass "container started; /health answers"
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/docs")
[ "$code" = "404" ] && pass "/docs is off in production" || fail "/docs -> $code"
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/v1/homes/home-demo/devices")
[ "$code" = "401" ] && pass "protected API without a session -> 401" || fail "anonymous -> $code"
code=$(curl -s -o /dev/null -w '%{http_code}' -H "X-Dev-User: user-owner" "$BASE/api/v1/homes/home-demo/devices")
[ "$code" = "401" ] && pass "X-Dev-User is ignored in production" || fail "dev header -> $code"
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Origin: https://evil.example" \
  -H "Content-Type: application/json" -d '{"email":"owner@smoke.test","password":"x"}' \
  "$BASE/api/v1/auth/login")
[ "$code" = "403" ] && pass "login from a foreign Origin -> 403" || fail "foreign origin -> $code"

[ "$(login)" = "200" ] && pass "login -> 200" || { cat "$BODY"; fail "login failed"; }
grep -qi "set-cookie: smarthome_session=.*HttpOnly" "$HEADERS" && grep -qi "secure" "$HEADERS" \
  && grep -qi "samesite=strict" "$HEADERS" && pass "cookie is HttpOnly, Secure, SameSite=Strict"
COOKIE=$(grep -i "set-cookie: smarthome_session=" "$HEADERS" | sed -E 's/.*smarthome_session=([^;]+).*/\1/' | tr -d '\r')
CSRF=$(json "d['csrf_token']" < "$BODY")
auth=(-H "Cookie: smarthome_session=$COOKIE")
count=$(curl -fsS "${auth[@]}" "$BASE/api/v1/homes/home-demo/devices" | json "len(d)")
[ "$count" = "10" ] && pass "session reads 10 devices" || fail "devices: $count"
body='{"device_id": "dev-living-light", "capability": "on_off", "value": true}'
code=$(curl -s -o /dev/null -w '%{http_code}' "${auth[@]}" -H "Origin: $ORIGIN" \
  -H "Content-Type: application/json" -d "$body" "$BASE/api/v1/homes/home-demo/commands")
[ "$code" = "403" ] && pass "write without CSRF token -> 403" || fail "no csrf -> $code"
command_id=$(curl -fsS "${auth[@]}" -H "Origin: $ORIGIN" -H "X-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" -d "$body" "$BASE/api/v1/homes/home-demo/commands" | json "d['id']")
pass "write with session, Origin and CSRF -> 202 (command $command_id)"
for _ in $(seq 1 30); do
  status=$(curl -fsS "${auth[@]}" "$BASE/api/v1/homes/home-demo/commands/$command_id" | json "d['status']")
  [ "$status" = "succeeded" ] && break
  sleep 0.5
done
[ "$status" = "succeeded" ] && pass "command verified by the simulator report" || fail "status $status"

checkpoint app  # after login: the workdir now holds a cookie and a CSRF token
echo "== restart"
docker restart "$APP" >/dev/null
for _ in $(seq 1 60); do curl -fsS "$BASE/health" >/dev/null 2>&1 && break; sleep 1; done
code=$(curl -s -o /dev/null -w '%{http_code}' "${auth[@]}" "$BASE/api/v1/homes/home-demo/devices")
[ "$code" = "200" ] && pass "session survives a restart (stored in PostgreSQL)" || fail "after restart -> $code"
status=$(curl -fsS "${auth[@]}" "$BASE/api/v1/homes/home-demo/commands/$command_id" | json "d['status']")
[ "$status" = "succeeded" ] && pass "command history survives a restart" || fail "history: $status"
count=$(curl -fsS "${auth[@]}" "$BASE/api/v1/homes/home-demo/devices" | json "len(d)")
[ "$count" = "10" ] && pass "home configuration survives a restart" || fail "devices after restart: $count"
health=""
for _ in $(seq 1 40); do
  health=$(docker inspect -f '{{.State.Health.Status}}' "$APP")
  [ "$health" = "healthy" ] && break
  sleep 1
done
[ "$health" = "healthy" ] && pass "Docker HEALTHCHECK reports healthy" || fail "health: $health"
checkpoint restart
docker rm -fv "$APP" >/dev/null

echo "== development Compose (app only, in-memory, 127.0.0.1)"
docker compose -p "$COMPOSE_PROJECT" config -q && pass "docker compose config"
SMOKE_COMPOSE_PORT="${SMOKE_COMPOSE_PORT:-8000}"
docker compose -p "$COMPOSE_PROJECT" up -d --build >/dev/null 2>&1 || fail "docker compose up"
for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:$SMOKE_COMPOSE_PORT/health" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "http://127.0.0.1:$SMOKE_COMPOSE_PORT/health" >/dev/null && pass "compose app answers /health on 127.0.0.1"
bound=$(docker compose -p "$COMPOSE_PROJECT" port api 8000)
case "$bound" in 127.0.0.1:*) pass "compose publishes only on $bound" ;; *) fail "compose bound to $bound" ;; esac

echo "== all Docker smoke checks passed"
