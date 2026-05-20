"""
Toyota Boshoku – Industrial Camera Control System
Multi-page Dash app with Toyota Boshoku theme.

Pages:
  /dashboard      – Overview KPIs + line health + recent cycles
  /line-monitor   – Select line → machine grid + per-machine cycle button
  /cycle-history  – Filterable cycle log + click-to-play video modal
  /analytics      – Shift/line/machine performance charts
  /admin          – Users, cameras, line/zone/machine config (admin only)
"""

import csv
import json
import os
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import cv2
import plotly.graph_objects as go
from dash import (
    ALL, MATCH, Dash, Input, Output, State,
    callback_context, dash_table, dcc, html, no_update,
)
from flask import Response, abort, send_file

from auth import ROLES, add_user, authenticate, delete_user, list_users
from camera_config import (
    add_camera, delete_camera, get_camera_rtsp_url, list_cameras,
)
from recorder import (
    DEFAULT_METADATA_CSV, DEFAULT_VIDEOS_DIR, NEW_CSV_COLUMNS,
    append_cycle_metadata, create_video_writer, ensure_metadata_file,
    get_next_cycle_number, get_resolved_videos_dir, open_rtsp_capture,
    update_cycle_tag,
)
from zone_config import (
    add_line, add_machine, add_zone, all_machines_flat,
    get_machines_for_zone, get_zones_for_line, list_lines,
)

# ─── constants ──────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
METADATA_CSV = os.path.join(BASE_DIR, DEFAULT_METADATA_CSV)
# VIDEOS_DIR is now resolved at write-time via get_resolved_videos_dir()
# so the External-HDD path setting in /api/settings is honored without
# restarting the dashboard service.  The legacy module-level constant
# stays as a fallback for any caller that imports it.
VIDEOS_DIR = DEFAULT_VIDEOS_DIR

NAV_PAGES = [
    ("/dashboard",     "🏠", "Dashboard"),
    ("/line-monitor",  "📷", "Line Monitor"),
    ("/cycle-history", "📋", "Cycle History"),
    ("/analytics",     "📊", "Analytics"),
]
PAGE_TITLES = {
    "/dashboard":     ("🏠", "Dashboard"),
    "/line-monitor":  ("📷", "Line Monitor"),
    "/cycle-history": ("📋", "Cycle History"),
    "/analytics":     ("📊", "Analytics"),
    "/admin":         ("⚙️",  "Admin"),
}


# ═══════════════════════════════════════════════════════════════════════════════
# Camera stream logic
# ═══════════════════════════════════════════════════════════════════════════════

