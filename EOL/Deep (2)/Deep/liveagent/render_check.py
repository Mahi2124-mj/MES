"""render_check.py — does the page the operator actually sees hold together?

Every other invariant in this folder asks the API a question.  This one opens the
page in a real browser and looks at it, because a whole class of defect never
reaches the API at all:

  • the work-centre selector sitting 88 px past the right edge of a 1366 laptop,
    so nothing could be chosen and the sheet just stayed blank;
  • form labels sliced by their own SVG clip-path — readable in one engine,
    "Repair /Sorting due to own ca" in another;
  • an SVG backdrop centred 109 px away from the absolutely-positioned overlay
    that carries the data, so a 1920 screen printed overlapping garbage;
  • a product row silently overwritten, leaving the column 118 pieces short of
    its own TOTAL.

None of those change a single API response.  All four are caught by loading the
page at several window widths and asking: can every control be reached, is any
text cut off, do the two layers share an origin, and do the printed numbers add
up to what the API says.

Headless Edge is used deliberately.  geckodriver on this box is snap-confined —
it cannot be signalled (kill returns EPERM even as its own owner), so every run
would leak a process and a port for good.  `microsoft-edge --headless=new
--dump-dom` runs the page, prints the finished DOM and exits by itself, leaving
nothing behind.
"""

import json
import os
import re
import shutil
import subprocess
import urllib.parse

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, "mes-frontend", "dist")
HARNESS_SRC = os.path.join(HERE, "render_harness.html")
HARNESS_NAME = "_agent_render_check.html"

FRONT = os.environ.get("MES_FRONT", "http://127.0.0.1:5656")
# Overridable so the detectors themselves can be re-validated against a known
# BAD copy of the page — a check that cannot fail is worth nothing.
PAGE = os.environ.get("AGENT_RENDER_PAGE", "/peff-sheet.html")
EDGE = os.environ.get("AGENT_BROWSER", "microsoft-edge")

# The widths that actually matter here: a full-HD monitor, and the laptop sizes
# the plant uses.  The sheet is ~1587 px wide, so these straddle it on both
# sides — the wide ones catch a stretched backdrop, the narrow ones catch
# controls pushed out of reach.
WIDTHS = [1920, 1600, 1366, 1280]

# Work-centre -> line, mirroring the sheet's own map, used only to pick a
# line/shift that has real production so "do the numbers add up" means something.
WC_TO_LINE = {"Q31S09B": 15, "Q31S09A": 15, "Q31S04B": 11, "Q31S06B": 2, "Q31S03B": 4}


def _p(sev, area, what, why, fix):
    return {"sev": sev, "area": area, "what": what, "why": why, "fix": fix}


def _edge_available():
    return shutil.which(EDGE) is not None


def _pick_subject(base, token, dates):
    """Find a work-centre / date that actually produced parts, so the
    reconciliation check has something to reconcile."""
    h = {"Authorization": "Bearer " + token}
    for date in dates:
        for wc, line in WC_TO_LINE.items():
            try:
                r = requests.get(f"{base}/api/peff/data",
                                 params={"line_id": line, "date": date, "shift": wc[-1]},
                                 headers=h, timeout=20)
                if not r.ok:
                    continue
                j = r.json()
                if (j.get("totals") or {}).get("total"):
                    return wc, date, j
            except Exception:
                continue
    return None, None, None


def _run_one(url, width, timeout=70):
    """Load a page headless at `width` and return the harness verdict."""
    cmd = [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
           "--virtual-time-budget=20000", f"--window-size={width},900",
           "--dump-dom", url]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        return None, "browser timed out"
    except Exception as exc:
        return None, str(exc)[:120]
    m = re.search(r"###(\{.*?\})###", out, re.S)
    if not m:
        return None, "harness did not report"
    try:
        return json.loads(m.group(1)), None
    except Exception as exc:
        return None, "unreadable verdict: " + str(exc)[:80]


