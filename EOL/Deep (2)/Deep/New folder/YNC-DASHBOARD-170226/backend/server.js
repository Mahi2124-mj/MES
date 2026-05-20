const express = require('express');
const cors = require('cors');
const { Pool } = require('pg');

const app = express();
const PORT = 3500;

// Middleware
app.use(cors());
app.use(express.json());

// PostgreSQL connection
const pool = new Pool({
  host: '192.168.10.210',
  port: 5432,
  database: 'energydb',
  user: 'postgres',
  password: 'tbdi@123',
  max: 10,
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 5000,
});

// Helper function to get current shift based on time
function getCurrentShift() {
  const now = new Date();
  const hour = now.getHours();
  const minute = now.getMinutes();
  const currentMinutes = hour * 60 + minute;
  
  console.log(`⏰ Current time: ${hour.toString().padStart(2, '0')}:${minute.toString().padStart(2, '0')} (${currentMinutes} minutes)`);
  
  // A Shift: 08:30 to 17:15
  if ((8*60+30) <= currentMinutes && currentMinutes < (17*60+15)) {
    return {
      shiftName: 'A',
      recordDate: now.toISOString().split('T')[0], // TODAY'S DATE
      isGap: false,
      isShiftActive: true,
      displayTime: '08:30 - 17:15'
    };
  }
  
  // B Shift: 18:30 to 03:15 (next day)
  if ((18*60+30) <= currentMinutes || currentMinutes < (3*60+15)) {
    let recordDate;
    let displayTime;
    
    // If it's between 00:00-03:15, it's previous day's B shift
    if (currentMinutes < (3*60+15)) {
      const yesterday = new Date(now);
      yesterday.setDate(yesterday.getDate() - 1);
      recordDate = yesterday.toISOString().split('T')[0];
      displayTime = '18:30 - 03:15 (Previous Day)';
    } else {
      recordDate = now.toISOString().split('T')[0];
      displayTime = '18:30 - 03:15';
    }
    
    return {
      shiftName: 'B',
      recordDate: recordDate,
      isGap: false,
      isShiftActive: true,
      displayTime: displayTime
    };
  }
  
  // GAP_AB: 17:15 to 18:30
  if ((17*60+15) <= currentMinutes && currentMinutes < (18*60+30)) {
    return {
      shiftName: 'GAP_AB',
      recordDate: now.toISOString().split('T')[0],
      isGap: true,
      isShiftActive: false,
      displayTime: '17:15 - 18:30'
    };
  }
  
  // GAP_BA: 03:15 to 08:30
  if ((3*60+15) <= currentMinutes && currentMinutes < (8*60+30)) {
    return {
      shiftName: 'GAP_BA',
      recordDate: now.toISOString().split('T')[0],
      isGap: true,
      isShiftActive: false,
      displayTime: '03:15 - 08:30'
    };
  }
  
  // Default (should not reach here)
  return {
    shiftName: 'A',
    recordDate: now.toISOString().split('T')[0],
    isGap: false,
    isShiftActive: false,
    displayTime: 'Unknown'
  };
}

