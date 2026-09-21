#!/usr/bin/env python3
"""video_probe.py — per-line video health, written where the user grid can show it.

The 15 supervisor panes are each on their own loopback ORIGIN, so the grid page
cannot reach into them to click a cycle and time the player.  This probe asks the
same question from outside: for every line the grid shows, call the exact
endpoint the player calls and record what the operator would get.

Writes loadtest/video_status.json every cycle; usergrid_sup.html polls it.
"""
import json, os, sys, time
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "Phase2"))
API = os.environ.get("MES_BASE", "http://127.0.0.1:8080")


def lines_with_cycles():
    import psycopg2, psycopg2.extras
    c = psycopg2.connect(dbname="energydb", user="postgres", password="tbdi@123",
                         host="127.0.0.1")
    cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT id, line_name, db_table_name FROM mes_lines
                    WHERE COALESCE(is_active, TRUE) AND db_table_name <> ''
                    ORDER BY line_name""")
    out = []
    for r in cur.fetchall():
        try:
            cur.execute(f"SELECT cycle_seq FROM {r['db_table_name']}_ct_log "
                        f"WHERE record_date=CURRENT_DATE ORDER BY ts DESC LIMIT 1")
            row = cur.fetchone()
            out.append({"id": r["id"], "name": r["line_name"],
                        "seq": row["cycle_seq"] if row else None})
        except Exception:
            c.rollback()
            out.append({"id": r["id"], "name": r["line_name"], "seq": None})
    c.close()
    return out


def main():
    from auth import create_token                      # type: ignore
    tok = create_token("videoprobe", "admin", 1)
    hdr = {"Authorization": "Bearer " + tok}
    while True:
        rows = []
        for ln in lines_with_cycles():
            if not ln["seq"]:
                rows.append({**ln, "http": None, "ms": None, "verdict": "no cycles today"})
                continue
            t0 = time.time()
            try:
                r = requests.get(f"{API}/api/lines/{ln['id']}/cycle-video",
                                 params={"cycle_seq": ln["seq"], "ng": 0},
                                 headers=hdr, timeout=40, stream=True)
                code = r.status_code
                r.close()
            except Exception:
                code = 0
            ms = int((time.time() - t0) * 1000)
            verdict = ("plays" if code in (200, 206)
                       else "no video" if code == 404
                       else "TIMES OUT" if code in (0, 502, 504)
                       else f"HTTP {code}")
            rows.append({**ln, "http": code, "ms": ms, "verdict": verdict})
        payload = {"ts": time.strftime("%H:%M:%S"), "lines": rows,
                   "plays": sum(1 for x in rows if x["verdict"] == "plays"),
                   "broken": sum(1 for x in rows if x["verdict"] == "TIMES OUT")}
        tmp = os.path.join(HERE, "video_status.json.tmp")
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, os.path.join(HERE, "video_status.json"))
        time.sleep(20)


if __name__ == "__main__":
    main()
