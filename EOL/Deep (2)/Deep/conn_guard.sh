#!/usr/bin/env bash
# ============================================================
#   DB HEALTH GUARD  (backstop for local DB max_connections=100)
#
#   The app runs on the local PostgreSQL whose max_connections is still
#   100 (300 is staged in postgresql.auto.conf but needs a
#   `sudo systemctl restart postgresql` to apply).  Two failure modes
#   this guard prevents WITHOUT restarting any collector (a collector
#   restart runs `ALTER TABLE mes_plc_configs ...` which, if an app
#   session is idle-in-transaction on that table, jams the whole DB and
#   kills login + video cycle-detection):
#
#     A. SATURATION — connections creep toward 100 -> "too many clients"
#        -> login 503.  Fix: terminate IDLE connections older than 60 s
#        (they get reopened lazily) to free slots.  No collector restart.
#
#     B. LOCK JAM — a session stuck 'idle in transaction' holds a lock and
#        everything queues behind it (the 2026-07-23 outage).  Fix:
#        terminate the blockers + long idle-in-tx sessions.
#
#   Once the operator runs the postgres restart (300) this rarely acts and
#   can be stopped:  pkill -f conn_guard.sh
#   Log: <data-disk>/eol-data/database/conn_guard.log
# ============================================================
set -uo pipefail
GLOG="/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735/eol-data/database/conn_guard.log"
HIGH=80           # free idle slots when local usage crosses this
INTERVAL=60
export PGPASSWORD="tbdi@123"
PSQL(){ psql -h 127.0.0.1 -U postgres -d energydb -tAc "$1" 2>/dev/null; }
log(){ echo "[$(date '+%F %T')] $*" >> "$GLOG" 2>/dev/null; }

log "=== db_health_guard start (HIGH=$HIGH, every ${INTERVAL}s) ==="
while true; do
  # --- B. clear any lock jam FIRST (blocked > 20s) ---------------
  blocked=$(PSQL "SELECT count(*) FROM pg_stat_activity WHERE cardinality(pg_blocking_pids(pid))>0")
  if [[ "${blocked:-0}" =~ ^[0-9]+$ && "${blocked:-0}" -gt 0 ]]; then
    log "LOCK JAM: $blocked blocked — terminating blockers + long idle-in-tx"
    PSQL "SELECT pg_terminate_backend(b.pid) FROM pg_stat_activity a
            JOIN pg_stat_activity b ON b.pid = ANY(pg_blocking_pids(a.pid))
           WHERE cardinality(pg_blocking_pids(a.pid))>0" >/dev/null
    PSQL "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
           WHERE state='idle in transaction'
             AND state_change < now()-interval '20 seconds'" >/dev/null
    log "  jam cleared"
  fi

  # --- A. relieve saturation by freeing IDLE slots ---------------
  used=$(PSQL "SELECT count(*) FROM pg_stat_activity")
  if [[ "${used:-0}" =~ ^[0-9]+$ && "${used:-0}" -ge "$HIGH" ]]; then
    freed=$(PSQL "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity
                   WHERE state='idle'
                     AND state_change < now()-interval '60 seconds'
                     AND pid <> pg_backend_pid()")
    log "saturation ${used}/100 >= $HIGH — freed ${freed:-0} idle connections"
  fi

  sleep "$INTERVAL"
done