// Helper function to prepare response data
function prepareResponseData(row, currentShift) {
  // Extract date properly
  let rowRecordDate;
  if (row.record_date instanceof Date) {
    rowRecordDate = row.record_date.toISOString().split('T')[0];
  } else if (typeof row.record_date === 'string') {
    // Remove time part if exists
    rowRecordDate = row.record_date.split('T')[0].split(' ')[0];
  } else {
    rowRecordDate = currentShift.recordDate;
  }
  
  const isToday = rowRecordDate === currentShift.recordDate;
  const isCurrentShift = row.shift_name === currentShift.shiftName;
  
  console.log(`📅 Date check: Row date=${rowRecordDate}, Today=${currentShift.recordDate}, IsToday=${isToday}, IsCurrentShift=${isCurrentShift}`);
  
  // Prepare the response object
  return {
    // Basic info
    id: row.id,
    timestamp: row.timestamp,
    record_date: rowRecordDate,
    shift_name: row.shift_name || currentShift.shiftName,
    line_name: row.line_name || 'YNC-SEAT SLIDER',
    
    // Model info
    current_model_number: row.current_model_number || 9,
    current_model_name: row.current_model_name || 'YHB/YNC/YCA 4WAY OTR',
    
    // Production counts
    ok_count: row.ok_count || 0,
    ng_count: row.ng_count || 0,
    shift_plan: row.shift_plan || 1820,
    
    // Cycle time
    cycle_time_plan: row.cycle_time_plan || '15.60',
    cycle_time_actual: row.cycle_time_actual || '0.00',
    
    // OEE metrics
    availability: row.availability || 0,
    performance: row.performance || 0,
    quality_oe: row.quality_oee || row.quality_oe || 0,
    overall_oe: row.overall_oee || row.overall_oe || 0,
    oee_grade: row.oee_grade || 'POOR',
    
    // Status
    operating_status: row.operating_status || 'RUNNING',
    period_type: row.period_type || 'SHIFT',
    is_gap_time: currentShift.isGap,
    is_shift_completed: row.is_shift_completed || false,
    
    // Loss parameters (in seconds)
    loss_breakdown_seconds: row.loss_breakdown_seconds || 0,
    loss_quality_seconds: row.loss_quality_seconds || 0,
    loss_material_seconds: row.loss_material_seconds || 0,
    loss_setup_seconds: row.loss_setup_seconds || 0,
    loss_others_seconds: row.loss_others_seconds || 0,
    
    // Loss parameters (HH:MM:SS format)
    loss_breakdown: row.loss_breakdown || '00:00:00',
    loss_quality: row.loss_quality || '00:00:00',
    loss_material: row.loss_material || '00:00:00',
    loss_setup: row.loss_setup || '00:00:00',
    loss_others: row.loss_others || '00:00:00',
    total_loss: row.total_loss || '00:00:00',
    
    // ===== HOURLY DATA FOR A SHIFT =====
    hour_0830_0930_plan: row.hour_0830_0930_plan || 0,
    hour_0830_0930_actual: row.hour_0830_0930_actual || 0,
    hour_0830_0930_ok: row.hour_0830_0930_ok || 0,
    hour_0830_0930_ng: row.hour_0830_0930_ng || 0,
    
    hour_0930_1030_plan: row.hour_0930_1030_plan || 0,
    hour_0930_1030_actual: row.hour_0930_1030_actual || 0,
    hour_0930_1030_ok: row.hour_0930_1030_ok || 0,
    hour_0930_1030_ng: row.hour_0930_1030_ng || 0,
    
    hour_1030_1130_plan: row.hour_1030_1130_plan || 0,
    hour_1030_1130_actual: row.hour_1030_1130_actual || 0,
    hour_1030_1130_ok: row.hour_1030_1130_ok || 0,
    hour_1030_1130_ng: row.hour_1030_1130_ng || 0,
    
    hour_1130_1305_plan: row.hour_1130_1305_plan || 0,
    hour_1130_1305_actual: row.hour_1130_1305_actual || 0,
    hour_1130_1305_ok: row.hour_1130_1305_ok || 0,
    hour_1130_1305_ng: row.hour_1130_1305_ng || 0,
    
    hour_1305_1405_plan: row.hour_1305_1405_plan || 0,
    hour_1305_1405_actual: row.hour_1305_1405_actual || 0,
    hour_1305_1405_ok: row.hour_1305_1405_ok || 0,
    hour_1305_1405_ng: row.hour_1305_1405_ng || 0,
    
    hour_1405_1505_plan: row.hour_1405_1505_plan || 0,
    hour_1405_1505_actual: row.hour_1405_1505_actual || 0,
    hour_1405_1505_ok: row.hour_1405_1505_ok || 0,
    hour_1405_1505_ng: row.hour_1405_1505_ng || 0,
    
    hour_1505_1605_plan: row.hour_1505_1605_plan || 0,
    hour_1505_1605_actual: row.hour_1505_1605_actual || 0,
    hour_1505_1605_ok: row.hour_1505_1605_ok || 0,
    hour_1505_1605_ng: row.hour_1505_1605_ng || 0,
    
    hour_1605_1715_plan: row.hour_1605_1715_plan || 0,
    hour_1605_1715_actual: row.hour_1605_1715_actual || 0,
    hour_1605_1715_ok: row.hour_1605_1715_ok || 0,
    hour_1605_1715_ng: row.hour_1605_1715_ng || 0,
    
    // ===== HOURLY DATA FOR B SHIFT =====
    hour_1830_1930_plan: row.hour_1830_1930_plan || 0,
    hour_1830_1930_actual: row.hour_1830_1930_actual || 0,
    hour_1830_1930_ok: row.hour_1830_1930_ok || 0,
    hour_1830_1930_ng: row.hour_1830_1930_ng || 0,
    
    hour_1930_2030_plan: row.hour_1930_2030_plan || 0,
    hour_1930_2030_actual: row.hour_1930_2030_actual || 0,
    hour_1930_2030_ok: row.hour_1930_2030_ok || 0,
    hour_1930_2030_ng: row.hour_1930_2030_ng || 0,
    
    hour_2030_2130_plan: row.hour_2030_2130_plan || 0,
    hour_2030_2130_actual: row.hour_2030_2130_actual || 0,
    hour_2030_2130_ok: row.hour_2030_2130_ok || 0,
    hour_2030_2130_ng: row.hour_2030_2130_ng || 0,
    
    hour_2130_2305_plan: row.hour_2130_2305_plan || 0,
    hour_2130_2305_actual: row.hour_2130_2305_actual || 0,
    hour_2130_2305_ok: row.hour_2130_2305_ok || 0,
    hour_2130_2305_ng: row.hour_2130_2305_ng || 0,
    
    hour_2305_0005_plan: row.hour_2305_0005_plan || 0,
    hour_2305_0005_actual: row.hour_2305_0005_actual || 0,
    hour_2305_0005_ok: row.hour_2305_0005_ok || 0,
    hour_2305_0005_ng: row.hour_2305_0005_ng || 0,
    
    hour_0005_0105_plan: row.hour_0005_0105_plan || 0,
    hour_0005_0105_actual: row.hour_0005_0105_actual || 0,
    hour_0005_0105_ok: row.hour_0005_0105_ok || 0,
    hour_0005_0105_ng: row.hour_0005_0105_ng || 0,
    
    hour_0105_0205_plan: row.hour_0105_0205_plan || 0,
    hour_0105_0205_actual: row.hour_0105_0205_actual || 0,
    hour_0105_0205_ok: row.hour_0105_0205_ok || 0,
    hour_0105_0205_ng: row.hour_0105_0205_ng || 0,
    
    hour_0205_0315_plan: row.hour_0205_0315_plan || 0,
    hour_0205_0315_actual: row.hour_0205_0315_actual || 0,
    hour_0205_0315_ok: row.hour_0205_0315_ok || 0,
    hour_0205_0315_ng: row.hour_0205_0315_ng || 0,
    
    // Additional info
    _debug_info: {
      row_date: row.record_date,
      row_shift: row.shift_name,
      is_today: isToday,
      is_current_shift: isCurrentShift
    }
  };
}

