<template>
  <div class="shift-timeline-container" :style="timelineStyle">
    <!-- API Connection Status -->
    <div style="
        position: absolute;
        top: 5px;
        right: 10px;
        font-size: 11px;
        color: #666;
        background: #111;
        padding: 2px 8px;
        border-radius: 4px;
        border: 1px solid #333;
        z-index: 1000;
    ">
      <span :style="{ color: apiConnected ? '#00FF00' : '#FF0000' }">●</span>
      API: {{ apiConnected ? 'Connected' : 'Disconnected' }}
      | Last: {{ lastUpdated }}
      | Time: {{ currentTime }}
      | Progress: A:{{ aShiftProgress.toFixed(0) }}% B:{{ bShiftProgress.toFixed(0) }}%
    </div>

    <!-- A SHIFT -->
    <div v-if="visibleShift === 'A'" style="margin-bottom: 10px;">
      <div style="display: flex; align-items: center; margin-bottom: 10px;">
        <div style="
            font-size: 14px;
            font-weight: bold;
            color: white;
            min-width: 130px;
        ">
          <div style="color: #00FF00; margin-bottom: 5px;">A SHIFT</div>
          <div style="font-size: 12px; color: #AAA;">08:30 - 17:15</div>
        </div>
       
        <div style="flex-grow: 1; margin-left: 15px; position: relative;">
          <!-- SHIFT A PROGRESS BAR -->
          <div id="shift-a-bar" style="
              width: 100%;
              height: 50px;
              background: #333333;
              position: relative;
              overflow: hidden;
          ">
            <!-- STATUS HISTORY SEGMENTS -->
            <div id="shift-a-status-segments" style="
                position: absolute;
                width: 100%;
                height: 100%;
                top: 0;
                left: 0;
                z-index: 1;
            ">
              <div
                v-for="(segment, index) in filteredStatusSegmentsA"
                :key="'status-seg-a-' + index + now"
                :style="getStatusSegmentStyle(segment, 'A')"
                :title="`${segment.status} from ${formatTimeFromPercent(segment.start, 'A')} to ${formatTimeFromPercent(segment.end, 'A')}`"
                class="status-segment"
              ></div>
            </div>
           
            <!-- BREAKS -->
            <div id="shift-a-breaks" style="
                position: absolute;
                width: 100%;
                height: 100%;
                top: 0;
                left: 0;
                z-index: 2;
            ">
              <div
                v-for="(breakItem, index) in A_SHIFT_BREAKS"
                :key="'break-a-' + index"
                :style="getBreakStyle('A', breakItem)"
                :title="`${breakItem.label}: ${breakItem.start} - ${breakItem.end}`"
              ></div>
            </div>
           
            <!-- HOUR LINES -->
            <div id="shift-a-hour-lines" style="
                position: absolute;
                width: 100%;
                height: 100%;
                top: 0;
                left: 0;
                z-index: 0;
                pointer-events: none;
            ">
              <div
                v-for="(hour, index) in A_SHIFT_HOURS"
                :key="'hour-a-' + index"
                :style="getHourLineStyle('A', hour)"
                :title="hour.label"
              ></div>
            </div>
           
            <!-- CURRENT TIME INDICATOR (WHITE LINE) -->
            <div :style="{
                position: 'absolute',
                height: '100%',
                width: '3px',
                background: '#FFFFFF',
                left: aShiftProgress + '%',
                top: '0',
                zIndex: '4',
                boxShadow: '0 0 10px #FFFFFF',
                pointerEvents: 'none'
              }"></div>
          </div>
         
          <!-- HOUR SCALE LABELS -->
          <div id="a-hour-scale" style="
              display: flex;
              margin-top: 5px;
              position: relative;
              height: 12px;
              background: #080808;
              padding: 2px 0;
          ">
            <div
              v-for="(hour, index) in A_SHIFT_HOURS"
              :key="'scale-a-' + index"
              :style="getHourScaleStyle('A', hour)"
              class="hour-label"
            >
              {{ hour.start }}
            </div>
            <div style="
                position: absolute;
                right: 0;
                top: 0;
                font-size: 10px;
                color: #AAA;
                padding: 3px 5px;
            ">
              {{ A_SHIFT_HOURS[A_SHIFT_HOURS.length - 1].end }}
            </div>
          </div>
        </div>
       
        <div style="
            font-size: 16px;
            color: #00FF00;
            font-weight: bold;
            margin-left: 15px;
            min-width: 60px;
            text-align: center;
        ">
          <div id="shift-a-progress">{{ aShiftProgress.toFixed(1) }}%</div>
          <div style="font-size: 11px; color: #AAA;">Progress</div>
        </div>
      </div>
     
      <div style="
          font-size: 12px;
          color: #AAA;
          padding-left: 145px;
          margin-top: 10px;
      ">
        Current-time: <span id="shift-a-time" style="color: white; padding-right: 20px;">{{ currentTime }}</span>
        | Status: <span :style="{
          color: getCurrentStatusColor('A'),
          fontWeight: 'bold'
        }">
          {{ getCurrentStatus('A') }}
        </span>
        | Since: <span style="color: #00FF00;">{{ getCurrentStatusSince('A') }}</span>
      </div>
    </div>
   
    <!-- B SHIFT -->
    <div v-if="visibleShift === 'B'">
      <div style="display: flex; align-items: center; margin-bottom: 10px;">
        <div style="
            font-size: 14px;
            font-weight: bold;
            color: white;
            min-width: 130px;
        ">
          <div style="color: #00FF00; margin-bottom: 5px;">B SHIFT</div>
          <div style="font-size: 12px; color: #AAA;">18:30 - 03:15</div>
        </div>
       
        <div style="flex-grow: 1; margin-left: 15px; position: relative;">
          <!-- SHIFT B PROGRESS BAR -->
          <div id="shift-b-bar" style="
              width: 100%;
              height: 50px;
              background: #333333;
              position: relative;
              overflow: hidden;
          ">
            <!-- STATUS HISTORY SEGMENTS -->
            <div id="shift-b-status-segments" style="
                position: absolute;
                width: 100%;
                height: 100%;
                top: 0;
                left: 0;
                z-index: 1;
            ">
              <div
                v-for="(segment, index) in filteredStatusSegmentsB"
                :key="'status-seg-b-' + index + now"
                :style="getStatusSegmentStyle(segment, 'B')"
                :title="`${segment.status} from ${formatTimeFromPercent(segment.start, 'B')} to ${formatTimeFromPercent(segment.end, 'B')}`"
                class="status-segment"
              ></div>
            </div>
           
            <!-- BREAKS -->
            <div id="shift-b-breaks" style="
                position: absolute;
                width: 100%;
                height: 100%;
                top: 0;
                left: 0;
                z-index: 2;
            ">
              <div
                v-for="(breakItem, index) in B_SHIFT_BREAKS"
                :key="'break-b-' + index"
                :style="getBreakStyle('B', breakItem)"
                :title="`${breakItem.label}: ${breakItem.start} - ${breakItem.end}`"
              ></div>
            </div>
           
            <!-- HOUR LINES -->
            <div id="shift-b-hour-lines" style="
                position: absolute;
                width: 100%;
                height: 100%;
                top: 0;
                left: 0;
                z-index: 0;
                pointer-events: none;
            ">
              <div
                v-for="(hour, index) in B_SHIFT_HOURS"
                :key="'hour-b-' + index"
                :style="getHourLineStyle('B', hour)"
                :title="hour.label"
              ></div>
            </div>
           
            <!-- CURRENT TIME INDICATOR (WHITE LINE) -->
            <div :style="{
                position: 'absolute',
                height: '100%',
                width: '3px',
                background: '#FFFFFF',
                left: bShiftProgress + '%',
                top: '0',
                zIndex: '4',
                boxShadow: '0 0 10px #FFFFFF',
                pointerEvents: 'none'
              }"></div>
          </div>
         
          <!-- HOUR SCALE LABELS -->
          <div id="b-hour-scale" style="
              display: flex;
              margin-top: 5px;
              position: relative;
              height: 12px;
              background: #080808;
              padding: 2px 0;
          ">
            <div
              v-for="(hour, index) in B_SHIFT_HOURS"
              :key="'scale-b-' + index"
              :style="getHourScaleStyle('B', hour)"
              class="hour-label"
            >
              {{ hour.start }}
            </div>
            <div style="
                position: absolute;
                right: 0;
                top: 0;
                font-size: 10px;
                color: #AAA;
                padding: 3px 5px;
            ">
              {{ B_SHIFT_HOURS[B_SHIFT_HOURS.length - 1].end }}
            </div>
          </div>
        </div>
       
        <div style="
            font-size: 16px;
            color: #00FF00;
            font-weight: bold;
            margin-left: 15px;
            min-width: 60px;
            text-align: center;
        ">
          <div id="shift-b-progress">{{ bShiftProgress.toFixed(1) }}%</div>
          <div style="font-size: 11px; color: #AAA;">Progress</div>
        </div>
      </div>
     
      <div style="
          font-size: 12px;
          color: #AAA;
          padding-left: 145px;
          margin-top: 10px;
      ">
        Current-time: <span id="shift-b-time" style="color: white; padding-right: 20px;">{{ currentTime }}</span>
        | Status: <span :style="{
          color: getCurrentStatusColor('B'),
          fontWeight: 'bold'
        }">
          {{ getCurrentStatus('B') }}
        </span>
        | Since: <span style="color: #00FF00;">{{ getCurrentStatusSince('B') }}</span>
      </div>
    </div>
   
    <!-- NO SHIFT MESSAGE -->
    <div v-if="visibleShift === 'NONE'" style="
        text-align: center;
        padding: 20px;
        color: #666;
        font-size: 18px;
        font-weight: bold;
        border: 2px dashed #333;
        margin: 20px 0;
    ">
      ⏸️ NO ACTIVE SHIFT ⏸️
    </div>
   
    <!-- LEGEND -->
    <div style="
        margin-top: 20px;
        padding-top: 10px;
        display: flex;
        justify-content: center;
        gap: 20px;
        flex-wrap: wrap;
        border-top: 1px solid #333;
    ">
      <div style="display: flex; align-items: center; gap: 8px;">
        <div :style="{ width: '15px', height: '15px', background: '#518029' }"></div>
        <span style="font-size: 12px; color: white;">RUNNING</span>
      </div>
      <div style="display: flex; align-items: center; gap: 8px;">
        <div :style="{ width: '15px', height: '15px', background: '#FF0000' }"></div>
        <span style="font-size: 12px; color: white;">BREAKDOWN</span>
      </div>
      <div style="display: flex; align-items: center; gap: 8px;">
        <div :style="{ width: '15px', height: '15px', background: '#FFFF00' }"></div>
        <span style="font-size: 12px; color: white;">QUALITY</span>
      </div>
      <div style="display: flex; align-items: center; gap: 8px;">
        <div :style="{ width: '15px', height: '15px', background: '#FFA500' }"></div>
        <span style="font-size: 12px; color: white;">MATERIAL</span>
      </div>
      <div style="display: flex; align-items: center; gap: 8px;">
        <div :style="{ width: '15px', height: '15px', background: '#0069AA' }"></div>
        <span style="font-size: 12px; color: white;">SET-UP</span>
      </div>
      <div style="display: flex; align-items: center; gap: 8px;">
        <div :style="{ width: '15px', height: '15px', background: '#00FFFF' }"></div>
        <span style="font-size: 12px; color: white;">OTHER LOSS</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  segments: {
    type: Array,
    default: () => []
  }
})

