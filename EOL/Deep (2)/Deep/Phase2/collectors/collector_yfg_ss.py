#!/usr/bin/env python3
"""
AUTO-GENERATED COLLECTOR — YFG-SS
Line ID    : 14
Table      : yfg_l7_complete
Generated  : 2026-07-03 13:19:17

DO NOT EDIT MANUALLY — regenerate via admin panel if config changes.
"""

# ── Re-use the shared collector engine ───────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector_engine import CollectorEngine

CONFIG = {
    "line_id":    14,
    "line_name":  "YFG-SS",
    "table_name": "yfg_l7_complete",
    "plc_ip":     "192.168.10.166",
    "plc_port":   5002,
    "ok_bit":     "D101",
    "ng_bit":     "D102",
    "status_addr":"D6005",
    "model_addr": "D1016",
    "ideal_ct":   15.0,
    "max_ct":     16.0,
    "models":     {5: 'TRACK ASSY FRONT SEAT YTA 6 WAY INR RH', 6: 'TRACK ASSY FRONT SEAT YTA 6 WAY OTR LH', 7: 'TRACK ASSY FRONT SEAT YTA 6 WAY INR LH', 9: 'TRACK ASSY FRONT SEAT YHB 4 WAY OTR', 10: 'TRACK ASSY FRONT SEAT YHB 4 WAY INR RH', 11: 'TRACK ASSY FRONT SEAT YHB 4 WAY INR LH', 12: 'TRACK ASSY FRONT SEAT YCA 4 WAY INR LH', 14: 'TRACK ASSY FRONT SEAT YNC 4 WAY INR RH', 15: 'TRACK ASSY FRONT SEAT YNC 4 WAY INR LH', 16: 'TRACK ASSY FRONT SEAT YNC 4 WAY OTR'},
    "status_map": {0: {'name': 'IDLE', 'loss': None}, 1: {'name': 'RUNNING', 'loss': None}, 2: {'name': 'BREAKDOWN', 'loss': 'breakdown'}, 3: {'name': 'QUALITY_ISSUE', 'loss': 'quality'}, 4: {'name': 'MODEL_SETUP', 'loss': 'setup'}, 5: {'name': 'MATERIAL_WAIT', 'loss': 'material'}, 6: {'name': 'OTHER_LOSS', 'loss': 'others'}, 7: {'name': 'CHANGE_OVER', 'loss': 'change_over'}, 8: {'name': 'BREAK', 'loss': 'break'}},
    "breaks":     [{'start': '01:00:00', 'end': '01:10:00', 'name': 'Night Tea Break'}, {'start': '04:00:00', 'end': '04:10:00', 'name': 'Early Morning Break'}, {'start': '10:00:00', 'end': '10:10:00', 'name': 'Morning Tea Break'}, {'start': '12:00:00', 'end': '12:35:00', 'name': 'Lunch Break'}, {'start': '14:30:00', 'end': '14:40:00', 'name': 'Evening Tea Break'}, {'start': '18:00:00', 'end': '18:10:00', 'name': 'Dinner Break 1'}, {'start': '20:00:00', 'end': '20:10:00', 'name': 'Tea Break'}, {'start': '22:00:00', 'end': '22:35:00', 'name': 'Dinner Break 2'}],
    "shifts":     {'A': {'start': '08:30:00', 'end': '17:15:00', 'plan': 1860, 'crosses_midnight': False}, 'B': {'start': '18:30:00', 'end': '03:15:00', 'plan': 1860, 'crosses_midnight': True}},
}

if __name__ == "__main__":
    engine = CollectorEngine(CONFIG)
    engine.run()
