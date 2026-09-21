import csv
import json
import os

CSV_PATH = r"C:\Users\vivek.kumar\Downloads\machines.csv"
JSON_PATH = os.path.join(os.path.dirname(__file__), "zones.json")

def import_data():
    zones_map = {}
    
    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            z_id = f"zone_{row['zone_id']}"
            z_name = row['zone_name']
            
            l_id = f"line_{row['line_id']}"
            l_name = row['line_name']
            
            m_id = f"machine_{row['id']}" # Using numerical id from row['id']
            m_name = row['machine_name']
            
            if z_id not in zones_map:
                zones_map[z_id] = {"id": z_id, "name": z_name, "lines": {}}
                
            if l_id not in zones_map[z_id]["lines"]:
                zones_map[z_id]["lines"][l_id] = {"id": l_id, "name": l_name, "machines": []}
                
            zones_map[z_id]["lines"][l_id]["machines"].append({
                "id": m_id,
                "name": m_name,
                "camera_id": None
            })

    # Convert maps to lists
    final_zones = []
    for z_id, z_data in zones_map.items():
        lines_list = list(z_data["lines"].values())
        final_zones.append({
            "id": z_id,
            "name": z_data["name"],
            "lines": lines_list
        })
        
    payload = {"zones": final_zones}
    
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        
    print(f"Successfully imported {len(final_zones)} zones with their respective lines and machines.")

if __name__ == "__main__":
    import_data()
