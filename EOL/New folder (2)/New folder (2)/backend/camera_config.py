"""
camera_config.py — Camera CRUD with Fernet-encrypted credentials at rest.

Credentials (username, password) are encrypted using a key stored in
backend/secret.key (auto-generated on first run). The key file must NOT
be committed to version control — it is listed in .gitignore.

API callers always receive plain-text credentials so recorder.py / RTSP
URL building still works normally. Encryption only applies on disk.
"""
import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple

from cryptography.fernet import Fernet

DEFAULT_CONFIG_FILE = "cameras.json"
SECRET_KEY_FILE = "secret.key"

import os

DEFAULT_CAMERA = {
    "id":       os.getenv("CAMERA_DEFAULT_ID",       "cam_default"),
    "name":     os.getenv("CAMERA_DEFAULT_NAME",     "Default Camera"),
    "ip":       os.getenv("CAMERA_DEFAULT_IP",       ""),
    "port":     int(os.getenv("CAMERA_DEFAULT_PORT", "554") or 554),
    "username": os.getenv("CAMERA_DEFAULT_USER",     ""),
    "password": os.getenv("CAMERA_DEFAULT_PASS",     ""),
    "path":     os.getenv("CAMERA_DEFAULT_PATH",     "/h264/ch1/main/av_stream"),
}


# ─── encryption helpers ───────────────────────────────────────────────────────

def _key_path(base_dir: Optional[str]) -> str:
    root = base_dir or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, SECRET_KEY_FILE)


def _get_fernet(base_dir: Optional[str] = None) -> Fernet:
    """Load or generate the site's Fernet key."""
    kp = _key_path(base_dir)
    if os.path.exists(kp):
        with open(kp, "rb") as f:
            key = f.read().strip()
    else:
        key = Fernet.generate_key()
        with open(kp, "wb") as f:
            f.write(key)
    return Fernet(key)


def _encrypt(value: str, fernet: Fernet) -> str:
    return fernet.encrypt(value.encode()).decode()


def _decrypt(value: str, fernet: Fernet) -> str:
    try:
        return fernet.decrypt(value.encode()).decode()
    except Exception:
        # If decryption fails (e.g. legacy plaintext value), return as-is
        return value


def _is_encrypted(value: str) -> bool:
    """Heuristic: Fernet tokens start with 'gAAAAA' and are base64."""
    return value.startswith("gAAAAA") and len(value) > 50


# ─── file path helpers ────────────────────────────────────────────────────────

def _config_path(base_dir: Optional[str]) -> str:
    root = base_dir or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, DEFAULT_CONFIG_FILE)


def _normalize_path(path: str) -> str:
    cleaned = (path or "").strip()
    if not cleaned:
        return "/h264/ch1/main/av_stream"
    return cleaned if cleaned.startswith("/") else f"/{cleaned}"


def _build_rtsp_url(camera: Dict[str, object]) -> str:
    # Credentials default to empty so a misconfigured camera can never
    # accidentally fall back to admin/admin123 baked into source.
    username = str(camera.get("username") or "")
    password = str(camera.get("password") or "")
    ip = str(camera.get("ip", ""))
    port = int(camera.get("port", 554))
    path = _normalize_path(str(camera.get("path", "")))
    return f"rtsp://{username}:{password}@{ip}:{port}{path}"


def _default_payload() -> Dict[str, object]:
    return {
        "active_camera_id": DEFAULT_CAMERA["id"],
        "cameras": [DEFAULT_CAMERA],
    }


# ─── load / save (handles encryption transparently) ───────────────────────────

def load_config(base_dir: Optional[str] = None) -> Dict[str, object]:
    path = _config_path(base_dir)
    fernet = _get_fernet(base_dir)

    if not os.path.exists(path):
        payload = _default_payload()
        save_config(payload, base_dir)
        return payload

    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    cameras = payload.get("cameras")
    if not isinstance(cameras, list) or not cameras:
        payload = _default_payload()
        save_config(payload, base_dir)
        return payload

    # Decrypt credentials in memory before returning
    decrypted_cameras = []
    for cam in cameras:
        cam = dict(cam)
        cam["username"] = _decrypt(str(cam.get("username", "")), fernet)
        cam["password"] = _decrypt(str(cam.get("password", "")), fernet)
        decrypted_cameras.append(cam)
    payload["cameras"] = decrypted_cameras

    if not payload.get("active_camera_id"):
        payload["active_camera_id"] = str(cameras[0].get("id", DEFAULT_CAMERA["id"]))
        save_config(payload, base_dir)

    return payload


