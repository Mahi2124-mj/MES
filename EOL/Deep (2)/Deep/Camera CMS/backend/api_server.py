from __future__ import annotations

import csv
import os
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, List

import cv2
from flask import Flask, jsonify, request, send_file, Response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from auth import authenticate, create_token, require_auth, require_role
from camera_config import add_camera, delete_camera, list_cameras, get_camera_rtsp_url, update_camera
from cycle_state import (
    end_cycle, get_all_states, get_machine_state,
    get_next_cycle_num, start_cycle,
)
from plc_config import list_plcs, add_plc, update_plc, delete_plc, load_plc_config, update_plc_config
from shifts_config import list_shifts, add_or_update_shift, delete_shift
from camera_bindings import list_bindings, add_binding, delete_binding
from recorder import DEFAULT_METADATA_CSV, ensure_metadata_file, open_rtsp_capture
from settings_config import get_settings, save_videos_dir
from mes_sync import pull_from_mes
from recorder_engine import RecordingManager, ffmpeg_available, FFMPEG_EXE
from cycle_events import append_cycle as ce_append, list_cycles as ce_list, get_cycle as ce_get
from zone_config import (
    list_zones, get_lines_for_zone, get_machines_for_line,
    add_zone, add_line, add_machine,
    delete_zone, delete_line, delete_machine,
    rename_zone, rename_line, rename_machine,
    assign_camera, all_machines_flat, list_all_lines_flat
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, DEFAULT_METADATA_CSV)

app = Flask(__name__)
# Allowed origins come from the ALLOWED_ORIGINS env var (comma-separated).
# The legacy localhost set stays as a default so dev runs work out of the
# box; production should set ALLOWED_ORIGINS=https://eol.tbdi.com,...
_DEFAULT_ORIGINS = ("http://localhost:5173,http://127.0.0.1:5173,"
                    "http://localhost:3000,http://127.0.0.1:3000")
_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", _DEFAULT_ORIGINS).split(",") if o.strip()]
CORS(app, resources={r"/api/*": {"origins": _origins}})

# ─── Rate limiter ─────────────────────────────────────────────────────────────
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],           # no global limit — per-route only
    storage_uri="memory://",
)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _json_ok(data: Any = None, message: str = "OK", status: int = 200):
    return jsonify({"ok": True, "message": message, "data": data}), status


def _json_err(message: str, status: int = 400):
    return jsonify({"ok": False, "message": message}), status


