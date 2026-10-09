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
    "my-escalations":    ("/my-escalations",     "Inbox",                ["inbox", "alerts", "notification"]),
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
                         r"dikhao\s+page|page\s+dikhao|wala\s+page|page\s+pe|page\s+par|jana\s+hai|take\s+me|le\s+jao)\b", re.I)

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


def _views_sql(conn, lines, live=None):
    """WITH prefix defining the curated views over this user's lines.
    The running shift's row takes the live values (live = _live_rows())."""
    ids = [l["id"] for l in lines]
    parts = []
    live = live or {}

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    for l in lines:
        r = live.get(l["id"])
        if r:
            cond = (f"(record_date = {psql.Literal(r['record_date']).as_string(conn)} "
                    f"AND shift_name = {psql.Literal(r['shift_name']).as_string(conn)})")
            def ov(col, key):
                v = r.get(key)
                lit = psql.Literal(v if isinstance(v, str) else num(v)).as_string(conn)
                lit = lit.replace("{", "{{").replace("}", "}}")     # the text goes through psql.SQL().format()
                return f"CASE WHEN {cond} THEN {lit} ELSE {col} END"
        else:
            def ov(col, key):
                return col
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
            "FROM {tbl} WHERE shift_name IN ('A','B')"
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
_MON = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _norm_tokens(s):
    return [t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t]


def _find_lines(msg, line_names):
    """Line names the message mentions, longest first; tolerant of case,
    dashes and spaces ("ync ss", "YNC-SS", "yncss", "loop pipe line 2")."""
    toks = _norm_tokens(msg)
    joined = " " + " ".join(toks) + " "
    hits = []
    for name in sorted(line_names, key=lambda n: -len(n)):
        nt = _norm_tokens(name)
        if not nt:
            continue
        if " " + " ".join(nt) + " " in joined:
            hits.append(name)
            continue
        compact = "".join(nt)
        if len(compact) >= 5 and compact in toks:     # "yncss" typed as one word
            hits.append(name)
    out = []
    for h in hits:          # drop "ysd" inside an already-found "ysd recliner"
        if not any(h != o and " ".join(_norm_tokens(h)) in " ".join(_norm_tokens(o)) for o in out):
            out.append(h)
    return out


def _find_zones(msg, zone_names):
    toks = " " + " ".join(_norm_tokens(msg)) + " "
    return [z for z in zone_names if " " + " ".join(_norm_tokens(z)) + " " in toks]


def _explicit_date(msg, d0):
    t = msg.lower()
    m = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d\d)\b", t)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", t) \
        or re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})\b", t)
    if m:
        g = m.groups()
        day, mon = (int(g[0]), _MON[g[1]]) if g[0].isdigit() else (int(g[1]), _MON[g[0]])
        y = d0.year if (mon, day) <= (d0.month, d0.day) else d0.year - 1
        try:
            return date(y, mon, day)
        except ValueError:
            return None
    # "3 din pehle" / "2 days ago" — not the "1" of "Loop Pipe-Line 1 day before yesterday"
    m = re.search(r"(?<![-\w])(?<!line )(\d{1,2})\s*(din|days?)\s*(pehle|pahle|ago|before)\b(?!\s+yesterday)", t)
    if m:
        return d0 - timedelta(days=int(m.group(1)))
    return None


