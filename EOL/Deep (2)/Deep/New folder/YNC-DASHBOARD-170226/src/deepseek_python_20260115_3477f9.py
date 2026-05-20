"""
MITSUBISHI FX5 PLC SERVER FOR VUE.JS DASHBOARD
Customized for Single Line Dashboard Components
"""

# ... (previous imports remain same)

# ========== DASHBOARD MAPPING CONFIG ==========
# Mapping your Vue.js components to PLC registers

# 1. LINE DETAILS COMPONENT MAPPING
LINE_DETAILS_MAPPING = {
    "line_name": "YNC-SEAT SLIDER",  # Hardcoded or from PLC
    "model": "D6049",  # Model number register
    "plan": "D6000",   # Daily plan
    "actual": "D6001", # Actual production
    "achievement": "D6002", # Achievement percentage
    "cycle_time": "D6003"  # Cycle time
}

# 2. OEE CALCULATION MAPPING
OEE_MAPPING = {
    "availability": "D6020",
    "performance": "D6021",
    "quality": "D6022",
    "overall": "D6023",
    "grade": "D6024"  # Can map numbers to grades
}

# 3. LOSS PARAMETERS MAPPING
LOSS_PARAMETERS_MAPPING = {
    "breakdown": "D6030",  # Breakdown time in seconds
    "quality": "D6031",    # Quality loss time
    "material": "D6032",   # Material loss time
    "setup": "D6033",      # Setup loss time
    "others": "D6034",     # Other losses time
    "total": "D6035"       # Total loss time
}

# 4. OPERATING STATUS MAPPING (L bits)
OPERATING_STATUS_MAPPING = {
    "RUNNING": "L100",
    "BREAKDOWN": "L101",
    "QUALITY_ISSUE": "L102",
    "MATERIAL_WAIT": "L103",
    "SETUP_CHANGE": "L104"
}

# 5. HOURLY PLAN vs ACTUAL MAPPING
HOURLY_MAPPING = {
    # A Shift hourly data (8 hours)
    "A_Shift": {
        "plan": ["D6010", "D6011", "D6012", "D6013", "D6014", "D6015", "D6016", "D6017"],
        "actual": ["D6005", "D6006", "D6007", "D6008", "D6009", "D6010", "D6011", "D6012"]
    },
    # B Shift hourly data
    "B_Shift": {
        "plan": ["D6019", "D6020", "D6021", "D6022", "D6023", "D6024", "D6025", "D6026"],
        "actual": ["D6014", "D6015", "D6016", "D6017", "D6018", "D6019", "D6020", "D6021"]
    }
}

print(f"📊 Vue.js Dashboard Mappings Configured")
print(f"   • Line Details: {len(LINE_DETAILS_MAPPING)} parameters")
print(f"   • OEE: {len(OEE_MAPPING)} parameters")
print(f"   • Loss Parameters: {len(LOSS_PARAMETERS_MAPPING)} parameters")
print(f"   • Operating Status: {len(OPERATING_STATUS_MAPPING)} bits")
print(f"   • Hourly Data: 8+8 registers per shift")

