<template>
  <div class="cycle-time-trend-container">
    <!-- Header -->
    <div class="trend-header">
      <div class="header-left">
        <h3>CYCLE TIME TREND</h3>
        <div class="time-range">
          <span class="label">Last 24 Hours</span>
          <span class="current-time">{{ currentTime }}</span>
        </div>
      </div>
      <div class="header-right">
        <div class="legend">
          <div class="legend-item">
            <div class="legend-color target"></div>
            <span>Target: {{ targetTime }}s</span>
          </div>
          <div class="legend-item">
            <div class="legend-color actual"></div>
            <span>Actual: {{ latestActual }}s</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Graph Container -->
    <div class="graph-container">
      <!-- Y-Axis Labels -->
      <div class="y-axis">
        <div class="y-label">Time (s)</div>
        <div class="y-scale">
          <div v-for="(tick, index) in yTicks" :key="'y-tick-' + index" class="y-tick">
            {{ tick }}
          </div>
        </div>
      </div>

      <!-- Main Graph -->
      <div class="main-graph" ref="graphContainer">
        <!-- Grid Lines -->
        <div class="grid-lines">
          <div 
            v-for="(tick, index) in yTicks" 
            :key="'grid-h-' + index" 
            class="grid-line horizontal"
            :style="{ top: `${100 - (index * (100 / (yTicks.length - 1)))}%` }"
          ></div>
          <div 
            v-for="(hour, index) in timeLabels" 
            :key="'grid-v-' + index" 
            class="grid-line vertical"
            :style="{ left: `${index * (100 / (timeLabels.length - 1))}%` }"
          ></div>
        </div>

        <!-- Target Line -->
        <div 
          class="target-line" 
          :style="{ bottom: `${calculateYPosition(targetTime)}%` }"
        >
          <div class="target-label">{{ targetTime }}s</div>
        </div>

        <!-- Actual Data Points and Line -->
        <svg class="trend-svg" :width="graphWidth" :height="graphHeight">
          <!-- Trend Line -->
          <polyline
            v-if="actualData.length > 1"
            :points="getTrendLinePoints()"
            fill="none"
            stroke="#FFD700"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
          
          <!-- Data Points -->
          <circle
            v-for="(point, index) in actualData"
            :key="'point-' + index"
            :cx="calculateXPosition(index)"
            :cy="calculateYPositionSVG(point.value)"
            r="3"
            fill="#FFD700"
            stroke="#000"
            stroke-width="1"
            class="data-point"
            @mouseover="showTooltip(index, $event)"
            @mouseleave="hideTooltip"
          />
        </svg>

        <!-- X-Axis Labels -->
        <div class="x-axis">
          <div 
            v-for="(label, index) in timeLabels" 
            :key="'x-label-' + index" 
            class="x-label"
            :style="{ left: `${index * (100 / (timeLabels.length - 1))}%` }"
          >
            {{ label }}
          </div>
        </div>
      </div>

      <!-- Tooltip -->
      <div 
        v-if="showTooltipFlag" 
        class="tooltip" 
        :style="tooltipStyle"
      >
        <div class="tooltip-time">{{ tooltipData.time }}</div>
        <div class="tooltip-value">
          <span class="label">Cycle Time:</span>
          <span class="value">{{ tooltipData.value }}s</span>
        </div>
        <div class="tooltip-variance">
          <span class="label">Variance:</span>
          <span class="value" :class="{ 'positive': tooltipData.variance <= 0, 'negative': tooltipData.variance > 0 }">
            {{ tooltipData.variance > 0 ? '+' : '' }}{{ tooltipData.variance }}s
          </span>
        </div>
      </div>
    </div>

    <!-- Stats Summary -->
    <div class="stats-summary">
      <div class="stat-item">
        <div class="stat-label">Current</div>
        <div class="stat-value" :class="getVarianceClass(currentVariance)">
          {{ latestActual }}s
        </div>
        <div class="stat-variance" :class="getVarianceClass(currentVariance)">
          {{ currentVariance > 0 ? '+' : '' }}{{ currentVariance }}s
        </div>
      </div>
      <div class="stat-item">
        <div class="stat-label">Average</div>
        <div class="stat-value" :class="getVarianceClass(averageVariance)">
          {{ averageActual.toFixed(2) }}s
        </div>
        <div class="stat-variance" :class="getVarianceClass(averageVariance)">
          {{ averageVariance > 0 ? '+' : '' }}{{ averageVariance.toFixed(2) }}s
        </div>
      </div>
      <div class="stat-item">
        <div class="stat-label">Best</div>
        <div class="stat-value positive">
          {{ bestTime.toFixed(2) }}s
        </div>
        <div class="stat-variance positive">
          {{ (bestTime - targetTime).toFixed(2) }}s
        </div>
      </div>
      <div class="stat-item">
        <div class="stat-label">Worst</div>
        <div class="stat-value negative">
          {{ worstTime.toFixed(2) }}s
        </div>
        <div class="stat-variance negative">
          +{{ (worstTime - targetTime).toFixed(2) }}s
        </div>
      </div>
    </div>

    <!-- Loading Indicator -->
    <div v-if="isLoading" class="loading-overlay">
      <div class="loading-spinner"></div>
      <div class="loading-text">Loading Cycle Time Data...</div>
    </div>

    <!-- Error Message -->
    <div v-if="errorMessage" class="error-message">
      <div class="error-icon">⚠️</div>
      <div class="error-text">{{ errorMessage }}</div>
      <button class="retry-btn" @click="fetchCycleTimeData">Retry</button>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, computed } from 'vue'