// ============ API Configuration ============
const API_URL = 'http://localhost:3500/api/dashboard/latest'
const dashboardData = ref({})
const apiConnected = ref(false)
const lastUpdated = ref('Never')
const isLoading = ref(false)
const error = ref(null)

// ============ LOCAL STORAGE FUNCTIONS ============
function loadHistoryFromStorage() {
  try {
    const saved = localStorage.getItem('shiftTimelineHistory')
    if (saved) {
      const parsed = JSON.parse(saved)
      if (parsed.A) {
        parsed.A = parsed.A.map(item => ({
          ...item,
          startTime: item.startTime,
          endTime: item.endTime
        }))
      }
      if (parsed.B) {
        parsed.B = parsed.B.map(item => ({
          ...item,
          startTime: item.startTime,
          endTime: item.endTime
        }))
      }
      return parsed
    }
  } catch (e) {
    console.error('Error loading from localStorage:', e)
  }
  return { 'A': [], 'B': [] }
}

function saveHistoryToStorage(history) {
  try {
    localStorage.setItem('shiftTimelineHistory', JSON.stringify(history))
  } catch (e) {
    console.error('Error saving to localStorage:', e)
  }
}

function loadClearDatesFromStorage() {
  try {
    return {
      lastAClearDate: localStorage.getItem('lastAClearDate'),
      lastBClearDate: localStorage.getItem('lastBClearDate')
    }
  } catch (e) {
    console.error('Error loading clear dates:', e)
    return { lastAClearDate: null, lastBClearDate: null }
  }
}