# ========== MODIFIED PLC CONTROLLER ==========
class FX5_PLC_Controller:
    def __init__(self):
        # ... (previous initialization remains same)
        
        # DASHBOARD DATA
        self.dashboard_data = {
            "line_details": {},
            "oee_data": {},
            "loss_parameters": {},
            "operating_status": "RUNNING",
            "hourly_data": {},
            "model_info": {},
            "timeline_segments": []
        }
        
        log_message("Vue.js Dashboard PLC Controller initialized", "INFO")
    
    # ========== DASHBOARD SPECIFIC METHODS ==========
    
    def get_dashboard_data(self):
        """Get complete dashboard data for Vue.js"""
        try:
            # Get operating status from L bits
            self.update_operating_status()
            
            # Get line details
            self.update_line_details()
            
            # Get OEE data
            self.update_oee_data()
            
            # Get loss parameters
            self.update_loss_parameters()
            
            # Get hourly data (current shift)
            self.update_hourly_data()
            
            # Get model info
            self.update_model_info()
            
            # Format the data for Vue.js
            dashboard_data = self.format_for_vue()
            
            return dashboard_data
            
        except Exception as e:
            log_message(f"Dashboard data error: {e}", "ERROR")
            return self.dashboard_data
    
    def update_operating_status(self):
        """Update operating status from L bits"""
        bits_data = self.read_bits()
        
        if not bits_data:
            return
        
        # Determine status based on L bits
        if bits_data.get("L100", 0) == 1:
            status = "RUNNING"
        elif bits_data.get("L101", 0) == 1:
            status = "BREAKDOWN"
        elif bits_data.get("L102", 0) == 1:
            status = "QUALITY_ISSUE"
        elif bits_data.get("L103", 0) == 1:
            status = "MATERIAL_WAIT"
        elif bits_data.get("L104", 0) == 1:
            status = "SETUP_CHANGE"
        else:
            status = "IDLE"
        
        self.dashboard_data["operating_status"] = status
        return status
    
    def update_line_details(self):
        """Update line details from PLC registers"""
        try:
            line_data = {}
            
            # Get model
            model_data = self.read_current_model()
            if model_data:
                line_data["model"] = model_data["name"]
            else:
                line_data["model"] = "updating"
            
            # Get plan (D6000)
            plan = self.read_single_data_register("D6000") or 1820
            line_data["plan"] = plan
            
            # Get actual (D6001)
            actual = self.read_single_data_register("D6001") or 0
            line_data["actual"] = actual
            
            # Calculate achievement
            if plan > 0:
                achievement = (actual / plan) * 100
                line_data["achievement"] = f"{achievement:.1f}%"
            else:
                line_data["achievement"] = "0%"
            
            # Get cycle time (D6003)
            cycle_time = self.read_single_data_register("D6003") or 156  # 15.6 seconds
            actual_cycle = cycle_time / 10  # Assuming stored as 156 for 15.6
            target_cycle = 15.6
            line_data["cycle_time"] = f"{target_cycle}s / {actual_cycle}s"
            
            # Line name (hardcoded)
            line_data["name"] = "YNC-SEAT SLIDER"
            
            self.dashboard_data["line_details"] = line_data
            return line_data
            
        except Exception as e:
            log_message(f"Line details error: {e}", "ERROR")
            return self.dashboard_data["line_details"]
    
    def update_oee_data(self):
        """Update OEE data from PLC"""
        try:
            oee_data = {}
            
            # Read OEE values
            availability = self.read_single_data_register("D6020") or 925  # 92.5%
            performance = self.read_single_data_register("D6021") or 882   # 88.2%
            quality = self.read_single_data_register("D6022") or 953      # 95.3%
            
            # Convert to percentages
            oee_data["availability"] = availability / 10
            oee_data["performance"] = performance / 10
            oee_data["quality"] = quality / 10
            
            # Calculate overall OEE
            oee_data["overall"] = (availability * performance * quality) / 1000000
            
            # Determine grade
            overall = oee_data["overall"]
            if overall >= 90:
                grade = "EXCELLENT"
            elif overall >= 80:
                grade = "GOOD"
            elif overall >= 70:
                grade = "AVERAGE"
            elif overall >= 60:
                grade = "FAIR"
            else:
                grade = "POOR"
            oee_data["grade"] = grade
            
            self.dashboard_data["oee_data"] = oee_data
            return oee_data
            
        except Exception as e:
            log_message(f"OEE data error: {e}", "ERROR")
            return self.dashboard_data["oee_data"]
    
    def update_loss_parameters(self):
        """Update loss parameters from PLC"""
        try:
            loss_data = {}
            
            # Read loss times in seconds
            breakdown = self.read_single_data_register("D6030") or 0
            quality_loss = self.read_single_data_register("D6031") or 0
            material = self.read_single_data_register("D6032") or 0
            setup = self.read_single_data_register("D6033") or 0
            others = self.read_single_data_register("D6034") or 0
            total = self.read_single_data_register("D6035") or 0
            
            # Convert seconds to HH:MM:SS
            def format_time(seconds):
                h = seconds // 3600
                m = (seconds % 3600) // 60
                s = seconds % 60
                return f"{h:02d}:{m:02d}:{s:02d}"
            
            loss_data["breakdown"] = format_time(breakdown)
            loss_data["quality"] = format_time(quality_loss)
            loss_data["material"] = format_time(material)
            loss_data["setup"] = format_time(setup)
            loss_data["others"] = format_time(others)
            loss_data["total"] = format_time(total)
            
            self.dashboard_data["loss_parameters"] = loss_data
            return loss_data
            
        except Exception as e:
            log_message(f"Loss parameters error: {e}", "ERROR")
            return self.dashboard_data["loss_parameters"]
    
    def update_hourly_data(self):
        """Update hourly plan vs actual data"""
        try:
            hourly_data = {
                "hours": [
                    '08:30-09:30',
                    '09:30-10:30',
                    '10:30-11:30',
                    '11:30-13:05',
                    '13:05-14:05',
                    '14:05-15:05',
                    '15:05-16:05',
                    '16:05-17:15'
                ],
                "plan": [],
                "actual": [],
                "variances": [],
                "totalPlan": 0,
                "totalActual": 0,
                "totalVariance": 0
            }
            
            # Get current shift
            current_shift = self.get_current_shift()
            
            # Read hourly data based on shift
            if current_shift == "A":
                registers = ["D6010", "D6011", "D6012", "D6013", 
                           "D6014", "D6015", "D6016", "D6017"]
            else:
                registers = ["D6019", "D6020", "D6021", "D6022",
                           "D6023", "D6024", "D6025", "D6026"]
            
            # Read plan values
            plan_values = []
            for reg in registers:
                value = self.read_single_data_register(reg) or 0
                plan_values.append(value)
            
            # Read actual values (assuming stored in next 8 registers)
            actual_values = []
            for i in range(len(registers)):
                actual_reg = f"D{6005 + i}"  # Adjust as needed
                value = self.read_single_data_register(actual_reg) or 0
                actual_values.append(value)
            
            # Calculate variances
            variances = []
            for p, a in zip(plan_values, actual_values):
                variances.append(a - p)
            
            # Calculate totals
            total_plan = sum(plan_values)
            total_actual = sum(actual_values)
            total_variance = total_actual - total_plan
            
            hourly_data["plan"] = plan_values
            hourly_data["actual"] = actual_values
            hourly_data["variances"] = variances
            hourly_data["totalPlan"] = total_plan
            hourly_data["totalActual"] = total_actual
            hourly_data["totalVariance"] = total_variance
            
            self.dashboard_data["hourly_data"] = hourly_data
            return hourly_data
            
        except Exception as e:
            log_message(f"Hourly data error: {e}", "ERROR")
            return self.dashboard_data["hourly_data"]
    
    def update_model_info(self):
        """Update model information"""
        model_data = self.read_current_model()
        if model_data:
            self.dashboard_data["model_info"] = model_data
        return model_data
    
    def get_current_shift(self):
        """Determine current shift based on time"""
        current_hour = datetime.now().hour
        if 8 <= current_hour < 17:
            return "A"
        else:
            return "B"
    
    def format_for_vue(self):
        """Format data exactly as Vue.js App expects"""
        return {
            "success": True,
            "connected": self.connected,
            "dashboard": {
                "operating_status": self.dashboard_data["operating_status"],
                "line_data": self.dashboard_data["line_details"],
                "oee_data": self.dashboard_data["oee_data"],
                "loss_times": self.dashboard_data["loss_parameters"],
                "hourly_data": self.dashboard_data["hourly_data"],
                "model_info": self.dashboard_data["model_info"]
            },
            "timestamp": datetime.now().isoformat()
        }
    
    # ========== NEW API ENDPOINTS FOR VUE.JS ==========
    
    def get_vue_dashboard_data(self):
        """Special method for Vue.js dashboard"""
        return self.get_dashboard_data()
    
    def update_status_from_vue(self, new_status):
        """Update operating status from Vue.js"""
        try:
            # Map Vue.js status to L bit
            status_map = {
                "RUNNING": ("L100", 1),
                "BREAKDOWN": ("L101", 1),
                "QUALITY_ISSUE": ("L102", 1),
                "MATERIAL_WAIT": ("L103", 1),
                "SETUP_CHANGE": ("L104", 1),
                "IDLE": ("L100", 0)  # Turn off all bits
            }
            
            if new_status in status_map:
                address, value = status_map[new_status]
                
                # First, turn off all status bits
                for bit in ["L100", "L101", "L102", "L103", "L104"]:
                    if bit != address:
                        self.write_bit(bit, 0)
                
                # Then set the new status
                success = self.write_bit(address, value)
                
                if success:
                    self.dashboard_data["operating_status"] = new_status
                    log_message(f"Status updated from Vue.js: {new_status}", "SUCCESS")
                    return True
            
            return False
            
        except Exception as e:
            log_message(f"Status update error: {e}", "ERROR")
            return False
    
    def write_actual_production(self, value):
        """Write actual production count to PLC"""
        return self.write_data_register("D6001", value)
    
    def add_timeline_segment(self, segment_type):
        """Add a timeline segment (for Vue.js)"""
        segment = {
            "id": int(time.time() * 1000),
            "type": segment_type,
            "start_time": datetime.now().isoformat(),
            "isActive": True,
            "shift": self.get_current_shift()
        }
        self.dashboard_data["timeline_segments"].append(segment)
        return segment
    
    def end_timeline_segment(self, segment_id):
        """End a timeline segment"""
        for segment in self.dashboard_data["timeline_segments"]:
            if segment["id"] == segment_id and segment["isActive"]:
                segment["end_time"] = datetime.now().isoformat()
                segment["isActive"] = False
                return segment
        return None

