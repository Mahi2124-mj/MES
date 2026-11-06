"""DB Structure (Admin) — 2026-10-29.
A read-only tree of the database: groups -> tables -> columns, with live row
counts, sizes and current DB activity, so the admin can watch the DB in real
time.  Admin-only.  Everything here is pure catalog / pg_stat reads (no table
scans except the explicit on-demand exact COUNT(*))."""
from __future__ import annotations

import re
from fastapi import APIRouter, Depends, Query

from database import get_conn, dict_cursor
from auth import require_admin

db_router = APIRouter(prefix="/api/admin/db", tags=["db-structure"])


def _group(name: str) -> str:
    """A friendly category for the tree, from the table name."""
    n = name.lower()
    if re.search(r"_ct_log(_p\w+|_old)?$", n):          return "Cycle logs (ct_log)"
    if n.endswith("_dashboard") or n.endswith("_complete"): return "Line dashboards"
    if n.startswith("mes_vcov") or "camera" in n or "clip" in n: return "Video / cameras"
    if n.startswith("mes_weld") or n.startswith("mes_gas"): return "Weld / gas / air"
    if n.startswith("mes_submachine"):                   return "Sub-machine"
    if n.startswith("mes_l6_"):                          return "L6 stations"
    if n.startswith("mes_py") or "poka" in n or "bypass" in n: return "Poka-yoke"
    if "fault" in n or "andon" in n or "breakdown" in n or "slip" in n: return "Maintenance / faults"
    if n.startswith("mes_user") or "escalation" in n or "permission" in n or "employee" in n: return "Users / access"
    if n.startswith("mes_"):                             return "MES core"
    return "Other"


