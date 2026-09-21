import os
import json
import psycopg2
import psycopg2.extras

DB_CONFIG = {
    'host': 'db.cpwfbkbjgbmfywdnxafh.supabase.co',
    'port': 5432,
    'dbname': 'postgres',
    'user': 'postgres',
    'password': 'tbdi@9592963360',
}

ZONES_FILE = os.path.join(os.path.dirname(__file__), "zones.json")

def load_existing():
    if os.path.exists(ZONES_FILE):
        with open(ZONES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"lines": []}

def save_zones(data):
    with open(ZONES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def import_supabase():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    cur.execute('SELECT * FROM zones ORDER BY id;')
    sb_zones = cur.fetchall()
    
    cur.execute('SELECT * FROM lines ORDER BY id;')
    sb_lines = cur.fetchall()
    
    cur.execute('SELECT * FROM machines ORDER BY id;')
    sb_machines = cur.fetchall()
    
    conn.close()

    # Old Supabase: Zone -> Line -> Machine
    # New JSON: Line -> Zone -> Machine
    # We will map Supabase Zone to JSON Line, and Supabase Line to JSON Zone
    
    existing_data = load_existing()
    # Build a lookup for existing cameras assigned to machines
    camera_map = {}
    for line in existing_data.get("lines", []):
        for zone in line.get("zones", []):
            for m in zone.get("machines", []):
                if m.get("camera_id"):
                    camera_map[m["name"].lower()] = m["camera_id"]
                    
    new_data = {"lines": []}
    
    for sz in sb_zones:
        line_item = {
            "id": f"line_sb_{sz['id']}",
            "name": sz['zone_name'],
            "zones": []
        }
        
        # find matching lines for this sz
        for sl in sb_lines:
            if sl['zone_id'] == sz['id']:
                zone_item = {
                    "id": f"zone_sb_{sl['id']}",
                    "name": sl['line_name'],
                    "machines": []
                }
                
                # find matching machines
                for sm in sb_machines:
                    if sm['line_id'] == sl['id']:
                        camera = camera_map.get(sm['machine_name'].lower(), None)
                        mac_item = {
                            "id": f"machine_sb_{sm['id']}",
                            "name": sm['machine_name'],
                            "camera_id": camera
                        }
                        zone_item["machines"].append(mac_item)
                        
                line_item["zones"].append(zone_item)
                
        new_data["lines"].append(line_item)
        
    save_zones(new_data)
    print("Migration from Supabase complete. Data saved to zones.json")
    print(f"Total lines created: {len(new_data['lines'])}")

if __name__ == "__main__":
    import_supabase()