// Helper function to create default data
function createDefaultData(currentShift) {
  const now = new Date();
  const isGapTime = currentShift.isGap || currentShift.shiftName.includes('GAP');
  
  return {
    line_name: 'YNC-SEAT SLIDER',
    shift_name: currentShift.shiftName,
    current_model_name: 'YHB/YNC/YCA 4WAY OTR',
    ok_count: 0,
    ng_count: 0,
    shift_plan: isGapTime ? 0 : 1820,
    availability: 0,
    performance: 0,
    quality_oe: 0,
    overall_oe: 0,
    oee_grade: isGapTime ? 'GAP' : 'NO_DATA',
    cycle_time_plan: '15.60',
    cycle_time_actual: '0.00',
    operating_status: 'IDLE',
    loss_breakdown: '00:00:00',
    loss_quality: '00:00:00',
    loss_material: '00:00:00',
    loss_setup: '00:00:00',
    loss_others: '00:00:00',
    total_loss: '00:00:00',
    record_date: currentShift.recordDate,
    timestamp: now.toISOString(),
    is_gap_time: isGapTime
  };
}

// ========== API ENDPOINTS ==========

// 1. ROOT - Test endpoint
app.get('/', (req, res) => {
  const currentShift = getCurrentShift();
  
  res.json({
    status: 'running',
    message: 'Dashboard Backend API',
    current_shift: currentShift.shiftName,
    current_date: currentShift.recordDate,
    current_time: new Date().toLocaleTimeString(),
    endpoints: {
      latest: '/api/dashboard/latest',
      cycle_time: '/api/cycle-time/latest',
      health: '/api/health',
      debug: '/api/debug/data',
      hourly: '/api/hourly/:shift'
    }
  });
});

