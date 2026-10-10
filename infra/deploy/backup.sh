#!/usr/bin/env bash
# Run on the deployment host with permission to read the encrypted data disk.
# Quiesces all application writers; never deletes old backups or user data.
set -euo pipefail
umask 077
runtime_env="${1:-/etc/agentforge/deploy.env}"
data_root="${2:-/srv/agentforge}"
backup_root="${3:-/srv/agentforge-backups}"
cd "$(dirname "$0")/../.."
compose=(docker compose --env-file "$runtime_env" -f infra/deploy/compose.yaml)
test -d "$data_root/artifacts"
mkdir -p "$backup_root"
backup_dir="$(mktemp -d "$backup_root/backup-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
running_services="$("${compose[@]}" ps --status running --services)"
resume_services=()
for service in $running_services; do
  case "$service" in api|worker|web|proxy) resume_services+=("$service");; esac
done
resume() {
  backup_status=$?
  trap - EXIT
  if ((${#resume_services[@]})); then
    # `start` restores existing containers without starting absent dependencies.
    if ! "${compose[@]}" start "${resume_services[@]}" >/dev/null; then
      echo "Backup recovery could not restart original app services; inspect service state." >&2
      exit 1
    fi
  fi
  exit "$backup_status"
}
trap resume EXIT
if ((${#resume_services[@]})); then
  "${compose[@]}" stop "${resume_services[@]}"
fi
"${compose[@]}" exec -T db pg_dump --username agentforge_admin --dbname agentforge --format custom > "$backup_dir/database.dump"
tar -C "$data_root" -czf "$backup_dir/artifacts.tar.gz" artifacts
git rev-parse HEAD > "$backup_dir/revision.txt"
(cd "$backup_dir" && sha256sum database.dump artifacts.tar.gz revision.txt > SHA256SUMS)
echo "Consistent database/artifact backup created at $backup_dir; copy to encrypted off-host storage."