def save_config(payload: Dict[str, object], base_dir: Optional[str] = None) -> None:
    fernet = _get_fernet(base_dir)
    # Encrypt credentials before writing to disk
    encrypted_cameras = []
    for cam in payload.get("cameras", []):
        cam = dict(cam)
        username = str(cam.get("username", ""))
        password = str(cam.get("password", ""))
        # Only encrypt if not already encrypted
        cam["username"] = username if _is_encrypted(username) else _encrypt(username, fernet)
        cam["password"] = password if _is_encrypted(password) else _encrypt(password, fernet)
        encrypted_cameras.append(cam)
    to_write = dict(payload)
    to_write["cameras"] = encrypted_cameras
    path = _config_path(base_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_write, f, indent=2)


# ─── public CRUD API ──────────────────────────────────────────────────────────

def list_cameras(base_dir: Optional[str] = None) -> List[Dict[str, object]]:
    payload = load_config(base_dir)
    cameras = payload.get("cameras", [])
    return cameras if isinstance(cameras, list) else []


def get_active_camera(base_dir: Optional[str] = None) -> Dict[str, object]:
    payload = load_config(base_dir)
    active_id = str(payload.get("active_camera_id", ""))
    cameras = payload.get("cameras", [])
    for cam in cameras:
        if str(cam.get("id", "")) == active_id:
            return cam
    return cameras[0] if cameras else DEFAULT_CAMERA


def get_camera_by_id(camera_id: str, base_dir: Optional[str] = None) -> Optional[Dict[str, object]]:
    for cam in list_cameras(base_dir):
        if str(cam.get("id", "")) == str(camera_id):
            return cam
    return None


def set_active_camera(camera_id: str, base_dir: Optional[str] = None) -> Tuple[bool, str]:
    payload = load_config(base_dir)
    cameras = payload.get("cameras", [])
    target = None
    for cam in cameras:
        if str(cam.get("id", "")) == str(camera_id):
            target = cam
            break
    if target is None:
        return False, f"Camera id not found: {camera_id}"
    payload["active_camera_id"] = str(camera_id)
    save_config(payload, base_dir)
    return True, f"Active camera set to: {target.get('name', camera_id)}"


def delete_camera(camera_id: str, base_dir: Optional[str] = None) -> Tuple[bool, str]:
    payload = load_config(base_dir)
    cameras = payload.get("cameras", [])
    if len(cameras) <= 1:
        return False, "At least one camera must remain configured"
    updated = [cam for cam in cameras if str(cam.get("id", "")) != str(camera_id)]
    if len(updated) == len(cameras):
        return False, f"Camera id not found: {camera_id}"
    payload["cameras"] = updated
    if str(payload.get("active_camera_id", "")) == str(camera_id):
        payload["active_camera_id"] = str(updated[0].get("id", ""))
    save_config(payload, base_dir)
    return True, "Camera deleted"


def add_camera(
    name: str,
    ip: str,
    username: str,
    password: str,
    path: str,
    port: int = 554,
    base_dir: Optional[str] = None,
) -> Tuple[bool, str, Optional[str]]:
    camera_name = (name or "").strip()[:80]
    camera_ip = (ip or "").strip()[:64]
    camera_user = (username or "").strip()
    camera_pass = (password or "").strip()
    camera_path = _normalize_path(path)

    if not camera_name or not camera_ip or not camera_user or not camera_pass:
        return False, "Name, IP, username, and password are required", None

    payload = load_config(base_dir)
    cameras = payload.get("cameras", [])

    for cam in cameras:
        if str(cam.get("ip", "")) == camera_ip and str(cam.get("path", "")) == camera_path:
            return False, "Camera with same IP and path already exists", None

    safe_name = re.sub(r"[^a-zA-Z0-9]+", "_", camera_name).strip("_").lower() or "camera"
    camera_id = f"cam_{safe_name}_{int(time.time())}"

    camera = {
        "id": camera_id,
        "name": camera_name,
        "ip": camera_ip,
        "port": int(port) if port else 554,
        "username": camera_user,
        "password": camera_pass,
        "path": camera_path,
    }
    cameras.append(camera)
    payload["cameras"] = cameras
    save_config(payload, base_dir)

    return True, f"Camera added: {camera_name}", camera_id


def get_active_rtsp_url(base_dir: Optional[str] = None) -> str:
    return _build_rtsp_url(get_active_camera(base_dir))


def get_camera_rtsp_url(camera_id: str, base_dir: Optional[str] = None) -> Optional[str]:
    cam = get_camera_by_id(camera_id, base_dir)
    if cam is None:
        return None
    return _build_rtsp_url(cam)
