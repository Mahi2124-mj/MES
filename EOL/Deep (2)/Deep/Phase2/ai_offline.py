# ───────────────────────────────────────────────────────────────────────
# ai_offline.py   —   Offline production-data chatbot (no external LLM)
# ───────────────────────────────────────────────────────────────────────
# 2026-09-14 — The floating chatbot depended on the paid Anthropic API, which
# ran out of credits. This module answers the common production questions
# entirely from the DB with a rule-based intent parser + hand-written SQL — so
# the chatbot works offline, instantly, at zero cost. It understands English +
# Hinglish for: a line's production / OK / NG / OEE / availability / performance
# / quality / plan / status / model / loss breakdown, plus plant-wide asks
# (today's summary, highest/lowest line, total loss, compare shifts A vs B) and
# poka-yoke alerts.
#
# Correctness rules baked in (see the AI-chat schema notes):
#  - Each line has its OWN table (mes_lines.db_table_name); only tables that
#    actually exist are used.
#  - PRODUCED qty = GREATEST(ok_count, shift_plan_completed) — register lines
#    (Loop Pipe) keep it in ok_count, the rest in shift_plan_completed.
#  - Rejects = ng_count.  GAP_* shift rows are excluded (shift_name IN 'A','B').
# ───────────────────────────────────────────────────────────────────────
import re
import time
from datetime import datetime, timedelta

from database import get_conn, dict_cursor

_CACHE = {"lines": None, "ts": 0.0}

# Message words that never help identify a line — dropped before line matching.
_STOP = {
    "shift", "line", "lines", "the", "ka", "ki", "ke", "ko", "me", "mein", "of",
    "and", "for", "on", "in", "today", "todays", "yesterday", "aaj", "kal", "abhi",
    "a", "b", "oee", "ng", "ok", "production", "produced", "produce", "output",
    "loss", "losses", "plan", "target", "status", "model", "kitna", "kitni",
    "hua", "tha", "batao", "dikha", "show", "give", "what", "was", "is", "how",
    "much", "many", "efficiency", "avg", "average", "total", "count", "parts",
    "part", "reject", "rejection", "quality", "availability", "performance",
    "current", "running", "vs", "compare", "summary", "report", "all",
}


def _lines():
    """Existing per-line dashboard tables: [{id, line_name, line_code, db_table_name}]."""
    if _CACHE["lines"] is not None and (time.time() - _CACHE["ts"]) < 300:
        return _CACHE["lines"]
    out = []
    try:
        with get_conn() as conn:
            cur = dict_cursor(conn)
            cur.execute("""SELECT id, line_name, line_code, db_table_name
                             FROM mes_lines
                            WHERE COALESCE(db_table_name,'') <> ''
                            ORDER BY line_name""")
            rows = cur.fetchall()
            for l in rows:
                cur.execute("SELECT to_regclass(%s) AS r", (l["db_table_name"],))
                if cur.fetchone()["r"]:
                    out.append(l)
            conn.commit()
    except Exception:
        pass
    _CACHE["lines"] = out
    _CACHE["ts"] = time.time()
    return out


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _match_lines(text, lines):
    """Rank lines by how many of their name tokens appear in the message; a code
    match (e.g. 'LP-02') is a strong bonus. Returns every line tied at the top
    score (so ambiguous asks like 'ysd' return all YSD lines to clarify)."""
    words = set(w for w in re.split(r"[^a-z0-9]+", text.lower()) if w and w not in _STOP)
    tnorm = _norm(text)
    scored = []
    for l in lines:
        toks = set(t for t in re.split(r"[^a-z0-9]+", (l["line_name"] or "").lower()) if t)
        code = _norm(l.get("line_code"))
        score = len(toks & words)
        if code and len(code) >= 3 and code in tnorm:
            score += 5
        if score > 0:
            scored.append((score, l))
    if not scored:
        return []
    top = max(s for s, _ in scored)
    return [l for s, l in scored if s == top]


def _parse_date(text, today):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            return d, str(d)
        except Exception:
            pass
    t = text.lower()
    if "yesterday" in t or re.search(r"\bkal\b", t):
        d = today - timedelta(days=1)
        return d, f"yesterday ({d})"
    return today, f"today ({today})"


def _parse_shift(text):
    t = text.lower()
    if re.search(r"\bshift\s*a\b", t) or re.search(r"\ba[\s-]*shift\b", t) or "day shift" in t or "morning" in t:
        return "A"
    if re.search(r"\bshift\s*b\b", t) or re.search(r"\bb[\s-]*shift\b", t) or "night shift" in t or "night" in t:
        return "B"
    return None


def _pct(v):
    try:
        return f"{float(v):.1f}%"
    except Exception:
        return "-"


