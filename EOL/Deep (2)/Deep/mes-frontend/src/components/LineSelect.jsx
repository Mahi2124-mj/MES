// LineSelect.jsx — 2026-11-10 (operator: "jahaan bhi line ke liye dropdown hai,
// usko hourly sheet ka jo dropdown hai uske jaisa kar de, bas work centre nahi").
//
// Drop-in for a LINE <select>: same props, same <option> children.  The control
// on the page stays the page's own <select> (its size and look do not change),
// but opening it shows the Hourly Sheet's picker instead of the browser list:
// zones as coloured headers with a count and ▸ / ▾, the zone holding the current
// line opened, its lines below with the zone's colour strip, hover blue, the
// current line green and bold.  No work centre codes.  Options that are not a
// line ("All lines", "— select —") stay at the top.  Picking sets the select's
// value and fires a real change event, so every page's onChange works as before.
import { Children, Fragment, isValidElement, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

// line master (zone of each line) — shared by every picker, refreshed every 5 min
let linesCache = null;
function loadLines(token) {
  if (linesCache && Date.now() - linesCache.at < 300000) return linesCache.promise;
  const promise = api.get("/api/lines/", token)
    .then(r => (Array.isArray(r) ? r : []))
    .catch(() => { linesCache = null; return []; });
  linesCache = { at: Date.now(), promise };
  return promise;
}

// same colours as the Hourly Sheet's work-centre panel (fixed per zone name)
const ZONE_TINT = [
  [/seat\s*slider/i, "#e0f2fe", "#38bdf8"], [/recliner/i, "#dcfce7", "#4ade80"],
  [/loop\s*pipe/i, "#fef3c7", "#f59e0b"], [/sub[\s-]*assem/i, "#ede9fe", "#a78bfa"],
  [/ywd|2ua/i, "#fce7f3", "#f472b6"], [/press/i, "#ffedd5", "#fb923c"],
  [/thin/i, "#ccfbf1", "#2dd4bf"],
];
const SPARE_TINT = [["#e0e7ff", "#818cf8"], ["#fae8ff", "#e879f9"], ["#ecfccb", "#a3e635"], ["#f1f5f9", "#94a3b8"]];

const CSS = `
.lsel-p{position:fixed;z-index:100000;overflow:auto;background:#fff;border:1px solid #b7c4d0;border-radius:6px;
  box-shadow:0 8px 26px rgba(0,0,0,.18);font:13px/1.45 Arial,Helvetica,sans-serif;color:#222;text-align:left;
  -webkit-overflow-scrolling:touch}
.lsel-h{display:flex;justify-content:space-between;gap:10px;padding:8px 10px;font-weight:bold;
  background:var(--z-bg,#f3f7fb);border-left:5px solid var(--z-bd,#cbd5e1);border-bottom:1px solid #e4eaf0;
  cursor:pointer;user-select:none}
.lsel-h:hover{filter:brightness(.96)}
.lsel-car{color:#5b6b7b;font-weight:normal}
.lsel-i{display:flex;justify-content:space-between;align-items:baseline;gap:14px;padding:6px 10px 6px 20px;
  border-bottom:1px solid #f2f5f8;border-left:5px solid var(--z-bd,transparent);cursor:pointer;white-space:nowrap}
.lsel-i.top{padding-left:10px}
.lsel-i:hover{background:#eaf3ff}
.lsel-i.sel{background:#ddf0dc;font-weight:bold}
.lsel-i.dis{color:#94a3b8;cursor:default;background:none}
`;
let cssDone = false;
function ensureCss() {
  if (cssDone || typeof document === "undefined") return;
  const s = document.createElement("style");
  s.setAttribute("data-line-select", "");
  s.textContent = CSS;
  document.head.appendChild(s);
  cssDone = true;
}

function textOf(n) {
  if (n == null || typeof n === "boolean") return "";
  if (typeof n === "string" || typeof n === "number") return String(n);
  if (Array.isArray(n)) return n.map(textOf).join("");
  if (isValidElement(n)) return textOf(n.props.children);
  return "";
}
function optionsOf(children, out = []) {
  Children.forEach(children, (c) => {
    if (!isValidElement(c)) return;
    if (c.type === "option") {
      const label = textOf(c.props.children).replace(/\s+/g, " ").trim();
      out.push({ value: c.props.value !== undefined && c.props.value !== null ? String(c.props.value) : label,
                 label, disabled: !!c.props.disabled });
    } else if (c.type === "optgroup" || c.type === Fragment) {
      optionsOf(c.props.children, out);
    }
  });
  return out;
}
const norm = (s) => String(s || "").toUpperCase().replace(/[^A-Z0-9]/g, "");

// zone of an option: its label is a line name, or its value is a line id whose
// name is in the label, or the longest line name found in the label
function zoneIndex(lines) {
  const byId = new Map(), byName = new Map(), names = [];
  lines.forEach((l) => {
    const z = { zone: l.zone_name || "Other lines", zoneId: l.zone_id == null ? 9999 : Number(l.zone_id) };
    byId.set(String(l.id), { ...z, name: norm(l.line_name) });
    const n = norm(l.line_name);
    if (n) { byName.set(n, z); names.push([n, z]); }
  });
  names.sort((a, b) => b[0].length - a[0].length);
  return (o) => {
    const lab = norm(o.label);
    if (!lab) return null;
    if (byName.has(lab)) return byName.get(lab);
    const i = byId.get(o.value);
    if (i && i.name && lab.includes(i.name)) return i;
    const hit = names.find(([n]) => n.length >= 4 && lab.includes(n));
    return hit ? hit[1] : null;
  };
}

export default function LineSelect({ children, onMouseDown, onKeyDown, onTouchStart, onTouchEnd, ...rest }) {
  const auth = useAuth() || {};
  const selRef = useRef(null);
  const panelRef = useRef(null);
  const hostRef = useRef(null);
  const touchRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [box, setBox] = useState(null);        // the select's rect when opened
  const [lines, setLines] = useState([]);
  const [openZone, setOpenZone] = useState(null);
  const [cur, setCur] = useState("");

  const opts = optionsOf(children);
  const zoneOf = zoneIndex(lines);
  const top = [], groups = [], gmap = new Map();
  opts.forEach((o) => {
    const z = zoneOf(o);
    if (!z) {
      // "All lines" / "— select —", or every option while the line master loads
      if (o.value === "" || o.value === "0" || lines.length === 0) top.push(o);
      else {                                  // a line we cannot place: its own group at the end
        if (!gmap.has("Other lines")) { const g = { zone: "Other lines", zoneId: 10000, items: [] }; gmap.set(g.zone, g); groups.push(g); }
        gmap.get("Other lines").items.push(o);
      }
      return;
    }
    if (!gmap.has(z.zone)) { const g = { zone: z.zone, zoneId: z.zoneId, items: [] }; gmap.set(z.zone, g); groups.push(g); }
    gmap.get(z.zone).items.push(o);
  });
  groups.sort((a, b) => (a.zoneId - b.zoneId) || a.zone.localeCompare(b.zone));
  let spare = 0;
  groups.forEach((g) => {
    const hit = ZONE_TINT.find(t => t[0].test(g.zone));
    [g.bg, g.bd] = hit ? [hit[1], hit[2]] : SPARE_TINT[(spare++) % SPARE_TINT.length];
  });

  const close = () => setOpen(false);
  const show = () => {
    const el = selRef.current;
    if (!el || el.disabled) return;
    ensureCss();
    if (!hostRef.current) {
      // outside <body>: a page that zooms the body would shift a fixed panel
      hostRef.current = document.createElement("div");
      document.documentElement.appendChild(hostRef.current);
    }
    setCur(String(el.value));
    setBox(el.getBoundingClientRect());
    setOpenZone(null);
    setOpen(true);
    loadLines(auth.token).then(setLines);
  };
  const toggle = () => (open ? close() : show());
  const pick = (o) => {
    close();
    const el = selRef.current;
    if (!el || o.disabled || String(el.value) === o.value) return;
    const setVal = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set;
    setVal.call(el, o.value);
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };

  useEffect(() => { loadLines(auth.token).then(setLines); }, [auth.token]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { if (hostRef.current) hostRef.current.remove(); }, []);
  // the zone of the current line starts open (or the only zone)
  const curZone = (groups.find(g => g.items.some(o => o.value === cur)) || {}).zone;
  const shownZone = openZone === null ? (curZone || (groups.length === 1 ? groups[0].zone : "")) : openZone;
  useEffect(() => {
    if (!open) return undefined;
    const away = (e) => {
      if (panelRef.current && panelRef.current.contains(e.target)) return;
      if (selRef.current && selRef.current.contains(e.target)) return;
      close();
    };
    const key = (e) => { if (e.key === "Escape") close(); };
    const scroll = (e) => { if (!(panelRef.current && panelRef.current.contains(e.target))) close(); };
    document.addEventListener("mousedown", away, true);
    document.addEventListener("touchstart", away, true);
    document.addEventListener("keydown", key, true);
    window.addEventListener("scroll", scroll, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", away, true);
      document.removeEventListener("touchstart", away, true);
      document.removeEventListener("keydown", key, true);
      window.removeEventListener("scroll", scroll, true);
      window.removeEventListener("resize", close);
    };
  }, [open]);
  // below the select; above it when there is no room below; inside the screen
  useLayoutEffect(() => {
    const p = panelRef.current;
    if (!open || !p || !box) return;
    const vh = window.innerHeight, vw = window.innerWidth;
    const h = p.offsetHeight, w = p.offsetWidth;
    let t = box.bottom + 4, l = box.left;
    if (t + h > vh - 8) t = Math.max(8, box.top - 4 - h);
    if (l + w > vw - 8) l = Math.max(8, vw - 8 - w);
    p.style.top = `${t}px`;
    p.style.left = `${l}px`;
    p.style.visibility = "visible";
  });

  const item = (o, g) => (
    <div key={`${g ? g.zone : "top"}:${o.value}`}
         className={`lsel-i${g ? "" : " top"}${o.value === cur ? " sel" : ""}${o.disabled ? " dis" : ""}`}
         style={g ? { "--z-bd": g.bd } : undefined}
         onClick={() => pick(o)}>
      <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{o.label || "—"}</span>
    </div>
  );

  return (
    <>
      <select ref={selRef} {...rest}
        onMouseDown={(e) => {
          if (onMouseDown) onMouseDown(e);
          if (e.button !== 0) return;
          e.preventDefault();                       // no browser list
          try { selRef.current.focus({ preventScroll: true }); } catch { /* old browser */ }
          toggle();
        }}
        onTouchStart={(e) => {
          if (onTouchStart) onTouchStart(e);
          const t = e.touches && e.touches[0];
          touchRef.current = t ? { x: t.clientX, y: t.clientY } : null;
        }}
        onTouchEnd={(e) => {
          if (onTouchEnd) onTouchEnd(e);
          const s = touchRef.current, t = e.changedTouches && e.changedTouches[0];
          touchRef.current = null;
          if (!s || !t || Math.abs(t.clientX - s.x) > 10 || Math.abs(t.clientY - s.y) > 10) return;   // a scroll, not a tap
          e.preventDefault();                       // no emulated mouse events → no browser list
          toggle();
        }}
        onKeyDown={(e) => {
          if (onKeyDown) onKeyDown(e);
          if (e.key === " " || e.key === "Enter" || e.key === "F4" || (e.altKey && e.key === "ArrowDown")) {
            e.preventDefault();
            toggle();
          }
        }}>
        {children}
      </select>
      {open && hostRef.current && createPortal(
        <div ref={panelRef} className="lsel-p" role="listbox"
             style={{ top: 0, left: 0, visibility: "hidden",
                      minWidth: Math.max(260, Math.round((box && box.width) || 0)),
                      maxWidth: "min(560px, calc(100vw - 16px))", maxHeight: "62vh" }}>
          {top.map(o => item(o, null))}
          {groups.map(g => (
            <Fragment key={g.zone}>
              <div className="lsel-h" style={{ "--z-bg": g.bg, "--z-bd": g.bd }}
                   onClick={() => setOpenZone(shownZone === g.zone ? "" : g.zone)}>
                <span>{g.zone} ({g.items.length})</span>
                <span className="lsel-car">{shownZone === g.zone ? "▾" : "▸"}</span>
              </div>
              {shownZone === g.zone && g.items.map(o => item(o, g))}
            </Fragment>
          ))}
        </div>, hostRef.current)}
    </>
  );
}