def page_renders_correctly(base, token, root=None, **_):
    """Open the sheet at several window widths and check what is on screen.

    Returns problems in the same shape as every other invariant here.
    """
    out = []
    if not _edge_available():
        return [_p("warning", "Agent",
                   "Render check skip hua — headless browser nahi mila",
                   f"'{EDGE}' PATH me nahi hai, isliye page dekha nahi ja saka",
                   "AGENT_BROWSER env se browser ka path de do")]
    if not os.path.isdir(DIST):
        return [_p("warning", "Agent", "Render check skip hua — dist folder nahi mila",
                   DIST, "mes-frontend build kahan hai ye confirm karo")]

    # Recent dates, newest first — the sheet is usually looked at for yesterday.
    import datetime
    today = datetime.date.today()
    # Yesterday first, today LAST.  A shift that is still running gains parts
    # between the API snapshot and the render, and the sheet would look wrong
    # when it is simply newer than the number we compared it against.
    dates = [(today - datetime.timedelta(days=d)).isoformat() for d in range(1, 5)]
    dates.append(today.isoformat())
    wc, date, api = _pick_subject(base, token, dates)

    harness = os.path.join(DIST, HARNESS_NAME)
    try:
        shutil.copyfile(HARNESS_SRC, harness)
    except Exception as exc:
        return [_p("warning", "Agent", "Render harness copy nahi hua", str(exc)[:120],
                   "liveagent/render_harness.html aur dist ki permission dekho")]

    seen_rules = {}
    try:
        qs = {"t": token, "page": PAGE}
        if wc:
            qs["wc"] = wc
            qs["date"] = date
        url = f"{FRONT}/{HARNESS_NAME}?" + urllib.parse.urlencode(qs)

        for width in WIDTHS:
            verdict, err = _run_one(url, width)
            if err:
                out.append(_p("warning", "Render",
                              f"{PAGE} {width}px par check nahi ho paya",
                              err, "liveagent/render_check.py"))
                continue

            for pr in verdict.get("problems", []):
                # Collapse a rule that fires at many widths into one finding —
                # the operator needs the fact, not four copies of it.
                key = (pr.get("rule"), pr.get("what"))
                seen_rules.setdefault(key, {"widths": set(), "detail": pr.get("detail", "")})
                seen_rules[key]["widths"].add(width)

            # The page is allowed to be zoomed to fit, but never to hide data.
            printed = verdict.get("printed")
            if printed and api:
                t = api.get("totals") or {}
                if (printed["total"], printed["ok"], printed["ng"]) != \
                   (t.get("total"), t.get("ok"), t.get("ng")):
                    # Read the API again.  On a live shift the line keeps
                    # producing, so the sheet is legitimately AHEAD of the
                    # snapshot taken before the page was rendered; only a value
                    # outside the before/after band is a real disagreement.
                    _, _, again = _pick_subject(base, token, [date])
                    t2 = (again or {}).get("totals") or {}
                    band_ok = all(
                        min(a, b) <= printed[k] <= max(a, b)
                        for k, a, b in (("total", t.get("total", 0), t2.get("total", 0)),
                                        ("ok",    t.get("ok", 0),    t2.get("ok", 0)),
                                        ("ng",    t.get("ng", 0),    t2.get("ng", 0)))
                        if a is not None and b is not None)
                    if band_ok:
                        continue
                    out.append(_p("critical", "Render",
                                  f"PEFF sheet par chhapa hua total API se match nahi kar raha "
                                  f"({width}px)",
                                  f"sheet: {printed['total']}/{printed['ok']}/{printed['ng']} — "
                                  f"API: {t.get('total')}/{t.get('ok')}/{t.get('ng')} "
                                  f"[{wc} {date}]",
                                  "product rows ka clustering ya remainder row dekho "
                                  "(mes-frontend/public/peff-sheet.html)"))
    finally:
        try:
            os.remove(harness)
        except Exception:
            pass

    RULE_TEXT = {
        "reachable":  ("critical", "Operator control screen se bahar hai"),
        "readable":   ("critical", "Screen par text kata hua hai"),
        "aligned":    ("critical", "Form ka backdrop aur data alag-alag jagah hain"),
        "fonts":      ("critical", "Page ek aise font par depend kar raha hai jo machine par ho bhi sakta hai nahi bhi"),
        "reconciled": ("critical", "Chhapi hui value ka koi label nahi"),
        "loads":      ("critical", "Page khula hi nahi"),
    }
    for (rule, what), info in sorted(seen_rules.items()):
        sev, head = RULE_TEXT.get(rule, ("warning", "Render problem"))
        widths = ", ".join(str(x) + "px" for x in sorted(info["widths"], reverse=True))
        out.append(_p(sev, "Render", f"{head}: {what}",
                      f"{widths} par dikha — {info['detail']}",
                      "mes-frontend/public/peff-sheet.html; "
                      "dobara dekhne ke liye: python3 liveagent/render_check.py"))
    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.join(ROOT, "Phase2"))
    from auth import create_token                      # type: ignore
    tok = create_token("liveagent", "admin", 1)
    api_base = os.environ.get("MES_BASE", "http://127.0.0.1:8080")
    found = page_renders_correctly(api_base, tok)
    if not found:
        print("render check: clean")
    for f in found:
        print(f"[{f['sev']}] {f['what']}\n    {f['why']}")
    raise SystemExit(1 if any(f["sev"] == "critical" for f in found) else 0)
