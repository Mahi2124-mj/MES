/* MES Control Board: "Save PDF" — the sheet saved straight to the device.
 *
 * 2026-11-06.  Every Control Board document (operator sheets + line layouts)
 * loads this file (copied next to them by install_sheets.py /
 * xlsx_layout_to_html.py, with cb-pdf-render.js which does the drawing).
 *
 * Inside the Control Board the sheet is a sandboxed frame: it may not start a
 * download, and html2canvas cannot work there (it copies the page into its own
 * frame, which the sandbox blocks).  So the sheet sends the page a SNAPSHOT
 * ({type:"cb-pdf-snapshot", html, name, own}); the Control Board draws it in a
 * hidden script-less frame, makes the PDF and saves it.  Opened on its own,
 * the sheet does the same in its own hidden frame and saves the PDF itself.
 *
 * The snapshot is static HTML: no scripts, no fit-to-frame zoom, canvases as
 * images, and every input / dropdown / text box replaced by plain text in a box
 * of the same size and font, centred like the browser shows it.  (html2canvas
 * guesses where text inside an <input> goes, and the guess depends on the
 * screen's fonts: on one laptop all values printed low and cut off.  Plain
 * text is placed by the browser's own layout, the same on every screen.)
 *
 * The page asks for it with {type:"cb-pdf-request"} (its own Save PDF button).
 * window.cbPdfName() (optional, set by a sheet) names the file, e.g. the Track
 * Record Sheet adds its line and month.
 */
