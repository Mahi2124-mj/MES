# ───────────────────────────────────────────────────────────────────────
# ai_local.py   —   Floating chatbot on a LOCAL LLM (Ollama), no cloud
# ───────────────────────────────────────────────────────────────────────
# 2026-10-08 — Operator: "mera agent local db se sab data dikhaye aur page pe
# ja sake", free, data never leaving the plant.  ai_offline.py stays the first
# stop (instant, exact) for the production / OEE / loss questions it knows;
# everything else comes here.
#
# How it answers
#   1. "<page> kholo / open <page>"         -> navigate action, no model call
#   2. otherwise the local model writes ONE SELECT over a few CURATED views
#      (line_shift, faults, py_bypass, cameras, comments).  The views are
#      generated here per request as a WITH prefix — no DB objects are created
#      — and they only contain the lines this user may see.
#   3. The SELECT is checked BEFORE it runs, in code (a prompt is not a
#      control): single statement, no dangerous functions, and EXPLAIN must
#      show that it touches only the tables behind the views.  It then runs in
#      a READ ONLY transaction with an 8 s timeout.
#   4. A failing SELECT is sent back to the model once with the error text.
#
# 2026-10-09 — tuned on 2114 real-style questions sent from the app (eval
# harness: /home/server/ollama-test/eval): line / zone names, dates, shifts and
# the KIND of answer (total vs per line, machine vs fault, "right now") are
# resolved in code and handed to the model as exact hints; answers carry a
# context line ("YNC-SS — 08 Oct") read from the SQL that actually ran.
#
# The model is qwen2.5-coder:3b behind `ollama serve` on 127.0.0.1:11500
# (user unit ollama-mes.service).  It runs on the CPU on purpose: the GPU's
# memory belongs to the camera/clip ffmpeg sessions, and a model holding VRAM
# could make a new NVENC session fail — i.e. lose video.
# ───────────────────────────────────────────────────────────────────────
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, date

from psycopg2 import sql as psql

from database import get_conn, dict_cursor

LLM_URL   = os.getenv("AI_LLM_URL", "http://127.0.0.1:11500")
LLM_MODEL = os.getenv("AI_LLM_MODEL", "qwen2.5-coder:3b")
LLM_TIMEOUT_S = float(os.getenv("AI_LLM_TIMEOUT_S", "60"))
MAX_ROWS = 15

# key -> (path, label, words that name it).  Keys are SlideNav's own keys, so
# the frontend can check canAccess(key) before it navigates.
PAGES = {
    "dashboard":         ("/dashboard",          "Production Dashboard", ["dashboard", "home", "production dashboard"]),
    "historical":        ("/historical",         "Historical",           ["historical", "history report", "hourly report", "report"]),
    "fault-history":     ("/fault-history",      "Fault History",        ["fault", "faults", "fault history"]),
    "py-bypass":         ("/py-bypass",          "PY Bypass",            ["bypass", "py bypass", "poka yoke bypass"]),
    "video-coverage":    ("/video-coverage",     "Video Coverage",       ["video coverage", "camera", "cameras", "coverage"]),
    "shift-compile":     ("/shift-compile",      "Shift Compile",        ["shift compile", "compile"]),
    "comments-history":  ("/comments-history",   "Comments History",     ["comment", "comments", "remarks"]),
    "andon-history":     ("/andon-history",      "Andon History",        ["andon"]),
    "prod-breakdown-slip": ("/prod-breakdown-slip", "Breakdown Slip",    ["breakdown slip", "slip"]),
    "my-escalations":    ("/my-escalations",     "Inbox",                ["inbox", "alerts", "notification", "notifications", "my escalations", "escalations"]),
    "weld-monitor":      ("/weld-monitor",       "Weld Monitor",         ["weld", "welding", "gas flow"]),
    "bin-filling":       ("/bin-filling",        "Bin Filling",          ["bin filling", "bin"]),
    "shift-allocation":  ("/shift-allocation",   "Shift Allocation",     ["allocation", "manpower"]),
    "quality-dashboard": ("/quality-dashboard",  "Quality Dashboard",    ["quality dashboard", "quality"]),
    "maintenance-dashboard": ("/maintenance-dashboard", "Maintenance Dashboard", ["maintenance"]),
    "logs":              ("/logs",               "Log Viewer",           ["log viewer", "logs"]),
    "six-sigma":         ("/six-sigma",          "6 Sigma",              ["six sigma", "6 sigma", "ball guide"]),
    "redbin-lock":       ("/redbin-lock",        "Red Bin Lock",         ["red bin"]),
    "settings":          ("/settings",           "Settings",             ["settings", "password"]),
}

_OPEN_WORDS = re.compile(r"\b(kholo|khol|kholna|khol\s*do|open|le\s*chalo|le\s*chal|chalo|jao|go\s*to|navigate|"
                         r"dikhao\s+page|page\s+dikhao|wala\s+page|page\s+pe|page\s+par|jana\s+hai|take\s+me|le\s+jao|"
                         r"screen\s+dikhao|dikhao\s+screen|wala\s+screen|wali\s+screen|bring\s+up|pull\s+up|"
                         r"(show|display)\s+(me\s+)?(the\s+)?[\w\s-]{0,30}?\b(page|screen))\b", re.I)

# "open bypass kitne hain" is a data question, not "open the page".
_DATA_WORDS = re.compile(r"\b(kitne|kitna|kitni|kaun|kaunsa|kaunse|kya|kis|how\s+many|how\s+much|"
                         r"count|total|sabse|which|list|batao|bata)\b", re.I)

# Anything that is not plain data reading.  Checked on the model's SQL text.
_BLOCK = re.compile(
    r"\bpg_|dblink|\blo_|\bcopy\b|set_config|current_setting|query_to_xml|"
    r"\binsert\b|\bupdate\b|\bdelete\b|\bdrop\b|\balter\b|\bcreate\b|\bgrant\b|"
    r"\brevoke\b|\btruncate\b|\bvacuum\b|\bcall\b|\bdo\b\s*\$|\bexecute\b|\binto\b",
    re.I)

_SHARED_TABLES = {"mes_lines", "mes_zones", "mes_fault_history", "mes_py_bypass_cases",
                  "mes_vcov_line_snap", "mes_cycle_comments"}


# ── production day / shift (B runs past midnight on the previous date) ──
def _prod_day_and_shift(now=None):
    now = now or datetime.now()
    t = now.time()
    def hm(s):
        return datetime.strptime(s, "%H:%M").time()
    if t < hm("03:15"):
        return (now.date() - timedelta(days=1)), "B"
    if t < hm("08:30"):
        return (now.date() - timedelta(days=1)), "GAP"
    if t < hm("17:15"):
        return now.date(), "A"
    if t < hm("18:30"):
        return now.date(), "GAP"
    return now.date(), "B"


# ── the views ──────────────────────────────────────────────────────────
def _scoped_lines(cur, user):
    """Lines this user may see, with zone name and their own shift table."""
    from routers.shift_compile import _accessible_lines
    rows = _accessible_lines(cur, user) or []
    ids = [r["id"] for r in rows]
    if not ids:
        return []
    cur.execute("""SELECT l.id, l.line_name, l.db_table_name, COALESCE(z.zone_name,'') AS zone_name
                     FROM mes_lines l LEFT JOIN mes_zones z ON z.id = l.zone_id
                    WHERE l.id = ANY(%s) AND COALESCE(l.db_table_name,'') <> ''
                      AND to_regclass(l.db_table_name) IS NOT NULL
                    ORDER BY z.zone_name, l.line_name""", (ids,))
    return cur.fetchall()


_LOSS = [("loss_breakdown_seconds", "breakdown_s"), ("loss_setup_seconds", "setup_s"),
         ("loss_material_seconds", "material_s"), ("loss_quality_seconds", "quality_loss_s"),
         ("loss_speed_seconds", "speed_loss_s"), ("loss_change_over_seconds", "changeover_s"),
         ("loss_others_seconds", "other_loss_s")]


_LIVE_CACHE = {}          # line_id -> (fetched_at, realtime row)
_LIVE_POOL = __import__("concurrent.futures").futures.ThreadPoolExecutor(max_workers=2)
_LIVE_TTL_S = 20.0


def _live_rows(lines):
    """The running shift of each line exactly as the dashboard shows it
    (routers.lines.get_line_realtime — the same function /realtime serves).
    Cached 20 s per API worker; fetched 8 at a time (~0.2 s each)."""
    try:
        from routers.lines import get_line_realtime
    except Exception:
        return {}
    from concurrent.futures import ThreadPoolExecutor
    now = time.time()
    out, need = {}, []
    for l in lines:
        c = _LIVE_CACHE.get(l["id"])
        if c and now - c[0] < _LIVE_TTL_S:
            out[l["id"]] = c[1]
        else:
            need.append(l["id"])

    def one(lid):
        try:
            return lid, get_line_realtime(lid, {"id": 0, "username": "ai-assistant", "role": "admin"})
        except Exception:
            return lid, None
    if need:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for lid, r in ex.map(one, need):
                if isinstance(r, dict) and r.get("record_date") and r.get("shift_name") in ("A", "B"):
                    _LIVE_CACHE[lid] = (time.time(), r)
                    out[lid] = r
    return out


# 2026-10-19 — finished shifts of the last 7 days carry the OEE + losses the
# Dashboard shows (lines.display_loss_oee: andon buckets + parts-based speed).
# Built per line in a background thread and kept 10 min, so an answer never
# waits on it; until the first build those rows keep the stored values.
_PAST_COLS = {"overall_oee": "d_oee", "availability": "d_av", "performance": "d_pf",
              **{c: f"d_l{i}" for i, (c, _a) in enumerate(_LOSS)}}
_PAST_DISP = {}           # line_id -> (built_at, {(record_date, shift): {col: value}}, full_at)
_PAST_BUSY = set()
_PAST_LOCK = __import__("threading").Lock()
_PAST_TTL_S = 600.0
_PAST_DAYS = 62            # 2026-10-29: was 7 -- "is mahine" / "21 tarikh" got the stored OEE + losses
_PAST_RECENT_DAYS = 3      # every 10 min only these days are built again (older shifts hardly change)
_PAST_FULL_S = 6 * 3600.0  # all _PAST_DAYS again every 6 h
_PAST_POOL = __import__("concurrent.futures").futures.ThreadPoolExecutor(max_workers=1)


