#!/usr/bin/env python
"""
plc_diag.py — live PLC D-register diagnostic for poka-yoke training.

For the currently running model on the line, this script shows for each
configured poka-yoke:
  •  Register (D401, D407, …)
  •  Expected output (from Admin Panel → Poka Yoke → Config)
  •  Actual PLC value (live)
  •  Mismatch status (OK / FAULT)

Plus it dumps the raw value of every D-register in the scan range so you
can see what values are coming in even for registers not yet configured.

Coding reference (spec):
    1-register PY   0 = PASS     1 = OFF     2 = ON
    2-register PY   0 = PASS     1 = OFF,OFF 2 = OFF,ON 3 = ON,OFF 4 = ON,ON

Usage
    python plc_diag.py                               # default range, auto-detect model
    python plc_diag.py --start 401 --end 425
    python plc_diag.py --line-id 2                   # force a line id
    python plc_diag.py --model 9                     # force a model bit
    python plc_diag.py --once
    python plc_diag.py --interval 1.0
    python plc_diag.py --no-color
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

try:
    import pymcprotocol
except ImportError:
    print("pymcprotocol not installed. Run: pip install pymcprotocol", file=sys.stderr)
    sys.exit(1)

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("psycopg2 not installed. Run: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)


# ── Defaults ────────────────────────────────────────────────────────────────
# IP / port are LOOKED UP from mes_lines for the chosen --line-id; only a
# last-resort fallback is hardcoded for offline diagnostics.
import os as _os
DEFAULT_IP       = _os.getenv("DIAG_PLC_IP", "192.168.10.150")
DEFAULT_PORT     = int(_os.getenv("DIAG_PLC_PORT", "5002") or 5002)
DEFAULT_START    = 401
DEFAULT_END      = 425
DEFAULT_INTERVAL = 2.0
DEFAULT_LINE_ID  = int(_os.getenv("DIAG_LINE_ID", "0") or 0)   # 0 = no default

# Reuse the canonical DB config from database.py so creds live in one place.
try:
    from database import DB_CONFIG as _BASE_DB
    DB = dict(_BASE_DB)
except Exception:
    DB = dict(
        host=_os.getenv("DB_HOST", "192.168.10.210"),
        port=int(_os.getenv("DB_PORT", "5432") or 5432),
        database=_os.getenv("DB_NAME", "energydb"),
        user=_os.getenv("DB_USER", "postgres"),
        password=_os.getenv("DB_PASS", "tbdi@123"),
    )


def fetch_line_plc(conn, line_id: int):
    """Return (ip, port) configured for `line_id` in mes_lines.  Falls
    back to (DEFAULT_IP, DEFAULT_PORT) if the line row has no PLC info."""
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT plc_ip, plc_port
            FROM mes_lines
            WHERE id = %s
        """, (line_id,))
        r = cur.fetchone() or {}
        ip   = r.get("plc_ip") or DEFAULT_IP
        port = int(r.get("plc_port") or DEFAULT_PORT)
        return ip, port
    except Exception:
        return DEFAULT_IP, DEFAULT_PORT

CODE_MAP_1REG = {0: "PASS", 1: "OFF",     2: "ON"}
CODE_MAP_2REG = {0: "PASS", 1: "OFF,OFF", 2: "OFF,ON", 3: "ON,OFF", 4: "ON,ON"}


# ── Colour helpers ──────────────────────────────────────────────────────────
def _c(code: str, on: bool) -> str:
    return code if on else ""

def paint(text: str, fg: str, use_color: bool, bold: bool = False) -> str:
    if not use_color: return text
    codes = {"g": "32", "y": "33", "r": "31", "m": "35", "c": "36", "b": "34", "w": "37", "k": "90"}
    return f"\033[{'1;' if bold else ''}{codes.get(fg,'0')}m{text}\033[0m"

def clear(use_color: bool) -> None:
    if use_color: print("\033[2J\033[H", end="")
    else: print("\n" + "─" * 100)


# ── Decode helpers ──────────────────────────────────────────────────────────
def decode_dual(v: int) -> str:
    a = CODE_MAP_1REG.get(v, "—")
    b = CODE_MAP_2REG.get(v, "—")
    return f"1-reg:{a:<5}  2-reg:{b}"

def expected_codes_1reg(dv: Any):
    if dv is None or dv == 0: return None
    return {int(dv)}

def expected_codes_2reg(dv1: Any, dv2: Any):
    if (dv1 is None or dv1 == 0) and (dv2 is None or dv2 == 0): return None
    def opts(v):
        if v is None or v == 0: return {1, 2}
        return {int(v)}
    out = set()
    for o1 in opts(dv1):
        for o2 in opts(dv2):
            out.add({(1,1):1,(1,2):2,(2,1):3,(2,2):4}[(o1,o2)])
    return out or None


# ── DB helpers ──────────────────────────────────────────────────────────────
def db_connect():
    try:
        return psycopg2.connect(**DB)
    except Exception as exc:
        print(f"[DB ERR] {exc}"); return None

