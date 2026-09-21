#!/usr/bin/env python3
"""
AUTO-GENERATED COLLECTOR — Loop Pipe-Line 1
Line ID    : 30
Table      : loop_pipe_dashboard_01
Generated  : 2026-08-18 11:01:12

DO NOT EDIT MANUALLY — regenerate via admin panel if config changes.
"""

# ── Re-use the shared collector engine ───────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector_engine import CollectorEngine

CONFIG = {
    "line_id":    30,
    "line_name":  "Loop Pipe-Line 1",
    "table_name": "loop_pipe_dashboard_01",
    "plc_ip":     "192.168.36.55",
    "plc_port":   5002,
    "ok_bit":     "D101",
    "ng_bit":     "D102",
    "status_addr":"D6005",
    "model_addr": "D6048",
    "ideal_ct":   19.0,
    "max_ct":     19.1,
    "models":     {},
    "status_map": {},
    "breaks":     [],
    "shifts":     {'A': {'start': '08:30:00', 'end': '17:15:00', 'plan': 1468, 'crosses_midnight': False}, 'B': {'start': '18:30:00', 'end': '03:15:00', 'plan': 1468, 'crosses_midnight': True}},
}

if __name__ == "__main__":
    engine = CollectorEngine(CONFIG)
    engine.run()
