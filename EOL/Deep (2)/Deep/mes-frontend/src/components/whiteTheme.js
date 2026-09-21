/* ════════════════════════════════════════════════════════════════════
 *  whiteTheme.js
 *  ────────────────────────────────────────────────────────────────────
 *  Shared light-theme tokens for all post-2026-04 pages (Shift
 *  Calculator, Kanban, Store, Dispatch, Anything-Wrong, Heijunka, 5S,
 *  PDCA, Shift Allocation).  Matches the existing AdminPanel / Dashboard
 *  aesthetic so the whole system feels visually consistent.
 *
 *  Palette
 *    page bg          #f8fafc
 *    card bg          #ffffff
 *    border           #e2e8f0
 *    primary text     #0f172a
 *    secondary text   #64748b
 *    muted text       #94a3b8
 *    accent blue      #2563eb / #1e40af
 *    success          #16a34a
 *    danger           #dc2626
 *    warning          #d97706
 * ════════════════════════════════════════════════════════════════════ */

export const COLORS = {
  bgPage:   "#f8fafc",
  bgCard:   "#ffffff",
  bgInput:  "#f8fafc",
  border:   "#e2e8f0",
  borderS:  "#f1f5f9",
  text:     "#0f172a",
  textSub:  "#475569",
  textMut:  "#64748b",
  textFade: "#94a3b8",
  accent:   "#2563eb",
  accentDk: "#1e40af",
  success:  "#16a34a",
  successL: "#dcfce7",
  danger:   "#dc2626",
  dangerL:  "#fee2e2",
  warning:  "#d97706",
  warningL: "#fef3c7",
  info:     "#0284c7",
  infoL:    "#dbeafe",
  purple:   "#7c3aed",
  purpleL:  "#ede9fe",
  pink:     "#db2777",
  pinkL:    "#fce7f3",
  cyan:     "#0891b2",
  cyanL:    "#cffafe",
};

/* ── Page shells ─────────────────────────────────────────────────── */
export const pageWrap = {
  minHeight: "100vh",
  background: COLORS.bgPage,
  fontFamily: "'Barlow',sans-serif",
  paddingBottom: 60,
  color: COLORS.text,
};

export const pageHeader = {
  padding: "20px 48px 16px",
  background: "#fff",
  borderBottom: `1px solid ${COLORS.border}`,
};

/* ── Cards ───────────────────────────────────────────────────────── */
export const cardStyle = {
  background: COLORS.bgCard,
  border:     `1px solid ${COLORS.border}`,
  borderRadius: 14,
  boxShadow:  "0 1px 3px rgba(0,0,0,.04)",
  overflow:   "hidden",
};

/* ── Inputs ──────────────────────────────────────────────────────── */
export const inputStyle = {
  padding: "9px 12px",
  fontSize: 13,
  background: COLORS.bgCard,
  border: `1.5px solid ${COLORS.border}`,
  borderRadius: 8,
  color: COLORS.text,
  outline: "none",
  width: "100%",
  fontFamily: "'Barlow',sans-serif",
  boxSizing: "border-box",
  transition: "border-color .15s, box-shadow .15s",
};

/* ── Buttons ─────────────────────────────────────────────────────── */
export const btnPrimary = {
  padding: "9px 18px",
  fontSize: 13,
  fontWeight: 700,
  background: `linear-gradient(135deg, ${COLORS.accentDk}, ${COLORS.accent})`,
  color: "#fff",
  border: "none",
  borderRadius: 8,
  cursor: "pointer",
  letterSpacing: ".02em",
  boxShadow: `0 2px 8px rgba(30,64,175,.3)`,
  fontFamily: "'Barlow',sans-serif",
};

export const btnGhost = {
  padding: "9px 16px",
  fontSize: 12,
  fontWeight: 600,
  background: COLORS.bgPage,
  color: COLORS.textSub,
  border: `1px solid ${COLORS.border}`,
  borderRadius: 8,
  cursor: "pointer",
  fontFamily: "'Barlow',sans-serif",
};

export const btnDanger = {
  padding: "6px 12px",
  fontSize: 11,
  fontWeight: 700,
  background: "rgba(220,38,38,.06)",
  color: COLORS.danger,
  border: `1px solid rgba(220,38,38,.3)`,
  borderRadius: 8,
  cursor: "pointer",
  fontFamily: "'Barlow',sans-serif",
};

/* ── Labels ──────────────────────────────────────────────────────── */
export const lblStyle = {
  fontSize: 10,
  fontWeight: 700,
  letterSpacing: ".1em",
  textTransform: "uppercase",
  color: COLORS.textMut,
};

/* ── Tables ──────────────────────────────────────────────────────── */
export const thStyle = {
  padding: "11px 10px",
  fontSize: 10,
  fontWeight: 700,
  letterSpacing: ".08em",
  textTransform: "uppercase",
  color: COLORS.textMut,
  textAlign: "left",
  background: COLORS.bgPage,
  borderBottom: `1px solid ${COLORS.border}`,
};

export const tdStyle = {
  padding: "9px 10px",
  fontSize: 12,
  color: COLORS.text,
  borderBottom: `1px solid ${COLORS.borderS}`,
};

/* ── Modals ──────────────────────────────────────────────────────── */
export const modalBackdrop = {
  position: "fixed",
  inset: 0,
  background: "rgba(15,23,42,.4)",
  backdropFilter: "blur(4px)",
  zIndex: 1000,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
};

export const modalBox = {
  background: COLORS.bgCard,
  border: `1px solid ${COLORS.border}`,
  borderRadius: 16,
  padding: 26,
  width: 600,
  maxWidth: "90vw",
  maxHeight: "85vh",
  overflowY: "auto",
  boxShadow: "0 24px 80px rgba(0,0,0,.18)",
};

/* ── Pill / badge helper ─────────────────────────────────────────── */
export function pillStyle(color, light = true) {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 5,
    padding: "3px 10px",
    borderRadius: 99,
    background: light ? `${color}1a` : color,
    color: light ? color : "#fff",
    fontSize: 11,
    fontWeight: 600,
  };
}

/* ── Title rendering (matches AdminPanel hero) ───────────────────── */
export const titleStyle = {
  fontFamily: "'Barlow Condensed',sans-serif",
  fontSize: 34,
  fontWeight: 800,
  margin: 0,
  letterSpacing: "-.01em",
  color: COLORS.text,
};

/* Use as: <span style={titleAccent}>WORD</span> for the blue right-most word */
export const titleAccent = { color: COLORS.accent };

/* ── KPI tile ────────────────────────────────────────────────────── */
export function kpiTile({ accent }) {
  return {
    background: COLORS.bgCard,
    border: `1px solid ${COLORS.border}`,
    borderTop: `3px solid ${accent || COLORS.accent}`,
    borderRadius: 12,
    padding: 16,
    boxShadow: "0 1px 3px rgba(0,0,0,.04)",
  };
}

/* ── Scrollbar helper CSS (use in <style> block) ─────────────────── */
export const SCROLLBAR_CSS = `
  .col-scroll::-webkit-scrollbar { width: 6px; height: 6px; }
  .col-scroll::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
  .col-scroll::-webkit-scrollbar-track  { background: transparent; }
`;

/* ── Font import (use in <style> block) ──────────────────────────── */
export const FONT_IMPORT_CSS = `
  @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@700;800&display=swap');
`;