import axios from 'axios'

// ========== CONFIGURATION ==========
const API_BASE = 'http://localhost:3500/api'
const CYCLE_TIME_API = `${API_BASE}/cycle-time/history`
const TARGET_CYCLE_TIME = 15.6

// ========== REACTIVE STATE ==========
const isLoading = ref(true)
const errorMessage = ref('')
const currentTime = ref('')
const graphWidth = ref(800)
const graphHeight = ref(400)

// Graph data
const actualData = ref([])
const timeLabels = ref([])
const showTooltipFlag = ref(false)
const tooltipData = reactive({
  time: '',
  value: 0,
  variance: 0
})
const tooltipStyle = reactive({
  left: '0px',
  top: '0px'
})

// ========== COMPUTED PROPERTIES ==========

// Latest actual cycle time
const latestActual = computed(() => {
  if (actualData.value.length === 0) return 0
  return actualData.value[actualData.value.length - 1].value
})

// Current variance from target
const currentVariance = computed(() => {
  return latestActual.value - TARGET_CYCLE_TIME
})

// Average actual cycle time
const averageActual = computed(() => {
  if (actualData.value.length === 0) return 0
  const sum = actualData.value.reduce((acc, item) => acc + item.value, 0)
  return sum / actualData.value.length
})

// Average variance
const averageVariance = computed(() => {
  return averageActual.value - TARGET_CYCLE_TIME
})

// Best (minimum) cycle time
const bestTime = computed(() => {
  if (actualData.value.length === 0) return 0
  return Math.min(...actualData.value.map(item => item.value))
})

// Worst (maximum) cycle time
const worstTime = computed(() => {
  if (actualData.value.length === 0) return 0
  return Math.max(...actualData.value.map(item => item.value))
})

// Y-axis ticks (0 to 30 seconds)
const yTicks = computed(() => {
  return [0, 5, 10, 15, 20, 25, 30]
})

// Target time
const targetTime = computed(() => {
  return TARGET_CYCLE_TIME
})

// ========== GRAPH CALCULATION FUNCTIONS ==========

// Calculate Y position for target line (percentage)
function calculateYPosition(value) {
  const maxValue = 30 // Max value on Y-axis
  return (value / maxValue) * 100
}

// Calculate Y position for SVG
function calculateYPositionSVG(value) {
  const maxValue = 30
  const percentage = value / maxValue
  return graphHeight.value - (percentage * graphHeight.value)
}

