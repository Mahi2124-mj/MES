#!/usr/bin/env bash
# rotate_logs.sh — hourly log rotation for the EOL stack (see logrotate.conf).
# Kept as a wrapper so cron has one stable command and the state file lives
# with the logs instead of in the system-wide logrotate state.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$ROOT/logs/.rotate"
exec /usr/sbin/logrotate -s "$ROOT/logs/.rotate/state" "$ROOT/logrotate.conf"
