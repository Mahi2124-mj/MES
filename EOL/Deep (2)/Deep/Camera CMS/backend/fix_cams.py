import json
import os

ZONES = os.path.join(os.path.dirname(__file__), "zones.json")

def fix_cams():
    with open(ZONES, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    for line in data.get("lines", []):
        for zone in line.get("zones", []):
            for m in zone.get("machines", []):
                # Assign default camera if missing
                m["camera_id"] = "cam_panasonic_default"
                
    with open(ZONES, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    fix_cams()
