#!/usr/bin/env bash
# ============================================================
#   LOCAL DB SETUP  — make this box's PostgreSQL the app's DB
#
#   Run ONCE with sudo AFTER `sudo apt install -y postgresql`:
#       sudo ./setup_local_db.sh
#
#   1. sets the local `postgres` role password to tbdi@123 (so the
#      app's existing DB_PASS keeps working)
#   2. allows password auth from 127.0.0.1
#   3. creates `energydb` and restores the dump taken from .210
#   4. verifies
#
#   After this, run (as the normal user) the repoint + restart —
#   the wrapper prints the exact command at the end.
# ============================================================
set -uo pipefail

if [[ $EUID -ne 0 ]]; then echo " [FATAL] run with sudo"; exit 1; fi

DUMP="/run/media/server/3ad0fece-b7bc-48b1-8f24-d21bb5153735/eol-data/database/energydb_localmigration.dump"
DBPASS="tbdi@123"

echo " [1/6] Checking local PostgreSQL..."
if ! command -v pg_isready >/dev/null 2>&1 || ! ls /usr/lib/postgresql/*/bin/postgres >/dev/null 2>&1; then
  echo "   [FATAL] postgres server not installed.  Run first:  sudo apt install -y postgresql"
  exit 1
fi
systemctl enable --now postgresql >/dev/null 2>&1
# find the cluster's version + hba path
PGVER="$(ls /usr/lib/postgresql/ | sort -V | tail -1)"
HBA="/etc/postgresql/$PGVER/main/pg_hba.conf"
echo "   postgres $PGVER, hba=$HBA"

echo " [2/6] Setting 'postgres' role password..."
sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD '${DBPASS}';" >/dev/null

echo " [3/6] Allowing password auth from 127.0.0.1..."
if ! grep -qE '^host\s+all\s+all\s+127\.0\.0\.1/32\s+(md5|scram-sha-256)' "$HBA" 2>/dev/null; then
  echo "host    all    all    127.0.0.1/32    md5" >> "$HBA"
fi
# make sure it listens on localhost (default does)
systemctl restart postgresql
sleep 3

echo " [4/6] Creating energydb (drop if exists)..."
sudo -u postgres psql -c "DROP DATABASE IF EXISTS energydb;" >/dev/null 2>&1
sudo -u postgres psql -c "CREATE DATABASE energydb OWNER postgres;" >/dev/null

echo " [5/6] Restoring dump ($(du -h "$DUMP" 2>/dev/null | cut -f1))... this takes a few minutes"
if [[ ! -s "$DUMP" ]]; then echo "   [FATAL] dump missing/empty: $DUMP"; exit 1; fi
# 2026-07-22 — restore over TCP (127.0.0.1) with the password set in step 2,
# NOT `sudo -u postgres` reading the file directly.  The dump lives on the
# udisks-mounted data disk (/run/media/server/<uuid>/...) which is owned by
# uid 1000 with perms that block the `postgres` OS user → `sudo -u postgres
# pg_restore` failed with "could not open input file: Permission denied" and
# restored 0 tables.  This process is root (can read the file) and connects
# via md5, so pg_restore reads the file as root and writes over the socket.
PGPASSWORD="$DBPASS" pg_restore --no-owner --no-privileges \
  -h 127.0.0.1 -U postgres -j 4 -d energydb "$DUMP" 2>/tmp/pgrestore.log
echo "   restore rc=$? (harmless 'already exists' notices are normal)"

echo " [6/6] Verifying..."
PGPASSWORD="$DBPASS" psql -h 127.0.0.1 -U postgres -d energydb -tAc \
  "SELECT 'mes_admin='||count(*) FROM mes_admin" 2>&1 | sed 's/^/   /'
PGPASSWORD="$DBPASS" psql -h 127.0.0.1 -U postgres -d energydb -tAc \
  "SELECT 'tables='||count(*) FROM information_schema.tables WHERE table_schema='public'" 2>&1 | sed 's/^/   /'

echo
echo " ==============================================================="
echo "   Local DB ready on 127.0.0.1:5432 (energydb)."
echo " ==============================================================="
echo "   Now repoint + restart the stack (as the 'server' user, NOT sudo):"
echo
echo "     cd \"/run/media/server/5b4e6f01-4c04-4bc3-b34e-b0df376c067f/server backup/D DRIVE/EOL/EOL/Deep (2)/Deep\""
echo "     ./switch_to_local_db.sh"
echo " ==============================================================="
