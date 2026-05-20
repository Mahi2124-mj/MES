from flask import Flask, jsonify, request
from flask_cors import CORS
import psycopg2
from psycopg2 import pool
from psycopg2.extras import DictCursor
from datetime import datetime, timedelta, time
import pytz
import json
from typing import Dict, Any, Optional, List
import traceback

app = Flask(__name__)
CORS(app)

# PostgreSQL connection pool
try:
    connection_pool = psycopg2.pool.SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        host='192.168.10.210',
        port=5432,
        database='energydb',
        user='postgres',
        password='tbdi@123',
        connect_timeout=5
    )
    print("✅ Database connection pool created successfully")
except Exception as e:
    print(f"❌ Error creating connection pool: {e}")
    connection_pool = None

class CustomJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder to handle datetime and time objects"""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, time):
            return obj.strftime('%H:%M:%S') if obj else "00:00:00"
        if isinstance(obj, timedelta):
            total_seconds = int(obj.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return super().default(obj)

app.json_encoder = CustomJSONEncoder

def get_current_shift() -> Dict[str, Any]:
    """Get current shift based on current time"""
    now = datetime.now()
    hour = now.hour
    minute = now.minute
    current_minutes = hour * 60 + minute
    
    print(f"⏰ Current time: {hour:02d}:{minute:02d} ({current_minutes} minutes)")
    
    # A Shift: 08:30 to 17:15
    if (8*60+30) <= current_minutes < (17*60+15):
        return {
            'shiftName': 'A',
            'recordDate': now.date().isoformat(),
            'isGap': False,
            'isShiftActive': True,
            'displayTime': '08:30 - 17:15'
        }
    
    # B Shift: 18:30 to 03:15 (next day)
    if (18*60+30) <= current_minutes or current_minutes < (3*60+15):
        if current_minutes < (3*60+15):
            yesterday = now - timedelta(days=1)
            record_date = yesterday.date().isoformat()
            display_time = '18:30 - 03:15 (Previous Day)'
        else:
            record_date = now.date().isoformat()
            display_time = '18:30 - 03:15'
        
        return {
            'shiftName': 'B',
            'recordDate': record_date,
            'isGap': False,
            'isShiftActive': True,
            'displayTime': display_time
        }
    
    # GAP_AB: 17:15 to 18:30
    if (17*60+15) <= current_minutes < (18*60+30):
        return {
            'shiftName': 'GAP_AB',
            'recordDate': now.date().isoformat(),
            'isGap': True,
            'isShiftActive': False,
            'displayTime': '17:15 - 18:30'
        }
    
    # GAP_BA: 03:15 to 08:30
    if (3*60+15) <= current_minutes < (8*60+30):
        return {
            'shiftName': 'GAP_BA',
            'recordDate': now.date().isoformat(),
            'isGap': True,
            'isShiftActive': False,
            'displayTime': '03:15 - 08:30'
        }
    
    return {
        'shiftName': 'A',
        'recordDate': now.date().isoformat(),
        'isGap': False,
        'isShiftActive': False,
        'displayTime': 'Unknown'
    }

def safe_get(row: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Safely get value from row with case-insensitive key lookup"""
    if not row:
        return default
    
    if key in row:
        value = row[key]
        return value if value is not None else default
    
    key_lower = key.lower()
    if key_lower in row:
        value = row[key_lower]
        return value if value is not None else default
    
    return default