# ========== UPDATE GLOBAL INSTANCE ==========
plc = FX5_PLC_Controller()
app = Flask(__name__)
CORS(app)

# ========== NEW VUE.JS SPECIFIC API ROUTES ==========
@app.route('/api/vue/dashboard')
def get_vue_dashboard():
    """Get complete dashboard data for Vue.js"""
    dashboard_data = plc.get_vue_dashboard_data()
    
    if dashboard_data:
        return jsonify(dashboard_data)
    else:
        return jsonify({
            "success": False,
            "error": "Failed to get dashboard data",
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route('/api/vue/status/<string:status>', methods=['POST'])
def update_operating_status(status):
    """Update operating status from Vue.js"""
    valid_statuses = ["RUNNING", "BREAKDOWN", "QUALITY_ISSUE", 
                     "MATERIAL_WAIT", "SETUP_CHANGE", "IDLE"]
    
    if status.upper() not in valid_statuses:
        return jsonify({
            "success": False,
            "error": f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
        }), 400
    
    success = plc.update_status_from_vue(status.upper())
    
    if success:
        return jsonify({
            "success": True,
            "message": f"Status updated to {status}",
            "new_status": status.upper(),
            "timestamp": datetime.now().isoformat()
        })
    else:
        return jsonify({
            "success": False,
            "error": "Failed to update status",
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route('/api/vue/actual/<int:count>', methods=['POST'])
def update_actual_production(count):
    """Update actual production count"""
    if count < 0:
        return jsonify({
            "success": False,
            "error": "Count must be positive"
        }), 400
    
    success = plc.write_actual_production(count)
    
    if success:
        return jsonify({
            "success": True,
            "message": f"Actual production updated to {count}",
            "new_count": count,
            "timestamp": datetime.now().isoformat()
        })
    else:
        return jsonify({
            "success": False,
            "error": "Failed to update production count",
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route('/api/vue/timeline/start/<string:segment_type>', methods=['POST'])
def start_timeline_segment(segment_type):
    """Start a timeline segment"""
    segment = plc.add_timeline_segment(segment_type)
    
    return jsonify({
        "success": True,
        "message": f"Started {segment_type} segment",
        "segment": segment,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/api/vue/timeline/end/<int:segment_id>', methods=['POST'])
def end_timeline_segment(segment_id):
    """End a timeline segment"""
    segment = plc.end_timeline_segment(segment_id)
    
    if segment:
        return jsonify({
            "success": True,
            "message": "Segment ended",
            "segment": segment,
            "timestamp": datetime.now().isoformat()
        })
    else:
        return jsonify({
            "success": False,
            "error": "Segment not found or already ended",
            "timestamp": datetime.now().isoformat()
        }), 404

# ========== UPDATE HOME ENDPOINT ==========
@app.route('/')
def home():
    return jsonify({
        "server": "Vue.js PLC Dashboard Server",
        "version": "4.0",
        "status": "online",
        "vue_integration": True,
        "components_supported": [
            "DashboardHeader",
            "LineDetails", 
            "OEECalculation",
            "LossParameters",
            "ShiftTimeline",
            "HourlyPlan"
        ],
        "vue_endpoints": {
            "/api/vue/dashboard": "Complete Vue.js dashboard data",
            "/api/vue/status/<status>": "Update operating status",
            "/api/vue/actual/<count>": "Update actual production",
            "/api/vue/timeline/start/<type>": "Start timeline segment",
            "/api/vue/timeline/end/<id>": "End timeline segment"
        },
        "original_endpoints": {
            "/api/data": "All data registers",
            "/api/model": "Current model",
            "/api/actual/A": "A Shift data",
            "/api/actual/B": "B Shift data",
            "/api/status": "PLC status"
        },
        "timestamp": datetime.now().isoformat()
    })

# ... (rest of the background services remain same)

def vue_dashboard_updater():
    """Background thread to update Vue.js dashboard data"""
    log_message("Vue.js dashboard updater started", "INFO")
    
    update_count = 0
    
    while True:
        try:
            if plc.connected:
                # Update dashboard data
                dashboard_data = plc.get_dashboard_data()
                
                update_count += 1
                if update_count % 10 == 0:
                    log_message(f"Vue.js dashboard updated #{update_count}", "INFO")
            
            time.sleep(2)  # Update every 2 seconds
            
        except Exception as e:
            log_message(f"Vue.js updater error: {e}", "ERROR")
            time.sleep(5)

# ========== UPDATE STARTUP ==========
def start_server():
    """Start the PLC server"""
    print("\n" + "="*80)
    print(f"🚀 VUE.JS PLC DASHBOARD SERVER v4.0")
    print(f"📡 PLC: {PLC_IP}:{PLC_PORT}")
    print(f"🌐 Vue.js Dashboard Integration: ENABLED")
    print(f"📊 Data Mapping: Configured for App.vue components")
    print(f"   • Line Details: D6000-D6003")
    print(f"   • OEE: D6020-D6024")
    print(f"   • Loss Parameters: D6030-D6035")
    print(f"   • Status Bits: L100-L104")
    print(f"   • Hourly Data: D6010-D6017 (A), D6019-D6026 (B)")
    print(f"🔄 Vue.js Updates: Every 2 seconds")
    print("="*80)
    
    setup_logging()
    
    services = [
        threading.Thread(target=background_logger, daemon=True),
        threading.Thread(target=data_register_logger, daemon=True),
        threading.Thread(target=time_based_updater, daemon=True),
        threading.Thread(target=model_monitor, daemon=True),
        threading.Thread(target=auto_reconnect, daemon=True),
        threading.Thread(target=vue_dashboard_updater, daemon=True)  # New
    ]
    
    for service in services:
        service.start()
    
    log_message("All background services started including Vue.js updater", "SUCCESS")
    
    # Initial connection
    if plc.connect():
        log_message("Initial connection successful!", "SUCCESS")
        # Initialize dashboard data
        plc.get_dashboard_data()
    
    log_message(f"Starting web server on port {SERVER_PORT}", "INFO")
    print("\n" + "="*80)
    print("🌐 VUE.JS DASHBOARD SERVER READY:")
    print(f"   • Vue.js Dashboard: http://localhost:{SERVER_PORT}/api/vue/dashboard")
    print(f"   • Update Status: POST http://localhost:{SERVER_PORT}/api/vue/status/RUNNING")
    print(f"   • Update Production: POST http://localhost:{SERVER_PORT}/api/vue/actual/100")
    print(f"   • Model Info: http://localhost:{SERVER_PORT}/api/model")
    print(f"   • PLC Status: http://localhost:{SERVER_PORT}/api/status")
    print("="*80)
    print("💡 Vue.js App should call: /api/vue/dashboard every 2-5 seconds")
    print("💡 Press CTRL+C to stop")
    print("="*80)
    
    try:
        app.run(
            host='0.0.0.0',
            port=SERVER_PORT,
            debug=False,
            threaded=True
        )
    except KeyboardInterrupt:
        log_message("Server stopped by user", "INFO")
        plc.close()
        print(f"\n📝 Log file saved: {LOG_FILE}")
        print("👋 Vue.js Dashboard Server stopped")

# ========== MAIN ==========
if __name__ == '__main__':
    start_server()