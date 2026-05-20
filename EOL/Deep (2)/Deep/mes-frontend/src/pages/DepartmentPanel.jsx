/* ───────────────────────────────────────────────────────────────────
 * DepartmentPanel.jsx
 * ───────────────────────────────────────────────────────────────────
 * Legacy entry point for department users — the slide nav now points
 * each role at its dedicated /admin/<slug> route directly (Production /
 * Maintenance / Quality Panels are all just /admin/* with role-aware
 * `readOnly` and `theme.accent`).
 *
 * This route stays alive purely as a redirect for any old bookmarks
 * pointing at /department-panel.  We pick the right /admin/<slug>
 * based on the logged-in user's department slug, or fall back to
 * /dashboard for non-department users.
 */
import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

const SLUG_TO_PATH = {
  maintenance: "/admin/maintenance",
  quality:     "/admin/quality",
  production:  "/admin/production",
};

export default function DepartmentPanel() {
  const { user, isDepartment } = useAuth();

  // Non-department users get bounced back to their own dashboard —
  // /department-panel never made sense for admin / plant_head /
  // production / operator anyway.
  if (!isDepartment || !user?.departmentId) {
    return <Navigate to="/dashboard" replace />;
  }

  const slug = (user?.departmentSlug || "").toLowerCase();
  const target = SLUG_TO_PATH[slug] || "/dashboard";
  return <Navigate to={target} replace />;
}
