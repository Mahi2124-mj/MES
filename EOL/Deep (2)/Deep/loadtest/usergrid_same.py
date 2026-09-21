#!/usr/bin/env python3
"""usergrid_same.py — N panes, ONE account, all on the supervisor page.

The 15-user grid asked "does the app hold up for fifteen different people".
This asks the opposite and harder question the operator actually hit: what
happens when the SAME login is open on ten screens at once — which is normal on
a shop floor, where one supervisor id is shared across the line displays.

Same trick as before: each pane gets its own loopback address so the browser
keeps ten separate sessionStorages; the TOKEN in all of them is identical, so
the server sees one identity on ten concurrent clients.

    python3 loadtest/usergrid_same.py --panes 10 --user <username>
then reload http://127.0.0.1:8095/usergrid_sup.html
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "Phase2"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panes", type=int, default=10)
    ap.add_argument("--user", default=None, help="username; default = first shift_incharge")
    a = ap.parse_args()

    import psycopg2, psycopg2.extras
    from auth import create_token                       # type: ignore
    c = psycopg2.connect(dbname="energydb", user="postgres", password="tbdi@123",
                         host="127.0.0.1")
    cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if a.user:
        cur.execute("SELECT id, username, role FROM mes_admin WHERE username=%s", (a.user,))
    else:
        cur.execute("""SELECT id, username, role FROM mes_admin
                        ORDER BY CASE role WHEN 'shift_incharge' THEN 0
                                           WHEN 'production_incharge' THEN 1
                                           ELSE 2 END, id LIMIT 1""")
    u = cur.fetchone()
    if not u:
        print("[usergrid_same] no such user"); return
    u = dict(u)
    u["token"] = create_token(u["username"], u["role"], u["id"])

    cur.execute("""SELECT id, line_name AS name FROM mes_lines
                    WHERE COALESCE(is_active, TRUE)
                      AND db_table_name IS NOT NULL AND db_table_name <> ''
                    ORDER BY line_name""")
    lines = [dict(r) for r in cur.fetchall()]
    c.close()

    cfg = {"users": [dict(u) for _ in range(a.panes)], "lines": lines}
    with open(os.path.join(HERE, "grid_users.json"), "w") as fh:
        json.dump(cfg, fh)
    print(f"[usergrid_same] {a.panes} panes, ALL as '{u['username']}' ({u['role']}, id={u['id']})")
    print(f"[usergrid_same] reload http://127.0.0.1:8095/usergrid_sup.html")


if __name__ == "__main__":
    main()