def _past_build(lid, tbl, days):
    try:
        from routers.lines import display_loss_oee
        m = {}
        with get_conn() as c:
            cur = dict_cursor(c)
            cur.execute(psql.SQL(
                "SELECT DISTINCT ON (record_date, shift_name) * FROM {} "
                "WHERE record_date >= current_date - %s AND shift_name IN ('A','B') "
                "AND COALESCE(ok_count,0) + COALESCE(ng_count,0) > 0 "
                "ORDER BY record_date, shift_name, timestamp DESC").format(psql.Identifier(tbl)), (days,))
            for r in cur.fetchall():
                d = display_loss_oee(cur, lid, r, r["record_date"], r["shift_name"])
                if days > _PAST_RECENT_DAYS:
                    time.sleep(0.01)        # the long build gives way to the API's requests
                if d.get("overall_oee") is None:
                    continue
                v = {k: d.get(k) for k in ("overall_oee", "availability", "performance")}
                for k, s in (d.get("losses") or {}).items():
                    v[f"loss_{k}_seconds"] = s
                m[(r["record_date"], r["shift_name"])] = v
            c.rollback()
        now = time.time()
        old = _PAST_DISP.get(lid)
        if days >= _PAST_DAYS or not old:
            _PAST_DISP[lid] = (now, m, now)
        else:                               # keep the older days, replace the recent ones
            cut = date.today() - timedelta(days=days)
            low = date.today() - timedelta(days=_PAST_DAYS)
            keep = {k: v for k, v in old[1].items() if low <= k[0] < cut}
            keep.update(m)
            _PAST_DISP[lid] = (now, keep, old[2])
    except Exception as exc:
        print(f"[AI-LOCAL] past display oee skipped line {lid}: {exc}")
    finally:
        with _PAST_LOCK:
            _PAST_BUSY.discard(lid)


def _past_display(lines):
    out = {}
    now = time.time()
    for l in lines:
        hit = _PAST_DISP.get(l["id"])
        if hit:
            out[l["id"]] = hit[1]
        if hit and now - hit[0] <= _PAST_TTL_S:
            continue
        days = _PAST_DAYS if not hit or now - hit[2] > _PAST_FULL_S else _PAST_RECENT_DAYS
        with _PAST_LOCK:
            start = l["id"] not in _PAST_BUSY
            if start:
                _PAST_BUSY.add(l["id"])
        if start:
            _PAST_POOL.submit(_past_build, l["id"], l["db_table_name"], days)
    return out


def _views_sql(conn, lines, live=None, since=None):
    """WITH prefix defining the curated views over this user's lines.
    The running shift's row takes the live values (live = _live_rows());
    recent finished shifts take the Dashboard's OEE + losses (_past_display)."""
    ids = [l["id"] for l in lines]
    parts = []
    live = live or {}
    past = _past_display(lines)

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def lit_sql(v):
        return psql.Literal(v).as_string(conn).replace("{", "{{").replace("}", "}}")
    for l in lines:
        r = live.get(l["id"])
        pm = past.get(l["id"]) or {}
        if since is not None:      # only the days the question can touch: the SQL stays small
            pm = {k: v for k, v in pm.items() if k[0] >= since}
        join = ""
        if pm:
            vals = ",".join(
                "(" + ", ".join([lit_sql(rd) + "::date", lit_sql(sh)]
                                + [lit_sql(num(v.get(c))) + "::numeric" for c in _PAST_COLS]) + ")"
                for (rd, sh), v in pm.items())
            join = (f" LEFT JOIN (VALUES {vals}) AS d(d_rd, d_sh, {', '.join(_PAST_COLS.values())})"
                    " ON d.d_rd = record_date AND d.d_sh = shift_name")

        def base(col):
            return f"COALESCE(d.{_PAST_COLS[col]}, {col})" if pm and col in _PAST_COLS else col
        if r:
            cond = (f"(record_date = {psql.Literal(r['record_date']).as_string(conn)} "
                    f"AND shift_name = {psql.Literal(r['shift_name']).as_string(conn)})")
            def ov(col, key):
                v = r.get(key)
                lit = psql.Literal(v if isinstance(v, str) else num(v)).as_string(conn)
                lit = lit.replace("{", "{{").replace("}", "}}")     # the text goes through psql.SQL().format()
                return f"CASE WHEN {cond} THEN {lit} ELSE {base(col)} END"
        else:
            def ov(col, key):
                return base(col)
        loss_cols = ", ".join(f"COALESCE({ov(c, c)},0) AS {a}" for c, a in _LOSS)
        loss_sum = " + ".join(f"COALESCE({ov(c, c)},0)" for c, _ in _LOSS)
        q = psql.SQL(
            "SELECT {lid} AS line_id, {lname}::text AS line_name, {zname}::text AS zone_name, "
            "record_date, shift_name::text AS shift_name, shift_plan AS plan, "
            f"COALESCE({ov('shift_plan_completed', 'shift_plan_completed')},0) AS plan_till_now, "
            # the card's ACTUAL = ok + ng; ok alone = good parts
            f"COALESCE({ov('ok_count', 'ok_count')},0) + COALESCE({ov('ng_count', 'ng_count')},0) AS produced, "
            f"COALESCE({ov('ok_count', 'ok_count')},0) AS ok, "
            f"COALESCE({ov('ng_count', 'ng_count')},0) AS ng, "
            # 0 = line not running that shift; the dashboard leaves those out of averages
            f"NULLIF({ov('overall_oee', 'overall_oee')},0) AS oee, "
            f"{ov('availability', 'availability')} AS availability, "
            f"{ov('performance', 'performance')} AS performance, "
            f"{ov('quality_oee', 'quality_oee')} AS quality, "
            + loss_cols + ", (" + loss_sum + ") AS total_loss_s, "
            f"({ov('current_model_name', 'current_model_name')})::text AS model, "
            f"({ov('operating_status', 'operating_status')})::text AS status, "
            + (f"CASE WHEN {cond} THEN now() ELSE updated_at END AS updated_at " if r else "updated_at ") +
            "FROM {tbl}" + join + " WHERE shift_name IN ('A','B')"
        ).format(lid=psql.Literal(l["id"]), lname=psql.Literal(l["line_name"]),
                 zname=psql.Literal(l["zone_name"]), tbl=psql.Identifier(l["db_table_name"]))
        parts.append(q.as_string(conn))
    line_shift = " UNION ALL ".join(parts) if parts else (
        "SELECT NULL::int AS line_id, NULL::text AS line_name, NULL::text AS zone_name, "
        "NULL::date AS record_date, NULL::text AS shift_name WHERE FALSE")
    id_arr = "ARRAY[" + ",".join(str(int(i)) for i in ids) + "]::int[]" if ids else "ARRAY[]::int[]"
    views = {
        "line_shift": line_shift,
        "line_now": (
            "SELECT DISTINCT ON (line_id) line_id, line_name, zone_name, record_date, shift_name, "
            "status, model, produced, ok, ng, oee, updated_at FROM line_shift "
            "ORDER BY line_id, record_date DESC, updated_at DESC NULLS LAST"),
        "faults": (
            "SELECT f.line_id, l.line_name::text AS line_name, z.zone_name::text AS zone_name, "
            "f.machine_name, f.fault_name, f.started_at, f.ended_at, "
            "ROUND(COALESCE(f.duration_s,0))::int AS duration_s, f.record_date, f.shift_name "
            "FROM mes_fault_history f JOIN mes_lines l ON l.id = f.line_id "
            "LEFT JOIN mes_zones z ON z.id = l.zone_id "
            f"WHERE f.line_id = ANY({id_arr})"),
        "py_bypass": (
            "SELECT line_id, line_name, zone_name, py_no, py_name, status, shift_name, "
            "detected_at, detected_at::date AS record_date, decided_by, decided_at, closed_at "
            f"FROM mes_py_bypass_cases WHERE line_id = ANY({id_arr})"),
        "cameras": (
            "SELECT line_id, line_name, zone_name, cameras, online, hung, offline, ts AS checked_at "
            "FROM mes_vcov_line_snap WHERE ts = (SELECT max(ts) FROM mes_vcov_line_snap) "
            f"AND line_id = ANY({id_arr})"),
        "comments": (
            "SELECT c.line_id, l.line_name::text AS line_name, c.machine_name, c.part_code, "
            "c.comment, c.author, c.leader_name, c.record_date, c.shift_name, c.created_at "
            "FROM mes_cycle_comments c JOIN mes_lines l ON l.id = c.line_id "
            f"WHERE c.line_id = ANY({id_arr})"),
    }
    prefix = "WITH " + ",\n".join(f"{k} AS NOT MATERIALIZED ({v})" for k, v in views.items())
    allowed = {l["db_table_name"].lower() for l in lines} | _SHARED_TABLES
    return prefix, allowed


def _schema_text(lines, prod_day, shift):
    names = ", ".join(l["line_name"] for l in lines) or "(none)"
    zones = ", ".join(sorted({l["zone_name"] for l in lines if l["zone_name"]})) or "(none)"
    pages = ", ".join(PAGES)
    return f"""VIEWS (the ONLY tables you may use):
line_shift(line_id, line_name, zone_name, record_date, shift_name, plan, plan_till_now, produced, ok, ng,
           oee, availability, performance, quality, breakdown_s, setup_s, material_s,
           quality_loss_s, speed_loss_s, changeover_s, other_loss_s, total_loss_s,
           model, status, updated_at)
   -> one row per line per day per shift ('A' or 'B'). produced = parts made (OK + NG,
      the dashboard's ACTUAL); ok = good parts; plan = full shift plan; plan_till_now = plan so far.
      oee/availability/performance/quality are percent. *_s columns are seconds of loss.
line_now(line_id, line_name, zone_name, record_date, shift_name, status, model, produced, ok, ng,
         oee, updated_at)
   -> each line RIGHT NOW (its latest row). status is RUNNING, IDLE (stopped) or BREAK.
faults(line_id, line_name, zone_name, machine_name, fault_name, started_at, ended_at,
       duration_s, record_date, shift_name)   -> one row per machine fault occurrence
py_bypass(line_id, line_name, zone_name, py_no, py_name, status, shift_name, detected_at,
          record_date, decided_by, decided_at, closed_at)
   -> poka-yoke (PY) bypass cases; status is WAITING (open), APPROVED, REJECTED or CLEARED
cameras(line_id, line_name, zone_name, cameras, online, hung, offline, checked_at)
   -> camera status RIGHT NOW, one row per line (sum the columns for the plant)
comments(line_id, line_name, machine_name, part_code, comment, author, leader_name,
         record_date, shift_name, created_at)   -> remarks written on cycles

LINE NAMES: {names}
ZONES: {zones}
PAGES: {pages}

Production date of TODAY = '{prod_day}'. The shift running now = '{shift}'
(use it ONLY when the user says "is shift" / "this shift" / "abhi ki shift").
"""


