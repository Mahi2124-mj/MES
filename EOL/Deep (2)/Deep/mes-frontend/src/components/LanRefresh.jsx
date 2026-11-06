// LAN panel app only (getApkInfo().isLan) — the Refresh button.
//
// 2026-11-03 — the button was one fixed copy in the top-right corner of every
// page, so it sat on top of whatever a page keeps there (the Dashboard search
// box and zone filter, "Last fetched", the "Signed in as" pill).  Pages with a
// header now give it a place inside that header with <LanRefreshSlot />, in
// line with the header's own controls.  The fixed corner copy (App.jsx) shows
// only on pages that offer no slot, e.g. the TV walls, whose headers were laid
// out around it.  On the website a slot renders nothing.
import { useEffect, useState, useSyncExternalStore } from "react";
import { getApkInfo } from "../api/appVersion";

let slotCount = 0;
const subs = new Set();
const subscribe = (fn) => { subs.add(fn); return () => subs.delete(fn); };
const emit = () => subs.forEach((fn) => fn());

/** Number of header slots mounted right now (0 = use the fixed corner copy). */
export const useLanRefreshSlots = () => useSyncExternalStore(subscribe, () => slotCount);

const isLanApp = () => { try { return getApkInfo().isLan; } catch { return false; } };

export function LanRefreshBtn({ style }) {
  const [spin, setSpin] = useState(false);
  return (
    <button
      onClick={() => { setSpin(true); try { window.location.reload(); } catch {} }}
      title="Refresh"
      aria-label="Refresh"
      style={{
        height: 30, padding: "0 14px", borderRadius: 8,
        border: "1px solid #475569",
        background: "#334155", color: "#e2e8f0",
        fontFamily: "inherit", fontSize: 13, fontWeight: 600, letterSpacing: ".02em", lineHeight: 1,
        cursor: "pointer", whiteSpace: "nowrap", flexShrink: 0,
        display: "flex", alignItems: "center", justifyContent: "center",
        boxShadow: "0 1px 4px rgba(0,0,0,.3)",
        ...style,
      }}>
      {spin ? "Refreshing…" : "Refresh"}
    </button>
  );
}

/** Place for the Refresh button inside a page header (LAN app only). */
export function LanRefreshSlot({ style }) {
  const lan = isLanApp();
  useEffect(() => {
    if (!lan) return undefined;
    slotCount += 1; emit();
    return () => { slotCount -= 1; emit(); };
  }, [lan]);
  return lan ? <LanRefreshBtn style={style} /> : null;
}