// 2. LATEST DASHBOARD DATA (Main endpoint for Vue)
app.get('/api/dashboard/latest', async (req, res) => {
  try {
    console.log('\n📊 ========== FETCHING LATEST DASHBOARD DATA ==========');
    
    const currentShift = getCurrentShift();
    console.log('🔍 Looking for:', {
      date: currentShift.recordDate,
      shift: currentShift.shiftName,
      isGap: currentShift.isGap,
      displayTime: currentShift.displayTime
    });
    
    let result;
    let queryType = 'specific';
    
    // If it's gap time OR shift name contains GAP, get latest data for today
    if (currentShift.isGap || currentShift.shiftName.includes('GAP')) {
      queryType = 'gap_time';
      console.log(`🕒 Gap time detected: ${currentShift.shiftName}`);
      
      result = await pool.query(`
        SELECT * FROM ync_dashboard_complete 
        WHERE record_date = $1
        ORDER BY timestamp DESC 
        LIMIT 1
      `, [currentShift.recordDate]);
      
    } else {
      // For regular shifts (A or B), get specific shift data
      queryType = 'regular_shift';
      console.log(`👷 Regular shift: ${currentShift.shiftName}`);
      
      result = await pool.query(`
        SELECT * FROM ync_dashboard_complete 
        WHERE record_date = $1 
          AND shift_name = $2
        ORDER BY timestamp DESC 
        LIMIT 1
      `, [currentShift.recordDate, currentShift.shiftName]);
    }
    
    console.log(`🔍 Query type: ${queryType}, Found rows: ${result.rows.length}`);
    
    if (result.rows.length > 0) {
      const row = result.rows[0];
      console.log('✅ Data found:', {
        id: row.id,
        record_date: row.record_date,
        shift_name: row.shift_name,
        timestamp: row.timestamp,
        model: row.current_model_name,
        ok: row.ok_count,
        ng: row.ng_count
      });
      
      const responseData = prepareResponseData(row, currentShift);
      const isTodayData = responseData.record_date === currentShift.recordDate;
      const isCurrentShiftData = row.shift_name === currentShift.shiftName;
      
      console.log('📋 Response prepared:', {
        isTodayData: isTodayData,
        isCurrentShiftData: isCurrentShiftData,
        responseDate: responseData.record_date,
        responseShift: responseData.shift_name
      });
      
      res.json({
        success: true,
        connected: true,
        data_source: 'database',
        current_shift: currentShift.shiftName,
        record_date: currentShift.recordDate,
        is_today_data: isTodayData,
        is_current_active: isCurrentShiftData,
        query_type: queryType,
        found_data: {
          id: row.id,
          record_date: row.record_date,
          shift_name: row.shift_name,
          timestamp: row.timestamp
        },
        data: responseData
      });
      
    } else {
      // No data found for today's shift
      console.log(`❌ No data found for ${currentShift.recordDate} shift ${currentShift.shiftName}`);
      
      // Try to get ANY data from today as fallback
      const fallbackResult = await pool.query(`
        SELECT * FROM ync_dashboard_complete 
        WHERE record_date = $1
        ORDER BY timestamp DESC 
        LIMIT 1
      `, [currentShift.recordDate]);
      
      if (fallbackResult.rows.length > 0) {
        const row = fallbackResult.rows[0];
        console.log(`⚠️ Using fallback data: shift ${row.shift_name}`);
        
        const responseData = prepareResponseData(row, currentShift);
        
        res.json({
          success: true,
          connected: true,
          data_source: 'fallback_database',
          current_shift: currentShift.shiftName,
          record_date: currentShift.recordDate,
          is_today_data: true,
          is_current_active: false,
          message: `No ${currentShift.shiftName} shift data found. Using ${row.shift_name} shift data from today.`,
          fallback_shift: row.shift_name,
          data: responseData
        });
        
      } else {
        // No data at all for today
        console.log('❌ No data at all for today. Using default data.');
        res.json({
          success: true,
          connected: true,
          data_source: 'default',
          message: 'No data found for today',
          current_shift: currentShift.shiftName,
          record_date: currentShift.recordDate,
          is_today_data: false,
          data: createDefaultData(currentShift)
        });
      }
    }
    
  } catch (error) {
    console.error('❌ Database error:', error.message);
    const currentShift = getCurrentShift();
    
    res.json({
      success: false,
      connected: false,
      error: error.message,
      current_shift: currentShift.shiftName,
      data: createDefaultData(currentShift)
    });
  }
});