function saveClearDateToStorage(shift, date) {
  try {
    if (shift === 'A') {
      localStorage.setItem('lastAClearDate', date)
    } else {
      localStorage.setItem('lastBClearDate', date)
    }
  } catch (e) {
    console.error('Error saving clear date:', e)
  }
}

// ============ STATUS HISTORY ============
const statusHistory = ref(loadHistoryFromStorage())
let { lastAClearDate, lastBClearDate } = loadClearDatesFromStorage()

// ============ REACTIVE DATA ============
const currentTime = ref('')
const aShiftProgress = ref(0)
const bShiftProgress = ref(0)
const hoveredSegment = ref(null)
const mousePosition = ref({ x: 0, y: 0 })

// ============ 🔥 FIX 1: Reactive now value ============
const now = ref(Date.now())

// ============ VISIBLE SHIFT LOGIC (with now dependency) ============
const visibleShift = computed(() => {
  // 🔥 now.value ensures reactivity
  const current = new Date(now.value)
  const currentHour = current.getHours()
  const currentMinute = current.getMinutes()
  const currentDecimal = currentHour + (currentMinute / 60)

  const aStart = 8.5
  const aEnd = 17.25
  const bStart = 18.5
  const bEnd = 27.25

  let adjustedCurrent = currentDecimal
  if (currentDecimal < 3.25) {
    adjustedCurrent += 24
  }

  const isAShift = currentDecimal >= aStart && currentDecimal <= aEnd
  const isBShift = (adjustedCurrent >= bStart && adjustedCurrent <= bEnd)

  if (isAShift) return 'A'
  if (isBShift) return 'B'
  return 'NONE'
})

