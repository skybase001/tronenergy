#!/usr/bin/env bash
set -euo pipefail
for name in BASTION_HOST BASTION_USER DEPLOY_HOST DEPLOY_USER DEPLOY_PATH COMPOSE_PROJECT_NAME BASTION_SSH_PRIVATE_KEY DEPLOY_SSH_PRIVATE_KEY SSH_KNOWN_HOSTS DEPLOY_IMAGE; do
  [[ -n "${!name:-}" ]] || { echo "Missing configuration: $name" >&2; exit 1; }
done
# Restrict values interpolated into SSH config and remote shell commands.
for name in BASTION_HOST DEPLOY_HOST; do
  [[ "${!name}" =~ ^[a-zA-Z0-9][a-zA-Z0-9.:-]*$ ]] || exit 1
done
for name in BASTION_USER DEPLOY_USER COMPOSE_PROJECT_NAME; do
  [[ "${!name}" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_-]*$ ]] || exit 1
done
[[ "$DEPLOY_PATH" =~ ^/[a-zA-Z0-9_./-]+$ && "$DEPLOY_PATH" != / ]] || exit 1
[[ "$DEPLOY_IMAGE" =~ ^ghcr.io/[a-z0-9_./-]+@sha256:[a-f0-9]{64}$ ]] || exit 1
BASTION_PORT="${BASTION_PORT:-22}"
DEPLOY_PORT="${DEPLOY_PORT:-22}"
for port in "$BASTION_PORT" "$DEPLOY_PORT"; do
  [[ "$port" =~ ^[0-9]{1,5}$ ]] && ((10#$port > 0 && 10#$port <= 65535)) || exit 1
done
ssh_dir=$(mktemp -d)
trap 'rm -rf "$ssh_dir"' EXIT
umask 077
printf '%s\n' "$BASTION_SSH_PRIVATE_KEY" > "$ssh_dir/bastion_key"
printf '%s\n' "$DEPLOY_SSH_PRIVATE_KEY" > "$ssh_dir/deploy_key"
printf '%s\n' "$SSH_KNOWN_HOSTS" > "$ssh_dir/known_hosts"
cat > "$ssh_dir/config" <<CONFIG
Host *
  BatchMode yes
  StrictHostKeyChecking yes
  UserKnownHostsFile $ssh_dir/known_hosts
  IdentitiesOnly yes
  ForwardAgent no
  ConnectTimeout 20
  ServerAliveInterval 15
  ServerAliveCountMax 3
Host bastion
  HostName $BASTION_HOST
  User $BASTION_USER
  Port $BASTION_PORT
  IdentityFile $ssh_dir/bastion_key
Host target
  HostName $DEPLOY_HOST
  User $DEPLOY_USER
  Port $DEPLOY_PORT
  IdentityFile $ssh_dir/deploy_key
  ProxyJump bastion
CONFIG
ssh -F "$ssh_dir/config" target "mkdir -p '$DEPLOY_PATH/deploy/compose'"
scp -F "$ssh_dir/config" deploy/compose/compose.yaml "target:$DEPLOY_PATH/deploy/compose/compose.yaml"
# The target user's Docker login must already allow pulling this GHCR package.
ssh -F "$ssh_dir/config" target \
  "bash -s -- '$DEPLOY_PATH' '$DEPLOY_IMAGE' '$COMPOSE_PROJECT_NAME'" < deploy/scripts/deploy-remote.sh
