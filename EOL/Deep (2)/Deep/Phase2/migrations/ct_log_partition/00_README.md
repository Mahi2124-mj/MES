# Partition `mes_submachine_ct_log` by month

**Goal:** keep years of cycle data, but every query only scans the month(s) it
needs (partition pruning) — so speed stays constant no matter how big the table
grows. No data is deleted. No app code changes (collectors/routers keep using
the same table name).

Verified up front: collectors do plain `INSERT` (no `ON CONFLICT` on this
table), always supply `record_date`, and nothing has a foreign key *into*
`mes_submachine_ct_log` and no view depends on it → the name-swap is safe.

---

## Run order

All steps are **psql writes** — run them yourself (the folder path has spaces,
so keep the quotes). `cd` into this folder first:

```bash
cd "/run/media/server/5b4e6f01-4c04-4bc3-b34e-b0df376c067f/server backup/D DRIVE/EOL/EOL/Deep (2)/Deep/Phase2/migrations/ct_log_partition"
```

### Step A — PREP  (any time · no lock · no downtime)
Builds the new partitioned table beside the live one and copies all rows.
```bash
bash run_prep.sh
```
Takes a few minutes (~8M rows). The live table is fully read/write throughout.
Re-runnable safely (02 only copies rows not yet copied).

### Step B — SWAP  (night / shift gap · ~seconds · brief write-lock)
```bash
PGPASSWORD=tbdi@123 psql -h 127.0.0.1 -U postgres -d energydb -v ON_ERROR_STOP=1 -f 04_swap.sql
```
One transaction: locks the table, copies the small delta since prep, renames
`new → live` and `live → _old`, fixes the sequence. Collectors block for a
couple of seconds and retry (their never-die loop handles it), then write to
the new table transparently. If anything fails mid-way it auto-rolls back —
nothing changes.

Check the output: `live_now` and `old_backup` counts should match.

### Step C — AUTO-PARTITIONS  (once, right after swap)
```bash
PGPASSWORD=tbdi@123 psql -h 127.0.0.1 -U postgres -d energydb -f 05_auto_partition.sql
```
Then add the monthly cron line shown inside 05 (via `crontab -e`) so future
months are always created ahead of time. Done — zero maintenance after this.

---

## After a day or two of verifying
Reclaim the old table's space (it's just a backup copy now):
```sql
DROP TABLE mes_submachine_ct_log_old;
```

## If something looks wrong (before dropping `_old`)
```bash
PGPASSWORD=tbdi@123 psql -h 127.0.0.1 -U postgres -d energydb -f 06_rollback.sql
```

---

## What you get
- Query for today / this shift → scans **only the current month's partition**,
  regardless of total history. Old-month reports scan only that month.
- Per-partition indexes (smaller, faster). Inserts auto-route by date.
- Drop any very-old month instantly some day (`DROP TABLE ...p2026MM`) — no
  giant `DELETE`.
- Same recipe can later be applied to `mes_submachine_data_log` (the other big
  table) if you want — ask and I'll generate its scripts.
