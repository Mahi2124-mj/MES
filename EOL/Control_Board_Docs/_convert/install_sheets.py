"""Copy the operator's HTML sheets into the MES frontend for the Control Board.

2026-11-05.  Copies each sheet from Control_Board_Docs to
mes-frontend/public/control-board-docs/ under its clean name and appends a small
script: the Control Board asks the sheet (postMessage "cb-zoom") to zoom itself
to the frame width.  A CSS-transform scale of the whole frame made Firefox drop
1 px table lines (operator's laptop: Track Record Sheet columns 7-10 merged);
zoom re-lays the sheet out at the final size so every line keeps at least one
device pixel.  The sheet answers "cb-zoom-ok" only when the browser supports
CSS zoom; without an answer the Control Board falls back to the transform.
The Track Record Sheet also gets line / month dropdowns and its cycle time and
target from Standard Data (TRACK_SCRIPT, data posted in by the Control Board).

usage: python3 install_sheets.py <Control_Board_Docs dir> <public/control-board-docs dir>
"""
import base64, hashlib, os, re, sys

SHEETS = {
    "1_track-record-sheet.html": "track-record-sheet.html",
    "2_4M_Change_Control_Sheet_Editable.html": "4m-change-control-sheet.html",
    "3_Operator_status.html": "operator-status.html",
    "4_defect_record_sheet.html": "defect-recording-sheet.html",
    "4_defect_record_rec.html": "defect-recording-sheet-recliner.html",
    "5_safety_chart.html": "annual-safety-chart.html",
    # 2026-11-12 (operator): Sheet 12 and Sheet 10 formats
    "6_5s_daily_checksheet.html": "5s-daily-check-sheet.html",
    "7_q_chart.html": "q-chart.html",
}
# These two have no red TOYOTA BOSHOKU box: the 5S sheet has no logo place, the
# Q Chart's "YOUR COMPANY" box becomes the logo through SHEET_FIXES.
NO_LOGO_BOX = {"5s-daily-check-sheet.html", "q-chart.html"}

ZOOM_SCRIPT = """
<script>
/* MES Control Board: fit-to-width zoom requested by the page around this frame. */
(function () {
  var ok = window.CSS && CSS.supports && CSS.supports("zoom", "0.5");
  window.addEventListener("message", function (e) {
    if (e.source !== window.parent || !e.data || e.data.type !== "cb-zoom" || !ok) return;
    var z = Number(e.data.zoom) || 1;
    document.documentElement.style.zoom = (z === 1) ? "" : String(z);
    try { window.parent.postMessage({ type: "cb-zoom-ok", zoom: z }, "*"); } catch (x) {}
  });
})();
</script>
"""
MARK = "MES Control Board: fit-to-width zoom"

# 2026-11-06: the red "TOYOTA BOSHOKU" text box at the top-left of each sheet is
# the logo's place — it becomes the company logo (TB mark + name, as on the
# Hourly Report).  The box is built either in a JS string or as plain HTML, so
# the snippet uses double quotes only and no line breaks.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tb_logo.png"), "rb") as _f:
    LOGO_URI = "data:image/png;base64," + base64.b64encode(_f.read()).decode()
# 2026-11-06: "Print / Save PDF" opened the print dialog; the operator wants the
# PDF saved straight to the device, A3, as laid out -> Save PDF (cb-pdf.js,
# copied next to the sheets) + a separate Print button.
PRINT_BTN = '<button class="primary" onclick="window.print()">Print / Save PDF</button>'
PDF_BTNS = ('<button class="primary cb-pdf-btn" onclick="cbSavePdf()">Save PDF</button>'
            '<button onclick="window.print()">Print</button>')
# 2026-11-12 (operator: "q chart ka alignment dekh, 2-3 months me sahi nahi hai,
# aur Q structure ko bhi sahi kar de"): the format's outline + month placement
# script is replaced by q-chart-layout.js — every pyramid placed inside its
# segment clear of all lines (q_chart/qlayout.py search, q_chart/qgen.py writes
# the file from q_chart/layout.json), and the Q tail as one bar that replaces
# the NOVEMBER / DECEMBER divider instead of a white patch over the ring.
# (file, start marker, end marker incl., replacement file)
SHEET_BLOCKS = {
    "q-chart.html": [("        // 1. GENERATE SVG OVAL BACKGROUND",
                      "        document.getElementById('months-layer').innerHTML = monthsHTML;",
                      "q-chart-layout.js")],
}


def apply_blocks(dst, html, src):
    here = os.path.dirname(os.path.abspath(__file__))
    for start, end, fname in SHEET_BLOCKS.get(dst, []):
        if html.count(start) != 1 or html.count(end) != 1:
            raise SystemExit("%s: block markers not found once" % src)
        i, j = html.index(start), html.index(end) + len(end)
        with open(os.path.join(here, fname), encoding="utf-8") as f:
            html = html[:i] + f.read() + html[j:]
    return html


# 2026-11-12: the 5S and Q Chart formats style their print button inline
PRINT_BTN_ALT = '<button onclick="window.print()" style="background:#0056b3; color:#fff;">Print / Save PDF</button>'
PDF_BTNS_ALT = ('<button class="cb-pdf-btn" onclick="cbSavePdf()" style="background:#0056b3; color:#fff;">Save PDF</button>'
                '<button onclick="window.print()">Print</button>')
PDF_JS = ("cb-pdf.js", "cb-pdf-render.js")   # sheet side + drawing (shared with the Control Board)
# the script files are cached by browsers for an hour (serve_prod), the sheets
# are not: the tag carries a content version so a changed script loads at once
def pdf_scripts():
    """(cb-pdf.js text with the render version filled in, render text, cb-pdf.js version)"""
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "cb-pdf-render.js"), encoding="utf-8") as f:
        render = f.read()
    with open(os.path.join(here, "cb-pdf.js"), encoding="utf-8") as f:
        side = f.read().replace("__RENDER_V__", hashlib.md5(render.encode()).hexdigest()[:8])
    return side, render, hashlib.md5(side.encode()).hexdigest()[:8]
LOGO_BOX = re.compile(r'<div\s+style="border:\s*2px solid #(?:c00|d00|b30000);[^"]*">\s*TOYOTA(?:<br>|\s)BOSHOKU\s*</div>')
LOGO_HTML = ('<div style="text-align:center; line-height:1;">'
             '<img src="' + LOGO_URI + '" alt="Toyota Boshoku" style="display:block; margin:0 auto; height:44px; '
             'max-width:100%; object-fit:contain;">'
             '<div style="margin-top:3px; font-size:9px; font-weight:bold; color:#000; letter-spacing:0.3px; '
             'white-space:nowrap;">TOYOTA BOSHOKU</div></div>')

