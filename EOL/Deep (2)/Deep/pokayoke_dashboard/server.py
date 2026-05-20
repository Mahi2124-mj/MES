"""
server.py — Flask backend for Poka Yoke Dashboard
==================================================
Run:  python server.py        → http://localhost:5000
"""

import json, os, tempfile
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE, "pokayoke_data.json")

VALUE_LABELS = {0: "PASS", 1: "OFF", 2: "ON"}


# ── Helpers ────────────────────────────────────────────────────
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"models": [], "poka_yokes": [], "sensor_mapping": {},
                "compiled": [], "model_columns": {}, "plc_actuals_raw": {},
                "last_updated": None, "stats": {}}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    data["last_updated"] = datetime.now().isoformat()
    # Recalc stats
    comp = data.get("compiled", [])
    match     = sum(1 for c in comp if c.get("status") == "MATCH")
    mismatch  = sum(1 for c in comp if c.get("status") == "MISMATCH")
    nodata    = sum(1 for c in comp if c.get("status") not in ("MATCH", "MISMATCH"))
    data["stats"] = {
        "total":        len(comp),
        "match":        match,
        "mismatch":     mismatch,
        "no_data":      nodata,
        "total_models": len(data.get("models", [])),
        "total_py":     len(data.get("poka_yokes", [])),
        "model_codes":  sorted(set(m.get("model", "") for m in data.get("models", []) if m.get("model"))),
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


# ── Static ─────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(BASE, "index.html")


@app.route("/api/data")
def api_data():
    return send_from_directory(BASE, "pokayoke_data.json")


# ── POST /api/update-desired ───────────────────────────────────
@app.route("/api/update-desired", methods=["POST"])
def update_desired():
    """
    Single row:  { py_no, model, model_type, type_side, desired_value }
    Batch:       { updates: [ {py_no, model, model_type, type_side, desired_value}, ... ] }
    """
    body = request.json
    data = load_data()

    updates = body.get("updates") or [body]
    changed = 0

    for upd in updates:
        py_no = upd.get("py_no", "").strip()
        model = upd.get("model", "").strip()
        mtype = upd.get("model_type", "").strip()
        side  = upd.get("type_side", "").strip()
        dv    = upd.get("desired_value")

        if not py_no or dv is None:
            continue
        try:
            dv = int(dv)
        except (ValueError, TypeError):
            continue

        for row in data["compiled"]:
            if row["py_no"] != py_no:
                continue
            if model and row.get("model", "") != model:
                continue
            if mtype and row.get("model_type", "") != mtype:
                continue
            if side and row.get("type_side", "") != side:
                continue

            row["desired"] = dv
            actual = row.get("actual")
            if actual is not None:
                row["status"] = "MATCH" if actual == dv else "MISMATCH"
            else:
                row["status"] = "NO_DATA"
            changed += 1

    if changed == 0:
        return jsonify({"ok": False, "error": "No matching rows found"}), 404

    save_data(data)
    return jsonify({"ok": True, "updated": changed})


# ── POST /api/add-model ───────────────────────────────────────
@app.route("/api/add-model", methods=["POST"])
def add_model():
    """
    { model_code, model_name, type, old_model_no }
    """
    body = request.json
    code = body.get("model_code", "").strip()
    name = body.get("model_name", "").strip()
    mtype = body.get("type", "").strip()
    old_no = body.get("old_model_no", "").strip()

    if not code or not name:
        return jsonify({"ok": False, "error": "model_code and model_name required"}), 400

    data = load_data()

    # Check duplicate
    for m in data["models"]:
        if m.get("model_name") == name:
            return jsonify({"ok": False, "error": f"Model '{name}' already exists"}), 409

    data["models"].append({
        "model_name": name,
        "type": mtype,
        "old_model_no": old_no,
        "model": code,
    })

    save_data(data)
    return jsonify({"ok": True, "model_name": name})


# ── POST /api/import-excel ─────────────────────────────────────
@app.route("/api/import-excel", methods=["POST"])
def import_excel():
    """
    Accepts multipart upload of an Excel file.
    If it has 'final seat' → full re-import via import_and_merge.
    If it has 'Sheet1' → update PLC actuals only.
    Also accepts model_columns JSON in form data for custom column mapping.
    """
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename.endswith((".xlsx", ".xls")):
        return jsonify({"ok": False, "error": "Only .xlsx files accepted"}), 400

    # Optional model column mapping from form data
    mc_json = request.form.get("model_columns")
    custom_columns = None
    if mc_json:
        try:
            custom_columns = json.loads(mc_json)
        except json.JSONDecodeError:
            pass

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    file.save(tmp.name)
    tmp.close()

    try:
        import openpyxl
        wb = openpyxl.load_workbook(tmp.name, data_only=True)
        sheets = wb.sheetnames

        if "final seat" in sheets or "MODEL MASTER" in sheets:
            from import_and_merge import (
                read_export, read_plc_actuals, merge,
                SENSOR_TO_DBIT, MODEL_COLUMNS,
            )
            # Look for PLC file
            plc_path = None
            for p in [os.path.join(BASE, "poka_yoke_full.xlsx"),
                      os.path.join(BASE, "..", "Phase2", "poka_yoke_full.xlsx")]:
                if os.path.exists(p):
                    plc_path = p
                    break

            plc_actuals = read_plc_actuals(plc_path, SENSOR_TO_DBIT) if plc_path else {}
            mm, py, asgn = read_export(tmp.name)
            mc = custom_columns or MODEL_COLUMNS
            merged = merge(plc_actuals, mm, py, asgn, SENSOR_TO_DBIT, mc)
            save_data(merged)
            return jsonify({"ok": True, "type": "full_reimport",
                            "models": len(mm), "assignments": len(asgn),
                            "stats": merged["stats"]})

        elif "Sheet1" in sheets:
            from import_and_merge import read_plc_actuals, SENSOR_TO_DBIT, MODEL_COLUMNS
            data = load_data()

            plc_actuals = read_plc_actuals(tmp.name, data.get("sensor_mapping") or SENSOR_TO_DBIT)
            mc = custom_columns or data.get("model_columns") or MODEL_COLUMNS

            updated = 0
            for row in data["compiled"]:
                d_bit = row.get("d_bit")
                if not d_bit or d_bit not in plc_actuals:
                    continue
                from import_and_merge import resolve_model_column
                col_key = resolve_model_column(row, mc)
                if col_key and col_key in plc_actuals[d_bit].get("actuals", {}):
                    row["actual"] = plc_actuals[d_bit]["actuals"][col_key]
                    row["sensor_name"] = plc_actuals[d_bit].get("sensor_name")
                    row["plc_column"] = col_key
                    desired = row.get("desired")
                    if desired is not None:
                        row["status"] = "MATCH" if row["actual"] == desired else "MISMATCH"
                    else:
                        row["status"] = "ACTUAL_ONLY"
                    updated += 1

            data["plc_actuals_raw"] = plc_actuals
            save_data(data)
            return jsonify({"ok": True, "type": "plc_update", "updated": updated,
                            "stats": data["stats"]})
        else:
            return jsonify({"ok": False, "error": f"Unknown format. Sheets: {sheets}"}), 400

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        os.unlink(tmp.name)


# ── POST /api/update-sensor-mapping ────────────────────────────
@app.route("/api/update-sensor-mapping", methods=["POST"])
def update_sensor_mapping():
    """
    { sensor_name: "relocate pin", d_bit: "D.081" }
    or { mappings: { "sensor1": "D.xxx", "sensor2": "D.yyy" } }
    """
    body = request.json
    data = load_data()

    if "mappings" in body:
        for sensor, dbit in body["mappings"].items():
            data["sensor_mapping"][sensor.strip()] = dbit.strip() if dbit else None
    else:
        sensor = body.get("sensor_name", "").strip()
        dbit   = body.get("d_bit", "").strip()
        if not sensor:
            return jsonify({"ok": False, "error": "sensor_name required"}), 400
        data["sensor_mapping"][sensor] = dbit or None

    save_data(data)
    return jsonify({"ok": True, "sensor_mapping": data["sensor_mapping"]})


# ── POST /api/delete-model ─────────────────────────────────────
@app.route("/api/delete-model", methods=["POST"])
def delete_model():
    body = request.json
    name = body.get("model_name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "model_name required"}), 400

    data = load_data()
    data["models"] = [m for m in data["models"] if m["model_name"] != name]
    data["compiled"] = [c for c in data["compiled"] if c["model_name"] != name]
    save_data(data)
    return jsonify({"ok": True})


# ── Main ───────────────────────────────────────────────────────
if __name__ == "__main__":
    if not os.path.exists(DATA_FILE):
        print("[WARN] pokayoke_data.json not found. Run: python import_and_merge.py")
    print("[INFO] http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