def convert_value_for_json(value):
    """Convert any value to JSON-serializable format"""
    if value is None:
        return None
    
    if isinstance(value, (int, float, bool, str)):
        return value
    
    if isinstance(value, datetime):
        return value.isoformat()
    
    if isinstance(value, time):
        return value.strftime('%H:%M:%S') if value else "00:00:00"
    
    if isinstance(value, timedelta):
        total_seconds = int(value.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    try:
        return str(value)
    except:
        return None

def prepare_response_data(row: Dict[str, Any], current_shift: Dict[str, Any]) -> Dict[str, Any]:
    """Prepare response data from database row - WITH SPEED LOSS & CHANGE OVER"""
    
    # First convert all values in row to JSON serializable format
    sanitized_row = {}
    for key, value in row.items():
        sanitized_row[key] = convert_value_for_json(value)
    
    # Now use sanitized_row for safe_get
    is_gap_time = current_shift['isGap']

    # ========== STATUS MAPPING WITH CHANGE OVER ==========
    db_status = safe_get(sanitized_row, 'operating_status')
    
    status_mapping = {
        # RUNNING
        'RUN': 'RUNNING',
        'RUNNING': 'RUNNING',
        'PRODUCTION': 'RUNNING',
        'OPERATING': 'RUNNING',
        'OK': 'RUNNING',
        
        # BREAKDOWN
        'BREAK': 'BREAKDOWN',
        'BREAKDOWN': 'BREAKDOWN',
        'DOWN': 'BREAKDOWN',
        'MACHINE_DOWN': 'BREAKDOWN',
        'STOP': 'BREAKDOWN',
        
        # MATERIAL
        'MATERIAL_WAIT': 'MATERIAL',
        'MAT_WAIT': 'MATERIAL',
        'MATERIAL_SHORTAGE': 'MATERIAL',
        'NO_MATERIAL': 'MATERIAL',
        'MATERIAL': 'MATERIAL',
        
        # SETUP
        'SETUP': 'SETUP',
        'SET_UP': 'SETUP',
        'MODEL_CHANGE': 'SETUP',
        'CHANGEOVER': 'SETUP',  # Will be overridden by explicit CHANGE_OVER
        
        # QUALITY
        'QUALITY_ISSUE': 'QUALITY',
        'QUALITY_PROBLEM': 'QUALITY',
        'QUALITY': 'QUALITY',
        'QC_ISSUE': 'QUALITY',
        'DEFECT': 'QUALITY',
        
        # OTHER LOSS
        'OTHER_LOSS': 'OTHER-LOSS',
        'OTHER': 'OTHER-LOSS',
        'MISC': 'OTHER-LOSS',
        'UNPLANNED': 'OTHER-LOSS',
        'LOSS': 'OTHER-LOSS',
        
        # IDLE
        'IDLE': 'IDLE',
        'WAITING': 'IDLE',
        'PAUSE': 'IDLE',
        'STANDBY': 'IDLE',
        
        # ===== NEW: CHANGE OVER (Status 7) =====
        'CHANGE_OVER': 'CHANGE_OVER',
        'CHANGEOVER': 'CHANGE_OVER',
        'MODEL_CHANGEOVER': 'CHANGE_OVER',
        'CHANGE': 'CHANGE_OVER',
        'TOOL_CHANGE': 'CHANGE_OVER',
        'DIE_CHANGE': 'CHANGE_OVER',
        'SETUP_CHANGE': 'CHANGE_OVER',
    }
    
    operating_status = 'IDLE'
    
    if db_status:
        db_status_str = str(db_status).upper().strip()
        print(f"🔍 Database status: {db_status_str}")
        
        if db_status_str in status_mapping:
            operating_status = status_mapping[db_status_str]
        else:
            found = False
            for db_key, frontend_value in status_mapping.items():
                if db_key in db_status_str:
                    operating_status = frontend_value
                    found = True
                    break
            
            if not found:
                operating_status = db_status_str
    
    print(f"✅ Mapped status: {db_status} → {operating_status}")
    # ========== END STATUS MAPPING ==========

    # Prepare the response object - WITH SPEED LOSS & CHANGE OVER
    response_data = {
        # Core metrics
        'availability': float(safe_get(sanitized_row, 'availability', 0)),
        'current_model_name': safe_get(sanitized_row, 'current_model_name') or 'YHB/YNC/YCA 4WAY OTR',
        'cycle_time_actual': str(safe_get(sanitized_row, 'cycle_time_actual', '0.00')),
        'cycle_time_plan': str(safe_get(sanitized_row, 'cycle_time_plan', '15.60')),
        'is_gap_time': is_gap_time,
        'is_shift_completed': bool(safe_get(sanitized_row, 'is_shift_completed', False)),
        'line_name': safe_get(sanitized_row, 'line_name') or 'YNC-SEAT SLIDER',
        
        # ===== LOSS TIMES (Formatted) =====
        'loss_breakdown': safe_get(sanitized_row, 'loss_breakdown') or '00:00:00',
        'loss_material': safe_get(sanitized_row, 'loss_material') or '00:00:00',
        'loss_others': safe_get(sanitized_row, 'loss_others') or '00:00:00',
        'loss_quality': safe_get(sanitized_row, 'loss_quality') or '00:00:00',
        'loss_setup': safe_get(sanitized_row, 'loss_setup') or '00:00:00',
        'loss_speed': safe_get(sanitized_row, 'loss_speed') or '00:00:00',          # NEW: Speed Loss
        'loss_change_over': safe_get(sanitized_row, 'loss_change_over') or '00:00:00',  # NEW: Change Over
        
        # Counts
        'ng_count': int(safe_get(sanitized_row, 'ng_count', 0)),
        'ok_count': int(safe_get(sanitized_row, 'ok_count', 0)),
        
        # OEE
        'oee_grade': safe_get(sanitized_row, 'oee_grade') or ('GAP' if is_gap_time else 'NO_DATA'),
        'operating_status': operating_status,
        'overall_oe': float(safe_get(sanitized_row, 'overall_oee', safe_get(sanitized_row, 'overall_oe', 0))),
        'performance': float(safe_get(sanitized_row, 'performance', 0)),
        'period_type': safe_get(sanitized_row, 'period_type') or 'SHIFT',
        'quality_oe': float(safe_get(sanitized_row, 'quality_oee', safe_get(sanitized_row, 'quality_oe', 0))),
        
        # Shift info
        'record_date': current_shift['recordDate'],
        'shift_name': current_shift['shiftName'],
        'shift_plan_completed': int(safe_get(sanitized_row, 'shift_plan_completed', 0 if is_gap_time else 1820)),
        'total_loss': safe_get(sanitized_row, 'total_loss') or '00:00:00',
        'timestamp': datetime.now().isoformat(),
        
        # Basic fields
        'id': safe_get(sanitized_row, 'id'),
        'shift_start_time': safe_get(sanitized_row, 'shift_start_time'),
        'shift_end_time': safe_get(sanitized_row, 'shift_end_time'),
        'current_model_number': int(safe_get(sanitized_row, 'current_model_number', 9)),
        
        # ===== LOSS PARAMETERS (Seconds) =====
        'loss_breakdown_seconds': int(safe_get(sanitized_row, 'loss_breakdown_seconds', 0)),
        'loss_quality_seconds': int(safe_get(sanitized_row, 'loss_quality_seconds', 0)),
        'loss_material_seconds': int(safe_get(sanitized_row, 'loss_material_seconds', 0)),
        'loss_setup_seconds': int(safe_get(sanitized_row, 'loss_setup_seconds', 0)),
        'loss_others_seconds': int(safe_get(sanitized_row, 'loss_others_seconds', 0)),
        'loss_speed_seconds': int(safe_get(sanitized_row, 'loss_speed_seconds', 0)),        # NEW: Speed Loss Seconds
        'loss_change_over_seconds': int(safe_get(sanitized_row, 'loss_change_over_seconds', 0)),  # NEW: Change Over Seconds
        
        # ===== ALL HOURLY DATA =====
        # A Shift hours
        'hour_0830_0930_plan': int(safe_get(sanitized_row, 'hour_0830_0930_plan', 0)),
        'hour_0830_0930_actual': int(safe_get(sanitized_row, 'hour_0830_0930_actual', 0)),
        'hour_0830_0930_variance': int(safe_get(sanitized_row, 'hour_0830_0930_variance', 0)),
        'hour_0830_0930_ok': int(safe_get(sanitized_row, 'hour_0830_0930_ok', 0)),
        'hour_0830_0930_ng': int(safe_get(sanitized_row, 'hour_0830_0930_ng', 0)),
        
        'hour_0930_1030_plan': int(safe_get(sanitized_row, 'hour_0930_1030_plan', 0)),
        'hour_0930_1030_actual': int(safe_get(sanitized_row, 'hour_0930_1030_actual', 0)),
        'hour_0930_1030_variance': int(safe_get(sanitized_row, 'hour_0930_1030_variance', 0)),
        'hour_0930_1030_ok': int(safe_get(sanitized_row, 'hour_0930_1030_ok', 0)),
        'hour_0930_1030_ng': int(safe_get(sanitized_row, 'hour_0930_1030_ng', 0)),
        
        'hour_1030_1130_plan': int(safe_get(sanitized_row, 'hour_1030_1130_plan', 0)),
        'hour_1030_1130_actual': int(safe_get(sanitized_row, 'hour_1030_1130_actual', 0)),
        'hour_1030_1130_variance': int(safe_get(sanitized_row, 'hour_1030_1130_variance', 0)),
        'hour_1030_1130_ok': int(safe_get(sanitized_row, 'hour_1030_1130_ok', 0)),
        'hour_1030_1130_ng': int(safe_get(sanitized_row, 'hour_1030_1130_ng', 0)),
        
        'hour_1130_1305_plan': int(safe_get(sanitized_row, 'hour_1130_1305_plan', 0)),
        'hour_1130_1305_actual': int(safe_get(sanitized_row, 'hour_1130_1305_actual', 0)),
        'hour_1130_1305_variance': int(safe_get(sanitized_row, 'hour_1130_1305_variance', 0)),
        'hour_1130_1305_ok': int(safe_get(sanitized_row, 'hour_1130_1305_ok', 0)),
        'hour_1130_1305_ng': int(safe_get(sanitized_row, 'hour_1130_1305_ng', 0)),
        
        'hour_1305_1405_plan': int(safe_get(sanitized_row, 'hour_1305_1405_plan', 0)),
        'hour_1305_1405_actual': int(safe_get(sanitized_row, 'hour_1305_1405_actual', 0)),
        'hour_1305_1405_variance': int(safe_get(sanitized_row, 'hour_1305_1405_variance', 0)),
        'hour_1305_1405_ok': int(safe_get(sanitized_row, 'hour_1305_1405_ok', 0)),
        'hour_1305_1405_ng': int(safe_get(sanitized_row, 'hour_1305_1405_ng', 0)),
        
        'hour_1405_1505_plan': int(safe_get(sanitized_row, 'hour_1405_1505_plan', 0)),
        'hour_1405_1505_actual': int(safe_get(sanitized_row, 'hour_1405_1505_actual', 0)),
        'hour_1405_1505_variance': int(safe_get(sanitized_row, 'hour_1405_1505_variance', 0)),
        'hour_1405_1505_ok': int(safe_get(sanitized_row, 'hour_1405_1505_ok', 0)),
        'hour_1405_1505_ng': int(safe_get(sanitized_row, 'hour_1405_1505_ng', 0)),
        
        'hour_1505_1605_plan': int(safe_get(sanitized_row, 'hour_1505_1605_plan', 0)),
        'hour_1505_1605_actual': int(safe_get(sanitized_row, 'hour_1505_1605_actual', 0)),
        'hour_1505_1605_variance': int(safe_get(sanitized_row, 'hour_1505_1605_variance', 0)),
        'hour_1505_1605_ok': int(safe_get(sanitized_row, 'hour_1505_1605_ok', 0)),
        'hour_1505_1605_ng': int(safe_get(sanitized_row, 'hour_1505_1605_ng', 0)),
        
        'hour_1605_1715_plan': int(safe_get(sanitized_row, 'hour_1605_1715_plan', 0)),
        'hour_1605_1715_actual': int(safe_get(sanitized_row, 'hour_1605_1715_actual', 0)),
        'hour_1605_1715_variance': int(safe_get(sanitized_row, 'hour_1605_1715_variance', 0)),
        'hour_1605_1715_ok': int(safe_get(sanitized_row, 'hour_1605_1715_ok', 0)),
        'hour_1605_1715_ng': int(safe_get(sanitized_row, 'hour_1605_1715_ng', 0)),
        
        'hour_1715_1830_plan': int(safe_get(sanitized_row, 'hour_1715_1830_plan', 0)),
        'hour_1715_1830_actual': int(safe_get(sanitized_row, 'hour_1715_1830_actual', 0)),
        'hour_1715_1830_variance': int(safe_get(sanitized_row, 'hour_1715_1830_variance', 0)),
        'hour_1715_1830_ok': int(safe_get(sanitized_row, 'hour_1715_1830_ok', 0)),
        'hour_1715_1830_ng': int(safe_get(sanitized_row, 'hour_1715_1830_ng', 0)),
        
        # B Shift hours
        'hour_1830_1930_plan': int(safe_get(sanitized_row, 'hour_1830_1930_plan', 0)),
        'hour_1830_1930_actual': int(safe_get(sanitized_row, 'hour_1830_1930_actual', 0)),
        'hour_1830_1930_variance': int(safe_get(sanitized_row, 'hour_1830_1930_variance', 0)),
        'hour_1830_1930_ok': int(safe_get(sanitized_row, 'hour_1830_1930_ok', 0)),
        'hour_1830_1930_ng': int(safe_get(sanitized_row, 'hour_1830_1930_ng', 0)),
        
        'hour_1930_2030_plan': int(safe_get(sanitized_row, 'hour_1930_2030_plan', 0)),
        'hour_1930_2030_actual': int(safe_get(sanitized_row, 'hour_1930_2030_actual', 0)),
        'hour_1930_2030_variance': int(safe_get(sanitized_row, 'hour_1930_2030_variance', 0)),
        'hour_1930_2030_ok': int(safe_get(sanitized_row, 'hour_1930_2030_ok', 0)),
        'hour_1930_2030_ng': int(safe_get(sanitized_row, 'hour_1930_2030_ng', 0)),
        
        'hour_2030_2130_plan': int(safe_get(sanitized_row, 'hour_2030_2130_plan', 0)),
        'hour_2030_2130_actual': int(safe_get(sanitized_row, 'hour_2030_2130_actual', 0)),
        'hour_2030_2130_variance': int(safe_get(sanitized_row, 'hour_2030_2130_variance', 0)),
        'hour_2030_2130_ok': int(safe_get(sanitized_row, 'hour_2030_2130_ok', 0)),
        'hour_2030_2130_ng': int(safe_get(sanitized_row, 'hour_2030_2130_ng', 0)),
        
        'hour_2130_2305_plan': int(safe_get(sanitized_row, 'hour_2130_2305_plan', 0)),
        'hour_2130_2305_actual': int(safe_get(sanitized_row, 'hour_2130_2305_actual', 0)),
        'hour_2130_2305_variance': int(safe_get(sanitized_row, 'hour_2130_2305_variance', 0)),
        'hour_2130_2305_ok': int(safe_get(sanitized_row, 'hour_2130_2305_ok', 0)),
        'hour_2130_2305_ng': int(safe_get(sanitized_row, 'hour_2130_2305_ng', 0)),
        
        'hour_2305_0005_plan': int(safe_get(sanitized_row, 'hour_2305_0005_plan', 0)),
        'hour_2305_0005_actual': int(safe_get(sanitized_row, 'hour_2305_0005_actual', 0)),
        'hour_2305_0005_variance': int(safe_get(sanitized_row, 'hour_2305_0005_variance', 0)),
        'hour_2305_0005_ok': int(safe_get(sanitized_row, 'hour_2305_0005_ok', 0)),
        'hour_2305_0005_ng': int(safe_get(sanitized_row, 'hour_2305_0005_ng', 0)),
        
        'hour_0005_0105_plan': int(safe_get(sanitized_row, 'hour_0005_0105_plan', 0)),
        'hour_0005_0105_actual': int(safe_get(sanitized_row, 'hour_0005_0105_actual', 0)),
        'hour_0005_0105_variance': int(safe_get(sanitized_row, 'hour_0005_0105_variance', 0)),
        'hour_0005_0105_ok': int(safe_get(sanitized_row, 'hour_0005_0105_ok', 0)),
        'hour_0005_0105_ng': int(safe_get(sanitized_row, 'hour_0005_0105_ng', 0)),
        
        'hour_0105_0205_plan': int(safe_get(sanitized_row, 'hour_0105_0205_plan', 0)),
        'hour_0105_0205_actual': int(safe_get(sanitized_row, 'hour_0105_0205_actual', 0)),
        'hour_0105_0205_variance': int(safe_get(sanitized_row, 'hour_0105_0205_variance', 0)),
        'hour_0105_0205_ok': int(safe_get(sanitized_row, 'hour_0105_0205_ok', 0)),
        'hour_0105_0205_ng': int(safe_get(sanitized_row, 'hour_0105_0205_ng', 0)),
        
        'hour_0205_0315_plan': int(safe_get(sanitized_row, 'hour_0205_0315_plan', 0)),
        'hour_0205_0315_actual': int(safe_get(sanitized_row, 'hour_0205_0315_actual', 0)),
        'hour_0205_0315_variance': int(safe_get(sanitized_row, 'hour_0205_0315_variance', 0)),
        'hour_0205_0315_ok': int(safe_get(sanitized_row, 'hour_0205_0315_ok', 0)),
        'hour_0205_0315_ng': int(safe_get(sanitized_row, 'hour_0205_0315_ng', 0)),
        
        # Additional gap hours
        'hour_1715_1830_actual': int(safe_get(sanitized_row, 'hour_1715_1830_actual', 0)),
        'hour_1715_1830_ok': int(safe_get(sanitized_row, 'hour_1715_1830_ok', 0)),
        'hour_1715_1830_ng': int(safe_get(sanitized_row, 'hour_1715_1830_ng', 0)),
        
        'hour_0315_0415_actual': int(safe_get(sanitized_row, 'hour_0315_0415_actual', 0)),
        'hour_0315_0415_ok': int(safe_get(sanitized_row, 'hour_0315_0415_ok', 0)),
        'hour_0315_0415_ng': int(safe_get(sanitized_row, 'hour_0315_0415_ng', 0)),
        
        'hour_0415_0515_actual': int(safe_get(sanitized_row, 'hour_0415_0515_actual', 0)),
        'hour_0415_0515_ok': int(safe_get(sanitized_row, 'hour_0415_0515_ok', 0)),
        'hour_0415_0515_ng': int(safe_get(sanitized_row, 'hour_0415_0515_ng', 0)),
        
        'hour_0515_0615_actual': int(safe_get(sanitized_row, 'hour_0515_0615_actual', 0)),
        'hour_0515_0615_ok': int(safe_get(sanitized_row, 'hour_0515_0615_ok', 0)),
        'hour_0515_0615_ng': int(safe_get(sanitized_row, 'hour_0515_0615_ng', 0)),
        
        # ===== CYCLE TIMES =====
        'ct1': float(safe_get(sanitized_row, 'ct1', 0)),
        'ct2': float(safe_get(sanitized_row, 'ct2', 0)),
        'ct3': float(safe_get(sanitized_row, 'ct3', 0)),
        'ct4': float(safe_get(sanitized_row, 'ct4', 0)),
        'ct5': float(safe_get(sanitized_row, 'ct5', 0)),
        'ct6': float(safe_get(sanitized_row, 'ct6', 0)),
        'ct7': float(safe_get(sanitized_row, 'ct7', 0)),
        'ct8': float(safe_get(sanitized_row, 'ct8', 0)),
        'ct9': float(safe_get(sanitized_row, 'ct9', 0)),
        'ct10': float(safe_get(sanitized_row, 'ct10', 0)),
        'ct11': float(safe_get(sanitized_row, 'ct11', 0)),
        'ct12': float(safe_get(sanitized_row, 'ct12', 0)),
        'ct13': float(safe_get(sanitized_row, 'ct13', 0)),
        'ct14': float(safe_get(sanitized_row, 'ct14', 0)),
        'ct15': float(safe_get(sanitized_row, 'ct15', 0)),
        'ct16': float(safe_get(sanitized_row, 'ct16', 0)),
        'ct17': float(safe_get(sanitized_row, 'ct17', 0)),
        'ct18': float(safe_get(sanitized_row, 'ct18', 0)),
        'ct19': float(safe_get(sanitized_row, 'ct19', 0)),
        'ct20': float(safe_get(sanitized_row, 'ct20', 0)),
        
        # Cycle time statistics
        'ct_avg_20': float(safe_get(sanitized_row, 'ct_avg_20', 0)),
        'min_ct': float(safe_get(sanitized_row, 'min_ct', 0)),
        'max_ct': float(safe_get(sanitized_row, 'max_ct', 0)),
        'std_dev_ct': float(safe_get(sanitized_row, 'std_dev_ct', 0)),
        
        # Timestamps
        'created_at': safe_get(sanitized_row, 'created_at'),
        'updated_at': safe_get(sanitized_row, 'updated_at'),
    }
    
    return response_data

def get_db_connection():
    """Get database connection from pool with auto-reconnect"""
    try:
        if connection_pool:
            return connection_pool.getconn()
        else:
            return psycopg2.connect(
                host='192.168.10.210',
                port=5432,
                database='energydb',
                user='postgres',
                password='tbdi@123',
                connect_timeout=5
            )
    except Exception as e:
        print(f"❌ Error getting database connection: {e}")
        return None

def release_db_connection(conn):
    """Release database connection back to pool"""
    try:
        if connection_pool and conn:
            connection_pool.putconn(conn)
        elif conn:
            conn.close()
    except Exception as e:
        print(f"❌ Error releasing database connection: {e}")

@app.route('/api/dashboard/latest', methods=['GET'])
def get_latest_dashboard_data():
    """LATEST DASHBOARD DATA - WITH SPEED LOSS & CHANGE OVER"""
    print('\n📊 ========== FETCHING LATEST DASHBOARD DATA ==========')
    
    current_shift = get_current_shift()
    print(f"🔍 Current shift: {current_shift['shiftName']} on {current_shift['recordDate']}")
    
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            print("❌ Database connection failed")
            return jsonify({
                'connected': False,
                'current_shift': current_shift['shiftName'],
                'data': {},
                'error': 'Database connection failed',
                'success': False
            })
        
        cursor = conn.cursor(cursor_factory=DictCursor)
        
        if current_shift['isGap']:
            print(f"🕒 Gap time detected: {current_shift['shiftName']}")
            
            if current_shift['shiftName'] == 'GAP_AB':
                cursor.execute("""
                    SELECT * FROM ync_dashboard_complete 
                    WHERE record_date = %s 
                      AND shift_name = 'A'
                    ORDER BY timestamp DESC 
                    LIMIT 1
                """, (current_shift['recordDate'],))
            else:
                cursor.execute("""
                    SELECT * FROM ync_dashboard_complete 
                    WHERE record_date = %s 
                      AND shift_name = 'B'
                    ORDER BY timestamp DESC 
                    LIMIT 1
                """, (current_shift['recordDate'],))
        else:
            print(f"👷 Regular shift: {current_shift['shiftName']}")
            cursor.execute("""
                SELECT * FROM ync_dashboard_complete 
                WHERE record_date = %s 
                  AND shift_name = %s
                ORDER BY timestamp DESC 
                LIMIT 1
            """, (current_shift['recordDate'], current_shift['shiftName']))
        
        result = cursor.fetchall()
        
        if result and len(result) > 0:
            row = dict(result[0])
            print(f"✅ Data found: ID={row.get('id')}, Date={row.get('record_date')}, Shift={row.get('shift_name')}")
            
            # Check if new columns exist
            has_speed_loss = 'loss_speed_seconds' in row or 'loss_speed' in row
            has_change_over = 'loss_change_over_seconds' in row or 'loss_change_over' in row
            
            if has_speed_loss:
                print(f"✅ Speed Loss data available")
            else:
                print(f"⚠️ Speed Loss columns not found in table")
                
            if has_change_over:
                print(f"✅ Change Over data available")
            else:
                print(f"⚠️ Change Over columns not found in table")
            
            response_data = prepare_response_data(row, current_shift)
            
            return jsonify({
                'connected': True,
                'current_shift': current_shift['shiftName'],
                'data': response_data,
                'error': None,
                'success': True
            })
            
        else:
            print(f"⚠️ No data found for {current_shift['shiftName']} shift")
            
            cursor.execute("""
                SELECT * FROM ync_dashboard_complete 
                WHERE record_date = %s
                ORDER BY timestamp DESC 
                LIMIT 1
            """, (current_shift['recordDate'],))
            
            fallback_result = cursor.fetchall()
            
            if fallback_result and len(fallback_result) > 0:
                row = dict(fallback_result[0])
                print(f"⚠️ Using fallback data: shift {row.get('shift_name')}")
                
                response_data = prepare_response_data(row, current_shift)
                
                return jsonify({
                    'connected': True,
                    'current_shift': current_shift['shiftName'],
                    'data': response_data,
                    'error': f"No {current_shift['shiftName']} shift data found. Using {row.get('shift_name')} shift data.",
                    'success': True
                })
            else:
                print("❌ No data found at all for today")
                empty_data = {
                    'availability': 0,
                    'current_model_name': 'YHB/YNC/YCA 4WAY OTR',
                    'cycle_time_actual': '0.00',
                    'cycle_time_plan': '15.60',
                    'is_gap_time': current_shift['isGap'],
                    'is_shift_completed': False,
                    'line_name': 'YNC-SEAT SLIDER',
                    'loss_breakdown': '00:00:00',
                    'loss_material': '00:00:00',
                    'loss_others': '00:00:00',
                    'loss_quality': '00:00:00',
                    'loss_setup': '00:00:00',
                    'loss_speed': '00:00:00',              # NEW
                    'loss_change_over': '00:00:00',         # NEW
                    'ng_count': 0,
                    'oee_grade': 'GAP' if current_shift['isGap'] else 'NO_DATA',
                    'ok_count': 0,
                    'operating_status': 'IDLE',
                    'overall_oe': 0,
                    'performance': 0,
                    'period_type': 'SHIFT',
                    'quality_oe': 0,
                    'record_date': current_shift['recordDate'],
                    'shift_name': current_shift['shiftName'],
                    'shift_plan_completed': 0 if current_shift['isGap'] else 1820,
                    'total_loss': '00:00:00',
                    'timestamp': datetime.now().isoformat(),
                    'loss_speed_seconds': 0,               # NEW
                    'loss_change_over_seconds': 0,         # NEW
                }
                
                return jsonify({
                    'connected': True,
                    'current_shift': current_shift['shiftName'],
                    'data': empty_data,
                    'error': 'No data found in database',
                    'success': True
                })
                
    except Exception as e:
        print(f"❌ Database error: {e}")
        print(traceback.format_exc())
        
        return jsonify({
            'connected': False,
            'current_shift': current_shift['shiftName'],
            'data': {},
            'error': str(e),
            'success': False
        })
        
    finally:
        if conn:
            release_db_connection(conn)

@app.route('/api/test/raw', methods=['GET'])
def test_raw_data():
    """Test endpoint to get raw data without processing"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({'error': 'Database connection failed'})
        
        cursor = conn.cursor(cursor_factory=DictCursor)
        
        cursor.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'ync_dashboard_complete'
            ORDER BY ordinal_position
        """)
        columns = cursor.fetchall()
        
        cursor.execute("SELECT * FROM ync_dashboard_complete ORDER BY timestamp DESC LIMIT 1")
        row = cursor.fetchone()
        
        release_db_connection(conn)
        
        if row:
            row_dict = dict(row)
            for key, value in row_dict.items():
                if isinstance(value, (datetime, time)):
                    row_dict[key] = str(value)
                elif hasattr(value, 'isoformat'):
                    try:
                        row_dict[key] = value.isoformat()
                    except:
                        row_dict[key] = str(value)
            
            return jsonify({
                'success': True,
                'columns': [{'name': c[0], 'type': c[1]} for c in columns],
                'data': row_dict
            })
        else:
            return jsonify({
                'success': False,
                'error': 'No data found in table'
            })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        })