def fetch_current_model(conn, line_id: int) -> tuple[int | None, str | None]:
    """Return (bit_number, model_name) for the currently-running model."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT db_table_name, current_shift_row_id FROM mes_lines WHERE id=%s", (line_id,))
    row = cur.fetchone()
    if not row or not row.get("db_table_name"):
        return None, None
    tbl = row["db_table_name"]
    try:
        if row.get("current_shift_row_id"):
            cur.execute(f"SELECT current_model_number, current_model_name FROM {tbl} WHERE id=%s",
                        (row["current_shift_row_id"],))
        else:
            cur.execute(f"SELECT current_model_number, current_model_name FROM {tbl} "
                        f"WHERE is_shift_completed=false ORDER BY timestamp DESC NULLS LAST LIMIT 1")
        r = cur.fetchone() or {}
        mnum = r.get("current_model_number")
        mname = r.get("current_model_name")
        if mname:
            import re
            mname = re.sub(r"^TYPE-SERIES:\s*", "", mname, flags=re.IGNORECASE)
        return mnum, mname
    except Exception as exc:
        print(f"[DB ERR] fetch model: {exc}"); return None, None

def fetch_py_configs_for_model(conn, model_bit: int) -> list[dict]:
    """Return list of dicts: {py_no, py_name, register_addr, register_count,
       desired_value, desired_value_2, desired_bit} for the active model."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT p.py_no, p.description AS py_name,
               p.bit AS register_addr,
               COALESCE(p.register_count,1) AS register_count,
               a.desired_value, a.desired_value_2, a.desired_bit
        FROM mes_py_master p
        JOIN mes_py_assignments a ON a.py_id = p.id
        JOIN mes_py_model_master m ON m.id = a.model_id AND m.is_active=true
        WHERE p.is_active=true AND m.bit_number=%s
        ORDER BY p.py_no
    """, (model_bit,))
    return [dict(r) for r in cur.fetchall()]


# ── Main diagnostic ─────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip",       default=DEFAULT_IP)
    ap.add_argument("--port",     type=int,   default=DEFAULT_PORT)
    ap.add_argument("--start",    type=int,   default=DEFAULT_START)
    ap.add_argument("--end",      type=int,   default=DEFAULT_END)
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    ap.add_argument("--line-id",  type=int,   default=DEFAULT_LINE_ID)
    ap.add_argument("--model",    type=int,   default=None, help="override model bit (auto-detect if omitted)")
    ap.add_argument("--once",     action="store_true")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()

    use_color = not args.no_color
    count     = args.end - args.start + 1

    # Connect to DB FIRST so we can look up the line's PLC IP/port from
    # mes_lines (unless the user explicitly passed --ip / --port).
    print(paint("[CONNECT] DB …", "c", use_color))
    conn = db_connect()

    plc_ip, plc_port = args.ip, args.port
    user_set_ip   = ("--ip"   in sys.argv)
    user_set_port = ("--port" in sys.argv)
    if conn is not None and args.line_id and not (user_set_ip and user_set_port):
        try:
            db_ip, db_port = fetch_line_plc(conn, args.line_id)
            if not user_set_ip   and db_ip:   plc_ip   = db_ip
            if not user_set_port and db_port: plc_port = db_port
            print(paint(f"[INFO] Resolved PLC for line {args.line_id}: "
                        f"{plc_ip}:{plc_port}", "c", use_color))
        except Exception as exc:
            print(paint(f"[WARN] Couldn't resolve line PLC: {exc}; "
                        f"using {plc_ip}:{plc_port}", "y", use_color))

    print(paint(f"[CONNECT] PLC {plc_ip}:{plc_port} …", "c", use_color))
    plc = pymcprotocol.Type4E()
    try:
        plc.connect(plc_ip, plc_port)
    except Exception as exc:
        print(paint(f"[ERROR] PLC connect failed: {exc}", "r", use_color)); sys.exit(2)
    print(paint("[OK] PLC connected", "g", use_color))
    if conn is None:
        print(paint("[ERROR] DB connect failed — running in raw-only mode", "r", use_color))
    else:
        print(paint("[OK] DB connected", "g", use_color))

    iteration = 0
    last_vals: dict[str, int] = {}

    try:
        while True:
            iteration += 1

            # 1. Current model
            if args.model is not None:
                model_bit, model_name = args.model, f"(forced #{args.model})"
            elif conn is not None:
                model_bit, model_name = fetch_current_model(conn, args.line_id)
            else:
                model_bit, model_name = None, None

            # 2. PY configs for this model
            py_configs = []
            if conn is not None and model_bit:
                try: py_configs = fetch_py_configs_for_model(conn, model_bit)
                except Exception as exc:
                    print(paint(f"[DB ERR] {exc}", "r", use_color))

            # Index configs by individual register token
            import re as _re
            REG_RE = _re.compile(r"(?:D|R|M|L|F|T|C|S)\d+|(?:X|Y|W|B)[0-9A-F]+", _re.IGNORECASE)
            cfg_by_reg: dict[str, dict] = {}
            for py in py_configs:
                for tok in REG_RE.findall((py.get("register_addr") or "").upper()):
                    cfg_by_reg[tok] = py

            # 3. Read PLC range
            try:
                vals = plc.batchread_wordunits(headdevice=f"D{args.start}", readsize=count) or []
            except Exception as exc:
                print(paint(f"[READ ERR] {exc}", "r", use_color)); time.sleep(args.interval); continue

            # 4. Render
            clear(use_color)
            # Header
            head = f"Poka-Yoke Diagnostic — Line #{args.line_id}"
            if model_bit is not None:
                head += f"  |  Running Model:  {paint(f'#{model_bit}', 'c', use_color, True)}"
                if model_name: head += f"  {paint(model_name, 'w', use_color)}"
            else:
                head += "  |  " + paint("No active model detected", "r", use_color)
            print(head)
            print("=" * 96)

            # PART A — configured PYs for current model with live comparison
            if py_configs:
                print(paint(f"▶ CONFIGURED POKA-YOKES FOR MODEL #{model_bit}  ({len(py_configs)} checks)", "c", use_color, True))
                print(f"┌──────────────────┬──────┬────────┬─────────────────┬──────────────┬────────┬──────────┐")
                print(f"│ PY No            │ Reg  │ Type   │ Expected        │ Actual (PLC) │ Value  │ Status   │")
                print(f"├──────────────────┼──────┼────────┼─────────────────┼──────────────┼────────┼──────────┤")
                for py in py_configs:
                    raw_regs = py.get("register_addr") or ""
                    regs     = REG_RE.findall(raw_regs.upper())
                    rc       = int(py.get("register_count") or 1)
                    if rc == 1:
                        expected     = expected_codes_1reg(py.get("desired_value"))
                        exp_label    = CODE_MAP_1REG.get(int(py.get("desired_value") or 0), "PASS") \
                                          if py.get("desired_value") else "PASS (skip)"
                    else:
                        expected     = expected_codes_2reg(py.get("desired_value"), py.get("desired_value_2"))
                        d1 = CODE_MAP_1REG.get(int(py.get("desired_value")   or 0), "PASS")
                        d2 = CODE_MAP_1REG.get(int(py.get("desired_value_2") or 0), "PASS")
                        exp_label    = f"{d1},{d2}"

                    for reg in regs:
                        # Locate this register in the scanned range
                        try: ridx = int(reg[1:]) - args.start
                        except: ridx = -1
                        if 0 <= ridx < len(vals):
                            actual = int(vals[ridx] or 0)
                            act_lbl = (CODE_MAP_1REG if rc == 1 else CODE_MAP_2REG).get(actual, f"raw{actual}")
                            if expected is None:
                                status = paint("SKIP (PASS)", "k", use_color)
                            elif actual in expected:
                                status = paint("OK  ✓",        "g", use_color, True)
                            else:
                                status = paint("FAULT ✗",      "r", use_color, True)
                            print(f"│ {py['py_no']:<16} │ {reg:<4} │ {rc}-reg  │ "
                                  f"{exp_label:<15} │ {act_lbl:<12} │ {paint(f'{actual:>6}', 'y', use_color)} │ "
                                  f"{status:<18} │")
                        else:
                            print(f"│ {py['py_no']:<16} │ {reg:<4} │ {rc}-reg  │ "
                                  f"{exp_label:<15} │ {'(out of range)':<12} │ {'—':>6} │ {'—':<8} │")
                print(f"└──────────────────┴──────┴────────┴─────────────────┴──────────────┴────────┴──────────┘")
            else:
                print(paint("▶ No poka-yoke configured for the current model "
                            "(or DB disconnected)", "y", use_color))

            # PART B — raw dump of every register in the scan range
            print()
            print(paint(f"▶ RAW DUMP  D{args.start}..D{args.end}", "c", use_color, True))
            print(f"┌──────────┬─────────┬─────────────────────────────────────────┬────────┐")
            print(f"│ Register │  Value  │ Decoded                                 │ Diff   │")
            print(f"├──────────┼─────────┼─────────────────────────────────────────┼────────┤")
            for i, raw in enumerate(vals):
                v     = int(raw or 0)
                reg   = f"D{args.start + i}"
                prev  = last_vals.get(reg)
                diff  = "" if prev is None else ("⇢ CHG" if prev != v else "·")
                lbl   = decode_dual(v)
                fg    = "g" if v == 0 else ("y" if v in (1,2) else ("m" if v in (3,4) else "r"))
                val_s = paint(f"{v:>7}", fg, use_color)
                cfg   = reg in cfg_by_reg
                marker = paint("★", "c", use_color) if cfg else " "
                print(f"│ {reg:<8}{marker} │ {val_s} │ {lbl:<39} │ {diff:<6} │")
                last_vals[reg] = v
            print(f"└──────────┴─────────┴─────────────────────────────────────────┴────────┘")
            print(f"Iter #{iteration}  {time.strftime('%Y-%m-%d %H:%M:%S')}  "
                  f"|  ★ = register has PY config for current model  "
                  f"|  interval={args.interval}s  |  Ctrl+C to stop")

            if args.once: break
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n[STOP] user interrupted.")
    finally:
        try: plc.close()
        except Exception: pass
        if conn is not None:
            try: conn.close()
            except Exception: pass


if __name__ == "__main__":
    main()
