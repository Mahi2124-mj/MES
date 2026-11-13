// utils/cbPdf.js — save a page snapshot as a one-page PDF (2026-11-12).
//
// Moved out of pages/ControlBoard.jsx (6-Nov "Save PDF") so the Line Rejection
// report and Historical Data can use it too.  The HTML is drawn in a hidden
// frame that cannot run scripts (sandbox allow-same-origin only) and turned
// into one A3-scaled page by control-board-docs/cb-pdf-render.js (html2canvas +
// jsPDF).  The Android app's WebView cannot save a file the page makes, so
// there the PDF is uploaded to /api/control-board/pdf and its 10-minute link
// opened, and the phone's browser saves it to Downloads.
import { getApkInfo } from "../api/appVersion";

export const DOC_BASE = "/control-board-docs/";
let renderLib = null;
// serve_prod lets browsers keep scripts for an hour: fetch it fresh once per page load
const RENDER_V = Date.now().toString(36);
export function loadRender() {
  if (window.cbPdf) return Promise.resolve();
  if (!renderLib) {
    renderLib = new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = `${DOC_BASE}cb-pdf-render.js?v=${RENDER_V}`; s.onload = res;
      s.onerror = () => { renderLib = null; rej(new Error("could not load the PDF tool")); };
      document.head.appendChild(s);
    });
  }
  return renderLib;
}
export async function snapshotPdf(html, title) {
  await loadRender();
  const fr = document.createElement("iframe");
  fr.setAttribute("sandbox", "allow-same-origin");
  fr.setAttribute("aria-hidden", "true");
  fr.style.cssText = "position:fixed;left:-30000px;top:0;width:2000px;height:3000px;border:0;";
  const loaded = new Promise((res) => { fr.onload = res; });
  fr.srcdoc = html;
  document.body.appendChild(fr);
  try {
    await loaded;
    // images (logo, signatures) must be drawn before the snapshot
    await Promise.all([...fr.contentDocument.images].map(im => (im.complete ? null
      : new Promise(r => { im.onload = r; im.onerror = r; setTimeout(r, 4000); }))));
    return await window.cbPdf.render(fr.contentDocument, title);
  } finally { fr.remove(); }
}
export const safeName = (s) => String(s || "").replace(/[\\/:*?"<>|]+/g, "-").replace(/\s+/g, " ").trim();
export function saveFile(buf, name) {
  const url = URL.createObjectURL(new Blob([buf], { type: "application/pdf" }));
  const a = document.createElement("a");
  a.href = url; a.download = name; a.style.display = "none";
  document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 4000);
}
export async function appDownload(buf, name, token) {
  const r = await fetch(`/api/control-board/pdf?name=${encodeURIComponent(name)}`, {
    method: "POST", body: buf,
    headers: { "Content-Type": "application/pdf", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  if (!r.ok) throw new Error(r.status === 404 ? "available after the next MES-API restart" : `upload failed (${r.status})`);
  const { url } = await r.json();
  const a = document.createElement("a");
  a.href = url; a.style.display = "none";
  document.body.appendChild(a); a.click();
  setTimeout(() => a.remove(), 3000);
}
export const inWebViewApp = () => {
  try { getApkInfo(); return sessionStorage.getItem("tbdi_src") === "capacitor"; } catch { return false; }
};
// render + save (browser) or upload + open (app); resolves to the note to show
export async function savePdfFile(html, name, token) {
  let file = safeName(name);
  if (!file.toLowerCase().endsWith(".pdf")) file += ".pdf";
  const buf = await snapshotPdf(html, file.replace(/\.pdf$/i, ""));
  if (inWebViewApp()) {
    await appDownload(buf, file, token);
    return "PDF opened in the browser — saved to Downloads";
  }
  saveFile(buf, file);
  return `Saved ${file}`;
}