def _read_cycles(limit: int | None = None) -> List[Dict[str, Any]]:
    ensure_metadata_file(CSV_PATH)
    binding_map = {
        str(binding.get("machine_id", "")): int(binding.get("target_time", 0) or 0)
        for binding in list_bindings(BASE_DIR)
        if str(binding.get("machine_id", "")).strip()
    }
    rows: List[Dict[str, Any]] = []
    with open(CSV_PATH, "r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            machine_id = row.get("machine_id", "")
            rows.append({
                "cycle_number": int(row.get("cycle_number", 0) or 0),
                "start_time": row.get("start_time", ""),
                "end_time": row.get("end_time", ""),
                "duration": int(float(row.get("duration", 0) or 0)),
                "file_path": row.get("file_path", ""),
                "machine_id": machine_id,
                "machine_name": row.get("machine_name", ""),
                "line_name": row.get("line_name", ""),
                "zone_name": row.get("zone_name", ""),
                "shift": row.get("shift", ""),
                "tag": row.get("tag", ""),
                "target_time": binding_map.get(machine_id),
            })
    rows.sort(key=lambda item: item["cycle_number"], reverse=True)
    return rows[:limit] if limit else rows


def _camera_name_map() -> Dict[str, str]:
    return {str(cam.get("id", "")): str(cam.get("name", "")) for cam in list_cameras(BASE_DIR)}


def _binding_camera_map() -> Dict[str, str]:
    return {
        str(binding.get("machine_id", "")): str(binding.get("camera_id", "")).strip()
        for binding in list_bindings(BASE_DIR)
        if str(binding.get("machine_id", "")).strip()
    }


def _flat_zones() -> List[Dict[str, Any]]:
    zones = []
    for z in list_zones(BASE_DIR):
        zones.append({
            "zone_id": z.get("id", ""),
            "zone_name": z.get("name", ""),
            "line_count": len(z.get("lines", []))
        })
    return zones

def _flat_machines() -> List[Dict[str, str]]:
    camera_names = _camera_name_map()
    binding_camera_map = _binding_camera_map()
    rows = []
    for machine in all_machines_flat(BASE_DIR):
        camera_id = str(machine.get("camera_id") or binding_camera_map.get(machine.get("machine_id", ""), "")).strip()
        rows.append({
            **machine,
            "camera_id": camera_id,
            "camera_name": camera_names.get(camera_id, "Unassigned"),
        })
    return rows

def _hierarchy() -> List[Dict[str, Any]]:
    camera_names = _camera_name_map()
    result = []
    for zone in list_zones(BASE_DIR):
        zone_payload = {"id": zone.get("id", ""), "name": zone.get("name", ""), "lines": []}
        for line in zone.get("lines", []):
            line_payload = {"id": line.get("id", ""), "name": line.get("name", ""), "machines": []}
            for machine in line.get("machines", []):
                line_payload["machines"].append({
                    "id": machine.get("id", ""),
                    "name": machine.get("name", ""),
                    "camera_id": machine.get("camera_id"),
                    "camera_name": camera_names.get(str(machine.get("camera_id", "")), "Unassigned"),
                })
            zone_payload["lines"].append(line_payload)
        result.append(zone_payload)
    return result

def _camera_grid() -> List[Dict[str, Any]]:
    """Return flat list of machines enriched with camera + cycle state."""
    camera_names = _camera_name_map()
    cameras_map: Dict[str, Dict] = {str(c.get("id", "")): c for c in list_cameras(BASE_DIR)}
    binding_camera_map = _binding_camera_map()
    cycle_states = get_all_states(BASE_DIR)
    rows = []
    for machine in all_machines_flat(BASE_DIR):
        cam_id = str(machine.get("camera_id") or binding_camera_map.get(machine.get("machine_id", ""), "")).strip()
        cam = cameras_map.get(cam_id, {})
        cstate = cycle_states.get(machine.get("machine_id", ""), {})
        rows.append({
            "zone_id": machine["zone_id"],
            "zone_name": machine["zone_name"],
            "line_id": machine["line_id"],
            "line_name": machine["line_name"],
            "machine_id": machine["machine_id"],
            "machine_name": machine["machine_name"],
            "camera_id": cam_id,
            "camera_name": camera_names.get(cam_id, "Unassigned"),
            "camera_ip": cam.get("ip", ""),
            "camera_port": cam.get("port", 554),
            "has_camera": bool(cam_id and cam),
            "recording": cstate.get("recording", False),
            "cycle_number": cstate.get("cycle_number"),
            "cycle_start": cstate.get("start_time"),
        })
    return rows


def _validate_name(val: str, field: str = "Name", max_len: int = 80) -> tuple[bool, str]:
    """Validate a name field — non-empty and within max length."""
    val = val.strip()
    if not val:
        return False, f"{field} is required"
    if len(val) > max_len:
        return False, f"{field} must be {max_len} characters or fewer"
    return True, val


# ─── routes ───────────────────────────────────────────────────────────────────

@app.get("/api/health")
def healthcheck():
    return _json_ok({"status": "healthy"})


# ─── Auth ─────────────────────────────────────────────────────────────────────

@app.post("/api/auth/login")
@limiter.limit("10 per minute")   # brute-force protection
def login():
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    if not username or not password:
        return _json_err("Username and password are required")
    ok, user = authenticate(username, password, BASE_DIR)
    if not ok or not user:
        return _json_err("Invalid username or password", 401)
    token = create_token(user)
    return _json_ok({
        "token": token,
        "id": user.get("id"),
        "username": user.get("username"),
        "role": user.get("role"),
        "display_name": user.get("display_name", user.get("username")),
    }, "Login successful")


@app.get("/api/auth/me")
@require_auth
def auth_me():
    """Return current user info from JWT payload."""
    from flask import g
    return _json_ok(g.current_user)


# ─── Overview ─────────────────────────────────────────────────────────────────

@app.get("/api/overview")
@require_auth
def overview():
    zones = _flat_zones()
    machines = _flat_machines()
    cameras = list_cameras(BASE_DIR)
    all_cycles = _read_cycles()          # single read
    recent_cycles = all_cycles[:8]
    return _json_ok({
        "counts": {
            "zones": len(zones),
            "lines": len(list_all_lines_flat(BASE_DIR)),
            "machines": len(machines),
            "cameras": len(cameras),
            "cycles": len(all_cycles),   # reuse — no second CSV read
        },
        "recent_cycles": recent_cycles,
        "hierarchy": _hierarchy(),
    })


@app.get("/api/hierarchy")
@require_auth
def hierarchy():
    return _json_ok(_hierarchy())


# ─── Masters ──────────────────────────────────────────────────────────────────

@app.get("/api/masters/zones")
@require_auth
def get_zones():
    return _json_ok(_flat_zones())


@app.post("/api/masters/zones")
@require_auth
@require_role("admin", "supervisor")
def create_zone():
    payload = request.get_json(silent=True) or {}
    valid, name_or_err = _validate_name(str(payload.get("name", "")), "Zone name")
    if not valid:
        return _json_err(name_or_err)
    ok, message, zone_id = add_zone(name_or_err, BASE_DIR)
    if not ok:
        return _json_err(message)
    return _json_ok({"id": zone_id}, message, 201)


@app.delete("/api/masters/zones/<zone_id>")
@require_auth
@require_role("admin")
def remove_zone(zone_id: str):
    ok, message = delete_zone(zone_id, BASE_DIR)
    if not ok:
        return _json_err(message, 404)
    return _json_ok(message=message)


@app.patch("/api/masters/zones/<zone_id>")
@require_auth
@require_role("admin", "supervisor")
def update_zone(zone_id: str):
    payload = request.get_json(silent=True) or {}
    valid, name_or_err = _validate_name(str(payload.get("name", "")), "Zone name")
    if not valid:
        return _json_err(name_or_err)
    ok, message = rename_zone(zone_id, name_or_err, BASE_DIR)
    if not ok:
        return _json_err(message, 404)
    return _json_ok(message=message)


@app.get("/api/masters/lines")
@require_auth
def get_lines():
    zone_id = request.args.get("zone_id")
    if zone_id:
        lines = [
            {
                "zone_id": zone_id,
                "zone_name": next((z.get("name", "") for z in list_zones(BASE_DIR) if z.get("id") == zone_id), ""),
                "id": line.get("id", ""),
                "name": line.get("name", ""),
                "machine_count": len(line.get("machines", [])),
            }
            for line in get_lines_for_zone(zone_id, BASE_DIR)
        ]
        return _json_ok(lines)
    return _json_ok(list_all_lines_flat(BASE_DIR))


@app.post("/api/masters/lines")
@require_auth
@require_role("admin", "supervisor")
def create_line():
    payload = request.get_json(silent=True) or {}
    valid, name_or_err = _validate_name(str(payload.get("name", "")), "Line name")
    if not valid:
        return _json_err(name_or_err)
    zone_id = str(payload.get("zone_id", "")).strip()
    if not zone_id:
        return _json_err("zone_id is required")
    ok, message, line_id = add_line(zone_id, name_or_err, BASE_DIR)
    if not ok:
        return _json_err(message)
    return _json_ok({"id": line_id}, message, 201)


@app.delete("/api/masters/lines/<zone_id>/<line_id>")
@require_auth
@require_role("admin")
def remove_line(zone_id: str, line_id: str):
    ok, message = delete_line(zone_id, line_id, BASE_DIR)
    if not ok:
        return _json_err(message, 404)
    return _json_ok(message=message)


@app.patch("/api/masters/lines/<zone_id>/<line_id>")
@require_auth
@require_role("admin", "supervisor")
def update_line(zone_id: str, line_id: str):
    payload = request.get_json(silent=True) or {}
    valid, name_or_err = _validate_name(str(payload.get("name", "")), "Line name")
    if not valid:
        return _json_err(name_or_err)
    ok, message = rename_line(zone_id, line_id, name_or_err, BASE_DIR)
    if not ok:
        return _json_err(message, 404)
    return _json_ok(message=message)


@app.get("/api/masters/machines")
@require_auth
def get_machines():
    return _json_ok(_flat_machines())


@app.post("/api/masters/machines")
@require_auth
@require_role("admin", "supervisor")
def create_machine():
    payload = request.get_json(silent=True) or {}
    valid, name_or_err = _validate_name(str(payload.get("name", "")), "Machine name")
    if not valid:
        return _json_err(name_or_err)
    zone_id = str(payload.get("zone_id", "")).strip()
    line_id = str(payload.get("line_id", "")).strip()
    if not zone_id or not line_id:
        return _json_err("zone_id and line_id are required")
    ok, message, machine_id = add_machine(
        zone_id, line_id, name_or_err,
        camera_id=payload.get("camera_id") or None,
        base_dir=BASE_DIR,
    )
    if not ok:
        return _json_err(message)
    return _json_ok({"id": machine_id}, message, 201)


@app.delete("/api/masters/machines/<zone_id>/<line_id>/<machine_id>")
@require_auth
@require_role("admin")
def remove_machine(zone_id: str, line_id: str, machine_id: str):
    ok, message = delete_machine(zone_id, line_id, machine_id, BASE_DIR)
    if not ok:
        return _json_err(message, 404)
    return _json_ok(message=message)


@app.patch("/api/masters/machines/<zone_id>/<line_id>/<machine_id>")
@require_auth
@require_role("admin", "supervisor")
def update_machine(zone_id: str, line_id: str, machine_id: str):
    payload = request.get_json(silent=True) or {}
    valid, name_or_err = _validate_name(str(payload.get("name", "")), "Machine name")
    if not valid:
        return _json_err(name_or_err)
    ok, message = rename_machine(zone_id, line_id, machine_id, name_or_err, BASE_DIR)
    if not ok:
        return _json_err(message, 404)
    return _json_ok(message=message)


@app.patch("/api/masters/machines/<zone_id>/<line_id>/<machine_id>/camera")
@require_auth
@require_role("admin", "supervisor")
def update_machine_camera(zone_id: str, line_id: str, machine_id: str):
    payload = request.get_json(silent=True) or {}
    camera_id = str(payload.get("camera_id", "")).strip()
    if not camera_id:
        return _json_err("camera_id is required")
    ok, message = assign_camera(zone_id, line_id, machine_id, camera_id, BASE_DIR)
    if not ok:
        return _json_err(message, 404)
    return _json_ok(message=message)


@app.get("/api/masters/cameras")
@require_auth
def get_cameras():
    return _json_ok(list_cameras(BASE_DIR))


@app.post("/api/masters/cameras")
@require_auth
@require_role("admin", "supervisor")
def create_camera():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()
    ip = str(payload.get("ip", "")).strip()
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", "")).strip()
    if not name or not ip or not username or not password:
        return _json_err("Name, IP, username, and password are required")
    if len(name) > 80 or len(ip) > 64:
        return _json_err("Name or IP too long (max 80 / 64 chars)")
    ok, message, camera_id = add_camera(
        name=name,
        ip=ip,
        username=username,
        password=password,
        path=str(payload.get("path", "")),
        port=int(payload.get("port", 554) or 554),
        base_dir=BASE_DIR,
    )
    if not ok:
        return _json_err(message)
    return _json_ok({"id": camera_id}, message, 201)


@app.patch("/api/masters/cameras/<camera_id>")
@require_auth
@require_role("admin", "supervisor")
def edit_camera(camera_id: str):
    """Rename or otherwise update an existing camera.  Empty fields in
    the payload are skipped (so you can change just the display name
    without re-typing the password)."""
    payload = request.get_json(silent=True) or {}
    allowed = {"name", "ip", "port", "username", "password", "path"}
    updates = {k: v for k, v in payload.items() if k in allowed}
    if not updates:
        return _json_err("No updatable fields provided")
    ok, message = update_camera(camera_id, updates, BASE_DIR)
    if not ok:
        return _json_err(message, 400)
    return _json_ok(message=message)


@app.delete("/api/masters/cameras/<camera_id>")
@require_auth
@require_role("admin")
def remove_camera(camera_id: str):
    ok, message = delete_camera(camera_id, BASE_DIR)
    if not ok:
        return _json_err(message, 400)
    return _json_ok(message=message)


# ─── Camera Grid ──────────────────────────────────────────────────────────────

@app.get("/api/camera-grid")
@require_auth
def camera_grid():
    """Return all machines with camera + cycle status for grid view."""
    return _json_ok(_camera_grid())


# ─── Cycle Trigger ────────────────────────────────────────────────────────────

@app.post("/api/cycle/trigger")
@require_auth
@require_role("admin", "supervisor", "operator")
def cycle_trigger():
    """
    Toggle cycle recording for a machine.
    Body: { machine_id, action: 'start'|'stop'|'toggle' }
    """
    payload = request.get_json(silent=True) or {}
    machine_id = str(payload.get("machine_id", "")).strip()
    action = str(payload.get("action", "toggle")).strip()

    if not machine_id:
        return _json_err("machine_id is required")

    current = get_machine_state(machine_id, BASE_DIR)
    is_recording = current.get("recording", False)

    if action == "toggle":
        action = "stop" if is_recording else "start"

    if action == "start":
        if is_recording:
            return _json_err("Machine is already recording")
        next_num = get_next_cycle_num(CSV_PATH)
        state = start_cycle(machine_id, next_num, BASE_DIR)
        return _json_ok(state, f"Cycle {next_num} started for {machine_id}")

    elif action == "stop":
        if not is_recording:
            return _json_err("No active cycle to stop")
        state = end_cycle(machine_id, BASE_DIR)
        return _json_ok(state, f"Cycle stopped for {machine_id}")

    return _json_err(f"Unknown action: {action}")


@app.get("/api/cycle/status")
@require_auth
def cycle_status():
    """Return current cycle recording state for all machines."""
    all_states = get_all_states(BASE_DIR)
    all_machines = all_machines_flat(BASE_DIR)
    result = []
    for machine in all_machines:
        mid = machine.get("machine_id", "")
        state = all_states.get(mid, {})
        result.append({
            "machine_id": mid,
            "machine_name": machine.get("machine_name", ""),
            "line_name": machine.get("line_name", ""),
            "zone_name": machine.get("zone_name", ""),
            "recording": state.get("recording", False),
            "cycle_number": state.get("cycle_number"),
            "start_time": state.get("start_time"),
        })
    return _json_ok(result)


@app.get("/api/cycle/history")
def cycle_history():
    """Return all cycle history records."""
    limit = request.args.get("limit", type=int)
    machine_id = request.args.get("machine_id")
    rows = _read_cycles(limit=limit)
    if machine_id:
        rows = [r for r in rows if r["machine_id"] == machine_id]
    return _json_ok(rows)


# ─── PLC Config (Multiple) ───────────────────────────────────────────────────

@app.get("/api/masters/plcs")
@require_auth
def get_plcs_master():
    return _json_ok(list_plcs(BASE_DIR))

@app.post("/api/masters/plcs")
@require_auth
@require_role("admin")
def create_plc_master():
    payload = request.get_json(silent=True) or {}
    ok, msg, pid = add_plc(payload, BASE_DIR)
    return _json_ok({"id": pid}, msg) if ok else _json_err(msg)

@app.patch("/api/masters/plcs/<plc_id>")
@require_auth
@require_role("admin")
def patch_plc_master(plc_id):
    payload = request.get_json(silent=True) or {}
    ok, msg = update_plc(plc_id, payload, BASE_DIR)
    return _json_ok({}, msg) if ok else _json_err(msg)

@app.delete("/api/masters/plcs/<plc_id>")
@require_auth
@require_role("admin")
def delete_plc_master(plc_id):
    ok, msg = delete_plc(plc_id, BASE_DIR)
    return _json_ok({}, msg) if ok else _json_err(msg)


# ─── Legacy PLC Config (Singular fallback) ───────────────────────────────────

# ─── System Settings (video storage path, etc.) ────────────────────────────

@app.get("/api/settings")
@require_auth
def get_settings_endpoint():
    """Return current system settings — primarily the video storage path.
    Visible to all authenticated users; only admin can change it."""
    return _json_ok(get_settings())


@app.post("/api/sync/from-mes")
@require_auth
@require_role("admin")
def sync_from_mes_endpoint():
    """Rebuild local zones.json from MES Postgres.  Wipes Zone/Line/
    Machine data and replaces it with whatever MES currently has —
    cameras_id assignments are preserved where the machine name still
    matches.  Frontend button: System Settings -> "Sync from MES"."""
    ok, message, summary = pull_from_mes(BASE_DIR)
    if not ok:
        return _json_err(message, 502)
    return _json_ok(summary, message)


@app.post("/api/settings")
@require_auth
@require_role("admin")
def save_settings_endpoint():
    """Update system settings.  Currently accepts:
        videos_dir: absolute path to store recorded videos
                    (empty string = revert to default backend/videos)"""
    payload = request.get_json(silent=True) or {}
    if "videos_dir" not in payload:
        return _json_err("videos_dir is required", 400)
    try:
        out = save_videos_dir(payload.get("videos_dir") or "")
    except ValueError as exc:
        return _json_err(str(exc), 400)
    return _json_ok(out, "Settings saved")


@app.get("/api/plc-config")
@require_auth
def get_plc_config():
    return _json_ok(load_plc_config(BASE_DIR))

@app.post("/api/plc-config")
@require_auth
@require_role("admin")
def save_plc_config():
    payload = request.get_json(silent=True) or {}
    allowed = {"ip", "port", "bit_address", "enabled", "description"}
    updates = {k: v for k, v in payload.items() if k in allowed}
    if "port" in updates:
        updates["port"] = int(updates["port"] or 502)
    cfg = update_plc_config(updates, BASE_DIR)
    return _json_ok(cfg, "PLC configuration saved")


# ─── Shifts Config ──────────────────────────────────────────────────────────

@app.get("/api/masters/shifts")
@require_auth
def get_shifts():
    return _json_ok(list_shifts(BASE_DIR))

@app.post("/api/masters/shifts")
@require_auth
@require_role("admin")
def save_shift():
    payload = request.get_json(silent=True) or {}
    ok, msg, sid = add_or_update_shift(payload, BASE_DIR)
    return _json_ok({"id": sid}, msg) if ok else _json_err(msg)

@app.delete("/api/masters/shifts/<shift_id>")
@require_auth
@require_role("admin")
def remove_shift(shift_id: str):
    ok, msg = delete_shift(shift_id, BASE_DIR)
    return _json_ok({}, msg) if ok else _json_err(msg, 404)


# ─── Camera Config Bindings ──────────────────────────────────────────────────

@app.get("/api/camera-configs")
@require_auth
def get_camera_bindings():
    return _json_ok(list_bindings(BASE_DIR))

@app.post("/api/camera-configs")
@require_auth
@require_role("admin")
def add_camera_binding():
    payload = request.get_json(silent=True) or {}
    ok, msg, bid = add_binding(payload, BASE_DIR)
    return _json_ok({"id": bid}, msg) if ok else _json_err(msg)

@app.delete("/api/camera-configs/<binding_id>")
@require_auth
@require_role("admin")
def delete_camera_binding(binding_id):
    ok, msg = delete_binding(binding_id, BASE_DIR)
    return _json_ok({}, msg) if ok else _json_err(msg)


# ─── Video Playback ──────────────────────────────────────────────────────────

@app.get("/live_feed/<camera_id>")
def live_feed(camera_id: str):
    rtsp_url = get_camera_rtsp_url(camera_id, BASE_DIR)
    if not rtsp_url:
        return "Camera not found", 404

    def generate():
        cap, _used_url, _tried = open_rtsp_capture(rtsp_url)
        if cap is None:
            return
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                ok_enc, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if not ok_enc:
                    continue
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" +
                    encoded.tobytes() +
                    b"\r\n"
                )
                time.sleep(0.04)
        finally:
            cap.release()

    return app.response_class(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.get("/camera_frame/<camera_id>")
def camera_frame(camera_id: str):
    """
    Return a single JPEG frame for browser-friendly camera grid previews.
    """
    rtsp_url = get_camera_rtsp_url(camera_id, BASE_DIR)
    if not rtsp_url:
        return "Camera not found", 404

    cap, _used_url, _tried = open_rtsp_capture(rtsp_url)
    if cap is None:
        return "Stream offline", 503

    try:
        ok, frame = cap.read()
        if not ok or frame is None:
            return "Stream offline", 503
        ok_enc, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok_enc:
            return "Frame encode failed", 500
        return Response(encoded.tobytes(), mimetype="image/jpeg")
    finally:
        cap.release()


@app.get("/api/video")
def serve_video():
    # Publicly accessible for dashboard embedding (or add @require_auth if token passed properly)
    path = request.args.get("path")
    if not path:
        return "Video not found", 404
    file_path = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
    file_path = os.path.normpath(file_path)
    if not os.path.exists(file_path):
        return "Video not found", 404
    return send_file(file_path, mimetype="video/mp4")


# ─── Cycle log + clip extraction ───────────────────────────────────

@app.get("/api/cycles")
@require_auth
def list_cycles_endpoint():
    """Return cycle history (newest first).  Optional query params:
    machine_id, limit."""
    machine_id = request.args.get("machine_id")
    limit_raw  = request.args.get("limit", "200")
    try: limit = int(limit_raw)
    except (TypeError, ValueError): limit = 200
    rows = ce_list(machine_id=machine_id, limit=limit, base_dir=BASE_DIR)
    return _json_ok(rows)


@app.post("/api/cycles")
@require_auth
@require_role("admin", "supervisor", "operator")
def append_cycle_endpoint():
    """Manual cycle insert (UI Start/Stop, or by the future PLC poller).

    Body shape:
      {
        "machine_id": "...",
        "camera_id":  "...",
        "start_ts":   "ISO datetime",   # optional, defaults to "now"
        "end_ts":     "ISO datetime",   # optional, defaults to "now"
        "status":     "OK|NG|COUNT|MANUAL",
        "notes":      ""
      }

    The recording manager's currently-rolling .ts file for the
    camera is auto-resolved here, so the caller doesn't need to
    know which file the cycle lives in — clip extraction will
    seek by the ts_offset_s saved on this row."""
    payload = request.get_json(silent=True) or {}
    machine_id = str(payload.get("machine_id", "")).strip()
    camera_id  = str(payload.get("camera_id", "")).strip()
    if not machine_id or not camera_id:
        return _json_err("machine_id and camera_id are required")

    def _parse_ts(v):
        if not v: return None
        try: return datetime.fromisoformat(str(v))
        except Exception: return None

    start_ts = _parse_ts(payload.get("start_ts")) or datetime.now()
    end_ts   = _parse_ts(payload.get("end_ts"))   or datetime.now()
    status   = str(payload.get("status", "MANUAL")).strip().upper() or "MANUAL"

    rec = RecordingManager.get(BASE_DIR).get_recorder(camera_id)
    info = rec.current_recording_info() if rec else None
    if not info:
        return _json_err(
            "No active recording for that camera — start the recorder first.",
            409,
        )
    ts_file, ts_started = info

    row = ce_append(
        machine_id       = machine_id,
        camera_id        = camera_id,
        start_ts         = start_ts,
        end_ts           = end_ts,
        status           = status,
        shift_id         = (rec._current_date or "") + "_" + (rec._current_shift or ""),
        ts_file          = ts_file,
        ts_file_start_ep = ts_started,
        notes            = str(payload.get("notes") or ""),
        base_dir         = BASE_DIR,
    )
    return _json_ok(row, "Cycle logged", 201)


@app.get("/api/cycle/clip/<machine_id>/<int:cycle_seq>")
@require_auth
def cycle_clip(machine_id: str, cycle_seq: int):
    """Extract this cycle's clip from its rolling .ts file using
    ffmpeg `-c copy` (instant byte-level cut, no re-encode).  Output
    is cached as MP4 so subsequent requests are file-served."""
    if not ffmpeg_available():
        return _json_err("ffmpeg not installed on the server", 500)

    row = ce_get(cycle_seq, machine_id, base_dir=BASE_DIR)
    if not row:
        return _json_err("Cycle not found", 404)

    ts_file  = row.get("ts_file") or ""
    if not ts_file or not os.path.exists(ts_file):
        return _json_err(f"Source recording missing: {ts_file}", 404)

    try:
        ss = float(row.get("ts_offset_s") or 0)
        to = float(row.get("ts_end_offset") or ss)
    except (TypeError, ValueError):
        return _json_err("Cycle has invalid timestamps", 500)

    cache_dir = os.path.join(os.path.dirname(ts_file) or BASE_DIR, "_clips")
    os.makedirs(cache_dir, exist_ok=True)
    out_file = os.path.join(cache_dir, f"{machine_id}_cycle_{cycle_seq}.mp4")

    if not os.path.exists(out_file):
        cmd = [
            FFMPEG_EXE if os.path.exists(FFMPEG_EXE) else "ffmpeg",
            "-y",
            "-ss", f"{max(0.0, ss - 0.5):.3f}",   # 0.5s pre-roll for keyframe alignment
            "-to", f"{to + 0.5:.3f}",
            "-i", ts_file,
            "-c", "copy",
            "-movflags", "+faststart",
            out_file,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=60, check=True)
        except subprocess.CalledProcessError as exc:
            return _json_err(f"ffmpeg failed: {exc.stderr.decode(errors='ignore')[:200]}", 500)
        except subprocess.TimeoutExpired:
            return _json_err("ffmpeg timed out", 504)

    return send_file(out_file, mimetype="video/mp4")


# ─── App startup hook: bring up the recording manager ─────────────

def _ensure_recording_manager_started():
    if not ffmpeg_available():
        print("[REC-MGR] WARNING: ffmpeg not found — continuous recording DISABLED.")
        print(f"           Expected at: {FFMPEG_EXE}")
        return
    print("[REC-MGR] starting recording manager...")
    RecordingManager.get(BASE_DIR).start()


_ensure_recording_manager_started()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
