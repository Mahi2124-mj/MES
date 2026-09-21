"""
plc_model_lookup.py
===================
Terminal utility — model bit number daalo, ye **live PLC** se actual output
padhega aur decode karke dikhayega ki PLC us model ke liye kaunsa D-bit par
kya value output kar raha hai.

Data source: live PLC (via pymcprotocol MC Protocol 4E).
DB ka use sirf ye pata karne ke liye hota hai ki:
  • given model bit ke liye kaunsi PYs configured hain (D-bits + descriptions)
  • model ka naam kya hai (display ke liye)

Usage:
    python plc_model_lookup.py              # prompt puchega — model bit daalo
    python plc_model_lookup.py 9            # directly model bit 9
    python plc_model_lookup.py list         # saare active models list karega
    python plc_model_lookup.py 9 --watch    # 2s interval pe continuous refresh
    python plc_model_lookup.py 9 --ip 192.168.10.150 --port 5002

Output columns:
    D-Bit        — PLC register address (e.g. D401)
    Poka-Yoke    — description from master
    Sensing      — X-bit (if configured)
    Expected     — configured desired (ON / OFF / PASS) — for reference
    PLC Raw      — actual integer read from PLC
    PLC Decoded  — PASS / OFF / ON / ON,OFF etc. depending on reg count
    Status       — ✓ MATCH, ✗ MISMATCH, — SKIP(PASS)
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Optional

try:
    import pymcprotocol
except ImportError:
    print("pymcprotocol not installed. Run: pip install pymcprotocol", file=sys.stderr)
    sys.exit(1)

from database import get_conn


# ── Defaults (override via CLI flags) ───────────────────────────────────────
DEFAULT_IP       = "192.168.10.150"
DEFAULT_PORT     = 5002
DEFAULT_INTERVAL = 2.0
DEFAULT_START    = 401      # scan range — every D-register here is shown
DEFAULT_END      = 425      # regardless of whether it's configured or not

CODE_MAP_1REG = {0: "PASS", 1: "OFF",     2: "ON"}
CODE_MAP_2REG = {0: "PASS", 1: "OFF,OFF", 2: "OFF,ON", 3: "ON,OFF", 4: "ON,ON"}

GREY  = "\033[90m"
BOLD  = "\033[1m"
CYAN  = "\033[36m"
GREEN = "\033[32m"
RED   = "\033[31m"
YELL  = "\033[33m"
RESET = "\033[0m"


# ── Helpers ─────────────────────────────────────────────────────────────────
def _int_or_none(v):
    try:
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def expected_codes_1reg(dv):
    v = _int_or_none(dv)
    if v is None or v == 0:
        return None
    return {v}


def expected_codes_2reg(dv1, dv2):
    v1, v2 = _int_or_none(dv1), _int_or_none(dv2)
    if (v1 is None or v1 == 0) and (v2 is None or v2 == 0):
        return None
    def opts(x):
        if x is None or x == 0: return {1, 2}
        return {x}
    out = set()
    for a in opts(v1):
        for b in opts(v2):
            out.add({(1,1):1,(1,2):2,(2,1):3,(2,2):4}[(a,b)])
    return out or None


def fmt_expected(dv, dv2, reg_cnt):
    v1, v2 = _int_or_none(dv), _int_or_none(dv2)
    if reg_cnt == 2:
        if (v1 is None or v1 == 0) and (v2 is None or v2 == 0):
            return "PASS"
        a = CODE_MAP_1REG.get(v1 or 0, "—")
        b = CODE_MAP_1REG.get(v2 or 0, "—")
        return f"{a}/{b}"
    return CODE_MAP_1REG.get(v1 or 0, "—")


# ── DB lookups ──────────────────────────────────────────────────────────────
def list_models():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT bit_number, model_name, model_type, series, old_model_no
            FROM mes_py_model_master
            WHERE is_active = true AND bit_number IS NOT NULL
            ORDER BY bit_number
        """)
        rows = cur.fetchall()

    if not rows:
        print("No active models found.")
        return

    print(f"\n{BOLD}Active Models{RESET}")
    print(f"{'Bit':<6} {'Model Name':<50} {'Type':<18} {'Series':<10} {'Old No.':<20}")
    print("─" * 108)
    for bit, name, typ, series, old in rows:
        print(f"{CYAN}{bit:<6}{RESET} {name or '—':<50} {typ or '—':<18} "
              f"{series or '—':<10} {old or '—':<20}")
    print(f"\n{len(rows)} models. Run `python plc_model_lookup.py <bit>` for live PLC readout.\n")