# ── date / shift words -> an exact filter for the model ──────────────────
# A 3B model kept falling back to "today" for "is mahine" and dropped "kal"
# on some views.  The words are read here, in code, and handed to the model as
# the exact condition to use, so it never has to work the date out itself.
def _hints(msg, d0, shift_now, line_names=(), zone_names=()):
    t = " " + msg.lower() + " "
    d1 = d0 - timedelta(days=1)
    wk = d0 - timedelta(days=d0.weekday())
    mo = d0.replace(day=1)
    date_cond = None
    if re.search(r"\b(is|iss|this)\s+(mahine|month)\b|\bmonthly\b", t):
        date_cond = f"record_date >= '{mo}'"
    elif re.search(r"\b(pichhle|pichle|last)\s+(mahine|month)\b", t):
        pm_end = mo - timedelta(days=1)
        date_cond = f"record_date >= '{pm_end.replace(day=1)}' AND record_date <= '{pm_end}'"
    elif re.search(r"\b(is|iss|this)\s+(hafte|hafta|week)\b|\bweekly\b", t):
        date_cond = f"record_date >= '{wk}'"
    elif re.search(r"\b(pichhle|pichle|last)\s+(hafte|hafta|week)\b", t):
        date_cond = f"record_date >= '{wk - timedelta(days=7)}' AND record_date < '{wk}'"
    elif re.search(r"\b(parso|day before yesterday)\b", t):
        date_cond = f"record_date = '{d0 - timedelta(days=2)}'"
    elif re.search(r"\b(kal|yesterday)\b", t):
        date_cond = f"record_date = '{d1}'"
    elif re.search(r"\b(aaj|today|todays|today's)\b", t):
        date_cond = f"record_date = '{d0}'"
    ex = _explicit_date(msg, d0)
    if ex:
        date_cond = f"record_date = '{ex}'"
    shift_cond = None
    has_a = re.search(r"\bshift\s*a\b|\ba[\s-]*shift\b|\bday\s*shift\b|\bdin\s*(ki|wali)?\s*shift\b|\bmorning\s*shift\b", t)
    has_b = re.search(r"\bshift\s*b\b|\bb[\s-]*shift\b|\bnight\s*shift\b|\braat\s*(ki|wali)?\s*shift\b", t)
    if re.search(r"\ba\s*(?:-|/|&|vs\.?|v/s|aur|and)\s*b\b|\b(which|kis|kaunsi|konsi)\s+shift\b|\bdono\s+shift|\bboth\s+shift"
                 r"|\ba\s+b\s+shift|\bshift\s+a\s+b\b|\bcompare\s+a\s+(?:and\s+|aur\s+)?b\b", t):
        has_a = has_b = True
    if has_a and has_b:
        shift_cond = "both shifts — GROUP BY shift_name"
    elif has_a:
        shift_cond = "shift_name = 'A'"
    elif has_b:
        shift_cond = "shift_name = 'B'"
    elif re.search(r"\b(is|iss|this|abhi\s+ki|current)\s+shift\b", t) and shift_now in ("A", "B"):
        shift_cond = f"shift_name = '{shift_now}'"
    if shift_cond and not date_cond:
        date_cond = f"record_date = '{d0}'"      # "is shift" = the shift of TODAY's production date
    out = []
    found = _find_lines(msg, line_names)
    if len(found) == 1:
        out.append(f"line: line_name = '{found[0]}'")
    elif found:
        out.append("lines: line_name IN (" + ", ".join(f"'{n}'" for n in found[:6]) + ")")
    elif zone_names:
        zs = _find_zones(msg, zone_names)
        if zs:
            out.append(f"zone: zone_name = '{zs[0]}'")
    if date_cond:
        out.append(f"date filter: {date_cond}")
    # what KIND of answer — the phrasings the 3B model misread most in the eval
    stop_w = re.search(r"\b(ruk\w*|band|stop\w*|idle|bandh|chal\s*nahi|not\s*running|off)\b", t)
    run_w = re.search(r"\b(chal\s*rah\w*|running|chalu|on\s*hai|live\s*status|status)\b", t)
    if (stop_w or run_w) and not date_cond and not re.search(r"\b(kitna|kitne|kitni|how\s+much|how\s+many|total|how\s+long)\b", t):
        out.append("use view line_now" + (": status <> 'RUNNING'" if stop_w and not found else ""))
    # rank words are read with the date phrases taken out ("last week" is a date, not "last place")
    tr = re.sub(r"\b(last|pichhle|pichle|this|is|iss)\s+(week|hafte|hafta|month|mahine|shift)\b", " ", t)
    rank_w = re.search(r"\b(most|sabse|top|max\w*|zyada|jyada|highest|common|frequent|baar\s*baar|number\s*1)\b", tr)
    if re.search(r"fault", t):
        # a machine word NOT used as an adjective of "fault": "most common machine
        # fault" is about fault types; "max fault wali machine", "top fault m/c",
        # "what machine had the most" are about machines
        mach = re.search(r"\b(machine|machines|m/c|mc)\b(?!\s*faults?\b)", t)
        if mach and (rank_w or re.search(r"\b(kis|kaunsi|konsi|kaun\s*si|which|what)\s+(machine|m/c|mc)\b", t)):
            out.append("group by machine_name")
        elif rank_w:
            out.append("group by fault_name")
    bad_metric = re.search(r"\b(ng|reject\w*|defect\w*|loss|breakdown|bd|dt|downtime|fault\w*|stop\w*)\b", t)
    asc_w = re.search(r"\b(sabse\s*kam|lowest|least|minimum|min|kam\s*se\s*kam|last|aakhri|akhri|bottom|neeche)\b", tr) or \
        (re.search(r"\b(worst|kharab)\b", t) and not bad_metric)
    desc_w = re.search(r"\b(number\s*1|no\.?\s*1|top|sabse\s*zyada|sabse\s*jyada|most|highest|max\w*|best)\b", tr) or \
        (re.search(r"\b(worst|kharab)\b", t) and bad_metric)
    line_q = re.search(r"\b(kis|kaunsi|konsi|kaun\s*si|which)\s+line|line\s+(kaunsi|konsi|kaun\s*si)|line\s+ne\b", t) \
        or (re.search(r"\b(best|top|worst|number\s*1|no\.?\s*1)\s+\w*\s*line\b", t) and not found)
    if line_q and desc_w and not found:
        out.append("rank lines: GROUP BY line_name ORDER BY the value DESC")
    elif line_q and asc_w and not found:
        out.append("rank lines: GROUP BY line_name ORDER BY the value ASC")
    # which number shop-floor shorthand means
    measure = None
    if re.search(r"\b(bd|breakdown\w*)\b", t): measure = "breakdown_s"
    elif re.search(r"\bsetup\b", t): measure = "setup_s"
    elif re.search(r"\bspeed\b", t): measure = "speed_loss_s"
    elif re.search(r"\b(dt|downtime|down\s*time|loss|stoppage)\b|how\s+long.*stop|kitni\s+der|kitna\s+(time|der)\s+(band|ruk)", t):
        measure = "total_loss_s"
    if not measure:
        if re.search(r"\b(ng|reject\w*|defect\w*|rejection)\b", t): measure = "ng"
        elif re.search(r"\bok\s*(count|qty|quantity|parts?|cnt)\b|\bgood\s*(parts?|count|qty)\b", t): measure = "ok"
        elif re.search(r"\b(oee|efficiency)\b", t): measure = "AVG:oee"
        elif re.search(r"\b(output|production|producton|prod|parts|qty|quantity|bane|banaye|banaya|produce\w*)\b", t):
            measure = "produced"
    if measure:
        out.append(f"measure: ROUND(AVG({measure[4:]}),1)" if measure.startswith("AVG:") else f"measure: SUM({measure})")
        if not date_cond and not re.search(r"\b(ever|all\s*time|since|tak\s+ka\s+total|lifetime|history)\b", t):
            out.append(f"date filter: record_date = '{d0}'")
    per_line = re.search(r"\b(har\s+line|line\s*wise|per\s+line|each\s+line|every\s+line|kis\s+line|which\s+line|"
                         r"sabse|top|most|highest|lowest|best|worst|last|rank\w*)\b", tr)
    total_w = re.search(r"\b(kitne|kitna|kitni|total|kul|overall|mila\s*ke|milake|combined|together|sum|how\s+many|how\s+much)\b", t)
    if total_w and not per_line and not found:
        out.append("one total for all of them: no GROUP BY")
    if re.search(r"bypass|\bpy\b", t) and re.search(
            r"\b(khul\w*|open|pending|waiting|active|unresolved|baaki|clear\s+nahi|band\s+nahi)\b", t):
        out.append("open bypass: status = 'WAITING'")
    if re.search(r"\b(comment\w*|cmnt\w*|remark\w*)\b", t) and not re.search(
            r"\b(dikhao|show|list|kya\s+likha|padh\w*|latest|last|recent)\b", t):
        out.append("answer with COUNT(*) AS comments")
    if shift_cond:
        out.append(f"shift filter: {shift_cond}")
    return ("  [" + "; ".join(out) + "]") if out else ""


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


