import { useAuth } from "../context/AuthContext";

/**
 * Shared sticky topbar used at the top of every TPS / production page.
 *
 * Renders:
 *   - sticky white bar (60 px)
 *   - centered two-tone title: <leading> <accent in theme color>
 *   - 2 px gradient line at the bottom (theme.gradient)
 *
 * Pattern copied from ImportExcel.jsx ("Production Import/Export") so
 * Shift Allocation, Shift Calculator, Kanban Dispatch, Anything Wrong,
 * Heijunka Schedule, 5S Audit, Store, Dispatch, PDCA all share one
 * visual identity at the top — operator can recognise "this is one of
 * the new production pages" at a glance.
 *
 * Usage:
 *   <PageTopbar leading="Production" accent="Import/Export" />
 *   <PageTopbar leading="Shift" accent="Allocation" />
 */
export default function PageTopbar({ leading, accent }) {
  const { theme } = useAuth();
  return (
    <div style={{
      background:"#fff",
      borderBottom:"1px solid #e2e8f0",
      padding:"0 40px 0 88px",
      height:60,
      display:"flex",
      alignItems:"center",
      position:"sticky",
      top:0,
      zIndex:100,
      boxShadow:"0 1px 3px rgba(0,0,0,.06)",
    }}>
      {/* Spacer so the absolute-positioned title can centre correctly */}
      <div style={{ fontFamily:"'Barlow Condensed',sans-serif", fontSize:22, fontWeight:800, color:"#0f172a" }} />
      {/* 2px coloured underline that runs the full bar width */}
      <div style={{
        position:"absolute", bottom:0, left:0, right:0, height:2,
        background: theme?.gradient || "linear-gradient(90deg,#1e40af,#2563eb,#60a5fa)",
      }} />
      {/* Centred title */}
      <div style={{
        position:"absolute", left:"50%", transform:"translateX(-50%)",
        fontFamily:"'Barlow Condensed',sans-serif",
        fontSize:37, fontWeight:800, color:"#0f172a", letterSpacing:"-.01em",
        pointerEvents:"none",
      }}>
        {leading} <span style={{ color: theme?.accent || "#2563eb" }}>{accent}</span>
      </div>
    </div>
  );
}