@db_router.get("/structure")
def db_structure(admin=Depends(require_admin)):
    """All public tables grouped, each with est. rows, size and its columns."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT current_database() AS db, "
                    "pg_size_pretty(pg_database_size(current_database())) AS size, "
                    "pg_database_size(current_database()) AS bytes")
        db = cur.fetchone()
        cur.execute("""
            SELECT c.relname AS name,
                   pg_total_relation_size(c.oid) AS bytes,
                   pg_size_pretty(pg_total_relation_size(c.oid)) AS size,
                   pg_size_pretty(pg_total_relation_size(c.oid) - pg_relation_size(c.oid)) AS index_size,
                   c.reltuples::bigint AS est_rows
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind = 'r'
             ORDER BY c.relname
        """)
        tbls = {r["name"]: {**r, "columns": []} for r in cur.fetchall()}
        cur.execute("""
            SELECT table_name, column_name, data_type, is_nullable
              FROM information_schema.columns
             WHERE table_schema = 'public'
             ORDER BY table_name, ordinal_position
        """)
        for r in cur.fetchall():
            t = tbls.get(r["table_name"])
            if t is not None:
                t["columns"].append({"name": r["column_name"],
                                     "type": _short_type(r["data_type"]),
                                     "nullable": r["is_nullable"] == "YES"})
        # ── relationships: real FOREIGN KEYS + links inferred from id columns ──
        # pg_catalog (conkey/confkey with ordinality) pairs the columns correctly;
        # the information_schema 3-table join cross-multiplies and must not be used.
        cur.execute("""
            SELECT c1.relname AS t, a1.attname AS col,
                   c2.relname AS rt, a2.attname AS rc
              FROM pg_constraint con
              JOIN pg_class c1 ON c1.oid = con.conrelid
              JOIN pg_class c2 ON c2.oid = con.confrelid
              JOIN pg_namespace n ON n.oid = con.connamespace
              JOIN unnest(con.conkey)  WITH ORDINALITY AS ck(attnum, ord)  ON TRUE
              JOIN unnest(con.confkey) WITH ORDINALITY AS cfk(attnum, ord) ON cfk.ord = ck.ord
              JOIN pg_attribute a1 ON a1.attrelid = con.conrelid  AND a1.attnum = ck.attnum
              JOIN pg_attribute a2 ON a2.attrelid = con.confrelid AND a2.attnum = cfk.attnum
             WHERE con.contype = 'f' AND n.nspname = 'public'
        """)
        fk_col, ref_by = {}, {}
        for r in cur.fetchall():
            fk_col[(r["t"], r["col"])] = (r["rt"], r["rc"])
            ref_by.setdefault(r["rt"], []).append({"table": r["t"], "col": r["col"], "inferred": False})
        conn.rollback()
    # id-column -> hub table (used only where there is no real FK on that column)
    INFER = {"line_id": "mes_lines", "zone_id": "mes_zones", "plant_id": "mes_plants",
             "machine_id": "mes_plc_configs", "sub_plc_id": "mes_plc_configs",
             "plc_config_id": "mes_plc_configs", "parent_plc_id": "mes_plc_configs",
             "user_id": "mes_users", "fg_part_id": "mes_fg_parts"}
    for name, t in tbls.items():
        for col in t["columns"]:
            key = (name, col["name"])
            if key in fk_col:
                rt, rc = fk_col[key]
                col["ref"] = {"to": rt, "rcol": rc, "inferred": False}
            elif col["name"] in INFER and INFER[col["name"]] in tbls and INFER[col["name"]] != name:
                rt = INFER[col["name"]]
                col["ref"] = {"to": rt, "rcol": "id", "inferred": True}
                ref_by.setdefault(rt, []).append({"table": name, "col": col["name"], "inferred": True})
    # fold into groups
    groups: dict[str, list] = {}
    for name, t in tbls.items():
        refs = [{"col": c["name"], **c["ref"]} for c in t["columns"] if c.get("ref")]
        g = _group(name)
        groups.setdefault(g, []).append({
            "name": name, "bytes": int(t["bytes"] or 0), "size": t["size"],
            "index_size": t["index_size"], "est_rows": int(t["est_rows"] or 0),
            "n_cols": len(t["columns"]), "columns": t["columns"],
            "refs": refs, "ref_by": sorted(ref_by.get(name, []), key=lambda x: x["table"]),
        })
    out = [{"group": g, "bytes": sum(x["bytes"] for x in ts),
            "tables": sorted(ts, key=lambda x: -x["bytes"])}
           for g, ts in groups.items()]
    out.sort(key=lambda x: -x["bytes"])
    return {"database": db["db"], "size": db["size"], "bytes": int(db["bytes"] or 0),
            "table_count": len(tbls), "groups": out}


@db_router.get("/live")
def db_live(count: str | None = Query(None), admin=Depends(require_admin)):
    """Live snapshot: per-table est rows + size (for the tree to refresh), the
    current DB activity, and — if `count` names a table — its EXACT row count."""
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute("""
            SELECT c.relname AS name, c.reltuples::bigint AS est_rows,
                   pg_total_relation_size(c.oid) AS bytes,
                   pg_size_pretty(pg_total_relation_size(c.oid)) AS size,
                   COALESCE(s.n_tup_ins, 0) AS inserts, COALESCE(s.n_live_tup, 0) AS live_tup
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
              LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
             WHERE n.nspname = 'public' AND c.relkind = 'r'
        """)
        tables = {r["name"]: {"est_rows": int(r["est_rows"] or 0), "bytes": int(r["bytes"] or 0),
                              "size": r["size"], "inserts": int(r["inserts"] or 0),
                              "live_tup": int(r["live_tup"] or 0)} for r in cur.fetchall()}
        cur.execute("SELECT COALESCE(state, 'other') AS state, COUNT(*) AS n "
                    "FROM pg_stat_activity WHERE datname = current_database() GROUP BY 1")
        by_state = {r["state"]: int(r["n"]) for r in cur.fetchall()}
        cur.execute("""
            SELECT pid, COALESCE(usename,'') AS usr,
                   EXTRACT(EPOCH FROM (now() - query_start))::int AS age_s,
                   wait_event_type AS wait,
                   LEFT(regexp_replace(query, '\\s+', ' ', 'g'), 160) AS query
              FROM pg_stat_activity
             WHERE datname = current_database() AND state = 'active'
               AND query NOT ILIKE '%pg_stat_activity%'
             ORDER BY age_s DESC NULLS LAST LIMIT 12
        """)
        active = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT COUNT(*) AS n FROM pg_locks WHERE NOT granted")
        waiters = cur.fetchone()["n"]
        exact = None
        if count:
            cur.execute("SELECT to_regclass(%s) AS t", ("public." + count,))
            if (cur.fetchone() or {}).get("t"):
                cur.execute("SET LOCAL statement_timeout = '15s'")
                try:
                    cur.execute(f'SELECT COUNT(*) AS n FROM "{count}"')
                    exact = {"table": count, "rows": int(cur.fetchone()["n"])}
                except Exception as e:
                    exact = {"table": count, "error": str(e)[:80]}
        conn.rollback()
    return {"tables": tables,
            "activity": {"by_state": by_state, "lock_waiters": int(waiters or 0), "active": active},
            "exact": exact}


def _short_type(t: str) -> str:
    return {"timestamp without time zone": "timestamp", "timestamp with time zone": "timestamptz",
            "character varying": "varchar", "double precision": "float8",
            "integer": "int", "bigint": "int8", "boolean": "bool", "numeric": "numeric",
            "character": "char", "text": "text"}.get(t, t)