// ============ AUTO-CLEAR LOGIC ============
function checkAutoClear() {
  const current = new Date(now.value)
  const currentHour = current.getHours()
  const currentMinute = current.getMinutes()
  const today = current.toDateString()
 
  if (currentHour === 8 && currentMinute === 25) {
    if (lastAClearDate !== today) {
      statusHistory.value['A'] = []
      lastAClearDate = today
      saveClearDateToStorage('A', today)
      saveHistoryToStorage(statusHistory.value)
      console.log('🧹 A Shift history auto-cleared')
    }
  }
 
  if (currentHour === 18 && currentMinute === 25) {
    if (lastBClearDate !== today) {
      statusHistory.value['B'] = []
      lastBClearDate = today
      saveClearDateToStorage('B', today)
      saveHistoryToStorage(statusHistory.value)
      console.log('🧹 B Shift history auto-cleared')
    }
  }
}

// ============ API FUNCTIONS ============
async function fetchDashboardData() {
  try {
    isLoading.value = true
    error.value = null
   
    console.log('🌐 Fetching API data...', new Date().toLocaleTimeString())
   
    const response = await fetch(API_URL, {
      headers: {
        'Accept': 'application/json',
        'Cache-Control': 'no-cache'
      }
    })
   
    if (!response.ok) {
      throw new Error(`API Error: ${response.status}`)
    }
   
    const data = await response.json()
    console.log('📦 API Response:', data)
    
    dashboardData.value = data
   
    apiConnected.value = data.connected || false
   
    const nowTime = new Date()
    lastUpdated.value = nowTime.toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    })
   
    const status = data.data?.operating_status
    console.log('📍 Current Status from API:', status)
    
    updateStatusHistory(status)
    
  } catch (err) {
    console.error('❌ Error fetching dashboard data:', err)
    error.value = err.message
    apiConnected.value = false
  } finally {
    isLoading.value = false
  }
}

