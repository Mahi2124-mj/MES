// ───────────────────────────────────────────────────────────────────────
// LeaderAllocationPage.jsx   (/leader-allocation)   2026-09-13
// ───────────────────────────────────────────────────────────────────────
// Standalone, first-class nav page for Leader Allocation so shift-incharge &
// above reach it directly (also lives as a tab inside Employee Master).
// ───────────────────────────────────────────────────────────────────────
import PageTopbar from "../components/PageTopbar";
import LeaderAllocation from "../components/LeaderAllocation";

export default function LeaderAllocationPage() {
  return (
    <div style={{ padding: "18px 22px 60px", color: "#0f172a" }}>
      <PageTopbar leading="Leader" accent="Allocation" />
      <div style={{ marginTop: 12 }}>
        <LeaderAllocation />
      </div>
    </div>
  );
}