def _hms(sec):
    try:
        sec = int(float(sec))
    except Exception:
        return "0:00:00"
    if sec < 0:
        sec = 0
    return f"{sec // 3600}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


# NOTE: total_loss column is a HH:MM:SS *string*; loss_*_seconds are integers.
# We compute total loss from the numeric seconds so arithmetic/formatting works.
_ROW_COLS = """shift_name, shift_plan,
    GREATEST(COALESCE(ok_count,0), COALESCE(shift_plan_completed,0)) AS produced,
    COALESCE(ng_count,0) AS ng, overall_oee, availability, performance, quality_oee,
    operating_status, current_model_name,
    COALESCE(loss_breakdown_seconds,0)  AS l_breakdown,
    COALESCE(loss_quality_seconds,0)    AS l_quality,
    COALESCE(loss_setup_seconds,0)      AS l_setup,
    COALESCE(loss_material_seconds,0)   AS l_material,
    COALESCE(loss_others_seconds,0)     AS l_others,
    COALESCE(loss_speed_seconds,0)      AS l_speed,
    COALESCE(loss_change_over_seconds,0) AS l_changeover,
    (COALESCE(loss_breakdown_seconds,0)+COALESCE(loss_quality_seconds,0)
     +COALESCE(loss_setup_seconds,0)+COALESCE(loss_material_seconds,0)
     +COALESCE(loss_others_seconds,0)+COALESCE(loss_speed_seconds,0)
     +COALESCE(loss_change_over_seconds,0)) AS total_loss"""


def _fetch_row(cur, table, date, shift):
    """Latest real shift row for a line on a date (specific shift if given)."""
    if shift:
        cur.execute(f"SELECT {_ROW_COLS} FROM {table} "
                    f"WHERE record_date=%s AND shift_name=%s "
                    f"ORDER BY timestamp DESC LIMIT 1", (date, shift))
    else:
        cur.execute(f"SELECT {_ROW_COLS} FROM {table} "
                    f"WHERE record_date=%s AND shift_name IN ('A','B') "
                    f"ORDER BY timestamp DESC LIMIT 1", (date,))
    return cur.fetchone()


def _metric_wanted(text):
    t = text.lower()
    if re.search(r"\bng\b|reject|kharab|defect", t):        return "ng"
    if re.search(r"\bok\b|good count", t):                  return "ok"
    if "oee" in t or "efficien" in t:                       return "oee"
    if "availab" in t:                                      return "availability"
    if "perform" in t:                                      return "performance"
    if "quality" in t:                                      return "quality"
    if "plan" in t or "target" in t:                        return "plan"
    if "loss" in t or "downtime" in t or "breakdown" in t:  return "loss"
    if "model" in t:                                        return "model"
    if "status" in t or "running" in t:                     return "status"
    if re.search(r"production|produce|output|kitna|kitni|banaya|bana|pieces|qty|units", t):
        return "production"
    return None


def _line_report(r, line_name, when):
    """Full one-line report for a single line/shift row."""
    if not r:
        return f"{line_name}: no data for {when}."
    return (
        f"{line_name} · {when} · Shift {r['shift_name']}\n"
        f"Produced: {r['produced']}   NG: {r['ng']}   Plan: {r['shift_plan']}\n"
        f"OEE: {_pct(r['overall_oee'])}  (Avail {_pct(r['availability'])} · "
        f"Perf {_pct(r['performance'])} · Qual {_pct(r['quality_oee'])})\n"
        f"Status: {r['operating_status'] or '-'}   Model: {r['current_model_name'] or '-'}\n"
        f"Total loss: {_hms(r['total_loss'])}"
    )


def _loss_report(r, line_name, when):
    if not r:
        return f"{line_name}: no data for {when}."
    parts = [
        ("Breakdown", r["l_breakdown"]), ("Quality", r["l_quality"]),
        ("Setup", r["l_setup"]), ("Material", r["l_material"]),
        ("Change Over", r["l_changeover"]), ("Speed", r["l_speed"]),
        ("Others", r["l_others"]),
    ]
    parts = [(n, s) for n, s in parts if s and float(s) > 0]
    parts.sort(key=lambda x: -float(x[1]))
    head = f"{line_name} · {when} · Shift {r['shift_name']} — Total loss {_hms(r['total_loss'])}"
    if not parts:
        return head + "\nNo loss recorded."
    return head + "\n" + "\n".join(f"  {n}: {_hms(s)}" for n, s in parts)


