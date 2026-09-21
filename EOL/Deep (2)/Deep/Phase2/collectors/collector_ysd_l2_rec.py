#!/usr/bin/env python3
"""
AUTO-GENERATED COLLECTOR — YSD Recliner
Line ID    : 29
Table      : ysd_recliner_complete
Generated  : 2026-08-17 14:22:14

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
    "plc_port":   5002,
    "ok_bit":     "D6001",
    "ng_bit":     "D6002",
    "status_addr":"D6005",
    "model_addr": "D6048",
    "ideal_ct":   16.0,
    "max_ct":     17.0,
    "models":     {},
    "status_map": {},
    "breaks":     [],
    "shifts":     {},
}

if __name__ == "__main__":
    engine = CollectorEngine(CONFIG)
    engine.run()
