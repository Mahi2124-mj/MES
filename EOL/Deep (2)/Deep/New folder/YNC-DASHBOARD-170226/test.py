"""
YNC COMPLETE DATA COLLECTOR - FINAL WORKING VERSION
WITH 7 LOSSES & DYNAMIC PLAN & REAL-TIME HOURLY UPDATES & CORRECT OEE
SINGLE TABLE ARCHITECTURE - Only ync_dashboard_complete
"""

import pymcprotocol
import psycopg2
import time
from datetime import datetime, date, time as dt_time, timedelta
import sys
import statistics
import traceback


# ========== CONFIGURATION ==========
PLC_IP = "192.168.10.150"
PLC_PORT = 5002

DB_CONFIG = {
    "host": "192.168.10.210",
    "port": "5432",
    "database": "energydb",
    "user": "postgres",
    "password": "tbdi@123"
}

# ========== MODEL MAPPING ==========
MODEL_MAPPING = {
    1: "4WAY OUTER",
    2: "4WAY INR RH", 
    3: "4WAY INR LH",
    4: "YRA 6WAY OTR RH",
    5: "YRA 6WAY INR RH",
    6: "YRA 6WAY OTR LH",
    7: "YRA 6WAY INR LH",
    8: "YJC 6WAY INR RH",
    9: "YHB/YNC/YCA 4WAY OTR",
    10: "YHB/YCA 4WAY INR RH",
    11: "YHB 4WAY INR LH",
    12: "YCA 4WAY INR LH",
    13: "YJC 4WAY INR LH",
    14: "YNC 4WAY INR RH",
    15: "YNC 4WAY INR LH",
    16: "YNC 4WAY INR W/O H",
    17: "YNC 6WAY INR RH",
    18: "YNC 6WAY OTR RH",
    19: "YTB INR LH EXPORT",
    20: "YY8 4WAY INR RH",
    21: "YY8 4WAY INR LH"
}

# ========== STATUS MAPPING (D6005) - WITH CHANGE OVER ==========
STATUS_MAPPING = {
    0: "IDLE",
    1: "RUNNING",
    2: "BREAKDOWN",
    3: "QUALITY_ISSUE",
    4: "MODEL_SETUP",
    5: "MATERIAL_WAIT",
    6: "OTHER_LOSS",
    7: "CHANGE_OVER"
}

# ========== SHIFT CONFIGURATION - UPDATED PLAN 1860 ==========
SHIFT_CONFIG = {
    "A": {
        "name": "A",
        "start_time": dt_time(8, 30),
        "end_time": dt_time(17, 15),
        "total_plan": 1860,
        "working_minutes": 465,
        "hourly_plan": {
            "08:30-09:30": 220,   # 55 min × 4
            "09:30-10:30": 200,   # 50 min × 4
            "10:30-11:30": 240,   # 60 min × 4
            "11:30-13:05": 240,   # 60 min × 4
            "13:05-14:05": 240,   # 60 min × 4
            "14:05-15:05": 200,   # 50 min × 4
            "15:05-16:05": 240,   # 60 min × 4
            "16:05-17:15": 280    # 70 min × 4
        }
    },
    "B": {
        "name": "B",
        "start_time": dt_time(18, 30),
        "end_time": dt_time(3, 15),
        "total_plan": 1860,
        "working_minutes": 465,
        "hourly_plan": {
            "18:30-19:30": 220,   # 55 min × 4
            "19:30-20:30": 200,   # 50 min × 4
            "20:30-21:30": 240,   # 60 min × 4
            "21:30-23:05": 240,   # 60 min × 4
            "23:05-00:05": 240,   # 60 min × 4
            "00:05-01:05": 220,   # 55 min × 4
            "01:05-02:05": 220,   # 55 min × 4
            "02:05-03:15": 280    # 70 min × 4
        }
    },
    "GAP_AB": {
        "name": "GAP_AB",
        "start_time": dt_time(17, 15),
        "end_time": dt_time(18, 30),
        "total_plan": 0,
        "working_minutes": 0,
        "hourly_plan": {}
    },
    "GAP_BA": {
        "name": "GAP_BA",
        "start_time": dt_time(3, 15),
        "end_time": dt_time(8, 30),
        "total_plan": 0,
        "working_minutes": 0,
        "hourly_plan": {}
    }
}

# ========== BREAK TIMES CONFIGURATION ==========
BREAK_TIMES = [
    {"start": dt_time(10, 0), "end": dt_time(10, 10), "name": "Morning Tea Break"},
    {"start": dt_time(12, 0), "end": dt_time(12, 35), "name": "Lunch Break"},
    {"start": dt_time(14, 30), "end": dt_time(14, 40), "name": "Evening Tea Break"},
    {"start": dt_time(18, 0), "end": dt_time(18, 10), "name": "Dinner Break 1"},
    {"start": dt_time(20, 0), "end": dt_time(20, 10), "name": "Tea Break"},
    {"start": dt_time(22, 0), "end": dt_time(22, 35), "name": "Dinner Break 2"},
    {"start": dt_time(1, 0), "end": dt_time(1, 10), "name": "Night Tea Break"},
    {"start": dt_time(4, 0), "end": dt_time(4, 10), "name": "Early Morning Tea Break"},
]

# ========== WORKING SEGMENTS WITH 5 MIN DELAY AT START ==========
A_SHIFT_SEGMENTS = [
    {"db_field": "hour_0830_0930", "time_slot": "08:30-09:30", "start_delay": 5, "working": 55},
    {"db_field": "hour_0930_1030", "time_slot": "09:30-10:30", "start_delay": 0, "working": 50},
    {"db_field": "hour_1030_1130", "time_slot": "10:30-11:30", "start_delay": 0, "working": 60},
    {"db_field": "hour_1130_1305", "time_slot": "11:30-13:05", "start_delay": 0, "working": 60},
    {"db_field": "hour_1305_1405", "time_slot": "13:05-14:05", "start_delay": 0, "working": 60},
    {"db_field": "hour_1405_1505", "time_slot": "14:05-15:05", "start_delay": 0, "working": 50},
    {"db_field": "hour_1505_1605", "time_slot": "15:05-16:05", "start_delay": 0, "working": 60},
    {"db_field": "hour_1605_1715", "time_slot": "16:05-17:15", "start_delay": 0, "working": 70}
]

B_SHIFT_SEGMENTS = [
    {"db_field": "hour_1830_1930", "time_slot": "18:30-19:30", "start_delay": 5, "working": 55},
    {"db_field": "hour_1930_2030", "time_slot": "19:30-20:30", "start_delay": 0, "working": 50},
    {"db_field": "hour_2030_2130", "time_slot": "20:30-21:30", "start_delay": 0, "working": 60},
    {"db_field": "hour_2130_2305", "time_slot": "21:30-23:05", "start_delay": 0, "working": 60},
    {"db_field": "hour_2305_0005", "time_slot": "23:05-00:05", "start_delay": 0, "working": 60},
    {"db_field": "hour_0005_0105", "time_slot": "00:05-01:05", "start_delay": 0, "working": 55},
    {"db_field": "hour_0105_0205", "time_slot": "01:05-02:05", "start_delay": 0, "working": 55},
    {"db_field": "hour_0205_0315", "time_slot": "02:05-03:15", "start_delay": 0, "working": 70}
]

