// Shared by BreakdownSlipPaper (slip view) and Historical → Breakdown History (table).
export const MAINT_SRC = ["AUTO-MAINT", "AUTO-TOOLROOM", "MANUAL"];

// 2026-10-16 — download one slip as a PDF (same paper, drawn by the API).
// Returns null on success, else an error message.
export async function downloadSlipPdf(src, id) {
  try {
    const jwt = sessionStorage.getItem("mes_token") || "";
    const res = await fetch(`/api/breakdown-history/slip/pdf?src=${encodeURIComponent(src)}&id=${id}`,
                            { headers: { Authorization: `Bearer ${jwt}` } });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      return e.detail || "PDF download failed";
    }
    const cd = res.headers.get("Content-Disposition") || "";
    const m = /filename="([^"]+)"/.exec(cd);
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = m ? m[1] : `BD_Slip_${src}_${id}.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    return null;
  } catch (e) {
    return e?.message || "PDF download failed";
  }
}