# Track Record Sheet only (2026-11-05): LINE NAME and MONTH become dropdowns and
# the Cycle Time / Target boxes of both shifts fill from Admin → Standard Data →
# Track Record.  2026-11-06: picking a line / month posts "cb-select" to the
# Control Board, which fetches /api/standard-data/track-record/sheet and posts
# it back (cb-data.sheet); every day's A–G, losses and the leader's signature
# fill both shifts, and manual edits keep G at one decimal.  The solid red target
# line, its value box and the black arrow move to the Target value on the graph
# scale (dashed line stays as drawn).  The sheet is sandboxed (no login, no API), so the Control Board
# posts the data in ({type:"cb-data", lines, line_id, month}); opened on its own
# the sheet keeps its original text boxes.
TRACK_SCRIPT = """
<script>
/* MES Control Board: line / month dropdowns + standard cycle time and target. */
(function () {
  var sheet = document.getElementById("mainSheet");
  if (!sheet) return;
  var MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  function cellAfter(label) {
    var tds = sheet.querySelectorAll("td");
    for (var i = 0; i < tds.length; i++) {
      if (tds[i].textContent.replace(/\\s+/g, " ").trim() === label && tds[i].nextElementSibling)
        return tds[i].nextElementSibling;
    }
    return null;
  }
  var ins = [].slice.call(sheet.querySelectorAll("input.cell-input"));
  var ctIn = ins.filter(function (i) { return /^16\\.36 sec\\.$/.test(i.value); });
  var tgIn = ins.filter(function (i) { return /^82 sec\\.$/.test(i.value); });
  var lineTd = cellAfter("LINE NAME:-"), monthTd = cellAfter("MONTH:-");
  var lineSel = null, monthSel = null, data = [];
  var css = document.createElement("style");
  css.textContent = "select.cb-sel{width:100%;height:100%;min-height:24px;border:none;background:transparent;" +
    "font:inherit;font-size:13px;font-weight:bold;padding:2px 6px;cursor:pointer;outline:none}" +
    "select.cb-sel:focus{background:#fff9c4}@media print{select.cb-sel{-webkit-appearance:none;appearance:none}}";
  document.head.appendChild(css);
  function fmt(v) { return (v === null || v === undefined || v === "") ? "" : (Number(v) + " sec."); }
  function tell() {
    try { window.parent.postMessage({ type: "cb-select", line_id: lineSel ? lineSel.value : "",
                                      month: monthSel ? monthSel.value : "" }, "*"); } catch (x) {}
  }
  // status note in the toolbar (hidden when printing, like the toolbar)
  var bar = document.querySelector(".toolbar > div");
  var note = document.createElement("span");
  note.style.cssText = "margin-left:14px;font-size:12px;font-weight:600;color:#475569";
  if (bar) bar.appendChild(note);
  var CODES = ["A","B","C","D","E","F","G","M1","M2","M3","M4","M5","M6"];
  function cell(sh, code, day) { return document.getElementById(sh + "-" + code + "-" + day); }
  // 2026-11-12 (operator): the line leader's signature only on a day whose
  // column has values (MES or typed) - never on an empty day.  Leader = the
  // day's MES row, else the leader saved in Shift Allocation (`leads`).
  var lastSd = null;
  function colHas(sh, day) {
    return CODES.some(function (code) { var el = cell(sh, code, day); return !!(el && String(el.value).trim()); });
  }
  function signDay(sh, day) {
    var s1 = cell(sh, "S1", day);
    if (!s1) return;
    var td = s1.parentNode, old = td.querySelector("img.cb-sign");
    if (old) td.removeChild(old);
    s1.style.display = "";
    if (s1.getAttribute("data-cb-sign")) { s1.value = ""; s1.title = ""; s1.style.fontSize = ""; s1.removeAttribute("data-cb-sign");
                                            s1.readOnly = s1.hasAttribute("data-cb-lock"); }
    var sd = lastSd;
    if (!sd || s1.value || !colHas(sh, day)) return;
    var v = sd.shifts && sd.shifts[sh] ? sd.shifts[sh][String(day)] : null;
    var lid = (v && v.S1) || (sd.leads && sd.leads[sh] ? sd.leads[sh][String(day)] : null);
    var L = lid ? (sd.leaders || {})[lid] : null, name = (v && v.S1_name) || (L && L.name) || "";
    if (L && L.signature) {
      var img = document.createElement("img");
      img.className = "cb-sign"; img.src = L.signature; img.alt = L.name || ""; img.title = L.name || "";
      img.style.cssText = "display:block;max-width:100%;max-height:30px;margin:0 auto;object-fit:contain";
      s1.style.display = "none"; s1.setAttribute("data-cb-sign", "1"); td.insertBefore(img, s1);
    } else if (name) {
      s1.value = name; s1.title = name; s1.style.fontSize = "9px"; s1.setAttribute("data-cb-sign", "1");
      s1.removeAttribute("data-cb-lock"); s1.readOnly = true;          // auto signature: frozen
    }
  }
  sheet.addEventListener("input", function (e) {
    var m = /^([AB])-([A-Z0-9]+)-(\\d+)$/.exec((e.target && e.target.id) || "");
    if (m && m[2] !== "S1" && m[2] !== "S2") signDay(m[1], +m[3]);
  });
  function fillDays(sd) {
    var n = 0;
    lastSd = sd;
    ["A", "B"].forEach(function (sh) {
      var days = (sd && sd.shifts && sd.shifts[sh]) || {};
      for (var day = 1; day <= 31; day++) {
        var v = days[String(day)] || null;
        if (v) n++;
        CODES.forEach(function (code) {
          var el = cell(sh, code, day);
          if (!el) return;
          var has = !!(v && v[code] !== null && v[code] !== undefined && String(v[code]) !== "");
          el.value = has ? String(v[code]) : "";
          // 2026-11-13 (operator): what MES fills is frozen
          if (has) el.removeAttribute("data-cb-lock");
          el.readOnly = has || el.hasAttribute("data-cb-lock");
          el.classList.toggle("cb-mes", has);
        });
        var s1 = cell(sh, "S1", day);
        if (s1) { s1.value = ""; s1.title = ""; s1.style.fontSize = ""; s1.removeAttribute("data-cb-sign");
                  s1.readOnly = s1.hasAttribute("data-cb-lock"); }
        signDay(sh, day);
      }
      if (window.drawShiftGraph) drawShiftGraph(sh);
    });
    return n;
  }
  // manual edits recalculate C, F and G with G at one decimal (operator rule)
  window.updateShiftCalc = function (shift, day) {
    function num(code) { var el = cell(shift, code, day); return el ? parseFloat(el.value) : NaN; }
    var a = num("A"), b = num("B"), d = num("D"), e = num("E");
    var c = (!isNaN(a) || !isNaN(b)) ? (isNaN(a) ? 0 : a) + (isNaN(b) ? 0 : b) : num("C");
    var f = (!isNaN(d) || !isNaN(e)) ? (isNaN(d) ? 0 : d) + (isNaN(e) ? 0 : e) : num("F");
    if (!isNaN(a) || !isNaN(b)) cell(shift, "C", day).value = String(c);
    if (!isNaN(d) || !isNaN(e)) cell(shift, "F", day).value = String(f);
    if (!isNaN(c) && !isNaN(f) && f > 0) cell(shift, "G", day).value = (c / f * 60).toFixed(1);
    if (window.drawShiftGraph) drawShiftGraph(shift);
  };
  function fill() {
    var id = lineSel ? lineSel.value : "", row = null;
    for (var i = 0; i < data.length; i++) if (String(data[i].line_id) === id) row = data[i];
    // 2026-11-13: Standard Data values are frozen (typed only when Standard Data has none)
    function std(el, v) {
      var has = v !== null && v !== undefined && v !== "";
      el.value = row ? fmt(v) : ""; el.placeholder = row ? "not set" : "";
      if (has) el.removeAttribute("data-cb-lock");
      el.readOnly = has || el.hasAttribute("data-cb-lock");
    }
    ctIn.forEach(function (el) { std(el, row ? row.cycle_time : null); });
    tgIn.forEach(function (el) { std(el, row ? row.target : null); });
    placeTargets();
  }
  // Solid red target line, its value box and the black arrow follow the Target
  // box (2026-11-06; the operator drew them fixed at the 80 level labelled "82").
  // Same scale as the blue graph: the sheet's valToY() gives y inside the graph.
  // The dashed line and its "90" box stay as drawn.
  var tcss = document.createElement("style");
  tcss.textContent = ".cb-tline{position:absolute;left:0;height:0;border-top:2.5px solid #ff0000;z-index:3;pointer-events:none}";
  document.head.appendChild(tcss);
  [].slice.call(sheet.querySelectorAll("td")).forEach(function (td) {
    var st = td.getAttribute("style") || "";
    if (st.indexOf("solid #ff0000") >= 0) td.setAttribute("style", st.replace(/border-bottom:[^;]*solid #ff0000[^;]*;?/, ""));
  });
  [].slice.call(sheet.querySelectorAll(".target-callout")).forEach(function (el) {
    if (el.textContent.trim() === "82") el.style.display = "none";
  });
  // Arrow (2026-11-06, operator): starts in the Target box just right of the
  // target value and points at the target line.  Graph px in the title cell:
  // title column 58+98+54 = 210 wide, Target value box = rows 17-19 (y 272-320).
  var COL_W = 210, TIP_X = 242, TAIL_Y = 296, ARROW_TIP_FIXED = 189;
  var meas = document.createElement("canvas").getContext("2d");
  function tailX(input) {
    var txt = input ? (input.value || input.placeholder || "") : "";
    var cs = input ? getComputedStyle(input) : null;
    meas.font = (cs ? cs.fontWeight : "400") + " " + ((input && input.style.fontSize) || "20px") + " " +
                (cs ? cs.fontFamily : "sans-serif");
    return Math.min(COL_W - 14, Math.max(COL_W / 2 + 10, COL_W / 2 + meas.measureText(txt).width / 2 + 8));
  }
  function drawArrow(svg, yTip, xTail) {
    if (!svg) return;
    var left = Math.min(xTail, TIP_X) - 10, top = Math.min(TAIL_Y, yTip) - 10;
    var sx = xTail - left, sy = TAIL_Y - top, px = TIP_X - left, py = yTip - top;
    var dx = px - sx, dy = py - sy, L = Math.sqrt(dx * dx + dy * dy) || 1, ux = dx / L, uy = dy / L;
    var bx = px - 12 * ux, by = py - 12 * uy, nx = -uy, ny = ux;
    function f(v) { return v.toFixed(1); }
    svg.setAttribute("width", String(Math.abs(TIP_X - xTail) + 20));
    svg.setAttribute("height", String(Math.abs(TAIL_Y - yTip) + 20));
    svg.style.right = "auto"; svg.style.bottom = "auto"; svg.style.left = left + "px"; svg.style.top = top + "px";
    svg.innerHTML = '<line x1="' + f(sx) + '" y1="' + f(sy) + '" x2="' + f(bx + 2 * ux) + '" y2="' + f(by + 2 * uy) +
      '" stroke="#000" stroke-width="2.5" /><polygon points="' + f(px) + "," + f(py) + " " + f(bx + 5.5 * nx) + "," +
      f(by + 5.5 * ny) + " " + f(bx - 5.5 * nx) + "," + f(by - 5.5 * ny) + '" fill="#000" />';
  }
  function placeTarget(sh, idx) {
    var cv = document.getElementById("canvas-" + sh);
    if (!cv || typeof window.valToY !== "function") return;
    var host = cv.parentNode, scaleTd = host.previousElementSibling, titleTd = host.parentNode.firstElementChild;
    var svg = titleTd ? titleTd.querySelector("svg") : null;
    var line = host.querySelector(".cb-tline"), call = scaleTd ? scaleTd.querySelector(".cb-tcall") : null;
    if (!line) { line = document.createElement("div"); line.className = "cb-tline"; host.appendChild(line); }
    if (!call && scaleTd) { call = document.createElement("div"); call.className = "target-callout cb-tcall"; scaleTd.appendChild(call); }
    var t = parseFloat(tgIn[idx] ? tgIn[idx].value : ""), ok = !isNaN(t) && t > 0;
    line.style.display = ok ? "" : "none";
    if (call) call.style.display = ok ? "" : "none";
    var xt = tailX(tgIn[idx]);
    if (!ok) { drawArrow(svg, ARROW_TIP_FIXED, xt); return; }
    var y = window.valToY(t);
    line.style.top = (y - 1.25) + "px"; line.style.width = cv.width + "px";
    if (call) { call.textContent = String(Math.round(t * 100) / 100); call.style.top = (y + 2) + "px"; call.style.bottom = "auto"; }
    drawArrow(svg, y, xt);
  }
  function placeTargets() { placeTarget("A", 0); placeTarget("B", 1); }
  tgIn.forEach(function (el) { el.addEventListener("input", placeTargets); el.addEventListener("change", placeTargets); });
  placeTargets();

  // 2026-11-06 (operator) — graph right edge: the day-31 cells carry both the
  // solid border (br-m) and the dotted one (br-h), and br-h wins (later rule),
  // so the graph had no black right edge.  Any such clash keeps the solid one.
  ["t", "b", "l", "r"].forEach(function (s) {
    [].slice.call(sheet.querySelectorAll("td.b" + s + "-m.b" + s + "-h")).forEach(function (td) { td.classList.remove("b" + s + "-h"); });
  });

  // Manager Sign 1/Week: one box per week (days 1-7, 8-14, 15-21, 22-28, 29-31)
  // instead of one per day.  The box keeps the week's first-day id.
  var WEEKS = [[1, 7], [8, 14], [15, 21], [22, 28], [29, 31]];
  ["A", "B"].forEach(function (sh) {
    WEEKS.forEach(function (w, n) {
      var keep = document.getElementById(sh + "-S2-" + w[0]);
      if (!keep) return;
      var td = keep.parentNode;
      for (var d = w[0] + 1; d <= w[1]; d++) {
        var x = document.getElementById(sh + "-S2-" + d);
        if (x && x.parentNode !== td) x.parentNode.parentNode.removeChild(x.parentNode);
      }
      td.colSpan = w[1] - w[0] + 1;
      if (w[1] === 31) { td.classList.remove("br-t"); td.classList.add("br-m"); }
      keep.placeholder = "Week " + (n + 1);
      keep.title = "Manager sign - week " + (n + 1) + " (days " + w[0] + "-" + w[1] + ")";
    });
  });
  // 2026-11-13: the day of each box (CELLS_SCRIPT locks all but today's)
  window.cbDateOf = function (el, P) {
    var m = /^([AB])-([A-Z0-9]+)-(\\d+)$/.exec(el.id || "");
    if (!m || !P.y || !P.m) return "";
    var dim = new Date(P.y, P.m, 0).getDate(), d = +m[3];
    if (m[2] === "S2") {
      for (var i = 0; i < WEEKS.length; i++) if (WEEKS[i][0] === d)
        return d > dim ? "X" : "W:" + cbYmd(P.y, P.m, d) + ":" + cbYmd(P.y, P.m, Math.min(WEEKS[i][1], dim));
      return "X";
    }
    return d <= dim ? cbYmd(P.y, P.m, d) : "X";
  };

  // Graph hover: point at a day to see its value (mouse; a tap on touch panels).
  // Ring on the point, dashed day line, and a box with the day's man hours per
  // piece against the target, man hours and parts.  Screen only (no-print).
  var hcss = document.createElement("style");
  hcss.textContent =
    ".cb-hov{position:absolute;left:0;top:0;z-index:6;cursor:crosshair}" +
    ".cb-vl{position:absolute;top:0;width:0;border-left:1px dashed #0056b3;opacity:.7;pointer-events:none;z-index:3;display:none}" +
    ".cb-dot{position:absolute;width:14px;height:14px;margin:-7px 0 0 -7px;border-radius:50%;box-sizing:border-box;" +
    "background:#fff;border:3px solid #0056b3;pointer-events:none;z-index:7;display:none}" +
    ".cb-tip{position:absolute;z-index:8;pointer-events:none;display:none;background:#0f172a;color:#fff;" +
    "font:12px/1.4 Arial,sans-serif;padding:6px 10px;border-radius:6px;white-space:nowrap;box-shadow:0 4px 14px rgba(0,0,0,.28)}" +
    ".cb-tip .h{font-weight:bold;font-size:13px}.cb-tip .ok{color:#86efac}.cb-tip .bad{color:#fca5a5}.cb-tip .m{color:#cbd5e1}" +
    "@media print{.no-print{display:none!important}}";
  document.head.appendChild(hcss);
  function val(sh, code, day) { var el = cell(sh, code, day), v = el ? parseFloat(el.value) : NaN; return isNaN(v) ? null : v; }
  ["A", "B"].forEach(function (sh, idx) {
    var cv = document.getElementById("canvas-" + sh);
    if (!cv) return;
    var host = cv.parentNode, W = cv.width, H = cv.height, hideT = null;
    function mk(cls) { var e = document.createElement("div"); e.className = cls + " no-print"; host.appendChild(e); return e; }
    var vl = mk("cb-vl"), hov = mk("cb-hov"), dot = mk("cb-dot"), tip = mk("cb-tip");
    vl.style.height = H + "px"; hov.style.width = W + "px"; hov.style.height = H + "px";
    function line(cls, text) { var d = document.createElement("div"); d.className = cls; d.textContent = text; tip.appendChild(d); }
    function hide() { vl.style.display = dot.style.display = tip.style.display = "none"; }
    function show(e) {
      clearTimeout(hideT);
      var r = hov.getBoundingClientRect();
      if (!r.width || typeof window.valToY !== "function") return;
      var day = Math.min(31, Math.max(1, Math.floor((e.clientX - r.left) / r.width * W / 44) + 1));
      var x = (day - 1) * 44 + 22, g = val(sh, "G", day), y = H / 2;
      vl.style.left = x + "px"; vl.style.display = "block";
      tip.textContent = "";
      if (g === null || g <= 0) {
        dot.style.display = "none";
        line("h", "Day " + day); line("m", "No data");
      } else {
        y = window.valToY(g);
        dot.style.left = x + "px"; dot.style.top = y + "px"; dot.style.display = "block";
        var t = parseFloat(tgIn[idx] ? tgIn[idx].value : ""), c = val(sh, "C", day), f = val(sh, "F", day);
        line("h", "Day " + day + ": " + g + " sec.");
        if (!isNaN(t) && t > 0) line(g <= t ? "ok" : "bad", (g <= t ? "Within" : "Above") + " target (" + t + " sec.)");
        if (c !== null) line("m", "Man hour: " + c + " min");
        if (f !== null) line("m", "Parts: " + f);
      }
      tip.style.display = "block";
      var tw = tip.offsetWidth, th = tip.offsetHeight;
      var left = x + 14; if (left + tw > W) left = x - 14 - tw;
      var top = y - th - 12; if (top < 0) top = Math.min(H - th, y + 14);
      tip.style.left = left + "px"; tip.style.top = Math.max(0, top) + "px";
    }
    hov.addEventListener("pointermove", show);
    hov.addEventListener("pointerdown", show);
    hov.addEventListener("pointerleave", function (e) {
      clearTimeout(hideT);
      if (e.pointerType === "mouse") hide(); else hideT = setTimeout(hide, 2500);
    });
  });
  function buildLine(lines, pick) {
    if (!lineTd) return;
    if (!lineSel) {
      lineSel = document.createElement("select"); lineSel.className = "cb-sel";
      lineSel.addEventListener("change", function () { fill(); tell(); });
      lineTd.innerHTML = ""; lineTd.appendChild(lineSel);
    }
    var keep = pick != null ? String(pick) : lineSel.value;
    var html = '<option value="">Select line</option>', zone = null;
    lines.forEach(function (l) {
      var z = l.zone_name || "No zone";
      if (z !== zone) { html += (zone === null ? "" : "</optgroup>") + '<optgroup label="' + z.replace(/"/g, "&quot;") + '">'; zone = z; }
      html += '<option value="' + l.line_id + '">' + String(l.line_name || "").replace(/</g, "&lt;") + "</option>";
    });
    lineSel.innerHTML = html + (zone === null ? "" : "</optgroup>");
    lineSel.value = keep;
    if (lineSel.value !== keep) lineSel.value = "";
  }
  function buildMonth(ym) {
    if (!monthTd) return;
    if (!monthSel) {
      monthSel = document.createElement("select"); monthSel.className = "cb-sel";
      monthSel.addEventListener("change", tell);
      monthTd.innerHTML = ""; monthTd.appendChild(monthSel);
      var now = new Date(), y = now.getFullYear(), html = "";
      for (var yy = y; yy >= y - 1; yy--) for (var m = 11; m >= 0; m--)
        html += '<option value="' + yy + "-" + (m < 9 ? "0" : "") + (m + 1) + '">' + MONTHS[m] + " " + yy + "</option>";
      monthSel.innerHTML = html;
      monthSel.value = y + "-" + (now.getMonth() < 9 ? "0" : "") + (now.getMonth() + 1);
    }
    if (ym && /^\\d{4}-\\d{2}$/.test(ym)) monthSel.value = ym;
  }
  window.addEventListener("message", function (e) {
    if (e.source !== window.parent || !e.data || e.data.type !== "cb-data") return;
    data = Array.isArray(e.data.lines) ? e.data.lines : [];
    buildLine(data, e.data.line_id);
    buildMonth(e.data.month);
    fill();
    var sd = e.data.sheet, cur = lineSel ? lineSel.value : "", mon = monthSel ? monthSel.value : "";
    if (!cur) { fillDays(null); note.textContent = "Select a line to load MES data."; return; }
    if (e.data.loading) { note.textContent = "Loading MES data…"; return; }
    if (sd && String(sd.line_id) === cur && sd.month === mon) {
      var n = fillDays(sd);
      note.textContent = n ? ("MES data: " + n + " shift" + (n === 1 ? "" : "s") + " filled") : "No MES data for this month.";
    } else if (e.data.error) {
      fillDays(null); note.textContent = e.data.error;
    }
  });
  // Save PDF file name: "Track Record Sheet - <line> - <Mon YYYY>"
  window.cbPdfName = function () {
    var ln = lineSel && lineSel.value ? lineSel.options[lineSel.selectedIndex].text : "";
    var mo = monthSel ? monthSel.options[monthSel.selectedIndex].text : "";
    return ["Track Record Sheet", ln, mo].filter(Boolean).join(" - ");
  };
  // Clear keeps the line, month, cycle time and target (the original kept the two defaults).
  // MES values come back when the line or month is picked again.
  window.clearSheet = function () {
    if (!confirm("Clear all entered data? Line, month, cycle time and target stay.")) return;
    ins.forEach(function (el) { if (ctIn.indexOf(el) < 0 && tgIn.indexOf(el) < 0) el.value = ""; });
    if (window.drawShiftGraph) { drawShiftGraph("A"); drawShiftGraph("B"); }
  };
})();
</script>
"""
# 2026-11-06 (operator: "control board ke saare document me month aur line ke
# dropdown lagaa de agar unme hai"): the LINE and MONTH boxes of the 4M Change,
# Operator Status and Defect Recording sheets become dropdowns (4M: MONTH + YEAR).
# Same hand-off as the Track Record Sheet: the Control Board posts
# {type:"cb-data", lines, line_id, month, ...}; a pick inside the sheet posts
# "cb-select" back.  A sheet with MES data defines window.cbOnData(data, sel).
# __CFG__ = {"title": <file name>, "four": <4M layout>}.
SELECT_SCRIPT = """
<script>
/* MES Control Board: line / month dropdowns (lines posted in by the Control Board). */
(function () {
  var CFG = __CFG__;
  var MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  function norm(t) { return String(t || "").replace(/\\s+/g, " ").trim().toUpperCase(); }
  function field(re) {
    var tds = document.querySelectorAll("td");
    for (var i = 0; i < tds.length; i++) {
      if (!re.test(norm(tds[i].textContent))) continue;
      var nx = tds[i].nextElementSibling;
      return tds[i].querySelector("input") || (nx && nx.querySelector("input")) || null;
    }
    return null;
  }
  var f = {};
  if (CFG.four) {
    var hi = document.querySelectorAll("#tbody4M tr:nth-child(2) input.header-input");
    f = { line: hi[0], month: hi[1], year: hi[2] };
  } else {
    f = { line: field(/^LINE( NAME)? ?:-?$/), month: field(/^MONTH ?:-?$/) };
  }
  if (!f.line && !f.month) return;
  var css = document.createElement("style");
  css.textContent = "select.cb-sel{background:transparent;cursor:pointer;outline:none;padding:0 2px;box-sizing:border-box;" +
    "max-width:100%}select.cb-sel:focus{background:#fff9c4}";
  document.head.appendChild(css);
  // a dropdown in place of the text box: same font, as wide as the cell allows
  function toSelect(input) {
    if (!input) return null;
    var cs = getComputedStyle(input), td = input.parentNode, sel = document.createElement("select");
    var room = td.getBoundingClientRect().right - input.getBoundingClientRect().left - 6;
    sel.className = "cb-sel";
    sel.style.cssText = "font-family:" + cs.fontFamily + ";font-size:" + cs.fontSize + ";font-weight:" + cs.fontWeight +
      ";color:" + cs.color + ";width:" + Math.max(input.offsetWidth, Math.floor(room)) + "px;height:" +
      Math.max(22, input.offsetHeight) + "px;border:none;border-bottom:" + cs.borderBottomWidth + " " +
      cs.borderBottomStyle + " " + cs.borderBottomColor + ";text-align:" + cs.textAlign + ";text-align-last:" + cs.textAlign;
    td.replaceChild(sel, input);
    return sel;
  }
  var lineSel = toSelect(f.line), monthSel = toSelect(f.month), yearSel = toSelect(f.year);
  var now = new Date(), Y = now.getFullYear(), M = now.getMonth();
  function two(n) { return (n < 10 ? "0" : "") + n; }
  if (monthSel && yearSel) {
    monthSel.innerHTML = MONTHS.map(function (m, i) { return '<option value="' + two(i + 1) + '">' + m + "</option>"; }).join("");
    yearSel.innerHTML = [Y, Y - 1].map(function (y) { return '<option value="' + y + '">' + y + "</option>"; }).join("");
    monthSel.value = two(M + 1); yearSel.value = String(Y);
  } else if (monthSel) {
    var h = "";
    for (var yy = Y; yy >= Y - 1; yy--) for (var mm = 11; mm >= 0; mm--)
      h += '<option value="' + yy + "-" + two(mm + 1) + '">' + MONTHS[mm] + " " + yy + "</option>";
    monthSel.innerHTML = h; monthSel.value = Y + "-" + two(M + 1);
  }
  if (lineSel) lineSel.innerHTML = '<option value="">Select line</option>';
  function month() {
    if (monthSel && yearSel) return yearSel.value + "-" + monthSel.value;
    return monthSel ? monthSel.value : "";
  }
  function setMonth(ym) {
    if (!ym || !/^\\d{4}-\\d{2}$/.test(ym)) return;
    if (monthSel && yearSel) {
      if (![].some.call(yearSel.options, function (o) { return o.value === ym.slice(0, 4); }))
        yearSel.insertAdjacentHTML("beforeend", '<option value="' + ym.slice(0, 4) + '">' + ym.slice(0, 4) + "</option>");
      yearSel.value = ym.slice(0, 4); monthSel.value = ym.slice(5, 7);
    } else if (monthSel) {
      if (![].some.call(monthSel.options, function (o) { return o.value === ym; }))
        monthSel.insertAdjacentHTML("beforeend", '<option value="' + ym + '">' + MONTHS[+ym.slice(5, 7) - 1] + " " + ym.slice(0, 4) + "</option>");
      monthSel.value = ym;
    }
  }
  function sel() { return { line_id: lineSel ? lineSel.value : "", month: month() }; }
  function tell() { try { window.parent.postMessage({ type: "cb-select", line_id: sel().line_id, month: sel().month }, "*"); } catch (x) {} }
  [lineSel, monthSel, yearSel].forEach(function (s) { if (s) s.addEventListener("change", tell); });
  function buildLine(lines, pick) {
    if (!lineSel) return;
    var keep = pick != null ? String(pick) : lineSel.value, zone = null;
    var html = '<option value="">Select line</option>';
    lines.forEach(function (l) {
      var z = l.zone_name || "No zone";
      if (z !== zone) { html += (zone === null ? "" : "</optgroup>") + '<optgroup label="' + z.replace(/"/g, "&quot;") + '">'; zone = z; }
      html += '<option value="' + l.line_id + '">' + String(l.line_name || "").replace(/</g, "&lt;") + "</option>";
    });
    lineSel.innerHTML = html + (zone === null ? "" : "</optgroup>");
    lineSel.value = keep;
    if (lineSel.value !== keep) lineSel.value = "";
  }
  // status note in the toolbar (screen only, like the toolbar)
  var bar = document.querySelector(".toolbar > div"), note = document.createElement("span");
  note.style.cssText = "margin-left:14px;font-size:12px;font-weight:600;color:#475569";
  if (bar) bar.appendChild(note);
  window.cbNote = function (t) { note.textContent = t || ""; };
  window.addEventListener("message", function (e) {
    if (e.source !== window.parent || !e.data || e.data.type !== "cb-data") return;
    buildLine(Array.isArray(e.data.lines) ? e.data.lines : [], e.data.line_id);
    setMonth(e.data.month);
    if (typeof window.cbOnData === "function") window.cbOnData(e.data, sel());
  });
  // Save PDF file name: "<sheet> - <line> - <Mon YYYY>"
  window.cbPdfName = function () {
    var ln = lineSel && lineSel.value ? lineSel.options[lineSel.selectedIndex].text : "";
    var m = month(), mo = /^\\d{4}-\\d{2}$/.test(m) ? MONTHS[+m.slice(5, 7) - 1] + " " + m.slice(0, 4) : "";
    return [CFG.title, window.cbTitleExtra || "", ln, mo].filter(Boolean).join(" - ");
  };
})();
</script>
"""