// ============ STATUS HISTORY UPDATE ============
function updateStatusHistory(currentStatus) {
  if (!currentStatus) return false
 
  const current = new Date(now.value)
  const currentHour = current.getHours()
  const currentMinute = current.getMinutes()
  const currentDecimal = currentHour + (currentMinute / 60)
 
  let currentShift = null
  if (currentDecimal >= 8.5 && currentDecimal <= 17.25) {
    currentShift = 'A'
  } else if (currentDecimal >= 18.5 || (currentDecimal >= 0 && currentDecimal <= 3.25)) {
    currentShift = 'B'
  } else {
    return false
  }
 
  const currentHistory = statusHistory.value[currentShift] || []
  const newHistory = [...currentHistory]
  const nowTime = current.getTime()
 
  if (newHistory.length === 0) {
    newHistory.push({
      status: currentStatus,
      startTime: nowTime,
      endTime: null
    })
    statusHistory.value[currentShift] = newHistory
    saveHistoryToStorage(statusHistory.value)
    return true
  }
 
  const lastEntry = { ...newHistory[newHistory.length - 1] }
 
  if (lastEntry.status !== currentStatus) {
    lastEntry.endTime = nowTime
    newHistory[newHistory.length - 1] = lastEntry
   
    newHistory.push({
      status: currentStatus,
      startTime: nowTime,
      endTime: null
    })
   
    statusHistory.value[currentShift] = newHistory
    saveHistoryToStorage(statusHistory.value)
    return true
  }
  
  return false
}

// ============ STATUS SEGMENTS ============
const filteredStatusSegmentsA = computed(() => {
  // 🔥 now.value ensures recompute
  now.value
  return getStatusSegmentsForDisplay('A')
})

const filteredStatusSegmentsB = computed(() => {
  // 🔥 now.value ensures recompute
  now.value
  return getStatusSegmentsForDisplay('B')
})

function getStatusSegmentsForDisplay(shift) {
  const history = statusHistory.value[shift] || []
  if (history.length === 0) return []
 
  const segments = []
  const today = new Date(now.value).toDateString()
  const currentProgress = shift === 'A' ? aShiftProgress.value : bShiftProgress.value
 
  for (let i = 0; i < history.length; i++) {
    const segment = history[i]
   
    const segmentDate = new Date(segment.startTime).toDateString()
    if (segmentDate !== today) continue
   
    let startPercent, endPercent
   
    if (i === 0) {
      startPercent = 0
    } else {
      const prevSegment = history[i-1]
      if (prevSegment.endTime) {
        const prevEndDate = new Date(prevSegment.endTime).toDateString()
        startPercent = prevEndDate === today ? timeToProgress(prevSegment.endTime, shift) : 0
      } else {
        startPercent = timeToProgress(prevSegment.startTime, shift)
      }
    }
   
    if (segment.endTime) {
      const endDate = new Date(segment.endTime).toDateString()
      endPercent = endDate === today ? timeToProgress(segment.endTime, shift) : currentProgress
    } else {
      endPercent = currentProgress
    }
   
    startPercent = Math.min(startPercent, currentProgress)
    endPercent = Math.min(endPercent, currentProgress)
   
    if (startPercent >= 0 && endPercent > startPercent && endPercent <= currentProgress) {
      segments.push({
        status: segment.status,
        start: startPercent,
        end: endPercent,
        startTime: segment.startTime,
        endTime: segment.endTime
      })
    }
  }
 
  return segments
}

// ============ UTILITY FUNCTIONS ============
function timeToDecimal(timeStr) {
  if (!timeStr) return 0
  const [hours, minutes] = timeStr.split(':').map(Number)
  return hours + (minutes / 60)
}