// 3. CYCLE TIME DATA FROM ync_cycle_time_tracking
app.get('/api/cycle-time/latest', async (req, res) => {
  try {
    console.log('\n⏱️ ========== FETCHING CYCLE TIME DATA ==========');
    
    const currentShift = getCurrentShift();
    console.log('Current shift for cycle time:', currentShift.shiftName);
    
    // Get latest cycle time data
    const result = await pool.query(`
      SELECT * FROM ync_cycle_time_tracking 
      WHERE record_date = $1 
        AND shift_name = $2
        AND is_active = true
      ORDER BY timestamp DESC 
      LIMIT 1
    `, [currentShift.recordDate, currentShift.shiftName]);
    
    if (result.rows.length > 0) {
      const row = result.rows[0];
      
      // Get all 20 cycle times
      const cycleTimes = [];
      for (let i = 1; i <= 20; i++) {
        cycleTimes.push(row[`ct${i}`] || 0);
      }
      
      const responseData = {
        id: row.id,
        timestamp: row.timestamp,
        record_date: row.record_date,
        shift_name: row.shift_name,
        model_number: row.model_number,
        model_name: row.model_name,
        cycle_times: cycleTimes,
        average_cycle_time: row.ct_avg_20,
        min_cycle_time: row.min_ct,
        max_cycle_time: row.max_ct,
        std_deviation: row.std_dev_ct,
        is_active: row.is_active,
        updated_at: row.updated_at
      };
      
      console.log(`✅ Cycle time data found: ${row.model_name}, avg: ${row.ct_avg_20}`);
      
      res.json({
        success: true,
        connected: true,
        data_source: 'database',
        current_shift: currentShift.shiftName,
        data: responseData
      });
      
    } else {
      // Try to get any recent cycle time data
      const fallbackResult = await pool.query(`
        SELECT * FROM ync_cycle_time_tracking 
        WHERE is_active = true
        ORDER BY timestamp DESC 
        LIMIT 1
      `);
      
      if (fallbackResult.rows.length > 0) {
        const row = fallbackResult.rows[0];
        
        const cycleTimes = [];
        for (let i = 1; i <= 20; i++) {
          cycleTimes.push(row[`ct${i}`] || 0);
        }
        
        const responseData = {
          id: row.id,
          timestamp: row.timestamp,
          record_date: row.record_date,
          shift_name: row.shift_name,
          model_number: row.model_number,
          model_name: row.model_name,
          cycle_times: cycleTimes,
          average_cycle_time: row.ct_avg_20,
          min_cycle_time: row.min_ct,
          max_cycle_time: row.max_ct,
          std_deviation: row.std_dev_ct,
          is_active: row.is_active,
          updated_at: row.updated_at
        };
        
        console.log(`⚠️ Using fallback cycle time data: ${row.model_name}`);
        
        res.json({
          success: true,
          connected: true,
          data_source: 'fallback_database',
          current_shift: currentShift.shiftName,
          message: 'Using latest available cycle time data',
          data: responseData
        });
        
      } else {
        console.log('❌ No cycle time data found');
        res.json({
          success: true,
          connected: true,
          data_source: 'default',
          message: 'No cycle time data found',
          current_shift: currentShift.shiftName,
          data: {
            model_name: 'No Data',
            average_cycle_time: 0,
            min_cycle_time: 0,
            max_cycle_time: 0,
            cycle_times: Array(20).fill(0),
            is_active: false
          }
        });
      }
    }
    
  } catch (error) {
    console.error('❌ Cycle time database error:', error.message);
    
    res.json({
      success: false,
      connected: false,
      error: error.message,
      data: {
        model_name: 'Error',
        average_cycle_time: 0,
        min_cycle_time: 0,
        max_cycle_time: 0,
        cycle_times: Array(20).fill(0),
        is_active: false
      }
    });
  }
});

