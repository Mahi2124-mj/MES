<template>
  <div class="table-middle">
    <table class="oee-table">
      <thead>
        <tr>
          <th colspan="2" class="oee-header">OEE CALCULATION</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td class="oee-param">
            <i class="fas fa-check-circle"></i> Availability
            <div class="oee-formula">(Operating Time / Planned Time)</div>
          </td>
          <td class="oee-value">
            <div class="oee-percent" id="availability">
              {{ oeeData.availability.toFixed(1) }}%
            </div>
            <div class="oee-bar">
              <div class="oee-fill availability-fill" 
                   :style="{ width: oeeData.availability + '%' }"></div>
            </div>
          </td>
        </tr>
        <tr>
          <td class="oee-param">
            <i class="fas fa-tachometer-alt"></i> Performance
            <div class="oee-formula">(Actual Output / Target Output)</div>
          </td>
          <td class="oee-value">
            <div class="oee-percent" id="performance">
              {{ oeeData.performance.toFixed(1) }}%
            </div>
            <div class="oee-bar">
              <div class="oee-fill performance-fill" 
                   :style="{ width: oeeData.performance + '%' }"></div>
            </div>
          </td>
        </tr>
        <tr>
          <td class="oee-param">
            <i class="fas fa-star"></i> Quality
            <div class="oee-formula">(Good Units / Total Units)</div>
          </td>
          <td class="oee-value">
            <div class="oee-percent" id="quality">
              {{ oeeData.quality.toFixed(1) }}%
            </div>
            <div class="oee-bar">
              <div class="oee-fill quality-fill" 
                   :style="{ width: oeeData.quality + '%' }"></div>
            </div>
          </td>
        </tr>
        <tr class="oee-total-row">
          <td class="oee-param total-param">
            <i class="fas fa-calculator"></i> OEE
            <div class="oee-formula">(A × P × Q)</div>
          </td>
          <td class="oee-value total-value">
            <div class="oee-percent total-percent" id="overall-oee">
              {{ oeeData.overall.toFixed(1) }}%
            </div>
            <div class="oee-bar total-bar">
              <div class="oee-fill total-fill" 
                   :style="{ width: oeeData.overall + '%' }"></div>
            </div>
            <div class="oee-grade" :style="{ color: gradeColor }" id="oee-grade">
              {{ oeeData.grade }}
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  oeeData: {
    type: Object,
    required: true,
    default: () => ({
      availability: 0,
      performance: 0,
      quality: 0,
      overall: 0,
      grade: 'GOOD'
    })
  }
})

// Grade color based on OEE value
const gradeColor = computed(() => {
  const oee = props.oeeData.overall
  if (oee >= 90) return '#27ae60'
  else if (oee >= 80) return '#2ecc71'
  else if (oee >= 70) return '#f39c12'
  else if (oee >= 60) return '#e67e22'
  else return '#e74c3c'
})
</script>