# Operator Status (2026-11-06): rows and day marks from Shift Allocation
# (GET /api/control-board/operator-status, fetched by the Control Board and
# posted in as cb-data.sheet).  Two sheets per line and month, one per shift
# crew, shown one under the other on the Control Board (Sheet 1 / Sheet 2 — no
# shift written: a crew's shift changes every week).  The crew at the start of
# the month comes first, later changes are added below and the earlier operator
# stays; their mark for the day changes.  Marks = the sheet's legend only, for
# now ○ = on the line that day (operator: "apni taraf se add mat kar"; which
# legend mark applies when comes later).  The legend, help text and click cycle
# stay as drawn.  More operators than the 14 printed rows: rows are added (the
# sheet grows), back to 14 for a smaller crew.
OPS_SCRIPT = """
<script>
/* MES Control Board: Operator Status rows and marks from Shift Allocation. */
(function () {
  var table = document.getElementById("operatorTable");
  if (!table) return;
  var MIN_ROWS = 14, GREY = "#64748b";
  var COL = {};                           // marks keep the sheet's black; carried days grey
  // 2026-11-10 (operator: entries too small): PROCESS gets the room it needs
  // (115 -> 142 px), taken from S.NO. (65 -> 48) and EMP. CODE (95 -> 85).
  var cols = table.querySelectorAll("col");
  if (cols.length >= 5) { cols[0].style.width = "48px"; cols[1].style.width = "85px"; cols[3].style.width = "142px"; }
  // legend rows (bigger, black text in OPS_STYLE)
  [].forEach.call(table.querySelectorAll("td"), function (td) {
    var t = td.textContent.trim();
    if (t === "[ STATUS ]") {
      td.parentNode.classList.add("cb-legend");
      if (td.parentNode.nextElementSibling) td.parentNode.nextElementSibling.classList.add("cb-legend");
    }
    if (t === "( More than 3 days )") td.classList.add("cb-legend-note");
  });
  function rows() {
    return [].slice.call(table.rows).filter(function (r) { return r.querySelector('td.status-cell[onclick^="cycleStatus"]'); });
  }
  var start = rows();
  if (!start.length) return;
  var tmpl = start[0].cloneNode(true);
  var meas = document.createElement("canvas").getContext("2d");
  function textW(cs, size, text) { meas.font = cs.fontWeight + " " + size + "px " + cs.fontFamily; return meas.measureText(text).width; }
  // long names / processes: a smaller font on one line (down to 3 px below the
  // sheet's 13 px), else two lines in a text area (process names like "Lower
  // Rail Grease & Bar Coding M/c") at 12 px or less, never below 9 px.
  function fit(input, text) {
    input.style.fontSize = "";
    var cs = getComputedStyle(input),
        w = input.clientWidth - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0) - 2,
        base = parseFloat(cs.fontSize) || 14, size = base;
    while (size > base - 3 && textW(cs, size, text) > w) size -= 0.5;
    if (textW(cs, size, text) <= w) { input.style.fontSize = size < base ? size + "px" : ""; return; }
    size = Math.min(base - 1, 12);       // two lines must fit the 32 px row in the PDF too
    while (size > 9 && textW(cs, size, text) > 1.8 * w) size -= 0.5;
    var ta = document.createElement("textarea");
    ta.className = input.className + " cb-wrap"; ta.value = text; ta.title = text; ta.rows = 2;
    ta.style.cssText = "resize:none;overflow:hidden;white-space:normal;line-height:1.15;font-size:" + size +
      "px;padding:" + Math.max(1, Math.round((input.offsetHeight - 2.3 * size) / 2)) + "px 4px 0;height:" + input.offsetHeight + "px";
    input.parentNode.replaceChild(ta, input);
  }
  function setRows(n) {
    var rs = rows(), want = Math.max(MIN_ROWS, n);
    while (rs.length < want) { var last = rs[rs.length - 1]; last.parentNode.insertBefore(tmpl.cloneNode(true), last); rs = rows(); }
    while (rs.length > want) { var r = rs[rs.length - 2]; r.parentNode.removeChild(r); rs = rows(); }
    rs.forEach(function (r, i) { r.cells[0].textContent = String(i + 1); });
    return rs;
  }
  function clearRow(r) {
    [].slice.call(r.querySelectorAll("textarea.cb-wrap")).forEach(function (ta) {
      var el = document.createElement("input");
      el.type = "text"; el.className = ta.className.replace(/\\s*cb-wrap/, "");
      ta.parentNode.replaceChild(el, ta);
    });
    [].slice.call(r.querySelectorAll("input")).forEach(function (el) { el.value = ""; el.title = ""; el.style.fontSize = ""; });
    [].slice.call(r.querySelectorAll("td.status-cell")).forEach(function (td) { td.textContent = ""; td.title = ""; td.style.color = ""; });
  }
  function fill(sd) {
    var ops = (sd && sd.operators) || [], rs = setRows(ops.length);
    rs.forEach(function (r, i) {
      clearRow(r);
      var o = ops[i];
      if (!o) return;
      var c = r.cells, ins = [1, 2, 3, 4].map(function (k) { return c[k].querySelector("input"); });
      [o.code, o.name, o.process, ""].forEach(function (v, k) {   // START DAY left blank (operator)
        if (!ins[k]) return;
        ins[k].value = v || ""; ins[k].title = v || "";
        if (k === 1 || k === 2) fit(ins[k], v || "");
      });
      Object.keys(o.days || {}).forEach(function (k) {
        var v = o.days[k], td = c[4 + Number(k)];
        if (!td || !v) return;
        td.textContent = v[0]; td.title = v[1] || "";
        td.style.color = v[2] ? GREY : (COL[v[0]] || "#000");
      });
    });
    return ops.length;
  }
  window.cbOnData = function (d, sel) {
    var sd = d.sheet;
    if (!sel.line_id) { fill(null); window.cbNote("Select a line to load MES data."); return; }
    if (d.loading) { window.cbNote("Loading MES data\\u2026"); return; }
    window.cbTitleExtra = d.sheet_no ? "Sheet " + d.sheet_no : "";
    if (sd && String(sd.line_id) === sel.line_id && sd.month === sel.month) {
      var n = fill(sd), grey = [].some.call(table.querySelectorAll("td.status-cell"), function (td) { return td.style.color && td.textContent && td.style.color !== "rgb(0, 0, 0)"; });
      window.cbNote(n ? "MES data: " + n + " operator" + (n === 1 ? "" : "s") + " from Shift Allocation" +
                        (grey ? " \u00b7 grey \u25cb = allocation not saved that day, crew of the same week" : "")
                      : "No crew for this sheet in Shift Allocation this month.");
    } else if (d.error) {
      fill(null); window.cbNote(d.error);
    }
  };
  // Clear keeps the line and month; MES rows come back when either is picked again
  window.resetSheet = function () {
    if (!confirm("Clear all entries and status marks? Line and month stay.")) return;
    rows().forEach(clearRow);
    [].slice.call(table.querySelectorAll("input.cell-input")).forEach(function (el) { el.value = ""; el.title = ""; el.style.fontSize = ""; });
    [].slice.call(table.querySelectorAll(".status-cell")).forEach(function (td) { td.textContent = ""; td.title = ""; td.style.color = "#000"; });
    setRows(0);
  };
})();
</script>
"""
# 2026-11-10 — 4M Change Control Sheet SAVED (operator spec, 10-Nov):
#   * every RED circle (A / B block, any row, any day) = one row of "4M CHANGE
#     DETAILS": Date "DD-MM-YYYY (A)", date order, A before B, as many rows as
#     red circles (the 12 printed rows grow when needed);
#   * everything in CAPITALS; Man / Machine / Method / Material / Unexpected
#     Change Details = ONE column per row (typing in another clears the first);
#   * Name of Supervisor (Shift Allocation leader) and OK / NG Qty (at shift
#     end) come from MES, read-only;
#   * Product Details, Action Taken, Quality Result and Status are compulsory:
#     empty ones are tinted, and a darker tint + red date once the shift ended
#     (= pending: alarm + Shift Compile will not close that shift).
# Data: the Control Board posts cb-data {m4:true, sheet4m:<GET /api/control-board/4m>};
# every circle click / cell edit is posted back as "cb-4m-save" and the
# Control Board saves it with the user's login ("cb-4m-ack" comes back).
# Opened on its own (no Control Board) the sheet behaves as drawn.
FOUR_STYLE = """
<style>
/* MES Control Board: 4M Change Control Sheet (saved) */
#main4MSheet .circle-badge.cb-off { visibility: hidden; pointer-events: none; }
#main4MSheet textarea.cb-ta { height: 34px; min-height: 0; resize: none; overflow: hidden; line-height: 1.15;
  padding: 2px 4px; white-space: pre-wrap; word-break: break-word; color: #000; }
#main4MSheet tr.cb-4m-row .cell-input { text-transform: uppercase; color: #000; }
#main4MSheet input.cb-auto { font-weight: 600; cursor: default; }
#main4MSheet input.cb-auto:focus { background: transparent; }
#main4MSheet tr.cb-4m-row td.cb-miss { background: #fff1f2; }
#main4MSheet tr.cb-4m-row.cb-pend td.cb-miss { background: #fecaca; }
#main4MSheet tr.cb-4m-fill .status-square { cursor: default; }
#main4MSheet tr.cb-4m-fill .cell-input:focus { background: transparent; }
#main4MSheet td.cb-dwrap { padding: 0; border-left: none; border-right: none; position: relative; }
#main4MSheet table.cb-days { position: absolute; top: 0; left: 0; width: 100%; height: 100%; table-layout: fixed;
  border-collapse: collapse; border: none; background: transparent; }
#main4MSheet table.cb-days td { border-top: none !important; border-bottom: none !important; border-right: none !important;
  border-left: 1px solid #000 !important; }
#main4MSheet table.cb-days td:first-child { border-left: none !important; }
#main4MSheet table.cb-days td.cb-dhide { display: none; }
#main4MSheet table.cb-days td.cb-dhead { cursor: pointer; }
#main4MSheet table.cb-days td.cb-dhead:hover { filter: brightness(.93); }
#main4MSheet table.cb-days td.cb-dsel.cb-screen { background: #1e40af !important; color: #fff; }
#main4MSheet table.cb-days td.cb-today.cb-screen { background: #fff7cc !important; }
#main4MSheet .circle-badge.cb-lock { cursor: not-allowed; }
#main4MSheet .circle-badge.cb-lock:hover { transform: none; }
#main4MSheet tr.cb-4m-row.cb-fhide.cb-screen { display: none; }
#main4MSheet tr.cb-4m-row.cb-ro .cell-input { cursor: default; }
#main4MSheet tr.cb-4m-row.cb-ro .cell-input:focus { background: transparent; }
#main4MSheet tr.cb-4m-row.cb-ro .status-square { cursor: default; }
#main4MSheet tr.cb-4m-row td.cb-qopen { background: #fffbe6; }
#main4MSheet tr.cb-4m-row td.cb-qopen .cell-input { cursor: text; }
#main4MSheet tr.cb-4m-row td.cb-qopen .cell-input:focus { background: #fff9c4; }
/* 2026-11-13: rows filled from a breakdown slip (TOOL BREAKAGE / M/C B/D) */
#main4MSheet tr.cb-4m-row td.cb-slip.cb-screen { background: #fff7ed; }
#main4MSheet tr.cb-4m-row td.cb-slip.cb-screen .cell-input { cursor: pointer; color: #000; }
#main4MSheet tr.cb-4m-row td.cb-slip.cb-screen .cell-input:focus { background: transparent; }
</style>
"""
FOUR_SCRIPT = """
<script>
/* MES Control Board: 4M Change Control Sheet saved — red circles -> detail rows. */
(function () {
  var tbody = document.getElementById("tbody4M");
  if (!tbody) return;
  var PMAP = { "MAN": "MAN", "MATERIAL": "MATERIAL", "MACHINE": "MACHINE", "METHOD": "METHOD",
               "TOOL BREAKAGE / M/C B/D": "TOOL_BREAKAGE", "POWER FAILURE": "POWER_FAILURE",
               "OPERATOR ON UNPLANNED LEAVE": "UNPLANNED_LEAVE" };
  var CHG = ["MAN", "MACHINE", "METHOD", "MATERIAL", "UNEXPECTED"];       // detail cells 1..5
  var TXT = { 6: "product", 7: "action_taken", 8: "quality_result", 12: "manager" };
  var QTY = { 9: "ok_qty", 10: "ng_qty" }, QF = { ok_qty: 9, ng_qty: 10 };
  var REQ = { product: 6, action_taken: 7, quality_result: 8, status: 13 };
  function norm(t) { return String(t || "").replace(/\\s+/g, " ").trim().toUpperCase(); }
  function two(n) { return (n < 10 ? "0" : "") + n; }
  function note(t) { if (window.cbNote) window.cbNote(t); }

  // 2026-11-10 (operator): the day columns of the circle grid follow the
  // month — a 28 / 30-day month spreads its days over the full width instead of
  // leaving 29-31 blank.  The details table below shares the sheet's column grid
  // (Manager / Status sit on the day 29-31 columns), so the grid stays: each
  // shift-header / circle row gets its 31 day cells moved into one inner table
  // (colspan 31) whose visible cells share the width equally.
  var dayRows = [], headRows = [];
  function wrapDays(tr, isHead) {
    var cells = [].slice.call(tr.cells, 1);
    if (cells.length !== 31) return;
    var wrap = document.createElement("td");
    wrap.colSpan = 31;
    wrap.className = "cb-dwrap";
    ["bt-m", "bb-m", "bg-ashift", "bg-bshift"].forEach(function (c) { if (cells[0].classList.contains(c)) wrap.classList.add(c); });
    var t = document.createElement("table"), r = t.insertRow();
    t.className = "cb-days";
    cells.forEach(function (td) { r.appendChild(td); });
    wrap.appendChild(t);
    tr.appendChild(wrap);
    dayRows.push(cells);
    if (isHead) {
      headRows.push(cells);
      cells.forEach(function (td, i) {
        td.classList.add("cb-dhead");
        td.title = "Show only this date in 4M CHANGE DETAILS";
        td.addEventListener("click", function () { setFilter(i + 1); });
      });
    }
  }
  var lastDays = 31;
  function layoutDays(n) {
    if (!(n >= 28 && n <= 31) || n === lastDays) return;
    lastDays = n;
    dayRows.forEach(function (cells) { cells.forEach(function (td, i) { td.classList.toggle("cb-dhide", i >= n); }); });
  }
  // circles: shift block, row and day of each
  var circles = [], shift = null;
  [].slice.call(tbody.rows).forEach(function (tr) {
    var c0 = tr.cells[0], t = norm(c0 && c0.textContent);
    if (t === "A SHIFT" || t === "B SHIFT") { shift = t.charAt(0); wrapDays(tr, true); return; }
    var p = c0 && c0.classList.contains("param-col") ? PMAP[t] : null;
    if (!p || !shift) return;
    [].forEach.call(tr.querySelectorAll(".circle-badge"), function (el, i) {
      el.setAttribute("data-shift", shift); el.setAttribute("data-param", p); el.setAttribute("data-day", String(i + 1));
      circles.push(el);
    });
    wrapDays(tr);
  });
  // detail rows: the printed rows between the column header and LEGENDS
  var head = null, legend = null;
  [].forEach.call(tbody.rows, function (tr) {
    var t = norm(tr.textContent);
    if (!head && t.indexOf("MAN CHANGE DETAILS") >= 0) head = tr;
    else if (head && !legend && t.indexOf("LEGENDS") === 0) legend = tr;
  });
  if (!head || !legend || !circles.length) return;
  var printed = [];
  for (var r0 = head.nextElementSibling; r0 && r0 !== legend; r0 = r0.nextElementSibling) printed.push(r0);
  if (!printed.length) return;
  var MIN_ROWS = printed.length;
  var tpl = printed[0].cloneNode(true);
  [].forEach.call(tpl.cells, function (td) { td.classList.remove("bb-m"); });

  var on = false, cur = { line_id: "", month: "", days: 31, key: "", current: null };
  // 2026-11-10 (operator): a click on a date in the A / B SHIFT row shows only
  // that date's rows below (both shifts); "Clear filter" or a second click shows all
  var filterDay = 0;
  var fbar = document.querySelector(".toolbar > div:last-child"), flab = null, fbtn = null;
  if (fbar) {
    flab = document.createElement("span");
    flab.style.cssText = "font-size:12px;font-weight:600;color:#1e40af;margin-right:8px;display:none";
    fbtn = document.createElement("button");
    fbtn.type = "button"; fbtn.textContent = "Clear filter"; fbtn.disabled = true;
    fbtn.style.display = "none";
    fbtn.addEventListener("click", function () { setFilter(0); });
    fbar.insertBefore(fbtn, fbar.firstChild);
    fbar.insertBefore(flab, fbtn);
  }
  function setFilter(d) {
    if (!on) return;
    filterDay = d && d !== filterDay && d <= cur.days ? d : 0;
    applyFilter();
  }
  function applyFilter() {
    if (filterDay > cur.days) filterDay = 0;
    headRows.forEach(function (cells) {
      cells.forEach(function (td, i) { td.classList.toggle("cb-dsel", i + 1 === filterDay); td.classList.add("cb-screen"); });
    });
    var shown = 0;
    rows.forEach(function (r) {
      var hide = filterDay && +String(r.d.date).slice(8, 10) !== filterDay;
      r.tr.classList.toggle("cb-fhide", !!hide); r.tr.classList.add("cb-screen");
      if (!hide) shown++;
    });
    if (flab) {
      flab.style.display = filterDay ? "" : "none";
      flab.textContent = filterDay ? "Showing " + two(filterDay) + "-" + cur.month.slice(5, 7) + "-" + cur.month.slice(0, 4)
        + ": " + (shown ? shown + " row(s)" : "no 4M change") : "";
    }
    if (fbtn) { fbtn.disabled = !filterDay; fbtn.style.display = on ? "" : "none"; }
  }
  var rows = [], pendingMarks = {}, markFrom = {}, markSending = {}, waiting = {}, seq = 0;

  function daysIn(m) { return /^\\d{4}-\\d{2}$/.test(m || "") ? new Date(+m.slice(0, 4), +m.slice(5, 7), 0).getDate() : 31; }
  function ckey(el) { return el.getAttribute("data-shift") + "|" + el.getAttribute("data-param") + "|" + el.getAttribute("data-day"); }
  function post(msg, local) {
    msg.type = "cb-4m-save"; msg.seq = ++seq; msg.line_id = cur.line_id;
    local.kind = msg.kind; waiting[msg.seq] = local;
    try { window.parent.postMessage(msg, "*"); } catch (x) {}
    note("Saving…");
  }

  // ── circles ───────────────────────────────────────────────────────────
  function paintCircles(marks) {
    layoutDays(cur.days);
    var c = cur.current, cday = c && String(c.date).slice(0, 7) === cur.month ? +String(c.date).slice(8, 10) : 0;
    circles.forEach(function (el) {
      var d = +el.getAttribute("data-day");
      var live = on && !!cday && d === cday && el.getAttribute("data-shift") === c.shift;
      el.classList.toggle("cb-lock", on && !live && !cur.admin);         // admin: any circle
      var td = el.closest("td");
      if (td) { td.classList.toggle("cb-today", live); td.classList.add("cb-screen"); }
      el.classList.toggle("cb-off", d > cur.days);
      var k = ckey(el);
      if (pendingMarks.hasOwnProperty(k)) return;            // a click still being saved
      var m = marks && marks[el.getAttribute("data-shift")];
      var st = (d <= cur.days && m && m[el.getAttribute("data-param")] && m[el.getAttribute("data-param")][String(d)]) || "blank";
      el.setAttribute("data-state", st);
    });
  }
  // one save per circle at a time: quick clicks (green -> red) send the latest state after the ack
  function sendMark(el) {
    var k = ckey(el);
    if (markSending.hasOwnProperty(k)) return;
    var st = pendingMarks[k];
    markSending[k] = st;
    post({ kind: "mark", date: cur.month + "-" + two(+el.getAttribute("data-day")),
           shift: el.getAttribute("data-shift"), param: el.getAttribute("data-param"), state: st },
         { ckey: k, el: el, state: st });
  }
  var origCircle = window.toggleCircle;
  window.toggleCircle = function (el) {
    if (!on && cur.key) return;                         // MES sheet still loading
    if (!on) return origCircle(el);
    if (!cur.line_id) { note("Select a line first"); return; }
    if (el.classList.contains("cb-off")) return;
    if (el.classList.contains("cb-lock")) {
      note(cur.current ? "Only the running shift can be marked: " + cur.current.label
                       : "No shift is running now: circles can be marked during the shift only");
      return;
    }
    var k = ckey(el);
    if (!pendingMarks.hasOwnProperty(k)) markFrom[k] = el.getAttribute("data-state");
    origCircle(el);
    pendingMarks[k] = el.getAttribute("data-state");
    sendMark(el);
  };

  // ── detail rows ───────────────────────────────────────────────────────
  function newRow() {
    var tr = tpl.cloneNode(true), c = tr.cells;
    [1, 2, 3, 4, 5, 6, 7, 8, 12].forEach(function (i) {
      var inp = c[i] && c[i].querySelector("input");
      if (!inp) return;
      var ta = document.createElement("textarea");
      ta.className = "cell-input left-input cb-ta"; ta.rows = 2; ta.spellcheck = false;
      c[i].replaceChild(ta, inp);
    });
    [0, 11].forEach(function (i) {
      var inp = c[i] && c[i].querySelector("input");
      if (inp) { inp.readOnly = true; inp.tabIndex = -1; inp.classList.add("cb-auto"); }
    });
    // 2026-11-12 (operator): OK / NG Qty typed by the user (the parts checked
    // after the change), open from 15 min before to 15 min after the shift end
    [9, 10].forEach(function (i) {
      var inp = c[i] && c[i].querySelector("input");
      if (inp) { inp.readOnly = true; inp.tabIndex = -1; inp.inputMode = "numeric"; inp.classList.add("cb-qty"); }
    });
    c[0].classList.add("cb-date");
    return tr;
  }
  function field(tr, i) { return tr.cells[i] && tr.cells[i].querySelector("textarea, input"); }
  // long text: smaller font first (down to 9 px), then the row grows — nothing is cut off
  function fit(ta) {
    if (!ta || ta.tagName !== "TEXTAREA") return;
    ta.style.fontSize = ""; ta.style.height = "";
    var base = parseFloat(getComputedStyle(ta).fontSize) || 12, s = base;
    while (s > 9 && ta.scrollHeight > ta.clientHeight + 1) { s -= 0.5; ta.style.fontSize = s + "px"; }
    if (ta.scrollHeight > ta.clientHeight + 1) ta.style.height = (ta.scrollHeight + 2) + "px";
  }
  function upper(el) {
    var a = el.selectionStart, b = el.selectionEnd, v = el.value.toUpperCase();
    if (v !== el.value) { el.value = v; try { el.setSelectionRange(a, b); } catch (x) {} }
  }
  function statusOf(tr) {
    var sq = tr.cells[13] && tr.cells[13].querySelector(".status-square");
    var v = sq ? sq.getAttribute("data-state") : "";
    return v === "returned" ? "RETURNED" : v === "not-returned" ? "NOT_RETURNED" : "";
  }
  function markMissing(row) {
    var miss = 0;
    Object.keys(REQ).forEach(function (f) {
      var i = REQ[f], v = f === "status" ? statusOf(row.tr) : (field(row.tr, i) || {}).value;
      var empty = !String(v || "").trim();
      row.tr.cells[i].classList.toggle("cb-miss", empty);
      if (empty) miss++;
    });
    row.tr.classList.toggle("cb-pend", !!(row.d.shift_ended && miss));
    return miss;
  }
  function setAuto(row) {
    var d = row.d, c = row.tr;
    field(c, 0).value = d.label || "";
    field(c, 11).value = d.supervisor || "";
    field(c, 11).title = d.supervisor ? "" : "Line leader from Shift Allocation (not saved yet)";
  }
  function hm(t) { return String(t || "").slice(11, 16); }
  function qtyTip(d) {
    if (!d.qty_from) return "OK / NG Qty: no shift timing for this line";
    return d.qty_open ? "Type the OK / NG parts checked after the change (open until " + hm(d.qty_to) + ")"
      : "OK / NG Qty can be filled from " + hm(d.qty_from) + " to " + hm(d.qty_to) +
        " only (15 min before to 15 min after the shift end)";
  }
  function setEditable(row, i, v) {          // MES value into a cell the user is not editing
    var el = field(row.tr, i);
    if (!el || el === document.activeElement) return;
    el.value = v || ""; fit(el);
  }
  function fillRow(row, keepDirty) {
    var d = row.d;
    setAuto(row);
    row.tr.classList.toggle("cb-ro", !d.editable);
    [].forEach.call(row.tr.querySelectorAll("textarea"), function (ta) { ta.readOnly = !d.editable; ta.tabIndex = d.editable ? 0 : -1; });
    // 2026-11-13: a breakdown-slip row — the change text comes from the slip
    // (read-only; a click opens the slip), Product / Action Taken start from it
    var sl = d.slip || null;
    for (var ci = 1; ci <= 5; ci++) {
      var cel = row.tr.cells[ci], cta = field(row.tr, ci);
      if (!cel) continue;
      cel.classList.toggle("cb-slip", !!sl && ci === 5); cel.classList.add("cb-screen");
      if (sl && cta) { cta.readOnly = true; cta.tabIndex = -1; }
    }
    if (row.tr.cells[5]) row.tr.cells[5].title = sl ? "Breakdown slip " + sl.no + (sl.stage ? " (" + stageText(sl.stage) + ")" : "")
                                                         + ": click to open the slip" : "";
    [6, 7].forEach(function (i) {
      var au = !!(d.auto && d.auto[TXT[i]]), cel = row.tr.cells[i];
      if (!cel) return;
      cel.classList.toggle("cb-autov", au); cel.classList.add("cb-screen");
      cel.title = au ? "From the breakdown slip: cannot be changed" : "";
      // 2026-11-13 (operator): data that comes automatically is frozen
      var ta6 = field(row.tr, i);
      if (au && ta6) { ta6.readOnly = true; ta6.tabIndex = -1; }
    });
    if (!(keepDirty && row.dirty.change))
      CHG.forEach(function (col, j) { setEditable(row, j + 1, d.change_col === col ? d.change_text : ""); });
    Object.keys(TXT).forEach(function (i) { if (!(keepDirty && row.dirty[TXT[i]])) setEditable(row, +i, d[TXT[i]]); });
    var qo = !!d.qty_open;
    Object.keys(QTY).forEach(function (i) {
      var el = field(row.tr, +i);
      if (!el) return;
      el.readOnly = !qo; el.tabIndex = qo ? 0 : -1; el.title = qtyTip(d);
      row.tr.cells[+i].classList.toggle("cb-qopen", qo);
      if (!(keepDirty && row.dirty[QTY[i]])) setEditable(row, +i, d[QTY[i]] == null ? "" : String(d[QTY[i]]));
    });
    if (!(keepDirty && row.dirty.status)) {
      var sq = row.tr.cells[13].querySelector(".status-square");
      if (sq) sq.setAttribute("data-state", d.status === "RETURNED" ? "returned" : d.status === "NOT_RETURNED" ? "not-returned" : "blank");
    }
    markMissing(row);
  }
  function rowOf(tr) { for (var i = 0; i < rows.length; i++) if (rows[i].tr === tr) return rows[i]; return null; }
  function stageText(st) {
    return st === "PENDING_PRODUCTION" ? "production half not filled yet"
         : st === "PENDING_MAINTENANCE" ? "maintenance half not filled yet"
         : st === "COMPLETED" ? "completed" : String(st || "").toLowerCase();
  }
  function save(row, now) {
    clearTimeout(row.timer);
    var go = function () {
      if (row.inflight) return;                          // the ack sends what is still dirty
      var fields = {}, vers = {}, any = false;
      // OK / NG Qty are sent on their own (own time window): a refused Qty never holds back the text
      var qtyFirst = Object.keys(row.dirty).some(function (f) { return row.dirty[f] && QF[f]; });
      Object.keys(row.dirty).forEach(function (f) {
        if (!row.dirty[f] || !!QF[f] !== qtyFirst) return;
        any = true; vers[f] = row.ver[f];
        if (f === "change") {
          var col = null, text = "";
          CHG.forEach(function (c, j) { var v = field(row.tr, j + 1).value.trim(); if (v && !col) { col = c; text = v; } });
          fields.change = { col: col, text: text };
        } else if (f === "status") fields.status = statusOf(row.tr);
        else if (QF[f]) fields[f] = field(row.tr, QF[f]).value.trim();
        else { for (var i in TXT) if (TXT[i] === f) fields[f] = field(row.tr, +i).value.trim(); }
      });
      if (!any) return;
      row.inflight = true;
      post({ kind: "row", date: row.d.date, shift: row.d.shift, param: row.d.param,
             slip_key: row.d.slip_key || "", fields: fields },
           { row: row, vers: vers, qty: qtyFirst, vals: fields });
    };
    if (now) go(); else row.timer = setTimeout(go, 700);
  }
  function edit(row, f, now) {
    row.ver[f] = (row.ver[f] || 0) + 1; row.dirty[f] = true;
    markMissing(row); save(row, now);
  }
  function bind(tr, d) {
    var row = { d: d, tr: tr, ver: {}, dirty: {}, timer: null };
    tr.classList.add("cb-4m-row");
    CHG.forEach(function (col, j) {
      var ta = field(tr, j + 1);
      ta.addEventListener("input", function () {
        upper(ta);
        if (ta.value.trim()) CHG.forEach(function (c2, k) {                // one change column per row
          var o = field(tr, k + 1); if (k !== j && o.value) { o.value = ""; fit(o); }
        });
        fit(ta); edit(row, "change");
      });
      ta.addEventListener("blur", function () { if (row.dirty.change) save(row, true); });
    });
    Object.keys(TXT).forEach(function (i) {
      var ta = field(tr, +i), f = TXT[i];
      ta.addEventListener("input", function () { upper(ta); fit(ta); edit(row, f); });
      ta.addEventListener("blur", function () { if (row.dirty[f]) save(row, true); });
    });
    // 2026-11-13: say why a box cannot be typed in
    [].forEach.call(tr.querySelectorAll("textarea"), function (ta) {
      ta.addEventListener("click", function () {
        if (!ta.readOnly || !row.d) return;
        var ci = [].indexOf.call(tr.cells, ta.closest("td"));
        if (row.d.auto && row.d.auto[TXT[ci]]) note("From the breakdown slip: it cannot be changed");
        else if (!row.d.editable) note("Only today's entries can be changed: earlier days are locked");
      });
    });
    Object.keys(QTY).forEach(function (i) {
      var el = field(tr, +i), f = QTY[i];
      if (!el) return;
      el.addEventListener("input", function () {
        var v = el.value.replace(/[^0-9]/g, "").slice(0, 5);
        if (v !== el.value) el.value = v;
        edit(row, f);
      });
      el.addEventListener("blur", function () { if (row.dirty[f]) save(row, true); });
      el.addEventListener("click", function () { if (el.readOnly) note(qtyTip(row.d)); });
    });
    var slipCell = field(tr, 5);
    if (slipCell) slipCell.addEventListener("click", function () {
      var sl = row.d && row.d.slip;
      if (!sl) return;
      try { window.parent.postMessage({ type: "cb-slip-open", src: sl.src, id: sl.id, stage: sl.stage || "" }, "*"); } catch (x) {}
    });
    rows.push(row);
    fillRow(row, false);
    return row;
  }
  function filler(tr) {
    tr.classList.add("cb-4m-fill");
    [].forEach.call(tr.querySelectorAll("textarea, input"), function (el) { el.readOnly = true; el.tabIndex = -1; });
  }
  var origSq = window.toggleStatusSquare;
  window.toggleStatusSquare = function (el) {
    if (!on) return origSq(el);
    var row = rowOf(el.closest("tr"));
    if (!row) return;                                   // rows come from red circles only
    if (!row.d.editable) { note("Only today's entries can be changed: earlier days are locked"); return; }
    origSq(el);
    edit(row, "status", true);
  };
  function focusInfo() {
    var a = document.activeElement, tr = a && a.closest && a.closest("tr"), row = tr && rowOf(tr);
    if (!row) return null;
    return { key: row.d.key, cell: [].indexOf.call(tr.cells, a.closest("td")),
             s: a.selectionStart, e: a.selectionEnd };
  }
  function renderRows(list, force) {
    list = list || [];
    var same = !force && rows.length === list.length && rows.every(function (r, i) { return r.d.key === list[i].key; });
    if (same) {
      rows.forEach(function (r, i) { r.d = list[i]; fillRow(r, true); });
      applyFilter();
      return;
    }
    var old = {}, fi = focusInfo();
    rows.forEach(function (r) { old[r.d.key] = r; });
    var cur_rows = [];
    for (var x = head.nextElementSibling; x && x !== legend; x = x.nextElementSibling) cur_rows.push(x);
    cur_rows.forEach(function (x) { x.parentNode.removeChild(x); });
    rows = [];
    var n = Math.max(MIN_ROWS, list.length + 1), made = [];   // always one free row below
    for (var i = 0; i < n; i++) {
      var tr = newRow();
      tbody.insertBefore(tr, legend);
      made.push(tr);
      if (i < list.length) {
        var row = bind(tr, list[i]), was = old[list[i].key];
        if (was) {                                         // keep edits not saved yet
          Object.keys(was.dirty).forEach(function (f) {
            if (!was.dirty[f]) return;
            row.dirty[f] = true; row.ver[f] = was.ver[f];
            if (f === "change") CHG.forEach(function (c, j) { field(tr, j + 1).value = field(was.tr, j + 1).value; fit(field(tr, j + 1)); });
            else if (f === "status") tr.cells[13].querySelector(".status-square").setAttribute("data-state",
              was.tr.cells[13].querySelector(".status-square").getAttribute("data-state"));
            else if (QF[f]) field(tr, QF[f]).value = field(was.tr, QF[f]).value;
            else for (var k in TXT) if (TXT[k] === f) { field(tr, +k).value = field(was.tr, +k).value; fit(field(tr, +k)); }
          });
          if (Object.keys(row.dirty).length) save(row, false);
          markMissing(row);
        }
      } else filler(tr);
    }
    [].forEach.call(made[made.length - 1].cells, function (td) { td.classList.add("bb-m"); });
    applyFilter();
    if (fi) rows.forEach(function (r) {
      if (r.d.key !== fi.key) return;
      var el = r.tr.cells[fi.cell] && r.tr.cells[fi.cell].querySelector("textarea");
      if (el) { el.focus(); try { el.setSelectionRange(fi.s, fi.e); } catch (y) {} }
    });
  }

  // ── MES hand-off ─────────────────────────────────────────────────────
  window.addEventListener("message", function (e) {
    if (e.source !== window.parent || !e.data || e.data.type !== "cb-4m-ack") return;
    var w = waiting[e.data.seq];
    delete waiting[e.data.seq];
    if (!w) return;
    if (w.kind === "mark") {
      delete markSending[w.ckey];
      if (!e.data.ok) {                                  // back to the last saved state
        w.el.setAttribute("data-state", markFrom[w.ckey] || "blank");
        delete pendingMarks[w.ckey]; delete markFrom[w.ckey];
      } else if (pendingMarks[w.ckey] !== w.state) sendMark(w.el);
      else { delete pendingMarks[w.ckey]; delete markFrom[w.ckey]; }
    } else {
      w.row.inflight = false;
      if (e.data.ok) Object.keys(w.vers).forEach(function (f) {
        if (w.qty) w.row.d[f] = w.vals[f] === "" ? null : +w.vals[f];
        if (w.row.ver[f] === w.vers[f]) w.row.dirty[f] = false;
      });
      else if (w.qty) Object.keys(w.vers).forEach(function (f) {      // refused Qty: back to the saved value
        if (w.row.ver[f] !== w.vers[f]) return;
        w.row.dirty[f] = false;
        var el = field(w.row.tr, QF[f]);
        if (el) el.value = w.row.d[f] == null ? "" : String(w.row.d[f]);
      });
      if ((e.data.ok || w.qty) && Object.keys(w.row.dirty).some(function (f) { return w.row.dirty[f]; })) save(w.row, true);
    }
    if (!e.data.ok) { note("Not saved: " + (e.data.error || "error")); return; }
    if (!Object.keys(waiting).length) {
      var t = new Date();
      note("Saved " + two(t.getHours()) + ":" + two(t.getMinutes()) + summary());
    }
  });
  function pendingNow() {           // ended shifts still missing a compulsory field (as on screen)
    return rows.filter(function (r) { return r.d.shift_ended && markMissing(r) > 0; }).length;
  }
  function summary() { var n = pendingNow(); return n ? " · " + n + " 4M change(s) pending" : ""; }
  window.cbOnData = function (data, s) {
    if (!data || !data.m4) return;
    var key = (s.line_id || "") + "|" + (s.month || "");
    var sh = data.sheet4m;
    if (!s.line_id) {
      on = true; cur = { line_id: "", month: s.month, days: daysIn(s.month), key: key, current: null };
      pendingMarks = {}; filterDay = 0; paintCircles(null); renderRows([], true); note("Select a line");
      return;
    }
    if (!sh || String(sh.line_id) !== String(s.line_id) || sh.month !== s.month) {
      if (key !== cur.key) {                              // another line / month: blank until it loads
        on = false; cur = { line_id: s.line_id, month: s.month, days: daysIn(s.month), key: key };
        pendingMarks = {}; paintCircles(null); renderRows([], true);
      }
      note(data.error || (data.loading ? "Loading…" : ""));
      return;
    }
    var switched = key !== cur.key || !on;
    on = true;
    cur = { line_id: s.line_id, month: s.month, days: sh.days || daysIn(s.month), key: key, current: sh.current || null,
            admin: !!sh.admin };
    if (switched) { pendingMarks = {}; filterDay = 0; }
    paintCircles(sh.marks);
    renderRows(sh.rows, switched);
    var n = pendingNow();
    if (!Object.keys(waiting).length)
      note(n ? n + " 4M change(s) pending: fill Product Details, Action Taken, Quality Result and Status"
             : (data.error || ""));
  };
})();
</script>
"""
# 2026-11-10 (operator): the Operator Visualization Sheet is FILLED BY THE USER
# and SAVED — no auto-fill from Shift Allocation any more (OPS_SCRIPT above is
# kept for reference, not installed).  Same limits as the 4M sheet: a day
# column only for the running shift's date (a still-pending day of an ended
# shift may be filled late while an operator-sheet rule is ON), the weekly
# manager sign only for this week, a text box filled in an earlier shift stays
# locked; a new row once the last row is used.  Data = cb-data {ops:true,
# sheet_no, sheetOps: GET /api/control-board/ops}; every change is posted as
# "cb-ops-save" {key, value} and saved by the Control Board ("cb-ops-ack").
OPS2_STYLE = """
<style>
/* MES Control Board: Operator sheet (saved) */
#operatorTable td.cb-lock { cursor: not-allowed; }
#operatorTable .cell-input[readonly] { cursor: default; }
#operatorTable .cell-input[readonly]:focus { background: transparent; }
#operatorTable td.cb-today.cb-screen { background: #fff7cc !important; }
#operatorTable td.cb-openday.cb-screen { background: #ffedd5 !important; }
#operatorTable .cb-dhide { display: none; }
#operatorTable td.status-cell[onclick^="cycleYN"] { font-size: 20px; line-height: 1; }
#operatorTable td.status-cell[onclick^="cycleYN"].cb-sq { font-size: 26px; font-weight: normal; -webkit-text-stroke: 0.6px currentColor; }
#operatorTable tr.cb-m4row td.status-cell { font-size: 13px; font-weight: bold; }
#operatorTable tr.cb-m4row td.status-cell.cb-dash { font-size: 18px; cursor: default; }
#operatorTable img.cb-sign { display: block; max-width: 100%; max-height: 26px; margin: 0 auto; object-fit: contain; }
#operatorTable input[data-cb-sign] { cursor: default; text-align: center; }
</style>
"""
OPS2_SCRIPT = """
<script>
/* MES Control Board: Operator Visualization Sheet - filled by the user, saved. */
(function () {
  var table = document.getElementById("operatorTable");
  if (!table) return;
  var MIN_ROWS = 14;
  // PROCESS gets the room it needs (115 -> 142 px) from S.NO. and EMP. CODE
  var cols = table.querySelectorAll("col");
  if (cols.length >= 5) { cols[0].style.width = "48px"; cols[1].style.width = "85px"; cols[3].style.width = "142px"; }
  [].forEach.call(table.querySelectorAll("td"), function (td) {
    var t = td.textContent.trim();
    if (t === "[ STATUS ]") {
      td.parentNode.classList.add("cb-legend");
      if (td.parentNode.nextElementSibling) td.parentNode.nextElementSibling.classList.add("cb-legend");
    }
    if (t === "( More than 3 days )") td.classList.add("cb-legend-note");
  });
  function two(n) { return (n < 10 ? "0" : "") + n; }
  function note(t) { if (window.cbNote) window.cbNote(t); }
  function norm(t) { return String(t || "").replace(/\\s+/g, " ").trim().toUpperCase(); }
  function opRows() {
    return [].slice.call(table.rows).filter(function (r) { return r.querySelector('td.status-cell[onclick^="cycleStatus"]'); });
  }
  var first = opRows();
  if (!first.length) return;
  var tmpl = first[0].cloneNode(true);
  [].forEach.call(tmpl.cells, function (td) { td.classList.remove("bb-m"); });
  var extra = {};
  [].forEach.call(table.rows, function (tr) {
    var t = norm(tr.cells[0] && tr.cells[0].textContent);
    if (t.indexOf("DEPLOYED ALL OPERATOR") === 0) extra.skill = tr;
    else if (t.indexOf("4 M CHANGE CONTROL SHEET UPDATED") === 0) extra.m4 = tr;
    else if (t.indexOf("NAME / SIGNATURE OF SUPERVISOR") === 0) extra.sup = tr;
    else if (t.indexOf("NAME / SIGN OF MANAGER") === 0) extra.mgr = tr;
  });
  if (extra.m4) extra.m4.classList.add("cb-m4row");
  // 2026-11-10 (later): EMP. CODE / NAME / PROCESS come from Shift Allocation
  // (read-only), START DAY stays blank; the user fills the day marks only.
  var ops = [], pnames = {};
  function weekOf(d) { return d <= 6 ? 1 : d <= 12 ? 2 : d <= 18 ? 3 : d <= 24 ? 4 : 5; }

  var on = false, curKey = "", cur = { line_id: "", month: "", days: 31, current: null, open: [] };
  var cells = {}, pending = {}, sending = {}, waiting = {}, seq = 0, els = {};
  var supSign = {}, supLeaders = {};

  // rows: the printed 14, more once the last one is used
  function setRows(n) {
    var rs = opRows(), want = Math.max(MIN_ROWS, n);
    while (rs.length < want) { var last = rs[rs.length - 1]; last.parentNode.insertBefore(tmpl.cloneNode(true), last); rs = opRows(); }
    while (rs.length > want) { var r = rs[rs.length - 2]; r.parentNode.removeChild(r); rs = opRows(); }
    rs.forEach(function (r, i) { r.cells[0].textContent = String(i + 1); });
    return rs;
  }
  // every cell of the sheet gets its key (data-k)
  function mapCells() {
    els = {};
    [].forEach.call(table.querySelectorAll("[data-k]"), function (el) { el.removeAttribute("data-k"); });
    [].forEach.call(table.querySelectorAll(".cb-today, .cb-openday, td.cb-lock"), function (el) {
      el.classList.remove("cb-today", "cb-openday", "cb-lock");      // set again below for this month / crew
    });
    var put = function (el, k) { if (el) { el.setAttribute("data-k", k); els[k] = el; } };
    setRows(ops.length).forEach(function (r, i) {
      var c = r.cells, o = ops[i];
      // 2026-11-13: PROCESS = the first day's process (with its number); another
      // process on a later day shows as that number inside the day's mark
      [o ? o.code : "", o ? o.name : "", o ? (o.pno ? o.pno + ". " : "") + o.process : "", ""].forEach(function (v, j) {
        var td = c[1 + j], el = td && td.querySelector("input, textarea");
        if (!el || !on) return;
        if (el.tagName === "TEXTAREA") {                  // back to one line, re-fitted below
          var inp = document.createElement("input");
          inp.type = "text"; inp.className = el.className.replace(/\\s*cb-wrap/, "");
          td.replaceChild(inp, el); el = inp;
        }
        el.value = v || ""; el.readOnly = true; el.tabIndex = -1;
        fitWrap(el, v || "");
      });
      for (var d = 1; d <= 31; d++) {
        if (o) put(c[4 + d], "o" + o.id + ".d" + d);
        else if (on && c[4 + d]) { c[4 + d].textContent = ""; c[4 + d].classList.add("cb-lock"); }
      }
    });
    ["skill", "m4", "sup"].forEach(function (p) {
      var tr = extra[p];
      if (!tr) return;
      put(tr.cells[1] && tr.cells[1].querySelector("input"), p + ".e");
      if (p === "sup") return;              // its day boxes: the leader's signature (paintSup)
      for (var d = 1; d <= 31; d++) put(tr.cells[1 + d], p + ".d" + d);
    });
    if (extra.mgr) {
      put(extra.mgr.cells[1] && extra.mgr.cells[1].querySelector("input"), "mgr.e");
      for (var w = 1; w <= 5; w++) put(extra.mgr.cells[1 + w] && extra.mgr.cells[1 + w].querySelector("input"), "mgr.w" + w);
    }
    Object.keys(els).forEach(function (k) {
      var el = els[k];
      if (el.tagName !== "INPUT" || el._cb) return;
      el._cb = true;
      el.addEventListener("input", function () { fitInput(el); if (on) save(k, el.value, false); });
      el.addEventListener("blur", function () { if (on && pending.hasOwnProperty(k)) flush(k); });
    });
  }
  var meas = document.createElement("canvas").getContext("2d");
  // long names / processes (read-only, from Shift Allocation): a smaller font on
  // one line, else two lines (<= 12 px, never below 9 px) - as on 10-Nov
  function fitWrap(input, text) {
    input.style.fontSize = ""; input.title = text;
    if (!text) return;
    var cs = getComputedStyle(input),
        w = input.clientWidth - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0) - 2,
        base = parseFloat(cs.fontSize) || 13, size = base;
    var tw = function (sz) { meas.font = cs.fontWeight + " " + sz + "px " + cs.fontFamily; return meas.measureText(text).width; };
    if (w <= 0) return;
    while (size > base - 3 && tw(size) > w) size -= 0.5;
    if (tw(size) <= w) { input.style.fontSize = size < base ? size + "px" : ""; return; }
    size = Math.min(base - 1, 12);
    while (size > 9 && tw(size) > 1.8 * w) size -= 0.5;
    var ta = document.createElement("textarea");
    ta.className = input.className + " cb-wrap"; ta.value = text; ta.title = text; ta.rows = 2;
    ta.readOnly = true; ta.tabIndex = -1;
    ta.style.cssText = "resize:none;overflow:hidden;white-space:normal;line-height:1.15;font-size:" + size +
      "px;padding:" + Math.max(1, Math.round((input.offsetHeight - 2.3 * size) / 2)) + "px 4px 0;height:" + input.offsetHeight + "px";
    input.parentNode.replaceChild(ta, input);
  }
  function fitInput(el) {
    el.style.fontSize = "";
    var cs = getComputedStyle(el), base = parseFloat(cs.fontSize) || 13, size = base,
        w = el.clientWidth - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0) - 2;
    if (w <= 0 || !el.value) { el.title = el.value || ""; return; }
    var tw = function (sz) { meas.font = cs.fontWeight + " " + sz + "px " + cs.fontFamily; return meas.measureText(el.value).width; };
    while (size > 9 && tw(size) > w) size -= 0.5;
    if (size < base) el.style.fontSize = size + "px";
    el.title = el.value;
  }
  function today() {
    var c = cur.current;
    return c && String(c.date).slice(0, 7) === cur.month ? +String(c.date).slice(8, 10) : 0;
  }
  function editable(k) {
    var c = cur.current, t = today(), m = /\\.d(\\d+)$/.exec(k), w = /\\.w(\\d)$/.exec(k);
    if (cur.admin) return !m || +m[1] <= cur.days;                      // admin: any day
    if (m) return +m[1] <= cur.days && (+m[1] === t || cur.open.indexOf(+m[1]) >= 0);
    if (w) return !!t && weekOf(t) === +w[1];
    if (!t) return false;
    var x = cells[k];
    return !x || !String(x.v || "").trim() || (x.d === c.date && x.s === c.shift);
  }
  function lockMsg(k) {
    if (!cur.current) return "No shift is running now: the sheet can be filled during the shift only";
    if (/\\.d\\d+$/.test(k)) return "Only the running shift's date can be filled: " + cur.current.label;
    if (/\\.w\\d$/.test(k)) return "Only this week's box can be signed";
    return "Filled in an earlier shift: it cannot be changed";
  }
  // 2026-11-12 (operator): the skill-matrix row shows the legend's symbols -
  // tick, cross, square - instead of YES ("YES" / "X" saved before show as tick /
  // cross).  (later) The 4M row below it shows "-" when the skill row is a tick
  // (nothing to update) and takes YES / NO only when it is a cross or a square;
  // blank until the skill row is marked.  "X" saved there before = NO.
  var SKILL_SHOW = { "YES": "\\u2713", "X": "\\u2715" };
  var M4_SHOW = { "X": "NO", "\\u2715": "NO", "\\u2713": "YES" };
  var M4_NEXT = { "": "YES", "YES": "NO", "NO": "" };
  function dayOf(k) { var m = /\\.d(\\d+)$/.exec(k); return m ? +m[1] : 0; }
  function skillOf(d) { var el = els["skill.d" + d]; return el ? el.textContent.trim() : ""; }
  // an operator's day mark drawn as a large shape, thin outline (operator: the marks
  // were too small for the box, then: borders too bold; then: first-day ● a
  // little smaller, the numbers a little bigger), with the day's process number inside when it differs
  // from the PROCESS column - never in the vacation marks (operator)
  function markSvg(v, no) {
    var sh, tc = "#000", fs = 16, tx = 14, ty = 13, txt = no;      // one number only (operator)
    if (v === "\\u25cb") sh = '<circle cx="14" cy="13" r="11.8" fill="none" stroke="#000" stroke-width="1.5"/>';
    else if (v === "\\u25cf") { sh = '<circle cx="14" cy="13" r="10.3" fill="#000"/>'; tc = "#fff"; }
    else if (v === "\\u25a1") sh = '<rect x="2.8" y="1.8" width="22.4" height="22.4" fill="none" stroke="#000" stroke-width="1.5"/>';
    else if (v === "\\u25b3") { sh = '<polygon points="14,1.2 27.2,24.8 0.8,24.8" fill="none" stroke="#000" stroke-width="1.5" stroke-linejoin="round"/>'; txt = ""; }
    else if (v === "\\u25b2") { sh = '<polygon points="14,0.5 27.5,25 0.5,25" fill="#000"/>'; txt = ""; }
    else return "";
    // no style on the svg itself: the PDF renderer (html2canvas) draws an svg's own
    // layout styles inside its picture and clips it - the wrapper centres it
    return '<span class="cb-msvgw"><svg class="cb-msvg" width="28" height="26" viewBox="0 0 28 26">' + sh +
      (txt ? '<text x="' + tx + '" y="' + ty + '" text-anchor="middle" dy="0.36em" font-family="Arial,sans-serif" font-weight="600" font-size="' +
             fs + '" fill="' + tc + '">' + txt + "</text>" : "") + "</svg></span>";
  }
  function dayNo(k) {                       // "o<id>.d<day>" -> that day's other process number(s)
    var m = /^o(\\d+)\\.d(\\d+)$/.exec(k);
    if (!m) return "";
    for (var i = 0; i < ops.length; i++)
      if (String(ops[i].id) === m[1]) return (ops[i].day_pno || {})[m[2]] || "";
    return "";
  }
  function showVal(k, el, v) {
    if (el.tagName === "INPUT") { if (el.value !== v) { el.value = v; } fitInput(el); return; }
    if (/^o\\d+\\.d\\d+$/.test(k)) {            // an operator's day: the mark, drawn large
      var no = v ? dayNo(k) : "", svg = v ? markSvg(v, no) : "";
      if (svg) {
        el.innerHTML = svg; el.style.color = "";
        el.title = no && !/[\\u25b3\\u25b2]/.test(v)
          ? "Process " + no.split(",").map(function (n) { return n + (pnames[n] ? " (" + pnames[n] + ")" : ""); }).join(" + ") + " this day"
          : "";
        return;
      }
      el.title = "";
    }
    var sv = v, col = "";
    if (/^skill\\.d/.test(k)) {
      sv = SKILL_SHOW[v] || v;
      col = sv === "\\u2715" ? "#d00" : sv === "\\u2713" ? "#080" : "";
    } else if (/^m4\\.d/.test(k) && on) {
      var sk = skillOf(dayOf(k));
      sv = sk === "\\u2713" ? "-" : !sk ? "" : (M4_SHOW[v] || v);
      col = sv === "NO" ? "#d00" : sv === "YES" ? "#080" : sv === "-" ? "#475569" : "";
    }
    el.textContent = sv;
    el.style.color = col;
    el.classList.toggle("cb-sq", sv === "\\u25a1" && /^skill\\./.test(k));
    el.classList.toggle("cb-dash", sv === "-");
  }
  function paintM4() {
    for (var d = 1; d <= 31; d++) {
      var k = "m4.d" + d, el = els[k];
      if (el && !pending.hasOwnProperty(k)) showVal(k, el, (cells[k] && cells[k].v) || "");
    }
  }
  // Name / Signature of Supervisor (Daily) = the line leader's signature (Shift
  // Allocation + Employee Master, sent by MES as sup_sign / leaders), shown ONLY
  // for a day whose two rows above both have a mark - never without values
  function paintSup() {
    var tr = extra.sup;
    if (!tr) return;
    var standalone = !on && !curKey;
    for (var d = 1; d <= 31; d++) {
      var td = tr.cells[1 + d], box = td && td.querySelector("input");
      if (!box) continue;
      var old = td.querySelector("img.cb-sign");
      if (old) td.removeChild(old);
      box.style.display = "";
      if (standalone) continue;                   // opened on its own: as drawn
      box.setAttribute("data-cb-sign", "1"); box.readOnly = true; box.tabIndex = -1; box.value = "";
      var a = els["skill.d" + d], b = els["m4.d" + d];
      var marked = !!(on && a && b && a.textContent.trim() && b.textContent.trim());
      var sg = marked ? supSign[String(d)] : null, L = sg && sg.leader ? supLeaders[String(sg.leader)] : null;
      // the leader saved in Shift Allocation with THIS crew that day - never the
      // leader of the shift running now (operator, 12-Nov)
      box.title = !marked ? "The line leader's signature appears once the rows above are filled for the day"
                : !sg ? "Loading the line leader\\u2026"
                : !sg.shift ? "Shift Allocation is not saved for this crew on this day: no signature"
                : !L ? "No line leader saved in Shift Allocation for this crew's " + sg.shift + " shift"
                : (L.name || "");
      if (L && L.signature) {
        var img = document.createElement("img");
        img.className = "cb-sign"; img.src = L.signature; img.alt = L.name || "";
        img.title = (L.name || "") + (sg.shift ? " (" + sg.shift + " shift)" : "");
        box.style.display = "none"; td.insertBefore(img, box);
      } else if (L && L.name) { box.value = L.name; fitInput(box); }
    }
  }
  // 2026-11-10 (operator): the day columns follow the month — a 28/30-day month
  // spreads its days over the width instead of leaving the last columns blank
  var dayCols = [].slice.call(table.querySelectorAll("col"), 5, 36);
  var headRow = [].filter.call(table.rows, function (r) { return norm(r.cells[0] && r.cells[0].textContent) === "S.NO."; })[0];
  function applyDays(n) {
    if (!(n >= 28 && n <= 31)) n = 31;
    var w = (31 * 38) / n;
    dayCols.forEach(function (c, i) { c.style.width = (i < n ? w.toFixed(3) : "0") + "px"; });
    var hide = function (td, d) { if (td) td.classList.toggle("cb-dhide", d > n); };
    for (var d = 1; d <= 31; d++) {
      if (headRow) hide(headRow.cells[4 + d], d);
      opRows().forEach(function (r) { hide(r.cells[4 + d], d); });
      ["skill", "m4", "sup"].forEach(function (p) { if (extra[p]) hide(extra[p].cells[1 + d], d); });
    }
  }
  function render() {
    mapCells();
    if (on) applyDays(cur.days);
    var t = today(), focus = document.activeElement;
    Object.keys(els).forEach(function (k) {
      var el = els[k], ed = on && editable(k);
      if (!(el === focus || pending.hasOwnProperty(k))) showVal(k, el, (cells[k] && cells[k].v) || "");
      if (el.tagName === "INPUT") { el.readOnly = on && !ed; el.tabIndex = on && !ed ? -1 : 0; }
      else el.classList.toggle("cb-lock", on && !ed);
      var m = /\\.d(\\d+)$/.exec(k), td = el.tagName === "INPUT" ? el.closest("td") : el;
      if (m && td) {
        td.classList.toggle("cb-today", on && +m[1] === t);
        td.classList.toggle("cb-openday", on && +m[1] !== t && cur.open.indexOf(+m[1]) >= 0);
        td.classList.add("cb-screen");
      }
    });
    paintM4();
    paintSup();
  }
  // saving: one request per cell at a time; the newest value goes after the ack
  function save(k, v, now) {
    pending[k] = v;
    clearTimeout(save["t" + k]);
    if (now) flush(k); else save["t" + k] = setTimeout(function () { flush(k); }, 700);
  }
  function flush(k) {
    clearTimeout(save["t" + k]);
    if (!pending.hasOwnProperty(k) || sending[k]) return;
    var v = pending[k];
    sending[k] = true;
    var s = ++seq;
    waiting[s] = { k: k, v: v };
    try { window.parent.postMessage({ type: "cb-ops-save", seq: s, line_id: cur.line_id, month: cur.month, key: k, value: v }, "*"); } catch (x) {}
    note("Saving\\u2026");
  }
  window.addEventListener("message", function (e) {
    if (e.source !== window.parent || !e.data || e.data.type !== "cb-ops-ack") return;
    var w = waiting[e.data.seq];
    delete waiting[e.data.seq];
    if (!w) return;
    sending[w.k] = false;
    if (!e.data.ok) {
      delete pending[w.k];
      if (els[w.k]) showVal(w.k, els[w.k], (cells[w.k] && cells[w.k].v) || "");
      paintM4();
      paintSup();
      note("Not saved: " + (e.data.error || "error"));
      return;
    }
    var c = cur.current || {};
    cells[w.k] = { v: w.v, d: c.date || null, s: c.shift || null };
    if (pending[w.k] !== w.v) flush(w.k); else delete pending[w.k];
    if (!Object.keys(waiting).length) { var t = new Date(); note("Saved " + two(t.getHours()) + ":" + two(t.getMinutes())); }
  });
  var origStatus = window.cycleStatus, origYN = window.cycleYN;
  function cycle(td, orig) {
    if (!on && curKey) return;                        // MES sheet still loading
    if (!on) return orig(td);                         // opened on its own: as drawn
    var k = td.getAttribute("data-k");
    if (!k) return;
    if (!cur.line_id) { note("Select a line first"); return; }
    if (!editable(k)) { note(lockMsg(k)); return; }
    var nv;
    if (/^m4\\.d/.test(k)) {                       // the 4M row: "-" / YES / NO by the skill row
      var d = dayOf(k), sk = skillOf(d);
      if (pending.hasOwnProperty("skill.d" + d)) { note("Saving the skill-matrix mark\\u2026"); return; }
      if (!sk) { note("Mark the skill-matrix row first: YES / NO is filled when it is \\u2715 or \\u25a1"); return; }
      if (sk === "\\u2713") { note("Not needed: all operators are deployed as per the skill matrix (\\u2713)"); return; }
      nv = M4_NEXT[td.textContent.trim()];
      if (nv === undefined) nv = "";
    } else {
      td.textContent = pending.hasOwnProperty(k) ? pending[k] : ((cells[k] && cells[k].v) || "");   // the mark without its number
      orig(td);
      nv = td.textContent.trim();
    }
    showVal(k, td, nv);
    save(k, nv, true);
    if (/^skill\\.d/.test(k)) paintM4();
    paintSup();
  }
  window.cycleStatus = function (td) { cycle(td, origStatus); };
  window.cycleYN = function (td) { cycle(td, origYN); };
  // a click on a read-only box says why
  table.addEventListener("click", function (e) {
    var el = e.target && e.target.closest && e.target.closest("input[data-k]");
    if (el && on && el.readOnly) note(lockMsg(el.getAttribute("data-k")));
    var st = e.target && e.target.closest && e.target.closest("td");
    if (on && st && st.parentNode === extra.sup && st.cellIndex > 1) {
      var im = st.querySelector("img.cb-sign"), bx = st.querySelector("input");
      note(im ? "Signed: " + (im.title || im.alt) : (bx && bx.title) || "");
    }
  });
  function statusNote() {
    if (!ops.length) return "No crew saved in Shift Allocation for this sheet this month";
    return cur.current ? "Fill for " + cur.current.label + (cur.open.length ? " \\u00b7 pending day(s) " + cur.open.join(", ") + " may still be filled" : "")
                       : "No shift is running now: the sheet is read-only";
  }
  window.cbOnData = function (d, s) {
    if (!d || !d.ops) return;
    window.cbTitleExtra = d.sheet_no ? "Sheet " + d.sheet_no : "";
    var key = (s.line_id || "") + "|" + (s.month || "");
    var sh = d.sheetOps;
    if (!s.line_id) {
      on = true; curKey = key; cells = {}; pending = {}; ops = []; supSign = {}; supLeaders = {};
      cur = { line_id: "", month: s.month, days: 31, current: null, open: [] };
      render(); note("Select a line");
      return;
    }
    if (!sh || String(sh.line_id) !== String(s.line_id) || sh.month !== s.month) {
      if (key !== curKey) {
        on = false; curKey = key; cells = {}; pending = {}; ops = []; supSign = {}; supLeaders = {};
        cur = { line_id: s.line_id, month: s.month, days: 31, current: null, open: [] };
        render();
      }
      note(d.error || (d.loading ? "Loading\\u2026" : ""));
      return;
    }
    if (key !== curKey || !on) pending = {};
    on = true; curKey = key;
    cur = { line_id: s.line_id, month: s.month, days: sh.days || 31, current: sh.current || null, open: sh.open_days || [],
            admin: !!sh.admin };
    cells = sh.cells || {};
    ops = sh.operators || [];
    pnames = sh.process_nos || {};
    supSign = sh.sup_sign || {}; supLeaders = sh.leaders || {};
    render();
    if (!Object.keys(waiting).length) note(statusNote());
  };
})();
</script>
"""
# 2026-11-10 (operator: "value input me bahut chhoti hai, visibility kam hai
# aur legend bhi — size bada kar de aur colour bhi dark kar de"; then "values
# ko bold mat kar, only colour black, thoda sa size bada").  Entries
# 11 -> 13 px black (not bold), day marks 14 -> 22 px with a heavier stroke (the thin
# ○ read as grey), legend rows 11 -> 15 px black, bottom row labels 11 -> 13 px.
# The two legend rows get class cb-legend from OPS_SCRIPT (found by "[ STATUS ]").
OPS_STYLE = """
<style>
/* MES Control Board: bigger, darker entries and legend. */
#operatorTable .cell-input { font-size: 13px; color: #000; }
#operatorTable .cell-input::placeholder { font-size: 12px; font-weight: normal; }
#operatorTable td .cb-msvgw { display: inline-block; width: 28px; height: 26px; line-height: 0; vertical-align: middle; }
#operatorTable td svg.cb-msvg { pointer-events: none; }
#operatorTable td.status-cell[onclick^="cycleStatus"] { font-size: 22px; line-height: 1;
  -webkit-text-stroke: 0.6px currentColor; }
#operatorTable tr.cb-legend { height: 30px !important; }
#operatorTable tr.cb-legend td { font-size: 15px !important; color: #000 !important; }
#operatorTable tr.cb-legend td.cb-legend-note { font-size: 13px !important; }
#operatorTable td.text-left[colspan="4"] { font-size: 13px !important; color: #000; }
#operatorTable td.text-left[colspan="4"] span { font-size: 12px !important; font-weight: bold !important; }
#operatorTable .cb-sym { font-size: 18px; line-height: 1; vertical-align: -1px; }
#operatorTable .cb-sym.big { font-size: 24px; vertical-align: -3px; -webkit-text-stroke: 0.6px currentColor; }
</style>
"""
# Fixed text of a format, exact match (each must be found once).  2026-11-06
# (operator, Operator Status): the legend of the two YES / NO rows was missing
# its symbols — "YES ✓ / NO X / FOR MULTI SKILL □" and "( IF X OR □ )".
SHEET_FIXES = {
    # 2026-11-12: Q Chart — the template's placeholders become the company's
    # (logo + name, as on every other sheet), a plain toolbar title, and the
    # chart page is the PDF's crop (`sheet`, see cb-pdf-render.js target()).
    "q-chart.html": [
        # 2026-11-13 (operator): no bulk clear — earlier days are locked
        ('<button onclick="location.reload()">Clear Data</button>', ""),
        ("<title>Portrait Q Chart - Perfect No Overlap</title>", "<title>Q Chart</title>"),
        ("<strong>PORTRAIT Q CHART (ZERO OVERLAP - 100% PERFECT)</strong>", "<strong>Q CHART</strong>"),
        ('<div class="logo-box">YOUR<br>COMPANY</div>', LOGO_HTML),
        ("YOUR COMPANY DEVICE INDIA PVT. LTD.", "TOYOTA BOSHOKU DEVICE INDIA PVT. LTD."),
        ('<div class="page-container">', '<div class="page-container sheet">'),
        # 2026-11-12: FEBRUARY's 29th follows this box (q-chart-layout.js qSyncFeb)
        ('<input class="inp" placeholder="e.g. 2026">', '<input class="inp" id="q-year" placeholder="e.g. 2026-27">'),
    ],
    # 2026-11-12: 5S Daily Check Sheet — hint in English; the 1600 px table sat
    # in a 1600 px box with 15 px padding, so it scrolled inside itself
    "5s-daily-check-sheet.html": [
        ('<button onclick="window.location.reload()">Clear All</button>', ""),
        ("(Boxes par click karein: Blank &rarr; ✓ &rarr; ✕)", "(Click a box: Blank &rarr; ✓ &rarr; ✕)"),
        ("max-width: 1600px;\n            margin: 0 auto 10px auto;", "max-width: 1630px;\n            margin: 0 auto 10px auto;"),
        ("max-width: 1600px;\n            margin: 0 auto;\n", "max-width: 1630px;\n            margin: 0 auto;\n"),
    ],
    # 2026-11-12: the Clear confirmation in English
    "defect-recording-sheet.html": [
        ('confirm("Kya aap saara data clear karna chahte hain?")', 'confirm("Clear all entries on this sheet?")'),
        ('<button onclick="clearSheet()">Clear All</button>', ""),
    ],
    "defect-recording-sheet-recliner.html": [
        ('confirm("Kya aap saara data clear karna chahte hain?")', 'confirm("Clear all entries on this sheet?")'),
        ('<button onclick="clearSheet()">Clear All</button>', ""),
    ],
    # 2026-11-13 (operator): no bulk clear / mark-all — earlier days are locked
    "track-record-sheet.html": [
        ('<button onclick="clearSheet()">Clear All Data</button>', ""),
    ],
    "annual-safety-chart.html": [
        ('<button onclick="markAllSafe()">Mark All Safe (Green)</button>', ""),
        ('<button onclick="resetSafetyChart()">Reset All</button>', ""),
    ],
    # 2026-11-10: the 4M sheet is saved now — "Reset All" would wipe the month
    "4m-change-control-sheet.html": [
        ('<button onclick="clearSheet()">Reset All</button>', ""),
    ],
    "operator-status.html": [
        # 2026-11-10: saved now — "Clear All" would wipe the month
        ('<button onclick="resetSheet()">Clear All</button>', ""),
        # 2026-11-10: the legend's ▲ (vacation without information) in the click cycle
        # 2026-11-13 (operator): P (Unplanned Leave) removed - it is never filled
        ("const symbols = ['', '●', '□', '○', '△', 'P'];", "const symbols = ['', '●', '□', '○', '△', '▲'];"),
        ("△ (Vacation) &rarr; P (Unplanned Leave)", "△ (Vacation) &rarr; ▲ (Vacation Without Information)"),
        ("(YES &nbsp;&nbsp;&nbsp;&nbsp; /", "(YES \u2713 /"),
        ("FOR MULTI SKILL)", "FOR MULTI SKILL \u25a1)"),
        ("( IF X OR &nbsp;&nbsp;&nbsp;&nbsp; )", "( IF \u2715 OR \u25a1 )"),
        # 2026-11-12 (operator): tick / cross / square instead of YES / X
        ("NO X &nbsp;&nbsp; /", "NO \u2715 &nbsp;&nbsp; /"),
        ("const symbols = ['', 'YES', 'X'];", "const symbols = ['', '\u2713', '\u2715', '\u25a1'];"),
        ("(symbols[nextIdx] === 'X') ? '#d00' : ((symbols[nextIdx] === 'YES') ? '#080' : '#000')",
         "(symbols[nextIdx] === '\u2715') ? '#d00' : ((symbols[nextIdx] === '\u2713') ? '#080' : '#000')"),
        # 2026-11-10: legend symbols in their own span (OPS_STYLE sizes them;
        # \u25a1 and \u25cb are drawn far smaller than \u25cf \u25b3 \u25b2 by the font)
        ("\u25cf\uff1aFIRST DAY", '<span class="cb-sym">\u25cf</span>\uff1aFIRST DAY'),
        ("\u25a1\uff1aTRAINING DONE", '<span class="cb-sym big">\u25a1</span>\uff1aTRAINING DONE'),
        ("\u25cb\uff1aAFTER TRAINING DONE (2 DAYS)", '<span class="cb-sym big">\u25cb</span>\uff1aAFTER TRAINING DONE (2 DAYS)'),
        ("\u25b3\uff1aVACATION\n", '<span class="cb-sym">\u25b3</span>\uff1aVACATION\n'),
        ("\u25b2 \uff1aVACATION WITHOUT INFORMATION", '<span class="cb-sym">\u25b2</span> \uff1aVACATION WITHOUT INFORMATION'),
    ],
}
# 2026-11-11 — Defect Recording Sheet filled from the Red Bin portal (operator:
# "defect recording sheet me feed karwa rejection ko ... uss par click karne
# par uska summary page aayega").  The Control Board posts the line's month
# (/api/line-rejection/sheet) in as data.rej; this lays it out on the format:
#   one block per SHIFT:- header (the shift box says which shift: A / B, or C
#   if typed), in-process defect rows (1-7) and FINAL INSPECTION rows (1-4),
#   each defect on its PROCESS / CHILD PART row by its cause, handling damage
#   on the ABNORMAL HANDLING row, TOTAL REJECTION QTY per day, LAST MONTH
#   REJECTION per defect.  More defects than rows -> the last row is "Other
#   defects (n)".  Filled cells are read-only and open the summary of their
#   rejections in the MES page ("cb-rj-open" with the record ids).  Rows the
#   Red Bin does not feed (Recliner "Welding spatter repair") stay manual.
DEFECT_SCRIPT = """
<style>
#mainDefectSheet input.cb-rj { color: #b91c1c; font-weight: 700; }
#mainDefectSheet input.cb-rj-name { color: #0f172a; font-weight: 700; }
#mainDefectSheet input.cb-rj-link { cursor: pointer; }
#mainDefectSheet input.cb-rj-link:hover { background: #fee2e2; outline: 1px solid #fca5a5; }
#mainDefectSheet input.cb-f { font-weight: 700; }
@media print { #mainDefectSheet input.cb-rj-link:hover { background: transparent; outline: none; } }
</style>
<script>
/* MES Control Board: Defect Recording Sheet filled from the Red Bin rejections. */
(function () {
  var T = document.getElementById("mainDefectSheet");
  if (!T) return;
  var MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  function txt(el) { return String(el.textContent || "").replace(/\\s+/g, " ").trim().toUpperCase(); }
  function inp(td) { return td ? td.querySelector("input") : null; }
  // the last 32 cells of a row = 31 days + TOTAL
  function grid(tr) {
    var tds = tr.children, n = tds.length;
    if (n < 32) return null;
    var days = [];
    for (var i = n - 32; i < n - 1; i++) days.push(tds[i]);
    return { days: days, total: tds[n - 1] };
  }
  // ---- read the format: a block per "SHIFT:-" header
  var blocks = [], cur = null, section = "proc", slot = null;
  [].forEach.call(T.rows, function (tr) {
    var tds = [].slice.call(tr.children), lab = tds.map(txt), t = txt(tr);
    var si = -1;
    lab.forEach(function (x, i) { if (si < 0 && /^SHIFT ?:-?$/.test(x)) si = i; });
    if (si >= 0) {
      cur = { shift: inp(tds[si + 1]), proc: [], fi: [], abnormal: null, total: null,
              rows: [], sign: null, done: false };
      blocks.push(cur); section = "proc"; slot = null;
      return;
    }
    if (!cur) return;
    // 2026-11-12: TOTAL REJECTION QTY = a formula over every quantity row of the
    // block (Red Bin, typed, Recliner welding spatter); the leader-sign row
    if (/^TOTAL REJECTION QTY/.test(t)) { cur.total = grid(tr); cur.done = true; return; }
    if (/SIGNATURE BY LINE LEADER/.test(t)) { cur.sign = grid(tr); return; }
    if (!cur.done) {
      var gr = grid(tr);
      if (gr && inp(gr.days[0]) && inp(gr.total)) cur.rows.push(gr);
    }
    if (t === "FINAL INSPECTION DEFECT") { section = "fi"; slot = null; return; }
    var pi = lab.indexOf("PROCESS"), ci = lab.indexOf("CHILD PART");
    var name = tr.querySelector('input[placeholder="Defect Name"]');
    if (pi >= 0 && name) {
      slot = { name: name, last: inp(tds[pi - 1]), process: grid(tr), child: null };
      cur[section].push(slot);
      return;
    }
    if (ci >= 0 && slot && !slot.child) { slot.child = grid(tr); return; }
    if (/ABNORMAL HANDLING/.test(t)) { cur.abnormal = grid(tr); return; }
  });
  if (!blocks.length) return;

  // ---- formula (2026-11-12, operator: "total rejection quantity me total ka
  // formula bhi check kar lena, naa ho toh lagaa dena"): each row's TOTAL = its
  // 31 days, TOTAL REJECTION QTY per day = all rows of the block, its TOTAL =
  // the month.  Recalculated on every entry; formula cells are read-only.
  function num(i) { if (!i) return NaN; var v = parseFloat(String(i.value).replace(/,/g, "")); return isNaN(v) ? NaN : v; }
  function fmtN(v) { return String(Math.round(v * 100) / 100); }
  function setF(i, v, tip) { if (!i) return; i.value = v; i.removeAttribute("data-cb-lock"); i.readOnly = true; i.classList.add("cb-f"); if (tip && !i.title) i.title = tip; }
  function recalc() {
    blocks.forEach(function (b) {
      if (!b.total) return;
      var dayT = [], any = [];
      b.rows.forEach(function (g) {
        var s = 0, has = false;
        g.days.forEach(function (td, d) {
          var v = num(inp(td));
          if (isNaN(v)) return;
          s += v; has = true; dayT[d] = (dayT[d] || 0) + v; any[d] = true;
        });
        setF(inp(g.total), has ? fmtN(s) : "", "Total of the row (formula)");
      });
      var tot = 0, hasT = false;
      b.total.days.forEach(function (td, d) {
        setF(inp(td), any[d] ? fmtN(dayT[d]) : "", "Total of the day (formula)");
        if (any[d]) { tot += dayT[d]; hasT = true; }
      });
      setF(inp(b.total.total), hasT ? fmtN(tot) : "", "Total of the month (formula)");
    });
    paintSign();
  }
  T.addEventListener("input", function (e) { if (e.target && e.target.tagName === "INPUT") recalc(); });
  // 2026-11-13: the day of each box (CELLS_SCRIPT locks all but today's)
  window.cbDateOf = function (el, P) {
    var td = el.closest && el.closest("td"), tr = td && td.parentNode;
    if (!tr || !T.contains(tr) || !P.y || !P.m) return "";
    var tds = tr.children, n = tds.length, i = [].indexOf.call(tds, td);
    if (n < 32 || i < n - 32 || i >= n - 1) return "";
    var d = i - (n - 32) + 1, dim = new Date(P.y, P.m, 0).getDate();
    return d <= dim ? cbYmd(P.y, P.m, d) : "X";
  };
  if (typeof window.clearSheet === "function") {
    var clear0 = window.clearSheet;
    window.clearSheet = function () { clear0(); recalc(); };
  }

  // ---- line leader's signature per day (2026-11-12, operator: "line leader
  // signature me signature auto fetch kar le jaise track record sheet me"):
  // the Track Record Sheet's data — the leader in Shift Allocation of each
  // shift that ran, signature image from Employee Master — posted in as `sign`
  var lastSign = null;
  // 2026-11-12 (operator): a signature only on a day that has a quantity in the
  // block (Red Bin or typed; a typed 0 counts) - never on an empty day.  Leader =
  // the Track Record day, else the leader saved in Shift Allocation (`leads`).
  function dayHas(b, k) {
    if (b.total) { var t = inp(b.total.days[k]); return !!(t && String(t.value).trim()); }
    return b.rows.some(function (g) { var i = inp(g.days[k]); return !!(i && String(i.value).trim()); });
  }
  function fillSign(sg) { lastSign = sg; paintSign(); }
  function paintSign() {
    var sg = lastSign;
    blocks.forEach(function (b) {
      if (!b.sign) return;
      var sh = String(b.shift ? b.shift.value : "").trim().toUpperCase();
      var days = (sg && sg.shifts && sg.shifts[sh]) || {}, leaders = (sg && sg.leaders) || {};
      var leads = (sg && sg.leads && sg.leads[sh]) || {};
      b.sign.days.forEach(function (td, k) {
        var box = inp(td);
        if (!box) return;
        var old = td.querySelector("img.cb-sign");
        if (old) td.removeChild(old);
        box.style.display = "";
        if (box.getAttribute("data-cb-sign")) { box.value = ""; box.title = ""; box.readOnly = box.hasAttribute("data-cb-lock"); box.removeAttribute("data-cb-sign"); }
        if (box.value) return;                       // typed by hand: kept
        if (!dayHas(b, k)) return;                   // no quantity that day: no signature
        var v = days[String(k + 1)], lid = (v && v.S1) || leads[String(k + 1)] || null;
        var L = lid ? leaders[lid] : null;
        if (L && L.signature) {
          var img = document.createElement("img");
          img.className = "cb-sign"; img.src = L.signature; img.alt = L.name || ""; img.title = L.name || "";
          img.style.cssText = "display:block;max-width:100%;max-height:26px;margin:0 auto;object-fit:contain";
          box.style.display = "none"; box.setAttribute("data-cb-sign", "1");
          td.insertBefore(img, box);
        } else if ((v && v.S1_name) || (L && L.name)) {
          box.value = (v && v.S1_name) || L.name; box.title = box.value;
          box.removeAttribute("data-cb-lock"); box.readOnly = true;          // auto signature: frozen
          box.setAttribute("data-cb-sign", "1");
        }
      });
    });
  }
  recalc();

  // ---- writing: every cell this script fills is remembered and cleared before the next fill
  var filled = [];
  function put(td, val, cls, ids, title) {
    var i = inp(td);
    if (!i) return;
    i.value = val; i.removeAttribute("data-cb-lock"); i.readOnly = true;   // Red Bin value: frozen
    i.classList.add(cls || "cb-rj");
    if (ids && ids.length) {
      i.classList.add("cb-rj-link");
      i.setAttribute("data-ids", ids.join(","));
      i.setAttribute("data-title", title || "");
      i.title = (title || "") + " - click for the summary";
    }
    filled.push(i);
  }
  function reset() {
    filled.forEach(function (i) {
      i.value = ""; i.readOnly = i.hasAttribute("data-cb-lock");
      i.classList.remove("cb-rj", "cb-rj-name", "cb-rj-link");
      i.removeAttribute("data-ids"); i.removeAttribute("data-title"); i.removeAttribute("title");
    });
    filled = [];
  }
  function sum(rs) { var q = 0; rs.forEach(function (r) { q += Number(r.qty) || 0; }); return q; }
  function ids(rs) { return rs.map(function (r) { return r.id; }); }
  var DUE = { process: "Process", child: "Child Part", handling: "Abnormal Handling" };
  var monLabel = "";
  function dayTitle(d) { return d + " " + monLabel; }
  // one row of 31 days + total from a list of records
  function fillRow(g, rs, what, shift) {
    if (!g || !rs.length) return;
    var by = {};
    rs.forEach(function (r) { (by[r.day] = by[r.day] || []).push(r); });
    Object.keys(by).forEach(function (d) {
      var td = g.days[Number(d) - 1];
      if (td) put(td, String(sum(by[d])), "cb-rj", ids(by[d]), what + " - " + dayTitle(d) + " - Shift " + shift);
    });
    put(g.total, String(sum(rs)), "cb-rj", ids(rs), what + " - " + monLabel + " - Shift " + shift);
  }
  var last = null;
  function fill(rej) {
    fill0(rej);
    recalc();
  }
  function fill0(rej) {
    reset();
    last = rej;
    if (!rej) return;
    var m = String(rej.month || "");
    monLabel = /^\\d{4}-\\d{2}$/.test(m) ? MONTHS[+m.slice(5, 7) - 1] + " " + m.slice(0, 4) : m;
    var recs = rej.records || [], prev = rej.last_month || [];
    blocks.forEach(function (b) {
      var sh = String(b.shift ? b.shift.value : "").trim().toUpperCase();
      if (!sh) return;
      var mine = recs.filter(function (r) { return String(r.shift || "").toUpperCase() === sh; });
      var mineLast = prev.filter(function (r) { return String(r.shift || "").toUpperCase() === sh; });
      ["proc", "fi"].forEach(function (sec) {
        var slots = b[sec];
        if (!slots.length) return;
        var g = {};
        mine.forEach(function (r) {
          if (r.section !== sec || r.due === "handling") return;
          var k = r.defect || "(No defect name)";
          (g[k] = g[k] || { name: k, names: [k], recs: [] }).recs.push(r);
        });
        var list = Object.keys(g).map(function (k) { return g[k]; });
        list.sort(function (a, c) { return sum(c.recs) - sum(a.recs) || a.name.localeCompare(c.name); });
        if (list.length > slots.length) {
          var keep = list.slice(0, slots.length - 1), rest = list.slice(slots.length - 1);
          var other = { name: "Other defects (" + rest.length + ")", names: [], recs: [] };
          rest.forEach(function (x) { other.names = other.names.concat(x.names); other.recs = other.recs.concat(x.recs); });
          list = keep.concat([other]);
        }
        list.forEach(function (grp, i) {
          var s = slots[i];
          put(s.name.parentNode, grp.name, "cb-rj-name", ids(grp.recs), grp.name + " - " + monLabel + " - Shift " + sh);
          var lq = 0;
          mineLast.forEach(function (r) {
            if (r.section === sec && r.due !== "handling" && grp.names.indexOf(r.defect || "(No defect name)") >= 0) lq += Number(r.qty) || 0;
          });
          if (lq && s.last) put(s.last.parentNode, String(lq), "cb-rj");
          ["process", "child"].forEach(function (due) {
            var rs = grp.recs.filter(function (r) { return r.due === due; });
            fillRow(due === "process" ? s.process : s.child, rs, grp.name + " (" + DUE[due] + ")", sh);
          });
        });
      });
      fillRow(b.abnormal, mine.filter(function (r) { return r.due === "handling"; }), "Defect due to abnormal handling", sh);
      fillRow(b.total, mine, "Total rejection", sh);
    });
  }
  // a click on a filled cell opens its rejections in the MES page
  T.addEventListener("click", function (e) {
    var i = e.target && e.target.closest ? e.target.closest("input.cb-rj-link") : null;
    if (!i) return;
    var list = String(i.getAttribute("data-ids") || "").split(",").filter(Boolean);
    if (!list.length) return;
    try { window.parent.postMessage({ type: "cb-rj-open", ids: list, title: i.getAttribute("data-title") || "" }, "*"); } catch (x) {}
  });
  // the shift boxes can be changed (e.g. C): fill again
  blocks.forEach(function (b) { if (b.shift) b.shift.addEventListener("change", function () { fill(last); fillSign(lastSign); }); });
  window.cbOnData = function (d, s) {
    var note = window.cbNote || function () {};
    var sg = d && d.sign;
    fillSign(s && s.line_id && sg && String(sg.line_id) === String(s.line_id) && sg.month === s.month ? sg : null);
    if (!s || !s.line_id) { fill(null); note("Select a line to fill the Red Bin rejections."); return; }
    var rej = d.rej;
    if (!rej || String(rej.line_id) !== String(s.line_id) || rej.month !== s.month) {
      fill(null);
      note(d.rejError ? d.rejError : d.rejLoading ? "Loading rejections..." : "");
      return;
    }
    fill(rej);
    if (!rej.rb_line) { note(rej.rb_zone ? "No Red Bin line matches this line." : "This line is not in the Red Bin portal."); return; }
    var n = (rej.records || []).length, q = sum(rej.records || []);
    note("Red Bin: " + String(rej.rb_zone || "").replace(/^Zone\\s*\\d+\\s*-\\s*/i, "") + " " + rej.rb_line +
         ((rej.shared_with || []).length ? " (also " + rej.shared_with.join(", ") + ")" : "") +
         " - " + (n ? q + " pcs in " + n + " rejection" + (n === 1 ? "" : "s") + ". Click a quantity for its summary." : "no rejection this month."));
  };
})();
</script>
"""