// 4. DEBUG ENDPOINT - Check today's data
app.get('/api/debug/data', async (req, res) => {
  try {
    const currentShift = getCurrentShift();
    
    // Get all data from today
    const todayData = await pool.query(`
      SELECT id, timestamp, record_date, shift_name, 
             current_model_name, ok_count, ng_count,
             hour_0830_0930_actual, hour_0930_1030_actual
      FROM ync_dashboard_complete 
      WHERE record_date = $1
      ORDER BY timestamp DESC
    `, [currentShift.recordDate]);
    
    // Get A shift data specifically
    const aShiftData = await pool.query(`
      SELECT id, timestamp, record_date, shift_name 
      FROM ync_dashboard_complete 
      WHERE record_date = $1 AND shift_name = 'A'
      ORDER BY timestamp DESC 
      LIMIT 1
    `, [currentShift.recordDate]);
    
    // Get B shift data specifically
    const bShiftData = await pool.query(`
      SELECT id, timestamp, record_date, shift_name 
      FROM ync_dashboard_complete 
      WHERE record_date = $1 AND shift_name = 'B'
      ORDER BY timestamp DESC 
      LIMIT 1
    `, [currentShift.recordDate]);
    
    res.json({
      debug_info: {
        current_time: new Date().toISOString(),
        current_shift: currentShift,
        server_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone
      },
      database_info: {
        total_today_records: todayData.rows.length,
        a_shift_records: aShiftData.rows.length,
        b_shift_records: bShiftData.rows.length,
        all_today_data: todayData.rows,
        a_shift_data: aShiftData.rows[0] || null,
        b_shift_data: bShiftData.rows[0] || null
      },
      queries_used: {
        today_query: `WHERE record_date = '${currentShift.recordDate}'`,
        a_shift_query: `WHERE record_date = '${currentShift.recordDate}' AND shift_name = 'A'`,
        b_shift_query: `WHERE record_date = '${currentShift.recordDate}' AND shift_name = 'B'`
      }
    });
    
  } catch (error) {
    res.json({ 
      error: error.message,
      stack: error.stack 
    });
  }
});

// 5. HEALTH CHECK
app.get('/api/health', async (req, res) => {
  try {
    // Test database connection
    await pool.query('SELECT 1');
    
    // Get database stats
    const dashboardStats = await pool.query(`
      SELECT 
        COUNT(*) as total_records,
        MAX(timestamp) as latest_timestamp,
        MIN(timestamp) as oldest_timestamp,
        MAX(record_date) as latest_date,
        MIN(record_date) as oldest_date
      FROM ync_dashboard_complete
    `);
    
    const cycleTimeStats = await pool.query(`
      SELECT 
        COUNT(*) as total_records,
        MAX(timestamp) as latest_timestamp,
        MIN(timestamp) as oldest_timestamp,
        MAX(record_date) as latest_date,
        MIN(record_date) as oldest_date
      FROM ync_cycle_time_tracking
    `);
    
    const currentShift = getCurrentShift();
    
    res.json({
      status: 'healthy',
      database: 'connected',
      server_time: new Date().toISOString(),
      server_time_local: new Date().toLocaleString(),
      current_shift: currentShift.shiftName,
      current_date: currentShift.recordDate,
      database_stats: {
        dashboard_table: dashboardStats.rows[0],
        cycle_time_table: cycleTimeStats.rows[0]
      },
      endpoints: {
        latest_data: '/api/dashboard/latest',
        cycle_time: '/api/cycle-time/latest',
        health: '/api/health',
        debug: '/api/debug/data',
        test_data: '/api/test/sample'
      }
    });
    
  } catch (error) {
    res.json({
      status: 'unhealthy',
      database: 'disconnected',
      error: error.message,
      server_time: new Date().toISOString()
    });
  }
});