function getCurrentTimeDecimal(shift) {
  const current = new Date(now.value)
  const currentHour = current.getHours()
  const currentMinute = current.getMinutes()
  let currentDecimal = currentHour + (currentMinute / 60)
 
  if (shift === 'B' && currentDecimal < 3.25) {
    currentDecimal += 24
  }
  return currentDecimal
}

function getCurrentTimeFormatted() {
  const current = new Date(now.value)
  const hours = current.getHours().toString().padStart(2, '0')
  const minutes = current.getMinutes().toString().padStart(2, '0')
  return `${hours}:${minutes}`
}

function timeToProgress(timestamp, shift) {
  if (!timestamp) return 0
 
  const date = new Date(timestamp)
  const today = new Date(now.value)
 
  if (date.toDateString() !== today.toDateString()) {
    return 0
  }
 
  const hour = date.getHours()
  const minute = date.getMinutes()
  const timeDecimal = hour + (minute / 60)
 
  if (shift === 'A') {
    const shiftStart = 8.5
    const shiftEnd = 17.25
    const totalDuration = shiftEnd - shiftStart
   
    let adjustedTime = timeDecimal
    if (adjustedTime < shiftStart) adjustedTime += 24
   
    return ((adjustedTime - shiftStart) / totalDuration) * 100
  } else {
    const shiftStart = 18.5
    const shiftEnd = 27.25
    const totalDuration = shiftEnd - shiftStart
   
    let adjustedTime = timeDecimal
    if (adjustedTime < 3.25) adjustedTime += 24
    if (adjustedTime < shiftStart) adjustedTime += 24
   
    return ((adjustedTime - shiftStart) / totalDuration) * 100
  }
}

function calculatePosition(shift, timeStr) {
  if (!timeStr) return 0
 
  if (shift === 'A') {
    const shiftStart = 8.5
    const shiftEnd = 17.25
    const totalDuration = shiftEnd - shiftStart
    const timeDecimal = timeToDecimal(timeStr)
    return ((timeDecimal - shiftStart) / totalDuration) * 100
  } else {
    const shiftStart = 18.5
    const shiftEnd = 27.25
    const totalDuration = shiftEnd - shiftStart
    let timeDecimal = timeToDecimal(timeStr)
    if (timeDecimal < 3.25) timeDecimal += 24
    return ((timeDecimal - shiftStart) / totalDuration) * 100
  }
}

// ============ 🔥 FIX 2: calculateShiftProgress with now.value ============
function calculateShiftProgress(shift) {
  const current = new Date(now.value)
  const currentHour = current.getHours()
  const currentMinute = current.getMinutes()
  const currentDecimal = currentHour + (currentMinute / 60)

  if (shift === 'A') {
    const start = 8.5
    const end = 17.25
    if (currentDecimal >= start && currentDecimal <= end) {
      return ((currentDecimal - start) / (end - start)) * 100
    }
    return currentDecimal > end ? 100 : 0
  } else {
    const start = 18.5
    const end = 27.25
    let adjustedCurrent = currentDecimal
    if (currentDecimal < 3.25) {
      adjustedCurrent += 24
    }
    if (adjustedCurrent >= start && adjustedCurrent <= end) {
      return ((adjustedCurrent - start) / (end - start)) * 100
    }
    return adjustedCurrent > end ? 100 : 0
  }
}

// ============ STYLE FUNCTIONS ============
function getStatusColor(status) {
  switch(status?.toUpperCase()) {
    case 'BREAKDOWN': return '#FF0000'
    case 'RUNNING': return '#518029'
    case 'MATERIAL': return '#FFA500'
    case 'SETUP': return '#0069AA'
    case 'QUALITY': return '#FFFF00'
    case 'OTHER-LOSS': return '#00FFFF'
    default: return '#00FF00'
  }
}

function getStatusSegmentStyle(segment, shift) {
  const width = segment.end - segment.start
  return {
    position: 'absolute',
    height: '100%',
    background: getStatusColor(segment.status),
    left: segment.start + '%',
    width: width + '%',
    top: '0',
    opacity: '0.7',
    zIndex: 1,
    borderRadius: '2px'
  }
}