class CameraStream:
    def __init__(self, camera_id: str, rtsp_url: str):
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self._lock = threading.Lock()
        self._cap: Optional[cv2.VideoCapture] = None
        self.connected = False
        self.connected_url = ""
        self.last_error = ""
        self._frame_jpeg: Optional[bytes] = None
        self._frame_shape: Optional[Tuple[int, int]] = None
        self.fps = 25.0
        self._do_reconnect = True

        self._writer: Optional[cv2.VideoWriter] = None
        self.current_cycle: Optional[int] = None
        self._start_dt: Optional[datetime] = None
        self._file_rel: Optional[str] = None
        self._machine: Dict = {}

        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # ── capture loop ──────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self.running:
            if self._do_reconnect:
                cap, url, _ = open_rtsp_capture(self.rtsp_url)
                with self._lock:
                    if self._cap is not None:
                        self._cap.release()
                    self._cap = cap
                    self.connected = cap is not None
                    self.connected_url = url or ""
                    self.last_error = "" if cap else "Connection failed"
                    self._do_reconnect = False
                if cap is None:
                    time.sleep(5)
                    self._do_reconnect = True
                    continue

            with self._lock:
                cap = self._cap
            if cap is None:
                time.sleep(0.1)
                continue

            ok, frame = cap.read()
            if not ok:
                with self._lock:
                    self.connected = False
                    self.last_error = "Stream lost – reconnecting"
                    self._do_reconnect = True
                continue

            h, w = frame.shape[:2]
            fps_val = cap.get(cv2.CAP_PROP_FPS)
            safe_fps = fps_val if fps_val and fps_val > 0 else 25.0

            with self._lock:
                if self._writer is not None:
                    self._writer.write(frame)
                is_rec = self._writer is not None
                cyc = self.current_cycle

            label = f"⏺  Cycle {cyc}" if is_rec else "  LIVE"
            color = (0, 0, 200) if is_rec else (0, 180, 0)
            cv2.putText(frame, label, (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

            ok_enc, enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ok_enc:
                with self._lock:
                    self._frame_jpeg = enc.tobytes()
                    self._frame_shape = (h, w)
                    self.fps = safe_fps
                    self.connected = True
                    self.last_error = ""

            time.sleep(0.012)

    # ── cycle control ─────────────────────────────────────────────────────────

    def toggle_cycle(self, machine: Dict) -> Tuple[bool, str]:
        """End current cycle (if any) and immediately start the next one."""
        with self._lock:
            end_msg = self._end_locked() if self._writer is not None else ""
            ok, start_msg = self._start_locked(machine)
        if end_msg:
            return ok, f"{end_msg}  →  {start_msg}"
        return ok, start_msg

    def end_cycle_only(self) -> Tuple[bool, str]:
        with self._lock:
            if self._writer is None:
                return False, "No active cycle"
            msg = self._end_locked()
        return True, msg

    def _start_locked(self, machine: Dict, force_cycle_num: Optional[int] = None, force_start_iso: Optional[str] = None) -> Tuple[bool, str]:
        if self._frame_shape is None:
            return False, "Waiting for first frame from camera"
        next_n = force_cycle_num if force_cycle_num else get_next_cycle_number(METADATA_CSV)
        machine_id = machine.get("machine_id", "default")
        # Live resolution: lets a UI change to /api/settings.videos_dir
        # take effect on the very next cycle (no restart needed).
        videos_root = get_resolved_videos_dir()
        vid_dir = os.path.join(videos_root, machine_id)
        os.makedirs(vid_dir, exist_ok=True)
        fname = f"cycle_{next_n}.mp4"
        fabs = os.path.join(vid_dir, fname)
        # CSV file_path: relative for in-tree storage, absolute for
        # external HDD (so api_server.serve_video resolves it correctly).
        try:
            common = os.path.commonpath([os.path.abspath(videos_root), BASE_DIR])
        except ValueError:
            common = ""
        if common == BASE_DIR:
            rel_root = os.path.relpath(videos_root, BASE_DIR).replace("\\", "/")
            frel = f"{rel_root}/{machine_id}/{fname}"
        else:
            frel = fabs.replace("\\", "/")
        h, w = self._frame_shape
        self._writer = create_video_writer(fabs, self.fps, (w, h))
        self.current_cycle = next_n
        if force_start_iso:
            try:
                self._start_dt = datetime.fromisoformat(force_start_iso)
            except ValueError:
                self._start_dt = datetime.now()
        else:
            self._start_dt = datetime.now()
        self._file_rel = frel
        self._machine = machine
        return True, f"Cycle {next_n} started"

    def _end_locked(self) -> str:
        if self._writer is None:
            return ""
        end_dt = datetime.now()
        self._writer.release()
        self._writer = None
        m = self._machine
        append_cycle_metadata(
            METADATA_CSV, self.current_cycle, self._start_dt, end_dt,
            self._file_rel,
            machine_id=m.get("machine_id", ""),
            machine_name=m.get("machine_name", ""),
            line_name=m.get("line_name", ""),
            zone_name=m.get("zone_name", ""),
        )
        cyc = self.current_cycle
        self.current_cycle = None
        self._start_dt = None
        self._file_rel = None
        self._machine = {}
        return f"Cycle {cyc} saved"

    # ── helpers ───────────────────────────────────────────────────────────────

    def status(self) -> Dict:
        with self._lock:
            return {
                "connected": self.connected,
                "error": self.last_error,
                "recording": self._writer is not None,
                "current_cycle": self.current_cycle,
                "start_iso": self._start_dt.isoformat() if self._start_dt else None,
                "machine": dict(self._machine),
            }

    def reconnect(self, rtsp_url: Optional[str] = None) -> None:
        with self._lock:
            if rtsp_url:
                self.rtsp_url = rtsp_url
            self._do_reconnect = True

    def mjpeg_stream(self):
        while self.running:
            with self._lock:
                frame = self._frame_jpeg
            if frame:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            time.sleep(0.035)

    def stop(self) -> None:
        self.running = False
        with self._lock:
            if self._writer:
                self._writer.release()
                self._writer = None
            if self._cap:
                self._cap.release()
                self._cap = None


class StreamManager:
    def __init__(self):
        self.streams: Dict[str, CameraStream] = {}
        self._lock = threading.Lock()
        ensure_metadata_file(METADATA_CSV)
        self._init_cameras()
        self._health_thread = threading.Thread(target=self._health_loop, daemon=True)
        self._health_thread.start()
        
        self._sync_thread = threading.Thread(target=self._sync_cycle_state_loop, daemon=True)
        self._sync_thread.start()

    def _sync_cycle_state_loop(self) -> None:
        try:
            from cycle_state import get_all_states
            from zone_config import all_machines_flat
        except ImportError:
            return
            
        while True:
            time.sleep(1)
            try:
                states = get_all_states(BASE_DIR)
                machines = {m["machine_id"]: m for m in all_machines_flat(BASE_DIR)}
                
                # Check for starts/stops based on state
                for mid, mstate in states.items():
                    is_recording = mstate.get("recording", False)
                    target_cycle = mstate.get("cycle_number")
                    target_start = mstate.get("start_time")
                    
                    machine = machines.get(mid)
                    if not machine: continue
                    cam_id = machine.get("camera_id")
                    if not cam_id: continue
                    
                    s = self.get(cam_id)
                    if not s: continue
                    
                    status = s.status()
                    s_rec = status["recording"]
                    s_cyc = status["current_cycle"]
                    
                    if is_recording and not s_rec:
                        with s._lock:
                            s._start_locked(machine, force_cycle_num=target_cycle, force_start_iso=target_start)
                    elif not is_recording and s_rec and s_cyc == target_cycle:
                        # State says not recording, but stream is recording the SAME cycle number.
                        # This happens when api_server calls end_cycle
                        with s._lock:
                            s._end_locked()
            except Exception:
                pass

    def _init_cameras(self) -> None:
        for cam in list_cameras(BASE_DIR):
            cid = str(cam.get("id", ""))
            rtsp = get_camera_rtsp_url(cid, BASE_DIR)
            if cid and rtsp:
                self._add_stream(cid, rtsp)

    def _add_stream(self, camera_id: str, rtsp_url: str) -> CameraStream:
        s = CameraStream(camera_id, rtsp_url)
        self.streams[camera_id] = s
        return s

    def start_stream(self, camera_id: str, rtsp_url: str) -> CameraStream:
        with self._lock:
            if camera_id in self.streams:
                self.streams[camera_id].reconnect(rtsp_url)
                return self.streams[camera_id]
            return self._add_stream(camera_id, rtsp_url)

    def stop_stream(self, camera_id: str) -> None:
        with self._lock:
            s = self.streams.pop(camera_id, None)
        if s:
            s.stop()

    def get(self, camera_id: str) -> Optional[CameraStream]:
        return self.streams.get(camera_id)

    def health(self) -> Dict[str, bool]:
        return {cid: s.status()["connected"] for cid, s in self.streams.items()}

    def ensure_stream(self, camera_id: str) -> Optional[CameraStream]:
        if camera_id in self.streams:
            return self.streams[camera_id]
        rtsp = get_camera_rtsp_url(camera_id, BASE_DIR)
        if rtsp:
            return self.start_stream(camera_id, rtsp)
        return None

    def _health_loop(self) -> None:
        while True:
            time.sleep(30)
            for s in list(self.streams.values()):
                if not s.status()["connected"]:
                    s.reconnect()


mgr = StreamManager()


# ═══════════════════════════════════════════════════════════════════════════════
# App + Flask routes
# ═══════════════════════════════════════════════════════════════════════════════

app = Dash(__name__, suppress_callback_exceptions=True)
app.title = "Toyota Boshoku – Camera Control"
app.server.secret_key = os.urandom(24)


@app.server.route("/live_feed/<camera_id>")
def live_feed(camera_id: str):
    s = mgr.get(camera_id)
    if s is None:
        abort(404)
    return Response(s.mjpeg_stream(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.server.route("/cycle-video/<path:rel_path>")
def serve_video(rel_path: str):
    safe = os.path.normpath(rel_path).replace("\\", "/")
    if ".." in safe:
        abort(403)
    full = os.path.join(BASE_DIR, safe)
    if not os.path.exists(full):
        abort(404)
    return send_file(full, mimetype="video/mp4", conditional=True)


@app.server.route("/logout")
def do_logout():
    return (
        "<html><body><script>"
        "sessionStorage.clear();localStorage.clear();"
        "window.location.replace('/');</script></body></html>"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Data helpers
# ═══════════════════════════════════════════════════════════════════════════════

def read_cycles() -> List[Dict]:
    if not os.path.exists(METADATA_CSV):
        return []
    out = []
    with open(METADATA_CSV, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                cn = int(row.get("cycle_number", 0))
                dur = float(row.get("duration", 0))
            except (TypeError, ValueError):
                continue
            out.append({
                "cycle_number": cn,
                "duration": round(dur, 1),
                "start_time": row.get("start_time", ""),
                "end_time": row.get("end_time", ""),
                "file_path": row.get("file_path", ""),
                "machine_id": row.get("machine_id", ""),
                "machine_name": row.get("machine_name", ""),
                "line_name": row.get("line_name", ""),
                "zone_name": row.get("zone_name", ""),
                "shift": row.get("shift", ""),
                "tag": row.get("tag", ""),
            })
    return sorted(out, key=lambda x: x["cycle_number"])


def _filter_cycles(cycles, shift, line, machine, tag, search):
    out = cycles
    if shift and shift != "All":
        out = [c for c in out if c["shift"] == shift]
    if line and line != "All":
        out = [c for c in out if c["line_name"] == line]
    if machine and machine != "All":
        out = [c for c in out if c["machine_name"] == machine]
    if tag and tag != "All":
        out = [c for c in out if c["tag"] == tag]
    if search:
        sq = search.lower()
        out = [c for c in out if sq in str(c["cycle_number"])
               or sq in c["start_time"].lower()
               or sq in c["machine_name"].lower()
               or sq in c["line_name"].lower()
               or sq in c["shift"].lower()]
    return out


def _today_cycles() -> List[Dict]:
    today = datetime.now().strftime("%Y-%m-%d")
    # cycles.csv stores only HH:MM:SS – compare count by today's runs
    # Fallback: return all cycles (timestamp doesn't include date in current schema)
    return read_cycles()


def _cam_options():
    return [{"label": f"{c.get('name','?')} ({c.get('ip','')})", "value": str(c.get("id", ""))}
            for c in list_cameras(BASE_DIR)]


def _machine_today_count(machine_name: str) -> int:
    return sum(1 for c in read_cycles() if c["machine_name"] == machine_name)


# ═══════════════════════════════════════════════════════════════════════════════
# Layout helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _badge(label: str, cls: str) -> html.Span:
    return html.Span(label, className=f"badge {cls}")


def _inp(pid, ph, **kw):
    return dcc.Input(
        id=pid, type="text", placeholder=ph, debounce=True,
        style={"width": "100%", "padding": "8px 10px", "border": "1px solid #d1d5db",
               "borderRadius": "6px", "fontSize": "13px", "boxSizing": "border-box"},
        **kw,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# LOGIN layout
# ═══════════════════════════════════════════════════════════════════════════════

def _login_layout() -> html.Div:
    return html.Div(
        style={
            "display": "flex", "alignItems": "center", "justifyContent": "center",
            "minHeight": "100vh",
            "background": "linear-gradient(135deg,#0a1628 0%,#0d1b2a 50%,#1a0000 100%)",
            "fontFamily": "Segoe UI, Tahoma, sans-serif",
        },
        children=[
            html.Div(
                style={
                    "background": "#fff", "borderRadius": "14px",
                    "padding": "40px 38px", "width": "360px",
                    "boxShadow": "0 16px 60px rgba(0,0,0,0.5)",
                },
                children=[
                    html.Div(
                        style={"textAlign": "center", "marginBottom": "28px"},
                        children=[
                            html.Div(
                                "TB",
                                style={
                                    "width": "54px", "height": "54px",
                                    "background": "#CC0000", "borderRadius": "12px",
                                    "display": "flex", "alignItems": "center",
                                    "justifyContent": "center", "margin": "0 auto 12px",
                                    "fontSize": "20px", "fontWeight": "900", "color": "#fff",
                                },
                            ),
                            html.H2("Toyota Boshoku",
                                    style={"margin": "0 0 2px", "fontSize": "19px",
                                           "fontWeight": "800", "color": "#0f172a"}),
                            html.P("Camera Control System",
                                   style={"margin": 0, "color": "#64748b", "fontSize": "13px"}),
                        ],
                    ),
                    dcc.Input(
                        id="login-username", type="text", placeholder="Username", value="",
                        n_submit=0,
                        style={"width": "100%", "padding": "11px 12px", "marginBottom": "10px",
                               "border": "1px solid #d1d5db", "borderRadius": "7px",
                               "fontSize": "14px", "boxSizing": "border-box"},
                    ),
                    dcc.Input(
                        id="login-password", type="password", placeholder="Password", value="",
                        n_submit=0,
                        style={"width": "100%", "padding": "11px 12px", "marginBottom": "18px",
                               "border": "1px solid #d1d5db", "borderRadius": "7px",
                               "fontSize": "14px", "boxSizing": "border-box"},
                    ),
                    html.Button(
                        "Sign In",
                        id="login-btn", n_clicks=0,
                        style={
                            "width": "100%", "padding": "12px", "background": "#CC0000",
                            "color": "#fff", "border": "none", "borderRadius": "8px",
                            "fontSize": "15px", "fontWeight": "700", "cursor": "pointer",
                        },
                    ),
                    html.Div(id="login-error",
                             style={"color": "#ef4444", "marginTop": "12px",
                                    "textAlign": "center", "fontSize": "13px"}),
                    html.Hr(style={"borderColor": "#f1f5f9", "margin": "22px 0 12px"}),
                    html.Div(
                        "admin / admin123  ·  supervisor / super123  ·  operator / oper123",
                        style={"fontSize": "11px", "color": "#94a3b8", "textAlign": "center"},
                    ),
                ],
            ),
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# APP SHELL  (sidebar + topbar + page content + video modal)
# ═══════════════════════════════════════════════════════════════════════════════

def _app_shell(user: Dict, pathname: str) -> html.Div:
    role = user.get("role", "operator")
    display_name = user.get("display_name", user.get("username", "User"))
    icon, title = PAGE_TITLES.get(pathname, PAGE_TITLES["/dashboard"])

    nav_items = list(NAV_PAGES)
    if role == "admin":
        nav_items.append(("/admin", "⚙️", "Admin"))

    def _nav(path, ico, label):
        active = pathname == path or (pathname in ("", "/") and path == "/dashboard")
        return html.A(
            [html.Span(ico, className="tb-nav-icon"), html.Span(label)],
            href=path,
            className="tb-nav-link active" if active else "tb-nav-link",
            style={"textDecoration": "none"},
        )

    sidebar = html.Aside(
        className="tb-sidebar",
        children=[
            html.Div(
                className="tb-brand",
                children=[html.Div(
                    className="tb-brand-row",
                    children=[
                        html.Div("TB", className="tb-logo-badge"),
                        html.Div([
                            html.Div("Toyota Boshoku", className="tb-brand-text-main"),
                            html.Div("Camera Control", className="tb-brand-text-sub"),
                        ]),
                    ],
                )],
            ),
            html.Nav(
                className="tb-nav",
                children=[
                    html.Div("NAVIGATION", className="tb-nav-section"),
                    *[_nav(p, i, lb) for p, i, lb in nav_items],
                ],
            ),
            html.Div(
                className="tb-footer",
                children=[
                    html.Div(display_name, className="tb-footer-name"),
                    html.Div(role.upper(), className="tb-footer-role"),
                    html.A("Logout →", href="/logout", className="tb-footer-logout"),
                ],
            ),
        ],
    )

    topbar = html.Div(
        className="tb-topbar",
        children=[
            html.Span(f"{icon}  {title}", className="tb-page-title"),
            html.Div([
                html.Div(id="health-badges",
                         style={"display": "flex", "gap": "8px", "alignItems": "center"}),
                html.Div(
                    f"⏰ {datetime.now().strftime('%d %b %Y')}",
                    style={"fontSize": "12px", "color": "#94a3b8", "marginLeft": "16px"},
                ),
            ], style={"display": "flex", "alignItems": "center"}),
        ],
    )

    # Video modal (always in DOM, hidden by default)
    video_modal = html.Div(
        id="video-modal",
        style={"display": "none"},
        children=[
            html.Div(
                className="video-modal-box",
                children=[
                    html.Div(
                        className="video-modal-hdr",
                        children=[
                            html.Div([
                                html.Div(id="modal-title",
                                         className="video-modal-hdr-title",
                                         children="Cycle Video"),
                            ]),
                            html.Button("✕", id="modal-close-btn", n_clicks=0,
                                        className="video-modal-close"),
                        ],
                    ),
                    html.Div(
                        id="modal-meta",
                        className="video-modal-info",
                    ),
                    html.Video(
                        id="modal-video-player", controls=True, autoPlay=True,
                        style={"width": "100%", "maxHeight": "520px",
                               "display": "block", "background": "#000"},
                    ),
                ],
            ),
        ],
    )

    return html.Div(
        style={"fontFamily": "Segoe UI, Tahoma, sans-serif"},
        children=[
            # Global timers + stores
            dcc.Interval(id="main-interval",      interval=3000,  n_intervals=0),
            dcc.Interval(id="second-interval",    interval=1000,  n_intervals=0),
            dcc.Interval(id="analytic-interval",  interval=6000,  n_intervals=0),
            dcc.Store(id="user-data-store",        data=user),
            dcc.Store(id="line-select-store",      data=None),

            sidebar,

            html.Div(
                className="tb-main",
                children=[
                    topbar,
                    html.Div(id="health-alert"),
                    html.Div(_render_page(user, pathname), className="tb-page-body"),
                ],
            ),

            video_modal,
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE renderers
# ═══════════════════════════════════════════════════════════════════════════════

def _render_page(user: Dict, pathname: str) -> html.Div:
    role = user.get("role", "operator")
    if pathname in ("", "/", "/dashboard"):
        return _page_dashboard()
    if pathname == "/line-monitor":
        return _page_line_monitor()
    if pathname == "/cycle-history":
        return _page_cycle_history()
    if pathname == "/analytics":
        return _page_analytics()
    if pathname == "/admin":
        return _page_admin(role)
    return html.Div(
        "404 – Page not found",
        style={"color": "#64748b", "padding": "60px", "textAlign": "center",
               "fontSize": "18px"},
    )


# ── DASHBOARD OVERVIEW ─────────────────────────────────────────────────────────

def _page_dashboard() -> html.Div:
    return html.Div([
        # KPI row
        html.Div(
            className="grid-4 mb-4",
            children=[
                html.Div([html.Div("Total Cycles", className="kpi-label"),
                          html.Div(id="ov-kpi-total", children="–", className="kpi-value"),
                          html.Div("all time", className="kpi-sub")], className="kpi-card"),
                html.Div([html.Div("Avg Duration", className="kpi-label"),
                          html.Div(id="ov-kpi-avg", children="–", className="kpi-value"),
                          html.Div("seconds", className="kpi-sub")], className="kpi-card"),
                html.Div([html.Div("Cameras Online", className="kpi-label"),
                          html.Div(id="ov-kpi-cams", children="–", className="kpi-value"),
                          html.Div("connected", className="kpi-sub")], className="kpi-card"),
                html.Div([html.Div("Active Recordings", className="kpi-label"),
                          html.Div(id="ov-kpi-rec", children="–", className="kpi-value"),
                          html.Div("right now", className="kpi-sub")], className="kpi-card"),
            ],
            style={"marginBottom": "16px"},
        ),

        html.Div(
            className="grid-2",
            children=[
                html.Div([
                    html.Div("🏭 Line Status", className="tb-card-title"),
                    html.Div(id="ov-line-health",
                             children=html.Div("Loading…", style={"color": "#94a3b8"})),
                ], className="tb-card"),

                html.Div([
                    html.Div("📋 Recent Cycles", className="tb-card-title"),
                    html.Div(id="ov-recent-cycles",
                             children=html.Div("Loading…", style={"color": "#94a3b8"})),
                ], className="tb-card"),
            ],
        ),
    ])


# ── LINE MONITOR ────────────────────────────────────────────────────────────────

def _page_line_monitor() -> html.Div:
    lines = list_lines(BASE_DIR)
    return html.Div([
        html.Div(
            className="tb-card mb-4",
            style={"marginBottom": "16px"},
            children=[
                html.Div("Select Production Line", className="tb-card-title"),
                html.Div(
                    [
                        html.Button(
                            l.get("name", l.get("id")),
                            id={"type": "line-btn", "line_id": l.get("id", "")},
                            n_clicks=0,
                            className="line-btn",
                        )
                        for l in lines
                    ] if lines else [html.Div(
                        "No lines configured. Add lines in Admin → Lines & Machines.",
                        style={"color": "#94a3b8", "fontSize": "13px"},
                    )]
                ),
            ],
        ),
        html.Div(id="machine-grid-container"),
    ])


def _make_machine_card(m: Dict) -> html.Div:
    machine_id = m["machine_id"]
    camera_id = m.get("camera_id")
    has_cam = bool(camera_id)

    feed = (
        html.Img(
            src=f"/live_feed/{camera_id}",
            className="machine-feed",
            alt="Live feed",
        )
        if has_cam else
        html.Div([
            html.Div("📷", style={"fontSize": "28px"}),
            html.Div("No camera assigned"),
        ], className="machine-no-feed")
    )

    return html.Div(
        className="machine-card",
        id={"type": "machine-card-wrap", "machine_id": machine_id},
        children=[
            # Store for machine metadata
            dcc.Store(
                id={"type": "machine-meta", "machine_id": machine_id},
                data=m,
            ),
            dcc.Store(
                id={"type": "last-toggle-ts", "machine_id": machine_id},
                data=0,
            ),

            # Card header
            html.Div(
                className="machine-card-hdr",
                children=[
                    html.Div(
                        f"{m['line_name']}  ›  {m['zone_name']}",
                        className="machine-hdr-meta",
                    ),
                    html.Span(
                        "● LIVE",
                        id={"type": "status-badge", "machine_id": machine_id},
                        className="badge badge-live",
                    ),
                ],
            ),

            # Machine name
            html.Div(m["machine_name"], className="machine-name"),

            # Live feed
            feed,

            # Timer + stats row
            html.Div(
                className="machine-stats",
                children=[
                    html.Div(
                        id={"type": "cycle-timer", "machine_id": machine_id},
                        children="––:––",
                        className="machine-timer",
                    ),
                    html.Div(
                        id={"type": "cycle-meta", "machine_id": machine_id},
                        className="machine-cycle-meta",
                    ),
                ],
            ),

            # Toggle button
            html.Button(
                "▶  START CYCLE" if has_cam else "No Camera",
                id={"type": "toggle-btn", "machine_id": machine_id},
                n_clicks=0,
                className="cycle-btn start" if has_cam else "cycle-btn no-cam",
                disabled=not has_cam,
            ),

            # Message row
            html.Div(
                id={"type": "cycle-msg", "machine_id": machine_id},
                className="cycle-msg",
            ),
        ],
    )


# ── CYCLE HISTORY ───────────────────────────────────────────────────────────────

def _page_cycle_history() -> html.Div:
    cycles = read_cycles()
    flat = all_machines_flat(BASE_DIR)
    lines = sorted({c["line_name"] for c in cycles if c["line_name"]} |
                   {m["line_name"] for m in flat if m["line_name"]})
    machines = sorted({c["machine_name"] for c in cycles if c["machine_name"]} |
                      {m["machine_name"] for m in flat if m["machine_name"]})

    line_opts = [{"label": "All Lines", "value": "All"}] + [{"label": n, "value": n} for n in lines]
    machine_opts = [{"label": "All Machines", "value": "All"}] + [{"label": n, "value": n} for n in machines]
    shift_opts = [
        {"label": "All Shifts", "value": "All"},
        {"label": "🌅 Morning  06:00–14:00", "value": "Morning"},
        {"label": "🌆 Evening  14:00–22:00", "value": "Evening"},
        {"label": "🌙 Night    22:00–06:00", "value": "Night"},
    ]
    tag_opts = [
        {"label": "All Tags", "value": "All"},
        {"label": "✅ Normal",  "value": "Normal"},
        {"label": "❌ Defect",  "value": "Defect"},
        {"label": "🔄 Rework",  "value": "Rework"},
        {"label": "⏸ Hold",    "value": "Hold"},
    ]

    iStyle = {
        "padding": "7px 10px", "border": "1px solid #d1d5db",
        "borderRadius": "6px", "fontSize": "13px", "width": "100%",
        "boxSizing": "border-box",
    }

    return html.Div([
        # Filters
        html.Div(
            className="tb-card mb-4",
            style={"marginBottom": "16px"},
            children=[
                html.Div("Filters", className="tb-card-title"),
                html.Div(
                    style={"display": "grid", "gridTemplateColumns": "1fr 1fr 1fr 1fr 1.5fr auto", "gap": "10px", "alignItems": "flex-end"},
                    children=[
                        html.Div([html.Div("Shift", style={"fontSize": "11px", "color": "#64748b", "fontWeight": "600", "marginBottom": "4px", "textTransform": "uppercase", "letterSpacing": "0.5px"}),
                                  dcc.Dropdown(id="hist-shift", options=shift_opts, value="All", clearable=False, style={"fontSize": "13px"})]),
                        html.Div([html.Div("Line", style={"fontSize": "11px", "color": "#64748b", "fontWeight": "600", "marginBottom": "4px", "textTransform": "uppercase", "letterSpacing": "0.5px"}),
                                  dcc.Dropdown(id="hist-line", options=line_opts, value="All", clearable=False, style={"fontSize": "13px"})]),
                        html.Div([html.Div("Machine", style={"fontSize": "11px", "color": "#64748b", "fontWeight": "600", "marginBottom": "4px", "textTransform": "uppercase", "letterSpacing": "0.5px"}),
                                  dcc.Dropdown(id="hist-machine", options=machine_opts, value="All", clearable=False, style={"fontSize": "13px"})]),
                        html.Div([html.Div("Tag", style={"fontSize": "11px", "color": "#64748b", "fontWeight": "600", "marginBottom": "4px", "textTransform": "uppercase", "letterSpacing": "0.5px"}),
                                  dcc.Dropdown(id="hist-tag", options=tag_opts, value="All", clearable=False, style={"fontSize": "13px"})]),
                        html.Div([html.Div("Search", style={"fontSize": "11px", "color": "#64748b", "fontWeight": "600", "marginBottom": "4px", "textTransform": "uppercase", "letterSpacing": "0.5px"}),
                                  dcc.Input(id="hist-search", type="text", placeholder="cycle #, machine, line…",
                                            value="", debounce=True, style=iStyle)]),
                        html.Div(html.Button("⬇ CSV", id="hist-dl-btn", n_clicks=0,
                                             style={"padding": "7px 14px", "background": "#0369a1",
                                                    "color": "#fff", "border": "none", "borderRadius": "6px",
                                                    "cursor": "pointer", "fontWeight": "600", "fontSize": "13px"})),
                    ],
                ),
                dcc.Download(id="hist-dl"),
            ],
        ),

        # Tag + last cycle
        html.Div(
            style={"display": "flex", "gap": "12px", "marginBottom": "14px", "alignItems": "center"},
            children=[
                html.Div(id="hist-row-count",
                         style={"fontSize": "13px", "color": "#64748b"}),
                html.Span("Tag selected cycle:", style={"fontSize": "13px", "color": "#334155", "marginLeft": "auto"}),
                dcc.Dropdown(
                    id="hist-tag-apply",
                    options=[
                        {"label": "✅  Normal",  "value": "Normal"},
                        {"label": "❌  Defect",  "value": "Defect"},
                        {"label": "🔄  Rework",  "value": "Rework"},
                        {"label": "⏸  Hold",    "value": "Hold"},
                    ],
                    placeholder="Select tag…",
                    style={"width": "160px", "fontSize": "13px"},
                    clearable=False,
                ),
                html.Button("Apply", id="hist-apply-tag-btn", n_clicks=0,
                            style={"padding": "7px 14px", "background": "#0369a1", "color": "#fff",
                                   "border": "none", "borderRadius": "6px", "cursor": "pointer",
                                   "fontWeight": "600", "fontSize": "13px"}),
                html.Div(id="hist-tag-msg", style={"fontSize": "12px", "color": "#64748b"}),
            ],
        ),

        # Table
        html.Div(
            className="tb-card",
            children=[
                html.Div(
                    id="cycle-table-hint",
                    children="👆  Click a row to play its cycle video",
                    style={"fontSize": "12px", "color": "#94a3b8", "marginBottom": "8px"},
                ),
                dash_table.DataTable(
                    id="hist-table",
                    columns=[
                        {"name": "Cycle #",     "id": "cycle_number"},
                        {"name": "Duration (s)", "id": "duration"},
                        {"name": "Start",        "id": "start_time"},
                        {"name": "End",          "id": "end_time"},
                        {"name": "Line",         "id": "line_name"},
                        {"name": "Zone",         "id": "zone_name"},
                        {"name": "Machine",      "id": "machine_name"},
                        {"name": "Shift",        "id": "shift"},
                        {"name": "Tag",          "id": "tag"},
                    ],
                    data=[],
                    page_size=15,
                    row_selectable="single",
                    style_table={"overflowX": "auto"},
                    style_cell={
                        "textAlign": "left", "fontFamily": "Segoe UI", "fontSize": 13,
                        "padding": "9px 12px", "cursor": "pointer",
                    },
                    style_header={
                        "backgroundColor": "#f8fafc", "fontWeight": "700",
                        "color": "#334155", "borderBottom": "2px solid #e2e8f0",
                        "fontSize": 12, "textTransform": "uppercase", "letterSpacing": "0.5px",
                    },
                    style_data_conditional=[
                        {"if": {"row_index": "odd"},          "backgroundColor": "#fcfcfd"},
                        {"if": {"state": "selected"},         "backgroundColor": "#fff1f1",
                         "border": "1px solid #CC0000"},
                        {"if": {"filter_query": '{tag} = "Defect"'}, "backgroundColor": "#fff6f6"},
                        {"if": {"filter_query": '{tag} = "Rework"'}, "backgroundColor": "#fffdf0"},
                        {"if": {"filter_query": '{tag} = "Hold"'},   "backgroundColor": "#f0f4ff"},
                    ],
                ),
            ],
        ),
    ])


# ── ANALYTICS ──────────────────────────────────────────────────────────────────

def _page_analytics() -> html.Div:
    flat = all_machines_flat(BASE_DIR)
    lines = sorted({m["line_name"] for m in flat if m["line_name"]})
    machines = sorted({m["machine_name"] for m in flat if m["machine_name"]})
    line_opts  = [{"label": "All", "value": "All"}] + [{"label": n, "value": n} for n in lines]
    mach_opts  = [{"label": "All", "value": "All"}] + [{"label": n, "value": n} for n in machines]
    shift_opts = [{"label": "All", "value": "All"},
                  {"label": "Morning", "value": "Morning"},
                  {"label": "Evening", "value": "Evening"},
                  {"label": "Night",   "value": "Night"}]
    dd_style = {"fontSize": "13px"}

    return html.Div([
        # Filter row
        html.Div(
            style={"display": "grid", "gridTemplateColumns": "1fr 1fr 1fr", "gap": "10px", "marginBottom": "16px"},
            children=[
                html.Div([html.Div("Shift", style={"fontSize": "11px", "color": "#64748b", "fontWeight": "600", "marginBottom": "4px", "textTransform": "uppercase"}),
                          dcc.Dropdown(id="an-shift", options=shift_opts, value="All", clearable=False, style=dd_style)]),
                html.Div([html.Div("Line", style={"fontSize": "11px", "color": "#64748b", "fontWeight": "600", "marginBottom": "4px", "textTransform": "uppercase"}),
                          dcc.Dropdown(id="an-line", options=line_opts, value="All", clearable=False, style=dd_style)]),
                html.Div([html.Div("Machine", style={"fontSize": "11px", "color": "#64748b", "fontWeight": "600", "marginBottom": "4px", "textTransform": "uppercase"}),
                          dcc.Dropdown(id="an-machine", options=mach_opts, value="All", clearable=False, style=dd_style)]),
            ],
        ),

        # KPI row
        html.Div(
            style={"display": "grid", "gridTemplateColumns": "repeat(4,1fr)", "gap": "12px", "marginBottom": "16px"},
            children=[
                html.Div([html.Div("Total", className="kpi-label"),
                          html.Div(id="an-kpi-total", className="kpi-value", children="–")], className="kpi-card"),
                html.Div([html.Div("Avg Duration", className="kpi-label"),
                          html.Div(id="an-kpi-avg", className="kpi-value", children="–")], className="kpi-card"),
                html.Div([html.Div("Max Duration", className="kpi-label"),
                          html.Div(id="an-kpi-max", className="kpi-value", children="–")], className="kpi-card"),
                html.Div([html.Div("Defect Rate", className="kpi-label"),
                          html.Div(id="an-kpi-defect", className="kpi-value", children="–")], className="kpi-card"),
            ],
        ),

        # Charts row 1
        html.Div(
            style={"display": "grid", "gridTemplateColumns": "2fr 1fr", "gap": "16px", "marginBottom": "16px"},
            children=[
                html.Div([dcc.Graph(id="an-duration-chart", style={"height": "320px"})], className="tb-card"),
                html.Div([dcc.Graph(id="an-shift-chart",    style={"height": "320px"})], className="tb-card"),
            ],
        ),

        # Charts row 2
        html.Div(
            style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "16px"},
            children=[
                html.Div([dcc.Graph(id="an-line-chart",    style={"height": "300px"})], className="tb-card"),
                html.Div([dcc.Graph(id="an-machine-chart", style={"height": "300px"})], className="tb-card"),
            ],
        ),
    ])


# ── ADMIN ───────────────────────────────────────────────────────────────────────

def _page_admin(role: str) -> html.Div:
    if role != "admin":
        return html.Div(
            "⛔  Admin access required.",
            style={"color": "#ef4444", "padding": "40px", "fontWeight": "700", "fontSize": "16px"},
        )

    users    = list_users(BASE_DIR)
    cameras  = list_cameras(BASE_DIR)
    cam_opts = _cam_options()
    lines    = list_lines(BASE_DIR)
    line_opts = [{"label": l.get("name"), "value": l.get("id")} for l in lines]
    flat     = all_machines_flat(BASE_DIR)
    tree_rows = [{"path": f"{m['line_name']}  ›  {m['zone_name']}  ›  {m['machine_name']}",
                  "camera": m.get("camera_id") or "— not assigned —"} for m in flat]

    inp = lambda pid, ph, **kw: _inp(pid, ph, **kw)

    def _section(title, children):
        return html.Div([
            html.Div(title, className="tb-card-title"),
            *children,
        ], className="tb-card", style={"marginBottom": "16px"})

    return html.Div([
        # ── Users ──
        _section("👤  User Management", [
            dash_table.DataTable(
                id="adm-users-table",
                columns=[
                    {"name": "Username",     "id": "username"},
                    {"name": "Role",         "id": "role"},
                    {"name": "Display Name", "id": "display_name"},
                ],
                data=users,
                row_selectable="single",
                style_cell={"textAlign": "left", "fontFamily": "Segoe UI", "fontSize": 13, "padding": "8px 10px"},
                style_header={"backgroundColor": "#f8fafc", "fontWeight": "700"},
                style_table={"marginBottom": "12px"},
            ),
            html.Div(
                style={"display": "grid", "gridTemplateColumns": "1fr 1fr 1fr 1fr", "gap": "8px", "marginBottom": "8px"},
                children=[
                    inp("adm-new-uname", "Username"),
                    dcc.Input(id="adm-new-pw", type="password", placeholder="Password",
                              style={"width": "100%", "padding": "8px 10px", "border": "1px solid #d1d5db",
                                     "borderRadius": "6px", "fontSize": "13px", "boxSizing": "border-box"}),
                    dcc.Dropdown(id="adm-new-role",
                                 options=[{"label": r.capitalize(), "value": r} for r in ROLES],
                                 placeholder="Role", style={"fontSize": "13px"}),
                    inp("adm-new-dname", "Display name"),
                ],
            ),
            html.Div([
                html.Button("Add User",   id="adm-add-user-btn", n_clicks=0,
                            style={"padding": "8px 14px", "background": "#00a651", "color": "#fff",
                                   "border": "none", "borderRadius": "6px", "cursor": "pointer", "fontWeight": "600", "fontSize": "13px", "marginRight": "8px"}),
                html.Button("Delete Selected", id="adm-del-user-btn", n_clicks=0,
                            style={"padding": "8px 14px", "background": "#CC0000", "color": "#fff",
                                   "border": "none", "borderRadius": "6px", "cursor": "pointer", "fontWeight": "600", "fontSize": "13px"}),
            ]),
            html.Div(id="adm-user-msg",
                     style={"marginTop": "8px", "fontSize": "13px", "color": "#334155"}),
        ]),

        # ── Cameras ──
        _section("📷  Camera Management", [
            html.Div(
                style={"display": "grid", "gridTemplateColumns": "repeat(3,1fr)", "gap": "8px", "marginBottom": "10px"},
                children=[
                    inp("adm-cam-name", "Camera name"),
                    inp("adm-cam-ip",   "Camera IP"),
                    inp("adm-cam-user", "Username", value="admin"),
                    dcc.Input(id="adm-cam-pass", type="password", placeholder="Password",
                              style={"width": "100%", "padding": "8px 10px", "border": "1px solid #d1d5db",
                                     "borderRadius": "6px", "fontSize": "13px", "boxSizing": "border-box"}),
                    dcc.Input(id="adm-cam-port", type="number", placeholder="RTSP Port",
                              value=554, min=1, max=65535,
                              style={"width": "100%", "padding": "8px 10px", "border": "1px solid #d1d5db",
                                     "borderRadius": "6px", "fontSize": "13px", "boxSizing": "border-box"}),
                    inp("adm-cam-path", "RTSP path", value="/h264/ch1/main/av_stream"),
                ],
            ),
            html.Div(
                style={"display": "flex", "gap": "8px", "alignItems": "center", "marginBottom": "8px"},
                children=[
                    html.Button("Add Camera", id="adm-add-cam-btn", n_clicks=0,
                                style={"padding": "8px 14px", "background": "#00a651", "color": "#fff",
                                       "border": "none", "borderRadius": "6px", "cursor": "pointer", "fontWeight": "600", "fontSize": "13px"}),
                    html.Button("Delete",  id="adm-del-cam-btn", n_clicks=0,
                                style={"padding": "8px 14px", "background": "#CC0000", "color": "#fff",
                                       "border": "none", "borderRadius": "6px", "cursor": "pointer", "fontWeight": "600", "fontSize": "13px"}),
                    html.Button("Test",    id="adm-test-cam-btn", n_clicks=0,
                                style={"padding": "8px 14px", "background": "#0369a1", "color": "#fff",
                                       "border": "none", "borderRadius": "6px", "cursor": "pointer", "fontWeight": "600", "fontSize": "13px"}),
                    dcc.Dropdown(id="adm-cam-sel", options=cam_opts,
                                 placeholder="Select camera (delete/test)",
                                 style={"fontSize": "13px", "width": "230px"}),
                ],
            ),
            html.Div(id="adm-cam-msg", style={"fontSize": "13px", "color": "#334155"}),
        ]),

        # ── Lines / Zones / Machines ──
        html.Div(
            className="grid-2",
            children=[
                html.Div([
                    html.Div("🏭  Add Line / Zone / Machine", className="tb-card-title"),
                    # Add line
                    html.Div("New Line", style={"fontSize": "12px", "fontWeight": "700", "color": "#64748b", "marginBottom": "4px"}),
                    html.Div([inp("adm-new-line", "Line name"),
                              html.Button("Add Line", id="adm-add-line-btn", n_clicks=0,
                                          style={"padding": "8px 14px", "background": "#0369a1", "color": "#fff",
                                                 "border": "none", "borderRadius": "6px", "cursor": "pointer", "fontWeight": "600", "fontSize": "13px", "whiteSpace": "nowrap"})],
                             style={"display": "flex", "gap": "8px", "marginBottom": "12px"}),
                    # Add zone
                    html.Hr(style={"borderColor": "#f1f5f9"}),
                    html.Div("New Zone", style={"fontSize": "12px", "fontWeight": "700", "color": "#64748b", "margin": "8px 0 4px"}),
                    dcc.Dropdown(id="adm-zone-line", options=line_opts, placeholder="Parent line", style={"marginBottom": "6px", "fontSize": "13px"}),
                    html.Div([inp("adm-new-zone", "Zone name"),
                              html.Button("Add Zone", id="adm-add-zone-btn", n_clicks=0,
                                          style={"padding": "8px 14px", "background": "#0369a1", "color": "#fff",
                                                 "border": "none", "borderRadius": "6px", "cursor": "pointer", "fontWeight": "600", "fontSize": "13px", "whiteSpace": "nowrap"})],
                             style={"display": "flex", "gap": "8px", "marginBottom": "12px"}),
                    # Add machine
                    html.Hr(style={"borderColor": "#f1f5f9"}),
                    html.Div("New Machine", style={"fontSize": "12px", "fontWeight": "700", "color": "#64748b", "margin": "8px 0 4px"}),
                    dcc.Dropdown(id="adm-mach-line", options=line_opts, placeholder="Line", style={"marginBottom": "6px", "fontSize": "13px"}),
                    dcc.Dropdown(id="adm-mach-zone", options=[], placeholder="Zone", style={"marginBottom": "6px", "fontSize": "13px"}),
                    html.Div([inp("adm-new-mach", "Machine name"),
                              html.Button("Add Machine", id="adm-add-mach-btn", n_clicks=0,
                                          style={"padding": "8px 14px", "background": "#0369a1", "color": "#fff",
                                                 "border": "none", "borderRadius": "6px", "cursor": "pointer", "fontWeight": "600", "fontSize": "13px", "whiteSpace": "nowrap"})],
                             style={"display": "flex", "gap": "8px", "marginBottom": "6px"}),
                    dcc.Dropdown(id="adm-mach-cam", options=cam_opts, placeholder="Assign camera (optional)", style={"marginBottom": "12px", "fontSize": "13px"}),
                    html.Div(id="adm-zone-msg", style={"fontSize": "13px", "color": "#334155"}),
                ], className="tb-card"),

                html.Div([
                    html.Div("🗺  Machine → Camera Map", className="tb-card-title"),
                    dash_table.DataTable(
                        id="adm-tree-table",
                        columns=[
                            {"name": "Line  ›  Zone  ›  Machine", "id": "path"},
                            {"name": "Camera assigned",           "id": "camera"},
                        ],
                        data=tree_rows,
                        style_cell={"textAlign": "left", "fontFamily": "Segoe UI", "fontSize": 12, "padding": "7px 10px"},
                        style_header={"backgroundColor": "#f8fafc", "fontWeight": "700"},
                        style_table={"overflowX": "auto"},
                    ),
                ], className="tb-card"),
            ],
        ),
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# ROOT LAYOUT
# ═══════════════════════════════════════════════════════════════════════════════

app.layout = html.Div([
    dcc.Location(id="url", refresh=False),
    dcc.Store(id="user-store", storage_type="session"),
    html.Div(id="app-root"),
])


# ═══════════════════════════════════════════════════════════════════════════════
# CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════════

# ── 1. Render login OR app shell ──────────────────────────────────────────────

@app.callback(
    Output("app-root", "children"),
    Input("user-store", "data"),
    Input("url", "pathname"),
)
def render_app(user, pathname):
    if not user or not user.get("logged_in"):
        return _login_layout()
    return _app_shell(user, pathname or "/dashboard")


# ── 2. Login ──────────────────────────────────────────────────────────────────

@app.callback(
    Output("user-store", "data"),
    Output("login-error", "children"),
    Input("login-btn", "n_clicks"),
    Input("login-username", "n_submit"),
    Input("login-password", "n_submit"),
    State("login-username", "value"),
    State("login-password", "value"),
    prevent_initial_call=True,
)
def handle_login(_n, _su, _sp, username, password):
    if not username or not password:
        return no_update, "Enter username and password"
    ok, user = authenticate((username or "").strip(), password or "", BASE_DIR)
    if not ok:
        return no_update, "❌  Invalid username or password"
    return {
        "logged_in": True,
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "display_name": user.get("display_name", user["username"]),
    }, ""


# ── 3. Health (topbar badges + alert) ────────────────────────────────────────

@app.callback(
    Output("health-badges", "children"),
    Output("health-alert",  "children"),
    Input("main-interval", "n_intervals"),
    prevent_initial_call=False,
)
def update_health(_n):
    health = mgr.health()
    badges = []
    any_down = False
    for cid, ok in health.items():
        cam = next((c for c in list_cameras(BASE_DIR) if str(c.get("id", "")) == cid), None)
        name = cam.get("name", cid) if cam else cid
        color = "#22c55e" if ok else "#ef4444"
        badges.append(html.Span(
            f"● {name}",
            style={"fontSize": "12px", "fontWeight": "600", "color": color},
        ))
        if not ok:
            any_down = True

    alert = (
        html.Div("⚠  One or more cameras are offline. Auto-reconnect is active.",
                 className="tb-alert")
        if any_down else html.Div()
    )
    return badges, alert


# ── 4. Dashboard Overview KPIs + content ─────────────────────────────────────

@app.callback(
    Output("ov-kpi-total",  "children"),
    Output("ov-kpi-avg",    "children"),
    Output("ov-kpi-cams",   "children"),
    Output("ov-kpi-rec",    "children"),
    Output("ov-line-health","children"),
    Output("ov-recent-cycles","children"),
    Input("main-interval", "n_intervals"),
    prevent_initial_call=False,
)
def update_overview(_n):
    cycles = read_cycles()
    total = len(cycles)
    avg = round(sum(c["duration"] for c in cycles) / total, 1) if total else 0

    health = mgr.health()
    cams_on = sum(1 for v in health.values() if v)

    active_recs = 0
    for s in mgr.streams.values():
        if s.status()["recording"]:
            active_recs += 1

    # Line health cards
    lines = list_lines(BASE_DIR)
    lh_cards = []
    for ln in lines:
        line_id = ln.get("id", "")
        line_name = ln.get("name", line_id)
        machines = [m for m in all_machines_flat(BASE_DIR) if m["line_id"] == line_id]
        total_cams = sum(1 for m in machines if m.get("camera_id"))
        online_cams = sum(
            1 for m in machines
            if m.get("camera_id") and health.get(m["camera_id"], False)
        )
        recording = sum(
            1 for m in machines
            if m.get("camera_id") and mgr.streams.get(m["camera_id"]) and
            mgr.streams[m["camera_id"]].status()["recording"]
        )
        lh_cards.append(html.Div(
            className="line-health-card",
            children=[
                html.Div(line_name, className="line-health-name"),
                html.Div([
                    html.Span(f"{len(machines)} machines"),
                    html.Span(f"{online_cams}/{total_cams} cameras online"),
                ], className="line-health-row"),
                html.Div(f"⏺ {recording} recording now" if recording else "All idle",
                         className="line-health-rec" if recording else "kpi-sub"),
            ],
        ))

    lh_grid = html.Div(
        lh_cards,
        style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "10px"},
    ) if lh_cards else html.Div("No lines configured.",
                                style={"color": "#94a3b8", "fontSize": "13px"})

    # Recent cycles
    recent = cycles[-10:] if cycles else []
    recent_rows = [
        html.Div(
            style={"display": "flex", "justifyContent": "space-between",
                   "padding": "7px 0", "borderBottom": "1px solid #f1f5f9",
                   "fontSize": "13px"},
            children=[
                html.Span(f"Cycle {c['cycle_number']}  ·  {c.get('machine_name', '–')}",
                          style={"color": "#334155"}),
                html.Span(f"{c['duration']}s  |  {c.get('shift', '')}  {c.get('tag', '')}",
                          style={"color": "#94a3b8"}),
            ],
        )
        for c in reversed(recent)
    ] if recent else [html.Div("No cycles recorded yet.",
                               style={"color": "#94a3b8", "fontSize": "13px"})]

    return (str(total), f"{avg} s", str(cams_on), str(active_recs),
            lh_grid, html.Div(recent_rows))


# ── 5. Line Monitor – line selector → store ───────────────────────────────────

@app.callback(
    Output("line-select-store", "data"),
    Input({"type": "line-btn", "line_id": ALL}, "n_clicks"),
    State({"type": "line-btn", "line_id": ALL}, "id"),
    prevent_initial_call=True,
)
def select_line(n_clicks_list, btn_ids):
    if not callback_context.triggered:
        return no_update
    prop = callback_context.triggered[0]["prop_id"]
    try:
        id_str = prop.rsplit(".", 1)[0]
        bid = json.loads(id_str)
        return bid.get("line_id")
    except Exception:
        return no_update


# ── 6. Update line button active class ───────────────────────────────────────

@app.callback(
    Output({"type": "line-btn", "line_id": ALL}, "className"),
    Input("line-select-store", "data"),
    State({"type": "line-btn", "line_id": ALL}, "id"),
    prevent_initial_call=False,
)
def style_line_buttons(selected, btn_ids):
    return [
        "line-btn active" if b.get("line_id") == selected else "line-btn"
        for b in (btn_ids or [])
    ]


# ── 7. Render machine grid when line changes ──────────────────────────────────

@app.callback(
    Output("machine-grid-container", "children"),
    Input("line-select-store", "data"),
    prevent_initial_call=False,
)
def render_machine_grid(line_id: Optional[str]):
    if not line_id:
        return html.Div(
            "← Select a production line above to see its machines.",
            style={"color": "#94a3b8", "fontSize": "14px", "padding": "40px 0",
                   "textAlign": "center"},
        )

    machines = [m for m in all_machines_flat(BASE_DIR) if m["line_id"] == line_id]
    if not machines:
        return html.Div(
            "No machines in this line. Add them under Admin → Lines & Machines.",
            style={"color": "#94a3b8", "fontSize": "14px", "padding": "40px 0",
                   "textAlign": "center"},
        )

    # Ensure streams are started for all cameras on this line
    for m in machines:
        cid = m.get("camera_id")
        if cid:
            mgr.ensure_stream(cid)

    cards = [_make_machine_card(m) for m in machines]
    ncols = min(len(cards), 3)
    col_tpl = " ".join(["1fr"] * ncols)
    return html.Div(
        children=cards,
        style={"display": "grid", "gridTemplateColumns": col_tpl, "gap": "16px"},
    )


# ── 8. Toggle cycle button (MATCH) ────────────────────────────────────────────

@app.callback(
    Output({"type": "last-toggle-ts", "machine_id": MATCH}, "data"),
    Output({"type": "cycle-msg",       "machine_id": MATCH}, "children"),
    Input({"type": "toggle-btn",   "machine_id": MATCH}, "n_clicks"),
    State({"type": "toggle-btn",   "machine_id": MATCH}, "id"),
    State({"type": "machine-meta", "machine_id": MATCH}, "data"),
    prevent_initial_call=True,
)
def toggle_cycle(_n, btn_id, machine_data):
    machine_id = btn_id.get("machine_id", "")
    camera_id  = (machine_data or {}).get("camera_id")

    if not camera_id:
        return time.time(), "⚠  No camera assigned to this machine"

    stream = mgr.ensure_stream(camera_id)
    if stream is None:
        return time.time(), "❌  Could not connect to camera"

    ok, msg = stream.toggle_cycle(machine_data or {})
    icon = "✅" if ok else "❌"
    return time.time(), f"{icon}  {msg}"


# ── 9. Timer + badge + button ALL update (every second) ───────────────────────

@app.callback(
    Output({"type": "cycle-timer",      "machine_id": ALL}, "children"),
    Output({"type": "cycle-timer",      "machine_id": ALL}, "className"),
    Output({"type": "toggle-btn",       "machine_id": ALL}, "children"),
    Output({"type": "toggle-btn",       "machine_id": ALL}, "className"),
    Output({"type": "status-badge",     "machine_id": ALL}, "children"),
    Output({"type": "status-badge",     "machine_id": ALL}, "className"),
    Output({"type": "cycle-meta",       "machine_id": ALL}, "children"),
    Input("second-interval", "n_intervals"),
    State({"type": "machine-meta",      "machine_id": ALL}, "data"),
    State({"type": "toggle-btn",        "machine_id": ALL}, "disabled"),
    prevent_initial_call=False,
)
def update_all_machines(_n, machine_data_list, disabled_list):
    timers, timer_cls = [], []
    btn_texts, btn_cls = [], []
    badges, badge_cls = [], []
    meta_texts = []

    for idx, mdata in enumerate(machine_data_list or []):
        camera_id = (mdata or {}).get("camera_id")
        disabled  = (disabled_list or [False] * (idx + 1))[idx]

        if not camera_id or disabled:
            timers.append("––:––")
            timer_cls.append("machine-timer")
            btn_texts.append("No Camera")
            btn_cls.append("cycle-btn no-cam")
            badges.append("● NO CAM")
            badge_cls.append("badge badge-no-cam")
            meta_texts.append("")
            continue

        stream = mgr.get(camera_id)
        if stream is None:
            timers.append("OFFLINE")
            timer_cls.append("machine-timer")
            btn_texts.append("▶  START CYCLE")
            btn_cls.append("cycle-btn start")
            badges.append("● OFFLINE")
            badge_cls.append("badge badge-offline")
            meta_texts.append("")
            continue

        st = stream.status()

        if not st["connected"]:
            timers.append("OFFLINE")
            timer_cls.append("machine-timer")
            btn_texts.append("▶  START CYCLE")
            btn_cls.append("cycle-btn start")
            badges.append("● OFFLINE")
            badge_cls.append("badge badge-offline")
            meta_texts.append("")
            continue

        if st["recording"] and st.get("start_iso"):
            try:
                start_dt = datetime.fromisoformat(st["start_iso"])
                elapsed  = int((datetime.now() - start_dt).total_seconds())
                mm, ss = divmod(elapsed, 60)
                hh, mm = divmod(mm, 60)
                timer_str = f"{hh:02d}:{mm:02d}:{ss:02d}" if hh else f"{mm:02d}:{ss:02d}"
            except Exception:
                timer_str = "00:00"

            machine_name = (mdata or {}).get("machine_name", "")
            day_count = _machine_today_count(machine_name)

            timers.append(f"⏱  {timer_str}")
            timer_cls.append("machine-timer rec")
            btn_texts.append(f"⟳  END & NEXT  (Cycle {st['current_cycle']})")
            btn_cls.append("cycle-btn recording")
            badges.append("⏺  RECORDING")
            badge_cls.append("badge badge-recording")
            meta_texts.append(f"Today: {day_count} cycles")
        else:
            machine_name = (mdata or {}).get("machine_name", "")
            day_count = _machine_today_count(machine_name)

            timers.append("––:––")
            timer_cls.append("machine-timer")
            btn_texts.append("▶  START CYCLE")
            btn_cls.append("cycle-btn start")
            badges.append("●  LIVE")
            badge_cls.append("badge badge-live")
            meta_texts.append(f"Today: {day_count} cycles")

    return (timers, timer_cls, btn_texts, btn_cls, badges, badge_cls, meta_texts)


# ── 10. Cycle history table ───────────────────────────────────────────────────

@app.callback(
    Output("hist-table",     "data"),
    Output("hist-row-count", "children"),
    Input("analytic-interval", "n_intervals"),
    Input("hist-shift",   "value"),
    Input("hist-line",    "value"),
    Input("hist-machine", "value"),
    Input("hist-tag",     "value"),
    Input("hist-search",  "value"),
    prevent_initial_call=False,
)
def update_hist_table(_n, shift, line, machine, tag, search):
    cycles   = read_cycles()
    filtered = _filter_cycles(cycles, shift or "All", line or "All",
                              machine or "All", tag or "All", search or "")
    data = [
        {k: c[k] for k in ("cycle_number", "duration", "start_time", "end_time",
                            "line_name", "zone_name", "machine_name", "shift", "tag")}
        for c in filtered
    ]
    return data, f"{len(filtered)} cycles shown"


# ── 11. Apply tag in history ──────────────────────────────────────────────────

@app.callback(
    Output("hist-tag-msg", "children"),
    Input("hist-apply-tag-btn", "n_clicks"),
    State("hist-table",     "selected_rows"),
    State("hist-table",     "data"),
    State("hist-tag-apply", "value"),
    prevent_initial_call=True,
)
def apply_hist_tag(_n, sel_rows, table_data, tag):
    if not sel_rows or not table_data:
        return "⚠  Select a row first"
    if not tag:
        return "⚠  Select a tag"
    cycle_number = table_data[sel_rows[0]].get("cycle_number")
    ok, msg = update_cycle_tag(METADATA_CSV, cycle_number, tag)
    return f"{'✅' if ok else '❌'}  {msg}"


# ── 12. CSV download ──────────────────────────────────────────────────────────

@app.callback(
    Output("hist-dl", "data"),
    Input("hist-dl-btn", "n_clicks"),
    prevent_initial_call=True,
)
def download_csv(_n):
    if os.path.exists(METADATA_CSV):
        return dcc.send_file(METADATA_CSV)
    return dcc.send_string("", "cycles.csv")


# ── 13. Video modal ────────────────────────────────────────────────────────────

@app.callback(
    Output("video-modal",       "style"),
    Output("video-modal",       "className"),
    Output("modal-video-player","src"),
    Output("modal-title",       "children"),
    Output("modal-meta",        "children"),
    Input("hist-table",         "active_cell"),
    Input("modal-close-btn",    "n_clicks"),
    State("hist-table",         "data"),
    prevent_initial_call=True,
)
def handle_video_modal(active_cell, close_n, table_data):
    hidden  = {"display": "none"}
    visible = {}   # CSS class video-modal-overlay handles display:flex

    trigger = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else ""

    if trigger == "modal-close-btn":
        return hidden, "video-modal-overlay", "", "Cycle Video", []

    if not active_cell or not table_data:
        return hidden, "video-modal-overlay", "", "Cycle Video", []

    row = table_data[active_cell["row"]]
    fp  = (row.get("file_path") or "").strip().replace("\\", "/").lstrip("/")
    if not fp:
        return hidden, "video-modal-overlay", "", "Cycle Video", []

    full = os.path.join(BASE_DIR, fp)
    if not os.path.exists(full):
        return hidden, "video-modal-overlay", "", f"Video not found: {fp}", []

    src   = f"/cycle-video/{fp}?t={int(time.time()*1000)}"
    title = f"Cycle {row.get('cycle_number')}  ·  {row.get('machine_name', '')}  ·  {row.get('line_name', '')}"

    def _tag(label: str, value: str) -> html.Span:
        return html.Span(
            [html.Span(f"{label}: ", style={"color": "#64748b", "fontSize": "11px"}),
             html.Span(value or "—",  style={"color": "#e2e8f0", "fontWeight": "600", "fontSize": "11px"})],
            style={"marginRight": "16px"},
        )

    meta = [
        _tag("Cycle",   str(row.get("cycle_number", ""))),
        _tag("Machine", row.get("machine_name", "")),
        _tag("Line",    row.get("line_name", "")),
        _tag("Zone",    row.get("zone_name", "")),
        _tag("Shift",   row.get("shift", "")),
        _tag("Duration",f"{row.get('duration', '')} s"),
        _tag("Tag",     row.get("tag", "")),
    ]

    return visible, "video-modal-overlay", src, title, meta


# ── 14. Analytics charts ──────────────────────────────────────────────────────

@app.callback(
    Output("an-kpi-total",      "children"),
    Output("an-kpi-avg",        "children"),
    Output("an-kpi-max",        "children"),
    Output("an-kpi-defect",     "children"),
    Output("an-duration-chart", "figure"),
    Output("an-shift-chart",    "figure"),
    Output("an-line-chart",     "figure"),
    Output("an-machine-chart",  "figure"),
    Input("analytic-interval",  "n_intervals"),
    Input("an-shift",   "value"),
    Input("an-line",    "value"),
    Input("an-machine", "value"),
    prevent_initial_call=False,
)
def update_analytics(_n, shift, line, machine):
    all_cycles = read_cycles()
    filtered   = _filter_cycles(all_cycles, shift or "All", line or "All",
                                machine or "All", "All", "")

    total   = len(filtered)
    avg     = round(sum(c["duration"] for c in filtered) / total, 1) if total else 0
    max_dur = round(max((c["duration"] for c in filtered), default=0), 1)
    defects = sum(1 for c in filtered if c["tag"] == "Defect")
    defect_pct = f"{round(defects/total*100, 1)}%" if total else "0%"

    base = dict(template="plotly_white",
                paper_bgcolor="#fff", plot_bgcolor="#fafbfc",
                margin=dict(l=40, r=10, t=40, b=40),
                font=dict(family="Segoe UI", size=12, color="#334155"))

    # Duration trend
    if filtered:
        tag_color = {"Normal": "#22c55e", "Defect": "#ef4444",
                     "Rework": "#f59e0b", "Hold": "#6366f1", "": "#CC0000"}
        mc = [tag_color.get(c["tag"], "#CC0000") for c in filtered]
        dur_fig = go.Figure()
        dur_fig.add_trace(go.Scatter(
            x=[c["cycle_number"] for c in filtered],
            y=[c["duration"]     for c in filtered],
            mode="lines+markers",
            line=dict(color="#CC0000", width=2),
            marker=dict(size=7, color=mc, line=dict(width=1.5, color="#fff")),
            customdata=[[c["machine_name"], c["line_name"], c["shift"], c["tag"]]
                        for c in filtered],
            hovertemplate=(
                "Cycle %{x}  ·  %{y} s<br>"
                "Machine: %{customdata[0]}<br>Line: %{customdata[1]}<br>"
                "Shift: %{customdata[2]}  ·  Tag: %{customdata[3]}<extra></extra>"
            ),
        ))
        dur_fig.update_layout(title="Cycle Duration Trend",
                              xaxis_title="Cycle #", yaxis_title="Duration (s)", **base)
    else:
        dur_fig = go.Figure()
        dur_fig.update_layout(title="Cycle Duration Trend", **base)
        dur_fig.add_annotation(text="No data", x=0.5, y=0.5, xref="paper", yref="paper",
                               showarrow=False, font=dict(size=14, color="#94a3b8"))

    # Shift bar
    shift_data: Dict[str, list] = {"Morning": [], "Evening": [], "Night": []}
    for c in all_cycles:
        if c["shift"] in shift_data:
            shift_data[c["shift"]].append(c["duration"])
    shift_avgs = {s: round(sum(v)/len(v), 1) if v else 0 for s, v in shift_data.items()}
    shift_cnt  = {s: len(v) for s, v in shift_data.items()}

    shift_fig = go.Figure(go.Bar(
        x=list(shift_avgs.keys()),
        y=list(shift_avgs.values()),
        marker_color=["#CC0000", "#f59e0b", "#0369a1"],
        text=[f"Avg {v}s<br>({shift_cnt[k]} cycles)" for k, v in shift_avgs.items()],
        textposition="auto",
    ))
    shift_fig.update_layout(title="Avg Duration by Shift",
                            yaxis_title="Seconds", **base)

    # Line bar
    line_data: Dict[str, list] = {}
    for c in all_cycles:
        ln = c["line_name"] or "?"
        line_data.setdefault(ln, []).append(c["duration"])
    line_avgs = {ln: round(sum(v)/len(v), 1) for ln, v in line_data.items()}

    line_fig = go.Figure(go.Bar(
        x=list(line_avgs.keys()),
        y=list(line_avgs.values()),
        marker_color="#CC0000",
        text=[f"{v}s" for v in line_avgs.values()],
        textposition="auto",
    ))
    line_fig.update_layout(title="Avg Duration by Line", yaxis_title="Seconds", **base)

    # Machine bar
    mach_data: Dict[str, list] = {}
    for c in all_cycles:
        mn = c["machine_name"] or "?"
        mach_data.setdefault(mn, []).append(c["duration"])
    mach_avgs = {mn: round(sum(v)/len(v), 1) for mn, v in mach_data.items()}

    mach_fig = go.Figure(go.Bar(
        x=list(mach_avgs.keys()),
        y=list(mach_avgs.values()),
        marker_color="#0369a1",
        text=[f"{v}s" for v in mach_avgs.values()],
        textposition="auto",
    ))
    mach_fig.update_layout(title="Avg Duration by Machine",
                           yaxis_title="Seconds", **base)

    return (str(total), f"{avg} s", f"{max_dur} s", defect_pct,
            dur_fig, shift_fig, line_fig, mach_fig)


# ── 15. Admin: user management ────────────────────────────────────────────────

@app.callback(
    Output("adm-users-table", "data"),
    Output("adm-user-msg",    "children"),
    Input("adm-add-user-btn", "n_clicks"),
    Input("adm-del-user-btn", "n_clicks"),
    State("adm-new-uname",  "value"),
    State("adm-new-pw",     "value"),
    State("adm-new-role",   "value"),
    State("adm-new-dname",  "value"),
    State("adm-users-table","selected_rows"),
    State("adm-users-table","data"),
    prevent_initial_call=True,
)
def admin_users(add_n, del_n, uname, pw, role, dname, sel_rows, tdata):
    trigger = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else ""
    if trigger == "adm-add-user-btn":
        if not uname or not pw or not role:
            return no_update, "⚠  Username, password and role required"
        ok, msg = add_user(uname.strip(), pw, role, dname or "", BASE_DIR)
        return list_users(BASE_DIR), f"{'✅' if ok else '❌'}  {msg}"
    if trigger == "adm-del-user-btn":
        if not sel_rows or not tdata:
            return no_update, "⚠  Select a user to delete"
        uid = tdata[sel_rows[0]].get("id", "")
        ok, msg = delete_user(uid, BASE_DIR)
        return list_users(BASE_DIR), f"{'✅' if ok else '❌'}  {msg}"
    return no_update, ""


# ── 16. Admin: camera management ──────────────────────────────────────────────

@app.callback(
    Output("adm-cam-msg", "children"),
    Input("adm-add-cam-btn",  "n_clicks"),
    Input("adm-del-cam-btn",  "n_clicks"),
    Input("adm-test-cam-btn", "n_clicks"),
    State("adm-cam-name", "value"),
    State("adm-cam-ip",   "value"),
    State("adm-cam-user", "value"),
    State("adm-cam-pass", "value"),
    State("adm-cam-port", "value"),
    State("adm-cam-path", "value"),
    State("adm-cam-sel",  "value"),
    prevent_initial_call=True,
)
def admin_cameras(add_n, del_n, test_n, name, ip, user, pw, port, path, sel_id):
    trigger = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else ""

    if trigger == "adm-add-cam-btn":
        ok, msg, cam_id = add_camera(
            name=name or "", ip=ip or "", username=user or "",
            password=pw or "", path=path or "", port=int(port or 554),
            base_dir=BASE_DIR,
        )
        if ok and cam_id:
            rtsp = get_camera_rtsp_url(cam_id, BASE_DIR)
            if rtsp:
                mgr.start_stream(cam_id, rtsp)
        return f"{'✅' if ok else '❌'}  {msg}"

    if trigger == "adm-del-cam-btn":
        if not sel_id:
            return "⚠  Select a camera to delete"
        mgr.stop_stream(sel_id)
        ok, msg = delete_camera(sel_id, BASE_DIR)
        return f"{'✅' if ok else '❌'}  {msg}"

    if trigger == "adm-test-cam-btn":
        if sel_id:
            rtsp = get_camera_rtsp_url(sel_id, BASE_DIR)
        elif ip and user and pw:
            p = (path or "/h264/ch1/main/av_stream").strip()
            if not p.startswith("/"):
                p = "/" + p
            rtsp = f"rtsp://{user}:{pw}@{ip}:{int(port or 554)}{p}"
        else:
            return "⚠  Select camera or fill IP/user/pass"
        cap, url, _ = open_rtsp_capture(rtsp)
        if cap:
            cap.release()
            return f"✅  Test OK: {url}"
        return "❌  Camera test failed"

    return ""


# ── 17. Admin: zone cascade + add line/zone/machine ───────────────────────────

@app.callback(
    Output("adm-mach-zone", "options"),
    Input("adm-mach-line",  "value"),
)
def cascade_admin_zones(line_id):
    if not line_id:
        return []
    return [{"label": z.get("name"), "value": z.get("id")}
            for z in get_zones_for_line(line_id, BASE_DIR)]


@app.callback(
    Output("adm-tree-table", "data"),
    Output("adm-zone-msg",   "children"),
    Input("adm-add-line-btn", "n_clicks"),
    Input("adm-add-zone-btn", "n_clicks"),
    Input("adm-add-mach-btn", "n_clicks"),
    State("adm-new-line",   "value"),
    State("adm-zone-line",  "value"),
    State("adm-new-zone",   "value"),
    State("adm-mach-line",  "value"),
    State("adm-mach-zone",  "value"),
    State("adm-new-mach",   "value"),
    State("adm-mach-cam",   "value"),
    prevent_initial_call=True,
)
def admin_zones(al, az, am, lname, zline, zname, mline, mzone, mname, mcam):
    trigger = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else ""

    def _tree():
        flat = all_machines_flat(BASE_DIR)
        return [{"path": f"{m['line_name']}  ›  {m['zone_name']}  ›  {m['machine_name']}",
                 "camera": m.get("camera_id") or "— not assigned —"} for m in flat]

    if trigger == "adm-add-line-btn":
        ok, msg, _ = add_line(lname or "", BASE_DIR)
        return _tree(), f"{'✅' if ok else '❌'}  {msg}"

    if trigger == "adm-add-zone-btn":
        if not zline:
            return no_update, "⚠  Select parent line"
        ok, msg, _ = add_zone(zline, zname or "", BASE_DIR)
        return _tree(), f"{'✅' if ok else '❌'}  {msg}"

    if trigger == "adm-add-mach-btn":
        if not mline or not mzone:
            return no_update, "⚠  Select line and zone"
        ok, msg, _ = add_machine(
            mline, mzone, mname or "",
            camera_id=mcam if mcam else None,
            base_dir=BASE_DIR,
        )
        return _tree(), f"{'✅' if ok else '❌'}  {msg}"

    return no_update, ""


# ─── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=False)
