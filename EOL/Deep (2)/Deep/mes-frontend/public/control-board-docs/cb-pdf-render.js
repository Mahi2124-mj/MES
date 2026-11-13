/* MES Control Board: draw a sheet into a one-page PDF.  2026-11-06.
 *
 * window.cbPdf.render(doc, title) -> Promise<ArrayBuffer>: draws the sheet of
 * `doc` (a same-origin, script-less copy of a sheet — see cb-pdf.js) with
 * html2canvas and makes a one-page PDF with jsPDF.
 *
 * Page = the sheet only (operator: no white space above / below): the drawing
 * is cropped to the sheet's edges, scaled as it would sit on A3 (portrait or
 * landscape, whichever shows it larger), and the page is cut to exactly that
 * size — the long side is A3 (420 mm) or the short side is (297 mm).  Printing
 * on A3 with "fit to page" fills the paper the same way.
 * Libraries: /html2canvas.min.js and /jspdf.umd.min.js (MES public folder),
 * loaded on first use.  Used by cb-pdf.js (sheet side) and the Control Board.
 */
(function () {
  if (window.cbPdf) return;
  var A3 = { w: 420, h: 297 };                           // mm
  var libsP = null;
  function load(src) {
    return new Promise(function (res, rej) {
      var s = document.createElement("script");
      s.src = src; s.onload = res;
      s.onerror = function () { rej(new Error("Could not load " + src)); };
      document.head.appendChild(s);
    });
  }
  function libs() {
    if (window.html2canvas && window.jspdf) return Promise.resolve();
    if (!libsP) libsP = load("/html2canvas.min.js").then(function () { return load("/jspdf.umd.min.js"); })
      .catch(function (e) { libsP = null; throw e; });
    return libsP;
  }
  function target(doc) { return doc.querySelector(".sheet-wrapper, .sheet-container, .sheet") || doc.body; }

  // crop the canvas to what is drawn (anything not near-white), keeping `pad` px
  function trim(cv, pad) {
    var w = cv.width, h = cv.height, d = cv.getContext("2d").getImageData(0, 0, w, h).data;
    function ink(i) { return d[i] < 240 || d[i + 1] < 240 || d[i + 2] < 240; }
    function rowInk(y) { for (var i = y * w * 4, e = i + w * 4; i < e; i += 4) if (ink(i)) return true; return false; }
    function colInk(x, y0, y1) { for (var y = y0; y <= y1; y++) if (ink((y * w + x) * 4)) return true; return false; }
    var t = 0, b = h - 1, l = 0, r = w - 1;
    while (t < b && !rowInk(t)) t++;
    while (b > t && !rowInk(b)) b--;
    while (l < r && !colInk(l, t, b)) l++;
    while (r > l && !colInk(r, t, b)) r--;
    t = Math.max(0, t - pad); l = Math.max(0, l - pad); b = Math.min(h - 1, b + pad); r = Math.min(w - 1, r + pad);
    if (t === 0 && l === 0 && b === h - 1 && r === w - 1) return cv;
    var out = document.createElement("canvas");
    out.width = r - l + 1; out.height = b - t + 1;
    var ctx = out.getContext("2d");
    ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, out.width, out.height);
    ctx.drawImage(cv, l, t, out.width, out.height, 0, 0, out.width, out.height);
    return out;
  }

  window.cbPdf = {
    target: target,
    render: function (doc, title) {
      var el = target(doc), root = doc.documentElement, scale = 2;
      return libs().then(function () {
        var w = el.scrollWidth, h = el.scrollHeight;
        scale = Math.max(1, Math.min(2, 4000 / Math.max(w, h)));
        return window.html2canvas(el, {
          scale: scale, backgroundColor: "#ffffff", logging: false, useCORS: true,
          width: w, height: h,
          windowWidth: Math.max(w + 40, root.scrollWidth), windowHeight: Math.max(h + 40, root.scrollHeight),
          scrollX: 0, scrollY: 0,
          onclone: function (cd) {
            // the sheet's drop shadow is screen decoration, not part of the sheet
            var t = target(cd);
            if (t && t.style) t.style.boxShadow = "none";
            // html2canvas draws an <svg> as an image of its markup, style included:
            // a positioned svg (Track Record arrow) then moves its own drawing out
            // of that image and prints blank.  Here the position goes to a wrapper.
            [].slice.call(cd.querySelectorAll("svg")).forEach(function (sv) {
              var st = sv.getAttribute("style") || "";
              if (!/position\s*:\s*(absolute|fixed|relative)/i.test(st)) return;
              var sw = parseFloat(sv.getAttribute("width")), sh = parseFloat(sv.getAttribute("height"));
              var box = cd.createElement("div");
              box.setAttribute("style", st + ";line-height:0;" + (sw ? "width:" + sw + "px;" : "") + (sh ? "height:" + sh + "px;" : ""));
              sv.parentNode.insertBefore(box, sv);
              box.appendChild(sv);
              sv.setAttribute("style", "display:block;overflow:visible");
            });
          },
          ignoreElements: function (n) {
            return !!(n.classList && (n.classList.contains("toolbar") || n.classList.contains("no-print")));
          }
        });
      }).then(function (cv) {
        cv = trim(cv, Math.round(3 * scale));
        var fitL = Math.min(A3.w / cv.width, A3.h / cv.height);     // mm per px, landscape A3
        var fitP = Math.min(A3.h / cv.width, A3.w / cv.height);     // portrait A3
        var s = Math.max(fitL, fitP);
        var pw = Math.round(cv.width * s * 10) / 10, ph = Math.round(cv.height * s * 10) / 10;
        var pdf = new window.jspdf.jsPDF({ orientation: pw >= ph ? "landscape" : "portrait", unit: "mm", format: [pw, ph], compress: true });
        pdf.setProperties({ title: title || "Control Board", creator: "TBDI MES Control Board" });
        pdf.addImage(cv.toDataURL("image/jpeg", 0.92), "JPEG", 0, 0, pw, ph);
        return pdf.output("arraybuffer");
      });
    }
  };
})();
