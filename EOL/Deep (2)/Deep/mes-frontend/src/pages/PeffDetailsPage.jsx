// PeffDetailsPage.jsx — the admin-side wrapper that puts line / date / shift
// pickers above the shared <PeffDetails> view.  Lives in Production → next to
// "Work Center (PEFF)".  Same component is reused (without pickers) inside the
// Historical hourly report, so both places show identical numbers.
import { useState, useEffect } from "react";
import { useAuth } from "../context/AuthContext";
import PeffDetails from "../components/PeffDetails";

const today = () => new Date().toISOString().split("T")[0];

export default function PeffDetailsPage() {
  const { token } = useAuth();
  const [lines, setLines] = useState([]);
  const [lineId, setLineId] = useState("");
  const [date, setDate] = useState(today());
  const [shift, setShift] = useState("A");

  // Populate the line dropdown with a PLAIN fetch — deliberately NOT the shared
  // api/client, because that client redirects to /login on any 401. Filling a
  // dropdown must never be able to log the operator out: a bad/stale token here
  // just leaves the list empty, the session stays. (Token still sent so a good
  // session gets its lines.)
  useEffect(() => {
    let alive = true;
    const tok = token || sessionStorage.getItem("mes_token") || "";
    if (!tok) return;
    fetch("/api/lines/", { headers: { Authorization: `Bearer ${tok}` } })
      .then(r => (r.ok ? r.json() : []))
      .then(r => {
        if (!alive) return;
        const all = Array.isArray(r) ? r : [];
        setLines(all);
        setLineId(prev => prev || (all.length ? String(all[0].id) : ""));
      })
      .catch(() => { if (alive) setLines([]); });
    return () => { alive = false; };
  }, [token]);

  const lineName = lines.find(l => String(l.id) === String(lineId))?.line_name || "";

  const fld = { display: "flex", flexDirection: "column", gap: 4 };
  const lbl = { fontSize: 11, fontWeight: 700, color: "#64748b",
                textTransform: "uppercase", letterSpacing: ".03em" };
  const inp = { padding: "8px 10px", border: "1px solid #cbd5e1", borderRadius: 8,
                fontSize: 13, background: "#fff", minWidth: 160 };

  return (
    <div style={{ maxWidth: 1000, margin: "0 auto" }}>
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "flex-end",
                    background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10,
                    padding: 14, marginBottom: 16 }}>
        <div style={fld}>
          <span style={lbl}>Line</span>
          <select value={lineId} onChange={e => setLineId(e.target.value)} style={inp}>
            {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
          </select>
        </div>
        <div style={fld}>
          <span style={lbl}>Date</span>
          <input type="date" value={date} max={today()}
                 onChange={e => setDate(e.target.value)} style={inp} />
        </div>
        <div style={fld}>
          <span style={lbl}>Shift</span>
          <select value={shift} onChange={e => setShift(e.target.value)} style={inp}>
            <option value="A">A Shift (08:30 – 17:15)</option>
            <option value="B">B Shift (18:30 – 03:15)</option>
          </select>
        </div>
      </div>

      {lineId
        ? <PeffDetails lineName={lineName} date={date} shift={shift} />
        : <div style={{ color: "#94a3b8", padding: 20 }}>Select a line to view its PEFF Details.</div>}
    </div>
  );
}