function getBreakStyle(shift, breakItem) {
  const startPos = calculatePosition(shift, breakItem.start)
  const endPos = calculatePosition(shift, breakItem.end)
  const width = endPos - startPos
 
  return {
    position: 'absolute',
    height: '100%',
    background: breakItem.color || '#666666',
    left: startPos + '%',
    width: width + '%',
    top: '0',
    opacity: '0.8',
    borderLeft: '2px dashed #888',
    borderRight: '2px dashed #888',
    zIndex: 2
  }
}

function getHourLineStyle(shift, hour) {
  const startPos = calculatePosition(shift, hour.start)
  const endPos = calculatePosition(shift, hour.end)
  const width = endPos - startPos
 
  return {
    position: 'absolute',
    height: '100%',
    left: startPos + '%',
    width: width + '%',
    top: '0',
    borderLeft: '1px solid #555',
    borderRight: '1px solid #555',
    pointerEvents: 'none'
  }
}

function getHourScaleStyle(shift, hour) {
  const startPos = calculatePosition(shift, hour.start)
  return {
    position: 'absolute',
    left: startPos + '%',
    fontSize: '10px',
    color: '#AAA',
    textAlign: 'center',
    minWidth: '40px',
    transform: 'translateX(-50%)'
  }
}

function getCurrentStatus(shift) {
  const history = statusHistory.value[shift] || []
  if (history.length === 0) return 'N/A'
  return history[history.length - 1].status
}

function getCurrentStatusColor(shift) {
  const status = getCurrentStatus(shift)
  return getStatusColor(status)
}

function getCurrentStatusSince(shift) {
  const history = statusHistory.value[shift] || []
  if (history.length === 0) return 'N/A'
  const lastEntry = history[history.length - 1]
  if (!lastEntry.startTime) return 'N/A'
  const startTime = new Date(lastEntry.startTime)
  return startTime.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit'
  })
}