import json as _json
CELLS_SCRIPT = """
<script>
/* MES Control Board: auto-save what the user types or clicks (2026-11-12).
   Every input / text box / editable text / click mark of the sheet is keyed (its
   id, else its position) and, a moment after the user changes it, posted to the
   Control Board ("cb-cells-save"), which saves it for the sheet's line and
   period.  Saved entries come back as "cb-cells" and are put in again after
   every MES fill.  Values MES fills itself (read-only boxes) are never sent, and
   only the box the user changed is saved (no formula or MES values).
   2026-11-13 (operator): only TODAY can be entered.  The Control Board sends the
   line's production day ("today"), the day each saved value was entered ("set")
   and the period; the sheet says which day a box belongs to (window.cbDateOf:
   "YYYY-MM-DD", "W:<from>:<to>" for a weekly box, "" for the whole period,
   "X" never).  Everything else is locked (read-only, clicks blocked); today's
   boxes get an orange frame on screen (not in the PDF). */
(function () {
  if (window.__cbCells) return;
  window.__cbCells = true;
  var SKIP = ".toolbar, .no-print, .cb-hov, .cb-tip";
  var applying = false, canSave = false, scope = "", dirty = {}, timer = null, seq = 0, pend = {}, retry = {};
  var base = {}, init = null, applied = {};
  var today = "", setOn = {}, open = true, P = {}, noteAt = 0, admin = false;
  function skip(el) { return !!(el.closest && el.closest(SKIP)); }
  function isForm(el) { return /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName); }
  function isEdit(el) {
    if (el.hasAttribute && el.hasAttribute("data-cb-ce")) return true;      // ours, locked for now
    var a = el.getAttribute && el.getAttribute("contenteditable");
    if (a === "" || a === "true") { el.setAttribute("data-cb-ce", "1"); return true; }
    return false;
  }
  var lcss = document.createElement("style");
  lcss.textContent = "[data-cb-lock]{cursor:not-allowed!important}" +
    "[data-cb-lock]:focus{background:transparent!important;outline:none!important}" +
    ".cb-today.cb-screen{outline:2px solid #f59e0b!important;outline-offset:-2px}";
  document.head.appendChild(lcss);
  function two(n) { return (n < 10 ? "0" : "") + n; }
  window.cbYmd = function (y, m, d) { return y + "-" + two(m) + "-" + two(d); };
  function dmy(iso) { return iso ? iso.slice(8, 10) + "-" + iso.slice(5, 7) + "-" + iso.slice(0, 4) : ""; }
  function pinfo(period, kind) {
    var p = String(period || "");
    if (kind === "year" && /^\\d{4}$/.test(p)) return { kind: kind, y: +p };
    if (kind === "fy" && /^\\d{4}-\\d{2}$/.test(p)) return { kind: kind, y0: +p.slice(0, 4) };
    if (/^\\d{4}-\\d{2}$/.test(p)) return { kind: "month", y: +p.slice(0, 4), m: +p.slice(5, 7) };
    return {};
  }
  function dateOf(el) {
    try { return typeof window.cbDateOf === "function" ? String(window.cbDateOf(el, P) || "") : ""; }
    catch (err) { return "X"; }
  }
  function why(k, el) {                     // "" = may be changed, else the reason
    if (!today) return "";                  // older API without the day rule: as before
    if (!canSave) return scope ? "Saved copy: read-only" : "Pick a line in the Control Board filter to enter data";
    if (admin) return dateOf(el) === "X" ? "This box cannot be filled" : "";      // admin: any day
    if (!open) return "Only today's entries (" + dmy(today) + ") can be made: this sheet is for another period";
    var dk = dateOf(el), w = /^W:(.{10}):(.{10})$/.exec(dk);
    if (dk === "X") return "This box cannot be filled";
    if (/^\\d{4}-\\d{2}-\\d{2}$/.test(dk) && dk !== today) return "Only today's column (" + dmy(today) + ") can be changed";
    if (w && !(w[1] <= today && today <= w[2])) return "Only the current week's box can be signed";
    if (setOn[k] && setOn[k] < today) return "Entered on " + dmy(setOn[k]) + ": it cannot be changed";
    return "";
  }
  function setLock(el, reason) {
    var lock = !!reason;
    if (isForm(el)) {
      if (lock) {
        if (!el.readOnly || el.hasAttribute("data-cb-lock")) { el.readOnly = true; el.setAttribute("data-cb-lock", reason); }
      } else if (el.hasAttribute("data-cb-lock")) { el.removeAttribute("data-cb-lock"); el.readOnly = false; }
      if (el.tagName === "SELECT" || /^(checkbox|radio)$/i.test(el.type || "")) el.disabled = el.hasAttribute("data-cb-lock");
    } else if (el.hasAttribute("data-cb-ce")) {
      el.setAttribute("contenteditable", lock ? "false" : "true");
      if (lock) el.setAttribute("data-cb-lock", reason); else el.removeAttribute("data-cb-lock");
    } else if (lock) el.setAttribute("data-cb-lock", reason);
    else el.removeAttribute("data-cb-lock");
  }
  function lockAll() {
    var m = keyed();
    for (var k in m) {
      var el = m[k], r = why(k, el), mine = !r && !!today && dateOf(el) === today && !(isForm(el) && el.readOnly);
      setLock(el, r);
      el.classList.toggle("cb-today", mine);
      if (mine) el.classList.add("cb-screen");
    }
  }
  function lockNote(el, text) {
    var t = Date.now();
    if (t - noteAt < 1500) return;
    noteAt = t;
    status(text || el.getAttribute("data-cb-lock") || "Locked", "error");
  }
  // a locked click mark does nothing; a locked box says why
  document.addEventListener("click", function (e) {
    var t = e.target, el = t && t.closest ? t.closest("[data-cb-lock]") : null;
    if (!el && t && isForm(t) && t.readOnly && today && !skip(t) && !t.classList.contains("cb-rj-link")) {
      lockNote(t, "Filled automatically: it cannot be changed");      // MES value / formula
      return;
    }
    if (!el || skip(el)) return;
    if (!isForm(el) && !el.hasAttribute("data-cb-ce")) { e.stopPropagation(); e.preventDefault(); }
    lockNote(el);
  }, true);
  function keyed() {
    var m = {}, n = 0;
    [].forEach.call(document.querySelectorAll("input, textarea, select"), function (el) {
      if (skip(el) || el.classList.contains("cb-sel")) return;
      if (/^(button|submit|reset|file|hidden|image)$/i.test(el.type || "")) return;
      if (el.id) m["#" + el.id] = el; else m["f" + (n++)] = el;
    });
    var e = 0;
    [].forEach.call(document.querySelectorAll("[contenteditable]"), function (el) { if (!skip(el) && isEdit(el)) m["e" + (e++)] = el; });
    var k = 0;
    [].forEach.call(document.querySelectorAll("[onclick]"), function (el) {
      if (skip(el) || /^(BUTTON|A|INPUT|SELECT|TEXTAREA)$/.test(el.tagName) || (el.closest && el.closest("button"))) return;
      m["k" + (k++)] = el;
    });
    return m;
  }
  function read(el) {
    if (isForm(el)) return /^(checkbox|radio)$/i.test(el.type || "") ? (el.checked ? "1" : "") : String(el.value == null ? "" : el.value);
    if (isEdit(el)) return el.innerText;
    // our screen-only marks (today's frame) are not part of the saved value
    var c = String(el.className || "").replace(/(^|\\s)cb-(today|screen)(?=\\s|$)/g, " ").replace(/\\s+/g, " ").trim();
    return JSON.stringify({ c: c, s: el.getAttribute("data-state"), t: el.children.length ? null : el.textContent });
  }
  function write(el, v) {
    if (isForm(el)) {
      if (/^(checkbox|radio)$/i.test(el.type || "")) el.checked = v === "1"; else el.value = v;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    } else if (isEdit(el)) {
      el.innerText = v;
    } else {
      try {
        var o = JSON.parse(v);
        el.className = o.c || "";
        if (o.s == null) el.removeAttribute("data-state"); else el.setAttribute("data-state", o.s);
        if (o.t != null && !el.children.length) el.textContent = o.t;
      } catch (err) { /* not a mark */ }
    }
  }
  function snap(m) { var b = {}; for (var k in m) b[k] = read(m[k]); return b; }
  function status(text, state) {
    try { parent.postMessage({ type: "cb-cells-status", text: text, state: state || "" }, "*"); } catch (e) { /* no parent */ }
  }
  function apply(cells, sc) {
    var m = keyed();
    if (!init) init = snap(m);
    applying = true;
    try {
      if (sc !== scope) {                  // another line / period: its typed entries go
        for (var k0 in applied) if (!(k0 in cells) && m[k0] && init[k0] != null) {
          if (!(isForm(m[k0]) && m[k0].readOnly && !m[k0].hasAttribute("data-cb-lock"))) write(m[k0], init[k0]);
        }
        dirty = {};
      }
      for (var k in cells) {
        var el = m[k];
        // MES has a value there (read-only, not by our lock): MES wins
        if (!el || (isForm(el) && el.readOnly && !el.hasAttribute("data-cb-lock"))) continue;
        if (read(el) !== cells[k]) write(el, cells[k]);
      }
    } finally { applying = false; }
    scope = sc; applied = cells;
    base = snap(keyed());
  }
  function mark(el) {
    if (applying || !el) return;
    var m = keyed();
    for (var k in m) if (m[k] === el) { dirty[k] = el; break; }
    clearTimeout(timer); timer = setTimeout(flush, 600);
  }
  function markAllMarks() {                // a button changed many marks at once (all safe / reset)
    if (applying) return;
    var m = keyed();
    for (var k in m) if (k.charAt(0) === "k" && read(m[k]) !== base[k]) dirty[k] = m[k];
    clearTimeout(timer); timer = setTimeout(flush, 600);
  }
  function flush() {
    var ch = {}, any = false;
    for (var k in dirty) {
      var el = dirty[k];
      if (!el.isConnected || (isForm(el) && el.readOnly)) continue;
      var v = read(el);
      if (v !== base[k]) { ch[k] = v; any = true; }
    }
    dirty = {};
    if (!any) return;
    if (!canSave) { status(scope ? "Not saved: read-only" : "Not saved: pick a line in the Control Board filter", "error"); return; }
    var s = ++seq, m2 = keyed(), dts = {};
    for (var k3 in ch) dts[k3] = m2[k3] ? dateOf(m2[k3]) : "";
    pend[s] = ch;
    status("Saving…", "busy");
    parent.postMessage({ type: "cb-cells-save", seq: s, changes: ch, dates: dts, scope: scope }, "*");
  }
  document.addEventListener("input", function (e) {
    var t = e.target;
    if (!t || skip(t)) return;
    if (isForm(t)) mark(t);
    else { var ce = t.closest && t.closest("[contenteditable]"); if (ce && isEdit(ce)) mark(ce); }
  }, true);
  document.addEventListener("change", function (e) { var t = e.target; if (t && isForm(t) && !skip(t) && !t.classList.contains("cb-sel")) mark(t); }, true);
  document.addEventListener("click", function (e) {      // bubble phase: after the sheet's own onclick
    var t = e.target, el = t && t.closest ? t.closest("[onclick]") : null;
    if (!el) return;
    // a button (toolbar too: "Mark all safe", reset) may change many marks at once
    if (/^(BUTTON|A)$/.test(el.tagName) || el.closest("button")) { setTimeout(markAllMarks, 0); return; }
    if (skip(el)) return;
    mark(el);
  });
  // the sheet's own Clear button (removed 2026-11-13; the server refuses a clear now)
  if (false && typeof window.clearSheet === "function") {
    var clear0 = window.clearSheet;
    window.clearSheet = function () {
      var conf0 = window.confirm, said = false;
      window.confirm = function (msg) { said = conf0.call(window, msg); return said; };
      try { clear0.apply(this, arguments); } finally { window.confirm = conf0; }
      if (said && canSave) {
        var s = ++seq;
        pend[s] = null;
        status("Clearing…", "busy");
        parent.postMessage({ type: "cb-cells-save", seq: s, changes: {}, clear: true, scope: scope }, "*");
      }
    };
  }
  window.addEventListener("message", function (e) {
    var d = e.data;
    if (!d || e.source !== window.parent) return;
    if (d.type === "cb-cells") {
      canSave = !!d.canSave;
      today = /^\\d{4}-\\d{2}-\\d{2}$/.test(d.today || "") ? d.today : "";
      setOn = d.set || {};
      open = d.open !== false;
      admin = !!d.admin;
      P = pinfo(d.period, d.pkind);
      apply(d.cells || {}, String(d.scope || ""));
      lockAll();
    } else if (d.type === "cb-cells-ack") {
      var ch = pend[d.seq];
      delete pend[d.seq];
      if (d.ok) {
        if (ch) for (var k in ch) { base[k] = ch[k]; if (today && String(ch[k]).trim()) setOn[k] = setOn[k] || today; }
        status("Saved", "done");
      } else if (d.locked) {                  // the day rule said no: put the saved value back
        status("Not saved: " + (d.error || "locked"), "error");
        if (ch) {
          var m1 = keyed();
          applying = true;
          try { for (var k1 in ch) if (m1[k1] && base[k1] != null) write(m1[k1], base[k1]); }
          finally { applying = false; }
          lockAll();
        }
      } else {
        status("Not saved: " + (d.error || "error"), "error");
        if (ch) {                             // try again once after 5 s
          var m = keyed();
          for (var k2 in ch) if (m[k2] && !retry[k2]) { retry[k2] = 1; dirty[k2] = m[k2]; }
          clearTimeout(timer); timer = setTimeout(function () { flush(); retry = {}; }, 5000);
        }
      }
    }
  });
})();
</script>
"""