// Calculate X position for SVG
function calculateXPosition(index) {
  if (actualData.value.length <= 1) return 0
  const percentage = index / (actualData.value.length - 1)
  return percentage * graphWidth.value
}

// Generate points for trend line
function getTrendLinePoints() {
  if (actualData.value.length <= 1) return ''
  
  return actualData.value.map((point, index) => {
    const x = calculateXPosition(index)
    const y = calculateYPositionSVG(point.value)
    return `${x},${y}`
  }).join(' ')
}

// Get CSS class for variance
function getVarianceClass(variance) {
  return variance <= 0 ? 'positive' : 'negative'
}

// Show tooltip on hover
function showTooltip(index, event) {
  if (index >= actualData.value.length) return
  
  const point = actualData.value[index]
  tooltipData.time = point.time
  tooltipData.value = point.value
  tooltipData.variance = point.value - TARGET_CYCLE_TIME
  
  const rect = event.target.getBoundingClientRect()
  const container = document.querySelector('.main-graph').getBoundingClientRect()
  
  tooltipStyle.left = `${rect.left - container.left + 15}px`
  tooltipStyle.top = `${rect.top - container.top - 60}px`
  
  showTooltipFlag.value = true
}

// Hide tooltip
function hideTooltip() {
  showTooltipFlag.value = false
}

// ========== DATA FETCHING ==========

// Fetch cycle time data from PostgreSQL
async function fetchCycleTimeData() {
  isLoading.value = true
  errorMessage.value = ''
  
  try {
    console.log('🔄 Fetching cycle time history from PostgreSQL...')
    
    // Try to fetch from your API endpoint
    const response = await axios.get(CYCLE_TIME_API, {
      params: {
        hours: 24, // Last 24 hours
        limit: 50  // Limit data points
      },
      timeout: 10000
    })
    
    if (response.data.success && response.data.data) {
      processCycleTimeData(response.data.data)
      console.log('✅ Cycle time data loaded:', actualData.value.length, 'data points')
    } else {
      // If API doesn't have history endpoint, use demo data
      console.log('⚠️ Using demo cycle time data')
      generateDemoData()
    }
    
  } catch (error) {
    console.error('❌ Error fetching cycle time:', error.message)
    errorMessage.value = `Failed to load cycle time data: ${error.message}`
    generateDemoData()
  } finally {
    isLoading.value = false
  }
}

// Process real API data
function processCycleTimeData(apiData) {
  actualData.value = []
  timeLabels.value = []
  
  // Format: apiData should be array of {timestamp, cycle_time}
  if (Array.isArray(apiData)) {
    // Sort by timestamp
    apiData.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp))
    
    // Take last 24 points for better visualization
    const dataPoints = apiData.slice(-24)
    
    dataPoints.forEach((item, index) => {
      const date = new Date(item.timestamp)
      const timeStr = date.toLocaleTimeString([], { 
        hour: '2-digit', 
        minute: '2-digit' 
      })
      
      actualData.value.push({
        time: timeStr,
        value: parseFloat(item.cycle_time) || 0
      })
      
      // Add to time labels (every 3rd point for cleaner display)
      if (index % 3 === 0) {
        timeLabels.value.push(timeStr)
      }
    })
    
    // Ensure we have at least some labels
    if (timeLabels.value.length < 3) {
      timeLabels.value = actualData.value
        .filter((_, i) => i % Math.ceil(actualData.value.length / 6) === 0)
        .map(item => item.time)
    }
  }
}

