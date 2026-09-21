#!/usr/bin/env python3
# One-shot fix: correct inverted PY bypass "desired" values on LINE 2 (YNC-SS).
# 5 PYs whose good-state is ON but desired was set to OFF -> false bypass alarms.
#   D401 LOCATE PIN, D402 RH.HARNESS-1  (1-reg):  desired '1' -> '2'
#   D406/D407/D408   protectors         (2-reg):  ('1','1') -> ('2','2')
# Touches ONLY the bypass-alarm desired_value. Counting / OEE untouched.
# Backs up old values to _py_desired_backup_<ts>.json before writing.
# Collector auto-reloads mes_py_config_live every ~20s -> no restart needed.
import psycopg2, json, datetime, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
c = psycopg2.connect(host=os.getenv("DB_HOST", "127.0.0.1"), port=5432,
                     dbname="energydb", user="postgres", password="tbdi@123",
                     connect_timeout=8)
c.autocommit = False
cur = c.cursor()
PYS = ('D401', 'D402', 'D406', 'D407', 'D408')

# 1) BACKUP
backup = {}
for tbl in ("mes_py_config", "mes_py_config_live"):
    cur.execute(f"""select id,model_number,py_no,reg_count,desired_value,desired_value_2,enabled
                    from {tbl} where line_id=2 and py_no in %s order by py_no,model_number""", (PYS,))
    backup[tbl] = [list(r) for r in cur.fetchall()]
stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
bpath = f"_py_desired_backup_{stamp}.json"
open(bpath, "w").write(json.dumps(backup, default=str, indent=1))
print("BACKUP saved ->", os.path.abspath(bpath))

# 2) FIX (both tables, surgical: only enabled + inverted rows)
total = 0
for tbl in ("mes_py_config", "mes_py_config_live"):
    cur.execute(f"""update {tbl} set desired_value='2', updated_at=now()
        where line_id=2 and py_no in ('D401','D402') and reg_count=1
              and enabled=true and desired_value='1'""")
    n1 = cur.rowcount
    cur.execute(f"""update {tbl} set desired_value='2', desired_value_2='2', updated_at=now()
        where line_id=2 and py_no in ('D406','D407','D408') and reg_count=2
              and enabled=true and desired_value='1' and desired_value_2='1'""")
    n2 = cur.rowcount
    print(f"{tbl:22}  1-reg changed={n1}  2-reg changed={n2}")
    total += n1 + n2
c.commit()
print("COMMITTED. total rows changed:", total)

# 3) VERIFY new state
cur.execute("""select model_number,py_no,reg_count,desired_value,desired_value_2,enabled
   from mes_py_config_live where line_id=2 and py_no in %s and enabled=true
   order by py_no,model_number""", (PYS,))
print("--- mes_py_config_live AFTER (enabled rows) ---")
for r in cur.fetchall():
    print(f"  model={r[0]:3} {r[1]:5} rc={r[2]} desired={r[3]!r}/{r[4]!r} en={r[5]}")
c.close()
print("\nDONE. Collector will pick up new desired within ~20s.")