# 2026-11-13 (operator: manual entries only for today): which day each box of
# these sheets belongs to, for CELLS_SCRIPT's lock.  "" = the whole period.
DATE_5S = """
<script>
(function () {
  // day cells = the last 31 cells of a row; VERIFIED BY = weekly boxes "Week n"
  window.cbDateOf = function (el, P) {
    if (!P.y || !P.m) return "";
    var dim = new Date(P.y, P.m, 0).getDate(), wk = /^Week (\\d)$/.exec(el.placeholder || "");
    if (wk) {
      var a = 7 * (+wk[1]) - 6, b = +wk[1] === 5 ? dim : Math.min(dim, 7 * (+wk[1]));
      return a > dim ? "X" : "W:" + cbYmd(P.y, P.m, a) + ":" + cbYmd(P.y, P.m, b);
    }
    var td = el.tagName === "TD" ? el : (el.closest && el.closest("td")), tr = td && td.parentNode;
    if (!tr || !document.getElementById("checkSheetTable").contains(tr)) return "";
    var tds = tr.children, n = tds.length, i = [].indexOf.call(tds, td);
    if (n < 32 || i < n - 31) return "";
    var d = i - (n - 31) + 1;
    return d <= dim ? cbYmd(P.y, P.m, d) : "X";
  };
})();
</script>
"""
DATE_SAFETY = """
<script>
(function () {
  // the chart is a cross of month blocks without month tags: a day box belongs to
  // the nearest month label in its row band (DEC / APR / NOV / MAY / OCT / JUN at
  // the sides) or its column band (JAN FEB MAR above, SEP AUG JULY below).
  // Checked: every month must get exactly its days, else every box stays locked.
  var MON = { JAN: 1, FEB: 2, MAR: 3, APR: 4, MAY: 5, JUN: 6, JUL: 7, JULY: 7, AUG: 8, SEP: 9, OCT: 10, NOV: 11, DEC: 12 };
  var NEED = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31], map = null;
  function build() {
    var body = document.getElementById("safetyBody");
    if (!body) return null;
    var labs = [];
    [].forEach.call(body.querySelectorAll("td"), function (td) {
      var t = td.textContent.replace(/[\\s.]/g, "").toUpperCase();
      if (MON[t] && !td.querySelector(".day-box")) labs.push({ m: MON[t], r: td.getBoundingClientRect() });
    });
    var out = new Map(), cnt = {};
    [].forEach.call(body.querySelectorAll(".day-box"), function (b) {
      var r = b.getBoundingClientRect(), cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2, best = 0, bd = 1e9;
      labs.forEach(function (L) {
        var d = null;
        if (cy >= L.r.top && cy <= L.r.bottom) d = cx < L.r.left ? L.r.left - cx : (cx > L.r.right ? cx - L.r.right : 0);
        else if (cx >= L.r.left && cx <= L.r.right) d = cy < L.r.top ? L.r.top - cy : cy - L.r.bottom;
        if (d !== null && d < bd) { bd = d; best = L.m; }
      });
      var day = parseInt(b.textContent, 10);
      if (best && day) { out.set(b, [best, day]); cnt[best] = (cnt[best] || 0) + 1; }
    });
    for (var m = 1; m <= 12; m++) if (cnt[m] !== NEED[m]) { console.error("safety chart: month map failed", JSON.stringify(cnt)); return null; }
    return out;
  }
  window.cbDateOf = function (el, P) {
    if (!el.classList || !el.classList.contains("day-box")) return "";
    if (!map) map = build();
    if (!map || !P.y) return "X";
    var md = map.get(el);
    return md ? cbYmd(P.y, md[0], md[1]) : "X";
  };
})();
</script>
"""
DATE_Q = """
<script>
(function () {
  // bubbles carry their day; the month is their month-container.  Financial
  // year: April-December in the first year, January-March in the next.
  var M = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
           "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"];
  window.cbDateOf = function (el, P) {
    if (!el.classList || !el.classList.contains("bubble")) return "";
    var mc = el.closest(".month-container"), m = mc ? M.indexOf(String(mc.getAttribute("data-month") || "").toUpperCase()) + 1 : 0;
    var d = +el.getAttribute("data-day");
    if (!m || !d || !P.y0) return "X";
    var y = m >= 4 ? P.y0 : P.y0 + 1;
    return d <= new Date(y, m, 0).getDate() ? cbYmd(y, m, d) : "X";
  };
})();
</script>
"""


