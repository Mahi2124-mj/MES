#!/usr/bin/env bash
# run_prep.sh — runs the SAFE prep steps (01 create, 02 copy, 03 index).
# No lock, no downtime, live table untouched. Takes a few minutes for ~8M rows.
# The actual SWAP (04) is separate and manual — run it in a night/shift gap.
set -e
cd "$(dirname "$0")"
export PGPASSWORD=tbdi@123
PSQL="psql -h 127.0.0.1 -U postgres -d energydb -v ON_ERROR_STOP=1"

echo "=== [01] create partitioned table + partitions ==="; date
$PSQL -f 01_create_partitioned.sql

echo "=== [02] bulk copy (~8M rows, few min) ==="; date
$PSQL -f 02_bulk_copy.sql

echo "=== [03] build indexes + analyze ==="; date
$PSQL -f 03_create_indexes.sql

echo "=== PREP DONE ==="; date
echo "Next: in a low-activity window run  ->  psql ... -f 04_swap.sql"