// 6. HOURLY DATA BY SHIFT
app.get('/api/hourly/:shift', async (req, res) => {
  try {
    const { shift } = req.params;
    const currentShift = getCurrentShift();
    
    console.log(`📈 Fetching hourly data for shift ${shift}...`);
    
    // Determine record date for the requested shift
    let recordDate = currentShift.recordDate;
    if (shift === 'B') {
      const now = new Date();
      const hour = now.getHours();
      const minute = now.getMinutes();
      const currentMinutes = hour * 60 + minute;
      
      // If current time is before 03:15, B shift belongs to yesterday
      if (currentMinutes < (3*60+15)) {
        const yesterday = new Date(now);
        yesterday.setDate(yesterday.getDate() - 1);
        recordDate = yesterday.toISOString().split('T')[0];
      }
    }
    
    const result = await pool.query(`
      SELECT * FROM ync_dashboard_complete 
      WHERE shift_name = $1 
        AND record_date = $2
      ORDER BY timestamp DESC 
      LIMIT 1
    `, [shift, recordDate]);
    
    if (result.rows.length > 0) {
      const row = result.rows[0];
      
      // Prepare hourly data
      const hourlyData = prepareHourlyData(row, shift);
      
      res.json({
        success: true,
        shift: shift,
        record_date: recordDate,
        current_shift: currentShift.shiftName,
        hourly_data: hourlyData,
        timestamp: row.timestamp
      });
      
    } else {
      res.json({
        success: true,
        shift: shift,
        record_date: recordDate,
        message: 'No data found for this shift',
        hourly_data: createEmptyHourlyData(shift)
      });
    }
    
  } catch (error) {
    res.json({
      success: false,
      error: error.message
    });
  }
});

// Helper: Prepare hourly data
function prepareHourlyData(row, shift) {
  if (shift === 'A') {
    return {
      hours: [
        '08:30-09:30', '09:30-10:30', '10:30-11:30', '11:30-13:05',
        '13:05-14:05', '14:05-15:05', '15:05-16:05', '16:05-17:15'
      ],
      plan: [
        row.hour_0830_0930_plan || 0,
        row.hour_0930_1030_plan || 0,
        row.hour_1030_1130_plan || 0,
        row.hour_1130_1305_plan || 0,
        row.hour_1305_1405_plan || 0,
        row.hour_1405_1505_plan || 0,
        row.hour_1505_1605_plan || 0,
        row.hour_1605_1715_plan || 0
      ],
      actual: [
        row.hour_0830_0930_actual || 0,
        row.hour_0930_1030_actual || 0,
        row.hour_1030_1130_actual || 0,
        row.hour_1130_1305_actual || 0,
        row.hour_1305_1405_actual || 0,
        row.hour_1405_1505_actual || 0,
        row.hour_1505_1605_actual || 0,
        row.hour_1605_1715_actual || 0
      ],
      ok: [
        row.hour_0830_0930_ok || 0,
        row.hour_0930_1030_ok || 0,
        row.hour_1030_1130_ok || 0,
        row.hour_1130_1305_ok || 0,
        row.hour_1305_1405_ok || 0,
        row.hour_1405_1505_ok || 0,
        row.hour_1505_1605_ok || 0,
        row.hour_1605_1715_ok || 0
      ],
      ng: [
        row.hour_0830_0930_ng || 0,
        row.hour_0930_1030_ng || 0,
        row.hour_1030_1130_ng || 0,
        row.hour_1130_1305_ng || 0,
        row.hour_1305_1405_ng || 0,
        row.hour_1405_1505_ng || 0,
        row.hour_1505_1605_ng || 0,
        row.hour_1605_1715_ng || 0
      ]
    };
  } else {
    return {
      hours: [
        '18:30-19:30', '19:30-20:30', '20:30-21:30', '21:30-23:05',
        '23:05-00:05', '00:05-01:05', '01:05-02:05', '02:05-03:15'
      ],
      plan: [
        row.hour_1830_1930_plan || 0,
        row.hour_1930_2030_plan || 0,
        row.hour_2030_2130_plan || 0,
        row.hour_2130_2305_plan || 0,
        row.hour_2305_0005_plan || 0,
        row.hour_0005_0105_plan || 0,
        row.hour_0105_0205_plan || 0,
        row.hour_0205_0315_plan || 0
      ],
      actual: [
        row.hour_1830_1930_actual || 0,
        row.hour_1930_2030_actual || 0,
        row.hour_2030_2130_actual || 0,
        row.hour_2130_2305_actual || 0,
        row.hour_2305_0005_actual || 0,
        row.hour_0005_0105_actual || 0,
        row.hour_0105_0205_actual || 0,
        row.hour_0205_0315_actual || 0
      ],
      ok: [
        row.hour_1830_1930_ok || 0,
        row.hour_1930_2030_ok || 0,
        row.hour_2030_2130_ok || 0,
        row.hour_2130_2305_ok || 0,
        row.hour_2305_0005_ok || 0,
        row.hour_0005_0105_ok || 0,
        row.hour_0105_0205_ok || 0,
        row.hour_0205_0315_ok || 0
      ],
      ng: [
        row.hour_1830_1930_ng || 0,
        row.hour_1930_2030_ng || 0,
        row.hour_2030_2130_ng || 0,
        row.hour_2130_2305_ng || 0,
        row.hour_2305_0005_ng || 0,
        row.hour_0005_0105_ng || 0,
        row.hour_0105_0205_ng || 0,
        row.hour_0205_0315_ng || 0
      ]
    };
  }
}