def _single_metric(r, metric, line_name, when):
    if not r:
        return f"{line_name}: no data for {when}."
    tag = f"{line_name} · {when} · Shift {r['shift_name']} — "
    if metric == "ng":           return tag + f"NG: {r['ng']}"
    if metric == "ok":           return tag + f"Produced (OK): {r['produced']}"
    if metric == "production":    return tag + f"Produced: {r['produced']}  (NG {r['ng']}, Plan {r['shift_plan']})"
    if metric == "oee":          return tag + f"OEE: {_pct(r['overall_oee'])}"
    if metric == "availability": return tag + f"Availability: {_pct(r['availability'])}"
    if metric == "performance":  return tag + f"Performance: {_pct(r['performance'])}"
    if metric == "quality":      return tag + f"Quality: {_pct(r['quality_oee'])}"
    if metric == "plan":         return tag + f"Plan: {r['shift_plan']}  (produced {r['produced']})"
    if metric == "status":       return tag + f"Status: {r['operating_status'] or '-'}"
    if metric == "model":        return tag + f"Model: {r['current_model_name'] or '-'}"
    return _line_report(r, line_name, when)


def _all_lines_rows(cur, lines, date, shift):
    rows = []
    for l in lines:
        try:
            r = _fetch_row(cur, l["db_table_name"], date, shift)
            if r:
                rows.append((l["line_name"], r))
        except Exception:
            continue
    return rows


