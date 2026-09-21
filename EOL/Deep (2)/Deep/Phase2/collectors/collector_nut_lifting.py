#!/usr/bin/env python3
"""
AUTO-GENERATED COLLECTOR — NUT LIFTING
Line ID    : 9
Table      : nut_lifting_dashboard
Generated  : 2026-09-19 09:24:32

DO NOT EDIT MANUALLY — regenerate via admin panel if config changes.
"""

# ── Re-use the shared collector engine ───────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector_engine import CollectorEngine

CONFIG = {
    "line_id":    9,
    "line_name":  "NUT LIFTING",
    "table_name": "nut_lifting_dashboard",
    "plc_ip":     "192.168.30.212",
    "plc_port":   5002,
    "ok_bit":     "",
    "ng_bit":     "",
    "status_addr":"",
    "model_addr": "",
    "ideal_ct":   15.0,
    "max_ct":     16.0,
    "models":     {},
    "status_map": {},
    "breaks":     [{'start': '01:00:00', 'end': '01:10:00', 'name': 'Night Tea Break'}, {'start': '04:00:00', 'end': '04:10:00', 'name': 'Early Morning Break'}, {'start': '10:00:00', 'end': '10:10:00', 'name': 'Morning Tea Break'}, {'start': '12:00:00', 'end': '12:35:00', 'name': 'Lunch Break'}, {'start': '14:30:00', 'end': '14:40:00', 'name': 'Evening Tea Break'}, {'start': '18:00:00', 'end': '18:10:00', 'name': 'Dinner Break 1'}, {'start': '20:00:00', 'end': '20:10:00', 'name': 'Tea Break'}, {'start': '22:00:00', 'end': '22:35:00', 'name': 'Dinner Break 2'}],
    "shifts":     {'A': {'start': '08:30:00', 'end': '17:15:00', 'plan': 1860, 'crosses_midnight': False}},
}

if __name__ == "__main__":
    engine = CollectorEngine(CONFIG)
    engine.run()