// Helper: Create empty hourly data
function createEmptyHourlyData(shift) {
  const emptyArray = Array(8).fill(0);
  
  if (shift === 'A') {
    return {
      hours: [
        '08:30-09:30', '09:30-10:30', '10:30-11:30', '11:30-13:05',
        '13:05-14:05', '14:05-15:05', '15:05-16:05', '16:05-17:15'
      ],
      plan: [...emptyArray],
      actual: [...emptyArray],
      ok: [...emptyArray],
      ng: [...emptyArray]
    };
  } else {
    return {
      hours: [
        '18:30-19:30', '19:30-20:30', '20:30-21:30', '21:30-23:05',
        '23:05-00:05', '00:05-01:05', '01:05-02:05', '02:05-03:15'
      ],
      plan: [...emptyArray],
      actual: [...emptyArray],
      ok: [...emptyArray],
      ng: [...emptyArray]
    };
  }
}

// Start server
app.listen(PORT, () => {
  console.log(`\n✅ ========== DASHBOARD BACKEND SERVER STARTED ==========`);
  console.log(`🌐 URL: http://localhost:${PORT}`);
  console.log(`\n📡 API ENDPOINTS:`);
  console.log(`├── GET  /                         - Server status`);
  console.log(`├── GET  /api/dashboard/latest     - Latest dashboard data`);
  console.log(`├── GET  /api/cycle-time/latest    - Latest cycle time data`);
  console.log(`├── GET  /api/health               - Health check`);
  console.log(`├── GET  /api/debug/data           - Debug data`);
  console.log(`├── GET  /api/hourly/:shift        - Hourly data (A/B)`);
  console.log(`└── GET  /api/test/sample         - Sample test data`);
  
  // Log current shift info
  const currentShift = getCurrentShift();
  console.log(`\n📅 CURRENT SHIFT INFO:`);
  console.log(`├── Shift: ${currentShift.shiftName}`);
  console.log(`├── Date: ${currentShift.recordDate}`);
  console.log(`├── Display Time: ${currentShift.displayTime}`);
  console.log(`├── Is Gap Time: ${currentShift.isGap}`);
  console.log(`└── Server Time: ${new Date().toLocaleTimeString()}`);
  console.log(`\n🚀 Server ready!`);
});