class CycleTimeTracker:
    """Tracks last 20 cycle times with speed loss detection"""
    
    def __init__(self, window_size=20, ideal_cycle_time=15.0, max_allowed_cycle=16.0):
        self.window_size = window_size
        self.cycle_times = []
        self.last_pulse_time = None
        self.pulse_counter = 0
        
        # Speed loss tracking
        self.ideal_cycle_time = ideal_cycle_time
        self.max_allowed_cycle = max_allowed_cycle
        self.speed_loss_seconds = 0
        self.last_speed_loss_log = 0
        
        # State tracking
        self.is_running = False
        self.pulse_received_in_current_state = False
        
    def is_break_time(self):
        """Check if current time is in break time"""
        now = datetime.now().time()
        
        for break_time in BREAK_TIMES:
            start = break_time["start"]
            end = break_time["end"]
            
            if end < start:
                if now >= start or now < end:
                    return True, break_time["name"]
            else:
                if start <= now < end:
                    return True, break_time["name"]
        
        return False, None
    
    def add_cycle_time(self, cycle_time: float):
        """Add new cycle time and detect speed loss"""
        cycle_time = round(cycle_time, 2)
        self.cycle_times.append(cycle_time)
        
        in_break, break_name = self.is_break_time()
        
        # Speed loss only when RUNNING and NOT in break
        if self.is_running and not in_break:
            if cycle_time > self.max_allowed_cycle:
                extra_time = cycle_time - self.ideal_cycle_time
                self.speed_loss_seconds += extra_time
                
                current_time = time.time()
                if current_time - self.last_speed_loss_log > 30:
                    print(f"[SPEED-LOSS] ⚠️ Cycle: {cycle_time}s (>{self.max_allowed_cycle}s) | "
                          f"Loss: {extra_time:.2f}s | Total: {self.speed_loss_seconds:.1f}s")
                    self.last_speed_loss_log = current_time
        else:
            self.pulse_received_in_current_state = False
        
        if len(self.cycle_times) > self.window_size:
            self.cycle_times.pop(0)
        
        self.pulse_counter += 1
        return self.get_current_data()
    
    def calculate_cycle_time(self, current_time: float, is_running: bool):
        """Calculate cycle time from current and last pulse"""
        self.is_running = is_running
        
        in_break, break_name = self.is_break_time()
        
        if not is_running or in_break:
            self.last_pulse_time = None
            self.pulse_received_in_current_state = False
            return None
        
        if self.last_pulse_time is None:
            self.last_pulse_time = current_time
            self.pulse_received_in_current_state = True
            return None
        
        cycle_time = current_time - self.last_pulse_time
        self.last_pulse_time = current_time
        self.pulse_received_in_current_state = True
        
        if 1.0 <= cycle_time <= 300.0:
            return self.add_cycle_time(cycle_time)
        
        return self.get_current_data()
    
    def check_speed_loss_continuous(self, current_time):
        """Check for continuous speed loss"""
        if not self.is_running:
            return 0
        
        in_break, break_name = self.is_break_time()
        if in_break:
            return 0
        
        if self.last_pulse_time is None:
            return 0
        
        time_since_last_pulse = current_time - self.last_pulse_time
        
        if (time_since_last_pulse > self.max_allowed_cycle and 
            time_since_last_pulse < 300 and 
            self.pulse_received_in_current_state):
            
            extra_loss = min(1.0, time_since_last_pulse - self.max_allowed_cycle)
            if extra_loss > 0:
                self.speed_loss_seconds += extra_loss
                
                if current_time - self.last_speed_loss_log > 30:
                    print(f"[SPEED-LOSS] 🔴 Continuous: No pulse for {time_since_last_pulse:.1f}s | "
                          f"Adding {extra_loss:.2f}s | Total: {self.speed_loss_seconds:.1f}s")
                    self.last_speed_loss_log = current_time
                
                return extra_loss
        
        return 0
    
    def get_current_data(self):
        """Get current cycle time data"""
        if not self.cycle_times:
            return {
                "cycle_times": [],
                "avg_20": 15.0,
                "min_ct": 15.0,
                "max_ct": 15.0,
                "std_dev": 0.0,
                "count": 0,
                "speed_loss_seconds": self.speed_loss_seconds
            }
        
        avg_20 = sum(self.cycle_times) / len(self.cycle_times)
        min_ct = min(self.cycle_times)
        max_ct = max(self.cycle_times)
        
        if len(self.cycle_times) > 1:
            std_dev = statistics.stdev(self.cycle_times)
        else:
            std_dev = 0.0
        
        return {
            "cycle_times": self.cycle_times.copy(),
            "avg_20": round(avg_20, 2),
            "min_ct": round(min_ct, 2),
            "max_ct": round(max_ct, 2),
            "std_dev": round(std_dev, 2),
            "count": len(self.cycle_times),
            "speed_loss_seconds": self.speed_loss_seconds
        }
    
    def get_ct_dict(self):
        """Get dictionary with ct1 to ct20 keys"""
        data = self.get_current_data()
        ct_dict = {}
        
        for i in range(1, 21):
            key = f"ct{i}"
            if i <= len(data["cycle_times"]):
                ct_dict[key] = data["cycle_times"][-i]
            else:
                ct_dict[key] = None
        
        ct_dict["ct_avg_20"] = data["avg_20"]
        ct_dict["min_ct"] = data["min_ct"]
        ct_dict["max_ct"] = data["max_ct"]
        ct_dict["std_dev_ct"] = data["std_dev"]
        ct_dict["speed_loss_seconds"] = data["speed_loss_seconds"]
        
        return ct_dict
    
    def reset_speed_loss(self):
        """Reset speed loss counter"""
        self.speed_loss_seconds = 0
        self.last_pulse_time = None
        self.pulse_received_in_current_state = False
        print("[SPEED-LOSS] 🔄 Counter reset for new shift")
    
    def set_running_state(self, is_running):
        """Update running state"""
        self.is_running = is_running
        if not is_running:
            self.last_pulse_time = None
            self.pulse_received_in_current_state = False