_SYSTEM = """You turn plant questions (English or Hinglish) into ONE PostgreSQL query
for a Toyota Boshoku MES. Reply with the SQL only — no prose, no markdown.

{schema}
RULES:
- Exactly one SELECT, using only the views above. Never any other table.
- Dates: aaj/today -> record_date = '{d0}'.  kal/yesterday -> record_date = '{d1}'.
  is hafte/this week -> record_date >= '{wk}'.  is mahine/this month -> record_date >= '{mo}'.
  No date given -> today.  The cameras view has NO date — never filter it by date.
- Do NOT filter shift_name unless the user names shift A or B or says "is shift".
  A day = both shifts together.
- Line: line_name ILIKE '%<name>%' with a name from LINE NAMES.  Zone: zone_name ILIKE '%<zone>%'.
- A [line: ...], [zone: ...], [date filter: ...], [shift filter: ...], [measure: ...] or
  other hint after the question is exact and already checked — follow it as written.
- "kitne / kitna / how many / total / count" -> ONE total number, no GROUP BY — unless the
  user asks per line: "kis line / which line / har line / line wise / per line / sabse zyada".
- A zone or plant total is one SUM over all its rows (no GROUP BY).
- Faults on a MACHINE ("kis machine", "which machine") -> GROUP BY machine_name.
- Comments / remarks count -> SELECT COUNT(*) AS comments FROM comments ...
- Comparing shifts ("A vs B", "which shift") -> GROUP BY shift_name and show BOTH rows (no LIMIT).
- Time is always returned in SECONDS with an _s alias — never divide by 60 or 3600; the app
  formats it (even when the user asks for minutes or hours).
- production / output / kitne bane = SUM(produced) (OK + NG).  OK / good parts = SUM(ok).
  NG/reject = SUM(ng).  loss = SUM(total_loss_s).
  breakdown = SUM(breakdown_s).  OEE of a line for a day = ROUND(AVG(oee),1) — never SUM a percent.
- "kis line par / which line / har line / all lines" -> GROUP BY line_name, ORDER BY the
  value, LIMIT 10.  Always SELECT the value you order by, next to line_name.
- Use short English aliases: produced, ok, ng, oee, loss_s, breakdown_s, faults, bypasses, hung.
  Name every seconds value with an _s ending.
- Open / pending bypass -> status = 'WAITING'.
- comments: SELECT created_at, line_name, machine_name, comment, author ... ORDER BY created_at DESC LIMIT 10.
- A [date filter: ...] or [shift filter: ...] after the question is exact — use it as written.
- "abhi / right now / current" status or model of lines -> use line_now (no date filter).
- If the user asks to OPEN or GO TO a page, reply exactly: NAVIGATE: <page>  (page from PAGES).
- If the views cannot answer it, reply exactly: UNKNOWN

EXAMPLES:
Q: YSD-SS ka aaj ka production
SELECT SUM(produced) AS produced FROM line_shift WHERE line_name ILIKE '%YSD-SS%' AND record_date = '{d0}'
Q: kal shift A me har line ka NG
SELECT line_name, SUM(ng) AS ng FROM line_shift WHERE record_date = '{d1}' AND shift_name = 'A' GROUP BY line_name ORDER BY ng DESC LIMIT 10
Q: is hafte sabse zyada breakdown kis line par
SELECT line_name, SUM(breakdown_s) AS breakdown_s FROM line_shift WHERE record_date >= '{wk}' GROUP BY line_name HAVING SUM(breakdown_s) > 0 ORDER BY breakdown_s DESC LIMIT 10
Q: aaj sab lines ka OEE
SELECT line_name, ROUND(AVG(oee),1) AS oee FROM line_shift WHERE record_date = '{d0}' GROUP BY line_name ORDER BY oee DESC LIMIT 10
Q: Recliner zone ka is mahine ka production
SELECT SUM(produced) AS produced FROM line_shift WHERE zone_name ILIKE '%Recliner%' AND record_date >= '{mo}'
Q: aaj kitne fault aaye
SELECT COUNT(*) AS faults FROM faults WHERE record_date = '{d0}'
Q: kal kaunsa fault sabse zyada aaya
SELECT fault_name, COUNT(*) AS faults FROM faults WHERE record_date = '{d1}' GROUP BY fault_name ORDER BY faults DESC LIMIT 10
Q: abhi kitne camera hung hain
SELECT SUM(hung) AS hung, SUM(cameras) AS cameras FROM cameras
Q: open bypass kitne hain
SELECT COUNT(*) AS bypasses FROM py_bypass WHERE status = 'WAITING'
Q: YCA-SS shift A vs shift B production aaj
SELECT shift_name, SUM(produced) AS produced, SUM(ng) AS ng FROM line_shift WHERE line_name ILIKE '%YCA-SS%' AND record_date = '{d0}' GROUP BY shift_name ORDER BY shift_name
Q: YJC-SS ka current model
SELECT model, status FROM line_now WHERE line_name ILIKE '%YJC-SS%'
Q: kaun si line abhi band hai
SELECT line_name, status, updated_at FROM line_now WHERE status <> 'RUNNING' ORDER BY line_name
Q: kal kitne bypass aaye  [date filter: record_date = '{d1}']
SELECT COUNT(*) AS bypasses FROM py_bypass WHERE record_date = '{d1}'
Q: kal sabse zyada bypass kis line par  [date filter: record_date = '{d1}']
SELECT line_name, COUNT(*) AS bypasses FROM py_bypass WHERE record_date = '{d1}' GROUP BY line_name ORDER BY bypasses DESC LIMIT 10
Q: YCA-SS pe kitne open bypass hain  [line: line_name = 'YCA-SS']
SELECT COUNT(*) AS bypasses FROM py_bypass WHERE line_name = 'YCA-SS' AND status = 'WAITING'
Q: is hafte sabse zyada fault kis machine pe  [date filter: record_date >= '{wk}'; group by machine_name]
SELECT machine_name, COUNT(*) AS faults FROM faults WHERE record_date >= '{wk}' GROUP BY machine_name ORDER BY faults DESC LIMIT 10
Q: YMC-SS pe kal kitne comments likhe  [line: line_name = 'YMC-SS'; date filter: record_date = '{d1}'; answer with COUNT(*) AS comments]
SELECT COUNT(*) AS comments FROM comments WHERE line_name = 'YMC-SS' AND record_date = '{d1}'
Q: seat slider zone ka is hafte ka production  [zone: zone_name = 'SEAT SLIDER'; date filter: record_date >= '{wk}']
SELECT SUM(produced) AS produced FROM line_shift WHERE zone_name = 'SEAT SLIDER' AND record_date >= '{wk}'
Q: is mahine plant me kitne NG  [date filter: record_date >= '{mo}']
SELECT SUM(ng) AS ng FROM line_shift WHERE record_date >= '{mo}'
Q: YJC-SS abhi chal rahi hai kya  [line: line_name = 'YJC-SS'; use view line_now]
SELECT status, model, updated_at FROM line_now WHERE line_name = 'YJC-SS'
Q: kal number 1 line kaunsi rahi production me  [date filter: record_date = '{d1}'; rank lines: GROUP BY line_name ORDER BY the value DESC]
SELECT line_name, SUM(produced) AS produced FROM line_shift WHERE record_date = '{d1}' GROUP BY line_name ORDER BY produced DESC LIMIT 10
Q: kis kis line pe production ruka hua hai  [use view line_now: status <> 'RUNNING']
SELECT line_name, status, updated_at FROM line_now WHERE status <> 'RUNNING' ORDER BY line_name
Q: YRA-SS ka kal A-B shift comparison  [line: line_name = 'YRA-SS'; date filter: record_date = '{d1}'; shift filter: both shifts — GROUP BY shift_name]
SELECT shift_name, SUM(produced) AS produced, SUM(ng) AS ng FROM line_shift WHERE line_name = 'YRA-SS' AND record_date = '{d1}' GROUP BY shift_name ORDER BY shift_name
Q: YFG-SS ka kal DT kitna  [line: line_name = 'YFG-SS'; date filter: record_date = '{d1}'; measure: SUM(total_loss_s)]
SELECT SUM(total_loss_s) AS total_loss_s FROM line_shift WHERE line_name = 'YFG-SS' AND record_date = '{d1}'
Q: fault history page kholo
NAVIGATE: fault-history
"""


def _ask_llm(messages, timeout=None):
    body = json.dumps({
        "model": LLM_MODEL, "stream": False, "keep_alive": -1,   # stay loaded (RAM only, CPU)
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 220,
                    "num_gpu": 0},   # CPU only — VRAM belongs to the camera ffmpeg sessions
        "messages": messages,
    }).encode()
    req = urllib.request.Request(LLM_URL + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout or LLM_TIMEOUT_S) as r:
        d = json.load(r)
    return (d.get("message") or {}).get("content", "").strip()


def _clean_sql(text):
    s = re.sub(r"^```[a-zA-Z]*\s*|```\s*$", "", text.strip(), flags=re.M).strip()
    s = s.rstrip().rstrip(";").strip()
    return s


def _check_and_run(conn, prefix, allowed, model_sql):
    """Validate the model's SELECT, then run it read-only.  Returns (rows, error)."""
    s = model_sql
    if ";" in s:
        return None, "only one statement is allowed"
    head = s.lstrip()[:6].upper()
    if head == "WITH" or head.startswith("WITH"):
        body = s.lstrip()[4:].lstrip()
        full = prefix + ",\n" + body
    elif head == "SELECT":
        full = prefix + "\n" + s
    else:
        return None, "not a SELECT"
    if _BLOCK.search(s):
        return None, "that query uses something that is not allowed"
    cur = dict_cursor(conn)
    try:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("SET LOCAL statement_timeout = '8s'")
        cur.execute("EXPLAIN (FORMAT JSON) " + full)
        plan = cur.fetchone()
        plan = list(plan.values())[0] if isinstance(plan, dict) else plan[0]
        rels = set()
        def walk(n):
            if isinstance(n, dict):
                if "Relation Name" in n:
                    rels.add(n["Relation Name"].lower())
                for v in n.values():
                    walk(v)
            elif isinstance(n, list):
                for v in n:
                    walk(v)
        walk(plan)
        bad = {r for r in rels if r not in allowed and not r.startswith("mes_submachine_ct_log")}
        if bad:
            conn.rollback()
            return None, "that query reads a table the assistant may not use"
        cur.execute(full)
        rows = cur.fetchmany(200)
        conn.rollback()
        return rows, None
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return None, str(e).split("\n")[0][:200]


# ── formatting ─────────────────────────────────────────────────────────
def _fmt(col, v):
    if v is None:
        return "-"
    c = col.lower()
    if "minute" in c or c.endswith("_min") or c.endswith("_mins"):
        try:
            return f"{float(v):.0f} min"
        except Exception:
            pass
    if "hour" in c or c.endswith("_hrs"):
        try:
            return f"{float(v):.1f} h"
        except Exception:
            pass
    if c.endswith("_s") or any(k in c for k in ("loss", "breakdown", "downtime", "duration", "seconds")):
        try:
            n = int(round(float(v)))
            return f"{n // 3600}:{(n % 3600) // 60:02d}:{n % 60:02d}"
        except Exception:
            pass
    if c in ("oee", "availability", "performance", "quality") or c.endswith("_oee") or c.endswith("pct"):
        try:
            return f"{float(v):.1f}%"
        except Exception:
            pass
    if isinstance(v, float) or (hasattr(v, "as_tuple") and not isinstance(v, int)):
        try:
            f = float(v)
            return str(int(f)) if f.is_integer() else f"{f:.2f}"
        except Exception:
            pass
    if isinstance(v, datetime):
        return v.strftime("%d %b %H:%M")
    if isinstance(v, date):
        return v.strftime("%d %b")
    return str(v)


def _label(col):
    return re.sub(r"_s$", "", col).replace("_", " ").strip()


