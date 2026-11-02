#!/usr/bin/env bash
# Check that database keys on SQLite work in the image, and survive a new container.
#
# Usage: scripts/check_image_sqlite.sh <image> <docker volume argument>
#   scripts/check_image_sqlite.sh myna:latest myna-data:/data
set -euo pipefail

image=$1
volume=$2
port=8001
container=image-sqlite-check
base="http://localhost:$port"
admin="Authorization: Bearer check-key-admin"
env_args=(
  -e MYNA_ENV=production
  -e MYNA_ADMIN_API_KEY=check-admin
  -e MYNA_METRICS_API_KEY=check-metrics
  -e MYNA_DATABASE_URL=sqlite:////data/keys.db
  -e MYNA_KEY_ADMIN_API_KEY=check-key-admin
)

cleanup() { docker rm -f "$container" >/dev/null 2>&1 || true; }
trap cleanup EXIT

start() {
  cleanup
  docker run -d --name "$container" -p "$port:8000" -v "$volume" "${env_args[@]}" "$image" >/dev/null
  for _ in $(seq 1 60); do
    curl -fsS -m 1 "$base/api/health" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  docker logs "$container"
  echo "The server didn't start." >&2
  exit 1
}

expect_ping() {
  local expected=$1 status
  status=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$base/mcp/" \
    -H "Authorization: Bearer $key" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"ping"}')
  echo "POST /mcp/ returned $status"
  test "$status" = "$expected"
}

docker run --rm -v "$volume" "${env_args[@]}" "$image" myna-migrate
start

created=$(curl -fsS -X POST "$base/api/admin/keys" -H "$admin" \
  -H 'Content-Type: application/json' -d '{"name":"check-client"}')
key=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["key"])' "$created")
id=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["id"])' "$created")
expect_ping 200

echo "Starting a new container on the same volume."
start
expect_ping 200

curl -fsS -X DELETE "$base/api/admin/keys/$id" -H "$admin"
expect_ping 401
echo "SQLite keys work, and survive a new container."
