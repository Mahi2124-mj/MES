#!/usr/bin/env bash
# ============================================================
#   EOL BOOT — reboot-safe startup   (runs as root via eol.service)
#
#   udisks only mounts the two EOL disks at DESKTOP LOGIN, and the
#   static ARP pin does not survive a reboot — so a headless reboot
#   (2026-07-22 ~06:30) left the code+data disks unmounted, the ARP
#   pin gone, and the DB "unreachable".  This script fixes all of
#   that at every boot, BEFORE any login:
#     1. mount BOTH disks by UUID at their usual /run/media paths
#     2. pin ARP for the DB host to the REAL DB's MAC (beats the
#        192.168.30.10 impostor conflict)
#     3. wait for the DB, then launch the full stack as 'server'
#
#   Idempotent — skips whatever is already done.  Installed copy
#   lives on the ROOT fs (/usr/local/bin) so it runs even before
#   the code disk is mounted.  Log: /var/log/eol_boot.log
# ============================================================
set -uo pipefail

CODE_UUID="5b4e6f01-4c04-4bc3-b34e-b0df376c067f"
DATA_UUID="3ad0fece-b7bc-48b1-8f24-d21bb5153735"
CODE_MNT="/run/media/server/$CODE_UUID"
DATA_MNT="/run/media/server/$DATA_UUID"
# 2026-07-25 — derive the NIC holding the box's 192.168.30.x IP (the DB-subnet
# link) at RUNTIME.  The interface name has drifted across boots
# (ens2f3 -> eno1np0 -> ens2f1) and eno1np0 is currently DOWN (no carrier),
# so hardcoding any single name is fragile — this always picks the live one.
NIC="$(ip -o -4 addr show 2>/dev/null | awk '$4 ~ /^192\.168\.30\./ {print $2; exit}')"
NIC="${NIC:-ens2f1}"
BOX_IP="192.168.30.15"
DB_IP="192.168.30.10"
DB_MAC="40:5b:7f:9e:ca:30"
RUN_USER="server"
DEEP="$CODE_MNT/server backup/D DRIVE/EOL/EOL/Deep (2)/Deep"
LOG="/var/log/eol_boot.log"

log(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG" 2>/dev/null; }

log "=== eol_boot start ==="

# 1. Mount both disks by UUID (idempotent) ----------------------
for pair in "$CODE_UUID|$CODE_MNT" "$DATA_UUID|$DATA_MNT"; do
  uuid="${pair%%|*}"; mnt="${pair#*|}"
  if mountpoint -q "$mnt"; then
    log "already mounted: $mnt"
  else
    mkdir -p "$mnt"
    if mount UUID="$uuid" "$mnt" 2>>"$LOG"; then log "mounted $uuid -> $mnt"
    else log "[WARN] mount failed for UUID=$uuid"; fi
  fi
done

# 2. Wait for the factory NIC to carry its IP (netplan) ---------
for i in $(seq 1 30); do
  ip -4 addr show "$NIC" 2>/dev/null | grep -q "$BOX_IP" && break
  sleep 2
done

# 3. Pin ARP to the real DB (beats the .210 impostor) -----------
if ip neigh replace "$DB_IP" lladdr "$DB_MAC" dev "$NIC" nud permanent 2>>"$LOG"; then
  log "ARP pinned: $DB_IP -> $DB_MAC on $NIC"
else
  log "[WARN] ARP pin failed"
fi

# 4. Wait for the LOCAL DB + data disk, then a settling delay -----
# 2026-07-25 — the app runs on the LOCAL postgres (127.0.0.1) now, and
# .210 is an off-box backup that is usually DOWN, so the old wait-for-.210
# loop just burned ~2.5 min and launched the stack before the local DB was
# actually ready.  Wait for what the stack REALLY needs — local postgres up
# AND the data disk (video/buffer) mounted — then hold a short settling
# delay so postgres finishes crash-recovery after the morning power-cut
# reboot before the collectors + recorder pile on.
for i in $(seq 1 60); do
  if timeout 3 bash -c "</dev/tcp/127.0.0.1/5432" 2>/dev/null \
     && mountpoint -q "$DATA_MNT"; then
    log "local DB (:5432) up + data disk mounted (waited ~$((i*2))s)"; break
  fi
  sleep 2
done
# settling delay: give postgres recovery + system services headroom
log "settling delay (30s) before launching stack..."
sleep 30

# 5. Launch the full stack as the real user ---------------------
# --force: a reboot kills every collector but leaves their rows in
# mes_collector_locks, so on the next boot the start gate would see
# "another host is collecting" and refuse.  On a fresh boot those locks
# are ALWAYS stale (this box just came up, nothing is collecting), so
# forcing past the gate is correct — start_everything then DELETEs the
# stale locks and each collector re-acquires cleanly.
# 2026-07-25 — clear any stale STOP.flag BEFORE launching, so collectors are
# never blocked after a reboot even if start_everything's own removal doesn't
# run (a reboot left STOP.flag set + 0 collectors — this makes it bulletproof).
rm -f "$DEEP/Phase2/collectors/STOP.flag" 2>/dev/null && log "cleared stale STOP.flag"
if [[ -x "$DEEP/start_everything.sh" ]]; then
  su - "$RUN_USER" -c "cd \"$DEEP\" && ./start_everything.sh --with-collectors --force" >>"$LOG" 2>&1 &
  log "stack launch dispatched (start_everything.sh --with-collectors --force)"
else
  log "[FATAL] start_everything.sh not found at: $DEEP"
fi

# 6. Boot extras — MAINTENANCEDX (:8892/:9965) + Cloudflare tunnel (tbdi-eol) ----
# 2026-07-29 — run as the real user (tunnel creds live under
# /home/server/.cloudflared).  boot_extras.sh is idempotent + never-die-wraps the
# tunnel, so this is safe to dispatch in the background alongside the stack.
if [[ -x "/home/server/boot_extras.sh" ]]; then
  su - "$RUN_USER" -c "/home/server/boot_extras.sh" >>"$LOG" 2>&1 &
  log "boot extras dispatched (MAINTENANCEDX + cloudflared tunnel)"
else
  log "[WARN] /home/server/boot_extras.sh missing — MAINTENANCEDX + tunnel will NOT auto-start"
fi

log "=== eol_boot done ==="
