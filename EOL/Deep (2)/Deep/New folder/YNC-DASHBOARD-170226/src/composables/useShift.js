import { ref, computed, onMounted, onUnmounted } from 'vue'

export function useShift() {
  const currentShift = ref('A')
  
  // Shift timings
  const shiftTimings = {
    'A': { start: '08:30', end: '17:15' },
    'B': { start: '18:30', end: '03:15' }
  }

  function detectShift() {
    const now = new Date()
    const hour = now.getHours()
    const minute = now.getMinutes()
    const totalMinutes = (hour * 60) + minute
    
    // A Shift: 08:30 to 17:15
    const aStart = (8 * 60) + 30
    const aEnd = (17 * 60) + 15
    
    // B Shift: 18:30 to 03:15 (next day)
    const bStart = (18 * 60) + 30
    let bEnd = (3 * 60) + 15
    if (bEnd < bStart) bEnd += (24 * 60) // Add 24 hours for overnight
    
    let adjustedTotal = totalMinutes
    if (totalMinutes < aStart) adjustedTotal += (24 * 60) // Before 08:30
    
    if (adjustedTotal >= aStart && adjustedTotal <= aEnd) {
      currentShift.value = 'A'
    } else if (adjustedTotal >= bStart && adjustedTotal <= bEnd) {
      currentShift.value = 'B'
    } else {
      currentShift.value = '-'
    }
  }

  // Shift hours for display
  const shiftHours = computed(() => {
    if (currentShift.value === 'A') {
      return [
        '08:30-09:30',
        '09:30-10:30', 
        '10:30-11:30',
        '11:30-13:05',
        '13:05-14:05',
        '14:05-15:05',
        '15:05-16:05',
        '16:05-17:15'
      ]
    } else {
      return [
        '18:30-19:30',
        '19:30-20:30',
        '20:30-21:30',
        '21:30-23:05',
        '23:05-00:05',
        '00:05-01:05',
        '01:05-02:05',
        '02:05-03:15'
      ]
    }
  })

  // Shift color
  const shiftColor = computed(() => {
    return '#ebff3bff' // Same as original
  })

  // Initialize
  onMounted(() => {
    detectShift()
    const interval = setInterval(detectShift, 60000)
    
    onUnmounted(() => {
      clearInterval(interval)
    })
  })

  return {
    currentShift,
    shiftHours,
    shiftColor,
    shiftTimings,
    detectShift
  }
}