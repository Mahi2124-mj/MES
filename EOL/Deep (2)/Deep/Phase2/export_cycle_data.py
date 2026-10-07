#!/usr/bin/env python3
"""
export_cycle_data.py — one row per FINAL cycle, with the Semi-Auto load-cell
readings for the same part joined on.  Read-only; writes nothing to the DB.

  Final cycle   <line>_db_table_name + "_ct_log"   (part_code, ct_value, is_ng)
  Load cell     mes_submachine_data_log            (part_code, data_values jsonb)
  joined on     part_code   (exact match)

Usage
  python3 export_cycle_data.py --line YNC-SS
  python3 export_cycle_data.py --line YNC-SS --date 2026-10-05 --shift A
  python3 export_cycle_data.py --line all --format json --out /tmp/cycles.json
  python3 export_cycle_data.py --list                       # line names

Options
  --line    line name (as in mes_lines.line_name), its id, or "all"
  --date    YYYY-MM-DD           (default: today)
  --shift   A / B                (default: every shift that day)
  --format  csv | json           (default: csv)
  --out     file path            (default: stdout)
  --no-sa   skip the load-cell join (Final cycles only, much faster)
"""
import sys, os, csv, json, argparse, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import get_conn
import psycopg2.extras

#  data_values holds 20 slots (D5801..D5820); only the first few are wired on
#  most machines, the rest read 0.  Flattened to lc_1..lc_N for CSV.
MAX_LC = 20


def lines_for(cur, which):
    cur.execute("""SELECT l.id, l.line_name, l.db_table_name, z.zone_name
                     FROM mes_lines l LEFT JOIN mes_zones z ON z.id = l.zone_id
                    WHERE COALESCE(l.db_table_name,'') <> ''
                    ORDER BY l.id""")
    rows = cur.fetchall()
    if which == "all":
        return rows
    w = str(which).strip().lower()
    hit = [r for r in rows
           if str(r["id"]) == w or (r["line_name"] or "").lower() == w]
    if not hit:
        sys.exit("line '%s' nahi mili.  --list se naam dekho." % which)
    return hit


def table_exists(cur, name):
    cur.execute("SELECT to_regclass(%s) AS t", (name,))
    return (cur.fetchone() or {}).get("t") is not None


def fetch(cur, line, date, shift, with_sa):
    ct = "%s_ct_log" % line["db_table_name"]
    if not table_exists(cur, ct):
        return []
    where = ["f.record_date = %s"]
    args  = [date]
    if shift:
        where.append("f.shift_name = %s"); args.append(shift)
    sa_sel  = sa_join = ""
    if with_sa:
        sa_sel = """,
               s.part_code        AS sa_part_code,
               s.cycle_seq        AS sa_cycle_seq,
               s.ts_plc           AS sa_ts_plc,
               s.data_values      AS sa_data_values,
               p.machine_name     AS sa_machine"""
        #  LEFT JOIN: a Final cycle with no SA row still comes out, with blanks.
        sa_join = """
          LEFT JOIN LATERAL (
              SELECT d.part_code, d.cycle_seq, d.ts_plc, d.data_values, d.sub_plc_id
                FROM mes_submachine_data_log d
               WHERE d.line_id = %s AND d.part_code = f.part_code
               ORDER BY d.id DESC LIMIT 1
          ) s ON TRUE
          LEFT JOIN mes_plc_configs p ON p.id = s.sub_plc_id"""
        args = [line["id"]] + args
    cur.execute("""
        SELECT f.ts, f.record_date, f.shift_name, f.cycle_seq,
               f.ct_value, f.part_code, COALESCE(f.is_ng, FALSE) AS is_ng %s
          FROM %s f %s
         WHERE %s
         ORDER BY f.ts
    """ % (sa_sel, ct, sa_join, " AND ".join(where)), args)
    out = []
    for r in cur.fetchall():
        row = {
            "line_id":     line["id"],
            "line_name":   line["line_name"],
            "zone":        line["zone_name"],
            "record_date": str(r["record_date"]),
            "shift":       r["shift_name"],
            "cycle_seq":   r["cycle_seq"],
            "ts":          r["ts"].isoformat() if r["ts"] else None,
            "ct_value":    float(r["ct_value"]) if r["ct_value"] is not None else None,
            "part_code":   r["part_code"],
            "is_ng":       bool(r["is_ng"]),
        }
        if with_sa:
            dv = r["sa_data_values"] or []
            row["sa_machine"]   = r["sa_machine"]
            row["sa_cycle_seq"] = r["sa_cycle_seq"]
            row["sa_ts_plc"]    = r["sa_ts_plc"].isoformat() if r["sa_ts_plc"] else None
            row["load_cells"]   = dv           # full list, JSON output
            for i in range(MAX_LC):            # flattened, CSV output
                v = dv[i] if i < len(dv) else None
                row["lc_%d" % (i + 1)] = (v or {}).get("scaled")
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--line", default="all")
    ap.add_argument("--date", default=str(datetime.date.today()))
    ap.add_argument("--shift", default=None)
    ap.add_argument("--format", default="csv", choices=["csv", "json"])
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-sa", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if a.list:
            for r in lines_for(cur, "all"):
                print("%-4s %-24s %-14s %s" % (r["id"], r["line_name"],
                                               r["zone_name"] or "", r["db_table_name"]))
            return
        rows = []
        for ln in lines_for(cur, a.line):
            rows += fetch(cur, ln, a.date, a.shift, not a.no_sa)

    fh = open(a.out, "w", newline="", encoding="utf-8") if a.out else sys.stdout
    try:
        if a.format == "json":
            json.dump(rows, fh, indent=1, default=str)
            fh.write("\n")
        else:
            if not rows:
                print("koi row nahi mili.", file=sys.stderr); return
            cols = [c for c in rows[0].keys() if c != "load_cells"]
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
    finally:
        if a.out:
            fh.close()
            print("%d rows -> %s" % (len(rows), a.out), file=sys.stderr)


if __name__ == "__main__":
    main()