class YNCCompleteCollector:
    def __init__(self):
        self.plc = None
        self.db_conn = None
        self.plc_connected = False
        self.db_connected = False
        
        # Production counting
        self.ok_count_total = 0
        self.ng_count_total = 0
        self.ok_count_shift = 0
        self.ng_count_shift = 0
        self.last_ok_state = 0
        self.last_ng_state = 0
        
        # Cycle time tracking
        self.cycle_tracker = CycleTimeTracker(
            window_size=20, 
            ideal_cycle_time=15.0,
            max_allowed_cycle=16.0
        )
        
        # Model tracking
        self.current_model = 1
        self.current_model_name = "4WAY OUTER"
        
        # Shift tracking
        self.current_shift = None
        self.shift_start_time = None
        self.shift_record_id = None
        self.shift_plan_total = 1860
        self.shift_plan_remaining = 1860
        self.shift_plan_completed = 0
        
        # Working minutes tracking (for break times)
        self.last_working_minutes = 0
        self.last_plan_calculation_time = 0
        
        # Hourly tracking
        self.hourly_data = {}
        self.current_hour_key = None
        self.last_hour_check = time.time()
        self.last_hourly_db_update = time.time()
        
        # Loss tracking - 7 LOSSES
        self.loss_seconds = {
            "breakdown": 0,
            "quality": 0,
            "setup": 0,
            "material": 0,
            "others": 0,
            "speed": 0,
            "change_over": 0
        }
        self.last_status_check = time.time()
        self.current_status_code = 0
        self.current_status_name = "IDLE"
        
        # Daily tracking
        self.current_date = datetime.now().date()
        
        # Pulse tracking
        self.last_ok_pulse_time = None
        self.last_ng_pulse_time = None
        self.pulse_min_interval = 0.5
        
        # Auto-reconnect
        self.plc_reconnect_attempts = 0
        self.db_reconnect_attempts = 0
        
        # Backup data
        self.last_plc_data = {
            "ok_bit": 0,
            "ng_bit": 0,
            "model_number": 1,
            "status_code": 0
        }
        
        # Speed loss check interval
        self.last_speed_check = time.time()
        self.speed_check_interval = 1
        self.last_break_log = time.time()
        
        print("=" * 80)
        print("🚀 YNC Data Collector - FINAL WORKING VERSION")
        print("=" * 80)
        print("📋 FEATURES:")
        print("   • 7 LOSSES: Breakdown, Quality, Material, Setup, Others, Speed, Change Over")
        print("   • DYNAMIC PLAN: 1860 total (465 min × 4 pieces)")
        print("   • REAL-TIME HOURLY UPDATES: Har 5 second me DB update")
        print("   • CORRECT OEE: 0-100% range (fixed overflow)")
        print("   • ✅ ACCURATE BREAK HANDLING: Elapsed - Break Time")
        print("   • ✅ PROPER SHIFT CREATION: Har shift ka alag record (FIXED)")
        print("   • PLAN CAPPED: 1860 se upar nahi jayega")
        print("   • 5 min delay only at shift start")
        print("=" * 80)
    
    def calculate_working_minutes(self):
        """Calculate actual working minutes by subtracting breaks from elapsed time"""
        if not self.current_shift or self.current_shift in ["GAP_AB", "GAP_BA"]:
            return 0
        
        now = datetime.now()
        current_time = now.time()
        current_date = now.date()
        
        # Calculate shift start datetime
        if self.current_shift == "A":
            shift_start = datetime.combine(current_date, dt_time(8, 30))
            start_delay = 5  # minutes
        else:  # B Shift
            if current_time < dt_time(3, 15):
                # After midnight - shift started yesterday
                shift_start = datetime.combine(current_date - timedelta(days=1), dt_time(18, 30))
            else:
                shift_start = datetime.combine(current_date, dt_time(18, 30))
            start_delay = 5  # minutes
        
        # Calculate elapsed minutes since shift start
        elapsed_seconds = (now - shift_start).total_seconds()
        elapsed_minutes = elapsed_seconds / 60.0
        
        # Cap at shift duration (525 minutes = 8h 45m)
        elapsed_minutes = max(0, min(525, elapsed_minutes))
        
        # Subtract start delay (only if elapsed > delay)
        if elapsed_minutes > start_delay:
            elapsed_minutes -= start_delay
        else:
            elapsed_minutes = 0
        
        # Calculate total break minutes that have occurred so far
        break_minutes = 0.0
        
        for break_time in BREAK_TIMES:
            break_start = break_time["start"]
            break_end = break_time["end"]
            
            # Convert break times to datetime on shift start date
            break_start_dt = datetime.combine(shift_start.date(), break_start)
            break_end_dt = datetime.combine(shift_start.date(), break_end)
            
            # Handle breaks that cross midnight
            if break_end < break_start:
                break_end_dt += timedelta(days=1)
            
            # Calculate overlap between break and [shift_start, now]
            overlap_start = max(shift_start, break_start_dt)
            overlap_end = min(now, break_end_dt)
            
            if overlap_end > overlap_start:
                overlap_seconds = (overlap_end - overlap_start).total_seconds()
                break_minutes += overlap_seconds / 60.0
        
        # Working minutes = elapsed minus breaks
        working_minutes = elapsed_minutes - break_minutes
        
        # Ensure non-negative and within shift limits
        working_minutes = max(0, min(465, working_minutes))
        
        # Store for reference
        self.last_working_minutes = working_minutes
        
        # Debug print occasionally
        if int(time.time()) % 60 < 1:
            print(f"[DEBUG-WORK] Time: {current_time}, Elapsed: {elapsed_minutes:.1f}, "
                  f"Breaks: {break_minutes:.1f}, Working: {working_minutes:.1f}")
        
        return int(working_minutes)
    
    def calculate_dynamic_plan(self):
        """Calculate dynamic plan based on working minutes only"""
        if not self.current_shift or self.current_shift in ["GAP_AB", "GAP_BA"]:
            return 1860, self.shift_plan_completed, 1860 - self.shift_plan_completed
        
        # Get working minutes (excluding breaks and delays)
        working_minutes = self.calculate_working_minutes()
        
        # Calculate completed plan based on working minutes
        # Always use 4 pieces per minute (ideal cycle time 15s)
        completed_plan = working_minutes * 4
        
        # ✅ CAP PLAN AT 1860 (SHIFT TOTAL)
        completed_plan = min(1860, completed_plan)
        
        # Ensure plan never decreases
        if completed_plan < self.shift_plan_completed:
            completed_plan = self.shift_plan_completed
        
        remaining_plan = max(0, 1860 - completed_plan)
        
        # Debug print every minute
        current_time = time.time()
        if current_time - self.last_plan_calculation_time > 60:
            print(f"[PLAN-DEBUG] Working: {working_minutes} min, Plan: {completed_plan}, Last: {self.shift_plan_completed}")
            self.last_plan_calculation_time = current_time
        
        return 1860, completed_plan, remaining_plan
    
    def is_break_time(self):
        """Check if current time is in break time"""
        now = datetime.now().time()
        
        for break_time in BREAK_TIMES:
            start = break_time["start"]
            end = break_time["end"]
            
            if end < start:
                if now >= start or now < end:
                    return True, break_time["name"]
            else:
                if start <= now < end:
                    return True, break_time["name"]
        
        return False, None
    
    def get_current_hour_slot(self):
        """Get current hour slot based on time"""
        now = datetime.now()
        current_time = now.time()
        
        # A Shift slots
        a_slots = [
            (dt_time(8, 30), dt_time(9, 30), "08:30-09:30"),
            (dt_time(9, 30), dt_time(10, 30), "09:30-10:30"),
            (dt_time(10, 30), dt_time(11, 30), "10:30-11:30"),
            (dt_time(11, 30), dt_time(13, 5), "11:30-13:05"),
            (dt_time(13, 5), dt_time(14, 5), "13:05-14:05"),
            (dt_time(14, 5), dt_time(15, 5), "14:05-15:05"),
            (dt_time(15, 5), dt_time(16, 5), "15:05-16:05"),
            (dt_time(16, 5), dt_time(17, 15), "16:05-17:15"),
        ]
        
        # B Shift slots
        b_slots = [
            (dt_time(18, 30), dt_time(19, 30), "18:30-19:30"),
            (dt_time(19, 30), dt_time(20, 30), "19:30-20:30"),
            (dt_time(20, 30), dt_time(21, 30), "20:30-21:30"),
            (dt_time(21, 30), dt_time(23, 5), "21:30-23:05"),
            (dt_time(23, 5), dt_time(0, 5), "23:05-00:05"),
            (dt_time(0, 5), dt_time(1, 5), "00:05-01:05"),
            (dt_time(1, 5), dt_time(2, 5), "01:05-02:05"),
            (dt_time(2, 5), dt_time(3, 15), "02:05-03:15"),
        ]
        
        # Gap slots
        gap_slots = [
            (dt_time(17, 15), dt_time(18, 30), "17:15-18:30"),
            (dt_time(3, 15), dt_time(4, 15), "03:15-04:15"),
            (dt_time(4, 15), dt_time(5, 15), "04:15-05:15"),
            (dt_time(5, 15), dt_time(6, 15), "05:15-06:15"),
        ]
        
        all_slots = a_slots + b_slots + gap_slots
        
        for start, end, slot_name in all_slots:
            if end.hour == 0:
                if start <= current_time or current_time < end:
                    return slot_name
            else:
                if start <= current_time < end:
                    return slot_name
        
        return None
    
    def load_shift_data_from_db(self, shift_id):
        """Load ALL shift data from database"""
        if not self.db_connected or not shift_id:
            return False
        
        try:
            cursor = self.db_conn.cursor()
            
            cursor.execute("""
                SELECT 
                    ok_count, ng_count,
                    loss_breakdown_seconds, loss_quality_seconds, 
                    loss_setup_seconds, loss_material_seconds, loss_others_seconds,
                    loss_speed_seconds, loss_change_over_seconds,
                    shift_plan, shift_plan_remaining, shift_plan_completed,
                    hour_0830_0930_ok, hour_0830_0930_ng,
                    hour_0930_1030_ok, hour_0930_1030_ng,
                    hour_1030_1130_ok, hour_1030_1130_ng,
                    hour_1130_1305_ok, hour_1130_1305_ng,
                    hour_1305_1405_ok, hour_1305_1405_ng,
                    hour_1405_1505_ok, hour_1405_1505_ng,
                    hour_1505_1605_ok, hour_1505_1605_ng,
                    hour_1605_1715_ok, hour_1605_1715_ng,
                    hour_1830_1930_ok, hour_1830_1930_ng,
                    hour_1930_2030_ok, hour_1930_2030_ng,
                    hour_2030_2130_ok, hour_2030_2130_ng,
                    hour_2130_2305_ok, hour_2130_2305_ng,
                    hour_2305_0005_ok, hour_2305_0005_ng,
                    hour_0005_0105_ok, hour_0005_0105_ng,
                    hour_0105_0205_ok, hour_0105_0205_ng,
                    hour_0205_0315_ok, hour_0205_0315_ng
                FROM ync_dashboard_complete 
                WHERE id = %s
            """, (shift_id,))
            
            row = cursor.fetchone()
            
            if row:
                self.ok_count_shift = row[0] or 0
                self.ng_count_shift = row[1] or 0
                
                self.loss_seconds["breakdown"] = row[2] or 0
                self.loss_seconds["quality"] = row[3] or 0
                self.loss_seconds["setup"] = row[4] or 0
                self.loss_seconds["material"] = row[5] or 0
                self.loss_seconds["others"] = row[6] or 0
                self.loss_seconds["speed"] = row[7] or 0
                self.loss_seconds["change_over"] = row[8] or 0
                
                self.shift_plan_total = row[9] or 1860
                self.shift_plan_remaining = row[10] or 1860
                self.shift_plan_completed = row[11] or 0
                
                self.cycle_tracker.speed_loss_seconds = self.loss_seconds["speed"]
                
                self.hourly_data = {}
                
                a_shift_hours = [
                    ("08:30-09:30", row[12], row[13]),
                    ("09:30-10:30", row[14], row[15]),
                    ("10:30-11:30", row[16], row[17]),
                    ("11:30-13:05", row[18], row[19]),
                    ("13:05-14:05", row[20], row[21]),
                    ("14:05-15:05", row[22], row[23]),
                    ("15:05-16:05", row[24], row[25]),
                    ("16:05-17:15", row[26], row[27]),
                ]
                
                b_shift_hours = [
                    ("18:30-19:30", row[28], row[29]),
                    ("19:30-20:30", row[30], row[31]),
                    ("20:30-21:30", row[32], row[33]),
                    ("21:30-23:05", row[34], row[35]),
                    ("23:05-00:05", row[36], row[37]),
                    ("00:05-01:05", row[38], row[39]),
                    ("01:05-02:05", row[40], row[41]),
                    ("02:05-03:15", row[42], row[43]),
                ]
                
                all_hours = a_shift_hours + b_shift_hours
                
                for slot_name, ok, ng in all_hours:
                    if slot_name not in self.hourly_data:
                        plan = 0
                        if slot_name in SHIFT_CONFIG["A"]["hourly_plan"]:
                            plan = SHIFT_CONFIG["A"]["hourly_plan"][slot_name]
                        elif slot_name in SHIFT_CONFIG["B"]["hourly_plan"]:
                            plan = SHIFT_CONFIG["B"]["hourly_plan"][slot_name]
                        
                        self.hourly_data[slot_name] = {
                            "ok": ok or 0,
                            "ng": ng or 0,
                            "plan": plan,
                            "type": "A" if slot_name in SHIFT_CONFIG["A"]["hourly_plan"] else 
                                   "B" if slot_name in SHIFT_CONFIG["B"]["hourly_plan"] else "GAP"
                        }
                
                self.current_hour_key = self.get_current_hour_slot()
                
                print(f"[RESTART] 🔄 Loaded shift data: OK={self.ok_count_shift}, NG={self.ng_count_shift}")
                print(f"[RESTART] 📊 Loaded 7 losses: Breakdown={self.loss_seconds['breakdown']}s, "
                      f"Speed={self.loss_seconds['speed']}s, Change Over={self.loss_seconds['change_over']}s")
                print(f"[RESTART] 📈 Plan: Total={self.shift_plan_total}, Completed={self.shift_plan_completed}")
                
                cursor.close()
                return True
            
            cursor.close()
            return False
                
        except Exception as e:
            print(f"[RESTART] ❌ Error loading shift data: {e}")
            return False
    
    def save_current_hour_to_db(self, hour_slot):
        """Save current hour data to database (real-time)"""
        if not self.db_connected or not self.shift_record_id:
            return
        
        try:
            cursor = self.db_conn.cursor()
            
            hourly = self.hourly_data.get(hour_slot, {})
            actual = hourly.get("ok", 0) + hourly.get("ng", 0)
            plan = hourly.get("plan", 0)
            variance = actual - plan
            
            slot_mapping = {
                "08:30-09:30": "hour_0830_0930",
                "09:30-10:30": "hour_0930_1030",
                "10:30-11:30": "hour_1030_1130",
                "11:30-13:05": "hour_1130_1305",
                "13:05-14:05": "hour_1305_1405",
                "14:05-15:05": "hour_1405_1505",
                "15:05-16:05": "hour_1505_1605",
                "16:05-17:15": "hour_1605_1715",
                "17:15-18:30": "hour_1715_1830",
                "18:30-19:30": "hour_1830_1930",
                "19:30-20:30": "hour_1930_2030",
                "20:30-21:30": "hour_2030_2130",
                "21:30-23:05": "hour_2130_2305",
                "23:05-00:05": "hour_2305_0005",
                "00:05-01:05": "hour_0005_0105",
                "01:05-02:05": "hour_0105_0205",
                "02:05-03:15": "hour_0205_0315",
                "03:15-04:15": "hour_0315_0415",
                "04:15-05:15": "hour_0415_0515",
                "05:15-06:15": "hour_0515_0615",
            }
            
            if hour_slot in slot_mapping:
                column_prefix = slot_mapping[hour_slot]
                
                if hour_slot in ["17:15-18:30", "03:15-04:15", "04:15-05:15", "05:15-06:15"]:
                    cursor.execute(f"""
                        UPDATE ync_dashboard_complete SET
                            {column_prefix}_actual = %s,
                            {column_prefix}_ok = %s,
                            {column_prefix}_ng = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (actual, hourly.get("ok", 0), hourly.get("ng", 0), self.shift_record_id))
                else:
                    cursor.execute(f"""
                        UPDATE ync_dashboard_complete SET
                            {column_prefix}_actual = %s,
                            {column_prefix}_variance = %s,
                            {column_prefix}_ok = %s,
                            {column_prefix}_ng = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (actual, variance, hourly.get("ok", 0), hourly.get("ng", 0), self.shift_record_id))
                
                self.db_conn.commit()
                cursor.close()
                
        except Exception as e:
            print(f"[HOURLY-REALTIME] ❌ Error saving {hour_slot}: {e}")
            if self.db_conn:
                try:
                    self.db_conn.rollback()
                except:
                    pass
    
    def update_hourly_counts(self, new_ok, new_ng):
        """Update hourly counts - REAL-TIME update in memory and DB"""
        current_slot = self.get_current_hour_slot()
        
        if not current_slot:
            return
        
        # Initialize if new slot
        if current_slot not in self.hourly_data:
            plan = 0
            slot_type = "GAP"
            
            if current_slot in SHIFT_CONFIG["A"]["hourly_plan"]:
                plan = SHIFT_CONFIG["A"]["hourly_plan"][current_slot]
                slot_type = "A"
            elif current_slot in SHIFT_CONFIG["B"]["hourly_plan"]:
                plan = SHIFT_CONFIG["B"]["hourly_plan"][current_slot]
                slot_type = "B"
            
            self.hourly_data[current_slot] = {
                "ok": 0,
                "ng": 0,
                "plan": plan,
                "type": slot_type
            }
        
        # Update counts for current hour
        self.hourly_data[current_slot]["ok"] += new_ok
        self.hourly_data[current_slot]["ng"] += new_ng
        
        # REAL-TIME DATABASE UPDATE - har 5 second me
        current_time = time.time()
        if current_time - self.last_hourly_db_update > 5:
            self.save_current_hour_to_db(current_slot)
            self.last_hourly_db_update = current_time
        
        # Check if hour changed
        if current_slot != self.current_hour_key:
            if self.current_hour_key:
                # Save previous hour data to database one last time
                print(f"[HOUR-CHANGE] 🔄 Slot change: {self.current_hour_key} → {current_slot}")
                self.save_hourly_data_to_db(self.current_hour_key)
                
                # Log previous hour summary
                prev_data = self.hourly_data.get(self.current_hour_key, {})
                plan = prev_data.get('plan', 0)
                actual = prev_data.get('ok', 0)
                var = actual - plan
                print(f"[HOUR-END] 📊 {self.current_hour_key}: Plan={plan}, OK={actual}, Variance={var}")
            
            self.current_hour_key = current_slot
    
    def save_hourly_data_to_db(self, hour_slot):
        """Save specific hour slot data to database (final save)"""
        if not self.db_connected or not self.shift_record_id:
            return False
        
        try:
            cursor = self.db_conn.cursor()
            
            hourly = self.hourly_data.get(hour_slot, {})
            actual = hourly.get("ok", 0) + hourly.get("ng", 0)
            plan = hourly.get("plan", 0)
            variance = actual - plan
            
            slot_mapping = {
                "08:30-09:30": "hour_0830_0930",
                "09:30-10:30": "hour_0930_1030",
                "10:30-11:30": "hour_1030_1130",
                "11:30-13:05": "hour_1130_1305",
                "13:05-14:05": "hour_1305_1405",
                "14:05-15:05": "hour_1405_1505",
                "15:05-16:05": "hour_1505_1605",
                "16:05-17:15": "hour_1605_1715",
                "17:15-18:30": "hour_1715_1830",
                "18:30-19:30": "hour_1830_1930",
                "19:30-20:30": "hour_1930_2030",
                "20:30-21:30": "hour_2030_2130",
                "21:30-23:05": "hour_2130_2305",
                "23:05-00:05": "hour_2305_0005",
                "00:05-01:05": "hour_0005_0105",
                "01:05-02:05": "hour_0105_0205",
                "02:05-03:15": "hour_0205_0315",
                "03:15-04:15": "hour_0315_0415",
                "04:15-05:15": "hour_0415_0515",
                "05:15-06:15": "hour_0515_0615",
            }
            
            if hour_slot in slot_mapping:
                column_prefix = slot_mapping[hour_slot]
                
                if hour_slot in ["17:15-18:30", "03:15-04:15", "04:15-05:15", "05:15-06:15"]:
                    cursor.execute(f"""
                        UPDATE ync_dashboard_complete SET
                            {column_prefix}_actual = %s,
                            {column_prefix}_ok = %s,
                            {column_prefix}_ng = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (actual, hourly.get("ok", 0), hourly.get("ng", 0), self.shift_record_id))
                else:
                    cursor.execute(f"""
                        UPDATE ync_dashboard_complete SET
                            {column_prefix}_actual = %s,
                            {column_prefix}_variance = %s,
                            {column_prefix}_ok = %s,
                            {column_prefix}_ng = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (actual, variance, hourly.get("ok", 0), hourly.get("ng", 0), self.shift_record_id))
                
                self.db_conn.commit()
                cursor.close()
                print(f"[HOURLY] ✅ Saved {hour_slot}: Plan={plan}, OK={actual}, Variance={variance}")
                return True
            
            cursor.close()
            return False
            
        except Exception as e:
            print(f"[HOURLY-SAVED] ❌ Error saving {hour_slot}: {e}")
            if self.db_conn:
                try:
                    self.db_conn.rollback()
                except:
                    pass
            return False
    
    def safe_plc_connect(self):
        """Safely connect to PLC"""
        try:
            if self.plc:
                try:
                    self.plc.close()
                except:
                    pass
                self.plc = None
            
            print(f"[PLC] 🔄 Connecting to PLC at {PLC_IP}:{PLC_PORT}...")
            self.plc = pymcprotocol.Type4E()
            self.plc.connect(PLC_IP, PLC_PORT)
            
            self.plc.batchread_wordunits(headdevice="D6005", readsize=1)
            
            self.plc_connected = True
            self.plc_reconnect_attempts = 0
            print(f"[PLC] ✅ Connected to PLC at {PLC_IP}:{PLC_PORT}")
            return True
            
        except Exception as e:
            self.plc_connected = False
            self.plc_reconnect_attempts += 1
            print(f"[PLC] ❌ Connection failed ({self.plc_reconnect_attempts}): {e}")
            return False
    
    def connect_database(self):
        """Connect to PostgreSQL"""
        try:
            print(f"[DB] 🔄 Connecting to database at {DB_CONFIG['host']}...")
            self.db_conn = psycopg2.connect(
                host=DB_CONFIG["host"],
                port=DB_CONFIG["port"],
                database=DB_CONFIG["database"],
                user=DB_CONFIG["user"],
                password=DB_CONFIG["password"],
                connect_timeout=3
            )
            
            cursor = self.db_conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            
            self.db_connected = True
            self.db_reconnect_attempts = 0
            print(f"[DB] ✅ Connected to database at {DB_CONFIG['host']}")
            
            self.verify_database_columns()
            
            return True
            
        except Exception as e:
            self.db_connected = False
            self.db_reconnect_attempts += 1
            print(f"[DB] ❌ Connection failed ({self.db_reconnect_attempts}): {e}")
            return False
    
    def verify_database_columns(self):
        """Verify all required columns exist"""
        if not self.db_connected:
            return
        
        try:
            cursor = self.db_conn.cursor()
            
            # Check if table exists
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'ync_dashboard_complete'
                )
            """)
            table_exists = cursor.fetchone()[0]
            
            if not table_exists:
                print("[DB] ❌ Table ync_dashboard_complete does not exist!")
                cursor.close()
                return
            
            # Check for required columns
            required_columns = [
                'shift_plan_remaining',
                'shift_plan_completed',
                'loss_speed_seconds',
                'loss_change_over_seconds',
                'loss_speed',
                'loss_change_over'
            ]
            
            missing_columns = []
            for col in required_columns:
                cursor.execute("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name='ync_dashboard_complete' AND column_name=%s
                """, (col,))
                if not cursor.fetchone():
                    missing_columns.append(col)
            
            if missing_columns:
                print(f"[DB] ⚠️ Missing columns: {', '.join(missing_columns)}")
                print("[DB] 📝 Please run the following SQL commands:")
                for col in missing_columns:
                    if col in ['shift_plan_remaining', 'shift_plan_completed']:
                        default = '1860' if col == 'shift_plan_remaining' else '0'
                        print(f"   ALTER TABLE ync_dashboard_complete ADD COLUMN {col} INTEGER DEFAULT {default};")
                    elif col in ['loss_speed_seconds', 'loss_change_over_seconds']:
                        print(f"   ALTER TABLE ync_dashboard_complete ADD COLUMN {col} INTEGER DEFAULT 0;")
                    else:
                        print(f"   ALTER TABLE ync_dashboard_complete ADD COLUMN {col} VARCHAR(20) DEFAULT '00:00:00';")
            else:
                print("[DB] ✅ All required columns verified")
            
            cursor.close()
            
        except Exception as e:
            print(f"[DB] ❌ Column verification error: {e}")
    
    def read_plc_data_safely(self):
        """Read PLC data"""
        if not self.plc_connected:
            return self.last_plc_data
        
        try:
            plc_data = {
                "ok_bit": 0,
                "ng_bit": 0,
                "model_number": self.current_model,
                "status_code": self.current_status_code
            }
            
            status_values = self.plc.batchread_wordunits(headdevice="D6005", readsize=1)
            if status_values:
                plc_data["status_code"] = int(status_values[0])
            
            ok_bit_values = self.plc.batchread_bitunits(headdevice="L108", readsize=1)
            plc_data["ok_bit"] = int(ok_bit_values[0]) if ok_bit_values else 0
            
            ng_bit_values = self.plc.batchread_bitunits(headdevice="L109", readsize=1)
            plc_data["ng_bit"] = int(ng_bit_values[0]) if ng_bit_values else 0
            
            model_values = self.plc.batchread_wordunits(headdevice="D6048", readsize=1)
            plc_data["model_number"] = int(model_values[0]) if model_values else self.current_model
            
            self.last_plc_data = plc_data
            return plc_data
            
        except Exception as e:
            print(f"[PLC] ⚠️ Read error: {e}")
            self.plc_connected = False
            return self.last_plc_data
    
    def update_counts(self, ok_bit, ng_bit):
        """Update OK and NG counts"""
        new_ok = 0
        new_ng = 0
        current_time = time.time()
        
        if self.last_ok_state == 0 and ok_bit == 1:
            if (self.last_ok_pulse_time is None or 
                current_time - self.last_ok_pulse_time >= self.pulse_min_interval):
                
                self.ok_count_total += 1
                self.ok_count_shift += 1
                new_ok = 1
                self.last_ok_pulse_time = current_time
        
        if self.last_ng_state == 0 and ng_bit == 1:
            if (self.last_ng_pulse_time is None or 
                current_time - self.last_ng_pulse_time >= self.pulse_min_interval):
                
                self.ng_count_total += 1
                self.ng_count_shift += 1
                new_ng = 1
                self.last_ng_pulse_time = current_time
        
        self.last_ok_state = ok_bit
        self.last_ng_state = ng_bit
        
        return new_ok, new_ng
    
    def update_model(self, model_number):
        """Update current model"""
        if model_number != self.current_model and model_number > 0:
            self.current_model = model_number
            self.current_model_name = MODEL_MAPPING.get(model_number, f"Model #{model_number}")
            print(f"[MODEL] 🔄 {self.current_model_name}")
    
    def update_status(self, status_code):
        """Update current status and track loss time"""
        current_time = time.time()
        time_elapsed = current_time - self.last_status_check
        
        # Track time for previous status
        old_status = self.current_status_code
        
        if old_status == 2:
            self.loss_seconds["breakdown"] += time_elapsed
        elif old_status == 3:
            self.loss_seconds["quality"] += time_elapsed
        elif old_status == 4:
            self.loss_seconds["setup"] += time_elapsed
        elif old_status == 5:
            self.loss_seconds["material"] += time_elapsed
        elif old_status == 6:
            self.loss_seconds["others"] += time_elapsed
        elif old_status == 7:
            self.loss_seconds["change_over"] += time_elapsed
        
        # Update current status
        if status_code != self.current_status_code:
            self.current_status_code = status_code
            self.current_status_name = STATUS_MAPPING.get(status_code, f"Status #{status_code}")
            old_name = STATUS_MAPPING.get(old_status, f"#{old_status}")
            
            # Update cycle tracker running state
            is_running = (status_code == 1)
            self.cycle_tracker.set_running_state(is_running)
            
            print(f"[STATUS] 🔄 {old_name} → {self.current_status_name} (+{time_elapsed:.1f}s)")
        
        self.last_status_check = current_time
    
    def check_speed_loss_continuously(self):
        """Check for speed loss continuously"""
        current_time = time.time()
        
        in_break, break_name = self.is_break_time()
        if in_break:
            if current_time - self.last_break_log > 60:
                print(f"[BREAK] ☕ {break_name} - Speed loss paused")
                self.last_break_log = current_time
            return
        
        if self.current_status_code == 1:
            added_loss = self.cycle_tracker.check_speed_loss_continuous(current_time)
            if added_loss > 0:
                self.loss_seconds["speed"] += added_loss
    
    def format_loss_time(self, seconds):
        """Convert seconds to HH:MM:SS format"""
        total_seconds = int(seconds)
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        s = total_seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    
    def get_current_shift(self):
        """Determine current shift"""
        now = datetime.now()
        current_time = now.time()
        current_date = now.date()
        
        # GAP_BA: 03:15 to 08:30
        if dt_time(3, 15) <= current_time < dt_time(8, 30):
            return "GAP_BA", dt_time(3, 15), dt_time(8, 30), current_date
        
        # A Shift: 08:30 to 17:15
        elif dt_time(8, 30) <= current_time < dt_time(17, 15):
            return "A", dt_time(8, 30), dt_time(17, 15), current_date
        
        # GAP_AB: 17:15 to 18:30
        elif dt_time(17, 15) <= current_time < dt_time(18, 30):
            return "GAP_AB", dt_time(17, 15), dt_time(18, 30), current_date
        
        # B Shift: 18:30 to 23:59
        elif dt_time(18, 30) <= current_time:
            return "B", dt_time(18, 30), dt_time(3, 15), current_date
        
        # B Shift after midnight: 00:00 to 03:15
        elif current_time < dt_time(3, 15):
            yesterday = current_date - timedelta(days=1)
            return "B", dt_time(18, 30), dt_time(3, 15), yesterday
        
        return None, None, None, current_date
    
    def get_or_create_shift_record(self, record_date, shift_name, start_time, end_time):
        """Get existing active shift OR create new - FIXED VERSION"""
        if not self.db_connected:
            return None
        
        try:
            cursor = self.db_conn.cursor()
            
            # ===========================================
            # STEP 1: Check if there's an active shift for this EXACT date and shift name
            # ===========================================
            cursor.execute("""
                SELECT id, ok_count, ng_count, is_shift_completed,
                       shift_plan, shift_plan_remaining, shift_plan_completed
                FROM ync_dashboard_complete 
                WHERE record_date = %s AND shift_name = %s AND is_shift_completed = false
                ORDER BY created_at DESC LIMIT 1
            """, (record_date, shift_name))
            
            result = cursor.fetchone()
            
            if result:
                shift_id, existing_ok, existing_ng, is_completed, db_plan_total, db_plan_remaining, db_plan_completed = result
                
                print(f"[SHIFT] 📊 Continuing EXISTING ACTIVE shift: {shift_name} for date {record_date} (ID: {shift_id})")
                
                # Load dynamic plan values from DB
                self.shift_plan_total = db_plan_total or 1860
                self.shift_plan_remaining = db_plan_remaining or 1860
                self.shift_plan_completed = db_plan_completed or 0
                
                if self.load_shift_data_from_db(shift_id):
                    print(f"[SHIFT] ✅ Loaded existing data: OK={self.ok_count_shift}, NG={self.ng_count_shift}")
                
                cursor.close()
                return shift_id
            
            # ===========================================
            # STEP 2: For B shift only - check if there's an active B shift from previous day
            # (B shift runs from 18:30 to 03:15 next day)
            # ===========================================
            if shift_name == "B":
                # Check for active B shift that started yesterday
                yesterday = record_date - timedelta(days=1)
                cursor.execute("""
                    SELECT id, ok_count, ng_count, is_shift_completed,
                           shift_plan, shift_plan_remaining, shift_plan_completed
                    FROM ync_dashboard_complete 
                    WHERE record_date = %s AND shift_name = 'B' AND is_shift_completed = false
                    ORDER BY created_at DESC LIMIT 1
                """, (yesterday,))
                
                result = cursor.fetchone()
                
                if result:
                    shift_id, existing_ok, existing_ng, is_completed, db_plan_total, db_plan_remaining, db_plan_completed = result
                    
                    print(f"[SHIFT] 📊 Continuing EXISTING B shift from previous day (ID: {shift_id})")
                    
                    # Load dynamic plan values from DB
                    self.shift_plan_total = db_plan_total or 1860
                    self.shift_plan_remaining = db_plan_remaining or 1860
                    self.shift_plan_completed = db_plan_completed or 0
                    
                    if self.load_shift_data_from_db(shift_id):
                        print(f"[SHIFT] ✅ Loaded existing data: OK={self.ok_count_shift}, NG={self.ng_count_shift}")
                    
                    cursor.close()
                    return shift_id
            
            # ===========================================
            # STEP 3: Check if record exists for this date and shift (even if completed)
            # ===========================================
            cursor.execute("""
                SELECT id FROM ync_dashboard_complete 
                WHERE record_date = %s AND shift_name = %s
            """, (record_date, shift_name))
            
            existing = cursor.fetchone()
            
            if existing:
                shift_id = existing[0]
                print(f"[SHIFT] 📝 Found existing record for {record_date} - {shift_name}, resetting to zero")
                
                # Reset this shift to zero
                is_gap = shift_name in ["GAP_AB", "GAP_BA"]
                shift_plan = 0 if is_gap else 1860
                
                cursor.execute("""
                    UPDATE ync_dashboard_complete SET
                        ok_count = 0, ng_count = 0,
                        shift_plan = %s,
                        shift_plan_remaining = %s,
                        shift_plan_completed = 0,
                        cycle_time_plan = 15.00,
                        operating_status = %s,
                        is_shift_completed = false,
                        timestamp = %s,
                        period_type = %s,
                        is_gap_time = %s,
                        loss_breakdown_seconds = 0,
                        loss_quality_seconds = 0,
                        loss_setup_seconds = 0,
                        loss_material_seconds = 0,
                        loss_others_seconds = 0,
                        loss_speed_seconds = 0,
                        loss_change_over_seconds = 0,
                        loss_breakdown = '00:00:00',
                        loss_quality = '00:00:00',
                        loss_setup = '00:00:00',
                        loss_material = '00:00:00',
                        loss_others = '00:00:00',
                        loss_speed = '00:00:00',
                        loss_change_over = '00:00:00',
                        total_loss = '00:00:00'
                    WHERE id = %s
                """, (
                    shift_plan,
                    shift_plan,
                    self.current_status_name,
                    datetime.now(),
                    "SHIFT" if not is_gap else "GAP",
                    is_gap,
                    shift_id
                ))
                
                self.db_conn.commit()
                cursor.close()
                
                # Reset local counters
                self.ok_count_shift = 0
                self.ng_count_shift = 0
                self.loss_seconds = {
                    "breakdown": 0, "quality": 0, "setup": 0, 
                    "material": 0, "others": 0, "speed": 0, "change_over": 0
                }
                self.cycle_tracker.reset_speed_loss()
                self.hourly_data = {}
                self.shift_plan_total = shift_plan
                self.shift_plan_remaining = shift_plan
                self.shift_plan_completed = 0
                
                print(f"[SHIFT] ✅ Reset existing shift: {record_date} - {shift_name}")
                return shift_id
            
            # ===========================================
            # STEP 4: Create NEW shift record
            # ===========================================
            is_gap = shift_name in ["GAP_AB", "GAP_BA"]
            shift_plan = 0 if is_gap else 1860
            
            print(f"[SHIFT] 🆕 Creating NEW shift: {shift_name} with plan {shift_plan}")
            
            # Reset counters for NEW shift
            self.ok_count_shift = 0
            self.ng_count_shift = 0
            self.loss_seconds = {
                "breakdown": 0, 
                "quality": 0, 
                "setup": 0, 
                "material": 0, 
                "others": 0,
                "speed": 0,
                "change_over": 0
            }
            self.cycle_tracker.reset_speed_loss()
            self.hourly_data = {}
            self.shift_plan_total = shift_plan
            self.shift_plan_remaining = shift_plan
            self.shift_plan_completed = 0
            
            cursor.execute("""
                INSERT INTO ync_dashboard_complete 
                (record_date, shift_name, shift_start_time, shift_end_time,
                 line_name, current_model_number, current_model_name, 
                 ok_count, ng_count, shift_plan, shift_plan_remaining, shift_plan_completed,
                 cycle_time_plan, operating_status, is_shift_completed, timestamp,
                 period_type, is_gap_time, loss_speed_seconds, loss_change_over_seconds)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                record_date,
                shift_name,
                start_time,
                end_time,
                "YNC-SEAT SLIDER",
                self.current_model,
                self.current_model_name,
                0,
                0,
                shift_plan,
                shift_plan,
                0,
                15.00,
                self.current_status_name,
                False,
                datetime.now(),
                "SHIFT" if not is_gap else "GAP",
                is_gap,
                0,
                0
            ))
            
            shift_id = cursor.fetchone()[0]
            self.db_conn.commit()
            cursor.close()
            
            print(f"[SHIFT] ✅ Created NEW shift: {record_date} - {shift_name} (Plan: {shift_plan})")
            return shift_id
                
        except Exception as e:
            print(f"[SHIFT] ❌ Database error: {e}")
            traceback.print_exc()
            if self.db_conn:
                try:
                    self.db_conn.rollback()
                except:
                    pass
            return None
    
    def calculate_oee(self):
        """Calculate OEE metrics correctly (0-100% range)"""
        if not self.shift_start_time or self.current_shift in ["GAP_AB", "GAP_BA"]:
            return {
                "availability": 0.00,
                "performance": 0.00,
                "quality_oee": 100.00,
                "overall_oee": 0.00,
                "oee_grade": "GAP",
                "avg_cycle_time": 0
            }
        
        shift_elapsed = time.time() - self.shift_start_time
        shift_elapsed = max(1, shift_elapsed)  # Avoid division by zero
        
        # Total loss seconds from all 7 losses
        total_loss_seconds = sum(self.loss_seconds.values())
        
        # Running time = shift elapsed minus total losses
        running_time = shift_elapsed - total_loss_seconds
        running_time = max(1, running_time)  # Avoid division by zero
        
        # Availability = Running Time / Total Time (as percentage 0-100)
        availability = (running_time / shift_elapsed * 100)
        availability = min(100, max(0, availability))
        
        # Total pieces produced
        total_pieces = self.ok_count_shift + self.ng_count_shift
        
        # Performance = (Total Pieces × Ideal Cycle Time) / Running Time (as percentage 0-100)
        # Ideal cycle = 15 seconds
        if total_pieces > 0:
            ideal_time_for_pieces = total_pieces * 15  # in seconds
            performance = (ideal_time_for_pieces / running_time * 100)
            # Cap at 100% (can't exceed ideal)
            performance = min(100, max(0, performance))
        else:
            performance = 0
        
        # Quality = OK Count / Total Count (as percentage 0-100)
        quality = (self.ok_count_shift / total_pieces * 100) if total_pieces > 0 else 100
        quality = min(100, max(0, quality))
        
        # Overall OEE = (Availability × Performance × Quality) / 10000
        # This gives a percentage between 0-100
        overall_oee = (availability * performance * quality) / 10000
        overall_oee = min(100, max(0, overall_oee))
        
        # Grade based on OEE
        if overall_oee >= 85:
            grade = "EXCELLENT"
        elif overall_oee >= 75:
            grade = "GOOD"
        elif overall_oee >= 65:
            grade = "AVERAGE"
        elif overall_oee >= 55:
            grade = "FAIR"
        else:
            grade = "POOR"
        
        return {
            "availability": round(availability, 2),
            "performance": round(performance, 2),
            "quality_oee": round(quality, 2),
            "overall_oee": round(overall_oee, 2),
            "oee_grade": grade,
            "avg_cycle_time": self.cycle_tracker.get_current_data()["avg_20"]
        }

    def update_dashboard_table(self):
        """Update main dashboard table"""
        if not self.db_connected or not self.shift_record_id:
            return False
        
        try:
            oee_data = self.calculate_oee()
            ct_data = self.cycle_tracker.get_ct_dict()
            
            # Sync cycle tracker speed loss with loss_seconds
            self.loss_seconds["speed"] = self.cycle_tracker.speed_loss_seconds
            
            # Calculate dynamic plan based on working minutes
            total_plan, completed_plan, remaining_plan = self.calculate_dynamic_plan()
            
            # ✅ CAP PLAN AT 1860 (SHIFT TOTAL)
            completed_plan = min(1860, completed_plan)
            
            self.shift_plan_total = total_plan
            self.shift_plan_completed = completed_plan
            self.shift_plan_remaining = max(0, 1860 - completed_plan)
            
            cursor = self.db_conn.cursor()
            
            update_query = """
                UPDATE ync_dashboard_complete SET
                    ok_count = %s, ng_count = %s,
                    current_model_number = %s, current_model_name = %s,
                    cycle_time_actual = %s,
                    operating_status = %s,
                    availability = %s, performance = %s,
                    quality_oee = %s, overall_oee = %s, oee_grade = %s,
                    
                    -- Dynamic plan values
                    shift_plan = %s,
                    shift_plan_remaining = %s,
                    shift_plan_completed = %s,
                    
                    -- 7 LOSSES
                    loss_breakdown_seconds = %s, loss_quality_seconds = %s,
                    loss_setup_seconds = %s, loss_material_seconds = %s,
                    loss_others_seconds = %s, loss_speed_seconds = %s,
                    loss_change_over_seconds = %s,
                    loss_breakdown = %s, loss_quality = %s,
                    loss_setup = %s, loss_material = %s,
                    loss_others = %s, loss_speed = %s,
                    loss_change_over = %s, total_loss = %s,
                    
                    ct1 = %s, ct2 = %s, ct3 = %s, ct4 = %s,
                    ct5 = %s, ct6 = %s, ct7 = %s, ct8 = %s,
                    ct9 = %s, ct10 = %s, ct11 = %s, ct12 = %s,
                    ct13 = %s, ct14 = %s, ct15 = %s, ct16 = %s,
                    ct17 = %s, ct18 = %s, ct19 = %s, ct20 = %s,
                    ct_avg_20 = %s, min_ct = %s, max_ct = %s, std_dev_ct = %s,
                    
                    updated_at = CURRENT_TIMESTAMP, timestamp = %s
                WHERE id = %s
            """
            
            total_loss_seconds = sum(self.loss_seconds.values())
            
            # Ensure all numeric values are within DECIMAL(5,2) range (0-999.99)
            cycle_time = min(99.99, oee_data["avg_cycle_time"])
            availability = min(99.99, oee_data["availability"])
            performance = min(99.99, oee_data["performance"])
            quality = min(99.99, oee_data["quality_oee"])
            overall_oee = min(99.99, oee_data["overall_oee"])
            
            cursor.execute(update_query, (
                self.ok_count_shift,
                self.ng_count_shift,
                self.current_model,
                self.current_model_name,
                cycle_time,
                self.current_status_name,
                availability,
                performance,
                quality,
                overall_oee,
                oee_data["oee_grade"],
                
                # Dynamic plan values
                total_plan,
                self.shift_plan_remaining,
                completed_plan,
                
                # 7 LOSSES
                int(self.loss_seconds["breakdown"]),
                int(self.loss_seconds["quality"]),
                int(self.loss_seconds["setup"]),
                int(self.loss_seconds["material"]),
                int(self.loss_seconds["others"]),
                int(self.loss_seconds["speed"]),
                int(self.loss_seconds["change_over"]),
                self.format_loss_time(self.loss_seconds["breakdown"]),
                self.format_loss_time(self.loss_seconds["quality"]),
                self.format_loss_time(self.loss_seconds["setup"]),
                self.format_loss_time(self.loss_seconds["material"]),
                self.format_loss_time(self.loss_seconds["others"]),
                self.format_loss_time(self.loss_seconds["speed"]),
                self.format_loss_time(self.loss_seconds["change_over"]),
                self.format_loss_time(total_loss_seconds),
                ct_data.get("ct1"), ct_data.get("ct2"), ct_data.get("ct3"), ct_data.get("ct4"),
                ct_data.get("ct5"), ct_data.get("ct6"), ct_data.get("ct7"), ct_data.get("ct8"),
                ct_data.get("ct9"), ct_data.get("ct10"), ct_data.get("ct11"), ct_data.get("ct12"),
                ct_data.get("ct13"), ct_data.get("ct14"), ct_data.get("ct15"), ct_data.get("ct16"),
                ct_data.get("ct17"), ct_data.get("ct18"), ct_data.get("ct19"), ct_data.get("ct20"),
                ct_data.get("ct_avg_20"), ct_data.get("min_ct"), ct_data.get("max_ct"), ct_data.get("std_dev_ct"),
                datetime.now(),
                self.shift_record_id
            ))
            
            self.db_conn.commit()
            cursor.close()
            
            # Log status every 30 seconds
            if int(time.time()) % 30 < 2:
                working_minutes = self.calculate_working_minutes()
                print(f"[STATUS] 📊 Shift: {self.current_shift} | "
                      f"Working: {working_minutes} min | "
                      f"OK: {self.ok_count_shift} | "
                      f"Plan: {completed_plan}/{total_plan} ({completed_plan/total_plan*100:.1f}%) | "
                      f"OEE: {overall_oee:.1f}% | "
                      f"Losses: {self.format_loss_time(total_loss_seconds)}")
            
            return True
            
        except Exception as e:
            print(f"[DB] ❌ Update error: {e}")
            traceback.print_exc()
            if self.db_conn:
                try:
                    self.db_conn.rollback()
                except:
                    pass
            return False

    def reset_shift_to_zero(self, shift_id):
        """Reset specific shift to ZERO values"""
        try:
            cursor = self.db_conn.cursor()
            
            cursor.execute("""
                UPDATE ync_dashboard_complete SET
                    ok_count = 0, ng_count = 0,
                    cycle_time_actual = 0.00,
                    availability = 0.00,
                    performance = 0.00,
                    quality_oee = 0.00,
                    overall_oee = 0.00,
                    oee_grade = 'GAP',
                    shift_plan_remaining = shift_plan,
                    shift_plan_completed = 0,
                    
                    -- Reset all hourly data
                    hour_0830_0930_actual = 0, hour_0830_0930_variance = 0, hour_0830_0930_ok = 0, hour_0830_0930_ng = 0,
                    hour_0930_1030_actual = 0, hour_0930_1030_variance = 0, hour_0930_1030_ok = 0, hour_0930_1030_ng = 0,
                    hour_1030_1130_actual = 0, hour_1030_1130_variance = 0, hour_1030_1130_ok = 0, hour_1030_1130_ng = 0,
                    hour_1130_1305_actual = 0, hour_1130_1305_variance = 0, hour_1130_1305_ok = 0, hour_1130_1305_ng = 0,
                    hour_1305_1405_actual = 0, hour_1305_1405_variance = 0, hour_1305_1405_ok = 0, hour_1305_1405_ng = 0,
                    hour_1405_1505_actual = 0, hour_1405_1505_variance = 0, hour_1405_1505_ok = 0, hour_1405_1505_ng = 0,
                    hour_1505_1605_actual = 0, hour_1505_1605_variance = 0, hour_1505_1605_ok = 0, hour_1505_1605_ng = 0,
                    hour_1605_1715_actual = 0, hour_1605_1715_variance = 0, hour_1605_1715_ok = 0, hour_1605_1715_ng = 0,
                    hour_1830_1930_actual = 0, hour_1830_1930_variance = 0, hour_1830_1930_ok = 0, hour_1830_1930_ng = 0,
                    hour_1930_2030_actual = 0, hour_1930_2030_variance = 0, hour_1930_2030_ok = 0, hour_1930_2030_ng = 0,
                    hour_2030_2130_actual = 0, hour_2030_2130_variance = 0, hour_2030_2130_ok = 0, hour_2030_2130_ng = 0,
                    hour_2130_2305_actual = 0, hour_2130_2305_variance = 0, hour_2130_2305_ok = 0, hour_2130_2305_ng = 0,
                    hour_2305_0005_actual = 0, hour_2305_0005_variance = 0, hour_2305_0005_ok = 0, hour_2305_0005_ng = 0,
                    hour_0005_0105_actual = 0, hour_0005_0105_variance = 0, hour_0005_0105_ok = 0, hour_0005_0105_ng = 0,
                    hour_0105_0205_actual = 0, hour_0105_0205_variance = 0, hour_0105_0205_ok = 0, hour_0105_0205_ng = 0,
                    hour_0205_0315_actual = 0, hour_0205_0315_variance = 0, hour_0205_0315_ok = 0, hour_0205_0315_ng = 0,
                    hour_1715_1830_actual = 0, hour_1715_1830_ok = 0, hour_1715_1830_ng = 0,
                    hour_0315_0415_actual = 0, hour_0315_0415_ok = 0, hour_0315_0415_ng = 0,
                    hour_0415_0515_actual = 0, hour_0415_0515_ok = 0, hour_0415_0515_ng = 0,
                    hour_0515_0615_actual = 0, hour_0515_0615_ok = 0, hour_0515_0615_ng = 0,
                    
                    -- Reset 7 LOSSES
                    loss_breakdown_seconds = 0,
                    loss_quality_seconds = 0,
                    loss_material_seconds = 0,
                    loss_setup_seconds = 0,
                    loss_others_seconds = 0,
                    loss_speed_seconds = 0,
                    loss_change_over_seconds = 0,
                    loss_breakdown = '00:00:00',
                    loss_quality = '00:00:00',
                    loss_material = '00:00:00',
                    loss_setup = '00:00:00',
                    loss_others = '00:00:00',
                    loss_speed = '00:00:00',
                    loss_change_over = '00:00:00',
                    total_loss = '00:00:00',
                    
                    -- Reset cycle time data
                    ct1 = NULL, ct2 = NULL, ct3 = NULL, ct4 = NULL,
                    ct5 = NULL, ct6 = NULL, ct7 = NULL, ct8 = NULL,
                    ct9 = NULL, ct10 = NULL, ct11 = NULL, ct12 = NULL,
                    ct13 = NULL, ct14 = NULL, ct15 = NULL, ct16 = NULL,
                    ct17 = NULL, ct18 = NULL, ct19 = NULL, ct20 = NULL,
                    ct_avg_20 = NULL,
                    min_ct = NULL,
                    max_ct = NULL,
                    std_dev_ct = NULL,
                    
                    timestamp = NOW(),
                    updated_at = NOW()
                WHERE id = %s
            """, (shift_id,))
            
            self.db_conn.commit()
            cursor.close()
            
            print(f"[RESET] ✅ Shift ID {shift_id} reset to ZERO")
            return True
            
        except Exception as e:
            print(f"[RESET] ❌ Error resetting shift: {e}")
            if self.db_conn:
                self.db_conn.rollback()
            return False

    def run(self):
        """Main execution loop"""
        print("\n🔄 Starting YNC Data Collector - FINAL VERSION...")
        print("📌 Press Ctrl+C to stop manually\n")
        
        self.safe_plc_connect()
        self.connect_database()
        
        last_display = time.time()
        last_db_update = time.time()
        last_plc_status = time.time()
        db_update_interval = 2
        last_debug_time = time.time()
        
        while True:
            try:
                current_time = time.time()
                
                # Auto-reconnect
                if not self.plc_connected and current_time % 30 < 1:
                    self.safe_plc_connect()
                
                if not self.db_connected and current_time % 45 < 1:
                    self.connect_database()
                
                # Get current shift
                shift_name, start_time, end_time, record_date = self.get_current_shift()
                
                if shift_name:
                    if shift_name != self.current_shift:
                        print(f"\n{'='*50}")
                        print(f"[SHIFT] 🔄 PERIOD CHANGE: {self.current_shift or 'None'} → {shift_name}")
                        print(f"{'='*50}\n")
                        
                        # ✅ Mark previous shift as completed
                        if self.current_shift and self.shift_record_id and self.db_connected:
                            try:
                                cursor = self.db_conn.cursor()
                                cursor.execute("""
                                    UPDATE ync_dashboard_complete 
                                    SET is_shift_completed = true 
                                    WHERE id = %s
                                """, (self.shift_record_id,))
                                self.db_conn.commit()
                                cursor.close()
                                print(f"[SHIFT] ✅ Previous shift {self.current_shift} (ID: {self.shift_record_id}) marked as COMPLETED")
                            except Exception as e:
                                print(f"[SHIFT] ⚠️ Error marking previous shift completed: {e}")
                        
                        self.current_shift = shift_name
                        self.shift_start_time = time.time()
                        
                        self.last_ok_pulse_time = None
                        self.last_ng_pulse_time = None
                        self.last_ok_state = 0
                        self.last_ng_state = 0
                        
                        if self.db_connected:
                            self.shift_record_id = self.get_or_create_shift_record(
                                record_date, shift_name, start_time, end_time
                            )
                        
                        print(f"[SHIFT] 📊 Started {shift_name} with plan 1860")
                        print(f"[SHIFT] 📈 Current counts: OK={self.ok_count_shift}, NG={self.ng_count_shift}")
                    
                    # Read PLC data
                    plc_data = self.read_plc_data_safely()
                    
                    # Update model
                    self.update_model(plc_data["model_number"])
                    
                    # Update status
                    self.update_status(plc_data["status_code"])
                    
                    # Update counts
                    new_ok, new_ng = self.update_counts(
                        plc_data["ok_bit"], 
                        plc_data["ng_bit"]
                    )
                    
                    # Update cycle time
                    is_running = (self.current_status_code == 1)
                    if new_ok > 0:
                        self.cycle_tracker.calculate_cycle_time(time.time(), is_running)
                    
                    # Check for continuous speed loss
                    if current_time - self.last_speed_check >= self.speed_check_interval:
                        self.check_speed_loss_continuously()
                        self.last_speed_check = current_time
                    
                    # Check and log break times
                    in_break, break_name = self.is_break_time()
                    if in_break and current_time - self.last_break_log > 60:
                        print(f"[BREAK] ☕ {break_name} - Machine stopped intentionally")
                        self.last_break_log = current_time
                    
                    # Update hourly counts (REAL-TIME)
                    self.update_hourly_counts(new_ok, new_ng)
                    
                    # Update database
                    if current_time - last_db_update > db_update_interval:
                        if self.db_connected and self.shift_record_id:
                            if self.update_dashboard_table():
                                last_db_update = current_time
                    
                    # Display status
                    if current_time - last_display > 1:
                        ct_stats = self.cycle_tracker.get_current_data()
                        
                        plc_status = "✅" if self.plc_connected else "❌"
                        db_status = "✅" if self.db_connected else "❌"
                        
                        timestamp = datetime.now().strftime("%H:%M:%S")
                        
                        in_break, break_name = self.is_break_time()
                        break_indicator = f"☕ {break_name[:10]}" if in_break else "     "
                        
                        working_minutes = self.calculate_working_minutes()
                        expected_plan = min(1860, working_minutes * 4)
                        oee_data = self.calculate_oee()
                        total_loss_seconds = sum(self.loss_seconds.values())
                        
                        print(f"[{timestamp}] "
                              f"Shift: {self.current_shift:6s} | "
                              f"{break_indicator:15s} | "
                              f"Model: {self.current_model_name[:15]:15s} | "
                              f"Status: {self.current_status_name[:12]:12s} | "
                              f"OK: {self.ok_count_shift:4d} | "
                              f"Plan: {expected_plan:4d} | "
                              f"OEE: {oee_data['overall_oee']:5.1f}% | "
                              f"Loss: {self.format_loss_time(total_loss_seconds):8s} | "
                              f"Cycle: {ct_stats['avg_20']:5.2f}s")
                        
                        last_display = current_time
                    
                    # DEBUG: Har 10 second me break status check
                    if current_time - last_debug_time > 10:
                        working = self.calculate_working_minutes()
                        print(f"[DEBUG-BREAK] Time: {datetime.now().strftime('%H:%M:%S')}, "
                              f"Working: {working} min, Last: {self.last_working_minutes}")
                        last_debug_time = current_time
                    
                    if current_time - last_plc_status > 30 and not self.plc_connected:
                        print(f"[PLC] ⚠️ Still disconnected ({self.plc_reconnect_attempts} attempts)")
                        last_plc_status = current_time
                
                else:
                    if self.current_shift:
                        print(f"[SHIFT] ⏸️  Period {self.current_shift} ended")
                        self.current_shift = None
                        self.shift_record_id = None
                    
                    time.sleep(5)
                    continue
                
                time.sleep(0.1)
                
            except KeyboardInterrupt:
                print("\n\n🛑 STOPPED BY USER")
                break
                
            except Exception as e:
                print(f"\n[ERROR] ⚠️ Unhandled exception: {type(e).__name__}")
                print(f"[ERROR] Message: {str(e)}")
                print("[SYSTEM] 🔄 Continuing after error...")
                time.sleep(2)
        
        print("\n🔄 Cleaning up...")
        if self.plc:
            try:
                self.plc.close()
                print("[PLC] ✅ Connection closed")
            except:
                pass
        
        if self.db_conn:
            try:
                self.db_conn.close()
                print("[DB] ✅ Connection closed")
            except:
                pass
        
        print("👋 Collector stopped")

# ========== MAIN EXECUTION ==========
if __name__ == "__main__":
    print("\n" + "="*80)
    print("🔧 YNC Data Collector - FINAL WORKING VERSION")
    print("="*80)
    
    try:
        import pymcprotocol
        print("✅ pymcprotocol module loaded")
    except ImportError:
        print("❌ pymcprotocol not installed. Run: pip install pymcprotocol")
        sys.exit(1)
    
    try:
        import psycopg2
        print("✅ psycopg2 module loaded")
    except ImportError:
        print("❌ psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)
    
    print("\n📝 CONFIGURATION:")
    print(f"   • PLC IP: {PLC_IP}:{PLC_PORT}")
    print(f"   • Database: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
    print(f"   • SINGLE TABLE: ync_dashboard_complete")
    print(f"   • 7 LOSSES: Breakdown, Quality, Material, Setup, Others, Speed, Change Over")
    print(f"   • SHIFT PLAN: 1860 pieces (465 min × 4 pieces)")
    print(f"   • REAL-TIME HOURLY UPDATES: Har 5 second me DB me save")
    print(f"   • CORRECT OEE: 0-100% range (fixed overflow)")
    print(f"   • ✅ ACCURATE BREAK HANDLING: Elapsed - Break Time")
    print(f"   • ✅ PROPER SHIFT CREATION: Har shift ka alag record")
    print(f"   • PLAN CAPPED: 1860 se upar nahi jayega")
    print(f"   • 5 min delay only at shift start")
    print("\n🚀 Starting collector in 3 seconds...")
    time.sleep(3)
    
    collector = YNCCompleteCollector()
    collector.run()