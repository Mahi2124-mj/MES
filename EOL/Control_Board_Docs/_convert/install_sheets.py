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
import os, sys

SHEETS = {
    "1_track-record-sheet.html": "track-record-sheet.html",
    "2_4M_Change_Control_Sheet_Editable.html": "4m-change-control-sheet.html",
    "3_Operator_status.html": "operator-status.html",
    "4_defect_record_sheet.html": "defect-recording-sheet.html",
    "4_defect_record_rec.html": "defect-recording-sheet-recliner.html",
    "5_safety_chart.html": "annual-safety-chart.html",
}

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

# Track Record Sheet only (2026-11-05): LINE NAME and MONTH become dropdowns and
# the Cycle Time / Target boxes of both shifts fill from Admin → Standard Data →
# Track Record.  The sheet is sandboxed (no login, no API), so the Control Board
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
  function fill() {
    var id = lineSel ? lineSel.value : "", row = null;
    for (var i = 0; i < data.length; i++) if (String(data[i].line_id) === id) row = data[i];
    ctIn.forEach(function (el) { el.value = row ? fmt(row.cycle_time) : ""; el.placeholder = row ? "not set" : ""; });
    tgIn.forEach(function (el) { el.value = row ? fmt(row.target) : ""; el.placeholder = row ? "not set" : ""; });
  }
  function buildLine(lines, pick) {
    if (!lineTd) return;
    if (!lineSel) {
      lineSel = document.createElement("select"); lineSel.className = "cb-sel";
      lineSel.addEventListener("change", fill);
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
  });
  // Clear keeps the line, month, cycle time and target (the original kept the two defaults).
  window.clearSheet = function () {
    if (!confirm("Clear all entered data? Line, month, cycle time and target stay.")) return;
    ins.forEach(function (el) { if (ctIn.indexOf(el) < 0 && tgIn.indexOf(el) < 0) el.value = ""; });
    if (window.drawShiftGraph) { drawShiftGraph("A"); drawShiftGraph("B"); }
  };
})();
</script>
"""
EXTRA = {"track-record-sheet.html": TRACK_SCRIPT}


def main(src_dir, dst_dir):
    for src, dst in SHEETS.items():
        with open(os.path.join(src_dir, src), encoding="utf-8") as f:
            html = f.read()
        if MARK not in html:
            add = ZOOM_SCRIPT + EXTRA.get(dst, "")
            i = html.lower().rfind("</body>")
            html = (html[:i] + add + html[i:]) if i >= 0 else (html + add)
        with open(os.path.join(dst_dir, dst), "w", encoding="utf-8") as f:
            f.write(html)
        print("installed", dst)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