def _sel(title, four=False):
    return SELECT_SCRIPT.replace("__CFG__", _json.dumps({"title": title, "four": four}))
EXTRA = {
    "track-record-sheet.html": TRACK_SCRIPT,
    "4m-change-control-sheet.html": _sel("4M Change Control Sheet", True) + FOUR_STYLE + FOUR_SCRIPT,
    "operator-status.html": _sel("Operator Status") + OPS_STYLE + OPS2_STYLE + OPS2_SCRIPT,
    "defect-recording-sheet.html": _sel("Defect Recording Sheet") + DEFECT_SCRIPT,
    "defect-recording-sheet-recliner.html": _sel("Defect Recording Sheet - Recliner") + DEFECT_SCRIPT,
}
# 2026-11-12 (operator): every sheet without its own saving auto-saves what is typed / clicked
for _f in ("track-record-sheet.html", "defect-recording-sheet.html", "defect-recording-sheet-recliner.html",
           "annual-safety-chart.html", "q-chart.html", "5s-daily-check-sheet.html"):
    EXTRA[_f] = EXTRA.get(_f, "") + CELLS_SCRIPT
# 2026-11-13: the day of each box for the today-only lock
EXTRA["5s-daily-check-sheet.html"] += DATE_5S
EXTRA["annual-safety-chart.html"] += DATE_SAFETY
EXTRA["q-chart.html"] += DATE_Q


