#!/usr/bin/env bash
set -euo pipefail
# Arguments are supplied by the CD workflow over SSH.
cd "$1"
export TRONENERGY_IMAGE="$2"
project="$3"
[[ -f .env && -f config/local/signer.key ]] || {
  echo 'Prepare .env and config/local/signer.key on the deployment server first.' >&2
  exit 1
}
compose=(docker compose --project-name "$project" --env-file .env -f deploy/compose/compose.yaml)
"${compose[@]}" config --quiet
"${compose[@]}" pull
"${compose[@]}" up -d --wait --wait-timeout 120 postgres redis
# Run migrations for every deployment, including retries of the same image.
"${compose[@]}" up --no-deps --no-build --force-recreate --exit-code-from migrate migrate
"${compose[@]}" up -d --no-deps --no-build --wait --wait-timeout 120 \
  api-service rental-service rental-worker chain-service signer-service observer-worker
"${compose[@]}" ps
printf '%s\n' "$TRONENERGY_IMAGE" > .deployed-image
