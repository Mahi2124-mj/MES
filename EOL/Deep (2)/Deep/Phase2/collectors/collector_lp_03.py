#!/usr/bin/env python3
"""
AUTO-GENERATED COLLECTOR — Loop Pipe-Line 3
Line ID    : 31
Table      : loop_pipe_dashboard_03
Generated  : 2026-09-03 10:00:36

DO NOT EDIT MANUALLY — regenerate via admin panel if config changes.
"""

# ── Re-use the shared collector engine ───────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector_engine import CollectorEngine

CONFIG = {
    "line_id":    31,
    "line_name":  "Loop Pipe-Line 3",
    "table_name": "loop_pipe_dashboard_03",
    "plc_ip":     "192.168.36.75",
    "plc_port":   5002,
    "ok_bit":     "D101",
    "ng_bit":     "D102",
    "status_addr":"D6005",
    "model_addr": "D6048",
    "ideal_ct":   10.07,
    "max_ct":     11.0,
    "models":     {},
    "status_map": {},
    "breaks":     [{'start': '01:00:00', 'end': '01:10:00', 'name': 'Night Tea Break'}, {'start': '04:00:00', 'end': '04:10:00', 'name': 'Early Morning Break'}, {'start': '10:00:00', 'end': '10:10:00', 'name': 'Morning Tea Break'}, {'start': '12:00:00', 'end': '12:35:00', 'name': 'Lunch Break'}, {'start': '14:30:00', 'end': '14:40:00', 'name': 'Evening Tea Break'}, {'start': '18:00:00', 'end': '18:10:00', 'name': 'Dinner Break 1'}, {'start': '20:00:00', 'end': '20:10:00', 'name': 'Tea Break'}, {'start': '22:00:00', 'end': '22:35:00', 'name': 'Dinner Break 2'}],
    "shifts":     {'A': {'start': '08:30:00', 'end': '17:15:00', 'plan': 2850, 'crosses_midnight': False}, 'B': {'start': '18:30:00', 'end': '03:15:00', 'plan': 2850, 'crosses_midnight': True}},
}

if __name__ == "__main__":
    engine = CollectorEngine(CONFIG)
    engine.run()
