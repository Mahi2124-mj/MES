#!/usr/bin/env python3
"""
AUTO-GENERATED COLLECTOR — GEAR LIFTER
Line ID    : 7
Table      : gear_lifter_dashboard
Generated  : 2026-09-03 10:00:23

DO NOT EDIT MANUALLY — regenerate via admin panel if config changes.
"""

# ── Re-use the shared collector engine ───────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector_engine import CollectorEngine

CONFIG = {
    "line_id":    7,
    "line_name":  "GEAR LIFTER",
    "table_name": "gear_lifter_dashboard",
    "plc_ip":     "192.168.30.210",
    "plc_port":   502,
    "ok_bit":     "D101",
    "ng_bit":     "D102",
    "status_addr":"D6005",
    "model_addr": "D6048",
    "ideal_ct":   9.8,
    "max_ct":     10.8,
    "models":     {1007: 'GEAR_LIFTER_ALL'},
    "status_map": {0: {'name': 'IDLE', 'loss': None}, 1: {'name': 'RUNNING', 'loss': None}, 2: {'name': 'BREAKDOWN', 'loss': 'breakdown'}, 3: {'name': 'QUALITY_ISSUE', 'loss': 'quality'}, 4: {'name': 'MODEL_SETUP', 'loss': 'setup'}, 5: {'name': 'MATERIAL_WAIT', 'loss': 'material'}, 6: {'name': 'OTHER_LOSS', 'loss': 'others'}, 7: {'name': 'CHANGE_OVER', 'loss': 'change_over'}, 8: {'name': 'BREAK', 'loss': 'break'}},
    "breaks":     [{'start': '01:00:00', 'end': '01:10:00', 'name': 'Night Tea Break'}, {'start': '04:00:00', 'end': '04:10:00', 'name': 'Early Morning Break'}, {'start': '10:00:00', 'end': '10:10:00', 'name': 'Morning Tea Break'}, {'start': '12:00:00', 'end': '12:35:00', 'name': 'Lunch Break'}, {'start': '14:30:00', 'end': '14:40:00', 'name': 'Evening Tea Break'}, {'start': '18:00:00', 'end': '18:10:00', 'name': 'Dinner Break 1'}, {'start': '20:00:00', 'end': '20:10:00', 'name': 'Tea Break'}, {'start': '22:00:00', 'end': '22:35:00', 'name': 'Dinner Break 2'}],
    "shifts":     {'A': {'start': '08:30:00', 'end': '17:15:00', 'plan': 2850, 'crosses_midnight': False}},
}

if __name__ == "__main__":
    engine = CollectorEngine(CONFIG)
    engine.run()
