"""
mes_client.py — thin HTTP client for MES Phase2's /api/cms-sync surface.

The CMS frontend's Machine Detail page treats MES Postgres as the
source-of-truth for plant/zone/line/machine config (because the MES
collectors and dashboards already read from there).  This module
proxies the small subset of MES endpoints the CMS UI actually needs.

Auth: none — `cms_sync` is the loopback-only sink on MES side, same
pattern as CMS's own `/api/plc-edge`.  Both services run on the same
box (5555 + 8080), so the trust boundary is the OS.

Failure mode: every call returns (ok, data_or_error_dict).  Caller
decides whether to surface a toast or silently fall back.  A long
connect-timeout would freeze the UI, so we cap at 4 s — MES is on
loopback, anything slower means it's down.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

import requests

_MES_BASE = os.environ.get("MES_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
_TIMEOUT_S = 4


def _url(path: str) -> str:
    """Join MES base with a /-prefixed path."""
    return f"{_MES_BASE}{path if path.startswith('/') else '/' + path}"


def _result(r: requests.Response) -> Tuple[bool, Any]:
    """Unwrap requests.Response into (ok, parsed_body_or_error_dict)."""
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text[:200]}
    if 200 <= r.status_code < 300:
        return True, body
    err_msg = body.get("detail") if isinstance(body, dict) else None
    return False, {"status": r.status_code, "error": err_msg or body}


# ── Read endpoints (no body) ──────────────────────────────────────

def get_state() -> Tuple[bool, Any]:
    """One-shot snapshot of plants + zones + lines + machines."""
    try:
        r = requests.get(_url("/api/cms-sync/state"), timeout=_TIMEOUT_S)
        return _result(r)
    except requests.RequestException as exc:
        return False, {"error": f"MES unreachable: {exc}"}


def list_machines() -> Tuple[bool, Any]:
    """Flat list of every PLC machine with zone+line names joined."""
    try:
        r = requests.get(_url("/api/cms-sync/machines"), timeout=_TIMEOUT_S)
        return _result(r)
    except requests.RequestException as exc:
        return False, {"error": f"MES unreachable: {exc}"}


# ── Write endpoints ────────────────────────────────────────────────

def upsert_machine(payload: Dict) -> Tuple[bool, Any]:
    """Insert or update one mes_plc_configs row.
    Pass id=None to create, else id=<int> to update."""
    try:
        r = requests.post(_url("/api/cms-sync/machine"),
                          json=payload, timeout=_TIMEOUT_S)
        return _result(r)
    except requests.RequestException as exc:
        return False, {"error": f"MES unreachable: {exc}"}


def delete_machine(plc_id: int) -> Tuple[bool, Any]:
    """Remove one mes_plc_configs row by id."""
    try:
        r = requests.delete(_url(f"/api/cms-sync/machine/{plc_id}"),
                            timeout=_TIMEOUT_S)
        return _result(r)
    except requests.RequestException as exc:
        return False, {"error": f"MES unreachable: {exc}"}
