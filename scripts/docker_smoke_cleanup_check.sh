#!/usr/bin/env bash
# R2: prove scripts/docker_smoke.sh cleans up after itself, on success AND when it fails half-way.
#
#   bash scripts/docker_smoke_cleanup_check.sh [image-tag] [stage ...]
#
# For each stage (default: db app) the smoke test is forced to fail there (SMOKE_FAIL_AT). After
# each run this checks: the run failed for the forced reason, its temporary directory (cookie,
# CSRF token) is gone, no shf-smoke container or network is left, the set of Docker volumes is
# unchanged (anonymous volumes removed), and the log contains no password, cookie or CSRF token.
# Only resources named after the run are ever removed by the smoke test itself.
set -euo pipefail

IMAGE="${1:-smart-home-future:smoke}"
shift || true
STAGES=("$@")
[ ${#STAGES[@]} -gt 0 ] || STAGES=(db app)
LOG_DIR=$(mktemp -d "${TMPDIR:-/tmp}/shf-cleanup-check.XXXXXX")
trap 'rm -rf "$LOG_DIR"' EXIT

ok() { printf '  PASS  %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1"; exit 1; }

leftovers() {
  docker ps -aq --filter "name=shf-smoke-"
  docker network ls -q --filter "name=shf-smoke-"
}

for stage in "${STAGES[@]}"; do
  echo "== forced failure at '$stage'"
  volumes_before=$(docker volume ls -q | sort)
  log="$LOG_DIR/$stage.log"
  if SMOKE_FAIL_AT="$stage" bash scripts/docker_smoke.sh "$IMAGE" >"$log" 2>&1; then
    bad "smoke test did not fail at '$stage'"
  fi
  grep -q "forced failure at $stage" "$log" && ok "failed at the forced stage" \
    || { cat "$log"; bad "failed for another reason"; }
  work=$(sed -n 's/^workdir: //p' "$log")
  [ -n "$work" ] && [ ! -e "$work" ] && ok "temporary directory removed ($work)" \
    || bad "temporary directory still present: $work"
  [ -z "$(leftovers)" ] && ok "no shf-smoke container or network left" || bad "leftovers: $(leftovers)"
  [ "$(docker volume ls -q | sort)" = "$volumes_before" ] && ok "Docker volumes unchanged" \
    || bad "volumes changed"
  if grep -E -q "smoke-test-passphrase|smarthome_session=|csrf_token" "$log"; then
    bad "the log contains a secret"
  fi
  ok "log contains no password, cookie or CSRF token"
done
echo "== cleanup verified for: ${STAGES[*]}"
