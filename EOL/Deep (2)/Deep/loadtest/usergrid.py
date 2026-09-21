#!/usr/bin/env python3
"""usergrid.py — 15 real MES users, each in its own pane, driving the REAL UI.

Operator: "15 user alag alag dock window se kaam karte hue emulator chala —
screen pe har user alag alag kya kar raha hai, exact UI ke saath."

Everything before this measured the API.  This runs the ACTUAL front end: each
pane is an iframe loading the built app, signed in as a DIFFERENT real account,
walking a different page, and the pane header shows what that user is on and
how long the page took.

The trick that makes 15 different sessions possible in one screen: same-origin
iframes share the tab's sessionStorage, so every pane pointed at one host would
be the same user.  Each pane instead uses its own loopback address
(127.0.0.1 … 127.0.0.15) — same server, fifteen distinct ORIGINS, fifteen
independent sessions.  dist/emulogin.html seeds each one (loopback only).

    python3 loadtest/usergrid.py --users 15 --port 8095
    open http://127.0.0.1:8095/usergrid.html
"""
import argparse, json, os, sys, threading, time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "Phase2"))


def db():
    import psycopg2
    c = psycopg2.connect(dbname="energydb", user="postgres", password="tbdi@123",
                         host="127.0.0.1", port=5432)
    c.autocommit = True
    return c


def dc(c):
    import psycopg2.extras
    return c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def build_config(n):
    """Real accounts, supervisors first — the people who actually use these pages."""
    from auth import create_token            # type: ignore
    with db() as c:
        cur = dc(c)
        cur.execute("""SELECT id, username, role FROM mes_admin
                        ORDER BY CASE role
                                   WHEN 'shift_incharge'      THEN 0
                                   WHEN 'production_incharge' THEN 1
                                   WHEN 'section_incharge'    THEN 2
                                   WHEN 'leader'              THEN 3
                                   WHEN 'operator'            THEN 4
                                   WHEN 'production'          THEN 5
                                   ELSE 6 END, id
                        LIMIT %s""", (n,))
        users = [dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT id, line_name AS name FROM mes_lines
                        WHERE COALESCE(is_active, TRUE)
                          AND db_table_name IS NOT NULL AND db_table_name <> ''
                        ORDER BY line_name""")
        lines = [dict(r) for r in cur.fetchall()]

    for u in users:
        # A token the account already deserves — no password is read or needed.
        u["token"] = create_token(u["username"], u["role"], u["id"])
    return {"users": users, "lines": lines}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=15)
    ap.add_argument("--port", type=int, default=8095)
    a = ap.parse_args()

    cfg = build_config(a.users)
    if not cfg["users"]:
        print("[usergrid] no accounts found"); return
    with open(os.path.join(HERE, "grid_users.json"), "w") as fh:
        json.dump(cfg, fh)

    print(f"[usergrid] {len(cfg['users'])} users, each on its own origin:")
    for i, u in enumerate(cfg["users"]):
        print(f"    pane {i+1:>2}  127.0.0.{i+1:<3}  {u['username']} ({u['role']})")
    print(f"[usergrid] LIVE → http://127.0.0.1:{a.port}/usergrid.html")

    handler = partial(SimpleHTTPRequestHandler, directory=HERE)
    srv = ThreadingHTTPServer(("0.0.0.0", a.port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