def _format_rows(rows):
    if not rows:
        return "No matching data."
    cols = list(rows[0].keys())
    if len(rows) == 1 and len(cols) == 1:
        return f"{_label(cols[0]).capitalize()}: {_fmt(cols[0], rows[0][cols[0]])}"
    if len(rows) == 1:
        return "\n".join(f"{_label(c).capitalize()}: {_fmt(c, rows[0][c])}" for c in cols)
    out = [" · ".join(_label(c) for c in cols)]
    for r in rows[:MAX_ROWS]:
        out.append(" · ".join(_fmt(c, r[c]) for c in cols))
    if len(rows) > MAX_ROWS:
        out.append(f"… {len(rows) - MAX_ROWS} more")
    return "\n".join(out)


def _header(sql):
    """One line saying what was actually looked up — read from the SQL that
    ran, so the user sees at once if the line, day or shift is not what they
    meant ("YNC-SS — 08 Oct, shift B")."""
    q = sql or ""
    def d(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").strftime("%d %b")
        except Exception:
            return s
    m = re.findall(r"line_name\s*(?:=|ILIKE)\s*'%?([^'%]+)%?'", q, re.I)
    z = re.findall(r"zone_name\s*(?:=|ILIKE)\s*'%?([^'%]+)%?'", q, re.I)
    scope = ", ".join(dict.fromkeys(m)) if m else (z[0] + " zone" if z else "")
    if not scope:
        scope = "All lines" if re.search(r"group\s+by\s+line_name", q, re.I) else "Plant"
    eq = re.search(r"record_date\s*=\s*'(\d{4}-\d{2}-\d{2})'", q)
    ge = re.search(r"record_date\s*>=?\s*'(\d{4}-\d{2}-\d{2})'", q)
    le = re.search(r"record_date\s*(<=?)\s*'(\d{4}-\d{2}-\d{2})'", q)
    if eq:
        when = d(eq.group(1))
    elif ge and le:
        end = le.group(2)
        if le.group(1) == "<":
            end = str(datetime.strptime(end, "%Y-%m-%d").date() - timedelta(days=1))
        when = f"{d(ge.group(1))} – {d(end)}"
    elif ge:
        when = f"since {d(ge.group(1))}"
    elif re.search(r"\b(line_now|cameras)\b|status\s*=\s*'WAITING'", q, re.I):
        when = "right now"
    else:
        when = ""
    sh = re.search(r"shift_name\s*=\s*'([AB])'", q)
    return scope + (f" — {when}" if when else "") + (f", shift {sh.group(1)}" if sh else "")


def _by_shift(conn, prefix, allowed, sql, rows):
    """For "SELECT <agg> AS x FROM line_shift WHERE <one day, no shift>" add the
    same number per shift: "Shift A: 1860 · Shift B: 863"."""
    if not rows or len(rows) != 1 or len(rows[0]) != 1:
        return ""
    m = re.match(r"^\s*SELECT\s+(.+?)\s+AS\s+(\w+)\s+FROM\s+line_shift\s+WHERE\s+(.+?)\s*$", sql or "", re.I | re.S)
    if not m or re.search(r"shift_name|group\s+by|limit|order\s+by", m.group(3), re.I):
        return ""
    if not re.search(r"record_date\s*=\s*'\d{4}-\d{2}-\d{2}'", m.group(3)):
        return ""
    q = (f"SELECT shift_name, {m.group(1)} AS {m.group(2)} FROM line_shift WHERE {m.group(3)} "
         f"GROUP BY shift_name ORDER BY shift_name")
    r2, err = _check_and_run(conn, prefix, allowed, q)
    if err or not r2 or len(r2) < 2:          # one shift only: the day total says it already
        return ""
    col = m.group(2)
    return " · ".join(f"Shift {r['shift_name']}: {_fmt(col, r[col])}" for r in r2)


# ── navigation without the model ─────────────────────────────────────
def _page_from_words(text):
    t = " " + re.sub(r"[^a-z0-9 ]+", " ", text.lower()) + " "
    best, best_len = None, 0
    for key, (_p, _l, words) in PAGES.items():
        for w in words:
            if f" {w} " in t and len(w) > best_len:
                best, best_len = key, len(w)
    return best


def _nav_reply(key):
    path, label, _ = PAGES[key]
    return {"reply": f"Opening {label}…", "action": {"navigate": path, "page_key": key},
            "provider": "local"}


# ── line / zone names and dates, resolved in code ──────────────────────
# The model mistyped names ("YRA-Recliner"), missed "ync ss" and "5 oct".
# 2026-10-29 -- 1000 questions written the way the floor writes them ("ymc rec",
# "yhb 4way", "loop pipe l2", "lps3", "kl", "yday", "this wk", "mtd", "25/10",
# "26 tarikh", "dusri shift") were 78 % right; 180 of the 221 wrong answers came
# from a name, date or shift this code did not read.  They are read here now.
_MON = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MON_RX = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_MON_FULL = ["january", "february", "march", "april", "may", "june", "july", "august",
             "september", "october", "november", "december"]


def _norm_tokens(s):
    return [t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t]


_TOK_ALIAS = {"rec": "recliner", "recl": "recliner", "reclnr": "recliner", "reclinr": "recliner",
              "reclinar": "recliner", "reclainer": "recliner", "recliners": "recliner", "reclner": "recliner",
              "assy": "assy", "asy": "assy", "weld": "welding", "welding": "welding"}


_HI_NUM = {}
for _n, _ws in enumerate((
        "ek", "do", "teen tin", "char chaar", "paanch panch", "chhe chhah che chah chhai chhe", "saat", "aath ath",
        "nau", "das dus", "gyarah gyara gyaarah gyaara", "barah bara baarah baara", "terah tera", "chaudah chaudha choudah chodah",
        "pandrah pandra pundrah pandhra", "solah sola", "satrah satra sattrah", "atharah athara attharah athaara",
        "unnis unees unis", "bees bis", "ikkis ikees ikis", "baais bais baees", "teis teyis teish", "chaubis chobis chaubees",
        "pachchis pachis pacchis pachees", "chhabbis chabbis chhabees chabbees", "sattais sataees sattaees sattais",
        "atthais athais atthaees athaees", "untees untis", "tees tis", "ikattis iktees ikattees ikatis"), 1):
    for _w in _ws.split():
        _HI_NUM.setdefault(_w, _n)
_HI_NUM_RX = re.compile(r"\b(" + "|".join(sorted(_HI_NUM, key=len, reverse=True)) + r")\s+(?=(tarikh|tareekh|tarik|tareek|tarekh|"
                        r"taarikh|tarakh|oct\w*|nov\w*|sep\w*)\b)", re.I)      # not "date": "bata do date wise"
_SPELL = [(re.compile(r"\b(shft|shif|sft|shfit|shitf)\b", re.I), "shift"),
          (re.compile(r"\b(mnth|mth|mnt|monh)\b", re.I), "month"),
          (re.compile(r"\b(prdn|prodn|prdctn|prdction|prodction|prduction)\b", re.I), "production"),
          (re.compile(r"(\w)['’]s\b"), r"\1")]                  # "loop pipe-line 2's" -> "loop pipe-line 2"


def _spell_fold(msg):
    """Voice typing and short forms -> plain words: "baais tarikh" -> "22 tarikh",
    "shft" -> shift, "mnth" -> month, "prdn" -> production."""
    t = _HI_NUM_RX.sub(lambda m: f"{_HI_NUM[m.group(1).lower()]} ", msg)
    for rx, w in _SPELL:
        t = rx.sub(w, t)
    return t


_LINE_NUM = {"ek": "1", "one": "1", "do": "2", "two": "2", "teen": "3", "tin": "3", "three": "3"}
_WAY_NUM = {"six": "6", "chhe": "6", "che": "6", "chhah": "6", "chah": "6", "char": "4", "chaar": "4", "four": "4"}


def _join_spelled(toks):
    """Voice typing spells line names out: "y h b s s" -> yhbss, "l p s 3" -> lps3,
    "2 u a" -> 2ua, "y 17" -> y17.  A run of "a" / "b" alone (the shifts) is kept."""
    out, i = [], 0
    while i < len(toks):
        j = i
        while j < len(toks) and len(toks[j]) == 1 and toks[j].isalnum():
            j += 1
        run = toks[i:j]
        letters = [c for c in run if c.isalpha()]
        if len(run) >= 2 and len(letters) >= 2 and not set(letters) <= {"a", "b"}:
            out.append("".join(run)); i = j; continue
        if toks[i] == "y" and i + 1 < len(toks) and re.fullmatch(r"\d\d", toks[i + 1]):
            out.append("y" + toks[i + 1]); i += 2; continue
        out.append(toks[i]); i += 1
    return out


def _canon_tokens(s):
    """Tokens with the floor's spellings folded: "4 way" -> 4way, "rec" -> recliner,
    "looppipe" -> loop pipe, "l2"/"line2" -> line 2, "lps3" -> lps 3, "yhbss" -> yhb ss."""
    toks, out, i = _join_spelled(_norm_tokens(s)), [], 0
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if t in _WAY_NUM and nxt in ("way", "ways"):              # "six way", "char way"
            out.append(_WAY_NUM[t] + "way"); i += 2; continue
        if t in ("line", "pipe") and nxt in _LINE_NUM:            # "loop pipe line do"
            out += [t, _LINE_NUM[nxt]]; i += 2; continue
        if re.fullmatch(r"y\d\d|y[a-z]{2}", t) and toks[i + 1:i + 3] == ["seat", "slider"]:
            out += [t, "ss"]; i += 3; continue                     # "y17 seat slider" = Y17-SS
        if t in ("4", "6") and nxt in ("way", "ways"):
            out.append(t + "way"); i += 2; continue
        if t in ("4ways", "6ways"):
            out.append(t[:4]); i += 1; continue
        if t == "looppipe":
            out += ["loop", "pipe"]; i += 1; continue
        m = re.fullmatch(r"(?:l|line|ln)(\d)", t)
        if m:
            out += ["line", m.group(1)]; i += 1; continue
        m = re.fullmatch(r"lps(\d)", t)
        if m:
            out += ["lps", m.group(1)]; i += 1; continue
        m = re.fullmatch(r"(y\d\d|y[a-z]{2})(ss|sa)", t)
        if m and t not in ("yesss",):
            out += [m.group(1), m.group(2)]; i += 1; continue
        out.append(_TOK_ALIAS.get(t, t)); i += 1
    return out


def _line_patterns(name):
    """Token sequences that name one line: its own name, the name without "sa"
    ("yhb 4way"), "loop pipe 2", "lps 3" / "location pin", "nut welding", "2ua"."""
    nt = _canon_tokens(name)
    pats = [nt]
    if "sa" in nt:
        pats.append([t for t in nt if t != "sa"])
    if nt[:2] == ["loop", "pipe"] and nt[-1].isdigit():
        pats += [["loop", "pipe", nt[-1]], ["loop", "pipe", "line", nt[-1]]]
    if "lps" in nt:
        pats += [["lps"] + [t for t in nt if t.isdigit()], ["lps"], ["location", "pin"]]
    if name.upper() == "2UA RECLINER":
        pats.append(["2ua"])
    return [p for p in pats if p]


def _find_lines(msg, line_names):
    """Line names the message mentions; tolerant of case, dashes, spaces and the
    floor's short forms.  A name inside a longer matched name is dropped."""
    toks = _canon_tokens(msg)
    raw = set(_norm_tokens(msg))
    n = len(toks)
    found = []
    for name in line_names:
        best = None
        for p in _line_patterns(name):
            k = len(p)
            for i in range(n - k + 1):
                if toks[i:i + k] != p:
                    continue
                if p == ["2ua"] and ((i > 0 and toks[i - 1] == "ywd") or
                                     (i + 1 < n and toks[i + 1] in ("zone", "area", "section", "side")) or
                                     (i > 1 and toks[i - 1] in ("aur", "and", "&") and toks[i - 2] == "ywd") or
                                     (i + 2 < n and toks[i + 1] in ("wale", "wala", "vale", "vala", "wali")
                                      and toks[i + 2] in ("zone", "area", "section", "side"))):
                    continue                    # "ywd 2ua" / "2ua zone" is the zone
                if best is None or k > best[1] - best[0]:
                    best = (i, i + k)
        compact = "".join(_norm_tokens(name))
        if best is None and len(compact) >= 5 and compact in raw:      # "yncss"
            best = (0, 0)
        if best:
            found.append((best[0], best[1], name))
    out = []
    for s, e, nm in sorted(found, key=lambda x: -(x[1] - x[0])):
        if e > s and any(s >= s2 and e <= e2 and (s, e) != (s2, e2) for s2, e2, _ in out):
            continue
        out.append((s, e, nm))
    return [nm for _s, _e, nm in sorted(out)]


_ZONE_PATS = [
    (("ywd", "2ua"), "ywd/2ua"), (("2ua", "zone"), "ywd/2ua"), (("ywd", "zone"), "ywd/2ua"),
    (("ywd", "aur", "2ua"), "ywd/2ua"), (("ywd", "and", "2ua"), "ywd/2ua"), (("2ua", "wale", "zone"), "ywd/2ua"),
    (("2ua", "area"), "ywd/2ua"), (("ywd", "area"), "ywd/2ua"),
    (("seat", "slider"), "seat slider"), (("ss", "zone"), "seat slider"), (("ss", "area"), "seat slider"),
    (("ss", "section"), "seat slider"), (("ss", "side"), "seat slider"), (("slider", "zone"), "seat slider"),
    (("sub", "assembly"), "sub-assembly"), (("subassembly",), "sub-assembly"), (("sub", "assy"), "sub-assembly"),
    (("subassy",), "sub-assembly"), (("sa", "zone"), "sub-assembly"), (("sa", "area"), "sub-assembly"),
    (("sa", "section"), "sub-assembly"),
    (("loop", "pipe"), "loop pipe"),
    (("recliner",), "recliner"),
]


def _find_zones(msg, zone_names):
    toks = _canon_tokens(msg)
    n = len(toks)
    zl = {z.lower(): z for z in zone_names}
    for pat, key in _ZONE_PATS:
        k = len(pat)
        if key in zl and any(tuple(toks[i:i + k]) == pat for i in range(n - k + 1)):
            return [zl[key]]
    joined = " " + " ".join(toks) + " "
    return [z for z in zone_names if " " + " ".join(_canon_tokens(z)) + " " in joined]


_NUM_WORD = {"do": 2, "two": 2, "teen": 3, "tin": 3, "three": 3, "char": 4, "chaar": 4, "four": 4,
             "paanch": 5, "panch": 5, "five": 5}


def _explicit_date(msg, d0):
    """A day written out: 25/10/2026, 25-10-26, 24.10.26, 25/10, 27 oct, oct 27,
    27th October, 26 tarikh, 3 din pehle / two days ago."""
    t = msg.lower()
    for m in re.finditer(r"(?<![\w/.:-])(\d{1,2})([/.-])(\d{1,2})(?:\2(\d{4}|\d{2}))?(?![\w/:-]|\.\d)", t):
        dd, sep, mm, yy = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
        if sep == "." and not yy:
            continue                                   # "92.5" is not a date
        if not (1 <= mm <= 12 and 1 <= dd <= 31):
            continue
        y = (2000 + int(yy) if len(yy) == 2 else int(yy)) if yy else \
            (d0.year if (mm, dd) <= (d0.month, d0.day) else d0.year - 1)
        try:
            return date(y, mm, dd)
        except ValueError:
            continue
    m = re.search(rf"\b(\d{{1,2}})\s*(?:st|nd|rd|th)?[\s/-]*(?:of\s+)?({_MON_RX})[a-z]*\b(?:[\s,/-]+(20\d\d)\b)?", t) \
        or re.search(rf"\b({_MON_RX})[a-z]*\.?[\s/-]+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:[\s,/-]+(20\d\d)\b)?", t)
    if m:
        g = m.groups()
        day, mon = (int(g[0]), _MON[g[1]]) if g[0].isdigit() else (int(g[1]), _MON[g[0]])
        y = int(g[2]) if g[2] else (d0.year if (mon, day) <= (d0.month, d0.day) else d0.year - 1)
        try:
            return date(y, mon, day)
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s*(?:tarikh|tareekh|tarik|tareek|tarekh|taarikh|tarakh|date)\b", t) \
        or re.search(r"\b(?:on|dated)\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b(?!\s*(?:shift|line))", t)
    if m:
        dd = int(m.group(1))
        y, mo = d0.year, d0.month
        if dd > d0.day:
            y, mo = (y, mo - 1) if mo > 1 else (y - 1, 12)
        try:
            return date(y, mo, dd)
        except ValueError:
            return None
    # "3 din pehle" / "two days ago" — not the "1" of "Loop Pipe-Line 1 day before yesterday"
    m = re.search(r"(?<![-\w])(?<!line )(\d{1,2}|do|two|teen|tin|three|char|chaar|four|paanch|panch|five)\s*"
                  r"(din|days?)\s*(pehle|pahle|pehele|ago|back|before)\b(?!\s+(yesterday|yday|yest))", t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _NUM_WORD[m.group(1)]
        return d0 - timedelta(days=n)
    return None


_RX_LASTMONTH = re.compile(r"\b(pichhle|pichle|pichhla|pichla|last|prev|previous)\s+(mahine|mahina|month)\b")
_RX_MONTH = re.compile(r"\b(is|iss|this|current|chalu)\s+(mahine|mahina|month)\b|\bmtd\b|\bmonth[\s-]*(to|till)[\s-]*date\b|\bmonthly\b|\bthis\s+month\s+so\s+far\b")
_RX_LASTWEEK = re.compile(r"\b(pichhle|pichle|pichhla|pichla|picchle|last|prev|previous)\s+"
                          r"(pure\s+|poore\s+|pura\s+|poora\s+)?(hafte|hafta|week|wk)\b")
_RX_WEEK = re.compile(r"\b(is|iss|this|current|chalu)\s+(pure\s+|poore\s+|pura\s+|poora\s+)?(hafte|hafta|week|wk)\b"
                      r"|\bwtd\b|\bweek[\s-]*(to|till)[\s-]*date\b|\bweekly\b")
_RX_DBY = re.compile(r"\b(parso|parson|parsoo|parsu|parsho)\b|\bday\s+before\s+(yesterday|yday|yest|yesterdy)\b"
                     r"|\bd\s*-\s*2\b")
_RX_YDAY = re.compile(r"\b(kal|kl|kall|yesterday|yesterdy|yesterda|yday|yest|ystrdy|yesteday|yestarday|yesterdays)\b"
                      r"|\bd\s*-\s*1\b|\b(prev|previous)\s+day\b")
_RX_TODAY = re.compile(r"\b(aaj|aj|aajj|today|todays|tday|tdy)\b|\b(abhi|ab)\s+tak\b|\btill\s+now\b|\bso\s+far\b"
                       r"|\bas\s+of\s+now\b")


def _month_by_name(t, d0):
    """"october" / "oct mtd" / "in october" with no day number -> that month."""
    m = re.search(r"\b(" + "|".join(n for n in _MON_FULL if n != "may") + r")\b", t) or \
        re.search(r"\b(jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b(?=\s*(mtd|month|mahine|me|mein|so\s+far|till|tak|ka|ki|ke|\?|$))", t) or \
        re.search(r"\bin\s+(may)\b", t)
    if not m:
        return None
    mon = _MON[m.group(1)[:3]]
    y = d0.year if mon <= d0.month else d0.year - 1
    start = date(y, mon, 1)
    if (y, mon) == (d0.year, d0.month):
        return f"record_date >= '{start}'"
    end = (date(y + (mon == 12), mon % 12 + 1, 1) - timedelta(days=1))
    return f"record_date >= '{start}' AND record_date <= '{end}'"


def _date_cond(msg, d0):
    t = " " + re.sub(r"\s+", " ", msg.lower()) + " "
    wk = d0 - timedelta(days=d0.weekday())
    mo = d0.replace(day=1)
    ex = _explicit_date(msg, d0)
    if ex:
        return f"record_date = '{ex}'"
    if _RX_LASTMONTH.search(t):
        pm_end = mo - timedelta(days=1)
        return f"record_date >= '{pm_end.replace(day=1)}' AND record_date <= '{pm_end}'"
    if _RX_MONTH.search(t):
        return f"record_date >= '{mo}'"
    if _RX_LASTWEEK.search(t):
        return f"record_date >= '{wk - timedelta(days=7)}' AND record_date < '{wk}'"
    if _RX_WEEK.search(t):
        return f"record_date >= '{wk}'"
    mn = _month_by_name(t, d0)
    if mn:
        return mn
    if _RX_DBY.search(t):
        return f"record_date = '{d0 - timedelta(days=2)}'"
    if _RX_YDAY.search(t):
        return f"record_date = '{d0 - timedelta(days=1)}'"
    if _RX_TODAY.search(t):
        return f"record_date = '{d0}'"
    return None


_RX_SHIFT_A = re.compile(r"\bshift\s*-?\s*a\b|\ba\s*-?\s*shift\b|\b(pehli|pehla|pahli|pahla|first|1st|morning|subah|din|day|general)"
                         r"\s*(ki|wali|vali|ka|wala)?\s*shift\b")
_RX_SHIFT_B = re.compile(r"\bshift\s*-?\s*b\b|\bb\s*-?\s*shift\b|\b(dusri|doosri|dusra|doosra|second|2nd|raat|night|evening|shaam)"
                         r"\s*(ki|wali|vali|ka|wala)?\s*shift\b")
_RX_SHIFT_BOTH = re.compile(r"\ba\s*(?:-|/|&|vs\.?|v/s|aur|and|ya|or)\s*b\b|\b(which|kis|kaunsi|konsi|kounsi|kaun\s*si|kaun\s*sa)\s+shift\b"
                            r"|\bdono\s+shift|\bboth\s+(the\s+)?shifts?\b|\btwo\s+shifts\b|\bshift\s*-?\s*wise\b"
                            r"|\ba\s+b\s+shift|\bshift\s+a\s+b\b|\bcompare\s+a\s+(?:and\s+|aur\s+|with\s+)?b\b"
                            r"|\bshifts?\s+compar\w*|\bcompar\w*\s+(?:of\s+|the\s+)?(?:both\s+|two\s+)?shifts?\b")


def _shift_cond(t, shift_now):
    has_a, has_b = _RX_SHIFT_A.search(t), _RX_SHIFT_B.search(t)
    if _RX_SHIFT_BOTH.search(t) or (has_a and has_b):
        return "both"
    if has_a:
        return "A"
    if has_b:
        return "B"
    if re.search(r"\b(is|iss|this|abhi\s+ki|current|chalu)\s+shift\b", t) and shift_now in ("A", "B"):
        return shift_now
    return None


# ── what the question asks for ───────────────────────────────────────
_RX_FAULT = re.compile(r"\b(fault\w*|fualt\w*|falut\w*|faut\w*|alarm\w*|alram\w*|alaram\w*)\b")
_RX_BYPASS = re.compile(r"bypass|by\s*pass|\bpy\b|poka[\s-]*yoke|pokayoke")
_RX_COMMENT = re.compile(r"\b(comment\w*|cmnt\w*|remark\w*)\b")
_RX_CAMERA = re.compile(r"\b(camera\w*|cam|cams|cctv)\b")
_RX_OPEN_BP = re.compile(r"\b(khul\w*|open|pending|waiting|await\w*|active|unresolved|unapproved|baaki|baki|outstanding|chalu|chaalu|unclosed|un-closed|"
                         r"lagi\s+hu\w*|lage\s+hu\w*|running|ruke|ruka|ruki|atke|atka|atki|"
                         r"approval\s+(ke\s+liye|pending|baaki|baki|me|mein|par|pe)|for\s+approval|not\s+(yet\s+)?approved)\b"
                         r"|\b(clear|close|closed|band|resolve)\w*\s+(nahi|nhi|na)\b|\bnot\s+(closed|cleared|resolved)\b")
_RX_STOP = re.compile(r"\b(ruk\w*|band|bandh|stop\w*|idle|off|down|khadi|khada)\b|\bchal\s*(nahi|nhi)\b|\bnot\s+running\b")
_RX_RUN = re.compile(r"\b(chal\s*rah\w*|chal\s*ri\w*|chal\s*rh\w*|running|chalu|live\s*status|status)\b|\bon\s+hai\b"
                     r"|\b(currently|right\s+now|abhi)\s+(in\s+production|producing|working)\b|\bproduction\s+(happening|going\s+on)\b|\blive\s+(right\s+now|now|abhi|currently)\b|\b(is|are)\s+\S+(\s+\S+){0,3}\s+in\s+production\s*\??\s*$")
_RX_MODEL = re.compile(r"\bmodel\w*\b")
_RX_COUNTW = re.compile(r"\b(kitna|kitne|kitni|kitney|kitnaa|ketna|how\s+many|how\s+much|total|count|qty|quantity|kul)\b")
_RX_LONG = re.compile(r"\b(der|time|minutes?|mins?|ghante|hours?|duration|how\s+long)\b")
_RX_LISTW = re.compile(r"\b(kaunsi|konsi|kounsi|kaun\s*si|which|list|naam|names?)\b")


def _measures(t):
    """line_shift measures the question names, in the order asked."""
    found = []
    def add(m, pat):
        mm = re.search(pat, t)
        if mm:
            found.append((mm.start(), m))
    add("breakdown", r"\b(bd|breakdown\w*|break\s+down|brkdown)\b")
    add("setup", r"\bsetup\b")
    add("speed", r"\bspeed\s*loss\b")
    add("loss", r"\b(dt|downtime|down\s*time|loss|losses|lost|lose|stoppage)\b|kitni\s+der|kitna\s+(time|der)\s+(band|ruk)"
                r"|\b(band|ruki|ruka|stop)\s+(rahi|raha|rhi|rha)\b|\bhow\s+long\b.*\b(stop|down|band)")
    add("ng", r"\b(ng|nok|reject\w*|rej|rejction|rejecton|rejection\w*|defect\w*|scrap)\b")
    add("ok", r"\bok\s*(count|qty|quantity|parts?|pcs|pieces?|cnt|production|maal|nos|output)\b|\bgood\s*(parts?|count|qty|pcs|pieces?|quantity|output|nos|maal)\b"
              r"|\bkitne\s+ok\b|\bok\s+(kitne|kitna)\b")
    add("oee", r"\b(oee|efficiency|effcy)\b")
    if not any(m in ("ok",) for _p, m in found):
        add("produced", r"\b(output|production|producton|prodution|prod|o/p|bane|bana|banaye|banaya|"
                        r"produce\w*|maal|made|make)\b|\bo\s*/\s*p\b")
        if not found:      # "parts / pcs / qty" alone mean production only when nothing else is named
            add("produced", r"\b(parts|qty|quantity|pcs|pieces|count)\b")
    if len({m for _p, m in found}) > 1 and "produced" in [m for _p, m in found] and not re.search(
            r"\b(output|production|producton|prodution|prod|o/p|aur|and|or|plus|tatha|sath|saath)\b|\bo\s*/\s*p\b|[,&+]", t):
        found = [(p, m) for p, m in found if m != "produced"]       # "produced the most NG", "kitne NG bane"
    if "loss" in [m for _p, m in found] and "breakdown" in [m for _p, m in found]:
        #  "lost most time to breakdown", "loss due to breakdown" = the breakdown time;
        #  both only when asked as two things ("loss aur breakdown", "total loss")
        if not re.search(r"\b(total|overall|sab|saare|all)\s+(time\s+)?loss|\b(loss|losses|downtime)\s+(and|aur|or|&|,|\+)\s+(bd|breakdown)"
                         r"|\b(bd|breakdown)\s+(and|aur|or|&|,|\+)\s+(total\s+)?(loss|losses|downtime)", t):
            found = [(p, m) for p, m in found if m != "loss"]
    out, seen = [], set()
    for _p, m in sorted(found):
        if m not in seen:
            seen.add(m); out.append(m)
    return out


_GOOD = {"produced", "ok", "oee"}


def _parse(msg, d0, shift_now, line_names=(), zone_names=()):
    """Everything code can read from the question, for the hint and for
    building the SELECT without the model."""
    msg = _spell_fold(msg)
    t = " " + re.sub(r"\s+", " ", msg.lower()) + " "
    tr = re.sub(r"\b(last|pichhle|pichle|prev|previous|this|is|iss|current)\s+(pure\s+|poore\s+)?(week|wk|hafte|hafta|month|mahine|shift)\b", " ", t)
    p = {"date": _date_cond(msg, d0), "shift": _shift_cond(t, shift_now), "lines": _find_lines(msg, line_names),
         "zone": None, "measures": _measures(t), "subject": None, "rank": None, "group": None,
         "open": False}
    if not p["lines"] and zone_names:
        zs = _find_zones(msg, zone_names)
        p["zone"] = zs[0] if zs else None
    if _RX_CAMERA.search(t):
        p["subject"] = "cameras"
    elif _RX_BYPASS.search(t):
        p["subject"] = "bypass"
        p["open"] = bool(_RX_OPEN_BP.search(t))
    elif _RX_COMMENT.search(t):
        p["subject"] = "comments"
    elif _RX_FAULT.search(t) and not re.search(r"\b(bd|breakdown)\b", t):
        p["subject"] = "faults"
    elif _RX_MODEL.search(t) and not p["measures"]:
        p["subject"] = "model"
    elif (_RX_STOP.search(t) or _RX_RUN.search(t)) and not p["date"] and not (
            p["measures"] and (_RX_COUNTW.search(t) or _RX_LONG.search(t))):
        p["subject"] = "status"
    elif p["measures"]:
        p["subject"] = "line_shift"
    elif p["shift"] == "both" and (p["lines"] or p["zone"]):
        p["subject"] = "line_shift"      # "kis shift ka performance accha tha"
    # ranking
    bad = bool(p["measures"]) and p["measures"][0] in ("ng", "loss", "breakdown", "setup", "speed")
    desc = re.search(r"\b(number\s*(1|one|ek)|no\.?\s*1|top|sabse\s*(zyada|jyada|jada|zada|jyda|zyda|adhik|upar|uppar|aage|aagey|lamba|lambi|lambe)|zyada|jyada|zada|jyda|most|highest|max\w*|best|"
                     r"peak|maximum|lead\w*|topp\w*|longest|biggest|largest|heaviest)\b", tr) or (re.search(r"\b(worst|kharab|bekar)\b", tr) and bad)
    asc = re.search(r"\b(sabse\s*kam|lowest|least|minimum|kam\s*se\s*kam|bottom\w*|neeche|ghatiya|poor|poorest|poorly|weakest|low|sabse\s+(peeche|piche|pichhe|peechhe|neeche)|min\s+(oee|output|production|prod)|last(?!\s+(week|wk|month|mahine|mahina|hafte|hafta|shift|year|saal|day|din|\d)))\b", tr) \
        or (re.search(r"\b(worst|kharab|bekar)\b", tr) and not bad)
    line_q = re.search(r"\b(kis|kaunsi|konsi|kounsi|kaun\s*si|which|what)\s+line|\bline\s+(kaun|kaunsi|konsi)\b|\bline\s+(kaunsi|konsi|kounsi|kaun\s*si|ne)\b"
                       r"|\b(best|top|worst|bottom\w*|lowest|least|highest|min|max|number\s*1|no\.?\s*1)\s+(\w+\s+){0,2}lines?\b"
                       r"|\blines?\s+(with|having)\s+(the\s+)?(max\w*|highest|most|min\w*|lowest|least)\b"
                       r"|\btop\s+(producing|prod)\b|\bline\s+(ka|ki|ke)\b.*\bsabse\b|\bsabse\b.*\bline\b", t)
    if p["subject"] == "line_shift" and not p["lines"] and (line_q or (desc or asc) and not _RX_COUNTW.search(t)):
        if asc and not (desc and not asc):
            p["rank"] = "asc" if not (desc and desc.start() < asc.start()) else "desc"
        elif desc:
            p["rank"] = "desc"
    if p["subject"] == "faults":
        mach_q = re.search(r"\b(kis|kaunsi|konsi|kounsi|kaun\s*si|which|what)\s+(single\s+|one\s+|ek\s+)?(machine|m/c|mc)\b|\bmachine\s+(with|having)\b"
                           r"|\b(top|max\w*|most|worst|highest|sabse\s+(zyada|jyada|jada|zada|jyda|adhik))\s+(fault\w*\s+|alarm\w*\s+)?(wali\s+|wala\s+|vali\s+|vala\s+)?(machine|machin|m/c|mc)\b"
                           r"|\b(machine|m/c|mc)\s+(pe|par|me|mein)\s+sabse\b"
                           r"|\b(problem|dikkat|kharab|issue|trouble)\w*\s+(wali\s+|wala\s+|vali\s+)?(machine|m/c|mc)\b"
                           r"|\b(number\s*(1|one|ek)|no\.?\s*1)\s+(machine|m/c|mc)\b|\bfaults?\s+(by|per)\s+(machine|m/c|mc)\b"
                           r"|\bfault[\s-]*prone\s+(machine|m/c|mc)\b|\b(machine|m/c|mc)[\s-]*wise\b"
                           r"|\b(max\w*|most|top|highest)\s+(fault\w*|alarm\w*)\s+(cnt\s+|count\s+|wali\s+|wala\s+)?(machine|m/c|mc)\b|\b(machine|machin|m/c|mc)s?\s+(by|with\s+(the\s+)?most)\s+(fault|alarm)", t)
        name_q = re.search(r"\b(kaunsa|konsa|kounsa|kaun\s*sa|which|what|kis)\s+(fault|alarm)\b|\bfault\s+(ka\s+)?(naam|name)\b"
                           r"|\b(fault|alarm)\s+(kaunsa|konsa|kounsa)\b", t)
        if mach_q and not name_q:
            p["group"] = "machine_name"
        elif desc or name_q or re.search(r"\b(common|frequent|baar\s*baar|repeat\w*|number\s*one)\b", t):
            p["group"] = "fault_name"
    return p


def _hints(msg, d0, shift_now, line_names=(), zone_names=()):
    """The parse as a hint after the question, for the model."""
    p = _parse(msg, d0, shift_now, line_names, zone_names)
    out = []
    if len(p["lines"]) == 1:
        out.append(f"line: line_name = '{p['lines'][0]}'")
    elif p["lines"]:
        out.append("lines: line_name IN (" + ", ".join(f"'{n}'" for n in p["lines"][:6]) + ")")
    elif p["zone"]:
        out.append(f"zone: zone_name = '{p['zone']}'")
    date_c = p["date"]
    if p["shift"] and not date_c:
        date_c = f"record_date = '{d0}'"
    if not date_c and p["subject"] in ("line_shift", "faults", "comments") or \
            (not date_c and p["subject"] == "bypass" and not p["open"]):
        date_c = f"record_date = '{d0}'"
    if date_c:
        out.append(f"date filter: {date_c}")
    if p["subject"] == "status":
        out.append("use view line_now" + (": status <> 'RUNNING'" if _RX_STOP.search(msg.lower()) and not p["lines"] else ""))
    if p["subject"] == "model":
        out.append("use view line_now")
    if p["group"]:
        out.append(f"group by {p['group']}")
    if p["rank"]:
        out.append(f"rank lines: GROUP BY line_name ORDER BY the value {p['rank'].upper()}")
    if p["measures"] and p["subject"] == "line_shift":
        m = p["measures"][0]
        col = {"produced": "produced", "ok": "ok", "ng": "ng", "loss": "total_loss_s", "breakdown": "breakdown_s",
               "setup": "setup_s", "speed": "speed_loss_s"}.get(m)
        out.append("measure: ROUND(AVG(oee),1)" if m == "oee" else f"measure: SUM({col})")
    if p["subject"] == "bypass" and p["open"]:
        out.append("open bypass: status = 'WAITING'")
    if p["subject"] == "comments":
        out.append("answer with COUNT(*) AS comments")
    if p["shift"] == "both":
        out.append("shift filter: both shifts — GROUP BY shift_name")
    elif p["shift"]:
        out.append(f"shift filter: shift_name = '{p['shift']}'")
    if not p["lines"] and not p["zone"] and not p["rank"] and not p["group"] and p["subject"] in ("line_shift", "faults", "bypass", "comments"):
        out.append("one total for all of them: no GROUP BY")
    return ("  [" + "; ".join(out) + "]") if out else ""


# ── the SELECT built in code when the question is plainly understood ───
_AGG = {"produced": "SUM(produced) AS produced", "ok": "SUM(ok) AS ok", "ng": "SUM(ng) AS ng",
        "oee": "ROUND(AVG(oee),1) AS oee", "loss": "SUM(total_loss_s) AS total_loss_s",
        "breakdown": "SUM(breakdown_s) AS breakdown_s", "setup": "SUM(setup_s) AS setup_s",
        "speed": "SUM(speed_loss_s) AS speed_loss_s"}


def _plan_sql(msg, p, d0):
    """One SELECT for a question whose subject, scope, date and shift were all read
    in code, or None (then the model writes it).  The model got these wrong
    often enough on its own: plan_till_now for "production", DESC for "sabse kam",
    status = 'WAITING' on a plain bypass count, cameras for "alarm"."""
    if len(p["lines"]) > 1:
        return None
    lit = lambda s: "'" + str(s).replace("'", "''") + "'"
    scope = []
    if p["lines"]:
        scope.append(f"line_name = {lit(p['lines'][0])}")
    elif p["zone"]:
        scope.append(f"zone_name = {lit(p['zone'])}")
    date_c = p["date"] or f"record_date = '{d0}'"
    subj = p["subject"]
    def where(conds):
        conds = [c for c in conds if c]
        return (" WHERE " + " AND ".join(conds)) if conds else ""
    if subj == "cameras":
        if p["zone"]:
            return None
        return (f"SELECT COALESCE(SUM(hung),0) AS hung, COALESCE(SUM(cameras),0) AS cameras FROM cameras"
                + where(scope))
    if subj == "bypass":
        if p["zone"] or p["rank"]:
            return None
        if p["open"]:
            return "SELECT COUNT(*) AS bypasses FROM py_bypass" + where(scope + ["status = 'WAITING'"])
        if re.search(r"\b(kis|kaunsi|konsi|which)\s+line\b|\bline\s*wise\b|\bper\s+line\b", msg.lower()):
            return None
        return "SELECT COUNT(*) AS bypasses FROM py_bypass" + where(scope + [date_c])
    if subj == "comments":
        if p["zone"] or re.search(r"\b(dikhao|show|list|kya\s+likha|padh\w*|latest|recent|read)\b", msg.lower()):
            return None
        return "SELECT COUNT(*) AS comments FROM comments" + where(scope + [date_c])
    if subj == "faults":
        if p["zone"]:
            return None
        sh = [f"shift_name = '{p['shift']}'"] if p["shift"] in ("A", "B") else []
        if p["shift"] == "both":
            return None
        if p["group"]:
            g = p["group"]
            return (f"SELECT {g}, COUNT(*) AS faults FROM faults" + where(scope + [date_c] + sh)
                    + f" GROUP BY {g} ORDER BY faults DESC LIMIT 10")
        if re.search(r"\b(kis|kaunsi|konsi|which)\s+line\b|\bline\s*wise\b", msg.lower()) and not p["lines"]:
            return None
        return "SELECT COUNT(*) AS faults FROM faults" + where(scope + [date_c] + sh)
    if subj in ("status", "model"):
        if p["lines"]:
            return f"SELECT status, model, updated_at FROM line_now WHERE line_name = {lit(p['lines'][0])}"
        if subj == "status" and _RX_STOP.search(msg.lower()):
            return "SELECT line_name, status, updated_at FROM line_now" + where(
                ([f"zone_name = {lit(p['zone'])}"] if p["zone"] else []) + ["status <> 'RUNNING'"]) + " ORDER BY line_name"
        return None
    if subj != "line_shift" or _RX_MODEL.search(msg.lower()):
        return None
    ms = [m for m in p["measures"] if m in _AGG]
    if not ms and p["shift"] == "both" and (p["lines"] or p["zone"]) and not p["rank"]:
        ms = ["produced"]          # "kis shift ka performance accha tha": produced, NG and OEE per shift
    if not ms:
        return None
    sh = [f"shift_name = '{p['shift']}'"] if p["shift"] in ("A", "B") else []
    if p["shift"] == "both":
        if p["rank"] or not (p["lines"] or p["zone"]):
            return None
        cols = ", ".join(dict.fromkeys([_AGG["produced"], _AGG["ng"], _AGG["oee"]] + [_AGG[m] for m in ms]))
        return (f"SELECT shift_name, {cols} FROM line_shift" + where(scope + [date_c])
                + " GROUP BY shift_name ORDER BY shift_name")
    if p["rank"]:
        if p["lines"]:
            return None
        m = ms[0]
        alias = _AGG[m].split(" AS ")[1]
        expr = _AGG[m].split(" AS ")[0]
        return (f"SELECT line_name, {_AGG[m]} FROM line_shift" + where(scope + [date_c] + sh)
                + f" GROUP BY line_name HAVING {expr} IS NOT NULL ORDER BY {alias} {p['rank'].upper()} LIMIT 10")
    if len(ms) > 3:
        return None
    return f"SELECT {', '.join(_AGG[m] for m in ms)} FROM line_shift" + where(scope + [date_c] + sh)


def _violations(sql, hint, line_names):
    """What the model's SELECT got wrong against the hints we already know
    are right: the date, the line, a single shift, and any line name that is
    not one of the plant's lines (the model once filtered on "PARSO")."""
    q = re.sub(r"\s+", " ", (sql or "")).lower()
    out = []
    m = re.search(r"date filter: ([^;\]]+)", hint)
    if m:
        cond = re.sub(r"\s+", " ", m.group(1).strip()).lower()
        if cond not in q:
            out.append(f"Use exactly this date condition: {m.group(1).strip()}")
    m = re.search(r"\bline: line_name = '([^']+)'", hint)
    if m:
        name = m.group(1).lower()
        if f"line_name = '{name}'" not in q and f"line_name ilike '%{name}%'" not in q:
            out.append(f"Filter the line with: line_name = '{m.group(1)}'")
    m = re.search(r"shift filter: shift_name = '([AB])'", hint)
    if m and f"shift_name = '{m.group(1).lower()}'" not in q:
        out.append(f"Add: shift_name = '{m.group(1)}'")
    norm = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
    known = [norm(n) for n in line_names]
    for v in re.findall(r"line_name\s*(?:=|ilike)\s*'%?([^'%]+)%?'", q):
        nv = norm(v)
        if nv and not any(nv in k or k in nv for k in known):
            out.append(f"'{v}' is not a line name — use only names from LINE NAMES, or no line filter")
    return out


# ── entry point ────────────────────────────────────────────────────────
_PAGE_SUBJECT = {
    "fault-history": "faults view", "py-bypass": "py_bypass view",
    "video-coverage": "cameras view", "comments-history": "comments view",
}
_SUBJECT_WORDS = re.compile(r"\b(prod\w*|output|parts|ng|reject\w*|oee|efficien\w*|loss|dt|downtime|bd|breakdown|"
                            r"fault\w*|bypass|py|camera\w*|cam|comment\w*|remark\w*|model|status|band|chal\w*|running)\b", re.I)


_PRIVATE = re.compile(r"\b(password\w*|passwd|pwd|otp|phone\s*(no|number|nmbr|num)|(ka|ki|ke|uska|unka|his|her|their)\s+phone|"
                      r"phone\s+(do|de|dena|batao|bhejo|share|send|chahiye)|mobile\s*(no|number)|contact\s*(no|number|details?)|"
                      r"e-?mail|whatsapp|salary|aadhaa?r|pan\s*card|ghar\s+ka\s+pata|home\s+address)\b", re.I)
_PERSON_NO = re.compile(r"\b(ka|ki|ke|kaa|uska|unka|his|her)\s+(number|no\.?|nmbr|num)\b|"
                        r"\bnumber\s+(do|de|dena|batao|bhejo|chahiye|send|share|give)\b|\bcontact\b", re.I)
_THING_NO = re.compile(r"\b(line|machine|part|fault|model|bin|py|cycle|serial|station|camera|cam|shift|slip|lot|batch)\b", re.I)


_NAV_VERB = re.compile(r"\b(kholo|khol|kholna|khol\s*do|le\s*chalo|le\s*chal|chalo|jao|go\s*to|navigate|take\s+me|page|screen|tab|module)\b", re.I)
_NAV_FILLER = {"page", "pls", "plz", "please", "open", "kholo", "khol", "the", "screen", "dikhao", "show", "do", "karo", "zara"}


_FOLLOW = re.compile(r"^\s*(aur|and|or|what\s+about|how\s+about|uska|iska|uske|iske|unka|inka|same|wahi|vahi|also|phir|fir|ab|toh?)\b", re.I)
_PLANT_WORDS = re.compile(r"\b(plant|all\s+lines|sab\s+lines|saari\s+lines|poore\s+plant|whole\s+plant|overall)\b", re.I)


def _follow_up(p, msg, pp):
    """ "aur NG?", "aur kal?", "and YCA-SS?" after a question: what this one leaves
    out (line/zone, date, shift, what is asked) comes from the earlier question."""
    has_scope = bool(p["lines"] or p["zone"] or _PLANT_WORDS.search(msg))
    if not (_FOLLOW.search(msg) or (len(_norm_tokens(msg)) <= 3 and (not has_scope or not p["subject"]))):
        return p
    p = dict(p)
    if p["subject"] is None and pp["subject"]:
        for k in ("subject", "measures", "rank", "group", "open"):
            p[k] = pp[k]
    if not has_scope and not p["rank"]:
        p["lines"], p["zone"] = pp["lines"], pp["zone"]
    if not p["date"] and pp["date"] and p["subject"] in ("line_shift", "faults", "comments") \
            or (not p["date"] and pp["date"] and p["subject"] == "bypass" and not p["open"]):
        p["date"] = pp["date"]
    if not p["shift"] and pp["shift"] and p["subject"] in ("line_shift", "faults"):
        p["shift"] = pp["shift"]
    return p


def _since(cond, d0):
    """The earliest day a date condition reaches (None = no lower bound)."""
    ds = re.findall(r"record_date\s*(?:=|>=|>)\s*'(\d{4}-\d{2}-\d{2})'", cond or "")
    if ds:
        return min(date.fromisoformat(x) for x in ds)
    return None if cond else d0 - timedelta(days=7)


def answer(message, user, history=None, context=None):
    """Return {"reply", "provider", optional "action", optional "sql"}."""
    msg = (message or "").strip()
    if _PRIVATE.search(msg) or (_PERSON_NO.search(msg) and not _SUBJECT_WORDS.search(msg)
                                and not _THING_NO.search(msg)):
        return {"reply": "I can't share passwords or anyone's personal details (phone number, "
                         "email, address). I can answer questions about production, OEE, NG, "
                         "losses, faults, PY bypass, cameras and comments.", "provider": "local"}
    if _OPEN_WORDS.search(msg) and not _DATA_WORDS.search(msg) and not (
            _RX_BYPASS.search(msg.lower()) and re.search(r"\bopen\b", msg, re.I) and not _NAV_VERB.search(msg)):
        key = _page_from_words(msg)
        if key:
            return _nav_reply(key)
    bare = [w for w in _norm_tokens(msg) if w not in _NAV_FILLER]
    if 1 <= len(bare) <= 3 and not (_RX_BYPASS.search(msg.lower()) and re.search(r"\bopen\b", msg, re.I)
                                    and not _NAV_VERB.search(msg)):          # just a page name: "video coverage", "shift compile pls"
        key = _page_from_words(" ".join(bare))
        if key and " ".join(bare) in set(PAGES[key][2]) | {PAGES[key][1].lower(), key.replace("-", " ")}:
            return _nav_reply(key)

    prod_day, shift = _prod_day_and_shift()
    with get_conn() as conn:
        cur = dict_cursor(conn)
        lines = _scoped_lines(cur, user)
        conn.rollback()
        if not lines:
            return {"reply": "No lines are assigned to you, so there is no data I can show.",
                    "provider": "local"}
        live_f = _LIVE_POOL.submit(_live_rows, lines)      # runs while the model thinks
        system = _SYSTEM.format(
            schema=_schema_text(lines, prod_day, shift),
            d0=prod_day, d1=prod_day - timedelta(days=1),
            wk=prod_day - timedelta(days=prod_day.weekday()),
            mo=prod_day.replace(day=1))
        msgs = [{"role": "system", "content": system}]
        prev = [h for h in (history or []) if h.get("role") == "user"][-1:]
        for h in prev:
            if h.get("content") and h["content"].strip() != msg:
                msgs.append({"role": "user", "content": "(earlier question, for context) " + h["content"][:300]})
                msgs.append({"role": "assistant", "content": "OK"})
        line_names = [l["line_name"] for l in lines]
        if re.search(r"\bline\s*(?:no\.?|number|num|#)?\s*-?\s*\d{1,2}\b", msg, re.I) \
                and not _find_lines(msg, line_names) \
                and not _find_zones(msg, sorted({l["zone_name"] for l in lines if l["zone_name"]})):
            return {"reply": "There is no line with that number — lines go by name. Which one did you mean? "
                             + ", ".join(line_names), "provider": "local"}
        hint = _hints(msg, prod_day, shift, line_names,
                      sorted({l["zone_name"] for l in lines if l["zone_name"]}))
        subj = _PAGE_SUBJECT.get(((context or {}).get("page") or "").strip())
        if subj and not _SUBJECT_WORDS.search(msg):
            hint = (hint[:-1] + f"; on the {subj[:-5].replace('_', ' ')} page: use the {subj}]") if hint \
                else f"  [on the {subj[:-5].replace('_', ' ')} page: use the {subj}]"
        msgs.append({"role": "user", "content": msg + hint})

        t0 = time.time()
        parse = _parse(msg, prod_day, shift, line_names, sorted({l["zone_name"] for l in lines if l["zone_name"]}))
        pm = (prev[0].get("content") or "").strip() if prev else ""
        if pm and pm != msg:
            parse = _follow_up(parse, msg, _parse(pm, prod_day, shift, line_names,
                                                  sorted({l["zone_name"] for l in lines if l["zone_name"]})))
        planned = None if (subj and not _SUBJECT_WORDS.search(msg)) else _plan_sql(msg, parse, prod_day)
        if planned:
            try:
                live = live_f.result(timeout=10)
            except Exception:
                live = {}
            prefix, allowed = _views_sql(conn, lines, live, _since(parse["date"] or f"record_date = '{prod_day}'", prod_day))
            rows, err = _check_and_run(conn, prefix, allowed, planned)
            if not err:
                print(f"[AI-LOCAL] {time.time() - t0:.1f}s user={user.get('username')} q={msg[:60]!r} "
                      f"{len(rows)} rows (built in code)", flush=True)
                reply = _header(planned) + "\n" + _format_rows(rows)
                extra = _by_shift(conn, prefix, allowed, planned, rows)
                if extra:
                    reply += "\n" + extra
                return {"reply": reply, "provider": "local", "sql": planned, "route": "code"}
            print(f"[AI-LOCAL] built query failed, asking the model: {err}", flush=True)
        try:
            out = _ask_llm(msgs)
        except Exception as e:
            return {"reply": "The local assistant is not running right now. "
                             "Try a production question, e.g. \"YSD-SS OEE today\".",
                    "provider": "local", "error": str(e)[:120], "llm_down": True}
        text = _clean_sql(out)
        try:
            live = live_f.result(timeout=10)
        except Exception:
            live = {}
        hd = re.search(r"date filter: ([^;\]]+)", hint)
        prefix, allowed = _views_sql(conn, lines, live, _since(hd.group(1) if hd else None, prod_day))

        if text.upper().startswith("NAVIGATE"):
            key = _page_from_words(msg)
            if key:
                return _nav_reply(key)
            key = text.split(":", 1)[-1].strip().strip(".").lower()
            if key in PAGES:
                return _nav_reply(key)
            return {"reply": "I could not tell which page to open.", "provider": "local"}
        if text.upper().startswith("UNKNOWN") or not text:
            return {"reply": "I can't answer that from the plant data I can read. I can do "
                             "production, NG, OEE, losses, faults, PY bypass, cameras and "
                             "comments, per line or for the plant.", "provider": "local"}

        if re.search(r"group\s+by\s+shift_name", text, re.I):
            text = re.sub(r"\s+LIMIT\s+1\s*$", "", text, flags=re.I)
        text = re.sub(r"((?:SUM|AVG|MAX|MIN)\(\s*\w+_s\s*\))\s*/\s*(?:60|3600)(?:\.0+)?", r"\1", text, flags=re.I)
        text = re.sub(r"\bAS\s+(\w+?)_(?:minutes|minute|mins|min|hours|hour|hrs)\b", r"AS \1_s", text, flags=re.I)
        bad = _violations(text, hint, line_names)
        if bad:
            msgs += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "Fix this query: " + " ".join(bad) +
                                                 "\nReply with the corrected single SELECT only."}]
            try:
                fixed = _clean_sql(_ask_llm(msgs))
                if fixed.upper().startswith(("SELECT", "WITH")):
                    text = fixed
            except Exception:
                pass
        rows, err = _check_and_run(conn, prefix, allowed, text)
        if err and "not allowed" not in err and "may not use" not in err:
            # one repair round: show the model its own error
            msgs += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"That query failed: {err}\n"
                                                 "Reply with a corrected single SELECT only."}]
            try:
                text = _clean_sql(_ask_llm(msgs))
                rows, err = _check_and_run(conn, prefix, allowed, text)
            except Exception as e:
                err = str(e)[:120]
        took = time.time() - t0
        print(f"[AI-LOCAL] {took:.1f}s user={user.get('username')} q={msg[:60]!r} "
              f"{'ERR ' + err if err else str(len(rows)) + ' rows'}", flush=True)
        if err:
            return {"reply": "I couldn't get that from the data. Try naming the line and "
                             "the day, e.g. \"YNC-SS production today\".",
                    "provider": "local", "sql": text, "error": err}
        reply = _header(text) + "\n" + _format_rows(rows)
        extra = _by_shift(conn, prefix, allowed, text, rows)
        if extra:
            reply += "\n" + extra
        return {"reply": reply, "provider": "local", "sql": text}


# Manual test (admin scope):  python ai_local.py "YNC-SS ka aaj ka production"
if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "aaj kitne fault aaye"
    r = answer(q, {"id": 1, "username": "admin", "role": "admin"})
    print("SQL:", r.get("sql"))
    print(r["reply"])
