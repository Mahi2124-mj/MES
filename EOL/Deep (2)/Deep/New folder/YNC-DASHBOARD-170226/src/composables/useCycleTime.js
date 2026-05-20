import { ref, onMounted, onUnmounted } from 'vue'

export function useCycleTime(targetCycleTime = 15.6) {
  const currentCycleTime = ref('--')
  const averageCycleTime = ref(0)
  const partTimestamps = ref([])
  const cycleTimes = ref([])
  const lastProductionValue = ref(0)

  const config = {
    maxHistory: 10,
    updateInterval: 1000
  }

  // Convert time to decimal
  function timeToDecimal(timeStr) {
    const [hours, minutes] = timeStr.split(':').map(Number)
    return hours + (minutes / 60)
  }

  // Get production value (simulated)
  function getProductionValue() {
    // In real app, this would read from PLC or API
    // For now, simulate random production
    return Math.floor(Math.random() * 1000)
  }

  // Record new part
  function recordPart(timestamp) {
    partTimestamps.value.push(timestamp)
    
    // Keep only last N timestamps
    if (partTimestamps.value.length > config.maxHistory) {
      partTimestamps.value.shift()
    }
    
    // Calculate individual cycle times
    if (partTimestamps.value.length >= 2) {
      const times = []
      for (let i = 1; i < partTimestamps.value.length; i++) {
        const timeDiff = (partTimestamps.value[i] - partTimestamps.value[i-1]) / 1000
        times.push(timeDiff)
      }
      cycleTimes.value = times
    }
  }

  // Calculate average cycle time
  function calculateAverage() {
    if (cycleTimes.value.length === 0) {
      averageCycleTime.value = 0
      return
    }
    
    const sum = cycleTimes.value.reduce((a, b) => a + b, 0)
    averageCycleTime.value = sum / cycleTimes.value.length
  }

  // Update cycle time display
  function updateDisplay() {
    if (averageCycleTime.value === 0) {
      currentCycleTime.value = `${targetCycleTime}s / --`
      return
    }
    
    const actualFormatted = averageCycleTime.value.toFixed(1)
    currentCycleTime.value = `${targetCycleTime}s / ${actualFormatted}s`
  }

  // Monitor production
  function monitorProduction() {
    const currentValue = getProductionValue()
    
    if (currentValue > lastProductionValue.value) {
      const partsProduced = currentValue - lastProductionValue.value
      const now = Date.now()
      
      for (let i = 0; i < partsProduced; i++) {
        recordPart(now)
      }
      
      calculateAverage()
      updateDisplay()
      lastProductionValue.value = currentValue
    } else if (currentValue < lastProductionValue.value) {
      // Counter reset
      lastProductionValue.value = currentValue
    }
  }

  // Initialize monitoring
  onMounted(() => {
    monitorProduction()
    const interval = setInterval(monitorProduction, config.updateInterval)
    
    onUnmounted(() => {
      clearInterval(interval)
    })
  })

  // Debug function
  const debug = () => {
    console.log('=== CYCLE TIME DEBUG ===')
    console.log('Last value:', lastProductionValue.value)
    console.log('Part timestamps:', partTimestamps.value.length)
    console.log('Cycle times:', cycleTimes.value)
    console.log('Average:', averageCycleTime.value.toFixed(2) + 's')
  }

  return {
    currentCycleTime,
    averageCycleTime,
    monitorProduction,
    debug,
    reset: () => {
      partTimestamps.value = []
      cycleTimes.value = []
      averageCycleTime.value = 0
      lastProductionValue.value = 0
      updateDisplay()
    }
  }
}