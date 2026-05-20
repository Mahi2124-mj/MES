import { ref, computed } from 'vue'

export function useOEE(initialData = {}) {
  // OEE data
  const oeeData = ref({
    availability: initialData.availability || 92.5,
    performance: initialData.performance || 88.2,
    quality: initialData.quality || 95.3,
    overall: initialData.overall || 77.8,
    grade: initialData.grade || 'GOOD'
  })

  // Calculate overall OEE
  const calculateOverall = () => {
    const overall = (oeeData.value.availability * oeeData.value.performance * oeeData.value.quality) / 10000
    oeeData.value.overall = parseFloat(overall.toFixed(1))
    updateGrade()
  }

  // Update grade based on OEE value
  const updateGrade = () => {
    const oee = oeeData.value.overall
    if (oee >= 90) oeeData.value.grade = 'EXCELLENT'
    else if (oee >= 80) oeeData.value.grade = 'GOOD'
    else if (oee >= 70) oeeData.value.grade = 'AVERAGE'
    else if (oee >= 60) oeeData.value.grade = 'FAIR'
    else oeeData.value.grade = 'POOR'
  }

  // Grade color
  const gradeColor = computed(() => {
    const oee = oeeData.value.overall
    if (oee >= 90) return '#27ae60'
    else if (oee >= 80) return '#2ecc71'
    else if (oee >= 70) return '#f39c12'
    else if (oee >= 60) return '#e67e22'
    else return '#e74c3c'
  })

  // Update availability
  const updateAvailability = (value) => {
    oeeData.value.availability = Math.max(0, Math.min(100, value))
    calculateOverall()
  }

  // Update performance
  const updatePerformance = (value) => {
    oeeData.value.performance = Math.max(0, Math.min(100, value))
    calculateOverall()
  }

  // Update quality
  const updateQuality = (value) => {
    oeeData.value.quality = Math.max(0, Math.min(100, value))
    calculateOverall()
  }

  // Reset to initial values
  const reset = () => {
    oeeData.value = {
      availability: 92.5,
      performance: 88.2,
      quality: 95.3,
      overall: 77.8,
      grade: 'GOOD'
    }
  }

  return {
    oeeData,
    gradeColor,
    updateAvailability,
    updatePerformance,
    updateQuality,
    calculateOverall,
    reset
  }
}