def fetch_model_info(model_bit: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, model_name, model_type, series, old_model_no
            FROM mes_py_model_master
            WHERE bit_number = %s AND is_active = true
            ORDER BY id DESC LIMIT 1
        """, (model_bit,))
        return cur.fetchone()


def fetch_py_configs(model_id: int):
    """Every PY assigned to this model — returns register + sensing + expected."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT m.py_no,
                   m.description,
                   m.bit                            AS register_addr,
                   COALESCE(m.register_count, 1)    AS register_count,
                   m.sensing_bits,
                   m.side,
                   a.desired_value,
                   a.desired_value_2,
                   z.zone_name
            FROM mes_py_assignments a
            JOIN mes_py_master m
              ON (m.id = a.py_id OR m.py_no = a.py_no)
             AND m.is_active = true
            LEFT JOIN mes_zones z ON z.id = m.zone_id
            WHERE a.model_id = %s
            ORDER BY m.bit NULLS LAST, m.py_no
        """, (model_id,))
        return cur.fetchall()


# ── PLC read ────────────────────────────────────────────────────────────────
def connect_plc(ip: str, port: int):
    plc = pymcprotocol.Type4E()
    plc.connect(ip, port)
    return plc


def read_register(plc, token: str):
    """Read a single Mitsubishi register.  X/Y/W/B → bit unit; everything
    else → word unit.  Returns int value or None on read error."""
    prefix = token[0].upper()
    is_bit = prefix in ("X", "Y", "W", "B")
    try:
        if is_bit:
            vals = plc.batchread_bitunits(headdevice=token, readsize=1)
        else:
            vals = plc.batchread_wordunits(headdevice=token, readsize=1)
        return int(vals[0] or 0)
    except Exception as e:
        print(f"{RED}  [PLC read {token} failed: {e}]{RESET}")
        return None


# ── Renderer ────────────────────────────────────────────────────────────────
def render(model_bit: int, plc, start: int, end: int, clear_screen: bool = False):
    model = fetch_model_info(model_bit)
    if not model:
        print(f"{RED}No active model found with bit_number = {model_bit}.{RESET}")
        print("Tip: `python plc_model_lookup.py list` to see all models.")
        return

    model_id, model_name, m_type, series, old_no = model

    if clear_screen:
        print("\033[2J\033[H", end="")

    print()
    print(f"{BOLD}{CYAN}Model Bit #{model_bit}{RESET}  —  "
          f"{BOLD}{model_name}{RESET}")
    print(f"  Type: {m_type or '—'}   |   Series: {series or '—'}   |   "
          f"Old No.: {old_no or '—'}")
    print(f"  Time: {time.strftime('%Y-%m-%d %H:%M:%S')}   |   "
          f"Range: D{start}..D{end}")
    print()

    configs = fetch_py_configs(model_id)

    # Index configured PYs by each register token they cover.
    import re
    REG_RE = re.compile(r"(?:D|R|M|L|F|T|C|S)\d+|(?:X|Y|W|B)[0-9A-F]+", re.IGNORECASE)
    cfg_by_reg: dict[str, dict] = {}
    for (py_no, desc, raw_reg, reg_cnt, sens, side, dv, dv2, zone) in configs:
        for tok in REG_RE.findall((raw_reg or "").upper()):
            cfg_by_reg[tok] = {
                "py_no": py_no, "desc": desc, "reg_cnt": reg_cnt,
                "sens": sens, "side": side, "dv": dv, "dv2": dv2, "zone": zone,
            }

    # One block batch-read of the whole D-range is way faster than 25 round-trips.
    count = end - start + 1
    try:
        vals = plc.batchread_wordunits(headdevice=f"D{start}", readsize=count) or []
    except Exception as e:
        print(f"{RED}[PLC READ ERR] {e}{RESET}")
        return

    # Header
    print(f"{BOLD}{'D-Bit':<8} {'PLC Raw':<8} {'1-reg':<8} {'2-reg':<10} "
          f"{'Poka-Yoke':<30} {'Expected':<10} {'Status':<12} {'Sensing':<10}{RESET}")
    print("─" * 108)

    match_ct = mismatch_ct = skip_ct = unconf_ct = 0
    for i in range(count):
        d_num = start + i
        d_bit = f"D{d_num}"
        raw   = int(vals[i] or 0) if i < len(vals) else None

        # Generic decode — show both 1-reg and 2-reg interpretations so user
        # can see regardless of how it's meant to be read.
        dec1 = CODE_MAP_1REG.get(raw, f"raw{raw}") if raw is not None else "—"
        dec2 = CODE_MAP_2REG.get(raw, f"raw{raw}") if raw is not None else "—"

        cfg = cfg_by_reg.get(d_bit)

        if cfg:
            # Configured for this model
            reg_cnt  = int(cfg["reg_cnt"] or 1)
            expected = (expected_codes_1reg(cfg["dv"]) if reg_cnt == 1
                        else expected_codes_2reg(cfg["dv"], cfg["dv2"]))
            exp_label  = fmt_expected(cfg["dv"], cfg["dv2"], reg_cnt)
            desc_short = (cfg["desc"] or "").strip()[:30]
            sens_short = (cfg["sens"] or "—")[:10]

            if raw is None:
                status = f"{YELL}  PLC ERR  {RESET}"
            elif expected is None:
                status = f"{GREY}—  SKIP   {RESET}"; skip_ct += 1
            elif raw in expected:
                status = f"{GREEN}✓  MATCH  {RESET}"; match_ct += 1
            else:
                status = f"{RED}✗  MISMATCH{RESET}"; mismatch_ct += 1

            # Color expected
            if exp_label == "PASS":          exp_col = f"{GREY}{exp_label:<10}{RESET}"
            elif exp_label in ("ON","ON/ON"):exp_col = f"{GREEN}{exp_label:<10}{RESET}"
            elif exp_label in ("OFF","OFF/OFF"):exp_col = f"{RED}{exp_label:<10}{RESET}"
            else:                             exp_col = f"{YELL}{exp_label:<10}{RESET}"
        else:
            # NOT configured for this model — just show raw decoded values.
            desc_short = f"{GREY}(not configured){RESET}"
            exp_col    = f"{GREY}{'—':<10}{RESET}"
            status     = f"{GREY}—  UNCONF  {RESET}"
            sens_short = "—"
            unconf_ct += 1

        bit_col = f"{CYAN}{d_bit:<8}{RESET}"
        raw_col = f"{'err' if raw is None else str(raw):<8}"
        # dec1/dec2 dim for unconfigured rows so eye lands on configured ones
        if cfg:
            d1_col = f"{dec1:<8}"
            d2_col = f"{dec2:<10}"
            desc_col = f"{desc_short:<30}"
        else:
            d1_col = f"{GREY}{dec1:<8}{RESET}"
            d2_col = f"{GREY}{dec2:<10}{RESET}"
            # plain-text width for the grey-wrapped string
            pad = max(0, 30 - len("(not configured)"))
            desc_col = f"{desc_short}{' ' * pad}"

        print(f"{bit_col} {raw_col} {d1_col} {d2_col} "
              f"{desc_col} {exp_col} {status:<12} {sens_short:<10}")

    print("─" * 108)
    total_conf = match_ct + mismatch_ct + skip_ct
    print(f"{BOLD}Summary:{RESET} "
          f"{GREEN}{match_ct} MATCH{RESET}  |  "
          f"{RED}{mismatch_ct} MISMATCH{RESET}  |  "
          f"{GREY}{skip_ct} SKIP (PASS){RESET}  |  "
          f"{GREY}{unconf_ct} unconfigured{RESET}  |  "
          f"total configured {total_conf}")
    print()


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Live PLC output viewer for a given model bit.",
    )
    ap.add_argument("model", nargs="?",
                    help="Model bit number, or 'list' to dump all models.")
    ap.add_argument("--ip",       default=DEFAULT_IP)
    ap.add_argument("--port",     type=int,   default=DEFAULT_PORT)
    ap.add_argument("--watch",    action="store_true",
                    help="Continuously refresh every --interval seconds.")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    ap.add_argument("--start",    type=int,   default=DEFAULT_START,
                    help=f"First D-register to scan (default {DEFAULT_START}).")
    ap.add_argument("--end",      type=int,   default=DEFAULT_END,
                    help=f"Last D-register to scan (default {DEFAULT_END}).")
    args = ap.parse_args()

    arg = args.model
    if arg and arg.lower() in ("list", "ls"):
        list_models()
        return

    if arg is None:
        try:
            arg = input("Enter model bit number (or 'list'): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if arg.lower() in ("list", "ls"):
            list_models()
            return

    try:
        bit = int(arg)
    except ValueError:
        print(f"{RED}Invalid model bit: {arg!r}{RESET}")
        sys.exit(1)

    print(f"{CYAN}[CONNECT] PLC {args.ip}:{args.port} …{RESET}")
    try:
        plc = connect_plc(args.ip, args.port)
    except Exception as e:
        print(f"{RED}[ERROR] PLC connect failed: {e}{RESET}")
        sys.exit(2)
    print(f"{GREEN}[OK] PLC connected{RESET}")

    try:
        if args.watch:
            while True:
                render(bit, plc, args.start, args.end, clear_screen=True)
                time.sleep(args.interval)
        else:
            render(bit, plc, args.start, args.end, clear_screen=False)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        try: plc.close()
        except Exception: pass


if __name__ == "__main__":
    main()
