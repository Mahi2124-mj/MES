#!/usr/bin/env python3
"""
AUTO-GENERATED COLLECTOR — YSD Recliner
Line ID    : 29
Table      : ysd_recliner_complete
Generated  : 2026-11-12 15:48:03

DO NOT EDIT MANUALLY — regenerate via admin panel if config changes.
"""

# ── Re-use the shared collector engine ───────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector_engine import CollectorEngine

CONFIG = {
    "line_id":    29,
    "line_name":  "YSD Recliner",
    "table_name": "ysd_recliner_complete",
    "plc_ip":     "192.168.32.97",
    "plc_port":   502,
    "ok_bit":     "D7001",
    "ng_bit":     "D7002",
    "status_addr":"D6005",
    "model_addr": "D6048",
    "ideal_ct":   15.5,
    "max_ct":     16.5,
    "models":     {},
    "status_map": {0: {'name': 'IDLE', 'loss': None}, 1: {'name': 'RUNNING', 'loss': None}, 2: {'name': 'BREAKDOWN', 'loss': 'breakdown'}, 3: {'name': 'QUALITY_ISSUE', 'loss': 'quality'}, 4: {'name': 'MODEL_SETUP', 'loss': 'setup'}, 5: {'name': 'MATERIAL_WAIT', 'loss': 'material'}, 6: {'name': 'OTHER_LOSS', 'loss': 'others'}, 7: {'name': 'CHANGE_OVER', 'loss': 'change_over'}, 8: {'name': 'BREAK', 'loss': 'break'}},
    "breaks":     [],
    "shifts":     {'A': {'start': '08:30:00', 'end': '17:15:00', 'plan': 1750, 'crosses_midnight': False}, 'B': {'start': '18:30:00', 'end': '03:15:00', 'plan': 1750, 'crosses_midnight': True}},
}

if __name__ == "__main__":
    engine = CollectorEngine(CONFIG)
    engine.run()