@app.route('/api/check/columns', methods=['GET'])
def check_columns():
    """Check if new columns exist in the table"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({'error': 'Database connection failed'})
        
        cursor = conn.cursor()
        
        # Check for new columns
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'ync_dashboard_complete' 
              AND column_name IN ('loss_speed_seconds', 'loss_speed', 
                                 'loss_change_over_seconds', 'loss_change_over')
        """)
        
        existing_columns = [row[0] for row in cursor.fetchall()]
        
        release_db_connection(conn)
        
        return jsonify({
            'success': True,
            'columns': {
                'loss_speed_seconds': 'loss_speed_seconds' in existing_columns,
                'loss_speed': 'loss_speed' in existing_columns,
                'loss_change_over_seconds': 'loss_change_over_seconds' in existing_columns,
                'loss_change_over': 'loss_change_over' in existing_columns,
            }
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })

@app.route('/api/add/columns', methods=['POST'])
def add_columns():
    """Add new columns to the table (Admin only)"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({'error': 'Database connection failed'})
        
        cursor = conn.cursor()
        
        # Add speed loss columns
        cursor.execute("""
            ALTER TABLE ync_dashboard_complete 
            ADD COLUMN IF NOT EXISTS loss_speed_seconds INTEGER DEFAULT 0,
            ADD COLUMN IF NOT EXISTS loss_speed VARCHAR(20) DEFAULT '00:00:00'
        """)
        
        # Add change over columns
        cursor.execute("""
            ALTER TABLE ync_dashboard_complete 
            ADD COLUMN IF NOT EXISTS loss_change_over_seconds INTEGER DEFAULT 0,
            ADD COLUMN IF NOT EXISTS loss_change_over VARCHAR(20) DEFAULT '00:00:00'
        """)
        
        conn.commit()
        release_db_connection(conn)
        
        return jsonify({
            'success': True,
            'message': '✅ Speed Loss and Change Over columns added successfully'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })

if __name__ == '__main__':
    PORT = 3500
    
    print("\n✅ ========== DASHBOARD BACKEND SERVER STARTED ==========")
    print(f"🌐 URL: http://localhost:{PORT}")
    print("\n📡 API ENDPOINTS:")
    print("├── GET  /api/dashboard/latest  - Main dashboard data (with Speed Loss & Change Over)")
    print("├── GET  /api/test/raw          - Get raw database data")
    print("├── GET  /api/check/columns     - Check if new columns exist")
    print("└── POST /api/add/columns       - Add Speed Loss & Change Over columns")
    
    current_shift = get_current_shift()
    print(f"\n📅 CURRENT SHIFT: {current_shift['shiftName']}")
    print(f"📅 CURRENT DATE: {current_shift['recordDate']}")
    print(f"⏰ DISPLAY TIME: {current_shift['displayTime']}")
    print(f"🔧 IS GAP TIME: {current_shift['isGap']}")
    print("\n🚀 NEW FEATURES:")
    print("   ✅ SPEED LOSS - loss_speed, loss_speed_seconds")
    print("   ✅ CHANGE OVER - loss_change_over, loss_change_over_seconds")
    print("\n🚀 Server ready!")
    
    app.run(host='0.0.0.0', port=PORT, debug=True, use_reloader=False)