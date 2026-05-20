<template>
  <div class="table-left">
    <table class="dashboard-table">
      <tr>
        <td class="param-label">
          <i class="fas fa-hashtag"></i> Line
        </td>
        <td class="param-value" id="line-name">
          {{ lineData.name }}
        </td>
      </tr>
      <tr>
        <td class="param-label">
          <i class="fas fa-cube"></i> Current Model
        </td>
        <td class="param-value" id="model-name">
          {{ lineData.model }}
        </td>
      </tr>
      <tr>
        <td class="param-label">
          <i class="fas fa-bullseye"></i> Production Plan
        </td>
        <td class="param-value" id="production-plan">
          {{ lineData.plan.toLocaleString() }}
        </td>
      </tr>
      <tr>
        <td class="param-label">
          <i class="fas fa-chart-bar"></i> Production ok_count
        </td>
        <td class="param-value" id="production-actual">
          {{ lineData.actual.toLocaleString() }}
        </td>
      </tr>
      <tr>
        <td class="param-label">
          <i class="fas fa-tachometer-alt"></i> Cycle Time
             (Plan/Actual)
        </td>
        <td class="param-value" id="cycle-time">
          {{ cycleTime }}
        </td>
      </tr>
      <tr>
        <td class="param-label">
          <i class="fas fa-trophy"></i> Plan Achievement (%)
        </td>
        <td class="param-value achievement" id="achievement">
          {{ lineData.achievement }}
        </td>
      </tr>
    </table>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  lineData: {
    type: Object,
    required: true,
    default: () => ({
      name: 'YNC-SEAT SLIDER',
      model: 'updating',
      plan: 1820,
      actual: 0,
      achievement: '85%'
    })
  },
  cycleTime: {
    type: String,
    default: '--'
  }
})
</script>