// Generate demo data (fallback)
function generateDemoData() {
  actualData.value = []
  timeLabels.value = []
  
  const now = new Date()
  const dataPoints = 24
  
  for (let i = 0; i < dataPoints; i++) {
    const time = new Date(now.getTime() - (dataPoints - i - 1) * 3600000) // Each hour back
    
    // Generate realistic cycle time data with some variation
    const baseTime = TARGET_CYCLE_TIME
    const variation = Math.sin(i * 0.5) * 3 + Math.random() * 2
    const cycleTime = Math.max(13, Math.min(22, baseTime + variation))
    
    const timeStr = time.toLocaleTimeString([], { 
      hour: '2-digit', 
      minute: '2-digit' 
    })
    
    actualData.value.push({
      time: timeStr,
      value: parseFloat(cycleTime.toFixed(2))
    })
    
    // Add time labels
    if (i % 4 === 0) {
      timeLabels.value.push(timeStr)
    }
  }
  
  // Ensure last label
  if (timeLabels.value[timeLabels.value.length - 1] !== actualData.value[actualData.value.length - 1].time) {
    timeLabels.value.push(actualData.value[actualData.value.length - 1].time)
  }
}

// Update current time
function updateCurrentTime() {
  const now = new Date()
  currentTime.value = now.toLocaleTimeString([], { 
    hour: '2-digit', 
    minute: '2-digit',
    second: '2-digit'
  })
}

// Update graph dimensions
function updateGraphDimensions() {
  const container = document.querySelector('.main-graph')
  if (container) {
    graphWidth.value = container.clientWidth - 40
    graphHeight.value = container.clientHeight - 60
  }
}

// ========== LIFECYCLE HOOKS ==========

onMounted(() => {
  console.log('📈 Cycle Time Trend Graph mounted')
  
  // Initial data fetch
  fetchCycleTimeData()
  updateCurrentTime()
  updateGraphDimensions()
  
  // Set up intervals
  const timeInterval = setInterval(updateCurrentTime, 1000)
  const dataInterval = setInterval(fetchCycleTimeData, 30000) // Update every 30 seconds
  const resizeInterval = setInterval(updateGraphDimensions, 1000)
  
  // Handle window resize
  window.addEventListener('resize', updateGraphDimensions)
  
  onUnmounted(() => {
    clearInterval(timeInterval)
    clearInterval(dataInterval)
    clearInterval(resizeInterval)
    window.removeEventListener('resize', updateGraphDimensions)
  })
})
</script>

<style scoped>
.cycle-time-trend-container {
  background: #000000;
  border: 1px solid #333;
  border-radius: 8px;
  padding: 15px;
  margin: 10px 0;
  position: relative;
  font-family: 'Arial', sans-serif;
}

/* Header Styles */
.trend-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 15px;
  padding-bottom: 10px;
  border-bottom: 1px solid #333;
}

.header-left h3 {
  color: #FFFFFF;
  margin: 0 0 5px 0;
  font-size: 16px;
  font-weight: bold;
}

.time-range {
  display: flex;
  gap: 15px;
  align-items: center;
}

.time-range .label {
  color: #AAA;
  font-size: 12px;
}

.time-range .current-time {
  color: #00FF00;
  font-size: 14px;
  font-weight: bold;
}

