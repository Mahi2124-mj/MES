// pages/CmsPortal.jsx — the CMS (camera system) inside the MES, admin only.
// 2026-10-09 — operator: "CMS ko MES ke andar hi ek page me add kar de, only
// in admin id, taaki kahin se bhi access kar sakun".  The CMS web app is served
// by the MES server under /cms/ (its own production build) and reaches the
// CMS API through /cms-api, so it works on the LAN and through mes.tbdi.in
// alike.  The CMS keeps its own login; its admin role is what allows changes.
import PageTopbar from "../components/PageTopbar";

export default function CmsPortal() {
  return (
    <div style={{ padding: "18px 22px 12px", color: "#0f172a" }}>
      <PageTopbar leading="CMS" accent="Camera System" />
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap",
                    margin: "10px 0 8px", fontSize: 12.5, color: "#64748b" }}>
        <span>Sign in with a CMS account. Camera Admin → Cameras lists and deletes cameras no machine uses.</span>
        <a href="/cms/" target="_blank" rel="noopener noreferrer"
           style={{ marginLeft: "auto", color: "#2563eb", fontWeight: 700, textDecoration: "none" }}>
          Open in new tab ↗
        </a>
      </div>
      <iframe title="CMS" src="/cms/"
        style={{ width: "100%", height: "calc(100vh - 150px)", minHeight: 480,
                 border: "1px solid #e2e8f0", borderRadius: 12, background: "#fff" }} />
    </div>
  );
}