(function () {
  var inFrame = window.parent && window.parent !== window, busy = false;

  function today() {
    var d = new Date(), p = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  }
  function ownName() {
    try { return typeof window.cbPdfName === "function" ? (window.cbPdfName() || "") : ""; } catch (x) { return ""; }
  }
  function fileName() {
    var n = ownName() || (String(document.title || "Control Board").split(" - TOYOTA")[0] + " " + today());
    return n.replace(/[\\/:*?"<>|]+/g, "-").replace(/\s+/g, " ").trim() + ".pdf";
  }

  var JUSTIFY = { left: "flex-start", start: "flex-start", right: "flex-end", end: "flex-end", center: "center", justify: "flex-start" };
  // a plain-text box standing in for a form control: same size, font, colour, padding
  function textBox(x, text) {
    var cs = getComputedStyle(x), r = x.getBoundingClientRect(), d = document.createElement("div");
    d.className = x.className;
    var multi = x.tagName === "TEXTAREA", inl = /^inline/.test(cs.display);
    var st = [   // inline controls (Operator Status "MONTH :- [Nov 2026]") stay on the label's line
      "display:" + (cs.display === "none" ? "none" : (multi ? (inl ? "inline-block" : "block") : (inl ? "inline-flex" : "flex"))),
      "align-items:center", "justify-content:" + (JUSTIFY[cs.textAlign] || "center"),
      "box-sizing:border-box", "width:" + r.width + "px", "height:" + r.height + "px",
      "min-height:0", "margin:" + cs.margin, "padding:" + cs.padding,
      "border-style:" + cs.borderStyle, "border-width:" + cs.borderWidth, "border-color:" + cs.borderColor,
      "background:" + cs.backgroundColor, "color:" + cs.color,
      "font-family:" + cs.fontFamily, "font-size:" + cs.fontSize, "font-weight:" + cs.fontWeight,
      "font-style:" + cs.fontStyle, "letter-spacing:" + cs.letterSpacing,
      "line-height:1.15", "text-align:" + cs.textAlign, "overflow:hidden",
      "white-space:" + (multi ? "pre-wrap" : "nowrap"), "vertical-align:" + cs.verticalAlign
    ];
    if (cs.position !== "static") st.push("position:" + cs.position, "top:" + cs.top, "left:" + cs.left, "right:" + cs.right, "bottom:" + cs.bottom);
    d.setAttribute("style", st.join(";"));
    d.textContent = text;
    return d;
  }

  function snapshot() {
    var root = document.documentElement, zoom = root.style.zoom;
    root.style.zoom = "";                                 // measure at the sheet's own size (no repaint in between)
    // 2026-11-10 — screen-only states (today's column, a date filter that hides
    // rows) are dropped for the PDF: off while measuring + cloning, back after
    var scr = [].slice.call(document.querySelectorAll(".cb-screen"));
    scr.forEach(function (e) { e.classList.remove("cb-screen"); });
    try {
      var cl = root.cloneNode(true);
      var a = root.querySelectorAll("input, textarea, select, canvas");
      var b = cl.querySelectorAll("input, textarea, select, canvas");
      for (var i = 0; i < a.length && i < b.length; i++) {
        var x = a[i], y = b[i], tag = x.tagName, rep = null;
        if (tag === "CANVAS") {
          rep = document.createElement("img");
          try { rep.src = x.toDataURL("image/png"); } catch (e) { /* tainted: leave blank */ }
          rep.className = x.className;
          rep.setAttribute("style", (x.getAttribute("style") || "") + ";width:" + x.width + "px;height:" + x.height + "px");
          rep.setAttribute("width", x.width); rep.setAttribute("height", x.height);
        } else if (tag === "SELECT") {
          var o = x.options[x.selectedIndex];
          rep = textBox(x, o && o.value !== "" ? o.text : "");
        } else if (tag === "TEXTAREA") {
          rep = textBox(x, x.value);
        } else if (x.type === "checkbox" || x.type === "radio") {
          if (x.checked) y.setAttribute("checked", ""); else y.removeAttribute("checked");
        } else if (x.type !== "hidden" && x.type !== "button" && x.type !== "submit" && x.type !== "file") {
          rep = textBox(x, x.value);
        }
        if (rep) y.parentNode.replaceChild(rep, y);
      }
      [].slice.call(cl.querySelectorAll("script")).forEach(function (s) { s.parentNode.removeChild(s); });
      cl.style.zoom = "";
      var head = cl.querySelector("head");
      if (head) { var base = document.createElement("base"); base.href = location.href; head.insertBefore(base, head.firstChild); }
      return "<!DOCTYPE html>\n" + cl.outerHTML;
    } finally {
      scr.forEach(function (e) { e.classList.add("cb-screen"); });
      root.style.zoom = zoom;
    }
  }

  function btns(on) {
    [].slice.call(document.querySelectorAll("button.cb-pdf-btn")).forEach(function (b) {
      if (on) { b.disabled = true; b.dataset.t = b.textContent; b.textContent = "Saving PDF…"; }
      else { b.disabled = false; b.textContent = b.dataset.t || "Save PDF"; }
    });
  }
  function loadRender() {
    if (window.cbPdf) return Promise.resolve();
    return new Promise(function (res, rej) {
      var s = document.createElement("script");
      s.src = "cb-pdf-render.js?v=8fe42235"; s.onload = res;   // version filled in when installed
      s.onerror = function () { rej(new Error("Could not load cb-pdf-render.js")); };
      document.head.appendChild(s);
    });
  }
  // opened on its own: draw the snapshot in a hidden script-less frame, save
  function saveHere() {
    busy = true; btns(true);
    var html = snapshot(), fr = null;
    loadRender().then(function () {
      fr = document.createElement("iframe");
      fr.setAttribute("sandbox", "allow-same-origin");
      fr.style.cssText = "position:fixed;left:-30000px;top:0;width:2000px;height:3000px;border:0;";
      var loaded = new Promise(function (res) { fr.onload = res; });
      fr.srcdoc = html; document.body.appendChild(fr);
      return loaded;
    }).then(function () { return window.cbPdf.render(fr.contentDocument, fileName().replace(/\.pdf$/, "")); })
      .then(function (buf) {
        var url = URL.createObjectURL(new Blob([buf], { type: "application/pdf" }));
        var a = document.createElement("a");
        a.href = url; a.download = fileName(); a.style.display = "none";
        document.body.appendChild(a); a.click();
        setTimeout(function () { URL.revokeObjectURL(url); a.parentNode && a.parentNode.removeChild(a); }, 4000);
      })
      .catch(function (e) { alert("PDF not saved: " + (e.message || e)); })
      .then(function () { if (fr && fr.parentNode) fr.parentNode.removeChild(fr); busy = false; btns(false); });
  }

  window.cbSavePdf = function () {
    if (busy) return;
    if (!inFrame) { saveHere(); return; }
    try { window.parent.postMessage({ type: "cb-pdf-snapshot", html: snapshot(), name: fileName(), own: !!ownName() }, "*"); }
    catch (e) { try { window.parent.postMessage({ type: "cb-pdf-status", state: "error", error: String(e.message || e) }, "*"); } catch (x) { /* gone */ } }
  };

  window.addEventListener("message", function (e) {
    if (e.source === window.parent && e.data && e.data.type === "cb-pdf-request") window.cbSavePdf();
  });
})();
