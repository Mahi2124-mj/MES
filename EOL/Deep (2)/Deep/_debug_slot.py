import psycopg2, psycopg2.extras
DB = {"host":"192.168.10.210","port":5432,"database":"energydb","user":"postgres","password":"tbdi@123"}
conn = psycopg2.connect(**DB)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
tbl = "ync_dashboard_complete_ct_log"

# Today's shift A data — first 5 and cycles around 09:30
cur.execute("SELECT cycle_seq, ts, ct_value, shift_name FROM " + tbl + " WHERE record_date = '2026-04-16' AND shift_name = 'A' ORDER BY ts ASC LIMIT 5")
print("=== First 5 cycles today (Shift A) ===")
for r in cur.fetchall():
    print("  seq={} ts={} ct={}".format(r["cycle_seq"], r["ts"], r["ct_value"]))

# Cycles near 09:30
cur.execute("SELECT cycle_seq, ts, ct_value FROM " + tbl + " WHERE record_date = '2026-04-16' AND shift_name = 'A' AND ts >= '2026-04-16 09:25:00' AND ts < '2026-04-16 09:35:00' ORDER BY ts ASC")
print("\n=== Cycles around 09:30 boundary ===")
for r in cur.fetchall():
    print("  seq={} ts={} ct={}".format(r["cycle_seq"], r["ts"], r["ct_value"]))

# Total count
cur.execute("SELECT COUNT(*) as c, MIN(cycle_seq) as mn, MAX(cycle_seq) as mx, MIN(ts) as first_ts, MAX(ts) as last_ts FROM " + tbl + " WHERE record_date = '2026-04-16' AND shift_name = 'A'")
r = cur.fetchone()
print("\n=== Summary ===")
print("  total={} cycle_seq={}..{} ts={}..{}".format(r["c"], r["mn"], r["mx"], r["first_ts"], r["last_ts"]))

# Check for duplicate cycle_seq
cur.execute("SELECT cycle_seq, COUNT(*) as c FROM " + tbl + " WHERE record_date = '2026-04-16' AND shift_name = 'A' GROUP BY cycle_seq HAVING COUNT(*) > 1 ORDER BY cycle_seq LIMIT 10")
dups = cur.fetchall()
if dups:
    print("\n=== DUPLICATE cycle_seq values ===")
    for r in dups:
        print("  seq={} count={}".format(r["cycle_seq"], r["c"]))
else:
    print("\n  No duplicate cycle_seq values")

conn.close()
