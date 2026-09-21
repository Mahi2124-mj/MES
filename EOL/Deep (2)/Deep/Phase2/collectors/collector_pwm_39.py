#!/usr/bin/env python3
"""
AUTO-GENERATED COLLECTOR — NUT WELDING SA
Line ID    : 41
Table      : nutwelding_pwm39_dashboard
Generated  : 2026-09-16 16:26:25

DO NOT EDIT MANUALLY — regenerate via admin panel if config changes.
"""

# ── Re-use the shared collector engine ───────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector_engine import CollectorEngine

CONFIG = {
    "line_id":    41,
    "line_name":  "NUT WELDING SA",
    "table_name": "nutwelding_pwm39_dashboard",
    "plc_ip":     "192.168.34.100",
    "plc_port":   502,
    "ok_bit":     "D6001",
    "ng_bit":     "D6002",
    "status_addr":"D6005",
    "model_addr": "D6048",
    "ideal_ct":   9.72,
    "max_ct":     10.0,
    "models":     {},
    "status_map": {},
    "breaks":     [{'start': '01:00:00', 'end': '01:10:00', 'name': 'Night Tea Break'}, {'start': '04:00:00', 'end': '04:10:00', 'name': 'Early Morning Break'}, {'start': '10:00:00', 'end': '10:10:00', 'name': 'Morning Tea Break'}, {'start': '12:00:00', 'end': '12:35:00', 'name': 'Lunch Break'}, {'start': '14:30:00', 'end': '14:40:00', 'name': 'Evening Tea Break'}, {'start': '18:00:00', 'end': '18:10:00', 'name': 'Dinner Break 1'}, {'start': '20:00:00', 'end': '20:10:00', 'name': 'Tea Break'}, {'start': '22:00:00', 'end': '22:35:00', 'name': 'Dinner Break 2'}],
    "shifts":     {'A': {'start': '08:30:00', 'end': '17:15:00', 'plan': 2900, 'crosses_midnight': False}, 'B': {'start': '18:30:00', 'end': '03:15:00', 'plan': 2900, 'crosses_midnight': True}},
}

if __name__ == "__main__":
    engine = CollectorEngine(CONFIG)
    engine.run()
