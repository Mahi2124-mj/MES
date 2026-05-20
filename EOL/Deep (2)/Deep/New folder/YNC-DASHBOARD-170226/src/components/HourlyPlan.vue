<template>
  <div class="horizontal-hourly-table">
    <!-- Hour Row -->
    <div class="hourly-row hour-row">
      <div class="row-label">HOUR</div>
      <div 
        v-for="(hour, index) in hourlyData.hours" 
        :key="'hour-' + index"
        class="hour-cell"
      >
        {{ hour }}
      </div>
      <div class="hour-total-cell">TOTAL</div>
    </div>

    <!-- Plan Row -->
    <div class="hourly-row plan-row">
      <div class="row-label">PLAN</div>
      <div 
        v-for="(plan, index) in hourlyData.plan" 
        :key="'plan-' + index"
        class="plan-cell"
      >
        {{ plan }}
      </div>
      <div class="plan-total-cell">
        {{ hourlyData.totalPlan.toLocaleString() }}
      </div>
    </div>

    <!-- Actual Row -->
    <div class="hourly-row actual-row">
      <div class="row-label">ACTUAL</div>
      <div 
        v-for="(actual, index) in hourlyData.actual" 
        :key="'actual-' + index"
        class="actual-cell"
      >
        <span class="actual-value">{{ actual }}</span>
        <span 
          class="variance" 
          :class="{
            'positive': hourlyData.variances[index] > 0,
            'negative': hourlyData.variances[index] < 0
          }"
        >
          ({{ hourlyData.variances[index] > 0 ? '+' : '' }}{{ hourlyData.variances[index] }})
        </span>
      </div>
      <div class="actual-total-cell">
        <span class="actual-value">{{ hourlyData.totalActual }}</span>
        <span 
          class="variance" 
          :class="{
            'positive': hourlyData.totalVariance > 0,
            'negative': hourlyData.totalVariance < 0
          }"
        >
          ({{ hourlyData.totalVariance > 0 ? '+' : '' }}{{ hourlyData.totalVariance }})
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  hourlyData: {
    type: Object,
    required: true,
    default: () => ({
      hours: [
        '08:30-09:30',
        '09:30-10:30',
        '10:30-11:30',
        '11:30-13:05',
        '13:05-14:05',
        '14:05-15:05',
        '15:05-16:05',
        '16:05-17:15',
        '17:15-18:30'
      ],
      plan: [235, 190, 235, 235, 235, 190, 235, 265, 0],
      actual: [0, 0, 0, 0, 0, 0, 0, 0, 0],
      variances: [-235, -190, -235, -235, -235, -190, -235, -265, 0],
      totalPlan: 1820,
      totalActual: 0,
      totalVariance: -1820
    })
  }
})
</script>