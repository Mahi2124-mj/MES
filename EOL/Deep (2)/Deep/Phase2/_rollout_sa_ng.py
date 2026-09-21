#!/usr/bin/env python3
# Roll out YFG-SS Semi-Auto NG-capture + Final-reject config to every seat-slider line.
#   SA Group A (id 12,41,34 — already capturing on M5700): ADD NG fields only.
#   SA Group B (id 50,58,75,91 — 0 captures, stale D5201): full YFG SA config copy.
#   Finals  (id 2,42,35,48,59,69,76): set fi_sa_ng_bit=L230 + fi_fetch_bit=M100.
# YFG-SS itself (SA id 68, Final id 62) is already done.
# All fields stay editable in the Admin UI. Counting/OEE untouched.
# Backs up every touched field first. Line collectors' SUB-RELOAD hot-restarts
# each changed sub-worker within ~30s (no manual restart for config to load).
import psycopg2, json, datetime, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
c = psycopg2.connect(host=os.getenv("DB_HOST", "127.0.0.1"), port=5432,
                     dbname="energydb", user="postgres", password="tbdi@123",
                     connect_timeout=8)
c.autocommit = False
cur = c.cursor()
GROUP_A = (12, 41, 34)
GROUP_B = (50, 58, 75, 91)
FINALS  = (2, 42, 35, 48, 59, 69, 76)
SA = ["sa_enabled","sa_fetch_bit","sa_ng_trigger_bit","sa_ok_bit","sa_ng_bit",
      "sa_part_code_addr","sa_part_code_len","sa_data_addr","sa_data_len",
      "sa_time_addr","sa_time_len","sa_register_names","sa_register_scales",
      "sa_shift_data_bit","sa_shift_reset_bit","sa_result_register",
      "sa_result_ok_value","sa_result_ng_value"]
FI = ["fi_sa_ng_bit","fi_fetch_bit","fi_sa_ng_bit_hold_sec"]

# 1) BACKUP (SA machines + Finals)
cur.execute(f"select id,machine_name,{','.join(SA)} from mes_plc_configs where id in %s order by id",
            (GROUP_A + GROUP_B,))
bk_sa = [list(r) for r in cur.fetchall()]
cur.execute(f"select id,machine_name,{','.join(FI)} from mes_plc_configs where id in %s order by id",
            (FINALS,))
bk_fi = [list(r) for r in cur.fetchall()]
stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
bpath = f"_rollout_sa_ng_backup_{stamp}.json"
open(bpath, "w").write(json.dumps({"sa": bk_sa, "fi": bk_fi}, default=str, indent=1))
print("BACKUP ->", os.path.abspath(bpath))

# 2) SA Group A — add NG fields only
cur.execute("""update mes_plc_configs
      set sa_ng_trigger_bit='M1151', sa_ok_bit='M5115', sa_ng_bit='M5116',
          sa_enabled=true, updated_at=now() where id in %s""", (GROUP_A,))
print("SA Group A (add NG fields):", cur.rowcount)

# 3) SA Group B — full YFG (id 68) copy
setcols = ", ".join(f"{k}=s.{k}" for k in SA)
cur.execute(f"""update mes_plc_configs t set {setcols}, updated_at=now()
      from mes_plc_configs s where s.id=68 and t.id in %s""", (GROUP_B,))
print("SA Group B (full YFG copy):", cur.rowcount)

# 4) Finals — set reject + fetch bits
cur.execute("""update mes_plc_configs
      set fi_sa_ng_bit='L230', fi_fetch_bit='M100', fi_sa_ng_bit_hold_sec=5.0,
          updated_at=now() where id in %s""", (FINALS,))
print("Finals (reject L230 + fetch M100):", cur.rowcount)

c.commit()
print("COMMITTED.")

# 5) verify
cur.execute("""select id,line_id,machine_name,sa_enabled,sa_fetch_bit,sa_ng_trigger_bit,
      sa_ok_bit,sa_ng_bit from mes_plc_configs where id in %s order by line_id,id""",
      (GROUP_A + GROUP_B,))
print("--- SA AFTER ---")
for r in cur.fetchall():
    print(f"  id={r[0]:3} L{r[1]:<2} {str(r[2])[:20]:20} en={r[3]} fetch={r[4]} "
          f"ngtrig={r[5]} ok={r[6]} ng={r[7]}")
cur.execute("""select id,line_id,machine_name,fi_sa_ng_bit,fi_fetch_bit
      from mes_plc_configs where id in %s order by line_id,id""", (FINALS,))
print("--- FINALS AFTER ---")
for r in cur.fetchall():
    print(f"  id={r[0]:3} L{r[1]:<2} {str(r[2])[:22]:22} reject={r[3]} fetch={r[4]}")
c.close()
print("\nDONE. Collectors' SUB-RELOAD picks up config within ~30s.")