.header-right .legend {
  display: flex;
  gap: 20px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.legend-color {
  width: 12px;
  height: 12px;
  border-radius: 2px;
}

.legend-color.target {
  background: #1E90FF;
  border: 1px solid #87CEFA;
}

.legend-color.actual {
  background: #FFD700;
  border: 1px solid #FFA500;
}

.legend-item span {
  color: #CCC;
  font-size: 12px;
}

/* Graph Container */
.graph-container {
  display: flex;
  height: 300px;
  position: relative;
}

.y-axis {
  width: 60px;
  padding-right: 10px;
  position: relative;
}

.y-label {
  color: #AAA;
  font-size: 11px;
  text-align: center;
  margin-bottom: 10px;
  transform: rotate(-90deg);
  position: absolute;
  top: 50%;
  left: -60px;
}

.y-scale {
  height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.y-tick {
  color: #666;
  font-size: 10px;
  text-align: right;
  padding-right: 5px;
}

.main-graph {
  flex: 1;
  position: relative;
  background: #111;
  border-radius: 4px;
  overflow: hidden;
}

/* Grid Lines */
.grid-lines {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.grid-line {
  position: absolute;
  background: #222;
}

.grid-line.horizontal {
  width: 100%;
  height: 1px;
  transform: translateY(-50%);
}

.grid-line.vertical {
  height: 100%;
  width: 1px;
  transform: translateX(-50%);
}

/* Target Line */
.target-line {
  position: absolute;
  left: 0;
  right: 0;
  height: 2px;
  background: #1E90FF;
  opacity: 0.8;
  z-index: 1;
}

.target-label {
  position: absolute;
  right: 5px;
  top: -20px;
  background: #1E90FF;
  color: white;
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: bold;
}

/* SVG Trend Line */
.trend-svg {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: 2;
}

.data-point {
  cursor: pointer;
  transition: r 0.2s;
}

.data-point:hover {
  r: 5;
  fill: #FFA500;
}

/* X-Axis */
.x-axis {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 30px;
  border-top: 1px solid #333;
}

.x-label {
  position: absolute;
  transform: translateX(-50%);
  color: #666;
  font-size: 10px;
  white-space: nowrap;
  top: 8px;
}

/* Tooltip */
.tooltip {
  position: absolute;
  background: rgba(0, 0, 0, 0.9);
  border: 1px solid #444;
  border-radius: 4px;
  padding: 8px;
  z-index: 100;
  min-width: 150px;
  pointer-events: none;
  box-shadow: 0 2px 8px rgba(0,0,0,0.5);
}

.tooltip::before {
  content: '';
  position: absolute;
  bottom: -5px;
  left: 50%;
  transform: translateX(-50%);
  border-left: 5px solid transparent;
  border-right: 5px solid transparent;
  border-top: 5px solid rgba(0, 0, 0, 0.9);
}

.tooltip-time {
  color: #00FF00;
  font-size: 12px;
  font-weight: bold;
  margin-bottom: 4px;
}

.tooltip-value, .tooltip-variance {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  margin-bottom: 2px;
}

.tooltip-value .label, .tooltip-variance .label {
  color: #AAA;
}

.tooltip-value .value {
  color: #FFD700;
  font-weight: bold;
}

.tooltip-variance .value.positive {
  color: #00FF00;
}

.tooltip-variance .value.negative {
  color: #FF5555;
}

/* Stats Summary */
.stats-summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 15px;
  margin-top: 20px;
  padding-top: 15px;
  border-top: 1px solid #333;
}

.stat-item {
  text-align: center;
  padding: 10px;
  background: #111;
  border-radius: 4px;
  border: 1px solid #222;
}

.stat-label {
  color: #AAA;
  font-size: 11px;
  margin-bottom: 5px;
  text-transform: uppercase;
}

.stat-value {
  font-size: 18px;
  font-weight: bold;
  margin-bottom: 3px;
}

.stat-value.positive {
  color: #00FF00;
}

.stat-value.negative {
  color: #FF5555;
}

.stat-variance {
  font-size: 12px;
}

.stat-variance.positive {
  color: #00FF00;
}

.stat-variance.negative {
  color: #FF5555;
}

/* Loading Overlay */
.loading-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.8);
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  z-index: 10;
}

.loading-spinner {
  width: 40px;
  height: 40px;
  border: 3px solid #333;
  border-top: 3px solid #00FF00;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin-bottom: 10px;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

.loading-text {
  color: #CCC;
  font-size: 14px;
}

/* Error Message */
.error-message {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  background: rgba(255, 0, 0, 0.1);
  border: 1px solid #FF5555;
  border-radius: 6px;
  padding: 20px;
  text-align: center;
  z-index: 10;
  min-width: 300px;
}

.error-icon {
  font-size: 24px;
  margin-bottom: 10px;
}

.error-text {
  color: #FF5555;
  margin-bottom: 15px;
  font-size: 14px;
}

.retry-btn {
  background: #FF5555;
  color: white;
  border: none;
  padding: 8px 16px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  transition: background 0.2s;
}

.retry-btn:hover {
  background: #FF3333;
}
</style>