_PRIVATE = re.compile(r"\b(password\w*|passwd|pwd|otp|phone|mobile\s*(no|number)|contact\s*(no|number|details?)|"
                      r"e-?mail|whatsapp|salary|aadhaa?r|pan\s*card|ghar\s+ka\s+pata|home\s+address)\b", re.I)
_PERSON_NO = re.compile(r"\b(ka|ki|ke|kaa|uska|unka|his|her)\s+(number|no\.?|nmbr|num)\b|"
                        r"\bnumber\s+(do|de|dena|batao|bhejo|chahiye|send|share|give)\b|\bcontact\b", re.I)
_THING_NO = re.compile(r"\b(line|machine|part|fault|model|bin|py|cycle|serial|station|camera|cam|shift|slip|lot|batch)\b", re.I)


def answer(message, user, history=None, context=None):
    """Return {"reply", "provider", optional "action", optional "sql"}."""
    msg = (message or "").strip()
    if _PRIVATE.search(msg) or (_PERSON_NO.search(msg) and not _SUBJECT_WORDS.search(msg)
                                and not _THING_NO.search(msg)):
        return {"reply": "I can't share passwords or anyone's personal details (phone number, "
                         "email, address). I can answer questions about production, OEE, NG, "
                         "losses, faults, PY bypass, cameras and comments.", "provider": "local"}
    if _OPEN_WORDS.search(msg) and not _DATA_WORDS.search(msg):
        key = _page_from_words(msg)
        if key:
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
        prefix, allowed = _views_sql(conn, lines, live)

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