def answer(message, context=None, history=None):
    """Return a plain-text reply for the chatbot, sourced entirely from the DB."""
    msg = (message or "").strip()
    if not msg:
        return "Ask me about a line's production, OEE, NG, losses or plan — e.g. \"YSD-SS OEE today\"."
    t = msg.lower()
    today = datetime.now().date()
    date, when = _parse_date(msg, today)
    shift = _parse_shift(msg)
    lines = _lines()
    if not lines:
        return "No production lines are available right now."

    try:
        with get_conn() as conn:
            conn.autocommit = True
            cur = dict_cursor(conn)

            # ── Poka-yoke alerts ────────────────────────────────────────
            if "poka" in t or ("alert" in t and "loss" not in t):
                cur.execute("""SELECT l.line_name,
                                      COUNT(*) AS n,
                                      COUNT(*) FILTER (WHERE NOT COALESCE(e.acknowledged,false)) AS open
                                 FROM mes_poka_yoke_events e
                                 JOIN mes_lines l ON l.id = e.line_id
                                WHERE e.detected_at::date = %s
                                GROUP BY l.line_name
                                ORDER BY n DESC""", (date,))
                pk = cur.fetchall()
                if not pk:
                    return f"No poka-yoke alerts on {when}."
                out = [f"Poka-yoke alerts · {when}:"]
                total = 0
                for p in pk:
                    total += p["n"]
                    out.append(f"  {p['line_name']}: {p['n']} ({p['open']} open)")
                out.append(f"Total: {total}")
                return "\n".join(out)

            # ── Ranking: lowest / highest OEE or production ─────────────
            rank_lo = bool(re.search(r"lowest|worst|kam|minimum|\bmin\b|least|slow", t))
            rank_hi = bool(re.search(r"highest|best|top|zyada|maximum|\bmax\b|most", t))
            if rank_lo or rank_hi:
                by_prod = bool(re.search(r"production|produce|output|kitna|units|qty", t))
                rows = _all_lines_rows(cur, lines, date, shift)
                if not rows:
                    return f"No data for {when}."
                keyf = (lambda nr: float(nr[1]["produced"])) if by_prod \
                    else (lambda nr: float(nr[1]["overall_oee"] or 0))
                rows.sort(key=keyf, reverse=rank_hi)
                label = "production" if by_prod else "OEE"
                head = ("Highest" if rank_hi else "Lowest") + f" {label} · {when}:"
                out = [head]
                for nm, r in rows[:5]:
                    val = r["produced"] if by_prod else _pct(r["overall_oee"])
                    out.append(f"  {nm}: {val}  (Shift {r['shift_name']})")
                return "\n".join(out)

            # ── Loss / downtime / reason across the plant (no line named) ─
            if re.search(r"loss|downtime|down\s*time|breakdown|reason|kyu|kyun|why", t) \
               and not _match_lines(msg, lines):
                rows = _all_lines_rows(cur, lines, date, shift)
                tot = sum(float(r["total_loss"]) for _, r in rows)
                rows.sort(key=lambda nr: -float(nr[1]["total_loss"]))
                out = [f"Total loss · {when}: {_hms(tot)}", "Top lines:"]
                for nm, r in rows[:5]:
                    if float(r["total_loss"]) <= 0:
                        continue
                    out.append(f"  {nm}: {_hms(r['total_loss'])}")
                return "\n".join(out)

            # ── Which line(s) does the message name? ───────────────────
            matched = _match_lines(msg, lines)

            # ── Compare shifts A vs B ──────────────────────────────────
            if re.search(r"compare|versus|dono shift|both shift", t) or re.search(r"\ba\s*vs\s*b\b", t):
                if len(matched) == 1:
                    l = matched[0]
                    ra = _fetch_row(cur, l["db_table_name"], date, "A")
                    rb = _fetch_row(cur, l["db_table_name"], date, "B")
                    out = [f"{l['line_name']} · {when} — Shift A vs B:"]
                    for nm, r in (("A", ra), ("B", rb)):
                        out.append(f"  Shift {nm}: produced {r['produced']}, NG {r['ng']}, "
                                   f"OEE {_pct(r['overall_oee'])}" if r else f"  Shift {nm}: no data")
                    return "\n".join(out)
                if len(matched) == 0:
                    out = [f"Plant · {when} — Shift A vs B:"]
                    for sh in ("A", "B"):
                        rows = _all_lines_rows(cur, lines, date, sh)
                        prod = sum(int(r["produced"]) for _, r in rows)
                        ng = sum(int(r["ng"]) for _, r in rows)
                        oees = [float(r["overall_oee"] or 0) for _, r in rows if r["overall_oee"] is not None]
                        avg = sum(oees) / len(oees) if oees else 0
                        out.append(f"  Shift {sh}: produced {prod}, NG {ng}, avg OEE {avg:.1f}% "
                                   f"({len(rows)} lines)")
                    return "\n".join(out)

            metric = _metric_wanted(msg)

            # ── Single named line ──────────────────────────────────────
            if len(matched) == 1:
                l = matched[0]
                r = _fetch_row(cur, l["db_table_name"], date, shift)
                if metric == "loss":
                    return _loss_report(r, l["line_name"], when)
                if metric:
                    return _single_metric(r, metric, l["line_name"], when)
                return _line_report(r, l["line_name"], when)

            # ── Ambiguous: several lines matched → ask to pick ─────────
            if len(matched) > 1:
                names = ", ".join(l["line_name"] for l in matched[:8])
                return f"Which line? Matches: {names}."

            # ── No line named + NG asked → NG per line ─────────────────
            if metric == "ng":
                rows = [(nm, r) for nm, r in _all_lines_rows(cur, lines, date, shift)
                        if int(r["ng"]) > 0]
                rows.sort(key=lambda nr: -int(nr[1]["ng"]))
                if not rows:
                    return f"No NG recorded on {when}."
                tot = sum(int(r["ng"]) for _, r in rows)
                out = [f"NG by line · {when} (total {tot}):"]
                for nm, r in rows[:8]:
                    out.append(f"  {nm}: {r['ng']}  (Shift {r['shift_name']})")
                return "\n".join(out)

            # ── General status / summary (only for real summary asks) ──
            if re.search(r"summary|overview|report|all\s*line|plant|overall|status|"
                         r"efficien|\boee\b|production|produce|output|today|kaisa|kaise|"
                         r"haal|scene|chal\s*rah|running|kya\s*ho", t):
                rows = _all_lines_rows(cur, lines, date, shift)
                if not rows:
                    return f"No data for {when}."
                tot_prod = sum(int(r["produced"]) for _, r in rows)
                tot_ng = sum(int(r["ng"]) for _, r in rows)
                oees = [float(r["overall_oee"] or 0) for _, r in rows if r["overall_oee"] is not None]
                avg_oee = sum(oees) / len(oees) if oees else 0
                rows.sort(key=lambda nr: -float(nr[1]["produced"]))
                out = [f"Plant summary · {when}",
                       f"Lines: {len(rows)}   Produced: {tot_prod}   NG: {tot_ng}   "
                       f"Avg OEE: {avg_oee:.1f}%", "Top by production:"]
                for nm, r in rows[:6]:
                    out.append(f"  {nm}: {r['produced']}  (OEE {_pct(r['overall_oee'])}, Shift {r['shift_name']})")
                return "\n".join(out)

            # ── Not understood → guide the user (NOT a canned summary) ──
            return _help(lines)
    except Exception as e:
        return f"Could not read the data ({str(e)[:120]}). Try: \"YSD-SS OEE today\"."


def _help(lines):
    ex = (lines[0]["line_name"] if lines else "YSD-SS")
    return (
        "I didn't catch that. I can answer, for example:\n"
        f"  • {ex} production today\n"
        f"  • {ex} OEE / NG / loss today (or yesterday, shift A/B)\n"
        "  • Today's OEE summary\n"
        "  • Highest production today · Lowest efficiency line\n"
        "  • Total loss today · Compare shifts A vs B\n"
        "  • Poka yoke alerts\n"
        "Tip: name the line clearly (e.g. \"YSD-SS\"), and add a date/shift."
    )


# Manual test:  python ai_offline.py "YSD-SS production today"
if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "today's oee summary"
    print(f"Q: {q}\n{'-'*50}")
    print(answer(q))