def main(src_dir, dst_dir):
    for src, dst in SHEETS.items():
        with open(os.path.join(src_dir, src), encoding="utf-8") as f:
            html = f.read()
        html, n = LOGO_BOX.subn(lambda m: LOGO_HTML, html)
        want = 0 if dst in NO_LOGO_BOX else 1
        if n != want:
            raise SystemExit("%s: expected %d logo box, found %d" % (src, want, n))
        if html.count(PRINT_BTN) == 1:
            html = html.replace(PRINT_BTN, PDF_BTNS)
        elif html.count(PRINT_BTN_ALT) == 1:
            html = html.replace(PRINT_BTN_ALT, PDF_BTNS_ALT)
        else:
            raise SystemExit("%s: expected 1 print button" % src)
        for old, new in SHEET_FIXES.get(dst, []):
            if html.count(old) != 1:
                raise SystemExit("%s: expected 1 of %r, found %d" % (src, old, html.count(old)))
            html = html.replace(old, new)
        html = apply_blocks(dst, html, src)
        if MARK not in html:
            add = ('\n<script src="cb-pdf.js?v=%s"></script>\n' % pdf_scripts()[2]) + ZOOM_SCRIPT + EXTRA.get(dst, "")
            i = html.lower().rfind("</body>")
            html = (html[:i] + add + html[i:]) if i >= 0 else (html + add)
        with open(os.path.join(dst_dir, dst), "w", encoding="utf-8") as f:
            f.write(html)
        print("installed", dst)
    side, render, _v = pdf_scripts()
    for js_name, js in zip(PDF_JS, (side, render)):
        with open(os.path.join(dst_dir, js_name), "w", encoding="utf-8") as f:
            f.write(js)
        print("installed", js_name)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
