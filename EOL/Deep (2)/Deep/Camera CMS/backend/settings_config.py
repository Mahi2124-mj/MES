"""
settings_config.py
==================
Persists global system settings (currently: video storage path) to
`settings.json` next to the backend modules.

Resolution priority for `videos_dir`:
    1. ENV var `VIDEOS_DIR`               — set by start_all.bat
    2. settings.json["videos_dir"]        — set via UI (POST /api/settings)
    3. recorder.DEFAULT_VIDEOS_DIR        — built-in fallback ("videos")

The path is created on read if it doesn't exist (so an external HDD
that's been disconnected and reconnected gets its videos folder back).
"""
from __future__ import annotations

import json
import os
import threading
from typing import Optional

_LOCK = threading.Lock()
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
DEFAULT_VIDEOS_DIR_REL = "videos"   # relative to BASE_DIR


def _read_settings() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fp:
            return json.load(fp) or {}
    except Exception:
        return {}


def _write_settings(d: dict) -> None:
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(d, fp, indent=2, ensure_ascii=False)
    os.replace(tmp, SETTINGS_FILE)


def get_videos_dir() -> str:
    """Resolve the absolute videos directory using priority order:
       env > settings.json > default."""
    env = os.getenv("VIDEOS_DIR", "").strip()
    if env:
        path = env
    else:
        s = _read_settings()
        path = (s.get("videos_dir") or "").strip()
    if not path:
        path = DEFAULT_VIDEOS_DIR_REL
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    path = os.path.normpath(path)
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        # If the external HDD is disconnected we don't crash here;
        # the recorder will surface the failure when it tries to write.
        pass
    return path


def get_settings() -> dict:
    """Public: full settings as the UI sees them, with `videos_dir`
    showing the *currently active* path (env override visible)."""
    s   = _read_settings()
    env = os.getenv("VIDEOS_DIR", "").strip()
    return {
        # Persisted value (what the UI form should show)
        "videos_dir":            s.get("videos_dir") or "",
        # Effective value after env-override + default fallback
        "videos_dir_effective":  get_videos_dir(),
        # Whether ENV is forcing the path (UI can disable the field)
        "videos_dir_env_locked": bool(env),
    }


def save_videos_dir(path: str) -> dict:
    """Persist a new videos_dir.  Empty string clears the override
    (back to default)."""
    path = (path or "").strip()
    if path and not os.path.isabs(path):
        raise ValueError(
            "Storage path must be an ABSOLUTE path "
            "(e.g. F:\\CameraCMS_Videos)"
        )
    if path:
        # Validate the parent drive/folder is reachable.  We don't require
        # the leaf folder to exist — we'll create it.  But the drive must
        # be mounted; otherwise the user is staring at a broken setting.
        parent = os.path.dirname(path) or path
        if not os.path.exists(parent) and not os.path.exists(os.path.splitdrive(path)[0] + os.sep):
            raise ValueError(
                f"Drive/folder not reachable: {parent}.  "
                f"Plug in the external drive and try again."
            )
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"Cannot create storage folder: {exc}") from exc

    with _LOCK:
        s = _read_settings()
        if path:
            s["videos_dir"] = path
        else:
            s.pop("videos_dir", None)
        _write_settings(s)
    return get_settings()