function formatTimeFromPercent(percent, shift) {
  let timeDecimal
 
  if (shift === 'A') {
    const shiftStart = 8.5
    const shiftEnd = 17.25
    const totalDuration = shiftEnd - shiftStart
    timeDecimal = shiftStart + (percent / 100) * totalDuration
  } else {
    const shiftStart = 18.5
    const shiftEnd = 27.25
    const totalDuration = shiftEnd - shiftStart
    timeDecimal = shiftStart + (percent / 100) * totalDuration
    if (timeDecimal >= 24) timeDecimal -= 24
  }
 
  const hours = Math.floor(timeDecimal)
  const minutes = Math.round((timeDecimal - hours) * 60)
  return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}`
}

function isShiftActive(shift) {
  const current = new Date(now.value)
  const currentHour = current.getHours()
  const currentMinute = current.getMinutes()
  const currentDecimal = currentHour + (currentMinute / 60)

  if (shift === 'A') {
    const start = 8.5
    const end = 17.25
    return currentDecimal >= start && currentDecimal <= end
  } else {
    const start = 18.5
    let adjustedCurrent = currentDecimal
    if (currentDecimal < 3.25) {
      adjustedCurrent += 24
    }
    return adjustedCurrent >= start && adjustedCurrent <= 27.25
  }
}

function updateShiftProgress() {
  aShiftProgress.value = calculateShiftProgress('A')
  bShiftProgress.value = calculateShiftProgress('B')
}

function updateCurrentTime() {
  const current = new Date(now.value)
  currentTime.value = current.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  })
  checkAutoClear()
}

const updateMousePosition = (event) => {
  mousePosition.value = {
    x: event.clientX,
    y: event.clientY
  }
}

// ============ SHIFT CONSTANTS ============
const A_SHIFT_HOURS = [
  { start: '08:30', end: '09:30', label: '08:30-09:30' },
  { start: '09:30', end: '10:00', label: '09:30-10:00' },
  { start: '10:10', end: '10:30', label: '10:10-10:30' },
  { start: '10:30', end: '11:30', label: '10:30-11:30' },
  { start: '11:30', end: '12:00', label: '11:30-12:00' },
  { start: '12:35', end: '13:05', label: '12:35-13:05' },
  { start: '13:05', end: '14:05', label: '13:05-14:05' },
  { start: '14:05', end: '14:30', label: '14:05-14:30' },
  { start: '14:40', end: '15:05', label: '14:40-15:05' },
  { start: '15:05', end: '16:05', label: '15:05-16:05' },
  { start: '16:05', end: '17:15', label: '16:05-17:15' }
]

const A_SHIFT_BREAKS = [
  { start: '10:00', end: '10:10', type: 'tea', label: 'TEA', color: '#666666' },
  { start: '12:00', end: '12:35', type: 'lunch', label: 'LUNCH', color: '#888888' },
  { start: '14:30', end: '14:40', type: 'tea', label: 'TEA', color: '#666666' }
]

const B_SHIFT_HOURS = [
  { start: '18:30', end: '19:30', label: '18:30-19:30' },
  { start: '19:30', end: '20:00', label: '19:30-20:00' },
  { start: '20:10', end: '20:30', label: '20:10-20:30' },
  { start: '20:30', end: '21:30', label: '20:30-21:30' },
  { start: '21:30', end: '22:00', label: '21:30-22:00' },
  { start: '22:35', end: '23:05', label: '22:35-23:05' },
  { start: '23:05', end: '00:05', label: '23:05-00:05' },
  { start: '00:05', end: '01:00', label: '00:05-01:00' },
  { start: '01:10', end: '02:05', label: '01:10-02:05' },
  { start: '02:05', end: '03:15', label: '02:05-03:15' }
]

const B_SHIFT_BREAKS = [
  { start: '20:00', end: '20:10', type: 'tea', label: 'TEA', color: '#666666' },
  { start: '22:00', end: '22:35', type: 'tea', label: 'DINNER', color: '#888888' },
  { start: '01:00', end: '01:10', type: 'tea', label: 'TEA', color: '#666666' }
]

const timelineStyle = computed(() => ({
  width: '100%',
  background: '#000000',
  margin: '2px 0',
  padding: '10px',
  fontFamily: 'Arial, sans-serif',
  position: 'relative',
  border: '1px solid white',
  cursor: 'default'
}))

// ============ LIFECYCLE HOOKS ============
let apiInterval, timeInterval, progressInterval

onMounted(() => {
  updateCurrentTime()
  updateShiftProgress()
  fetchDashboardData()
  
  // 🔥 FIX 3: Update now.value every second
  timeInterval = setInterval(() => {
    now.value = Date.now()
    updateCurrentTime()
  }, 1000)
  
  progressInterval = setInterval(() => {
    now.value = Date.now()
    updateShiftProgress()
  }, 1000)
  
  apiInterval = setInterval(() => {
    console.log('⏰ Auto-refresh triggered at', new Date().toLocaleTimeString())
    fetchDashboardData()
  }, 2000)
  
  document.addEventListener('mousemove', updateMousePosition)
})

// Clean up on unmount
onUnmounted(() => {
  if (apiInterval) clearInterval(apiInterval)
  if (timeInterval) clearInterval(timeInterval)
  if (progressInterval) clearInterval(progressInterval)
  document.removeEventListener('mousemove', updateMousePosition)
})
</script>

<style scoped>
.hour-label {
  position: absolute;
  font-size: 10px;
  color: #AAA;
  text-align: center;
  min-width: 40px;
  transform: translateX(-50%);
}

.segment-bar {
  transition: all 0.3s ease;
}

.segment-bar:hover {
  opacity: 1 !important;
  z-index: 10 !important;
  transform: scaleY(1.1);
  box-shadow: 0 0 15px rgba(255, 255, 255, 0.7) !important;
}

.duration-label {
  position: absolute;
  top: -25px;
  left: 50%;
  transform: translateX(-50%) !important;
  background: rgba(0, 0, 0, 0.9) !important;
  color: #FFF !important;
  padding: 3px 8px !important;
  border-radius: 3px !important;
  font-size: 11px !important;
  white-space: nowrap !important;
  z-index: 100 !important;
  border: 1px solid #666 !important;
  pointer-events: none !important;
}

.status-segment {
  transition: all 0.3s ease;
  pointer-events: none;
}
</style>